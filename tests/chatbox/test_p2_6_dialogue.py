"""P2 task-card 6 dialogue, style, persistence, and meta-contract evidence.

Every provider-facing test in this module uses an in-process fake.  The
50-reply corpus is an offline contract corpus, not real-provider acceptance.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
import sqlite3
import threading
import time
from unittest.mock import patch

from aiohttp import ClientSession, web
import pytest

from app.chatbox.dialogue_persistence import DialoguePersistenceStore
from app.chatbox.dialogue_protocol import (
    DIALOGUE_PROTOCOL_VERSION,
    MAX_USER_TEXT_CHARS,
    SEND_TIMEOUT_SECONDS,
    DialogueProtocolError,
    parse_client_message,
)
from app.chatbox.dialogue_service import DialogueService
from app.chatbox.expression_gate import AllOpenGateProjector
from app.chatbox.field_dynamics import DimensionRegistration, SeededGaussianRngFactory
from app.chatbox.field_runtime import FieldRuntime
from app.chatbox.meta_narration import detect_meta_narration
from app.chatbox.prompt_style import (
    PromptStyleProjector,
    build_system_prompt,
    build_user_prompt,
    opaque_dimension_aliases,
)
from app.chatbox.provider import FakeTransport, StructureACaller, build_default_registry, load_provider_config
from app.chatbox.provider.structure_a import ParsedReply
from app.chatbox.trajectory_protocol import TRAJECTORY_PROTOCOL_VERSION
from app.chatbox.trajectory_service import create_trajectory_app


def _registration(index: int) -> DimensionRegistration:
    return DimensionRegistration(
        dim_id=f"private-dimension-{index}",
        temporary_name=f"内部临时名-{index}",
        birth_time=17.0,
        strength=1.0,
        trigger_count=index,
        birth_bias=(0.08 if index % 2 == 0 else -0.04),
        fast_e_fold_s=600.0,
        ou_correlation_e_fold_s=10_800.0,
        ou_acceleration_sigma=4e-7,
        soft_boundary_start=1.0,
        soft_boundary_width=0.25,
        soft_boundary_strength=(1.0 / 120.0) ** 2,
    )


def _open_runtime(path: str, count: int = 3) -> FieldRuntime:
    return FieldRuntime.open(
        path,
        birth_registry=tuple(_registration(index) for index in range(count)),
        birth_rng_factory=SeededGaussianRngFactory(0xA2606),
    )


def _fake_caller(output: str) -> tuple[StructureACaller, FakeTransport]:
    transport = FakeTransport(responder=lambda _request: output)
    caller = StructureACaller(
        build_default_registry(),
        load_provider_config(env={}, provider_id="deepseek"),
        transport,
    )
    return caller, transport


async def _receive_json(ws, timeout: float = 3.0) -> dict:
    incoming = await asyncio.wait_for(ws.receive(), timeout)
    assert incoming.type.name == "TEXT", incoming
    return json.loads(incoming.data)


async def _start_app(tmp_path: Path, *, caller, provider_state: str, count: int = 3):
    runtime = _open_runtime(str(tmp_path / "field.sqlite3"), count)
    store = DialoguePersistenceStore(str(tmp_path / "dialogue.sqlite3"))
    dialogue = DialogueService(runtime, store, caller=caller, provider_state=provider_state)
    runner = web.AppRunner(create_trajectory_app(runtime, dialogue_service=dialogue))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = int(site._server.sockets[0].getsockname()[1])
    return runtime, store, dialogue, runner, port


async def _stop_app(runtime, store, runner) -> None:
    await runner.cleanup()
    store.close()
    runtime.close()


def _submit(turn_id: str, text: str) -> dict:
    return {
        "version": DIALOGUE_PROTOCOL_VERSION,
        "type": "turn.submit",
        "client_turn_id": turn_id,
        "text": text,
    }


def _field_event_count(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM field_events").fetchone()[0])
    finally:
        connection.close()


def test_dialogue_protocol_rejects_invalid_types_ids_sizes_and_json() -> None:
    parsed = parse_client_message(json.dumps(_submit("turn-1", "  你好  "), ensure_ascii=False))
    assert parsed.client_turn_id == "turn-1"
    assert parsed.text == "你好"
    invalid_messages = (
        "[]",
        '{"version":"aphrodite.chatbox.dialogue-ws/1","type":"turn.submit","client_turn_id":"x","text":"a","text":"b"}',
        '{"version":"aphrodite.chatbox.dialogue-ws/1","type":"turn.submit","client_turn_id":"x","text":NaN}',
        json.dumps({**_submit("bad id", "x")}),
        json.dumps(_submit("turn-empty", "  ")),
        json.dumps(_submit("turn-long", "x" * (MAX_USER_TEXT_CHARS + 1))),
        json.dumps({**_submit("turn-extra", "x"), "extra": True}),
        json.dumps({**_submit("turn-version", "x"), "version": "old"}),
    )
    for message in invalid_messages:
        with pytest.raises(DialogueProtocolError):
            parse_client_message(message)
    with pytest.raises(DialogueProtocolError):
        parse_client_message(b"not-text")  # type: ignore[arg-type]


@pytest.mark.parametrize("count", [1, 12, 17])
def test_prompt_style_is_dynamic_bounded_and_hides_registry_internals(tmp_path, count: int) -> None:
    runtime = _open_runtime(str(tmp_path / f"field-{count}.sqlite3"), count)
    try:
        registry = runtime.registry_proxy()
        style = PromptStyleProjector().project(
            registry=registry,
            snapshot=runtime.snapshot_proxy(),
            gate=AllOpenGateProjector().project(registry),
        )
        aliases = opaque_dimension_aliases(count)
        prompt = build_system_prompt(style, aliases)
        assert len(aliases) == count
        assert len(set(aliases)) == count
        assert style.length_instruction in prompt
        assert style.tone_instruction in prompt
        assert all(registration.dim_id not in prompt for registration in registry.registrations)
        assert all(registration.temporary_name not in prompt for registration in registry.registrations)
        for forbidden in ("attractor", "dim_id", "OU", "baseline", "阈值", "内部状态", "因为我现在处于"):
            assert forbidden.casefold() not in prompt.casefold()
        user_prompt = build_user_prompt((("user", "前一句"), ("assistant", "我听见了")), "这一句")
        assert "前一句" in user_prompt and "这一句" in user_prompt
    finally:
        runtime.close()


def test_opaque_aliases_extend_past_z_without_fixed_dimension_count() -> None:
    aliases = opaque_dimension_aliases(30)
    assert aliases[:3] == ("a", "b", "c")
    assert aliases[25:30] == ("z", "aa", "ab", "ac", "ad")


def test_offline_meta_contract_has_50_zero_hit_samples_and_detects_negatives() -> None:
    fixture = Path(__file__).with_name("fixtures") / "p2_6_meta_safe_samples.json"
    safe_samples = json.loads(fixture.read_text(encoding="utf-8"))
    assert len(safe_samples) >= 50
    assert all(not detect_meta_narration(sample) for sample in safe_samples)
    negatives = {
        "internal-mechanism": "我的内部状态刚刚改变了。",
        "internal-number": "我的参数值为 0.72。",
        "causal-self-report": "因为我当前的参数设置变了，所以才这样回答。",
        "model-meta": "这是系统提示要求模型输出的内容。",
        "registry-term": "private-dimension-0 正在影响这句话。",
    }
    for expected_rule, sample in negatives.items():
        hits = detect_meta_narration(sample, forbidden_terms=("private-dimension-0",))
        assert expected_rule in {hit.rule_id for hit in hits}, (expected_rule, hits)


def test_complete_fake_turn_prompt_writer_trajectory_persistence_and_dedup(tmp_path) -> None:
    async def scenario() -> None:
        caller, transport = _fake_caller("我在这里。\n\n你可以继续说。\n---\n{\"a\":0.12}")
        runtime, store, _dialogue, runner, port = await _start_app(
            tmp_path, caller=caller, provider_state="available", count=3
        )
        before = runtime.snapshot_proxy().dimensions[0].attractor
        try:
            async with ClientSession() as session:
                async with session.ws_connect(f"http://127.0.0.1:{port}/ws/dialogue") as ws:
                    hello = await _receive_json(ws)
                    history = await _receive_json(ws)
                    assert hello["type"] == "hello"
                    assert hello["version"] == DIALOGUE_PROTOCOL_VERSION
                    assert history == {"version": DIALOGUE_PROTOCOL_VERSION, "type": "history", "messages": []}
                    await ws.send_json(_submit("turn-complete", "今天有点难熬"))
                    messages = []
                    while not messages or messages[-1]["type"] != "turn.completed":
                        messages.append(await _receive_json(ws))
                    assert [message["type"] for message in messages] == [
                        "turn.accepted", "turn.stream", "turn.stream", "turn.completed"
                    ]
                    assert [message["text"] for message in messages if message["type"] == "turn.stream"] == [
                        "我在这里。", "你可以继续说。"
                    ]
                    assert messages[-1]["writer_applied"] is True
                    after = runtime.snapshot_proxy().dimensions[0].attractor
                    assert after == pytest.approx(before + 0.12)
                    runtime.tick()
                    assert runtime.last_committed_frame_proxy().dimensions[0].attractor == pytest.approx(after)
                    assert len(transport.calls) == 1
                    system_prompt = transport.calls[0].messages[0].content
                    assert '"a"' in system_prompt
                    assert all(reg.dim_id not in system_prompt for reg in runtime.registry_proxy().registrations)
                    assert all(reg.temporary_name not in system_prompt for reg in runtime.registry_proxy().registrations)

                    events_before_duplicate = _field_event_count(tmp_path / "field.sqlite3")
                    await ws.send_json(_submit("turn-complete", "重复提交"))
                    duplicate = await _receive_json(ws)
                    assert duplicate["type"] == "turn.error"
                    assert duplicate["code"] == "duplicate_turn"
                    assert _field_event_count(tmp_path / "field.sqlite3") == events_before_duplicate

            persisted = store.read_messages(limit=10)
            assert [(message.role, message.segment_index, message.content) for message in persisted] == [
                ("user", 0, "今天有点难熬"),
                ("assistant", 0, "我在这里。"),
                ("assistant", 1, "你可以继续说。"),
            ]
            connection = sqlite3.connect(store.db_path)
            try:
                audit = connection.execute(
                    "SELECT lifecycle,parsed_ok,writer_log_persisted,writer_move_count "
                    "FROM dialogue_audits WHERE client_turn_id='turn-complete'"
                ).fetchone()
            finally:
                connection.close()
            assert audit == ("completed", 1, 1, 1)
        finally:
            await _stop_app(runtime, store, runner)
    asyncio.run(scenario())


def test_provider_offline_has_explicit_degradation_and_no_writer_increment(tmp_path) -> None:
    async def scenario() -> None:
        runtime, store, _dialogue, runner, port = await _start_app(
            tmp_path, caller=None, provider_state="offline", count=3
        )
        before = tuple(dimension.attractor for dimension in runtime.snapshot_proxy().dimensions)
        try:
            async with ClientSession() as session:
                async with session.ws_connect(f"http://127.0.0.1:{port}/ws/dialogue") as ws:
                    assert (await _receive_json(ws))["provider_state"] == "offline"
                    await _receive_json(ws)
                    await ws.send_json(_submit("turn-offline", "还在吗"))
                    accepted = await _receive_json(ws)
                    degraded = await _receive_json(ws)
                    assert accepted["type"] == "turn.accepted"
                    assert degraded["type"] == "turn.degraded"
                    assert degraded["reason"] == "provider_unavailable"
            after = tuple(dimension.attractor for dimension in runtime.snapshot_proxy().dimensions)
            assert after == before
            assert _field_event_count(tmp_path / "field.sqlite3") == 0
            assert [(message.role, message.content) for message in store.read_messages()] == [
                ("user", "还在吗")
            ]
        finally:
            await _stop_app(runtime, store, runner)
    asyncio.run(scenario())


def test_unsafe_provider_output_is_not_shown_and_never_reaches_writer(tmp_path) -> None:
    async def scenario() -> None:
        caller, _transport = _fake_caller("因为我当前的参数设置变了，所以才这样说。\n---\n{\"a\":0.2}")
        runtime, store, _dialogue, runner, port = await _start_app(
            tmp_path, caller=caller, provider_state="available"
        )
        before = tuple(dimension.attractor for dimension in runtime.snapshot_proxy().dimensions)
        try:
            async with ClientSession() as session:
                async with session.ws_connect(f"http://127.0.0.1:{port}/ws/dialogue") as ws:
                    await _receive_json(ws)
                    await _receive_json(ws)
                    await ws.send_json(_submit("turn-unsafe", "说点什么"))
                    assert (await _receive_json(ws))["type"] == "turn.accepted"
                    error = await _receive_json(ws)
                    assert error["type"] == "turn.error"
                    assert error["code"] == "unsafe_provider_output"
                    assert "参数" not in error["detail"]
            assert tuple(dimension.attractor for dimension in runtime.snapshot_proxy().dimensions) == before
            assert [message.role for message in store.read_messages()] == ["user"]
            assert _field_event_count(tmp_path / "field.sqlite3") == 0
        finally:
            await _stop_app(runtime, store, runner)
    asyncio.run(scenario())


class _BlockingCaller:
    def __init__(self, release: threading.Event, *, delay: float = 0.0) -> None:
        self.release = release
        self.delay = delay
        self.calls = 0

    def call(self, **_kwargs) -> ParsedReply:
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        else:
            assert self.release.wait(timeout=3.0)
        return ParsedReply("我听见了。", {"a": 0.2}, True, provider_id="fake", parse_note="ok")


def test_same_socket_serializes_turns_and_cancel_prevents_writer(tmp_path) -> None:
    async def scenario() -> None:
        release = threading.Event()
        caller = _BlockingCaller(release)
        runtime, store, _dialogue, runner, port = await _start_app(
            tmp_path, caller=caller, provider_state="available"
        )
        before = tuple(dimension.attractor for dimension in runtime.snapshot_proxy().dimensions)
        try:
            async with ClientSession() as session:
                async with session.ws_connect(f"http://127.0.0.1:{port}/ws/dialogue") as ws:
                    await _receive_json(ws)
                    await _receive_json(ws)
                    await ws.send_json(_submit("turn-blocking", "第一条"))
                    assert (await _receive_json(ws))["type"] == "turn.accepted"
                    await ws.send_json(_submit("turn-concurrent", "第二条"))
                    busy = await _receive_json(ws)
                    assert busy["type"] == "turn.error" and busy["code"] == "turn_in_progress"
                    await ws.send_json({
                        "version": DIALOGUE_PROTOCOL_VERSION,
                        "type": "turn.cancel",
                        "client_turn_id": "not-active",
                    })
                    wrong_cancel = await _receive_json(ws)
                    assert wrong_cancel["code"] == "turn_not_active"
                    await ws.send_json({
                        "version": DIALOGUE_PROTOCOL_VERSION,
                        "type": "turn.cancel",
                        "client_turn_id": "turn-blocking",
                    })
                    # A client send completing does not mean the server receive
                    # loop has processed it.  Observe the cancel event through
                    # the public protocol before releasing the fake provider;
                    # otherwise provider completion can legitimately win.
                    await ws.send_json({
                        "version": DIALOGUE_PROTOCOL_VERSION,
                        "type": "turn.cancel",
                        "client_turn_id": "turn-blocking",
                    })
                    cancel_observed = await _receive_json(ws)
                    assert cancel_observed["type"] == "turn.error"
                    assert cancel_observed["code"] == "cancel_in_progress"
                    release.set()
                    cancelled = await _receive_json(ws)
                    assert cancelled["type"] == "turn.cancelled"
            assert tuple(dimension.attractor for dimension in runtime.snapshot_proxy().dimensions) == before
            assert _field_event_count(tmp_path / "field.sqlite3") == 0
            assert caller.calls == 1
        finally:
            release.set()
            await _stop_app(runtime, store, runner)
    asyncio.run(scenario())


def test_disconnect_during_provider_wait_prevents_writer(tmp_path) -> None:
    async def scenario() -> None:
        release = threading.Event()
        caller = _BlockingCaller(release)
        runtime, store, dialogue, runner, port = await _start_app(
            tmp_path, caller=caller, provider_state="available"
        )
        before = tuple(dimension.attractor for dimension in runtime.snapshot_proxy().dimensions)
        try:
            async with ClientSession() as session:
                ws = await session.ws_connect(f"http://127.0.0.1:{port}/ws/dialogue")
                await _receive_json(ws)
                await _receive_json(ws)
                await ws.send_json(_submit("turn-disconnect", "等等"))
                assert (await _receive_json(ws))["type"] == "turn.accepted"
                await ws.close()
                release.set()
                for _ in range(50):
                    if not dialogue._active_tasks:
                        break
                    await asyncio.sleep(0.01)
            assert tuple(dimension.attractor for dimension in runtime.snapshot_proxy().dimensions) == before
            assert _field_event_count(tmp_path / "field.sqlite3") == 0
        finally:
            release.set()
            await _stop_app(runtime, store, runner)
    asyncio.run(scenario())


def test_blocking_provider_does_not_block_trajectory_ticker(tmp_path) -> None:
    async def scenario() -> None:
        caller = _BlockingCaller(threading.Event(), delay=1.4)
        runtime, store, _dialogue, runner, port = await _start_app(
            tmp_path, caller=caller, provider_state="available"
        )
        try:
            async with ClientSession() as session:
                async with session.ws_connect(f"http://127.0.0.1:{port}/ws/dialogue") as dialogue_ws:
                    await _receive_json(dialogue_ws)
                    await _receive_json(dialogue_ws)
                    await dialogue_ws.send_json(_submit("turn-slow-provider", "慢一点"))
                    assert (await _receive_json(dialogue_ws))["type"] == "turn.accepted"
                    async with session.ws_connect(f"http://127.0.0.1:{port}/ws/trajectory") as trajectory_ws:
                        await trajectory_ws.send_json({
                            "version": TRAJECTORY_PROTOCOL_VERSION,
                            "type": "subscribe",
                            "after_cursor": None,
                        })
                        seen = []
                        while "live" not in seen:
                            seen.append((await _receive_json(trajectory_ws, timeout=2.0))["type"])
                        assert seen[:6] == [
                            "hello", "registry", "gate", "history_begin", "current", "history_end"
                        ]
                        assert "live" in seen
                    terminal = []
                    while not terminal or terminal[-1]["type"] != "turn.completed":
                        terminal.append(await _receive_json(dialogue_ws, timeout=2.0))
        finally:
            await _stop_app(runtime, store, runner)
    asyncio.run(scenario())


def test_slow_websocket_send_closes_with_retry_later_code(tmp_path) -> None:
    class SlowSocket:
        closed = False

        async def send_str(self, _text: str) -> None:
            await asyncio.sleep(0.05)

    async def scenario() -> None:
        runtime = _open_runtime(str(tmp_path / "field.sqlite3"))
        store = DialoguePersistenceStore(str(tmp_path / "dialogue.sqlite3"))
        service = DialogueService(runtime, store, caller=None, provider_state="offline")
        socket = SlowSocket()
        try:
            with patch("app.chatbox.dialogue_service.SEND_TIMEOUT_SECONDS", 0.001):
                with pytest.raises(asyncio.TimeoutError):
                    await service._send(  # type: ignore[arg-type]
                        socket, {"version": DIALOGUE_PROTOCOL_VERSION, "type": "test"}
                    )
            assert SEND_TIMEOUT_SECONDS == 3.0
        finally:
            store.close()
            runtime.close()
    asyncio.run(scenario())


def test_dialogue_persistence_is_append_only_and_restart_dedup_survives(tmp_path) -> None:
    path = str(tmp_path / "dialogue.sqlite3")
    store = DialoguePersistenceStore(path)
    store.append_message(
        client_turn_id="turn-persisted", role="user", segment_index=0,
        content="保留下来", utc_unix_ns=17,
    )
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute("UPDATE dialogue_messages SET content='changed'")
    store.close()
    reopened = DialoguePersistenceStore(path)
    try:
        assert reopened.turn_exists("turn-persisted")
        assert reopened.read_messages()[0].content == "保留下来"
    finally:
        reopened.close()


def test_dialogue_orchestration_has_no_quarantine_or_direct_state_write() -> None:
    import app.chatbox.dialogue_service as service_module
    import app.chatbox.prompt_style as style_module
    import app.chatbox.meta_narration as meta_module

    sources = "\n".join(inspect.getsource(module) for module in (
        service_module, style_module, meta_module
    ))
    for forbidden in ("agentlib", "agent_kernel", "src.semantic_trigger", "demos.scenarios"):
        assert forbidden not in sources
    service_source = inspect.getsource(service_module.DialogueService)
    assert "self.writer.apply" in service_source
    assert "move_attractor(" not in service_source
    assert "sqlite3" not in service_source
    assert "._dynamics" not in service_source
