"""Task-card 12 Owner control plane, lifecycle, privacy, and smoke tests."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from app.chatbox.formal_operation import (
    ARTIFACT_NAMES,
    MANUAL_GATES,
    ControlStore,
    FormalOperationError,
    RunConfig,
    RunLease,
    RunManifest,
    append_gate,
    append_stop,
    artifact_paths,
    build_result,
    create_run,
    load_manifest,
    operation_status,
)
from app.chatbox.soak_detection import FORMAL_PROFILE, TEST_PROFILE
from app.chatbox.soak_evidence import profile_from_name
from app.chatbox.trajectory_service import TrajectoryHub


ROOT = Path(__file__).resolve().parents[2]


def _cli(*args: str, env: dict[str, str] | None = None, timeout: float = 30.0) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed = subprocess.run(
        [sys.executable, "-m", "app.chatbox.run_formal", *args],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout,
    )
    stream = completed.stdout if completed.returncode == 0 else completed.stderr
    assert stream.count("\n") == 1, (completed.stdout, completed.stderr)
    return completed, json.loads(stream)


def _fresh(tmp_path: Path, name: str = "run", **config) -> tuple[Path, RunManifest]:
    return create_run(str(tmp_path / name), RunConfig(**config))


@pytest.mark.parametrize("profile,expected", [("formal", FORMAL_PROFILE), ("smoke", TEST_PROFILE)])
def test_profile_mapping_and_no_duration_or_threshold_override(profile, expected) -> None:
    config = RunConfig(profile=profile)
    assert profile_from_name(config.soak_profile) == expected
    completed = subprocess.run(
        [sys.executable, "-m", "app.chatbox.run_formal", "start", "--help"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    for forbidden in ("duration", "tick", "cadence", "threshold", "offline-fake"):
        assert forbidden not in completed.stdout.casefold()
    top_level = subprocess.run(
        [sys.executable, "-m", "app.chatbox.run_formal", "--help"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    assert "_worker" not in top_level.stdout


def test_new_run_path_and_cap_validation_fail_closed(tmp_path, monkeypatch) -> None:
    run_dir, manifest = _fresh(tmp_path)
    assert set(manifest.artifacts) == set(ARTIFACT_NAMES)
    assert len(set(artifact_paths(run_dir, manifest).values())) == len(ARTIFACT_NAMES)
    with pytest.raises(FormalOperationError, match="run_dir_exists"):
        _fresh(tmp_path)
    with pytest.raises(FormalOperationError, match="parent traversal"):
        create_run(str(tmp_path / ".." / "escape"), RunConfig())
    with pytest.raises(FormalOperationError, match="non_loopback"):
        RunConfig(host="0.0.0.0")
    with pytest.raises(FormalOperationError, match="invalid_proactive_cap"):
        RunConfig(proactive_daily_limit=3)
    if hasattr(os, "symlink"):
        target = tmp_path / "target"
        target.mkdir()
        linked = tmp_path / "linked"
        try:
            linked.symlink_to(target, target_is_directory=True)
        except OSError:
            return
        with pytest.raises(FormalOperationError, match="symlink"):
            create_run(str(linked / "unsafe"), RunConfig())


def test_manifest_control_hash_schema_and_append_only_tamper_rejected(tmp_path) -> None:
    run_dir, manifest = _fresh(tmp_path)
    paths = artifact_paths(run_dir, manifest)
    control = ControlStore(paths["control"], manifest)
    control.append("launch_requested", launch_id="launch", payload={"action": "start"})
    control.close()
    with sqlite3.connect(paths["control"]) as conn:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("UPDATE control_events SET kind='tampered'")
    raw = json.loads(paths["manifest"].read_text("utf-8"))
    raw["config"]["port"] = 9999
    paths["manifest"].write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(FormalOperationError, match="manifest_invalid"):
        load_manifest(str(run_dir))


def test_lease_is_authority_pid_is_only_audit_and_interruption_is_derived(tmp_path) -> None:
    run_dir, manifest = _fresh(tmp_path)
    paths = artifact_paths(run_dir, manifest)
    control = ControlStore(paths["control"], manifest)
    control.append("launch_requested", launch_id="launch", payload={"action": "start", "pid": 12345})
    control.append("worker_started", launch_id="launch", payload={"pid": 12345})
    control.append("ready", launch_id="launch", payload={"type": "ready"})
    control.close()
    assert operation_status(str(run_dir))["process_state"] == "interrupted"
    lease = RunLease(paths["lease"], manifest.lease_token)
    assert lease.acquire()
    try:
        status = operation_status(str(run_dir))
        assert status["lease_state"] == "held"
        assert status["process_state"] == "running"
        assert status["pid_audit"] == 12345
        assert not RunLease(paths["lease"], manifest.lease_token).acquire()
    finally:
        lease.release()


def test_manual_gates_are_independent_append_only_and_formal_gate_is_absent(tmp_path) -> None:
    run_dir, _manifest = _fresh(tmp_path)
    status = operation_status(str(run_dir))
    assert status["manual_gates"] == {gate: "not_run" for gate in MANUAL_GATES}
    changed = append_gate(str(run_dir), "p2_owner_ten_turn", "passed")
    assert changed["manual_gates"]["p2_owner_ten_turn"] == "passed"
    assert changed["formal_48h"] == "not_run"
    with pytest.raises(FormalOperationError, match="invalid_gate"):
        append_gate(str(run_dir), "formal_48h", "passed")


def test_stop_is_launch_scoped_and_idempotent(tmp_path) -> None:
    run_dir, manifest = _fresh(tmp_path)
    paths = artifact_paths(run_dir, manifest)
    lease = RunLease(paths["lease"], manifest.lease_token)
    assert lease.acquire()
    control = ControlStore(paths["control"], manifest)
    control.append("launch_requested", launch_id="new", payload={"action": "start"})
    control.append("worker_started", launch_id="new", payload={"pid": 1})
    control.append("ready", launch_id="new", payload={"type": "ready"})
    try:
        first = append_stop(str(run_dir))
        second = append_stop(str(run_dir))
        assert first["stop_operation"] == "requested"
        assert second["stop_operation"] == "already_requested"
        assert control.stop_requested("new")
        assert not control.stop_requested("old")
    finally:
        control.close()
        lease.release()


class _Runtime:
    def __init__(self) -> None:
        self.frame = object()
        self.ticks = 0
    def tick(self) -> None:
        self.ticks += 1
    def last_committed_frame_proxy(self):
        return self.frame


@pytest.mark.parametrize("terminal", ["PASS", "FAIL", "EVIDENCE_CORRUPT"])
def test_strict_formal_terminal_states_stop_after_real_committed_frame(monkeypatch, terminal) -> None:
    class Observer:
        def on_committed_frame(self, frame):
            assert frame is runtime.frame
            return terminal
    runtime = _Runtime()
    hub = TrajectoryHub(runtime, soak_observer=Observer(), strict_formal_soak=True)  # type: ignore[arg-type]
    async def run() -> None:
        real_sleep = asyncio.sleep
        async def immediate(_delay):
            await real_sleep(0)
        monkeypatch.setattr("app.chatbox.trajectory_service.asyncio.sleep", immediate)
        await hub._tick_loop()
    asyncio.run(run())
    assert runtime.ticks == 1
    assert hub.terminal_soak_state == terminal
    assert hub.fatal_error is None


def test_default_observer_failure_is_isolated_but_strict_fails_closed(monkeypatch) -> None:
    class Observer:
        def on_committed_frame(self, _frame):
            raise RuntimeError("injected")
    async def run(strict: bool):
        runtime = _Runtime()
        hub = TrajectoryHub(runtime, soak_observer=Observer(), strict_formal_soak=strict)  # type: ignore[arg-type]
        calls = 0
        async def one_then_stop(_delay):
            nonlocal calls
            calls += 1
            if calls > 1:
                hub._stopping = True
        monkeypatch.setattr("app.chatbox.trajectory_service.asyncio.sleep", one_then_stop)
        await hub._tick_loop()
        return hub
    default = asyncio.run(run(False))
    assert default.fatal_error is None
    strict = asyncio.run(run(True))
    assert isinstance(strict.fatal_error, RuntimeError)
    assert strict.terminal_soak_state == "EVIDENCE_CORRUPT"


def test_real_provider_without_credential_rejected_before_directory_creation(tmp_path) -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("CHATBOX_")}
    run_dir = tmp_path / "real-missing"
    completed, error = _cli(
        "start", "--run-dir", str(run_dir), "--profile", "smoke", "--port", "0", "--enable-provider",
        env=env,
    )
    assert completed.returncode != 0
    assert error["code"] == "provider_credential_missing"
    assert not run_dir.exists()


def test_real_subprocess_offline_smoke_status_stop_result_and_privacy(tmp_path) -> None:
    sentinel = "P4_12_CREDENTIAL_SENTINEL_NEVER_PERSIST"
    env = dict(os.environ)
    env["CHATBOX_PROVIDER_API_KEY"] = sentinel
    run_dir = tmp_path / "managed-smoke"
    started, start = _cli(
        "start", "--run-dir", str(run_dir), "--profile", "smoke", "--port", "0", env=env,
    )
    assert started.returncode == 0
    assert start["process_state"] == "running"
    assert start["lease_state"] == "held"
    assert start["profile"] == "smoke" and start["soak_profile"] == "test"
    assert start["provider_mode"] == "offline"
    assert start["formal_48h"] == "not_run"
    assert start["ready"]["provider_state"] == "offline"
    assert start["ready"]["soak_profile"] == "test"
    status_done, status = _cli("status", "--run-dir", str(run_dir))
    assert status_done.returncode == 0 and status["process_state"] == "running"
    running_done, running = _cli("result", "--run-dir", str(run_dir))
    assert running_done.returncode == 0 and running["result_state"] == "running"
    assert running["sources"] == {}
    stopped_done, stopped = _cli("stop", "--run-dir", str(run_dir), timeout=40.0)
    assert stopped_done.returncode == 0
    assert stopped["lease_state"] == "idle" and stopped["process_state"] == "stopped"
    again_done, again = _cli("stop", "--run-dir", str(run_dir))
    assert again_done.returncode == 0 and again["stop_operation"] == "already_stopped"
    result_done, result = _cli("result", "--run-dir", str(run_dir))
    assert result_done.returncode == 0 and result["result_state"] == "verified"
    assert result["profile"] == "smoke" and result["soak_profile"] == "test"
    assert result["formal_48h"] == "not_run"
    assert result["manual_gates"] == {gate: "not_run" for gate in MANUAL_GATES}
    assert result["soak_state"] != "PASS"
    for name in ("field", "dialogue", "perception", "proactive", "soak_evidence", "soak_report"):
        assert result["sources"][name]["status"] == "verified"
    assert sentinel.encode() not in b"".join(path.read_bytes() for path in run_dir.iterdir() if path.is_file())
