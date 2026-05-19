"""测试 EvidenceItem + ObserverToEvidenceAdapter。

验证：
- 观察器信号正确转换为 EvidenceItem
- 不活跃信号返回 None
- behavior_affecting 始终为 False
- 现有观察器输出不变
- 未添加新的正则模式
"""
import pytest
from src.field_trace.store import (
    CorrectionObserver,
    CorrectionSignal,
    EvidenceItem,
    EvidenceStrength,
    EvidenceType,
    GripLossObserver,
    GripLossSignal,
    NoObservableFieldSignal,
    ObserverToEvidenceAdapter,
    CORRECTION_PATTERNS,
    GRIP_LOSS_PATTERNS,
)


# ---------------------------------------------------------------------------
# CorrectionSignal → EvidenceItem
# ---------------------------------------------------------------------------

class TestCorrectionSignalToEvidence:
    """CorrectionSignal 转换为 EvidenceItem。"""

    def test_correction_comfort_strong(self):
        """高置信度 comfort 纠正 → strong 强度。"""
        cs = CorrectionSignal(
            active=True,
            target="comfort",
            evidence="comforting me again",
            provenance="heuristic_observer",
            confidence=0.85,
            behavior_affecting=False,
        )
        item = ObserverToEvidenceAdapter.from_correction_signal(cs)
        assert item is not None
        assert item.evidence_type == EvidenceType.EXPLICIT_USER_FEEDBACK.value
        assert item.source == "correction_observer"
        assert item.strength == EvidenceStrength.STRONG.value
        assert item.behavior_affecting == False
        assert "用户拒绝了安慰模式" in item.why_it_matters
        assert item.excerpt_or_reference == "comforting me again"
        assert "ceasefire" not in item.why_it_matters

    def test_correction_generic_medium(self):
        """低置信度 generic_correction → medium 强度。"""
        cs = CorrectionSignal(
            active=True,
            target="generic_correction",
            evidence="that's not right",
            provenance="heuristic_observer",
            confidence=0.75,
            behavior_affecting=False,
        )
        item = ObserverToEvidenceAdapter.from_correction_signal(cs)
        assert item is not None
        assert item.evidence_type == EvidenceType.EXPLICIT_USER_FEEDBACK.value
        assert item.strength == EvidenceStrength.MEDIUM.value
        assert item.behavior_affecting == False

    def test_correction_customer_service_tone(self):
        """customer_service_tone 纠正映射正确。"""
        cs = CorrectionSignal(
            active=True,
            target="customer_service_tone",
            evidence="too customer-service-like",
            provenance="heuristic_observer",
            confidence=0.85,
            behavior_affecting=False,
        )
        item = ObserverToEvidenceAdapter.from_correction_signal(cs)
        assert item is not None
        assert "客服语调" in item.why_it_matters

    def test_correction_ai_girlfriend(self):
        """ai_girlfriend_behavior 纠正映射正确。"""
        cs = CorrectionSignal(
            active=True,
            target="ai_girlfriend_behavior",
            evidence="acting like an AI girlfriend",
            provenance="heuristic_observer",
            confidence=0.85,
            behavior_affecting=False,
        )
        item = ObserverToEvidenceAdapter.from_correction_signal(cs)
        assert item is not None
        assert "AI 女友" in item.why_it_matters
        assert "边界压力" in item.why_it_matters

    def test_unknown_target_fallback(self):
        """未知 target 不会崩溃——使用 fallback 文本。"""
        cs = CorrectionSignal(
            active=True,
            target="some_new_target",
            evidence="something weird",
            provenance="heuristic_observer",
            confidence=0.80,
            behavior_affecting=False,
        )
        item = ObserverToEvidenceAdapter.from_correction_signal(cs)
        assert item is not None
        assert "some_new_target" in item.why_it_matters


# ---------------------------------------------------------------------------
# GripLossSignal → EvidenceItem
# ---------------------------------------------------------------------------

