"""Judgment Gate — 确定性规则门控，用于 P40 LLM 实验输出。

在 DeepSeek 生成的候选片段进入任何输出通道之前，
必须通过此 Gate。Gate 不消费场数据，不写入场状态。
"""

from src.llm_gate.schema import GateResult
from src.llm_gate.judgment_gate import JudgmentGate, REJECTION_REASONS
from src.llm_gate.proposal_schema import (
    EvidenceProposal,
    VALID_CANDIDATE_KINDS,
    VALID_CANDIDATE_ROLES,
    FORBIDDEN_JSON_KEYS,
)
from src.llm_gate.proposal_generator import LLMProposalGenerator

__all__ = [
    "JudgmentGate",
    "GateResult",
    "REJECTION_REASONS",
    "EvidenceProposal",
    "VALID_CANDIDATE_KINDS",
    "VALID_CANDIDATE_ROLES",
    "FORBIDDEN_JSON_KEYS",
    "LLMProposalGenerator",
]
