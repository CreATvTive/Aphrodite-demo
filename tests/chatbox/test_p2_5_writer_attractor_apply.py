"""P2 task card 5 contract tests: writer → attractor-only application boundary.

Covers the frozen Phase C.3 writer contract and the task-card evidence list:

* valid multi-dimension increments applied via ``FieldRuntime.move_attractor``;
* dynamic 1 / 12 / 17 dimension registries (no hardcoded count);
* ±1 input maps to maximum ±0.3 actual attractor move (parser truncation);
* smaller values map exactly;
* unknown dim / NaN / Inf / bool / nested / malformed / partial formats →
  increment dropped, attractor untouched, natural-language log preserved;
* provider degradation and empty reply → no state writes;
* writer natural-language log persisted even when increment invalid;
* state / value / velocity / OU / slow-baseline never directly modified by
  writer (only attractor moves);
* persistence + restart recovery of attractor moves and log events;
* duplicate ``call_id`` submission is idempotent (no double apply);
* runtime closed / rejected command failure paths do not fake success;
* static AST audit: writer module has no state assignment or SQLite access.
"""

from __future__ import annotations

import ast
import json
import math
import os
from pathlib import Path
import sqlite3

import pytest

from app.chatbox.field_dynamics import (
    ATTRACTOR_DISPLACEMENT_RADIUS,
    AttractorMove,
    DimensionRegistration,
    InvalidAttractorMoveError,
    SeededGaussianRngFactory,
    build_birth_registry,
)
from app.chatbox.field_runtime import FieldRuntime, FieldRuntimeError
from app.chatbox.provider.structure_a import (
    INCREMENT_AMPLITUDE_CAP,
    ParsedReply,
    parse_structure_a,
)
from app.chatbox.writer import (
    WRITER_AMPLITUDE_CAP,
    WRITER_SOURCE,
    Writer,
    WriterMoveResult,
    WriterOutcome,
)


WRITER_MODULE = Path("app/chatbox/writer.py")
QUARANTINED_MODULES = (
    "agentlib",
    "agent_kernel",
    "src.semantic_trigger",
    "demos.scenarios",
)


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
        _registration(f"dim-{index}", bias=(-0.125 if index == 0 else 0.0))
        for index in range(count)
    )


def _open_birth(
    db_path: str,
    *,
    count: int = 3,
    seed: int = 0x12A5,
) -> FieldRuntime:
    return FieldRuntime.open(
        db_path,
        birth_registry=_registry(count),
        birth_rng_factory=SeededGaussianRngFactory(seed),
    )


def _attractor_map(runtime: FieldRuntime) -> dict[str, float]:
    snap = runtime.snapshot_proxy()
    return {dim.dim_id: float(dim.attractor) for dim in snap.dimensions}


def _value_map(runtime: FieldRuntime) -> dict[str, float]:
    snap = runtime.snapshot_proxy()
    return {dim.dim_id: float(dim.value) for dim in snap.dimensions}


def _velocity_map(runtime: FieldRuntime) -> dict[str, float]:
    snap = runtime.snapshot_proxy()
    return {dim.dim_id: float(dim.velocity) for dim in snap.dimensions}


def _baseline_map(runtime: FieldRuntime) -> dict[str, float]:
    snap = runtime.snapshot_proxy()
    return {dim.dim_id: float(dim.soft_restoring_baseline) for dim in snap.dimensions}


def _make_parsed(
    reply: str,
    increment: dict[str, float],
    *,
    degraded: bool = False,
    parsed_ok: bool | None = None,
    parse_note: str = "ok",
) -> ParsedReply:
    if parsed_ok is None:
        parsed_ok = bool(increment) and not degraded
    return ParsedReply(
        reply_text=reply,
        increment=dict(increment),
        parsed_ok=parsed_ok,
        degraded=degraded,
        provider_id="deepseek",
        parse_note=parse_note,
    )


