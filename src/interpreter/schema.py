from __future__ import annotations

from typing import Any, Dict, List


def unknown_output(warnings: List[str] | None = None) -> Dict[str, Any]:
    warns = list(warnings or [])
    return {
        "semantic_event": {"event_type": "unknown", "type": "unknown", "topic": None},
        "affective_signal": {"valence": 0.0, "arousal": 0.1},
        "goal_signal": {"explicitness": 0.2, "type": "presence"},
        "relationship_signal": {"dependency_risk": 0.0},
        "memory_trigger_signal": {"memory_type": "none", "type": "none", "strength": 0.1},
        "boundary_signal": {"needs_boundary": False, "sensitivity_raise": 0.2},
        "performance_signal": {"requires_pause": False, "assistant_pull_risk": 0.2},
        "confidence": {"overall": 0.3, "event": 0.3},
        "warnings": warns,
    }
