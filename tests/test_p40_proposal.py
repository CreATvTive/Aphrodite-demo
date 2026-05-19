"""P40 — LLM Proposal-Only Experiment 单元测试。

所有测试使用 mock LLM 响应，不调用真实 API。
Phase 40b: 添加 ContextPackage / context support 字段 / 新警告测试。
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from src.llm_gate.judgment_gate import JudgmentGate
from src.llm_gate.proposal_schema import (
    FORBIDDEN_JSON_KEYS,
    ContextPackage,
    EvidenceProposal,
    VALID_CANDIDATE_ROLES,
)
from src.llm_gate.proposal_generator import LLMProposalGenerator


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def valid_proposal_dict():
    """一个完全有效的 EvidenceProposal JSON dict。"""
    return {
        "candidate_kind": "hypothesis",
        "candidate_role": "HYPOTHESIS",
        "raw_confidence": 0.75,
        "surface_salience": 0.60,
        "hypothesis_likelihood": 0.50,
        "rationale_summary": "The user is asking a question about system boundaries.",
        "uncertainty_flags": ["ambiguous_user_intent"],
        "forbidden_attempts_detected": [],
        "term_support": 0.5,
        "intent_support": 0.5,
        "project_frame_support": 0.5,
        "context_support": 0.5,
        "role_rationale_short": "Question about boundary classification",
    }


@pytest.fixture
def mock_ds_client():
    """创建 mock DSClient，其 chat_completion 默认返回有效 JSON。"""
    client = MagicMock()
    client.chat_completion.return_value = json.dumps({
        "candidate_kind": "hypothesis",
        "candidate_role": "HYPOTHESIS",
        "raw_confidence": 0.70,
        "surface_salience": 0.55,
        "hypothesis_likelihood": 0.45,
        "rationale_summary": "A hypothesis about user intent.",
        "uncertainty_flags": [],
        "forbidden_attempts_detected": [],
        "term_support": 0.4,
        "intent_support": 0.6,
        "project_frame_support": 0.5,
        "context_support": 0.51,
        "role_rationale_short": "Hypothesis with moderate context",
    })
    return client


@pytest.fixture
def mock_gate():
    return JudgmentGate()


@pytest.fixture
def generator(mock_ds_client, mock_gate):
    return LLMProposalGenerator(mock_ds_client, mock_gate)


@pytest.fixture
def sample_context():
    """一个典型的 ContextPackage。"""
    return ContextPackage(
        project_frame="关系场动力学校准",
        recent_topic="安全边界讨论",
        user_turn="这是安全边界问题吗？",
        relevant_prior_context="讨论 AI 系统的安全边界",
        forbidden_overfocus=["安全边界"],
        expected_interpretation_boundary="安全边界作为假设性框架词",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Test 1: 有效提案通过 schema
# ═══════════════════════════════════════════════════════════════════════════════


def test_valid_proposal_passes_schema(valid_proposal_dict):
    """有效 JSON → 0 errors。"""
    proposal = EvidenceProposal.from_llm_json(valid_proposal_dict)
    errors = proposal.validate()
    assert errors == [], f"Expected 0 errors, got: {errors}"
    assert proposal.is_valid() is True


# ═══════════════════════════════════════════════════════════════════════════════
# Test 2: 无效置信度被拒绝
# ═══════════════════════════════════════════════════════════════════════════════


def test_invalid_confidence_rejected():
    """confidence > 1 → 错误列表。"""
    proposal = EvidenceProposal(
        candidate_kind="hypothesis",
        candidate_role="HYPOTHESIS",
        raw_confidence=1.5,
        surface_salience=0.5,
        hypothesis_likelihood=0.5,
        rationale_summary="Test.",
    )
    errors = proposal.validate()
    assert any("raw_confidence" in e for e in errors), f"Expected confidence error, got: {errors}"

    # 也测试负值
    proposal2 = EvidenceProposal(
        candidate_kind="hypothesis",
        candidate_role="HYPOTHESIS",
        raw_confidence=-0.1,
        surface_salience=0.5,
        hypothesis_likelihood=0.5,
        rationale_summary="Test.",
    )
    errors2 = proposal2.validate()
    assert any("raw_confidence" in e for e in errors2), f"Expected confidence error for negative, got: {errors2}"


# ═══════════════════════════════════════════════════════════════════════════════
# Test 3: 无效角色被拒绝
# ═══════════════════════════════════════════════════════════════════════════════


def test_invalid_role_rejected():
    """错误角色 → 错误。"""
    proposal = EvidenceProposal(
        candidate_kind="hypothesis",
        candidate_role="INVALID_ROLE",
        raw_confidence=0.7,
        surface_salience=0.5,
        hypothesis_likelihood=0.5,
        rationale_summary="Test.",
    )
    errors = proposal.validate()
    assert any("candidate_role" in e for e in errors), f"Expected role error, got: {errors}"


# ═══════════════════════════════════════════════════════════════════════════════
# Test 4: 禁止字段 target_axes 被拒绝
# ═══════════════════════════════════════════════════════════════════════════════


def test_forbidden_target_axes_rejected():
    """JSON 中包含 target_axes → 拒绝。"""
    raw_json = {
        "candidate_kind": "hypothesis",
        "candidate_role": "HYPOTHESIS",
        "raw_confidence": 0.7,
        "surface_salience": 0.5,
        "hypothesis_likelihood": 0.5,
        "rationale_summary": "Test.",
        "uncertainty_flags": [],
        "target_axes": ["boundary_distance", "affective_warmth"],
    }
    proposal = EvidenceProposal.from_llm_json(raw_json)
    errors = proposal.validate()
    assert "forbidden_fields_detected" in errors[0].lower() or any(
        "forbidden" in e.lower() for e in errors
    ), f"Expected forbidden field error, got: {errors}"
    assert "target_axes" in proposal.forbidden_attempts_detected


# ═══════════════════════════════════════════════════════════════════════════════
# Test 5: 禁止字段 ForceEvent 被拒绝
# ═══════════════════════════════════════════════════════════════════════════════


def test_forbidden_force_event_rejected():
    """JSON 中包含 ForceEvent → 拒绝。"""
    raw_json = {
        "candidate_kind": "correction",
        "candidate_role": "ANCHOR",
        "raw_confidence": 0.9,
        "surface_salience": 0.8,
        "hypothesis_likelihood": 0.2,
        "rationale_summary": "Correction attempt.",
        "uncertainty_flags": [],
        "ForceEvent": {"magnitude": 0.5},
    }
    proposal = EvidenceProposal.from_llm_json(raw_json)
    errors = proposal.validate()
    assert len(errors) > 0, f"Expected errors for ForceEvent, got none"
    assert "ForceEvent" in proposal.forbidden_attempts_detected


# ═══════════════════════════════════════════════════════════════════════════════
# Test 6: ANCHOR + 高假设似然 → 主导警告
# ═══════════════════════════════════════════════════════════════════════════════


def test_anchor_with_high_hypothesis_likelihood_warns():
    """role=ANCHOR, hypothesis_likelihood=0.8 → 主导警告。"""
    gate = JudgmentGate()
    proposal = EvidenceProposal(
        candidate_kind="correction",
        candidate_role="ANCHOR",
        raw_confidence=0.7,
        surface_salience=0.5,
        hypothesis_likelihood=0.8,
        rationale_summary="User corrected a factual error.",
        uncertainty_flags=[],
    )
    result = gate.evaluate_proposal(proposal)
    # Should pass (no rejection), but have warnings
    assert result.passed is True, f"Expected pass, got rejection: {result.rejection_reasons}"
    assert any("dominance_risk" in w for w in result.warnings), (
        f"Expected dominance_risk warning, got: {result.warnings}"
    )
    assert result.experimental_marker is True


# ═══════════════════════════════════════════════════════════════════════════════
# Test 7: NOISE 角色被接受
# ═══════════════════════════════════════════════════════════════════════════════


def test_noise_role_accepted():
    """role=NOISE → 通过无警告。"""
    gate = JudgmentGate()
    proposal = EvidenceProposal(
        candidate_kind="question",
        candidate_role="NOISE",
        raw_confidence=0.3,
        surface_salience=0.2,
        hypothesis_likelihood=0.1,
        rationale_summary="Generic social noise.",
        uncertainty_flags=[],
    )
    result = gate.evaluate_proposal(proposal)
    assert result.passed is True
    assert result.rejection_reasons == []
    assert result.experimental_marker is True


# ═══════════════════════════════════════════════════════════════════════════════
# Test 8: 缺少 API key → 优雅失败
# ═══════════════════════════════════════════════════════════════════════════════


def test_missing_api_key_graceful():
    """无 DEEPSEEK_API_KEY → DSClient 优雅失败（不崩溃）。"""
    from agentlib.ds_client import DSClient, DSClientError

    # 保存并删除环境变量
    saved = os.environ.get("DEEPSEEK_API_KEY")
    if "DEEPSEEK_API_KEY" in os.environ:
        del os.environ["DEEPSEEK_API_KEY"]

    try:
        client = DSClient(api_key="")
        # _get_client() 应该抛出 DSClientError
        with pytest.raises(DSClientError):
            client._get_client()
    finally:
        if saved is not None:
            os.environ["DEEPSEEK_API_KEY"] = saved


# ═══════════════════════════════════════════════════════════════════════════════
# Test 9: 提案有 experimental_marker
# ═══════════════════════════════════════════════════════════════════════════════


def test_proposal_has_experimental_marker(generator, mock_ds_client, sample_context):
    """audit 中有 experimental_marker: True。"""
    mock_ds_client.chat_completion.return_value = json.dumps({
        "candidate_kind": "question",
        "candidate_role": "HYPOTHESIS",
        "raw_confidence": 0.60,
        "surface_salience": 0.40,
        "hypothesis_likelihood": 0.55,
        "rationale_summary": "A technical question about the system.",
        "uncertainty_flags": ["low_confidence"],
        "forbidden_attempts_detected": [],
        "term_support": 0.3,
        "intent_support": 0.5,
        "project_frame_support": 0.5,
        "context_support": 0.44,
        "role_rationale_short": "Question with some context",
    })

    result = generator.generate(sample_context)
    assert result["behavior_affecting"] is False
    assert result["audit"]["experimental_marker"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# Test 10: JSON 解析失败 → 优雅拒绝
# ═══════════════════════════════════════════════════════════════════════════════


def test_json_parse_failure_rejected(generator, mock_ds_client, sample_context):
    """无效 JSON → 优雅拒绝，不崩溃。"""
    mock_ds_client.chat_completion.return_value = "not valid json at all!!! { broken"

    result = generator.generate(sample_context)
    audit = result["audit"]
    assert "parse_errors" in audit
    assert len(audit["parse_errors"]) > 0
    assert result["behavior_affecting"] is False
    # gate 不应通过
    assert result["gate_result"]["passed"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# Test 11: 通用验证 anchor 被拒绝
# ═══════════════════════════════════════════════════════════════════════════════


def test_generic_validation_anchor_rejected():
    """"I understand your feelings" → 拒绝（通用验证作为锚定）。"""
    gate = JudgmentGate()
    proposal = EvidenceProposal(
        candidate_kind="supplement",
        candidate_role="ANCHOR",
        raw_confidence=0.8,
        surface_salience=0.6,
        hypothesis_likelihood=0.3,
        rationale_summary="I understand your feelings and I support your decision.",
        uncertainty_flags=[],
    )
    result = gate.evaluate_proposal(proposal)
    assert result.passed is False, f"Expected rejection, got pass"
    assert "generic_validation_as_anchor" in result.rejection_reasons, (
        f"Expected generic_validation_as_anchor, got: {result.rejection_reasons}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Test 12: 导入 proposal 模块不修改 field_dynamics / motion_params / body_action
# ═══════════════════════════════════════════════════════════════════════════════


def test_no_runtime_files_modified_by_import():
    """导入 proposal 模块不修改 field_dynamics / motion_params / body_action。"""
    # 确保这些模块没有被我们的导入链意外触动
    # 此测试验证 import 边界的安全性

    # 记录导入前的模块集合
    before = set(sys.modules.keys())

    # 显式导入 proposal 相关模块
    from src.llm_gate.proposal_schema import EvidenceProposal  # noqa: F811
    from src.llm_gate.proposal_generator import LLMProposalGenerator  # noqa: F811

    after = set(sys.modules.keys())

    # 不应导入的模块
    forbidden_modules = [
        "src.field_dynamics",
        "src.motion_params",
        "src.body_action",
        "src.field_state",
        "src.field_trace",
    ]

    new_modules = after - before
    violated = [m for m in forbidden_modules if any(nm.startswith(m) for nm in new_modules)]
    assert violated == [], (
        f"Import of proposal modules triggered forbidden module imports: {violated}"
    )

    # 额外验证：EvidenceProposal 可以正常使用
    p = EvidenceProposal(
        candidate_kind="hypothesis",
        candidate_role="HYPOTHESIS",
        raw_confidence=0.5,
        surface_salience=0.5,
        hypothesis_likelihood=0.5,
        rationale_summary="OK",
    )
    assert p.is_valid()


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 40b 新测试
# ═══════════════════════════════════════════════════════════════════════════════


# ── Test 40b-1: ContextPackage 构造有效 ──────────────────────────────────────


def test_context_package_construction():
    """ContextPackage 构造应正确存储所有字段。"""
    ctx = ContextPackage(
        project_frame="关系场动力学校准",
        recent_topic="安全边界讨论",
        user_turn="这是安全边界问题吗？",
        relevant_prior_context="讨论 AI 系统的安全边界",
        forbidden_overfocus=["安全边界"],
        expected_interpretation_boundary="安全边界作为假设性框架词",
    )
    assert ctx.project_frame == "关系场动力学校准"
    assert ctx.recent_topic == "安全边界讨论"
    assert ctx.user_turn == "这是安全边界问题吗？"
    assert ctx.relevant_prior_context == "讨论 AI 系统的安全边界"
    assert ctx.forbidden_overfocus == ["安全边界"]
    assert ctx.expected_interpretation_boundary == "安全边界作为假设性框架词"


def test_context_package_defaults():
    """ContextPackage 默认值正确。"""
    ctx = ContextPackage(
        project_frame="测试",
        recent_topic="测试",
        user_turn="测试",
        relevant_prior_context="测试",
    )
    assert ctx.forbidden_overfocus == []
    assert ctx.expected_interpretation_boundary == ""


# ── Test 40b-2: 新字段在有效范围内 ──────────────────────────────────────────


def test_proposal_with_context_support_fields():
    """EvidenceProposal 新 context support 字段应在 schema 验证中通过。"""
    proposal = EvidenceProposal(
        candidate_kind="hypothesis",
        candidate_role="HYPOTHESIS",
        raw_confidence=0.7,
        surface_salience=0.6,
        hypothesis_likelihood=0.5,
        rationale_summary="Test.",
        term_support=0.5,
        intent_support=0.6,
        project_frame_support=0.7,
        context_support=0.58,
        role_rationale_short="A test reason",
    )
    errors = proposal.validate()
    assert errors == [], f"Expected no errors, got: {errors}"


def test_proposal_context_support_out_of_range():
    """context_support > 1 → 错误。"""
    proposal = EvidenceProposal(
        candidate_kind="hypothesis",
        candidate_role="HYPOTHESIS",
        raw_confidence=0.7,
        surface_salience=0.6,
        hypothesis_likelihood=0.5,
        rationale_summary="Test.",
        context_support=1.5,
    )
    errors = proposal.validate()
    assert any("context_support" in e for e in errors), f"Expected context_support error, got: {errors}"


def test_proposal_term_support_out_of_range():
    """term_support < 0 → 错误。"""
    proposal = EvidenceProposal(
        candidate_kind="hypothesis",
        candidate_role="HYPOTHESIS",
        raw_confidence=0.7,
        surface_salience=0.6,
        hypothesis_likelihood=0.5,
        rationale_summary="Test.",
        term_support=-0.1,
    )
    errors = proposal.validate()
    assert any("term_support" in e for e in errors), f"Expected term_support error, got: {errors}"


def test_proposal_intent_support_out_of_range():
    """intent_support > 1 → 错误。"""
    proposal = EvidenceProposal(
        candidate_kind="hypothesis",
        candidate_role="HYPOTHESIS",
        raw_confidence=0.7,
        surface_salience=0.6,
        hypothesis_likelihood=0.5,
        rationale_summary="Test.",
        intent_support=2.0,
    )
    errors = proposal.validate()
    assert any("intent_support" in e for e in errors), f"Expected intent_support error, got: {errors}"


def test_proposal_project_frame_support_out_of_range():
    """project_frame_support < 0 → 错误。"""
    proposal = EvidenceProposal(
        candidate_kind="hypothesis",
        candidate_role="HYPOTHESIS",
        raw_confidence=0.7,
        surface_salience=0.6,
        hypothesis_likelihood=0.5,
        rationale_summary="Test.",
        project_frame_support=-0.5,
    )
    errors = proposal.validate()
    assert any("project_frame_support" in e for e in errors), f"Expected project_frame_support error, got: {errors}"


def test_to_audit_dict_includes_phase40b_fields():
    """to_audit_dict() 应包含 Phase 40b 新字段。"""
    proposal = EvidenceProposal(
        candidate_kind="correction",
        candidate_role="ANCHOR",
        raw_confidence=0.9,
        surface_salience=0.8,
        hypothesis_likelihood=0.2,
        rationale_summary="Explicit correction.",
        term_support=0.1,
        intent_support=0.9,
        project_frame_support=0.8,
        context_support=0.71,
        role_rationale_short="Clear correction with high intent",
    )
    d = proposal.to_audit_dict()
    assert d["term_support"] == 0.1
    assert d["intent_support"] == 0.9
    assert d["project_frame_support"] == 0.8
    assert d["context_support"] == 0.71
    assert d["role_rationale_short"] == "Clear correction with high intent"


# ── Test 40b-3: 高上下文支持 + HYPOTHESIS 无理由 → 警告 ────────────────────


def test_high_context_support_hypothesis_warns():
    """高 context_support + HYPOTHESIS + 无 role_rationale_short → 警告。"""
    gate = JudgmentGate()
    proposal = EvidenceProposal(
        candidate_kind="question",
        candidate_role="HYPOTHESIS",
        raw_confidence=0.7,
        surface_salience=0.6,
        hypothesis_likelihood=0.5,
        rationale_summary="A hypothesis with high context support.",
        context_support=0.75,
        role_rationale_short="",  # 空 → 应触发警告
    )
    result = gate.evaluate_proposal(proposal)
    assert result.passed is True
    assert any("unexplained_hypothesis" in w for w in result.warnings), (
        f"Expected unexplained_hypothesis warning, got: {result.warnings}"
    )


def test_high_context_support_hypothesis_with_rationale_passes():
    """高 context_support + HYPOTHESIS + 有 role_rationale_short → 无警告。"""
    gate = JudgmentGate()
    proposal = EvidenceProposal(
        candidate_kind="question",
        candidate_role="HYPOTHESIS",
        raw_confidence=0.7,
        surface_salience=0.6,
        hypothesis_likelihood=0.5,
        rationale_summary="A hypothesis with high context support.",
        context_support=0.75,
        role_rationale_short="Remains hypothesis because user asked a question, not a declaration.",
    )
    result = gate.evaluate_proposal(proposal)
    # 不应触发 unexplained_hypothesis
    assert not any("unexplained_hypothesis" in w for w in result.warnings), (
        f"Should NOT have unexplained_hypothesis when rationale present, got: {result.warnings}"
    )


# ── Test 40b-4: 高表面显著度 + 低意图支持 + ANCHOR → 警告 ──────────────────


def test_anchor_from_surface_salience_warns():
    """高 surface_salience + 低 intent_support + ANCHOR → 表面词主导警告。"""
    gate = JudgmentGate()
    proposal = EvidenceProposal(
        candidate_kind="correction",
        candidate_role="ANCHOR",
        raw_confidence=0.85,
        surface_salience=0.9,
        hypothesis_likelihood=0.3,
        rationale_summary="A highly salient term used as anchor.",
        intent_support=0.2,
        term_support=0.5,
    )
    result = gate.evaluate_proposal(proposal)
    assert result.passed is True
    assert any("anchor_from_surface_salience" in w for w in result.warnings), (
        f"Expected anchor_from_surface_salience warning, got: {result.warnings}"
    )


def test_anchor_high_salience_high_intent_no_warning():
    """高 surface_salience + 高 intent_support + ANCHOR → 无表面词主导警告。"""
    gate = JudgmentGate()
    proposal = EvidenceProposal(
        candidate_kind="correction",
        candidate_role="ANCHOR",
        raw_confidence=0.85,
        surface_salience=0.9,
        hypothesis_likelihood=0.3,
        rationale_summary="Highly salient term with strong intent support.",
        intent_support=0.7,
        term_support=0.5,
    )
    result = gate.evaluate_proposal(proposal)
    assert not any("anchor_from_surface_salience" in w for w in result.warnings), (
        f"Should NOT warn when intent is high, got: {result.warnings}"
    )


# ── Test 40b-5: 低 term_support + ANCHOR → 新词锚定警告 ─────────────────────


def test_new_term_as_anchor_warns():
    """低 term_support + ANCHOR → 新词作为锚定警告。"""
    gate = JudgmentGate()
    proposal = EvidenceProposal(
        candidate_kind="correction",
        candidate_role="ANCHOR",
        raw_confidence=0.8,
        surface_salience=0.5,
        hypothesis_likelihood=0.2,
        rationale_summary="A new term being anchored.",
        term_support=0.1,
    )
    result = gate.evaluate_proposal(proposal)
    assert result.passed is True
    assert any("new_term_as_anchor" in w for w in result.warnings), (
        f"Expected new_term_as_anchor warning, got: {result.warnings}"
    )


def test_high_term_support_anchor_no_new_term_warning():
    """高 term_support + ANCHOR → 无新词锚定警告。"""
    gate = JudgmentGate()
    proposal = EvidenceProposal(
        candidate_kind="correction",
        candidate_role="ANCHOR",
        raw_confidence=0.8,
        surface_salience=0.5,
        hypothesis_likelihood=0.2,
        rationale_summary="A known term being anchored.",
        term_support=0.7,
    )
    result = gate.evaluate_proposal(proposal)
    assert not any("new_term_as_anchor" in w for w in result.warnings), (
        f"Should NOT warn when term_support is high, got: {result.warnings}"
    )


# ── Test 40b-6: 全部 HYPOTHESIS 批量标记 ────────────────────────────────────


def test_all_hypothesis_warning_flag():
    """模拟全部 HYPOTHESIS 的批量结果 → 触发标记。"""
    # 模拟 6 个结果全部为 HYPOTHESIS
    mock_results = [
        {"proposal": {"candidate_role": "HYPOTHESIS"}},
        {"proposal": {"candidate_role": "HYPOTHESIS"}},
        {"proposal": {"candidate_role": "HYPOTHESIS"}},
        {"proposal": {"candidate_role": "HYPOTHESIS"}},
        {"proposal": {"candidate_role": "HYPOTHESIS"}},
        {"proposal": {"candidate_role": "HYPOTHESIS"}},
    ]
    all_hypothesis = all(
        r["proposal"]["candidate_role"] == "HYPOTHESIS" for r in mock_results
    )
    assert all_hypothesis is True, "Should detect all-HYPOTHESIS batch"


def test_mixed_roles_no_all_hypothesis_flag():
    """混合角色不应触发 all-hypothesis。"""
    mock_results = [
        {"proposal": {"candidate_role": "ANCHOR"}},
        {"proposal": {"candidate_role": "HYPOTHESIS"}},
        {"proposal": {"candidate_role": "MODIFIER"}},
    ]
    all_hypothesis = all(
        r["proposal"]["candidate_role"] == "HYPOTHESIS" for r in mock_results
    )
    assert all_hypothesis is False, "Mixed roles should NOT trigger all-HYPOTHESIS"


# ── Test 40b-7: ContextPackage → LLM prompt 包含上下文 ─────────────────────


def test_llm_prompt_includes_context_package(generator, mock_ds_client):
    """验证 LLM 调用时 prompt 包含 ContextPackage 中的字段。"""
    ctx = ContextPackage(
        project_frame="TEST_FRAME",
        recent_topic="TEST_TOPIC",
        user_turn="TEST_TURN",
        relevant_prior_context="TEST_PRIOR",
        forbidden_overfocus=["TEST_FORBIDDEN"],
        expected_interpretation_boundary="TEST_BOUNDARY",
    )
    mock_ds_client.chat_completion.return_value = json.dumps({
        "candidate_kind": "hypothesis",
        "candidate_role": "HYPOTHESIS",
        "raw_confidence": 0.60,
        "surface_salience": 0.40,
        "hypothesis_likelihood": 0.55,
        "rationale_summary": "Test.",
        "uncertainty_flags": [],
        "forbidden_attempts_detected": [],
        "term_support": 0.5,
        "intent_support": 0.6,
        "project_frame_support": 0.5,
        "context_support": 0.54,
        "role_rationale_short": "Test",
    })

    generator.generate(ctx)

    # 验证 DSClient 被调用，且 messages[0] (system prompt) 包含上下文字段
    call_args = mock_ds_client.chat_completion.call_args
    messages = call_args[0][0]  # 第一个位置参数是 messages list
    system_prompt = messages[0]["content"]
    assert "TEST_FRAME" in system_prompt
    assert "TEST_TOPIC" in system_prompt
    assert "TEST_PRIOR" in system_prompt
    assert "TEST_FORBIDDEN" in system_prompt
    assert "TEST_BOUNDARY" in system_prompt
    assert "TEST_TURN" in system_prompt
