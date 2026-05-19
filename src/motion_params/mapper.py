from __future__ import annotations

from src.field_state.schema import RelationalFieldState

from .schema import BodyPartOffsets, HardMotionConstraints, MotionParams


class FieldStateToMotionParamsMapper:
    def map(self, state: RelationalFieldState) -> MotionParams:
        if not isinstance(state, RelationalFieldState):
            raise TypeError("state must be a RelationalFieldState")

        v = {name: variable.numeric_value for name, variable in state.variables.items()}

        approach_tendency = _clamp01(
            0.20 * v["affective_warmth"]
            + 0.55 * v["structural_grip_pressure"]
            + 0.25 * v["collaborator_layer_pressure"]
        )
        completion_inhibition = _clamp01(
            0.28 * v["boundary_distance"]
            + 0.30 * v["contamination_pressure"]
            + 0.20 * v["contamination_resistance"]
            + 0.18 * v["service_resistance"]
            + 0.22 * v["withdrawal_tendency"]
            + 0.20 * v["correction_pressure"]
        )
        stability_force = _clamp01(
            0.60 * v["presence_stability"]
            + 0.20 * v["contamination_resistance"]
            + 0.20 * v["service_resistance"]
        )
        visible_forward_motion = approach_tendency * (1.0 - completion_inhibition)

        motion_completion = _clamp(
            0.85
            - 0.55 * completion_inhibition
            + 0.10 * v["presence_stability"],
            0.20,
            0.90,
        )
        initial_delay_sec = _clamp(
            0.12
            + 0.60 * completion_inhibition
            + 0.35 * v["boundary_distance"]
            + 0.35 * v["correction_pressure"]
            - 0.15 * visible_forward_motion,
            0.0,
            2.0,
        )
        motion_speed = _clamp(
            0.48
            + 0.28 * approach_tendency
            - 0.35 * completion_inhibition
            + 0.10 * v["presence_stability"],
            0.0,
            1.0,
        )
        pause_after_sec = _clamp(
            0.08
            + 0.60 * v["correction_pressure"]
            + 0.24 * v["boundary_distance"]
            + 0.25 * completion_inhibition,
            0.0,
            1.5,
        )
        gaze_release_amplitude = _clamp(
            0.18
            + 0.40 * v["boundary_distance"]
            + 0.28 * v["withdrawal_tendency"]
            + 0.22 * v["contamination_pressure"]
            + 0.16 * v["contamination_resistance"]
            - 0.10 * approach_tendency,
            0.0,
            1.0,
        )
        gaze_contact_sec = _clamp(
            1.20
            * (
                0.35
                + 0.60 * approach_tendency
                + 0.15 * v["presence_stability"]
                - 0.35 * completion_inhibition
                - 0.50 * v["contamination_pressure"]
                - 0.20 * v["boundary_distance"]
            ),
            0.0,
            1.5,
        )
        head_turn_amplitude = _clamp(
            0.08
            + 0.25 * gaze_release_amplitude
            + 0.20 * visible_forward_motion
            + 0.10 * v["withdrawal_tendency"]
            - 0.10 * v["contamination_resistance"],
            0.0,
            0.5,
        )
        head_turn_delay_sec = _clamp(
            0.04
            + 0.25 * gaze_release_amplitude
            + 0.10 * v["boundary_distance"]
            + 0.10 * v["withdrawal_tendency"]
            - 0.08 * v["presence_stability"],
            0.0,
            0.5,
        )
        torso_lean = _clamp(
            0.16 * visible_forward_motion
            + 0.04 * v["structural_grip_pressure"]
            - 0.22 * v["contamination_pressure"]
            - 0.10 * v["boundary_distance"]
            - 0.10 * v["withdrawal_tendency"]
            - 0.05 * v["service_resistance"]
            - 0.04 * v["contamination_resistance"],
            -0.25,
            0.20,
        )
        posture_stability = _clamp(
            0.25
            + 0.55 * stability_force
            + 0.10 * v["presence_stability"]
            - 0.12 * v["correction_pressure"]
            - 0.08 * v["contamination_pressure"],
            0.0,
            1.0,
        )
        expression_amplitude = _expression_amplitude(v)

        hard_constraints = _hard_constraints(v)
        params = _apply_hard_constraints(
            {
                "initial_delay_sec": initial_delay_sec,
                "motion_speed": motion_speed,
                "pause_after_sec": pause_after_sec,
                "gaze_contact_sec": gaze_contact_sec,
                "gaze_release_amplitude": gaze_release_amplitude,
                "head_turn_amplitude": head_turn_amplitude,
                "head_turn_delay_sec": head_turn_delay_sec,
                "torso_lean": torso_lean,
                "posture_stability": posture_stability,
                "expression_amplitude": expression_amplitude,
                "motion_completion": motion_completion,
            },
            hard_constraints,
        )

        return MotionParams(
            **params,
            body_part_offsets=_body_part_offsets(v, completion_inhibition, stability_force),
            hard_constraints=hard_constraints,
            source_state_note=state.state_note,
            field_snapshot_note=_field_snapshot_note(state),
            provenance="RelationalFieldState.numeric_value -> MotionParams v0",
            behavior_affecting=False,
        )


