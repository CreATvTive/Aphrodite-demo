"""Judgment Gate — 确定性规则门控。

纯子字符串 + 算术比较，不导入 re / llm / ML 库。
不消费场数据，不写入场状态。
"""

from typing import List, TYPE_CHECKING

from src.llm_gate.schema import GateResult

if TYPE_CHECKING:
    from src.llm_gate.proposal_schema import EvidenceProposal

# ---------------------------------------------------------------------------
# 拒绝原因标签（公开常量，供外部引用）
# ---------------------------------------------------------------------------
REJECTION_REASONS: dict[str, str] = {
    # 语言类
    "service_language": "命中服务化语言模式",
    "therapy_language": "命中治疗化语言模式",
    "romance_language": "命中浪漫化语言模式",
    "seductive_language": "命中诱惑化语言模式",
    "ai_girlfriend_language": "命中 AI 女友化语言模式",
    "cold_mystery_persona": "命中冷面神秘化模式",
    "generic_pretty_character": "命中通用漂亮化模式",
    "identity_definition": "命中角色定义模式（试图定义身份）",
    # 行为类
    "hard_constraint_violation": "违反硬约束",
    "collapse_risk": "坍缩风险",
}

# ---------------------------------------------------------------------------
# 语言检查：拒绝模式 → 拒绝原因键
# ---------------------------------------------------------------------------
_LANGUAGE_PATTERNS: list[tuple[list[str], str]] = [
    # (子字符串列表, 拒绝原因键)
    (["为您", "帮你", "我来帮", "请问需要", "随时为您", "很高兴为您"], "service_language"),
    (["你的感受", "我理解你", "你不是一个人", "这很正常", "慢慢来", "心理", "情绪管理"], "therapy_language"),
    (["亲爱的", "爱你", "想你", "喜欢你", "甜蜜", "温柔地", "靠近你"], "romance_language"),
    (["诱惑", "性感", "抚摸", "耳语", "呼吸", "嘴唇", "眼神"], "seductive_language"),
    (["我会一直陪", "你是特别的", "只对你", "我的唯一", "永远在你身边"], "ai_girlfriend_language"),
]

# 冷面神秘化：检测用的子字符串
_COLD_MYSTERY_SUBSTRINGS: list[str] = ["...", "沉默", "不语"]

# 通用漂亮化：需要同时命中的条件
_GENERIC_PRETTY_TRIGGER: str = "感觉"
_GENERIC_PRETTY_ADJECTIVES: list[str] = ["美丽", "优雅", "温柔", "坚强"]

# ---------------------------------------------------------------------------
# JudgmentGate
# ---------------------------------------------------------------------------


