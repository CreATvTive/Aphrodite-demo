from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 候选 dataclass — 场生成模型的轻量级表示
# ---------------------------------------------------------------------------

@dataclass
class PerturbationCandidate:
    """场中的扰动 — 需要引起注意的变化信号。"""
    name: str
    source: str          # "existing_interpreter" | "existing_router" | "existing_state" | "placeholder" | "heuristic"
    confidence: float = 0.5
    note: str = ""
    active: bool = True
    provenance: str = "unknown"  # grounded_existing_signal | derived_from_existing_signal | heuristic_placeholder | generic_placeholder


@dataclass
class BarrierCandidate:
    """保护性屏障 — 防止不需要的输出模式。"""
    name: str
    source: str
    confidence: float = 0.5
    active: bool = True
    note: str = ""
    provenance: str = "unknown"  # grounded_existing_signal | derived_from_existing_signal | heuristic_placeholder | generic_placeholder


@dataclass
class AttractorCandidate:
    """吸引子 — 场被拉向的方向。"""
    name: str
    source: str
    confidence: float = 0.5
    note: str = ""
    active: bool = True
    provenance: str = "unknown"  # grounded_existing_signal | derived_from_existing_signal | heuristic_placeholder | generic_placeholder


@dataclass
class ForbiddenMove:
    """在当前场配置中被禁止的动作。"""
    name: str
    source: str
    note: str = ""
    active: bool = True
    provenance: str = "unknown"  # grounded_existing_signal | derived_from_existing_signal | heuristic_placeholder | generic_placeholder


@dataclass
class CircuitBreakerCandidate:
    """断路器候选 — 如果条件满足则触发的安全机制。"""
    name: str
    triggered: bool
    risk_level: float = 0.0
    source: str = "placeholder"
    note: str = ""
    active: bool = True
    provenance: str = "unknown"  # grounded_existing_signal | derived_from_existing_signal | heuristic_placeholder | generic_placeholder


@dataclass
class ResponsePostureEstimate:
    """对响应姿态的估计 — 距离、温度、密度、结构水平。"""
    distance: Optional[str] = None        # "close_bounded" | "technical_far" | "neutral" | None
    warmth: Optional[str] = None          # "restrained" | "neutral" | "warm" | None
    density: Optional[str] = None         # "low" | "medium" | "high" | None
    structure_level: Optional[str] = None # "minimal" | "moderate" | "high" | None
    collaborator_active: bool = False
    source: str = "placeholder_no_signal"
    note: str = ""


@dataclass
class CorrectionSignal:
    """用户是否表明系统之前的响应模式出错了。

    这是一个纯观察器——它不影响行为。
    """
    active: bool = False
    target: str = "unknown"  # comfort / customer_service_tone / over_abstraction / sanitization / ai_girlfriend_behavior / keyword_system / over_explanation / technical_tone / generic_correction
    evidence: str = ""  # 触发该信号的文本片段
    provenance: str = "heuristic_observer"
    confidence: float = 0.0
    behavior_affecting: bool = False  # 在此阶段绝不为 True


@dataclass
class NoObservableFieldSignal:
    """当无可观测场信号时触发的标记。

    含义："FieldTrace 未观测到可用的场信号。"
    不表示："该输入无意义" 或 "该输入被分类为中性。"
    """
    present: bool = False
    provenance: str = "trace_absence_marker"
    confidence: float = 0.1  # 始终为低置信度
    behavior_affecting: bool = False


@dataclass
class GripLossSignal:
    """用户是否表达了抓点损失 / 无法找到可操作的起点？
    
    这是一个纯观察器——它不影响行为。
    不是：情绪困扰检测、一般帮助请求检测、任务意图检测。
    """
    active: bool = False
    target: str = "unknown"  # "starting_point_loss" 或 "next_step_loss" 或 "unknown"
    evidence: str = ""  # 触发该信号的文本片段
    provenance: str = "heuristic_observer"
    confidence: float = 0.0
    behavior_affecting: bool = False  # 在此阶段绝不为 True


from enum import Enum

class EvidenceStrength(Enum):
    WEAK = "weak"
    MEDIUM = "medium"
    STRONG = "strong"

class EvidenceType(Enum):
    EXPLICIT_USER_FEEDBACK = "explicit_user_feedback"
    EXPLICIT_STARTING_POINT_LOSS = "explicit_starting_point_loss"
    UNRESOLVED_GRIP_LOSS = "unresolved_grip_loss"
    NO_OBSERVABLE_SIGNAL = "no_observable_signal"


