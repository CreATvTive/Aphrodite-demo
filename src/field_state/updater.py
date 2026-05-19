"""
FieldStateUpdater — 将场扰动应用于关系场状态以产生下一个状态。

更新顺序：
1. 将值弛豫至基线
2. 应用扰动
3. 钳制至 [0.0, 1.0]
4. 从数值重新计算波段
"""

from __future__ import annotations
from dataclasses import replace
from typing import Dict, List, Tuple

from .schema import RelationalFieldState, FieldVariable
from .perturbation import FieldPerturbation


# ---------------------------------------------------------------------------
# 衰减率表
# ---------------------------------------------------------------------------
DECAY_RATES = {
    "instant": 1.00,
    "fast": 0.45,
    "medium": 0.25,
    "slow": 0.12,
    "very_slow": 0.04,
}

# ---------------------------------------------------------------------------
# 波段边界 — 每个元组为 (上限阈值, 波段名称)
# 数值 <= 上限阈值即属于该波段；若未匹配则属于 "saturated"
# ---------------------------------------------------------------------------
BAND_BOUNDARIES = [
    (0.20, "low"),
    (0.50, "baseline"),
    (0.70, "elevated"),
    (0.90, "high"),
]


# ---------------------------------------------------------------------------
# FieldStateUpdater
# ---------------------------------------------------------------------------

class FieldStateUpdater:
    """将场扰动应用于关系场状态以产生下一个状态。

    更新顺序：
    1. 将值弛豫至基线
    2. 应用扰动
    3. 钳制至 [0.0, 1.0]
    4. 从数值重新计算波段
    """

    def __init__(self, decay_rates: dict | None = None):
        self.decay_rates = decay_rates or DECAY_RATES
        # 用于检测连续钳制饱和的跨调用计数器（仅诊断，不影响行为）
        self._consecutive_saturation_count: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def update(self, state: RelationalFieldState,
               perturbations: List[FieldPerturbation]) -> RelationalFieldState:
        """应用扰动并弛豫，返回新的 RelationalFieldState（不变异输入）。"""
        new_state, _trace = self._compute_update(state, perturbations)
        return new_state

    def update_with_trace(
        self,
        state: RelationalFieldState,
        perturbations: List[FieldPerturbation],
    ) -> Tuple[RelationalFieldState, dict]:
        """应用扰动并弛豫，返回 (新状态, 诊断追踪字典)。

        诊断追踪为仅审计用途。其存在与否不改变返回的
        RelationalFieldState，也不影响 behavior_affecting 路径。
        """
        return self._compute_update(state, perturbations)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _compute_update(
        self,
        state: RelationalFieldState,
        perturbations: List[FieldPerturbation],
    ) -> Tuple[RelationalFieldState, dict]:
        """核心更新逻辑：返回 (新状态, 诊断追踪)。"""

        # 1. 按变量聚合扰动增量 + 计数
        deltas: dict[str, float] = {}
        per_axis_count: dict[str, int] = {}
        for p in perturbations:
            axis = p.target_variable
            deltas[axis] = deltas.get(axis, 0.0) + p.numeric_delta
            per_axis_count[axis] = per_axis_count.get(axis, 0) + 1

        # 2. 对每个变量：弛豫 → 扰动 → 钳制 → 重新计算波段
        trace_per_axis: dict = {}
        saturated_axes: list = []
        updated_vars: dict[str, FieldVariable] = {}

        for name, var in state.variables.items():
            decay_rate = self.decay_rates.get(
                var.decay_profile, self.decay_rates.get("medium", 0.25)
            )
            current_val = var.numeric_value
            baseline_val = var.baseline_numeric_value
            relaxed = self._relax(current_val, baseline_val, decay_rate)
            delta_sum = deltas.get(name, 0.0)
            pre_clamp = relaxed + delta_sum
            new_value = self._clamp(pre_clamp)
            clamp_amount = pre_clamp - new_value
            clamp_activated = abs(clamp_amount) > 1e-9
            new_band = self._numeric_to_band(new_value)

            updated_vars[name] = replace(
                var,
                numeric_value=new_value,
                value=new_band,
            )

            # 连续饱和追踪（仅诊断）
            if clamp_activated:
                self._consecutive_saturation_count[name] = (
                    self._consecutive_saturation_count.get(name, 0) + 1
                )
                saturated_axes.append(name)
            else:
                self._consecutive_saturation_count[name] = 0

            trace_per_axis[name] = {
                "current_value": current_val,
                "baseline": baseline_val,
                "decay_rate": decay_rate,
                "relaxed": round(relaxed, 6),
                "delta_sum": round(delta_sum, 6),
                "pre_clamp_value": round(pre_clamp, 6),
                "clamp_amount": round(clamp_amount, 6),
                "clamp_activated": clamp_activated,
                "new_value": new_value,
            }

        # 3. 构造 state_note
        note = (
            f"FieldStateUpdater: applied {len(perturbations)} perturbations, "
            f"relaxed all variables"
        )

        new_state = replace(
            state,
            variables=updated_vars,
            state_note=note,
        )

        # 4. 组装诊断追踪
        trace = {
            "per_axis": trace_per_axis,
            "same_axis_perturbation_count": per_axis_count,
            "total_perturbations": len(perturbations),
            "saturated_axes": saturated_axes,
            "consecutive_saturation_count": dict(self._consecutive_saturation_count),
        }

        return new_state, trace

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _relax(current: float, baseline: float, decay_rate: float) -> float:
        """弛豫：current + decay_rate * (baseline - current)"""
        return current + decay_rate * (baseline - current)

    @staticmethod
    def _clamp(value: float) -> float:
        """钳制至 [0.0, 1.0]"""
        return max(0.0, min(1.0, value))

    @staticmethod
    def _numeric_to_band(numeric_value: float) -> str:
        """根据 BAND_BOUNDARIES 将数值映射为波段名称。"""
        for threshold, band in BAND_BOUNDARIES:
            if numeric_value <= threshold:
                return band
        return "saturated"
