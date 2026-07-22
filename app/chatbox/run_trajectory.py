"""Command-line entry for the localhost trajectory and P2 dialogue service."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import time
from collections.abc import Callable

from aiohttp import web

from app.chatbox.dialogue_persistence import DialoguePersistenceStore
from app.chatbox.dialogue_protocol import DIALOGUE_PROTOCOL_VERSION
from app.chatbox.dialogue_service import DialogueService
from app.chatbox.field_runtime import FieldRuntime
from app.chatbox.perception_bus import PerceptionBus
from app.chatbox.perception_ingress import PerceptionIngress
from app.chatbox.perception_persistence import PerceptionPersistenceStore
from app.chatbox.proactive_coordinator import ProactiveCoordinator
from app.chatbox.proactive_pressure import PressureConfig
from app.chatbox.proactive_store import CapConfig, ProactiveStore
from app.chatbox.soak_evidence import SoakObserver, profile_from_name
from app.chatbox.provider.config import load_provider_config
from app.chatbox.provider.registry import build_default_registry
from app.chatbox.provider.structure_a import StructureACaller
from app.chatbox.provider.transport import FakeTransport, HttpTransport
from app.chatbox.trajectory_protocol import TRAJECTORY_PROTOCOL_VERSION
from app.chatbox.trajectory_service import _loopback_host, create_trajectory_app


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Aphrodite localhost chatbox")
    parser.add_argument("--db", default="var/chatbox/field.sqlite3")
    parser.add_argument("--dialogue-db", default=None)
    parser.add_argument("--perception-db", default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--temperature", type=float, default=1.0)
    provider = parser.add_mutually_exclusive_group()
    provider.add_argument(
        "--offline-fake",
        action="store_true",
        help="use deterministic in-process replies; never accesses the network",
    )
    provider.add_argument(
        "--enable-provider",
        action="store_true",
        help="explicitly permit configured provider HTTP calls when a credential exists",
    )
    # P4.10 proactive hard-cap configuration.  Defaults are the phase-plan
    # floor; CLI may only tighten (lower daily limit, raise min interval, or
    # widen curfew).  Invalid/looser values fail-closed at startup.
    parser.add_argument(
        "--proactive-db",
        default=None,
        help="proactive companion SQLite path; defaults to <db>.proactive",
    )
    parser.add_argument(
        "--proactive-daily-limit",
        type=int,
        default=2,
        help="max proactive admissions per local day; 0..2 (phase-plan floor 2)",
    )
    parser.add_argument(
        "--proactive-min-interval-seconds",
        type=int,
        default=21600,
        help="min seconds between proactive admissions; >= 21600 (6h floor)",
    )
    parser.add_argument(
        "--proactive-curfew-start-hour",
        type=int,
        default=1,
        help="curfew start local hour; <= 1 (may only widen)",
    )
    parser.add_argument(
        "--proactive-curfew-end-hour",
        type=int,
        default=9,
        help="curfew end local hour; >= 9 (may only widen)",
    )
    parser.add_argument(
        "--soak-evidence-db",
        default=None,
        help="explicitly enable P4.11 observer with this companion SQLite path",
    )
    parser.add_argument(
        "--soak-report",
        default=None,
        help="P4.11 canonical JSON report path; required with --soak-evidence-db",
    )
    parser.add_argument(
        "--soak-profile",
        choices=("formal", "test"),
        default="test",
        help="P4.11 eligibility profile; test can never PASS",
    )
    return parser


def _dialogue_caller(args: argparse.Namespace) -> tuple[StructureACaller | None, str]:
    registry = build_default_registry()
    if args.offline_fake:
        config = load_provider_config(env={}, provider_id="deepseek")
        transport = FakeTransport(
            responder=lambda _request: (
                "我在。你可以慢慢说，不必先把它整理成一个结论。\n\n"
                "如果愿意，就从此刻最难放下的那一点开始。\n---\n{\"a\":0.12}"
            )
        )
        return StructureACaller(registry, config, transport), "available"
    if not args.enable_provider:
        return None, "offline"
    config = load_provider_config()
    if not config.api_key:
        return None, "offline"
    return StructureACaller(registry, config, HttpTransport()), "available"


async def _serve(
    args: argparse.Namespace,
    *,
    ready_callback: Callable[[dict[str, object]], None] | None = None,
    stop_predicate: Callable[[], bool] | None = None,
    strict_formal_soak: bool = False,
) -> int:
    if not _loopback_host(args.host):
        raise ValueError("host must be 127.0.0.1, localhost, or ::1")
    if isinstance(args.port, bool) or not 0 <= args.port <= 65535:
        raise ValueError("port must be in [0, 65535]")
    runtime = FieldRuntime.open(args.db)
    dialogue_store: DialoguePersistenceStore | None = None
    perception_store: PerceptionPersistenceStore | None = None
    proactive_store: ProactiveStore | None = None
    runner: web.AppRunner | None = None
    coordinator: ProactiveCoordinator | None = None
    soak_observer: SoakObserver | None = None
    try:
        if (args.soak_evidence_db is None) != (args.soak_report is None):
            raise ValueError("--soak-evidence-db and --soak-report must be supplied together")
        dialogue_path = args.dialogue_db or f"{args.db}.dialogue"
        dialogue_store = DialoguePersistenceStore(dialogue_path)
        # P3.7 production wiring: perception bus + ingress consume the five
        # server-trusted signals on the real chat path.  The bus applies
        # attractor moves through FieldRuntime.move_attractor only; it never
        # calls the provider or writes state directly.
        perception_path = getattr(args, "perception_db", None) or f"{args.db}.perception"
        perception_store = PerceptionPersistenceStore(perception_path)
        perception_bus = PerceptionBus(runtime, perception_store)
        perception_ingress = PerceptionIngress()
        caller, provider_state = _dialogue_caller(args)
        dialogue = DialogueService(
            runtime, dialogue_store, caller=caller, provider_state=provider_state,
            perception_bus=perception_bus, perception_ingress=perception_ingress,
        )
        # P4.10 proactive wiring: companion store + coordinator.  The cap is
        # constructed from CLI args; CapConfig rejects looser-than-floor values
        # at startup (fail-closed).  Proactive output is only enabled when a
        # provider is explicitly available; offline stays disabled.
        proactive_path = args.proactive_db or f"{args.db}.proactive"
        cap = CapConfig(
            daily_limit=args.proactive_daily_limit,
            min_interval_seconds=args.proactive_min_interval_seconds,
            curfew_start_hour=args.proactive_curfew_start_hour,
            curfew_end_hour=args.proactive_curfew_end_hour,
        )
        proactive_store = ProactiveStore(proactive_path, cap=cap)
        coordinator = ProactiveCoordinator(
            proactive_store,
            output=dialogue,
            utc_clock=time.time_ns,
            runtime_registry_proxy=runtime.registry_proxy,
            runtime_snapshot_proxy=runtime.snapshot_proxy,
            field_tick_proxy=lambda: runtime.field_tick,
        )
        if provider_state == "available" and caller is not None:
            dialogue.set_proactive_enabled(True)
        if args.soak_evidence_db is not None:
            soak_observer = SoakObserver.open(
                args.soak_evidence_db,
                args.soak_report,
                runtime.registry_proxy(),
                profile=profile_from_name(args.soak_profile),
            )
        app = create_trajectory_app(
            runtime,
            temperature=args.temperature,
            dialogue_service=dialogue,
            proactive_coordinator=coordinator,
            soak_observer=soak_observer,
            strict_formal_soak=strict_formal_soak,
        )
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, args.host, args.port)
        await site.start()
        sockets = site._server.sockets if site._server is not None else ()
        if not sockets:
            raise RuntimeError("server started without a listening socket")
        actual_port = int(sockets[0].getsockname()[1])
        display_host = f"[{args.host}]" if ":" in args.host and not args.host.startswith("[") else args.host
        ready_payload: dict[str, object] = {
            "type": "ready",
            "host": args.host,
            "port": actual_port,
            "protocols": [TRAJECTORY_PROTOCOL_VERSION, DIALOGUE_PROTOCOL_VERSION],
            "provider_state": provider_state,
            "proactive_enabled": bool(dialogue._proactive_enabled),
            "soak_enabled": soak_observer is not None,
            "soak_profile": args.soak_profile if soak_observer is not None else None,
            "soak_formal_48h": bool(soak_observer is not None and args.soak_profile == "formal"),
            "formal_48h_run": "not_run",
            "p4_human_gate": "not_run",
            "url": f"http://{display_host}:{actual_port}/",
        }
        sys.stdout.write(json.dumps(ready_payload, separators=(",", ":")) + "\n")
        sys.stdout.flush()
        if ready_callback is not None:
            ready_callback(dict(ready_payload))
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signame in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, signame, None)
            if sig is not None:
                try:
                    loop.add_signal_handler(sig, stop.set)
                except (NotImplementedError, RuntimeError):
                    pass
        hub = app["trajectory_hub"]
        while not stop.is_set() and hub.fatal_error is None:
            if stop_predicate is not None and stop_predicate():
                stop.set()
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.25)
            except asyncio.TimeoutError:
                pass
        if hub.fatal_error is not None:
            return 1
        if strict_formal_soak and hub.terminal_soak_state in {"FAIL", "EVIDENCE_CORRUPT"}:
            return 2
        return 0
    finally:
        cleanup_error: BaseException | None = None
        if runner is not None:
            try:
                await runner.cleanup()
            except BaseException as exc:
                cleanup_error = exc
        if dialogue_store is not None:
            try:
                dialogue_store.close()
            except BaseException:
                if cleanup_error is None:
                    raise
        if perception_store is not None:
            try:
                perception_store.close()
            except BaseException:
                if cleanup_error is None:
                    raise
        if proactive_store is not None:
            try:
                proactive_store.close()
            except BaseException:
                if cleanup_error is None:
                    raise
        if soak_observer is not None:
            try:
                soak_observer.close()
            except BaseException:
                if cleanup_error is None:
                    raise
        try:
            runtime.close()
        except BaseException:
            if cleanup_error is None:
                raise
        if cleanup_error is not None:
            raise cleanup_error


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_serve(args))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        sys.stderr.write(json.dumps({
            "type": "trajectory_service_error",
            "code": "service_failed",
            "detail": str(exc),
        }, separators=(",", ":")) + "\n")
        sys.stderr.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
