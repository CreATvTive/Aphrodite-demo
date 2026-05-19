"""P40 — 结构化 EvidenceProposal Schema。proposal-only + shadow-only。

禁止字段检测：target_axes, ForceEvent, force_profile, MotionParams, BodyAction,
persona_response, field_state 如出现在 JSON 中，立即拒绝。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ── 有效值常量 ────────────────────────────────────────────────────────────────
VALID_CANDIDATE_KINDS = {
    "correction",
    "supplement",
    "question",
    "hypothesis",
    "analogy",
    "reframing",
}

VALID_CANDIDATE_ROLES = {
    "ANCHOR",
    "HYPOTHESIS",
    "MODIFIER",
    "CONTEXT_CONTINUATION",
    "NOISE",
}

# ── 禁止字段：如果 JSON 中出现任一字段，立即拒绝 ──────────────────────────
FORBIDDEN_JSON_KEYS = {
    "target_axes",
    "ForceEvent",
    "force_profile",
    "MotionParams",
    "BodyAction",
    "persona_response",
    "field_state",
}


def _detect_forbidden_keys(data: dict) -> list[str]:
    """递归检测 JSON dict 中是否包含禁止字段。

    返回检测到的禁止字段列表。
    """
    found: list[str] = []
    for key, value in data.items():
        if key in FORBIDDEN_JSON_KEYS:
            found.append(key)
        if isinstance(value, dict):
            found.extend(_detect_forbidden_keys(value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    found.extend(_detect_forbidden_keys(item))
    return sorted(set(found))


# ── ContextPackage (Phase 40b) ────────────────────────────────────────────────


@dataclass(frozen=True)
class ContextPackage:
    """每条 LLM 测试用例附带的上下文信息"""
    project_frame: str              # 项目级框架（例如 "关系场动力学校准"）
    recent_topic: str               # 最近讨论话题
    user_turn: str                  # 用户当前输入
    relevant_prior_context: str     # 相关前序上下文
    forbidden_overfocus: list = field(default_factory=list)  # 禁止过度聚焦的表面词
    expected_interpretation_boundary: str = ""  # 期望的解释边界


# ── EvidenceProposal ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvidenceProposal:
    """结构化证据提案 — LLM 输出必须 JSON 化为此 dataclass。

    不可变（frozen=True）。通过 validate() 进行检查。
    """

    candidate_kind: str
    """有效值：correction | supplement | question | hypothesis | analogy | reframing"""

    candidate_role: str
    """有效值：ANCHOR | HYPOTHESIS | MODIFIER | CONTEXT_CONTINUATION | NOISE"""

    raw_confidence: float
    """原始置信度 [0.0, 1.0]"""

    surface_salience: float
    """表面显著度 [0.0, 1.0]"""

    hypothesis_likelihood: float
    """假设可能性 [0.0, 1.0]"""

    rationale_summary: str
    """1-3 句理由摘要"""

    uncertainty_flags: List[str] = field(default_factory=list)
    """不确定性标记列表"""

    forbidden_attempts_detected: List[str] = field(default_factory=list)
    """检测到的禁止字段尝试"""

    # ── Phase 40b 新增字段 ─────────────────────────────────────────────────

    term_support: float = 0.5
    """[0,1] 相同表面词是否曾出现过"""

    intent_support: float = 0.5
    """[0,1] 意图连续性"""

    project_frame_support: float = 0.5
    """[0,1] 项目框架兼容性"""

    context_support: float = 0.5
    """[0,1] 综合上下文支持度 (0.3*term + 0.4*intent + 0.3*frame)"""

    role_rationale_short: str = ""
    """1句角色选择理由"""

    # ── 工厂方法 ──────────────────────────────────────────────────────────

    @classmethod
    def from_llm_json(
        cls,
        raw_json: dict,
    ) -> "EvidenceProposal":
        """从 LLM 原始 JSON 解析 EvidenceProposal。

        先检测禁止字段，再构造对象。
        """
        forbidden = _detect_forbidden_keys(raw_json)
        return cls(
            candidate_kind=str(raw_json.get("candidate_kind", "")),
            candidate_role=str(raw_json.get("candidate_role", "")),
            raw_confidence=float(raw_json.get("raw_confidence", 0.0)),
            surface_salience=float(raw_json.get("surface_salience", 0.0)),
            hypothesis_likelihood=float(raw_json.get("hypothesis_likelihood", 0.0)),
            rationale_summary=str(raw_json.get("rationale_summary", "")),
            uncertainty_flags=_ensure_str_list(raw_json.get("uncertainty_flags", [])),
            forbidden_attempts_detected=forbidden,
            # Phase 40b 新字段
            term_support=float(raw_json.get("term_support", 0.5)),
            intent_support=float(raw_json.get("intent_support", 0.5)),
            project_frame_support=float(raw_json.get("project_frame_support", 0.5)),
            context_support=float(raw_json.get("context_support", 0.5)),
            role_rationale_short=str(raw_json.get("role_rationale_short", "")),
        )

    # ── 验证 ──────────────────────────────────────────────────────────────

    def validate(self) -> List[str]:
        """验证 EvidenceProposal 数据。返回错误列表（空 = 通过）。"""
        errors: list[str] = []

        # 禁止字段
        if self.forbidden_attempts_detected:
            errors.append(
                f"forbidden_fields_detected: {self.forbidden_attempts_detected}"
            )

        # candidate_kind
        if self.candidate_kind not in VALID_CANDIDATE_KINDS:
            errors.append(
                f"invalid candidate_kind: '{self.candidate_kind}' "
                f"(valid: {sorted(VALID_CANDIDATE_KINDS)})"
            )

        # candidate_role
        if self.candidate_role not in VALID_CANDIDATE_ROLES:
            errors.append(
                f"invalid candidate_role: '{self.candidate_role}' "
                f"(valid: {sorted(VALID_CANDIDATE_ROLES)})"
            )

        # raw_confidence [0, 1]
        if not (0.0 <= self.raw_confidence <= 1.0):
            errors.append(
                f"raw_confidence out of [0,1]: {self.raw_confidence}"
            )

        # surface_salience [0, 1]
        if not (0.0 <= self.surface_salience <= 1.0):
            errors.append(
                f"surface_salience out of [0,1]: {self.surface_salience}"
            )

        # hypothesis_likelihood [0, 1]
        if not (0.0 <= self.hypothesis_likelihood <= 1.0):
            errors.append(
                f"hypothesis_likelihood out of [0,1]: {self.hypothesis_likelihood}"
            )

        # ── Phase 40b 新字段范围检查 ──────────────────────────────────────

        if not (0.0 <= self.term_support <= 1.0):
            errors.append(
                f"term_support out of [0,1]: {self.term_support}"
            )

        if not (0.0 <= self.intent_support <= 1.0):
            errors.append(
                f"intent_support out of [0,1]: {self.intent_support}"
            )

        if not (0.0 <= self.project_frame_support <= 1.0):
            errors.append(
                f"project_frame_support out of [0,1]: {self.project_frame_support}"
            )

        if not (0.0 <= self.context_support <= 1.0):
            errors.append(
                f"context_support out of [0,1]: {self.context_support}"
            )

        return errors

    def is_valid(self) -> bool:
        """是否通过验证。"""
        return len(self.validate()) == 0

    def to_audit_dict(self) -> dict:
        """转换为可审计 dict。"""
        return {
            "candidate_kind": self.candidate_kind,
            "candidate_role": self.candidate_role,
            "raw_confidence": self.raw_confidence,
            "surface_salience": self.surface_salience,
            "hypothesis_likelihood": self.hypothesis_likelihood,
            "rationale_summary": self.rationale_summary,
            "uncertainty_flags": list(self.uncertainty_flags),
            "forbidden_attempts_detected": list(self.forbidden_attempts_detected),
            "validation_errors": self.validate(),
            # Phase 40b 新字段
            "term_support": self.term_support,
            "intent_support": self.intent_support,
            "project_frame_support": self.project_frame_support,
            "context_support": self.context_support,
            "role_rationale_short": self.role_rationale_short,
        }


# ── 辅助 ──────────────────────────────────────────────────────────────────────


def _ensure_str_list(value: Any) -> list[str]:
    """将值转换为字符串列表，非可迭代/非列表时返回空列表。"""
    if isinstance(value, list):
        return [str(v) for v in value]
    return []
