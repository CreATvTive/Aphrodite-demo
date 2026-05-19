"""P40c — ContextualEvidenceRegulator 影子干运行。

最小化的数值 Regulator。将 P40b 的 EvidenceProposal 输出通过数值门控，
产出 audit-only 结果。

不连接 ForceEvent Adapter、U(t)、FieldState、MotionParams、BodyAction、runtime。
behavior_affecting 始终为 False。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.llm_gate.proposal_schema import EvidenceProposal


# ── RegulatorResult ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RegulatorResult:
    """ContextualEvidenceRegulator 影子干运行结果。audit-only。"""

    # 输入
    candidate_role: str
    raw_confidence: float
    surface_salience: float
    hypothesis_likelihood: float
    context_support: float
    term_support: float
    intent_support: float
    project_frame_support: float

    # Regulator 决策
    authorized_role: str  # ANCHOR|HYPOTHESIS|MODIFIER|CONTEXT_CONTINUATION|NOISE
    role_shift_reason: str  # 为何改变角色（若未改变则为""）
    dominance_risk: float  # d_i ∈ [0,1]
    adjusted_weight: float  # w_i' ∈ [0,1]
    registration_budget_ok: bool  # 是否在预算内
    dominance_warning: bool  # 主导风险是否触发警告

    # 审计
    audit_trace: dict = field(default_factory=dict)
    behavior_affecting: bool = False  # 始终 False

    # Gate 结果
    field_compatibility: float = 1.0  # 默认兼容（mock）
    blocked: bool = False  # 强冲突阻塞


# ── ContextualEvidenceRegulatorDryRun ───────────────────────────────────────────


class ContextualEvidenceRegulatorDryRun:
    """最小化的数值 Regulator。影子干运行。"""

    # 默认参数（来自设计规范）
    ROLE_WEIGHTS = {
        "ANCHOR": 1.0,
        "HYPOTHESIS": 0.35,
        "MODIFIER": 0.50,
        "CONTEXT_CONTINUATION": 0.75,
        "NOISE": 0.0,
    }

    DOMINANCE_BETA = 2.0  # 主导惩罚指数
    CONTEXT_ALPHA = 0.40  # 低上下文支持底
    BUDGET_CAP = 0.80  # 注册预算上限

    def evaluate(
        self,
        proposal: "EvidenceProposal",
        field_compatibility: float = 1.0,
    ) -> RegulatorResult:
        """计算受管制的提案结果。

        Args:
            proposal: 上游 EvidenceProposal。
            field_compatibility: 场兼容性 (1.0 / 0.5 / 0.0)，默认 1.0。

        Returns:
            RegulatorResult: audit-only 结果。
        """
        # 1. 从提案提取输入
        candidate_role = proposal.candidate_role
        raw_confidence = proposal.raw_confidence
        surface_salience = proposal.surface_salience
        hypothesis_likelihood = proposal.hypothesis_likelihood
        context_support = proposal.context_support
        term_support = proposal.term_support
        intent_support = proposal.intent_support
        project_frame_support = proposal.project_frame_support

        # 2. 计算主导风险 (§E)
        # d_i = max(0, s_i - q_i) × h_i
        d_i = max(0.0, surface_salience - context_support) * hypothesis_likelihood
        d_i = max(0.0, min(1.0, d_i))

        # 3. 角色授权 (§D)
        authorized = candidate_role
        reason = ""

        # 降级规则
        if candidate_role == "ANCHOR" and d_i > 0.5:
            authorized = "HYPOTHESIS"
            reason = "downgrade: ANCHOR→HYPOTHESIS — dominance_risk exceed threshold"
        elif candidate_role == "ANCHOR" and hypothesis_likelihood > 0.7:
            authorized = "HYPOTHESIS"
            reason = "downgrade: ANCHOR→HYPOTHESIS — high hypothesis_likelihood"

        # 噪声规则
        if raw_confidence < 0.1:
            authorized = "NOISE"
            # 如果之前已有降级理由，追加噪声理由
            noise_reason = "downgrade to NOISE: confidence too low"
            if reason:
                reason = reason + "; " + noise_reason
            else:
                reason = noise_reason

        # 4. 上下文支持因子 (§F)
        # context_factor = q_i + (1.0 - q_i) * CONTEXT_ALPHA
        context_factor = context_support + (1.0 - context_support) * self.CONTEXT_ALPHA

        # 5. 主导惩罚 (§F)
        # dominance_penalty = (1.0 - d_i) ** DOMINANCE_BETA
        dominance_penalty = (1.0 - d_i) ** self.DOMINANCE_BETA

        # 6. 调整权重 (§F)
        role_weight = self.ROLE_WEIGHTS.get(authorized, 0.3)
        w_i = (
            raw_confidence
            * role_weight
            * context_factor
            * field_compatibility
            * 1.0  # recurrence_score: v0 中为 1.0（无记忆）
            * dominance_penalty
        )
        w_i = max(0.0, min(1.0, w_i))

        # 7. 注册预算 (§G)
        budget_ok = w_i <= self.BUDGET_CAP
        # 单提案干运行：仅标记。批量运行时缩放：w_i * (BUDGET_CAP / sum)

        # 8. 阻塞逻辑 (§H)
        blocked = False
        if field_compatibility == 0.0:  # STRONG_CONFLICT
            blocked = True
            w_i = 0.0
            authorized = "NOISE"
            reason = "blocked: STRONG_CONFLICT — field_compatibility=0"

        # 9. 主导警告标记
        dominance_warning = d_i >= 0.5

        # 10. 构建审计追踪
        audit_trace = {
            "raw_confidence": raw_confidence,
            "role_weight": role_weight,
            "context_factor": context_factor,
            "field_compatibility": field_compatibility,
            "recurrence_score": 1.0,
            "dominance_risk": d_i,
            "dominance_penalty": dominance_penalty,
            "adjusted_weight_raw": w_i,
            "budget_cap": self.BUDGET_CAP,
            "candidate_role": candidate_role,
            "authorized_role": authorized,
            "surface_salience": surface_salience,
            "context_support": context_support,
            "hypothesis_likelihood": hypothesis_likelihood,
            "term_support": term_support,
            "intent_support": intent_support,
            "project_frame_support": project_frame_support,
        }

        # 11. 返回 RegulatorResult
        return RegulatorResult(
            candidate_role=candidate_role,
            raw_confidence=raw_confidence,
            surface_salience=surface_salience,
            hypothesis_likelihood=hypothesis_likelihood,
            context_support=context_support,
            term_support=term_support,
            intent_support=intent_support,
            project_frame_support=project_frame_support,
            authorized_role=authorized,
            role_shift_reason=reason,
            dominance_risk=round(d_i, 6),
            adjusted_weight=round(w_i, 6),
            registration_budget_ok=budget_ok,
            dominance_warning=dominance_warning,
            audit_trace=audit_trace,
            behavior_affecting=False,
            field_compatibility=field_compatibility,
            blocked=blocked,
        )
