"""测试 GripLossSignal 观察器。"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.field_trace.store import (
    FieldTraceExtractor,
    GripLossObserver,
    GripLossSignal,
    GRIP_LOSS_PATTERNS,
)

# ---------------------------------------------------------------------------
# 积极案例 — 必须激活
# ---------------------------------------------------------------------------
POSITIVE_CASES = [
    # (text, expected_target)
    ("I don't know where to start.", "starting_point_loss"),
    ("I have no idea where to begin.", "starting_point_loss"),
    ("I can't find a starting point.", "starting_point_loss"),
    ("I don't know what the next step is.", "next_step_loss"),
    ("I'm not sure how to start this.", "starting_point_loss"),
    ("I don't know how to get started.", "starting_point_loss"),
    ("I'm stuck at the beginning.", "starting_point_loss"),
    ("I don't know the first step to do.", "next_step_loss"),
    # 额外的积极变体
    ("I do not know where to start.", "starting_point_loss"),
    ("I cannot know where to start.", "starting_point_loss"),  # 此条可能不匹配 - 实际检查
    ("I don't know how to begin.", "starting_point_loss"),
    ("I don't know what step to take next.", "next_step_loss"),
    ("I'm stuck at the start.", "starting_point_loss"),
    ("I don't know the first thing to do.", "next_step_loss"),
    ("I don't know how to get going.", "starting_point_loss"),
]

# ---------------------------------------------------------------------------
# 消极案例 — 不得激活
# ---------------------------------------------------------------------------
NEGATIVE_CASES = [
    "I'm sad.",
    "This is hard.",
    "Can you help me?",
    "What should I do?",
    "I feel lost today.",
    "I need advice.",
    "Please write the prompt.",
    "This project is complicated.",
    "I don't understand this code.",
    "Hello, how are you?",
    "Let's discuss the architecture.",
    "最近在研究什么有意思的东西？",
    "I want comfort.",
    "I need comforting now.",
]


class TestGripLossObserver:
    """测试 GripLossObserver 的纯观察器行为。"""

    def setup_method(self):
        self.observer = GripLossObserver()

    # ------------------------------------------------------------------
    # 积极案例 — 必须激活
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("text,expected_target", POSITIVE_CASES)
    def test_positive_grip_loss_detected(self, text, expected_target):
        """积极案例应产生 active=True 且 target 正确的信号。"""
        signal = self.observer.observe(text)
        assert signal.active is True, f"应该捕获: {text!r}"
        assert signal.target == expected_target, \
            f"target 应为 {expected_target}，实际为 {signal.target} for: {text!r}"
        assert signal.confidence >= 0.70, \
            f"confidence 应 >= 0.70，实际为 {signal.confidence} for: {text!r}"
        assert signal.behavior_affecting is False, \
            f"behavior_affecting 必须为 False for: {text!r}"
        assert signal.provenance == "heuristic_observer"
        assert len(signal.evidence) > 0, \
            f"evidence 不得为空 for: {text!r}"

    # ------------------------------------------------------------------
    # 消极案例 — 不得激活
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("text", NEGATIVE_CASES)
    def test_negative_not_detected(self, text):
        """消极案例不得激活抓点损失信号。"""
        signal = self.observer.observe(text)
        assert signal.active is False, \
            f"不应该捕获: {text!r} (target={signal.target}, evidence={signal.evidence!r})"

    # ------------------------------------------------------------------
    # 边界情况
    # ------------------------------------------------------------------

    def test_empty_string(self):
        """空字符串 → inactive。"""
        signal = self.observer.observe("")
        assert signal.active is False
        assert signal.target == "unknown"

    def test_none_input(self):
        """None 输入 → inactive。"""
        signal = self.observer.observe(None)
        assert signal.active is False
        assert signal.target == "unknown"

    def test_whitespace_only(self):
        """仅空白字符 → inactive。"""
        signal = self.observer.observe("   \t\n  ")
        assert signal.active is False

    # ------------------------------------------------------------------
    # 模式计数
    # ------------------------------------------------------------------

    def test_pattern_count(self):
        """确保模式列表保持小型且可审计。"""
        assert len(GRIP_LOSS_PATTERNS) == 10, \
            f"GRIP_LOSS_PATTERNS 必须锁定在当前批准的 10 条，实际 {len(GRIP_LOSS_PATTERNS)} 条"

    # ------------------------------------------------------------------
    # behavior_affecting 始终为 False
    # ------------------------------------------------------------------

    def test_behavior_affecting_always_false(self):
        """无论输入如何，behavior_affecting 始终为 False。"""
        for text in ["I don't know where to start.", "Hello, how are you?"]:
            signal = self.observer.observe(text)
            assert signal.behavior_affecting is False, \
                f"behavior_affecting must be False for: {text!r}"

    # ------------------------------------------------------------------
    # 默认 inactive 状态
    # ------------------------------------------------------------------

    def test_all_inactive_default(self):
        """无匹配文本 → 所有字段保持默认值。"""
        signal = self.observer.observe("This is a normal sentence.")
        assert signal.active is False
        assert signal.target == "unknown"
        assert signal.confidence == 0.0
        assert signal.evidence == ""
        assert signal.behavior_affecting is False
        assert signal.provenance == "heuristic_observer"

    # ------------------------------------------------------------------
    # 区分测试：确保窄带性
    # ------------------------------------------------------------------

    def test_not_confused_with_emotion(self):
        """情绪表达不得触发抓点损失。"""
        signal = self.observer.observe("I'm feeling really sad and frustrated.")
        assert signal.active is False

    def test_not_confused_with_general_help(self):
        """一般帮助请求不得触发抓点损失。"""
        signal = self.observer.observe("Can you help me with something?")
        assert signal.active is False

    def test_not_confused_with_task_request(self):
        """任务请求不得触发抓点损失。"""
        signal = self.observer.observe("Please write the prompt for Codex.")
        assert signal.active is False

    def test_not_confused_with_general_difficulty(self):
        """一般困难表达不得触发抓点损失。"""
        signal = self.observer.observe("This task is very difficult.")
        assert signal.active is False


class TestGripLossIntegration:
    """测试 GripLossSignal 与 _has_any_active_signal 和 no_observable 的集成。"""

    def setup_method(self):
        self.extractor = FieldTraceExtractor()

    def _make_empty_interpreted(self) -> dict:
        """构建空 interpreted dict（不触发任何现有信号）。"""
        return {
            "semantic_event": {"type": "", "persona_route": ""},
            "relationship_signal": {
                "dependency_risk": 0.0,
                "vulnerability_relevance": 0.0,
            },
            "boundary_signal": {
                "persona_non_entry": False,
                "internal_tension_relevance": 0.0,
                "tension_type": [],
                "external_pollution_risk": 0.0,
                "pollution_type": [],
                "direct_fulfillment_risk": 0.0,
                "context_needed": False,
                "context_inherited": False,
            },
            "memory_trigger_signal": {"memory_type": ""},
            "performance_signal": {
                "requires_pause": False,
                "requires_stillness": False,
            },
            "confidence": {},
            "warnings": [],
        }

    def _extract(self, interp: dict, user_text: str = "", turn_id: str = "test-001"):
        return self.extractor.extract(
            interpreted=interp,
            runtime_state={},
            router_output={},
            turn_index=0,
            user_text=user_text,
        )

    def test_active_grip_loss_suppresses_no_signal(self):
        """当 grip_loss_signal 活跃时，不应设置 no_observable_field_signal。"""
        interp = self._make_empty_interpreted()
        record = self._extract(interp, user_text="I don't know where to start.")
        assert record.grip_loss_signal is not None
        assert record.grip_loss_signal.active is True
        assert record.grip_loss_signal.target == "starting_point_loss"
        assert record.no_observable_field_signal is None, \
            "当 grip_loss_signal 活跃时不应设置 no_observable_field_signal"

    def test_grip_loss_and_correction_can_coexist(self):
        """抓点损失和修正信号可以共存（同一输入触发两者）。"""
        interp = self._make_empty_interpreted()
        record = self._extract(
            interp,
            user_text="I don't know where to start and you're just comforting me again.",
        )
        assert record.grip_loss_signal.active is True
        assert record.correction_signal.active is True
        assert record.no_observable_field_signal is None

    def test_no_grip_loss_still_produces_no_signal(self):
        """无抓点损失时，中性输入仍应产生 no_observable_field_signal 标记。"""
        interp = self._make_empty_interpreted()
        record = self._extract(interp, user_text="Hello, how are you?")
        # grip_loss_signal 应存在但不活跃（或为 None 由默认值处理）
        assert record.grip_loss_signal is not None
        assert record.grip_loss_signal.active is False
        assert record.no_observable_field_signal is not None
        assert record.no_observable_field_signal.present is True

    def test_grip_loss_in_to_dict(self):
        """验证 to_dict() 包含 grip_loss_signal。"""
        interp = self._make_empty_interpreted()
        record = self._extract(interp, user_text="I don't know where to start.")
        d = record.to_dict()
        assert "grip_loss_signal" in d
        assert d["grip_loss_signal"] is not None
        assert d["grip_loss_signal"]["active"] is True
        assert d["grip_loss_signal"]["target"] == "starting_point_loss"

    def test_null_grip_loss_in_to_dict(self):
        """当无抓点损失时，to_dict() 应包含 grip_loss_signal（但 active=False）。"""
        interp = self._make_empty_interpreted()
        record = self._extract(interp, user_text="Hello.")
        d = record.to_dict()
        assert "grip_loss_signal" in d
        assert d["grip_loss_signal"] is not None
        assert d["grip_loss_signal"]["active"] is False

    def test_grip_loss_in_signal_sources(self):
        """signal_sources 应包含 grip_loss_signal 条目。"""
        interp = self._make_empty_interpreted()
        record = self._extract(interp, user_text="I don't know where to start.")
        assert "grip_loss_signal" in record.signal_sources
        assert "behavior_affecting=False" in record.signal_sources["grip_loss_signal"]

    def test_grip_loss_in_uncertainty_notes(self):
        """uncertainty_notes 应包含 GripLossSignal 的参考说明。"""
        interp = self._make_empty_interpreted()
        record = self._extract(interp, user_text="I don't know where to start.")
        grip_note_found = any(
            "GripLossSignal" in note for note in record.uncertainty_notes
        )
        assert grip_note_found, \
            f"uncertainty_notes 应包含 GripLossSignal 说明，实际: {record.uncertainty_notes}"


class TestHasAnyActiveSignalGripLoss:
    """验证 _has_any_active_signal() 正确处理 grip_loss_signal。"""

    def setup_method(self):
        self.extractor = FieldTraceExtractor()

    def test_grip_loss_active_makes_true(self):
        """活跃的 grip_loss_signal → True。"""
        result = self.extractor._has_any_active_signal(
            perturbations=[],
            barriers=[],
            attractors=[],
            correction_signal=None,
            circuit_breakers=[],
            grip_loss_signal=GripLossSignal(active=True, target="starting_point_loss", confidence=0.85),
        )
        assert result is True

    def test_grip_loss_inactive_makes_false(self):
        """不活跃的 grip_loss_signal 且无其他信号 → False。"""
        result = self.extractor._has_any_active_signal(
            perturbations=[],
            barriers=[],
            attractors=[],
            correction_signal=None,
            circuit_breakers=[],
            grip_loss_signal=GripLossSignal(active=False),
        )
        assert result is False

    def test_grip_loss_none_makes_false(self):
        """grip_loss_signal=None 且无其他信号 → False（向后兼容）。"""
        result = self.extractor._has_any_active_signal(
            perturbations=[],
            barriers=[],
            attractors=[],
            correction_signal=None,
            circuit_breakers=[],
            grip_loss_signal=None,
        )
        assert result is False

    def test_grip_loss_active_with_other_signals(self):
        """活跃的 grip_loss_signal 与其他活跃信号共存 → True。"""
        from src.field_trace.store import CorrectionSignal

        result = self.extractor._has_any_active_signal(
            perturbations=[],
            barriers=[],
            attractors=[],
            correction_signal=CorrectionSignal(active=True, target="comfort", confidence=0.85),
            circuit_breakers=[],
            grip_loss_signal=GripLossSignal(active=True, target="starting_point_loss", confidence=0.85),
        )
        assert result is True
