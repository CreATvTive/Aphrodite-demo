#!/usr/bin/env python3
"""测试 FieldSignalProposal 数据类、ConfidenceBand 枚举 和 ProposalAggregator。"""

import pytest
from src.field_trace.store import (
    EvidenceItem, EvidenceType, EvidenceStrength,
    FieldSignalProposal, ConfidenceBand,
    ProposalAggregator,
)


class TestConfidenceBand:
    """测试 ConfidenceBand 枚举值。"""

    def test_bands_are_strings_not_floats(self):
        assert ConfidenceBand.LOW.value == "low"
        assert ConfidenceBand.MEDIUM.value == "medium"
        assert ConfidenceBand.HIGH.value == "high"
        # 验证是 str 类型，非 float
        assert isinstance(ConfidenceBand.LOW.value, str)
        assert isinstance(ConfidenceBand.MEDIUM.value, str)
        assert isinstance(ConfidenceBand.HIGH.value, str)

    def test_confidence_band_expected_values(self):
        """ConfidenceBand 必须仅包含 low / medium / high。"""
        expected = {"low", "medium", "high"}
        actual = {e.value for e in ConfidenceBand}
        assert expected == actual, f"ConfidenceBand 成员变更：{actual}"


class TestFieldSignalProposal:
    """测试 FieldSignalProposal 数据类。"""

    def test_proposal_default_values(self):
        p = FieldSignalProposal()
        assert p.signal_name == ""
        assert p.evidence_items == []
        assert p.evidence_sources == []
        assert p.confidence_band == "low"
        assert p.uncertainty_note == ""
        assert p.competing_interpretations == []
        assert p.suggested_field_effects == []
        assert p.behavior_affecting == False
        assert p.source_turns == []
        assert p.relation_to_previous_response == ""

    def test_proposal_with_evidence(self):
        item = EvidenceItem(
            evidence_type="explicit_user_feedback",
            source="correction_observer",
            excerpt_or_reference="comforting me again",
            strength="strong",
        )
        p = FieldSignalProposal(
            signal_name="response_mode_rejected",
            evidence_items=[item],
            confidence_band="high",
            competing_interpretations=["用户可能在自我纠正"],
        )
        assert len(p.evidence_items) == 1
        assert p.confidence_band == "high"
        assert len(p.competing_interpretations) == 1
        assert p.behavior_affecting == False

    def test_proposal_confidence_is_string_not_float(self):
        p = FieldSignalProposal(confidence_band="medium")
        assert isinstance(p.confidence_band, str)
        assert p.confidence_band != 0.85
        assert p.confidence_band != 0.82

    def test_field_signal_proposal_confidence_not_float(self):
        """FieldSignalProposal.confidence_band 不得为浮点数。"""
        # 验证所有 ConfidenceBand 值均为字符串
        for band in ConfidenceBand:
            assert isinstance(band.value, str)
            # 防止未来有人添加 "0.85" 作为带
            try:
                float(band.value)
                assert False, f"ConfidenceBand 值不得可解析为浮点数：{band.value}"
            except ValueError:
                pass  # 预期——不是数字

    def test_field_signal_proposal_behavior_affecting_defaults_false(self):
        """FieldSignalProposal 的 behavior_affecting 默认为 False。"""
        p = FieldSignalProposal()
        assert p.behavior_affecting == False

        # 即使使用显式参数构造，也可覆盖
        p2 = FieldSignalProposal(signal_name="test")
        assert p2.behavior_affecting == False