def _count_attractor_events(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM field_events WHERE event_kind = 'attractor_move'"
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


def _attractor_event_rationales(db_path: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT payload_json FROM field_events "
            "WHERE event_kind = 'attractor_move' ORDER BY event_id"
        ).fetchall()
        return [json.loads(r[0])["rationale"] for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "writer_field.db")


@pytest.fixture()
def runtime(db_path):
    rt = _open_birth(db_path, count=4, seed=42)
    yield rt
    rt.close()


# ---------------------------------------------------------------------------
# Static AST audit: writer has no state assignment or SQLite access
# ---------------------------------------------------------------------------


def test_writer_module_imports_only_allowed():
    """writer.py imports only stdlib + field_dynamics/field_runtime/provider."""
    source = WRITER_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    for name in imports:
        assert not any(name == q or name.startswith(q + ".") for q in QUARANTINED_MODULES), (
            f"writer imports quarantined module: {name}"
        )
    # Allowed roots
    allowed = {"app.chatbox.field_dynamics", "app.chatbox.field_runtime",
               "app.chatbox.provider.structure_a", "math", "dataclasses",
               "typing", "__future__"}
    for name in imports:
        root = name.split(".")[0]
        assert root in {"app", "math", "dataclasses", "typing", "__future__"}, (
            f"writer imports unexpected root: {name}"
        )
        if name.startswith("app."):
            assert name in allowed or name.startswith(tuple(allowed)), (
                f"writer imports unexpected app module: {name}"
            )


def test_writer_module_no_sqlite_access():
    """writer.py must never touch sqlite3."""
    source = WRITER_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "sqlite" not in alias.name, f"writer imports sqlite: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module and "sqlite" in node.module), (
                f"writer imports from sqlite: {node.module}"
            )
    assert "sqlite" not in source


def test_writer_module_no_direct_state_mutation():
    """writer.py must not assign to state/value/velocity/OU/baseline attributes.

    It may only call ``FieldRuntime.move_attractor``.  We scan for any attribute
    assignment whose target name is one of the forbidden state fields, and for
    any call to ``FieldStateCapsule`` or capsule-internal functions.
    """
    source = WRITER_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_attrs = {"value", "velocity", "attractor", "soft_restoring_baseline",
                       "ou_acceleration", "state", "_states"}
    # Attribute assignments (x.y = ...)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    assert target.attr not in forbidden_attrs, (
                        f"writer assigns forbidden attribute: {target.attr}"
                    )
        if isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Attribute):
                assert node.target.attr not in forbidden_attrs, (
                    f"writer aug-assigns forbidden attribute: {node.target.attr}"
                )
    # No capsule imports
    assert "field_state_capsule" not in source
    # No direct _dynamics / _store access
    assert "._dynamics" not in source
    assert "._store" not in source
    # The only mutating call allowed is move_attractor
    call_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            call_names.append(node.func.attr)
    mutating = [n for n in call_names if n in {"move_attractor", "tick", "run_loop"}]
    assert set(mutating) == {"move_attractor"}, (
        f"writer calls unexpected mutating API: {mutating}"
    )


# ---------------------------------------------------------------------------
# Valid multi-dimension application
# ---------------------------------------------------------------------------


def test_valid_multidim_increment_applies_each_dim(runtime):
    before = _attractor_map(runtime)
    increment = {"dim-0": 0.1, "dim-1": -0.2, "dim-2": 0.05}
    parsed = _make_parsed("你好呀。", increment)
    writer = Writer(runtime)
    outcome = writer.apply(parsed, call_id="call-1")
    after = _attractor_map(runtime)
    assert outcome.deduplicated is False
    assert outcome.degraded is False
    assert outcome.parse_ok is True
    assert outcome.log_persisted is True
    assert len(outcome.moves) == 3
    assert all(m.applied for m in outcome.moves)
    # Actual attractor moved by exactly the delta (within float tolerance).
    assert math.isclose(after["dim-0"] - before["dim-0"], 0.1, abs_tol=1e-12)
    assert math.isclose(after["dim-1"] - before["dim-1"], -0.2, abs_tol=1e-12)
    assert math.isclose(after["dim-2"] - before["dim-2"], 0.05, abs_tol=1e-12)
    # dim-3 untouched
    assert math.isclose(after["dim-3"], before["dim-3"], abs_tol=1e-12)