class TestGripLossSignalToEvidence:
    """GripLossSignal 转换为 EvidenceItem。"""

    def test_starting_point_loss(self):
        """starting_point_loss → EXPLICIT_STARTING_POINT_LOSS。"""
        gls = GripLossSignal(
            active=True,
            target="starting_point_loss",
            evidence="i don't know where to start",
            provenance="heuristic_observer",
            confidence=0.85,
            behavior_affecting=False,
        )
        item = ObserverToEvidenceAdapter.from_grip_loss_signal(gls)
        assert item is not None
        assert item.evidence_type == EvidenceType.EXPLICIT_STARTING_POINT_LOSS.value
        assert item.source == "grip_loss_observer"
        assert item.strength == EvidenceStrength.MEDIUM.value
        assert item.behavior_affecting == False
        assert "抓点损失" in item.why_it_matters
        assert "starting_point_loss" in item.why_it_matters

    def test_next_step_loss(self):
        """next_step_loss → EXPLICIT_STARTING_POINT_LOSS。"""
        gls = GripLossSignal(
            active=True,
            target="next_step_loss",
            evidence="i don't know what step to take next",
            provenance="heuristic_observer",
            confidence=0.88,
            behavior_affecting=False,
        )
        item = ObserverToEvidenceAdapter.from_grip_loss_signal(gls)
        assert item is not None
        assert item.evidence_type == EvidenceType.EXPLICIT_STARTING_POINT_LOSS.value
        assert item.strength == EvidenceStrength.MEDIUM.value

    def test_unknown_grip_target(self):
        """未知 grip target 回退为 UNRESOLVED_GRIP_LOSS。"""
        gls = GripLossSignal(
            active=True,
            target="unknown",
            evidence="i'm lost",
            provenance="heuristic_observer",
            confidence=0.75,
            behavior_affecting=False,
        )
        item = ObserverToEvidenceAdapter.from_grip_loss_signal(gls)
        assert item is not None
        assert item.evidence_type == EvidenceType.UNRESOLVED_GRIP_LOSS.value


# ---------------------------------------------------------------------------
# NoObservableFieldSignal → EvidenceItem
# ---------------------------------------------------------------------------

class TestNoObservableSignalToEvidence:
    """NoObservableFieldSignal 转换为弱证据。"""

    def test_no_observable_weak(self):
        """NoObservableFieldSignal → weak 证据。"""
        nos = NoObservableFieldSignal(present=True)
        item = ObserverToEvidenceAdapter.from_no_observable_signal(nos)
        assert item is not None
        assert item.evidence_type == EvidenceType.NO_OBSERVABLE_SIGNAL.value
        assert item.source == "trace_absence_marker"
        assert item.strength == EvidenceStrength.WEAK.value
        assert item.behavior_affecting == False
        assert len(item.why_it_matters) > 0
        assert "FieldTrace" in item.why_it_matters
        assert len(item.limitations) > 0

    def test_no_observable_not_present(self):
        """present=False → None。"""
        nos = NoObservableFieldSignal(present=False)
        item = ObserverToEvidenceAdapter.from_no_observable_signal(nos)
        assert item is None


# ---------------------------------------------------------------------------
# 不活跃 / None → None
# ---------------------------------------------------------------------------

class TestInactiveReturnsNone:
    """不活跃或 None 信号应返回 None。"""

    def test_inactive_correction_returns_none(self):
        cs = CorrectionSignal(active=False, target="unknown")
        assert ObserverToEvidenceAdapter.from_correction_signal(cs) is None

    def test_inactive_grip_loss_returns_none(self):
        gls = GripLossSignal(active=False, target="unknown")
        assert ObserverToEvidenceAdapter.from_grip_loss_signal(gls) is None

    def test_none_signal_returns_none(self):
        assert ObserverToEvidenceAdapter.from_correction_signal(None) is None
        assert ObserverToEvidenceAdapter.from_grip_loss_signal(None) is None
        assert ObserverToEvidenceAdapter.from_no_observable_signal(None) is None


# ---------------------------------------------------------------------------
# behavior_affecting 始终为 False
# ---------------------------------------------------------------------------

class TestBehaviorAffectingAlwaysFalse:
    """所有 EvidenceItem 的 behavior_affecting 必须始终为 False。"""

    def test_behavior_affecting_always_false(self):
        cases = [
            ("correction", CorrectionSignal(active=True, target="comfort", evidence="x", confidence=0.90)),
            ("grip_loss", GripLossSignal(active=True, target="starting_point_loss", evidence="x")),
            ("no_observable", NoObservableFieldSignal(present=True)),
        ]
        for signal_type, signal in cases:
            if signal_type == "correction":
                item = ObserverToEvidenceAdapter.from_correction_signal(signal)
            elif signal_type == "grip_loss":
                item = ObserverToEvidenceAdapter.from_grip_loss_signal(signal)
            else:
                item = ObserverToEvidenceAdapter.from_no_observable_signal(signal)
            assert item is not None, f"{signal_type} 应生成 EvidenceItem"
            assert item.behavior_affecting == False, f"{signal_type} behavior_affecting 应为 False"


# ---------------------------------------------------------------------------
# 现有观察器输出不变
# ---------------------------------------------------------------------------

