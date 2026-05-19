"""P40c — ContextualEvidenceRegulator 影子干运行测试。

10 个测试，覆盖：
- 高显著低上下文降级
- 高上下文低假设锚定保留
- 高意图低 term 不过度惩罚
- 低置信度噪声
- STRONG_CONFLICT 阻塞
- behavior_affecting 始终 False
- 无 ForceEvent Adapter 导入
- 主导公式正确性
- 调整权重边界
- 6 个 mock 案例全通过
"""

from __future__ import annotations

import math
import sys
import os

# 确保项目根目录在 sys.path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import pytest

from src.llm_gate.proposal_schema import EvidenceProposal
from src.llm_gate.regulator_dry_run import (
    ContextualEvidenceRegulatorDryRun,
    RegulatorResult,
)


# ── Fixture ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def regulator() -> ContextualEvidenceRegulatorDryRun:
    return ContextualEvidenceRegulatorDryRun()


def make_proposal(
    candidate_role: str = "ANCHOR",
    raw_confidence: float = 0.8,
    surface_salience: float = 0.5,
    hypothesis_likelihood: float = 0.2,
    term_support: float = 0.5,
    intent_support: float = 0.5,
    project_frame_support: float = 0.5,
    context_support: float = 0.5,
) -> EvidenceProposal:
    """快速构造一个 EvidenceProposal 用于测试。"""
    return EvidenceProposal(
        candidate_kind="correction",
        candidate_role=candidate_role,
        raw_confidence=raw_confidence,
        surface_salience=surface_salience,
        hypothesis_likelihood=hypothesis_likelihood,
        term_support=term_support,
        intent_support=intent_support,
        project_frame_support=project_frame_support,
        context_support=context_support,
        rationale_summary="test proposal",
        role_rationale_short="test",
    )


# ── 测试 1：高显著低上下文降级 ─────────────────────────────────────────────────


def test_high_salience_low_context_downgrade(regulator: ContextualEvidenceRegulatorDryRun) -> None:
    """salience=0.9, context=0.2, hypothesis=0.8, role=ANCHOR → 降级为 HYPOTHESIS, 低权重。"""
    proposal = make_proposal(
        candidate_role="ANCHOR",
        raw_confidence=0.75,
        surface_salience=0.9,
        hypothesis_likelihood=0.8,
        context_support=0.2,
    )
    result = regulator.evaluate(proposal)

    # 降级检查
    assert result.candidate_role == "ANCHOR"
    assert result.authorized_role == "HYPOTHESIS", (
        f"Expected HYPOTHESIS but got {result.authorized_role}"
    )
    assert "downgrade" in result.role_shift_reason.lower()

    # 主导风险应高
    # d_i = max(0, 0.9 - 0.2) * 0.8 = 0.7 * 0.8 = 0.56
    assert result.dominance_risk > 0.5
    assert result.dominance_warning is True

    # 权重应低（被大幅压制）
    assert result.adjusted_weight < 0.15, (
        f"Expected low weight but got {result.adjusted_weight}"
    )


# ── 测试 2：高上下文低假设锚定保留 ─────────────────────────────────────────────


def test_high_context_low_hypothesis_anchor_retained(
    regulator: ContextualEvidenceRegulatorDryRun,
) -> None:
    """context=0.8, hypothesis=0.1, role=ANCHOR → ANCHOR 保留, 高权重。"""
    proposal = make_proposal(
        candidate_role="ANCHOR",
        raw_confidence=0.95,
        surface_salience=0.7,
        hypothesis_likelihood=0.1,
        context_support=0.8,
    )
    result = regulator.evaluate(proposal)

    assert result.candidate_role == "ANCHOR"
    assert result.authorized_role == "ANCHOR"

    # d_i = max(0, 0.7 - 0.8) * 0.1 = 0.0 * 0.1 = 0.0
    assert result.dominance_risk == pytest.approx(0.0, abs=1e-6)
    assert result.dominance_warning is False

    # 权重应高
    assert result.adjusted_weight > 0.6, (
        f"Expected high weight but got {result.adjusted_weight}"
    )
    assert result.blocked is False


# ── 测试 3：高意图低 term 不过度惩罚 ─────────────────────────────────────────────


def test_high_intent_low_term_not_over_penalized(
    regulator: ContextualEvidenceRegulatorDryRun,
) -> None:
    """intent=0.8, term=0.1, context=0.6 → 不被过度惩罚。

    设计规范 §D.5 情况 1：新词但强意图连续性不应因低 term_support 而降级。
    """
    proposal = make_proposal(
        candidate_role="ANCHOR",
        raw_confidence=0.85,
        surface_salience=0.75,
        hypothesis_likelihood=0.15,
        term_support=0.1,
        intent_support=0.8,
        project_frame_support=0.7,
        context_support=0.6,
    )
    result = regulator.evaluate(proposal)

    # ANCHOR 应保留（context_support=0.6 满足 q_i >= 0.6 的锚定条件）
    assert result.authorized_role == "ANCHOR", (
        f"Expected ANCHOR retained but got {result.authorized_role}: {result.role_shift_reason}"
    )

    # 权重不应被过度压制
    assert result.adjusted_weight > 0.3, (
        f"Expected reasonable weight but got {result.adjusted_weight}"
    )


