from __future__ import annotations

from dataclasses import asdict, dataclass, field


HARD_CONSTRAINT_FIELDS: tuple[str, ...] = (
    "no_approach_step",
    "no_forward_lean",
    "no_cute_head_tilt",
    "no_welcoming_gesture",
    "no_service_gesture",
    "no_seductive_expression",
)

ALL_HARD_CONSTRAINTS: frozenset[str] = frozenset(HARD_CONSTRAINT_FIELDS)

MOTION_PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "initial_delay_sec": (0.0, 2.0),
    "motion_speed": (0.0, 1.0),
    "pause_after_sec": (0.0, 1.5),
    "gaze_contact_sec": (0.0, 1.5),
    "gaze_release_amplitude": (0.0, 1.0),
    "head_turn_amplitude": (0.0, 0.5),
    "head_turn_delay_sec": (0.0, 0.5),
    "torso_lean": (-0.25, 0.20),
    "posture_stability": (0.0, 1.0),
    "expression_amplitude": (0.0, 0.35),
    "motion_completion": (0.20, 0.90),
}

BODY_PART_OFFSET_BOUNDS: dict[str, tuple[int, int]] = {
    "gaze_offset_ms": (0, 600),
    "head_offset_ms": (0, 600),
    "shoulder_offset_ms": (0, 600),
    "hand_offset_ms": (0, 600),
}


def _clamp(value: float, lower: float, upper: float) -> float:
    if not isinstance(value, (int, float)):
        raise ValueError(f"motion parameter must be a number, got {type(value).__name__}")
    return round(max(lower, min(upper, float(value))), 3)


def _clamp_int(value: int, lower: int, upper: int) -> int:
    if not isinstance(value, int):
        raise ValueError(f"offset must be an integer, got {type(value).__name__}")
    return max(lower, min(upper, value))


@dataclass(frozen=True)
class HardMotionConstraints:
    no_approach_step: bool = False
    no_forward_lean: bool = False
    no_cute_head_tilt: bool = False
    no_welcoming_gesture: bool = False
    no_service_gesture: bool = False
    no_seductive_expression: bool = False

    def __post_init__(self) -> None:
        for name in HARD_CONSTRAINT_FIELDS:
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a bool")

    def active_names(self) -> tuple[str, ...]:
        return tuple(name for name in HARD_CONSTRAINT_FIELDS if getattr(self, name))


@dataclass(frozen=True)
class BodyPartOffsets:
    gaze_offset_ms: int = 0
    head_offset_ms: int = 60
    shoulder_offset_ms: int = 120
    hand_offset_ms: int = 180

    def __post_init__(self) -> None:
        for name, (lower, upper) in BODY_PART_OFFSET_BOUNDS.items():
            object.__setattr__(self, name, _clamp_int(getattr(self, name), lower, upper))

        if not (
            self.gaze_offset_ms <= self.head_offset_ms
            <= self.shoulder_offset_ms <= self.hand_offset_ms
        ):
            raise ValueError("body part offsets must preserve gaze <= head <= shoulder <= hand")


@dataclass(frozen=True)
class MotionParams:
    initial_delay_sec: float = 0.0
    motion_speed: float = 0.5
    pause_after_sec: float = 0.0
    gaze_contact_sec: float = 0.0
    head_turn_delay_sec: float = 0.0
    gaze_release_amplitude: float = 0.0
    head_turn_amplitude: float = 0.0
    torso_lean: float = 0.0
    posture_stability: float = 0.0
    expression_amplitude: float = 0.0
    motion_completion: float = 1.0
    body_part_offsets: BodyPartOffsets = field(default_factory=BodyPartOffsets)
    hard_constraints: HardMotionConstraints = field(default_factory=HardMotionConstraints)
    source_state_note: str = ""
    provenance: str = ""
    field_snapshot_note: str = ""
    behavior_affecting: bool = False

    def __post_init__(self) -> None:
        for name, (lower, upper) in MOTION_PARAM_BOUNDS.items():
            object.__setattr__(self, name, _clamp(getattr(self, name), lower, upper))

        if not isinstance(self.body_part_offsets, BodyPartOffsets):
            raise ValueError("body_part_offsets must be a BodyPartOffsets instance")
        if not isinstance(self.hard_constraints, HardMotionConstraints):
            raise ValueError("hard_constraints must be a HardMotionConstraints instance")

        if self.behavior_affecting is not False:
            raise ValueError("behavior_affecting must be False")

    def to_dict(self) -> dict:
        return asdict(self)
