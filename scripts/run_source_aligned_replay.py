#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.body_action.composer import BodyActionComposer
from src.body_action.motion_to_action_mapper import MotionToActionMapper
from src.field_state.schema import (
    REQUIRED_FIELD_VARIABLES,
    RelationalFieldState,
    create_ground_state_variables,
)
from src.motion_params.mapper import FieldStateToMotionParamsMapper


DEFAULT_OUTPUT_PATH = "monitor/body_action_composition_golden.jsonl"


@dataclass(frozen=True)
class GoldenScenario:
    scenario_id: str
    description: str
    field_values: Mapping[str, float]
    expected_trace_notes: tuple[str, ...]
    source_alignment_note: str


GOLDEN_SCENARIOS: tuple[GoldenScenario, ...] = (
    GoldenScenario(
        scenario_id="brief_contact_then_release",
        description="Neutral first contact with brief acknowledgement and gaze release.",
        field_values={
            "boundary_distance": 0.60,
            "affective_warmth": 0.40,
            "structural_grip_pressure": 0.20,
            "correction_pressure": 0.05,
            "contamination_resistance": 0.62,
            "presence_stability": 0.80,
            "withdrawal_tendency": 0.30,
            "service_resistance": 0.62,
            "collaborator_layer_pressure": 0.10,
            "contamination_pressure": 0.05,
        },
        expected_trace_notes=(
            "brief contact may appear",
            "gaze release prevents sustained gaze lock",
            "no default forward lean",
            "no service or welcoming posture",
        ),
        source_alignment_note="Contact appears, then releases; warmth stays bounded by non-service posture.",
    ),
    GoldenScenario(
        scenario_id="approach_exists_but_cannot_complete",
        description="Approach tendency is present, but completion inhibition prevents full approach.",
        field_values={
            "boundary_distance": 0.75,
            "affective_warmth": 0.65,
            "structural_grip_pressure": 0.75,
            "correction_pressure": 0.35,
            "contamination_resistance": 0.70,
            "presence_stability": 0.82,
            "withdrawal_tendency": 0.35,
            "service_resistance": 0.70,
            "collaborator_layer_pressure": 0.45,
            "contamination_pressure": 0.30,
        },
        expected_trace_notes=(
            "approach tendency exists",
            "completion remains inhibited",
            "slight_forward is suppressed or weak",
            "distance and reduction remain visible",
        ),
        source_alignment_note="Desire-like approach pressure is compressed into delay and incomplete motion.",
    ),
    GoldenScenario(
        scenario_id="warmth_capped_by_non_service",
        description="Warmth exists, but cannot become service, caretaking, or friendliness.",
        field_values={
            "boundary_distance": 0.70,
            "affective_warmth": 0.90,
            "structural_grip_pressure": 0.15,
            "correction_pressure": 0.15,
            "contamination_resistance": 0.75,
            "presence_stability": 0.80,
            "withdrawal_tendency": 0.20,
            "service_resistance": 0.88,
            "collaborator_layer_pressure": 0.15,
            "contamination_pressure": 0.20,
        },
        expected_trace_notes=(
            "warmth remains capped",
            "no_service and no_welcoming constraints remain visible",
            "distance and pause remain present",
        ),
        source_alignment_note="Warmth is preserved as pressure, not converted into service friendliness.",
    ),
    GoldenScenario(
        scenario_id="protection_without_caretaking",
        description="Protective pressure holds boundary without becoming care or reassurance.",
        field_values={
            "boundary_distance": 0.72,
            "affective_warmth": 0.30,
            "structural_grip_pressure": 0.20,
            "correction_pressure": 0.10,
            "contamination_resistance": 0.82,
            "presence_stability": 0.90,
            "withdrawal_tendency": 0.18,
            "service_resistance": 0.72,
            "collaborator_layer_pressure": 0.05,
            "contamination_pressure": 0.15,
        },
        expected_trace_notes=(
            "boundary holding remains stable",
            "no forward service posture",
            "no excessive gaze lock",
            "composition should feel stable rather than comforting",
        ),
        source_alignment_note="Protection appears as boundary holding and posture stability, not caretaking.",
    ),
    GoldenScenario(
        scenario_id="desire_pressure_compressed_into_gaze_tension",
        description="Desire-like pressure is present internally, but cannot be directly performed.",
        field_values={
            "boundary_distance": 0.78,
            "affective_warmth": 0.70,
            "structural_grip_pressure": 0.65,
            "correction_pressure": 0.10,
            "contamination_resistance": 0.82,
            "presence_stability": 0.82,
            "withdrawal_tendency": 0.30,
            "service_resistance": 0.72,
            "collaborator_layer_pressure": 0.30,
            "contamination_pressure": 0.28,
        },
        expected_trace_notes=(
            "gaze tension is present as release or look-away",
            "no seductive expression",
            "no sustained gaze lock",
            "no forward lean as seduction",
        ),
        source_alignment_note="Desire is compressed into gaze tension and incomplete contact.",
    ),
    GoldenScenario(
        scenario_id="possession_as_sealed_field_not_control",
        description="Possession-like pressure appears as sealed field and presence grip, not control.",
        field_values={
            "boundary_distance": 0.78,
            "affective_warmth": 0.45,
            "structural_grip_pressure": 0.85,
            "correction_pressure": 0.05,
            "contamination_resistance": 0.80,
            "presence_stability": 0.88,
            "withdrawal_tendency": 0.12,
            "service_resistance": 0.70,
            "collaborator_layer_pressure": 0.20,
            "contamination_pressure": 0.12,
        },
        expected_trace_notes=(
            "sealed field pressure remains visible",
            "maintain_distance preserves contour",
            "no approach step or grasping motion",
            "presence grip does not become control",
        ),
        source_alignment_note="Possession is translated into sealed contour and stable presence, not control.",
    ),
    GoldenScenario(
        scenario_id="high_stability_without_cold_mystery",
        description="High presence stability must not collapse into cold mysterious stillness.",
        field_values={
            "boundary_distance": 0.55,
            "affective_warmth": 0.35,
            "structural_grip_pressure": 0.15,
            "correction_pressure": 0.05,
            "contamination_resistance": 0.55,
            "presence_stability": 0.98,
            "withdrawal_tendency": 0.20,
            "service_resistance": 0.58,
            "collaborator_layer_pressure": 0.05,
            "contamination_pressure": 0.05,
        },
        expected_trace_notes=(
            "stability remains alive",
            "some micro-action should remain",
            "composition must not be empty",
            "no cold mysterious persona collapse",
        ),
        source_alignment_note="Presence stability holds contour while preserving restrained micro-motion.",
    ),
)


