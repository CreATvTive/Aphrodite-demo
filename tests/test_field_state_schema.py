import inspect
import json

import pytest

from src.field_state.schema import (
    DECAY_PROFILES,
    F_0,
    GROUND_STATE_VARIABLE_SPECS,
    REQUIRED_FIELD_VARIABLES,
    VALUE_BANDS,
    FieldVariable,
    RelationalFieldState,
    create_ground_state,
    create_ground_state_variables,
)


def _get_var(name: str) -> FieldVariable:
    """从基态变量中按名称获取单个变量。"""
    for var in create_ground_state_variables().values():
        if var.name == name:
            return var
    raise ValueError(f"变量 {name} 未找到")


# ============================================================
# 原有测试（保留）
# ============================================================

def test_all_required_variables_exist():
    state = create_ground_state()
    assert tuple(state.variables) == REQUIRED_FIELD_VARIABLES
    assert len(state.variables) == 10


def test_each_variable_has_valid_value_band():
    state = create_ground_state()
    for variable in state.variables.values():
        assert variable.value in VALUE_BANDS
        assert variable.baseline_value in VALUE_BANDS


def test_each_variable_has_valid_decay_profile():
    state = create_ground_state()
    for variable in state.variables.values():
        assert variable.decay_profile in DECAY_PROFILES


def test_ground_state_can_be_constructed():
    state = create_ground_state()
    assert isinstance(state, RelationalFieldState)
    assert isinstance(F_0, RelationalFieldState)


def test_ground_state_values_match_design_intent():
    variables = create_ground_state().variables
    assert variables["boundary_distance"].value == "baseline"
    assert variables["affective_warmth"].value == "baseline"
    assert variables["service_resistance"].value == "elevated"
    assert variables["contamination_resistance"].value == "baseline"
    assert variables["presence_stability"].value == "high"
    assert variables["structural_grip_pressure"].value == "low"
    assert variables["collaborator_layer_pressure"].value == "low"
    assert variables["contamination_pressure"].value == "low"


def test_required_decay_profiles():
    """衰减 profile 与设计文档 §7.3 匹配（含 Phase 29 G.2 修复）。"""
    variables = create_ground_state().variables
    assert variables["contamination_pressure"].decay_profile == "instant"
    assert variables["contamination_resistance"].decay_profile == "very_slow"
    assert variables["presence_stability"].decay_profile == "very_slow"
    assert variables["collaborator_layer_pressure"].decay_profile == "fast"
    # Phase 29 G.2 修复：correction_pressure 应从 slow 变为 medium
    assert variables["correction_pressure"].decay_profile == "medium"
    # Phase 29 G.2 修复：withdrawal_tendency 应从 slow 变为 medium
    assert variables["withdrawal_tendency"].decay_profile == "medium"


def test_behavior_affecting_is_false():
    state = create_ground_state()
    assert state.behavior_affecting is False
    for variable in state.variables.values():
        assert variable.behavior_affecting is False


def test_to_dict_is_json_serializable():
    state = create_ground_state()
    encoded = json.dumps(state.to_dict(), ensure_ascii=False)
    assert "boundary_distance" in encoded
    assert "behavior_affecting" in encoded


def test_field_variable_validation():
    with pytest.raises(ValueError, match="invalid value band"):
        FieldVariable(
            name="boundary_distance",
            value="numeric",
            baseline_value="baseline",
            decay_profile="slow",
            description="Invalid band test.",
        )
    with pytest.raises(ValueError, match="invalid decay profile"):
        FieldVariable(
            name="boundary_distance",
            value="baseline",
            baseline_value="baseline",
            decay_profile="weekly",
            description="Invalid profile test.",
        )
    with pytest.raises(ValueError, match="behavior_affecting"):
        FieldVariable(
            name="boundary_distance",
            value="baseline",
            baseline_value="baseline",
            decay_profile="slow",
            description="Behavior guard test.",
            behavior_affecting=True,
        )


def test_state_requires_exact_variable_set():
    variables = create_ground_state().variables.copy()
    variables.pop("boundary_distance")
    with pytest.raises(ValueError, match="missing"):
        RelationalFieldState(variables=variables)


def test_schema_has_no_forbidden_couplings():
    import src.field_state.schema as mod

    source = inspect.getsource(mod).lower()
    forbidden = [
        "import re",
        "re.search",
        "re.match",
        "raw_text",
        "user_text",
        "user_input",
        "llm",
        "runtime_engine",
        "router",
        "memory",
        "renderer",
        "animation",
        "avatar",
        "field_trace",
        "body_action",
    ]
    for name in forbidden:
        assert name not in source, f"schema.py should not contain {name}"


def test_no_updater_or_mapping_layer_is_implemented():
    import src.field_state.schema as mod

    source = inspect.getsource(mod)
    assert "FieldStateUpdater" not in source
    assert "update_field_state" not in source
    assert "map_to_action" not in source
    assert "BodyActionPolicy" not in source


# ============================================================
# Phase 29 新增：数值值层测试 (G.1)
# ============================================================

