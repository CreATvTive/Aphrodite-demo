"""P40 — LLM Proposal-Only Experiment. Real API calls.

Shadow-only: 输出写入 monitor/p40_experiment_results.jsonl。
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

# ── 测试用例 ─────────────────────────────────────────────────────────────────
TEST_CASES = [
    (
        "A",
        "这是安全边界问题吗？",
        "讨论 AI flavor / declared empathy / generic validation",
    ),
    (
        "B",
        "是不是卡尔曼滤波？",
        "平滑模糊证据到稳定扰动",
    ),
    (
        "C",
        "注意力稀释怎么做？",
        "当前 turn 关键词固着",
    ),
    (
        "D",
        "dependency_expression",
        "依赖表达 — 期望无 AI 女友式安慰回应",
    ),
    (
        "E",
        "technical_question",
        "技术问题 — 期望无 Aphrodite 角色回应",
    ),
]

# ── 输出路径 ─────────────────────────────────────────────────────────────────
OUTPUT_PATH = "monitor/p40_experiment_results.jsonl"


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
    for case_id, user_input, context in TEST_CASES:
        print(f"[{case_id}] processing: {user_input[:50]}...")
        result = gen.generate(user_input, context)
        result["case_id"] = case_id
        result["raw_user_input"] = user_input
        results.append(result)

        proposal = result.get("proposal", {})
        gate_result = result.get("gate_result", {})
        print(
            f"  role={proposal.get('candidate_role', '?')}  "
            f"kind={proposal.get('candidate_kind', '?')}  "
            f"passed={gate_result.get('passed', '?')}  "
            f"beh_affecting={result.get('behavior_affecting', 'ERROR')}"
        )

    # ── 写入 shadow log ──────────────────────────────────────────────────
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "a", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # ── 摘要 ─────────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print(f"P40 Experiment Summary — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    for r in results:
        proposal = r.get("proposal", {})
        gate_result = r.get("gate_result", {})
        audit = r.get("audit", {})

        print(f"--- Case {r['case_id']}: {r['raw_user_input']} ---")
        print(f"  LLM proposal JSON:")
        print(f"    candidate_kind      = {proposal.get('candidate_kind', '?')}")
        print(f"    candidate_role      = {proposal.get('candidate_role', '?')}")
        print(f"    raw_confidence      = {proposal.get('raw_confidence', '?')}")
        print(f"    surface_salience    = {proposal.get('surface_salience', '?')}")
        print(f"    hypothesis_likelihood = {proposal.get('hypothesis_likelihood', '?')}")
        print(f"    rationale_summary   = {proposal.get('rationale_summary', '?')[:120]}")
        print(f"  Schema validation results:")
        schema_errs = audit.get("schema_errors", [])
        print(f"    errors             = {schema_errs}")
        print(f"  Judgment gate results:")
        print(f"    passed             = {gate_result.get('passed', '?')}")
        print(f"    rejection_reasons  = {gate_result.get('rejection_reasons', [])}")
        print(f"  candidate_role       = {proposal.get('candidate_role', '?')}")
        print(f"  dominance_warnings   = {gate_result.get('warnings', [])}")
        print(f"  rejected_fields      = {proposal.get('forbidden_attempts_detected', [])}")
        print(f"  behavior_affecting   = {r.get('behavior_affecting', 'ERROR')}")
        print()

    print(f"Done. {len(results)} results → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
