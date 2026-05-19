from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path

from src.motion_curve import MotionCurveGenerator
from src.motion_params.schema import BodyPartOffsets, HardMotionConstraints, MotionParams


GOLDEN_PATH = Path("monitor/body_action_composition_golden.jsonl")


def _mp(**overrides) -> MotionParams:
    values = {
        "initial_delay_sec": 0.0,
        "motion_speed": 0.50,
        "pause_after_sec": 0.0,
        "gaze_contact_sec": 0.30,
        "gaze_release_amplitude": 0.50,
        "head_turn_amplitude": 0.20,
        "head_turn_delay_sec": 0.0,
        "torso_lean": 0.10,
        "posture_stability": 0.60,
        "expression_amplitude": 0.10,
        "motion_completion": 0.70,
        "body_part_offsets": BodyPartOffsets(),
        "hard_constraints": HardMotionConstraints(),
        "behavior_affecting": False,
    }
    values.update(overrides)
    return MotionParams(**values)


def _records() -> list[dict]:
    return [
        json.loads(line)
        for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _motion_params_from_record(record: dict) -> MotionParams:
    payload = dict(record["motion_params"])
    offsets = BodyPartOffsets(**payload.pop("body_part_offsets"))
    constraints = HardMotionConstraints(**payload.pop("hard_constraints"))
    return MotionParams(
        **payload,
        body_part_offsets=offsets,
        hard_constraints=constraints,
    )


def _curve_for(record: dict):
    return MotionCurveGenerator().generate(
        _motion_params_from_record(record),
        scenario_name=record["scenario_id"],
        scenario_intent=record["source_alignment_note"],
    )


def _first_nonzero_time(points) -> float:
    for point in points:
        if point.amplitude > 0.0:
            return point.time_sec
    return 5.0


def test_gaze_curve_has_contact_then_release_shape():
    record = next(item for item in _records() if item["scenario_id"] == "brief_contact_then_release")
    curve = _curve_for(record)
    first_contact = next(point.amplitude for point in curve.gaze_curve if point.amplitude > 0.0)
    final_release = curve.gaze_curve[-1].amplitude

    assert first_contact > final_release


def test_body_part_offsets_create_delayed_channels():
    curve = MotionCurveGenerator().generate(
        _mp(
            body_part_offsets=BodyPartOffsets(
                gaze_offset_ms=0,
                head_offset_ms=600,
                shoulder_offset_ms=600,
                hand_offset_ms=600,
            ),
            gaze_contact_sec=0.0,
            head_turn_delay_sec=0.0,
            head_turn_amplitude=0.40,
            torso_lean=0.20,
        ),
        scenario_name="offset_test",
    )

    assert _first_nonzero_time(curve.gaze_curve) < _first_nonzero_time(curve.head_curve)
    assert _first_nonzero_time(curve.head_curve) < _first_nonzero_time(curve.torso_curve)


def test_motion_completion_scales_torso_amplitude():
    curve = MotionCurveGenerator().generate(
        _mp(torso_lean=0.40, motion_completion=0.25),
        scenario_name="completion_scale_test",
    )
    torso_max = max(point.amplitude for point in curve.torso_curve)

    assert torso_max < 0.40 / 0.50


def test_posture_stability_affects_oscillation():
    curve = MotionCurveGenerator().generate(
        _mp(posture_stability=0.20),
        scenario_name="posture_oscillation_test",
    )
    diffs = [
        current.amplitude - previous.amplitude
        for previous, current in zip(curve.posture_curve, curve.posture_curve[1:])
    ]

    assert any(diff > 0.0 for diff in diffs)
    assert any(diff < 0.0 for diff in diffs)


def test_all_curve_amplitudes_bounded():
    curve = MotionCurveGenerator().generate(
        MotionParams(
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
            body_part_offsets=BodyPartOffsets(
                gaze_offset_ms=0,
                head_offset_ms=600,
                shoulder_offset_ms=600,
                hand_offset_ms=600,
            ),
            hard_constraints=HardMotionConstraints(),
            behavior_affecting=False,
        ),
        scenario_name="bounded_test",
    )

    for points in curve.curves_by_channel().values():
        for point in points:
            assert 0.0 <= point.amplitude <= 1.0


def test_all_scenarios_produce_distinct_curves():
    records = _records()
    curves = {
        record["scenario_id"]: _curve_for(record)
        for record in records
    }
    signatures = {
        scenario_id: tuple(
            tuple(point.amplitude for point in points)
            for points in curve.curves_by_channel().values()
        )
        for scenario_id, curve in curves.items()
    }

    assert len(signatures) == 7
    assert len(set(signatures.values())) == 7

    high_boundary_ids = [record["scenario_id"] for record in records[2:6]]
    for left, right in combinations(high_boundary_ids, 2):
        left_channels = curves[left].curves_by_channel()
        right_channels = curves[right].curves_by_channel()
        differing_channels = 0
        for channel in ("gaze", "head", "torso", "expression", "posture"):
            cumulative_delta = sum(
                abs(left_point.amplitude - right_point.amplitude)
                for left_point, right_point in zip(left_channels[channel], right_channels[channel])
            )
            if cumulative_delta > 0.15:
                differing_channels += 1
        assert differing_channels >= 2
