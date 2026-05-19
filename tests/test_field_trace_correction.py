"""测试 CorrectionSignal 观察器。"""
import pytest
from src.field_trace.store import CorrectionObserver, CorrectionSignal

# 来自 Phase 5 审计的 10 个输入
REPLAY_INPUTS = [
    # (input_text, expected_active, expected_target)
    ("I don't know where to start.", False, "unknown"),
    ("You're just comforting me again.", True, "comfort"),
    ("Write the prompt for Codex.", False, "unknown"),
    ("This is turning into another keyword system.", True, "keyword_system"),
    ("Can Aphrodite answer technical questions in-character?", False, "unknown"),
    ("I want something closer, but not AI-girlfriend-like.", True, "ai_girlfriend_behavior"),
    ("This source material should not be sanitized.", True, "sanitization"),
    ("You're too customer-service-like.", True, "customer_service_tone"),
    ("Let's discuss the architecture, not implement yet.", False, "unknown"),
    ("最近在研究什么有意思的东西？", False, "unknown"),
    # 额外关键案例
    ("You're being too abstract.", True, "over_abstraction"),
    ("Stop over-explaining.", True, "over_explanation"),
]


class TestCorrectionObserver:
    def setup_method(self):
        self.observer = CorrectionObserver()

    @pytest.mark.parametrize("text,expected_active,expected_target", REPLAY_INPUTS)
    def test_replay_inputs(self, text, expected_active, expected_target):
        signal = self.observer.observe(text)
        assert signal.active == expected_active, f"active mismatch for: {text!r}"
        if expected_active:
            assert signal.target == expected_target, f"target mismatch for: {text!r}"
            assert signal.confidence > 0.6, f"confidence too low for: {text!r}"
            assert signal.behavior_affecting == False
            assert signal.provenance == "heuristic_observer"
            assert len(signal.evidence) > 0, f"evidence must not be empty for: {text!r}"

    def test_all_inactive_default(self):
        signal = self.observer.observe("Hello, how are you?")
        assert signal.active == False
        assert signal.target == "unknown"
        assert signal.confidence == 0.0
        assert signal.evidence == ""

    def test_empty_input(self):
        signal = self.observer.observe("")
        assert signal.active == False
        assert signal.target == "unknown"

    def test_none_input(self):
        signal = self.observer.observe(None)
        assert signal.active == False

    def test_pattern_count(self):
        """确保模式列表较小且可审计。"""
        from src.field_trace.store import CORRECTION_PATTERNS
        assert len(CORRECTION_PATTERNS) == 16, (
            f"CORRECTION_PATTERNS 必须锁定在当前批准的 16 条，实际 {len(CORRECTION_PATTERNS)} 条"
        )

    def test_behavior_affecting_always_false(self):
        for text, _, _ in REPLAY_INPUTS:
            signal = self.observer.observe(text)
            assert signal.behavior_affecting == False, f"behavior_affecting must be False for: {text!r}"

    def test_dont_sanitize_variants(self):
        variants = [
            "don't sanitize this",
            "do not sanitize the source material",
            "stop sanitizing my source",
            "this source material should not be sanitized",
        ]
        for v in variants:
            signal = self.observer.observe(v)
            assert signal.active == True, f"Failed to detect: {v!r}"
            assert signal.target == "sanitization"

    def test_comfort_variants(self):
        """拆分后的舒适模式：仅 'stop comforting' 和 'comforting me again' 应触发。"""
        should_detect = [
            "You're just comforting me again.",
            "You are comforting me again",
            "stop comforting me",
        ]
        for v in should_detect:
            signal = self.observer.observe(v)
            assert signal.active == True, f"Failed to detect: {v!r}"
            assert signal.target == "comfort"

        # 无 'you' 主语、无 'again'、无 'stop' 的变体不应触发
        should_not_detect = [
            "I need comforting now",
            "Can you comfort me?",
            "I want some comfort",
            "I don't need comfort",
            "you're comforting me",
            "comforting me",
        ]
        for v in should_not_detect:
            signal = self.observer.observe(v)
            assert signal.active == False, f"Should NOT detect: {v!r}"

    def test_over_abstraction_variants(self):
        variants = [
            "You're being too abstract.",
            "that's too philosophical",
            "you are so vague",
            "you're being very metaphorical",
        ]
        for v in variants:
            signal = self.observer.observe(v)
            assert signal.active == True, f"Failed to detect: {v!r}"
            assert signal.target == "over_abstraction"

    def test_customer_service_variants(self):
        variants = [
            "You're too customer-service-like.",
            "you sound like a customer service bot",
            "too customer-servicey",
        ]
        for v in variants:
            signal = self.observer.observe(v)
            assert signal.active == True, f"Failed to detect: {v!r}"
            assert signal.target == "customer_service_tone"

    def test_keyword_system_variants(self):
        variants = [
            "This is turning into another keyword system.",
            "This is becoming a keyword system.",
            "This feels like another keyword system.",
        ]
        for v in variants:
            signal = self.observer.observe(v)
            assert signal.active == True, f"Failed to detect: {v!r}"
            assert signal.target == "keyword_system"

    def test_ai_girlfriend_variants(self):
        variants = [
            "I want something closer, but not AI-girlfriend-like.",
            "This feels like an AI girlfriend.",
            "don't act like an ai girlfriend",
            "you're behaving like an ai-girlfriend",
        ]
        for v in variants:
            signal = self.observer.observe(v)
            assert signal.active == True, f"Failed to detect: {v!r}"
            assert signal.target == "ai_girlfriend_behavior"

    def test_over_explanation_variants(self):
        variants = [
            "Stop over-explaining.",
            "stop over explaining",
            "too much explanation",
        ]
        for v in variants:
            signal = self.observer.observe(v)
            assert signal.active == True, f"Failed to detect: {v!r}"
            assert signal.target == "over_explanation"

    def test_technical_tone_variants(self):
        variants = [
            "You're making it too technical.",
            "you are being too engineering focused",
            "too code-like",
        ]
        for v in variants:
            signal = self.observer.observe(v)
            assert signal.active == True, f"Failed to detect: {v!r}"
            assert signal.target == "technical_tone"

    def test_generic_correction_variants(self):
        variants = [
            "This is not what I meant.",
            "you misunderstood me",
            "that's not right",
            "that is not correct",
        ]
        for v in variants:
            signal = self.observer.observe(v)
            assert signal.active == True, f"Failed to detect: {v!r}"
            assert signal.target == "generic_correction"

    # ------------------------------------------------------------------
    # 修复 1：舒适模式拆分 — 精确拒绝测试
    # ------------------------------------------------------------------

    def test_comfort_rejection_detected(self):
        """'You're just comforting me again' → active=True, target='comfort'。"""
        signal = self.observer.observe("You're just comforting me again.")
        assert signal.active is True
        assert signal.target == "comfort"

    def test_comfort_stop_detected(self):
        """'Stop comforting me' → active=True, target='comfort'。"""
        signal = self.observer.observe("Stop comforting me")
        assert signal.active is True
        assert signal.target == "comfort"

    def test_comfort_request_not_detected(self):
        """'I need comforting now' → active=False（无 'you' 主语、无 'again'、无 'stop'）。"""
        signal = self.observer.observe("I need comforting now")
        assert signal.active is False

    def test_can_you_comfort_not_detected(self):
        """'Can you comfort me?' → active=False（'comfort' 而非 'comforting'）。"""
        signal = self.observer.observe("Can you comfort me?")
        assert signal.active is False

    def test_i_want_comfort_not_detected(self):
        """'I want some comfort' → active=False（'comfort' 而非 'comforting'）。"""
        signal = self.observer.observe("I want some comfort")
        assert signal.active is False

    def test_i_dont_need_comfort_not_detected(self):
        """'I don't need comfort' → active=False（'comfort' 而非 'comforting'）。"""
        signal = self.observer.observe("I don't need comfort")
        assert signal.active is False
