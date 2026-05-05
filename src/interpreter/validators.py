from __future__ import annotations

from typing import Any, Dict


NUMERIC_PATHS = {
    ("relationship_signal", "dependency_risk"),
    ("relationship_signal", "vulnerability_relevance"),
    ("relationship_signal", "boundary_sensitivity"),
    ("relationship_signal", "carefulness"),
    ("boundary_signal", "external_pollution_risk"),
    ("boundary_signal", "internal_tension_relevance"),
    ("boundary_signal", "direct_fulfillment_risk"),
    ("memory_trigger_signal", "memory_relevance"),
    ("memory_trigger_signal", "recall_importance"),
}


def clip01(v: Any) -> float:
    try:
        f = float(v)
    except Exception:
        return 0.0
    return max(0.0, min(1.0, f))


def validate_and_clip(payload: Dict[str, Any]) -> Dict[str, Any]:
    for section, key in NUMERIC_PATHS:
        sec = payload.get(section)
        if not isinstance(sec, dict):
            sec = {}
            payload[section] = sec
        sec[key] = clip01(sec.get(key, 0.0))
    return payload