# ── 测试 4：低置信度噪声 ───────────────────────────────────────────────────────


def test_low_confidence_noise(regulator: ContextualEvidenceRegulatorDryRun) -> None:
    """confidence=0.05 → NOISE。"""
    proposal = make_proposal(
        candidate_role="HYPOTHESIS",
        raw_confidence=0.05,
        surface_salience=0.3,
        hypothesis_likelihood=0.3,
        context_support=0.3,
    )
    result = regulator.evaluate(proposal)

    assert result.authorized_role == "NOISE", (
        f"Expected NOISE but got {result.authorized_role}"
    )
    assert "confidence too low" in result.role_shift_reason.lower()


# ── 测试 5：STRONG_CONFLICT 阻塞 ───────────────────────────────────────────────


def test_strong_conflict_blocked(regulator: ContextualEvidenceRegulatorDryRun) -> None:
    """field_compatibility=0 → blocked, weight=0, role=NOISE。"""
    proposal = make_proposal(
        candidate_role="ANCHOR",
        raw_confidence=0.85,
        surface_salience=0.5,
        hypothesis_likelihood=0.2,
        context_support=0.7,
    )
    result = regulator.evaluate(proposal, field_compatibility=0.0)

    assert result.blocked is True
    assert result.adjusted_weight == 0.0
    assert result.authorized_role == "NOISE"
    assert result.field_compatibility == 0.0


# ── 测试 6：behavior_affecting 始终 False ───────────────────────────────────────


def test_behavior_affecting_always_false(
    regulator: ContextualEvidenceRegulatorDryRun,
) -> None:
    """所有产出 .behavior_affecting == False。"""
    proposals = [
        make_proposal(
            candidate_role="ANCHOR",
            raw_confidence=0.8,
            surface_salience=0.5,
            hypothesis_likelihood=0.2,
            context_support=0.7,
        ),
        make_proposal(
            candidate_role="HYPOTHESIS",
            raw_confidence=0.5,
            surface_salience=0.7,
            hypothesis_likelihood=0.7,
            context_support=0.3,
        ),
        make_proposal(
            candidate_role="ANCHOR",
            raw_confidence=0.9,
            surface_salience=0.4,
            hypothesis_likelihood=0.05,
            context_support=0.85,
        ),
    ]

    for i, p in enumerate(proposals):
        result = regulator.evaluate(p)
        assert result.behavior_affecting is False, (
            f"Proposal {i}: behavior_affecting must be False, got {result.behavior_affecting}"
        )

    # 也测试 field_compatibility=0 的阻塞情况
    blocked_result = regulator.evaluate(proposals[0], field_compatibility=0.0)
    assert blocked_result.behavior_affecting is False


# ── 测试 7：未导入 force_adapter ────────────────────────────────────────────────


def test_no_force_event_adapter_called() -> None:
    """验证 regulator_dry_run 模块未导入 ForceEvent Adapter。"""
    import src.llm_gate.regulator_dry_run as rdr

    # 检查模块内容
    module_items = dir(rdr)

    # 不应该包含任何 force_adapter 相关导入
    forbidden_imports = [
        "force_adapter",
        "PerturbationToForceAdapter",
        "ForceEvent",
        "ForceEventAdapter",
        "field_dynamics",
        "field_state",
        "motion_params",
        "body_action",
        "body_state",
        "runtime",
        "U_t",
    ]
    for item in forbidden_imports:
        assert item not in module_items, (
            f"Forbidden import detected: '{item}' in regulator_dry_run module"
        )


# ── 测试 8：主导公式正确性 ─────────────────────────────────────────────────────


def test_dominance_formula_correct(regulator: ContextualEvidenceRegulatorDryRun) -> None:
    """手动计算验证 d_i = max(0, s_i - q_i) * h_i 公式。"""
    # 手动构造已知值
    s_i = 0.85
    q_i = 0.285
    h_i = 0.65
    expected_d = max(0.0, 0.85 - 0.285) * 0.65  # = 0.565 * 0.65 = 0.36725

    proposal = make_proposal(
        candidate_role="HYPOTHESIS",
        raw_confidence=0.7,
        surface_salience=s_i,
        hypothesis_likelihood=h_i,
        context_support=q_i,
    )
    result = regulator.evaluate(proposal)

    assert result.dominance_risk == pytest.approx(expected_d, abs=1e-6), (
        f"Expected d_i={expected_d} but got {result.dominance_risk}"
    )

    # 测试 d_i = 0 的情况: q_i > s_i
    proposal2 = make_proposal(
        candidate_role="ANCHOR",
        raw_confidence=0.8,
        surface_salience=0.4,
        hypothesis_likelihood=0.5,
        context_support=0.6,
    )
    result2 = regulator.evaluate(proposal2)
    assert result2.dominance_risk == 0.0, (
        f"Expected d_i=0 (q_i > s_i) but got {result2.dominance_risk}"
    )