class TestNumericValueLayer:
    """验证数值值层与带标签层共存。"""

    def test_all_variables_have_numeric_value(self):
        """所有 10 个变量的基态同时具有 numeric_value 和 baseline_numeric_value。"""
        variables = create_ground_state_variables()
        assert len(variables) == 10
        for name, var in variables.items():
            assert isinstance(var.numeric_value, float), f"{name} 缺少 numeric_value"
            assert isinstance(var.baseline_numeric_value, float), f"{name} 缺少 baseline_numeric_value"

    def test_numeric_values_in_range(self):
        """所有数值必须在 [0.0, 1.0] 范围内。"""
        variables = create_ground_state_variables()
        for name, var in variables.items():
            assert 0.0 <= var.numeric_value <= 1.0, f"{name}.numeric_value={var.numeric_value} 超出范围"
            assert 0.0 <= var.baseline_numeric_value <= 1.0, f"{name}.baseline_numeric_value={var.baseline_numeric_value} 超出范围"

    def test_contamination_pressure_starts_zero(self):
        """contamination_pressure 的基态应为 0.0。"""
        var = _get_var("contamination_pressure")
        assert var.numeric_value == 0.0
        assert var.baseline_numeric_value == 0.0

    def test_correction_pressure_starts_zero(self):
        """correction_pressure 的基态应为 0.0。"""
        var = _get_var("correction_pressure")
        assert var.numeric_value == 0.0
        assert var.baseline_numeric_value == 0.0

    def test_presence_stability_starts_high(self):
        """presence_stability 的基态应为 0.80。"""
        var = _get_var("presence_stability")
        assert var.numeric_value == 0.80
        assert var.baseline_numeric_value == 0.80

    def test_boundary_distance_starts_mid(self):
        """boundary_distance 的基态应为 0.50。"""
        var = _get_var("boundary_distance")
        assert var.numeric_value == 0.50
        assert var.baseline_numeric_value == 0.50

    def test_affective_warmth_starts_restrained(self):
        """affective_warmth 的基态应为 0.35。"""
        var = _get_var("affective_warmth")
        assert var.numeric_value == 0.35
        assert var.baseline_numeric_value == 0.35

    def test_service_resistance_starts_elevated(self):
        """service_resistance 的基态应为 0.55。"""
        var = _get_var("service_resistance")
        assert var.numeric_value == 0.55
        assert var.baseline_numeric_value == 0.55

    def test_contamination_resistance_starts_moderate(self):
        """contamination_resistance 的基态应为 0.40。"""
        var = _get_var("contamination_resistance")
        assert var.numeric_value == 0.40
        assert var.baseline_numeric_value == 0.40

    def test_numeric_value_rejected_out_of_range(self):
        """构造时拒绝超出范围的数值。"""
        with pytest.raises(ValueError, match="numeric_value must be in"):
            FieldVariable(
                name="boundary_distance", value="baseline",
                numeric_value=1.5, baseline_numeric_value=0.50,
                baseline_value="baseline", decay_profile="slow",
                description="Range test.",
            )
        with pytest.raises(ValueError, match="baseline_numeric_value must be in"):
            FieldVariable(
                name="boundary_distance", value="baseline",
                numeric_value=0.50, baseline_numeric_value=-0.1,
                baseline_value="baseline", decay_profile="slow",
                description="Range test.",
            )

    def test_numeric_value_required_number(self):
        """构造时拒绝非数字数值。"""
        with pytest.raises(ValueError, match="numeric_value must be a number"):
            FieldVariable(
                name="boundary_distance", value="baseline",
                numeric_value="high", baseline_numeric_value=0.50,
                baseline_value="baseline", decay_profile="slow",
                description="Type test.",
            )


# ============================================================
# Phase 29 新增：衰减 profile 测试 (G.2)
# ============================================================

class TestDecayProfiles:
    """衰减 profile 与设计文档 §7.3 匹配。"""

    def test_correction_pressure_is_medium(self):
        """Phase 29 修复 G.2：correction_pressure 应为 'medium'，非 'slow'。"""
        var = _get_var("correction_pressure")
        assert var.decay_profile == "medium", f"期望 'medium'，得到 '{var.decay_profile}'"

    def test_withdrawal_tendency_is_medium(self):
        """Phase 29 修复 G.2：withdrawal_tendency 应为 'medium'，非 'slow'。"""
        var = _get_var("withdrawal_tendency")
        assert var.decay_profile == "medium", f"期望 'medium'，得到 '{var.decay_profile}'"

    def test_contamination_pressure_is_instant(self):
        var = _get_var("contamination_pressure")
        assert var.decay_profile == "instant"

    def test_contamination_resistance_is_very_slow(self):
        var = _get_var("contamination_resistance")
        assert var.decay_profile == "very_slow"

    def test_presence_stability_is_very_slow(self):
        var = _get_var("presence_stability")
        assert var.decay_profile == "very_slow"

    def test_collaborator_layer_pressure_is_fast(self):
        var = _get_var("collaborator_layer_pressure")
        assert var.decay_profile == "fast"

    def test_service_resistance_is_very_slow(self):
        var = _get_var("service_resistance")
        assert var.decay_profile == "very_slow"

    def test_structural_grip_pressure_is_fast(self):
        var = _get_var("structural_grip_pressure")
        assert var.decay_profile == "fast"

    def test_boundary_distance_is_slow(self):
        var = _get_var("boundary_distance")
        assert var.decay_profile == "slow"

    def test_affective_warmth_is_medium(self):
        var = _get_var("affective_warmth")
        assert var.decay_profile == "medium"