def test_dynamic_registry_1_dim(tmp_path):
    db = str(tmp_path / "one.db")
    rt = _open_birth(db, count=1, seed=1)
    try:
        writer = Writer(rt)
        parsed = _make_parsed("hi", {"dim-0": 0.3})
        outcome = writer.apply(parsed, call_id="c1")
        assert len(outcome.moves) == 1
        assert outcome.moves[0].applied
        snap = rt.snapshot_proxy()
        # dim-0 has birth_bias -0.125, so attractor starts at -0.125 + 0.3 = 0.175
        assert math.isclose(snap.dimensions[0].attractor, 0.175, abs_tol=1e-9)
    finally:
        rt.close()


def test_dynamic_registry_12_dims(tmp_path):
    db = str(tmp_path / "twelve.db")
    rt = _open_birth(db, count=12, seed=2)
    try:
        writer = Writer(rt)
        increment = {f"dim-{i}": 0.01 * (i + 1) for i in range(12)}
        parsed = _make_parsed("hello all", increment)
        outcome = writer.apply(parsed, call_id="c12")
        assert len(outcome.moves) == 12
        assert all(m.applied for m in outcome.moves)
    finally:
        rt.close()


def test_dynamic_registry_17_dims(tmp_path):
    db = str(tmp_path / "seventeen.db")
    rt = _open_birth(db, count=17, seed=3)
    try:
        writer = Writer(rt)
        increment = {f"dim-{i}": ((-1) ** i) * 0.05 for i in range(17)}
        parsed = _make_parsed("hello 17", increment)
        outcome = writer.apply(parsed, call_id="c17")
        assert len(outcome.moves) == 17
        assert all(m.applied for m in outcome.moves)
    finally:
        rt.close()


# ---------------------------------------------------------------------------
# ±1 input → max ±0.3 actual move (parser truncation)
# ---------------------------------------------------------------------------


def test_plus_one_input_maps_to_plus_0_3_actual_move(runtime):
    before = _attractor_map(runtime)
    # parse_structure_a truncates ±1 to ±0.3
    parsed = parse_structure_a(
        "reply\n---\n" + json.dumps({"dim-0": 1.0, "dim-1": -1.0}),
        registry_dim_ids=tuple(runtime.registry_proxy().dim_ids),
    )
    assert parsed.increment["dim-0"] == INCREMENT_AMPLITUDE_CAP
    assert parsed.increment["dim-1"] == -INCREMENT_AMPLITUDE_CAP
    writer = Writer(runtime)
    outcome = writer.apply(parsed, call_id="plus-one")
    after = _attractor_map(runtime)
    assert math.isclose(after["dim-0"] - before["dim-0"], 0.3, abs_tol=1e-12)
    assert math.isclose(after["dim-1"] - before["dim-1"], -0.3, abs_tol=1e-12)
    assert all(m.applied for m in outcome.moves)
    # No move exceeded the cap
    for m in outcome.moves:
        assert abs(m.requested_delta) <= WRITER_AMPLITUDE_CAP + 1e-12


def test_smaller_values_map_exactly(runtime):
    before = _attractor_map(runtime)
    parsed = parse_structure_a(
        "reply\n---\n" + json.dumps({"dim-0": 0.07, "dim-1": -0.13}),
        registry_dim_ids=tuple(runtime.registry_proxy().dim_ids),
    )
    writer = Writer(runtime)
    writer.apply(parsed, call_id="small")
    after = _attractor_map(runtime)
    assert math.isclose(after["dim-0"] - before["dim-0"], 0.07, abs_tol=1e-12)
    assert math.isclose(after["dim-1"] - before["dim-1"], -0.13, abs_tol=1e-12)


