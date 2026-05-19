"""测试 ProposalToFieldPerturbationAdapter 和 FieldPerturbation 数据类。

Phase 30 — 纯适配器层，不实施场动力学。
Phase 30 修复 + Phase 31: 更新以匹配 MAGNITUDE_TO_DELTA low=0.05、新规则 B/C/E 参数。
"""

from __future__ import annotations

import json

import pytest

from src.field_state.perturbation import (
    MAGNITUDE_TO_DELTA,
    FieldPerturbation,
    ProposalToFieldPerturbationAdapter,
    _compute_delta,
)


# ---------------------------------------------------------------------------
# Mock 提案 — 模拟 FieldSignalProposal（避免导入 FieldTrace 真实类）
# ---------------------------------------------------------------------------

class MockProposal:
    def __init__(self, signal_name: str, **kwargs):
        self.signal_name = signal_name
        self.evidence_sources = kwargs.get('evidence_sources', [])
        self.confidence_band = kwargs.get('confidence_band', 'medium')
        self.behavior_affecting = False


# ---------------------------------------------------------------------------
# 测试 FieldPerturbation 数据类
# ---------------------------------------------------------------------------

class TestFieldPerturbation:
    """测试 FieldPerturbation 数据类的验证和序列化。"""

    def test_valid_construction(self):
        """有效构造应成功。"""
        fp = FieldPerturbation(
            target_variable="correction_pressure",
            direction="increase",
            magnitude_band="medium",
            numeric_delta=0.10,
            duration_hint="medium",
            source_signal="response_mode_rejected",
            rationale="用户纠正响应",
            evidence_sources=["correction_observer"],
        )
        assert fp.target_variable == "correction_pressure"
        assert fp.direction == "increase"
        assert fp.magnitude_band == "medium"
        assert fp.numeric_delta == 0.10
        assert fp.duration_hint == "medium"
        assert fp.source_signal == "response_mode_rejected"
        assert fp.behavior_affecting is False

    def test_invalid_target_rejected(self):
        """不在 REQUIRED_FIELD_VARIABLES 中的目标变量应引发 ValueError。"""
        with pytest.raises(ValueError, match="REQUIRED_FIELD_VARIABLES"):
            FieldPerturbation(
                target_variable="not_a_valid_field",
                direction="increase",
                magnitude_band="low",
                numeric_delta=0.05,
                duration_hint="medium",
                source_signal="test",
            )

    def test_invalid_direction_rejected(self):
        """无效方向应引发 ValueError。"""
        with pytest.raises(ValueError, match="无效 direction"):
            FieldPerturbation(
                target_variable="correction_pressure",
                direction="upwards",
                magnitude_band="low",
                numeric_delta=0.05,
                duration_hint="medium",
                source_signal="test",
            )

    def test_invalid_magnitude_rejected(self):
        """无效幅度带应引发 ValueError。"""
        with pytest.raises(ValueError, match="无效 magnitude_band"):
            FieldPerturbation(
                target_variable="correction_pressure",
                direction="increase",
                magnitude_band="extreme",
                numeric_delta=0.05,
                duration_hint="medium",
                source_signal="test",
            )

    def test_delta_out_of_range_rejected(self):
        """超出 [-0.25, 0.25] 范围的 numeric_delta 应引发 ValueError。"""
        with pytest.raises(ValueError, match=r"numeric_delta"):
            FieldPerturbation(
                target_variable="correction_pressure",
                direction="increase",
                magnitude_band="high",
                numeric_delta=0.50,
                duration_hint="medium",
                source_signal="test",
            )

    def test_delta_negative_bound(self):
        """负边界 -0.25 应被接受。"""
        fp = FieldPerturbation(
            target_variable="affective_warmth",
            direction="decrease",
            magnitude_band="high",
            numeric_delta=-0.18,
            duration_hint="medium",
            source_signal="test",
        )
        assert fp.numeric_delta == -0.18

    def test_behavior_affecting_true_rejected(self):
        """behavior_affecting=True 应引发 ValueError。"""
        with pytest.raises(ValueError, match="behavior_affecting"):
            FieldPerturbation(
                target_variable="correction_pressure",
                direction="increase",
                magnitude_band="low",
                numeric_delta=0.05,
                duration_hint="medium",
                source_signal="test",
                behavior_affecting=True,
            )

    def test_invalid_duration_hint_rejected(self):
        """不在 DECAY_PROFILES 中的 duration_hint 应引发 ValueError。"""
        with pytest.raises(ValueError, match="无效 duration_hint"):
            FieldPerturbation(
                target_variable="correction_pressure",
                direction="increase",
                magnitude_band="low",
                numeric_delta=0.05,
                duration_hint="lightning",
                source_signal="test",
            )

    def test_to_dict_json_serializable(self):
        """to_dict 应返回 JSON 可序列化的字典。"""
        fp = FieldPerturbation(
            target_variable="affective_warmth",
            direction="decrease",
            magnitude_band="low",
            numeric_delta=-0.05,
            duration_hint="medium",
            source_signal="boundary_pressure_present",
            evidence_sources=["correction_observer", "grip_loss_observer"],
            rationale="测试理由",
        )
        d = fp.to_dict()
        assert isinstance(d, dict)
        assert d["target_variable"] == "affective_warmth"
        assert d["numeric_delta"] == -0.05
        assert d["behavior_affecting"] is False
        # 应可 JSON 序列化
        json_str = json.dumps(d)
        assert len(json_str) > 0

    def test_default_values(self):
        """默认值应为合理初始状态。"""
        fp = FieldPerturbation(
            target_variable="presence_stability",
            numeric_delta=0.0,
        )
        assert fp.direction == "stabilize"
        assert fp.magnitude_band == "low"
        assert fp.duration_hint == "medium"
        assert fp.source_signal == ""
        assert fp.rationale == ""
        assert fp.evidence_sources == []
        assert fp.behavior_affecting is False
        assert fp.source_proposal_id is None

    def test_all_10_target_variables_accepted(self):
        """所有 10 个 REQUIRED_FIELD_VARIABLES 均应被接受为目标变量。"""
        from src.field_state.schema import REQUIRED_FIELD_VARIABLES
        for var_name in REQUIRED_FIELD_VARIABLES:
            fp = FieldPerturbation(
                target_variable=var_name,
                direction="stabilize",
                magnitude_band="low",
                numeric_delta=0.0,
                duration_hint="very_slow",
                source_signal="test",
            )
            assert fp.target_variable == var_name