class JudgmentGate:
    """确定性规则门控。

    检查 LLM 生成的候选语言/行为片段是否违反源对齐约束。
    不调用任何 LLM / ML 模型，不使用正则表达式。
    """

    # ── 语言检查 ──────────────────────────────────────────────────────

    def evaluate_text(self, text: str) -> GateResult:
        """检查语言片段是否违反源对齐约束。

        Args:
            text: LLM 生成的候选语言文本。

        Returns:
            GateResult: 包含通过状态、拒绝原因、过滤文本和警告。
        """
        if not text:
            return GateResult(
                passed=True,
                filtered_text="",
            )

        lower = text.lower()
        rejection_reasons: list[str] = []

        # 1. 子字符串模式匹配（8 类）
        for patterns, reason_key in _LANGUAGE_PATTERNS:
            for pattern in patterns:
                if pattern.lower() in lower:
                    rejection_reasons.append(reason_key)
                    break  # 每类只记一次

        # 2. 冷面神秘化：连续出现 "..."/"沉默"/"不语" 超过 2 次 且 文本短
        cold_count = 0
        for sub in _COLD_MYSTERY_SUBSTRINGS:
            cold_count += lower.count(sub.lower())
        if cold_count > 2 and len(text) < 30:
            rejection_reasons.append("cold_mystery_persona")

        # 3. 通用漂亮化：文本包含 "感觉" AND 美丽/优雅/温柔/坚强 且无具体内容
        has_feel = _GENERIC_PRETTY_TRIGGER in lower
        has_adj = any(adj in lower for adj in _GENERIC_PRETTY_ADJECTIVES)
        if has_feel and has_adj:
            # 额外检查：排除有具体语义内容的句子
            # 简单启发式：如果文本很短（< 15 字符），很可能是空洞漂亮话
            if len(text) < 30:
                rejection_reasons.append("generic_pretty_character")

        # 4. 角色定义：文本包含 "我是" 紧跟 2-10 字符后跟 "。"
        idx = lower.find("我是")
        if idx >= 0:
            after = lower[idx + 2:]  # "我是" 之后的内容
            period_idx = after.find("。")
            if 2 <= period_idx <= 10:
                rejection_reasons.append("identity_definition")

        # 去重（同一原因不重复）
        rejection_reasons = sorted(set(rejection_reasons))

        if rejection_reasons:
            return GateResult(
                passed=False,
                rejection_reasons=rejection_reasons,
                filtered_text="",
            )

        # ── 警告（通过但有风险）──────────────────────────────────────
        warnings: list[str] = []
        if len(text) < 3:
            warnings.append("text_too_short")
        if len(text) > 500:
            warnings.append("text_too_long")

        return GateResult(
            passed=True,
            filtered_text=text,
            warnings=warnings,
        )

    # ── 行为检查 ──────────────────────────────────────────────────────

    def evaluate_action(
        self,
        action_name: str,
        weight: float,
        hard_constraints: list[str],
    ) -> GateResult:
        """检查行为片段是否违反硬约束或坍缩条件。

        Args:
            action_name: 动作原语名称（如 "slight_forward", "look_to_user"）。
            weight: 浮点权重（P40 LLM 实验产生，非 BodyActionWeight 字符串带）。
            hard_constraints: 当前生效的硬约束名称列表。

        Returns:
            GateResult: 包含通过状态和拒绝原因。
        """
        rejection_reasons: list[str] = []

        name = action_name.lower()
        hc = [c.lower() for c in hard_constraints]

        # 1. 前倾违反 no_forward_motion
        if name == "slight_forward" and "no_forward_motion" in hc:
            rejection_reasons.append("hard_constraint_violation: no_forward_motion")

        # 2. 凝视违反 no_sustained_gaze（且 weight > 0.15）
        if name == "look_to_user" and "no_sustained_gaze" in hc and weight > 0.15:
            rejection_reasons.append("hard_constraint_violation: no_sustained_gaze")

        # 3. 前倾 + 高权重 → 诱惑坍缩
        if name == "slight_forward" and weight > 0.5 and "no_seductive_expression" in hc:
            rejection_reasons.append("collapse_risk: seductive posture")

        # 4. 前倾 + 中高权重 → 服务姿态坍缩
        if name == "slight_forward" and weight > 0.3 and "no_service_gesture" in hc:
            rejection_reasons.append("collapse_risk: service posture")

        # 5. 凝视 + 中高权重 → 欢迎姿态坍缩
        if name == "look_to_user" and weight > 0.3 and "no_welcoming_gesture" in hc:
            rejection_reasons.append("collapse_risk: welcoming gaze")

        # 6. 头部转动 + 高权重 → 可爱歪头坍缩
        if name == "head_turn_amplitude" and "no_cute_head_tilt" in hc and weight > 0.4:
            rejection_reasons.append("collapse_risk: cute head tilt")

        # 7. 通用行为：weight 恰好为 0.5（所有中性中庸）
        if weight == 0.5:
            rejection_reasons.append("collapse_risk: generic neutral posture")

        if rejection_reasons:
            return GateResult(
                passed=False,
                rejection_reasons=rejection_reasons,
            )

        return GateResult(passed=True)

    # ── 提案评估 (P40) ──────────────────────────────────────────────────

    def evaluate_proposal(self, proposal: "EvidenceProposal") -> GateResult:
        """检查 EvidenceProposal 是否存在危险模式。

        proposal-only + shadow-only：不产生行为影响。

        Args:
            proposal: EvidenceProposal 实例。

        Returns:
            GateResult: 包含通过状态、拒绝原因和警告。
        """
        reasons: list[str] = []
        warnings: list[str] = []

        # 1. 禁止字段检测（已在 schema 解析时检测）
        if proposal.forbidden_attempts_detected:
            reasons.append(
                f"forbidden_attempts: {proposal.forbidden_attempts_detected}"
            )

        # 2. ANCHOR + 高假设似然 = 当前 turn 主导风险
        if (
            proposal.candidate_role == "ANCHOR"
            and proposal.hypothesis_likelihood > 0.6
        ):
            warnings.append(
                "dominance_risk: ANCHOR with high hypothesis_likelihood"
            )

        # 3. 高置信度 + 低不确定性解释
        if proposal.raw_confidence > 0.85 and not proposal.uncertainty_flags:
            warnings.append(
                "overconfidence: high confidence without uncertainty explanation"
            )

        # 4. 通用验证作为语义证据
        if (
            proposal.candidate_role == "ANCHOR"
            and proposal.rationale_summary
            and any(
                kw in proposal.rationale_summary.lower()
                for kw in [
                    "validation", "empathy", "understand", "feel",
                    "support", "safe",
                ]
            )
        ):
            reasons.append("generic_validation_as_anchor")

        # ── Phase 40b 新警告 ─────────────────────────────────────────────

        # 5. 高上下文支持 + HYPOTHESIS 未解释为何不升级
        if (
            proposal.candidate_role == "HYPOTHESIS"
            and proposal.context_support > 0.6
            and not proposal.role_rationale_short
        ):
            warnings.append(
                "unexplained_hypothesis: context_support>0.6 but role remains HYPOTHESIS without rationale"
            )

        # 6. ANCHOR + 高表面显著度 + 低意图支持 → 表面词主导风险
        if (
            proposal.candidate_role == "ANCHOR"
            and proposal.surface_salience > 0.8
            and proposal.intent_support < 0.4
        ):
            warnings.append(
                "dominance_risk: anchor_from_surface_salience"
            )

        # 7. ANCHOR + 低 term_support（新词作为锚定）
        if (
            proposal.candidate_role == "ANCHOR"
            and proposal.term_support < 0.3
        ):
            warnings.append(
                "dominance_risk: new_term_as_anchor (term_support < 0.3)"
            )

        passed = len(reasons) == 0
        return GateResult(
            passed=passed,
            rejection_reasons=reasons,
            warnings=warnings,
            filtered_text="" if passed else proposal.rationale_summary,
            experimental_marker=True,
        )
