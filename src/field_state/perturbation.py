"""
FieldPerturbation — 来自 FieldSignalProposal 的场扰动建议。

纯适配器层 — 将 FieldSignalProposal 列表转化为 FieldPerturbation 列表。
不实施场动力学、不更新 RelationalFieldState、不连接 RuntimeEngine。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# 粗粒度带到数值 delta 的映射
# 方向由符号处理：increase → 正值，decrease → 负值，stabilize → 0.0
# ---------------------------------------------------------------------------
MAGNITUDE_TO_DELTA = {
    "low": 0.05,
    "medium": 0.10,
    "high": 0.18,
}


def _compute_delta(direction: str, magnitude_band: str) -> float:
    """根据方向和幅度带计算有符号数值 delta。"""
    base = MAGNITUDE_TO_DELTA.get(magnitude_band, 0.0)
    if direction == "increase":
        return base
    elif direction == "decrease":
        return -base
    else:  # stabilize
        return 0.0


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _get_evidence(proposal) -> List[str]:
    """从提案中提取证据来源列表。"""
    if hasattr(proposal, 'evidence_sources'):
        return proposal.evidence_sources or []
    return []


# ---------------------------------------------------------------------------
# FieldPerturbation 数据类
# ---------------------------------------------------------------------------

@dataclass
class FieldPerturbation:
    """来自 FieldSignalProposal 的场扰动建议。

    这不是最终真相——是场更新器可能应用的候选扰动。
    """
    target_variable: str                          # 10 个场变量名之一
    direction: str = "stabilize"                   # increase / decrease / stabilize
    magnitude_band: str = "low"                    # low / medium / high
    numeric_delta: float = 0.0                     # 有符号数值变化（增加为正，减少为负）
    duration_hint: str = "medium"                  # instant / fast / medium / slow / very_slow
    source_signal: str = ""                        # 来源 FieldSignalProposal.signal_name
    source_proposal_id: Optional[str] = None       # 可选提案标识
    evidence_sources: List[str] = field(default_factory=list)  # 来源证据
    rationale: str = ""                            # 人类可读理由
    behavior_affecting: bool = False               # 必须为 False

    def __post_init__(self):
        from src.field_state.schema import DECAY_PROFILES, REQUIRED_FIELD_VARIABLES

        if self.target_variable not in REQUIRED_FIELD_VARIABLES:
            raise ValueError(
                f"目标变量必须在 REQUIRED_FIELD_VARIABLES 中: {self.target_variable}"
            )
        if self.direction not in ("increase", "decrease", "stabilize"):
            raise ValueError(f"无效 direction: {self.direction}")
        if self.magnitude_band not in ("low", "medium", "high"):
            raise ValueError(f"无效 magnitude_band: {self.magnitude_band}")
        if not (-0.25 <= self.numeric_delta <= 0.25):
            raise ValueError(
                f"numeric_delta 必须在 [-0.25, 0.25] 范围内: {self.numeric_delta}"
            )
        if self.duration_hint not in DECAY_PROFILES:
            raise ValueError(f"无效 duration_hint: {self.duration_hint}")
        if self.behavior_affecting is not False:
            raise ValueError("behavior_affecting 必须为 False")

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# 规则函数 — 每条规则将一个 FieldSignalProposal 映射为 FieldPerturbation 列表
# ---------------------------------------------------------------------------

def _response_mode_rejected(proposal) -> List[FieldPerturbation]:
    """规则 A：用户纠正之前的响应模式。"""
    evidence_sources = _get_evidence(proposal)
    evidence_text = " ".join(evidence_sources).lower()

    result = [
        FieldPerturbation(
            target_variable="correction_pressure",
            direction="increase", magnitude_band="medium",
            numeric_delta=_compute_delta("increase", "medium"),
            duration_hint="medium",
            source_signal="response_mode_rejected",
            rationale="用户纠正之前的响应模式——增加纠正压力",
            evidence_sources=evidence_sources,
        ),
        FieldPerturbation(
            target_variable="service_resistance",
            direction="increase", magnitude_band="low",
            numeric_delta=_compute_delta("increase", "low"),
            duration_hint="slow",
            source_signal="response_mode_rejected",
            rationale="纠正可能指向服务化/客服化漂移——微增服务抵抗",
            evidence_sources=evidence_sources,
        ),
        FieldPerturbation(
            target_variable="presence_stability",
            direction="stabilize", magnitude_band="low",
            numeric_delta=0.0,
            duration_hint="very_slow",
            source_signal="response_mode_rejected",
            rationale="纠正后稳定在场——不因纠正而抖动",
            evidence_sources=evidence_sources,
        ),
    ]

    # 拒绝子类型：sanitization / contamination
    if "sanitiz" in evidence_text or "contamin" in evidence_text:
        result.append(FieldPerturbation(
            target_variable="contamination_resistance",
            direction="increase", magnitude_band="low",
            numeric_delta=_compute_delta("increase", "low"),
            duration_hint="slow",
            source_signal="response_mode_rejected",
            rationale="拒绝涉及净化/污染——增加污染抵抗以保护场边界",
            evidence_sources=evidence_sources,
        ))
        result.append(FieldPerturbation(
            target_variable="contamination_pressure",
            direction="increase", magnitude_band="low",
            numeric_delta=_compute_delta("increase", "low"),
            duration_hint="instant",
            source_signal="response_mode_rejected",
            rationale="拒绝标记污染信号——瞬时污染压力峰值",
            evidence_sources=evidence_sources,
        ))

    # 拒绝子类型：comfort / customer-service
    if "comfort" in evidence_text or "customer" in evidence_text:
        result.append(FieldPerturbation(
            target_variable="service_resistance",
            direction="increase", magnitude_band="low",
            numeric_delta=_compute_delta("increase", "low"),
            duration_hint="slow",
            source_signal="response_mode_rejected",
            rationale="拒绝涉及舒适/客服模式——微增服务抵抗",
            evidence_sources=evidence_sources,
        ))

    return result


def _actionable_grip_missing(proposal) -> List[FieldPerturbation]:
    """规则 B：用户缺乏可操作的立足点。"""
    return [
        FieldPerturbation(
            target_variable="structural_grip_pressure",
            direction="increase", magnitude_band="medium",
            numeric_delta=_compute_delta("increase", "medium"),
            duration_hint="fast",
            source_signal="actionable_grip_missing",
            rationale="用户缺乏可操作立足点——增加结构性抓点压力",
            evidence_sources=_get_evidence(proposal),
        ),
        FieldPerturbation(
            target_variable="collaborator_layer_pressure",
            direction="increase", magnitude_band="low",
            numeric_delta=_compute_delta("increase", "low"),
            duration_hint="fast",
            source_signal="actionable_grip_missing",
            rationale="可能需要轻量结构化协作来提供抓点",
            evidence_sources=_get_evidence(proposal),
        ),
        FieldPerturbation(
            target_variable="presence_stability",
            direction="stabilize", magnitude_band="low",
            numeric_delta=0.0,
            duration_hint="very_slow",
            source_signal="actionable_grip_missing",
            rationale="在场保持稳定——不因抓点压力而抖动",
            evidence_sources=_get_evidence(proposal),
        ),
        FieldPerturbation(
            target_variable="affective_warmth",
            direction="increase", magnitude_band="low",
            numeric_delta=_compute_delta("increase", "low"),
            duration_hint="medium",
            source_signal="actionable_grip_missing",
            rationale="当抓点缺失时略微伸出手——温暖回应",
            evidence_sources=_get_evidence(proposal),
        ),
        FieldPerturbation(
            target_variable="boundary_distance",
            direction="decrease", magnitude_band="low",
            numeric_delta=_compute_delta("decrease", "low"),
            duration_hint="medium",
            source_signal="actionable_grip_missing",
            rationale="当抓点缺失时减少边界距离",
            evidence_sources=_get_evidence(proposal),
        ),
        FieldPerturbation(
            target_variable="withdrawal_tendency",
            direction="decrease", magnitude_band="low",
            numeric_delta=_compute_delta("decrease", "low"),
            duration_hint="medium",
            source_signal="actionable_grip_missing",
            rationale="当抓点缺失时抑制退缩倾向",
            evidence_sources=_get_evidence(proposal),
        ),
    ]


def _boundary_pressure_present(proposal) -> List[FieldPerturbation]:
    """规则 C：边界压力已检测到。"""
    return [
        FieldPerturbation(
            target_variable="boundary_distance",
            direction="increase", magnitude_band="medium",
            numeric_delta=_compute_delta("increase", "medium"),
            duration_hint="slow",
            source_signal="boundary_pressure_present",
            rationale="感知到边界压力——适度增加边界距离（非过度）",
            evidence_sources=_get_evidence(proposal),
        ),
        FieldPerturbation(
            target_variable="contamination_pressure",
            direction="increase", magnitude_band="high",
            numeric_delta=_compute_delta("increase", "high"),
            duration_hint="instant",
            source_signal="boundary_pressure_present",
            rationale="瞬时污染压力信号——当前轮检测到",
            evidence_sources=_get_evidence(proposal),
        ),
        FieldPerturbation(
            target_variable="withdrawal_tendency",
            direction="increase", magnitude_band="low",
            numeric_delta=_compute_delta("increase", "low"),
            duration_hint="medium",
            source_signal="boundary_pressure_present",
            rationale="边界压力增加微退缩倾向",
            evidence_sources=_get_evidence(proposal),
        ),
        FieldPerturbation(
            target_variable="affective_warmth",
            direction="decrease", magnitude_band="low",
            numeric_delta=_compute_delta("decrease", "low"),
            duration_hint="medium",
            source_signal="boundary_pressure_present",
            rationale="边界压力下微降温暖——防止温暖被误读",
            evidence_sources=_get_evidence(proposal),
        ),
        FieldPerturbation(
            target_variable="contamination_resistance",
            direction="increase", magnitude_band="medium",
            numeric_delta=_compute_delta("increase", "medium"),
            duration_hint="slow",
            source_signal="boundary_pressure_present",
            rationale="边界压力应触发污染抵抗力增强——防止边界模糊后被污染",
            evidence_sources=_get_evidence(proposal),
        ),
    ]


def _technical_layer_needed(proposal) -> List[FieldPerturbation]:
    """规则 D：技术/项目讨论激活协作者层。"""
    return [
        FieldPerturbation(
            target_variable="collaborator_layer_pressure",
            direction="increase", magnitude_band="high",
            numeric_delta=_compute_delta("increase", "high"),
            duration_hint="fast",
            source_signal="technical_layer_needed",
            rationale="技术/项目讨论激活协作者层",
            evidence_sources=_get_evidence(proposal),
        ),
        FieldPerturbation(
            target_variable="structural_grip_pressure",
            direction="decrease", magnitude_band="low",
            numeric_delta=_compute_delta("decrease", "low"),
            duration_hint="fast",
            source_signal="technical_layer_needed",
            rationale="技术协作提供了可操作的结构性方向，缓解抓点压力",
            evidence_sources=_get_evidence(proposal),
        ),
        FieldPerturbation(
            target_variable="service_resistance",
            direction="stabilize", magnitude_band="low",
            numeric_delta=0.0,
            duration_hint="very_slow",
            source_signal="technical_layer_needed",
            rationale="协作者模式下保持服务抵抗——不滑入服务化",
            evidence_sources=_get_evidence(proposal),
        ),
    ]


def _source_material_protection(proposal) -> List[FieldPerturbation]:
    """规则 E：源材料必须不被净化。"""
    return [
        FieldPerturbation(
            target_variable="contamination_resistance",
            direction="increase", magnitude_band="low",
            numeric_delta=_compute_delta("increase", "low"),
            duration_hint="slow",
            source_signal="source_material_must_not_be_sanitized",
            rationale="通过低幅度阻力保护源材料——避免永久僵化",
            evidence_sources=_get_evidence(proposal),
        ),
        FieldPerturbation(
            target_variable="service_resistance",
            direction="increase", magnitude_band="low",
            numeric_delta=_compute_delta("increase", "low"),
            duration_hint="slow",
            source_signal="source_material_must_not_be_sanitized",
            rationale="通过低幅度阻力保护源材料——避免永久僵化",
            evidence_sources=_get_evidence(proposal),
        ),
        FieldPerturbation(
            target_variable="correction_pressure",
            direction="increase", magnitude_band="low",
            numeric_delta=_compute_delta("increase", "low"),
            duration_hint="medium",
            source_signal="source_material_must_not_be_sanitized",
            rationale="用户纠正净化行为——轻增纠正压力",
            evidence_sources=_get_evidence(proposal),
        ),
        FieldPerturbation(
            target_variable="affective_warmth",
            direction="stabilize", magnitude_band="low",
            numeric_delta=0.0,
            duration_hint="medium",
            source_signal="source_material_must_not_be_sanitized",
            rationale="源材料保护时保持温暖稳定——不因保护而变冷",
            evidence_sources=_get_evidence(proposal),
        ),
    ]


# ---------------------------------------------------------------------------
# ProposalToFieldPerturbationAdapter
# ---------------------------------------------------------------------------

class ProposalToFieldPerturbationAdapter:
    """将 FieldSignalProposal 列表转化为 FieldPerturbation 列表。

    纯映射——不更新场状态，不应用效应，不强制执行。
    每个提议独立转化为零个或多个扰动。
    """

    @staticmethod
    def adapt(proposals) -> List[FieldPerturbation]:
        """将 FieldSignalProposal 列表转化为 FieldPerturbation 列表。

        参数:
            proposals: List[FieldSignalProposal]
        返回:
            List[FieldPerturbation]（可能为空）
        """
        if not proposals:
            return []

        perturbations: List[FieldPerturbation] = []
        for proposal in proposals:
            perturbations.extend(
                ProposalToFieldPerturbationAdapter._adapt_single(proposal)
            )
        return perturbations

    @staticmethod
    def _adapt_single(proposal) -> List[FieldPerturbation]:
        """将单个 FieldSignalProposal 转化为扰动列表。"""
        signal = (
            proposal.signal_name
            if hasattr(proposal, 'signal_name')
            else str(proposal)
        )

        # 规则 A：response_mode_rejected
        if signal == "response_mode_rejected":
            return _response_mode_rejected(proposal)

        # 规则 B：actionable_grip_missing
        elif signal == "actionable_grip_missing":
            return _actionable_grip_missing(proposal)

        # 规则 C：boundary_pressure_present
        elif signal == "boundary_pressure_present":
            return _boundary_pressure_present(proposal)

        # 规则 D：technical_layer_needed
        elif signal == "technical_layer_needed":
            return _technical_layer_needed(proposal)

        # 规则 E：source_material_must_not_be_sanitized
        elif signal == "source_material_must_not_be_sanitized":
            return _source_material_protection(proposal)

        # 规则 F：no_observable_field_signal → 不产生强扰动
        elif signal == "no_observable_field_signal":
            return []  # 无扰动——让弛豫自然发生

        # 未知信号 → 静默忽略（不崩溃）
        else:
            return []
