from __future__ import annotations


def decide_persistence(candidate: dict) -> dict:
    confidence = float(candidate.get("confidence", 0.0))
    importance = float(candidate.get("importance", 0.0))
    first_seen = bool(candidate.get("first_seen", True))
    speculative = bool(candidate.get("speculative", False))

    if speculative:
        level = "working"
        reason = "speculative_content"
    elif confidence < 0.45:
        level = "working"
        reason = "low_confidence"
    elif first_seen and confidence < 0.8:
        level = "tentative"
        reason = "first_seen_needs_confirmation"
    elif importance > 0.7 and confidence > 0.85:
        level = "stable"
        reason = "high_confidence_high_importance"
    else:
        level = "tentative"
        reason = "default_tentative"

    out = dict(candidate)
    out["persistence_level"] = level
    out["write_reason"] = reason
    return out
