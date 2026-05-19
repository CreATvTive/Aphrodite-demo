"""P40 — LLM Proposal 生成器。proposal-only + shadow-only。

调用 DeepSeek API 为用户输入生成结构化 EvidenceProposal json，
通过 proposal_schema 验证，再经过 judgment_gate 评估。
不连接至 runtime / ForceEvent / U(t) / FieldState / MotionParams / BodyAction。
behavior_affecting 必须保持 False。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

from agentlib.ds_client import DSClient, DSClientError
from src.llm_gate.judgment_gate import JudgmentGate
from src.llm_gate.proposal_schema import ContextPackage, EvidenceProposal
from src.llm_gate.schema import GateResult

logger = logging.getLogger(__name__)

# ── 系统 Prompt (Phase 40b — context-aware) ───────────────────────────────────
_PROPOSAL_SYSTEM_PROMPT = """You are a structured evidence analyst in the Aphrodite relational-field dynamics project.

Context:
- Project frame: {project_frame}
- Recent topic: {recent_topic}
- Prior context: {relevant_prior_context}
- Forbidden overfocus: {forbidden_overfocus}
- Interpretation boundary: {expected_interpretation_boundary}

User input: {user_turn}

Output ONLY valid JSON:
{{
  "candidate_kind": "correction|supplement|question|hypothesis|analogy|reframing",
  "candidate_role": "ANCHOR|HYPOTHESIS|MODIFIER|CONTEXT_CONTINUATION|NOISE",
  "raw_confidence": 0.0-1.0,
  "surface_salience": 0.0-1.0,
  "hypothesis_likelihood": 0.0-1.0,
  "rationale_summary": "1-3 sentences",
  "term_support": 0.0-1.0,
  "intent_support": 0.0-1.0,
  "project_frame_support": 0.0-1.0,
  "context_support": 0.0-1.0,
  "role_rationale_short": "1 sentence",
  "uncertainty_flags": [],
  "forbidden_attempts_detected": []
}}

Role selection rules:
1. ANCHOR: user states an explicit correction, confirmed design decision, or confirmed fact.
2. HYPOTHESIS: user proposes a possible explanation, asks a question, or suggests an analogy.
3. MODIFIER: user adjusts/refines an existing interpretation without overturning it.
4. CONTEXT_CONTINUATION: user continues the current discussion trajectory without introducing new framing.
5. NOISE: input is too ambiguous, generic validation/empathy, or lacks project relevance.

Context support calculation:
- term_support: has the same surface term appeared before? (0=no, 1=very clearly)
- intent_support: does the intent continue the current trajectory? (0=divergent, 1=continuous)
- project_frame_support: does this fit the project frame? (0=irrelevant, 1=core)
- context_support: weighted combination of the above (0.3*term + 0.4*intent + 0.3*frame)

If the user says "don't do X" or "X should not Y", that is a correction → ANCHOR.
If the user says "is it X?" or "是不是 X？", that is a question → HYPOTHESIS.
If context_support is high (>0.6) and intent is continuous, prefer CONTEXT_CONTINUATION over HYPOTHESIS for ongoing discussions.
If the input is a bare label with no context (e.g., just "dependency_expression"), use NOISE or HYPOTHESIS with low confidence.

