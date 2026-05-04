from agentlib.runtime_engine import RuntimeEngine


def test_presence_min_flow_uses_interpreter_shape():
    eng = RuntimeEngine()
    trace = eng._presence_min_flow(
        user_text="这个 Python bug 为什么会触发 KeyError？",
        assistant_text="...",
        trace_id="t-1",
        event_id="e-1",
    )
    assert trace["interpreted_event"]["semantic_event"]["event_type"] == "technical_question"
    assert "overall" in trace["interpreted_event"]["confidence"]