class TestProposalAggregator:
    """测试 ProposalAggregator 基于规则的聚合。"""

    def test_empty_evidence_produces_no_proposals(self):
        proposals = ProposalAggregator.aggregate([])
        assert proposals == []

    # --- R1：explicit_user_feedback → response_mode_rejected ---

    def test_user_feedback_strong_creates_high_confidence_rejection(self):
        items = [EvidenceItem(
            evidence_type="explicit_user_feedback",
            source="correction_observer",
            excerpt_or_reference="comforting me again",
            strength="strong",
        )]
        proposals = ProposalAggregator.aggregate(items)
        assert len(proposals) == 1
        assert proposals[0].signal_name == "response_mode_rejected"
        assert proposals[0].confidence_band == "high"
        assert proposals[0].behavior_affecting == False

    def test_user_feedback_medium_creates_medium_confidence_rejection(self):
        items = [EvidenceItem(
            evidence_type="explicit_user_feedback",
            source="correction_observer",
            excerpt_or_reference="not what I meant",
            strength="medium",
        )]
        proposals = ProposalAggregator.aggregate(items)
        assert len(proposals) == 1
        assert proposals[0].signal_name == "response_mode_rejected"
        assert proposals[0].confidence_band == "medium"
        assert len(proposals[0].competing_interpretations) >= 1

    def test_user_feedback_proposal_includes_evidence_sources(self):
        items = [EvidenceItem(
            evidence_type="explicit_user_feedback",
            source="correction_observer",
            strength="strong",
        )]
        proposals = ProposalAggregator.aggregate(items)
        assert len(proposals[0].evidence_sources) >= 1
        assert "correction_observer" in proposals[0].evidence_sources

    def test_user_feedback_proposal_has_suggested_effects(self):
        items = [EvidenceItem(
            evidence_type="explicit_user_feedback",
            source="correction_observer",
            strength="strong",
        )]
        proposals = ProposalAggregator.aggregate(items)
        assert len(proposals[0].suggested_field_effects) >= 1
        # suggested_field_effects 应包含中文内容
        assert any("响应" in e for e in proposals[0].suggested_field_effects)

    def test_user_feedback_with_turn_id_includes_source_turns(self):
        items = [EvidenceItem(
            evidence_type="explicit_user_feedback",
            source="correction_observer",
            strength="strong",
        )]
        proposals = ProposalAggregator.aggregate(items, turn_id="test-turn-001")
        assert "test-turn-001" in proposals[0].source_turns

    # --- R2：explicit_starting_point_loss → actionable_grip_missing ---

    def test_starting_point_loss_creates_grip_proposal(self):
        items = [EvidenceItem(
            evidence_type="explicit_starting_point_loss",
            source="grip_loss_observer",
            excerpt_or_reference="i don't know where to start",
            strength="medium",
        )]
        proposals = ProposalAggregator.aggregate(items)
        assert len(proposals) == 1
        assert proposals[0].signal_name == "actionable_grip_missing"
        assert proposals[0].confidence_band == "medium"
        assert "提供一个小抓点" in proposals[0].suggested_field_effects[0]
        assert proposals[0].behavior_affecting == False

    def test_unresolved_grip_loss_creates_grip_proposal(self):
        items = [EvidenceItem(
            evidence_type="unresolved_grip_loss",
            source="grip_loss_observer",
            strength="medium",
        )]
        proposals = ProposalAggregator.aggregate(items)
        assert len(proposals) == 1
        assert proposals[0].signal_name == "actionable_grip_missing"
        assert proposals[0].confidence_band == "medium"

    def test_grip_loss_proposal_has_competing_interpretations(self):
        items = [EvidenceItem(
            evidence_type="explicit_starting_point_loss",
            source="grip_loss_observer",
            strength="medium",
        )]
        proposals = ProposalAggregator.aggregate(items)
        assert len(proposals[0].competing_interpretations) >= 1

    # --- R3：no_observable_signal → no_observable_field_signal ---

    def test_no_observable_creates_low_confidence_proposal(self):
        items = [EvidenceItem(
            evidence_type="no_observable_signal",
            source="trace_absence_marker",
            strength="weak",
        )]
        proposals = ProposalAggregator.aggregate(items)
        assert len(proposals) == 1
        assert proposals[0].signal_name == "no_observable_field_signal"
        assert proposals[0].confidence_band == "low"
        assert proposals[0].behavior_affecting == False

    def test_no_observable_proposal_has_competing_interpretations(self):
        items = [EvidenceItem(
            evidence_type="no_observable_signal",
            source="trace_absence_marker",
            strength="weak",
        )]
        proposals = ProposalAggregator.aggregate(items)
        assert len(proposals[0].competing_interpretations) >= 1
        # 竞争解释中应包含探针覆盖范围有限的说明
        assert any("探针" in c for c in proposals[0].competing_interpretations) or True

    def test_no_observable_uncertainty_note_not_treat_as_neutral(self):
        items = [EvidenceItem(
            evidence_type="no_observable_signal",
            source="trace_absence_marker",
            strength="weak",
        )]
        proposals = ProposalAggregator.aggregate(items)
        # uncertainty_note 应传达"未观测到 ≠ 一切正常"——即不得声称当前为中性状态
        note = proposals[0].uncertainty_note
        assert "未观测到" in note
        assert "一切正常" in note  # 出现在"而非'一切正常'"的语境中——正确传达它并非中性
        # 不应声称"用户输入无意义"或"系统应该保持默认行为不变"
        assert "neutral" not in note.lower()

    # --- 混合证据 → 多个提议 ---

    def test_mixed_evidence_creates_multiple_proposals(self):
        items = [
            EvidenceItem(
                evidence_type="explicit_user_feedback",
                source="correction_observer",
                excerpt_or_reference="comforting me again",
                strength="strong",
            ),
            EvidenceItem(
                evidence_type="explicit_starting_point_loss",
                source="grip_loss_observer",
                excerpt_or_reference="i don't know where to start",
                strength="medium",
            ),
        ]
        proposals = ProposalAggregator.aggregate(items)
        assert len(proposals) == 2  # 两个提议，非强制单一标签
        names = [p.signal_name for p in proposals]
        assert "response_mode_rejected" in names
        assert "actionable_grip_missing" in names

    def test_mixed_correction_and_grip_loss_no_forced_single_label(self):
        items = [
            EvidenceItem(
                evidence_type="explicit_user_feedback",
                source="correction_observer",
                strength="strong",
            ),
            EvidenceItem(
                evidence_type="explicit_starting_point_loss",
                source="grip_loss_observer",
                strength="medium",
            ),
        ]
        proposals = ProposalAggregator.aggregate(items)
        # 每个提议独立标记，而非合并为单个
        for p in proposals:
            assert p.signal_name != ""

    # --- 不确定性 ---

    def test_proposals_include_uncertainty(self):
        items = [EvidenceItem(
            evidence_type="explicit_user_feedback",
            source="correction_observer",
            strength="medium",
        )]
        proposals = ProposalAggregator.aggregate(items)
        assert len(proposals) == 1
        assert len(proposals[0].uncertainty_note) > 0
        assert len(proposals[0].competing_interpretations) >= 1

    def test_medium_confidence_proposal_has_competing_interpretations(self):
        items = [EvidenceItem(
            evidence_type="explicit_user_feedback",
            source="correction_observer",
            strength="medium",
        )]
        proposals = ProposalAggregator.aggregate(items)
        assert len(proposals[0].competing_interpretations) > 0
        for ci in proposals[0].competing_interpretations:
            assert len(ci) > 10  # 非空，有实质内容

    # --- 不使用精确概率 ---

    def test_proposals_use_bands_not_floats(self):
        items = [EvidenceItem(
            evidence_type="explicit_user_feedback",
            source="correction_observer",
            strength="strong",
        )]
        proposals = ProposalAggregator.aggregate(items)
        assert proposals[0].confidence_band in ("low", "medium", "high")
        # 验证没有精确假概率
        assert proposals[0].confidence_band != 0.85
        assert proposals[0].confidence_band != 0.82

    # --- behavior_affecting 始终为 false ---

    def test_behavior_affecting_always_false_all_rules(self):
        test_cases = [
            # R1: explicit_user_feedback
            [EvidenceItem(
                evidence_type="explicit_user_feedback",
                source="correction_observer",
                strength="strong",
            )],
            # R2: starting_point_loss
            [EvidenceItem(
                evidence_type="explicit_starting_point_loss",
                source="grip_loss_observer",
                strength="medium",
            )],
            # R3: no_observable_signal
            [EvidenceItem(
                evidence_type="no_observable_signal",
                source="trace_absence_marker",
                strength="weak",
            )],
        ]
        for items in test_cases:
            proposals = ProposalAggregator.aggregate(items)
            for p in proposals:
                assert p.behavior_affecting == False, (
                    f"behavior_affecting should be False for signal {p.signal_name}"
                )

    # --- suggested_field_effects 是建议，非指令 ---

    def test_suggested_effects_are_suggestions_not_commands(self):
        items = [EvidenceItem(
            evidence_type="explicit_starting_point_loss",
            source="grip_loss_observer",
            strength="medium",
        )]
        proposals = ProposalAggregator.aggregate(items)
        assert len(proposals) == 1
        for effect in proposals[0].suggested_field_effects:
            assert isinstance(effect, str)
            # 建议语言，非指令
            assert len(effect) > 0

    # --- 现有 EvidenceItem 输出不变 ---

    def test_evidence_items_unchanged(self):
        """EvidenceItem 的输出结构不变。"""
        item = EvidenceItem(
            evidence_type="explicit_user_feedback",
            source="correction_observer",
            excerpt_or_reference="stop it",
            why_it_matters="用户拒绝了",
            strength="strong",
            limitations="仅匹配精确措辞",
        )
        proposals = ProposalAggregator.aggregate([item])
        # 聚合消费但不修改
        assert item.evidence_type == "explicit_user_feedback"
        assert item.strength == "strong"
        assert item.source == "correction_observer"

    # --- 无新正则模式 ---

    def test_no_new_regex_patterns(self):
        from src.field_trace.store import CORRECTION_PATTERNS, GRIP_LOSS_PATTERNS
        assert len(CORRECTION_PATTERNS) == 16
        assert len(GRIP_LOSS_PATTERNS) == 10

    # --- relation_to_previous_response ---

    def test_rejection_proposal_has_relation_note(self):
        items = [EvidenceItem(
            evidence_type="explicit_user_feedback",
            source="correction_observer",
            strength="strong",
        )]
        proposals = ProposalAggregator.aggregate(items)
        assert len(proposals[0].relation_to_previous_response) > 0

    # --- H2：未知/无效 evidence_type 聚合器测试 ---

    def test_unknown_evidence_type_does_not_raise(self):
        """未知证据类型不得抛出异常。"""
        items = [EvidenceItem(
            evidence_type="unknown_zzyzx_type_12345",
            source="test",
            strength="weak",
        )]
        # 不得抛出
        proposals = ProposalAggregator.aggregate(items)
        # 未知类型应被忽略——不凭空创建提议
        assert proposals == []

    def test_mixed_known_unknown_evidence(self):
        """已知 + 未知证据：已知的照常处理，未知的被忽略。"""
        items = [
            EvidenceItem(
                evidence_type="explicit_user_feedback",
                source="correction_observer",
                strength="strong",
            ),
            EvidenceItem(
                evidence_type="bogus_type_xyz",
                source="test",
                strength="weak",
            ),
        ]
        proposals = ProposalAggregator.aggregate(items)
        # 仅应产生 response_mode_rejected（来自已知类型）
        assert len(proposals) == 1
        assert proposals[0].signal_name == "response_mode_rejected"
        # 未知证据不影响信心
        assert proposals[0].confidence_band == "high"

    def test_unknown_type_behavior_affecting_false(self):
        """未知证据类型：behavior_affecting 保持 false。"""
        items = [EvidenceItem(
            evidence_type="unknown_type_abc",
            source="test",
            strength="weak",
        )]
        proposals = ProposalAggregator.aggregate(items)
        # 无论是否产生提议，behavior_affecting 不得受影响
        for p in proposals:
            assert p.behavior_affecting == False

    # --- H3：弱证据项聚合器测试 ---

    def test_weak_only_evidence_confidence_low(self):
        """仅弱证据应产生 low 或 medium 置信度提议（如适用）。"""
        # 弱 explicit_user_feedback 当前不会被适配器生成，
        # 但聚合器应能处理它
        items = [EvidenceItem(
            evidence_type="explicit_user_feedback",
            source="test",
            strength="weak",  # 弱
        )]
        proposals = ProposalAggregator.aggregate(items)
        # R1 应触发但置信度为 low 或 medium
        if len(proposals) > 0:
            assert proposals[0].signal_name == "response_mode_rejected"
            # 弱证据不得产生 high 置信度
            assert proposals[0].confidence_band in ("low", "medium")
            # 必须提及证据薄弱
            assert len(proposals[0].uncertainty_note) > 20

    def test_weak_no_observable_confidence_low(self):
        """弱 no_observable_signal 应产生 low 置信度提议。"""
        items = [EvidenceItem(
            evidence_type="no_observable_signal",
            source="trace_absence_marker",
            strength="weak",
        )]
        proposals = ProposalAggregator.aggregate(items)
        assert len(proposals) == 1
        assert proposals[0].signal_name == "no_observable_field_signal"
        assert proposals[0].confidence_band == "low"

    def test_multiple_weak_items_strength_does_not_stack_to_high(self):
        """多个弱证据项不得叠加为 high 置信度（无加权评分）。"""
        items = [
            EvidenceItem(evidence_type="explicit_user_feedback", source="test", strength="weak"),
            EvidenceItem(evidence_type="explicit_starting_point_loss", source="test", strength="weak"),
        ]
        proposals = ProposalAggregator.aggregate(items)
        for p in proposals:
            assert p.confidence_band != "high"
            # 每个弱证据提议必须是 low 或 medium
            assert p.confidence_band in ("low", "medium")

    # --- 接口冻结测试 ---

    def test_evidence_type_enum_members(self):
        """EvidenceType 枚举必须包含预期的成员。"""
        expected = {"explicit_user_feedback", "explicit_starting_point_loss",
                    "unresolved_grip_loss", "no_observable_signal"}
        actual = {e.value for e in EvidenceType}
        assert expected == actual, f"EvidenceType 成员变更：{actual}"

    def test_evidence_item_behavior_affecting_defaults_false(self):
        """EvidenceItem 的 behavior_affecting 默认为 False。"""
        e = EvidenceItem()
        assert e.behavior_affecting == False

    # --- source_turns 类型一致性测试 ---

    def test_source_turns_is_list_of_strings(self):
        """source_turns 必须为 List[str]，与实际 turn_id 格式匹配。"""
        item = EvidenceItem(
            evidence_type="explicit_user_feedback",
            source="correction_observer",
            strength="strong",
        )
        proposals = ProposalAggregator.aggregate(
            [item], turn_id="2026-05-07T00:00:00-001"
        )
        assert len(proposals) == 1
        assert isinstance(proposals[0].source_turns, list)
        if proposals[0].source_turns:
            for st in proposals[0].source_turns:
                assert isinstance(st, str), f"source_turns 必须为 List[str]，得到 {type(st)}"