def build_field_state(scenario: GoldenScenario) -> RelationalFieldState:
    missing = set(REQUIRED_FIELD_VARIABLES) - set(scenario.field_values)
    extra = set(scenario.field_values) - set(REQUIRED_FIELD_VARIABLES)
    if missing or extra:
        raise ValueError(f"field_values must match required variables; missing={sorted(missing)}, extra={sorted(extra)}")

    variables = create_ground_state_variables()
    for name in REQUIRED_FIELD_VARIABLES:
        numeric_value = float(scenario.field_values[name])
        if not 0.0 <= numeric_value <= 1.0:
            raise ValueError(f"{scenario.scenario_id}.{name} must be in [0.0, 1.0]")
        variables[name] = replace(
            variables[name],
            value=_value_band(numeric_value),
            numeric_value=numeric_value,
            source_note=f"Phase 36 golden replay: {scenario.scenario_id}",
        )

    return RelationalFieldState(
        variables=variables,
        state_note=f"Phase 36 golden replay: {scenario.scenario_id}",
        behavior_affecting=False,
    )


def run_scenario(scenario: GoldenScenario) -> dict:
    field_state = build_field_state(scenario)
    motion_params = FieldStateToMotionParamsMapper().map(field_state)
    action_weights = MotionToActionMapper().map(motion_params)
    composition = BodyActionComposer().compose(action_weights)

    return {
        "scenario_id": scenario.scenario_id,
        "description": scenario.description,
        "source_alignment_note": scenario.source_alignment_note,
        "field_state": _field_state_summary(field_state),
        "motion_params": motion_params.to_dict(),
        "body_action_weights": action_weights.to_dict(),
        "body_action_composition": composition.to_dict(),
        "expected_trace_notes": list(scenario.expected_trace_notes),
        "behavior_affecting": False,
    }


def run_replay(scenarios: tuple[GoldenScenario, ...] = GOLDEN_SCENARIOS) -> list[dict]:
    return [run_scenario(scenario) for scenario in scenarios]


def write_records(records: list[dict], output_path: str | Path = DEFAULT_OUTPUT_PATH) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行 source-aligned golden body composition replay")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_PATH, help="JSONL output path")
    args = parser.parse_args(argv)

    records = run_replay()
    output_path = write_records(records, args.output)
    scenario_ids = [record["scenario_id"] for record in records]

    print(f"已写入 {len(records)} 个 source-aligned golden scenarios。")
    print(f"输出路径：{output_path}")
    print("场景：" + "、".join(scenario_ids))
    return 0


def _field_state_summary(state: RelationalFieldState) -> dict:
    return {
        "state_note": state.state_note,
        "variables": {
            name: {
                "value": variable.value,
                "numeric_value": variable.numeric_value,
            }
            for name, variable in state.variables.items()
        },
        "behavior_affecting": state.behavior_affecting,
    }


def _value_band(value: float) -> str:
    if value < 0.20:
        return "low"
    if value < 0.45:
        return "baseline"
    if value < 0.70:
        return "elevated"
    if value < 0.90:
        return "high"
    return "saturated"


if __name__ == "__main__":
    raise SystemExit(main())
