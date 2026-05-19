from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping


VALUE_BANDS = frozenset({"low", "baseline", "elevated", "high", "saturated"})
DECAY_PROFILES = frozenset({"instant", "fast", "medium", "slow", "very_slow"})

REQUIRED_FIELD_VARIABLES = (
    "boundary_distance",
    "affective_warmth",
    "structural_grip_pressure",
    "correction_pressure",
    "contamination_resistance",
    "presence_stability",
    "withdrawal_tendency",
    "service_resistance",
    "collaborator_layer_pressure",
    "contamination_pressure",
)

GROUND_STATE_VARIABLE_SPECS: Mapping[str, dict] = {
    "boundary_distance": {
        "value": "baseline",
        "numeric_value": 0.50,
        "baseline_value": "baseline",
        "baseline_numeric_value": 0.50,
        "decay_profile": "slow",
        "description": "Clear non-contact distance: present without fusion or withdrawal.",
        "source_note": "F_0 design: boundary distance is structurally present at rest.",
    },
    "affective_warmth": {
        "value": "baseline",
        "numeric_value": 0.35,
        "baseline_value": "baseline",
        "baseline_numeric_value": 0.35,
        "decay_profile": "medium",
        "description": "Restrained warmth: perceptible care without overflow or invitation.",
        "source_note": "F_0 design: warmth remains bounded below the distance priority.",
    },
    "structural_grip_pressure": {
        "value": "low",
        "numeric_value": 0.05,
        "baseline_value": "low",
        "baseline_numeric_value": 0.05,
        "decay_profile": "fast",
        "description": "Pressure for a small concrete foothold in the shared structure.",
        "source_note": "F_0 design: no active loss of foothold is present at rest.",
    },
    "correction_pressure": {
        "value": "low",
        "numeric_value": 0.00,
        "baseline_value": "low",
        "baseline_numeric_value": 0.00,
        "decay_profile": "medium",
        "description": "Accumulated pressure from recent correction of response posture.",
        "source_note": "F_0 design: no active correction pressure is present at rest.",
    },
    "contamination_resistance": {
        "value": "baseline",
        "numeric_value": 0.40,
        "baseline_value": "baseline",
        "baseline_numeric_value": 0.40,
        "decay_profile": "very_slow",
        "description": "Persistent resistance to AI-girlfriend, false-intimacy, and service drift.",
        "source_note": "F_0 design: the field is protected before any active contamination.",
    },
    "presence_stability": {
        "value": "high",
        "numeric_value": 0.80,
        "baseline_value": "high",
        "baseline_numeric_value": 0.80,
        "decay_profile": "very_slow",
        "description": "Stable, predictable presence across turns.",
        "source_note": "F_0 design: the ground posture is steady rather than reactive.",
    },
    "withdrawal_tendency": {
        "value": "low",
        "numeric_value": 0.10,
        "baseline_value": "low",
        "baseline_numeric_value": 0.10,
        "decay_profile": "medium",
        "description": "Current tendency to move away from the shared field.",
        "source_note": "F_0 design: the field remains present and does not retreat.",
    },
    "service_resistance": {
        "value": "elevated",
        "numeric_value": 0.55,
        "baseline_value": "elevated",
        "baseline_numeric_value": 0.55,
        "decay_profile": "very_slow",
        "description": "Persistent resistance to helper, customer-service, or pleasing posture.",
        "source_note": "F_0 design: anti-service posture is part of the ground state.",
    },
    "collaborator_layer_pressure": {
        "value": "low",
        "numeric_value": 0.05,
        "baseline_value": "low",
        "baseline_numeric_value": 0.05,
        "decay_profile": "fast",
        "description": "Activation pressure for technical or project collaboration.",
        "source_note": "F_0 design: collaborator pressure stays low without such context.",
    },
    "contamination_pressure": {
        "value": "low",
        "numeric_value": 0.00,
        "baseline_value": "low",
        "baseline_numeric_value": 0.00,
        "decay_profile": "instant",
        "description": "Current-turn contamination pressure before it is absorbed as resistance.",
        "source_note": "F_0 design: no active contamination pressure is present at rest.",
    },
}


@dataclass(frozen=True)
class FieldVariable:
    name: str
    value: str
    baseline_value: str
    decay_profile: str
    numeric_value: float = 0.0
    baseline_numeric_value: float = 0.0
    description: str = ""
    source_note: str = ""
    behavior_affecting: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")
        if self.value not in VALUE_BANDS:
            raise ValueError(f"invalid value band: {self.value}")
        if self.baseline_value not in VALUE_BANDS:
            raise ValueError(f"invalid baseline value band: {self.baseline_value}")
        if self.decay_profile not in DECAY_PROFILES:
            raise ValueError(f"invalid decay profile: {self.decay_profile}")
        if not self.description:
            raise ValueError("description must not be empty")
        if self.behavior_affecting is not False:
            raise ValueError("behavior_affecting must be False")
        if not isinstance(self.numeric_value, (int, float)):
            raise ValueError(f"numeric_value must be a number, got {type(self.numeric_value).__name__}")
        if not (0.0 <= self.numeric_value <= 1.0):
            raise ValueError(f"numeric_value must be in [0.0, 1.0], got {self.numeric_value}")
        if not isinstance(self.baseline_numeric_value, (int, float)):
            raise ValueError(f"baseline_numeric_value must be a number, got {type(self.baseline_numeric_value).__name__}")
        if not (0.0 <= self.baseline_numeric_value <= 1.0):
            raise ValueError(f"baseline_numeric_value must be in [0.0, 1.0], got {self.baseline_numeric_value}")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "numeric_value": self.numeric_value,
            "baseline_value": self.baseline_value,
            "baseline_numeric_value": self.baseline_numeric_value,
            "decay_profile": self.decay_profile,
            "description": self.description,
            "source_note": self.source_note,
            "behavior_affecting": self.behavior_affecting,
        }


def create_ground_state_variables() -> Dict[str, FieldVariable]:
    result: Dict[str, FieldVariable] = {}
    for name in REQUIRED_FIELD_VARIABLES:
        spec = GROUND_STATE_VARIABLE_SPECS[name]
        result[name] = FieldVariable(
            name=name,
            value=spec["value"],
            numeric_value=spec["numeric_value"],
            baseline_value=spec["baseline_value"],
            baseline_numeric_value=spec["baseline_numeric_value"],
            decay_profile=spec["decay_profile"],
            description=spec.get("description", ""),
            source_note=spec.get("source_note", ""),
            behavior_affecting=False,
        )
    return result


@dataclass(frozen=True)
class RelationalFieldState:
    variables: Dict[str, FieldVariable] = field(default_factory=create_ground_state_variables)
    state_note: str = "F_0 relational field ground state"
    behavior_affecting: bool = False

    def __post_init__(self) -> None:
        actual = set(self.variables)
        required = set(REQUIRED_FIELD_VARIABLES)
        if actual != required:
            missing = sorted(required - actual)
            extra = sorted(actual - required)
            raise ValueError(f"field variables must match required set; missing={missing}, extra={extra}")
        for name, variable in self.variables.items():
            if variable.name != name:
                raise ValueError(f"variable key/name mismatch: {name} != {variable.name}")
        if self.behavior_affecting is not False:
            raise ValueError("behavior_affecting must be False")

    def to_dict(self) -> dict:
        return {
            "variables": {
                name: variable.to_dict()
                for name, variable in self.variables.items()
            },
            "state_note": self.state_note,
            "behavior_affecting": self.behavior_affecting,
        }


def create_ground_state() -> RelationalFieldState:
    return RelationalFieldState()


F_0 = create_ground_state()

