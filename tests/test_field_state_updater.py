"""测试 FieldStateUpdater — Phase 31 场更新器。

验证：
1. 弛豫向基线移动
2. 扰动按变量聚合并正确应用
3. 钳制到 [0.0, 1.0]
4. 波段重新计算
5. 输入不变异
6. 无禁止模块导入
"""

from __future__ import annotations

import ast
import copy

import pytest

from src.field_state.schema import (
    RelationalFieldState,
    FieldVariable,
    F_0,
    create_ground_state_variables,
)
from src.field_state.perturbation import FieldPerturbation, _compute_delta
from src.field_state.updater import FieldStateUpdater, DECAY_RATES, BAND_BOUNDARIES


# ---------------------------------------------------------------------------
# 测试辅助函数
# ---------------------------------------------------------------------------

def _make_test_state(**overrides) -> RelationalFieldState:
    """使用合理默认值构建 RelationalFieldState。

    overrides: 一个字典，将字段变量名映射到 (numeric_value, value_band) 元组。
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
    magnitude_band: str = "low",
    duration_hint: str = "medium",
    **kwargs,
) -> FieldPerturbation:
    """使用最小字段创建 FieldPerturbation。"""
    return FieldPerturbation(
        target_variable=target,
        direction=direction,
        magnitude_band=magnitude_band,
        numeric_delta=_compute_delta(direction, magnitude_band),
        duration_hint=duration_hint,
        source_signal="test_signal",
        rationale=kwargs.get("rationale", "测试扰动"),
        evidence_sources=kwargs.get("evidence_sources", []),
    )


# ---------------------------------------------------------------------------
# 测试 FieldStateUpdater
# ---------------------------------------------------------------------------

class TestFieldStateUpdater:
    """测试 FieldStateUpdater 更新逻辑。"""

    def test_relaxation_moves_toward_baseline(self):
        """测试 1：无扰动时，值向基线衰减。"""
        updater = FieldStateUpdater()
        # contamination_pressure baseline=0.0, decay=instant(1.00)
        # 设为 elevated (0.60) → 一个回合后应回到 0.0
        state = _make_test_state(contamination_pressure=(0.60, "elevated"))
        new_state = updater.update(state, [])

        new_var = new_state.variables["contamination_pressure"]
        assert new_var.numeric_value == pytest.approx(0.0, abs=0.001)
        assert new_var.value == "low"

    def test_relaxation_happens_before_perturbation(self):
        """测试 2：弛豫发生在扰动之前。

        设 current > baseline，并应用增加它的扰动。
        最终值 = relaxed + delta，而非 current + delta。
        """
        updater = FieldStateUpdater()
        # structural_grip_pressure: baseline=0.05, decay=fast(0.45)
        # 设为 0.50 (elevated)
        state = _make_test_state(structural_grip_pressure=(0.50, "elevated"))

        # 施加 +0.10 扰动
        p = _make_perturbation("structural_grip_pressure", "increase", "medium")
        new_state = updater.update(state, [p])

        # 弛豫: 0.50 + 0.45*(0.05-0.50) = 0.50 - 0.2025 = 0.2975
        # 扰动后: 0.2975 + 0.10 = 0.3975
        # current + delta 会是 0.50 + 0.10 = 0.60
        new_val = new_state.variables["structural_grip_pressure"].numeric_value
        assert new_val == pytest.approx(0.3975, abs=0.01)
        # 应小于 current + delta
        assert new_val < 0.60

    def test_perturbation_increases_variable(self):
        """测试 3：正扰动增加变量值。"""
        updater = FieldStateUpdater()
        state = _make_test_state(structural_grip_pressure=(0.10, "low"))
        old_val = state.variables["structural_grip_pressure"].numeric_value

        p = _make_perturbation("structural_grip_pressure", "increase", "medium")
        new_state = updater.update(state, [p])
        new_val = new_state.variables["structural_grip_pressure"].numeric_value

        # 弛豫后 + 0.10 应 > 弛豫前
        # 确认新值大于旧值
        assert new_val > old_val

    def test_perturbation_decreases_variable(self):
        """测试 4：负扰动减少变量值。"""
        updater = FieldStateUpdater()
        state = _make_test_state(boundary_distance=(0.60, "elevated"))
        old_val = state.variables["boundary_distance"].numeric_value

        p = _make_perturbation("boundary_distance", "decrease", "low")
        new_state = updater.update(state, [p])
        new_val = new_state.variables["boundary_distance"].numeric_value

        # 弛豫后 - 0.05 应 < 旧值
        assert new_val < old_val

    def test_clamp_prevents_values_below_zero(self):
        """测试 5：钳制阻止值低于 0.0。"""
        updater = FieldStateUpdater()
        # 极低 current + 大负扰动
        state = _make_test_state(boundary_distance=(0.02, "low"))

        p = _make_perturbation("boundary_distance", "decrease", "high")
        new_state = updater.update(state, [p])
        new_val = new_state.variables["boundary_distance"].numeric_value

        assert new_val >= 0.0

    def test_clamp_prevents_values_above_one(self):
        """测试 6：钳制阻止值超过 1.0。"""
        updater = FieldStateUpdater()
        # 极高 current + 大正扰动
        state = _make_test_state(presence_stability=(0.95, "saturated"))

        p = _make_perturbation("presence_stability", "increase", "high")
        new_state = updater.update(state, [p])
        new_val = new_state.variables["presence_stability"].numeric_value

        assert new_val <= 1.0

    def test_contamination_pressure_decays_strongly(self):
        """测试 7：contamination_pressure（instant decay, 1.00）在一个回合内完全返回基线。"""
        updater = FieldStateUpdater()
        # contamination_pressure baseline=0.0, decay=instant(1.00)
        state = _make_test_state(contamination_pressure=(0.80, "high"))
        new_state = updater.update(state, [])

        new_var = new_state.variables["contamination_pressure"]
        assert new_var.numeric_value == pytest.approx(0.0, abs=0.001)
        assert new_var.value == "low"

    def test_different_decay_profiles_behave_differently(self):
        """测试 8：不同衰减率产生不同弛豫速度。"""
        updater = FieldStateUpdater()
        # structural_grip_pressure: decay=fast(0.45), baseline=0.05
        # presence_stability: decay=very_slow(0.04), baseline=0.80

        state = _make_test_state(
            structural_grip_pressure=(0.50, "elevated"),
            presence_stability=(0.50, "baseline"),
        )

        new_state = updater.update(state, [])

        # structural_grip: 弛豫 = 0.50 + 0.45*(0.05-0.50) = 0.50 - 0.2025 = 0.2975
        #   变化 = -0.2025
        sg_new = new_state.variables["structural_grip_pressure"].numeric_value
        sg_change = abs(sg_new - 0.50)

        # presence_stability: 弛豫 = 0.50 + 0.04*(0.80-0.50) = 0.50 + 0.012 = 0.512
        #   变化 = +0.012
        ps_new = new_state.variables["presence_stability"].numeric_value
        ps_change = abs(ps_new - 0.50)

        # fast(0.45) 应比 very_slow(0.04) 变化更大
        assert sg_change > ps_change

    def test_band_values_updated_after_update(self):
        """测试 9：扰动后波段值正确更新。"""
        updater = FieldStateUpdater()
        # structural_grip_pressure baseline=0.05, current=0.12 ("low")
        state = _make_test_state(structural_grip_pressure=(0.12, "low"))

        # 施加 +0.10 扰动（medium）
        p = _make_perturbation("structural_grip_pressure", "increase", "medium")
        new_state = updater.update(state, [p])

        # 弛豫: 0.12 + 0.45*(0.05-0.12) = 0.12 - 0.0315 = 0.0885
        # 扰动后: 0.0885 + 0.10 = 0.1885
        # 这应该还是 "low" (≤0.20)
        # 如果从 0.50 开始：0.50 + 0.45*(0.05-0.50) = 0.2975; +0.10 = 0.3975 → "baseline"
        new_var = new_state.variables["structural_grip_pressure"]
        # 数值应在 0.0885 + 0.10 = 0.1885 附近
        assert new_var.value == "low"

        # 用更高的 current 测试波段跃迁
        state2 = _make_test_state(structural_grip_pressure=(0.50, "elevated"))
        new_state2 = updater.update(state2, [p])
        new_var2 = new_state2.variables["structural_grip_pressure"]
        # 弛豫: 0.50 + 0.45*(0.05-0.50) = 0.2975; + 0.10 = 0.3975
        assert new_var2.value == "baseline"
        assert new_var2.numeric_value == pytest.approx(0.3975, abs=0.01)

    def test_input_state_not_mutated(self):
        """测试 10：调用 update() 后，原始状态对象不变异。"""
        updater = FieldStateUpdater()
        state = _make_test_state(
            structural_grip_pressure=(0.50, "elevated"),
            contamination_pressure=(0.30, "baseline"),
        )

        # 深拷贝以获取调用前的快照
        original_values = {
            name: (var.numeric_value, var.value)
            for name, var in state.variables.items()
        }

        # 应用扰动
        perturbations = [
            _make_perturbation("structural_grip_pressure", "decrease", "low"),
            _make_perturbation("boundary_distance", "increase", "medium"),
        ]
        _ = updater.update(state, perturbations)

        # 检查原始状态变量未变化
        for name, var in state.variables.items():
            orig = original_values[name]
            assert var.numeric_value == orig[0], f"{name} numeric_value mutated"
            assert var.value == orig[1], f"{name} value mutated"

    def test_updater_does_not_import_forbidden_modules(self):
        """测试 11：updater.py 不导入任何禁止模块。"""
        import ast

        updater_path = "src/field_state/updater.py"
        with open(updater_path, "r", encoding="utf-8") as f:
            source = f.read()

        tree = ast.parse(source)

        forbidden = {
            "body", "runtime", "language", "llm",
            "router", "memory", "renderer", "avatar",
        }

        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name.lower()
                    if any(fb in name for fb in forbidden):
                        violations.append(f"import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = (node.module or "").lower()
                if any(fb in module for fb in forbidden):
                    violations.append(f"from {node.module} import ...")

        assert len(violations) == 0, f"Found forbidden imports: {violations}"

    def test_multiple_perturbations_same_variable_sum_deltas(self):
        """多个针对同一变量的扰动应正确求和所有增量。"""
        updater = FieldStateUpdater()
        state = _make_test_state(affective_warmth=(0.35, "baseline"))

        # 两个增加扰动：+0.05 和 +0.05 = +0.10
        p1 = _make_perturbation("affective_warmth", "increase", "low")
        p2 = _make_perturbation("affective_warmth", "increase", "low")
        new_state = updater.update(state, [p1, p2])

        # affective_warmth baseline=0.35, decay=medium(0.25)
        # 弛豫: 0.35 + 0.25*(0.35-0.35) = 0.35 (已在基线)
        # 扰动后: 0.35 + 0.05 + 0.05 = 0.45
        new_val = new_state.variables["affective_warmth"].numeric_value
        assert new_val == pytest.approx(0.45, abs=0.01)

    def test_empty_perturbation_list_only_relaxes(self):
        """空扰动列表：仅弛豫，不应用增量。"""
        updater = FieldStateUpdater()
        # contamination_pressure: baseline=0.0, decay=instant(1.00)
        state = _make_test_state(contamination_pressure=(0.70, "high"))
        new_state = updater.update(state, [])

        new_var = new_state.variables["contamination_pressure"]
        assert new_var.numeric_value == pytest.approx(0.0, abs=0.001)
        assert new_var.value == "low"

    def test_state_note_is_updated(self):
        """验证 state_note 包含更新描述。"""
        updater = FieldStateUpdater()
        state = _make_test_state()
        new_state = updater.update(state, [
            _make_perturbation("boundary_distance", "increase", "low"),
        ])
        assert "FieldStateUpdater" in new_state.state_note
        assert "1 perturbations" in new_state.state_note

    def test_unknown_decay_profile_falls_back_to_medium(self):
        """如果衰减率键缺失，应优雅降级为 medium(0.25)。"""
        # 创建一个没有某些键的自定义衰减率字典
        custom_rates = {"instant": 1.00}  # 缺失 'medium', 'slow' 等
        updater = FieldStateUpdater(decay_rates=custom_rates)

        # structural_grip_pressure has decay_profile='fast', not in custom_rates
        # 应回退到 'medium' (不在 custom_rates 中，但 DEFAULT 表中有)
        # 实际上 fallback 链是：先查 custom；若不在则查 DECAY_RATES.get("medium", 0.25)
        state = _make_test_state(structural_grip_pressure=(0.50, "elevated"))
        new_state = updater.update(state, [])

        # 如果 fallback 到 medium(0.25):
        # 弛豫: 0.50 + 0.25*(0.05-0.50) = 0.50 - 0.1125 = 0.3875
        # 如果仍然用了 fast(0.45)（bug）：0.50 - 0.2025 = 0.2975
        new_val = new_state.variables["structural_grip_pressure"].numeric_value
        assert new_val == pytest.approx(0.3875, abs=0.01)
