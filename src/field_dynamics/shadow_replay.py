"""
Shadow Mode Replay — 并行运行遗留和新场更新路径，产出对比报告。
Stage 3 of migration architecture.

两条路径并行运行，输出用于审计，不切换运行时路由：
- 遗留路径：FieldStateUpdater v0（relax→perturb→clamp→band）
- 新路径：PerturbationToForceAdapter → RelationalFieldDynamicsKernel
"""
from __future__ import annotations

import copy
import time
from typing import List

import numpy as np

from src.field_state.schema import RelationalFieldState, REQUIRED_FIELD_VARIABLES
from src.field_state.perturbation import FieldPerturbation
from src.field_state.updater import FieldStateUpdater
from src.field_dynamics.force_adapter import PerturbationToForceAdapter, AXIS_INDEX
from src.field_dynamics.kernel import RelationalFieldDynamicsKernel
from src.field_dynamics.schema import (
    FieldDynamicsConfig,
    FieldDynamicsState,
    FieldDynamicsInput,
)

# ---------------------------------------------------------------------------
# 轴名称 — 必须严格按此顺序
# ---------------------------------------------------------------------------
AXIS_NAMES = [
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
]


class ShadowReplay:
    """并行运行遗留和新场更新路径，产出对比报告。

    不切换运行时路由——仅并行诊断。
    """

    def __init__(self, kernel_config: FieldDynamicsConfig | None = None):
        self._kernel_config = kernel_config
        self._updater = FieldStateUpdater()
        self._force_adapter = PerturbationToForceAdapter()

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def run_comparison(
        self,
        legacy_state: RelationalFieldState,
        perturbations: list[FieldPerturbation],
        num_steps: int = 3,
    ) -> dict:
        """运行两条路径进行 num_steps 步，返回对比字典。

        参数:
            legacy_state: 初始 RelationalFieldState
            perturbations: FieldPerturbation 列表
            num_steps: 两条路径运行的步数（默认 3）

        返回:
            对比报告字典（详见任务规范）
        """
        # 1. 克隆状态
        legacy_state_copy = copy.deepcopy(legacy_state)

        # 2. 遗留路径
        legacy_times: list[float] = []
        legacy_values_per_step: list[dict[str, float]] = []
        current_state = legacy_state_copy

        for _step in range(num_steps):
            t0 = time.perf_counter()
            new_state = self._updater.update(current_state, perturbations)
            t1 = time.perf_counter()
            legacy_times.append(t1 - t0)

            step_values = {
                name: var.numeric_value
                for name, var in new_state.variables.items()
            }
            legacy_values_per_step.append(step_values)
            current_state = new_state

        legacy_final = legacy_values_per_step[-1]
        initial_values = {
            name: var.numeric_value
            for name, var in legacy_state_copy.variables.items()
        }
        legacy_max_delta = max(
            abs(legacy_final[name] - initial_values[name])
            for name in REQUIRED_FIELD_VARIABLES
        )

        # 3. 新路径设置 — 从 legacy_state 变量构造初始 FieldDynamicsState
        F_tilde_initial = np.array(
            [legacy_state.variables[name].numeric_value for name in AXIS_NAMES],
            dtype=float,
        )
        V_initial = np.zeros(10, dtype=float)

        config = self._kernel_config or FieldDynamicsConfig(
            M=np.ones(10, dtype=float),
            C=0.4 * np.ones(10, dtype=float),
            K=0.8 * np.ones(10, dtype=float),
            B=np.array(
                [legacy_state.variables[name].baseline_numeric_value for name in AXIS_NAMES],
                dtype=float,
            ),
            dt_max=0.05,
            V_max=2.0,
            A_max=5.0,
            overshoot_max=0.1 * np.ones(10, dtype=float),
        )

        initial_dynamics_state = FieldDynamicsState(
            F_tilde=F_tilde_initial,
            V=V_initial,
        )
        kernel = RelationalFieldDynamicsKernel(config, initial_dynamics_state)

        # 4. 新路径
        dt = 0.05
        U_t = self._force_adapter.adapt(perturbations)
        peak_force_norm = float(np.linalg.norm(U_t))

        new_F_bounded_per_step: list[np.ndarray] = []
        new_V_per_step: list[np.ndarray] = []
        new_A_per_step: list[np.ndarray] = []
        tension_metrics_per_step: list[dict] = []

        for _step in range(num_steps):
            input_data = FieldDynamicsInput(U_t=U_t, dt=dt)
            output = kernel.step(input_data)
            new_F_bounded_per_step.append(output.F_bounded.copy())
            new_V_per_step.append(output.V.copy())
            new_A_per_step.append(output.A.copy())
            tension_metrics_per_step.append(dict(output.tension_metrics))

        final_F_bounded = new_F_bounded_per_step[-1]
        final_V = new_V_per_step[-1]
        final_A = new_A_per_step[-1]
        final_tension = tension_metrics_per_step[-1]

        # 5. 振荡检测
        oscillation_detected = self._detect_oscillation(new_V_per_step)

        # 6. 长尾残留
        long_tail_residue = float(np.linalg.norm(final_V)) > 0.01

        # 7. 最大越界
        max_overshoot = max(
            tm.get("overshoot_magnitude", 0.0) for tm in tension_metrics_per_step
        )

        # 8. 意外力轴
        unexpected_force_axes = self._detect_unexpected_force(U_t, perturbations)

        # 9. 方向匹配 & 幅度差异
        direction_match: dict[str, bool] = {}
        magnitude_diff: dict[str, float] = {}
        for axis, name in enumerate(AXIS_NAMES):
            legacy_val = legacy_final.get(name, 0.0)
            new_val = float(final_F_bounded[axis])
            baseline = legacy_state.variables[name].baseline_numeric_value

            legacy_side = (
                1 if legacy_val > baseline else (-1 if legacy_val < baseline else 0)
            )
            new_side = 1 if new_val > baseline else (-1 if new_val < baseline else 0)
            direction_match[name] = legacy_side == new_side
            magnitude_diff[name] = abs(legacy_val - new_val)

        max_magnitude_diff = max(magnitude_diff.values())
        mean_magnitude_diff = float(np.mean(list(magnitude_diff.values())))

        # 10. 风险标记
        risk_flags = self._build_risk_flags(
            oscillation_detected=oscillation_detected,
            max_overshoot=max_overshoot,
            direction_match=direction_match,
            long_tail_residue=long_tail_residue,
            unexpected_force_axes=unexpected_force_axes,
        )

        # 构建报告
        return {
            "scenario": "shadow_replay",
            "num_steps": num_steps,
            "legacy": {
                "final_values": legacy_final,
                "max_delta": legacy_max_delta,
            },
            "new_route": {
                "final_F_bounded": {
                    AXIS_NAMES[i]: float(final_F_bounded[i]) for i in range(10)
                },
                "final_V": {AXIS_NAMES[i]: float(final_V[i]) for i in range(10)},
                "final_A": {AXIS_NAMES[i]: float(final_A[i]) for i in range(10)},
                "peak_force_norm": peak_force_norm,
                "tension_metrics": final_tension,
                "max_overshoot": max_overshoot,
                "oscillation_detected": oscillation_detected,
                "long_tail_residue": long_tail_residue,
            },
            "comparison": {
                "direction_match": direction_match,
                "magnitude_diff": magnitude_diff,
                "unexpected_force_axes": unexpected_force_axes,
                "max_magnitude_diff": max_magnitude_diff,
                "mean_magnitude_diff": mean_magnitude_diff,
            },
            "risk_flags": risk_flags,
        }

    # ------------------------------------------------------------------
    # 多时间跨度影子回放
    # ------------------------------------------------------------------

    def run_multi_horizon(
        self,
        legacy_state: RelationalFieldState,
        perturbations: list[FieldPerturbation],
        horizons: list[float] | None = None,
    ) -> dict:
        """在多个时间跨度上运行影子对比。返回嵌套报告。

        参数:
            legacy_state: 初始 RelationalFieldState。
            perturbations: FieldPerturbation 列表。
            horizons: 要采样的时间跨度列表（秒），默认 [0.15, 1.0, 3.0]。

        返回:
            嵌套字典，包含 "scenario", "horizon_reports"。
        """
        if horizons is None:
            horizons = [0.15, 1.0, 3.0]

        config = self._kernel_config or FieldDynamicsConfig(
            M=np.ones(10, dtype=float),
            C=0.4 * np.ones(10, dtype=float),
            K=0.8 * np.ones(10, dtype=float),
            B=self._baseline_from_state(legacy_state),
            dt_max=0.05,
            V_max=2.0,
            A_max=5.0,
            overshoot_max=0.1 * np.ones(10, dtype=float),
        )

        F_tilde_initial = np.array(
            [legacy_state.variables[name].numeric_value for name in AXIS_NAMES],
            dtype=float,
        )
        V_initial = np.zeros(10, dtype=float)

        horizon_reports: list[dict] = []

        for horizon in horizons:
            num_substeps = max(1, int(horizon / config.dt_max))
            effective_dt = horizon / num_substeps

            U_sequence = self._force_adapter.adapt_sequence(
                perturbations, dt=horizon, num_substeps=num_substeps
            )

            initial_dynamics_state = FieldDynamicsState(
                F_tilde=F_tilde_initial.copy(),
                V=V_initial.copy(),
            )
            kernel = RelationalFieldDynamicsKernel(config, initial_dynamics_state)

            peak_force_norm = 0.0
            F_bounded_per_step: list[np.ndarray] = []
            V_per_step: list[np.ndarray] = []
            tension_metrics_per_step: list[dict] = []

            for U_t in U_sequence:
                input_data = FieldDynamicsInput(U_t=U_t, dt=effective_dt)
                output = kernel.step(input_data)
                F_bounded_per_step.append(output.F_bounded.copy())
                V_per_step.append(output.V.copy())
                tension_metrics_per_step.append(dict(output.tension_metrics))
                fnorm = float(np.linalg.norm(U_t))
                if fnorm > peak_force_norm:
                    peak_force_norm = fnorm

            final_F = F_bounded_per_step[-1] if F_bounded_per_step else F_tilde_initial.copy()
            final_V = V_per_step[-1] if V_per_step else V_initial.copy()
            final_tension = tension_metrics_per_step[-1] if tension_metrics_per_step else {}

            long_tail_residue = float(np.linalg.norm(final_V)) > 0.01
            oscillation_detected = self._detect_oscillation(V_per_step)

            horizon_reports.append({
                "horizon": horizon,
                "substeps": num_substeps,
                "final_F_bounded": {
                    AXIS_NAMES[i]: float(final_F[i]) for i in range(10)
                },
                "final_V": {AXIS_NAMES[i]: float(final_V[i]) for i in range(10)},
                "peak_force_norm": peak_force_norm,
                "long_tail_status": "residual" if long_tail_residue else "settled",
                "oscillation_detected": oscillation_detected,
                "tension_metrics": final_tension,
            })

        return {
            "scenario": "multi_horizon",
            "horizon_reports": horizon_reports,
        }

    # ------------------------------------------------------------------
    # 方向不匹配诊断
    # ------------------------------------------------------------------

    def diagnose_direction_mismatch(
        self,
        scenario_name: str,
        legacy_state: RelationalFieldState,
        perturbations: list[FieldPerturbation],
    ) -> dict:
        """诊断方向不匹配：报告 force 映射是否产生意外方向。

        对每个扰动：
        - 报告目标轴、方向、力符号
        - 检查遗留路径和新路径的目标轴方向是否一致
        - 区分 force-mapping 问题 vs M/C/K 校准问题

        参数:
            scenario_name: 场景名称。
            legacy_state: 初始 RelationalFieldState。
            perturbations: FieldPerturbation 列表。

        返回:
            诊断字典。
        """
        U_t = self._force_adapter.adapt(perturbations)
        report = self.run_comparison(legacy_state, perturbations, num_steps=4)
        direction_match = report["comparison"]["direction_match"]

        direction_issues: list[dict] = []
        force_mapping_issues: list[dict] = []
        mck_calibration_issues: list[dict] = []

        for p in perturbations:
            if p.direction == "stabilize":
                continue

            axis_idx = AXIS_INDEX.get(p.target_variable)
            if axis_idx is None:
                continue

            axis_name = AXIS_NAMES[axis_idx]
            force_sign = np.sign(U_t[axis_idx])

            target_sign = 1.0 if p.direction == "increase" else -1.0
            force_mapping_ok = (force_sign == 0.0) or (np.sign(force_sign) == target_sign)

            if not force_mapping_ok:
                force_mapping_issues.append({
                    "axis": axis_name,
                    "target_direction": p.direction,
                    "numeric_delta": p.numeric_delta,
                    "force_at_t0": float(U_t[axis_idx]),
                    "expected_sign": target_sign,
                    "actual_sign": int(force_sign),
                    "issue": "force_mapping",
                    "detail": (
                        "在 t=0 时 U_t 符号与目标方向 "
                        + p.direction + " 不一致"
                    ),
                })

            dm_ok = direction_match.get(axis_name, True)
            if not dm_ok and force_mapping_ok:
                mck_calibration_issues.append({
                    "axis": axis_name,
                    "force_at_t0": float(U_t[axis_idx]),
                    "target_direction": p.direction,
                    "issue": "mck_calibration",
                    "detail": (
                        "力映射正确，但最终 F_bounded 方向与 "
                        "遗留路径不一致 — 可能是惯性超调或 M/C/K 校准问题"
                    ),
                })

            if not dm_ok or not force_mapping_ok:
                direction_issues.append({
                    "axis": axis_name,
                    "target_direction": p.direction,
                    "force_at_t0": float(U_t[axis_idx]),
                    "direction_match_legacy": dm_ok,
                    "force_mapping_ok": force_mapping_ok,
                    "category": (
                        "force_mapping" if not force_mapping_ok else "mck_calibration"
                    ),
                })

        overall = "PASS"
        if force_mapping_issues:
            overall = "FAIL: force_mapping"
        elif mck_calibration_issues:
            overall = "WARN: mck_calibration"

        return {
            "scenario": scenario_name,
            "direction_issues": direction_issues,
            "force_mapping_issues": force_mapping_issues,
            "mck_calibration_issues": mck_calibration_issues,
            "overall_assessment": overall,
        }

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _get_axis_name(idx: int) -> str:
        """返回给定索引的轴名称。"""
        return AXIS_NAMES[idx]

    @staticmethod
    def _baseline_from_state(state: RelationalFieldState) -> np.ndarray:
        """从 RelationalFieldState 提取 baseline 值数组。"""
        return np.array(
            [state.variables[name].baseline_numeric_value for name in AXIS_NAMES],
            dtype=float,
        )

    @staticmethod
    def _detect_oscillation(V_per_step: list[np.ndarray]) -> bool:
        """检测任何轴的速度是否在步骤间改变符号。

        对每个轴，检查 V[t] * V[t+1] < 0 且 |V[t]| > 0.01 且 |V[t+1]| > 0.01。
        """
        if len(V_per_step) < 2:
            return False

        for axis in range(10):
            for t in range(len(V_per_step) - 1):
                v_t = V_per_step[t][axis]
                v_t1 = V_per_step[t + 1][axis]
                if v_t * v_t1 < 0 and abs(v_t) > 0.01 and abs(v_t1) > 0.01:
                    return True
        return False

    @staticmethod
    def _detect_unexpected_force(
        U_t: np.ndarray, perturbations: list[FieldPerturbation]
    ) -> list[str]:
        """检测力非零但 perturbation 中无目标为该轴的意外力轴。"""
        targeted_axes: set[int] = set()
        for p in perturbations:
            axis_idx = AXIS_INDEX.get(p.target_variable)
            if axis_idx is not None:
                targeted_axes.add(axis_idx)

        unexpected: list[str] = []
        for axis in range(10):
            if abs(U_t[axis]) > 1e-9 and axis not in targeted_axes:
                unexpected.append(AXIS_NAMES[axis])
        return unexpected

    @staticmethod
    def _build_risk_flags(
        oscillation_detected: bool,
        max_overshoot: float,
        direction_match: dict[str, bool],
        long_tail_residue: bool,
        unexpected_force_axes: list[str],
    ) -> list[str]:
        """构建风险标记列表。"""
        flags: list[str] = []
        if oscillation_detected:
            flags.append("oscillation_detected")
        if max_overshoot > 0.05:
            flags.append("high_overshoot")
        if not all(direction_match.values()):
            mismatched = [name for name, ok in direction_match.items() if not ok]
            flags.append(f"direction_mismatch: {mismatched}")
        if long_tail_residue:
            flags.append("long_tail_residue")
        if unexpected_force_axes:
            flags.append(f"unexpected_force_axes: {unexpected_force_axes}")
        return flags
