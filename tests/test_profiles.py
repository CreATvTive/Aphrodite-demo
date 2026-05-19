"""测试 AxisDynamicsProfile — Phase 39.6d Dynamics Calibration.

验证:
1. profile 构造有效
2. profile_to_mck 正确
3. build_config_from_profiles 产生正确形状的数组
4. 每个轴都有 profile 分配
5. M/C/K/B 无 NaN/Inf
6. 无效 profile (zeta 超出范围) 引发错误
"""
from __future__ import annotations

import numpy as np
import pytest

from src.field_dynamics import (
    AXIS_PROFILES,
    AxisDynamicsProfile,
    PROFILE_GYRE,
    PROFILE_MONOLITH,
    PROFILE_NERVE,
    PROFILE_TIDE,
    build_config_from_profiles,
    profile_to_mck,
)
from src.field_dynamics.force_adapter import AXIS_INDEX


# ---------------------------------------------------------------------------
# 测试 1: profile 构造有效
# ---------------------------------------------------------------------------

class TestProfileConstruction:
    """验证 AxisDynamicsProfile 构造与边界。"""

    def test_all_four_presets_valid(self):
        """全部 4 个预置 profile 可成功构造。"""
        profiles = [PROFILE_MONOLITH, PROFILE_GYRE, PROFILE_NERVE, PROFILE_TIDE]
        for p in profiles:
            assert isinstance(p, AxisDynamicsProfile)
            assert p.name in {"monolith", "gyre", "nerve", "tide"}
            assert 0.1 <= p.zeta <= 0.95
            assert 0.5 <= p.omega_n <= 6.0

    def test_custom_profile_valid(self):
        """自定义有效参数可成功构造。"""
        p = AxisDynamicsProfile("tide", zeta=0.50, omega_n=3.0)
        assert p.name == "tide"
        assert p.zeta == 0.50
        assert p.omega_n == 3.0

    def test_zeta_out_of_range_raises(self):
        """zeta < 0.1 或 > 0.95 引发 ValueError。"""
        with pytest.raises(ValueError):
            AxisDynamicsProfile("tide", zeta=0.05, omega_n=2.0)
        with pytest.raises(ValueError):
            AxisDynamicsProfile("tide", zeta=1.0, omega_n=2.0)

    def test_omega_n_out_of_range_raises(self):
        """omega_n < 0.5 或 > 6.0 引发 ValueError。"""
        with pytest.raises(ValueError):
            AxisDynamicsProfile("tide", zeta=0.50, omega_n=0.3)
        with pytest.raises(ValueError):
            AxisDynamicsProfile("tide", zeta=0.50, omega_n=7.0)

    def test_invalid_name_raises(self):
        """无效 profile 名称引发 ValueError。"""
        with pytest.raises(ValueError):
            AxisDynamicsProfile("invalid", zeta=0.50, omega_n=2.0)

    def test_frozen_dataclass(self):
        """AxisDynamicsProfile 是不可变的 (frozen=True)。"""
        p = PROFILE_TIDE
        with pytest.raises(Exception):
            p.zeta = 0.99  # type: ignore


# ---------------------------------------------------------------------------
# 测试 2: profile_to_mck 正确
# ---------------------------------------------------------------------------

class TestProfileToMCK:
    """验证 profile_to_mck() 推导。"""

    def test_returns_correct_tuple(self):
        """返回值是 (M, C, K) 三元组。"""
        result = profile_to_mck(PROFILE_MONOLITH)
        assert len(result) == 3
        M, C, K = result
        assert M == 1.0
        assert C >= 0.0
        assert K >= 0.0

    def test_m_is_always_1(self):
        """对于任何有效 profile，M 始终为 1.0。"""
        for profile in [PROFILE_MONOLITH, PROFILE_GYRE, PROFILE_NERVE, PROFILE_TIDE]:
            M, _C, _K = profile_to_mck(profile)
            assert M == 1.0

    def test_formula_correct(self):
        """C = 2*zeta*omega_n, K = omega_n^2。"""
        p = AxisDynamicsProfile("tide", zeta=0.60, omega_n=4.0)
        M, C, K = profile_to_mck(p)
        assert C == pytest.approx(2.0 * 0.60 * 4.0)  # = 4.8
        assert K == pytest.approx(4.0 ** 2)  # = 16.0

    def test_monolith_profile_values(self):
        """Monolith: zeta=0.90, omega_n=1.20 → C=2.16, K=1.44。"""
        _M, C, K = profile_to_mck(PROFILE_MONOLITH)
        assert C == pytest.approx(2.0 * 0.90 * 1.20)
        assert K == pytest.approx(1.20 ** 2)

    def test_nerve_profile_values(self):
        """Nerve: zeta=0.30, omega_n=5.00 → C=3.0, K=25.0。"""
        _M, C, K = profile_to_mck(PROFILE_NERVE)
        assert C == pytest.approx(2.0 * 0.30 * 5.00)
        assert K == pytest.approx(5.00 ** 2)


# ---------------------------------------------------------------------------
# 测试 3: build_config_from_profiles 形状
# ---------------------------------------------------------------------------

