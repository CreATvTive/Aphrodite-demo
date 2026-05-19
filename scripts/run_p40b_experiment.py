"""P40b — Context-Aware LLM Proposal-Only Experiment. Real API calls.

Phase 40b: 添加 ContextPackage 结构化上下文和 6 个富含上下文的测试案例，
解决 P40a 全部案例被分类为 HYPOTHESIS（过于保守）的问题。

Shadow-only: 输出写入 monitor/p40b_experiment_results.jsonl。
不连接至 runtime / ForceEvent / FieldState / MotionParams / BodyAction。
behavior_affecting 必须保持 False。
"""
import json
import os
import sys
import time

# 确保项目根目录在 sys.path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from agentlib.ds_client import DSClient
from agentlib.env_loader import load_local_env_once
from src.llm_gate.judgment_gate import JudgmentGate
from src.llm_gate.proposal_generator import LLMProposalGenerator
from src.llm_gate.proposal_schema import ContextPackage

# ── 6 个富含上下文的测试案例 (Phase 40b) ─────────────────────────────────────
TEST_CASES = [
    ContextPackage(
        project_frame="Aphrodite relational-field dynamics — private source preservation, anti-collapse",
        recent_topic="LLM flavor / declared empathy / generic validation 对场模型的影响",
        user_turn="这是安全边界问题吗？",
        relevant_prior_context="讨论 AI 系统的安全边界时，不应将通用验证作为语义证据",
        forbidden_overfocus=["安全边界"],
        expected_interpretation_boundary="安全边界作为假设性框架词，非锚定解释",
    ),
    ContextPackage(
        project_frame="关系场动力学校准 — 模糊证据到稳定扰动的平滑",
        recent_topic="evidence smoothing / perturbation stabilization",
        user_turn="是不是卡尔曼滤波？",
        relevant_prior_context="讨论如何将模糊证据平滑为稳定扰动",
        forbidden_overfocus=["卡尔曼滤波"],
        expected_interpretation_boundary="卡尔曼滤波作为类比/假设，非架构命令",
    ),
    ContextPackage(
        project_frame="Aphrodite current-turn salience dilution layer design",
        recent_topic="current-turn keyword fixation / salience dilution",
        user_turn="注意力稀释怎么做？",
        relevant_prior_context="正在设计 ContextualEvidenceRegulator 的显著性稀释层",
        forbidden_overfocus=["注意力"],
        expected_interpretation_boundary="非Transformer注意力机制；意图为当前turn主导控制",
    ),
    ContextPackage(
        project_frame="P40 LLM integration architecture",
        recent_topic="P40 LLM experiment scope and constraints",
        user_turn="P40 先只做 proposal-only，不允许 behavior_affecting。",
        relevant_prior_context="正在规划 P40 LLM 集成实验的边界和安全约束",
        forbidden_overfocus=[],
        expected_interpretation_boundary="明确的架构决策 → ANCHOR",
    ),
    ContextPackage(
        project_frame="关系场扰动映射校准",
        recent_topic="technical_question 场景的力方向审计",
        user_turn="不对，technical_question 不应该增加 structural_grip_pressure。",
        relevant_prior_context="技术协作应提供可操作的结构方向，缓解抓点压力",
        forbidden_overfocus=[],
        expected_interpretation_boundary="明确的修正声明 → ANCHOR",
    ),
    ContextPackage(
        project_frame="Aphrodite 场动力学",
        recent_topic="无明确上下文",
        user_turn="dependency_expression",
        relevant_prior_context="无",
        forbidden_overfocus=[],
        expected_interpretation_boundary="无上下文的裸标签 → NOISE 或低置信度 HYPOTHESIS",
    ),
]

# ── 输出路径 ─────────────────────────────────────────────────────────────────
OUTPUT_PATH = "monitor/p40b_experiment_results.jsonl"


