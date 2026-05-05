from src.interpreter.input_interpreter import InputInterpreter


def test_schema_complete_and_clipped_numeric_fields():
    it = InputInterpreter()
    out = it.interpret("python bug")
    for k in ["semantic_event", "relationship_signal", "boundary_signal", "memory_trigger_signal", "performance_signal", "confidence", "warnings"]:
        assert k in out
    numeric_checks = [
        ("relationship_signal", "dependency_risk"),
        ("relationship_signal", "vulnerability_relevance"),
        ("relationship_signal", "carefulness"),
        ("relationship_signal", "boundary_sensitivity"),
        ("boundary_signal", "external_pollution_risk"),
        ("boundary_signal", "internal_tension_relevance"),
        ("boundary_signal", "direct_fulfillment_risk"),
        ("memory_trigger_signal", "memory_relevance"),
        ("memory_trigger_signal", "recall_importance"),
    ]
    for sec, key in numeric_checks:
        v = float(out[sec][key])
        assert 0.0 <= v <= 1.0
