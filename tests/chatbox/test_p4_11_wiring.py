"""P4.11 opt-in committed-frame wiring and offline CLI tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys

from app.chatbox.field_dynamics import DimensionRegistration, SeededGaussianRngFactory
from app.chatbox.field_runtime import FieldRuntime
from app.chatbox.soak_evidence import SoakObserver
from app.chatbox.trajectory_service import TrajectoryHub


def _registration() -> DimensionRegistration:
    return DimensionRegistration(
        dim_id="dim-0", temporary_name="dim-0", birth_time=1.0, strength=1.0,
        trigger_count=0, birth_bias=0.0, fast_e_fold_s=600.0,
        ou_correlation_e_fold_s=10_800.0, ou_acceleration_sigma=4e-7,
        soft_boundary_start=1.0, soft_boundary_width=0.25,
        soft_boundary_strength=(1 / 120) ** 2,
    )


def test_real_committed_frame_observer_is_one_for_one(tmp_path) -> None:
    runtime = FieldRuntime.open(
        str(tmp_path / "field.sqlite3"), birth_registry=(_registration(),),
        birth_rng_factory=SeededGaussianRngFactory(17),
    )
    observer = SoakObserver.open(str(tmp_path / "soak.sqlite3"), str(tmp_path / "report.json"), runtime.registry_proxy())
    try:
        runtime.tick()
        committed = runtime.last_committed_frame_proxy()
        before = runtime.snapshot_proxy()
        observer.on_committed_frame(committed)
        after = runtime.snapshot_proxy()
        assert before == after
        assert observer._store._conn.execute("SELECT COUNT(*) FROM soak_frames").fetchone()[0] == 1
    finally:
        observer.close()
        runtime.close()


def test_hub_passes_actual_frame_and_observer_failure_does_not_escape(tmp_path, monkeypatch) -> None:
    runtime = FieldRuntime.open(
        str(tmp_path / "field.sqlite3"), birth_registry=(_registration(),),
        birth_rng_factory=SeededGaussianRngFactory(18),
    )
    seen = []
    class Observer:
        def on_committed_frame(self, value):
            seen.append(value)
            raise RuntimeError("injected")
        def close(self): pass
    hub = TrajectoryHub(runtime, soak_observer=Observer())
    async def one_tick():
        sleeps = 0
        async def fake_sleep(_seconds):
            nonlocal sleeps
            sleeps += 1
            if sleeps > 1:
                hub._stopping = True
        monkeypatch.setattr("app.chatbox.trajectory_service.asyncio.sleep", fake_sleep)
        await hub._tick_loop()
    try:
        asyncio.run(one_tick())
        assert len(seen) == 1
        assert seen[0] is runtime.last_committed_frame_proxy()
        assert hub.fatal_error is None
    finally:
        runtime.close()


def test_offline_query_cli_outputs_one_canonical_line(tmp_path) -> None:
    runtime = FieldRuntime.open(
        str(tmp_path / "field.sqlite3"), birth_registry=(_registration(),),
        birth_rng_factory=SeededGaussianRngFactory(19),
    )
    db = str(tmp_path / "soak.sqlite3")
    report = str(tmp_path / "report.json")
    observer = SoakObserver.open(db, report, runtime.registry_proxy())
    runtime.tick()
    observer.on_committed_frame(runtime.last_committed_frame_proxy())
    observer.close()
    runtime.close()
    completed = subprocess.run([
        sys.executable, "-m", "app.chatbox.run_soak_detection",
        "--evidence-db", db, "--report", report,
    ], check=True, text=True, capture_output=True)
    assert completed.stderr == ""
    assert completed.stdout.count("\n") == 1
    result = json.loads(completed.stdout)
    assert result["formal_48h"] is False
    assert result["formal_48h_run"] == "not_run"
    assert result["p4_human_gate"] == "not_run"


def test_soak_modules_have_no_quarantine_provider_writer_or_timer_imports() -> None:
    import app.chatbox.soak_detection as core
    import app.chatbox.soak_evidence as evidence
    for module in (core, evidence):
        source = Path(module.__file__).read_text("utf-8")
        for forbidden in ("agentlib", "agent_kernel", "semantic_trigger", "demos.scenarios",
                          "app.chatbox.provider", "app.chatbox.writer", "move_attractor",
                          "asyncio.sleep", "Timer"):
            assert forbidden not in source