class TestExistingObserversUnchanged:
    """现有观察器（CorrectionObserver、GripLossObserver）输出不受影响。"""

    def test_correction_observer_output_unchanged(self):
        observer = CorrectionObserver()
        result = observer.observe("You're just comforting me again.")
        assert result.active == True
        assert result.target == "comfort"
        assert result.confidence == 0.85
        assert result.behavior_affecting == False
        assert result.evidence == "you're just comforting me again"

    def test_grip_loss_observer_output_unchanged(self):
        observer = GripLossObserver()
        result = observer.observe("I don't know where to start.")
        assert result.active == True
        assert result.target == "starting_point_loss"
        assert result.behavior_affecting == False

    def test_observer_returns_default_on_neutral_input(self):
        co = CorrectionObserver()
        gl = GripLossObserver()
        neutral = "Hello, how are you doing today?"
        assert co.observe(neutral).active == False
        assert gl.observe(neutral).active == False


# ---------------------------------------------------------------------------
# 无新正则模式
# ---------------------------------------------------------------------------

class TestNoNewRegexPatterns:
    """模式列表长度未改变。"""

    def test_correction_patterns_unchanged(self):
        assert len(CORRECTION_PATTERNS) == 16, (
            f"CORRECTION_PATTERNS 应为 16 条，实际为 {len(CORRECTION_PATTERNS)}"
        )

    def test_grip_loss_patterns_unchanged(self):
        assert len(GRIP_LOSS_PATTERNS) == 10, (
            f"GRIP_LOSS_PATTERNS 应为 10 条，实际为 {len(GRIP_LOSS_PATTERNS)}"
        )


# ---------------------------------------------------------------------------
# EvidenceItem 数据类结构
# ---------------------------------------------------------------------------

class TestEvidenceItemSchema:
    """EvidenceItem 数据类字段和默认值。"""

    def test_default_values(self):
        ei = EvidenceItem()
        assert ei.evidence_type == ""
        assert ei.source == ""
        assert ei.excerpt_or_reference == ""
        assert ei.why_it_matters == ""
        assert ei.strength == EvidenceStrength.WEAK.value
        assert ei.limitations == ""
        assert ei.behavior_affecting == False

    def test_field_assignment(self):
        ei = EvidenceItem(
            evidence_type=EvidenceType.EXPLICIT_USER_FEEDBACK.value,
            source="correction_observer",
            excerpt_or_reference="test evidence",
            why_it_matters="test reason",
            strength=EvidenceStrength.STRONG.value,
            limitations="test limitation",
            behavior_affecting=False,
        )
        assert ei.evidence_type == "explicit_user_feedback"
        assert ei.strength == "strong"
        assert ei.behavior_affecting == False


# ---------------------------------------------------------------------------
# EvidenceStrength / EvidenceType 枚举
# ---------------------------------------------------------------------------

class TestEvidenceEnums:
    """枚举值正确。"""

    def test_evidence_strength_values(self):
        assert EvidenceStrength.WEAK.value == "weak"
        assert EvidenceStrength.MEDIUM.value == "medium"
        assert EvidenceStrength.STRONG.value == "strong"

    def test_evidence_type_values(self):
        assert EvidenceType.EXPLICIT_USER_FEEDBACK.value == "explicit_user_feedback"
        assert EvidenceType.EXPLICIT_STARTING_POINT_LOSS.value == "explicit_starting_point_loss"
        assert EvidenceType.UNRESOLVED_GRIP_LOSS.value == "unresolved_grip_loss"
        assert EvidenceType.NO_OBSERVABLE_SIGNAL.value == "no_observable_signal"


# ---------------------------------------------------------------------------
# 适配器 limitations 字段非空
# ---------------------------------------------------------------------------

class TestAdapterLimitations:
    """每个适配器方法生成的 EvidenceItem 必须有非空 limitations。"""

    def test_correction_limitations(self):
        cs = CorrectionSignal(active=True, target="comfort", evidence="comforting me", confidence=0.85)
        item = ObserverToEvidenceAdapter.from_correction_signal(cs)
        assert item is not None
        assert len(item.limitations) > 0
        assert "正则模式匹配" in item.limitations

    def test_grip_loss_limitations(self):
        gls = GripLossSignal(active=True, target="starting_point_loss", evidence="i don't know where to start")
        item = ObserverToEvidenceAdapter.from_grip_loss_signal(gls)
        assert item is not None
        assert len(item.limitations) > 0
        assert "正则模式匹配" in item.limitations

    def test_no_observable_limitations(self):
        nos = NoObservableFieldSignal(present=True)
        item = ObserverToEvidenceAdapter.from_no_observable_signal(nos)
        assert item is not None
        assert len(item.limitations) > 0
        assert len(item.limitations) > 0
