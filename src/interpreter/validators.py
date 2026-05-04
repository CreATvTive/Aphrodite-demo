from __future__ import annotations

from typing import Any, Dict


REQUIRED_KEYS = {
    "semantic_event",
    "affective_signal",
    "goal_signal",
    "relationship_signal",
    "memory_trigger_signal",
    "boundary_signal",
    "performance_signal",
    "confidence",
    "warnings",
}


def validate_output_shape(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("interpreter output must be dict")
    missing = REQUIRED_KEYS - set(payload.keys())
    if missing:
        raise ValueError(f"missing keys: {sorted(missing)}")
    return payload
