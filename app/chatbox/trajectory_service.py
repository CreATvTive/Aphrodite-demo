"""Localhost aiohttp service for committed trajectory history and live frames."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import ipaddress
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

from app.chatbox.dialogue_service import DialogueService
from app.chatbox.expression_gate import AllOpenGateProjector
from app.chatbox.field_persistence import TrajectoryFrame
from app.chatbox.field_runtime import FieldRuntime, TICK_INTERVAL_SECONDS
from app.chatbox.trajectory_protocol import (
    CLIENT_LIVE_QUEUE_FRAMES,
    HISTORY_BATCH_FRAMES,
    INITIAL_HISTORY_FRAMES,
    MAX_RESUME_FRAMES,
    SUBSCRIBE_TIMEOUT_SECONDS,
    ProtocolError,
    current_message,
    error_message,
    gate_message,
    hello_message,
    history_batch_message,
    history_begin_message,
    history_end_message,
    live_message,
    parse_subscribe,
    registry_message,
    serialize_message,
)


STATIC_FILES = {
    "/": "index.html",
    "/styles.css": "styles.css",
    "/protocol.js": "protocol.js",
    "/trajectory-model.js": "trajectory-model.js",
    "/trajectory-chart.js": "trajectory-chart.js",
    "/gate-bars.js": "gate-bars.js",
    "/dialogue-protocol.js": "dialogue-protocol.js",
    "/dialogue-model.js": "dialogue-model.js",
    "/dialogue-view.js": "dialogue-view.js",
    "/app.js": "app.js",
}


@dataclass(slots=True, eq=False)
class _Subscriber:
    ws: web.WebSocketResponse
    queue: asyncio.Queue[TrajectoryFrame | None]
    min_cursor: int | None
    writer: asyncio.Task | None = None


@dataclass(frozen=True, slots=True)
class PreparedSubscription:
    cutoff_cursor: int | None
    frames: tuple[TrajectoryFrame, ...]
    truncated_before: bool
    subscriber: _Subscriber


class TrajectoryHub:
    def __init__(
        self,
        runtime: FieldRuntime,
        *,
        temperature: float = 1.0,
        proactive_coordinator: object | None = None,
        soak_observer: object | None = None,
        strict_formal_soak: bool = False,
    ) -> None:
        self.runtime = runtime
        self.gate = AllOpenGateProjector(temperature=temperature, bandwidth=4)
        self._subscribers: set[_Subscriber] = set()
        self._ticker: asyncio.Task | None = None
        self._stopping = False
        self.fatal_error: BaseException | None = None
        # P4.10: optional committed-tick observer for proactive output.  It is
        # driven only by the committed-tick hook below; it never creates its
        # own timer/interval.  Typed as object to avoid importing the
        # coordinator module here (keeps the trajectory service decoupled).
        self._proactive_coordinator = proactive_coordinator
        # P4.11: optional read-only committed-frame observer.  It is called
        # synchronously at the existing frame boundary and owns no task/timer.
        self._soak_observer = soak_observer
        self._strict_formal_soak = strict_formal_soak
        self.terminal_soak_state: str | None = None

    def prepare_subscription(
        self, ws: web.WebSocketResponse, after_cursor: int | None
    ) -> PreparedSubscription:
        """Capture cutoff, history, and registration without an await gap."""
        cutoff = self.runtime.latest_tick_cursor_proxy()
        if after_cursor is not None and not self.runtime.tick_cursor_exists_proxy(after_cursor):
            raise ProtocolError("invalid_cursor", "cursor does not identify a tick event")
        if after_cursor is not None and cutoff is not None and after_cursor > cutoff:
            raise ProtocolError("invalid_cursor", "cursor is beyond the committed head")
        if after_cursor is None:
            frames = self.runtime.trajectory_frames_proxy(
                after_cursor=None,
                cutoff_cursor=cutoff,
                limit=INITIAL_HISTORY_FRAMES + 1,
            )
            truncated = len(frames) > INITIAL_HISTORY_FRAMES
            if truncated:
                frames = frames[-INITIAL_HISTORY_FRAMES:]
        else:
            frames = self.runtime.trajectory_frames_proxy(
                after_cursor=after_cursor,
                cutoff_cursor=cutoff,
                limit=MAX_RESUME_FRAMES + 1,
            )
            if len(frames) > MAX_RESUME_FRAMES:
                raise ProtocolError(
                    "resync_required",
                    "resume backlog exceeds 3600 frames",
                    retry="fresh",
                )
            truncated = False
        subscriber = _Subscriber(
            ws=ws,
            queue=asyncio.Queue(maxsize=CLIENT_LIVE_QUEUE_FRAMES),
            min_cursor=cutoff,
        )
        self._subscribers.add(subscriber)
        return PreparedSubscription(cutoff, frames, truncated, subscriber)

    def remove(self, subscriber: _Subscriber) -> None:
        self._subscribers.discard(subscriber)

    def publish(self, frame: TrajectoryFrame) -> None:
        for subscriber in tuple(self._subscribers):
            if subscriber.min_cursor is not None and frame.cursor <= subscriber.min_cursor:
                continue
            try:
                subscriber.queue.put_nowait(frame)
            except asyncio.QueueFull:
                self._subscribers.discard(subscriber)
                asyncio.create_task(self._close_slow(subscriber))

    async def _close_slow(self, subscriber: _Subscriber) -> None:
        try:
            await subscriber.ws.send_str(
                serialize_message(error_message(
                    code="slow_client", fatal=True, retry="fresh",
                    detail="live queue exceeded 32 frames",
                ))
            )
        except Exception:
            pass
        await subscriber.ws.close(code=1013, message=b"slow_client")

    async def start(self) -> None:
        if self._ticker is None:
            self._ticker = asyncio.create_task(self._tick_loop(), name="trajectory-ticker")

    async def _tick_loop(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + TICK_INTERVAL_SECONDS
        try:
            while not self._stopping:
                await asyncio.sleep(max(0.0, deadline - loop.time()))
                if self._stopping:
                    return
                self.runtime.tick()
                frame = self.runtime.last_committed_frame_proxy()
                if frame is None:
                    raise RuntimeError("committed tick returned no trajectory frame")
                self.publish(frame)
                # P4.10: drive the proactive observer only after the field tick
                # has been persisted and a frame exists.  The observer is
                # fail-closed and never raises into the ticker; a proactive
                # failure must not fabricate a frame or poison the runtime.
                if self._proactive_coordinator is not None:
                    try:
                        self._proactive_coordinator.on_committed_tick()
                    except Exception:
                        pass
                if self._soak_observer is not None:
                    try:
                        soak_state = self._soak_observer.on_committed_frame(frame)
                        state_value = str(getattr(soak_state, "value", soak_state))
                        if self._strict_formal_soak and state_value in {
                            "PASS", "FAIL", "EVIDENCE_CORRUPT",
                        }:
                            self.terminal_soak_state = state_value
                            self._stopping = True
                            return
                    except Exception as exc:
                        # Observer is independently fail-closed.  It cannot
                        # poison field ownership or suppress committed frames.
                        if self._strict_formal_soak:
                            self.terminal_soak_state = "EVIDENCE_CORRUPT"
                            raise RuntimeError("strict formal soak observer failed") from exc
                deadline = max(deadline + TICK_INTERVAL_SECONDS, loop.time() + TICK_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self.fatal_error = exc
            self._stopping = True
            await self._broadcast_runtime_failure()

    async def _broadcast_runtime_failure(self) -> None:
        subscribers = tuple(self._subscribers)
        self._subscribers.clear()
        for subscriber in subscribers:
            try:
                await subscriber.ws.send_str(serialize_message(error_message(
                    code="runtime_failed", fatal=True, retry="later",
                    detail="field runtime failed",
                )))
            except Exception:
                pass
            await subscriber.ws.close(code=1011, message=b"runtime_failed")

    async def stop(self) -> None:
        if self._stopping and self._ticker is None:
            return
        # P4.10: stop the proactive coordinator before closing the dialogue/
        # runtime stores so any in-flight emission is cancelled cleanly and
        # no new emission can start during shutdown.
        if self._proactive_coordinator is not None:
            try:
                await self._proactive_coordinator.stop()
            except Exception:
                pass
        if self._soak_observer is not None:
            try:
                self._soak_observer.close()
            except Exception:
                pass
        self._stopping = True
        if self._ticker is not None:
            self._ticker.cancel()
            try:
                await self._ticker
            except asyncio.CancelledError:
                pass
            self._ticker = None
        subscribers = tuple(self._subscribers)
        self._subscribers.clear()
        for subscriber in subscribers:
            try:
                subscriber.queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
            if subscriber.writer is not None:
                try:
                    await subscriber.writer
                except Exception:
                    pass
            if not subscriber.ws.closed:
                await subscriber.ws.close(code=1001, message=b"server_shutdown")


def _loopback_host(host: str) -> bool:
    normalized = host.strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _origin_allowed(request: web.Request) -> bool:
    origin = request.headers.get("Origin")
    if origin is None:
        return True
    parsed = urlsplit(origin)
    return parsed.scheme == request.scheme and parsed.netloc == request.host


async def _writer(subscriber: _Subscriber) -> None:
    while True:
        frame = await subscriber.queue.get()
        if frame is None:
            return
        await subscriber.ws.send_str(serialize_message(live_message(frame)))


async def _send_initial(
    hub: TrajectoryHub,
    prepared: PreparedSubscription,
    after_cursor: int | None,
) -> None:
    ws = prepared.subscriber.ws
    registry = hub.runtime.registry_proxy()
    await ws.send_str(serialize_message(hello_message(
        connection_id=secrets.token_hex(16), head_cursor=prepared.cutoff_cursor
    )))
    await ws.send_str(serialize_message(registry_message(registry)))
    await ws.send_str(serialize_message(gate_message(hub.gate.project(registry))))
    await ws.send_str(serialize_message(history_begin_message(
        mode="tail" if after_cursor is None else "resume",
        after_cursor=after_cursor,
        cutoff_cursor=prepared.cutoff_cursor,
        truncated_before=prepared.truncated_before,
    )))
    for start in range(0, len(prepared.frames), HISTORY_BATCH_FRAMES):
        await ws.send_str(serialize_message(history_batch_message(
            prepared.frames[start:start + HISTORY_BATCH_FRAMES]
        )))
    if prepared.cutoff_cursor is None:
        await ws.send_str(serialize_message(current_message(hub.runtime.snapshot_proxy())))
    await ws.send_str(serialize_message(history_end_message(
        cutoff_cursor=prepared.cutoff_cursor, frames=prepared.frames
    )))


async def _ws_handler(request: web.Request) -> web.StreamResponse:
    hub: TrajectoryHub = request.app["trajectory_hub"]
    if not _loopback_host(request.host.split(":", 1)[0].strip("[]")):
        raise web.HTTPForbidden(text="loopback host required")
    if not _origin_allowed(request):
        raise web.HTTPForbidden(text="origin mismatch")
    ws = web.WebSocketResponse(heartbeat=15.0, max_msg_size=4096)
    await ws.prepare(request)
    subscriber: _Subscriber | None = None
    try:
        try:
            message = await asyncio.wait_for(ws.receive(), timeout=SUBSCRIBE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            await ws.close(code=1008, message=b"subscribe_timeout")
            return ws
        if message.type == WSMsgType.BINARY:
            await ws.send_str(serialize_message(error_message(
                code="binary_unsupported", fatal=True, retry="none", detail="binary messages are unsupported"
            )))
            await ws.close(code=1003, message=b"binary_unsupported")
            return ws
        if message.type != WSMsgType.TEXT:
            await ws.close(code=1008, message=b"invalid_message")
            return ws
        try:
            after_cursor = parse_subscribe(message.data)
            prepared = hub.prepare_subscription(ws, after_cursor)
            subscriber = prepared.subscriber
            await _send_initial(hub, prepared, after_cursor)
        except ProtocolError as exc:
            await ws.send_str(serialize_message(error_message(
                code=exc.code, fatal=True, retry=exc.retry, detail=exc.detail
            )))
            close_code = {
                "unsupported_version": 1002,
                "oversize": 1009,
            }.get(exc.code, 1008)
            await ws.close(code=close_code, message=exc.code.encode("ascii"))
            return ws
        subscriber.writer = asyncio.create_task(_writer(subscriber))
        async for incoming in ws:
            if incoming.type == WSMsgType.BINARY:
                await ws.close(code=1003, message=b"binary_unsupported")
                break
            if incoming.type == WSMsgType.TEXT:
                await ws.close(code=1008, message=b"invalid_message")
                break
    finally:
        if subscriber is not None:
            hub.remove(subscriber)
            if subscriber.writer is not None:
                subscriber.writer.cancel()
                try:
                    await subscriber.writer
                except asyncio.CancelledError:
                    pass
    return ws


async def _health(request: web.Request) -> web.Response:
    hub: TrajectoryHub = request.app["trajectory_hub"]
    return web.json_response({"status": "ok" if hub.runtime.healthy else "failed"})


async def _static(request: web.Request) -> web.StreamResponse:
    name = STATIC_FILES.get(request.path)
    if name is None:
        raise web.HTTPNotFound()
    path = Path(__file__).with_name("web") / name
    if not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path)


def create_trajectory_app(
    runtime: FieldRuntime,
    *,
    temperature: float = 1.0,
    dialogue_service: DialogueService | None = None,
    proactive_coordinator: object | None = None,
    soak_observer: object | None = None,
    strict_formal_soak: bool = False,
) -> web.Application:
    app = web.Application()
    hub = TrajectoryHub(
        runtime, temperature=temperature, proactive_coordinator=proactive_coordinator,
        soak_observer=soak_observer, strict_formal_soak=strict_formal_soak,
    )
    app["trajectory_hub"] = hub
    if dialogue_service is not None:
        app["dialogue_service"] = dialogue_service

    async def lifecycle(_app: web.Application):
        await hub.start()
        try:
            yield
        finally:
            if dialogue_service is not None:
                await dialogue_service.stop()
            await hub.stop()

    app.cleanup_ctx.append(lifecycle)
    app.router.add_get("/healthz", _health)
    app.router.add_get("/ws/trajectory", _ws_handler)
    if dialogue_service is not None:
        app.router.add_get("/ws/dialogue", dialogue_service.handle_ws)
    for route in STATIC_FILES:
        app.router.add_get(route, _static)
    return app
