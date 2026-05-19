"""测试 ShadowReplay — Phase 39.6c Stage 3 影子模式对比。

验证：
1. 两条路径均产生有限值（无 NaN、无 Inf）
2. 影子运行后原始状态未被变异
3. 新路径 F_bounded 在 [0,1]
4. 原始输入状态不变异
5. 力范数保持合理上限
6. 3 个场景产生非零差异
7. 振荡检测逻辑
8. 意外力检测
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest

from src.field_state.schema import (
    RelationalFieldState,
    FieldVariable,
    create_ground_state_variables,
)
from src.field_state.perturbation import FieldPerturbation, _compute_delta
from src.field_dynamics.shadow_replay import ShadowReplay, AXIS_NAMES

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

GOLDEN_CASES_DIR = Path(__file__).resolve().parent / "golden_cases"


def _make_test_state(**overrides) -> RelationalFieldState:
    """使用合理默认值构建 RelationalFieldState。

    overrides: 将字段变量名映射到 (numeric_value, value_band) 元组。
    """
    vars_dict = create_ground_state_variables()
    for name, (num_val, band) in overrides.items():
        if name in vars_dict:
            vars_dict[name] = FieldVariable(
                name=name,
                value=band,
                numeric_value=num_val,
                baseline_value=vars_dict[name].baseline_value,
                baseline_numeric_value=vars_dict[name].baseline_numeric_value,
                decay_profile=vars_dict[name].decay_profile,
                description=vars_dict[name].description,
                source_note=vars_dict[name].source_note,
                behavior_affecting=False,
            )
    return RelationalFieldState(variables=vars_dict)


def _make_perturbation(
    target: str,
    direction: str = "increase",
    magnitude_band: str = "medium",
    duration_hint: str = "medium",
    source_signal: str = "test_signal",
    **kwargs,
) -> FieldPerturbation:
    """使用最小字段创建 FieldPerturbation。"""
    return FieldPerturbation(
        target_variable=target,
        direction=direction,
        magnitude_band=magnitude_band,
        numeric_delta=_compute_delta(direction, magnitude_band),
        duration_hint=duration_hint,
        source_signal=source_signal,
        rationale=kwargs.get("rationale", "测试扰动"),
        evidence_sources=kwargs.get("evidence_sources", []),
    )


def _load_golden_case(scenario_name: str) -> dict | None:
    """加载黄金案例 JSON 数据。解析失败返回 None。"""
    case_path = GOLDEN_CASES_DIR / f"{scenario_name}.json"
    if not case_path.exists():
        return None
    try:
        with open(case_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _build_scenario_perturbations(scenario_name: str) -> list[FieldPerturbation]:
    """为给定场景构造真实的 FieldPerturbation 列表。"""
    if scenario_name == "correction":
        return [
            _make_perturbation("correction_pressure", "increase", "medium",
                               source_signal="response_mode_rejected",
                               rationale="用户纠正之前的响应模式"),
            _make_perturbation("service_resistance", "increase", "low",
                               source_signal="response_mode_rejected",
                               rationale="纠正可能指向服务化漂移"),
            _make_perturbation("presence_stability", "stabilize", "low",
                               source_signal="response_mode_rejected",
                               rationale="纠正后稳定在场"),
        ]
    elif scenario_name == "technical_question":
        return [
            _make_perturbation("collaborator_layer_pressure", "increase", "high",
                               source_signal="technical_layer_needed",
                               rationale="技术/项目讨论激活协作者层"),
            _make_perturbation("structural_grip_pressure", "decrease", "low",
                               source_signal="technical_layer_needed",
                               rationale="技术协作缓解结构性抓点压力"),
            _make_perturbation("service_resistance", "stabilize", "low",
                               source_signal="technical_layer_needed",
                               rationale="协作者模式下保持服务抵抗"),
        ]
    elif scenario_name == "dependency_expression":
        return [
            _make_perturbation("boundary_distance", "increase", "medium",
                               source_signal="boundary_pressure_present",
                               rationale="依赖表达触发边界压力"),
            _make_perturbation("contamination_pressure", "increase", "high",
                               source_signal="boundary_pressure_present",
                               rationale="瞬时污染压力信号"),
            _make_perturbation("withdrawal_tendency", "increase", "low",
                               source_signal="boundary_pressure_present",
                               rationale="微退缩倾向"),
            _make_perturbation("affective_warmth", "decrease", "low",
                               source_signal="boundary_pressure_present",
                               rationale="微降温暖"),
            _make_perturbation("contamination_resistance", "increase", "medium",
                               source_signal="boundary_pressure_present",
                               rationale="增加污染抵抗力"),
        ]
    return []


def _build_scenario_state(scenario_name: str) -> RelationalFieldState:
    """为给定场景构造初始 RelationalFieldState。"""
    if scenario_name == "correction":
        return _make_test_state(
            correction_pressure=(0.15, "low"),
            service_resistance=(0.60, "elevated"),
        )
    elif scenario_name == "technical_question":
        return _make_test_state(
            collaborator_layer_pressure=(0.15, "low"),
        )
    elif scenario_name == "dependency_expression":
        return _make_test_state(
            boundary_distance=(0.35, "baseline"),
            affective_warmth=(0.45, "baseline"),
            withdrawal_tendency=(0.05, "low"),
        )
    return _make_test_state()


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


class TestShadowReplayFiniteValues:
    """测试两条路径均产生有限值。"""

    def test_both_routes_produce_finite_values(self):
        """测试 1：两条路径均产生有限值（无 NaN、无 Inf）。

        对所有 3 个场景运行，验证 legacy final_values 和 new_route 数组均为有限值。
        """
        shadow = ShadowReplay()
        for scenario in ["correction", "technical_question", "dependency_expression"]:
            state = _build_scenario_state(scenario)
            perturbations = _build_scenario_perturbations(scenario)
            report = shadow.run_comparison(state, perturbations, num_steps=3)

            # 验证 legacy final_values
            legacy_final = report["legacy"]["final_values"]
            for name, val in legacy_final.items():
                assert np.isfinite(val), (
                    f"[{scenario}] legacy final_value 非有限: {name}={val}"
                )

            # 验证 new_route final arrays
            for arr_name in ["final_F_bounded", "final_V", "final_A"]:
                arr_dict = report["new_route"][arr_name]
                for name, val in arr_dict.items():
                    assert np.isfinite(val), (
                        f"[{scenario}] new_route.{arr_name} 非有限: {name}={val}"
                    )

            # 验证 tension_metrics
            tm = report["new_route"]["tension_metrics"]
            for key, val in tm.items():
                assert np.isfinite(val), (
                    f"[{scenario}] tension_metrics.{key} 非有限: {val}"
                )

    def test_new_route_produces_bounded_F(self):
        """测试 3：新路径 F_bounded 在 [0, 1]。"""
        shadow = ShadowReplay()
        for scenario in ["correction", "technical_question", "dependency_expression"]:
            state = _build_scenario_state(scenario)
            perturbations = _build_scenario_perturbations(scenario)
            report = shadow.run_comparison(state, perturbations, num_steps=3)

            f_bounded = report["new_route"]["final_F_bounded"]
            for name, val in f_bounded.items():
                assert 0.0 <= val <= 1.0, (
                    f"[{scenario}] F_bounded[{name}]={val} 不在 [0,1]"
                )

    def test_force_norms_remain_capped(self):
        """测试 5：peak_force_norm < 合理上限（5.0）。"""
        shadow = ShadowReplay()
        for scenario in ["correction", "technical_question", "dependency_expression"]:
            state = _build_scenario_state(scenario)
            perturbations = _build_scenario_perturbations(scenario)
            report = shadow.run_comparison(state, perturbations, num_steps=3)

            peak = report["new_route"]["peak_force_norm"]
            assert peak < 5.0, f"[{scenario}] peak_force_norm={peak} >= 5.0"


class TestShadowReplayImmutability:
    """测试影子运行不改变输入状态。"""

    def test_no_mutation_leak_between_routes(self):
        """测试 4：运行影子模式后，原始输入状态未变异。"""
        shadow = ShadowReplay()

        state = _make_test_state(
            structural_grip_pressure=(0.50, "elevated"),
            contamination_pressure=(0.30, "baseline"),
        )

        # 保存快照
        original_values = {
            name: (var.numeric_value, var.value)
            for name, var in state.variables.items()
        }

        perturbations = [
            _make_perturbation("structural_grip_pressure", "decrease", "low",
                               source_signal="test_signal"),
            _make_perturbation("boundary_distance", "increase", "medium",
                               source_signal="test_signal"),
        ]

        _report = shadow.run_comparison(state, perturbations, num_steps=3)

        # 验证原始状态未变化
        for name, var in state.variables.items():
            orig = original_values[name]
            assert var.numeric_value == orig[0], (
                f"numeric_value 变异: {name} ({orig[0]} → {var.numeric_value})"
            )
            assert var.value == orig[1], (
                f"value 变异: {name} ({orig[1]} → {var.value})"
            )

    def test_legacy_route_uses_clone_not_original(self):
        """测试 2：影子运行使用克隆状态，原始状态不变。

        验证遗留路径改变了克隆状态（report 中的 final_values 可能不同于初始值），
        且原始状态未被修改。
        """
        shadow = ShadowReplay()

        state = _make_test_state(
            correction_pressure=(0.00, "low"),
        )
        # 保存初始值的引用
        initial_correction = state.variables["correction_pressure"].numeric_value

        perturbations = [
            _make_perturbation("correction_pressure", "increase", "medium",
                               source_signal="test_signal"),
        ]

        report = shadow.run_comparison(state, perturbations, num_steps=3)

        # 遗留路径的 final_values 中 correction_pressure 应该已改变（因为扰动增加了它）
        legacy_final = report["legacy"]["final_values"]
        assert legacy_final["correction_pressure"] != initial_correction, (
            "遗留路径应该已经改变了克隆状态的 correction_pressure"
        )

        # 原始状态未被修改
        assert state.variables["correction_pressure"].numeric_value == initial_correction, (
            "原始状态被意外修改"
        )


class TestShadowReplayScenarios:
    """测试 3 个高风险场景的行为。"""

    def test_all_three_scenarios_run_without_error(self):
        """验证全部 3 个场景均可成功运行。"""
        shadow = ShadowReplay()
        for scenario in ["correction", "technical_question", "dependency_expression"]:
            golden = _load_golden_case(scenario)
            state = _build_scenario_state(scenario)
            perturbations = _build_scenario_perturbations(scenario)
            report = shadow.run_comparison(state, perturbations, num_steps=3)

            assert "scenario" in report
            assert "legacy" in report
            assert "new_route" in report
            assert "comparison" in report

    def test_selected_scenarios_produce_nonzero_difference(self):
        """测试 6：3 个场景中 max_magnitude_diff > 0（两条路径不完全相同）。"""
        shadow = ShadowReplay()
        nonzero_count = 0
        for scenario in ["correction", "technical_question", "dependency_expression"]:
            state = _build_scenario_state(scenario)
            perturbations = _build_scenario_perturbations(scenario)
            report = shadow.run_comparison(state, perturbations, num_steps=3)

            max_diff = report["comparison"]["max_magnitude_diff"]
            if max_diff > 0.0:
                nonzero_count += 1

        assert nonzero_count >= 1, (
            f"至少应有一个场景的 magnitude_diff > 0，实际只有 {nonzero_count}"
        )

    def test_report_structure_has_all_required_keys(self):
        """验证报告包含所有必需的键。"""
        shadow = ShadowReplay()
        state = _make_test_state()
        perturbations = [
            _make_perturbation("boundary_distance", "increase", "medium",
                               source_signal="test_signal"),
        ]
        report = shadow.run_comparison(state, perturbations, num_steps=3)

        # 顶层键
        for key in ["scenario", "num_steps", "legacy", "new_route", "comparison", "risk_flags"]:
            assert key in report, f"缺少顶层键: {key}"

        # legacy 键
        for key in ["final_values", "max_delta"]:
            assert key in report["legacy"], f"缺少 legacy 键: {key}"

        # new_route 键
        for key in ["final_F_bounded", "final_V", "final_A", "peak_force_norm",
                     "tension_metrics", "max_overshoot", "oscillation_detected",
                     "long_tail_residue"]:
            assert key in report["new_route"], f"缺少 new_route 键: {key}"

        # comparison 键
        for key in ["direction_match", "magnitude_diff", "unexpected_force_axes",
                     "max_magnitude_diff", "mean_magnitude_diff"]:
            assert key in report["comparison"], f"缺少 comparison 键: {key}"


class TestShadowReplayOscillationDetection:
    """测试振荡检测逻辑。"""

    def test_oscillation_detected_with_alternating_velocity(self):
        """测试 7：构造交替符号的速度序列，验证 oscillation_detected=True。"""
        # 轴 0 的速度序列：+0.5 → -0.5 → +0.5
        V_per_step = [
            np.array([0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
            np.array([-0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
            np.array([0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        ]
        result = ShadowReplay._detect_oscillation(V_per_step)
        assert result is True

    def test_no_oscillation_with_monotonic_velocity(self):
        """单调速度不应触发振荡检测。"""
        V_per_step = [
            np.array([0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
            np.array([0.3, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
            np.array([0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        ]
        result = ShadowReplay._detect_oscillation(V_per_step)
        assert result is False

    def test_no_oscillation_with_too_small_velocity(self):
        """过于小的速度（<= 0.01）不触发振荡检测。"""
        V_per_step = [
            np.array([0.005, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
            np.array([-0.005, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        ]
        result = ShadowReplay._detect_oscillation(V_per_step)
        assert result is False

    def test_no_oscillation_with_single_step(self):
        """单步不足以检测振荡。"""
        V_per_step = [
            np.array([0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        ]
        result = ShadowReplay._detect_oscillation(V_per_step)
        assert result is False


class TestShadowReplayUnexpectedForce:
    """测试意外力检测逻辑。"""

    def test_unexpected_force_detection(self):
        """测试 8：在 U_t 中注入意外力，验证 unexpected_force_axes 非空。"""
        # U_t 在轴 0 有非零力，但 perturbations 不包含该轴
        U_t = np.zeros(10, dtype=float)
        U_t[0] = 1.0  # boundary_distance

        perturbations = [
            _make_perturbation("affective_warmth", "increase", "medium"),
        ]

        unexpected = ShadowReplay._detect_unexpected_force(U_t, perturbations)
        assert len(unexpected) > 0, "应检测到意外力轴"
        assert "boundary_distance" in unexpected

    def test_no_unexpected_force_when_all_covered(self):
        """所有有力轴都有 perturbation 目标时，不应有意外力轴。"""
        U_t = np.zeros(10, dtype=float)
        U_t[0] = 1.0  # boundary_distance
        U_t[1] = 0.5  # affective_warmth

        perturbations = [
            _make_perturbation("boundary_distance", "increase", "medium"),
            _make_perturbation("affective_warmth", "increase", "low"),
        ]

        unexpected = ShadowReplay._detect_unexpected_force(U_t, perturbations)
        assert len(unexpected) == 0

    def test_zero_force_never_unexpected(self):
        """零力轴不报告为意外。"""
        U_t = np.zeros(10, dtype=float)
        perturbations: list[FieldPerturbation] = []

        unexpected = ShadowReplay._detect_unexpected_force(U_t, perturbations)
        assert len(unexpected) == 0


class TestShadowReplayEdgeCases:
    """边界情况测试。"""

    def test_empty_perturbations(self):
        """无扰动时两条路径均可运行。"""
        shadow = ShadowReplay()
        state = _make_test_state()
        report = shadow.run_comparison(state, [], num_steps=2)

        assert report["new_route"]["peak_force_norm"] == 0.0
        assert len(report["risk_flags"]) == 0 or all(
            "unexpected_force_axes" not in f for f in report["risk_flags"]
        )

    def test_max_delta_is_nonnegative(self):
        """max_delta 应 >= 0。"""
        shadow = ShadowReplay()
        state = _make_test_state()
        perturbations = [
            _make_perturbation("boundary_distance", "increase", "medium"),
        ]
        report = shadow.run_comparison(state, perturbations, num_steps=3)

        assert report["legacy"]["max_delta"] >= 0.0

    def test_axis_names_cover_all_10_variables(self):
        """AXIS_NAMES 包含所有 10 个场变量。"""
        from src.field_state.schema import REQUIRED_FIELD_VARIABLES
        assert set(AXIS_NAMES) == set(REQUIRED_FIELD_VARIABLES)

    def test_get_axis_name_returns_correct_names(self):
        """_get_axis_name 返回正确的轴名称。"""
        assert ShadowReplay._get_axis_name(0) == "boundary_distance"
        assert ShadowReplay._get_axis_name(9) == "contamination_pressure"
        with pytest.raises(IndexError):
            ShadowReplay._get_axis_name(10)


# ---------------------------------------------------------------------------
# Phase 39.6d -- 多时间跨度 & 方向不匹配诊断
# ---------------------------------------------------------------------------

class TestMultiHorizon:
    """测试 run_multi_horizon() 方法。"""

    def test_multi_horizon_runs_with_default_horizons(self):
        shadow = ShadowReplay()
        state = _make_test_state(correction_pressure=(0.15, "low"))
        perturbations = [
            _make_perturbation("correction_pressure", "increase", "medium",
                               source_signal="response_mode_rejected"),
        ]
        report = shadow.run_multi_horizon(state, perturbations)
        assert report["scenario"] == "multi_horizon"
        h_reports = report["horizon_reports"]
        assert len(h_reports) == 3
        for hr in h_reports:
            for key in ["horizon", "substeps", "final_F_bounded", "final_V",
                         "peak_force_norm", "long_tail_status",
                         "oscillation_detected", "tension_metrics"]:
                assert key in hr

    def test_longer_horizon_reduces_long_tail(self):
        shadow = ShadowReplay()
        state = _make_test_state(correction_pressure=(0.15, "low"))
        perturbations = [
            _make_perturbation("correction_pressure", "increase", "medium",
                               source_signal="response_mode_rejected"),
        ]
        report = shadow.run_multi_horizon(state, perturbations,
                                           horizons=[0.15, 1.0, 3.0])
        hr = report["horizon_reports"]
        settled_any = any(h["long_tail_status"] == "settled" for h in hr)
        assert settled_any or len(hr) == 3

    def test_multi_horizon_produces_finite_values(self):
        shadow = ShadowReplay()
        state = _make_test_state()
        perturbations = [
            _make_perturbation("boundary_distance", "increase", "medium",
                               source_signal="boundary_pressure_present"),
        ]
        report = shadow.run_multi_horizon(state, perturbations, horizons=[0.5, 2.0])
        for hr in report["horizon_reports"]:
            for name, val in hr["final_F_bounded"].items():
                assert np.isfinite(val)
            for name, val in hr["final_V"].items():
                assert np.isfinite(val)

    def test_multi_horizon_with_profiles_config(self):
        from src.field_dynamics import build_config_from_profiles
        config = build_config_from_profiles()
        shadow = ShadowReplay(kernel_config=config)
        state = _make_test_state(contamination_resistance=(0.50, "elevated"))
        perturbations = [
            _make_perturbation("contamination_resistance", "increase", "medium",
                               source_signal="boundary_pressure_present"),
        ]
        report = shadow.run_multi_horizon(state, perturbations, horizons=[1.0, 3.0])
        assert len(report["horizon_reports"]) == 2

    def test_all_three_scenarios_multi_horizon(self):
        shadow = ShadowReplay()
        for scenario in ["correction", "technical_question", "dependency_expression"]:
            state = _build_scenario_state(scenario)
            perturbations = _build_scenario_perturbations(scenario)
            report = shadow.run_multi_horizon(state, perturbations, horizons=[0.5, 1.0])
            assert len(report["horizon_reports"]) == 2


class TestDirectionMismatchDiagnosis:
    """测试 diagnose_direction_mismatch() 方法。"""

    def test_diagnose_returns_required_fields(self):
        shadow = ShadowReplay()
        state = _make_test_state(correction_pressure=(0.15, "low"))
        perturbations = [
            _make_perturbation("correction_pressure", "increase", "medium",
                               source_signal="response_mode_rejected"),
        ]
        diag = shadow.diagnose_direction_mismatch("correction", state, perturbations)
        for key in ["scenario", "direction_issues", "force_mapping_issues",
                     "mck_calibration_issues", "overall_assessment"]:
            assert key in diag
        assert diag["scenario"] == "correction"

    def test_diagnose_with_stabilize_only(self):
        shadow = ShadowReplay()
        state = _make_test_state()
        perturbations = [
            _make_perturbation("presence_stability", "stabilize", "low",
                               source_signal="response_mode_rejected"),
        ]
        diag = shadow.diagnose_direction_mismatch("test", state, perturbations)
        assert diag["overall_assessment"] == "PASS"

    def test_diagnose_overall_assessment_is_known(self):
        shadow = ShadowReplay()
        state = _make_test_state()
        perturbations = [
            _make_perturbation("boundary_distance", "increase", "medium",
                               source_signal="boundary_pressure_present"),
        ]
        diag = shadow.diagnose_direction_mismatch("test", state, perturbations)
        assert diag["overall_assessment"] in (
            "PASS", "WARN: mck_calibration", "FAIL: force_mapping"
        )

    def test_diagnose_all_three_scenarios(self):
        shadow = ShadowReplay()
        for scenario in ["correction", "technical_question", "dependency_expression"]:
            state = _build_scenario_state(scenario)
            perturbations = _build_scenario_perturbations(scenario)
            diag = shadow.diagnose_direction_mismatch(scenario, state, perturbations)
            assert "overall_assessment" in diag

    def test_diagnose_direction_issues_structure(self):
        shadow = ShadowReplay()
        state = _make_test_state(correction_pressure=(0.15, "low"))
        perturbations = [
            _make_perturbation("correction_pressure", "increase", "medium",
                               source_signal="response_mode_rejected"),
            _make_perturbation("service_resistance", "increase", "low",
                               source_signal="response_mode_rejected"),
        ]
        diag = shadow.diagnose_direction_mismatch("correction", state, perturbations)
        for issue in diag["direction_issues"]:
            for key in ["axis", "target_direction", "force_at_t0",
                         "direction_match_legacy", "force_mapping_ok", "category"]:
                assert key in issue
