"""P3 task-card 9 contract tests: offline synthetic sweep harness.

Covers the forced/alliance gate semantics, dynamic dimensions, determinism,
blind/answer privacy layering, manifest auditability, atomic publication
safety, zero external calls, and the Windows CLI subprocess entry point.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import inspect
import io
import json
import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.chatbox.expression_gate import (
    AllOpenGateProjector,
    EXPERIMENTAL_FORCED_GATE_MODE,
    ForcedTargetGateError,
    ForcedTargetGateProjector,
    V0_GATE_MODE,
)
from app.chatbox.field_dynamics import (
    DimensionRegistration,
    DimensionSnapshot,
    FieldSnapshot,
    build_birth_registry,
)
from app.chatbox.field_runtime import RegistryProxy
from app.chatbox.sweep_harness import (
    DEFAULT_ALLIANCE_CONDITIONS,
    DEFAULT_FORCED_LEVELS,
    SWEEP_SCHEMA,
    SweepError,
    _default_synthetic_renderer,
    generate_sweep,
    publish_package,
)
from app.chatbox.meta_narration import detect_meta_narration


# ---------------------------------------------------------------------------
# dynamic synthetic registration helpers (no copied 12-dim position semantics)
# ---------------------------------------------------------------------------


def _make_registration(dim_id: str, index: int) -> DimensionRegistration:
    return DimensionRegistration(
        dim_id=dim_id,
        temporary_name=f"synthetic-temp-{index}",
        birth_time=0.0,
        strength=1.0,
        trigger_count=0,
        birth_bias=0.0,
        fast_e_fold_s=4.0,
        ou_correlation_e_fold_s=600.0,
        ou_acceleration_sigma=4.0e-7,
        soft_boundary_start=1.0,
        soft_boundary_width=0.25,
        soft_boundary_strength=(1.0 / 120.0) ** 2,
    )


def _make_registry(dim_ids: tuple[str, ...]) -> RegistryProxy:
    return RegistryProxy(
        registrations=tuple(_make_registration(dim_id, i) for i, dim_id in enumerate(dim_ids))
    )


def _zero_snapshot(registry: RegistryProxy) -> FieldSnapshot:
    return FieldSnapshot(
        tick=0,
        dimensions=tuple(
            DimensionSnapshot(
                registration=registration,
                value=0.0,
                velocity=0.0,
                attractor=0.0,
                soft_restoring_baseline=0.0,
                ou_acceleration=0.0,
            )
            for registration in registry.registrations
        ),
    )


def _snapshot_scalar_tuples(snapshot: FieldSnapshot) -> tuple[tuple, ...]:
    return tuple(
        (d.dim_id, d.value, d.velocity, d.attractor, d.soft_restoring_baseline, d.ou_acceleration)
        for d in snapshot.dimensions
    )


def _registry_scalar_tuples(registry: RegistryProxy) -> tuple[tuple, ...]:
    return tuple(
        (
            r.dim_id,
            r.temporary_name,
            r.birth_time,
            r.strength,
            r.trigger_count,
            r.birth_bias,
            r.fast_e_fold_s,
            r.ou_correlation_e_fold_s,
            r.ou_acceleration_sigma,
            r.soft_boundary_start,
            r.soft_boundary_width,
            r.soft_boundary_strength,
        )
        for r in registry.registrations
    )


# ---------------------------------------------------------------------------
# forced experimental gate projector
# ---------------------------------------------------------------------------


def test_forced_gate_target_weight_one_others_zero_and_registry_unchanged() -> None:
    registry = _make_registry(("d0", "d1", "d2", "d3"))
    before_registry = _registry_scalar_tuples(registry)
    projector = ForcedTargetGateProjector()
    gate = projector.project(registry, "d2")
    after_registry = _registry_scalar_tuples(registry)
    assert gate.mode == EXPERIMENTAL_FORCED_GATE_MODE
    assert gate.temperature_applied is False
    weights = {w.dim_id: w.weight for w in gate.weights}
    assert weights == {"d0": 0.0, "d1": 0.0, "d2": 1.0, "d3": 0.0}
    assert tuple(w.ordinal for w in gate.weights) == (0, 1, 2, 3)
    assert before_registry == after_registry


@pytest.mark.parametrize("dim_ids", [(), ("only",), tuple(f"b{i}" for i in range(12)), tuple(f"c{i}" for i in range(17))])
def test_forced_gate_dynamic_dimensions_safe(dim_ids: tuple[str, ...]) -> None:
    registry = _make_registry(dim_ids)
    projector = ForcedTargetGateProjector()
    if not dim_ids:
        with pytest.raises(ForcedTargetGateError):
            projector.project(registry, "missing")
        return
    gate = projector.project(registry, dim_ids[0])
    assert len(gate.weights) == len(dim_ids)
    assert gate.weights[0].weight == 1.0
    assert all(w.weight == 0.0 for w in gate.weights[1:])


def test_forced_gate_shuffled_registry_preserves_order() -> None:
    dim_ids = ("z", "a", "m", "b", "q")
    registry = _make_registry(dim_ids)
    projector = ForcedTargetGateProjector()
    gate = projector.project(registry, "m")
    assert tuple(w.dim_id for w in gate.weights) == dim_ids
    assert gate.weights[2].weight == 1.0


def test_forced_gate_unknown_target_fail_closed() -> None:
    registry = _make_registry(("d0", "d1"))
    projector = ForcedTargetGateProjector()
    with pytest.raises(ForcedTargetGateError) as exc:
        projector.project(registry, "nope")
    assert exc.value.code == "unknown_target"


def test_production_all_open_gate_unchanged() -> None:
    registry = _make_registry(("d0", "d1", "d2"))
    projector = AllOpenGateProjector()
    gate = projector.project(registry)
    assert gate.mode == V0_GATE_MODE
    assert all(w.weight == 1.0 for w in gate.weights)


# ---------------------------------------------------------------------------
# generate_sweep: forced vs alliance gate semantics + full-pool invariance
# ---------------------------------------------------------------------------


def test_generate_forced_uses_forced_gate_and_preserves_inputs() -> None:
    registry = _make_registry(("d0", "d1", "d2"))
    snapshot = _zero_snapshot(registry)
    before_registry = _registry_scalar_tuples(registry)
    before_snapshot = _snapshot_scalar_tuples(snapshot)
    result = generate_sweep(
        registry=registry,
        snapshot=snapshot,
        mode="forced",
        seed=42,
        message="固定一句",
        forced_targets=("d1",),
        forced_levels=(0.5,),
    )
    assert result.mode == "forced"
    assert len(result.cases) == 1
    case = result.cases[0].case
    assert case.gate_mode == EXPERIMENTAL_FORCED_GATE_MODE
    weights = {dim_id: weight for dim_id, weight in case.gate_weights}
    assert weights == {"d0": 0.0, "d1": 1.0, "d2": 0.0}
    assert case.condition == (0.0, 0.5, 0.0)
    # Inputs unchanged.
    assert _registry_scalar_tuples(registry) == before_registry
    assert _snapshot_scalar_tuples(snapshot) == before_snapshot


def test_generate_alliance_uses_normal_all_open_gate() -> None:
    registry = _make_registry(("d0", "d1", "d2"))
    snapshot = _zero_snapshot(registry)
    normal_gate = AllOpenGateProjector().project(registry)
    result = generate_sweep(
        registry=registry,
        snapshot=snapshot,
        mode="alliance",
        seed=7,
        message="固定一句",
        alliance_conditions=((0.1, -0.2, 0.3),),
    )
    assert len(result.cases) == 1
    case = result.cases[0].case
    assert case.gate_mode == V0_GATE_MODE
    assert case.gate_weights == tuple((w.dim_id, w.weight) for w in normal_gate.weights)
    assert all(weight == 1.0 for _, weight in case.gate_weights)


def test_generate_forced_default_targets_sweeps_registry() -> None:
    registry = _make_registry(("d0", "d1", "d2"))
    snapshot = _zero_snapshot(registry)
    result = generate_sweep(
        registry=registry, snapshot=snapshot, mode="forced", seed=1, message="m",
        forced_levels=(0.0,),
    )
    targets = {case.case.target_dim_id for case in result.cases}
    assert targets == {"d0", "d1", "d2"}
    assert len(result.cases) == 3


def test_generate_unknown_forced_target_recorded_as_skipped() -> None:
    registry = _make_registry(("d0", "d1"))
    snapshot = _zero_snapshot(registry)
    result = generate_sweep(
        registry=registry, snapshot=snapshot, mode="forced", seed=1, message="m",
        forced_targets=("d0", "ghost", "d1"),
    )
    assert result.skipped == ("ghost",)
    targets = {case.case.target_dim_id for case in result.cases}
    assert targets == {"d0", "d1"}


@pytest.mark.parametrize("n", [0, 1, 12, 17])
def test_generate_dynamic_dimensions_safe(n: int) -> None:
    dim_ids = tuple(f"dyn{i}" for i in range(n))
    registry = _make_registry(dim_ids)
    snapshot = _zero_snapshot(registry)
    # forced
    if n == 0:
        result = generate_sweep(registry=registry, snapshot=snapshot, mode="forced", seed=3, message="m")
        assert result.cases == ()
        assert result.skipped == ()
    else:
        result = generate_sweep(
            registry=registry, snapshot=snapshot, mode="forced", seed=3, message="m",
            forced_levels=(0.0,),
        )
        assert len(result.cases) == n
    # alliance
    result_a = generate_sweep(registry=registry, snapshot=snapshot, mode="alliance", seed=3, message="m")
    if n == 0:
        assert result_a.cases == ()
    else:
        assert len(result_a.cases) == len(DEFAULT_ALLIANCE_CONDITIONS)


def test_generate_shuffled_registry_safe() -> None:
    registry = _make_registry(("z", "a", "m"))
    snapshot = _zero_snapshot(registry)
    result = generate_sweep(
        registry=registry, snapshot=snapshot, mode="forced", seed=5, message="m",
        forced_targets=("m",), forced_levels=(1.0,),
    )
    assert result.cases[0].case.condition_dim_ids == ("z", "a", "m")
    assert result.cases[0].case.condition == (0.0, 0.0, 1.0)


def test_generate_empty_registry_alliance_zero_cases() -> None:
    registry = _make_registry(())
    snapshot = _zero_snapshot(registry)
    result = generate_sweep(registry=registry, snapshot=snapshot, mode="alliance", seed=9, message="m")
    assert result.cases == ()
    assert result.skipped == ()


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def _publish_to(tmp_path: Path, name: str, seed: int, mode: str, message: str) -> Path:
    registry = _make_registry(("d0", "d1", "d2", "d3"))
    snapshot = _zero_snapshot(registry)
    result = generate_sweep(
        registry=registry, snapshot=snapshot, mode=mode, seed=seed, message=message,
        forced_targets=("d0", "d1") if mode == "forced" else None,
        forced_levels=(0.0, 1.0) if mode == "forced" else DEFAULT_FORCED_LEVELS,
    )
    out = tmp_path / name
    publish_package(result=result, output_path=str(out))
    return out


def _read_package_bytes(pkg: Path) -> dict[str, bytes]:
    return {
        "blind": (pkg / "blind" / "samples.jsonl").read_bytes(),
        "answer": (pkg / "answer" / "answer-key.json").read_bytes(),
        "manifest": (pkg / "manifest.json").read_bytes(),
    }


def test_same_seed_reproduces_identical_bytes_across_parents(tmp_path) -> None:
    parent_a = tmp_path / "a"
    parent_b = tmp_path / "b"
    parent_a.mkdir()
    parent_b.mkdir()
    pkg_a = _publish_to(parent_a, "pkg", 90210, "forced", "固定同一句话")
    pkg_b = _publish_to(parent_b, "pkg", 90210, "forced", "固定同一句话")
    bytes_a = _read_package_bytes(pkg_a)
    bytes_b = _read_package_bytes(pkg_b)
    assert bytes_a == bytes_b


def test_different_seed_produces_distinguishable_output(tmp_path) -> None:
    pkg1 = _publish_to(tmp_path, "pkg1", 90210, "forced", "固定同一句话")
    pkg2 = _publish_to(tmp_path, "pkg2", 90211, "forced", "固定同一句话")
    b1 = _read_package_bytes(pkg1)
    b2 = _read_package_bytes(pkg2)
    # At least manifest, blind, or answer must differ.
    assert b1 != b2
    # sample_ids or text should differ.
    a1 = json.loads(b1["answer"].decode("utf-8"))
    a2 = json.loads(b2["answer"].decode("utf-8"))
    ids1 = {e["sample_id"] for e in a1["answers"]}
    ids2 = {e["sample_id"] for e in a2["answers"]}
    assert ids1 != ids2 or {e["reply_text"] for e in a1["answers"]} != {e["reply_text"] for e in a2["answers"]}


# ---------------------------------------------------------------------------
# privacy / blind side
# ---------------------------------------------------------------------------


def _blind_sample_ids(pkg: Path) -> list[str]:
    blind = (pkg / "blind" / "samples.jsonl").read_text(encoding="utf-8")
    return [json.loads(line)["sample_id"] for line in blind.splitlines() if line.strip()]


def test_blind_side_has_no_labels_or_internal_tokens(tmp_path) -> None:
    pkg = _publish_to(tmp_path, "pkg", 123, "forced", "固定一句")
    blind_text = (pkg / "blind" / "samples.jsonl").read_text(encoding="utf-8")
    forbidden = [
        "mode", "target", "alliance", "forced", "experimental", "synthetic",
        "condition", "case", "dim_id", "dim", "registry", "ordinal", "weight",
        "vector", "receptor", "plan", "answer", "synthetic-temp",
    ]
    lowered = blind_text.casefold()
    for token in forbidden:
        assert token.casefold() not in lowered, f"blind leaks {token!r}"
    # Structural: only sample_id / input / reply keys.
    for line in blind_text.splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        assert set(obj.keys()) <= {"sample_id", "input", "reply"}


def test_answer_side_carries_labels_and_bijection(tmp_path) -> None:
    pkg = _publish_to(tmp_path, "pkg", 123, "forced", "固定一句")
    answer = json.loads((pkg / "answer" / "answer-key.json").read_text(encoding="utf-8"))
    blind_ids = _blind_sample_ids(pkg)
    answer_ids = [e["sample_id"] for e in answer["answers"]]
    assert set(blind_ids) == set(answer_ids)
    assert len(blind_ids) == len(set(blind_ids))
    assert len(answer_ids) == len(set(answer_ids))
    for entry in answer["answers"]:
        assert entry["synthetic"] is True
        assert entry["mode"] == "forced"
        assert "condition" in entry
        assert "gate_weights" in entry
        assert "receptor_vector" in entry


def test_unsafe_renderer_does_not_publish(tmp_path) -> None:
    registry = _make_registry(("d0", "d1"))
    snapshot = _zero_snapshot(registry)

    def unsafe_renderer(message: str, style: str, seed: int) -> str:
        return "我的内部状态值为 0.5，因为 dim_id d0 的权重变成了 1。"

    with pytest.raises(SweepError):
        generate_sweep(
            registry=registry, snapshot=snapshot, mode="forced", seed=1, message="m",
            forced_targets=("d0",), forced_levels=(0.5,), renderer=unsafe_renderer,
        )
    out = tmp_path / "unsafe_pkg"
    assert not out.exists()


# ---------------------------------------------------------------------------
# manifest auditability
# ---------------------------------------------------------------------------


def test_manifest_independent_hash_and_package_digest(tmp_path) -> None:
    pkg = _publish_to(tmp_path, "pkg", 555, "alliance", "固定一句")
    manifest = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == SWEEP_SCHEMA
    assert manifest["owner_blind_gate"] == "not_run"
    assert manifest["two_hour_silence_gate"] == "not_run"
    assert manifest["synthetic"] is True
    # Recompute each file hash independently.
    for f in manifest["files"]:
        data = (pkg / f["path"]).read_bytes()
        assert len(data) == f["byte_size"]
        assert hashlib.sha256(data).hexdigest() == f["sha256"]
    # Recompute package digest.
    digest_payload = "|".join(
        f"{f['path']}:{f['byte_size']}:{f['sha256']}" for f in manifest["files"]
    ).encode("utf-8")
    assert hashlib.sha256(digest_payload).hexdigest() == manifest["package_digest"]
    # manifest is not self-hashed.
    assert all(f["path"] != "manifest.json" for f in manifest["files"])


# ---------------------------------------------------------------------------
# atomic publication safety
# ---------------------------------------------------------------------------


def test_publish_rejects_existing_empty_and_nonempty_dir(tmp_path) -> None:
    registry = _make_registry(("d0",))
    snapshot = _zero_snapshot(registry)
    result = generate_sweep(registry=registry, snapshot=snapshot, mode="forced", seed=1, message="m")
    existing_empty = tmp_path / "empty"
    existing_empty.mkdir()
    with pytest.raises(SweepError):
        publish_package(result=result, output_path=str(existing_empty))
    existing_nonempty = tmp_path / "nonempty"
    existing_nonempty.mkdir()
    (existing_nonempty / "file.txt").write_text("x", encoding="utf-8")
    with pytest.raises(SweepError):
        publish_package(result=result, output_path=str(existing_nonempty))


def test_publish_rejects_traversal_and_root(tmp_path) -> None:
    registry = _make_registry(("d0",))
    snapshot = _zero_snapshot(registry)
    result = generate_sweep(registry=registry, snapshot=snapshot, mode="forced", seed=1, message="m")
    with pytest.raises(SweepError):
        publish_package(result=result, output_path=str(tmp_path / "sub" / ".." / "evil"))
    with pytest.raises(SweepError):
        publish_package(result=result, output_path=".")


def test_publish_rejects_file_path(tmp_path) -> None:
    registry = _make_registry(("d0",))
    snapshot = _zero_snapshot(registry)
    result = generate_sweep(registry=registry, snapshot=snapshot, mode="forced", seed=1, message="m")
    file_path = tmp_path / "afile"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(SweepError):
        publish_package(result=result, output_path=str(file_path))


def test_publish_rejects_missing_parent(tmp_path) -> None:
    registry = _make_registry(("d0",))
    snapshot = _zero_snapshot(registry)
    result = generate_sweep(registry=registry, snapshot=snapshot, mode="forced", seed=1, message="m")
    with pytest.raises(SweepError):
        publish_package(result=result, output_path=str(tmp_path / "missing_parent" / "leaf"))


def test_publish_no_partial_dir_on_verify_failure(tmp_path, monkeypatch) -> None:
    registry = _make_registry(("d0",))
    snapshot = _zero_snapshot(registry)
    result = generate_sweep(registry=registry, snapshot=snapshot, mode="forced", seed=1, message="m")
    out = tmp_path / "partial"
    # Corrupt os.replace to raise, simulating a publish-time failure after staging.
    real_replace = os.replace

    def boom(src, dst):
        raise OSError("simulated publish failure")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        publish_package(result=result, output_path=str(out))
    monkeypatch.setattr(os, "replace", real_replace)
    assert not out.exists()
    # staging cleaned
    leftovers = [p for p in out.parent.iterdir() if p.name.startswith(".partial")]
    assert leftovers == []


# ---------------------------------------------------------------------------
# zero external calls / no pollution
# ---------------------------------------------------------------------------


def test_sweep_harness_imports_no_runtime_persistence_provider_writer() -> None:
    import app.chatbox.sweep_harness as mod
    src = inspect.getsource(mod)
    # Only inspect actual import statements, not docstring references.
    import_lines = [
        line.strip() for line in src.splitlines()
        if line.lstrip().startswith("import ") or line.lstrip().startswith("from ")
    ]
    import_text = "\n".join(import_lines)
    forbidden = [
        "agentlib", "agent_kernel", "semantic_trigger", "demos.scenarios",
        "field_runtime import FieldRuntime", "dialogue_service", "writer",
        "field_persistence", "dialogue_persistence", "perception_",
        "provider.transport", "HttpTransport", "StructureACaller",
    ]
    for token in forbidden:
        assert token not in import_text, f"sweep_harness imports forbidden {token!r}"


def test_socket_not_used_during_sweep(tmp_path, monkeypatch) -> None:
    calls: list[str] = []
    real_socket = socket.socket

    class GuardSocket(real_socket):
        def __init__(self, *a, **kw):
            calls.append("socket")
            raise AssertionError("socket must not be opened during sweep")

    monkeypatch.setattr(socket, "socket", GuardSocket)
    _publish_to(tmp_path, "pkg", 1, "forced", "m")
    assert calls == []


def test_production_db_sentinel_untouched(tmp_path) -> None:
    sentinel = tmp_path / "field.sqlite3"
    sentinel.write_bytes(b"SENTINEL")
    mtime_before = sentinel.stat().st_mtime_ns
    bytes_before = sentinel.read_bytes()
    _publish_to(tmp_path, "pkg", 1, "forced", "m")
    assert sentinel.read_bytes() == bytes_before
    assert sentinel.stat().st_mtime_ns == mtime_before


def test_runtime_and_snapshot_inputs_unchanged_after_sweep(tmp_path) -> None:
    registry = _make_registry(("d0", "d1", "d2"))
    snapshot = _zero_snapshot(registry)
    before_registry = _registry_scalar_tuples(registry)
    before_snapshot = _snapshot_scalar_tuples(snapshot)
    _publish_to(tmp_path, "pkg", 2, "forced", "m")
    assert _registry_scalar_tuples(registry) == before_registry
    assert _snapshot_scalar_tuples(snapshot) == before_snapshot


# ---------------------------------------------------------------------------
# Windows CLI subprocess
# ---------------------------------------------------------------------------


def test_cli_forced_subprocess_publishes_parseable_package(tmp_path) -> None:
    out = tmp_path / "cli-forced"
    proc = subprocess.run(
        [
            sys.executable, "-m", "app.chatbox.run_sweep",
            "--output", str(out),
            "--mode", "forced",
            "--seed", "90210",
            "--message", "固定同一句话",
        ],
        cwd=str(Path(__file__).resolve().parents[2]),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout.strip())
    assert summary["type"] == "sweep_published"
    assert summary["mode"] == "forced"
    assert summary["synthetic"] is True
    assert (out / "blind" / "samples.jsonl").exists()
    assert (out / "answer" / "answer-key.json").exists()
    assert (out / "manifest.json").exists()
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    for f in manifest["files"]:
        data = (out / f["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == f["sha256"]


def test_cli_alliance_subprocess_publishes_parseable_package(tmp_path) -> None:
    out = tmp_path / "cli-alliance"
    proc = subprocess.run(
        [
            sys.executable, "-m", "app.chatbox.run_sweep",
            "--output", str(out),
            "--mode", "alliance",
            "--seed", "90210",
            "--message", "固定同一句话",
        ],
        cwd=str(Path(__file__).resolve().parents[2]),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout.strip())
    assert summary["mode"] == "alliance"


def test_cli_rejects_existing_output_with_nonzero(tmp_path) -> None:
    out = tmp_path / "existing"
    out.mkdir()
    proc = subprocess.run(
        [
            sys.executable, "-m", "app.chatbox.run_sweep",
            "--output", str(out),
            "--mode", "forced",
            "--seed", "1",
        ],
        cwd=str(Path(__file__).resolve().parents[2]),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode != 0
    err = json.loads(proc.stderr.strip())
    assert err["type"] == "sweep_error"
