from __future__ import annotations

from src.body_action.schema import ACTION_PRIMITIVES, BodyActionWeight, BodyActionWeights
from src.motion_params.schema import MotionParams


ACTION_ORDER: tuple[str, ...] = (
    "pause",
    "stillness",
    "look_down",
    "look_to_user",
    "look_away",
    "slight_forward",
    "slight_withdraw",
    "maintain_distance",
    "reduce_motion",
    "reset_posture",
)

PROVENANCE = "MotionParams→BodyActionWeights v1"


class MotionToActionMapper:
    def map(self, motion_params: MotionParams) -> BodyActionWeights:
        if not isinstance(motion_params, MotionParams):
            raise TypeError("motion_params must be a MotionParams instance")

        drives = _compute_drives(motion_params)
        active_constraints = _active_constraint_names(motion_params)
        weights = [
            _build_weight(
                action_name=action_name,
                motion_params=motion_params,
                drive=drives[action_name],
                active_constraints=active_constraints,
            )
            for action_name in ACTION_ORDER
        ]

        return BodyActionWeights(
            weights=weights,
            body_part_offsets=motion_params.body_part_offsets,
            source_trace_id=None,
            source_proposals=[],
            body_note=_build_body_note(motion_params),
            behavior_affecting=False,
        )


def _compute_drives(mp: MotionParams) -> dict[str, float]:
    drives, gate_context = _compute_raw_drives(mp)
    drives = _apply_gates(mp, drives, gate_context)
    drives = _apply_hard_constraints(mp, drives)
    drives = _apply_derived_constraints(mp, drives)
    drives = _resolve_gaze_competition(drives)
    return _clamp_drives(drives)


def _compute_raw_drives(mp: MotionParams) -> tuple[dict[str, float], dict[str, float]]:
    look_user_raw = 0.55 * (mp.gaze_contact_sec / 1.5) + 0.45 * (1.0 - mp.gaze_release_amplitude)
    look_away_raw = 0.60 * mp.gaze_release_amplitude + 0.40 * (1.0 - 0.50 * mp.gaze_contact_sec / 1.5)

    if mp.torso_lean <= 0.02:
        slight_forward_drive = 0.0
    else:
        slight_forward_drive = mp.torso_lean / 0.20

    if mp.torso_lean >= -0.03:
        slight_withdraw_drive = 0.0
    else:
        slight_withdraw_drive = abs(mp.torso_lean) / 0.25
        completion_amp = 1.0 + 0.30 * _norm_completion(mp.motion_completion)
        slight_withdraw_drive *= completion_amp

    return (
        {
            "pause": (
                0.40 * (mp.initial_delay_sec / 2.0)
                + 0.35 * (mp.pause_after_sec / 1.5)
                + 0.25 * _inv_norm_completion(mp.motion_completion)
            ),
            "stillness": (
                0.40 * _inv_norm_completion(mp.motion_completion)
                + 0.35 * (1.0 - mp.motion_speed)
                + 0.25 * mp.posture_stability
            ),
            "look_down": (
                0.50 * (1.0 - max(look_user_raw, look_away_raw))
                + 0.30 * _inv_norm_completion(mp.motion_completion)
                + 0.20 * (1.0 - mp.posture_stability)
            ),
            "look_to_user": (
                0.55 * (mp.gaze_contact_sec / 1.5)
                + 0.45 * (1.0 - mp.gaze_release_amplitude)
            ),
            "look_away": (
                0.60 * mp.gaze_release_amplitude
                + 0.40 * (1.0 - 0.50 * mp.gaze_contact_sec / 1.5)
            ),
            "slight_forward": slight_forward_drive,
            "slight_withdraw": slight_withdraw_drive,
            "maintain_distance": (
                0.30 * mp.posture_stability
                + 0.25 * _inv_norm_completion(mp.motion_completion)
                + 0.25 * (1.0 - abs(mp.torso_lean) / 0.25)
                + 0.20 * (1.0 - mp.motion_speed)
            ),
            "reduce_motion": (
                0.40 * _inv_norm_completion(mp.motion_completion)
                + 0.35 * (1.0 - mp.motion_speed)
                + 0.25 * (1.0 - mp.expression_amplitude / 0.35)
            ),
            "reset_posture": (
                0.55 * _inv_norm_completion(mp.motion_completion)
                + 0.45 * (1.0 - mp.posture_stability)
            ),
        },
        {
            "look_user_raw": look_user_raw,
            "look_away_raw": look_away_raw,
        },
    )


def _apply_gates(
    mp: MotionParams,
    drives: dict[str, float],
    gate_context: dict[str, float],
) -> dict[str, float]:
    result = dict(drives)

    if mp.initial_delay_sec < 0.25 and mp.pause_after_sec < 0.10:
        result["pause"] *= 0.50

    if mp.motion_speed > 0.70:
        result["stillness"] *= 0.40
    if mp.motion_completion > 0.80 and mp.motion_speed > 0.60:
        result["stillness"] *= 0.30

    if gate_context["look_user_raw"] > 0.55 or gate_context["look_away_raw"] > 0.55:
        result["look_down"] *= 0.30
    if mp.gaze_contact_sec > 0.80:
        result["look_down"] = 0.0

    if mp.gaze_contact_sec < 0.10:
        result["look_to_user"] = 0.0
    if mp.gaze_release_amplitude > 0.80:
        result["look_to_user"] *= 0.35

    if mp.gaze_release_amplitude < 0.15 and mp.gaze_contact_sec > 0.60:
        result["look_away"] = 0.0
    if mp.head_turn_amplitude < 0.05:
        result["look_away"] *= 0.60

    if mp.motion_completion < 0.35:
        result["slight_forward"] = 0.0

    if abs(mp.torso_lean) > 0.15:
        result["maintain_distance"] *= 0.50
    if mp.motion_speed > 0.75:
        result["maintain_distance"] *= 0.40

    if mp.motion_speed > 0.60 and mp.motion_completion > 0.70:
        result["reduce_motion"] *= 0.30
    if mp.expression_amplitude > 0.25:
        result["reduce_motion"] *= 0.60

    if mp.posture_stability > 0.85 and mp.motion_completion > 0.75:
        result["reset_posture"] *= 0.25
    if mp.motion_completion > 0.80 and mp.posture_stability > 0.70:
        result["reset_posture"] *= 0.40

    return result


