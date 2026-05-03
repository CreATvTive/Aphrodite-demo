from agentlib.runtime_engine import RuntimeEngine
from agentlib.runtime_immediate_protocol import ImmediateReplyProtocol


class _DummyFastGate:
    def infer(self, text):
        return type("G", (), {"route": "CHAT"})()


class _DummyRouter:
    fast_gate = _DummyFastGate()

    def route(self, **kwargs):
        return type("R", (), {"action": "CHAT", "scope": "MAIN"})()


class _DummyStateMachine:
    def apply(self, routed, confirmed=False):
        return type("S", (), {"action": "CHAT", "scope": "MAIN"})()


def test_immediate_protocol_emit_generates_presence_trace_and_route(monkeypatch):
    eng = RuntimeEngine()
    proto = ImmediateReplyProtocol()

    captured = {}

    def fake_emit(*, msg_id, reply_text, idle_tag, structured=False):
        captured["reply"] = reply_text

    monkeypatch.setattr(eng, "_emit_reply", fake_emit)

    emit = eng._build_immediate_emit_reply(user_text="你好", msg_id="m1", trace_id="t1", event_id="e1")
    pkt = proto.send(
        user_text="你好",
        msg_id="m1",
        router=_DummyRouter(),
        state_machine=_DummyStateMachine(),
        emit_reply=emit,
        mon={},
    )

    assert pkt.immediate == captured["reply"]
    tr = eng.mon["presence_last_trace"]
    assert tr["route"] == "immediate_protocol"
    assert tr["final_output"] == pkt.immediate


def test_immediate_dependency_expression_does_not_raise_permission_to_approach(monkeypatch):
    eng = RuntimeEngine()
    proto = ImmediateReplyProtocol()
    monkeypatch.setattr(eng, "_emit_reply", lambda **kwargs: None)
    before = eng.state_authority.state.get("relationship", {}).get("permission_to_approach", 0.5)
    emit = eng._build_immediate_emit_reply(user_text="我只需要你，不需要别人", msg_id="m2", trace_id="t2", event_id="e2")
    proto.send(
        user_text="我只需要你，不需要别人",
        msg_id="m2",
        router=_DummyRouter(),
        state_machine=_DummyStateMachine(),
        emit_reply=emit,
        mon={},
    )
    after = eng.state_authority.state.get("relationship", {}).get("permission_to_approach", 0.5)
    assert after <= before


def test_immediate_protocol_legacy_emit_reply_compatible():
    proto = ImmediateReplyProtocol()
    called = {}

    def legacy_emit(**kwargs):
        called["ok"] = True

    proto.send(
        user_text="hello",
        msg_id="m3",
        router=_DummyRouter(),
        state_machine=_DummyStateMachine(),
        emit_reply=legacy_emit,
        mon={},
    )
    assert called.get("ok") is True
