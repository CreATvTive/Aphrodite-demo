"""Bounded adversarial delta validation for the P3.7 cross-database receipt fix.

This test module is intentionally concentrated on the revised causal surface:
perception append -> atomic field batch -> live candidate publication ->
consumption marker -> restart/replay.  It is not a replacement for the wider P4
formal or soak matrices.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import gc
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import tracemalloc
from typing import TypedDict

import pytest

from app.chatbox.field_dynamics import (
    AttractorMove,
    DimensionRegistration,
    SeededGaussianRngFactory,
    build_birth_registry,
)
from app.chatbox.field_persistence import (
    AttractorBatchMoveInput,
    AttractorBatchReceipt,
    FieldPersistenceError,
    FieldPersistenceStore,
    PERSISTENCE_SCHEMA_VERSION,
    _strict_json_dumps,
)
from app.chatbox.field_runtime import FieldRuntime, FieldRuntimeError
from app.chatbox.perception_bus import PerceptionBus
from app.chatbox.perception_config import PERCEPTION_EVENT_VERSION
from app.chatbox.perception_persistence import (
    PerceptionPersistenceError,
    PerceptionPersistenceStore,
)
from app.chatbox.perception_schema import validate_event


SEED = 0xD37A


class _Clock:
    def __init__(self, start: int = 1_900_000_000_000_000_000) -> None:
        self.value = start

    def __call__(self) -> int:
        self.value += 1
        return self.value


def _registration(index: int) -> DimensionRegistration:
    return DimensionRegistration(
        dim_id=f"dim-{index}", temporary_name=f"stress-{index}", birth_time=0.0,
        strength=1.0, trigger_count=0, birth_bias=0.0, fast_e_fold_s=600.0,
        ou_correlation_e_fold_s=10_800.0, ou_acceleration_sigma=4.0e-7,
        soft_boundary_start=1.0, soft_boundary_width=0.25,
        soft_boundary_strength=(1.0 / 120.0) ** 2,
    )


def _open_runtime(root: Path, *, birth: bool = False, count: int = 12) -> FieldRuntime:
    path = str(root / "field.sqlite3")
    if birth:
        registry = build_birth_registry() if count == 12 else tuple(
            _registration(index) for index in range(count)
        )
        return FieldRuntime.open(
            path, birth_registry=registry,
            birth_rng_factory=SeededGaussianRngFactory(SEED), utc_clock=_Clock(),
        )
    return FieldRuntime.open(path, utc_clock=_Clock())


def _open_system(root: Path, *, birth: bool = False, count: int = 12):
    runtime = _open_runtime(root, birth=birth, count=count)
    store = PerceptionPersistenceStore(str(root / "perception.sqlite3"))
    return runtime, store, PerceptionBus(runtime, store, utc_clock=_Clock())


def _envelope(event_id: str, *, state: str = "start", observed_at: int = 1_700_000_000) -> dict:
    return {
        "version": PERCEPTION_EVENT_VERSION,
        "event_id": event_id,
        "session_id": "stress-session",
        "kind": "typing",
        "observed_at": observed_at,
        "payload": {"state": state},
        "source": "stress.injected",
    }


def _gap_envelope(event_id: str, *, first: bool = False) -> dict:
    return {
        "version": PERCEPTION_EVENT_VERSION,
        "event_id": event_id,
        "session_id": "stress-session",
        "kind": "message_gap",
        "observed_at": 1_700_000_000,
        "payload": {
            "duration_seconds": 7200.0,
            "is_first": first,
            "is_new_session": False,
        },
        "source": "stress.injected",
    }


def _attractors(runtime: FieldRuntime) -> dict[str, float]:
    return {item.dim_id: item.attractor for item in runtime.snapshot_proxy().dimensions}


class _DbState(TypedDict):
    receipts: list[tuple]
    events: list[tuple]
    snapshots: list[tuple]


def _db_state(root: Path) -> _DbState:
    conn = sqlite3.connect(str(root / "field.sqlite3"))
    try:
        receipts = conn.execute(
            "SELECT operation_id,request_sha256,receipt_json,receipt_sha256 "
            "FROM field_operation_receipts ORDER BY operation_id"
        ).fetchall()
        events = conn.execute(
            "SELECT event_id,payload_json,payload_sha256 FROM field_events "
            "WHERE event_kind='attractor_move' ORDER BY event_id"
        ).fetchall()
        snapshots = conn.execute(
            "SELECT snapshot_id,field_tick,capsule_json,capsule_sha256 "
            "FROM field_snapshots ORDER BY snapshot_id"
        ).fetchall()
        return {"receipts": receipts, "events": events, "snapshots": snapshots}
    finally:
        conn.close()


def _count_consumed(root: Path) -> int:
    conn = sqlite3.connect(str(root / "perception.sqlite3"))
    try:
        return int(conn.execute("SELECT COUNT(*) FROM perception_consumption").fetchone()[0])
    finally:
        conn.close()


def _install_trigger(conn: sqlite3.Connection, name: str, table: str) -> None:
    conn.execute(
        f"CREATE TRIGGER {name} BEFORE INSERT ON {table} BEGIN "
        "SELECT RAISE(ABORT, 'stress cut'); END"
    )


def _drop_trigger(root: Path, name: str) -> None:
    conn = sqlite3.connect(str(root / "field.sqlite3"))
    try:
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        conn.commit()
    finally:
        conn.close()


def _close_system(runtime: FieldRuntime, store: PerceptionPersistenceStore) -> None:
    store.close()
    runtime.close()


def _assert_one_field_application(root: Path, expected: dict[str, float]) -> None:
    runtime = _open_runtime(root)
    try:
        assert _attractors(runtime) == expected
        state = _db_state(root)
        assert len(state["receipts"]) == 1
        receipt = json.loads(state["receipts"][0][2])
        applied = [item for item in receipt["results"] if item["status"] == "applied"]
        assert len(state["events"]) == len(applied)
        assert {row[0] for row in state["events"]} == {
            item["event_id"] for item in applied
        }
    finally:
        runtime.close()


@pytest.mark.parametrize("iteration", range(20))
def test_original_cross_database_counterexample_is_closed_20_of_20(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, iteration: int,
) -> None:
    """Field commit without consumption replays by receipt, never by mutation."""
    root = tmp_path / str(iteration)
    root.mkdir()
    runtime, store, bus = _open_system(root, birth=True)

    def fail_consumption(*_args, **_kwargs):
        raise PerceptionPersistenceError("stress_consumption_cut", "after field commit")

    monkeypatch.setattr(store, "record_consumption", fail_consumption)
    before = _attractors(runtime)
    partial = bus.ingest(_envelope("same-event"))
    after = _attractors(runtime)
    assert partial.accepted and not partial.consumption_recorded
    assert partial.error_code == "stress_consumption_cut"
    assert after != before
    assert _count_consumed(root) == 0
    _close_system(runtime, store)

    runtime2, store2, bus2 = _open_system(root)
    try:
        recovered_before = _attractors(runtime2)
        replay = bus2.replay_unconsumed()
        assert len(replay) == 1
        assert replay[0].field_application_deduplicated
        assert replay[0].consumption_recorded
        assert _attractors(runtime2) == recovered_before == after
        assert _count_consumed(root) == 1
    finally:
        _close_system(runtime2, store2)
    _assert_one_field_application(root, after)


@pytest.mark.parametrize(
    ("cut_name", "trigger_name", "table"),
    [
        ("before_first_event", "stress_fail_event", "field_events"),
        ("between_events", "stress_fail_second_event", "field_events"),
        ("between_events_and_snapshot", "stress_fail_snapshot", "field_snapshots"),
        ("between_snapshot_and_receipt", "stress_fail_receipt", "field_operation_receipts"),
    ],
)
def test_atomic_field_transaction_fault_cuts_replay_without_loss_or_duplication(
    tmp_path: Path, cut_name: str, trigger_name: str, table: str,
) -> None:
    root = tmp_path / cut_name
    root.mkdir()
    runtime, store, bus = _open_system(root, birth=True)
    before = _attractors(runtime)
    conn = runtime._store._conn  # noqa: SLF001 - deterministic transaction cut
    if cut_name == "between_events":
        conn.execute(
            "CREATE TRIGGER stress_fail_second_event BEFORE INSERT ON field_events "
            "WHEN (SELECT COUNT(*) FROM field_events WHERE event_kind='attractor_move') >= 1 "
            "BEGIN SELECT RAISE(ABORT, 'stress cut'); END"
        )
        envelope = _gap_envelope("tx-cut")
    else:
        _install_trigger(conn, trigger_name, table)
        envelope = _gap_envelope("tx-cut")
    failed = bus.ingest(envelope)
    assert failed.accepted and not failed.consumption_recorded
    assert failed.error_code == "persistence_batch_commit_failed"
    # A write-path uncertainty poisons the process image; only the database is
    # authoritative from here.  The transaction must have left no batch trace.
    with pytest.raises(FieldRuntimeError) as poisoned:
        runtime.snapshot_proxy()
    assert poisoned.value.code == "runtime_poisoned"
    state = _db_state(root)
    assert state["receipts"] == []
    assert state["events"] == []
    assert _count_consumed(root) == 0
    _close_system(runtime, store)

    # Remove only the test fault object, then a clean restart must recover the
    # exact pre-candidate state: the failed live candidate was never durable.
    _drop_trigger(root, trigger_name)
    runtime_probe = _open_runtime(root)
    try:
        assert _attractors(runtime_probe) == before
    finally:
        runtime_probe.close()
    runtime2, store2, bus2 = _open_system(root)
    try:
        replay = bus2.replay_unconsumed()
        assert len(replay) == 1 and replay[0].consumption_recorded
        assert not replay[0].field_application_deduplicated
        expected = _attractors(runtime2)
        assert expected != before
    finally:
        _close_system(runtime2, store2)
    _assert_one_field_application(root, expected)


def test_cut_after_db_commit_before_live_install_recovers_receipt_without_reapply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path
    runtime, store, bus = _open_system(root, birth=True)
    before = _attractors(runtime)
    original = runtime._store.commit_attractor_batch  # noqa: SLF001

    def commit_then_terminate(**kwargs):
        original(**kwargs)
        raise SystemExit("stress termination after durable commit")

    monkeypatch.setattr(runtime._store, "commit_attractor_batch", commit_then_terminate)  # noqa: SLF001
    with pytest.raises(SystemExit):
        bus.ingest(_gap_envelope("post-commit-cut"))
    # The old process image never published its candidate, but the durable DB did.
    assert _attractors(runtime) == before
    assert len(_db_state(root)["receipts"]) == 1
    assert _count_consumed(root) == 0
    monkeypatch.setattr(runtime._store, "commit_attractor_batch", original)  # noqa: SLF001
    _close_system(runtime, store)

    runtime2, store2, bus2 = _open_system(root)
    try:
        durable = _attractors(runtime2)
        assert durable != before
        replay = bus2.replay_unconsumed()
        assert len(replay) == 1
        assert replay[0].field_application_deduplicated
        assert replay[0].consumption_recorded
        assert _attractors(runtime2) == durable
    finally:
        _close_system(runtime2, store2)
    _assert_one_field_application(root, durable)


def test_cut_before_field_and_after_consumption_are_recoverable_terminal_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Persisted perception event, process ends before entering field transaction.
    root_before = tmp_path / "before"
    root_before.mkdir()
    runtime, store, _bus = _open_system(root_before, birth=True)
    before = _attractors(runtime)
    event = validate_event(_envelope("before-field"))
    assert store.append_event(event, utc_unix_ns=_Clock()())
    _close_system(runtime, store)
    runtime2, store2, bus2 = _open_system(root_before)
    try:
        outcome = bus2.replay_unconsumed()[0]
        assert outcome.consumption_recorded
        after = _attractors(runtime2)
        assert after != before
    finally:
        _close_system(runtime2, store2)
    _assert_one_field_application(root_before, after)

    # Process termination after the consumption insert must not replay.
    root_after = tmp_path / "after"
    root_after.mkdir()
    runtime3, store3, bus3 = _open_system(root_after, birth=True)
    original = store3.record_consumption

    def consume_then_terminate(*args, **kwargs):
        original(*args, **kwargs)
        raise SystemExit("stress termination after consumption")

    monkeypatch.setattr(store3, "record_consumption", consume_then_terminate)
    with pytest.raises(SystemExit):
        bus3.ingest(_envelope("after-consumption"))
    committed = _attractors(runtime3)
    assert _count_consumed(root_after) == 1
    monkeypatch.setattr(store3, "record_consumption", original)
    _close_system(runtime3, store3)
    runtime4, store4, bus4 = _open_system(root_after)
    try:
        assert bus4.replay_unconsumed() == ()
        assert _attractors(runtime4) == committed
    finally:
        _close_system(runtime4, store4)
    _assert_one_field_application(root_after, committed)


def test_operation_key_fingerprint_equivalence_conflicts_and_no_false_merge(
    tmp_path: Path,
) -> None:
    runtime = _open_runtime(tmp_path, birth=True, count=3)
    try:
        first = (
            AttractorMove("dim-0", 0.02, "stress", "first"),
            AttractorMove("dim-1", -0.03, "stress", "second"),
        )
        receipt = runtime.move_attractor_batch("op", first)
        once = _attractors(runtime)
        # Fresh objects and int/float canonical equivalence hit the same fingerprint.
        equivalent = (
            AttractorMove("dim-0", 2 / 100, "stress", "first"),
            AttractorMove("dim-1", -3 / 100, "stress", "second"),
        )
        duplicate = runtime.move_attractor_batch("op", equivalent)
        assert duplicate.deduplicated
        assert duplicate.request_sha256 == receipt.request_sha256
        assert _attractors(runtime) == once

        # Ordered move sequence is semantic: reverse order must conflict, not merge.
        with pytest.raises(FieldRuntimeError) as conflict:
            runtime.move_attractor_batch("op", tuple(reversed(equivalent)))
        assert conflict.value.code == "persistence_operation_conflict"
        assert _attractors(runtime) == once

        # Different ids with byte-identical payloads each apply once.
        runtime.move_attractor_batch("op-2", equivalent)
        twice = _attractors(runtime)
        assert twice != once
        assert len(_db_state(tmp_path)["receipts"]) == 2
    finally:
        runtime.close()


def test_same_event_id_different_payload_cannot_mutate_now_or_after_restart(tmp_path: Path) -> None:
    runtime, store, bus = _open_system(tmp_path, birth=True)
    original = bus.ingest(_envelope("collision", state="start"))
    once = _attractors(runtime)
    conflicting = bus.ingest(_envelope("collision", state="heartbeat"))
    assert original.consumption_recorded
    assert conflicting.deduplicated and not conflicting.accepted
    assert _attractors(runtime) == once
    assert len(_db_state(tmp_path)["receipts"]) == 1
    _close_system(runtime, store)

    runtime2, store2, bus2 = _open_system(tmp_path)
    try:
        again = bus2.ingest(_envelope("collision", state="heartbeat"))
        assert again.deduplicated and not again.accepted
        assert bus2.replay_unconsumed() == ()
        assert _attractors(runtime2) == once
    finally:
        _close_system(runtime2, store2)


def test_zero_target_and_all_rejected_receipts_never_gain_future_side_effects(
    tmp_path: Path,
) -> None:
    runtime, store, bus = _open_system(tmp_path, birth=True)
    baseline = _attractors(runtime)
    zero = bus.ingest(_gap_envelope("zero", first=True))
    assert zero.consumption_recorded and zero.applied_dim_ids == ()
    rejected = runtime.move_attractor_batch(
        "rejected", (AttractorMove("birth_00", 10.0, "stress", "reject"),)
    )
    assert not rejected.results[0].applied
    assert _attractors(runtime) == baseline
    # Move live state later; reusing receipts must not make old operations active.
    runtime.move_attractor_batch(
        "later", (AttractorMove("birth_00", 0.02, "stress", "later"),)
    )
    later = _attractors(runtime)
    assert runtime.move_attractor_batch("rejected", (
        AttractorMove("birth_00", 10.0, "stress", "reject"),
    )).deduplicated
    assert _attractors(runtime) == later
    _close_system(runtime, store)

    runtime2, store2, bus2 = _open_system(tmp_path)
    try:
        assert bus2.replay_unconsumed() == ()
        assert runtime2.move_attractor_batch("rejected", (
            AttractorMove("birth_00", 10.0, "stress", "reject"),
        )).deduplicated
        assert _attractors(runtime2) == later
    finally:
        _close_system(runtime2, store2)


def test_same_operation_concurrency_converges_to_one_receipt_with_barrier(
    tmp_path: Path,
) -> None:
    runtime = _open_runtime(tmp_path, birth=True, count=3)
    move = AttractorMove("dim-0", 0.02, "stress", "same-op")
    # Build a valid candidate/result.  The race itself uses one independent
    # thread-confined store per worker, matching the store's ownership contract.
    runtime.close()
    recovered = FieldRuntime.open(str(tmp_path / "field.sqlite3"))
    candidate = recovered._dynamics  # noqa: SLF001
    before = candidate.snapshot().dimensions[0].attractor
    after_snapshot = candidate.move_attractor(move)
    after = after_snapshot.dimensions[0].attractor
    from app.chatbox.field_state_capsule import _capture_field_state_capsule, encode_field_state_capsule
    primitive = encode_field_state_capsule(_capture_field_state_capsule(candidate))
    from app.chatbox.field_persistence import AttractorBatchMoveResult
    request = AttractorBatchMoveInput("dim-0", 0.02, "stress", "same-op")
    result = AttractorBatchMoveResult(
        "dim-0", 0.02, "stress", "same-op", True, before, after,
    )
    recovered.close()
    barrier = threading.Barrier(2)
    outputs: list[AttractorBatchReceipt] = []

    def commit() -> AttractorBatchReceipt:
        store_obj = FieldPersistenceStore(str(tmp_path / "field.sqlite3"))
        try:
            store_obj.ensure_schema()
            barrier.wait(timeout=5)
            return store_obj.commit_attractor_batch(
                operation_id="raced", boot_id="stress-boot", utc_unix_ns=7,
                moves=(request,), candidate_capsule_primitive=primitive,
                results=(result,),
            )
        finally:
            store_obj.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(commit) for _ in range(2)]
        for future in futures:
            outputs.append(future.result(timeout=10))
    assert len(outputs) == 2
    assert sorted(item.deduplicated for item in outputs) == [False, True]
    state = _db_state(tmp_path)
    assert len(state["receipts"]) == 1
    assert len(state["events"]) == 1


def test_runtime_owner_lock_blocks_duplicate_runtime_and_releases_cleanly(tmp_path: Path) -> None:
    runtime = _open_runtime(tmp_path, birth=True, count=3)
    try:
        with pytest.raises(FieldRuntimeError) as caught:
            FieldRuntime.open(str(tmp_path / "field.sqlite3"))
        assert caught.value.code in {"owner_lock_held", "owner_lock_acquire_failed"}
    finally:
        runtime.close()
    reopened = _open_runtime(tmp_path)
    reopened.close()


def test_duplicate_bus_and_replay_interleave_at_deterministic_consumption_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the reachable serial interleaving around consumption commit."""
    runtime, store, bus = _open_system(tmp_path, birth=True)
    event = validate_event(_envelope("interleave"))
    assert store.append_event(event, utc_unix_ns=_Clock()())
    original = store.record_consumption

    def cut_consumption(*_args, **_kwargs):
        raise PerceptionPersistenceError("stress_barrier", "consumption barrier")

    monkeypatch.setattr(store, "record_consumption", cut_consumption)
    first = bus.replay_unconsumed()
    assert len(first) == 1 and not first[0].consumption_recorded
    assert not first[0].field_application_deduplicated
    # At the exact cross-DB window, another bus attempts duplicate ingest.
    assert len(_db_state(tmp_path)["receipts"]) == 1
    assert _count_consumed(tmp_path) == 0
    duplicate_bus = PerceptionBus(runtime, store, utc_clock=_Clock())
    duplicate = duplicate_bus.ingest(_envelope("interleave"))
    assert duplicate.deduplicated and not duplicate.accepted
    with pytest.raises(FieldRuntimeError) as duplicate_owner:
        FieldRuntime.open(str(tmp_path / "field.sqlite3"))
    assert duplicate_owner.value.code in {"owner_lock_held", "owner_lock_acquire_failed"}

    monkeypatch.setattr(store, "record_consumption", original)
    second = bus.replay_unconsumed()
    assert len(second) == 1
    assert second[0].field_application_deduplicated
    assert second[0].consumption_recorded
    try:
        assert _count_consumed(tmp_path) == 1
        assert len(_db_state(tmp_path)["receipts"]) == 1
        final = _attractors(runtime)
    finally:
        _close_system(runtime, store)
    _assert_one_field_application(tmp_path, final)


