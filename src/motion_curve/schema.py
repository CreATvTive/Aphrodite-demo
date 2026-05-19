from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


CURVE_CHANNELS = frozenset({"gaze", "head", "torso", "expression", "posture"})


def _clamp(value: float, lower: float, upper: float) -> float:
    return round(max(lower, min(upper, float(value))), 3)


@dataclass(frozen=True)
class CurvePoint:
    time_sec: float
    amplitude: float
    channel: str

    def __post_init__(self) -> None:
        if self.channel not in CURVE_CHANNELS:
            raise ValueError(f"unknown curve channel: {self.channel}")
        object.__setattr__(self, "time_sec", _clamp(self.time_sec, 0.0, 5.0))
        object.__setattr__(self, "amplitude", _clamp(self.amplitude, 0.0, 1.0))

    def to_dict(self) -> dict:
        return {
            "time_sec": self.time_sec,
            "amplitude": self.amplitude,
            "channel": self.channel,
        }


@dataclass(frozen=True)
class MotionCurve:
    scenario_name: str
    gaze_curve: List[CurvePoint]
    head_curve: List[CurvePoint]
    torso_curve: List[CurvePoint]
    expression_curve: List[CurvePoint]
    posture_curve: List[CurvePoint]
    body_part_offsets_sec: float = 0.0
    motion_completion: float = 1.0
    scenario_intent: str = ""
    behavior_affecting: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "body_part_offsets_sec", _clamp(self.body_part_offsets_sec, 0.0, 5.0))
        object.__setattr__(self, "motion_completion", _clamp(self.motion_completion, 0.0, 1.0))
        if self.behavior_affecting is not False:
            raise ValueError("behavior_affecting must be False")

    def curves_by_channel(self) -> dict[str, List[CurvePoint]]:
        return {
            "gaze": self.gaze_curve,
            "head": self.head_curve,
            "torso": self.torso_curve,
            "expression": self.expression_curve,
            "posture": self.posture_curve,
        }

    def to_dict(self) -> dict:
        return {
            "scenario_name": self.scenario_name,
            "gaze_curve": [point.to_dict() for point in self.gaze_curve],
            "head_curve": [point.to_dict() for point in self.head_curve],
            "torso_curve": [point.to_dict() for point in self.torso_curve],
            "expression_curve": [point.to_dict() for point in self.expression_curve],
            "posture_curve": [point.to_dict() for point in self.posture_curve],
            "body_part_offsets_sec": self.body_part_offsets_sec,
            "motion_completion": self.motion_completion,
            "scenario_intent": self.scenario_intent,
            "behavior_affecting": self.behavior_affecting,
        }