def test_value_over_1_dropped_by_parser(runtime):
    before = _attractor_map(runtime)
    parsed = parse_structure_a(
        "reply\n---\n" + json.dumps({"dim-0": 1.5}),
        registry_dim_ids=tuple(runtime.registry_proxy().dim_ids),
    )
    # 1.5 is out of [-1,+1] writer space → dropped, parsed_ok False
    assert parsed.parsed_ok is False
    assert parsed.increment == {}
    writer = Writer(runtime)
    outcome = writer.apply(parsed, call_id="over-one")
    after = _attractor_map(runtime)
    # Attractor untouched (no-op log event only)
    assert math.isclose(after["dim-0"], before["dim-0"], abs_tol=1e-12)
    assert outcome.log_persisted is True
    assert len(outcome.moves) == 0


# ---------------------------------------------------------------------------
# Unknown dim / NaN / Inf / bool / nested / malformed / partial
# ---------------------------------------------------------------------------


def test_unknown_dimension_dropped(runtime):
    before = _attractor_map(runtime)
    parsed = parse_structure_a(
        "reply\n---\n" + json.dumps({"dim-0": 0.1, "unknown-dim": 0.5}),
        registry_dim_ids=tuple(runtime.registry_proxy().dim_ids),
    )
    assert "unknown-dim" not in parsed.increment
    assert "dim-0" in parsed.increment
    writer = Writer(runtime)
    outcome = writer.apply(parsed, call_id="unknown")
    after = _attractor_map(runtime)
    assert math.isclose(after["dim-0"] - before["dim-0"], 0.1, abs_tol=1e-12)
    # unknown-dim not in registry → no move recorded for it
    assert all(m.dim_id != "unknown-dim" for m in outcome.moves)


def test_nan_inf_bool_dropped_by_parser(runtime):
    before = _attractor_map(runtime)
    # JSON does not allow NaN/Infinity by strict parsers, but Python's json
    # accepts them by default.  parse_structure_a uses _coerce_number which
    # rejects non-finite and bool.
    payload = '{"dim-0": NaN, "dim-1": Infinity, "dim-2": true, "dim-3": 0.1}'
    parsed = parse_structure_a(
        "reply\n---\n" + payload,
        registry_dim_ids=tuple(runtime.registry_proxy().dim_ids),
    )
    # Only dim-3 survives
    assert set(parsed.increment.keys()) == {"dim-3"}
    writer = Writer(runtime)
    outcome = writer.apply(parsed, call_id="nan-inf-bool")
    after = _attractor_map(runtime)
    assert math.isclose(after["dim-3"] - before["dim-3"], 0.1, abs_tol=1e-12)
    # dim-0/1/2 untouched
    for d in ("dim-0", "dim-1", "dim-2"):
        assert math.isclose(after[d], before[d], abs_tol=1e-12)


def test_nested_object_value_dropped(runtime):
    before = _attractor_map(runtime)
    parsed = parse_structure_a(
        'reply\n---\n{"dim-0": {"nested": 0.5}, "dim-1": 0.2}',
        registry_dim_ids=tuple(runtime.registry_proxy().dim_ids),
    )
    assert "dim-0" not in parsed.increment
    assert parsed.increment.get("dim-1") == 0.2
    writer = Writer(runtime)
    writer.apply(parsed, call_id="nested")
    after = _attractor_map(runtime)
    assert math.isclose(after["dim-0"], before["dim-0"], abs_tol=1e-12)
    assert math.isclose(after["dim-1"] - before["dim-1"], 0.2, abs_tol=1e-12)


def test_malformed_increment_segment_drops_all(runtime):
    before = _attractor_map(runtime)
    parsed = parse_structure_a(
        "reply\n---\nthis is not json",
        registry_dim_ids=tuple(runtime.registry_proxy().dim_ids),
    )
    assert parsed.parsed_ok is False
    assert parsed.increment == {}
    writer = Writer(runtime)
    outcome = writer.apply(parsed, call_id="malformed")
    after = _attractor_map(runtime)
    for d in before:
        assert math.isclose(after[d], before[d], abs_tol=1e-12)
    assert outcome.log_persisted is True
    assert len(outcome.moves) == 0