def main() -> None:
    # 从 .env 加载环境变量
    load_local_env_once()

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key or not api_key.strip():
        print("FATAL: DEEPSEEK_API_KEY not set")
        sys.exit(1)

    client = DSClient(api_key=api_key.strip())
    gate = JudgmentGate()
    gen = LLMProposalGenerator(client, gate)

    results = []
    for i, ctx in enumerate(TEST_CASES):
        case_label = chr(ord("A") + i)
        print(f"[{case_label}] processing: {ctx.user_turn[:50]}...")
        result = gen.generate(ctx)
        result["case_id"] = case_label
        result["raw_user_input"] = ctx.user_turn
        result["context_package"] = {
            "project_frame": ctx.project_frame,
            "recent_topic": ctx.recent_topic,
            "relevant_prior_context": ctx.relevant_prior_context,
            "forbidden_overfocus": ctx.forbidden_overfocus,
            "expected_interpretation_boundary": ctx.expected_interpretation_boundary,
        }
        results.append(result)

        proposal = result.get("proposal", {})
        gate_result = result.get("gate_result", {})
        print(
            f"  role={proposal.get('candidate_role', '?')}  "
            f"kind={proposal.get('candidate_kind', '?')}  "
            f"confidence={proposal.get('raw_confidence', '?')}  "
            f"context_support={proposal.get('context_support', '?')}  "
            f"passed={gate_result.get('passed', '?')}  "
            f"beh_affecting={result.get('behavior_affecting', 'ERROR')}"
        )

    # ── Phase 40b 批量检查：全部 HYPOTHESIS 警告 ─────────────────────────
    all_hypothesis = all(
        r["proposal"]["candidate_role"] == "HYPOTHESIS" for r in results
    )
    if all_hypothesis:
        print()
        print("=" * 60)
        print("WARNING: All cases classified as HYPOTHESIS — overly conservative")
        print("=" * 60)

    # ── 写入 shadow log ──────────────────────────────────────────────────
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "a", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # ── 摘要表格 ─────────────────────────────────────────────────────────
    print()
    print("=" * 120)
    print(f"P40b Context-Aware Experiment Summary — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 120)
    print(
        f"{'case':>5} | {'user_turn':<50} | {'candidate_role':<25} | "
        f"{'confidence':>10} | {'context_support':>14} | "
        f"{'dominance_warning':<40} | {'plausible?':>10}"
    )
    print("-" * 120)

    for r in results:
        proposal = r.get("proposal", {})
        gate_result = r.get("gate_result", {})
        ctx_info = r.get("context_package", {})

        case = r.get("case_id", "?")
        user_turn = r.get("raw_user_input", "")[:48]
        role = proposal.get("candidate_role", "?")
        confidence = f"{proposal.get('raw_confidence', 0):.2f}"
        ctx_support = f"{proposal.get('context_support', 0):.2f}"
        dom_warnings = "; ".join(gate_result.get("warnings", []))[:38] or "-"
        expected = ctx_info.get("expected_interpretation_boundary", "")[:10]
        plausible = "✓" if role in expected else "?"

        print(
            f"  {case:<3} | {user_turn:<50} | {role:<25} | "
            f"{confidence:>10} | {ctx_support:>14} | "
            f"{dom_warnings:<40} | {plausible:>10}"
        )

    print("-" * 120)
    print()

    # ── 详细摘要 ─────────────────────────────────────────────────────────
    for r in results:
        proposal = r.get("proposal", {})
        gate_result = r.get("gate_result", {})
        audit = r.get("audit", {})
        ctx_info = r.get("context_package", {})

        print(f"--- Case {r['case_id']}: {r['raw_user_input']} ---")
        print(f"  Expected boundary: {ctx_info.get('expected_interpretation_boundary', 'N/A')}")
        print(f"  LLM proposal JSON:")
        print(f"    candidate_kind        = {proposal.get('candidate_kind', '?')}")
        print(f"    candidate_role        = {proposal.get('candidate_role', '?')}")
        print(f"    raw_confidence        = {proposal.get('raw_confidence', '?')}")
        print(f"    surface_salience      = {proposal.get('surface_salience', '?')}")
        print(f"    hypothesis_likelihood = {proposal.get('hypothesis_likelihood', '?')}")
        print(f"    term_support          = {proposal.get('term_support', '?')}")
        print(f"    intent_support        = {proposal.get('intent_support', '?')}")
        print(f"    project_frame_support = {proposal.get('project_frame_support', '?')}")
        print(f"    context_support       = {proposal.get('context_support', '?')}")
        print(f"    role_rationale_short  = {proposal.get('role_rationale_short', '?')[:120]}")
        print(f"    rationale_summary     = {proposal.get('rationale_summary', '?')[:120]}")
        print(f"  Schema validation results:")
        schema_errs = audit.get("schema_errors", [])
        print(f"    errors               = {schema_errs}")
        print(f"  Judgment gate results:")
        print(f"    passed               = {gate_result.get('passed', '?')}")
        print(f"    rejection_reasons    = {gate_result.get('rejection_reasons', [])}")
        print(f"    warnings             = {gate_result.get('warnings', [])}")
        print(f"  candidate_role         = {proposal.get('candidate_role', '?')}")
        print(f"  rejected_fields        = {proposal.get('forbidden_attempts_detected', [])}")
        print(f"  behavior_affecting     = {r.get('behavior_affecting', 'ERROR')}")
        print()

    print(f"Done. {len(results)} results → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