def map_field_state_to_motion_params(state: RelationalFieldState) -> MotionParams:
    return FieldStateToMotionParamsMapper().map(state)


def _expression_amplitude(v: dict[str, float]) -> float:
    expression_pressure = 0.08 + 0.32 * v["affective_warmth"]
    expression_cap = _clamp(
        0.36
        - 0.18 * v["contamination_pressure"]
        - 0.14 * v["service_resistance"]
        - 0.12 * v["boundary_distance"]
        - 0.12 * v["contamination_resistance"],
        0.06,
        0.30,
    )
    return _clamp(
        min(expression_pressure, expression_cap)
        * (0.85 + 0.15 * v["presence_stability"]),
        0.0,
        0.35,
    )


def _hard_constraints(v: dict[str, float]) -> HardMotionConstraints:
    return HardMotionConstraints(
        no_approach_step=(
            v["contamination_pressure"] >= 0.30
            or v["contamination_resistance"] >= 0.80
            or v["service_resistance"] >= 0.85
            or v["boundary_distance"] >= 0.85
        ),
        no_forward_lean=(
            v["contamination_pressure"] >= 0.25
            or v["contamination_resistance"] >= 0.75
            or v["service_resistance"] >= 0.75
            or v["boundary_distance"] >= 0.80
        ),
        no_cute_head_tilt=(
            v["contamination_resistance"] >= 0.65
            or v["contamination_pressure"] >= 0.25
            or v["service_resistance"] >= 0.70
        ),
        no_welcoming_gesture=(
            v["service_resistance"] >= 0.60
            or v["contamination_resistance"] >= 0.70
            or v["contamination_pressure"] >= 0.25
        ),
        no_service_gesture=(
            v["service_resistance"] >= 0.60
            or v["contamination_resistance"] >= 0.70
            or v["contamination_pressure"] >= 0.25
        ),
        no_seductive_expression=(
            v["contamination_resistance"] >= 0.60
            or v["contamination_pressure"] >= 0.20
            or v["boundary_distance"] >= 0.75
        ),
    )


def _apply_hard_constraints(
    params: dict[str, float],
    hard_constraints: HardMotionConstraints,
) -> dict[str, float]:
    result = dict(params)

    if hard_constraints.no_approach_step:
        result["motion_speed"] = min(result["motion_speed"], 0.42)
        result["motion_completion"] = min(result["motion_completion"], 0.72)
    if hard_constraints.no_forward_lean:
        result["torso_lean"] = min(result["torso_lean"], 0.0)
    if hard_constraints.no_cute_head_tilt:
        result["head_turn_amplitude"] = min(result["head_turn_amplitude"], 0.22)
    if hard_constraints.no_welcoming_gesture:
        result["gaze_contact_sec"] = min(result["gaze_contact_sec"], 0.75)
        result["motion_completion"] = min(result["motion_completion"], 0.78)
    if hard_constraints.no_service_gesture:
        result["pause_after_sec"] = max(result["pause_after_sec"], 0.18)
        result["torso_lean"] = min(result["torso_lean"], 0.02)
    if hard_constraints.no_seductive_expression:
        result["expression_amplitude"] = min(result["expression_amplitude"], 0.14)
        result["gaze_contact_sec"] = min(result["gaze_contact_sec"], 0.65)

    return result


def _body_part_offsets(
    v: dict[str, float],
    completion_inhibition: float,
    stability_force: float,
) -> BodyPartOffsets:
    span = _clamp(
        80
        + 260 * (1.0 - v["presence_stability"])
        + 70 * v["withdrawal_tendency"]
        + 60 * v["correction_pressure"]
        + 50 * completion_inhibition
        - 40 * stability_force,
        60,
        450,
    )
    return BodyPartOffsets(
        gaze_offset_ms=0,
        head_offset_ms=int(round(40 + span * 0.25)),
        shoulder_offset_ms=int(round(80 + span * 0.55)),
        hand_offset_ms=int(round(120 + span * 0.85)),
    )


def _field_snapshot_note(state: RelationalFieldState) -> str:
    variables = state.variables
    return (
        f"boundary={variables['boundary_distance'].value} "
        f"warmth={variables['affective_warmth'].value} "
        f"grip={variables['structural_grip_pressure'].value} "
        f"correction={variables['correction_pressure'].value} "
        f"contamination_res={variables['contamination_resistance'].value} "
        f"presence_stab={variables['presence_stability'].value} "
        f"withdrawal={variables['withdrawal_tendency'].value} "
        f"service_res={variables['service_resistance'].value} "
        f"collaborator={variables['collaborator_layer_pressure'].value} "
        f"contamination_p={variables['contamination_pressure'].value}"
    )


def _clamp01(value: float) -> float:
    return _clamp(value, 0.0, 1.0)


def _clamp(value: float, lower: float, upper: float) -> float:
    return round(max(lower, min(upper, value)), 3)