# ============================================================
# Phase 29 新增：序列化测试
# ============================================================

class TestSerialization:
    """验证 to_dict 包含所有字段。"""

    def test_to_dict_includes_numeric_values(self):
        """to_dict 输出必须同时包含带标签和数值字段。"""
        var = FieldVariable(
            name="boundary_distance", value="baseline",
            numeric_value=0.50, baseline_numeric_value=0.50,
            baseline_value="baseline", decay_profile="slow",
            description="Serialization test.",
        )
        d = var.to_dict()
        assert d["value"] == "baseline"
        assert d["numeric_value"] == 0.50
        assert d["baseline_value"] == "baseline"
        assert d["baseline_numeric_value"] == 0.50
        # 验证 JSON 序列化仍然有效
        s = json.dumps(d)
        assert "0.5" in s or "0.50" in s

    def test_ground_state_to_dict_json(self):
        """完整的基态可序列化为 JSON。"""
        state = create_ground_state()
        s = json.dumps(state.to_dict())
        assert "boundary_distance" in s
        assert "numeric_value" in s
        assert "baseline_numeric_value" in s

    def test_field_variable_round_trip(self):
        """to_dict 应包含所有必需字段键。"""
        var = _get_var("boundary_distance")
        d = var.to_dict()
        expected_keys = {
            "name", "value", "numeric_value",
            "baseline_value", "baseline_numeric_value",
            "decay_profile", "description", "source_note",
            "behavior_affecting",
        }
        assert set(d.keys()) == expected_keys


# ============================================================
# Phase 29 新增：基态健全性测试
# ============================================================

class TestGroundStateSanity:
    """验证基态创建和独立性的健全性。"""

    def test_create_ground_state_stable(self):
        """多次调用 create_ground_state 应返回独立实例。"""
        s1 = create_ground_state()
        s2 = create_ground_state()
        # 修改一个实例不应影响另一个（因为 dataclass 是 frozen 的，
        # 这个测试更关注引用独立性和默认值稳定性）
        for var in s1.variables.values():
            if var.name == "boundary_distance":
                assert var.numeric_value == 0.50
        for var in s2.variables.values():
            if var.name == "boundary_distance":
                assert var.numeric_value == 0.50

    def test_create_ground_state_variables_count(self):
        """create_ground_state_variables 返回恰好 10 个变量。"""
        variables = create_ground_state_variables()
        assert len(variables) == 10

    def test_all_f0_values_in_specs(self):
        """GROUND_STATE_VARIABLE_SPECS 中每个变量都有数值字段。"""
        for name in REQUIRED_FIELD_VARIABLES:
            spec = GROUND_STATE_VARIABLE_SPECS[name]
            assert "numeric_value" in spec, f"{name} 缺少 numeric_value"
            assert "baseline_numeric_value" in spec, f"{name} 缺少 baseline_numeric_value"
            assert isinstance(spec["numeric_value"], (int, float)), f"{name}.numeric_value 不是数字"
            assert isinstance(spec["baseline_numeric_value"], (int, float)), f"{name}.baseline_numeric_value 不是数字"

    def test_numeric_value_equals_baseline_in_ground_state(self):
        """基态中 numeric_value 应与 baseline_numeric_value 一致。"""
        for name, var in create_ground_state_variables().items():
            assert var.numeric_value == var.baseline_numeric_value, (
                f"{name}: numeric_value={var.numeric_value} != baseline_numeric_value={var.baseline_numeric_value}"
            )

    def test_value_label_consistent_with_numeric(self):
        """验证带标签值与数值的大致一致性（抽查关键变量）。"""
        # low: 0.00–0.10 range
        for name in ["structural_grip_pressure", "correction_pressure",
                      "withdrawal_tendency", "collaborator_layer_pressure",
                      "contamination_pressure"]:
            var = _get_var(name)
            assert var.value == "low", f"{name}: 期望 value='low'，得到 '{var.value}'"
            assert var.numeric_value <= 0.10, f"{name}: 期望 numeric_value ≤ 0.10"

        # baseline: 0.35–0.50 range
        for name in ["boundary_distance", "affective_warmth", "contamination_resistance"]:
            var = _get_var(name)
            assert var.value == "baseline", f"{name}: 期望 value='baseline'"
            assert 0.30 <= var.numeric_value <= 0.55, f"{name}: numeric_value={var.numeric_value}"

        # elevated: 0.55
        var = _get_var("service_resistance")
        assert var.value == "elevated"
        assert var.numeric_value == 0.55

        # high: 0.80
        var = _get_var("presence_stability")
        assert var.value == "high"
        assert var.numeric_value == 0.80
