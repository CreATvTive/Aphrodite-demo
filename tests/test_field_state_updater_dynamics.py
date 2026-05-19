"""测试 FieldStateUpdater 动态诊断 — Phase P0 数学强化。

验证：
1. 零扰动弛豫（向基线单调移动）
2. 重复同轴正扰动（加性累积可见性）
3. 重复同轴负扰动（向低钳制边界）
4. 交替扰动（带跃迁可观察性）
5. 钳制边界诊断（前钳制值、增量求和、钳制量、钳制激活）
6. 同轴扰动计数（诊断暴露每轴命中数）
7. 饱和条纹 / 重复钳制可见性
8. 无禁止导入（无 LLM、渲染器、运行时、内存、语言生成、行为执行模块）

重要限制：
- 不替换求和、饱和度、最大值、平均值或竞争。
- 添加的诊断仅限审计用途——不影响行为。
- 旧的行为必须保持不变。
- 行为影响路径不得修改。
"""

from __future__ import annotations

import ast
import copy

import pytest

from src.field_state.schema import (
    F_0,
    RelationalFieldState,
    FieldVariable,
    create_ground_state_variables,
)
from src.field_state.perturbation import FieldPerturbation, _compute_delta
from src.field_state.updater import FieldStateUpdater, DECAY_RATES


# ---------------------------------------------------------------------------
# 测试辅助函数
# ---------------------------------------------------------------------------

