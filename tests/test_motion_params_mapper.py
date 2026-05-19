from __future__ import annotations

import ast
import inspect
from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from pathlib import Path

import pytest

from src.field_state.schema import RelationalFieldState, create_ground_state, create_ground_state_variables
from src.motion_params import (
    BODY_PART_OFFSET_BOUNDS,
    HARD_CONSTRAINT_FIELDS,
    MOTION_PARAM_BOUNDS,
    BodyPartOffsets,
    FieldStateToMotionParamsMapper,
    HardMotionConstraints,
    MotionParams,
    map_field_state_to_motion_params,
)


def _build_state(**overrides: float) -> RelationalFieldState:
    variables = create_ground_state_variables()
    for name, numeric_value in overrides.items():
        variables[name] = replace(variables[name], numeric_value=numeric_value)
    return RelationalFieldState(variables=variables, state_note="test relational field state")


def _motion_source() -> str:
    source_dir = Path("src/motion_params")
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(source_dir.glob("*.py")))


def _assert_motion_params_bounded(params: MotionParams) -> None:
    for name, (lower, upper) in MOTION_PARAM_BOUNDS.items():
        value = getattr(params, name)
        assert lower <= value <= upper, f"{name}={value} outside {lower}..{upper}"
    for name, (lower, upper) in BODY_PART_OFFSET_BOUNDS.items():
        value = getattr(params.body_part_offsets, name)
        assert lower <= value <= upper, f"{name}={value} outside {lower}..{upper}"


def test_1_mapper_consumes_relational_field_state_only():
    mapper = FieldStateToMotionParamsMapper()
    signature = inspect.signature(mapper.map)

    assert tuple(signature.parameters) == ("state",)
    assert signature.parameters["state"].annotation in {RelationalFieldState, "RelationalFieldState"}
    with pytest.raises(TypeError, match="RelationalFieldState"):
        mapper.map({"boundary_distance": 0.5})  # type: ignore[arg-type]


def test_2_mapper_does_not_inspect_raw_user_input():
    source = _motion_source()
    forbidden = (
        "raw_text",
        "user_text",
        "user_input",
        "user_message",
        "prompt",
        "message_text",
    )
    for token in forbidden:
        assert token not in source


