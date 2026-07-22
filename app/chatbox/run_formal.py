"""Owner CLI for task-card 12 managed formal and smoke runs."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from app.chatbox.formal_operation import (
    MANUAL_GATES,
    ControlStore,
    FormalOperationError,
    RunConfig,
    RunLease,
    append_gate,
    append_stop,
    artifact_paths,
    build_result,
    canonical_output,
    config_from_mapping,
    create_run,
    ensure_restartable,
    load_manifest,
    operation_status,
)
from app.chatbox.provider.config import load_provider_config
from app.chatbox.run_trajectory import _serve


START_WAIT_SECONDS = 20.0
STOP_WAIT_SECONDS = 10.0


def _add_run_dir(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-dir", required=True, help="explicit managed run directory")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Own a formal or smoke Aphrodite run")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="create a new run directory and start its worker")
    _add_run_dir(start)
    start.add_argument("--profile", choices=("formal", "smoke"), default="formal")
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=8765)
    start.add_argument("--enable-provider", action="store_true")
    start.add_argument("--temperature", type=float, default=1.0)
    start.add_argument("--proactive-daily-limit", type=int, default=2)
    start.add_argument("--proactive-min-interval-seconds", type=int, default=21600)
    start.add_argument("--proactive-curfew-start-hour", type=int, default=1)
    start.add_argument("--proactive-curfew-end-hour", type=int, default=9)

    for name in ("status", "stop", "restart", "result"):
        child = sub.add_parser(name)
        _add_run_dir(child)

    gate = sub.add_parser("gate")
    _add_run_dir(gate)
    gate.add_argument("gate", choices=MANUAL_GATES)
    gate.add_argument("state", choices=("passed", "failed"))

    worker = sub.add_parser("_worker", help=argparse.SUPPRESS)
    _add_run_dir(worker)
    worker.add_argument("--launch-id", required=True)
    # Keep the transport command parseable for the spawned child without
    # advertising it as an Owner operation. ``argparse.SUPPRESS`` alone is
    # rendered as ``==SUPPRESS==`` for subcommands, and argparse builds the
    # usage choice list from the public choices mapping separately.
    sub._choices_actions = [
        action for action in sub._choices_actions if action.dest != "_worker"
    ]
    visible_commands = tuple(name for name in sub.choices if name != "_worker")
    sub.metavar = "{" + ",".join(visible_commands) + "}"
    return parser


def _config(args: argparse.Namespace) -> RunConfig:
    return RunConfig(
        profile=args.profile,
        host=args.host,
        port=args.port,
        provider_mode="real" if args.enable_provider else "offline",
        temperature=args.temperature,
        proactive_daily_limit=args.proactive_daily_limit,
        proactive_min_interval_seconds=args.proactive_min_interval_seconds,
        proactive_curfew_start_hour=args.proactive_curfew_start_hour,
        proactive_curfew_end_hour=args.proactive_curfew_end_hour,
    )


def _validate_provider(config: RunConfig) -> None:
    if config.provider_mode == "real" and not load_provider_config().api_key:
        raise FormalOperationError(
            "provider_credential_missing",
            "real provider was enabled but no configured credential resolved",
        )


def _spawn(run_dir: Path, manifest, *, action: str) -> dict[str, object]:
    config = config_from_mapping(manifest.config)
    _validate_provider(config)
    paths = artifact_paths(run_dir, manifest)
    launch_id = uuid.uuid4().hex
    command = [
        sys.executable, "-m", "app.chatbox.run_formal", "_worker",
        "--run-dir", str(run_dir), "--launch-id", launch_id,
    ]
    creationflags = 0
    start_new_session = False
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        start_new_session = True
    control = ControlStore(paths["control"], manifest)
    try:
        control.append(
            "launch_requested", launch_id=launch_id,
            payload={"action": action},
        )
    finally:
        control.close()
    try:
        with open(paths["worker_stdout"], "ab", buffering=0) as stdout, \
                open(paths["worker_stderr"], "ab", buffering=0) as stderr:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                shell=False,
                close_fds=True,
                creationflags=creationflags,
                start_new_session=start_new_session,
            )
    except BaseException as exc:
        control = ControlStore(paths["control"], manifest)
        try:
            control.append(
                "failed", launch_id=launch_id,
                payload={"exit_code": 3, "reason": "worker_spawn_failed", "error_type": type(exc).__name__},
            )
        finally:
            control.close()
        raise FormalOperationError("worker_spawn_failed", "worker process could not be started") from exc
    deadline = time.monotonic() + START_WAIT_SECONDS
    while time.monotonic() < deadline:
        status = operation_status(str(run_dir))
        if status["launch_id"] == launch_id and status["ready"] is not None:
            return {"operation": action, **status}
        if process.poll() is not None:
            status = operation_status(str(run_dir))
            raise FormalOperationError(
                "worker_start_failed", f"worker exited before ready (exit_code={process.returncode})"
            )
        time.sleep(0.1)
    raise FormalOperationError("worker_start_timeout", "worker did not publish ready before timeout")


def _start(args: argparse.Namespace) -> dict[str, object]:
    config = _config(args)
    _validate_provider(config)
    run_dir, manifest = create_run(args.run_dir, config)
    return _spawn(run_dir, manifest, action="start")


def _restart(run_dir_raw: str) -> dict[str, object]:
    run_dir, manifest = ensure_restartable(run_dir_raw)
    return _spawn(run_dir, manifest, action="restart")


def _stop(run_dir_raw: str) -> dict[str, object]:
    status = append_stop(run_dir_raw)
    if status.get("stop_operation") == "already_stopped":
        return status
    deadline = time.monotonic() + STOP_WAIT_SECONDS
    while time.monotonic() < deadline:
        current = operation_status(run_dir_raw)
        if current["lease_state"] == "idle":
            return {**current, "stop_operation": "completed"}
        time.sleep(0.1)
    return {**operation_status(run_dir_raw), "stop_operation": "pending"}


def _worker_args(config: RunConfig, paths: dict[str, Path]) -> argparse.Namespace:
    return argparse.Namespace(
        db=str(paths["field"]),
        dialogue_db=str(paths["dialogue"]),
        perception_db=str(paths["perception"]),
        proactive_db=str(paths["proactive"]),
        soak_evidence_db=str(paths["soak_evidence"]),
        soak_report=str(paths["soak_report"]),
        soak_profile=config.soak_profile,
        host=config.host,
        port=config.port,
        temperature=config.temperature,
        offline_fake=False,
        enable_provider=config.provider_mode == "real",
        proactive_daily_limit=config.proactive_daily_limit,
        proactive_min_interval_seconds=config.proactive_min_interval_seconds,
        proactive_curfew_start_hour=config.proactive_curfew_start_hour,
        proactive_curfew_end_hour=config.proactive_curfew_end_hour,
    )


def _worker(run_dir_raw: str, launch_id: str) -> int:
    run_dir, manifest = load_manifest(run_dir_raw)
    paths = artifact_paths(run_dir, manifest)
    config = config_from_mapping(manifest.config)
    _validate_provider(config)
    lease = RunLease(paths["lease"], manifest.lease_token)
    if not lease.acquire():
        raise FormalOperationError("run_active", "another worker owns the run lease")
    control = ControlStore(paths["control"], manifest)
    last_stop_check = 0.0
    stop_cached = False

    def stop_predicate() -> bool:
        nonlocal last_stop_check, stop_cached
        now = time.monotonic()
        if not stop_cached and now - last_stop_check >= 0.5:
            last_stop_check = now
            stop_cached = control.stop_requested(launch_id)
        return stop_cached

    def ready(payload: dict[str, object]) -> None:
        # Only the credential-free payload emitted by _serve is persisted.
        control.append("ready", launch_id=launch_id, payload=payload)

    try:
        control.append("worker_started", launch_id=launch_id, payload={"pid": os.getpid()})
        exit_code = asyncio.run(_serve(
            _worker_args(config, paths),
            ready_callback=ready,
            stop_predicate=stop_predicate,
            strict_formal_soak=config.profile == "formal",
        ))
        if exit_code == 0:
            reason = "stop_requested" if stop_cached else (
                "formal_pass" if config.profile == "formal" else "service_completed"
            )
            control.append("exited", launch_id=launch_id, payload={"exit_code": 0, "reason": reason})
        else:
            reason = "formal_detector_terminal" if exit_code == 2 else "runtime_failure"
            control.append("failed", launch_id=launch_id, payload={"exit_code": exit_code, "reason": reason})
        return exit_code
    except BaseException as exc:
        try:
            control.append(
                "failed", launch_id=launch_id,
                payload={"exit_code": 3, "reason": "worker_exception", "error_type": type(exc).__name__},
            )
        except BaseException:
            pass
        raise
    finally:
        control.close()
        lease.release()


def _dispatch(args: argparse.Namespace) -> dict[str, object] | int:
    if args.command == "start":
        return _start(args)
    if args.command == "status":
        return operation_status(args.run_dir)
    if args.command == "stop":
        return _stop(args.run_dir)
    if args.command == "restart":
        return _restart(args.run_dir)
    if args.command == "result":
        return build_result(args.run_dir)
    if args.command == "gate":
        return append_gate(args.run_dir, args.gate, args.state)
    if args.command == "_worker":
        return _worker(args.run_dir, args.launch_id)
    raise AssertionError(args.command)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = _dispatch(args)
        if isinstance(result, int):
            return result
        sys.stdout.write(canonical_output(result) + "\n")
        sys.stdout.flush()
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        if isinstance(exc, FormalOperationError):
            code, detail = exc.code, exc.detail
        else:
            code, detail = "operation_failed", type(exc).__name__
        sys.stderr.write(canonical_output({"type": "formal_operation_error", "code": code, "detail": detail}) + "\n")
        sys.stderr.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
