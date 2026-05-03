from agentlib.runtime_engine import RuntimeEngine


def test_emit_presence_reply_llm_route_and_text_unchanged(monkeypatch):
    eng = RuntimeEngine()
    captured = {}

    def fake_emit(msg_id, reply_text, idle_tag, structured=False):
        captured["reply_text"] = reply_text
        captured["msg_id"] = msg_id

    monkeypatch.setattr(eng, "_emit_reply", fake_emit)
    eng._emit_presence_reply(
        msg_id="m1",
        user_text="怎么修这个 bug",
        reply_text="先看 traceback。",
        idle_tag=False,
        route="llm",
        latency_tier="tier_2",
    )
    assert captured["reply_text"] == "先看 traceback。"
    assert eng.mon["presence_last_trace"]["route"] == "llm"


def test_emit_presence_reply_direct_route_dependency_guard():
    eng = RuntimeEngine()
    before = eng.state_authority.state.get("relationship", {}).get("permission_to_approach", 0.5)
    tr = eng._presence_min_flow(
        user_text="我只需要你，不需要别人",
        assistant_text="我会保持边界。",
        trace_id="td",
        event_id="ed",
        route="direct",
        latency_tier="tier_1",
    )
    after = eng.state_authority.state.get("relationship", {}).get("permission_to_approach", 0.5)
    assert tr["route"] == "direct"
    assert after <= before


def test_emit_presence_reply_error_safe_route(monkeypatch):
    eng = RuntimeEngine()
    monkeypatch.setattr(eng, "_emit_reply", lambda *args, **kwargs: None)
    eng._emit_presence_reply(
        msg_id="m3",
        user_text="",
        reply_text="我刚刚有点走神了，再说一次好吗？",
        idle_tag=False,
        route="error_safe",
        latency_tier="tier_1",
    )
    tr = eng.mon["presence_last_trace"]
    assert tr["route"] == "error_safe"
    assert tr["final_output"] == "我刚刚有点走神了，再说一次好吗？"
