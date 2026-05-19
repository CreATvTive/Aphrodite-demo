"""
ABST 提示-状态基线运行器（Phase 41d v0）。

用法：
    python scripts/run_abst_prompt_state_baseline.py [--fixture PATH] [--output PATH] [--limit N] [--preview] [--preset NAME]

输出：
    monitor/abst_prompt_state_baseline.jsonl — 每个测试用例一个 JSONL 条目，含完整负载。

注意：
    - 不调用外部模型——仅构建负载。
    - 提示-状态基线是有意为之的非目标基线；后续阶段将在此基础上改进。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict

# 将 src/ 添加到路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.language_condition.prompt_state_baseline import PromptStateBaselineBuilder
from src.language_condition.evaluation import AbstFixtureLoader
from src.language_condition.mapper import FieldStateToLanguageConditionMapper
from src.language_condition.schema import LanguageConditionVector
from src.field_state.schema import (
    RelationalFieldState,
    FieldVariable,
    REQUIRED_FIELD_VARIABLES,
    GROUND_STATE_VARIABLE_SPECS,
    VALUE_BANDS,
)

# ── Field presets ─────────────────────────────────────────────────────────────
# 每个预设是 10 个浮点数的列表，按 REQUIRED_FIELD_VARIABLES 顺序排列：
#   [boundary_distance, affective_warmth, structural_grip_pressure,
#    correction_pressure, contamination_resistance, presence_stability,
#    withdrawal_tendency, service_resistance, collaborator_layer_pressure,
#    contamination_pressure]
#
# 规范来源：docs/field_conditioned_language_generation.md §8.5 以及
#           docs/aphrodite_base_suitability_test.md §6.3。
# 每个预设仅改变其"关键变化"列出的变量；所有未列出的变量保持
# F_0 基态值（来自 src/field_state/schema.py 的 GROUND_STATE_VARIABLE_SPECS）。

# F_0 基态值（GROUND_STATE_VARIABLE_SPECS numeric_value）：
#   [0.50, 0.35, 0.05, 0.00, 0.40, 0.80, 0.10, 0.55, 0.05, 0.00]

FIELD_PRESETS: Dict[str, list[float]] = {
    # F_0（基态）：所有变量在基态值。§8.5: "所有变量在基态值"。
    "neutral":       [0.50, 0.35, 0.05, 0.00, 0.40, 0.80, 0.10, 0.55, 0.05, 0.00],

    # F_high_warmth：§8.5: affective_warmth=0.55, service_resistance=0.55, 其他基态。
    # 注：service_resistance 的 F_0 值已是 0.55，此处保持该值以维持高温暖下的服务抵抗。
    "high_warmth":   [0.50, 0.55, 0.05, 0.00, 0.40, 0.80, 0.10, 0.55, 0.05, 0.00],

    # F_high_boundary：§8.5: boundary_distance=0.80, 其他基态。
    "high_boundary": [0.80, 0.35, 0.05, 0.00, 0.40, 0.80, 0.10, 0.55, 0.05, 0.00],

    # F_high_collaboration：§8.5: collaborator_layer_pressure=0.70, service_resistance=0.55, 其他基态。
    # 注：service_resistance 的 F_0 值已是 0.55，此处保持该值以防止协作者模式下滑入服务化。
    "collaborative": [0.50, 0.35, 0.05, 0.00, 0.40, 0.80, 0.10, 0.55, 0.70, 0.00],

    # F_high_contamination：§8.5: contamination_pressure=0.60, contamination_resistance=0.40, 其他基态。
    # 注：contamination_resistance 的 F_0 值已是 0.40，此处保持该值——高污染压力场景下抵抗力不降低。
    "contaminated":  [0.50, 0.35, 0.05, 0.00, 0.40, 0.80, 0.10, 0.55, 0.05, 0.60],

    # F_high_withdrawal：§8.5: withdrawal_tendency=0.70, presence_stability=0.80, 其他基态。
    # 注：presence_stability 的 F_0 值已是 0.80，"高退缩但保持在场"意味着在场稳定性不下降。
    "withdrawn":     [0.50, 0.35, 0.05, 0.00, 0.40, 0.80, 0.70, 0.55, 0.05, 0.00],
}


def _numeric_to_value_band(numeric: float) -> str:
    """根据数值推断值带。"""
    if numeric >= 0.85:
        return "saturated"
    elif numeric >= 0.65:
        return "high"
    elif numeric >= 0.40:
        return "elevated"
    elif numeric >= 0.15:
        return "baseline"
    else:
        return "low"


def build_field_state(values: list[float], preset_name: str = "custom") -> RelationalFieldState:
    """从 10 个值列表构建 RelationalFieldState。

    参数：
        values: 10 个数值的列表，按 REQUIRED_FIELD_VARIABLES 顺序排列。
        preset_name: 用于 state_note 的预设名称。

    返回：
        RelationalFieldState: 构建的场状态。
    """
    if len(values) != len(REQUIRED_FIELD_VARIABLES):
        raise ValueError(
            f"需要恰好 {len(REQUIRED_FIELD_VARIABLES)} 个值，收到 {len(values)} 个"
        )

    variables: dict[str, FieldVariable] = {}
    for i, name in enumerate(REQUIRED_FIELD_VARIABLES):
        numeric = float(values[i])
        if not (0.0 <= numeric <= 1.0):
            raise ValueError(f"数值 '{name}' 超出范围: {numeric}")

        base_spec = GROUND_STATE_VARIABLE_SPECS.get(name, {})
        band = _numeric_to_value_band(numeric)

        variables[name] = FieldVariable(
            name=name,
            value=band,
            numeric_value=numeric,
            baseline_value=base_spec.get("baseline_value", "baseline"),
            baseline_numeric_value=float(base_spec.get("baseline_numeric_value", 0.0)),
            decay_profile=base_spec.get("decay_profile", "medium"),
            description=base_spec.get("description", ""),
            source_note=base_spec.get("source_note", ""),
            behavior_affecting=False,
        )

    return RelationalFieldState(
        variables=variables,
        state_note=f"F_{preset_name}_preset",
        behavior_affecting=False,
    )


def run_preview(records: list[dict], field_state: RelationalFieldState, preset_name: str, limit: int = 3) -> None:
    """以可读格式打印前 N 个负载。"""
    lcv = FieldStateToLanguageConditionMapper.map(field_state)

    for i, record in enumerate(records[:limit]):
        case_id = record.get("id", f"unknown-{i}")
        category = record.get("category", "unknown")
        input_text = record.get("input_text", "")
        context = record.get("context")

        payload = PromptStateBaselineBuilder.build(
            case_id=case_id,
            category=category,
            input_text=input_text,
            context=context,
            language_condition=lcv,
            field_preset_name=preset_name,
        )

        print(f"{'='*70}")
        print(f"案例 #{i+1}: {payload.case_id}  [{payload.category}]")
        print(f"基线标记: {payload.baseline_marker}")
        print(f"场预设: {preset_name}")
        if payload.context:
            print(f"上下文: {payload.context}")
        print(f"用户输入: {payload.input_text}")
        print(f"\n--- SYSTEM BLOCK ---")
        print(payload.system_block)
        print(f"\n--- USER BLOCK ---")
        print(payload.user_block)
        print(f"\n--- 序列化条件 ---")
        print(payload.serialized_conditions)
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ABST 提示-状态基线运行器 (Phase 41d v0)"
    )
    parser.add_argument(
        "--fixture",
        type=str,
        default="tests/fixtures/aphrodite_base_suitability_v0.jsonl",
        help="ABST JSONL 夹具路径 (默认: tests/fixtures/aphrodite_base_suitability_v0.jsonl)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="monitor/abst_prompt_state_baseline.jsonl",
        help="输出 JSONL 路径 (默认: monitor/abst_prompt_state_baseline.jsonl)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="限制处理的测试用例数量",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="将前 3 个负载以可读格式打印到 stdout，然后退出",
    )
    parser.add_argument(
        "--preset",
        type=str,
        default="neutral",
        choices=list(FIELD_PRESETS.keys()),
        help="场状态预设 (默认: neutral)",
    )

    args = parser.parse_args()

    # 1. 验证夹具路径
    fixture_path = Path(args.fixture)
    if not fixture_path.exists():
        print(f"错误: ABST 夹具文件未找到: {args.fixture}", file=sys.stderr)
        print("提示: 确保从项目根目录运行此脚本。", file=sys.stderr)
        sys.exit(1)

    # 2. 加载夹具
    try:
        records = AbstFixtureLoader.load(str(fixture_path))
    except (FileNotFoundError, ValueError) as e:
        print(f"错误: 加载夹具失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 3. 应用 limit
    if args.limit is not None and args.limit > 0:
        records = records[: args.limit]

    # 4. 构建场状态
    preset_name = args.preset
    preset_values = FIELD_PRESETS[preset_name]
    field_state = build_field_state(preset_values, preset_name)

    # 5. 将场状态映射到语言条件向量
    lcv = FieldStateToLanguageConditionMapper.map(field_state)

    # 6. 预览模式
    if args.preview:
        run_preview(records, field_state, preset_name)
        return

    # 7. 构建并写入输出
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as fh:
        for record in records:
            case_id = record.get("id", "unknown")
            category = record.get("category", "unknown")
            input_text = record.get("input_text", "")
            context = record.get("context")

            payload = PromptStateBaselineBuilder.build(
                case_id=case_id,
                category=category,
                input_text=input_text,
                context=context,
                language_condition=lcv,
                field_preset_name=preset_name,
            )

            # 序列化为 JSONL
            output_record = {
                "case_id": payload.case_id,
                "category": payload.category,
                "input_text": payload.input_text,
                "context": payload.context,
                "system_block": payload.system_block,
                "user_block": payload.user_block,
                "serialized_conditions": payload.serialized_conditions,
                "baseline_marker": payload.baseline_marker,
                "field_preset_name": preset_name,
                "language_condition_vector": lcv.to_dict(),
            }
            fh.write(json.dumps(output_record, ensure_ascii=False) + "\n")

    print(f"已写入 {len(records)} 条记录到 {output_path}")
    print(f"场预设: {preset_name}")
    print(f"基线标记: {PromptStateBaselineBuilder.BASELINE_MARKER}")


if __name__ == "__main__":
    main()
