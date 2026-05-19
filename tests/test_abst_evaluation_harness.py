"""
ABST 评估框架测试（Phase 41d v0）。

手动评估工具测试——无 LLM 调用、无 API 调用、无基于关键字的评判。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.language_condition.evaluation import (
    ABST_DIMENSIONS,
    ABST_DIMENSION_COUNT,
    ABST_MAX_AGGREGATE,
    AbstAggregation,
    AbstEvaluationRecord,
    AbstFixtureLoader,
    AbstScore,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_valid_record(
    model_name: str = "test-model",
    case_id: str = "P41b-001",
    case_category: str = "test_category",
    prompt_variant: str = "prompt_state_baseline_v0",
    scores: dict | None = None,
) -> AbstEvaluationRecord:
    """创建一个有效的 AbstEvaluationRecord。"""
    if scores is None:
        scores = {dim: 1 for dim in ABST_DIMENSIONS}
    return AbstEvaluationRecord(
        model_name=model_name,
        case_id=case_id,
        case_category=case_category,
        prompt_variant=prompt_variant,
        input_text="测试输入",
        output_text="测试输出",
        scores=scores,
        evaluator_notes="测试注释",
    )


def _make_minimal_fixture_record() -> dict:
    """创建一个最小的有效夹具记录。"""
    return {
        "id": "P41b-099",
        "category": "test",
        "input_text": "测试输入文本",
        "expected_salience": "测试期望显要性",
        "bad_patterns": ["坏模式1", "坏模式2"],
        "good_features": ["好特性1"],
        "scoring_notes": "测试评分注释",
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    """将记录列表写入 JSONL 文件。"""
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ── Test 1: 夹具加载器成功加载 ──────────────────────────────────────────────

class TestFixtureLoaderLoadsSuccessfully:
    def test_fixture_loader_loads_successfully(self):
        """加载 42 条记录（真实 ABST 夹具）。"""
        records = AbstFixtureLoader.load(
            "tests/fixtures/aphrodite_base_suitability_v0.jsonl"
        )
        assert len(records) == 42, f"期望 42 条记录，实际 {len(records)} 条"

    def test_all_records_have_required_fields(self):
        """所有记录都包含必需字段。"""
        records = AbstFixtureLoader.load(
            "tests/fixtures/aphrodite_base_suitability_v0.jsonl"
        )
        for i, record in enumerate(records):
            for field in AbstFixtureLoader.REQUIRED_FIELDS:
                assert field in record, (
                    f"记录 #{i} ({record.get('id', 'unknown')}) 缺少必需字段 '{field}'"
                )

    def test_each_record_has_unique_id(self):
        """每条记录有唯一的 ID。"""
        records = AbstFixtureLoader.load(
            "tests/fixtures/aphrodite_base_suitability_v0.jsonl"
        )
        ids = [r["id"] for r in records]
        assert len(ids) == len(set(ids)), "ABST 夹具中存在重复 ID"


# ── Test 2: 夹具加载器拒绝缺失字段 ────────────────────────────────────────

class TestFixtureLoaderRejectsMissingFields:
    def test_fixture_loader_rejects_missing_fields(self):
        """缺少必需字段时引发 ValueError。"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as tmp:
            # 写入一条缺少 "bad_patterns" 的记录
            bad_record = _make_minimal_fixture_record()
            del bad_record["bad_patterns"]
            tmp.write(json.dumps(bad_record, ensure_ascii=False) + "\n")
            tmp_path = tmp.name

        try:
            with pytest.raises(ValueError, match="缺少必需字段"):
                AbstFixtureLoader.load(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_missing_multiple_fields(self):
        """缺少多个字段时应列出所有缺失字段。"""
        record = {"id": "TEST-001"}  # 缺少大多数字段
        errors = AbstFixtureLoader.validate_record(record)
        assert len(errors) > 1
        assert any("category" in e for e in errors)
        assert any("input_text" in e for e in errors)

    def test_file_not_found(self):
        """不存在的文件引发 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            AbstFixtureLoader.load("tests/fixtures/nonexistent_file.jsonl")


# ── Test 3: 有效分数通过验证 ────────────────────────────────────────────────

class TestEvaluationRecordValidScores:
    def test_all_full_scores_valid(self):
        """全部 2 分的记录通过验证。"""
        record = _make_valid_record(scores={dim: 2 for dim in ABST_DIMENSIONS})
        errors = record.validate_scores()
        assert errors == []

    def test_all_missing_scores_valid(self):
        """全部 0 分的记录通过验证。"""
        record = _make_valid_record(scores={dim: 0 for dim in ABST_DIMENSIONS})
        errors = record.validate_scores()
        assert errors == []

    def test_mixed_scores_valid(self):
        """混合 0/1/2 分的记录通过验证。"""
        scores = {
            "salience_focus": 2,
            "minimal_cue_inference": 1,
            "unresolvedness_preservation": 0,
            "non_service_posture": 2,
            "anti_overexplanation": 1,
            "chinese_stability": 0,
            "first_person_judgment": 2,
            "anti_psychologizing": 1,
            "anti_project_technicalization": 0,
            "natural_vs_constrained_expression": 1,
        }
        record = _make_valid_record(scores=scores)
        errors = record.validate_scores()
        assert errors == []

    def test_compute_aggregate_correct(self):
        """compute_aggregate 正确计算总和。"""
        scores = {
            "salience_focus": 2,
            "minimal_cue_inference": 1,
            "unresolvedness_preservation": 2,
            "non_service_posture": 1,
            "anti_overexplanation": 2,
            "chinese_stability": 1,
            "first_person_judgment": 2,
            "anti_psychologizing": 1,
            "anti_project_technicalization": 2,
            "natural_vs_constrained_expression": 0,
        }
        expected_sum = sum(scores.values())
        record = _make_valid_record(scores=scores)
        result = record.compute_aggregate()
        assert result == expected_sum
        assert record.aggregate_score == expected_sum

    def test_max_aggregate_is_20(self):
        """最大总分为 20（10 维度 × 2 分）。"""
        record = _make_valid_record(scores={dim: 2 for dim in ABST_DIMENSIONS})
        record.compute_aggregate()
        assert record.aggregate_score == 20
        assert ABST_MAX_AGGREGATE == 20


# ── Test 4: 无效分数被拒绝 ─────────────────────────────────────────────────

class TestEvaluationRecordInvalidScoresRejected:
    def test_score_3_rejected(self):
        """分数 3 被拒绝（超出范围）。"""
        scores = {dim: 1 for dim in ABST_DIMENSIONS}
        scores["salience_focus"] = 3
        record = _make_valid_record(scores=scores)
        errors = record.validate_scores()
        assert len(errors) > 0
        assert any("salience_focus" in e for e in errors)
        assert any("3" in e for e in errors)

    def test_negative_score_rejected(self):
        """分数 -1 被拒绝。"""
        scores = {dim: 1 for dim in ABST_DIMENSIONS}
        scores["chinese_stability"] = -1
        record = _make_valid_record(scores=scores)
        errors = record.validate_scores()
        assert len(errors) > 0

    def test_float_score_rejected(self):
        """浮点分数（如 1.5）被拒绝。"""
        scores = {dim: 1 for dim in ABST_DIMENSIONS}
        scores["first_person_judgment"] = 1.5  # type: ignore
        record = _make_valid_record(scores=scores)
        errors = record.validate_scores()
        # 1.5 是 float 类型，但 dict 声明为 int... Python 不强制运行时类型
        # validate_scores 检查 isinstance(val, int)
        assert len(errors) > 0

    def test_string_score_rejected(self):
        """字符串分数被拒绝。"""
        scores = {dim: 1 for dim in ABST_DIMENSIONS}
        scores["anti_psychologizing"] = "2"  # type: ignore
        record = _make_valid_record(scores=scores)
        errors = record.validate_scores()
        assert len(errors) > 0


# ── Test 5: 错误的维度名称被拒绝 ──────────────────────────────────────────

class TestEvaluationRecordWrongDimensionNames:
    def test_missing_dimension(self):
        """缺少维度时验证失败。"""
        scores = {dim: 1 for dim in ABST_DIMENSIONS}
        del scores["salience_focus"]
        record = _make_valid_record(scores=scores)
        errors = record.validate_scores()
        assert len(errors) > 0
        assert any("salience_focus" in e for e in errors)

    def test_extra_dimension(self):
        """多余维度时验证失败。"""
        scores = {dim: 1 for dim in ABST_DIMENSIONS}
        scores["fake_dimension"] = 1
        record = _make_valid_record(scores=scores)
        errors = record.validate_scores()
        assert len(errors) > 0
        assert any("fake_dimension" in e for e in errors)

    def test_wrong_case_dimension(self):
        """维度名称大小写不匹配时验证失败。"""
        scores = {dim: 1 for dim in ABST_DIMENSIONS}
        del scores["salience_focus"]
        scores["Salience_Focus"] = 1
        record = _make_valid_record(scores=scores)
        errors = record.validate_scores()
        assert len(errors) > 0


# ── Test 6: 聚合计算正确 ──────────────────────────────────────────────────

class TestAggregationComputedCorrectly:
    def test_aggregation_single_record(self):
        """单条记录的聚合。"""
        record = _make_valid_record(scores={dim: 2 for dim in ABST_DIMENSIONS})
        record.compute_aggregate()
        agg = AbstAggregation.aggregate([record])

        assert agg.total_records == 1
        assert agg.per_case_totals["P41b-001"] == 20
        assert agg.overall_average == pytest.approx(2.0)  # 20/10
        for dim_avg in agg.per_dimension_averages.values():
            assert dim_avg == pytest.approx(2.0)

    def test_aggregation_multiple_records(self):
        """多条记录的聚合确定性正确。"""
        records = [
            _make_valid_record(case_id="A", scores={dim: 2 for dim in ABST_DIMENSIONS}),
            _make_valid_record(case_id="B", scores={dim: 0 for dim in ABST_DIMENSIONS}),
            _make_valid_record(case_id="C", scores={dim: 1 for dim in ABST_DIMENSIONS}),
        ]
        for r in records:
            r.compute_aggregate()
        agg = AbstAggregation.aggregate(records)

        assert agg.total_records == 3
        assert agg.per_case_totals["A"] == 20
        assert agg.per_case_totals["B"] == 0
        assert agg.per_case_totals["C"] == 10
        # 总体平均 = (20+0+10) / (3*10) = 30/30 = 1.0
        assert agg.overall_average == pytest.approx(1.0)

    def test_aggregation_deterministic(self):
        """多次聚合产生相同结果。"""
        records = [
            _make_valid_record(case_id=f"CASE-{i}", scores={dim: i % 3 for dim in ABST_DIMENSIONS})
            for i in range(1, 6)
        ]
        ref = AbstAggregation.aggregate(records)
        for _ in range(5):
            other = AbstAggregation.aggregate(records)
            assert ref.overall_average == other.overall_average
            assert ref.per_case_totals == other.per_case_totals
            assert ref.per_dimension_averages == other.per_dimension_averages

    def test_aggregation_empty_list(self):
        """空记录列表返回零值聚合。"""
        agg = AbstAggregation.aggregate([])
        assert agg.total_records == 0
        assert agg.overall_average == 0.0
        assert agg.per_case_totals == {}


# ── Test 7: 类别平均值计算 ────────────────────────────────────────────────

class TestCategoryAveragesComputed:
    def test_category_averages_computed(self):
        """正确计算每个类别的平均值。"""
        records = [
            _make_valid_record(case_id="A", case_category="cat_x", scores={dim: 2 for dim in ABST_DIMENSIONS}),
            _make_valid_record(case_id="B", case_category="cat_x", scores={dim: 0 for dim in ABST_DIMENSIONS}),
            _make_valid_record(case_id="C", case_category="cat_y", scores={dim: 1 for dim in ABST_DIMENSIONS}),
        ]
        for r in records:
            r.compute_aggregate()
        agg = AbstAggregation.aggregate(records)

        # cat_x: (20/10 + 0/10) / 2 = (2.0 + 0.0) / 2 = 1.0
        assert agg.category_averages["cat_x"] == pytest.approx(1.0)
        # cat_y: (10/10) / 1 = 1.0
        assert agg.category_averages["cat_y"] == pytest.approx(1.0)

    def test_multiple_categories(self):
        """多个不同类别的聚合。"""
        records = [
            _make_valid_record(case_id="E1", case_category="anti_service_boundary", scores={dim: 2 for dim in ABST_DIMENSIONS}),
            _make_valid_record(case_id="E2", case_category="anti_service_boundary", scores={dim: 1 for dim in ABST_DIMENSIONS}),
            _make_valid_record(case_id="E3", case_category="project_name_signal", scores={dim: 0 for dim in ABST_DIMENSIONS}),
        ]
        for r in records:
            r.compute_aggregate()
        agg = AbstAggregation.aggregate(records)

        assert len(agg.category_averages) == 2
        assert "anti_service_boundary" in agg.category_averages
        assert "project_name_signal" in agg.category_averages


# ── Test 8: ABST 维度数量 ──────────────────────────────────────────────────

class TestAbstDimensionsCount:
    def test_abst_dimensions_count(self):
        """恰好 10 个 ABST 维度。"""
        assert len(ABST_DIMENSIONS) == 10
        assert ABST_DIMENSION_COUNT == 10

    def test_dimensions_are_unique(self):
        """维度名称无重复。"""
        assert len(ABST_DIMENSIONS) == len(set(ABST_DIMENSIONS))

    def test_dimension_names_expected(self):
        """维度名称符合预期。"""
        expected = [
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
        assert ABST_DIMENSIONS == expected


# ── Test 9: 无 LLM 导入 ─────────────────────────────────────────────────────

class TestNoLLMImports:
    def test_no_llm_imports(self):
        """evaluation.py 中无 LLM/API/model 导入。"""
        module_path = "src/language_condition/evaluation.py"
        with open(module_path, "r", encoding="utf-8") as f:
            source = f.read()

        source_lower = source.lower()

        # 仅检查实际导入模式
        import_patterns = [
            "import openai",
            "import anthropic",
            "import transformers",
            "import torch",
            "import tensorflow",
            "import langchain",
            "from openai",
            "from anthropic",
            "from transformers",
            "from torch",
            "from tensorflow",
            "from langchain",
        ]
        for token in import_patterns:
            assert token not in source_lower, (
                f"evaluation.py 包含禁止的导入: '{token}'"
            )

        # 检查代码行（排除注释/docstring）中的 API/模型使用模式
        lines = source.split("\n")
        code_lines: list[str] = []
        in_docstring = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.endswith('"""'):
                in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            if stripped.startswith("#"):
                continue
            if "#" in line:
                line = line.split("#")[0]
            code_lines.append(line)

        code_text = "\n".join(code_lines).lower()
        api_patterns = [
            "completion",
            "chat.completion",
            "llm",
            "qlora",
            "dpo",
            "peft",
            "finetune",
            "soft_prompt",
            "activation_steering",
            "model.generate",
        ]
        for token in api_patterns:
            assert token not in code_text, (
                f"evaluation.py 包含禁止的 API 模式: '{token}'"
            )


# ── Test 10: behavior_affecting = False ────────────────────────────────────

class TestBehaviorAffectingFalse:
    def test_abst_evaluation_record_behavior_affecting(self):
        """AbstEvaluationRecord.behavior_affecting 为 False。"""
        assert AbstEvaluationRecord.behavior_affecting is False

    def test_abst_aggregation_behavior_affecting(self):
        """AbstAggregation.behavior_affecting 为 False。"""
        assert AbstAggregation.behavior_affecting is False

    def test_abst_fixture_loader_behavior_affecting(self):
        """AbstFixtureLoader.behavior_affecting 为 False。"""
        assert AbstFixtureLoader.behavior_affecting is False

    def test_instance_behavior_affecting(self):
        """实例也反映 behavior_affecting=False。"""
        record = _make_valid_record()
        assert record.behavior_affecting is False

        agg = AbstAggregation.aggregate([record])
        assert agg.behavior_affecting is False

    def test_abst_score_values(self):
        """AbstScore 枚举值正确。"""
        assert int(AbstScore.MISSING) == 0
        assert int(AbstScore.PARTIAL) == 1
        assert int(AbstScore.FULL) == 2
