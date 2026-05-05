from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class InterpretedEvent:
    semantic_event: Dict[str, Any] = field(default_factory=dict)
    relationship_signal: Dict[str, Any] = field(default_factory=dict)
    boundary_signal: Dict[str, Any] = field(default_factory=dict)
    memory_trigger_signal: Dict[str, Any] = field(default_factory=dict)
    performance_signal: Dict[str, Any] = field(default_factory=dict)
    confidence: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "semantic_event": dict(self.semantic_event),
            "relationship_signal": dict(self.relationship_signal),
            "boundary_signal": dict(self.boundary_signal),
            "memory_trigger_signal": dict(self.memory_trigger_signal),
            "performance_signal": dict(self.performance_signal),
            "confidence": dict(self.confidence),
            "warnings": list(self.warnings),
        }
