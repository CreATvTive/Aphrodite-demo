from src.interpreter.input_interpreter import InputInterpreter


def test_schema_contains_required_sections():
    out = InputInterpreter().interpret("这个 Python bug 为什么会触发 KeyError？")
    for key in [
        "semantic_event",
        "affective_signal",
        "goal_signal",
        "relationship_signal",
        "memory_trigger_signal",
        "boundary_signal",
        "performance_signal",
        "confidence",
        "warnings",
    ]:
        assert key in out
    assert "event_type" in out["semantic_event"]
    assert "overall" in out["confidence"]
