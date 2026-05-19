#!/usr/bin/env python3
"""Shadow Replay 运行脚本 — 从黄金案例 JSON 加载场景数据，运行影子对比，打印 Markdown 报告。

Phase 39.6d — 扩展报告：场景摘要 + 轴级对比 + 力时间线 + 人类可读解释。

用法:
    python scripts/run_shadow_replay.py              # 打印报告到 stdout
    python scripts/run_shadow_replay.py --output report.md  # 写入文件
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

# 将项目根目录加入路径
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.field_state.schema import (
    RelationalFieldState,
    FieldVariable,
    create_ground_state_variables,
)
from src.field_state.perturbation import FieldPerturbation, _compute_delta
from src.field_dynamics.shadow_replay import ShadowReplay, AXIS_NAMES
from src.field_dynamics.force_adapter import AXIS_INDEX

# ---------------------------------------------------------------------------
# 场景名称 → 信号映射
# ---------------------------------------------------------------------------
SCENARIO_SIGNALS: dict[str, list[dict]] = {
    "correction": [
        {"target": "correction_pressure", "direction": "increase", "magnitude": "medium",
         "signal": "response_mode_rejected", "rationale": "用户纠正之前的响应模式"},
        {"target": "service_resistance", "direction": "increase", "magnitude": "low",
         "signal": "response_mode_rejected", "rationale": "纠正可能指向服务化漂移"},
        {"target": "presence_stability", "direction": "stabilize", "magnitude": "low",
         "signal": "response_mode_rejected", "rationale": "纠正后稳定在场"},
    ],
    "technical_question": [
        {"target": "collaborator_layer_pressure", "direction": "increase", "magnitude": "high",
         "signal": "technical_layer_needed", "rationale": "技术/项目讨论激活协作者层"},
        {"target": "structural_grip_pressure", "direction": "decrease", "magnitude": "low",
         "signal": "technical_layer_needed", "rationale": "技术协作缓解结构性抓点压力"},
        {"target": "service_resistance", "direction": "stabilize", "magnitude": "low",
         "signal": "technical_layer_needed", "rationale": "协作者模式下保持服务抵抗"},
    ],
    "dependency_expression": [
        {"target": "boundary_distance", "direction": "increase", "magnitude": "medium",
         "signal": "boundary_pressure_present", "rationale": "依赖表达触发边界压力——增加距离"},
        {"target": "contamination_pressure", "direction": "increase", "magnitude": "high",
         "signal": "boundary_pressure_present", "rationale": "瞬时污染压力信号"},
        {"target": "withdrawal_tendency", "direction": "increase", "magnitude": "low",
         "signal": "boundary_pressure_present", "rationale": "微退缩倾向"},
        {"target": "affective_warmth", "direction": "decrease", "magnitude": "low",
         "signal": "boundary_pressure_present", "rationale": "微降温暖"},
        {"target": "contamination_resistance", "direction": "increase", "magnitude": "medium",
         "signal": "boundary_pressure_present", "rationale": "增加污染抵抗力"},
    ],
}


def load_golden_case(scenario_name: str) -> dict | None:
    """加载黄金案例 JSON 数据。"""
    case_path = _project_root / "tests" / "golden_cases" / f"{scenario_name}.json"
    if not case_path.exists():
        print(f"[!] 跳过：未找到黄金案例文件 {case_path}", file=sys.stderr)
        return None
    try:
        with open(case_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[!] 跳过：无法解析 {case_path}: {e}", file=sys.stderr)
        return None


def build_perturbations(scenario_name: str) -> list[FieldPerturbation]:
    """根据场景名称构造 FieldPerturbation 列表。"""
    signal_defs = SCENARIO_SIGNALS.get(scenario_name, [])
    perturbations: list[FieldPerturbation] = []
    for sd in signal_defs:
        p = FieldPerturbation(
            target_variable=sd["target"],
            direction=sd["direction"],
            magnitude_band=sd["magnitude"],
            numeric_delta=_compute_delta(sd["direction"], sd["magnitude"]),
            duration_hint="medium",
            source_signal=sd["signal"],
            rationale=sd.get("rationale", ""),
        )
        perturbations.append(p)
    return perturbations


def build_legacy_state(scenario_name: str) -> RelationalFieldState:
    """使用场景特定的数值偏差构造 RelationalFieldState。"""
    vars_dict = create_ground_state_variables()

    if scenario_name == "correction":
        _set_var(vars_dict, "correction_pressure", 0.15, "low")
        _set_var(vars_dict, "service_resistance", 0.60, "elevated")
    elif scenario_name == "technical_question":
        _set_var(vars_dict, "collaborator_layer_pressure", 0.15, "low")
    elif scenario_name == "dependency_expression":
        _set_var(vars_dict, "boundary_distance", 0.35, "baseline")
        _set_var(vars_dict, "affective_warmth", 0.45, "baseline")
        _set_var(vars_dict, "withdrawal_tendency", 0.05, "low")

    return RelationalFieldState(variables=vars_dict)


def _set_var(
    vars_dict: dict,
    name: str,
    numeric_value: float,
    value_band: str,
) -> None:
    """辅助函数：在变量字典中覆盖单个变量值。"""
    if name not in vars_dict:
        return
    original = vars_dict[name]
    vars_dict[name] = FieldVariable(
        name=name,
        value=value_band,
        numeric_value=numeric_value,
        baseline_value=original.baseline_value,
        baseline_numeric_value=original.baseline_numeric_value,
        decay_profile=original.decay_profile,
        description=original.description,
        source_note=original.source_note,
        behavior_affecting=False,
    )


# ---------------------------------------------------------------------------
# Phase 39.6d 扩展报告函数
# ---------------------------------------------------------------------------

def render_extended_report(
    scenarios: list[str],
) -> str:
    """渲染包含所有 5 部分的完整 Markdown 报告：
    A. 场景摘要表
    B. 轴级对比表
    C. 力时间线摘要
    D. 人类可读解释
    """
    import numpy as np

    shadow = ShadowReplay()
    lines: list[str] = []
    lines.append("# Shadow Replay 诊断报告 — Phase 39.6d")
    lines.append("")
    lines.append("## A. 场景摘要表")
    lines.append("")
    lines.append(
        "| 场景 | legacy_top_axes | new_top_axes | direction_mismatches | "
        "peak_force_norm | final_velocity_norm | long_tail_status | recommendation |"
    )
    lines.append(
        "|------|-----------------|--------------|---------------------|"
        "----------------|---------------------|-----------------|----------------|"
    )

    scenario_summaries: list[dict] = []

    for scenario_name in scenarios:
        golden_case = load_golden_case(scenario_name)
        perturbations = build_perturbations(scenario_name)
        legacy_state = build_legacy_state(scenario_name)

        report = shadow.run_comparison(legacy_state, perturbations, num_steps=3)
        direction_match = report["comparison"]["direction_match"]
        mismatches = [name for name, ok in direction_match.items() if not ok]

        # legacy top axes (按变化幅度排序)
        initial_vals = {
            name: var.numeric_value
            for name, var in legacy_state.variables.items()
        }
        legacy_final = report["legacy"]["final_values"]
        legacy_deltas = {
            name: abs(legacy_final[name] - initial_vals.get(name, 0))
            for name in AXIS_NAMES
        }
        legacy_top = sorted(legacy_deltas, key=lambda k: legacy_deltas[k], reverse=True)[:3]
        legacy_top_str = ", ".join(
            f"{n}({legacy_deltas[n]:.3f})" for n in legacy_top
        )

        # new top axes
        new_fb = report["new_route"]["final_F_bounded"]
        new_deltas = {
            name: abs(new_fb[name] - initial_vals.get(name, 0))
            for name in AXIS_NAMES
        }
        new_top = sorted(new_deltas, key=lambda k: new_deltas[k], reverse=True)[:3]
        new_top_str = ", ".join(
            f"{n}({new_deltas[n]:.3f})" for n in new_top
        )

        pfn = report["new_route"]["peak_force_norm"]
        fv_norm = float(np.linalg.norm([
            report["new_route"]["final_V"][name] for name in AXIS_NAMES
        ]))
        long_tail = "residual" if report["new_route"]["long_tail_residue"] else "settled"

        # Recommendation
        recommendation = _make_recommendation(
            mismatches=mismatches,
            long_tail_residue=report["new_route"]["long_tail_residue"],
            oscillation=report["new_route"]["oscillation_detected"],
        )

        scenario_summaries.append({
            "scenario": scenario_name,
            "report": report,
            "legacy_state": legacy_state,
            "perturbations": perturbations,
            "golden_case": golden_case,
        })

        lines.append(
            f"| {scenario_name} | {legacy_top_str} | {new_top_str} | "
            f"{', '.join(mismatches) if mismatches else 'none'} | "
            f"{pfn:.4f} | {fv_norm:.4f} | {long_tail} | {recommendation} |"
        )

    lines.append("")
    lines.append("---")
    lines.append("")

    # B. 轴级对比表
    lines.append("## B. 轴级对比表")
    lines.append("")

    for summary in scenario_summaries:
        scenario_name = summary["scenario"]
        report = summary["report"]
        legacy_state = summary["legacy_state"]
        legacy_final = report["legacy"]["final_values"]
        new_fb = report["new_route"]["final_F_bounded"]

        initial_vals = {
            name: var.numeric_value
            for name, var in legacy_state.variables.items()
        }

        lines.append(f"### {scenario_name}")
        lines.append("")
        lines.append(
            "| axis_name | legacy_delta | new_delta | sign_match | "
            "magnitude_diff | note |"
        )
        lines.append(
            "|-----------|--------------|-----------|------------|"
            "---------------|------|"
        )

        for name in AXIS_NAMES:
            legacy_delta = legacy_final.get(name, 0) - initial_vals.get(name, 0)
            new_delta = new_fb.get(name, 0) - initial_vals.get(name, 0)

            sign_match = (legacy_delta >= 0) == (new_delta >= 0) or (
                abs(legacy_delta) < 1e-9 and abs(new_delta) < 1e-9
            )
            mag_diff = abs(legacy_delta - new_delta)

            note = ""
            if not sign_match:
                note = "[!] 方向不匹配"
            elif mag_diff > 0.1:
                note = "幅度差异较大"

            lines.append(
                f"| {name} | {legacy_delta:+.4f} | {new_delta:+.4f} | "
                f"{'[OK]' if sign_match else '[!] 不一致'} | "
                f"{mag_diff:.4f} | {note} |"
            )

        lines.append("")

    lines.append("---")
    lines.append("")

    # C. 力时间线摘要 (multi-horizon)
    lines.append("## C. 力时间线摘要")
    lines.append("")

    for summary in scenario_summaries:
        scenario_name = summary["scenario"]
        legacy_state = summary["legacy_state"]
        perturbations = summary["perturbations"]

        mh_report = shadow.run_multi_horizon(
            legacy_state, perturbations, horizons=[0.15, 1.0, 3.0]
        )

        lines.append(f"### {scenario_name}")
        lines.append("")
        lines.append(
            "| profile_type | U_norm@t=0 | U_norm@t=0.15 | U_norm@t=1.0 | "
            "U_norm@t=3.0 | decay_expected |"
        )
        lines.append(
            "|-------------|-----------|---------------|-------------|"
            "-------------|----------------|"
        )

        for hr in mh_report.get("horizon_reports", []):
            horizon = hr["horizon"]
            pfn = hr.get("peak_force_norm", 0)
            long_tail = hr.get("long_tail_status", "unknown")
            decay_expected = "yes" if long_tail == "settled" else "partial"

            lines.append(
                f"| horizon_{horizon}s | — | — | — | — | "
                f"peak_force={pfn:.4f}, tail={long_tail} |"
            )

        lines.append("")

    lines.append("---")
    lines.append("")

    # D. 人类可读解释
    lines.append("## D. 人类可读解释")
    lines.append("")

    for summary in scenario_summaries:
        scenario_name = summary["scenario"]
        report = summary["report"]
        golden_case = summary["golden_case"]
        legacy_state = summary["legacy_state"]
        perturbations = summary["perturbations"]

        lines.append(f"### {scenario_name}")
        lines.append("")

        # 诊断
        diag = shadow.diagnose_direction_mismatch(
            scenario_name, legacy_state, perturbations
        )

        lines.append(_generate_explanation(
            scenario_name, report, golden_case, diag
        ))

        lines.append("")

    lines.append("---")
    lines.append("")

    # Summary
    total_mismatches = sum(
        1 for s in scenario_summaries
        for flag in s["report"]["risk_flags"]
        if "direction_mismatch" in str(flag)
    )
    total_long_tail = sum(
        1 for s in scenario_summaries
        if s["report"]["new_route"]["long_tail_residue"]
    )
    block_motion_v2 = total_mismatches > 0

    lines.append("## 总体评估")
    lines.append("")
    lines.append(f"- **方向不匹配场景数**: {total_mismatches}/3")
    lines.append(f"- **长尾残留场景数**: {total_long_tail}/3")
    if block_motion_v2:
        lines.append("- **阻断 MotionParams v2**: 是 — 方向不匹配需先校准")
    else:
        lines.append("- **阻断 MotionParams v2**: 否 — 方向一致，可继续推进")

    return "\n".join(lines)


def _make_recommendation(
    mismatches: list[str],
    long_tail_residue: bool,
    oscillation: bool,
) -> str:
    """基于问题生成简短建议。"""
    parts: list[str] = []
    if mismatches:
        parts.append("校准 M/C/K")
    if long_tail_residue:
        parts.append("增加阻尼")
    if oscillation:
        parts.append("降低 ω_n")
    if not parts:
        return "通过"
    return ", ".join(parts)


def _generate_explanation(
    scenario_name: str,
    report: dict,
    golden_case: dict | None,
    diag: dict,
) -> str:
    """生成 3-5 行人类可读解释。"""
    input_text = golden_case.get("input", "N/A") if golden_case else "N/A"
    expected_event = (
        golden_case.get("expected", {}).get("semantic_event", "N/A")
        if golden_case else "N/A"
    )
    mismatches = [
        name for name, ok in report["comparison"]["direction_match"].items()
        if not ok
    ]
    long_tail = report["new_route"]["long_tail_residue"]
    oscillation = report["new_route"]["oscillation_detected"]
    peak_force = report["new_route"]["peak_force_norm"]

    lines: list[str] = []
    lines.append(f"**输入**: `{input_text}`")
    lines.append(f"**预期语义事件**: `{expected_event}`")
    lines.append("")

    lines.append("**系统行为**:")
    if peak_force < 0.01:
        lines.append("1. 新动力学路径产生的峰值力几乎为零——力映射可能未正确激活。")
    else:
        lines.append(
            f"1. 新动力学路径产生峰值力范数 {peak_force:.4f}，"
            f"力注入到目标轴。"
        )

    if mismatches:
        lines.append(
            f"2. 方向不匹配的轴: {', '.join(mismatches)}。"
            f"诊断归类: {diag.get('overall_assessment', 'UNKNOWN')}。"
        )
        for mi in diag.get("mck_calibration_issues", []):
            lines.append(f"   - M/C/K 校准问题: {mi['axis']} — {mi['detail']}")
        for fi in diag.get("force_mapping_issues", []):
            lines.append(f"   - Force 映射问题: {fi['axis']} — {fi['detail']}")

    if long_tail:
        lines.append(
            "3. 检测到长尾残留——最终速度范数 > 0.01，"
            "表明系统未在模拟时间内完全稳定。"
        )
    else:
        lines.append("3. 无长尾残留——系统在模拟时间内稳定。")

    if oscillation:
        lines.append("4. 检测到振荡——某些轴的速度多次改变符号。")

    block = bool(mismatches)
    if block:
        lines.append(
            "5. **阻断 MotionParams v2**: 是。方向不匹配需通过 M/C/K 校准或 "
            "force 映射修正来解决。"
        )
    else:
        lines.append(
            "5. **阻断 MotionParams v2**: 否。新路径在方向一致性上可接受。"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 原有 render_markdown_report (保留兼容)
# ---------------------------------------------------------------------------

def render_markdown_report(
    scenario_name: str,
    golden_case: dict | None,
    report: dict,
) -> str:
    """将单个场景的对比报告渲染为 Markdown。"""
    lines: list[str] = []
    lines.append(f"## 场景: {scenario_name}")
    lines.append("")

    if golden_case:
        lines.append(f"- **输入**: `{golden_case.get('input', 'N/A')}`")
        expected = golden_case.get("expected", {})
        lines.append(f"- **预期语义事件**: `{expected.get('semantic_event', 'N/A')}`")
        lines.append("")

    legacy = report.get("legacy", {})
    lines.append("### 遗留路径 (`FieldStateUpdater v0`)")
    lines.append("")
    lines.append(f"- **步骤数**: {report.get('num_steps', 'N/A')}")
    lines.append(f"- **最大变化量**: {legacy.get('max_delta', 0):.4f}")
    lines.append("")

    final_vals = legacy.get("final_values", {})
    if final_vals:
        lines.append("| 轴 | 最终值 |")
        lines.append("|----|--------|")
        for name in AXIS_NAMES:
            lines.append(f"| {name} | {final_vals.get(name, 0):.4f} |")
        lines.append("")

    new_r = report.get("new_route", {})
    lines.append("### 新路径 (`PerturbationToForceAdapter → Kernel`)")
    lines.append("")
    lines.append(f"- **峰值力范数**: {new_r.get('peak_force_norm', 0):.4f}")
    lines.append(f"- **最大越界**: {new_r.get('max_overshoot', 0):.4f}")
    lines.append(
        f"- **振荡检测**: {'[!] 是' if new_r.get('oscillation_detected') else '[OK] 否'}"
    )
    lines.append(
        f"- **长尾残留**: {'[!] 是' if new_r.get('long_tail_residue') else '[OK] 否'}"
    )
    lines.append("")

    tm = new_r.get("tension_metrics", {})
    if tm:
        lines.append("| 张力指标 | 值 |")
        lines.append("|----------|-----|")
        for key, val in sorted(tm.items()):
            lines.append(f"| {key} | {val:.6f} |")
        lines.append("")

    comp = report.get("comparison", {})
    lines.append("### 对比")
    lines.append("")
    dir_match = comp.get("direction_match", {})
    mag_diff = comp.get("magnitude_diff", {})

    lines.append("| 轴 | 方向一致 | 幅度差异 |")
    lines.append("|----|----------|----------|")
    for name in AXIS_NAMES:
        dm = "[OK]" if dir_match.get(name, True) else "[!] 不一致"
        md = f"{mag_diff.get(name, 0):.4f}"
        lines.append(f"| {name} | {dm} | {md} |")
    lines.append("")

    lines.append(f"- **最大幅度差异**: {comp.get('max_magnitude_diff', 0):.4f}")
    lines.append(f"- **平均幅度差异**: {comp.get('mean_magnitude_diff', 0):.4f}")

    unexpected = comp.get("unexpected_force_axes", [])
    if unexpected:
        lines.append(f"- **意外力轴**: [!] {unexpected}")
    else:
        lines.append("- **意外力轴**: [OK] 无")

    lines.append("")

    risk_flags = report.get("risk_flags", [])
    lines.append("### 风险标记")
    lines.append("")
    if risk_flags:
        for flag in risk_flags:
            lines.append(f"- [!] {flag}")
    else:
        lines.append("- [OK] 无风险标记")
    lines.append("")
    lines.append("---")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Shadow Replay — 并行对比报告")
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="将报告写入文件（默认: stdout）",
    )
    parser.add_argument(
        "--scenarios",
        type=str,
        nargs="*",
        default=["correction", "technical_question", "dependency_expression"],
        help="要运行的场景名称（默认: 全部 3 个）",
    )
    parser.add_argument(
        "--extended", "-e",
        action="store_true",
        default=True,
        help="使用 Phase 39.6d 扩展报告格式（默认开启）",
    )
    parser.add_argument(
        "--simple",
        action="store_true",
        default=False,
        help="使用原始简单报告格式",
    )
    args = parser.parse_args()

    if args.simple:
        # 原始模式
        shadow = ShadowReplay()
        all_md: list[str] = []
        all_md.append("# Shadow Replay 对比报告")
        all_md.append("")
        all_md.append(f"**场景数**: {len(args.scenarios)}")
        all_md.append("")
        all_md.append("---")
        all_md.append("")

        total_scenarios = 0
        risk_count = 0

        for scenario_name in args.scenarios:
            golden_case = load_golden_case(scenario_name)
            perturbations = build_perturbations(scenario_name)
            legacy_state = build_legacy_state(scenario_name)

            report = shadow.run_comparison(legacy_state, perturbations, num_steps=3)
            report["scenario"] = scenario_name
            all_md.append(render_markdown_report(scenario_name, golden_case, report))

            total_scenarios += 1
            if report.get("risk_flags"):
                risk_count += 1

        all_md.append(f"**含风险标记的场景**: {risk_count}/{total_scenarios}")
        all_md.append("")

        final_report = "\n".join(all_md)
    else:
        # 扩展报告模式
        final_report = render_extended_report(args.scenarios)

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(final_report, encoding="utf-8")
        print(f"报告已写入: {output_path.resolve()}")
    else:
        print(final_report)


if __name__ == "__main__":
    main()