def test_3_mapper_has_no_forbidden_architecture_imports():
    source_dir = Path("src/motion_params")
    forbidden_import_fragments = (
        "field_trace",
        "ProposalAggregator",
        "FieldSignalProposal",
        "FieldPerturbation",
        "RuntimeEngine",
        "runtime_engine",
        "llm",
        "router",
        "memory",
        "renderer",
        "animation",
        "avatar",
        "body_action",
    )

    for path in sorted(source_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not any(fragment in name for fragment in forbidden_import_fragments)


def test_4_output_is_motion_params_not_body_action_weights():
    params = map_field_state_to_motion_params(create_ground_state())

    assert isinstance(params, MotionParams)
    assert "BodyActionWeights" not in _motion_source()


def test_5_high_boundary_distance_increases_delay_and_gaze_release():
    mapper = FieldStateToMotionParamsMapper()
    low_boundary = mapper.map(_build_state(boundary_distance=0.10))
    high_boundary = mapper.map(_build_state(boundary_distance=0.90))

    assert high_boundary.initial_delay_sec > low_boundary.initial_delay_sec
    assert high_boundary.gaze_release_amplitude > low_boundary.gaze_release_amplitude


def test_6_high_contamination_pressure_suppresses_expression_and_torso_lean():
    mapper = FieldStateToMotionParamsMapper()
    low_pressure = mapper.map(_build_state(contamination_pressure=0.0, structural_grip_pressure=0.80))
    high_pressure = mapper.map(_build_state(contamination_pressure=0.80, structural_grip_pressure=0.80))

    assert high_pressure.expression_amplitude < low_pressure.expression_amplitude
    assert high_pressure.torso_lean < low_pressure.torso_lean
    assert high_pressure.hard_constraints.no_forward_lean is True


def test_7_high_service_resistance_blocks_service_and_welcoming_gestures():
    params = FieldStateToMotionParamsMapper().map(_build_state(service_resistance=0.80))

    assert params.hard_constraints.no_service_gesture is True
    assert params.hard_constraints.no_welcoming_gesture is True


def test_8_high_contamination_resistance_blocks_seductive_and_cute_motion():
    params = FieldStateToMotionParamsMapper().map(_build_state(contamination_resistance=0.80))

    assert params.hard_constraints.no_seductive_expression is True
    assert params.hard_constraints.no_cute_head_tilt is True
    assert params.hard_constraints.no_service_gesture is True
    assert params.hard_constraints.no_welcoming_gesture is True


def test_9_high_withdrawal_tendency_decreases_motion_completion():
    mapper = FieldStateToMotionParamsMapper()
    low_withdrawal = mapper.map(_build_state(withdrawal_tendency=0.0))
    high_withdrawal = mapper.map(_build_state(withdrawal_tendency=0.90))

    assert high_withdrawal.motion_completion < low_withdrawal.motion_completion


def test_10_high_presence_stability_increases_posture_stability():
    mapper = FieldStateToMotionParamsMapper()
    low_presence = mapper.map(_build_state(presence_stability=0.20))
    high_presence = mapper.map(_build_state(presence_stability=0.95))

    assert high_presence.posture_stability > low_presence.posture_stability


def test_10b_high_presence_stability_reduces_noise_without_freezing_motion():
    mapper = FieldStateToMotionParamsMapper()
    low_presence = mapper.map(_build_state(
        presence_stability=0.15,
        boundary_distance=0.65,
        withdrawal_tendency=0.20,
    ))
    high_presence = mapper.map(_build_state(
        presence_stability=0.98,
        boundary_distance=0.65,
        withdrawal_tendency=0.20,
    ))

    assert high_presence.posture_stability > low_presence.posture_stability
    assert high_presence.body_part_offsets.head_offset_ms < low_presence.body_part_offsets.head_offset_ms
    assert high_presence.body_part_offsets.shoulder_offset_ms < low_presence.body_part_offsets.shoulder_offset_ms
    assert high_presence.body_part_offsets.hand_offset_ms < low_presence.body_part_offsets.hand_offset_ms

    assert high_presence.gaze_release_amplitude > 0.0
    assert high_presence.initial_delay_sec > 0.0
    assert high_presence.motion_speed > 0.0
    assert 0.20 < high_presence.motion_completion < 0.90


def test_11_high_affective_warmth_alone_does_not_create_uncapped_friendliness():
    params = FieldStateToMotionParamsMapper().map(_build_state(affective_warmth=1.0))

    assert params.expression_amplitude <= 0.25
    assert params.gaze_contact_sec <= 0.75
    assert params.torso_lean <= 0.05


def test_11b_expression_amplitude_stays_capped_under_contamination_service_and_boundary_pressure():
    mapper = FieldStateToMotionParamsMapper()
    warmth_only = mapper.map(_build_state(
        affective_warmth=1.0,
        contamination_pressure=0.0,
        service_resistance=0.0,
        boundary_distance=0.10,
        contamination_resistance=0.0,
    ))
    capped = mapper.map(_build_state(
        affective_warmth=1.0,
        contamination_pressure=0.80,
        service_resistance=0.90,
        boundary_distance=0.90,
        contamination_resistance=0.60,
    ))

    assert warmth_only.expression_amplitude > capped.expression_amplitude
    assert capped.expression_amplitude <= 0.14
    assert capped.behavior_affecting is False


def test_12_approach_tendency_can_exist_while_completion_remains_restrained():
    params = FieldStateToMotionParamsMapper().map(_build_state(
        affective_warmth=0.80,
        structural_grip_pressure=0.90,
        collaborator_layer_pressure=0.80,
        boundary_distance=0.80,
        contamination_resistance=0.80,
        service_resistance=0.75,
        correction_pressure=0.30,
    ))

    assert params.head_turn_amplitude > 0.0
    assert params.motion_speed > 0.0
    assert params.motion_completion < 0.80
    assert params.hard_constraints.no_approach_step is True


def test_13_body_part_offsets_preserve_gaze_head_shoulder_hand_order():
    offsets = FieldStateToMotionParamsMapper().map(create_ground_state()).body_part_offsets

    assert offsets.gaze_offset_ms <= offsets.head_offset_ms
    assert offsets.head_offset_ms <= offsets.shoulder_offset_ms
    assert offsets.shoulder_offset_ms <= offsets.hand_offset_ms

    with pytest.raises(ValueError, match="gaze <= head <= shoulder <= hand"):
        BodyPartOffsets(gaze_offset_ms=100, head_offset_ms=50, shoulder_offset_ms=120, hand_offset_ms=180)


def test_13b_high_service_and_contamination_resistance_stack_constraints_without_erasing_timing_or_gaze():
    params = FieldStateToMotionParamsMapper().map(_build_state(
        service_resistance=0.90,
        contamination_resistance=0.85,
    ))

    assert params.hard_constraints.no_service_gesture is True
    assert params.hard_constraints.no_welcoming_gesture is True
    assert params.hard_constraints.no_seductive_expression is True
    assert params.hard_constraints.no_cute_head_tilt is True

    assert params.initial_delay_sec > 0.0
    assert params.pause_after_sec > 0.0
    assert params.gaze_release_amplitude > 0.0
    assert params.gaze_contact_sec > 0.0
    assert params.head_turn_delay_sec > 0.0
    assert params.behavior_affecting is False


def test_14_all_numeric_outputs_are_bounded():
    variable_names = tuple(create_ground_state().variables)
    states = [
        create_ground_state(),
        _build_state(**{name: 0.0 for name in variable_names}),
        _build_state(**{name: 1.0 for name in variable_names}),
        _build_state(
            boundary_distance=1.0,
            affective_warmth=1.0,
            structural_grip_pressure=1.0,
            correction_pressure=1.0,
            contamination_resistance=1.0,
            presence_stability=0.0,
            withdrawal_tendency=1.0,
            service_resistance=1.0,
            collaborator_layer_pressure=1.0,
            contamination_pressure=1.0,
        ),
    ]

    mapper = FieldStateToMotionParamsMapper()
    for state in states:
        _assert_motion_params_bounded(mapper.map(state))


def test_15_behavior_affecting_remains_false_and_schema_is_frozen():
    params = FieldStateToMotionParamsMapper().map(create_ground_state())

    assert is_dataclass(params)
    assert params.behavior_affecting is False
    assert all(getattr(params.hard_constraints, name) in {True, False} for name in HARD_CONSTRAINT_FIELDS)
    with pytest.raises(FrozenInstanceError):
        params.motion_speed = 0.1
    with pytest.raises(ValueError, match="behavior_affecting"):
        MotionParams(behavior_affecting=True)
    with pytest.raises(ValueError, match="bool"):
        HardMotionConstraints(no_service_gesture=1)  # type: ignore[arg-type]


def test_16_no_body_action_policy_v1_is_implemented():
    assert not Path("src/body_action/policy_v1.py").exists()
    assert "policy_v1" not in _motion_source()


def test_17_no_body_action_composition_is_implemented_or_connected_here():
    source = _motion_source()

    assert "BodyActionComposition" not in source
    assert "BodyActionComposer" not in source
    assert "composition" not in source.lower()


def test_18_no_runtime_renderer_or_language_integration():
    source = _motion_source()
    forbidden = (
        "RuntimeEngine",
        "runtime_engine",
        "renderer",
        "render",
        "animation",
        "avatar",
        "language",
        "prompt",
        "llm",
    )

    for token in forbidden:
        assert token not in source

    params = FieldStateToMotionParamsMapper().map(create_ground_state())
    assert params.source_state_note == "F_0 relational field ground state"
    assert params.provenance == "RelationalFieldState.numeric_value -> MotionParams v0"
    assert "0." not in params.field_snapshot_note
    assert {field.name for field in fields(params)} == {
        "initial_delay_sec",
        "motion_speed",
        "pause_after_sec",
        "gaze_contact_sec",
        "gaze_release_amplitude",
        "head_turn_amplitude",
        "head_turn_delay_sec",
        "torso_lean",
        "posture_stability",
        "expression_amplitude",
        "motion_completion",
        "body_part_offsets",
        "hard_constraints",
        "source_state_note",
        "field_snapshot_note",
        "provenance",
        "behavior_affecting",
    }
