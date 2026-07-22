"""P1.3 committed trajectory backend contract and localhost integration."""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
import shutil
import socket
import subprocess

from aiohttp import ClientSession, web
import pytest

from app.chatbox.expression_gate import AllOpenGateProjector
from app.chatbox.field_dynamics import DimensionRegistration, SeededGaussianRngFactory
from app.chatbox.field_persistence import FieldPersistenceError
from app.chatbox.field_runtime import FieldRuntime, FieldRuntimeError
from app.chatbox.trajectory_protocol import (
    TRAJECTORY_PROTOCOL_VERSION,
    ProtocolError,
    frame_message,
    parse_subscribe,
    serialize_message,
)
from app.chatbox.trajectory_service import TrajectoryHub, create_trajectory_app


def _registration(index: int) -> DimensionRegistration:
    return DimensionRegistration(
        dim_id=f"p13-{index}", temporary_name=f"维-{index}", birth_time=17.0,
        strength=1.0, trigger_count=index, birth_bias=0.0,
        fast_e_fold_s=600.0, ou_correlation_e_fold_s=10_800.0,
        ou_acceleration_sigma=4e-7, soft_boundary_start=1.0,
        soft_boundary_width=0.25, soft_boundary_strength=(1.0 / 120.0) ** 2,
    )


def _open(path: str, count: int = 3) -> FieldRuntime:
    return FieldRuntime.open(
        path,
        birth_registry=tuple(_registration(i) for i in range(count)),
        birth_rng_factory=SeededGaussianRngFactory(1234),
    )


@pytest.mark.parametrize("count", [1, 3, 12, 17])
def test_committed_frame_history_and_gate_are_registry_driven(tmp_path, count):
    runtime = _open(str(tmp_path / "field.db"), count)
    try:
        assert runtime.latest_tick_cursor_proxy() is None
        assert runtime.last_committed_frame_proxy() is None
        runtime.tick()
        runtime.move_attractor(__import__(
            "app.chatbox.field_dynamics", fromlist=["AttractorMove"]
        ).AttractorMove(f"p13-{count - 1}", 0.01, "test", "cursor gap"))
        runtime.tick()
        frames = runtime.trajectory_frames_proxy(
            after_cursor=None, cutoff_cursor=runtime.latest_tick_cursor_proxy(), limit=10
        )
        assert [frame.cursor for frame in frames] == [1, 3]
        assert [frame.field_tick for frame in frames] == [1, 2]
        assert all(len(frame.dimensions) == count for frame in frames)
        assert frame_message(frames[-1])["cursor"] == "3"
        assert serialize_message({
            "version": TRAJECTORY_PROTOCOL_VERSION, "type": "x", "n": 0.0
        }).endswith('"n":0.0}')
        gate = AllOpenGateProjector(temperature=0.5).project(runtime.registry_proxy())
        assert [item.dim_id for item in gate.weights] == list(runtime.registry_proxy().dim_ids)
        assert all(item.weight == 1.0 for item in gate.weights)
    finally:
        runtime.close()


def test_runtime_uses_one_utc_call_and_no_frame_after_commit_failure(tmp_path, monkeypatch):
    calls = 0
    def utc():
        nonlocal calls
        calls += 1
        return 17
    path = str(tmp_path / "field.db")
    runtime = FieldRuntime.open(
        path, birth_registry=tuple(_registration(i) for i in range(3)),
        birth_rng_factory=SeededGaussianRngFactory(1), utc_clock=utc,
    )
    assert calls == 1
    try:
        runtime.tick()
        assert calls == 2
        committed = runtime.last_committed_frame_proxy()
        def fail(**_kwargs):
            raise FieldPersistenceError("injected", "tick", path, "commit failed")
        monkeypatch.setattr(runtime._store, "write_tick_event", fail)
        with pytest.raises(FieldRuntimeError):
            runtime.tick()
        assert runtime._last_committed_frame is committed
        assert runtime._poisoned
    finally:
        runtime.close()