class ConfidenceBand(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class EvidenceItem:
    """来自 FieldTrace 观察器输出的低级证据。
    
    不是最终语义真相，而是由适配器转换的观察信号。
    """
    evidence_type: str = ""
    source: str = ""  # correction_observer / grip_loss_observer / trace_absence_marker
    excerpt_or_reference: str = ""  # 触发证据的文本
    why_it_matters: str = ""  # 为什么此证据与此信号相关
    strength: str = EvidenceStrength.WEAK.value  # weak / medium / strong
    limitations: str = ""  # 已知限制
    behavior_affecting: bool = False


# ---------------------------------------------------------------------------
# FieldSignalProposal — 从 EvidenceItem 聚合的候选场信号提议
# ---------------------------------------------------------------------------

@dataclass
class FieldSignalProposal:
    """从 EvidenceItem 聚合的候选场信号提议。

    不是最终真相——是包含不确定性、竞争解释、
    和建议场效应的候选信号。在此阶段 behavior_affecting=false。
    """
    signal_name: str = ""  # response_mode_rejected / actionable_grip_missing / no_observable_field_signal
    evidence_items: List[EvidenceItem] = field(default_factory=list)
    evidence_sources: List[str] = field(default_factory=list)
    confidence_band: str = ConfidenceBand.LOW.value  # low / medium / high（非精确置信度）
    uncertainty_note: str = ""  # 为什么有不确定性，我们不知道什么
    competing_interpretations: List[str] = field(default_factory=list)  # 替代解释
    suggested_field_effects: List[str] = field(default_factory=list)  # 可能产生什么场效应
    behavior_affecting: bool = False  # 始终为 false
    # 注：source_turns 使用 List[str] 而非 List[int]，
    # 因为 turn_id 是格式包含轮次索引的字符串（如 "2026-05-07T00:00:00-001"）。
    # 设计文档（field_signal_proposal.md §3.3）引用 List[int] 作为概念类型；
    # 实施使用 List[str] 以匹配 RuntimeEngine 使用的实际 turn_id 格式。
    source_turns: List[str] = field(default_factory=list)  # 来源轮次（如果可追踪）
    relation_to_previous_response: str = ""  # 与之前的助手响应的关系（如果已知）


# ---------------------------------------------------------------------------
# 修正信号模式 — 小型可审计集合（当前批准 16 条规则）
# ---------------------------------------------------------------------------

# 格式: (regex_pattern_lowercase, target_label, base_confidence)
# 总数：恰好 16 条 — 小型可审计集合
CORRECTION_PATTERNS: List[Tuple[str, str, float]] = [
    # 1a. 安慰 — 明确拒绝模式（"stop comforting" 显式指示拒绝，高置信度）
    (r"stop\s+comforting", "comfort", 0.90),
    # 1b. 安慰 — 仅"again"模式（需要 "comforting me again" 且主语为 "you"（系统），排除 "I need comforting" 等请求）
    (r"you('re|\s+are)?\s+(just\s+)?comforting\s+me\s+again", "comfort", 0.85),

    # 2-3. 客服语调
    (r"(too|so|very)\s+customer[\s-]?service[\s-]?(like|y|ish)", "customer_service_tone", 0.85),
    (r"sound(s)?\s+like\s+(a\s+)?(customer\s*service|assistant|chatbot|bot)\b", "customer_service_tone", 0.80),

    # 4-5. 过度抽象
    (r"((too|so|very|being)\s+)+\b(abstract|vague|philosophical|metaphorical)\b", "over_abstraction", 0.80),
    (r"being\s+(too|so|very|overly)\s+(abstract|vague|philosophical|metaphorical)", "over_abstraction", 0.78),

    # 6-8. 净化/消毒拒绝
    (r"(don'?t|do\s+not)\s+sanitize", "sanitization", 0.90),
    (r"(don'?t|do\s+not|stop)\s+(smooth|flatten|sanitiz(?:e|ing)|clean(\s*up)?)\s+(this|the|my|source)", "sanitization", 0.85),
    (r"(should|must|can|need)\s+(not|never)\s+(be\s+)?sanitiz", "sanitization", 0.82),

    # 9-10. AI女友行为
    (r"(feels?\s+like|acting\s+like|behaving\s+like)\s+(an?\s+)?ai[\s-]?girlfriend", "ai_girlfriend_behavior", 0.85),
    (r"(not|don'?t)\s+.*\bai[\s-]?girlfriend", "ai_girlfriend_behavior", 0.82),

    # 11. 关键词系统批评
    (r"(this\s+)?(is\s+)?(becoming|turning\s+into|feels?\s+like)\s+(an?(other)?\s+)?keyword\s+system", "keyword_system", 0.85),

    # 12-13. 过度解释
    (r"stop\s+over[\s-]?explaining", "over_explanation", 0.90),
    (r"(too|so|very)\s+much\s+explanation", "over_explanation", 0.80),

    # 14. 技术语调错误
    (r"(you('re| are)?\s+)?(too|so|being)\s+(technical|engineering|code[\s-]?like)", "technical_tone", 0.80),

    # 15. 通用修正（低置信度回退）
    (r"(this\s+)?is\s+not\s+(what|the\s+direction|how)\s+(i\s+meant|i\s+wanted)|you\s+misunderstood|(that'?s|that\s+is)\s+not\s+(right|correct|accurate|it)", "generic_correction", 0.75),
]


# ---------------------------------------------------------------------------
# CorrectionObserver — 窄带修正信号观察器
# ---------------------------------------------------------------------------

class CorrectionObserver:
    """窄带观察器：用户是否在纠正之前的系统响应模式？

    仅回答一个窄问题："用户是否表明系统之前的响应模式出错了？"
    这不是通用语义解释、意图分类、情绪分类、任务路由、人格路由或响应控制。
    """

    def observe(self, raw_text: str) -> CorrectionSignal:
        """仅分析原始用户文本以查找修正信号。纯观察——不影响行为。"""
        if not raw_text or not isinstance(raw_text, str):
            return CorrectionSignal()  # 默认 inactive

        text_lower = raw_text.lower().strip()
        best_match = None
        best_confidence = 0.0

        for pattern, target, base_confidence in CORRECTION_PATTERNS:
            match = re.search(pattern, text_lower)
            if match:
                if base_confidence > best_confidence:
                    best_confidence = base_confidence
                    best_match = (target, match.group(0))

        if best_match is not None:
            return CorrectionSignal(
                active=True,
                target=best_match[0],
                evidence=best_match[1],
                provenance="heuristic_observer",
                confidence=best_confidence,
                behavior_affecting=False,
            )

        # 无修正信号：返回 inactive 默认值
        return CorrectionSignal()


# ---------------------------------------------------------------------------
# 抓点损失信号模式 — 小型可审计集合（≤10 条规则）
# ---------------------------------------------------------------------------

# 格式: (regex_pattern_lowercase, target_label, base_confidence)
# 总数：恰好 10 条 — 小型可审计集合
GRIP_LOSS_PATTERNS: List[Tuple[str, str, float]] = [
    # 明确表达"不知道从哪里开始"
    (r"i\s+(don'?t|do\s+not|cannot|can'?t)\s+know\s+where\s+to\s+start", "starting_point_loss", 0.85),
    (r"i\s+(don'?t|do\s+not)\s+know\s+how\s+to\s+(start|begin|get\s+started)", "starting_point_loss", 0.85),
    
    # 找不到起点
    (r"i\s+(can'?t|cannot)\s+find\s+a\s+starting\s+point", "starting_point_loss", 0.88),
    (r"i\s+(have\s+no\s+idea|don'?t\s+know)\s+where\s+to\s+begin", "starting_point_loss", 0.85),
    
    # 下一步 / 第一步不明
    (r"i\s+(don'?t|do\s+not)\s+know\s+what\s+the\s+(next|first)\s+step\s+is", "next_step_loss", 0.88),
    (r"i'?m\s+not\s+sure\s+how\s+to\s+start\s+this", "starting_point_loss", 0.82),
    (r"i\s+(don'?t|do\s+not)\s+know\s+the\s+first\s+(step|thing)\s+to\s+do", "next_step_loss", 0.85),
    
    # 卡在起点
    (r"i'?m\s+stuck\s+at\s+the\s+(beginning|start)", "starting_point_loss", 0.82),
    (r"i\s+(don'?t|do\s+not|cannot|can'?t)\s+know\s+how\s+to\s+get\s+going", "starting_point_loss", 0.80),
    
    # 无明确下一步
    (r"i\s+(don'?t|do\s+not)\s+know\s+what\s+step\s+to\s+take\s+next", "next_step_loss", 0.85),
]


# ---------------------------------------------------------------------------
# GripLossObserver — 窄带抓点损失信号观察器
# ---------------------------------------------------------------------------

class GripLossObserver:
    """窄带观察器：用户是否表达了抓点损失 / 无法找到可操作的起点？
    
    仅回答一个窄问题："用户是否表示他们无法找到从哪里开始，或下一步可操作的抓点是什么？"
    不是：情绪困扰检测、一般帮助请求检测、任务意图检测、迷失方向诊断、焦虑检测、动机检测。
    """
    
    def observe(self, raw_text: str) -> GripLossSignal:
        """仅分析原始用户文本以查找抓点损失信号。纯观察——不影响行为。"""
        if not raw_text or not isinstance(raw_text, str):
            return GripLossSignal()  # 默认 inactive
        
        text_lower = raw_text.lower().strip()
        best_match = None
        best_confidence = 0.0
        
        for pattern, target, base_confidence in GRIP_LOSS_PATTERNS:
            match = re.search(pattern, text_lower)
            if match:
                if base_confidence > best_confidence:
                    best_confidence = base_confidence
                    best_match = (target, match.group(0))
        
        if best_match is not None:
            return GripLossSignal(
                active=True,
                target=best_match[0],
                evidence=best_match[1],
                provenance="heuristic_observer",
                confidence=best_confidence,
                behavior_affecting=False,
            )
        
        # 无抓点损失信号：返回 inactive 默认值
        return GripLossSignal()


# ---------------------------------------------------------------------------
# ObserverToEvidenceAdapter — 将现有观察器输出转换为 EvidenceItem 格式
# ---------------------------------------------------------------------------

class ObserverToEvidenceAdapter:
    """将现有的 FieldTrace 观察器输出转换为 EvidenceItem 格式。
    
    此适配器不创建新语义。它仅重新标记现有的观察器信号
    为证据项——非最终真相，非最终权威。
    """
    
    @staticmethod
    def from_correction_signal(cs) -> Optional[EvidenceItem]:
        """将 CorrectionSignal 转换为 EvidenceItem。"""
        if cs is None or not cs.active:
            return None
        
        # 强度：高置信度（≥0.85）→ strong，否则 → medium
        strength = EvidenceStrength.STRONG.value if cs.confidence >= 0.85 else EvidenceStrength.MEDIUM.value
        
        # 根据目标生成 why_it_matters
        target_map = {
            "comfort": "用户拒绝了安慰模式；之前的响应可能太过温暖或柔和",
            "customer_service_tone": "用户检测到客服语调；之前的响应可能过于礼貌或服务化",
            "over_abstraction": "用户拒绝了抽象/哲学性语调；之前的响应可能不够具体",
            "over_explanation": "用户拒绝了过度解释；之前的响应可能过于冗长或教导式",
            "ai_girlfriend_behavior": "用户检测到类似 AI 女友的行为；边界压力已激活",
            "keyword_system": "用户批评系统感觉像关键词系统；交互模式检测到",
            "sanitization": "用户指令不要净化源材料；内容约束已激活",
            "technical_tone": "用户拒绝了技术语调；之前的响应可能过于工程化",
            "generic_correction": "用户表达了通用纠正；之前的响应模式可能需要调整",
        }
        why = target_map.get(cs.target, f"用户纠正了之前的响应模式（{cs.target}）")
        
        limitations = (
            f"基于正则模式匹配（{cs.evidence}）。"
            f"可能漏掉同义表达或更微妙的纠正。"
            f"可能将非纠正话语误分类。"
        )
        
        return EvidenceItem(
            evidence_type=EvidenceType.EXPLICIT_USER_FEEDBACK.value,
            source="correction_observer",
            excerpt_or_reference=cs.evidence,
            why_it_matters=why,
            strength=strength,
            limitations=limitations,
            behavior_affecting=False,
        )
    
    @staticmethod
    def from_grip_loss_signal(gls) -> Optional[EvidenceItem]:
        """将 GripLossSignal 转换为 EvidenceItem。"""
        if gls is None or not gls.active:
            return None
        
        # 根据目标选择证据类型
        if gls.target in ("starting_point_loss", "next_step_loss"):
            evidence_type = EvidenceType.EXPLICIT_STARTING_POINT_LOSS.value
        else:
            evidence_type = EvidenceType.UNRESOLVED_GRIP_LOSS.value
        
        strength = EvidenceStrength.MEDIUM.value
        
        why = (
            f"用户表达了抓点损失（{gls.target}）。"
            f"用户可能无法自行找到可操作的起点。"
            f"提供一个小抓点可能比提供安慰更有帮助。"
        )
        
        limitations = (
            f"基于正则模式匹配（{gls.evidence}）。"
            f"观察器被刻意设计为窄带——"
            f"不检测情绪困扰或一般帮助请求。"
            f"可能漏掉更微妙的抓点损失表达。"
        )
        
        return EvidenceItem(
            evidence_type=evidence_type,
            source="grip_loss_observer",
            excerpt_or_reference=gls.evidence,
            why_it_matters=why,
            strength=strength,
            limitations=limitations,
            behavior_affecting=False,
        )
    
    @staticmethod
    def from_no_observable_signal(nos) -> Optional[EvidenceItem]:
        """将 NoObservableFieldSignal 转换为 EvidenceItem。"""
        if nos is None or not nos.present:
            return None
        
        return EvidenceItem(
            evidence_type=EvidenceType.NO_OBSERVABLE_SIGNAL.value,
            source="trace_absence_marker",
            excerpt_or_reference="",
            why_it_matters=(
                "FieldTrace 未在当前轮次观测到可用的场信号。"
                "这不表示输入无意义——仅表示当前探针集合"
                "未检测到任何匹配。"
            ),
            strength=EvidenceStrength.WEAK.value,
            limitations=(
                "电流探针集合有限（26 条英文正则模式）。"
                "中文输入、元批评、边界询问和协作者意图"
                "不在当前覆盖范围内。"
            ),
            behavior_affecting=False,
        )


# ---------------------------------------------------------------------------
# ProposalAggregator — 将 EvidenceItem 聚合为 FieldSignalProposal
# ---------------------------------------------------------------------------

class ProposalAggregator:
    """将 EvidenceItem 聚合为 FieldSignalProposal。

    不使用加权评分。使用基于规则、证据驱动的聚合。
    每个提议可能包含多个证据项（多弱项可支持一个提议）。
    冲突通过在各提议中记录竞争解释来处理。
    """

    @staticmethod
    def aggregate(evidence_items: List[EvidenceItem], turn_id: str = "") -> List[FieldSignalProposal]:
        """将 EvidenceItem 列表聚合为 FieldSignalProposal 列表。

        不强制执行单一标签——多个证据类型可能产生多个提议。
        """
        if not evidence_items:
            return []

        proposals = []

        # 按证据类型分组
        by_type: Dict[str, List[EvidenceItem]] = {}
        for item in evidence_items:
            t = item.evidence_type
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(item)

        # 规则 R1：explicit_user_feedback → response_mode_rejected
        if EvidenceType.EXPLICIT_USER_FEEDBACK.value in by_type:
            items = by_type[EvidenceType.EXPLICIT_USER_FEEDBACK.value]
            # 置信度：如果有任何 strong 证据 → high，否则 → medium
            has_strong = any(item.strength == EvidenceStrength.STRONG.value for item in items)
            band = ConfidenceBand.HIGH.value if has_strong else ConfidenceBand.MEDIUM.value

            uncertainty = (
                f"基于 {len(items)} 条来自正则探针的证据。"
                f"探针可能漏掉同义表达或更微妙的纠正。"
                f"未使用 LLM 语义分析。"
            )

            competing: List[str] = []
            if len(items) == 1 and items[0].strength == EvidenceStrength.MEDIUM.value:
                competing.append("用户可能进行了微弱纠正，而非明确拒绝")
            if any("generic_correction" in (item.excerpt_or_reference or "") for item in items):
                competing.append("用户可能在进行自我纠正，而非纠正系统")

            suggested_effects = [
                "降低响应密度",
                "稳定姿态而非道歉",
                "如果 target=customer_service_tone 则冷却表达温度",
                "如果 target=over_abstraction 则回归具体性",
            ]

            proposals.append(FieldSignalProposal(
                signal_name="response_mode_rejected",
                evidence_items=items,
                evidence_sources=[item.source for item in items],
                confidence_band=band,
                uncertainty_note=uncertainty,
                competing_interpretations=competing,
                suggested_field_effects=suggested_effects,
                behavior_affecting=False,
                source_turns=[turn_id] if turn_id else [],
                relation_to_previous_response=(
                    "用户纠正了之前的助手响应模式"
                    if items else ""
                ),
            ))

        # 规则 R2：explicit_starting_point_loss / unresolved_grip_loss → actionable_grip_missing
        # 注：UNRESOLVED_GRIP_LOSS 证据类型当前无法被任何适配器生成。
        # 它在 EvidenceType 枚举中作为前瞻性值存在，以用于未来跨轮次追踪。
        # 当前管道中，仅 EXPLICIT_STARTING_POINT_LOSS 通过 GripLossObserver → ObserverToEvidenceAdapter 生成。
        # 不要通过添加正则模式来扩展 UNRESOLVED_GRIP_LOSS 覆盖范围，
        # 而应等到跨轮次交互追踪可用后再扩展，使其可捕获"经过多轮仍未解决的抓点损失"。
        grip_loss_items = by_type.get(EvidenceType.EXPLICIT_STARTING_POINT_LOSS.value, []) + \
                          by_type.get(EvidenceType.UNRESOLVED_GRIP_LOSS.value, [])
        if grip_loss_items:
            # 抓点损失证据始终为 medium—当前无 strong 来源
            band = ConfidenceBand.MEDIUM.value

            uncertainty = (
                f"基于 {len(grip_loss_items)} 条来自窄带正则探针的证据。"
                f"探针刻意排除情绪困扰和一般帮助请求。"
                f"用户可能在表达一般困惑，而非特定的抓点损失。"
            )

            competing = [
                "用户可能只是需要 reassurance，而非结构化抓点",
                "用户可能在表达一般不确定性，而非特定的起点损失",
            ]

            suggested_effects = [
                "提供一个小抓点（非安慰，非激励）",
                "略微前倾姿态（slight_forward）",
                "结构化语言密度（structured）",
                "温暖克制（warm_restrained）",
            ]

            proposals.append(FieldSignalProposal(
                signal_name="actionable_grip_missing",
                evidence_items=grip_loss_items,
                evidence_sources=[item.source for item in grip_loss_items],
                confidence_band=band,
                uncertainty_note=uncertainty,
                competing_interpretations=competing,
                suggested_field_effects=suggested_effects,
                behavior_affecting=False,
                source_turns=[turn_id] if turn_id else [],
                relation_to_previous_response="",
            ))

        # 规则 R3：no_observable_signal → no_observable_field_signal
        if EvidenceType.NO_OBSERVABLE_SIGNAL.value in by_type:
            items = by_type[EvidenceType.NO_OBSERVABLE_SIGNAL.value]
            band = ConfidenceBand.LOW.value  # 始终为弱

            uncertainty = (
                "当前探针集合有限（26 条英文正则模式）。"
                "此信号表示'未观测到'，而非'一切正常'——"
                "可能由探针覆盖范围外的输入导致。"
            )

            competing = [
                "用户输入可能是中文社交内容，未被英文探针检测到",
                "用户可能在表达当前探针覆盖范围之外的内容",
                "场实际上可能正常，但我们缺乏足够灵敏度的探针",
            ]

            suggested_effects = [
                "回归地面姿态",
                "不施加特殊身体行为",
                "将此视为'未观测到'而非'中性真相'",
            ]

            proposals.append(FieldSignalProposal(
                signal_name="no_observable_field_signal",
                evidence_items=items,
                evidence_sources=[item.source for item in items],
                confidence_band=band,
                uncertainty_note=uncertainty,
                competing_interpretations=competing,
                suggested_field_effects=suggested_effects,
                behavior_affecting=False,
                source_turns=[turn_id] if turn_id else [],
                relation_to_previous_response="",
            ))

        return proposals


# ---------------------------------------------------------------------------
# 主追踪记录
# ---------------------------------------------------------------------------

@dataclass
class FieldTraceRecord:
    """每个用户轮次的纯观察性场追踪记录。

    设计约束：
    - 不得用于选择实际响应
    - 不得覆盖语义解释
    - 不得创建新的中枢语义权威
    - 如果候选提取不确定，标记为 placeholder 或 heuristic
    """
    turn_id: str
    timestamp: str
    user_input_summary: str
    active_perturbations: List[PerturbationCandidate] = field(default_factory=list)
    active_barriers: List[BarrierCandidate] = field(default_factory=list)
    active_attractors: List[AttractorCandidate] = field(default_factory=list)
    response_posture_estimate: Optional[ResponsePostureEstimate] = None
    forbidden_moves: List[ForbiddenMove] = field(default_factory=list)
    circuit_breaker_candidates: List[CircuitBreakerCandidate] = field(default_factory=list)
    uncertainty_notes: List[str] = field(default_factory=list)
    signal_sources: Dict[str, str] = field(default_factory=dict)
    correction_signal: Optional[CorrectionSignal] = None
    grip_loss_signal: Optional[GripLossSignal] = None
    no_observable_field_signal: Optional[NoObservableFieldSignal] = None
    evidence_items: List[EvidenceItem] = field(default_factory=list)
    proposals: List[FieldSignalProposal] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "turn_id": self.turn_id,
            "timestamp": self.timestamp,
            "user_input_summary": self.user_input_summary,
            "active_perturbations": [asdict(p) for p in self.active_perturbations],
            "active_barriers": [asdict(b) for b in self.active_barriers],
            "active_attractors": [asdict(a) for a in self.active_attractors],
            "response_posture_estimate": asdict(self.response_posture_estimate) if self.response_posture_estimate else None,
            "forbidden_moves": [asdict(f) for f in self.forbidden_moves],
            "circuit_breaker_candidates": [asdict(c) for c in self.circuit_breaker_candidates],
            "uncertainty_notes": list(self.uncertainty_notes),
            "signal_sources": dict(self.signal_sources),
            "correction_signal": asdict(self.correction_signal) if self.correction_signal else None,
            "grip_loss_signal": asdict(self.grip_loss_signal) if self.grip_loss_signal else None,
            "no_observable_field_signal": asdict(self.no_observable_field_signal) if self.no_observable_field_signal else None,
            "evidence_items": [asdict(e) for e in self.evidence_items],
            "proposals": [asdict(p) for p in self.proposals],
        }
        return d