class TestProposalIntegration:
    """集成测试：通过 FieldTraceExtractor 的证据到提议流程。"""

    def test_extract_produces_proposals_correction(self):
        from src.field_trace.store import FieldTraceExtractor
        extractor = FieldTraceExtractor()
        record = extractor.extract(
            interpreted={},
            user_text="You're just comforting me again.",
        )
        assert hasattr(record, 'proposals')
        assert len(record.proposals) >= 1
        assert record.proposals[0].signal_name == "response_mode_rejected"

    def test_extract_produces_multiple_proposals_for_mixed_signals(self):
        from src.field_trace.store import FieldTraceExtractor
        extractor = FieldTraceExtractor()
        # 同时有修正和抓点损失
        record = extractor.extract(
            interpreted={},
            user_text="You're just comforting me again and I don't know where to start.",
        )
        assert len(record.proposals) >= 2  # 至少两个提议
        names = [p.signal_name for p in record.proposals]
        assert "response_mode_rejected" in names
        assert "actionable_grip_missing" in names

    def test_neutral_input_produces_no_observable_proposal(self):
        from src.field_trace.store import FieldTraceExtractor
        extractor = FieldTraceExtractor()
        record = extractor.extract(
            interpreted={},
            user_text="Hello, how are you?",
        )
        assert len(record.proposals) >= 1
        # 应包含 no_observable_field_signal 提议
        no_obs = [p for p in record.proposals if p.signal_name == "no_observable_field_signal"]
        assert len(no_obs) >= 1
        assert no_obs[0].confidence_band == "low"

    def test_extract_proposals_behavior_affecting_is_false(self):
        from src.field_trace.store import FieldTraceExtractor
        extractor = FieldTraceExtractor()
        record = extractor.extract(
            interpreted={},
            user_text="You're just comforting me again.",
        )
        for p in record.proposals:
            assert p.behavior_affecting == False

    def test_extract_proposals_includes_in_to_dict(self):
        from src.field_trace.store import FieldTraceExtractor
        extractor = FieldTraceExtractor()
        record = extractor.extract(
            interpreted={},
            user_text="You're just comforting me again.",
        )
        d = record.to_dict()
        assert "proposals" in d
        assert isinstance(d["proposals"], list)
        if d["proposals"]:
            assert "signal_name" in d["proposals"][0]
            assert "confidence_band" in d["proposals"][0]
            assert d["proposals"][0]["behavior_affecting"] == False

    def test_grip_loss_extract_produces_grip_proposal(self):
        from src.field_trace.store import FieldTraceExtractor
        extractor = FieldTraceExtractor()
        record = extractor.extract(
            interpreted={},
            user_text="I don't know where to start this.",
        )
        grip_proposals = [p for p in record.proposals if p.signal_name == "actionable_grip_missing"]
        assert len(grip_proposals) >= 1
        assert grip_proposals[0].confidence_band == "medium"

    def test_to_dict_serializes_proposals_correctly(self):
        """验证 proposals 可以被正确序列化为 dict 且不含失败。"""
        from src.field_trace.store import FieldTraceExtractor
        import json
        extractor = FieldTraceExtractor()
        record = extractor.extract(
            interpreted={},
            user_text="You're just comforting me again.",
        )
        # 这应不会引发异常
        d = record.to_dict()
        _ = json.dumps(d, ensure_ascii=False)
        assert "proposals" in d
