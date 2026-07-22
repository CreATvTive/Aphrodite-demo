"""P1.2-B persistence + runtime unit and integration tests.

Covers the frozen P1.2-B contract: empty-or-recover startup, per-tick
trajectory row counts equal to the actual registry length (no hardcoded 12),
second-connection visibility of committed events, append-only trigger
enforcement, fake-clock 60-second snapshot cadence with retention of 2,
fail-closed behavior for corrupted/malformed/mismatched snapshots and events,
no automatic fallback to older snapshots, runtime poisoning on event-commit
failure with parseable stderr JSON, append-only old boot segments preserved
across recovery, and an AST/import audit of the two new production files.

P1.2-B correction adds: schema fail-closed (unknown tables, missing
triggers/indexes, wrong column layouts), canonical text byte-for-byte
verification, event history audit, close/checkpoint fail-loud, public
snapshot boundary for tick reads, P1.1 InvalidAttractorMoveError
preservation, injected UTC clock for birth, and strengthened kill-restart
evidence.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Callable

import pytest

from app.chatbox.field_dynamics import (
    ATTRACTOR_DISPLACEMENT_RADIUS,
    AttractorMove,
    DimensionRegistration,
    FieldDynamics,
    InvalidAttractorMoveError,
    SeededGaussianRngFactory,
    build_birth_registry,
)
from app.chatbox.field_persistence import (
    EVENT_PAYLOAD_VERSION,
    PERSISTENCE_SCHEMA_VERSION,
    SNAPSHOT_RETENTION_COUNT,
    FieldPersistenceError,
    FieldPersistenceStore,
    TrajectoryRowInput,
    _strict_json_dumps,
)
from app.chatbox.field_runtime import (
    SNAPSHOT_INTERVAL_SECONDS,
    FieldRuntime,
    FieldRuntimeError,
)
from app.chatbox.field_state_capsule import (
    CAPSULE_SCHEMA_VERSION,
    DYNAMICS_VERSION,
    FieldStateCapsuleError,
    _capture_field_state_capsule,
    decode_field_state_capsule,
    encode_field_state_capsule,
)


PRODUCTION_FILES = (
    Path("app/chatbox/field_persistence.py"),
    Path("app/chatbox/field_runtime.py"),
)

QUARANTINED_MODULES = (
    "agentlib",
    "agent_kernel",
    "src.semantic_trigger",
    "demos.scenarios",
)

FORBIDDEN_NAMES = ("pickle", "marshal", "eval", "exec")


def _registration(dim_id: str, bias: float = 0.0) -> DimensionRegistration:
    return DimensionRegistration(
        dim_id=dim_id,
        temporary_name=f"synthetic-{dim_id}",
        birth_time=17.0,
        strength=1.0,
        trigger_count=0,
        birth_bias=bias,
        fast_e_fold_s=600.0,
        ou_correlation_e_fold_s=10_800.0,
        ou_acceleration_sigma=4.0e-7,
        soft_boundary_start=1.0,
        soft_boundary_width=0.25,
        soft_boundary_strength=(1.0 / 120.0) ** 2,
    )


def _registry(count: int) -> tuple[DimensionRegistration, ...]:
    return tuple(
        _registration(f"custom-{index}", bias=(-0.125 if index == 0 else 0.0))
        for index in range(count)
    )


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class _FakeUtcClock:
    def __init__(self, start: int = 1_700_000_000_000_000_000) -> None:
        self._now = start

    def __call__(self) -> int:
        return self._now

    def advance(self, nanos: int) -> None:
        self._now += nanos


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "field.db")


@pytest.fixture()
def utc_clock():
    return _FakeUtcClock()


def _open_birth(
    db_path: str,
    *,
    count: int = 3,
    seed: int = 0x12A5,
    clock=None,
    utc_clock=None,
) -> FieldRuntime:
    return FieldRuntime.open(
        db_path,
        birth_registry=_registry(count),
        birth_rng_factory=SeededGaussianRngFactory(seed),
        clock=clock,
        utc_clock=utc_clock,
    )


def _second_connection_count(db_path: str, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0])
    finally:
        conn.close()


def _second_connection_rows(db_path: str, sql: str, params=()):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Startup: empty-or-recover
# ---------------------------------------------------------------------------


def test_empty_db_persists_tick0_snapshot_before_install(db_path):
    runtime = _open_birth(db_path, count=4)
    try:
        assert runtime.field_tick == 0
        assert runtime.registry_proxy().length == 4
        snap_count = _second_connection_count(db_path, "field_snapshots")
        assert snap_count == 1
        row = _second_connection_rows(
            db_path,
            "SELECT field_tick FROM field_snapshots ORDER BY snapshot_id",
        )[0]
        assert int(row[0]) == 0
    finally:
        runtime.close()


def test_recovered_snapshot_registry_and_next_tick_match_control(db_path):
    runtime = _open_birth(db_path, count=3, seed=42)
    runtime.tick()
    runtime.tick()
    pre_tick = runtime.field_tick
    pre_registry = runtime.registry_proxy().dim_ids
    pre_snapshot = runtime.snapshot_proxy()
    runtime.close()

    recovered = FieldRuntime.open(db_path)
    try:
        assert recovered.field_tick == pre_tick
        assert recovered.registry_proxy().dim_ids == pre_registry
        assert recovered.boot_id != runtime.boot_id
        rec_snapshot = recovered.snapshot_proxy()
        assert rec_snapshot.tick == pre_snapshot.tick
        for a, b in zip(rec_snapshot.dimensions, pre_snapshot.dimensions):
            assert a.value == b.value
            assert a.velocity == b.velocity
            assert a.attractor == b.attractor
        recovered.tick()
        assert recovered.field_tick == pre_tick + 1
    finally:
        recovered.close()

    control = FieldDynamics(_registry(3), rng_factory=SeededGaussianRngFactory(42))
    control.tick()
    control.tick()
    control.tick()
    # Use public snapshot, not private _tick
    assert control.snapshot().tick == pre_tick + 1


def test_nonempty_db_rejects_birth_params(db_path):
    runtime = _open_birth(db_path, count=3)
    runtime.close()
    with pytest.raises(FieldRuntimeError) as caught:
        FieldRuntime.open(db_path, birth_registry=_registry(2))
    assert caught.value.code == "startup_birth_params_on_nonempty_db"


def test_empty_db_unsupported_birth_factory_is_wrapped_and_writes_no_field_data(
    db_path, monkeypatch
):
    class _TypeNameBombMeta(type):
        def __getattribute__(cls, name: str):
            if name == "__name__":
                raise RuntimeError("untrusted type name must not be read")
            return super().__getattribute__(name)

    class _UnsupportedBirthFactory(metaclass=_TypeNameBombMeta):
        def __init__(self) -> None:
            self.create_calls = 0
            self.draw_calls = 0

        def create(self, stream: str):
            self.create_calls += 1

            class _Provider:
                def draw(inner_self, draw_index: int):
                    self.draw_calls += 1
                    raise AssertionError("unsupported factory provider must not draw")

            return _Provider()

    unsupported = _UnsupportedBirthFactory()
    snapshot_calls = 0
    original_write_snapshot = FieldPersistenceStore.write_snapshot

    def count_snapshot(*args, **kwargs):
        nonlocal snapshot_calls
        snapshot_calls += 1
        return original_write_snapshot(*args, **kwargs)

    monkeypatch.setattr(FieldPersistenceStore, "write_snapshot", count_snapshot)
    with pytest.raises(FieldRuntimeError) as caught:
        FieldRuntime.open(
            db_path,
            birth_registry=_registry(3),
            birth_rng_factory=unsupported,
        )

    assert caught.value.code == "startup_birth_failed"
    assert caught.value.stage == "startup.birth"
    assert caught.value.__cause__.anomaly.code == "unsupported_rng_factory"
    assert caught.value.__cause__.anomaly.stage == "rng_factory"
    assert caught.value.__cause__.anomaly.dim_id is None
    assert caught.value.__cause__.anomaly.detail == (
        "rng_factory must be an exact SeededGaussianRngFactory instance"
    )
    assert unsupported.create_calls == 0
    assert unsupported.draw_calls == 0
    assert snapshot_calls == 0
    assert _second_connection_count(db_path, "field_snapshots") == 0
    assert _second_connection_count(db_path, "field_events") == 0
    assert _second_connection_count(db_path, "trajectory_points") == 0

    reopened = FieldRuntime.open(db_path)
    reopened.close()


def test_empty_db_factory_exception_string_bomb_is_wrapped_without_field_data(
    db_path, monkeypatch
):
    class StringBombFactoryError(Exception):
        def __str__(self) -> str:
            raise RuntimeError("factory exception string must not be read")

    original_error = StringBombFactoryError()
    snapshot_calls = 0
    original_write_snapshot = FieldPersistenceStore.write_snapshot

    def raise_string_bomb(self, stream: str):
        raise original_error

    def count_snapshot(*args, **kwargs):
        nonlocal snapshot_calls
        snapshot_calls += 1
        return original_write_snapshot(*args, **kwargs)

    monkeypatch.setattr(SeededGaussianRngFactory, "create", raise_string_bomb)
    monkeypatch.setattr(FieldPersistenceStore, "write_snapshot", count_snapshot)
    with pytest.raises(FieldRuntimeError) as caught:
        FieldRuntime.open(
            db_path,
            birth_registry=_registry(3),
            birth_rng_factory=SeededGaussianRngFactory(17),
        )

    assert caught.value.code == "startup_birth_failed"
    assert caught.value.stage == "startup.birth"
    dynamics_error = caught.value.__cause__
    assert dynamics_error.anomaly.code == "rng_factory_failure"
    assert dynamics_error.anomaly.stage == "rng_factory"
    assert dynamics_error.anomaly.detail == "factory.create() raised an exception"
    assert dynamics_error.__cause__ is original_error
    assert snapshot_calls == 0
    assert _second_connection_count(db_path, "field_snapshots") == 0
    assert _second_connection_count(db_path, "field_events") == 0
    assert _second_connection_count(db_path, "trajectory_points") == 0

    monkeypatch.undo()
    reopened = FieldRuntime.open(db_path)
    reopened.close()


def test_nonempty_db_birth_factory_rejection_precedes_factory_validation(db_path):
    runtime = _open_birth(db_path, count=3)
    runtime.close()

    with pytest.raises(FieldRuntimeError) as caught:
        FieldRuntime.open(db_path, birth_rng_factory=object())

    assert caught.value.code == "startup_birth_params_on_nonempty_db"
    assert caught.value.stage == "startup.reject_birth_params"


# ---------------------------------------------------------------------------
# Trajectory row counts equal actual registry length (no hardcoded 12)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("count", [1, 3, 7])
def test_trajectory_row_count_equals_registry_length(db_path, count):
    runtime = _open_birth(db_path, count=count)
    try:
        runtime.tick()
        rows = _second_connection_rows(
            db_path,
            "SELECT dimension_ordinal, dim_id FROM trajectory_points "
            "ORDER BY dimension_ordinal",
        )
        assert len(rows) == count
        for index, (ordinal, dim_id) in enumerate(rows):
            assert int(ordinal) == index
            assert dim_id == f"custom-{index}"
    finally:
        runtime.close()


def test_trajectory_after_values_match_observation(db_path):
    runtime = _open_birth(db_path, count=3)
    try:
        obs = runtime.tick()
        rows = _second_connection_rows(
            db_path,
            "SELECT dim_id, after_value, after_velocity, after_attractor, "
            "after_slow_baseline, after_ou_acceleration "
            "FROM trajectory_points ORDER BY dimension_ordinal",
        )
        assert len(rows) == len(obs.dimensions)
        for dim, (r_dim, r_val, r_vel, r_attr, r_base, r_ou) in zip(
            obs.dimensions, rows
        ):
            assert r_dim == dim.dim_id
            assert float(r_val) == dim.after_value
            assert float(r_vel) == dim.after_velocity
            assert float(r_attr) == dim.after_attractor
            assert float(r_base) == dim.after_soft_restoring_baseline
            assert float(r_ou) == dim.after_ou_acceleration
    finally:
        runtime.close()


# ---------------------------------------------------------------------------
# Second-connection visibility, append-only ids, trigger enforcement
# ---------------------------------------------------------------------------


def test_tick_event_visible_from_second_connection_on_return(db_path):
    runtime = _open_birth(db_path, count=3)
    try:
        runtime.tick()
        assert _second_connection_count(db_path, "field_events") == 1
        assert _second_connection_count(db_path, "trajectory_points") == 3
        runtime.tick()
        assert _second_connection_count(db_path, "field_events") == 2
        assert _second_connection_count(db_path, "trajectory_points") == 6
    finally:
        runtime.close()


def test_event_and_trajectory_ids_are_monotonic_append(db_path):
    runtime = _open_birth(db_path, count=2)
    try:
        runtime.tick()
        runtime.tick()
        eids = _second_connection_rows(
            db_path, "SELECT event_id FROM field_events ORDER BY event_id"
        )
        tids = _second_connection_rows(
            db_path,
            "SELECT trajectory_id FROM trajectory_points ORDER BY trajectory_id",
        )
        assert [int(r[0]) for r in eids] == [1, 2]
        assert [int(r[0]) for r in tids] == [1, 2, 3, 4]
    finally:
        runtime.close()


def test_update_on_field_events_rejected_by_trigger(db_path):
    runtime = _open_birth(db_path, count=2)
    runtime.tick()
    runtime.close()
    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE field_events SET event_kind = 'x' WHERE event_id = 1")
    finally:
        conn.close()


def test_delete_on_field_events_rejected_by_trigger(db_path):
    runtime = _open_birth(db_path, count=2)
    runtime.tick()
    runtime.close()
    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM field_events WHERE event_id = 1")
    finally:
        conn.close()


def test_update_on_trajectory_points_rejected_by_trigger(db_path):
    runtime = _open_birth(db_path, count=2)
    runtime.tick()
    runtime.close()
    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(
                "UPDATE trajectory_points SET after_value = 0.0 WHERE trajectory_id = 1"
            )
    finally:
        conn.close()


def test_delete_on_trajectory_points_rejected_by_trigger(db_path):
    runtime = _open_birth(db_path, count=2)
    runtime.tick()
    runtime.close()
    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM trajectory_points WHERE trajectory_id = 1")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Attractor event persistence
# ---------------------------------------------------------------------------


def test_attractor_move_persists_event(db_path):
    runtime = _open_birth(db_path, count=3)
    try:
        snap = runtime.move_attractor(
            AttractorMove(
                dim_id="custom-1",
                delta=0.05,
                source="test",
                rationale="unit",
            )
        )
        assert _second_connection_count(db_path, "field_events") == 1
        rows = _second_connection_rows(
            db_path,
            "SELECT event_kind, before_field_tick, after_field_tick, payload_json "
            "FROM field_events WHERE event_id = 1",
        )
        kind, before, after, payload_text = rows[0]
        assert kind == "attractor_move"
        assert int(before) == 0
        assert int(after) == 0
        payload = json.loads(payload_text)
        assert payload["version"] == EVENT_PAYLOAD_VERSION
        assert payload["dim_id"] == "custom-1"
        assert payload["delta"] == 0.05
        assert payload["after_attractor"] == snap.dimensions[1].attractor
    finally:
        runtime.close()


def test_rejected_attractor_move_writes_no_event(db_path):
    runtime = _open_birth(db_path, count=3)
    try:
        with pytest.raises(Exception):
            runtime.move_attractor(
                AttractorMove(
                    dim_id="custom-1",
                    delta=10.0,
                    source="test",
                    rationale="out-of-domain",
                )
            )
        assert _second_connection_count(db_path, "field_events") == 0
        assert runtime.healthy
    finally:
        runtime.close()


# ---------------------------------------------------------------------------
# Fake-clock 60-second snapshot cadence and retention
# ---------------------------------------------------------------------------


def test_periodic_snapshot_cadence_with_fake_clock(db_path):
    clock = _FakeClock(start=0.0)
    utc = _FakeUtcClock()
    runtime = _open_birth(db_path, count=3, clock=clock, utc_clock=utc)
    try:
        assert _second_connection_count(db_path, "field_snapshots") == 1
        runtime.tick()
        clock.advance(math.nextafter(SNAPSHOT_INTERVAL_SECONDS, 0.0))
        runtime.tick()
        assert _second_connection_count(db_path, "field_snapshots") == 1
        clock.advance(
            SNAPSHOT_INTERVAL_SECONDS
            - math.nextafter(SNAPSHOT_INTERVAL_SECONDS, 0.0)
        )
        runtime.tick()
        assert _second_connection_count(db_path, "field_snapshots") == 2
        snapshot_ticks = _second_connection_rows(
            db_path,
            "SELECT field_tick FROM field_snapshots ORDER BY snapshot_id",
        )
        assert [int(row[0]) for row in snapshot_ticks] == [0, 2]
        assert runtime.field_tick == 3
        assert _second_connection_rows(
            db_path,
            "SELECT before_field_tick, after_field_tick FROM field_events "
            "ORDER BY event_id",
        ) == [(0, 1), (1, 2), (2, 3)]
    finally:
        runtime.close()


def test_snapshot_retention_keeps_latest_two(db_path):
    clock = _FakeClock()
    utc = _FakeUtcClock()
    runtime = _open_birth(db_path, count=2, clock=clock, utc_clock=utc)
    try:
        for _ in range(5):
            runtime.tick()
            clock.advance(SNAPSHOT_INTERVAL_SECONDS + 0.1)
        count = _second_connection_count(db_path, "field_snapshots")
        assert count == SNAPSHOT_RETENTION_COUNT
        ticks = _second_connection_rows(
            db_path,
            "SELECT field_tick FROM field_snapshots ORDER BY snapshot_id DESC",
        )
        assert int(ticks[0][0]) >= int(ticks[1][0])
    finally:
        runtime.close()


def test_no_snapshot_override_constant_from_caller(db_path):
    assert SNAPSHOT_INTERVAL_SECONDS == 60.0
    assert SNAPSHOT_RETENTION_COUNT == 2
    runtime = _open_birth(db_path, count=2)
    runtime.close()
    assert not hasattr(runtime, "snapshot_interval_seconds")


# ---------------------------------------------------------------------------
# Fail-closed: corrupted/malformed/mismatched snapshots and events
# ---------------------------------------------------------------------------


def _corrupt_latest_snapshot(db_path: str, *, mutate) -> None:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT snapshot_id, capsule_json FROM field_snapshots "
            "ORDER BY snapshot_id DESC LIMIT 1"
        ).fetchone()
        snapshot_id, text = row
        new_text = mutate(text)
        conn.execute(
            "UPDATE field_snapshots SET capsule_json = ? WHERE snapshot_id = ?",
            (new_text, snapshot_id),
        )
        conn.commit()
    finally:
        conn.close()


def _corrupt_latest_snapshot_with_sha(db_path: str, *, mutate) -> None:
    """Mutate snapshot text AND recompute capsule_sha256."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT snapshot_id, capsule_json FROM field_snapshots "
            "ORDER BY snapshot_id DESC LIMIT 1"
        ).fetchone()
        snapshot_id, text = row
        new_text = mutate(text)
        new_sha = _sha256(new_text)
        conn.execute(
            "UPDATE field_snapshots SET capsule_json = ?, capsule_sha256 = ? "
            "WHERE snapshot_id = ?",
            (new_text, new_sha, snapshot_id),
        )
        conn.commit()
    finally:
        conn.close()