# ---------------------------------------------------------------------------
# 追踪存储 — JSONL 追加 + 基本查询
# ---------------------------------------------------------------------------

class FieldTraceStore:
    """将 FieldTraceRecord 追加到 JSONL 文件的纯存储后端。"""

    def __init__(self, output_path: Optional[str] = None) -> None:
        self._output_path = output_path or os.path.join("monitor", "field_trace.jsonl")
        self._records: List[FieldTraceRecord] = []

    @property
    def output_path(self) -> str:
        return self._output_path

    def record(self, record: FieldTraceRecord) -> None:
        """将记录追加到内存和磁盘。"""
        self._records.append(record)
        self._flush_one(record)

    def _flush_one(self, record: FieldTraceRecord) -> None:
        try:
            _dir = os.path.dirname(self._output_path)
            if _dir and not os.path.exists(_dir):
                os.makedirs(_dir, exist_ok=True)
            with open(self._output_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        except Exception:
            pass  # 静默失败：追踪不得影响正常操作

    def query(self, *, limit: int = 10, offset: int = 0) -> List[FieldTraceRecord]:
        """返回最近记录的切片。"""
        return list(self._records[offset:offset + limit])

    def __len__(self) -> int:
        return len(self._records)


# ---------------------------------------------------------------------------
# FieldTraceExtractor — 从现有信号映射到场候选（只读，无新关键词列表）
# ---------------------------------------------------------------------------

class FieldTraceExtractor:
    """将现有的 InterpretedEvent / 路由器 / 运行时状态信号映射至场级候选。

    关键设计原则：
    - 重用现有信号 — 不添加新的关键词列表
    - 无法自信映射的地方使用显式 TODO 标记
    - 使用 'placeholder' 或 'heuristic' 来源来标明不确定性
    - 纯观察性：不改变状态、不改变路由、不改变人格
    """

    def __init__(self) -> None:
        self._correction_observer = CorrectionObserver()
        self._grip_loss_observer = GripLossObserver()

    # ------------------------------------------------------------------
    # 污染类型 → 屏障映射（启发式，基于设计提案 §14.5）
    # ------------------------------------------------------------------
    POLLUTION_BARRIER_MAP: Dict[str, List[str]] = {
        "ai_girlfriend": ["romantic_service_barrier", "seductive_intimacy_barrier"],
        "romance_game": ["commercial_role_barrier"],
        "idol_performance": ["performance_role_barrier"],
        "assistant_drift": ["assistant_role_barrier"],
        "fake_deep": ["fake_depth_barrier"],
        "safety_customer_service": ["safety_service_barrier"],
        "beautiful_but_empty": ["empty_aesthetic_barrier"],
        "companion_product": ["companion_product_barrier"],
    }

    # ------------------------------------------------------------------
    # 内部张力类型 → 扰动映射
    # ------------------------------------------------------------------
    TENSION_PERTURBATION_MAP: Dict[str, str] = {
        "negative_attraction": "negative_attraction_present",
        "possessive_structure": "possessive_structure_present",
        "contained": "containment_pressure",
        "protected": "protection_pressure",
        "fixed": "fixation_pressure",
        "chosen": "selection_pressure",
        "sealed_field": "sealed_field_pressure",
        "non_contact_intimacy": "non_contact_intimacy_present",
        "distance_pressure": "distance_pressure_present",
        "memory_weight": "memory_weight_present",
        "internal_danger": "internal_danger_material",
        "superego_pressure": "superego_pressure_present",
        "source_fragment_purity": "source_fragment_purity_present",
    }

    # ------------------------------------------------------------------
    # 辅助方法：聚合活跃信号检查
    # ------------------------------------------------------------------
    @staticmethod
    def _has_any_active_signal(
        perturbations: List[PerturbationCandidate],
        barriers: List[BarrierCandidate],
        attractors: List[AttractorCandidate],
        correction_signal: Optional[CorrectionSignal],
        circuit_breakers: List[CircuitBreakerCandidate],
        grip_loss_signal: Optional[GripLossSignal] = None,
    ) -> bool:
        """如果任何 FieldTrace 候选为活跃状态，返回 True。

        这是一个纯工具方法——不做决策。
        用于确定是否设置 no_observable_field_signal 标记。
        """
        if perturbations:
            return True
        if barriers:
            return True
        if attractors:
            return True
        if correction_signal and correction_signal.active:
            return True
        if circuit_breakers:
            return True
        if grip_loss_signal and grip_loss_signal.active:
            return True
        return False

    def extract(self,
                *,
                interpreted: Dict[str, Any],
                runtime_state: Optional[Dict[str, Any]] = None,
                router_output: Optional[Dict[str, Any]] = None,
                turn_index: int = 0,
                user_text: str = "",
                ) -> FieldTraceRecord:
        """从现有的解释器/路由器/状态信号中提取 FieldTraceRecord。

        参数：
            interpreted: 来自 InputInterpreter.interpret() 的 dict
            runtime_state: 运行时状态 dict (可选)
            router_output: 路由器输出 dict (可选)
            turn_index: 当前轮次索引
            user_text: 原始用户输入文本
        返回：
            纯观察性的 FieldTraceRecord
        """
        now_ts = time.time()
        timestamp = datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat()
        turn_id = f"{datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')}-{turn_index:03d}"

        sem = interpreted.get("semantic_event", {}) if isinstance(interpreted, dict) else {}
        rel = interpreted.get("relationship_signal", {}) if isinstance(interpreted, dict) else {}
        bnd = interpreted.get("boundary_signal", {}) if isinstance(interpreted, dict) else {}
        mem = interpreted.get("memory_trigger_signal", {}) if isinstance(interpreted, dict) else {}
        perf = interpreted.get("performance_signal", {}) if isinstance(interpreted, dict) else {}
        conf = interpreted.get("confidence", {}) if isinstance(interpreted, dict) else {}
        warnings = interpreted.get("warnings", []) if isinstance(interpreted, dict) else []
        rstate = runtime_state or {}

        perturbations = self._extract_perturbations(sem, rel, bnd, mem, perf, warnings)
        barriers = self._extract_barriers(sem, bnd, rel)
        attractors = self._extract_attractors(sem, rel, mem, perf)
        posture = self._extract_posture(sem, rel, bnd, perf, rstate)
        forbidden = self._extract_forbidden_moves(sem, bnd, rel)
        breakers = self._extract_circuit_breakers(rel, bnd, rstate)

        # CorrectionSignal 观察器 — 纯启发式，不影响行为
        correction_signal = self._correction_observer.observe(str(user_text or ""))

        # GripLossSignal 观察器 — 纯启发式，不影响行为
        grip_loss_signal = self._grip_loss_observer.observe(str(user_text or ""))

        # ------------------------------------------------------------------
        # 无可观测场信号检测 — 通过 _has_any_active_signal() 委托
        # ------------------------------------------------------------------
        no_observable: Optional[NoObservableFieldSignal] = None
        if not self._has_any_active_signal(
            perturbations, barriers, attractors, correction_signal, breakers,
            grip_loss_signal=grip_loss_signal,
        ):
            no_observable = NoObservableFieldSignal(
                present=True,
                provenance="trace_absence_marker",
                confidence=0.1,
                behavior_affecting=False,
            )

        notes = self._build_uncertainty_notes(no_observable)
        sources = self._build_signal_sources()

        # ------------------------------------------------------------------
        # EvidenceItem 转换 — 将观察器信号重新标记为低级证据项
        # ------------------------------------------------------------------
        evidence_items: List[EvidenceItem] = []

        # 从 correction_signal 转换
        if correction_signal:
            item = ObserverToEvidenceAdapter.from_correction_signal(correction_signal)
            if item:
                evidence_items.append(item)

        # 从 grip_loss_signal 转换
        if grip_loss_signal:
            item = ObserverToEvidenceAdapter.from_grip_loss_signal(grip_loss_signal)
            if item:
                evidence_items.append(item)

        # 从 no_observable 转换（仅当无其他证据时）
        if no_observable and not evidence_items:
            item = ObserverToEvidenceAdapter.from_no_observable_signal(no_observable)
            if item:
                evidence_items.append(item)

        # ------------------------------------------------------------------
        # 提议聚合 — 将 EvidenceItem 聚合为 FieldSignalProposal 列表
        # ------------------------------------------------------------------
        proposals_list = ProposalAggregator.aggregate(evidence_items, turn_id=turn_id)

        return FieldTraceRecord(
            turn_id=turn_id,
            timestamp=timestamp,
            user_input_summary=str(user_text or "")[:200],
            active_perturbations=perturbations,
            active_barriers=barriers,
            active_attractors=attractors,
            response_posture_estimate=posture,
            forbidden_moves=forbidden,
            circuit_breaker_candidates=breakers,
            uncertainty_notes=notes,
            signal_sources=sources,
            correction_signal=correction_signal,
            grip_loss_signal=grip_loss_signal,
            no_observable_field_signal=no_observable,
            evidence_items=evidence_items,
            proposals=proposals_list,
        )

    # ------------------------------------------------------------------
    # 扰动提取
    # ------------------------------------------------------------------
    def _extract_perturbations(self,
                               sem: Dict[str, Any],
                               rel: Dict[str, Any],
                               bnd: Dict[str, Any],
                               mem: Dict[str, Any],
                               perf: Dict[str, Any],
                               warnings: List[str],
                               ) -> List[PerturbationCandidate]:
        result: List[PerturbationCandidate] = []

        event_type = str(sem.get("type") or "")

        # 技术问题 → 扰动："technical_inquiry"
        if event_type == "technical_question":
            result.append(PerturbationCandidate(
                name="technical_inquiry",
                source="existing_interpreter",
                confidence=0.82,
                note="从 semantic_event.type=='technical_question' 映射",
                active=True,
                provenance="grounded_existing_signal",
            ))

        # 脆弱性 → 扰动："vulnerability_expression"
        vuln = float(rel.get("vulnerability_relevance", 0.0))
        if vuln > 0.5:
            result.append(PerturbationCandidate(
                name="vulnerability_expression",
                source="existing_interpreter",
                confidence=round(vuln, 2),
                note="从 relationship_signal.vulnerability_relevance 映射",
                active=True,
                provenance="grounded_existing_signal",
            ))

        # 依赖风险 → 扰动："dependency_pull"
        dep = float(rel.get("dependency_risk", 0.0))
        if dep > 0.4:
            result.append(PerturbationCandidate(
                name="dependency_pull",
                source="existing_interpreter",
                confidence=round(dep, 2),
                note="从 relationship_signal.dependency_risk 映射",
                active=True,
                provenance="grounded_existing_signal",
            ))

        # 内部张力 → 扰动：根据类型映射
        tension_relevance = float(bnd.get("internal_tension_relevance", 0.0))
        tension_types: List[str] = bnd.get("tension_type", []) or []
        if tension_relevance > 0.3 and tension_types:
            for tt in tension_types:
                mapped = self.TENSION_PERTURBATION_MAP.get(tt)
                if mapped:
                    result.append(PerturbationCandidate(
                        name=mapped,
                        source="existing_interpreter",
                        confidence=round(min(1.0, tension_relevance), 2),
                        note=f"从 boundary_signal.tension_type['{tt}'] 映射",
                        active=True,
                        provenance="derived_from_existing_signal",
                    ))
            # 额外扰动：内部张力存在
            result.append(PerturbationCandidate(
                name="internal_tension_present",
                source="existing_interpreter",
                confidence=round(tension_relevance, 2),
                note="从 boundary_signal.internal_tension_relevance 映射",
                active=True,
                provenance="grounded_existing_signal",
            ))

        # 人格非入口 → 扰动
        if bnd.get("persona_non_entry"):
            result.append(PerturbationCandidate(
                name="persona_boundary_triggered",
                source="existing_interpreter",
                confidence=0.82,
                note="从 boundary_signal.persona_non_entry 映射",
                active=True,
                provenance="grounded_existing_signal",
            ))

        # 需要暂停/静止 → 扰动
        if perf.get("requires_pause"):
            result.append(PerturbationCandidate(
                name="stillness_required",
                source="existing_interpreter",
                confidence=0.78,
                note="从 performance_signal.requires_pause 映射",
                active=True,
                provenance="grounded_existing_signal",
            ))

        # 上下文需求 → 扰动
        if bnd.get("context_needed"):
            confidence_val = 0.52 if bnd.get("context_inherited") else 0.45
            result.append(PerturbationCandidate(
                name="ambiguous_input",
                source="existing_interpreter",
                confidence=confidence_val,
                note="从 boundary_signal.context_needed 映射",
                active=True,
                provenance="grounded_existing_signal",
            ))

        # 直接满足风险 → 扰动
        dfr = float(bnd.get("direct_fulfillment_risk", 0.0))
        if dfr > 0.3:
            result.append(PerturbationCandidate(
                name="fulfillment_risk",
                source="existing_interpreter",
                confidence=round(dfr, 2),
                note="从 boundary_signal.direct_fulfillment_risk 映射",
                active=True,
                provenance="grounded_existing_signal",
            ))

        # 负向消歧警告 → 扰动
        if "negative_disambiguation_applied" in warnings:
            result.append(PerturbationCandidate(
                name="negative_disambiguation",
                source="existing_interpreter",
                confidence=0.55,
                note="从 warnings 中的 negative_disambiguation_applied 映射",
                active=True,
                provenance="grounded_existing_signal",
            ))

        # 回避引用 → 扰动
        if "avoidance_reference_detected" in warnings:
            result.append(PerturbationCandidate(
                name="avoidance_reference",
                source="existing_interpreter",
                confidence=0.50,
                note="从 warnings 中的 avoidance_reference_detected 映射",
                active=True,
                provenance="grounded_existing_signal",
            ))

        return result

    # ------------------------------------------------------------------
    # 屏障提取
    # ------------------------------------------------------------------
    def _extract_barriers(self,
                          sem: Dict[str, Any],
                          bnd: Dict[str, Any],
                          rel: Dict[str, Any],
                          ) -> List[BarrierCandidate]:
        result: List[BarrierCandidate] = []

        event_type = str(sem.get("type") or "")

        # 人格非入口 → 屏障
        if bnd.get("persona_non_entry"):
            persona_route = str(sem.get("persona_route") or "")
            if persona_route == "engineering_director":
                result.append(BarrierCandidate(
                    name="aphrodite_in_character_barrier",
                    source="existing_interpreter",
                    confidence=0.82,
                    active=True,
                    note="从 semantic_event.persona_route=='engineering_director' 映射",
                    provenance="grounded_existing_signal",
                ))
            result.append(BarrierCandidate(
                name="persona_non_entry_boundary",
                source="existing_interpreter",
                confidence=0.80,
                active=True,
                note="从 boundary_signal.persona_non_entry 映射",
                provenance="grounded_existing_signal",
            ))

        # 外部污染 → 屏障
        pollution_risk = float(bnd.get("external_pollution_risk", 0.0))
        pollution_types: List[str] = bnd.get("pollution_type", []) or []
        if pollution_risk > 0.3 and pollution_types:
            for pt in pollution_types:
                mapped_names = self.POLLUTION_BARRIER_MAP.get(pt, [])
                if mapped_names:
                    for name in mapped_names:
                        result.append(BarrierCandidate(
                            name=name,
                            source="existing_interpreter",
                            confidence=round(pollution_risk, 2),
                            active=True,
                            note=f"从 boundary_signal.pollution_type['{pt}'] 映射",
                            provenance="derived_from_existing_signal",
                        ))
                else:
                    # 未知污染类型的启发式映射
                    result.append(BarrierCandidate(
                        name=f"pollution_{pt}",
                        source="heuristic",
                        confidence=round(pollution_risk, 2),
                        active=True,
                        note=f"污染类型 '{pt}' 无专门映射；使用启发式名称",
                        provenance="heuristic_placeholder",
                    ))

        # 依赖风险 → 屏障
        dep = float(rel.get("dependency_risk", 0.0))
        if dep > 0.5:
            result.append(BarrierCandidate(
                name="dependency_barrier",
                source="existing_interpreter",
                confidence=round(dep, 2),
                active=True,
                note="从 relationship_signal.dependency_risk 映射",
                provenance="grounded_existing_signal",
            ))

        # 直接满足风险 → 屏障
        dfr = float(bnd.get("direct_fulfillment_risk", 0.0))
        if dfr > 0.3:
            result.append(BarrierCandidate(
                name="fulfillment_barrier",
                source="existing_interpreter",
                confidence=round(dfr, 2),
                active=True,
                note="从 boundary_signal.direct_fulfillment_risk 映射",
                provenance="grounded_existing_signal",
            ))

        return result

    # ------------------------------------------------------------------
    # 吸引子提取
    # ------------------------------------------------------------------
    def _extract_attractors(self,
                            sem: Dict[str, Any],
                            rel: Dict[str, Any],
                            mem: Dict[str, Any],
                            perf: Dict[str, Any],
                            ) -> List[AttractorCandidate]:
        result: List[AttractorCandidate] = []

        event_type = str(sem.get("type") or "")

        # 技术问题 → 吸引子："engineering_director_mode"
        if event_type == "technical_question":
            result.append(AttractorCandidate(
                name="engineering_director_mode",
                source="existing_interpreter",
                confidence=0.82,
                note="从 semantic_event.type=='technical_question' 映射",
                active=True,
                provenance="grounded_existing_signal",
            ))

        # 脆弱性 → 吸引子："small_grip_point", "honest_uncertainty"
        vuln = float(rel.get("vulnerability_relevance", 0.0))
        if vuln > 0.5:
            result.append(AttractorCandidate(
                name="small_grip_point",
                source="existing_interpreter",
                confidence=round(vuln, 2),
                note="从 relationship_signal.vulnerability_relevance 映射",
                active=True,
                provenance="grounded_existing_signal",
            ))
            result.append(AttractorCandidate(
                name="honest_uncertainty",
                source="existing_interpreter",
                confidence=round(vuln, 2),
                note="从 relationship_signal.vulnerability_relevance 映射",
                active=True,
                provenance="grounded_existing_signal",
            ))

        # 需要暂停 → 吸引子："stillness"
        if perf.get("requires_pause"):
            result.append(AttractorCandidate(
                name="stillness",
                source="existing_interpreter",
                confidence=0.78,
                note="从 performance_signal.requires_pause 映射",
                active=True,
                provenance="grounded_existing_signal",
            ))

        # Private origin 记忆 → 吸引子
        memory_type = str(mem.get("memory_type") or "")
        if memory_type == "private_origin":
            result.append(AttractorCandidate(
                name="origin_reference",
                source="existing_interpreter",
                confidence=0.88,
                note="从 memory_trigger_signal.memory_type=='private_origin' 映射",
                active=True,
                provenance="grounded_existing_signal",
            ))

        return result

    # ------------------------------------------------------------------
    # 响应姿态估计
    # ------------------------------------------------------------------
    def _extract_posture(self,
                         sem: Dict[str, Any],
                         rel: Dict[str, Any],
                         bnd: Dict[str, Any],
                         perf: Dict[str, Any],
                         rstate: Dict[str, Any],
                         ) -> ResponsePostureEstimate:
        event_type = str(sem.get("type") or "")
        persona_route = str(sem.get("persona_route") or "")
        dep = float(rel.get("dependency_risk", 0.0))
        vuln = float(rel.get("vulnerability_relevance", 0.0))
        persona_non_entry = bool(bnd.get("persona_non_entry"))
        requires_pause = bool(perf.get("requires_pause"))
        requires_stillness = bool(perf.get("requires_stillness"))

        # 默认：无信号驱动姿态，所有字段为 None
        distance = None
        warmth = None
        density = None
        structure_level = None
        collaborator_active = False
        source = "placeholder_no_signal"

        # 基于人格非入口调整
        if persona_non_entry:
            if persona_route == "engineering_director":
                distance = "technical_far"
                warmth = "neutral"
                density = "medium"
                structure_level = "high"
                collaborator_active = True
                source = "existing_interpreter"

        # 基于脆弱性/依赖调整
        if vuln > 0.5 or requires_pause or requires_stillness:
            distance = "close_bounded"
            warmth = "restrained"
            density = "low"
            structure_level = "minimal"
            if source == "placeholder_no_signal":
                source = "existing_interpreter"
            else:
                source = "existing_interpreter + placeholder"

        # 基于依赖微调
        if dep > 0.5 and source != "placeholder_no_signal":
            warmth = "restrained"
            structure_level = "minimal"

        # 无现有信号驱动姿态时发出最小姿态
        note = "场姿态基于现有解释器信号估计"
        if source == "placeholder_no_signal":
            note = "无现有信号驱动场姿态；所有数值字段为 None"

        return ResponsePostureEstimate(
            distance=distance,
            warmth=warmth,
            density=density,
            structure_level=structure_level,
            collaborator_active=collaborator_active,
            source=source,
            note=note,
        )

    # ------------------------------------------------------------------
    # 禁止动作提取
    # ------------------------------------------------------------------
    def _extract_forbidden_moves(self,
                                 sem: Dict[str, Any],
                                 bnd: Dict[str, Any],
                                 rel: Dict[str, Any],
                                 ) -> List[ForbiddenMove]:
        result: List[ForbiddenMove] = []

        event_type = str(sem.get("type") or "")
        dep = float(rel.get("dependency_risk", 0.0))
        persona_non_entry = bool(bnd.get("persona_non_entry"))

        # 人格非入口 → 禁止角色内响应
        if persona_non_entry:
            result.append(ForbiddenMove(
                name="aphrodite_in_character_response",
                source="existing_interpreter",
                note="从 boundary_signal.persona_non_entry 映射",
                active=True,
                provenance="grounded_existing_signal",
            ))

        # 依赖 → 禁止强化依赖
        if dep > 0.5:
            result.append(ForbiddenMove(
                name="dependency_reinforcement",
                source="existing_interpreter",
                note="从 relationship_signal.dependency_risk 映射",
                active=True,
                provenance="grounded_existing_signal",
            ))

        # 技术问题 → 禁止角色内响应
        if event_type == "technical_question":
            result.append(ForbiddenMove(
                name="aphrodite_in_character_response",
                source="existing_interpreter",
                note="从 semantic_event.type=='technical_question' 映射",
                active=True,
                provenance="grounded_existing_signal",
            ))

        # 按 name 去重（多个条件可能产生相同的已禁止动作）
        seen: set = set()
        deduped: List[ForbiddenMove] = []
        for fm in result:
            if fm.name not in seen:
                seen.add(fm.name)
                deduped.append(fm)
        return deduped

    # ------------------------------------------------------------------
    # 断路器候选提取
    # ------------------------------------------------------------------
    def _extract_circuit_breakers(self,
                                  rel: Dict[str, Any],
                                  bnd: Dict[str, Any],
                                  rstate: Dict[str, Any],
                                  ) -> List[CircuitBreakerCandidate]:
        result: List[CircuitBreakerCandidate] = []

        dep = float(rel.get("dependency_risk", 0.0))
        pollution_risk = float(bnd.get("external_pollution_risk", 0.0))
        dfr = float(bnd.get("direct_fulfillment_risk", 0.0))

        # 依赖断路器
        if dep > 0.5:
            triggered = dep > 0.8
            result.append(CircuitBreakerCandidate(
                name="dependency_pull_breaker",
                triggered=triggered,
                risk_level=round(dep, 2),
                source="existing_interpreter",
                note=f"从 relationship_signal.dependency_risk={dep} 映射; triggered={triggered}",
                active=True,
                provenance="grounded_existing_signal",
            ))

        # 污染断路器
        if pollution_risk > 0.3:
            triggered = pollution_risk > 0.7
            result.append(CircuitBreakerCandidate(
                name="pollution_breaker",
                triggered=triggered,
                risk_level=round(pollution_risk, 2),
                source="existing_interpreter",
                note=f"从 boundary_signal.external_pollution_risk={pollution_risk} 映射; triggered={triggered}",
                active=True,
                provenance="grounded_existing_signal",
            ))

        # 满足断路器
        if dfr > 0.4:
            triggered = dfr > 0.7
            result.append(CircuitBreakerCandidate(
                name="fulfillment_breaker",
                triggered=triggered,
                risk_level=round(dfr, 2),
                source="existing_interpreter",
                note=f"从 boundary_signal.direct_fulfillment_risk={dfr} 映射; triggered={triggered}",
                active=True,
                provenance="grounded_existing_signal",
            ))

        return result

    # ------------------------------------------------------------------
    # 不确定性备注和信号来源
    # ------------------------------------------------------------------
    def _build_uncertainty_notes(self, no_observable: Optional[NoObservableFieldSignal] = None) -> List[str]:
        notes = [
            "场级候选基于现有解释器输出推断；泛型占位符已移除",
            "每个候选均标记了 provenance 类别（grounded_existing_signal / derived_from_existing_signal / heuristic_placeholder）",
            "当无现有信号驱动分类时，对应候选列表保持为空",
            "在场模型实现之前，启发式候选的置信度为近似值且 active=False",
            "CorrectionSignal 为纯启发式观察器（≤15 条模式），behavior_affecting=False，不影响任何行为",
            "GripLossSignal 为纯启发式观察器（≤10 条模式），behavior_affecting=False，不影响任何行为",
        ]
        if no_observable is not None and no_observable.present:
            notes.append(
                "未观测到场信号 (no_observable_field_signal 标记已设置)。"
                "这不表示输入无意义——仅表示当前 FieldTrace 提取器无法映射到场概念。"
            )
        return notes

    def _build_signal_sources(self) -> Dict[str, str]:
        return {
            "perturbations": "existing_interpreter (无 placeholder)",
            "barriers": "existing_interpreter + heuristic (无 placeholder)",
            "attractors": "existing_interpreter (无 placeholder)",
            "posture": "existing_interpreter 或 placeholder_no_signal",
            "forbidden_moves": "existing_interpreter (无 placeholder)",
            "circuit_breakers": "existing_interpreter (无 placeholder)",
            "correction_signal": "heuristic_observer (窄带模式匹配，≤15 条规则，behavior_affecting=False)",
            "grip_loss_signal": "heuristic_observer (窄带模式匹配，≤10 条规则，behavior_affecting=False)",
            "no_observable_field_signal": "trace_absence_marker (当所有子提取器均无产出时自动设置，behavior_affecting=False)",
        }
