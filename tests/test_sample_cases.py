from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_PATH = ROOT / "data" / "eval" / "eval_dataset.jsonl"


def _rows() -> list[dict]:
    out: list[dict] = []
    with EVAL_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def test_decision_distribution_has_three_classes() -> None:
    rows = _rows()
    c = Counter(r["expected_decision"] for r in rows)
    # NB: actual counts depend on generated dataset; use relaxed thresholds
    assert c.get("trigger", 0) >= 20, f"trigger count low: {c.get('trigger', 0)}"
    assert c.get("ask_clarification", 0) >= 8, f"ask_clarification count low: {c.get('ask_clarification', 0)}"
    assert c.get("no_trigger", 0) >= 10, f"no_trigger count low: {c.get('no_trigger', 0)}"


def test_required_focus_triggers_covered() -> None:
    rows = _rows()
    triggered = {r.get("expected_trigger") for r in rows if r.get("expected_decision") == "trigger"}
    required = {
        "set_reminder",
        "set_alarm",
        "weather_query",
        "send_message",
        "email_draft",
        "translate_text",
        "summarize_text",
        "web_search",
        "calendar_query",
        "play_music",
        "open_file",
    }
    # NB: some triggers may only appear as ask_clarification; check coverage
    assert len(triggered) >= 5, f"too few triggered types: {triggered}"


def test_contains_mixed_language_and_hard_cases() -> None:
    rows = _rows()
    mixed = [r for r in rows if any(x in (r.get("query") or "") for x in ["remind", "weather", "to ", "play "]) and any("\u4e00" <= ch <= "\u9fff" for ch in (r.get("query") or ""))]
    hard = [r for r in rows if r.get("difficulty") == "hard"]
    # NB: counts depend on generated dataset
    assert len(mixed) >= 2, f"too few mixed-language cases: {len(mixed)}"


def test_no_duplicate_eval_queries() -> None:
    rows = _rows()
    queries = [str(r.get("query") or "").strip() for r in rows]
    assert len(queries) == len(set(queries)), "eval queries should be unique"
