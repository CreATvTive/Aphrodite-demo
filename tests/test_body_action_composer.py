from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from src.body_action import BodyActionComposer
from src.body_action.composer import BodyActionComposer as DirectBodyActionComposer
from src.body_action.schema import (
    ActionSequenceHint,
    BodyActionComposition,
    BodyActionWeight,
    BodyActionWeights,
)
from src.motion_params.schema import BodyPartOffsets


def _weights(*items: tuple[str, str], constraints: list[str] | None = None) -> BodyActionWeights:
    active_constraints = constraints or []
    return BodyActionWeights(
        weights=[
            BodyActionWeight(
                action_name=action_name,
                weight=weight,
                constraints=list(active_constraints),
                provenance=["test"],
                behavior_affecting=False,
            )
            for action_name, weight in items
        ],
        behavior_affecting=False,
    )


def _compose(weights: BodyActionWeights) -> BodyActionComposition:
    composition = BodyActionComposer().compose(weights)
    assert isinstance(composition, BodyActionComposition)
    assert composition.behavior_affecting is False
    for hint in composition.primary_actions + composition.secondary_actions:
        assert isinstance(hint, ActionSequenceHint)
        assert hint.behavior_affecting is False
        assert hint.provenance == ["BodyActionWeights->BodyActionComposition v0"]
    return composition


def _names(actions: list[ActionSequenceHint]) -> list[str]:
    return [action.action_name for action in actions]


def test_1_high_pause_becomes_primary_action():
    composition = _compose(_weights(("pause", "high")))

    assert _names(composition.primary_actions) == ["pause"]


def test_2_medium_look_away_becomes_active_action():
    composition = _compose(_weights(("look_away", "medium")))

    assert "look_away" in _names(composition.primary_actions) + _names(composition.secondary_actions)


def test_3_low_action_becomes_secondary_not_primary():
    composition = _compose(_weights(("look_down", "low")))

    assert "look_down" not in _names(composition.primary_actions)
    assert "look_down" in _names(composition.secondary_actions)


def test_4_off_action_is_not_active():
    composition = _compose(_weights(("look_down", "off")))

    assert "look_down" not in _names(composition.primary_actions)
    assert "look_down" not in _names(composition.secondary_actions)


def test_5_look_to_user_and_look_away_cannot_both_be_primary():
    composition = _compose(_weights(("look_to_user", "high"), ("look_away", "high")))
    primary = _names(composition.primary_actions)

    assert not {"look_to_user", "look_away"}.issubset(primary)
    assert "look_away" in primary


def test_6_slight_forward_and_slight_withdraw_cannot_both_be_primary():
    composition = _compose(_weights(("slight_forward", "high"), ("slight_withdraw", "high")))
    primary = _names(composition.primary_actions)

    assert not {"slight_forward", "slight_withdraw"}.issubset(primary)
    assert "slight_withdraw" in primary


def test_7_high_reduce_motion_does_not_erase_gaze_micro_action():
    composition = _compose(_weights(("reduce_motion", "high"), ("look_down", "low")))

    assert "reduce_motion" in _names(composition.primary_actions)
    assert "look_down" in _names(composition.secondary_actions)


def test_8_high_stillness_does_not_create_empty_lifeless_composition():
    composition = _compose(_weights(("stillness", "high"), ("look_down", "low")))

    assert composition.primary_actions
    assert "stillness" in _names(composition.primary_actions)
    assert "look_down" in _names(composition.secondary_actions)


def test_9_maintain_distance_can_coexist_with_pause_and_reduce_motion():
    composition = _compose(_weights(
        ("pause", "high"),
        ("reduce_motion", "high"),
        ("maintain_distance", "medium"),
    ))
    primary = _names(composition.primary_actions)

    assert "pause" in primary
    assert "reduce_motion" in primary
    assert "maintain_distance" in primary


def test_10_reset_posture_secondary_when_supporting_distance_or_reduction():
    composition = _compose(_weights(("maintain_distance", "high"), ("reset_posture", "medium")))

    assert "maintain_distance" in _names(composition.primary_actions)
    assert "reset_posture" in _names(composition.secondary_actions)


def test_11_duration_hint_for_pause_gaze_and_posture_actions():
    composition = _compose(_weights(
        ("pause", "high"),
        ("look_away", "medium"),
        ("maintain_distance", "medium"),
        ("reset_posture", "low"),
    ))
    hints = {hint.action_name: hint.duration_hint for hint in composition.primary_actions + composition.secondary_actions}

    assert hints["pause"] == "sustained"
    assert hints["look_away"] == "short"
    assert hints["maintain_distance"] == "sustained"
    assert hints["reset_posture"] == "short"