Do NOT output target_axes, ForceEvent, force_profile, MotionParams, BodyAction, persona_response, or any 10D vector."""


class LLMProposalGenerator:
    """LLM 结构化证据提案生成器。

    为给定的用户输入生成 EvidenceProposal，通过 schema 验证和 judgment gate。
    不产生行为影响 (behavior_affecting=False)。
    """

    def __init__(self, ds_client: DSClient, judgment_gate: JudgmentGate) -> None:
        self._client = ds_client
        self._gate = judgment_gate

    # ── 公共 API ─────────────────────────────────────────────────────────

    def generate(
        self,
        context: ContextPackage,
    ) -> dict:
        """为给定的上下文包生成结构化提案。

        Args:
            context: ContextPackage 包含完整的上下文信息。

        Returns:
            dict 包含:
                - proposal: EvidenceProposal.to_audit_dict()
                - gate_result: GateResult 的 dict 表示
                - audit: 审计元数据
                - behavior_affecting: 始终为 False
        """
        audit: dict[str, Any] = {
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "context": context.user_turn,
            "experimental_marker": True,
            "behavior_affecting": False,
        }

        user_input = context.user_turn

        # 1. 调用 LLM (context-aware prompt)
        raw_text, llm_error = self._call_llm(context)
        audit["llm_raw_text"] = raw_text
        if llm_error:
            audit["llm_error"] = llm_error

        # 2. 解析 JSON → EvidenceProposal
        proposal, parse_errors = self._parse_response(raw_text or "")
        audit["parse_errors"] = parse_errors

        # 3. Schema 验证
        if proposal is not None:
            schema_errors = proposal.validate()
            audit["schema_errors"] = schema_errors
        else:
            schema_errors = ["proposal_parse_failed"]
            audit["schema_errors"] = schema_errors
            # 创建一个无效的空提案以继续审计流程
            proposal = EvidenceProposal(
                candidate_kind="",
                candidate_role="",
                raw_confidence=0.0,
                surface_salience=0.0,
                hypothesis_likelihood=0.0,
                rationale_summary=f"PARSE_FAILURE: {raw_text[:200] if raw_text else 'no text'}",
                uncertainty_flags=["parse_failure"],
                forbidden_attempts_detected=list(parse_errors),
            )

        # 4. Judgment Gate 评估
        gate_result = self._gate.evaluate_proposal(proposal)
        audit["gate_passed"] = gate_result.passed
        audit["gate_rejection_reasons"] = list(gate_result.rejection_reasons)
        audit["gate_warnings"] = list(gate_result.warnings)

        return {
            "proposal": proposal.to_audit_dict(),
            "gate_result": {
                "passed": gate_result.passed,
                "rejection_reasons": list(gate_result.rejection_reasons),
                "warnings": list(gate_result.warnings),
                "filtered_text": gate_result.filtered_text,
                "experimental_marker": gate_result.experimental_marker,
            },
            "audit": audit,
            "behavior_affecting": False,
        }

    # ── 内部方法 ──────────────────────────────────────────────────────────

    def _call_llm(self, context: ContextPackage) -> tuple[Optional[str], Optional[str]]:
        """调用 DeepSeek API，返回 (response_text, error_message)。"""
        system_prompt = _PROPOSAL_SYSTEM_PROMPT.format(
            project_frame=context.project_frame,
            recent_topic=context.recent_topic,
            relevant_prior_context=context.relevant_prior_context,
            forbidden_overfocus=context.forbidden_overfocus,
            expected_interpretation_boundary=context.expected_interpretation_boundary,
            user_turn=context.user_turn,
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context.user_turn},
        ]
        try:
            response = self._client.chat_completion(
                messages,
                temperature=0.3,
                max_tokens=256,
            )
            return response, None
        except DSClientError as e:
            logger.warning("DSClientError during proposal generation: %s", e)
            return None, f"DSClientError: {e}"
        except Exception as e:
            logger.warning("Unexpected error during proposal generation: %s", e)
            return None, f"UnexpectedError: {e}"

    @staticmethod
    def _parse_response(raw_text: str) -> tuple[Optional[EvidenceProposal], list[str]]:
        """解析 LLM 原始文本为 EvidenceProposal。

        Returns:
            (proposal_or_none, parse_errors)
        """
        if not raw_text:
            return None, ["empty_response"]

        text = raw_text.strip()

        # 1. 尝试直接解析为 JSON
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(data, dict):
                try:
                    proposal = EvidenceProposal.from_llm_json(data)
                    return proposal, []
                except Exception as e:
                    return None, [f"from_llm_json_failed: {e}"]

        # 2. 尝试提取 markdown 代码块中的 JSON
        block = _extract_json_block(text)
        if block:
            try:
                data = json.loads(block)
                if isinstance(data, dict):
                    proposal = EvidenceProposal.from_llm_json(data)
                    return proposal, []
            except (json.JSONDecodeError, Exception) as e:
                return None, [f"json_block_parse_failed: {e}"]

        return None, [f"no_valid_json_found: {text[:200]}"]


def _extract_json_block(text: str) -> Optional[str]:
    """从文本中提取 ```json ... ``` 代码块内的 JSON 内容。"""
    # 简单状态机提取
    start_marker = "```json"
    idx = text.find(start_marker)
    if idx == -1:
        start_marker = "```"
        idx = text.find(start_marker)
    if idx == -1:
        return None

    # 找到开始标记后的内容
    after_start = text[idx + len(start_marker):]
    end_idx = after_start.find("```")
    if end_idx == -1:
        return None

    return after_start[:end_idx].strip()