def test_protocol_subscribe_is_strict_and_large_cursor_roundtrips():
    large = 2**53 + 1
    text = json.dumps({
        "version": TRAJECTORY_PROTOCOL_VERSION,
        "type": "subscribe",
        "after_cursor": str(large),
    }, separators=(",", ":"))
    assert parse_subscribe(text) == large
    for invalid in (
        '{"version":"%s","type":"subscribe","after_cursor":"1","after_cursor":"2"}' % TRAJECTORY_PROTOCOL_VERSION,
        '{"version":"%s","type":"subscribe","after_cursor":1}' % TRAJECTORY_PROTOCOL_VERSION,
        '{"version":"%s","type":"subscribe","after_cursor":"01"}' % TRAJECTORY_PROTOCOL_VERSION,
        '{"version":"%s","type":"subscribe","after_cursor":null,"extra":1}' % TRAJECTORY_PROTOCOL_VERSION,
        '{"version":"%s","type":"subscribe","after_cursor":NaN}' % TRAJECTORY_PROTOCOL_VERSION,
    ):
        with pytest.raises(ProtocolError):
            parse_subscribe(invalid)
    with pytest.raises(ValueError):
        serialize_message({"version": TRAJECTORY_PROTOCOL_VERSION, "type": "x", "n": math.nan})


def test_history_read_detects_corrupt_dimension_row(tmp_path):
    path = str(tmp_path / "field.db")
    runtime = _open(path, 3)
    runtime.tick()
    triggers = runtime._store._conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger'"
    ).fetchall()
    for name, _ in triggers:
        runtime._store._conn.execute(f"DROP TRIGGER {name}")
    runtime._store._conn.execute(
        "DELETE FROM trajectory_points WHERE event_id=1 AND dimension_ordinal=1"
    )
    with pytest.raises(FieldRuntimeError) as caught:
        runtime.trajectory_frames_proxy(after_cursor=None, cutoff_cursor=1, limit=2)
    assert caught.value.code == "persistence_trajectory_count_mismatch"
    runtime._poisoned = True
    runtime.close()


def test_real_localhost_ws_empty_current_then_live(tmp_path):
    async def scenario():
        runtime = _open(str(tmp_path / "field.db"), 3)
        runner = web.AppRunner(create_trajectory_app(runtime))
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with ClientSession() as session:
                async with session.ws_connect(f"http://127.0.0.1:{port}/ws/trajectory") as ws:
                    await ws.send_json({
                        "version": TRAJECTORY_PROTOCOL_VERSION,
                        "type": "subscribe", "after_cursor": None,
                    })
                    types = []
                    live = None
                    for _ in range(9):
                        data = json.loads((await asyncio.wait_for(ws.receive(), 2.0)).data)
                        types.append(data["type"])
                        if data["type"] == "live":
                            live = data
                            break
                    assert types[:6] == [
                        "hello", "registry", "gate", "history_begin", "current", "history_end"
                    ]
                    assert live is not None
                    assert len(live["frame"]["dimensions"]) == 3
        finally:
            await runner.cleanup()
            runtime.close()
    asyncio.run(scenario())


def test_slow_client_queue_is_bounded_and_isolated(tmp_path):
    async def scenario():
        runtime = _open(str(tmp_path / "field.db"), 1)
        hub = TrajectoryHub(runtime)
        class WS:
            closed = False
            async def send_str(self, _text): pass
            async def close(self, **_kwargs): self.closed = True
        slow = hub.prepare_subscription(WS(), None).subscriber
        fast = hub.prepare_subscription(WS(), None).subscriber
        for _ in range(33):
            runtime.tick()
            frame = runtime.last_committed_frame_proxy()
            if not fast.queue.empty():
                fast.queue.get_nowait()
            hub.publish(frame)
        await asyncio.sleep(0)
        assert slow not in hub._subscribers
        assert fast in hub._subscribers
        assert slow.queue.qsize() == 32
        runtime.close()
    asyncio.run(scenario())


def test_same_origin_page_and_modules_are_served(tmp_path):
    async def scenario():
        runtime = _open(str(tmp_path / "field.db"), 3)
        runner = web.AppRunner(create_trajectory_app(runtime))
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with ClientSession() as session:
                for path, marker in (
                    ("/", "P1 FIELD OBSERVATORY"),
                    ("/protocol.js", "TRAJECTORY_PROTOCOL_VERSION"),
                    ("/trajectory-model.js", "MAX_BUFFERED_FRAMES"),
                    ("/trajectory-chart.js", "buildChartColumns"),
                    ("/gate-bars.js", "GateBars"),
                    ("/app.js", "TrajectoryProtocolSession"),
                    ("/styles.css", "prefers-reduced-motion"),
                ):
                    response = await session.get(f"http://127.0.0.1:{port}{path}")
                    assert response.status == 200
                    assert marker in await response.text()
        finally:
            await runner.cleanup()
            runtime.close()
    asyncio.run(scenario())


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_frontend_state_and_rendering_contracts_with_node():
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [shutil.which("node"), "--test", "tests/chatbox/p1_3_frontend_contract.mjs"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