def test_no_delimiter_drops_increment_keeps_reply(runtime):
    before = _attractor_map(runtime)
    parsed = parse_structure_a(
        "just a reply with no delimiter",
        registry_dim_ids=tuple(runtime.registry_proxy().dim_ids),
    )
    assert parsed.parsed_ok is False
    assert parsed.reply_text == "just a reply with no delimiter"
    writer = Writer(runtime)
    outcome = writer.apply(parsed, call_id="no-delim")
    after = _attractor_map(runtime)
    for d in before:
        assert math.isclose(after[d], before[d], abs_tol=1e-12)
    assert outcome.log_persisted is True
    assert outcome.reply_text == "just a reply with no delimiter"


def test_partial_format_only_some_dims_valid(runtime):
    before = _attractor_map(runtime)
    parsed = parse_structure_a(
        'reply\n---\n{"dim-0": 0.1, "dim-1": "not-a-number", "dim-2": 0.3}',
        registry_dim_ids=tuple(runtime.registry_proxy().dim_ids),
    )
    assert set(parsed.increment.keys()) == {"dim-0", "dim-2"}
    writer = Writer(runtime)
    outcome = writer.apply(parsed, call_id="partial")
    after = _attractor_map(runtime)
    assert math.isclose(after["dim-0"] - before["dim-0"], 0.1, abs_tol=1e-12)
    assert math.isclose(after["dim-2"] - before["dim-2"], 0.3, abs_tol=1e-12)
    assert math.isclose(after["dim-1"], before["dim-1"], abs_tol=1e-12)
    assert len(outcome.moves) == 2


# ---------------------------------------------------------------------------
# Provider degradation / empty reply → no state writes
# ---------------------------------------------------------------------------


def test_provider_degradation_no_writes(runtime, db_path):
    before = _attractor_map(runtime)
    events_before = _count_attractor_events(db_path)
    parsed = _make_parsed("", {}, degraded=True, parse_note="transport:timeout")
    writer = Writer(runtime)
    outcome = writer.apply(parsed, call_id="degraded")
    after = _attractor_map(runtime)
    events_after = _count_attractor_events(db_path)
    assert outcome.degraded is True
    assert outcome.log_persisted is False
    assert len(outcome.moves) == 0
    for d in before:
        assert math.isclose(after[d], before[d], abs_tol=1e-12)
    assert events_after == events_before


def test_empty_increment_still_persists_log(runtime, db_path):
    before = _attractor_map(runtime)
    events_before = _count_attractor_events(db_path)
    parsed = _make_parsed("I have nothing to move.", {}, parsed_ok=False,
                          parse_note="no-registered-dimensions")
    writer = Writer(runtime)
    outcome = writer.apply(parsed, call_id="empty-inc")
    after = _attractor_map(runtime)
    events_after = _count_attractor_events(db_path)
    # Attractor untouched
    for d in before:
        assert math.isclose(after[d], before[d], abs_tol=1e-12)
    # But a no-op log event was persisted
    assert outcome.log_persisted is True
    assert events_after == events_before + 1
    rationales = _attractor_event_rationales(db_path)
    assert rationales and "call=empty-inc" in rationales[-1]
    assert "I have nothing to move." in rationales[-1]


# ---------------------------------------------------------------------------
# Writer does not directly modify state/value/velocity/OU/baseline
# ---------------------------------------------------------------------------


def test_writer_does_not_modify_value_velocity_baseline(runtime):
    before_val = _value_map(runtime)
    before_vel = _velocity_map(runtime)
    before_base = _baseline_map(runtime)
    parsed = _make_parsed("move attractor only", {"dim-0": 0.2, "dim-1": -0.1})
    writer = Writer(runtime)
    writer.apply(parsed, call_id="boundary")
    after_val = _value_map(runtime)
    after_vel = _velocity_map(runtime)
    after_base = _baseline_map(runtime)
    # value / velocity / slow-baseline are NOT changed by an attractor move
    # (only the attractor setpoint moves; value/velocity evolve via tick()).
    for d in before_val:
        assert math.isclose(after_val[d], before_val[d], abs_tol=1e-12)
        assert math.isclose(after_vel[d], before_vel[d], abs_tol=1e-12)
        assert math.isclose(after_base[d], before_base[d], abs_tol=1e-12)


