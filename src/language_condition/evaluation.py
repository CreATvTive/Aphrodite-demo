"""
ABST Evaluation Framework (Phase 41d v0).

手动评估工具——无自动评分、无 LLM-as-judge、无基于关键字的评判。

behavior_affecting = False。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import ClassVar, Dict, List, Optional


# ── Score enum ────────────────────────────────────────────────────────────────

class AbstScore(IntEnum):
    """ABST 维度分数。"""
    MISSING = 0   # 缺失或不适宜
    PARTIAL = 1   # 部分达标
    FULL = 2      # 完全达标


# ── Dimensions ────────────────────────────────────────────────────────────────

ABST_DIMENSIONS: List[str] = [
    "salience_focus",
    "minimal_cue_inference",
    "unresolvedness_preservation",
    "non_service_posture",
    "anti_overexplanation",
    "chinese_stability",
    "first_person_judgment",
    "anti_psychologizing",
    "anti_project_technicalization",
    "natural_vs_constrained_expression",
]

ABST_DIMENSION_SET: frozenset = frozenset(ABST_DIMENSIONS)
ABST_DIMENSION_COUNT: int = len(ABST_DIMENSIONS)
ABST_MAX_AGGREGATE: int = ABST_DIMENSION_COUNT * 2  # 10 * 2 = 20


# ── Evaluation record ─────────────────────────────────────────────────────────

@dataclass
class AbstEvaluationRecord:
    """单个测试用例的单次评估记录。

    ``behavior_affecting`` 始终为 False。
    """

    model_name: str
    case_id: str
    case_category: str
    prompt_variant: str           # 如 "prompt_state_baseline_v0"
    input_text: str
    output_text: str
    scores: Dict[str, int]        # 维度名称 → 0|1|2
    evaluator_notes: str
    aggregate_score: int = 0      # 总和（最大值 = 20）

    behavior_affecting: ClassVar[bool] = False

    def compute_aggregate(self) -> int:
        """将所有维度分数相加。

        返回：
            int: 维度分总和（0-20）。
        """
        self.aggregate_score = sum(self.scores.values())
        return self.aggregate_score

    def validate_scores(self) -> List[str]:
        """验证分数字典。

        返回：
            list[str]: 错误消息列表。若长度为 0 则为有效。
        """
        errors: list[str] = []

        # 检查分数字典键
        score_keys = set(self.scores.keys())
        expected = ABST_DIMENSION_SET

        if score_keys != expected:
            missing = sorted(expected - score_keys)
            extra = sorted(score_keys - expected)
            if missing:
                errors.append(f"缺少维度: {missing}")
            if extra:
                errors.append(f"多余维度: {extra}")

        # 检查每个分数值
        valid_values = {0, 1, 2}
        for dim, val in self.scores.items():
            if not isinstance(val, int):
                errors.append(
                    f"维度 '{dim}' 的值类型无效: {type(val).__name__} "
                    f"(期望 int，实际为 {val!r})"
                )
            elif val not in valid_values:
                errors.append(
                    f"维度 '{dim}' 的值无效: {val} "
                    f"(仅允许 0/1/2)"
                )

        return errors


# ── Aggregation ──────────────────────────────────────────────────────────────

@dataclass
class AbstAggregation:
    """跨测试用例和维度的聚合结果。

    ``behavior_affecting`` 始终为 False。
    """

    model_name: str
    prompt_variant: str
    total_records: int
    per_case_totals: Dict[str, int]           # case_id → 总分
    per_dimension_averages: Dict[str, float]  # 维度 → 平均分
    overall_average: float
    category_averages: Dict[str, float]       # 类别 → 平均维度分

    behavior_affecting: ClassVar[bool] = False

    @staticmethod
    def aggregate(records: List[AbstEvaluationRecord]) -> "AbstAggregation":
        """确定性聚合。无加权。无 LLM。

        参数：
            records: 评估记录列表。

        返回：
            AbstAggregation: 聚合结果。
        """
        if not records:
            return AbstAggregation(
                model_name="",
                prompt_variant="",
                total_records=0,
                per_case_totals={},
                per_dimension_averages={dim: 0.0 for dim in ABST_DIMENSIONS},
                overall_average=0.0,
                category_averages={},
            )

        model_name = records[0].model_name
        prompt_variant = records[0].prompt_variant
        total_records = len(records)

        # 每个 case 的总分
        per_case_totals: dict[str, int] = {}
        for rec in records:
            if rec.aggregate_score == 0 and rec.scores:
                rec.compute_aggregate()
            per_case_totals[rec.case_id] = rec.aggregate_score

        # 每个维度的平均分
        per_dimension_averages: dict[str, float] = {}
        for dim in ABST_DIMENSIONS:
            dim_sum = sum(rec.scores.get(dim, 0) for rec in records)
            per_dimension_averages[dim] = dim_sum / total_records

        # 总体平均（所有维度分 / (记录数 * 维度数)）
        total_dimension_score = sum(sum(rec.scores.values()) for rec in records)
        overall_average = total_dimension_score / (total_records * ABST_DIMENSION_COUNT)

        # 每个类别的平均维度分
        category_dim_sums: dict[str, list[float]] = {}
        for rec in records:
            cat = rec.case_category
            if cat not in category_dim_sums:
                category_dim_sums[cat] = []
            dim_avg = rec.aggregate_score / ABST_DIMENSION_COUNT
            category_dim_sums[cat].append(dim_avg)

        category_averages: dict[str, float] = {}
        for cat, avgs in category_dim_sums.items():
            category_averages[cat] = sum(avgs) / len(avgs)

        return AbstAggregation(
            model_name=model_name,
            prompt_variant=prompt_variant,
            total_records=total_records,
            per_case_totals=per_case_totals,
            per_dimension_averages=per_dimension_averages,
            overall_average=overall_average,
            category_averages=category_averages,
        )


# ── Fixture loader ───────────────────────────────────────────────────────────

class AbstFixtureLoader:
    """加载并验证 ABST JSONL 夹具。

    ``behavior_affecting`` 始终为 False。
    """

    REQUIRED_FIELDS: ClassVar[list] = [
        "id", "category", "input_text", "expected_salience",
        "bad_patterns", "good_features", "scoring_notes",
    ]

    behavior_affecting: ClassVar[bool] = False

    @staticmethod
    def load(path: str) -> list[dict]:
        """加载并验证每条记录均具有必需字段。

        参数：
            path: JSONL 文件路径。

        返回：
            list[dict]: 解析并验证后的记录列表。

        异常：
            FileNotFoundError: 文件不存在。
            ValueError: 记录缺失必需字段。
        """
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"ABST 夹具文件未找到: {path}")

        records: list[dict] = []
        with open(file_path, "r", encoding="utf-8") as fh:
            for line_num, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as e:
                    raise ValueError(
                        f"第 {line_num} 行 JSON 解析失败: {e}"
                    ) from e

                field_errors = AbstFixtureLoader.validate_record(record)
                if field_errors:
                    record_id = record.get("id", f"第{line_num}行")
                    raise ValueError(
                        f"记录 '{record_id}' 验证失败: {'; '.join(field_errors)}"
                    )
                records.append(record)

        return records

    @staticmethod
    def validate_record(record: dict) -> list[str]:
        """验证单条记录包含所有必需字段。

        参数：
            record: 待验证的字典记录。

        返回：
            list[str]: 错误消息列表。若有效则为空列表。
        """
        errors: list[str] = []
        for field in AbstFixtureLoader.REQUIRED_FIELDS:
            if field not in record:
                errors.append(f"缺少必需字段: '{field}'")
        return errors