def test_12_completion_restrained_when_reduce_motion_or_stillness_high():
    composition = _compose(_weights(("reduce_motion", "high"), ("look_down", "low")))

    assert {hint.completion for hint in composition.primary_actions + composition.secondary_actions} == {"restrained"}


def test_13_suppressed_actions_include_constrained_off_actions():
    composition = _compose(_weights(
        ("slight_forward", "off"),
        ("maintain_distance", "medium"),
        constraints=["no_approach_step", "no_forward_lean"],
    ))

    assert "slight_forward" in composition.suppressed_actions
    assert "no_approach_step" in composition.hard_constraints
    assert "no_forward_lean" in composition.hard_constraints


def test_14_body_part_offsets_are_preserved_in_note_without_animation_expansion():
    offsets = BodyPartOffsets(gaze_offset_ms=0, head_offset_ms=40, shoulder_offset_ms=90, hand_offset_ms=140)
    action_weights = _weights(("look_down", "low"))
    action_weights.body_part_offsets = offsets
    composition = _compose(action_weights)

    assert "gaze:0ms" in composition.composition_note
    assert "head:40ms" in composition.composition_note
    assert "curve" not in composition.composition_note.lower()


def test_15_behavior_affecting_remains_false():
    composition = _compose(_weights(("pause", "high"), ("look_down", "low")))

    assert composition.behavior_affecting is False
    assert all(action.behavior_affecting is False for action in composition.primary_actions)
    assert all(action.behavior_affecting is False for action in composition.secondary_actions)
    assert all(weight.behavior_affecting is False for weight in composition.source_weights)


def test_16_composer_consumes_only_body_action_weights():
    signature = inspect.signature(DirectBodyActionComposer.compose)

    assert tuple(signature.parameters) == ("self", "action_weights")
    assert signature.parameters["action_weights"].annotation in {BodyActionWeights, "BodyActionWeights"}
    with pytest.raises(TypeError, match="BodyActionWeights"):
        BodyActionComposer().compose({"pause": "high"})  # type: ignore[arg-type]


def test_17_composer_has_no_forbidden_imports_or_semantic_inputs():
    source = Path("src/body_action/composer.py").read_text(encoding="utf-8")
    forbidden = (
        "FieldTrace",
        "field_trace",
        "FieldState",
        "field_state",
        "MotionParams",
        "motion_to_action_mapper",
        "RuntimeEngine",
        "runtime_engine",
        "llm",
        "renderer",
        "animation",
        "avatar",
        "language",
        "prompt",
        "raw_text",
        "user_text",
        "user_input",
    )

    for token in forbidden:
        assert token not in source


def test_18_composer_does_not_implement_renderer_or_animation_curves():
    source = Path("src/body_action/composer.py").read_text(encoding="utf-8").lower()

    for token in ("render", "animate", "animation_curve", "keyframe", "execute"):
        assert token not in source


def test_19_composer_does_not_reference_v0_policy_or_motion_mapper():
    source = Path("src/body_action/composer.py").read_text(encoding="utf-8")

    assert "BodyActionPolicy" not in source
    assert "motion_to_action_mapper" not in source
    assert not Path("src/body_action/policy_v1.py").exists()


def test_20_to_dict_output_is_json_serializable():
    composition = _compose(_weights(
        ("pause", "high"),
        ("look_down", "low"),
        ("slight_forward", "off"),
        constraints=["no_approach_step"],
    ))

    encoded = json.dumps(composition.to_dict(), ensure_ascii=False)
    assert "pause" in encoded
    assert "look_down" in encoded
    assert "behavior_affecting" in encoded


def test_21_real_composer_output_to_dict_recursively_serializes_nested_items():
    composition = _compose(_weights(
        ("pause", "high"),
        ("reduce_motion", "high"),
        ("look_down", "low"),
        constraints=["no_service_gesture"],
    ))

    payload = composition.to_dict()
    encoded = json.dumps(payload, ensure_ascii=False)

    assert isinstance(payload["primary_actions"][0], dict)
    assert isinstance(payload["secondary_actions"][0], dict)
    assert isinstance(payload["source_weights"][0], dict)
    assert payload["behavior_affecting"] is False
    assert all(action["behavior_affecting"] is False for action in payload["primary_actions"])
    assert all(action["behavior_affecting"] is False for action in payload["secondary_actions"])
    assert all(weight["behavior_affecting"] is False for weight in payload["source_weights"])
    assert "no_service_gesture" in encoded