def _make_test_state(**overrides) -> RelationalFieldState:
    """使用合理默认值构建 RelationalFieldState。

    overrides: 将字段变量名映射到 (numeric_value, value_band) 的字典。
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
# DC-1: 零扰动弛豫
# ---------------------------------------------------------------------------

class TestZeroPerturbationRelaxation:
    """验证无扰动时间向基线的单调解弛豫。"""

    def test_above_baseline_relaxes_downward(self):
        """高于基线的值应单调向下弛豫。"""
        updater = FieldStateUpdater()
        # structural_grip_pressure: baseline=0.05, decay=fast(0.45)
        state = _make_test_state(structural_grip_pressure=(0.80, "high"))
        new_state, trace = updater.update_with_trace(state, [])

        new_val = new_state.variables["structural_grip_pressure"].numeric_value
        old_val = 0.80
        baseline = 0.05
        decay = 0.45
        # relaxed = 0.80 + 0.45*(0.05 - 0.80) = 0.80 - 0.3375 = 0.4625
        expected = 0.80 + decay * (baseline - 0.80)
        assert new_val == pytest.approx(expected, abs=0.01)
        assert new_val < old_val  # 单调下降
        # 诊断追踪应显示弛豫后的值
        assert trace["per_axis"]["structural_grip_pressure"]["relaxed"] == pytest.approx(expected, abs=0.01)

    def test_below_baseline_relaxes_upward(self):
        """低于基线的值应单调向上弛豫。"""
        updater = FieldStateUpdater()
        # presence_stability: baseline=0.80, decay=very_slow(0.04)
        state = _make_test_state(presence_stability=(0.30, "baseline"))
        new_state, trace = updater.update_with_trace(state, [])

        new_val = new_state.variables["presence_stability"].numeric_value
        old_val = 0.30
        baseline = 0.80
        decay = 0.04
        expected = old_val + decay * (baseline - old_val)
        assert new_val == pytest.approx(expected, abs=0.01)
        assert new_val > old_val  # 单调上升

    def test_at_baseline_stays_at_baseline(self):
        """已在基线的值应保持不动（零增量）。"""
        updater = FieldStateUpdater()
        # affective_warmth: baseline=0.35, decay=medium(0.25)
        state = _make_test_state(affective_warmth=(0.35, "baseline"))
        new_state, trace = updater.update_with_trace(state, [])

        new_val = new_state.variables["affective_warmth"].numeric_value
        assert new_val == pytest.approx(0.35, abs=0.001)

    def test_previous_state_not_mutated(self):
        """update_with_trace 不得变异先前的状态对象。"""
        updater = FieldStateUpdater()
        state = _make_test_state(
            structural_grip_pressure=(0.80, "high"),
            contamination_pressure=(0.30, "baseline"),
        )

        original_values = {
            name: (var.numeric_value, var.value)
            for name, var in state.variables.items()
        }

        _new_state, _trace = updater.update_with_trace(state, [])

        for name, var in state.variables.items():
            orig = original_values[name]
            assert var.numeric_value == orig[0], f"{name} 数值被变异"
            assert var.value == orig[1], f"{name} 值被变异"


# ---------------------------------------------------------------------------
# DC-2/DC-3: 重复同轴正/负扰动
# ---------------------------------------------------------------------------

class TestRepeatedSameAxisPerturbation:
    """验证重复同轴扰动的加性累积是否可见。"""

    def test_repeated_positive_same_axis_visible_in_delta_sum(self):
        """同一轴上的多次正扰动应在诊断追踪中显示全部增量。"""
        updater = FieldStateUpdater()
        state = _make_test_state(affective_warmth=(0.35, "baseline"))

        p1 = _make_perturbation("affective_warmth", "increase", "low")   # +0.05
        p2 = _make_perturbation("affective_warmth", "increase", "medium") # +0.10
        p3 = _make_perturbation("affective_warmth", "increase", "low")   # +0.05

        new_state, trace = updater.update_with_trace(state, [p1, p2, p3])

        per_axis = trace["per_axis"]["affective_warmth"]
        assert per_axis["delta_sum"] == pytest.approx(0.20, abs=0.001)
        # pre_clamp 应反映完整求和（若未触发钳制）
        assert per_axis["pre_clamp_value"] == pytest.approx(0.35 + 0.20, abs=0.01)

        # 新值应反映累积
        new_val = new_state.variables["affective_warmth"].numeric_value
        assert new_val == pytest.approx(0.55, abs=0.01)

    def test_repeated_positive_approaches_clamp_boundary(self):
        """多次正扰动应推动前钳制值接近或超过钳制边界。"""
        updater = FieldStateUpdater()
        # correction_pressure: baseline=0.00, decay=medium(0.25)
        # 从 0.60 开始，施加 3 次高扰动
        state = _make_test_state(correction_pressure=(0.60, "elevated"))

        perturbations = [
            _make_perturbation("correction_pressure", "increase", "high")  # +0.18
            for _ in range(3)
        ]

        _new_state, trace = updater.update_with_trace(state, perturbations)

        per_axis = trace["per_axis"]["correction_pressure"]
        # 弛豫: 0.60 + 0.25*(0.00 - 0.60) = 0.45
        # 3 * 0.18 = 0.54
        # pre_clamp = 0.45 + 0.54 = 0.99
        assert per_axis["relaxed"] == pytest.approx(0.45, abs=0.01)
        assert per_axis["delta_sum"] == pytest.approx(0.54, abs=0.001)
        assert per_axis["pre_clamp_value"] == pytest.approx(0.99, abs=0.01)
        # 前钳制值非常接近边界 —— 若再加一个扰动，将触发钳制

    def test_repeated_negative_pushes_toward_lower_boundary(self):
        """多次负扰动应朝低钳制边界推动。"""
        updater = FieldStateUpdater()
        # affective_warmth: baseline=0.35, decay=medium(0.25)
        # 从 0.40 开始，施加多次减少扰动
        state = _make_test_state(affective_warmth=(0.40, "baseline"))

        perturbations = [
            _make_perturbation("affective_warmth", "decrease", "high")  # -0.18
            for _ in range(3)
        ]

        _new_state, trace = updater.update_with_trace(state, perturbations)

        per_axis = trace["per_axis"]["affective_warmth"]
        # 弛豫: 0.40 + 0.25*(0.35 - 0.40) = 0.3875
        # 3 * (-0.18) = -0.54
        # pre_clamp = 0.3875 - 0.54 = -0.1525 → clamp → 0.0
        assert per_axis["relaxed"] == pytest.approx(0.3875, abs=0.01)
        assert per_axis["delta_sum"] == pytest.approx(-0.54, abs=0.001)
        assert per_axis["pre_clamp_value"] == pytest.approx(-0.1525, abs=0.01)
        assert per_axis["clamp_activated"] is True
        # clamp_amount = pre_clamp - new_value = -0.1525 - 0.0 = -0.1525（有符号）
        # 幅度为 |clamp_amount| = 0.1525
        assert abs(per_axis["clamp_amount"]) == pytest.approx(0.1525, abs=0.01)
        assert per_axis["clamp_amount"] < 0.0  # 从下方钳制 → 负值

    def test_same_axis_perturbation_count_exposed(self):
        """诊断追踪应暴露每个轴接收到的扰动数量。"""
        updater = FieldStateUpdater()
        state = _make_test_state()

        perturbations = [
            _make_perturbation("boundary_distance", "increase", "low"),
            _make_perturbation("boundary_distance", "increase", "low"),
            _make_perturbation("affective_warmth", "decrease", "medium"),
        ]

        _new_state, trace = updater.update_with_trace(state, perturbations)

        counts = trace["same_axis_perturbation_count"]
        assert counts.get("boundary_distance", 0) == 2
        assert counts.get("affective_warmth", 0) == 1
        # 未瞄准的轴应为 0 或不存在
        assert counts.get("presence_stability", 0) == 0


# ---------------------------------------------------------------------------
# DC-4: 交替扰动
# ---------------------------------------------------------------------------

class TestAlternatingPerturbations:
    """验证交替正负扰动不会无声地显示为"稳定"。"""

    def test_alternating_perturbations_visible_in_trace(self):
        """交替扰动应在诊断追踪的 pre_clamp_value 中产生可观察的摆动。"""
        updater = FieldStateUpdater()
        state = _make_test_state(affective_warmth=(0.35, "baseline"))

        # 第 1 轮：+0.18
        p_pos = _make_perturbation("affective_warmth", "increase", "high")
        s1, t1 = updater.update_with_trace(state, [p_pos])
        val1 = t1["per_axis"]["affective_warmth"]["pre_clamp_value"]

        # 第 2 轮：-0.18（从 s1 开始）
        p_neg = _make_perturbation("affective_warmth", "decrease", "high")
        s2, t2 = updater.update_with_trace(s1, [p_neg])
        val2 = t2["per_axis"]["affective_warmth"]["pre_clamp_value"]

        # 第 3 轮：+0.18（从 s2 开始）
        s3, t3 = updater.update_with_trace(s2, [p_pos])
        val3 = t3["per_axis"]["affective_warmth"]["pre_clamp_value"]

        # 前钳制值应摆动——不是常数
        # 变化幅度应超过 0.05（否则被弛豫掩盖）
        assert abs(val1 - val2) > 0.05, f"交替扰动未产生可见摆动: {val1} → {val2}"
        assert abs(val2 - val3) > 0.05, f"交替扰动未产生可见摆动: {val2} → {val3}"

    def test_band_transitions_observable_under_alternation(self):
        """当交替扰动越过带边界时，带跃迁应可观察。"""
        updater = FieldStateUpdater()
        # structural_grip_pressure: baseline=0.05, decay=fast(0.45)
        # 波段: ≤0.20=low, ≤0.50=baseline, ≤0.70=elevated, ≤0.90=high
        # 从接近边界开始：0.48 ("baseline")

        state = _make_test_state(structural_grip_pressure=(0.48, "baseline"))
        # 施加强扰动 → 应越过 0.50 边界
        p_up = _make_perturbation("structural_grip_pressure", "increase", "high")
        s1, _ = updater.update_with_trace(state, [p_up])
        band1 = s1.variables["structural_grip_pressure"].value

        # 施加负扰动 → 应回落
        p_down = _make_perturbation("structural_grip_pressure", "decrease", "high")
        s2, _ = updater.update_with_trace(s1, [p_down])
        band2 = s2.variables["structural_grip_pressure"].value

        # 至少发生一次跃迁
        bands_seen = {band1, band2}
        assert len(bands_seen) >= 2 or band1 != "baseline", (
            f"未检测到带跃迁: {band1} → {band2}"
        )

    def test_alternation_produces_measurable_drift_due_to_relax_order(self):
        """先弛豫后扰动的顺序使得交替正负扰动产生小幅可测量的漂移。

        这不是一个错误——它是先弛豫后扰动顺序的已记录数学后果。
        扰动的净和为零，弛豫始终将值向基线拉动。
        但在偶数轮中，交替扰动与衰减速率交互，
        可能产生近 0.05 的漂移。此测试确保漂移数量级
        保持可观察而非破坏性。
        """
        updater = FieldStateUpdater()
        state = _make_test_state(affective_warmth=(0.35, "baseline"))

        current = state
        for _ in range(4):
            p_pos = _make_perturbation("affective_warmth", "increase", "medium")
            current, _ = updater.update_with_trace(current, [p_pos])
            p_neg = _make_perturbation("affective_warmth", "decrease", "medium")
            current, _ = updater.update_with_trace(current, [p_neg])

        final_val = current.variables["affective_warmth"].numeric_value
        # 漂移距基线不得超过 0.08（衰减率 = 0.25 时，4 个周期后）
        assert abs(final_val - 0.35) < 0.08, (
            f"交替漂移过大: final={final_val:.4f}, baseline=0.35"
        )
        # 记录漂移方向以供审计
        # 当前顺序（先弛豫后扰动）轻微有利于下行漂移


# ---------------------------------------------------------------------------
# DC-5: 钳制边界诊断
# ---------------------------------------------------------------------------

class TestClampBoundaryDiagnostics:
    """验证诊断追踪暴露前钳制状态，而不改变行为。"""

    def test_pre_clamp_value_exposed_in_trace(self):
        """每个轴的前钳制值应在诊断追踪中可见。"""
        updater = FieldStateUpdater()
        state = _make_test_state(presence_stability=(0.95, "saturated"))

        p = _make_perturbation("presence_stability", "increase", "high")
        _new_state, trace = updater.update_with_trace(state, [p])

        per_axis = trace["per_axis"]["presence_stability"]
        assert "pre_clamp_value" in per_axis
        assert per_axis["pre_clamp_value"] > 1.0  # 前钳制超过边界

    def test_clamp_amount_exposed(self):
        """钳制量（钳制损失的幅度）应暴露。"""
        updater = FieldStateUpdater()
        state = _make_test_state(presence_stability=(0.95, "saturated"))

        p = _make_perturbation("presence_stability", "increase", "high")
        _new_state, trace = updater.update_with_trace(state, [p])

        per_axis = trace["per_axis"]["presence_stability"]
        assert "clamp_amount" in per_axis
        assert per_axis["clamp_amount"] > 0.0
        assert per_axis["clamp_activated"] is True

    def test_clamp_not_activated_when_within_bounds(self):
        """当值在 [0,1] 范围内时，clamp_activated 应为 False。"""
        updater = FieldStateUpdater()
        state = _make_test_state(affective_warmth=(0.40, "baseline"))

        p = _make_perturbation("affective_warmth", "increase", "low")  # +0.05
        _new_state, trace = updater.update_with_trace(state, [p])

        per_axis = trace["per_axis"]["affective_warmth"]
        assert per_axis["clamp_activated"] is False
        assert per_axis["clamp_amount"] == pytest.approx(0.0, abs=1e-9)

    def test_delta_sum_exposed_separately(self):
        """增量求和应独立于弛豫值暴露。"""
        updater = FieldStateUpdater()
        state = _make_test_state(boundary_distance=(0.50, "baseline"))

        perturbations = [
            _make_perturbation("boundary_distance", "increase", "low"),    # +0.05
            _make_perturbation("boundary_distance", "decrease", "medium"), # -0.10
            _make_perturbation("boundary_distance", "increase", "low"),   # +0.05
        ]

        _new_state, trace = updater.update_with_trace(state, perturbations)
        per_axis = trace["per_axis"]["boundary_distance"]

        # 求和 = 0.05 - 0.10 + 0.05 = 0.00
        assert per_axis["delta_sum"] == pytest.approx(0.0, abs=1e-9)
        # 弛豫应与 delta_sum 分离
        assert "relaxed" in per_axis

    def test_saturated_axes_listed_in_trace(self):
        """追踪应列出所有发生钳制的轴。"""
        updater = FieldStateUpdater()
        state = _make_test_state(
            boundary_distance=(0.02, "low"),
            presence_stability=(0.95, "saturated"),
        )

        perturbations = [
            _make_perturbation("boundary_distance", "decrease", "high"),  # 推至 < 0
            _make_perturbation("presence_stability", "increase", "high"),  # 推至 > 1
        ]

        _new_state, trace = updater.update_with_trace(state, perturbations)

        saturated = trace["saturated_axes"]
        assert "boundary_distance" in saturated
        assert "presence_stability" in saturated


# ---------------------------------------------------------------------------
# DC-6: 同轴扰动计数
# ---------------------------------------------------------------------------

class TestSameAxisPerturbationCount:
    """验证诊断追踪暴露每个轴接收到的扰动数量。"""

    def test_count_matches_number_of_perturbations(self):
        """每个轴的计数值应等于针对该轴的扰动数量。"""
        updater = FieldStateUpdater()
        state = _make_test_state()

        perturbations = [
            _make_perturbation("affective_warmth", "increase", "low"),
            _make_perturbation("affective_warmth", "increase", "low"),
            _make_perturbation("affective_warmth", "decrease", "medium"),
            _make_perturbation("boundary_distance", "increase", "high"),
        ]

        _new_state, trace = updater.update_with_trace(state, perturbations)

        counts = trace["same_axis_perturbation_count"]
        assert counts.get("affective_warmth") == 3
        assert counts.get("boundary_distance") == 1

    def test_zero_count_for_untargeted_axes(self):
        """未瞄准的轴应显示为 0 或不存在。"""
        updater = FieldStateUpdater()
        state = _make_test_state()

        perturbations = [
            _make_perturbation("structural_grip_pressure", "increase", "low"),
        ]

        _new_state, trace = updater.update_with_trace(state, perturbations)

        counts = trace["same_axis_perturbation_count"]
        # 总扰动计数应与输入长度匹配
        assert trace["total_perturbations"] == 1
        # 未涉及的轴应为 0
        for axis in ["affective_warmth", "presence_stability", "boundary_distance"]:
            assert counts.get(axis, 0) == 0

    def test_single_perturbation_count_is_one(self):
        """单个扰动应计数为 1。"""
        updater = FieldStateUpdater()
        state = _make_test_state()

        p = _make_perturbation("withdrawal_tendency", "decrease", "low")
        _new_state, trace = updater.update_with_trace(state, [p])

        assert trace["same_axis_perturbation_count"]["withdrawal_tendency"] == 1


# ---------------------------------------------------------------------------
# DC-7: 饱和条纹 / 重复钳制可见性
# ---------------------------------------------------------------------------

class TestSaturationStreakVisibility:
    """验证连续饱和通过诊断计数器可见。"""

    def test_consecutive_saturation_count_increments(self):
        """连续 n 次钳制后，计数器应递增至 n。（使用同一 updater 实例，无冗余 update() 调用。）"""
        updater = FieldStateUpdater()
        state = _make_test_state(presence_stability=(0.95, "saturated"))

        # 第 1 次钳制
        p1 = _make_perturbation("presence_stability", "increase", "high")
        s1, t1 = updater.update_with_trace(state, [p1])
        assert t1["consecutive_saturation_count"].get("presence_stability") == 1

        # 第 2 次钳制（从 s1 开始；因基线为 0.80，缓慢衰减后仍在 0.90+ 附近）
        p2 = _make_perturbation("presence_stability", "increase", "high")
        _s2, t2 = updater.update_with_trace(s1, [p2])
        assert t2["consecutive_saturation_count"].get("presence_stability") == 2

    def test_consecutive_saturation_resets_on_non_clamp(self):
        """当一轮未发生钳制时，连续计数器应重置为 0。"""
        updater = FieldStateUpdater()
        state = _make_test_state(presence_stability=(0.90, "high"))

        # 第 1 轮：钳制
        p1 = _make_perturbation("presence_stability", "increase", "high")
        _s1, t1 = updater.update_with_trace(state, [p1])
        assert t1["consecutive_saturation_count"].get("presence_stability") == 1

        # 第 2 轮：无扰动（弛豫应将其带离钳制区域）
        s1 = updater.update(state, [p1])
        _s2, t2 = updater.update_with_trace(s1, [])
        # 弛豫：new = clamp(1.0 + 0.04*(0.80 - 1.0)) = clamp(0.992) = 0.992
        # 实际上前钳制值将是 0.992，但钳制后 clamped_value 为 0.992（在范围内）
        # 等等——若前钳制在 [0,1] 内，则钳制未激活
        # presence_stability baseline=0.80, decay=0.04
        # 从 1.0（钳制后）: relaxed = 1.0 + 0.04*(0.80-1.0) = 1.0 - 0.008 = 0.992
        # pre_clamp = 0.992，在范围内 → clamp_activated = False
        assert t2["consecutive_saturation_count"].get("presence_stability") == 0, (
            f"无钳制时连续计数应重置，得到 {t2['consecutive_saturation_count']}"
        )

    def test_trace_present_for_audit_only(self):
        """追踪字段的存在不得改变返回的 RelationalFieldState。"""
        updater = FieldStateUpdater()
        state = _make_test_state(affective_warmth=(0.35, "baseline"))
        p = _make_perturbation("affective_warmth", "increase", "medium")

        # 无追踪更新
        s_no_trace = updater.update(state, [p])
        # 有追踪更新
        s_with_trace, _trace = updater.update_with_trace(state, [p])

        # 两种方法在数值上应产生相同的结果
        for name in s_no_trace.variables:
            v1 = s_no_trace.variables[name]
            v2 = s_with_trace.variables[name]
            assert v1.numeric_value == v2.numeric_value, (
                f"追踪污染了 {name}: {v1.numeric_value} != {v2.numeric_value}"
            )
            assert v1.value == v2.value


# ---------------------------------------------------------------------------
# DC-8: 无禁止导入
# ---------------------------------------------------------------------------

class TestNoForbiddenImports:
    """验证 updater.py 不导入任何禁止模块。"""

    def test_no_forbidden_modules_in_updater(self):
        """updater.py 不得导入 LLM、渲染器、运行时、内存或行为模块。"""
        updater_path = "src/field_state/updater.py"
        with open(updater_path, "r", encoding="utf-8") as f:
            source = f.read()

        tree = ast.parse(source)

        forbidden = {
            "body_action", "runtime_engine", "language", "llm",
            "router", "memory", "renderer", "avatar",
            "animation", "behavior", "field_trace",
            "motion_curve", "motion_params",
            "companion", "prompt", "speech",
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

        assert len(violations) == 0, f"发现禁止导入: {violations}"

    def test_diagnostic_file_does_not_import_renderer_or_runtime(self):
        """本测试文件不得导入渲染器、运行时或行为模块。"""
        import ast

        test_path = "tests/test_field_state_updater_dynamics.py"
        with open(test_path, "r", encoding="utf-8") as f:
            source = f.read()

        tree = ast.parse(source)

        forbidden = {
            "renderer", "runtime_engine", "runtime_state",
            "llm", "language",
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

        assert len(violations) == 0, f"发现禁止导入: {violations}"


# ---------------------------------------------------------------------------
# 行为保留测试（旧 API 不变）
# ---------------------------------------------------------------------------

class TestLegacyBehaviorPreserved:
    """验证旧版 update() 方法产生与更新器修改前相同的结果。"""

    def test_legacy_update_still_works(self):
        """旧版 update() 应仍然返回有效的 RelationalFieldState。"""
        updater = FieldStateUpdater()
        state = _make_test_state(affective_warmth=(0.35, "baseline"))
        p = _make_perturbation("affective_warmth", "increase", "medium")

        result = updater.update(state, [p])
        assert isinstance(result, RelationalFieldState)
        assert result.behavior_affecting is False
        assert result.variables["affective_warmth"].numeric_value > 0.35

    def test_relaxation_unchanged_from_before(self):
        """弛豫行为必须与诊断添加之前相同。"""
        updater = FieldStateUpdater()
        # structural_grip_pressure: baseline=0.05, decay=fast(0.45)
        state = _make_test_state(structural_grip_pressure=(0.50, "elevated"))

        p = _make_perturbation("structural_grip_pressure", "increase", "medium")
        new_state = updater.update(state, [p])

        # 弛豫: 0.50 + 0.45*(0.05-0.50) = 0.2975
        # 扰动后: 0.2975 + 0.10 = 0.3975
        new_val = new_state.variables["structural_grip_pressure"].numeric_value
        assert new_val == pytest.approx(0.3975, abs=0.01)

    def test_multiple_perturbations_same_variable_still_sum(self):
        """针对同一变量的多个扰动应仍求和所有增量。"""
        updater = FieldStateUpdater()
        state = _make_test_state(affective_warmth=(0.35, "baseline"))

        p1 = _make_perturbation("affective_warmth", "increase", "low")
        p2 = _make_perturbation("affective_warmth", "increase", "low")
        new_state = updater.update(state, [p1, p2])

        # affective_warmth baseline=0.35, decay=medium(0.25)
        # 弛豫: 0.35 + 0.25*(0.35-0.35) = 0.35 (已在基线)
        # 扰动后: 0.35 + 0.05 + 0.05 = 0.45
        new_val = new_state.variables["affective_warmth"].numeric_value
        assert new_val == pytest.approx(0.45, abs=0.01)

    def test_contamination_pressure_decay_still_instant(self):
        """contamination_pressure（instant 衰减）应仍在单轮内完全衰减。"""
        updater = FieldStateUpdater()
        state = _make_test_state(contamination_pressure=(0.80, "high"))
        new_state = updater.update(state, [])

        new_var = new_state.variables["contamination_pressure"]
        assert new_var.numeric_value == pytest.approx(0.0, abs=0.001)
        assert new_var.value == "low"

    def test_clamp_bounds_still_hold(self):
        """钳制边界应仍将值保持在 [0.0, 1.0] 内。"""
        updater = FieldStateUpdater()

        # 低于零测试
        s_low = _make_test_state(boundary_distance=(0.02, "low"))
        p_down = _make_perturbation("boundary_distance", "decrease", "high")
        r_low = updater.update(s_low, [p_down])
        assert r_low.variables["boundary_distance"].numeric_value >= 0.0

        # 高于一测试
        s_high = _make_test_state(presence_stability=(0.95, "saturated"))
        p_up = _make_perturbation("presence_stability", "increase", "high")
        r_high = updater.update(s_high, [p_up])
        assert r_high.variables["presence_stability"].numeric_value <= 1.0