def _downgrade_exact_v1(path: Path) -> dict[str, list[tuple]]:
    conn = sqlite3.connect(str(path))
    try:
        history = {
            table: conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in ("field_snapshots", "field_events", "trajectory_points")
        }
        conn.execute("DROP TRIGGER trg_no_update_field_operation_receipts")
        conn.execute("DROP TRIGGER trg_no_delete_field_operation_receipts")
        conn.execute("DROP INDEX idx_field_receipts_request_sha256")
        conn.execute("DROP TABLE field_operation_receipts")
        conn.execute(
            "UPDATE chatbox_meta SET value='aphrodite.chatbox.field-persistence/1' "
            "WHERE key='schema_version'"
        )
        conn.execute("PRAGMA user_version=1")
        conn.commit()
        return history
    finally:
        conn.close()


def test_real_v1_upgrade_is_atomic_idempotent_and_preserves_history(tmp_path: Path) -> None:
    runtime = _open_runtime(tmp_path, birth=True, count=3)
    runtime.tick()
    runtime.close()
    path = tmp_path / "field.sqlite3"
    history = _downgrade_exact_v1(path)
    for _ in range(2):
        store = FieldPersistenceStore(str(path))
        store.ensure_schema()
        store.close()
    conn = sqlite3.connect(str(path))
    try:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 2
        assert conn.execute(
            "SELECT value FROM chatbox_meta WHERE key='schema_version'"
        ).fetchone()[0] == PERSISTENCE_SCHEMA_VERSION
        for table, rows in history.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == rows
    finally:
        conn.close()


