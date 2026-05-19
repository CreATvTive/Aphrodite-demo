from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.body_action.motion_to_action_mapper import MotionToActionMapper
from src.body_action.schema import ACTION_PRIMITIVES, WEIGHT_BANDS, BodyActionWeights
from src.motion_params.schema import BodyPartOffsets, HardMotionConstraints, MotionParams


BAND_ORDER = {"off": 0, "low": 1, "medium": 2, "high": 3}

BASE_MOTION = {
    "initial_delay_sec": 0.0,
    "motion_speed": 0.50,
    "pause_after_sec": 0.0,
    "gaze_contact_sec": 0.0,
    "gaze_release_amplitude": 0.0,
    "head_turn_amplitude": 0.10,
    "head_turn_delay_sec": 0.0,
    "torso_lean": 0.0,
    "posture_stability": 0.50,
    "expression_amplitude": 0.15,
    "motion_completion": 0.60,
    "body_part_offsets": BodyPartOffsets(
        gaze_offset_ms=0,
        head_offset_ms=60,
        shoulder_offset_ms=120,
        hand_offset_ms=180,
    ),
    "hard_constraints": HardMotionConstraints(),
    "behavior_affecting": False,
}


def _mp(**overrides) -> MotionParams:
    values = dict(BASE_MOTION)
    values.update(overrides)
    return MotionParams(**values)


def _map(mp: MotionParams) -> BodyActionWeights:
    result = MotionToActionMapper().map(mp)
    assert isinstance(result, BodyActionWeights)
    assert result.behavior_affecting is False
    assert result.source_trace_id is None
    assert result.source_proposals == []
    assert result.body_part_offsets is mp.body_part_offsets
    assert {weight.action_name for weight in result.weights} == ACTION_PRIMITIVES
    for weight in result.weights:
        assert weight.behavior_affecting is False
        assert weight.weight in WEIGHT_BANDS
        assert weight.provenance == ["MotionParams→BodyActionWeights v1"]
    return result


def _weights(mp: MotionParams) -> dict[str, str]:
    return {weight.action_name: weight.weight for weight in _map(mp).weights}


def _at_least(actual: str, expected: str) -> bool:
    return BAND_ORDER[actual] >= BAND_ORDER[expected]


def _at_most(actual: str, expected: str) -> bool:
    return BAND_ORDER[actual] <= BAND_ORDER[expected]


def test_pause_driven_by_high_delay():
    weights = _weights(_mp(initial_delay_sec=1.8, pause_after_sec=1.2, motion_completion=0.40))

    assert _at_least(weights["pause"], "medium")


def test_pause_off_when_low_delay():
    weights = _weights(_mp(initial_delay_sec=0.10, pause_after_sec=0.05, motion_completion=0.85))

    assert weights["pause"] == "off"


def test_stillness_driven_by_low_completion():
    weights = _weights(_mp(motion_speed=0.20, motion_completion=0.30, posture_stability=0.80))

    assert _at_least(weights["stillness"], "medium")


def test_stillness_off_when_high_speed():
    weights = _weights(_mp(motion_speed=0.85, motion_completion=0.85))

    assert _at_most(weights["stillness"], "low")


def test_look_down_emerges_when_neither_look_dominates():
    weights = _weights(_mp(gaze_contact_sec=0.20, gaze_release_amplitude=0.25, motion_completion=0.45))

    assert _at_least(weights["look_down"], "low")


def test_look_to_user_driven_by_gaze_contact():
    weights = _weights(_mp(gaze_contact_sec=1.20, gaze_release_amplitude=0.10))

    assert _at_least(weights["look_to_user"], "high")
    assert weights["look_away"] == "off"


def test_look_to_user_off_when_zero_contact():
    weights = _weights(_mp(gaze_contact_sec=0.05, gaze_release_amplitude=0.20))

    assert weights["look_to_user"] == "off"


def test_look_away_driven_by_high_release():
    weights = _weights(_mp(gaze_release_amplitude=0.85, gaze_contact_sec=0.10))

    assert _at_least(weights["look_away"], "high")
    assert weights["look_to_user"] == "off"


def test_slight_forward_driven_by_torso_lean():
    weights = _weights(_mp(torso_lean=0.18, motion_completion=0.70))

    assert _at_least(weights["slight_forward"], "medium")


def test_slight_withdraw_driven_by_negative_torso():
    weights = _weights(_mp(torso_lean=-0.22, motion_completion=0.75))

    assert _at_least(weights["slight_withdraw"], "medium")


def test_slight_forward_gated_by_low_completion():
    weights = _weights(_mp(torso_lean=0.18, motion_completion=0.25))

    assert weights["slight_forward"] == "off"


