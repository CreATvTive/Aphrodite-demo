"""
ForceEvent Adapter — 将 FieldPerturbation 列表转换为时间剖面力向量 U_t。

填补断层：FieldPerturbation（标量增量 delta）→ RelationalFieldDynamicsKernel（二阶ODE，
需要时间剖面力 U(t)）。

将扰动增量转化为时间剖面力函数，可以直接馈入 Kernel 的 U_t。
"""

from __future__ import annotations

import math
from typing import Callable, List

import numpy as np

from src.field_state.perturbation import FieldPerturbation


# ---------------------------------------------------------------------------
# 10 轴索引 — 必须严格按此顺序
# ---------------------------------------------------------------------------
AXIS_INDEX: dict[str, int] = {
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

# ---------------------------------------------------------------------------
# 6 种力剖面类型
# ---------------------------------------------------------------------------
FORCE_PROFILE_TYPES: list[str] = [
    "impulse",
    "decaying_pulse",
    "sharp_pulse",
    "ramp",
    "persistent_step",
    "slow_pressure",
]

# ---------------------------------------------------------------------------
# duration_hint → 秒数常量
# ---------------------------------------------------------------------------
DURATION_HINT_TO_SEC: dict[str, float] = {
    "instant": 0.15,
    "fast": 0.30,
    "medium": 0.60,
    "slow": 1.00,
    "very_slow": 2.00,
}

# ---------------------------------------------------------------------------
# delta → 力缩放因子
# ---------------------------------------------------------------------------
DEFAULT_DELTA_TO_FORCE_SCALE: float = 2.0

# ---------------------------------------------------------------------------
# 信号（规则）→ 力剖面类型映射
# ---------------------------------------------------------------------------
_SIGNAL_TO_PROFILE: dict[str, str] = {
    "response_mode_rejected": "sharp_pulse",
    "actionable_grip_missing": "ramp",
    "boundary_pressure_present": "persistent_step",
    "technical_layer_needed": "ramp",
    "source_material_must_not_be_sanitized": "slow_pressure",
    "no_observable_field_signal": "impulse",  # 规则 F 无扰动 → 零力；此处 fallback，实际不产生力
}


# ---------------------------------------------------------------------------
# 私有：力剖面函数
# ---------------------------------------------------------------------------

def _impulse(t: float, duration: float, amplitude: float) -> float:
    """持续 0.3*duration，然后归零。"""
    pulse_width = 0.3 * duration
    if 0.0 <= t <= pulse_width:
        return amplitude
    return 0.0


def _decaying_pulse(t: float, duration: float, amplitude: float) -> float:
    """立即尖峰，持续 0.5*duration 以指数衰减 exp(-t/tau)，tau = 0.5*duration。"""
    tau = 0.5 * duration
    pulse_end = 0.5 * duration
    if 0.0 <= t <= pulse_end:
        return amplitude * math.exp(-t / tau)
    return 0.0


def _sharp_pulse(t: float, duration: float, amplitude: float) -> float:
    """持续 0.15*duration 的短尖峰，瞬时释放。"""
    pulse_width = 0.15 * duration
    if 0.0 <= t <= pulse_width:
        return amplitude
    return 0.0


def _ramp(t: float, duration: float, amplitude: float) -> float:
    """持续 0.8*duration 线性上升至 full amplitude，然后 0.2 秒归零。"""
    rise_time = 0.8 * duration
    fall_time = 0.2
    if 0.0 <= t <= rise_time:
        return amplitude * (t / rise_time)
    elif rise_time < t <= rise_time + fall_time:
        return amplitude * (1.0 - (t - rise_time) / fall_time)
    return 0.0


def _persistent_step(t: float, duration: float, amplitude: float) -> float:
    """达到 full、持续 0.9*duration、归零。接近方波。"""
    hold_time = 0.9 * duration
    if 0.0 <= t <= hold_time:
        return amplitude
    return 0.0


def _slow_pressure(t: float, duration: float, amplitude: float) -> float:
    """在 duration 上非常缓慢上升到 max 然后缓慢下降（半正弦形状）。"""
    if 0.0 <= t <= duration:
        return amplitude * math.sin(math.pi * t / duration)
    return 0.0


_PROFILE_FUNCTIONS: dict[str, Callable] = {
    "impulse": _impulse,
    "decaying_pulse": _decaying_pulse,
    "sharp_pulse": _sharp_pulse,
    "ramp": _ramp,
    "persistent_step": _persistent_step,
    "slow_pressure": _slow_pressure,
}


# ---------------------------------------------------------------------------
# PerturbationToForceAdapter
# ---------------------------------------------------------------------------

class PerturbationToForceAdapter:
    """将 FieldPerturbation 列表转换为 U_t 力向量（形状 (10,)）。

    每个扰动根据其 source_signal 分配一个力剖面类型，
    力值 = abs(numeric_delta) * delta_to_force_scale * direction_sign。
    stabilize 方向贡献零力。
    """

    def __init__(self, scale: float = DEFAULT_DELTA_TO_FORCE_SCALE):
        self.scale = scale

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def adapt(self, perturbations: list[FieldPerturbation]) -> np.ndarray:
        """将扰动列表转换为形状 (10,) 的 U_t 力向量。

        在 t=0 时计算力（即剖面初始值）。对于 ramp 和 slow_pressure，
        初始力为零——它们需要时间展开才能产生非零力。
        """
        U_t = np.zeros(10, dtype=float)

        if not perturbations:
            return U_t

        for p in perturbations:
            # stabilize 方向 → 零力贡献
            if p.direction == "stabilize":
                continue

            axis = AXIS_INDEX.get(p.target_variable)
            if axis is None:
                continue

            # 计算幅值
            amplitude = abs(p.numeric_delta) * self.scale

            # 方向符号
            if p.direction == "decrease":
                amplitude = -amplitude
            # increase: 保持正值
            # stabilize: 已在上面跳过

            # 获取剖面类型
            profile_type = _SIGNAL_TO_PROFILE.get(p.source_signal, "decaying_pulse")

            # 获取持续时间
            duration = DURATION_HINT_TO_SEC.get(p.duration_hint, 0.60)

            # 在 t=0 时计算力
            force = _profile_value(profile_type, t=0.0, duration=duration, amplitude=amplitude)
            U_t[axis] += force

        return U_t

    def adapt_sequence(
        self,
        perturbations: list[FieldPerturbation],
        dt: float,
        num_substeps: int,
    ) -> list[np.ndarray]:
        """返回每个子步的形状(10,)力向量列表，剖面随时间展开。

        参数:
            perturbations: FieldPerturbation 列表。
            dt: 总模拟持续时间（秒）。
            num_substeps: 要在其上展开的子步数。

        返回:
            每个子步一个形状 (10,) 的 numpy 数组列表。
            如果 dt <= 0 或 num_substeps <= 0，返回 [np.zeros(10)]。
        """
        total_duration = self.compute_duration(perturbations) or dt
        if total_duration <= 0 or num_substeps <= 0:
            return [np.zeros(10, dtype=float)]

        effective_dt = total_duration / num_substeps
        sequence: list[np.ndarray] = []

        for i in range(num_substeps):
            t = i * effective_dt
            U_t = np.zeros(10, dtype=float)

            for p in perturbations:
                if p.direction == "stabilize":
                    continue

                axis_idx = AXIS_INDEX.get(p.target_variable)
                if axis_idx is None:
                    continue

                profile_type = _SIGNAL_TO_PROFILE.get(
                    p.source_signal, "decaying_pulse"
                )
                dur = DURATION_HINT_TO_SEC.get(p.duration_hint, 0.60)
                amp = abs(p.numeric_delta) * self.scale

                sign = 1.0
                if p.direction == "decrease":
                    sign = -1.0

                val = _profile_value(profile_type, t, dur, amp)
                U_t[axis_idx] += sign * val

            sequence.append(U_t)

        return sequence

    def profile_value(
        self, profile_type: str, t: float, duration: float, amplitude: float
    ) -> float:
        """返回给定时间和剖面参数下的力值。

        参数:
            profile_type: 6 种力剖面类型之一
            t: 当前时间（秒）
            duration: 剖面总持续时间（秒）
            amplitude: 力幅值（峰值）
        返回:
            当前时间 t 的力值（标量）
        """
        return _profile_value(profile_type, t, duration, amplitude)

    def compute_duration(self, perturbations: list) -> float:
        """返回所有扰动 hint 中最大的 duration_hint_sec（用于 kernel 子步进）。

        参数:
            perturbations: FieldPerturbation 列表
        返回:
            最长持续时间（秒）；空列表返回 0.0
        """
        if not perturbations:
            return 0.0

        max_sec = 0.0
        for p in perturbations:
            sec = DURATION_HINT_TO_SEC.get(p.duration_hint, 0.60)
            if sec > max_sec:
                max_sec = sec
        return max_sec


# ---------------------------------------------------------------------------
# 私有：profile_value 实现
# ---------------------------------------------------------------------------

def _profile_value(profile_type: str, t: float, duration: float, amplitude: float) -> float:
    """返回给定时间和剖面参数下的力值。"""
    if t < 0.0 or t > duration:
        return 0.0

    fn = _PROFILE_FUNCTIONS.get(profile_type)
    if fn is None:
        # 未知剖面类型 → 回退到 decaying_pulse
        return _decaying_pulse(t, duration, amplitude)

    return fn(t, duration, amplitude)
