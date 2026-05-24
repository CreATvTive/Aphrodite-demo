from agentlib.runtime_engine import RuntimeEngine


def test_presence_guard_no_warning_after_normal_emit(monkeypatch):
    eng = RuntimeEngine()
    monkeypatch.setattr(eng, "_emit_reply", lambda **kwargs: None)
    eng._emit_presence_reply(
        msg_id="m1",
        user_text="你好",
        reply_text="嗯。",
        idle_tag=False,
        route="immediate_protocol",
        latency_tier="tier_1",
    )
    assert not eng.mon.get("presence_warnings")


def test_presence_guard_warns_when_trace_missing(monkeypatch):
    eng = RuntimeEngine()
    monkeypatch.setattr(eng, "_emit_reply", lambda **kwargs: None)
    eng.mon.pop("presence_last_trace", None)
    eng._check_presence_trace_after_emit(route="llm", msg_id="m2", final_output="abc")
    warns = eng.mon.get("presence_warnings") or []
    assert warns and warns[-1]["issue"] == "missing_presence_trace"


def test_presence_guard_warns_route_mismatch(monkeypatch):
    eng = RuntimeEngine()
    monkeypatch.setattr(eng, "_emit_reply", lambda **kwargs: None)
    eng.mon["presence_last_trace"] = {"route": "direct", "final_output": "text"}
    eng._check_presence_trace_after_emit(route="llm", msg_id="m3", final_output="text")
    warns = eng.mon.get("presence_warnings") or []
    assert warns and "route_mismatch" in warns[-1]["issue"]


def test_presence_guard_does_not_block_reply_send(monkeypatch):
    eng = RuntimeEngine()
    called = {}

    def fake_emit(**kwargs):
        called["ok"] = True

    monkeypatch.setattr(eng, "_presence_min_flow", lambda **kwargs: {"route": "x", "final_output": "y"})
    monkeypatch.setattr(eng, "_emit_reply", fake_emit)
    eng._emit_presence_reply(
        msg_id="m4",
        user_text="hi",
        reply_text="hello",
        idle_tag=False,
        route="llm",
        latency_tier="tier_2",
    )
    assert called.get("ok") is True
    assert eng.mon.get("presence_warnings")