def test_slight_forward_gated_by_tiny_torso():
    weights = _weights(_mp(torso_lean=0.01, motion_completion=0.70))

    assert weights["slight_forward"] == "off"


def test_stillness_gated_by_high_speed_and_completion():
    weights = _weights(_mp(motion_speed=0.75, motion_completion=0.85))

    assert _at_most(weights["stillness"], "low")


def test_look_down_gated_by_high_gaze_contact():
    weights = _weights(_mp(gaze_contact_sec=0.90, gaze_release_amplitude=0.30))

    assert weights["look_down"] == "off"


def test_maintain_distance_gated_by_high_torso_abs():
    weights = _weights(_mp(torso_lean=0.18, posture_stability=0.80, motion_completion=0.40))

    assert _at_most(weights["maintain_distance"], "low")


def test_look_user_wins_over_look_away():
    weights = _weights(_mp(gaze_contact_sec=1.0, gaze_release_amplitude=0.40))

    assert _at_least(weights["look_to_user"], "medium")
    assert weights["look_away"] == "off"


def test_look_away_wins_over_look_user():
    weights = _weights(_mp(gaze_contact_sec=0.20, gaze_release_amplitude=0.80))

    assert _at_least(weights["look_away"], "medium")
    assert weights["look_to_user"] == "off"


def test_look_tie_resolves_to_look_down():
    weights = _weights(_mp(gaze_contact_sec=0.11, gaze_release_amplitude=0.10))

    assert weights["look_to_user"] == "off"
    assert weights["look_away"] == "off"
    assert _at_least(weights["look_down"], "low")


def test_no_approach_step_blocks_slight_forward():
    weights = _weights(_mp(
        torso_lean=0.18,
        motion_completion=0.70,
        hard_constraints=HardMotionConstraints(no_approach_step=True),
    ))

    assert weights["slight_forward"] == "off"
    assert _at_least(weights["maintain_distance"], "low")
    assert _at_least(weights["reset_posture"], "medium")


def test_no_forward_lean_blocks_slight_forward():
    weights = _weights(_mp(
        torso_lean=0.15,
        motion_completion=0.65,
        hard_constraints=HardMotionConstraints(no_forward_lean=True),
    ))

    assert weights["slight_forward"] == "off"


def test_no_cute_head_tilt_reduces_look_to_user():
    base = _weights(_mp(gaze_contact_sec=1.0, gaze_release_amplitude=0.10))
    constrained = _weights(_mp(
        gaze_contact_sec=1.0,
        gaze_release_amplitude=0.10,
        hard_constraints=HardMotionConstraints(no_cute_head_tilt=True),
    ))

    assert BAND_ORDER[constrained["look_to_user"]] < BAND_ORDER[base["look_to_user"]]
    assert _at_least(constrained["look_down"], "off")


def test_no_welcoming_gesture_caps_forward_and_gaze():
    weights = _weights(_mp(
        torso_lean=0.18,
        gaze_contact_sec=0.80,
        hard_constraints=HardMotionConstraints(no_welcoming_gesture=True),
    ))

    assert _at_most(weights["slight_forward"], "low")
    assert _at_most(weights["look_to_user"], "low")


def test_no_service_gesture_caps_forward_and_expression():
    weights = _weights(_mp(
        torso_lean=0.15,
        expression_amplitude=0.30,
        hard_constraints=HardMotionConstraints(no_service_gesture=True),
    ))

    assert _at_most(weights["slight_forward"], "low")
    assert _at_least(weights["reduce_motion"], "low")


def test_no_seductive_expression_reduces_gaze_and_forward():
    weights = _weights(_mp(
        gaze_contact_sec=0.90,
        torso_lean=0.16,
        hard_constraints=HardMotionConstraints(no_seductive_expression=True),
    ))

    assert _at_most(weights["look_to_user"], "low")
    assert _at_most(weights["slight_forward"], "low")


def test_motion_paused_forces_pause_high():
    weights = _weights(_mp(initial_delay_sec=1.40, pause_after_sec=0.10))

    assert _at_least(weights["pause"], "medium")


def test_expression_suppressed_caps_look_to_user():
    weights = _weights(_mp(expression_amplitude=0.05, gaze_contact_sec=0.80))

    assert _at_most(weights["look_to_user"], "low")


def test_no_default_forward_lean():
    weights = _weights(_mp(torso_lean=0.0, motion_completion=0.50))

    assert weights["slight_forward"] == "off"


def test_no_default_gaze_lock():
    weights = _weights(_mp(gaze_contact_sec=0.0, gaze_release_amplitude=0.0))

    assert weights["look_to_user"] == "off"


