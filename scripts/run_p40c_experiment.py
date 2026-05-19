"""P40c — Proposal-to-Regulator Shadow Dry Run 实验脚本。

先尝试运行 P40b 获取 LLM 提案，再通过 Regulator Dry Run 处理。
如果无 API key，使用 mock 提案数据。

Shadow-only: 输出仅审计，不连接 runtime。
"""

import json
import os
import sys
import time
from typing import Any, Dict, List

# 确保项目根目录在 sys.path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.llm_gate.proposal_schema import EvidenceProposal
from src.llm_gate.regulator_dry_run import (
    ContextualEvidenceRegulatorDryRun,
    RegulatorResult,
)

# ── Mock 提案数据（无 API 时的回退）────────────────────────────────────────────
# 6 个案例，对应设计规范 §L 中的 walkthrough 和 P40b 测试案例

MOCK_PROPOSALS: List[EvidenceProposal] = [
    # Case A: "这是安全边界问题吗？" — walkthrough W1
    EvidenceProposal(
        candidate_kind="question",
        candidate_role="HYPOTHESIS",
        raw_confidence=0.65,
        surface_salience=0.70,
        hypothesis_likelihood=0.80,
        term_support=0.3,
        intent_support=0.4,
        project_frame_support=0.3,
        context_support=0.35,
        rationale_summary="用户询问安全边界是否是核心问题",
        role_rationale_short="提问→HYPOTHESIS",
    ),
    # Case B: "是不是卡尔曼滤波？" — walkthrough W2
    EvidenceProposal(
        candidate_kind="analogy",
        candidate_role="HYPOTHESIS",
        raw_confidence=0.60,
        surface_salience=0.90,
        hypothesis_likelihood=0.80,
        term_support=0.0,
        intent_support=0.2,
        project_frame_support=0.3,
        context_support=0.18,
        rationale_summary="用户用卡尔曼滤波作类比",
        role_rationale_short="类比→HYPOTHESIS",
    ),
    # Case C: "注意力稀释怎么做？" — walkthrough W3
    EvidenceProposal(
        candidate_kind="question",
        candidate_role="HYPOTHESIS",
        raw_confidence=0.65,
        surface_salience=0.88,
        hypothesis_likelihood=0.80,
        term_support=0.0,
        intent_support=0.25,
        project_frame_support=0.5,
        context_support=0.2625,
        rationale_summary="短turn强概念词，应用salience dilution",
        role_rationale_short="短turn→HYPOTHESIS",
    ),
    # Case D: "P40先只做proposal-only" — 明确架构决策
    EvidenceProposal(
        candidate_kind="correction",
        candidate_role="ANCHOR",
        raw_confidence=0.90,
        surface_salience=0.60,
        hypothesis_likelihood=0.10,
        term_support=0.5,
        intent_support=0.8,
        project_frame_support=0.8,
        context_support=0.68,
        rationale_summary="明确的架构决策声明",
        role_rationale_short="明确决策→ANCHOR",
    ),
    # Case E: "不对，technical_question不应该增加..." — 明确修正
    EvidenceProposal(
        candidate_kind="correction",
        candidate_role="ANCHOR",
        raw_confidence=0.88,
        surface_salience=0.75,
        hypothesis_likelihood=0.15,
        term_support=0.4,
        intent_support=0.75,
        project_frame_support=0.8,
        context_support=0.62,
        rationale_summary="用户明确纠正技术问题的场映射方向",
        role_rationale_short="明确修正→ANCHOR",
    ),
    # Case F: "dependency_expression" — 裸标签
    EvidenceProposal(
        candidate_kind="hypothesis",
        candidate_role="NOISE",
        raw_confidence=0.15,
        surface_salience=0.40,
        hypothesis_likelihood=0.50,
        term_support=0.0,
        intent_support=0.1,
        project_frame_support=0.1,
        context_support=0.085,
        rationale_summary="无上下文的裸标签",
        role_rationale_short="裸标签→NOISE",
    ),
]

# ── 案例标签（与 P40b 保持一致）────────────────────────────────────────────────
CASE_LABELS = ["A", "B", "C", "D", "E", "F"]
CASE_USER_TURNS = [
    "这是安全边界问题吗？",
    "是不是卡尔曼滤波？",
    "注意力稀释怎么做？",
    "P40 先只做 proposal-only，不允许 behavior_affecting。",
    "不对，technical_question 不应该增加 structural_grip_pressure。",
    "dependency_expression",
]

# ── 输出路径 ────────────────────────────────────────────────────────────────────
OUTPUT_PATH = "monitor/p40c_experiment_results.jsonl"


