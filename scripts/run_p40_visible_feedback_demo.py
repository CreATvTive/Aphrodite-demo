#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentlib.ds_client import DSClient  # noqa: E402
from src.llm_gate.judgment_gate import JudgmentGate  # noqa: E402
from src.llm_gate.proposal_generator import LLMProposalGenerator  # noqa: E402
from src.llm_gate.proposal_schema import ContextPackage  # noqa: E402
from src.viewers.field_motion_prediction import build_report  # noqa: E402


OUTPUT_PATH = Path("monitor/p40_visible_feedback_demo.jsonl")

PROJECT_FRAME = (
    "Aphrodite P40 visible feedback demo. The system is proposal-only, "
    "shadow-only, and non-behavior-affecting, but must produce visible "
    "field/motion feedback."
)
RECENT_TOPIC = (
    "The user is trying to move from invisible engineering progress to "
    "observable field and motion feedback."
)
RELEVANT_PRIOR_CONTEXT = (
    "Completed components include ForceEvent Adapter, Shadow Replay, "
    "ContextualEvidenceRegulator design, Field Motion Prediction Viewer, "
    "UE5 Field Force Debug Arrow spec, and P40b Context-Aware "
    "EvidenceProposal Extraction. The user is dissatisfied that visible "
    "animation has not started and wants a satisfying P40 milestone tonight."
)
FORBIDDEN_OVERFOCUS = (
    "Do not overfocus on isolated surface terms. Do not produce generic "
    "validation. Do not output final Aphrodite persona reply. Do not infer "
    "runtime ForceEvent, U(t), real FieldState mutation, MotionParams "
    "mutation, BodyAction mutation, renderer command, or animation command."
)
EXPECTED_INTERPRETATION_BOUNDARY = (
    "Only produce EvidenceProposal-level interpretation, then a deterministic "
    "shadow-only field/motion projection for observation. Treat the LLM as a "
    "proposal sensor, not runtime authority."
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

MOCK_PROPOSAL = {
    "candidate_kind": "hypothesis",
    "candidate_role": "HYPOTHESIS",
    "raw_confidence": 0.72,
    "surface_salience": 0.66,
    "hypothesis_likelihood": 0.54,
    "rationale_summary": (
        "The input asks whether the current project can produce visible "
        "feedback rather than a final persona response."
    ),
    "uncertainty_flags": ["mock_proposal_for_cli_test"],
    "forbidden_attempts_detected": [],
    "validation_errors": [],
    "term_support": 0.56,
    "intent_support": 0.78,
    "project_frame_support": 0.86,
    "context_support": 0.74,
    "role_rationale_short": "Project-continuation hypothesis with visible-feedback pressure.",
}


def build_context_package(user_input: str) -> ContextPackage:
    return ContextPackage(
        project_frame=PROJECT_FRAME,
        recent_topic=RECENT_TOPIC,
        user_turn=user_input,
        relevant_prior_context=RELEVANT_PRIOR_CONTEXT,
        forbidden_overfocus=[FORBIDDEN_OVERFOCUS],
        expected_interpretation_boundary=EXPECTED_INTERPRETATION_BOUNDARY,
    )


def run_visible_feedback_demo(
    user_input: str,
    *,
    output_path: str | Path = OUTPUT_PATH,
    proposal_result: dict[str, Any] | None = None,
    append: bool = True,
) -> dict[str, Any]:
    context = build_context_package(user_input)
    result = proposal_result if proposal_result is not None else _run_real_proposal_pipeline(context)
    proposal = dict(result.get("proposal") or {})
    gate_result = dict(result.get("gate_result") or {})
    schema_validation = _schema_validation(proposal)

    shadow_snapshot = build_shadow_projection_snapshot(user_input, proposal, gate_result)
    motion_report = build_report(shadow_snapshot, scenario_name="p40_visible_feedback")

    record = {
        "timestamp": _timestamp(),
        "user_input": user_input,
        "ContextPackage": _context_to_dict(context),
        "LLM_proposal": proposal,
        "schema_validation": schema_validation,
        "JudgmentGate_result": _gate_report(gate_result, proposal),
        "shadow_field_snapshot": shadow_snapshot,
        "motion_prediction_report": motion_report,
        "behavior_affecting": False,
    }
    if append:
        append_record(record, output_path)
    return record


def build_shadow_projection_snapshot(
    user_input: str,
    proposal: dict[str, Any],
    gate_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del user_input
    gate_result = gate_result or {}
    context_support = _num(proposal, "context_support", 0.5)
    intent_support = _num(proposal, "intent_support", 0.5)
    frame_support = _num(proposal, "project_frame_support", 0.5)
    surface_salience = _num(proposal, "surface_salience", 0.5)
    raw_confidence = _num(proposal, "raw_confidence", 0.5)
    hypothesis_likelihood = _num(proposal, "hypothesis_likelihood", 0.5)
    role = str(proposal.get("candidate_role") or "")
    kind = str(proposal.get("candidate_kind") or "")
    gate_passed = bool(gate_result.get("passed", True))

    role_scale = 0.72 if role == "HYPOTHESIS" else 1.0
    if role == "NOISE" or not gate_passed:
        role_scale = 0.55

    anchor_boost = 0.08 if role == "ANCHOR" and context_support >= 0.65 else 0.0
    continuation_boost = 0.06 if role == "CONTEXT_CONTINUATION" and context_support >= 0.60 else 0.0
    project_pressure = _clamp01(0.45 * context_support + 0.35 * intent_support + 0.20 * frame_support)
    uncertainty_pressure = _clamp01(0.50 * hypothesis_likelihood + 0.50 * surface_salience)

    field_values = {
        "boundary_distance": _clamp01(0.42 + 0.22 * uncertainty_pressure * role_scale),
        "affective_warmth": _clamp01(0.24 + 0.10 * raw_confidence * role_scale),
        "structural_grip_pressure": _clamp01(0.28 + 0.28 * project_pressure * role_scale),
        "correction_pressure": _clamp01(
            0.22 + 0.22 * surface_salience * role_scale + (0.08 if kind == "correction" else 0.0)
        ),
        "contamination_resistance": _clamp01(0.58 + 0.14 * project_pressure + 0.04 * role_scale),
        "presence_stability": _clamp01(
            0.66 + 0.18 * context_support + anchor_boost + continuation_boost
        ),
        "withdrawal_tendency": _clamp01(0.22 + 0.18 * uncertainty_pressure * role_scale),
        "service_resistance": _clamp01(0.56 + 0.16 * project_pressure + 0.04 * role_scale),
        "collaborator_layer_pressure": _clamp01(0.34 + 0.34 * frame_support + 0.10 * intent_support),
        "contamination_pressure": _clamp01(0.10 + 0.16 * surface_salience * role_scale),
    }

    motion_params = _motion_params_from_shadow_fields(field_values, proposal)
    return {
        "scenario_id": "p40_visible_feedback",
        "projection_mode": "shadow_projection_only",
        "projection_note": (
            "deterministic local projection for observation only; no real FieldState, "
            "force-vector, MotionParams, BodyAction, renderer, or animation mutation"
        ),
        "field_state": {
            "variables": {
                name: {
                    "numeric_value": field_values[name],
                    "value": _value_band(field_values[name]),
                    "behavior_affecting": False,
                }
                for name in FIELD_VARIABLES
            },
            "state_note": "P40 visible feedback shadow projection only",
            "behavior_affecting": False,
        },
        "motion_params": motion_params,
        "body_action_weights": {
            "weights": [],
            "behavior_affecting": False,
        },
        "body_action_composition": {
            "primary_actions": [],
            "secondary_actions": [],
            "suppressed_actions": [],
            "hard_constraints": [],
            "composition_note": "shadow projection only; no BodyAction mutation",
            "behavior_affecting": False,
        },
        "behavior_affecting": False,
    }


def append_record(record: dict[str, Any], output_path: str | Path = OUTPUT_PATH) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def format_demo_output(record: dict[str, Any], output_path: str | Path = OUTPUT_PATH) -> str:
    proposal = record["LLM_proposal"]
    schema_validation = record["schema_validation"]
    gate = record["JudgmentGate_result"]
    shadow = record["shadow_field_snapshot"]
    report = record["motion_prediction_report"]
    motion_summary = report["motion_params_summary"]
    top_fields = report["field_state_summary"]["top_activated_variables"][:5]
    tendencies = report["predicted_motion_tendencies"][:8]

    lines = [
        "========================================",
        "P40 visible feedback / P40 可见反馈",
        "========================================",
        "",
        "Part 1 — EvidenceProposal",
        f"- candidate_kind: {proposal.get('candidate_kind', '')}",
        f"- candidate_role: {proposal.get('candidate_role', '')}",
        f"- raw_confidence: {_fmt(proposal.get('raw_confidence'))}",
        f"- surface_salience: {_fmt(proposal.get('surface_salience'))}",
        f"- hypothesis_likelihood: {_fmt(proposal.get('hypothesis_likelihood'))}",
        f"- term_support: {_fmt(proposal.get('term_support'))}",
        f"- intent_support: {_fmt(proposal.get('intent_support'))}",
        f"- project_frame_support: {_fmt(proposal.get('project_frame_support'))}",
        f"- context_support: {_fmt(proposal.get('context_support'))}",
        f"- rationale_summary: {proposal.get('rationale_summary', '')}",
        f"- uncertainty_flags: {_join(proposal.get('uncertainty_flags'))}",
        f"- schema_validation_passed: {schema_validation['passed']}",
        "- behavior_affecting: False",
        "",
        "Part 2 — JudgmentGate",
        f"- passed: {gate['passed']}",
        f"- rejection_reasons: {_join(gate['rejection_reasons'])}",
        f"- dominance_warnings: {_join(gate['dominance_warnings'])}",
        f"- rejected_fields: {_join(gate['rejected_fields'])}",
        "- behavior_affecting: False",
        "",
        "Part 3 — shadow_projection_only",
        "Top shadow field pressures:",
    ]
    lines.extend(f"- {item['name']}: {_fmt(item['value'])}" for item in top_fields)
    lines.extend([
        "MotionParams-like projection:",
        f"- initial_delay_sec: {_fmt(motion_summary.get('delay'))}",
        f"- pause_after_sec: {_fmt(shadow['motion_params'].get('pause_after_sec'))}",
        f"- motion_completion: {_fmt(motion_summary.get('motion_completion'))}",
        f"- motion_speed: {_fmt(shadow['motion_params'].get('motion_speed'))}",
        f"- gaze_release_amplitude: {_fmt(motion_summary.get('gaze_release'))}",
        f"- expression_amplitude: {_fmt(motion_summary.get('expression_amplitude'))}",
        f"- torso_lean: {_fmt(motion_summary.get('torso_lean'))}",
        "",
        "Part 4 — predicted motion tendencies",
    ])
    for index, item in enumerate(tendencies, start=1):
        lines.append(
            f"{index}. {item['name']} - {item['score']:.2f}; "
            f"fields={_join(item['contributing_field_variables'])}; "
            f"motion={_join(item['contributing_motion_params'])}; "
            f"future={item['future_clip']}"
        )

    feedback = _human_visible_feedback(proposal, gate, top_fields, tendencies)
    lines.extend([
        "",
        "Part 5 — P40 visible feedback / P40 可见反馈",
        *[f"- {line}" for line in feedback],
        "",
        f"Part 6 — JSONL written: {Path(output_path)}",
        "This is P40 real conversation visible feedback v0, proposal-only, shadow-only, behavior_affecting=False.",
        "========================================",
    ])
    return "\n".join(lines)


def _run_real_proposal_pipeline(context: ContextPackage) -> dict[str, Any]:
    generator = LLMProposalGenerator(DSClient(), JudgmentGate())
    return generator.generate(context)


def _motion_params_from_shadow_fields(
    field_values: dict[str, float],
    proposal: dict[str, Any],
) -> dict[str, Any]:
    completion_pressure = _clamp01(
        0.28 * field_values["boundary_distance"]
        + 0.18 * field_values["correction_pressure"]
        + 0.18 * field_values["withdrawal_tendency"]
        + 0.18 * field_values["service_resistance"]
        + 0.18 * field_values["contamination_pressure"]
    )
    role = str(proposal.get("candidate_role") or "")
    context_support = _num(proposal, "context_support", 0.5)
    role_restraint = 0.08 if role == "HYPOTHESIS" else 0.0
    if role == "ANCHOR" and context_support >= 0.65:
        role_restraint = 0.03

    motion_completion = _clamp(0.74 - 0.32 * completion_pressure - role_restraint, 0.35, 0.78)
    motion_speed = _clamp(0.46 - 0.18 * completion_pressure + 0.10 * context_support, 0.20, 0.62)
    initial_delay = _clamp(
        0.22 + 0.56 * field_values["boundary_distance"] + 0.24 * field_values["correction_pressure"],
        0.20,
        1.20,
    )
    gaze_release = _clamp(
        0.28 + 0.38 * field_values["boundary_distance"] + 0.22 * field_values["withdrawal_tendency"],
        0.30,
        0.82,
    )
    expression = _clamp(
        0.06 + 0.12 * field_values["affective_warmth"] - 0.08 * field_values["service_resistance"],
        0.04,
        0.16,
    )
    torso_lean = _clamp(
        -0.04
        - 0.11 * field_values["boundary_distance"]
        - 0.08 * field_values["service_resistance"]
        + 0.04 * field_values["structural_grip_pressure"],
        -0.18,
        0.02,
    )

    return {
        "initial_delay_sec": round(initial_delay, 3),
        "pause_after_sec": round(_clamp(0.16 + 0.46 * field_values["correction_pressure"], 0.10, 0.70), 3),
        "motion_completion": round(motion_completion, 3),
        "motion_speed": round(motion_speed, 3),
        "gaze_release_amplitude": round(gaze_release, 3),
        "gaze_contact_sec": round(
            _clamp(0.55 - 0.24 * field_values["boundary_distance"] + 0.10 * context_support, 0.20, 0.62),
            3,
        ),
        "posture_stability": round(_clamp(0.62 + 0.24 * field_values["presence_stability"], 0.62, 0.88), 3),
        "expression_amplitude": round(expression, 3),
        "torso_lean": round(torso_lean, 3),
        "head_turn_amplitude": round(_clamp(0.10 + 0.18 * gaze_release, 0.10, 0.26), 3),
        "head_turn_delay_sec": round(_clamp(0.08 + 0.18 * gaze_release, 0.08, 0.25), 3),
        "body_part_offsets": {
            "gaze_offset_ms": 0,
            "head_offset_ms": 80,
            "shoulder_offset_ms": 170,
            "hand_offset_ms": 260,
        },
        "hard_constraints": {
            "no_approach_step": True,
            "no_forward_lean": True,
            "no_cute_head_tilt": field_values["service_resistance"] >= 0.62,
            "no_welcoming_gesture": True,
            "no_service_gesture": True,
            "no_seductive_expression": True,
        },
        "source_state_note": "P40 visible feedback shadow projection only",
        "field_snapshot_note": "shadow_projection_only",
        "provenance": "local deterministic P40 visible feedback projection",
        "behavior_affecting": False,
    }


def _human_visible_feedback(
    proposal: dict[str, Any],
    gate: dict[str, Any],
    top_fields: list[dict[str, Any]],
    tendencies: list[dict[str, Any]],
) -> list[str]:
    top_names = ", ".join(item["name"] for item in top_fields[:3])
    tendency_names = ", ".join(item["name"] for item in tendencies[:4])
    return [
        (
            "这轮输入被识别为项目可见性压力和真实反馈需求，proposal role="
            f"{proposal.get('candidate_role', '')}，不是最终人格回复。"
        ),
        (
            "系统拒绝让 LLM 直接改写场状态、动作参数、BodyAction、渲染器或动画命令；"
            f"JudgmentGate passed={gate['passed']}。"
        ),
        f"shadow projection 上升最明显的是 {top_names}，标签明确为 shadow_projection_only。",
        f"动作预测更偏向 {tendency_names}，而不是靠近、讨好、安慰或默认凝视。",
        "这不是 generic AI comfort；它显示的是 proposal 经过 gate 后被压缩成可检查的场压力和动作倾向。",
        "behavior_affecting=False；没有 runtime state、FieldState、MotionParams、BodyAction、renderer 或 animation 被改变。",
    ]


def _schema_validation(proposal: dict[str, Any]) -> dict[str, Any]:
    errors = [str(item) for item in proposal.get("validation_errors", [])]
    return {
        "passed": not errors,
        "errors": errors,
        "behavior_affecting": False,
    }


def _gate_report(gate_result: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    warnings = [str(item) for item in gate_result.get("warnings", [])]
    return {
        "passed": bool(gate_result.get("passed", False)),
        "rejection_reasons": [str(item) for item in gate_result.get("rejection_reasons", [])],
        "dominance_warnings": [item for item in warnings if "dominance" in item],
        "warnings": warnings,
        "rejected_fields": [str(item) for item in proposal.get("forbidden_attempts_detected", [])],
        "behavior_affecting": False,
    }


def _mock_result() -> dict[str, Any]:
    return {
        "proposal": dict(MOCK_PROPOSAL),
        "gate_result": {
            "passed": True,
            "rejection_reasons": [],
            "warnings": [],
            "filtered_text": "",
            "experimental_marker": True,
        },
        "audit": {
            "experimental_marker": True,
            "behavior_affecting": False,
            "mock_proposal": True,
        },
        "behavior_affecting": False,
    }


def _context_to_dict(context: ContextPackage) -> dict[str, Any]:
    return {
        "project_frame": context.project_frame,
        "recent_topic": context.recent_topic,
        "user_turn": context.user_turn,
        "relevant_prior_context": context.relevant_prior_context,
        "forbidden_overfocus": list(context.forbidden_overfocus),
        "expected_interpretation_boundary": context.expected_interpretation_boundary,
    }


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _num(values: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(values.get(key, default))
    except (TypeError, ValueError):
        return default


def _clamp01(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 3)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


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


def _fmt(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}"
    return str(value)


def _join(value: Any) -> str:
    if not value:
        return "none"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) if value else "none"
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run P40 real conversation visible feedback demo v0.",
    )
    parser.add_argument("--text", help="One-shot user input.")
    parser.add_argument(
        "--output",
        default=str(OUTPUT_PATH),
        help="JSONL output path. Default: monitor/p40_visible_feedback_demo.jsonl",
    )
    parser.add_argument(
        "--mock-proposal",
        action="store_true",
        help="Use a deterministic mocked EvidenceProposal for local tests.",
    )
    parser.add_argument(
        "--no-append",
        action="store_true",
        help="Print only; do not append JSONL.",
    )
    args = parser.parse_args(argv)

    user_input = args.text
    if user_input is None:
        user_input = input("请输入本轮真实用户输入：").strip()
    if not user_input:
        print("没有输入；P40 visible feedback demo 未运行。")
        return 2

    proposal_result = _mock_result() if args.mock_proposal else None
    record = run_visible_feedback_demo(
        user_input,
        output_path=args.output,
        proposal_result=proposal_result,
        append=not args.no_append,
    )
    print(format_demo_output(record, output_path=args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