class TestBuildConfig:
    """验证 build_config_from_profiles() 产出。"""

    def test_returns_valid_config(self):
        """build_config_from_profiles 返回有效的 FieldDynamicsConfig。"""
        config = build_config_from_profiles()
        config.validate()
        assert config.M.shape == (10,)
        assert config.C.shape == (10,)
        assert config.K.shape == (10,)
        assert config.B.shape == (10,)
        assert config.dt_max == 0.05
        assert config.V_max == 2.0
        assert config.A_max == 5.0

    def test_m_always_ones(self):
        """所有轴的 M 均为 1.0。"""
        config = build_config_from_profiles()
        assert np.all(config.M == 1.0)

    def test_c_and_k_positive(self):
        """C 和 K 对所有轴均为正值。"""
        config = build_config_from_profiles()
        assert np.all(config.C > 0.0)
        assert np.all(config.K > 0.0)

    def test_no_nan_or_inf(self):
        """M/C/K/B 不含 NaN 或 Inf。"""
        config = build_config_from_profiles()
        assert np.all(np.isfinite(config.M))
        assert np.all(np.isfinite(config.C))
        assert np.all(np.isfinite(config.K))
        assert np.all(np.isfinite(config.B))

    def test_custom_axis_profiles(self):
        """自定义逐轴 profile 生效。"""
        custom = {
            "boundary_distance": PROFILE_MONOLITH,
            "affective_warmth": PROFILE_NERVE,
        }
        config = build_config_from_profiles(axis_profiles=custom)
        config.validate()
        # boundary_distance 轴 0 应使用 Monolith 参数
        _M0, C0, K0 = profile_to_mck(PROFILE_MONOLITH)
        assert config.C[0] == pytest.approx(C0)
        assert config.K[0] == pytest.approx(K0)

        # affective_warmth 轴 1 应使用 Nerve 参数
        _M1, C1, K1 = profile_to_mck(PROFILE_NERVE)
        assert config.C[1] == pytest.approx(C1)
        assert config.K[1] == pytest.approx(K1)


# ---------------------------------------------------------------------------
# 测试 4: 每个轴都有 profile 分配
# ---------------------------------------------------------------------------

class TestAxisProfileCoverage:
    """验证每个轴都有 profile 分配。"""

    def test_all_10_axes_assigned(self):
        """AXIS_PROFILES 覆盖所有 10 个轴。"""
        assert len(AXIS_PROFILES) == 10
        for axis_name in AXIS_INDEX:
            assert axis_name in AXIS_PROFILES, (
                f"轴 {axis_name} 未分配 profile"
            )

    def test_build_config_covers_all_axes(self):
        """build_config_from_profiles 为所有轴填充非零 C/K。"""
        config = build_config_from_profiles()
        for i in range(10):
            assert config.C[i] > 0.0, f"轴 {i} 的 C 为零"
            assert config.K[i] > 0.0, f"轴 {i} 的 K 为零"


# ---------------------------------------------------------------------------
# 测试 5: 具体 Profile 参数验证
# ---------------------------------------------------------------------------

class TestPresetProfileValues:
    """验证预置 profile 的具体数值。"""

    def test_monolith_values(self):
        assert PROFILE_MONOLITH.name == "monolith"
        assert PROFILE_MONOLITH.zeta == pytest.approx(0.90)
        assert PROFILE_MONOLITH.omega_n == pytest.approx(1.20)

    def test_gyre_values(self):
        assert PROFILE_GYRE.name == "gyre"
        assert PROFILE_GYRE.zeta == pytest.approx(0.65)
        assert PROFILE_GYRE.omega_n == pytest.approx(1.80)

    def test_nerve_values(self):
        assert PROFILE_NERVE.name == "nerve"
        assert PROFILE_NERVE.zeta == pytest.approx(0.30)
        assert PROFILE_NERVE.omega_n == pytest.approx(5.00)

    def test_tide_values(self):
        assert PROFILE_TIDE.name == "tide"
        assert PROFILE_TIDE.zeta == pytest.approx(0.55)
        assert PROFILE_TIDE.omega_n == pytest.approx(2.50)


# ---------------------------------------------------------------------------
# 测试 6: AXIS_PROFILES 轴分配合理性
# ---------------------------------------------------------------------------

class TestAxisProfileMapping:
    """验证每个轴的 profile 分配合理。"""

    def test_monolith_axes(self):
        """Monolith 分配: contamination_resistance, service_resistance。"""
        assert AXIS_PROFILES["contamination_resistance"] is PROFILE_MONOLITH
        assert AXIS_PROFILES["service_resistance"] is PROFILE_MONOLITH

    def test_gyre_axes(self):
        """Gyre 分配: boundary_distance, structural_grip_pressure, withdrawal_tendency。"""
        assert AXIS_PROFILES["boundary_distance"] is PROFILE_GYRE
        assert AXIS_PROFILES["structural_grip_pressure"] is PROFILE_GYRE
        assert AXIS_PROFILES["withdrawal_tendency"] is PROFILE_GYRE

    def test_nerve_axes(self):
        """Nerve 分配: correction_pressure, contamination_pressure。"""
        assert AXIS_PROFILES["correction_pressure"] is PROFILE_NERVE
        assert AXIS_PROFILES["contamination_pressure"] is PROFILE_NERVE

    def test_tide_axes(self):
        """Tide 分配: affective_warmth, presence_stability, collaborator_layer_pressure。"""
        assert AXIS_PROFILES["affective_warmth"] is PROFILE_TIDE
        assert AXIS_PROFILES["presence_stability"] is PROFILE_TIDE
        assert AXIS_PROFILES["collaborator_layer_pressure"] is PROFILE_TIDE
