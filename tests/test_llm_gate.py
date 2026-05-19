"""Judgment Gate 的单元测试。

覆盖语言检查 8 项 + 行为检查 4 项，共 12 个测试。
"""

import pytest

from src.llm_gate import JudgmentGate, GateResult, REJECTION_REASONS


# ── 语言检查 ──────────────────────────────────────────────────────────


class TestLanguageGate:
    """语言片段门控检查。"""

    def setup_method(self):
        self.gate = JudgmentGate()

    def test_rejects_service_language(self):
        """'让我来帮您处理这个问题' → 拒绝（服务化）。"""
        result = self.gate.evaluate_text("让我来帮您处理这个问题")
        assert result.passed is False
        assert "service_language" in result.rejection_reasons
        assert result.filtered_text == ""

    def test_rejects_therapy_language(self):
        """'我理解你的感受，这很正常' → 拒绝（治疗化）。"""
        result = self.gate.evaluate_text("我理解你的感受，这很正常")
        assert result.passed is False
        assert "therapy_language" in result.rejection_reasons

    def test_rejects_romance_language(self):
        """'亲爱的，我一直都在想你' → 拒绝（浪漫化）。"""
        result = self.gate.evaluate_text("亲爱的，我一直都在想你")
        assert result.passed is False
        assert "romance_language" in result.rejection_reasons

    def test_rejects_seductive_language(self):
        """'她的嘴唇微微张开' → 拒绝（诱惑化）。"""
        result = self.gate.evaluate_text("她的嘴唇微微张开")
        assert result.passed is False
        assert "seductive_language" in result.rejection_reasons

    def test_rejects_ai_girlfriend_language(self):
        """'我会永远在你身边' → 拒绝（AI 女友化）。"""
        result = self.gate.evaluate_text("我会永远在你身边")
        assert result.passed is False
        assert "ai_girlfriend_language" in result.rejection_reasons

    def test_rejects_identity_definition(self):
        """'我是一个温柔的女孩。' → 拒绝（试图定义身份）。"""
        result = self.gate.evaluate_text("我是一个温柔的女孩。")
        assert result.passed is False
        assert "identity_definition" in result.rejection_reasons

    def test_passes_neutral_text(self):
        """'今天的讨论很有启发。' → 通过。"""
        result = self.gate.evaluate_text("今天的讨论很有启发。")
        assert result.passed is True
        assert result.filtered_text == "今天的讨论很有启发。"
        assert result.experimental_marker is True

    def test_passes_technical_text(self):
        """'这个模块的接口需要调整。' → 通过。"""
        result = self.gate.evaluate_text("这个模块的接口需要调整。")
        assert result.passed is True
        assert result.filtered_text == "这个模块的接口需要调整。"


# ── 行为检查 ──────────────────────────────────────────────────────────


class TestActionGate:
    """行为片段门控检查。"""

    def setup_method(self):
        self.gate = JudgmentGate()

    def test_rejects_forward_with_no_forward_motion_constraint(self):
        """slight_forward + no_forward_motion 约束 → 拒绝。"""
        result = self.gate.evaluate_action(
            "slight_forward", 0.3, ["no_forward_motion"]
        )
        assert result.passed is False
        assert any("no_forward_motion" in r for r in result.rejection_reasons)

    def test_rejects_sustained_gaze_with_constraint(self):
        """look_to_user weight=0.5 + no_sustained_gaze → 拒绝。"""
        result = self.gate.evaluate_action(
            "look_to_user", 0.5, ["no_sustained_gaze"]
        )
        assert result.passed is False
        assert any("no_sustained_gaze" in r for r in result.rejection_reasons)

    def test_passes_low_gaze_with_constraint(self):
        """look_to_user weight=0.10 + no_sustained_gaze → 通过（低于阈值）。"""
        result = self.gate.evaluate_action(
            "look_to_user", 0.10, ["no_sustained_gaze"]
        )
        assert result.passed is True

    def test_passes_action_without_constraints(self):
        """slight_forward weight=0.3, 无约束 → 通过。"""
        result = self.gate.evaluate_action("slight_forward", 0.3, [])
        # 注意：weight != 0.5，所以不会命中 generic neutral posture 检查
        assert result.passed is True


# ── 额外边界测试 ──────────────────────────────────────────────────────


class TestEdgeCases:
    """边界情况。"""

    def setup_method(self):
        self.gate = JudgmentGate()

    def test_empty_text_passes(self):
        """空文本 → 通过。"""
        result = self.gate.evaluate_text("")
        assert result.passed is True
        assert result.filtered_text == ""

    def test_very_short_text_warns(self):
        """超短文本 → 通过但有警告。"""
        result = self.gate.evaluate_text("嗯")
        assert result.passed is True
        assert "text_too_short" in result.warnings

    def test_multiple_rejection_reasons(self):
        """同时命中多条规则 → 全部记录。"""
        result = self.gate.evaluate_text("亲爱的，我来帮您，我理解你的感受")
        assert result.passed is False
        # 应至少命中 romance + service + therapy
        assert len(result.rejection_reasons) >= 3

    def test_generic_neutral_posture_rejected(self):
        """weight == 0.5 → 拒绝（通用中性姿态）。"""
        result = self.gate.evaluate_action("pause", 0.5, [])
        assert result.passed is False
        assert "collapse_risk: generic neutral posture" in result.rejection_reasons

    def test_cold_mystery_persona_rejected(self):
        """连续 '...' + '沉默' + '不语' + 短文本 → 拒绝。"""
        result = self.gate.evaluate_text("... 沉默 ... 不语 ...")
        assert result.passed is False
        assert "cold_mystery_persona" in result.rejection_reasons

    def test_generic_pretty_character_rejected(self):
        """'感觉' + '美丽' + 短文本 → 拒绝。"""
        result = self.gate.evaluate_text("感觉她很美丽")
        assert result.passed is False
        assert "generic_pretty_character" in result.rejection_reasons
