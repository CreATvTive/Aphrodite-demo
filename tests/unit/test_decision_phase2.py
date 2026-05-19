from __future__ import annotations

from semantic_trigger.decision import decide
from semantic_trigger.schemas import CandidateScore


def test_decide_uses_per_trigger_threshold_and_margin() -> None:
    out = decide(
        candidates=[
            CandidateScore(trigger_id="set_reminder", final_score=0.56),
            CandidateScore(trigger_id="set_alarm", final_score=0.30),
        ],
        extracted_slots={"time": "tomorrow 9am", "content": "standup"},
        required_slots=[{"slot_name": "time", "required": True}, {"slot_name": "content", "required": True}],
        no_trigger_threshold=0.25,
        ask_clarification_margin=0.03,
        trigger_threshold=0.50,
        min_trigger_margin=0.02,
        low_confidence_threshold=0.40,
        margin_threshold=0.20,
        per_trigger_threshold={"set_reminder": 0.60},
    )
    assert out.decision == "ask_clarification"
    assert out.selected_trigger == "set_reminder"
    # NB: reason text may vary; check for threshold-related keywords
    assert any(kw in " ".join(out.reasons) for kw in ["threshold", "mid_confidence", "small_margin"]), f"unexpected reasons: {out.reasons}"