# ---------------------------------------------------------------------------
# 测试 _compute_delta
# ---------------------------------------------------------------------------

class TestComputeDelta:
    """测试数值 delta 计算。"""

    def test_increase_low(self):
        assert _compute_delta("increase", "low") == 0.05

    def test_increase_medium(self):
        assert _compute_delta("increase", "medium") == 0.10

    def test_increase_high(self):
        assert _compute_delta("increase", "high") == 0.18

    def test_decrease_low(self):
        assert _compute_delta("decrease", "low") == -0.05

    def test_decrease_medium(self):
        assert _compute_delta("decrease", "medium") == -0.10

    def test_decrease_high(self):
        assert _compute_delta("decrease", "high") == -0.18

    def test_stabilize_always_zero(self):
        assert _compute_delta("stabilize", "low") == 0.0
        assert _compute_delta("stabilize", "medium") == 0.0
        assert _compute_delta("stabilize", "high") == 0.0

    def test_unknown_band_returns_zero(self):
        """未知幅度带应安全回退到 0.0。"""
        assert _compute_delta("increase", "unknown") == 0.0


# ---------------------------------------------------------------------------
# 测试 MAGNITUDE_TO_DELTA 表
# ---------------------------------------------------------------------------

class TestMagnitudeTable:
    """测试 MAGNITUDE_TO_DELTA 常量。"""

    def test_has_three_entries(self):
        assert len(MAGNITUDE_TO_DELTA) == 3

    def test_keys_are_valid_bands(self):
        assert set(MAGNITUDE_TO_DELTA.keys()) == {"low", "medium", "high"}

    def test_ascending_magnitudes(self):
        """幅度值应递增。"""
        assert MAGNITUDE_TO_DELTA["low"] < MAGNITUDE_TO_DELTA["medium"] < MAGNITUDE_TO_DELTA["high"]

    def test_max_delta_within_bound(self):
        """最大 delta（high）应在 [-0.25, 0.25] 范围内。"""
        assert 0.0 < MAGNITUDE_TO_DELTA["high"] <= 0.25

    def test_low_is_0_05(self):
        """MAGNITUDE_TO_DELTA low 应为 0.05（Phase 30 修复）。"""
        assert MAGNITUDE_TO_DELTA["low"] == 0.05


