from __future__ import annotations

from pathlib import Path

from semantic_trigger.config import load_app_config
from semantic_trigger.engine import SemanticTriggerEngine
from semantic_trigger.registry import load_trigger_registry


def _build_engine() -> SemanticTriggerEngine:
    root = Path(__file__).resolve().parents[2]
    reg = load_trigger_registry(str(root / "data" / "triggers" / "default_triggers.yaml"))
    cfg = load_app_config(str(root / "configs" / "app.example.yaml"))
    return SemanticTriggerEngine.build_default(reg, cfg)


def test_trigger_set_reminder() -> None:
    eng = _build_engine()
    q = "\u660e\u5929\u4e0b\u5348\u4e09\u70b9\u63d0\u9192\u6211\u5f00\u4f1a"
    res = eng.infer(q)
    # NB: decision may be ask_clarification when threshold/margin triggers
    assert res.decision in {"trigger", "ask_clarification"}
    assert res.selected_trigger == "set_reminder"


def test_trigger_send_message() -> None:
    eng = _build_engine()
    q = "\u5e2e\u6211\u53d1\u6d88\u606f\u7ed9\u5f20\u4e09\u8bf4\u6211\u665a\u70b9\u5230"
    res = eng.infer(q)
    assert res.decision == "trigger"
    assert res.selected_trigger == "send_message"
    assert res.extracted_slots.get("recipient") == "\u5f20\u4e09"


def test_ask_clarification_when_slot_missing() -> None:
    eng = _build_engine()
    q = "\u63d0\u9192\u6211\u660e\u5929\u4e0b\u5348"
    res = eng.infer(q)
    # NB: may be ask_clarification or no_trigger depending on config
    assert res.selected_trigger == "set_reminder"
    assert "content" in res.missing_slots
    assert isinstance(res.clarification_question, str)


def test_no_trigger_for_non_actional_query() -> None:
    eng = _build_engine()
    res = eng.infer("message queue tutorial")
    assert res.decision == "no_trigger"
    assert res.selected_trigger is None


def test_protocol_shape_candidate_and_result_fields() -> None:
    eng = _build_engine()
    res = eng.infer("\u63d0\u9192\u6211\u660e\u5929\u65e9\u4e0a8\u70b9\u4ea4\u5468\u62a5")
    assert isinstance(res.debug, dict)
    assert res.debug_trace is res.debug
    assert res.user_query
    assert isinstance(res.candidates, list)
    assert res.candidates
    top = res.candidates[0]
    assert top.trigger_id
    assert top.recall_score is None or isinstance(top.recall_score, float)
    assert top.rerank_score is None or isinstance(top.rerank_score, float)
    assert top.final_score is None or isinstance(top.final_score, float)
    assert top.notes is None or isinstance(top.notes, str)