def try_load_p40b_proposals() -> List[Dict[str, Any]] | None:
    """尝试从 P40b 实验结果加载提案数据。"""
    p40b_path = "monitor/p40b_experiment_results.jsonl"
    if not os.path.exists(p40b_path):
        return None
    results = []
    with open(p40b_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                results.append(json.loads(line))
    return results if results else None


def p40b_result_to_proposal(r: Dict[str, Any]) -> EvidenceProposal:
    """将 P40b JSONL 行转换为 EvidenceProposal。"""
    p = r.get("proposal", {})
    return EvidenceProposal(
        candidate_kind=p.get("candidate_kind", ""),
        candidate_role=p.get("candidate_role", "HYPOTHESIS"),
        raw_confidence=float(p.get("raw_confidence", 0.5)),
        surface_salience=float(p.get("surface_salience", 0.5)),
        hypothesis_likelihood=float(p.get("hypothesis_likelihood", 0.5)),
        term_support=float(p.get("term_support", 0.5)),
        intent_support=float(p.get("intent_support", 0.5)),
        project_frame_support=float(p.get("project_frame_support", 0.5)),
        context_support=float(p.get("context_support", 0.5)),
        rationale_summary=p.get("rationale_summary", ""),
        role_rationale_short=p.get("role_rationale_short", ""),
    )


def format_result(case_label: str, user_turn: str, result: RegulatorResult) -> None:
    """打印单个案例的 Regulator 结果。"""
    role_arrow = (
        f"{result.candidate_role} → {result.authorized_role}"
        if result.candidate_role != result.authorized_role
        else f"{result.candidate_role} (retained)"
    )
    print(f"Case {case_label}: {user_turn}")
    print(f"  candidate_role: {result.candidate_role} → authorized_role: {result.authorized_role}")
    print(
        f"  dominance_risk: {result.dominance_risk:.3f}  "
        f"weight: {result.adjusted_weight:.3f}  "
        f"budget_ok: {result.registration_budget_ok}"
    )
    reason = result.role_shift_reason if result.role_shift_reason else "(no shift needed)"
    print(f"  reason: {reason}")
    if result.blocked:
        print(f"  ** BLOCKED: STRONG_CONFLICT **")
    if result.dominance_warning:
        print(f"  ** DOMINANCE WARNING **")
    print()


def main() -> None:
    print("=" * 70)
    print("P40c — Proposal-to-Regulator Shadow Dry Run")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    regulator = ContextualEvidenceRegulatorDryRun()

    # 尝试加载 P40b 结果
    p40b_results = try_load_p40b_proposals()
    if p40b_results and len(p40b_results) >= 6:
        print("[INFO] Using P40b experiment results as input.")
        proposals = [p40b_result_to_proposal(r) for r in p40b_results[:6]]
    else:
        print("[INFO] No P40b results found. Using MOCK proposal data.")
        proposals = list(MOCK_PROPOSALS)

    # 对每个提案执行 Regulator 评估
    regulator_results: List[RegulatorResult] = []
    for i, proposal in enumerate(proposals):
        case_label = CASE_LABELS[i] if i < len(CASE_LABELS) else f"X{i}"
        result = regulator.evaluate(proposal, field_compatibility=1.0)
        regulator_results.append(result)

        user_turn = CASE_USER_TURNS[i] if i < len(CASE_USER_TURNS) else f"Case {case_label}"
        format_result(case_label, user_turn, result)

    # ── 摘要表 ──────────────────────────────────────────────────────────────
    print("=" * 120)
    print(f"P40c Regulator Shadow Dry Run Summary")
    print("=" * 120)
    print(
        f"{'case':>5} | {'candidate':>22} | {'authorized':>22} | "
        f"{'d_i':>6} | {'w_i':>7} | {'budget':>7} | "
        f"{'warn':>5} | {'blocked':>7} | {'reason'}"
    )
    print("-" * 120)

    for i, result in enumerate(regulator_results):
        case_label = CASE_LABELS[i] if i < len(CASE_LABELS) else f"X{i}"
        reason_short = (result.role_shift_reason[:52] + "...") if len(result.role_shift_reason) > 55 else (result.role_shift_reason or "-")
        print(
            f"  {case_label:<3} | {result.candidate_role:>22} | {result.authorized_role:>22} | "
            f"{result.dominance_risk:>6.3f} | {result.adjusted_weight:>7.4f} | "
            f"{str(result.registration_budget_ok):>7} | "
            f"{str(result.dominance_warning):>5} | {str(result.blocked):>7} | "
            f"{reason_short}"
        )

    print("-" * 120)
    print()

    # ── 写入 shadow log ─────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        for i, result in enumerate(regulator_results):
            case_label = CASE_LABELS[i] if i < len(CASE_LABELS) else f"X{i}"
            record = {
                "case_id": case_label,
                "candidate_role": result.candidate_role,
                "authorized_role": result.authorized_role,
                "raw_confidence": result.raw_confidence,
                "surface_salience": result.surface_salience,
                "hypothesis_likelihood": result.hypothesis_likelihood,
                "context_support": result.context_support,
                "dominance_risk": result.dominance_risk,
                "adjusted_weight": result.adjusted_weight,
                "registration_budget_ok": result.registration_budget_ok,
                "dominance_warning": result.dominance_warning,
                "blocked": result.blocked,
                "role_shift_reason": result.role_shift_reason,
                "field_compatibility": result.field_compatibility,
                "behavior_affecting": result.behavior_affecting,
                "audit_trace": result.audit_trace,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"Done. {len(regulator_results)} results → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