def test_migration_failure_rolls_back_without_partial_v2_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _open_runtime(tmp_path, birth=True, count=3)
    runtime.close()
    path = tmp_path / "field.sqlite3"
    _downgrade_exact_v1(path)
    original = FieldPersistenceStore._RECEIPT_DDL
    monkeypatch.setattr(
        FieldPersistenceStore, "_RECEIPT_DDL",
        (original[0], "CREATE TABLE field_operation_receipts (broken"),
    )
    store = FieldPersistenceStore(str(path))
    with pytest.raises(FieldPersistenceError) as caught:
        store.ensure_schema()
    assert caught.value.code == "persistence_schema_migration_failed"
    store._conn.close()  # failed migration store: avoid checkpoint masking primary probe
    monkeypatch.setattr(FieldPersistenceStore, "_RECEIPT_DDL", original)
    conn = sqlite3.connect(str(path))
    try:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 1
        assert conn.execute(
            "SELECT value FROM chatbox_meta WHERE key='schema_version'"
        ).fetchone()[0] == "aphrodite.chatbox.field-persistence/1"
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name='field_operation_receipts'"
        ).fetchone()[0] == 0
    finally:
        conn.close()
    reopened = FieldPersistenceStore(str(path))
    reopened.ensure_schema()
    reopened.close()