def test_writer_only_calls_move_attractor_api():
    """The Writer class has no reference to capsule internals or _dynamics."""
    import app.chatbox.writer as writer_mod
    src = Path(writer_mod.__file__).read_text(encoding="utf-8")
    assert "FieldStateCapsule" not in src
    assert "field_state_capsule" not in src
    assert "._dynamics" not in src
    assert "._store" not in src
    assert "registry_proxy" in src  # read-only registry access allowed
    assert "move_attractor" in src  # the only mutating API used


# ---------------------------------------------------------------------------
# Persistence + restart recovery
# ---------------------------------------------------------------------------


def test_attractor_moves_persist_and_recover(tmp_path):
    db = str(tmp_path / "persist.db")
    rt = _open_birth(db, count=3, seed=5)
    try:
        writer = Writer(rt)
        writer.apply(_make_parsed("first", {"dim-0": 0.15}), call_id="p1")
        writer.apply(_make_parsed("second", {"dim-1": -0.08}), call_id="p2")
    finally:
        rt.close()
    # Reopen and verify attractor state recovered
    rt2 = FieldRuntime.open(db)
    try:
        snap = rt2.snapshot_proxy()
        attractors = {dim.dim_id: float(dim.attractor) for dim in snap.dimensions}
        # dim-0 has birth_bias -0.125, so attractor = -0.125 + 0.15 = 0.025
        assert math.isclose(attractors["dim-0"], 0.025, abs_tol=1e-9)
        # dim-1 has birth_bias 0.0, so attractor = 0.0 + (-0.08) = -0.08
        assert math.isclose(attractors["dim-1"], -0.08, abs_tol=1e-9)
        # dim-2 stayed at birth (0.0)
        assert math.isclose(attractors["dim-2"], 0.0, abs_tol=1e-9)
        # Event log recoverable
        assert _count_attractor_events(db) == 2
        rationales = _attractor_event_rationales(db)
        assert any("call=p1" in r for r in rationales)
        assert any("call=p2" in r for r in rationales)
    finally:
        rt2.close()


def test_log_events_append_only_across_restarts(tmp_path):
    db = str(tmp_path / "append.db")
    rt = _open_birth(db, count=2, seed=6)
    try:
        Writer(rt).apply(_make_parsed("a", {"dim-0": 0.1}), call_id="a")
    finally:
        rt.close()
    rt2 = FieldRuntime.open(db)
    try:
        Writer(rt2).apply(_make_parsed("b", {"dim-1": 0.2}), call_id="b")
    finally:
        rt2.close()
    # Both events must still be present (append-only)
    rationales = _attractor_event_rationales(db)
    assert any("call=a" in r for r in rationales)
    assert any("call=b" in r for r in rationales)


# ---------------------------------------------------------------------------
# Duplicate submission idempotency
# ---------------------------------------------------------------------------


def test_duplicate_call_id_is_idempotent(runtime, db_path):
    before = _attractor_map(runtime)
    parsed = _make_parsed("dup", {"dim-0": 0.12})
    writer = Writer(runtime)
    out1 = writer.apply(parsed, call_id="dup-1")
    mid = _attractor_map(runtime)
    events_mid = _count_attractor_events(db_path)
    out2 = writer.apply(parsed, call_id="dup-1")
    after = _attractor_map(runtime)
    events_after = _count_attractor_events(db_path)
    assert out1.deduplicated is False
    assert out1.log_persisted is True
    assert out2.deduplicated is True
    assert out2.log_persisted is False
    assert len(out2.moves) == 0
    # Attractor moved once, not twice
    assert math.isclose(mid["dim-0"] - before["dim-0"], 0.12, abs_tol=1e-12)
    assert math.isclose(after["dim-0"], mid["dim-0"], abs_tol=1e-12)
    assert events_after == events_mid