def test_frozen_state_still_has_micro_motion():
    weights = _weights(_mp(
        motion_speed=0.15,
        motion_completion=0.25,
        posture_stability=0.90,
        expression_amplitude=0.04,
        gaze_contact_sec=0.20,
        gaze_release_amplitude=0.25,
    ))

    assert _at_least(weights["stillness"], "high")
    assert _at_least(weights["look_down"], "low")
    assert _at_least(weights["look_away"], "low")


def test_all_params_at_minimum():
    offsets = BodyPartOffsets(gaze_offset_ms=0, head_offset_ms=0, shoulder_offset_ms=0, hand_offset_ms=0)
    weights = _map(MotionParams(
        initial_delay_sec=0.0,
        motion_speed=0.0,
        pause_after_sec=0.0,
        gaze_contact_sec=0.0,
        gaze_release_amplitude=0.0,
        head_turn_amplitude=0.0,
        head_turn_delay_sec=0.0,
        torso_lean=-0.25,
        posture_stability=0.0,
        expression_amplitude=0.0,
        motion_completion=0.20,
        body_part_offsets=offsets,
        hard_constraints=HardMotionConstraints(),
        behavior_affecting=False,
    ))

    assert weights.body_part_offsets is offsets
    assert all(weight.weight in WEIGHT_BANDS for weight in weights.weights)


def test_all_params_at_maximum():
    offsets = BodyPartOffsets(gaze_offset_ms=0, head_offset_ms=600, shoulder_offset_ms=600, hand_offset_ms=600)
    weights = _map(MotionParams(
        initial_delay_sec=2.0,
        motion_speed=1.0,
        pause_after_sec=1.5,
        gaze_contact_sec=1.5,
        gaze_release_amplitude=1.0,
        head_turn_amplitude=0.5,
        head_turn_delay_sec=0.5,
        torso_lean=0.20,
        posture_stability=1.0,
        expression_amplitude=0.35,
        motion_completion=0.90,
        body_part_offsets=offsets,
        hard_constraints=HardMotionConstraints(
            no_approach_step=True,
            no_forward_lean=True,
            no_cute_head_tilt=True,
            no_welcoming_gesture=True,
            no_service_gesture=True,
            no_seductive_expression=True,
        ),
        behavior_affecting=False,
    ))
    by_name = {weight.action_name: weight.weight for weight in weights.weights}

    assert weights.body_part_offsets is offsets
    assert all(weight.weight in WEIGHT_BANDS for weight in weights.weights)
    assert _at_most(by_name["slight_forward"], "high")
    assert not Path("src/body_action/policy_v1.py").exists()


def test_complex_representative_output_bands_remain_stable_after_refactor():
    weights = _weights(_mp(
        initial_delay_sec=1.10,
        motion_speed=0.35,
        pause_after_sec=0.80,
        gaze_contact_sec=0.55,
        gaze_release_amplitude=0.65,
        head_turn_amplitude=0.04,
        head_turn_delay_sec=0.20,
        torso_lean=-0.12,
        posture_stability=0.78,
        expression_amplitude=0.06,
        motion_completion=0.45,
        hard_constraints=HardMotionConstraints(
            no_forward_lean=True,
            no_service_gesture=True,
            no_seductive_expression=True,
        ),
    ))

    assert weights == {
        "pause": "medium",
        "stillness": "high",
        "look_down": "off",
        "look_to_user": "off",
        "look_away": "medium",
        "slight_forward": "off",
        "slight_withdraw": "medium",
        "maintain_distance": "high",
        "reduce_motion": "high",
        "reset_posture": "medium",
    }


def test_real_mapper_output_to_dict_is_json_serializable_with_offsets():
    offsets = BodyPartOffsets(
        gaze_offset_ms=0,
        head_offset_ms=45,
        shoulder_offset_ms=95,
        hand_offset_ms=155,
    )
    action_weights = _map(_mp(
        gaze_contact_sec=0.30,
        gaze_release_amplitude=0.70,
        body_part_offsets=offsets,
        hard_constraints=HardMotionConstraints(no_service_gesture=True),
    ))

    payload = action_weights.to_dict()
    encoded = json.dumps(payload, ensure_ascii=False)

    assert payload["body_part_offsets"] == {
        "gaze_offset_ms": 0,
        "head_offset_ms": 45,
        "shoulder_offset_ms": 95,
        "hand_offset_ms": 155,
    }
    assert payload["behavior_affecting"] is False
    assert all(weight["behavior_affecting"] is False for weight in payload["weights"])
    assert "look_away" in encoded