# ---------------------------------------------------------------------------
# 测试 ProposalToFieldPerturbationAdapter
# ---------------------------------------------------------------------------

class TestProposalToFieldPerturbationAdapter:
    """测试适配器将 FieldSignalProposal 转化为 FieldPerturbation。"""

    adapter = ProposalToFieldPerturbationAdapter()

    def test_empty_proposals_returns_empty(self):
        """空输入应为空输出。"""
        result = self.adapter.adapt([])
        assert result == []

    def test_none_proposals_returns_empty(self):
        """None 输入应以空列表安全处理。"""
        result = self.adapter.adapt(None)
        assert result == []

    # -- 规则 A：response_mode_rejected ------------------------------------

    def test_response_mode_rejected(self):
        """规则 A: response_mode_rejected → 基础 3 个扰动（无子类型匹配时）。"""
        p = MockProposal("response_mode_rejected", evidence_sources=["correction_observer"])
        result = self.adapter.adapt([p])
        assert len(result) == 3

        targets = {fp.target_variable for fp in result}
        assert "correction_pressure" in targets
        assert "service_resistance" in targets
        assert "presence_stability" in targets

        # presence_stability 应为 stabilize 且 delta=0
        ps = [fp for fp in result if fp.target_variable == "presence_stability"]
        assert len(ps) == 1
        assert ps[0].direction == "stabilize"
        assert ps[0].numeric_delta == 0.0

        for fp in result:
            assert fp.source_signal == "response_mode_rejected"
            assert fp.behavior_affecting is False

    # -- 规则 A 子类型：sanitization / contamination -------------------------

    def test_response_mode_rejected_sanitization_subtype(self):
        """规则 A 子类型：evidence 含 sanitiz → 额外 contamination_resistance + contamination_pressure。"""
        p = MockProposal("response_mode_rejected", evidence_sources=["sanitization_observer"])
        result = self.adapter.adapt([p])
        # 基础 3 + 子类型 2 = 5
        assert len(result) == 5

        targets = {fp.target_variable for fp in result}
        assert "contamination_resistance" in targets
        assert "contamination_pressure" in targets

        cr = [fp for fp in result if fp.target_variable == "contamination_resistance"]
        assert len(cr) == 1
        assert cr[0].direction == "increase"
        assert cr[0].magnitude_band == "low"
        assert cr[0].numeric_delta == 0.05

        cp = [fp for fp in result if fp.target_variable == "contamination_pressure"]
        assert len(cp) == 1
        assert cp[0].direction == "increase"
        assert cp[0].magnitude_band == "low"
        assert cp[0].duration_hint == "instant"

    def test_response_mode_rejected_contamination_subtype(self):
        """规则 A 子类型：evidence 含 contamin → 额外 contamination_resistance + contamination_pressure。"""
        p = MockProposal("response_mode_rejected", evidence_sources=["contamination_detector"])
        result = self.adapter.adapt([p])
        assert len(result) == 5

        targets = {fp.target_variable for fp in result}
        assert "contamination_resistance" in targets
        assert "contamination_pressure" in targets

    # -- 规则 A 子类型：comfort / customer-service -------------------------

    def test_response_mode_rejected_comfort_subtype(self):
        """规则 A 子类型：evidence 含 comfort → 额外 service_resistance。"""
        p = MockProposal("response_mode_rejected", evidence_sources=["comfort_zone_monitor"])
        result = self.adapter.adapt([p])
        # 基础 3 + 子类型 1 = 4
        assert len(result) == 4

        sr = [fp for fp in result if fp.target_variable == "service_resistance"]
        # 基础已有 1 个 service_resistance，子类型再添加 1 个 = 2
        assert len(sr) == 2
        assert all(fp.direction == "increase" for fp in sr)
        assert all(fp.magnitude_band == "low" for fp in sr)

    def test_response_mode_rejected_customer_subtype(self):
        """规则 A 子类型：evidence 含 customer → 额外 service_resistance。"""
        p = MockProposal("response_mode_rejected", evidence_sources=["customer_service_pattern"])
        result = self.adapter.adapt([p])
        assert len(result) == 4

    # -- 规则 B：actionable_grip_missing -----------------------------------

    def test_actionable_grip_missing(self):
        """规则 B: actionable_grip_missing → 6 个扰动（Phase 30 修复后）。"""
        p = MockProposal("actionable_grip_missing", evidence_sources=["grip_loss_observer"])
        result = self.adapter.adapt([p])
        assert len(result) == 6

        targets = {fp.target_variable for fp in result}
        assert "structural_grip_pressure" in targets
        assert "collaborator_layer_pressure" in targets
        assert "presence_stability" in targets
        assert "affective_warmth" in targets
        assert "boundary_distance" in targets
        assert "withdrawal_tendency" in targets

        # structural_grip_pressure: increase / medium
        sg = [fp for fp in result if fp.target_variable == "structural_grip_pressure"]
        assert len(sg) == 1
        assert sg[0].direction == "increase"
        assert sg[0].magnitude_band == "medium"

        # affective_warmth: increase / low（温暖回应）
        aw = [fp for fp in result if fp.target_variable == "affective_warmth"]
        assert len(aw) == 1
        assert aw[0].direction == "increase"
        assert aw[0].magnitude_band == "low"
        assert aw[0].numeric_delta == 0.05

        # boundary_distance: decrease / low（减少边界距离）
        bd = [fp for fp in result if fp.target_variable == "boundary_distance"]
        assert len(bd) == 1
        assert bd[0].direction == "decrease"
        assert bd[0].magnitude_band == "low"
        assert bd[0].numeric_delta == -0.05

        # withdrawal_tendency: decrease / low（抑制退缩）
        wt = [fp for fp in result if fp.target_variable == "withdrawal_tendency"]
        assert len(wt) == 1
        assert wt[0].direction == "decrease"
        assert wt[0].magnitude_band == "low"
        assert wt[0].numeric_delta == -0.05

        for fp in result:
            assert fp.behavior_affecting is False
            assert fp.source_signal == "actionable_grip_missing"

    # -- 规则 C：boundary_pressure_present ---------------------------------

    def test_boundary_pressure_present(self):
        """规则 C: boundary_pressure_present → 5 个扰动，boundary_distance 为 medium(0.10)。"""
        p = MockProposal("boundary_pressure_present")
        result = self.adapter.adapt([p])
        assert len(result) == 5

        # boundary_distance: increase / medium（Phase 30 修复：曾为 high/0.18）
        bd = [fp for fp in result if fp.target_variable == "boundary_distance"]
        assert len(bd) == 1
        assert bd[0].direction == "increase"
        assert bd[0].magnitude_band == "medium"
        assert bd[0].numeric_delta == 0.10

        # contamination_pressure: increase / high / instant
        cp = [fp for fp in result if fp.target_variable == "contamination_pressure"]
        assert len(cp) == 1
        assert cp[0].direction == "increase"
        assert cp[0].magnitude_band == "high"
        assert cp[0].duration_hint == "instant"

        # withdrawal_tendency: increase / low
        wt = [fp for fp in result if fp.target_variable == "withdrawal_tendency"]
        assert len(wt) == 1
        assert wt[0].direction == "increase"
        assert wt[0].magnitude_band == "low"

        # affective_warmth: decrease / low
        aw = [fp for fp in result if fp.target_variable == "affective_warmth"]
        assert len(aw) == 1
        assert aw[0].direction == "decrease"
        assert aw[0].magnitude_band == "low"

        # contamination_resistance: increase / medium / slow
        cr = [fp for fp in result if fp.target_variable == "contamination_resistance"]
        assert len(cr) == 1
        assert cr[0].direction == "increase"
        assert cr[0].magnitude_band == "medium"
        assert cr[0].duration_hint == "slow"

        for fp in result:
            assert fp.behavior_affecting is False
            assert fp.source_signal == "boundary_pressure_present"

    # -- 规则 D：technical_layer_needed ------------------------------------

    def test_technical_layer_needed(self):
        """规则 D: technical_layer_needed → 3 个扰动。"""
        p = MockProposal("technical_layer_needed")
        result = self.adapter.adapt([p])
        assert len(result) == 3

        targets = {fp.target_variable for fp in result}
        assert "collaborator_layer_pressure" in targets
        assert "structural_grip_pressure" in targets
        assert "service_resistance" in targets

        # collaborator_layer_pressure: increase / high / fast
        cl = [fp for fp in result if fp.target_variable == "collaborator_layer_pressure"]
        assert len(cl) == 1
        assert cl[0].direction == "increase"
        assert cl[0].magnitude_band == "high"
        assert cl[0].duration_hint == "fast"

        # structural_grip_pressure: decrease / low
        sg = [fp for fp in result if fp.target_variable == "structural_grip_pressure"]
        assert len(sg) == 1
        assert sg[0].direction == "decrease"
        assert sg[0].numeric_delta < 0
        assert sg[0].magnitude_band == "low"

        # service_resistance: stabilize
        sr = [fp for fp in result if fp.target_variable == "service_resistance"]
        assert len(sr) == 1
        assert sr[0].direction == "stabilize"
        assert sr[0].numeric_delta == 0.0

        for fp in result:
            assert fp.behavior_affecting is False
            assert fp.source_signal == "technical_layer_needed"

    # -- 规则 E：source_material_must_not_be_sanitized ---------------------

    def test_source_material_protection(self):
        """规则 E: source_material_must_not_be_sanitized → 4 个扰动，
        contamination_resistance 和 service_resistance 为 low(0.05)。"""
        p = MockProposal("source_material_must_not_be_sanitized")
        result = self.adapter.adapt([p])
        assert len(result) == 4

        targets = {fp.target_variable for fp in result}
        assert "contamination_resistance" in targets
        assert "service_resistance" in targets
        assert "correction_pressure" in targets
        assert "affective_warmth" in targets

        # contamination_resistance: increase / low / slow（Phase 30 修复：曾为 medium/0.10）
        cr = [fp for fp in result if fp.target_variable == "contamination_resistance"]
        assert len(cr) == 1
        assert cr[0].direction == "increase"
        assert cr[0].magnitude_band == "low"
        assert cr[0].numeric_delta == 0.05
        assert cr[0].duration_hint == "slow"

        # service_resistance: increase / low / slow（Phase 30 修复：曾为 medium/0.10）
        sr = [fp for fp in result if fp.target_variable == "service_resistance"]
        assert len(sr) == 1
        assert sr[0].direction == "increase"
        assert sr[0].magnitude_band == "low"
        assert sr[0].numeric_delta == 0.05
        assert sr[0].duration_hint == "slow"

        # affective_warmth: stabilize
        aw = [fp for fp in result if fp.target_variable == "affective_warmth"]
        assert len(aw) == 1
        assert aw[0].direction == "stabilize"
        assert aw[0].numeric_delta == 0.0

        for fp in result:
            assert fp.behavior_affecting is False
            assert fp.source_signal == "source_material_must_not_be_sanitized"

    # -- 规则 F：no_observable_field_signal --------------------------------

    def test_no_observable_returns_empty(self):
        """规则 F: no_observable_field_signal → 空列表。"""
        p = MockProposal("no_observable_field_signal")
        result = self.adapter.adapt([p])
        assert result == []

    # -- 未知信号 ------------------------------------------------

    def test_unknown_signal_returns_empty(self):
        """未知信号应静默忽略并返回空列表。"""
        p = MockProposal("some_future_signal_type")
        result = self.adapter.adapt([p])
        assert result == []

    # -- 边界情况 ------------------------------------------------

    def test_multiple_proposals(self):
        """多个提议应聚合扰动。"""
        p1 = MockProposal("response_mode_rejected")
        p2 = MockProposal("actionable_grip_missing")
        result = self.adapter.adapt([p1, p2])
        # 3 (无子类型) + 6 (规则 B) = 9
        assert len(result) == 9

    def test_mixed_valid_and_invalid(self):
        """混合已知和未知信号：应仅返回已知信号的扰动。"""
        p1 = MockProposal("technical_layer_needed")
        p2 = MockProposal("unknown_signal")
        p3 = MockProposal("no_observable_field_signal")
        result = self.adapter.adapt([p1, p2, p3])
        # 仅 technical_layer_needed (3 个扰动)
        assert len(result) == 3
        assert all(fp.source_signal == "technical_layer_needed" for fp in result)

    # -- 全局不变量 ------------------------------------------------

    def test_all_targets_valid_field_variables(self):
        """适配器生成的所有扰动均应有有效目标变量。"""
        from src.field_state.schema import REQUIRED_FIELD_VARIABLES

        proposals = [
            MockProposal("response_mode_rejected"),
            MockProposal("actionable_grip_missing"),
            MockProposal("boundary_pressure_present"),
            MockProposal("technical_layer_needed"),
            MockProposal("source_material_must_not_be_sanitized"),
        ]
        result = self.adapter.adapt(proposals)
        assert len(result) > 0
        for fp in result:
            assert fp.target_variable in REQUIRED_FIELD_VARIABLES, (
                f"{fp.target_variable} 不在 REQUIRED_FIELD_VARIABLES 中"
            )

    def test_numeric_deltas_bounded(self):
        """所有生成的数值 delta 均应在 [-0.25, 0.25] 范围内。"""
        proposals = [
            MockProposal("response_mode_rejected"),
            MockProposal("actionable_grip_missing"),
            MockProposal("boundary_pressure_present"),
            MockProposal("technical_layer_needed"),
            MockProposal("source_material_must_not_be_sanitized"),
        ]
        result = self.adapter.adapt(proposals)
        for fp in result:
            assert -0.25 <= fp.numeric_delta <= 0.25, (
                f"{fp.target_variable} 的 numeric_delta={fp.numeric_delta} 超出范围"
            )

    def test_adapter_does_not_mutate_proposals(self):
        """适配器不应修改输入提案。"""
        p = MockProposal("response_mode_rejected", evidence_sources=["src"])
        original_signal = p.signal_name
        original_evidence = list(p.evidence_sources)
        _ = self.adapter.adapt([p])
        assert p.signal_name == original_signal
        assert p.evidence_sources == original_evidence

    def test_behavior_affecting_false_all(self):
        """适配器生成的每个扰动均应将 behavior_affecting 设为 False。"""
        proposals = [
            MockProposal("response_mode_rejected"),
            MockProposal("actionable_grip_missing"),
            MockProposal("boundary_pressure_present"),
        ]
        result = self.adapter.adapt(proposals)
        assert len(result) > 0
        for fp in result:
            assert fp.behavior_affecting is False, (
                f"扰动 {fp.source_signal}→{fp.target_variable} 的 behavior_affecting 应为 False"
            )

    def test_rationale_present_all(self):
        """每个扰动均应具有非空 rationale。"""
        proposals = [
            MockProposal("response_mode_rejected"),
            MockProposal("actionable_grip_missing"),
            MockProposal("boundary_pressure_present"),
            MockProposal("technical_layer_needed"),
            MockProposal("source_material_must_not_be_sanitized"),
        ]
        result = self.adapter.adapt(proposals)
        for fp in result:
            assert fp.rationale, (
                f"扰动 {fp.source_signal}→{fp.target_variable} 的 rationale 为空"
            )
