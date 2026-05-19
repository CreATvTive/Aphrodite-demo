"""
AxisDynamicsProfile — 轴级动力学校准配置。

每个 profile 定义了一个轴的二阶动力学特性（ζ, ω_n），
推导为 M/C/K 参数供 RelationalFieldDynamicsKernel 使用。

Phase 39.6d — Dynamics Calibration + Profile Scanning.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .schema import FieldDynamicsConfig
from .force_adapter import AXIS_INDEX


# ---------------------------------------------------------------------------
# AxisDynamicsProfile
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AxisDynamicsProfile:
    """轴级动力学 profile：阻尼比 ζ 和固有频率 ω_n。

    标准二阶系统关系：M=1.0, C=2ζω_n·M, K=ω_n²·M。
    """
    name: str           # "monolith" | "gyre" | "nerve" | "tide"
    zeta: float         # 阻尼比 ζ，范围 [0.1, 0.95]
    omega_n: float      # 固有频率 ω_n，范围 [0.5, 6.0]

    def __post_init__(self) -> None:
        if not (0.1 <= self.zeta <= 0.95):
            raise ValueError(f"zeta must be in [0.1, 0.95], got {self.zeta}")
        if not (0.5 <= self.omega_n <= 6.0):
            raise ValueError(f"omega_n must be in [0.5, 6.0], got {self.omega_n}")
        valid_names = {"monolith", "gyre", "nerve", "tide"}
        if self.name not in valid_names:
            raise ValueError(f"name must be one of {valid_names}, got {self.name!r}")


# ---------------------------------------------------------------------------
# Profile → M/C/K 推导
# ---------------------------------------------------------------------------

def profile_to_mck(profile: AxisDynamicsProfile) -> tuple[float, float, float]:
    """从 AxisDynamicsProfile 推导 (M, C, K)。

    标准二阶系统：M=1.0, C=2ζω_n·M, K=ω_n²·M。
    """
    M = 1.0
    C = 2.0 * profile.zeta * profile.omega_n * M
    K = profile.omega_n ** 2 * M
    return M, C, K


# ---------------------------------------------------------------------------
# 4 个预置 Profile 常量
# ---------------------------------------------------------------------------

PROFILE_MONOLITH = AxisDynamicsProfile("monolith", zeta=0.90, omega_n=1.20)
PROFILE_GYRE = AxisDynamicsProfile("gyre", zeta=0.65, omega_n=1.80)
PROFILE_NERVE = AxisDynamicsProfile("nerve", zeta=0.30, omega_n=5.00)
PROFILE_TIDE = AxisDynamicsProfile("tide", zeta=0.55, omega_n=2.50)


# ---------------------------------------------------------------------------
# 轴 → Profile 分配（10 轴）
# ---------------------------------------------------------------------------

AXIS_PROFILES: dict[str, AxisDynamicsProfile] = {
    "boundary_distance": PROFILE_GYRE,
    "affective_warmth": PROFILE_TIDE,
    "structural_grip_pressure": PROFILE_GYRE,
    "correction_pressure": PROFILE_NERVE,
    "contamination_resistance": PROFILE_MONOLITH,
    "presence_stability": PROFILE_TIDE,
    "withdrawal_tendency": PROFILE_GYRE,
    "service_resistance": PROFILE_MONOLITH,
    "collaborator_layer_pressure": PROFILE_TIDE,
    "contamination_pressure": PROFILE_NERVE,
}


# ---------------------------------------------------------------------------
# build_config_from_profiles
# ---------------------------------------------------------------------------

def build_config_from_profiles(
    axis_profiles: dict[str, AxisDynamicsProfile] | None = None,
    dt_max: float = 0.05,
    V_max: float = 2.0,
    A_max: float = 5.0,
    overshoot_max: float = 0.1,
) -> FieldDynamicsConfig:
    """从逐轴 profile 字典构建完整的 FieldDynamicsConfig。

    参数:
        axis_profiles: 轴名 → AxisDynamicsProfile 映射；默认使用 AXIS_PROFILES。
        dt_max: 最大时间步长（秒）。
        V_max: 最大速度限幅。
        A_max: 最大加速度限幅。
        overshoot_max: 最大越界限幅（标量，应用于全部 10 轴）。

    返回:
        填充了逐轴 M/C/K 的 FieldDynamicsConfig。
        B 设置为全零（由调用方根据 legacy state 覆盖）。
    """
    if axis_profiles is None:
        axis_profiles = AXIS_PROFILES

    M = np.ones(10, dtype=float)
    C = np.zeros(10, dtype=float)
    K = np.zeros(10, dtype=float)

    for axis_name, axis_idx in AXIS_INDEX.items():
        profile = axis_profiles.get(axis_name, PROFILE_TIDE)
        m, c, k = profile_to_mck(profile)
        M[axis_idx] = m
        C[axis_idx] = c
        K[axis_idx] = k

    return FieldDynamicsConfig(
        M=M,
        C=C,
        K=K,
        B=np.zeros(10, dtype=float),
        dt_max=dt_max,
        V_max=V_max,
        A_max=A_max,
        overshoot_max=overshoot_max,
    )