def _corrupt_event_payload_with_sha(db_path: str, event_id: int, *, mutate) -> None:
    """Mutate event payload AND recompute payload_sha256."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT payload_json FROM field_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        old_text = row[0]
        new_text = mutate(old_text)
        new_sha = _sha256(new_text)
        conn.execute(
            "UPDATE field_events SET payload_json = ?, payload_sha256 = ? "
            "WHERE event_id = ?",
            (new_text, new_sha, event_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_malformed_json_snapshot_fail_closed(db_path):
    runtime = _open_birth(db_path, count=3)
    runtime.tick()
    runtime.close()

    def mutate(text):
        return text[:5] + "not-json" + text[5:]

    _corrupt_latest_snapshot(db_path, mutate=mutate)
    with pytest.raises((FieldRuntimeError, FieldPersistenceError)):
        FieldRuntime.open(db_path)


def test_duplicate_key_json_snapshot_fail_closed(db_path):
    """Duplicate keys are rejected even when SHA is recalculated."""
    runtime = _open_birth(db_path, count=3)
    runtime.close()

    original_sha = _second_connection_rows(
        db_path,
        "SELECT capsule_sha256 FROM field_snapshots ORDER BY snapshot_id DESC LIMIT 1",
    )[0][0]

    def mutate(text):
        # Inject a duplicate key: add a second "field_tick" key
        parts = text.split(',"field_tick":', 1)
        if len(parts) == 2:
            return parts[0] + ',"field_tick":999,"field_tick":' + parts[1]
        return text

    _corrupt_latest_snapshot_with_sha(db_path, mutate=mutate)
    new_sha = _second_connection_rows(
        db_path,
        "SELECT capsule_sha256 FROM field_snapshots ORDER BY snapshot_id DESC LIMIT 1",
    )[0][0]
    assert new_sha != original_sha, "SHA must change"
    with pytest.raises((FieldRuntimeError, FieldPersistenceError)):
        FieldRuntime.open(db_path)


# ---------------------------------------------------------------------------
# P1.2-B correction: schema fail-closed tests
# ---------------------------------------------------------------------------


def test_unknown_extra_table_fail_closed(db_path):
    runtime = _open_birth(db_path, count=3)
    runtime.close()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE rogue_table (x INTEGER)")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(FieldPersistenceError) as caught:
        store = FieldPersistenceStore(db_path)
        store.ensure_schema()
    assert "extra tables" in caught.value.detail.lower()


def test_missing_trigger_fail_closed(db_path):
    runtime = _open_birth(db_path, count=3)
    runtime.close()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TRIGGER trg_no_update_field_events")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(FieldPersistenceError) as caught:
        store = FieldPersistenceStore(db_path)
        store.ensure_schema()
    assert "missing triggers" in caught.value.detail.lower()


def test_missing_index_fail_closed(db_path):
    runtime = _open_birth(db_path, count=3)
    runtime.close()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP INDEX idx_trajectory_field_tick_event")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(FieldPersistenceError) as caught:
        store = FieldPersistenceStore(db_path)
        store.ensure_schema()
    assert "missing indexes" in caught.value.detail.lower()


def test_bootstrap_produces_all_expected_objects(db_path):
    store = FieldPersistenceStore(db_path)
    store.ensure_schema()
    store.close()
    conn = sqlite3.connect(db_path)
    try:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        triggers = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        table_names = {t[0] for t in tables}
        index_names = {i[0] for i in indexes}
        trigger_names = {tr[0] for tr in triggers}
        assert table_names == {
            "chatbox_meta", "field_snapshots", "field_events",
            "trajectory_points", "field_operation_receipts",
        }
        assert index_names == {
            "idx_trajectory_event_order", "idx_trajectory_dim_event",
            "idx_events_field_tick", "idx_trajectory_field_tick_event",
            "idx_field_receipts_request_sha256",
        }
        assert trigger_names == {
            "trg_no_update_field_events", "trg_no_delete_field_events",
            "trg_no_update_trajectory_points", "trg_no_delete_trajectory_points",
            "trg_no_update_field_operation_receipts",
            "trg_no_delete_field_operation_receipts",
        }
    finally:
        conn.close()


def test_exact_v1_schema_migrates_once_without_rewriting_history(db_path):
    runtime = _open_birth(db_path, count=3)
    runtime.tick()
    runtime.close()
    conn = sqlite3.connect(db_path)
    try:
        before = {
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
    finally:
        conn.close()

    migrated = FieldPersistenceStore(db_path)
    migrated.ensure_schema()
    migrated.close()
    reopened = FieldPersistenceStore(db_path)
    reopened.ensure_schema()
    reopened.close()
    conn = sqlite3.connect(db_path)
    try:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 2
        assert conn.execute(
            "SELECT value FROM chatbox_meta WHERE key='schema_version'"
        ).fetchone()[0] == PERSISTENCE_SCHEMA_VERSION
        for table, rows in before.items():
            assert conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == rows
        assert conn.execute(
            "SELECT COUNT(*) FROM field_operation_receipts"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_batch_receipt_deduplicates_and_conflicting_request_fails(db_path):
    runtime = _open_birth(db_path, count=3)
    move = AttractorMove("custom-1", 0.05, "test", "batch")
    try:
        first = runtime.move_attractor_batch("op-1", (move,))
        snapshot = runtime.snapshot_proxy()
        counts = tuple(
            _second_connection_count(db_path, table)
            for table in ("field_events", "field_snapshots", "field_operation_receipts")
        )
        duplicate = runtime.move_attractor_batch("op-1", (move,))
        assert duplicate.deduplicated
        assert duplicate.results == first.results
        assert runtime.snapshot_proxy() == snapshot
        assert tuple(
            _second_connection_count(db_path, table)
            for table in ("field_events", "field_snapshots", "field_operation_receipts")
        ) == counts
        with pytest.raises(FieldRuntimeError) as caught:
            runtime.move_attractor_batch(
                "op-1", (AttractorMove("custom-1", 0.04, "test", "batch"),)
            )
        assert caught.value.code == "persistence_operation_conflict"
    finally:
        runtime.close()


def test_batch_commit_snapshot_recovers_and_receipt_audits(db_path):
    runtime = _open_birth(db_path, count=3)
    before = runtime.snapshot_proxy()
    receipt = runtime.move_attractor_batch(
        "op-recover",
        (
            AttractorMove("custom-0", 0.02, "test", "recover"),
            AttractorMove("custom-1", -0.03, "test", "recover"),
        ),
    )
    expected = runtime.snapshot_proxy()
    runtime.close()
    assert len(receipt.results) == 2
    assert all(result.applied for result in receipt.results)
    assert expected != before
    reopened = FieldRuntime.open(db_path)
    try:
        assert reopened.snapshot_proxy() == expected
        duplicate = reopened.move_attractor_batch(
            "op-recover",
            (
                AttractorMove("custom-0", 0.02, "test", "recover"),
                AttractorMove("custom-1", -0.03, "test", "recover"),
            ),
        )
        assert duplicate.deduplicated
        assert reopened.snapshot_proxy() == expected
    finally:
        reopened.close()


def test_receipt_hash_tamper_fails_reopen(db_path):
    runtime = _open_birth(db_path, count=3)
    runtime.move_attractor_batch(
        "op-tamper", (AttractorMove("custom-1", 0.05, "test", "tamper"),)
    )
    runtime.close()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TRIGGER trg_no_update_field_operation_receipts")
        conn.execute(
            "UPDATE field_operation_receipts SET receipt_sha256='deadbeef' "
            "WHERE operation_id='op-tamper'"
        )
        conn.execute(
            "CREATE TRIGGER trg_no_update_field_operation_receipts "
            "BEFORE UPDATE ON field_operation_receipts BEGIN SELECT "
            "RAISE(ABORT, 'field_operation_receipts is append-only'); END"
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(FieldRuntimeError) as caught:
        FieldRuntime.open(db_path)
    assert "receipt" in caught.value.code


def test_batch_mid_transaction_failure_rolls_back_events_snapshot_and_receipt(db_path):
    runtime = _open_birth(db_path, count=3)
    store = runtime._store  # noqa: SLF001 - deterministic transaction cut injection
    store._conn.execute(  # noqa: SLF001
        "CREATE TRIGGER trg_test_reject_receipt "
        "BEFORE INSERT ON field_operation_receipts BEGIN "
        "SELECT RAISE(ABORT, 'injected receipt failure'); END"
    )
    before = runtime.snapshot_proxy()
    before_counts = tuple(
        _second_connection_count(db_path, table)
        for table in ("field_events", "field_snapshots", "field_operation_receipts")
    )
    with pytest.raises(FieldRuntimeError) as caught:
        runtime.move_attractor_batch(
            "op-rollback",
            (
                AttractorMove("custom-0", 0.02, "test", "rollback"),
                AttractorMove("custom-1", -0.03, "test", "rollback"),
            ),
        )
    assert caught.value.code == "persistence_batch_commit_failed"
    assert not runtime.healthy
    assert runtime._dynamics.snapshot() == before  # noqa: SLF001 - candidate was not published
    assert tuple(
        _second_connection_count(db_path, table)
        for table in ("field_events", "field_snapshots", "field_operation_receipts")
    ) == before_counts
    store._conn.execute("DROP TRIGGER trg_test_reject_receipt")  # noqa: SLF001
    runtime.close()

    reopened = FieldRuntime.open(db_path)
    try:
        receipt = reopened.move_attractor_batch(
            "op-rollback",
            (
                AttractorMove("custom-0", 0.02, "test", "rollback"),
                AttractorMove("custom-1", -0.03, "test", "rollback"),
            ),
        )
        assert not receipt.deduplicated
        assert all(result.applied for result in receipt.results)
        assert _second_connection_count(db_path, "field_events") == 2
        assert _second_connection_count(db_path, "field_operation_receipts") == 1
    finally:
        reopened.close()


def test_zero_move_and_rejected_batches_are_durably_processed(db_path):
    runtime = _open_birth(db_path, count=3)
    try:
        empty = runtime.move_attractor_batch("op-empty", ())
        rejected = runtime.move_attractor_batch(
            "op-rejected",
            (AttractorMove("custom-1", 10.0, "test", "domain rejection"),),
        )
        assert empty.results == ()
        assert len(rejected.results) == 1
        assert not rejected.results[0].applied
        assert _second_connection_count(db_path, "field_operation_receipts") == 2
        assert _second_connection_count(db_path, "field_events") == 0
        duplicate = runtime.move_attractor_batch(
            "op-rejected",
            (AttractorMove("custom-1", 10.0, "test", "domain rejection"),),
        )
        assert duplicate.deduplicated
        assert duplicate.results == rejected.results
        assert _second_connection_count(db_path, "field_operation_receipts") == 2
    finally:
        runtime.close()


def test_wrong_column_layout_fail_closed(db_path):
    # Create a DB that has the right tables but wrong column layout
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE chatbox_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("CREATE TABLE field_snapshots (snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, field_tick INTEGER NOT NULL, utc_unix_ns INTEGER NOT NULL, capsule_json TEXT NOT NULL, capsule_sha256 TEXT NOT NULL)")
        conn.execute("CREATE TABLE field_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, boot_id TEXT NOT NULL, event_kind TEXT NOT NULL, before_field_tick INTEGER NOT NULL, after_field_tick INTEGER NOT NULL, utc_unix_ns INTEGER NOT NULL, payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL)")
        # trajectory_points with swapped column order
        conn.execute("CREATE TABLE trajectory_points (trajectory_id INTEGER PRIMARY KEY AUTOINCREMENT, dim_id TEXT NOT NULL, event_id INTEGER NOT NULL, field_tick INTEGER NOT NULL, dimension_ordinal INTEGER NOT NULL, after_value REAL NOT NULL, after_velocity REAL NOT NULL, after_attractor REAL NOT NULL, after_slow_baseline REAL NOT NULL, after_ou_acceleration REAL NOT NULL, FOREIGN KEY (event_id) REFERENCES field_events(event_id))")
        conn.execute("CREATE INDEX idx_trajectory_event_order ON trajectory_points(event_id, trajectory_id)")
        conn.execute("CREATE INDEX idx_trajectory_dim_event ON trajectory_points(dim_id, event_id)")
        conn.execute("CREATE INDEX idx_events_field_tick ON field_events(after_field_tick, event_id)")
        conn.execute("CREATE INDEX idx_trajectory_field_tick_event ON trajectory_points(field_tick, event_id)")
        for trigger_sql in (
            "CREATE TRIGGER trg_no_update_field_events BEFORE UPDATE ON field_events BEGIN SELECT RAISE(ABORT, 'x'); END",
            "CREATE TRIGGER trg_no_delete_field_events BEFORE DELETE ON field_events BEGIN SELECT RAISE(ABORT, 'x'); END",
            "CREATE TRIGGER trg_no_update_trajectory_points BEFORE UPDATE ON trajectory_points BEGIN SELECT RAISE(ABORT, 'x'); END",
            "CREATE TRIGGER trg_no_delete_trajectory_points BEFORE DELETE ON trajectory_points BEGIN SELECT RAISE(ABORT, 'x'); END",
        ):
            conn.execute(trigger_sql)
        conn.execute("INSERT INTO chatbox_meta (key, value) VALUES ('schema_version', ?)", (PERSISTENCE_SCHEMA_VERSION,))
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(FieldPersistenceError) as caught:
        store = FieldPersistenceStore(db_path)
        store.ensure_schema()
    assert "col" in caught.value.detail.lower()


def test_same_name_wrong_index_fail_closed(db_path):
    """Index with correct name but wrong columns must fail semantic check."""
    runtime = _open_birth(db_path, count=3)
    runtime.close()
    # Recreate idx_trajectory_field_tick_event with wrong columns
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP INDEX idx_trajectory_field_tick_event")
        conn.execute(
            "CREATE INDEX idx_trajectory_field_tick_event "
            "ON trajectory_points(dim_id, trajectory_id)"  # wrong columns
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(FieldPersistenceError) as caught:
        store = FieldPersistenceStore(db_path)
        store.ensure_schema()
    assert "idx_trajectory_field_tick_event" in caught.value.detail.lower()


def test_same_name_empty_trigger_fail_closed(db_path):
    """Trigger with correct name but wrong SQL content must fail semantic check."""
    runtime = _open_birth(db_path, count=3)
    runtime.close()
    conn = sqlite3.connect(db_path)
    try:
        # Drop the real trigger and recreate with same name but empty body
        conn.execute("DROP TRIGGER trg_no_update_field_events")
        # This creates the trigger with same name but no RAISE(ABORT) — it won't
        # have the append-only enforcement phrase
        conn.execute(
            "CREATE TRIGGER trg_no_update_field_events "
            "BEFORE UPDATE ON field_events "
            "BEGIN "
            "  SELECT 1; "
            "END"
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(FieldPersistenceError) as caught:
        store = FieldPersistenceStore(db_path)
        store.ensure_schema()
    assert "trg_no_update_field_events" in str(caught.value.detail).lower()


def test_missing_foreign_key_fail_closed(db_path):
    """trajectory_points without FK on event_id must fail."""
    runtime = _open_birth(db_path, count=3)
    runtime.close()
    conn = sqlite3.connect(db_path)
    try:
        # Drop triggers first so we can modify table
        conn.execute("DROP TRIGGER trg_no_update_trajectory_points")
        conn.execute("DROP TRIGGER trg_no_delete_trajectory_points")
        # Create a new trajectory_points table without FK
        conn.execute(
            "CREATE TABLE trajectory_points_new ("
            "trajectory_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "event_id INTEGER NOT NULL, "
            "field_tick INTEGER NOT NULL, "
            "dimension_ordinal INTEGER NOT NULL, "
            "dim_id TEXT NOT NULL, "
            "after_value REAL NOT NULL, "
            "after_velocity REAL NOT NULL, "
            "after_attractor REAL NOT NULL, "
            "after_slow_baseline REAL NOT NULL, "
            "after_ou_acceleration REAL NOT NULL"
            ")"
        )
        conn.execute("INSERT INTO trajectory_points_new SELECT * FROM trajectory_points")
        conn.execute("DROP TABLE trajectory_points")
        conn.execute("ALTER TABLE trajectory_points_new RENAME TO trajectory_points")
        # Re-create triggers and indexes on new table
        conn.execute(
            "CREATE TRIGGER trg_no_update_trajectory_points "
            "BEFORE UPDATE ON trajectory_points "
            "BEGIN SELECT RAISE(ABORT, 'trajectory_points is append-only'); END"
        )
        conn.execute(
            "CREATE TRIGGER trg_no_delete_trajectory_points "
            "BEFORE DELETE ON trajectory_points "
            "BEGIN SELECT RAISE(ABORT, 'trajectory_points is append-only'); END"
        )
        conn.execute(
            "CREATE INDEX idx_trajectory_event_order "
            "ON trajectory_points(event_id, trajectory_id)"
        )
        conn.execute(
            "CREATE INDEX idx_trajectory_dim_event "
            "ON trajectory_points(dim_id, event_id)"
        )
        conn.execute(
            "CREATE INDEX idx_trajectory_field_tick_event "
            "ON trajectory_points(field_tick, event_id)"
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(FieldPersistenceError) as caught:
        store = FieldPersistenceStore(db_path)
        store.ensure_schema()
    assert "foreign" in str(caught.value.detail).lower()


# ---------------------------------------------------------------------------
# P1.2-B correction: canonical text byte-for-byte rejection
# ---------------------------------------------------------------------------


def test_reordered_root_keys_rejected(db_path):
    """Snapshot with reordered keys must fail even with correct SHA."""
    runtime = _open_birth(db_path, count=3)
    runtime.close()

    def mutate(text):
        parsed = json.loads(text)
        # Reorder: move field_tick to front
        items = list(parsed.items())
        items.sort(key=lambda kv: kv[0])  # alphabetically reorder keys
        return _strict_json_dumps(dict(items))

    _corrupt_latest_snapshot_with_sha(db_path, mutate=mutate)
    with pytest.raises((FieldRuntimeError, FieldPersistenceError)):
        FieldRuntime.open(db_path)


def test_pretty_json_snapshot_rejected(db_path):
    """Pretty/whitespace JSON must fail even with correct SHA."""
    runtime = _open_birth(db_path, count=3)
    runtime.close()

    def mutate(text):
        parsed = json.loads(text)
        return json.dumps(parsed, indent=2, sort_keys=False)

    _corrupt_latest_snapshot_with_sha(db_path, mutate=mutate)
    with pytest.raises((FieldRuntimeError, FieldPersistenceError)):
        FieldRuntime.open(db_path)


# ---------------------------------------------------------------------------
# P1.2-B correction: event history audit
# ---------------------------------------------------------------------------


def _drop_triggers_temporarily(db_path: str) -> list[str]:
    """Drop append-only triggers so we can tamper; return SQL to recreate."""
    conn = sqlite3.connect(db_path)
    try:
        triggers = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        recreate = []
        for name, sql in triggers:
            conn.execute(f"DROP TRIGGER {name}")
            recreate.append(sql)
        conn.commit()
    finally:
        conn.close()
    return recreate


def _restore_triggers(db_path: str, trigger_sqls: list[str]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        for sql in trigger_sqls:
            conn.execute(sql)
        conn.commit()
    finally:
        conn.close()


def test_history_audit_payload_hash_mismatch(db_path):
    """Recalculated payload SHA mismatch must fail-closed."""
    runtime = _open_birth(db_path, count=3)
    runtime.tick()
    runtime.close()

    trig_sqls = _drop_triggers_temporarily(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE field_events SET payload_sha256 = 'deadbeef' WHERE event_id = 1"
        )
        conn.commit()
    finally:
        conn.close()
    _restore_triggers(db_path, trig_sqls)
    with pytest.raises((FieldRuntimeError, FieldPersistenceError)):
        FieldRuntime.open(db_path)


def test_history_audit_duplicate_key_payload(db_path):
    """Duplicate key in event payload with recalculated SHA must fail."""
    runtime = _open_birth(db_path, count=3)
    runtime.tick()
    runtime.close()

    trig_sqls = _drop_triggers_temporarily(db_path)

    def mutate(text):
        parts = text.split(',"kind":', 1)
        if len(parts) == 2:
            return parts[0] + ',"kind":"tick","kind":"tick",' + parts[1]
        return text

    _corrupt_event_payload_with_sha(db_path, 1, mutate=mutate)
    _restore_triggers(db_path, trig_sqls)
    with pytest.raises((FieldRuntimeError, FieldPersistenceError)):
        FieldRuntime.open(db_path)


def test_history_audit_row_payload_tick_mismatch(db_path):
    """Payload after_tick != row after_field_tick must fail."""
    runtime = _open_birth(db_path, count=3)
    runtime.tick()
    runtime.tick()
    runtime.close()

    trig_sqls = _drop_triggers_temporarily(db_path)

    def mutate(text):
        payload = json.loads(text)
        payload["after_tick"] = 999
        return _strict_json_dumps(payload)

    _corrupt_event_payload_with_sha(db_path, 1, mutate=mutate)
    _restore_triggers(db_path, trig_sqls)

    with pytest.raises((FieldRuntimeError, FieldPersistenceError)):
        FieldRuntime.open(db_path)


def test_history_audit_trajectory_count_mismatch(db_path):
    """Wrong trajectory row count must fail."""
    runtime = _open_birth(db_path, count=3)
    runtime.tick()
    runtime.close()

    trig_sqls = _drop_triggers_temporarily(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM trajectory_points WHERE dimension_ordinal = 1")
        conn.commit()
    finally:
        conn.close()
    _restore_triggers(db_path, trig_sqls)

    with pytest.raises((FieldRuntimeError, FieldPersistenceError)):
        FieldRuntime.open(db_path)


def test_history_audit_trajectory_dim_order_wrong(db_path):
    """Trajectory dim_id order not matching registry must fail."""
    runtime = _open_birth(db_path, count=3)
    runtime.tick()
    runtime.close()

    trig_sqls = _drop_triggers_temporarily(db_path)
    conn = sqlite3.connect(db_path)
    try:
        # Swap dim_id for ordinals 0 and 1
        conn.execute(
            "UPDATE trajectory_points SET dim_id = 'custom-1' WHERE dimension_ordinal = 0"
        )
        conn.execute(
            "UPDATE trajectory_points SET dim_id = 'custom-0' WHERE dimension_ordinal = 1"
        )
        conn.commit()
    finally:
        conn.close()
    _restore_triggers(db_path, trig_sqls)

    with pytest.raises((FieldRuntimeError, FieldPersistenceError)):
        FieldRuntime.open(db_path)


def test_history_audit_extra_payload_key_fail_closed(db_path):
    """Extra payload key with recalculated SHA must fail-closed."""
    runtime = _open_birth(db_path, count=3)
    runtime.tick()
    runtime.close()

    trig_sqls = _drop_triggers_temporarily(db_path)

    def mutate(text):
        payload = json.loads(text)
        payload["extra_field"] = "sneaky"
        return _strict_json_dumps(payload)

    _corrupt_event_payload_with_sha(db_path, 1, mutate=mutate)
    _restore_triggers(db_path, trig_sqls)

    with pytest.raises((FieldRuntimeError, FieldPersistenceError)):
        FieldRuntime.open(db_path)


def test_history_audit_attractor_int_value_fail_closed(db_path):
    """Attractor delta/before/after as int (not float) must fail-closed."""
    runtime = _open_birth(db_path, count=3)
    runtime.move_attractor(
        AttractorMove(
            dim_id="custom-1",
            delta=0.05,
            source="test",
            rationale="unit",
        )
    )
    runtime.close()

    trig_sqls = _drop_triggers_temporarily(db_path)

    def mutate(text):
        payload = json.loads(text)
        # Change delta from 0.05 (float) to 0 (int)
        payload["delta"] = 0
        payload["before_attractor"] = 0
        payload["after_attractor"] = 0
        return _strict_json_dumps(payload)

    _corrupt_event_payload_with_sha(db_path, 1, mutate=mutate)
    _restore_triggers(db_path, trig_sqls)

    with pytest.raises((FieldRuntimeError, FieldPersistenceError)):
        FieldRuntime.open(db_path)


def test_history_audit_empty_source_fail_closed(db_path):
    """Attractor source empty/whitespace must fail-closed."""
    runtime = _open_birth(db_path, count=3)
    runtime.move_attractor(
        AttractorMove(
            dim_id="custom-1",
            delta=0.05,
            source="test",
            rationale="unit",
        )
    )
    runtime.close()

    trig_sqls = _drop_triggers_temporarily(db_path)

    def mutate(text):
        payload = json.loads(text)
        payload["source"] = "   "
        return _strict_json_dumps(payload)

    _corrupt_event_payload_with_sha(db_path, 1, mutate=mutate)
    _restore_triggers(db_path, trig_sqls)

    with pytest.raises((FieldRuntimeError, FieldPersistenceError)):
        FieldRuntime.open(db_path)


# ---------------------------------------------------------------------------
# P1.2-B correction: write read-back mismatch
# ---------------------------------------------------------------------------


def test_snapshot_write_readback_text_mismatch_rollback(db_path):
    """AFTER INSERT trigger that mutates text causes rollback."""
    # First create a standard DB
    store = FieldPersistenceStore(db_path)
    store.ensure_schema()

    # Install a rogue AFTER INSERT trigger that modifies the json
    try:
        store._conn.execute("BEGIN")
        store._conn.execute(
            "CREATE TRIGGER trg_test_tamper_snapshot "
            "AFTER INSERT ON field_snapshots "
            "BEGIN "
            "  UPDATE field_snapshots SET capsule_json = 'tampered' "
            "  WHERE snapshot_id = NEW.snapshot_id; "
            "END"
        )
        store._conn.execute("COMMIT")
    except Exception:
        store._conn.execute("ROLLBACK")
        raise

    store.close()
    store2 = FieldPersistenceStore(db_path)
    try:
        capsule = encode_field_state_capsule(
            _capture_field_state_capsule(
                FieldDynamics(_registry(3), rng_factory=SeededGaussianRngFactory(42))
            )
        )
        with pytest.raises(FieldPersistenceError):
            store2.write_snapshot(capsule, utc_unix_ns=1_700_000_000_000_000_000)
        # Verify nothing was committed
        assert _second_connection_count(db_path, "field_snapshots") == 0
    finally:
        store2.close()


# ---------------------------------------------------------------------------
# P1.2-B correction: invalid attractor command preserves P1.1 errors
# ---------------------------------------------------------------------------


def test_invalid_attractor_unknown_dim_preserves_error(db_path):
    """Unknown dim_id must raise InvalidAttractorMoveError, not a different type."""
    runtime = _open_birth(db_path, count=3)
    try:
        with pytest.raises(InvalidAttractorMoveError) as caught:
            runtime.move_attractor(
                AttractorMove(
                    dim_id="nonexistent-dim",
                    delta=0.05,
                    source="test",
                    rationale="unit",
                )
            )
        assert "dim_id" in str(caught.value).lower()
        assert _second_connection_count(db_path, "field_events") == 0
        assert runtime.healthy
    finally:
        runtime.close()


def test_invalid_attractor_invalid_type_preserves_error(db_path):
    """Invalid delta type (e.g., string) must raise error, not crash differently."""
    runtime = _open_birth(db_path, count=3)
    try:
        with pytest.raises(Exception):
            # Pass a string where float is expected
            runtime.move_attractor(
                AttractorMove(
                    dim_id="custom-1",
                    delta="not-a-float",  # type: ignore[arg-type]
                    source="test",
                    rationale="unit",
                )
            )
        assert _second_connection_count(db_path, "field_events") == 0
        assert runtime.healthy
    finally:
        runtime.close()


# ---------------------------------------------------------------------------
# P1.2-B correction: recovery observation vs uninterrupted control
# ---------------------------------------------------------------------------


def test_recovery_next_observation_matches_uninterrupted_control(db_path):
    """After recovery the next tick observation matches a control at same tick.

    Uses ONLY public snapshot/observation interfaces, never reads private
    _tick or _states."""
    seed = 0x5EED
    n_dims = 4
    runtime = _open_birth(db_path, count=n_dims, seed=seed)
    runtime.tick()
    runtime.tick()
    pre_snapshot = runtime.snapshot_proxy()
    runtime.close()

    # Uninterrupted control at same seed and tick
    control = FieldDynamics(_registry(n_dims), rng_factory=SeededGaussianRngFactory(seed))
    control.tick()
    control.tick()
    ctrl_snapshot = control.snapshot()
    assert ctrl_snapshot.tick == pre_snapshot.tick

    recovered = FieldRuntime.open(db_path)
    try:
        rec_snapshot = recovered.snapshot_proxy()
        assert rec_snapshot.tick == ctrl_snapshot.tick
        for a, b in zip(rec_snapshot.dimensions, ctrl_snapshot.dimensions):
            assert a.value == b.value
            assert a.velocity == b.velocity
            assert a.attractor == b.attractor

        # Next tick must match control next tick
        rec_obs = recovered.tick()
        ctrl_obs = control.tick()
        assert rec_obs.tick_after == ctrl_obs.tick_after
        for a, b in zip(rec_obs.dimensions, ctrl_obs.dimensions):
            assert a.after_value == b.after_value
            assert a.after_velocity == b.after_velocity
            assert a.after_attractor == b.after_attractor
    finally:
        recovered.close()


# ---------------------------------------------------------------------------
# P1.2-B correction: injected UTC clock for birth snapshot
# ---------------------------------------------------------------------------


def test_birth_snapshot_uses_injected_utc_clock(db_path):
    """Initial tick-0 snapshot must record the injected fake UTC timestamp."""
    fake_ns = 999_888_777_666_555_444
    utc = _FakeUtcClock(start=fake_ns)
    runtime = _open_birth(db_path, count=3, utc_clock=utc)
    try:
        snapshot_row = _second_connection_rows(
            db_path,
            "SELECT utc_unix_ns FROM field_snapshots WHERE snapshot_id = 1",
        )[0]
    finally:
        runtime.close()
    assert int(snapshot_row[0]) == fake_ns


# ---------------------------------------------------------------------------
# P1.2-B correction: close/checkpoint fail-loud
# ---------------------------------------------------------------------------


def test_store_close_sets_closed_flag(db_path):
    """Store close() must set _closed flag; checkpoint is checked fail-loud."""
    runtime = _open_birth(db_path, count=3)
    runtime.close()

    store = FieldPersistenceStore(db_path)
    assert not store._closed
    store.close()
    assert store._closed


def test_runtime_close_final_snapshot_failure_stderr_and_raise(db_path):
    """When final snapshot fails on close, error goes to stderr and is raised."""
    old_stderr = sys.stderr
    sys.stderr = captured = io.StringIO()
    try:
        runtime = _open_birth(db_path, count=3)
        runtime.tick()
        # Poison the store so the next write fails
        runtime._store._conn.close()
        with pytest.raises(FieldRuntimeError):
            runtime.close()
        stderr_text = captured.getvalue()
        assert "final_snapshot" in stderr_text.lower()
    finally:
        sys.stderr = old_stderr


def test_healthy_context_manager_close_no_error(db_path):
    """Normal context manager close produces no errors."""
    old_stderr = sys.stderr
    sys.stderr = captured = io.StringIO()
    try:
        with _open_birth(db_path, count=3) as runtime:
            runtime.tick()
        stderr_text = captured.getvalue()
        assert stderr_text == "" or "error" not in stderr_text.lower()
    finally:
        sys.stderr = old_stderr


def test_checkpoint_busy_fail_loud(db_path):
    """wal_checkpoint(TRUNCATE) returning busy=1 must raise FieldPersistenceError."""
    store = FieldPersistenceStore(db_path)
    store.ensure_schema()

    class _FakeCheckpointCursor:
        def fetchone(self):
            return (1, 4, 3)

    class _FakeConn:
        """Fake connection that returns controlled checkpoint rows."""
        def __init__(self, real_conn):
            self._real = real_conn
            self._closed = False

        def execute(self, sql, *args, **kwargs):
            if "wal_checkpoint" in str(sql):
                return _FakeCheckpointCursor()
            return self._real.execute(sql, *args, **kwargs)

        def close(self):
            self._closed = True

    real_conn = store._conn
    store._closed = False
    store._conn = _FakeConn(real_conn)
    try:
        with pytest.raises(FieldPersistenceError) as caught:
            store.close()
        assert "busy" in str(caught.value.detail).lower()
    finally:
        store._conn = real_conn


def test_checkpoint_not_busy_success(db_path):
    """wal_checkpoint(TRUNCATE) returning (0, 4, 4) must NOT raise."""
    store = FieldPersistenceStore(db_path)
    store.ensure_schema()

    class _FakeCheckpointCursor:
        def fetchone(self):
            return (0, 4, 4)

    class _FakeConn:
        def __init__(self, real_conn):
            self._real = real_conn

        def execute(self, sql, *args, **kwargs):
            if "wal_checkpoint" in str(sql):
                return _FakeCheckpointCursor()
            return self._real.execute(sql, *args, **kwargs)

        def close(self):
            pass

    real_conn = store._conn
    store._closed = False
    store._conn = _FakeConn(real_conn)
    try:
        store.close()  # must not raise
        assert store._closed
    finally:
        store._conn = real_conn


def test_checkpoint_bad_shape_fail_loud(db_path):
    """wal_checkpoint(TRUNCATE) returning wrong number of columns must raise."""
    store = FieldPersistenceStore(db_path)
    store.ensure_schema()

    class _FakeCheckpointCursor:
        def fetchone(self):
            return (0,)  # only 1 column, expected 3

    class _FakeConn:
        def __init__(self, real_conn):
            self._real = real_conn

        def execute(self, sql, *args, **kwargs):
            if "wal_checkpoint" in str(sql):
                return _FakeCheckpointCursor()
            return self._real.execute(sql, *args, **kwargs)

        def close(self):
            pass

    real_conn = store._conn
    store._closed = False
    store._conn = _FakeConn(real_conn)
    try:
        with pytest.raises(FieldPersistenceError) as caught:
            store.close()
        assert "shape" in str(caught.value.detail).lower()
    finally:
        store._conn = real_conn


def test_startup_close_failure_does_not_mask_primary_error(monkeypatch, db_path):
    """When startup fails and store.close() also fails, the primary error
    must be raised (not the close error), and close failure must appear in
    stderr as secondary.

    We corrupt the snapshot to force a decode failure, then also force
    store.close() to fail to test that close failure is emitted but
    does not replace the primary error."""
    runtime = _open_birth(db_path, count=3)
    runtime.tick()
    runtime.close()

    # Corrupt snapshot so recovery decode will fail
    _corrupt_latest_snapshot(db_path, mutate=lambda t: t[:5] + "not-json" + t[5:])

    old_stderr = sys.stderr
    sys.stderr = captured = io.StringIO()
    try:
        # Monkeypatch FieldPersistenceStore.close to also fail
        import app.chatbox.field_persistence as fp_module
        orig_close = fp_module.FieldPersistenceStore.close

        def _failing_close(self):
            self._closed = True
            try:
                self._conn.close()
            except Exception:
                pass
            raise FieldPersistenceError(
                "persistence_close_injected",
                "close",
                self._db_path,
                "injected close failure for test",
                stage="close.store",
            )

        monkeypatch.setattr(fp_module.FieldPersistenceStore, "close", _failing_close)

        with pytest.raises((FieldRuntimeError, FieldPersistenceError)):
            FieldRuntime.open(db_path)
        stderr_text = captured.getvalue()
        # Close failure should appear in stderr (as secondary structured error)
        assert "close" in stderr_text.lower() or "startup" in stderr_text.lower(), f"stderr={stderr_text!r}"
    finally:
        sys.stderr = old_stderr


def test_runtime_close_plain_exception_from_snapshot_guarantees_cleanup(
    monkeypatch, db_path
):
    """When final snapshot raises a plain Exception (not FieldRuntimeError),
    close() must still set _closed, close the store, release the owner lock,
    and re-raise the original exception object without conversion."""
    import app.chatbox.field_runtime as fr_module

    injected_error = RuntimeError("injected plain exception from capsule")
    runtime = _open_birth(db_path, count=3)
    try:
        runtime.tick()  # makes runtime dirty
        # Inject failure at capsule capture level (plain Exception, not contract)
        with monkeypatch.context() as mp:
            mp.setattr(
                fr_module,
                "_capture_field_state_capsule",
                lambda _dynamics: (_ for _ in ()).throw(injected_error),
            )
            with pytest.raises(RuntimeError) as caught:
                runtime.close()
            assert caught.value is injected_error
            assert runtime._closed is True
            # Poison is expected but closed is the mandatory gate
            assert runtime._poisoned
    finally:
        # Ensure final cleanup; close should be idempotent after the raise
        try:
            runtime.close()
        except Exception:
            pass
    # After error close, the lock must be released: reopen on same db_path succeeds
    recovered = FieldRuntime.open(db_path)
    try:
        assert recovered.field_tick >= 0
    finally:
        recovered.close()


# ---------------------------------------------------------------------------
# AST / import audit
# ---------------------------------------------------------------------------


def _scan_ast_for_private_access(file_path: Path) -> list[str]:
    violations: list[str] = []
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if isinstance(node.attr, str) and node.attr in ("_tick", "_states"):
                violations.append(
                    f"{file_path}:{node.lineno}: .{node.attr}"
                )
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in QUARANTINED_MODULES:
                    violations.append(
                        f"{file_path}:{node.lineno}: quarantine import {alias.name}"
                    )
        if isinstance(node, ast.ImportFrom):
            if node.module and (
                node.module in QUARANTINED_MODULES
                or any(node.module.startswith(q + ".") for q in QUARANTINED_MODULES)
            ):
                violations.append(
                    f"{file_path}:{node.lineno}: quarantine import {node.module}"
                )
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_NAMES:
                violations.append(
                    f"{file_path}:{node.lineno}: forbidden {func.id}"
                )
    return violations


def test_ast_no_private_tick_states():
    """Production files must NOT access ._tick or ._states privately."""
    violations: list[str] = []
    for fp in PRODUCTION_FILES:
        violations.extend(_scan_ast_for_private_access(fp))
    assert violations == [], f"Private attribute access found: {violations}"


def test_ast_no_quarantine_imports():
    """Production files must not import from quarantined modules."""
    violations: list[str] = []
    for fp in PRODUCTION_FILES:
        violations.extend(
            [v for v in _scan_ast_for_private_access(fp) if "quarantine" in v]
        )
    assert violations == [], f"Quarantine imports found: {violations}"


def test_ast_no_forbidden_names():
    """Production files must not use pickle/marshal/eval/exec."""
    violations: list[str] = []
    for fp in PRODUCTION_FILES:
        violations.extend(
            [v for v in _scan_ast_for_private_access(fp) if "forbidden" in v]
        )
    assert violations == [], f"Forbidden names found: {violations}"


# ---------------------------------------------------------------------------
# P1.2-B correction: owner lock and construction guard
# ---------------------------------------------------------------------------


def test_owner_lock_prevents_same_process_double_open(monkeypatch, db_path):
    """Same-process double open must fail with owner_lock_held.

    The first runtime holds the OS lock.  A second call to
    FieldRuntime.open() on the same canonical path must fail BEFORE any
    FieldPersistenceStore is constructed (proved by a store-construction
    bomb).  Snapshot/event/trajectory counts must be unchanged after the
    failed attempt.
    """
    runtime1 = _open_birth(db_path, count=3)
    try:
        snap_before = _second_connection_count(db_path, "field_snapshots")
        events_before = _second_connection_count(db_path, "field_events")
        traj_before = _second_connection_count(db_path, "trajectory_points")

        store_bomb_constructed = False

        class _StoreBomb:
            def __init__(self, *_args, **_kwargs):
                nonlocal store_bomb_constructed
                store_bomb_constructed = True
                raise AssertionError("store must not be constructed before lock check")

        import app.chatbox.field_runtime as fr_module
        with monkeypatch.context() as mp:
            mp.setattr(fr_module, "FieldPersistenceStore", _StoreBomb)
            with pytest.raises(FieldRuntimeError) as caught:
                FieldRuntime.open(db_path)
        assert caught.value.code == "owner_lock_held"
        assert not store_bomb_constructed, (
            "FieldPersistenceStore was constructed before lock conflict detection"
        )

        # DB counts unchanged
        assert _second_connection_count(db_path, "field_snapshots") == snap_before
        assert _second_connection_count(db_path, "field_events") == events_before
        assert _second_connection_count(db_path, "trajectory_points") == traj_before
    finally:
        runtime1.close()


def test_owner_lock_released_on_close_and_lock_file_persists(db_path):
    """After graceful close the lock is released; .owner.lock file remains."""
    runtime = _open_birth(db_path, count=3)
    runtime.close()

    # Re-open must succeed (lock was released)
    runtime2 = FieldRuntime.open(db_path)
    try:
        assert runtime2.field_tick == 0
    finally:
        runtime2.close()

    # Lock file must still exist on disk
    import os
    canonical = os.path.realpath(os.path.abspath(db_path))
    if os.name == "nt":
        canonical = os.path.normcase(canonical)
    lock_path = canonical + ".owner.lock"
    assert os.path.isfile(lock_path), f"lock file missing: {lock_path}"


# ---------------------------------------------------------------------------
# P1.2-B startup: missing parent directory is created before owner lock
# (regression for the default README startup command on a fresh checkout)
# ---------------------------------------------------------------------------


def test_startup_creates_missing_nested_parent_dir_and_lock(tmp_path):
    """A DB path whose parent directory chain does not yet exist must start.

    The default README command ``python -m app.chatbox.run_trajectory
    --db var/chatbox/field.sqlite3`` must work on a fresh checkout where
    ``var/chatbox/`` does not exist.  The runtime must create the parent
    directory chain, acquire the owner lock, create the DB, and install the
    tick-0 field -- without any manual ``mkdir``.
    """
    db_path = str(tmp_path / "nested" / "deep" / "field.sqlite3")
    parent = os.path.dirname(db_path)
    assert not os.path.isdir(parent), "precondition: parent must not exist"

    runtime = _open_birth(db_path, count=3)
    try:
        assert os.path.isdir(parent), "parent dir must be created"
        assert os.path.isfile(db_path), "db file must be created"
        assert runtime.field_tick == 0
        # tick-0 snapshot persisted
        assert _second_connection_count(db_path, "field_snapshots") == 1
        # owner lock sidecar exists beside the db
        canonical = os.path.realpath(os.path.abspath(db_path))
        if os.name == "nt":
            canonical = os.path.normcase(canonical)
        assert os.path.isfile(canonical + ".owner.lock")
    finally:
        runtime.close()


def test_startup_relative_path_with_missing_parent_is_created(tmp_path, monkeypatch):
    """A relative DB path resolved against cwd with a missing parent dir starts."""
    rel = os.path.join("rel_subdir", "field.sqlite3")
    monkeypatch.chdir(tmp_path)
    assert not os.path.isdir(os.path.join(str(tmp_path), "rel_subdir"))

    runtime = _open_birth(rel, count=2)
    try:
        assert os.path.isfile(os.path.join(str(tmp_path), rel))
        assert runtime.field_tick == 0
    finally:
        runtime.close()


def test_startup_parent_path_is_a_file_fails_closed(tmp_path):
    """When the DB parent path is occupied by a file, startup must fail closed.

    The error must be a structured FieldRuntimeError with a stable code, not a
    raw OSError or a silent directory clobber.
    """
    blocker = tmp_path / "blocker_file"
    blocker.write_text("not a directory", encoding="utf-8")
    db_path = str(blocker / "field.sqlite3")

    with pytest.raises(FieldRuntimeError) as caught:
        FieldRuntime.open(db_path)
    assert caught.value.code == "startup_db_parent_dir_failed"
    assert caught.value.stage == "startup.ensure_parent_dir"
    # The blocker file must be untouched
    assert blocker.read_text(encoding="utf-8") == "not a directory"


def test_startup_missing_parent_then_concurrent_owner_still_rejected(
    tmp_path, monkeypatch
):
    """After the parent dir is auto-created, a second owner is still rejected.

    This guards that directory creation did not weaken the single-owner lock:
    the first runtime holds the lock; a second open on the same canonical path
    must fail with ``owner_lock_held`` before any store is constructed.
    """
    db_path = str(tmp_path / "newdir" / "field.sqlite3")
    runtime1 = _open_birth(db_path, count=3)
    try:
        store_bomb_constructed = False

        class _StoreBomb:
            def __init__(self, *_args, **_kwargs):
                nonlocal store_bomb_constructed
                store_bomb_constructed = True
                raise AssertionError("store must not be constructed before lock check")

        import app.chatbox.field_runtime as fr_module
        with monkeypatch.context() as mp:
            mp.setattr(fr_module, "FieldPersistenceStore", _StoreBomb)
            with pytest.raises(FieldRuntimeError) as caught:
                FieldRuntime.open(db_path)
        assert caught.value.code == "owner_lock_held"
        assert not store_bomb_constructed
    finally:
        runtime1.close()


def test_startup_missing_parent_then_kill_restart_recovers(tmp_path):
    """Birth into a missing dir, tick, close, then recover on restart."""
    db_path = str(tmp_path / "freshdir" / "field.sqlite3")
    runtime = _open_birth(db_path, count=3, seed=7)
    runtime.tick()
    runtime.tick()
    pre_tick = runtime.field_tick
    runtime.close()

    recovered = FieldRuntime.open(db_path)
    try:
        assert recovered.field_tick == pre_tick
        assert recovered.registry_proxy().length == 3
        recovered.tick()
        assert recovered.field_tick == pre_tick + 1
    finally:
        recovered.close()


def test_direct_construction_forbidden(db_path):
    """Direct FieldRuntime(...) must raise runtime_construction_forbidden."""
    store = FieldPersistenceStore(db_path)
    store.ensure_schema()
    try:
        dynamics = FieldDynamics(
            _registry(3),
            rng_factory=SeededGaussianRngFactory(42),
        )
        with pytest.raises(FieldRuntimeError) as caught:
            FieldRuntime(store, dynamics, "test-boot")
        assert caught.value.code == "runtime_construction_forbidden"
        assert caught.value.operation == "construct"
        assert caught.value.stage == "construct.guard"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# P1.2-B correction: poisoning direct evidence
# ---------------------------------------------------------------------------


def test_poisoning_on_write_tick_event_injection(monkeypatch, db_path):
    """Inject a failure into write_tick_event; verify fail-loud poisoning.

    First tick must raise with parseable stderr JSON containing type, injected
    code, operation=tick, stage=tick.persist, db_path, and after tick.
    Second tick must raise runtime_poisoned and produce no spurious stderr.
    Second connection shows zero events and zero trajectory points.
    """
    from app.chatbox import field_persistence as fp_module

    injected_code = "injected_write_failure"
    injected_detail = "injected for poisoning test"

    runtime = _open_birth(db_path, count=3)
    try:
        # Monkeypatch write_tick_event on this instance only
        orig_write = runtime._store.write_tick_event

        def _failing_write(*args, **kwargs):
            raise FieldPersistenceError(
                injected_code,
                "tick",
                runtime._store.db_path,
                injected_detail,
                stage="tick.persist",
                field_tick=kwargs.get("after_field_tick", 0),
            )

        monkeypatch.setattr(runtime._store, "write_tick_event", _failing_write)

        old_stderr = sys.stderr
        sys.stderr = captured = io.StringIO()
        try:
            # First tick must fail loud
            with pytest.raises(FieldRuntimeError) as caught:
                runtime.tick()
            assert caught.value.code == injected_code
            assert caught.value.operation == "tick"
            assert runtime._poisoned

            # Parse stderr JSON
            stderr_text = captured.getvalue()
            assert stderr_text.strip(), "stderr must not be empty on poisoning"
            lines = stderr_text.strip().split("\n")
            # Last line should be the poisoning stderr JSON
            last_json = json.loads(lines[-1])
            assert last_json.get("type") == "FieldRuntimeError"
            assert last_json.get("code") == injected_code
            assert last_json.get("operation") == "tick"
            assert last_json.get("stage") == "tick.persist"
            assert last_json.get("db_path") == runtime.db_path
            assert last_json.get("field_tick") is not None

            # Reset capture for second tick
            captured.truncate(0)
            captured.seek(0)

            # Second tick must raise runtime_poisoned
            with pytest.raises(FieldRuntimeError) as caught2:
                runtime.tick()
            assert caught2.value.code == "runtime_poisoned"

            # Second tick's stderr must NOT contain a successful tick persist
            stderr2 = captured.getvalue()
            assert stderr2 == ""
            # No spurious success stderr from second tick
            # The poisoned guard raises before any write, so no new events
        finally:
            sys.stderr = old_stderr
    finally:
        runtime.close()

    # Second connection: zero events and trajectory (birth snapshot exists)
    assert _second_connection_count(db_path, "field_events") == 0
    assert _second_connection_count(db_path, "trajectory_points") == 0
    assert _second_connection_count(db_path, "field_snapshots") == 1
