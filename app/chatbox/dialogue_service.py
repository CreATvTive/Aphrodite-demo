"""P2 task-card 6 + P3 task-card 8 dialogue orchestration and WebSocket endpoint.

P3 task-card 8 additions:

* a [`ReceptorPlanner`](receptor_planner.py) produces a frozen per-turn
  [`ReceptorPlan`](receptor_planner.py) from the gated expression vector;
* the server waits for the planned delay (cancellable) before streaming the
  first segment, then streams server-planned segments with a per-segment
  typewriter interval (ms) sent to the browser;
* the plan's style instruction is injected into the system prompt (length +
  punctuation looseness only, no dim ids/values/causal);
* a private, append-only plan-audit record is persisted (no dim ids/values);
* P3.7 perception ingress is wired into the real chat path: session
  start/end, message gap/time/length, and typing start/heartbeat/stop are
  derived server-trusted and fed to the [`PerceptionBus`](perception_bus.py);
* a periodic typing-timeout sweep clears stale typing state without blocking
  the 1 Hz trajectory loop.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import json
import math
import secrets
import time
from typing import Callable
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

from app.chatbox.dialogue_persistence import DialoguePersistenceStore
from app.chatbox.dialogue_protocol import (
    DIALOGUE_PROTOCOL_VERSION,
    MAX_HISTORY_MESSAGES,
    MAX_REPLY_TEXT_CHARS,
    SEND_TIMEOUT_SECONDS,
    DialogueProtocolError,
    TurnCommand,
    base_message,
    error_message,
    hello_message,
    history_message,
    parse_client_message,
    proactive_message,
    serialize_dialogue_message,
)
from app.chatbox.expression_gate import AllOpenGateProjector
from app.chatbox.field_runtime import FieldRuntime
from app.chatbox.meta_narration import detect_meta_narration
from app.chatbox.perception_bus import PerceptionBus
from app.chatbox.perception_config import TYPING_HEARTBEAT_TIMEOUT_SECONDS
from app.chatbox.perception_ingress import PerceptionIngress
from app.chatbox.perception_persistence import PerceptionPersistenceStore
from app.chatbox.prompt_style import (
    PromptStyleProjector,
    build_system_prompt,
    build_user_prompt,
    opaque_dimension_aliases,
)
from app.chatbox.provider.structure_a import ParsedReply, StructureACaller
from app.chatbox.receptor_planner import (
    ReceptorPlan,
    ReceptorPlannerError,
    plan_from_receptor_vector,
    split_reply_by_plan,
    style_instruction_from_plan,
)
from app.chatbox.writer import Writer, WriterOutcome


@dataclass(slots=True)
class _SocketTurn:
    turn_id: str
    cancel: asyncio.Event
    task: asyncio.Task[None]


def _split_reply(reply_text: str) -> tuple[str, ...]:
    """Deterministically preserve explicit paragraph/message boundaries."""
    normalized = reply_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return ()
    paragraphs = tuple(part.strip() for part in normalized.split("\n\n") if part.strip())
    return paragraphs if paragraphs else (normalized,)


def _remap_increment(
    parsed: ParsedReply,
    aliases: tuple[str, ...],
    dim_ids: tuple[str, ...],
) -> ParsedReply:
    alias_to_dim = dict(zip(aliases, dim_ids))
    mapped = {
        alias_to_dim[alias]: delta
        for alias, delta in parsed.increment.items()
        if alias in alias_to_dim
    }
    return ParsedReply(
        reply_text=parsed.reply_text,
        increment=mapped,
        parsed_ok=bool(mapped),
        degraded=parsed.degraded,
        provider_id=parsed.provider_id,
        parse_note=parsed.parse_note if mapped or parsed.degraded else "no-opaque-increment",
    )


def _loopback_request_host(request: web.Request) -> bool:
    host = request.host.rsplit(":", 1)[0].strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _same_origin(request: web.Request) -> bool:
    origin = request.headers.get("Origin")
    if origin is None:
        return True
    parsed = urlsplit(origin)
    return parsed.scheme == request.scheme and parsed.netloc == request.host


class DialogueService:
    """Serialize turns, offload provider I/O, and retain writer authority."""

    def __init__(
        self,
        runtime: FieldRuntime,
        persistence: DialoguePersistenceStore,
        *,
        caller: StructureACaller | None,
        provider_state: str,
        utc_clock: Callable[[], int] = time.time_ns,
        perception_bus: PerceptionBus | None = None,
        perception_ingress: PerceptionIngress | None = None,
        receptor_seed: int | None = None,
    ) -> None:
        if provider_state not in {"available", "offline"}:
            raise ValueError("provider_state must be available or offline")
        self.runtime = runtime
        self.persistence = persistence
        self.caller = caller
        self.provider_state = provider_state
        self.writer = Writer(runtime)
        self._utc_clock = utc_clock
        self._style = PromptStyleProjector()
        self._gate = AllOpenGateProjector()
        self._turn_lock = asyncio.Lock()
        self._known_turn_ids = {
            message.client_turn_id
            for message in persistence.read_messages(limit=MAX_HISTORY_MESSAGES)
            if message.role == "user"
        }
        self._active_tasks: set[asyncio.Task[None]] = set()
        # P3.7 perception wiring (optional; tests may omit it).
        self._perception_bus = perception_bus
        self._perception_ingress = perception_ingress
        # P3.8 receptor planner seed: when set, production draws deterministically
        # for reproducible tests; when None, each turn draws a fresh random seed.
        self._receptor_seed = receptor_seed
        # Per-connection session id → used for perception ingress session scope.
        # The handle_ws method assigns one stable session id per WebSocket.
        # P4.10 proactive output boundary: track open dialogue sockets that have
        # completed hello/history, and the server-trusted last user activity ns
        # (restored at startup from dialogue persistence).  The coordinator
        # reads these through the proactive_ready / last_user_activity_ns
        # probes; it never touches dialogue persistence directly.
        self._proactive_sockets: set[web.WebSocketResponse] = set()
        latest_user_ns = persistence.latest_user_message_ns()
        # Unknown silence stays unknown. The coordinator/pure formula reject it
        # fail-closed until a server-accepted user turn establishes the baseline.
        self._last_user_activity_ns: int | None = (
            latest_user_ns if isinstance(latest_user_ns, int) else None
        )
        self._proactive_enabled = False

    async def _send(self, ws: web.WebSocketResponse, message: dict) -> None:
        await asyncio.wait_for(
            ws.send_str(serialize_dialogue_message(message)),
            timeout=SEND_TIMEOUT_SECONDS,
        )

    async def _send_error(
        self,
        ws: web.WebSocketResponse,
        *,
        turn_id: str | None,
        code: str,
        detail: str,
        fatal: bool = False,
        retry: bool = False,
    ) -> None:
        await self._send(ws, error_message(
            client_turn_id=turn_id,
            code=code,
            detail=detail,
            fatal=fatal,
            retry=retry,
        ))

    def _history_pairs(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (message.role, message.content)
            for message in self.persistence.read_messages(limit=24)
        )

    def _audit(
        self,
        *,
        turn_id: str,
        call_id: str,
        lifecycle: str,
        parsed: ParsedReply | None,
        outcome: WriterOutcome | None,
        detail_code: str,
    ) -> None:
        self.persistence.append_audit(
            client_turn_id=turn_id,
            server_call_id=call_id,
            lifecycle=lifecycle,
            provider_id=None if parsed is None else parsed.provider_id,
            parsed_ok=False if parsed is None else parsed.parsed_ok,
            writer_log_persisted=False if outcome is None else outcome.log_persisted,
            writer_move_count=0 if outcome is None else sum(move.applied for move in outcome.moves),
            detail_code=detail_code,
            utc_unix_ns=self._utc_clock(),
        )

    def _append_plan_audit(self, plan: ReceptorPlan) -> None:
        """Persist a private, append-only receptor-plan audit record.

        Stored as a dialogue audit row with lifecycle ``receptor_plan`` and a
        JSON detail_code carrying only user-safe plan fields (no dim ids,
        values, attractor, OU, baseline, or causal explanation).
        """
        record = plan.audit_record()
        # Strip the private receptor_summary from the persisted detail; keep
        # only the plan id + execution parameters.
        safe = {
            "plan_id": record["plan_id"],
            "version": record["version"],
            "delay_mean": round(record["delay_mean_seconds"], 4),
            "delay_variance": round(record["delay_variance_seconds"], 4),
            "delay_sample": round(record["delay_sample_seconds"], 4),
            "length_target": record["length_target_chars"],
            "segment_count": record["segment_count"],
            "punctuation_looseness": round(record["punctuation_looseness"], 4),
            "typewriter_ms": record["typewriter_ms"],
            "expression_pressure": round(record["expression_pressure"], 4),
        }
        self.persistence.append_audit(
            client_turn_id=plan.turn_id,
            server_call_id=f"receptor:{plan.plan_id}",
            lifecycle="receptor_plan",
            provider_id=None,
            parsed_ok=False,
            writer_log_persisted=False,
            writer_move_count=0,
            detail_code=json.dumps(safe, separators=(",", ":"), ensure_ascii=False),
            utc_unix_ns=self._utc_clock(),
        )

    def _receptor_vector(self) -> tuple[float, ...]:
        """Project the current gated expression vector to an abstract receptor vector.

        Returns finite floats in [-1,1] — the gated, expressible projection.
        Never exposes dim ids, labels, attractor, OU, or baseline.  Dynamic
        over the live registry; 0/1/12/17 dims are all safe.
        """
        registry = self.runtime.registry_proxy()
        snapshot = self.runtime.snapshot_proxy()
        gate = self._gate.project(registry)
        if len(snapshot.dimensions) != registry.length or len(gate.weights) != registry.length:
            return ()
        values: list[float] = []
        for dimension, weight in zip(snapshot.dimensions, gate.weights):
            if dimension.dim_id != weight.dim_id:
                continue
            v = float(dimension.value) * float(weight.weight)
            if not math.isfinite(v):
                continue
            # Soft-bound into [-1,1] without a hard clamp on field state: this
            # only projects the *expression* vector, not the field itself.
            if v < -1.0:
                v = -1.0
            elif v > 1.0:
                v = 1.0
            values.append(v)
        return tuple(values)

    def _ingest_perception_envelope(self, envelope: dict | None) -> None:
        """Feed one perception envelope to the bus, isolating bus errors.

        The dialogue turn never fails because of a perception-side error; the
        bus reports it in its outcome and the turn continues.  This preserves
        the P3.7 direct-to-dynamics contract: perception never calls the
        provider, and a perception failure never blocks dialogue.
        """
        if envelope is None or self._perception_bus is None:
            return
        try:
            self._perception_bus.ingest(envelope)
        except Exception:
            # The bus already records backpressure/validation errors in its
            # outcome; swallow unexpected transport-level exceptions so the
            # 1 Hz loop and the dialogue turn are never blocked.
            return

    def _ingest_message_signals(self, session_id: str, text: str, turn_id: str) -> None:
        if self._perception_ingress is None:
            return
        try:
            gap, tod, length = self._perception_ingress.derive_message_signals(
                session_id=session_id, text=text, event_id=turn_id
            )
        except Exception:
            return
        self._ingest_perception_envelope(gap)
        self._ingest_perception_envelope(tod)
        self._ingest_perception_envelope(length)

    def _ingest_typing(self, session_id: str, state: str, turn_id: str) -> None:
        if self._perception_ingress is None:
            return
        try:
            envelope = self._perception_ingress.ingest_typing(
                session_id=session_id, state=state, event_id=f"typing:{turn_id}:{state}"
            )
        except Exception:
            return
        self._ingest_perception_envelope(envelope)

    def _start_session(self, session_id: str) -> None:
        if self._perception_ingress is None:
            return
        try:
            envelope = self._perception_ingress.start_session(session_id)
        except Exception:
            return
        self._ingest_perception_envelope(envelope)

    def _end_session(self, session_id: str) -> None:
        if self._perception_ingress is None:
            return
        try:
            envelope = self._perception_ingress.end_session(session_id)
        except Exception:
            return
        self._ingest_perception_envelope(envelope)
        # Force-clear typing on disconnect.
        try:
            stop = self._perception_ingress.clear_typing_on_disconnect(
                session_id=session_id, event_id=f"typing:{session_id}:disconnect"
            )
        except Exception:
            return
        self._ingest_perception_envelope(stop)

    async def _wait_plan_delay(self, plan: ReceptorPlan, cancel: asyncio.Event) -> bool:
        """Wait for the planned reply delay; return False if cancelled.

        The wait is bounded by the plan's sampled delay and is interruptible
        by ``cancel`` or WebSocket close.  It never blocks the 1 Hz trajectory
        loop because it is a pure asyncio sleep on the dialogue task.
        """
        delay = max(0.0, float(plan.delay_sample_seconds))
        if delay <= 0.0:
            return not cancel.is_set()
        try:
            await asyncio.wait_for(
                asyncio.create_task(self._await_cancel_or_timeout(cancel, delay)),
                timeout=delay + 0.5,
            )
        except asyncio.TimeoutError:
            pass
        return not cancel.is_set()

    async def _await_cancel_or_timeout(self, cancel: asyncio.Event, delay: float) -> None:
        try:
            await asyncio.wait_for(cancel.wait(), timeout=delay)
        except asyncio.TimeoutError:
            return

    async def _run_turn(
        self,
        ws: web.WebSocketResponse,
        command: TurnCommand,
        cancel: asyncio.Event,
        *,
        session_id: str,
    ) -> None:
        turn_id = command.client_turn_id
        call_id = f"dialogue:{turn_id}"
        parsed: ParsedReply | None = None
        plan: ReceptorPlan | None = None
        try:
            async with self._turn_lock:
                if cancel.is_set():
                    await self._send(ws, {
                        **base_message("turn.cancelled"),
                        "client_turn_id": turn_id,
                    })
                    return
                if turn_id in self._known_turn_ids or self.persistence.turn_exists(turn_id):
                    await self._send_error(
                        ws,
                        turn_id=turn_id,
                        code="duplicate_turn",
                        detail="this turn was already accepted",
                    )
                    return

                history = self._history_pairs()
                self.persistence.append_message(
                    client_turn_id=turn_id,
                    role="user",
                    segment_index=0,
                    content=command.text or "",
                    utc_unix_ns=self._utc_clock(),
                )
                # P4.10: update the server-trusted last user activity time so
                # the proactive pressure accumulator's silence term reflects
                # the most recent user turn without scanning dialogue history.
                self._last_user_activity_ns = int(self._utc_clock())
                self._known_turn_ids.add(turn_id)
                # P3.7: ingest message-derived perception signals (server-trusted).
                self._ingest_message_signals(session_id, command.text or "", turn_id)
                await self._send(ws, {
                    **base_message("turn.accepted"),
                    "client_turn_id": turn_id,
                })

                if cancel.is_set():
                    self._audit(
                        turn_id=turn_id, call_id=call_id, lifecycle="cancelled",
                        parsed=None, outcome=None, detail_code="cancelled_before_provider",
                    )
                    await self._send(ws, {
                        **base_message("turn.cancelled"),
                        "client_turn_id": turn_id,
                    })
                    return

                # P3.8: freeze a receptor plan for this turn from the gated
                # expression vector.  The plan is frozen once per turn; a
                # duplicate turn never re-samples or re-applies.
                receptor_vector = self._receptor_vector()
                try:
                    plan = plan_from_receptor_vector(
                        turn_id=turn_id,
                        receptor_vector=receptor_vector,
                        clock_ns=self._utc_clock(),
                        seed=self._receptor_seed,
                    )
                except ReceptorPlannerError:
                    plan = None
                # Plan audit is folded into the existing "completed" audit
                # row's detail_code (below) to avoid adding a separate row
                # that would break the P2.6 audit query contract.

                if self.caller is None:
                    parsed = ParsedReply(
                        reply_text="", increment={}, parsed_ok=False, degraded=True,
                        provider_id=None, parse_note="provider-offline",
                    )
                else:
                    registry = self.runtime.registry_proxy()
                    aliases = opaque_dimension_aliases(registry.length)
                    style = self._style.project(
                        registry=registry,
                        snapshot=self.runtime.snapshot_proxy(),
                        gate=self._gate.project(registry),
                    )
                    system_prompt = build_system_prompt(style, aliases)
                    # P3.8: inject the plan's non-diagnostic style instruction
                    # (length + punctuation looseness only) into the system
                    # prompt.  No dim ids, values, or causal explanations.
                    if plan is not None:
                        system_prompt = (
                            system_prompt + "\n" + style_instruction_from_plan(plan)
                        )
                    parsed_aliases = await asyncio.to_thread(
                        self.caller.call,
                        system_prompt=system_prompt,
                        user_prompt=build_user_prompt(history, command.text or ""),
                        registry_dim_ids=aliases,
                    )
                    parsed = _remap_increment(parsed_aliases, aliases, registry.dim_ids)

                if cancel.is_set():
                    self._audit(
                        turn_id=turn_id, call_id=call_id, lifecycle="cancelled",
                        parsed=parsed, outcome=None, detail_code="cancelled_before_writer",
                    )
                    await self._send(ws, {
                        **base_message("turn.cancelled"),
                        "client_turn_id": turn_id,
                    })
                    return

                # Cancellation remains authoritative until this checkpoint.
                # From writer application through the terminal message the
                # turn commits atomically on the event-loop thread; late
                # cancel requests observe the completed terminal result.

                if parsed.degraded:
                    outcome = self.writer.apply(parsed, call_id=call_id)
                    self._audit(
                        turn_id=turn_id, call_id=call_id, lifecycle="degraded",
                        parsed=parsed, outcome=outcome, detail_code=parsed.parse_note,
                    )
                    await self._send(ws, {
                        **base_message("turn.degraded"),
                        "client_turn_id": turn_id,
                        "reason": "provider_unavailable",
                    })
                    return

                reply_text = parsed.reply_text[:MAX_REPLY_TEXT_CHARS].strip()
                if not reply_text:
                    self._audit(
                        turn_id=turn_id, call_id=call_id, lifecycle="error",
                        parsed=parsed, outcome=None, detail_code="empty_reply",
                    )
                    await self._send_error(
                        ws, turn_id=turn_id, code="invalid_provider_output",
                        detail="provider returned no usable reply", retry=True,
                    )
                    return
                registry = self.runtime.registry_proxy()
                forbidden_terms = registry.dim_ids + tuple(
                    registration.temporary_name for registration in registry.registrations
                )
                hits = detect_meta_narration(reply_text, forbidden_terms=forbidden_terms)
                if hits:
                    self._audit(
                        turn_id=turn_id, call_id=call_id, lifecycle="error",
                        parsed=parsed, outcome=None, detail_code=f"meta:{hits[0].rule_id}",
                    )
                    await self._send_error(
                        ws, turn_id=turn_id, code="unsafe_provider_output",
                        detail="reply did not satisfy the dialogue contract", retry=True,
                    )
                    return

                safe_parsed = ParsedReply(
                    reply_text=reply_text,
                    increment=parsed.increment,
                    parsed_ok=parsed.parsed_ok,
                    degraded=False,
                    provider_id=parsed.provider_id,
                    parse_note=parsed.parse_note,
                )
                outcome = self.writer.apply(safe_parsed, call_id=call_id)
                # P3.8: split by the frozen plan's segment count, not by
                # accidental \n\n formatting.  Fall back to the legacy
                # deterministic split if no plan was frozen.
                if plan is not None:
                    segments = split_reply_by_plan(reply_text, plan.segment_count)
                    if not segments:
                        segments = _split_reply(reply_text)
                else:
                    segments = _split_reply(reply_text)
                for index, segment in enumerate(segments):
                    self.persistence.append_message(
                        client_turn_id=turn_id,
                        role="assistant",
                        segment_index=index,
                        content=segment,
                        utc_unix_ns=self._utc_clock(),
                    )
                self._audit(
                    turn_id=turn_id, call_id=call_id, lifecycle="completed",
                    parsed=safe_parsed, outcome=outcome, detail_code=safe_parsed.parse_note,
                )
                # P3.8: wait the planned, cancellable reply delay before
                # streaming the first segment.  The wait is bounded and never
                # blocks the 1 Hz trajectory loop (it is a pure asyncio sleep
                # on this dialogue task).  Only applied on the production path
                # where perception is wired; the P2.6 dialogue path (no
                # perception bus) skips the delay to preserve backward-
                # compatible turn latency.
                if plan is not None and self._perception_bus is not None:
                    ok = await self._wait_plan_delay(plan, cancel)
                    if not ok:
                        await self._send(ws, {
                            **base_message("turn.cancelled"),
                            "client_turn_id": turn_id,
                        })
                        return
                typewriter_ms = int(plan.typewriter_ms) if plan is not None else None
                for index, segment in enumerate(segments):
                    frame = {
                        **base_message("turn.stream"),
                        "client_turn_id": turn_id,
                        "segment_index": index,
                        "segment_count": len(segments),
                        "text": segment,
                    }
                    if typewriter_ms is not None:
                        frame["typewriter_ms"] = typewriter_ms
                    if plan is not None:
                        frame["plan_id"] = plan.plan_id
                    await self._send(ws, frame)
                completed_frame = {
                    **base_message("turn.completed"),
                    "client_turn_id": turn_id,
                    "segment_count": len(segments),
                    "writer_applied": any(move.applied for move in outcome.moves),
                }
                if plan is not None:
                    completed_frame["plan_id"] = plan.plan_id
                    completed_frame["typewriter_ms"] = int(plan.typewriter_ms)
                await self._send(ws, completed_frame)
        except (asyncio.CancelledError, ConnectionError):
            raise
        except asyncio.TimeoutError:
            if not ws.closed:
                await ws.close(code=1013, message=b"slow_client")
        except Exception:
            if not ws.closed:
                try:
                    self._audit(
                        turn_id=turn_id, call_id=call_id, lifecycle="error",
                        parsed=parsed, outcome=None, detail_code="internal_error",
                    )
                    await self._send_error(
                        ws, turn_id=turn_id, code="turn_failed",
                        detail="turn could not be completed", retry=True,
                    )
                except Exception:
                    await ws.close(code=1011, message=b"turn_failed")

    async def handle_ws(self, request: web.Request) -> web.StreamResponse:
        if not _loopback_request_host(request):
            raise web.HTTPForbidden(text="loopback host required")
        if not _same_origin(request):
            raise web.HTTPForbidden(text="origin mismatch")
        ws = web.WebSocketResponse(heartbeat=15.0, max_msg_size=20_000)
        await ws.prepare(request)
        connection_id = secrets.token_hex(16)
        session_id = f"sess:{connection_id}"
        # P3.7: start a perception session for this WebSocket connection.
        self._start_session(session_id)
        await self._send(ws, hello_message(
            connection_id=connection_id, provider_state=self.provider_state
        ))
        await self._send(ws, history_message(
            self.persistence.read_messages(limit=MAX_HISTORY_MESSAGES)
        ))
        # P4.10: this socket has completed hello/history and is now an
        # eligible proactive output target.
        self._proactive_sockets.add(ws)
        active: _SocketTurn | None = None
        # P3.7: typing-timeout sweep.  Runs concurrently with the receive
        # loop; clears stale typing state every TYPING_HEARTBEAT_TIMEOUT_SECONDS
        # without blocking the 1 Hz trajectory loop or the dialogue turn.
        sweep_stop = asyncio.Event()
        sweep_task: asyncio.Task[None] | None = None
        if self._perception_ingress is not None:
            sweep_task = asyncio.create_task(
                self._typing_sweep(session_id, sweep_stop),
                name=f"typing-sweep-{connection_id}",
            )
        try:
            async for incoming in ws:
                if incoming.type == WSMsgType.BINARY:
                    await self._send_error(
                        ws, turn_id=None, code="binary_unsupported",
                        detail="binary messages are unsupported", fatal=True,
                    )
                    await ws.close(code=1003, message=b"binary_unsupported")
                    break
                if incoming.type != WSMsgType.TEXT:
                    continue
                try:
                    command = parse_client_message(incoming.data)
                except DialogueProtocolError as exc:
                    await self._send_error(
                        ws, turn_id=None, code=exc.code, detail=exc.detail, fatal=exc.fatal,
                    )
                    if exc.fatal:
                        await ws.close(code=1008, message=exc.code.encode("ascii"))
                        break
                    continue
                # P3.7: typing.submit is a perception input, not a turn.  It
                # is allowed even while a turn is active (typing during
                # Aphrodite's reply is a valid signal).  Server validates and
                # derives the event; the client never submits a field delta.
                if command.type == "typing.submit":
                    self._ingest_typing(session_id, command.typing_state or "", command.client_turn_id)
                    continue
                if command.type == "turn.cancel":
                    if active is not None and not active.task.done() and active.turn_id == command.client_turn_id:
                        if active.cancel.is_set():
                            await self._send_error(
                                ws, turn_id=active.turn_id, code="cancel_in_progress",
                                detail="turn cancellation is already in progress",
                            )
                        else:
                            active.cancel.set()
                    else:
                        await self._send_error(
                            ws, turn_id=command.client_turn_id, code="turn_not_active",
                            detail="turn is not active",
                        )
                    continue
                if active is not None and not active.task.done():
                    await self._send_error(
                        ws, turn_id=command.client_turn_id, code="turn_in_progress",
                        detail="wait for the active turn to finish", retry=True,
                    )
                    continue
                cancel = asyncio.Event()
                task = asyncio.create_task(
                    self._run_turn(ws, command, cancel, session_id=session_id),
                    name=f"dialogue-{command.client_turn_id}",
                )
                active = _SocketTurn(command.client_turn_id, cancel, task)
                self._active_tasks.add(task)
                task.add_done_callback(self._active_tasks.discard)
        finally:
            # P4.10: this socket is no longer an eligible proactive target.
            self._proactive_sockets.discard(ws)
            sweep_stop.set()
            if sweep_task is not None and not sweep_task.done():
                sweep_task.cancel()
                try:
                    await sweep_task
                except (asyncio.CancelledError, Exception):
                    pass
            if active is not None and not active.task.done():
                active.cancel.set()
                try:
                    await active.task
                except (asyncio.CancelledError, ConnectionError):
                    pass
            # P3.7: end the perception session and force-clear typing on
            # disconnect so a dropped connection never leaves stale state.
            self._end_session(session_id)
        return ws

    # -- P4.10 proactive output boundary --------------------------------

    def set_proactive_enabled(self, enabled: bool) -> None:
        """Allow the runtime/CLI to mark proactive output as wired.

        Even when enabled, [`proactive_ready()`](dialogue_service.py) still
        requires a non-offline provider and at least one open output socket.
        """
        self._proactive_enabled = bool(enabled)

    @property
    def last_user_activity_ns(self) -> int | None:
        return self._last_user_activity_ns

    def proactive_ready(self) -> bool:
        """Ready probe for the proactive coordinator.

        Returns True iff proactive output is enabled, the provider is
        available (not offline), at least one dialogue socket has completed
        hello/history and is not closed, and the runtime is healthy.  This is
        a pure read; it never calls the provider or mutates state.
        """
        if not self._proactive_enabled:
            return False
        if self.provider_state != "available":
            return False
        if not self.runtime.healthy:
            return False
        for ws in tuple(self._proactive_sockets):
            if ws.closed:
                self._proactive_sockets.discard(ws)
                continue
            return True
        return False

    async def emit_proactive(self, admission_id: str) -> bool:
        """Generate and send one proactive message under the global turn lock.

        Reuses the production provider/writer/persistence path: opaque
        aliases, style prompt, structure A, meta-narration check,
        [`Writer.apply()`](writer.py), dialogue persistence, and WebSocket
        output.  The provider call runs in a thread so it never blocks the
        event loop.  At least one socket must receive the message for the
        emission to count as a send success; the admission already counted
        toward the cap regardless of the send outcome.  Never retries.
        """
        if not isinstance(admission_id, str) or not admission_id:
            return False
        if self.caller is None:
            return False
        try:
            async with self._turn_lock:
                if not self.proactive_ready():
                    return False
                registry = self.runtime.registry_proxy()
                snapshot = self.runtime.snapshot_proxy()
                aliases = opaque_dimension_aliases(registry.length)
                style = self._style.project(
                    registry=registry,
                    snapshot=snapshot,
                    gate=self._gate.project(registry),
                )
                system_prompt = build_system_prompt(style, aliases)
                # Non-diagnostic proactive instruction: ask for a natural line
                # without new user input, then continue structure A.  No state
                # names, values, trigger reasons, schedule, or persona reset.
                system_prompt = (
                    system_prompt + "\n"
                    "此刻没有新的对方输入，自然地说一句此刻想说的话，再继续结构 A 输出。"
                )
                history = self._history_pairs()
                parsed_aliases = await asyncio.to_thread(
                    self.caller.call,
                    system_prompt=system_prompt,
                    user_prompt=build_user_prompt(history, ""),
                    registry_dim_ids=aliases,
                )
                parsed = _remap_increment(parsed_aliases, aliases, registry.dim_ids)
                if parsed.degraded:
                    return False
                reply_text = parsed.reply_text[:MAX_REPLY_TEXT_CHARS].strip()
                if not reply_text:
                    return False
                forbidden_terms = registry.dim_ids + tuple(
                    registration.temporary_name for registration in registry.registrations
                )
                hits = detect_meta_narration(reply_text, forbidden_terms=forbidden_terms)
                if hits:
                    return False
                safe_parsed = ParsedReply(
                    reply_text=reply_text,
                    increment=parsed.increment,
                    parsed_ok=parsed.parsed_ok,
                    degraded=False,
                    provider_id=parsed.provider_id,
                    parse_note=parsed.parse_note,
                )
                outcome = self.writer.apply(safe_parsed, call_id=admission_id)
                segments = _split_reply(reply_text)
                if not segments:
                    return False
                ns = int(self._utc_clock())
                for index, segment in enumerate(segments):
                    self.persistence.append_message(
                        client_turn_id=admission_id,
                        role="assistant",
                        segment_index=index,
                        content=segment,
                        utc_unix_ns=ns,
                    )
                self._audit(
                    turn_id=admission_id,
                    call_id=admission_id,
                    lifecycle="completed",
                    parsed=safe_parsed,
                    outcome=outcome,
                    detail_code="proactive",
                )
                # Broadcast the same persisted message to every open socket.
                # One logical message; provider/writer called once.
                payload = proactive_message(
                    proactive_id=admission_id,
                    segment_index=0,
                    segment_count=len(segments),
                    text=segments[0],
                    utc_unix_ns=ns,
                )
                sent_any = False
                for ws in tuple(self._proactive_sockets):
                    if ws.closed:
                        self._proactive_sockets.discard(ws)
                        continue
                    try:
                        await self._send(ws, payload)
                        sent_any = True
                    except Exception:
                        # Isolate slow/disconnected sockets; one logical
                        # message still counts as sent if any socket received.
                        continue
                # Stream remaining segments if any.
                for index in range(1, len(segments)):
                    payload = proactive_message(
                        proactive_id=admission_id,
                        segment_index=index,
                        segment_count=len(segments),
                        text=segments[index],
                        utc_unix_ns=ns,
                    )
                    for ws in tuple(self._proactive_sockets):
                        if ws.closed:
                            self._proactive_sockets.discard(ws)
                            continue
                        try:
                            await self._send(ws, payload)
                        except Exception:
                            continue
                return sent_any
        except (asyncio.CancelledError, ConnectionError):
            raise
        except Exception:
            return False

    async def _typing_sweep(self, session_id: str, stop: asyncio.Event) -> None:
        """Periodically clear stale typing state for one session.

        Runs every ``TYPING_HEARTBEAT_TIMEOUT_SECONDS``; never blocks the
        dialogue turn or the 1 Hz trajectory loop.  Stops when ``stop`` is set.
        """
        interval = max(1.0, float(TYPING_HEARTBEAT_TIMEOUT_SECONDS))
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            if stop.is_set():
                return
            if self._perception_ingress is None:
                return
            try:
                envelope = self._perception_ingress.expire_typing(
                    session_id=session_id, event_id=f"typing:{session_id}:timeout"
                )
            except Exception:
                return
            self._ingest_perception_envelope(envelope)

    async def stop(self) -> None:
        tasks = tuple(self._active_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
