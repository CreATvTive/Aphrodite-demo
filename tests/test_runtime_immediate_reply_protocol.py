from __future__ import annotations

import pytest

from agentlib.runtime_engine import RuntimeEngine
from agentlib.runtime_immediate_protocol import ImmediateReplyProtocol


BANNED_SERVICE_PHRASES = [
    "我来帮你处理",
    "我来帮你",
    "我在这",
    "继续和你聊",
    "别担心",
    "我理解你",
    "我会陪你",
]

ROUTE_ACTIONS = [
    "ASK_CLARIFY",
    "EXECUTE_LIGHT",
    "EXECUTE_HEAVY",
    "TOOL_LIGHT",
    "CHAT",
    "",  # falls through to default branch
]


def test_immediate_reply_for_clarify_contains_single_question() -> None:
    e = RuntimeEngine()
    text = e.immediate_protocol.compose_immediate_reply("帮我弄这个", {"action": "ASK_CLARIFY"})
    q_count = text.count("？") + text.count("?")
    assert q_count == 1


def test_immediate_reply_for_execute_is_short_ack_only() -> None:
    e = RuntimeEngine()
    text = e.immediate_protocol.compose_immediate_reply("请帮我运行评测", {"action": "EXECUTE_HEAVY"})
    assert "```" not in text
    assert "1." not in text and "步骤" not in text
    assert len(text) <= 24


def test_send_immediate_reply_sets_trace_fields() -> None:
    e = RuntimeEngine()
    packet = e.immediate_protocol.send(
        user_text="你能安慰我一下吗",
        msg_id=None,
        router=e.semantic_intent_lane.router,
        state_machine=e.semantic_intent_lane.state_machine,
        emit_reply=e._emit_reply,
        mon=e.mon,
    )
    assert packet.action
    assert int(e.mon.get("immediate_reply_sent", 0) or 0) == 1
    assert float(e.mon.get("sent_at_timestamp") or 0.0) > 0
    assert str(e.mon.get("route_decision") or "")


def test_execute_event_checks_immediate_reply_order() -> None:
    e = RuntimeEngine()
    _ = e.immediate_protocol.send(
        user_text="请开始自推进",
        msg_id=None,
        router=e.semantic_intent_lane.router,
        state_machine=e.semantic_intent_lane.state_machine,
        emit_reply=e._emit_reply,
        mon=e.mon,
    )
    before = int(e.mon.get("immediate_reply_order_violation", 0) or 0)
    _ = e._execute_selfdrive_control_dsl({"command": "STATUS_SELFDRIVE", "args": {}}, source_text="status")
    after = int(e.mon.get("immediate_reply_order_violation", 0) or 0)
    assert after == before


@pytest.mark.parametrize("action", ROUTE_ACTIONS)
@pytest.mark.parametrize("user_text", ["你好", "我感觉很难受", "请帮我跑一下脚本", "帮我弄这个"])
def test_immediate_reply_has_no_service_shaped_phrases(action: str, user_text: str) -> None:
    proto = ImmediateReplyProtocol()
    text = proto.compose_immediate_reply(user_text, {"action": action})
    for phrase in BANNED_SERVICE_PHRASES:
        assert phrase not in text, (
            f"banned service-shaped phrase {phrase!r} appeared in immediate reply "
            f"for action={action!r}, user_text={user_text!r}: {text!r}"
        )


@pytest.mark.parametrize("action", ROUTE_ACTIONS)
def test_immediate_reply_returns_non_empty_string(action: str) -> None:
    proto = ImmediateReplyProtocol()
    text = proto.compose_immediate_reply("hi", {"action": action})
    assert isinstance(text, str)
    assert text.strip(), f"empty immediate reply for action={action!r}"


def test_immediate_reply_for_execute_is_neutral_ack() -> None:
    proto = ImmediateReplyProtocol()
    text = proto.compose_immediate_reply("please run the eval", {"action": "EXECUTE_HEAVY"})
    assert text == "收到。"


def test_immediate_reply_for_chat_is_neutral_ack() -> None:
    proto = ImmediateReplyProtocol()
    text = proto.compose_immediate_reply("你好", {"action": "CHAT"})
    assert text == "嗯。"


def test_immediate_reply_for_clarify_uses_neutral_prefix() -> None:
    proto = ImmediateReplyProtocol()
    text = proto.compose_immediate_reply("帮我弄这个", {"action": "ASK_CLARIFY"})
    assert text.startswith("收到。")
    assert "明白" not in text
    assert "我来帮你" not in text


def test_send_prefers_fastgate_for_chat_route() -> None:
    class _FG:
        def infer(self, _: str):
            return type("_FGOut", (), {"route": "CHAT"})()

    class _Router:
        def __init__(self):
            self.fast_gate = _FG()

        def route(self, **_: object):
            raise AssertionError("router.route should not be called when fastgate says CHAT")

    class _SM:
        def apply(self, route, confirmed=False):
            return route

    sent = {}

    def _emit_reply(**kwargs):
        sent.update(kwargs)

    proto = RuntimeEngine().immediate_protocol
    mon = {}
    out = proto.send(
        user_text="你好",
        msg_id=None,
        router=_Router(),
        state_machine=_SM(),
        emit_reply=_emit_reply,
        mon=mon,
    )

    assert out.action == "CHAT"
    assert mon.get("immediate_reply_route_source") == "fast_gate"
    assert str(sent.get("reply_text") or "")
