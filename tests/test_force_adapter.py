"""测试 PerturbationToForceAdapter — 将 FieldPerturbation 列表转换为 U_t 力向量。

Phase 39.6b — ForceEvent Adapter 实施。
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import numpy as np
import pytest

from src.field_dynamics.force_adapter import (
    AXIS_INDEX,
    DURATION_HINT_TO_SEC,
    FORCE_PROFILE_TYPES,
    PerturbationToForceAdapter,
)
from src.field_state.perturbation import FieldPerturbation


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _perturbation(
    target_variable: str,
    direction: str = "increase",
    magnitude_band: str = "medium",
    numeric_delta: float = 0.10,
    duration_hint: str = "medium",
    source_signal: str = "response_mode_rejected",
) -> FieldPerturbation:
    return FieldPerturbation(
        target_variable=target_variable,
        direction=direction,
        magnitude_band=magnitude_band,
        numeric_delta=numeric_delta,
        duration_hint=duration_hint,
        source_signal=source_signal,
        rationale="测试扰动",
    )


# ---------------------------------------------------------------------------
# 基本转换
# ---------------------------------------------------------------------------

class TestBasicConversion:
    """扰动 → 力向量的基本转换。"""

    def test_empty_perturbations_returns_zero_U_t(self):
        """空列表 → 全零力向量。"""
        adapter = PerturbationToForceAdapter()
        U_t = adapter.adapt([])
        assert U_t.shape == (10,)
        assert np.all(U_t == 0.0)

    def test_increase_perturbation_positive_force(self):
        """方向 "increase" → 正力。"""
        adapter = PerturbationToForceAdapter()
        p = _perturbation(
            target_variable="correction_pressure",
            direction="increase",
            numeric_delta=0.10,
            source_signal="response_mode_rejected",
        )
        U_t = adapter.adapt([p])
        axis = AXIS_INDEX["correction_pressure"]
        assert U_t[axis] > 0.0
        # 幅值 = 0.10 * 2.0 = 0.20（sharp_pulse 在 t=0 为全幅值）
        assert U_t[axis] == pytest.approx(0.20)

    def test_decrease_perturbation_negative_force(self):
        """方向 "decrease" → 负力。"""
        adapter = PerturbationToForceAdapter()
        p = _perturbation(
            target_variable="affective_warmth",
            direction="decrease",
            numeric_delta=-0.05,
            source_signal="boundary_pressure_present",
        )
        U_t = adapter.adapt([p])
        axis = AXIS_INDEX["affective_warmth"]
        assert U_t[axis] < 0.0
        # 幅值 = 0.05 * 2.0 = 0.10，负号 → -0.10
        assert U_t[axis] == pytest.approx(-0.10)

    def test_stabilize_perturbation_zero_force(self):
        """方向 "stabilize" → 零力贡献。"""
        adapter = PerturbationToForceAdapter()
        p = _perturbation(
            target_variable="presence_stability",
            direction="stabilize",
            numeric_delta=0.0,
            source_signal="response_mode_rejected",
        )
        U_t = adapter.adapt([p])
        assert np.all(U_t == 0.0)

    def test_multiple_perturbations_sum(self):
        """同一轴上有 2 个扰动时力求和。"""
        adapter = PerturbationToForceAdapter()
        p1 = _perturbation(
            target_variable="correction_pressure",
            direction="increase",
            numeric_delta=0.10,
            source_signal="response_mode_rejected",
        )
        p2 = _perturbation(
            target_variable="correction_pressure",
            direction="increase",
            numeric_delta=0.05,
            source_signal="response_mode_rejected",
        )
        U_t = adapter.adapt([p1, p2])
        axis = AXIS_INDEX["correction_pressure"]
        # 0.10*2.0 + 0.05*2.0 = 0.20 + 0.10 = 0.30
        assert U_t[axis] == pytest.approx(0.30)


# ---------------------------------------------------------------------------
# 剖面函数
# ---------------------------------------------------------------------------

class TestProfileFunctions:
    """profile_value() 的 6 种力剖面形状。"""

    def test_impulse_profile_shape(self):
        """impulse：在 0.3*duration 内为 full amplitude，之后为 0。"""
        adapter = PerturbationToForceAdapter()
        duration = 1.0
        amp = 1.0
        pulse_end = 0.3 * duration  # 0.3

        # 在脉冲窗口内
        assert adapter.profile_value("impulse", t=0.0, duration=duration, amplitude=amp) == pytest.approx(1.0)
        assert adapter.profile_value("impulse", t=0.2, duration=duration, amplitude=amp) == pytest.approx(1.0)
        # 刚好在脉冲结束后
        assert adapter.profile_value("impulse", t=0.31, duration=duration, amplitude=amp) == pytest.approx(0.0)
        assert adapter.profile_value("impulse", t=0.8, duration=duration, amplitude=amp) == pytest.approx(0.0)

    def test_decaying_pulse_exponential(self):
        """decaying_pulse：曲线单调递减（指数衰减）。"""
        adapter = PerturbationToForceAdapter()
        duration = 2.0
        amp = 1.0
        tau = 0.5 * duration  # 1.0

        v0 = adapter.profile_value("decaying_pulse", t=0.0, duration=duration, amplitude=amp)
        v1 = adapter.profile_value("decaying_pulse", t=0.3, duration=duration, amplitude=amp)
        v2 = adapter.profile_value("decaying_pulse", t=0.6, duration=duration, amplitude=amp)
        v3 = adapter.profile_value("decaying_pulse", t=1.0, duration=duration, amplitude=amp)

        assert v0 == pytest.approx(1.0)
        # 单调递减
        assert v0 > v1 > v2 > v3
        # 在 t=tau 时应为 amp * exp(-1)
        assert v3 == pytest.approx(amp * math.exp(-1.0 * 1.0 / tau))
        # 超过 0.5*duration 后为 0
        assert adapter.profile_value("decaying_pulse", t=1.1, duration=duration, amplitude=amp) == pytest.approx(0.0)

    def test_ramp_profile_increasing(self):
        """ramp：从 0 线性上升到峰值。"""
        adapter = PerturbationToForceAdapter()
        duration = 1.0
        amp = 1.0
        rise_time = 0.8 * duration  # 0.8

        # t=0 时为 0
        assert adapter.profile_value("ramp", t=0.0, duration=duration, amplitude=amp) == pytest.approx(0.0)
        # 中间某点应大于 0 小于 amp
        mid_val = adapter.profile_value("ramp", t=0.4, duration=duration, amplitude=amp)
        assert 0.0 < mid_val < amp
        # 在 0.8*duration 时应几乎达到 amp
        peak_val = adapter.profile_value("ramp", t=rise_time, duration=duration, amplitude=amp)
        assert peak_val == pytest.approx(amp)
        # 超过 duration 后为 0
        assert adapter.profile_value("ramp", t=1.1, duration=duration, amplitude=amp) == pytest.approx(0.0)

    def test_persistent_step_flat_top(self):
        """persistent_step：方波在中间平坦。"""
        adapter = PerturbationToForceAdapter()
        duration = 1.0
        amp = 1.0
        hold_end = 0.9 * duration  # 0.9

        # 起始
        assert adapter.profile_value("persistent_step", t=0.0, duration=duration, amplitude=amp) == pytest.approx(amp)
        # 中间平坦
        assert adapter.profile_value("persistent_step", t=0.45, duration=duration, amplitude=amp) == pytest.approx(amp)
        # 刚好在 hold 结束前
        assert adapter.profile_value("persistent_step", t=hold_end, duration=duration, amplitude=amp) == pytest.approx(amp)
        # 超过后为 0
        assert adapter.profile_value("persistent_step", t=0.91, duration=duration, amplitude=amp) == pytest.approx(0.0)

    def test_profile_zero_outside_duration(self):
        """t < 0 或 t > duration 时力为 0。"""
        adapter = PerturbationToForceAdapter()
        for profile_type in FORCE_PROFILE_TYPES:
            assert adapter.profile_value(profile_type, t=-0.1, duration=1.0, amplitude=1.0) == pytest.approx(0.0), (
                f"{profile_type}: t=-0.1 应返回 0.0"
            )
            assert adapter.profile_value(profile_type, t=1.1, duration=1.0, amplitude=1.0) == pytest.approx(0.0), (
                f"{profile_type}: t=1.1 应返回 0.0"
            )


# ---------------------------------------------------------------------------
# 规则 → Profile 映射
# ---------------------------------------------------------------------------

class TestRuleToProfileMapping:
    """验证信号（规则）→ 力剖面类型映射。"""

    def test_rule_A_maps_to_sharp_pulse(self):
        """response_mode_rejected → sharp_pulse（在 t=0 时立即产生非零力）。"""
        adapter = PerturbationToForceAdapter()
        p = _perturbation(
            target_variable="correction_pressure",
            direction="increase",
            numeric_delta=0.10,
            source_signal="response_mode_rejected",
        )
        U_t = adapter.adapt([p])
        axis = AXIS_INDEX["correction_pressure"]
        # sharp_pulse 在 t=0 时立即到达 full amplitude → 非零力
        assert U_t[axis] > 0.0
        # 验证力值正确：0.10 * 2.0 = 0.20
        assert U_t[axis] == pytest.approx(0.20)

    def test_rule_D_maps_to_ramp(self):
        """technical_layer_needed → ramp（在 t=0 时力为零，需时间展开）。"""
        adapter = PerturbationToForceAdapter()
        p = _perturbation(
            target_variable="collaborator_layer_pressure",
            direction="increase",
            numeric_delta=0.18,
            source_signal="technical_layer_needed",
        )
        U_t = adapter.adapt([p])
        axis = AXIS_INDEX["collaborator_layer_pressure"]
        # ramp 在 t=0 时为 0
        assert U_t[axis] == pytest.approx(0.0)

        # 但在稍后时间点 ramp 应产生非零力
        force_later = adapter.profile_value(
            "ramp", t=0.3, duration=DURATION_HINT_TO_SEC["fast"], amplitude=0.36
        )
        assert force_later > 0.0

    def test_rule_F_no_observable_empty_U_t(self):
        """no_observable_field_signal → 空扰动列表 → 零力向量。"""
        adapter = PerturbationToForceAdapter()
        # 规则 F 不产生扰动 → 空列表
        U_t = adapter.adapt([])
        assert np.all(U_t == 0.0)


# ---------------------------------------------------------------------------
# 持续时间计算
# ---------------------------------------------------------------------------

class TestComputeDuration:
    """compute_duration() 返回最大 duration_hint_sec。"""

    def test_compute_duration_returns_max_hint(self):
        """多个扰动 → 最长持续时间。"""
        adapter = PerturbationToForceAdapter()
        perturbations = [
            _perturbation(
                target_variable="correction_pressure",
                duration_hint="instant",
            ),
            _perturbation(
                target_variable="service_resistance",
                duration_hint="very_slow",
            ),
            _perturbation(
                target_variable="affective_warmth",
                duration_hint="medium",
            ),
        ]
        result = adapter.compute_duration(perturbations)
        # instant=0.15, very_slow=2.00, medium=0.60 → max=2.00
        assert result == pytest.approx(2.00)

    def test_compute_duration_empty_zero(self):
        """空列表 → 0.0。"""
        adapter = PerturbationToForceAdapter()
        result = adapter.compute_duration([])
        assert result == 0.0


# ---------------------------------------------------------------------------
# 禁止导入检查
# ---------------------------------------------------------------------------

class TestForbiddenImports:
    """force_adapter.py 不得导入禁止模块。"""

    FORCE_ADAPTER_SOURCE = Path("src/field_dynamics/force_adapter.py")

    def test_no_forbidden_imports_in_force_adapter(self):
        forbidden = (
            "llm",
            "runtime",
            "renderer",
            "animation",
            "body_action",
            "motion_params",
            "motion_curve",
            "field_trace",
        )
        path = self.FORCE_ADAPTER_SOURCE
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        for module in imported:
            lowered = module.lower()
            for token in forbidden:
                assert token not in lowered, (
                    f"{path} must not import {token}: {module}"
                )


# ---------------------------------------------------------------------------
# 常量检查
# ---------------------------------------------------------------------------

class TestConstants:
    """验证硬编码常量的值。"""

    def test_delta_to_force_scale_default(self):
        """delta_to_force_scale 默认值为 2.0。"""
        adapter = PerturbationToForceAdapter()
        assert adapter.scale == 2.0

    def test_duration_hint_to_sec_mapping(self):
        """DURATION_HINT_TO_SEC 包含 5 个条目且值正确。"""
        assert len(DURATION_HINT_TO_SEC) == 5
        assert DURATION_HINT_TO_SEC["instant"] == 0.15
        assert DURATION_HINT_TO_SEC["fast"] == 0.30
        assert DURATION_HINT_TO_SEC["medium"] == 0.60
        assert DURATION_HINT_TO_SEC["slow"] == 1.00
        assert DURATION_HINT_TO_SEC["very_slow"] == 2.00

    def test_axis_index_has_10_entries(self):
        """AXIS_INDEX 包含恰好 10 个轴。"""
        assert len(AXIS_INDEX) == 10

    def test_axis_index_correct_order(self):
        """AXIS_INDEX 顺序与 10 轴索引规范一致。"""
        expected = {
            "boundary_distance": 0,
            "affective_warmth": 1,
            "structural_grip_pressure": 2,
            "correction_pressure": 3,
            "contamination_resistance": 4,
            "presence_stability": 5,
            "withdrawal_tendency": 6,
            "service_resistance": 7,
            "collaborator_layer_pressure": 8,
            "contamination_pressure": 9,
        }
        assert AXIS_INDEX == expected

    def test_force_profile_types_count(self):
        """FORCE_PROFILE_TYPES 包含恰好 6 种类型。"""
        assert len(FORCE_PROFILE_TYPES) == 6
        expected = ["impulse", "decaying_pulse", "sharp_pulse", "ramp", "persistent_step", "slow_pressure"]
        assert FORCE_PROFILE_TYPES == expected


# ---------------------------------------------------------------------------
# 轴正确性
# ---------------------------------------------------------------------------

class TestAxisAssignment:
    """验证力被分配到正确的轴上。"""

    def test_boundary_distance_axis_0(self):
        """boundary_distance → 轴 0。"""
        adapter = PerturbationToForceAdapter()
        p = _perturbation(
            target_variable="boundary_distance",
            direction="increase",
            numeric_delta=0.10,
            source_signal="boundary_pressure_present",
        )
        U_t = adapter.adapt([p])
        assert U_t[0] != 0.0
        # 验证其他轴未被影响（对角线行为）
        assert U_t[1] == 0.0

    def test_contamination_pressure_axis_9(self):
        """contamination_pressure → 轴 9。"""
        adapter = PerturbationToForceAdapter()
        p = _perturbation(
            target_variable="contamination_pressure",
            direction="increase",
            numeric_delta=0.18,
            source_signal="boundary_pressure_present",
            duration_hint="instant",
        )
        U_t = adapter.adapt([p])
        assert U_t[9] != 0.0


# ---------------------------------------------------------------------------
# 比例因子
# ---------------------------------------------------------------------------

class TestScaleFactor:
    """验证 delta → 力的比例缩放。"""

    def test_custom_scale_factor(self):
        """自定义 scale 因子正确缩放力幅值。"""
        adapter = PerturbationToForceAdapter(scale=4.0)
        p = _perturbation(
            target_variable="correction_pressure",
            direction="increase",
            numeric_delta=0.10,
            source_signal="response_mode_rejected",
        )
        U_t = adapter.adapt([p])
        axis = AXIS_INDEX["correction_pressure"]
        # 0.10 * 4.0 = 0.40
        assert U_t[axis] == pytest.approx(0.40)

    def test_extreme_delta_does_not_explode(self):
        """极端 delta 值（0.25）在默认 scale 下产生合理力值。"""
        adapter = PerturbationToForceAdapter()
        p = _perturbation(
            target_variable="correction_pressure",
            direction="increase",
            numeric_delta=0.25,
            source_signal="response_mode_rejected",
        )
        U_t = adapter.adapt([p])
        axis = AXIS_INDEX["correction_pressure"]
        # 0.25 * 2.0 = 0.50
        assert U_t[axis] == pytest.approx(0.50)
        assert U_t[axis] <= 10.0  # 不应产生极大力值


# ---------------------------------------------------------------------------
# slow_pressure 剖面
# ---------------------------------------------------------------------------

class TestSlowPressureProfile:
    """slow_pressure 剖面形状验证。"""

    def test_slow_pressure_sinusoidal(self):
        """slow_pressure 呈半正弦形状：从 0 上升再下降到 0。"""
        adapter = PerturbationToForceAdapter()
        duration = 2.0
        amp = 1.0

        v_start = adapter.profile_value("slow_pressure", t=0.0, duration=duration, amplitude=amp)
        v_mid = adapter.profile_value("slow_pressure", t=1.0, duration=duration, amplitude=amp)
        v_end = adapter.profile_value("slow_pressure", t=2.0, duration=duration, amplitude=amp)

        assert v_start == pytest.approx(0.0)
        assert v_mid == pytest.approx(amp)  # sin(pi/2) = 1
        assert v_end == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# adapt_sequence — Phase 39.6d
# ---------------------------------------------------------------------------

class TestAdaptSequence:
    """adapt_sequence() 方法 — 将扰动展开为时间力向量序列。"""

    def test_adapt_sequence_returns_correct_count(self):
        """adapt_sequence 返回 num_substeps 个力向量。"""
        adapter = PerturbationToForceAdapter()
        p = _perturbation(
            target_variable="correction_pressure",
            direction="increase",
            numeric_delta=0.10,
            source_signal="response_mode_rejected",
        )
        sequence = adapter.adapt_sequence([p], dt=1.0, num_substeps=10)
        assert len(sequence) == 10
        for U_t in sequence:
            assert U_t.shape == (10,)

    def test_adapt_sequence_forces_bounded(self):
        """adapt_sequence 每个子步的力有界（< 10.0）。"""
        adapter = PerturbationToForceAdapter()
        p = _perturbation(
            target_variable="correction_pressure",
            direction="increase",
            numeric_delta=0.25,
            source_signal="response_mode_rejected",
        )
        sequence = adapter.adapt_sequence([p], dt=2.0, num_substeps=20)
        for U_t in sequence:
            assert np.all(np.isfinite(U_t))
            assert np.max(np.abs(U_t)) < 10.0

    def test_adapt_sequence_consistent_with_adapt_at_t0(self):
        """adapt_sequence 的第一个力向量应与 adapt() 在 t=0 一致（非 ramp/slow 剖面）。"""
        adapter = PerturbationToForceAdapter()
        p = _perturbation(
            target_variable="correction_pressure",
            direction="increase",
            numeric_delta=0.10,
            source_signal="response_mode_rejected",
        )
        U_t_single = adapter.adapt([p])
        sequence = adapter.adapt_sequence([p], dt=1.0, num_substeps=10)
        # sharp_pulse 在 t=0 时满幅值，与 adapt 一致
        axis = AXIS_INDEX["correction_pressure"]
        assert sequence[0][axis] == pytest.approx(U_t_single[axis])

    def test_sharp_pulse_only_early_substeps(self):
        """sharp_pulse 只在早期子步为非零。"""
        adapter = PerturbationToForceAdapter()
        p = _perturbation(
            target_variable="correction_pressure",
            direction="increase",
            numeric_delta=0.10,
            source_signal="response_mode_rejected",
            duration_hint="medium",  # duration=0.60
        )
        sequence = adapter.adapt_sequence([p], dt=1.0, num_substeps=20)
        axis = AXIS_INDEX["correction_pressure"]
        # sharp_pulse: pulse_width = 0.15 * 0.60 = 0.09，有效子步 dt = 1.0/20 = 0.05
        # 前 2 个子步 (t=0.0, t=0.05) 应非零，之后为零
        nonzero_indices = [i for i, U in enumerate(sequence) if abs(U[axis]) > 1e-9]
        assert len(nonzero_indices) > 0
        # 应只在前面一小部分非零
        assert nonzero_indices[-1] < len(sequence) * 0.5, (
            "sharp_pulse 应只在早期子步为非零"
        )

    def test_decaying_pulse_decreases_over_time(self):
        """decaying_pulse 序列随时间的幅值单调递减。"""
        adapter = PerturbationToForceAdapter()
        p = _perturbation(
            target_variable="affective_warmth",
            direction="increase",
            numeric_delta=0.10,
            source_signal="boundary_pressure_present",
            duration_hint="medium",  # duration=0.60
        )
        # default fallback is decaying_pulse
        # But boundary_pressure_present → persistent_step
        # Let's use unknown signal → decaying_pulse fallback
        p2 = _perturbation(
            target_variable="affective_warmth",
            direction="increase",
            numeric_delta=0.10,
            source_signal="unknown_fallback_test",
        )
        sequence = adapter.adapt_sequence([p2], dt=1.0, num_substeps=20)
        axis = AXIS_INDEX["affective_warmth"]
        vals = [U[axis] for U in sequence]
        # 在非零部分上应单调递减
        nonzero_vals = [v for v in vals if abs(v) > 1e-9]
        if len(nonzero_vals) >= 2:
            assert all(
                nonzero_vals[i] >= nonzero_vals[i + 1]
                for i in range(len(nonzero_vals) - 1)
            ), "decaying_pulse 应单调递减"

    def test_slow_pressure_persists(self):
        """slow_pressure 在较长时间内持续非零。"""
        adapter = PerturbationToForceAdapter()
        p = _perturbation(
            target_variable="contamination_resistance",
            direction="increase",
            numeric_delta=0.05,
            source_signal="source_material_must_not_be_sanitized",
            duration_hint="very_slow",  # duration=2.00
        )
        sequence = adapter.adapt_sequence([p], dt=3.0, num_substeps=30)
        axis = AXIS_INDEX["contamination_resistance"]
        nonzero_count = sum(1 for U in sequence if abs(U[axis]) > 1e-9)
        # slow_pressure 应持续大部分 duration
        assert nonzero_count > len(sequence) * 0.3, (
            f"slow_pressure 应持续较长时间，但仅 {nonzero_count}/{len(sequence)} 子步非零"
        )

    def test_decrease_direction_negative_force_in_sequence(self):
        """decrease 方向在序列中产生负力。"""
        adapter = PerturbationToForceAdapter()
        p = _perturbation(
            target_variable="affective_warmth",
            direction="decrease",
            numeric_delta=-0.05,
            source_signal="boundary_pressure_present",
        )
        sequence = adapter.adapt_sequence([p], dt=1.0, num_substeps=10)
        axis = AXIS_INDEX["affective_warmth"]
        # 检查第一个非零子步是否含负值
        any_negative = any(U[axis] < -1e-9 for U in sequence)
        assert any_negative, "decrease 方向应产生负力"


# ---------------------------------------------------------------------------
# adapt_sequence 边界情况
# ---------------------------------------------------------------------------

class TestAdaptSequenceEdgeCases:
    """adapt_sequence() 边界情况。"""

    def test_zero_dt_empty_returns_single_zero(self):
        """空扰动且 dt <= 0 返回 [np.zeros(10)]。"""
        adapter = PerturbationToForceAdapter()
        sequence = adapter.adapt_sequence([], dt=0.0, num_substeps=10)
        assert len(sequence) == 1
        assert np.all(sequence[0] == 0.0)

    def test_zero_substeps_returns_single_zero(self):
        """num_substeps <= 0 返回 [np.zeros(10)]。"""
        adapter = PerturbationToForceAdapter()
        p = _perturbation(
            target_variable="correction_pressure",
            direction="increase",
            numeric_delta=0.10,
        )
        sequence = adapter.adapt_sequence([p], dt=1.0, num_substeps=0)
        assert len(sequence) == 1
        assert np.all(sequence[0] == 0.0)

    def test_empty_perturbations_zero_sequence(self):
        """空扰动列表 → 全部零向量序列。"""
        adapter = PerturbationToForceAdapter()
        sequence = adapter.adapt_sequence([], dt=1.0, num_substeps=5)
        assert len(sequence) == 5
        for U_t in sequence:
            assert np.all(U_t == 0.0)

    def test_stabilize_perturbation_no_force(self):
        """stabilize 扰动不在序列中产生力。"""
        adapter = PerturbationToForceAdapter()
        p = _perturbation(
            target_variable="presence_stability",
            direction="stabilize",
            numeric_delta=0.0,
        )
        sequence = adapter.adapt_sequence([p], dt=1.0, num_substeps=5)
        for U_t in sequence:
            assert np.all(U_t == 0.0)