@pytest.mark.parametrize("corruption", ["partial_schema", "unknown_version"])
def test_partial_or_unknown_schema_fails_closed(tmp_path: Path, corruption: str) -> None:
    runtime = _open_runtime(tmp_path, birth=True, count=3)
    runtime.close()
    path = tmp_path / "field.sqlite3"
    conn = sqlite3.connect(str(path))
    try:
        if corruption == "partial_schema":
            conn.execute("DROP TRIGGER trg_no_update_field_operation_receipts")
        else:
            conn.execute("PRAGMA user_version=99")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises((FieldPersistenceError, FieldRuntimeError)):
        FieldRuntime.open(str(path))


def _mutate_receipt_database(root: Path, variant: str) -> None:
    conn = sqlite3.connect(str(root / "field.sqlite3"))
    try:
        conn.execute("DROP TRIGGER trg_no_update_field_operation_receipts")
        conn.execute("DROP TRIGGER trg_no_delete_field_operation_receipts")
        row = conn.execute(
            "SELECT operation_id,receipt_json FROM field_operation_receipts LIMIT 1"
        ).fetchone()
        operation_id, text = row
        receipt = json.loads(text)
        if variant == "receipt_hash":
            conn.execute(
                "UPDATE field_operation_receipts SET receipt_sha256='0' WHERE operation_id=?",
                (operation_id,),
            )
        elif variant == "request_hash":
            conn.execute(
                "UPDATE field_operation_receipts SET request_sha256=? WHERE operation_id=?",
                ("f" * 64, operation_id),
            )
        elif variant == "event_reference":
            receipt["results"][0]["event_id"] += 100000
            changed = _strict_json_dumps(receipt)
            conn.execute(
                "UPDATE field_operation_receipts SET receipt_json=?,receipt_sha256=? "
                "WHERE operation_id=?",
                (changed, hashlib.sha256(changed.encode()).hexdigest(), operation_id),
            )
        elif variant == "snapshot_reference":
            receipt["snapshot_id"] += 100000
            changed = _strict_json_dumps(receipt)
            conn.execute(
                "UPDATE field_operation_receipts SET receipt_json=?,receipt_sha256=? "
                "WHERE operation_id=?",
                (changed, hashlib.sha256(changed.encode()).hexdigest(), operation_id),
            )
        elif variant == "event_payload":
            event_id = receipt["results"][0]["event_id"]
            conn.execute("DROP TRIGGER trg_no_update_field_events")
            conn.execute(
                "UPDATE field_events SET payload_sha256='0' WHERE event_id=?", (event_id,)
            )
        elif variant == "snapshot_payload":
            snapshot_id = receipt["snapshot_id"]
            conn.execute(
                "UPDATE field_snapshots SET capsule_sha256='0' WHERE snapshot_id=?",
                (snapshot_id,),
            )
        elif variant == "truncate_receipt":
            truncated = text[:-1]
            conn.execute(
                "UPDATE field_operation_receipts SET receipt_json=?,receipt_sha256=? "
                "WHERE operation_id=?",
                (truncated, hashlib.sha256(truncated.encode()).hexdigest(), operation_id),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize(
    "variant",
    [
        "receipt_hash", "request_hash", "event_reference", "snapshot_reference",
        "event_payload", "snapshot_payload", "truncate_receipt",
    ],
)
def test_receipt_event_snapshot_tampering_never_silently_repairs(
    tmp_path: Path, variant: str,
) -> None:
    root = tmp_path / variant
    root.mkdir()
    runtime = _open_runtime(root, birth=True, count=3)
    runtime.move_attractor_batch(
        "tamper", (AttractorMove("dim-0", 0.02, "stress", "tamper"),)
    )
    runtime.close()
    before = (root / "field.sqlite3").read_bytes()
    _mutate_receipt_database(root, variant)
    corrupted = (root / "field.sqlite3").read_bytes()
    assert corrupted != before
    with pytest.raises((FieldRuntimeError, FieldPersistenceError)):
        FieldRuntime.open(str(root / "field.sqlite3"))
    # Failed open must not rewrite the evidence into a supposedly valid form.
    assert (root / "field.sqlite3").read_bytes() == corrupted


def test_bounded_reopen_replay_lifecycle_has_no_monotonic_resource_growth(tmp_path: Path) -> None:
    # Seed one cross-database partial completion.
    runtime, store, bus = _open_system(tmp_path, birth=True)
    original = store.record_consumption

    def fail(*_args, **_kwargs):
        raise PerceptionPersistenceError("stress_cut", "lifecycle seed")

    store.record_consumption = fail  # type: ignore[method-assign]
    partial = bus.ingest(_envelope("lifecycle"))
    assert not partial.consumption_recorded
    store.record_consumption = original  # type: ignore[method-assign]
    _close_system(runtime, store)

    tracemalloc.start()
    thread_baseline = threading.active_count()
    samples: list[int] = []
    for iteration in range(24):
        runtime_i, store_i, bus_i = _open_system(tmp_path)
        bus_i.replay_unconsumed()
        _close_system(runtime_i, store_i)
        gc.collect()
        if iteration in {7, 15, 23}:
            samples.append(tracemalloc.get_traced_memory()[0])
    tracemalloc.stop()
    assert threading.active_count() == thread_baseline
    # Allow allocator/cache noise, but reject sustained linear growth after warm-up.
    assert samples[-1] - samples[0] < 512_000, samples
    runtime_final, store_final, bus_final = _open_system(tmp_path)
    try:
        assert bus_final.replay_unconsumed() == ()
        assert _count_consumed(tmp_path) == 1
        assert len(_db_state(tmp_path)["receipts"]) == 1
        # Clean close checkpoints/truncates both WAL files when no peer is open.
    finally:
        _close_system(runtime_final, store_final)
    for filename in ("field.sqlite3-wal", "perception.sqlite3-wal"):
        wal = tmp_path / filename
        assert not wal.exists() or wal.stat().st_size == 0
    # The owner sidecar remains as a zero/one-byte lock anchor, not an open handle.
    reopened = _open_runtime(tmp_path)
    reopened.close()
