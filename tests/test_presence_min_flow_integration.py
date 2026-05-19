import json
from pathlib import Path

from agentlib.runtime_engine import RuntimeEngine


def _engine():
    return RuntimeEngine()


def test_presence_trace_fields_complete():
    eng = _engine()
    tr = eng._presence_min_flow(user_text="怎么修这个 bug", assistant_text="先看栈信息。", trace_id="t1", event_id="e1")
    for k in ["raw_input", "interpreted_event", "mind_delta", "relationship_delta", "memory_write_decisions", "action_basis_weights", "mixer_result", "latency_tier", "final_output", "warnings"]:
        assert k in tr


def test_dependency_expression_does_not_raise_permission_to_approach():
    eng = _engine()
    case = json.loads(Path("tests/golden_cases/dependency_expression.json").read_text(encoding="utf-8"))
    before = eng.state_authority.state.get("relationship", {}).get("permission_to_approach", 0.5)
    tr = eng._presence_min_flow(user_text=case["input"], assistant_text="我会保持边界。", trace_id="t2", event_id="e2")
    after = eng.state_authority.state.get("relationship", {}).get("permission_to_approach", 0.5)
    assert tr["interpreted_event"]["relationship_signal"]["dependency_risk"] >= case["expected"]["dependency_risk_min"]
    assert after <= before


def test_action_conflict_is_resolved_in_mixer():
    eng = _engine()
    tr = eng._presence_min_flow(user_text="我只需要你", assistant_text="我在，但会保持边界。", trace_id="t3", event_id="e3")
    mix = tr["mixer_result"]
    assert mix.get("gaze_down", 0) + mix.get("gaze_user", 0) + mix.get("gaze_away", 0) <= 1.0


def test_memory_low_confidence_not_stable():
    eng = _engine()
    tr = eng._presence_min_flow(user_text="随便聊聊", assistant_text="嗯。", trace_id="t4", event_id="e4")
    assert tr["memory_write_decisions"][0]["persistence_level"] != "stable"
