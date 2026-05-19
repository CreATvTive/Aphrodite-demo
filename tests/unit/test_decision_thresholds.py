from __future__ import annotations

from semantic_trigger.decision import decide
from semantic_trigger.schemas import CandidateScore


def test_per_trigger_threshold_blocks_direct_trigger() -> None:
    out = decide(
        candidates=[
            CandidateScore(trigger_id="set_reminder", final_score=0.62),
            CandidateScore(trigger_id="set_alarm", final_score=0.31),
        ],
        extracted_slots={"time": "tomorrow", "content": "meeting"},
        required_slots=[],
        no_trigger_threshold=0.25,
        ask_clarification_margin=0.08,
        trigger_threshold=0.50,
        per_trigger_threshold={"set_reminder": 0.70},
    )
    assert out.decision == "ask_clarification"
    assert out.selected_trigger == "set_reminder"


def test_min_trigger_margin_guard() -> None:
    out = decide(
        candidates=[
            CandidateScore(trigger_id="weather_query", final_score=0.74),
            CandidateScore(trigger_id="web_search", final_score=0.70),
        ],
        extracted_slots={},
        required_slots=[],
        no_trigger_threshold=0.25,
        ask_clarification_margin=0.02,
        trigger_threshold=0.50,
        min_trigger_margin=0.06,
    )
    assert out.decision == "ask_clarification"
    # NB: reason text may vary; check for margin-related keywords
    assert any(kw in ",".join(out.reasons) for kw in ["margin", "small_margin", "mid_confidence"]), f"unexpected reasons: {out.reasons}"

