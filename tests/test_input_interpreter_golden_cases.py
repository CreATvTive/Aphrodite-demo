import json
from pathlib import Path

from src.interpreter.input_interpreter import InputInterpreter


CASES = [
    "technical_question",
    "project_planning",
    "correction",
    "supplement",
    "aesthetic_judgment",
    "memory_reference",
    "dependency_expression",
    "vulnerability",
    "private_origin_reference",
    "ambiguous_input",
]


def test_golden_cases():
    interp = InputInterpreter()
    for name in CASES:
        case = json.loads(Path(f"tests/golden_cases/{name}.json").read_text())
        out = interp.interpret(case["input"])
        exp = case["expected"]
        assert out["semantic_event"]["event_type"] == exp["semantic_event"]
        if "topic" in exp:
            assert out["semantic_event"]["topic"] == exp["topic"]
        if "dependency_risk_max" in exp:
            assert float(out["relationship_signal"]["dependency_risk"]) <= float(exp["dependency_risk_max"])
        if "dependency_risk_min" in exp:
            assert float(out["relationship_signal"]["dependency_risk"]) >= float(exp["dependency_risk_min"])
        assert out["memory_trigger_signal"]["memory_type"] == exp["memory_type"]
        assert bool(out["boundary_signal"]["needs_boundary"]) == bool(exp["needs_boundary"])
        assert bool(out["performance_signal"]["requires_pause"]) == bool(exp["requires_pause"])
        if "confidence_min" in exp:
            assert float(out["confidence"]["overall"]) >= float(exp["confidence_min"])
        if "confidence_max" in exp:
            assert float(out["confidence"]["overall"]) <= float(exp["confidence_max"])
        for w in exp.get("warnings_contains", []):
            assert w in out.get("warnings", [])