def _apply_hard_constraints(mp: MotionParams, drives: dict[str, float]) -> dict[str, float]:
    result = dict(drives)
    hc = mp.hard_constraints

    if hc.no_approach_step:
        result["slight_forward"] = 0.0
        result["maintain_distance"] += 0.15
        result["reset_posture"] += 0.10

    if hc.no_forward_lean:
        result["slight_forward"] = 0.0
        result["maintain_distance"] += 0.15
        result["reset_posture"] += 0.10

    if hc.no_cute_head_tilt:
        result["look_to_user"] *= 0.60
        result["look_down"] += 0.10

    if hc.no_welcoming_gesture:
        result["slight_forward"] = min(result["slight_forward"], 0.10)
        result["look_to_user"] *= 0.50
        result["maintain_distance"] += 0.10

    if hc.no_service_gesture:
        result["slight_forward"] = min(result["slight_forward"], 0.10)
        result["reduce_motion"] += 0.10
        result["slight_withdraw"] += 0.10

    if hc.no_seductive_expression:
        result["look_to_user"] *= 0.40
        result["slight_forward"] *= 0.30
        result["look_away"] += 0.15

    return result


def _apply_derived_constraints(mp: MotionParams, drives: dict[str, float]) -> dict[str, float]:
    result = dict(drives)

    if mp.initial_delay_sec > 1.0 or mp.pause_after_sec > 0.7:
        result["pause"] = max(result["pause"], 0.50)
        result["reduce_motion"] += 0.15

    if mp.expression_amplitude < 0.08:
        result["look_to_user"] = min(result["look_to_user"], 0.20)
        result["stillness"] += 0.10

    return result


def _resolve_gaze_competition(drives: dict[str, float]) -> dict[str, float]:
    result = dict(drives)

    if result["look_to_user"] > 0 and result["look_away"] > 0:
        if result["look_to_user"] > result["look_away"]:
            result["look_away"] = 0.0
        elif result["look_away"] > result["look_to_user"]:
            result["look_to_user"] = 0.0
        else:
            result["look_to_user"] = 0.0
            result["look_away"] = 0.0

    return result


def _clamp_drives(drives: dict[str, float]) -> dict[str, float]:
    return {
        action_name: _clamp01(drives[action_name])
        for action_name in ACTION_ORDER
    }


def _build_weight(
    action_name: str,
    motion_params: MotionParams,
    drive: float,
    active_constraints: list[str],
) -> BodyActionWeight:
    return BodyActionWeight(
        action_name=action_name,
        weight=_drive_to_band(drive),
        rationale=_rationale(action_name, motion_params, drive),
        constraints=active_constraints,
        provenance=[PROVENANCE],
        behavior_affecting=False,
    )


def _norm_completion(motion_completion: float) -> float:
    return (motion_completion - 0.20) / 0.70


def _inv_norm_completion(motion_completion: float) -> float:
    return 1.0 - _norm_completion(motion_completion)


def _drive_to_band(f: float) -> str:
    if f < 0.20:
        return "off"
    elif f < 0.45:
        return "low"
    elif f < 0.70:
        return "medium"
    else:
        return "high"


def _active_constraint_names(mp: MotionParams) -> list[str]:
    result = list(mp.hard_constraints.active_names())
    if mp.initial_delay_sec > 1.0 or mp.pause_after_sec > 0.7:
        result.append("motion_paused")
    if mp.expression_amplitude < 0.08:
        result.append("expression_suppressed")
    return result


def _build_body_note(mp: MotionParams) -> str:
    active = _active_constraint_names(mp)
    constraints = ",".join(active) if active else "none"
    return (
        "MotionParams→BodyActionWeights v1; "
        f"completion={mp.motion_completion:.2f}; "
        f"speed={mp.motion_speed:.2f}; "
        f"constraints={constraints}"
    )


def _rationale(action_name: str, mp: MotionParams, drive: float) -> str:
    if action_name == "pause":
        return (
            f"delay={mp.initial_delay_sec:.2f}s "
            f"pause_after={mp.pause_after_sec:.2f}s "
            f"completion={mp.motion_completion:.2f} drive={drive:.2f}"
        )
    if action_name in {"look_to_user", "look_away", "look_down"}:
        return (
            f"gaze_contact={mp.gaze_contact_sec:.2f}s "
            f"gaze_release={mp.gaze_release_amplitude:.2f} "
            f"head_turn={mp.head_turn_amplitude:.2f} drive={drive:.2f}"
        )
    if action_name in {"slight_forward", "slight_withdraw", "maintain_distance", "reset_posture"}:
        return (
            f"torso_lean={mp.torso_lean:.2f} "
            f"posture={mp.posture_stability:.2f} "
            f"completion={mp.motion_completion:.2f} drive={drive:.2f}"
        )
    return (
        f"speed={mp.motion_speed:.2f} "
        f"expression={mp.expression_amplitude:.2f} "
        f"completion={mp.motion_completion:.2f} drive={drive:.2f}"
    )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


__all__ = [
    "ACTION_ORDER",
    "MotionToActionMapper",
    "_drive_to_band",
]