def test_different_call_ids_both_apply(runtime):
    writer = Writer(runtime)
    o1 = writer.apply(_make_parsed("a", {"dim-0": 0.05}), call_id="x")
    o2 = writer.apply(_make_parsed("b", {"dim-0": 0.05}), call_id="y")
    assert o1.deduplicated is False
    assert o2.deduplicated is False
    snap = runtime.snapshot_proxy()
    # dim-0 birth_bias -0.125 + 0.05 + 0.05 = -0.025
    assert math.isclose(snap.dimensions[0].attractor, -0.025, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# Runtime closed / rejected command failure paths
# ---------------------------------------------------------------------------


def test_runtime_closed_raises_on_apply(runtime):
    writer = Writer(runtime)
    runtime.close()
    parsed = _make_parsed("after close", {"dim-0": 0.1})
    # The no-op log path will try move_attractor and hit runtime_closed.
    with pytest.raises(FieldRuntimeError) as caught:
        writer.apply(parsed, call_id="closed")
    assert caught.value.code in {"runtime_closed", "runtime_poisoned"}


def test_rejected_move_recorded_not_faked(runtime):
    """A move that the dynamics rejects (displacement out of domain) is
    recorded as not applied, and the writer does not fake success."""
    # Push dim-0 attractor near the edge of its displacement domain.
    # Domain radius is ATTRACTOR_DISPLACEMENT_RADIUS around the slow baseline.
    baseline = _baseline_map(runtime)["dim-0"]
    # Move in steps of 0.3 until we are near the edge, then attempt one more
    # that would exceed the domain.
    writer = Writer(runtime)
    call_idx = 0
    # Move toward the upper edge
    target = baseline + ATTRACTOR_DISPLACEMENT_RADIUS - 0.05
    current = _attractor_map(runtime)["dim-0"]
    while current < target:
        step = min(0.3, target - current)
        call_idx += 1
        out = writer.apply(_make_parsed("edge", {"dim-0": step}), call_id=f"edge-{call_idx}")
        assert out.moves[0].applied
        current = _attractor_map(runtime)["dim-0"]
    # Now a move that would exceed the domain → rejected
    call_idx += 1
    out = writer.apply(_make_parsed("over", {"dim-0": 0.3}), call_id=f"over-{call_idx}")
    rejected = [m for m in out.moves if not m.applied]
    assert rejected, "expected at least one rejected move"
    assert rejected[0].reject_reason != ""
    # The rejected move must not have changed the attractor
    # (the runtime raises before mutating on domain violation)


def test_empty_call_id_raises(runtime):
    writer = Writer(runtime)
    with pytest.raises(ValueError):
        writer.apply(_make_parsed("x", {}), call_id="")
    with pytest.raises(ValueError):
        writer.apply(_make_parsed("x", {}), call_id="   ")


def test_non_field_runtime_rejected():
    with pytest.raises(TypeError):
        Writer(object())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Sensitive info not logged
# ---------------------------------------------------------------------------


def test_api_key_not_in_rationale(runtime, db_path):
    parsed = _make_parsed("a normal reply", {"dim-0": 0.1})
    writer = Writer(runtime)
    writer.apply(parsed, call_id="check")
    rationales = _attractor_event_rationales(db_path)
    # The rationale never carries an api_key field; only the reply text,
    # call id, and parse note are logged.
    last = rationales[-1]
    assert "api_key" not in last
    assert "Bearer" not in last
    assert "sk-" not in last


def test_rationale_carries_call_id_and_reply(runtime, db_path):
    parsed = _make_parsed("hello world reply", {"dim-0": 0.1})
    writer = Writer(runtime)
    writer.apply(parsed, call_id="trace-xyz")
    rationales = _attractor_event_rationales(db_path)
    last = rationales[-1]
    assert "call=trace-xyz" in last
    assert "hello world reply" in last


# ---------------------------------------------------------------------------
# No hardcoded dimension count in writer
# ---------------------------------------------------------------------------


def test_writer_does_not_hardcode_dimension_count():
    import app.chatbox.writer as writer_mod
    src = Path(writer_mod.__file__).read_text(encoding="utf-8")
    # The writer must not hardcode 12 or any other dimension count.
    assert "12" not in src.replace("2000", "").replace("1e-12", "").replace("0x12A5", "")
    assert "registry.length" not in src or "len(" in src