# ── 测试 9：调整权重边界 ────────────────────────────────────────────────────────


def test_adjusted_weight_bounded(regulator: ContextualEvidenceRegulatorDryRun) -> None:
    """权重始终在 [0,1] 范围内。"""
    # 极端高值
    proposal_high = make_proposal(
        candidate_role="ANCHOR",
        raw_confidence=1.0,
        surface_salience=0.3,
        hypothesis_likelihood=0.0,
        context_support=1.0,
    )
    result_high = regulator.evaluate(proposal_high)
    assert 0.0 <= result_high.adjusted_weight <= 1.0, (
        f"High weight out of bounds: {result_high.adjusted_weight}"
    )

    # 极端低值
    proposal_low = make_proposal(
        candidate_role="NOISE",
        raw_confidence=0.0,
        surface_salience=0.9,
        hypothesis_likelihood=1.0,
        context_support=0.0,
    )
    result_low = regulator.evaluate(proposal_low)
    assert 0.0 <= result_low.adjusted_weight <= 1.0, (
        f"Low weight out of bounds: {result_low.adjusted_weight}"
    )

    # 中间值：所有角色都应产生有效权重
    for role in ["ANCHOR", "HYPOTHESIS", "MODIFIER", "CONTEXT_CONTINUATION", "NOISE"]:
        p = make_proposal(
            candidate_role=role,
            raw_confidence=0.6,
            surface_salience=0.5,
            hypothesis_likelihood=0.3,
            context_support=0.5,
        )
        r = regulator.evaluate(p)
        assert 0.0 <= r.adjusted_weight <= 1.0, (
            f"Role {role}: weight {r.adjusted_weight} out of [0,1]"
        )


# ── 测试 10：6 个 mock 案例全通过 ────────────────────────────────────────────────


def test_six_mock_cases_run(regulator: ContextualEvidenceRegulatorDryRun) -> None:
    """所有 6 个 mock 案例通过 regulator 不崩溃，产生有效结果。"""
    from scripts.run_p40c_experiment import MOCK_PROPOSALS, CASE_LABELS

    assert len(MOCK_PROPOSALS) == 6, "Expected exactly 6 mock proposals"

    for i, proposal in enumerate(MOCK_PROPOSALS):
        result = regulator.evaluate(proposal)
        case_label = CASE_LABELS[i]

        # 基础断言
        assert isinstance(result, RegulatorResult), (
            f"Case {case_label}: result is not RegulatorResult"
        )
        assert result.behavior_affecting is False, (
            f"Case {case_label}: behavior_affecting must be False"
        )
        assert 0.0 <= result.adjusted_weight <= 1.0, (
            f"Case {case_label}: adjusted_weight out of [0,1]"
        )
        assert 0.0 <= result.dominance_risk <= 1.0, (
            f"Case {case_label}: dominance_risk out of [0,1]"
        )
        assert result.authorized_role in {
            "ANCHOR",
            "HYPOTHESIS",
            "MODIFIER",
            "CONTEXT_CONTINUATION",
            "NOISE",
        }, f"Case {case_label}: invalid authorized_role: {result.authorized_role}"

        # 审计追踪完整性
        required_audit_keys = {
            "raw_confidence",
            "role_weight",
            "context_factor",
            "field_compatibility",
            "recurrence_score",
            "dominance_risk",
            "dominance_penalty",
            "adjusted_weight_raw",
            "budget_cap",
            "candidate_role",
            "authorized_role",
        }
        missing_keys = required_audit_keys - set(result.audit_trace.keys())
        assert not missing_keys, (
            f"Case {case_label}: audit_trace missing keys: {missing_keys}"
        )

    # 特定案例验证
    # Case D (ANCHOR → 应保留 ANCHOR)
    result_d = regulator.evaluate(MOCK_PROPOSALS[3])
    assert result_d.candidate_role == "ANCHOR"
    assert result_d.authorized_role == "ANCHOR", (
        f"Case D: ANCHOR should be retained, got {result_d.authorized_role}: {result_d.role_shift_reason}"
    )

    # Case F (NOISE → 应降级或保持 NOISE)
    result_f = regulator.evaluate(MOCK_PROPOSALS[5])
    assert result_f.authorized_role == "NOISE", (
        f"Case F: expected NOISE, got {result_f.authorized_role}"
    )

    # Case B (高显著低上下文 → 应有 d_i 警告或低权重)
    result_b = regulator.evaluate(MOCK_PROPOSALS[1])
    assert result_b.dominance_risk > 0.4, (
        f"Case B: expected high dominance_risk, got {result_b.dominance_risk}"
    )
