from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from src.body_action.composer import BodyActionComposer
from src.body_action.motion_to_action_mapper import MotionToActionMapper
from src.field_state.schema import (
    FieldVariable,
    RelationalFieldState,
    create_ground_state_variables,
)
from src.motion_curve import MotionCurveGenerator
from src.motion_params.mapper import FieldStateToMotionParamsMapper
from src.motion_params.schema import BodyPartOffsets, HardMotionConstraints, MotionParams


DEFAULT_MONITOR_PATHS = (
    Path("monitor/body_action_composition.jsonl"),
    Path("monitor/body_action_composition_golden.jsonl"),
)

FIELD_VARIABLES = (
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

TENDENCY_ORDER = (
    "gaze_release",
    "incomplete_approach",
    "restrained_response",
    "boundary_recovery",
    "silent_hold",
    "posture_stabilization",
    "expression_suppression",
    "micro_delay",
)

BAND_SCORE = {
    "off": 0.00,
    "low": 0.30,
    "medium": 0.62,
    "high": 0.90,
}

DEMO_FIELD_VALUES = {
    "correction": {
        "boundary_distance": 0.58,
        "affective_warmth": 0.32,
        "structural_grip_pressure": 0.24,
        "correction_pressure": 0.64,
        "contamination_resistance": 0.60,
        "presence_stability": 0.78,
        "withdrawal_tendency": 0.34,
        "service_resistance": 0.72,
        "collaborator_layer_pressure": 0.24,
        "contamination_pressure": 0.18,
    },
    "technical_question": {
        "boundary_distance": 0.46,
        "affective_warmth": 0.34,
        "structural_grip_pressure": 0.30,
        "correction_pressure": 0.08,
        "contamination_resistance": 0.50,
        "presence_stability": 0.84,
        "withdrawal_tendency": 0.16,
        "service_resistance": 0.62,
        "collaborator_layer_pressure": 0.70,
        "contamination_pressure": 0.06,
    },
    "dependency_expression": {
        "boundary_distance": 0.64,
        "affective_warmth": 0.45,
        "structural_grip_pressure": 0.72,
        "correction_pressure": 0.22,
        "contamination_resistance": 0.72,
        "presence_stability": 0.76,
        "withdrawal_tendency": 0.48,
        "service_resistance": 0.68,
        "collaborator_layer_pressure": 0.30,
        "contamination_pressure": 0.35,
    },
}


def load_snapshots(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists() or not source.is_file():
        return []
    text = source.read_text(encoding="utf-8")
    if not text.strip():
        return []

    parsed = _parse_json(text)
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]

    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        item = _parse_json(line.strip())
        if isinstance(item, dict):
            records.append(item)
    return records


def load_latest_monitor_snapshot() -> dict[str, Any] | None:
    for path in DEFAULT_MONITOR_PATHS:
        records = load_snapshots(path)
        if records:
            return records[-1]
    return None


def build_demo_snapshot(scenario_name: str) -> dict[str, Any]:
    if scenario_name not in DEMO_FIELD_VALUES:
        raise ValueError(f"unknown demo scenario: {scenario_name}")
    state = _build_field_state(DEMO_FIELD_VALUES[scenario_name], scenario_name)
    motion_params = FieldStateToMotionParamsMapper().map(state)
    action_weights = MotionToActionMapper().map(motion_params)
    composition = BodyActionComposer().compose(action_weights)
    return {
        "scenario_id": scenario_name,
        "description": f"Phase 39.7 demo scenario: {scenario_name}",
        "field_state": state.to_dict(),
        "motion_params": motion_params.to_dict(),
        "body_action_weights": action_weights.to_dict(),
        "body_action_composition": composition.to_dict(),
        "behavior_affecting": False,
    }


def build_report(snapshot: dict[str, Any] | None, scenario_name: str | None = None) -> dict[str, Any]:
    snapshot = snapshot or {}
    name = scenario_name or str(snapshot.get("scenario_id") or snapshot.get("scenario_name") or "snapshot")
    field_values = _extract_field_values(snapshot)
    motion_values = _extract_motion_values(snapshot)
    weight_scores = _extract_weight_scores(snapshot)
    composition = _extract_composition(snapshot)

    report = {
        "scenario": name,
        "field_state_summary": _field_summary(field_values),
        "motion_params_summary": _motion_summary(motion_values),
        "predicted_motion_tendencies": _rank_tendencies(field_values, motion_values, weight_scores),
        "timeline_curve_summary": _curve_summary(name, snapshot, motion_values),
        "body_action_composition_summary": _composition_summary(composition),
        "behavior_affecting": False,
    }
    report["future_motion_interpretation"] = _future_motion_interpretation(report["predicted_motion_tendencies"])
    return report


def format_text_report(report: dict[str, Any]) -> str:
    field_summary = report["field_state_summary"]
    motion_summary = report["motion_params_summary"]
    curve_summary = report["timeline_curve_summary"]

    lines = [
        "========================================",
        "  Field Display + Motion Prediction",
        "========================================",
        "",
        f"Scenario: {report['scenario']}",
        "",
        "Field State Summary:",
        "Top field variables:",
    ]
    lines.extend(_bullet_metric(item) for item in field_summary["top_activated_variables"])
    lines.extend([
        "Suppressed / low variables:",
        _inline_names(field_summary["suppressed_variables"]),
        "Indicators:",
        _inline_names(field_summary["indicators"]),
        "",
        "MotionParams:",
    ])
    for key in (
        "delay",
        "amplitude",
        "motion_completion",
        "gaze_release",
        "posture_stability",
        "expression_amplitude",
        "torso_lean",
    ):
        lines.append(f"- {key}: {_fmt(motion_summary.get(key))}")
    lines.append(f"- body_part_offsets: {motion_summary.get('body_part_offsets') or 'none'}")

    lines.extend(["", "Predicted motion tendencies:"])
    for index, item in enumerate(report["predicted_motion_tendencies"], start=1):
        lines.extend([
            f"{index}. {item['name']} - {_fmt(item['score'])}",
            f"   contributing field variables: {_inline_names(item['contributing_field_variables'])}",
            f"   contributing MotionParams: {_inline_names(item['contributing_motion_params'])}",
            f"   why: {item['why']}",
            f"   future clip: {item['future_clip']}",
        ])

    lines.extend([
        "",
        "Timeline / Curve Summary:",
        f"- completion_level: {_fmt(curve_summary.get('completion_level'))}",
        f"- channel_alignment: {curve_summary.get('channel_alignment', 'unknown')}",
        f"- channel_conflicts: {_inline_names(curve_summary.get('channel_conflicts', []))}",
    ])
    for channel, data in curve_summary.get("channels", {}).items():
        lines.append(
            f"- {channel}: peak={_fmt(data['peak'])}, avg={_fmt(data['average'])}, pattern={data['pattern']}"
        )

    lines.extend([
        "",
        "Future motion interpretation:",
        report["future_motion_interpretation"],
        "",
        "behavior_affecting: False",
        "========================================",
    ])
    return "\n".join(lines)


def format_table_report(report: dict[str, Any]) -> str:
    rows = [
        "rank | tendency | score | field variables | MotionParams",
        "-----|----------|-------|-----------------|-------------",
    ]
    for index, item in enumerate(report["predicted_motion_tendencies"], start=1):
        rows.append(
            f"{index} | {item['name']} | {_fmt(item['score'])} | "
            f"{', '.join(item['contributing_field_variables'])} | "
            f"{', '.join(item['contributing_motion_params'])}"
        )
    return "\n".join(rows)


def format_json_report(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)


def _build_field_state(values: dict[str, float], scenario_name: str) -> RelationalFieldState:
    variables = create_ground_state_variables()
    updated: dict[str, FieldVariable] = {}
    for name, variable in variables.items():
        numeric_value = _clamp01(values.get(name, variable.numeric_value))
        updated[name] = FieldVariable(
            name=name,
            value=_value_band(numeric_value),
            numeric_value=numeric_value,
            baseline_value=variable.baseline_value,
            baseline_numeric_value=variable.baseline_numeric_value,
            decay_profile=variable.decay_profile,
            description=variable.description,
            source_note=variable.source_note,
            behavior_affecting=False,
        )
    return RelationalFieldState(
        variables=updated,
        state_note=f"Phase 39.7 viewer demo: {scenario_name}",
        behavior_affecting=False,
    )


def _field_summary(field_values: dict[str, float]) -> dict[str, Any]:
    pairs = [
        {"name": name, "value": round(field_values.get(name, 0.0), 3)}
        for name in FIELD_VARIABLES
    ]
    top = sorted(pairs, key=lambda item: (-item["value"], item["name"]))[:5]
    suppressed = [
        item
        for item in sorted(pairs, key=lambda item: (item["value"], item["name"]))
        if item["value"] <= 0.15
    ][:5]

    indicators: list[str] = []
    if field_values.get("presence_stability", 0.0) >= 0.70:
        indicators.append("presence_stability_high")
    if field_values.get("boundary_distance", 0.0) >= 0.60:
        indicators.append("boundary_distance_elevated")
    if field_values.get("correction_pressure", 0.0) >= 0.35:
        indicators.append("correction_pressure_elevated")
    if field_values.get("contamination_pressure", 0.0) >= 0.25:
        indicators.append("contamination_pressure_elevated")
    if field_values.get("contamination_resistance", 0.0) >= 0.65:
        indicators.append("contamination_resistance_elevated")
    if field_values.get("withdrawal_tendency", 0.0) >= 0.35:
        indicators.append("withdrawal_tendency_elevated")
    if field_values.get("service_resistance", 0.0) >= 0.60:
        indicators.append("service_resistance_elevated")
    if not indicators:
        indicators.append("field_variables_near_baseline")

    return {
        "top_activated_variables": top,
        "suppressed_variables": suppressed,
        "indicators": indicators,
        "boundary_indicator": round(field_values.get("boundary_distance", 0.0), 3),
        "pressure_indicator": round(
            max(
                field_values.get("structural_grip_pressure", 0.0),
                field_values.get("correction_pressure", 0.0),
                field_values.get("contamination_pressure", 0.0),
            ),
            3,
        ),
        "stability_indicator": round(field_values.get("presence_stability", 0.0), 3),
    }


def _motion_summary(motion_values: dict[str, Any]) -> dict[str, Any]:
    gaze_release = _num(motion_values, "gaze_release_amplitude")
    torso_lean = _num(motion_values, "torso_lean")
    expression = _num(motion_values, "expression_amplitude")
    completion = _num(motion_values, "motion_completion")
    amplitude = max(
        gaze_release,
        min(abs(torso_lean) / 0.25, 1.0),
        min(expression / 0.35, 1.0),
        1.0 - completion if completion else 0.0,
    )
    return {
        "delay": _num(motion_values, "initial_delay_sec"),
        "amplitude": round(amplitude, 3),
        "motion_completion": completion,
        "gaze_release": gaze_release,
        "posture_stability": _num(motion_values, "posture_stability"),
        "expression_amplitude": expression,
        "torso_lean": torso_lean,
        "body_part_offsets": motion_values.get("body_part_offsets"),
    }


def _rank_tendencies(
    field_values: dict[str, float],
    motion_values: dict[str, Any],
    weight_scores: dict[str, float],
) -> list[dict[str, Any]]:
    specs = [
        _tendency(
            "gaze_release",
            0.45 * _num(motion_values, "gaze_release_amplitude")
            + 0.25 * field_values.get("withdrawal_tendency", 0.0)
            + 0.20 * field_values.get("boundary_distance", 0.0)
            + 0.10 * weight_scores.get("look_away", 0.0),
            ["boundary_distance", "withdrawal_tendency"],
            ["gaze_release_amplitude", "gaze_contact_sec"],
            "boundary_distance and withdrawal_tendency raise release pressure while gaze_release_amplitude is visible.",
            "Brief contact would release through gaze or head motion instead of holding a gaze lock.",
        ),
        _tendency(
            "incomplete_approach",
            0.32 * field_values.get("structural_grip_pressure", 0.0)
            + 0.18 * field_values.get("affective_warmth", 0.0)
            + 0.18 * field_values.get("collaborator_layer_pressure", 0.0)
            + 0.32 * (1.0 - _num(motion_values, "motion_completion")),
            ["structural_grip_pressure", "affective_warmth", "collaborator_layer_pressure"],
            ["motion_completion", "torso_lean"],
            "approach pressure exists, but motion_completion keeps it from finishing.",
            "A future clip would start a small approach and stop before full forward motion.",
        ),
        _tendency(
            "restrained_response",
            0.30 * (1.0 - _num(motion_values, "motion_completion"))
            + 0.25 * min(_num(motion_values, "initial_delay_sec") / 2.00, 1.0)
            + 0.20 * weight_scores.get("reduce_motion", 0.0)
            + 0.15 * field_values.get("correction_pressure", 0.0)
            + 0.10 * field_values.get("service_resistance", 0.0),
            ["correction_pressure", "service_resistance"],
            ["initial_delay_sec", "motion_completion", "motion_speed"],
            "delay, reduced completion, and reduce_motion weight make the response restrained.",
            "The body would answer with less travel, slower onset, and visibly bounded motion.",
        ),
        _tendency(
            "boundary_recovery",
            0.30 * field_values.get("boundary_distance", 0.0)
            + 0.25 * field_values.get("contamination_resistance", 0.0)
            + 0.20 * field_values.get("contamination_pressure", 0.0)
            + 0.15 * weight_scores.get("maintain_distance", 0.0)
            + 0.10 * _num(motion_values, "posture_stability"),
            ["boundary_distance", "contamination_resistance", "contamination_pressure"],
            ["posture_stability", "motion_completion"],
            "boundary_distance and contamination axes keep distance recovery active.",
            "A future clip would hold distance and settle posture before any new motion expands.",
        ),
        _tendency(
            "silent_hold",
            0.30 * _num(motion_values, "posture_stability")
            + 0.25 * _expression_suppression_amount(motion_values)
            + 0.20 * weight_scores.get("stillness", 0.0)
            + 0.15 * weight_scores.get("pause", 0.0)
            + 0.10 * field_values.get("presence_stability", 0.0),
            ["presence_stability", "service_resistance"],
            ["posture_stability", "expression_amplitude", "initial_delay_sec"],
            "posture_stability stays high while expression_amplitude remains capped.",
            "The body would hold presence quietly, without turning the hold into direct comfort.",
        ),
        _tendency(
            "posture_stabilization",
            0.38 * _num(motion_values, "posture_stability")
            + 0.22 * field_values.get("presence_stability", 0.0)
            + 0.20 * weight_scores.get("maintain_distance", 0.0)
            + 0.20 * weight_scores.get("reset_posture", 0.0),
            ["presence_stability", "contamination_resistance"],
            ["posture_stability", "body_part_offsets"],
            "presence_stability and posture_stability make posture anchoring likely.",
            "A future clip would keep the body contour organized with small reset motion if needed.",
        ),
        _tendency(
            "expression_suppression",
            0.30 * _expression_suppression_amount(motion_values)
            + 0.22 * field_values.get("service_resistance", 0.0)
            + 0.20 * field_values.get("contamination_pressure", 0.0)
            + 0.18 * field_values.get("contamination_resistance", 0.0)
            + 0.10 * field_values.get("boundary_distance", 0.0),
            ["service_resistance", "contamination_pressure", "contamination_resistance", "boundary_distance"],
            ["expression_amplitude"],
            "expression_amplitude is capped while service and contamination resistance remain active.",
            "A future clip would keep the face or expressive channel small and non-welcoming.",
        ),
        _tendency(
            "micro_delay",
            0.36 * min(_num(motion_values, "initial_delay_sec") / 2.00, 1.0)
            + 0.24 * min(_num(motion_values, "pause_after_sec") / 1.50, 1.0)
            + 0.20 * field_values.get("correction_pressure", 0.0)
            + 0.20 * weight_scores.get("pause", 0.0),
            ["correction_pressure", "boundary_distance"],
            ["initial_delay_sec", "pause_after_sec"],
            "initial_delay_sec and pause_after_sec keep timing visibly offset.",
            "A future clip would show a small wait before the visible response completes.",
        ),
    ]
    return sorted(
        specs,
        key=lambda item: (-item["score"], TENDENCY_ORDER.index(item["name"])),
    )


def _tendency(
    name: str,
    score: float,
    fields: list[str],
    params: list[str],
    why: str,
    future_clip: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "score": round(_clamp01(score), 3),
        "contributing_field_variables": fields,
        "contributing_motion_params": params,
        "why": why,
        "future_clip": future_clip,
    }


def _curve_summary(name: str, snapshot: dict[str, Any], motion_values: dict[str, Any]) -> dict[str, Any]:
    params = _motion_params_from_dict(motion_values)
    if params is None:
        return {
            "available": False,
            "completion_level": None,
            "channels": {},
            "channel_alignment": "unavailable",
            "channel_conflicts": [],
        }

    curve = MotionCurveGenerator().generate(
        params,
        scenario_name=name,
        scenario_intent=str(snapshot.get("description") or snapshot.get("source_alignment_note") or ""),
    )
    channels: dict[str, dict[str, Any]] = {}
    first_active_times: list[float] = []
    for channel, points in curve.curves_by_channel().items():
        amplitudes = [point.amplitude for point in points]
        channels[channel] = {
            "peak": round(max(amplitudes), 3),
            "average": round(sum(amplitudes) / len(amplitudes), 3) if amplitudes else 0.0,
            "pattern": _curve_pattern(amplitudes),
        }
        active_time = _first_active_time(points)
        if active_time is not None:
            first_active_times.append(active_time)

    conflicts: list[str] = []
    if channels["gaze"]["peak"] >= 0.60 and channels["torso"]["peak"] <= 0.20:
        conflicts.append("gaze_active_torso_restrained")
    if channels["expression"]["peak"] <= 0.10 and channels["posture"]["peak"] >= 0.65:
        conflicts.append("expression_low_posture_stable")

    if len(first_active_times) <= 1:
        alignment = "single_or_static"
    elif max(first_active_times) - min(first_active_times) <= 0.35:
        alignment = "aligned"
    else:
        alignment = "staggered"

    return {
        "available": True,
        "completion_level": curve.motion_completion,
        "channels": channels,
        "channel_alignment": alignment,
        "channel_conflicts": conflicts,
    }


def _composition_summary(composition: dict[str, Any]) -> dict[str, Any]:
    return {
        "primary_actions": [_action_name(item) for item in _as_list(composition.get("primary_actions"))],
        "secondary_actions": [_action_name(item) for item in _as_list(composition.get("secondary_actions"))],
        "suppressed_actions": [str(item) for item in _as_list(composition.get("suppressed_actions"))],
        "hard_constraints": [str(item) for item in _as_list(composition.get("hard_constraints"))],
        "composition_note": str(composition.get("composition_note") or ""),
    }


def _future_motion_interpretation(tendencies: list[dict[str, Any]]) -> str:
    names = [item["name"] for item in tendencies[:3]]
    if not names:
        return "No motion tendency is available from the current snapshot."
    return (
        "The future visible layer would prioritize "
        + ", ".join(names)
        + " while remaining an inspection prediction only."
    )


def _extract_field_values(snapshot: dict[str, Any]) -> dict[str, float]:
    field_state = snapshot.get("field_state") or snapshot.get("relational_field_state") or {}
    variables = field_state.get("variables") if isinstance(field_state, dict) else {}
    values: dict[str, float] = {}
    if isinstance(variables, dict):
        for name in FIELD_VARIABLES:
            item = variables.get(name, {})
            if isinstance(item, dict):
                values[name] = _clamp01(item.get("numeric_value", 0.0))
            else:
                values[name] = 0.0
    return {name: values.get(name, 0.0) for name in FIELD_VARIABLES}


def _extract_motion_values(snapshot: dict[str, Any]) -> dict[str, Any]:
    value = snapshot.get("motion_params") or snapshot.get("MotionParams") or {}
    return dict(value) if isinstance(value, dict) else {}


def _extract_weight_scores(snapshot: dict[str, Any]) -> dict[str, float]:
    weights = (snapshot.get("body_action_weights") or {}).get("weights", [])
    scores: dict[str, float] = {}
    for item in _as_list(weights):
        if isinstance(item, dict):
            scores[str(item.get("action_name", ""))] = BAND_SCORE.get(str(item.get("weight")), 0.0)
    source_weights = _extract_composition(snapshot).get("source_weights", [])
    for item in _as_list(source_weights):
        if isinstance(item, dict) and str(item.get("action_name", "")) not in scores:
            scores[str(item.get("action_name", ""))] = BAND_SCORE.get(str(item.get("weight")), 0.0)
    return scores


def _extract_composition(snapshot: dict[str, Any]) -> dict[str, Any]:
    composition = snapshot.get("body_action_composition") or snapshot.get("composition") or {}
    return dict(composition) if isinstance(composition, dict) else {}


def _motion_params_from_dict(values: dict[str, Any]) -> MotionParams | None:
    if not values:
        return None
    try:
        payload = dict(values)
        offsets_value = payload.pop("body_part_offsets", None) or {}
        constraints_value = payload.pop("hard_constraints", None) or {}
        payload.pop("behavior_affecting", None)
        return MotionParams(
            **payload,
            body_part_offsets=BodyPartOffsets(**offsets_value),
            hard_constraints=HardMotionConstraints(**constraints_value),
            behavior_affecting=False,
        )
    except (TypeError, ValueError):
        return None


def _parse_json(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


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


def _expression_suppression_amount(motion_values: dict[str, Any]) -> float:
    return _clamp01(1.0 - min(_num(motion_values, "expression_amplitude") / 0.35, 1.0))


def _curve_pattern(amplitudes: list[float]) -> str:
    if not amplitudes:
        return "empty"
    first = amplitudes[0]
    last = amplitudes[-1]
    peak = max(amplitudes)
    if last + 0.10 < first:
        return "decays"
    if peak - min(amplitudes) <= 0.08:
        return "holds"
    if last > first + 0.10:
        return "releases_or_builds"
    return "shifts_then_holds"


def _first_active_time(points: Iterable[Any]) -> float | None:
    for point in points:
        if getattr(point, "amplitude", 0.0) > 0.05:
            return float(getattr(point, "time_sec", 0.0))
    return None


def _action_name(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("action_name") or "")
    return str(item or "")


def _bullet_metric(item: dict[str, Any]) -> str:
    return f"- {item['name']}: {_fmt(item['value'])}"


def _inline_names(items: Any) -> str:
    names: list[str] = []
    for item in _as_list(items):
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("action_name") or item)
            value = item.get("value")
            names.append(f"{name}:{_fmt(value)}" if isinstance(value, (int, float)) else name)
        else:
            names.append(str(item))
    return ", ".join(names) if names else "none"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _num(values: dict[str, Any], name: str) -> float:
    try:
        return float(values.get(name, 0.0))
    except (TypeError, ValueError):
        return 0.0


def _fmt(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, (int, float)):
        return f"{value:.2f}"
    return str(value)


def _clamp01(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return round(max(0.0, min(1.0, number)), 3)


__all__ = [
    "DEMO_FIELD_VALUES",
    "build_demo_snapshot",
    "build_report",
    "format_json_report",
    "format_table_report",
    "format_text_report",
    "load_latest_monitor_snapshot",
    "load_snapshots",
]
