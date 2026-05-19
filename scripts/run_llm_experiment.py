"""Run P40 LLM integration experiment.

Usage:
    python scripts/run_llm_experiment.py [--scenario SCENARIO_NAME]

Requires:
    - DEEPSEEK_API_KEY environment variable
    - monitor/body_action_composition_golden.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# 确保项目根目录在 sys.path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from agentlib.ds_client import DSClient, DSClientError
from agentlib.env_loader import load_local_env_once
from src.llm_gate.experiment import LLMExperiment
from src.llm_gate.judgment_gate import JudgmentGate


def _resolve_golden_path() -> str:
    """Resolve the golden JSONL path relative to project root."""
    default = os.path.join(_project_root, "monitor", "body_action_composition_golden.jsonl")
    if os.path.isfile(default):
        return default
    # fallback: try relative to cwd
    alt = os.path.join(os.getcwd(), "monitor", "body_action_composition_golden.jsonl")
    if os.path.isfile(alt):
        return alt
    raise FileNotFoundError(
        f"Cannot find body_action_composition_golden.jsonl. Tried: {default}, {alt}"
    )


def _resolve_output_path() -> str:
    """Resolve the output JSONL path."""
    return os.path.join(_project_root, "monitor", "llm_experiment_results.jsonl")


def _load_golden_scenarios(golden_path: str) -> list[dict]:
    """Load all scenarios from golden JSONL."""
    scenarios = []
    with open(golden_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                scenarios.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"[SKIP] 无效 JSON 行: {line[:60]}...", file=sys.stderr)
    return scenarios


def _find_scenario_by_name(scenarios: list[dict], name: str) -> tuple[int, dict] | None:
    """Find a scenario by scenario_name (e.g., '场景-3')."""
    for i, sc in enumerate(scenarios):
        comp = sc.get("body_action_composition", {})
        comp_note = comp.get("composition_note", "")
        # 通过索引匹配 "场景-N"
        if name == f"场景-{i+1}":
            return i, sc
    return None


def main() -> None:
    # 从 .env 加载环境变量
    load_local_env_once()

    parser = argparse.ArgumentParser(description="P40 LLM Integration Experiment")
    parser.add_argument(
        "--scenario",
        type=str,
        default=None,
        help="Run only the specified scenario (e.g., '场景-3'). If omitted, run all 7.",
    )
    args = parser.parse_args()

    # 检查 API key
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        print(
            "[ERROR] DEEPSEEK_API_KEY environment variable is not set. "
            "Please set it before running the experiment.",
            file=sys.stderr,
        )
        print(
            "  Windows (cmd):   set DEEPSEEK_API_KEY=sk-...",
            file=sys.stderr,
        )
        print(
            "  Windows (ps):    $env:DEEPSEEK_API_KEY='sk-...'",
            file=sys.stderr,
        )
        sys.exit(1)

    # 加载 golden 数据
    golden_path = _resolve_golden_path()
    print(f"[INFO] 加载 golden 数据: {golden_path}")
    scenarios = _load_golden_scenarios(golden_path)
    print(f"[INFO] 加载了 {len(scenarios)} 个场景")

    if not scenarios:
        print("[ERROR] Golden 文件为空或无效。", file=sys.stderr)
        sys.exit(1)

    # 初始化组件
    try:
        ds_client = DSClient(api_key=api_key)
        gate = JudgmentGate()
    except DSClientError as e:
        print(f"[ERROR] DSClient 初始化失败: {e}", file=sys.stderr)
        sys.exit(1)

    output_path = _resolve_output_path()
    experiment = LLMExperiment(
        ds_client=ds_client,
        judgment_gate=gate,
        output_log_path=output_path,
    )

    # 运行实验
    if args.scenario:
        # 单场景运行
        found = _find_scenario_by_name(scenarios, args.scenario)
        if found is None:
            print(
                f"[ERROR] 找不到场景 '{args.scenario}'. 可用: "
                f"{[f'场景-{i+1}' for i in range(len(scenarios))]}",
                file=sys.stderr,
            )
            sys.exit(1)

        idx, sc = found
        print(f"[INFO] 运行单场景: 场景-{idx + 1}")
        result = experiment.run_scenario(sc, idx)

        # 写入单个结果
        if output_path:
            with open(output_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(result, ensure_ascii=False) + "\n")

        # 打印结果
        lang_status = "通过" if result["language_gate"]["passed"] else "拒绝"
        beh_status = "通过" if result["behavior_gate"]["passed"] else "拒绝"
        print(f"\n[{result['scenario_name']}] 结果:")
        print(f"  场景意图: {result['scenario_intent']}")
        print(f"  动作摘要: {result['action_summary']}")
        print(f"  LLM 原始回应: {result['llm_raw_response'][:120]}...")
        print(f"  语言片段: {result['language_fragment'][:80]}")
        print(f"  语言门控: {lang_status}")
        if result["language_gate"]["rejection_reasons"]:
            print(f"    拒绝原因: {result['language_gate']['rejection_reasons']}")
        print(f"  行为片段: {result['behavior_fragment'][:80]}")
        print(f"  行为门控: {beh_status}")
        if result["behavior_gate"]["rejection_reasons"]:
            print(f"    拒绝原因: {result['behavior_gate']['rejection_reasons']}")
        if result.get("llm_error"):
            print(f"  LLM 错误: {result['llm_error']}")
        print(f"  输出日志: {output_path}")
    else:
        # 全部 7 个场景
        print(f"[INFO] 运行全部 {len(scenarios)} 个场景...")
        print(f"[INFO] 输出日志: {output_path}\n")
        experiment.run_all_scenarios(golden_path)
        print(f"\n[INFO] 详细结果已写入: {output_path}")


if __name__ == "__main__":
    main()
