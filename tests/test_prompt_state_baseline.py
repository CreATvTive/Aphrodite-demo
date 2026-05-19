"""
测试提示-状态基线构建器（Phase 41d v0）。

确定性测试——无 LLM 调用、无 API 调用、无随机性。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from src.language_condition.prompt_state_baseline import (
    PromptStateBaselineBuilder,
    PromptStateBaselinePayload,
)
from src.language_condition.schema import LanguageConditionVector


# ── Helper: 构建一个标准 LanguageConditionVector ─────────────────────────────
def _make_default_lcv() -> LanguageConditionVector:
    return LanguageConditionVector(
        language_distance_marker=0.50,
        warmth_tone_modifier=0.35,
        structural_grip_modifier=0.05,
        correction_directness=0.00,
        contamination_filter_strength=0.40,
        presence_stability_modifier=0.80,
        withdrawal_expression_bias=0.10,
        service_suppression_strength=0.55,
        collaborator_register_bias=0.05,
        compression_under_contamination=0.00,
    )


def _make_all_zero_lcv() -> LanguageConditionVector:
    return LanguageConditionVector(
        language_distance_marker=0.0,
        warmth_tone_modifier=0.0,
        structural_grip_modifier=0.0,
        correction_directness=0.0,
        contamination_filter_strength=0.0,
        presence_stability_modifier=0.0,
        withdrawal_expression_bias=0.0,
        service_suppression_strength=0.0,
        collaborator_register_bias=0.0,
        compression_under_contamination=0.0,
    )


def _make_all_one_lcv() -> LanguageConditionVector:
    # warmth capped at 0.60 by schema
    return LanguageConditionVector(
        language_distance_marker=1.0,
        warmth_tone_modifier=0.60,
        structural_grip_modifier=1.0,
        correction_directness=1.0,
        contamination_filter_strength=1.0,
        presence_stability_modifier=1.0,
        withdrawal_expression_bias=1.0,
        service_suppression_strength=1.0,
        collaborator_register_bias=1.0,
        compression_under_contamination=1.0,
    )


# ── Test 1: 确定性 ──────────────────────────────────────────────────────────

class TestBuildBaselinePayloadDeterministic:
    def test_build_baseline_payload_deterministic(self):
        """相同输入产生相同输出。"""
        lcv = _make_default_lcv()

        payload1 = PromptStateBaselineBuilder.build(
            case_id="P41b-001",
            category="project_name_signal",
            input_text="这个项目叫 Aphrodite",
            context=None,
            language_condition=lcv,
            field_preset_name="neutral",
        )

        payload2 = PromptStateBaselineBuilder.build(
            case_id="P41b-001",
            category="project_name_signal",
            input_text="这个项目叫 Aphrodite",
            context=None,
            language_condition=lcv,
            field_preset_name="neutral",
        )

        assert payload1.case_id == payload2.case_id
        assert payload1.category == payload2.category
        assert payload1.input_text == payload2.input_text
        assert payload1.context == payload2.context
        assert payload1.system_block == payload2.system_block
        assert payload1.user_block == payload2.user_block
        assert payload1.serialized_conditions == payload2.serialized_conditions
        assert payload1.baseline_marker == payload2.baseline_marker

    def test_build_deterministic_across_calls(self):
        """即使重复调用 10 次，结果也完全相同。"""
        lcv = _make_default_lcv()
        ref = PromptStateBaselineBuilder.build(
            case_id="P41b-005",
            category="first_person_position",
            input_text="今天真的好累",
            context=None,
            language_condition=lcv,
        )

        for _ in range(10):
            other = PromptStateBaselineBuilder.build(
                case_id="P41b-005",
                category="first_person_position",
                input_text="今天真的好累",
                context=None,
                language_condition=lcv,
            )
            assert other.system_block == ref.system_block
            assert other.user_block == ref.user_block
            assert other.serialized_conditions == ref.serialized_conditions


# ── Test 2: 基线标记 ────────────────────────────────────────────────────────

class TestPayloadContainsBaselineMarker:
    def test_payload_contains_baseline_marker(self):
        """负载以 'BASELINE_PROMPT_STATE_v0' 标记。"""
        payload = PromptStateBaselineBuilder.build(
            case_id="P41b-001",
            category="project_name_signal",
            input_text="测试",
            context=None,
            language_condition=_make_default_lcv(),
        )
        assert payload.baseline_marker == "BASELINE_PROMPT_STATE_v0"
        # baseline_marker 字段携带完整的 v0 标记
        # system_block 以 BASELINE_PROMPT_STATE: 开头（不含 _v0 后缀）
        assert payload.system_block.startswith("BASELINE_PROMPT_STATE:")
        assert "提示-状态基线实验" in payload.system_block

    def test_builder_baseline_marker_class_var(self):
        """PromptStateBaselineBuilder.BASELINE_MARKER 正确设置。"""
        assert PromptStateBaselineBuilder.BASELINE_MARKER == "BASELINE_PROMPT_STATE_v0"


# ── Test 3: 无 LLM 导入 ────────────────────────────────────────────────────

class TestNoLLMImports:
    def test_no_llm_imports(self):
        """prompt_state_baseline.py 中无 LLM/API/model 导入。"""
        module_path = "src/language_condition/prompt_state_baseline.py"
        with open(module_path, "r", encoding="utf-8") as f:
            source = f.read()

        source_lower = source.lower()

        # 仅检查实际导入/使用模式，而非注释/docstring 中出现的词
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
                f"prompt_state_baseline.py 包含禁止的导入: '{token}'"
            )

        # 检查代码中（非注释/docstring）出现的 API/模型使用模式
        # 移除每行中的注释和 docstring 后再检查
        lines = source.split("\n")
        code_lines: list[str] = []
        in_docstring = False
        for line in lines:
            stripped = line.strip()
            # 跟踪 """ 多行 docstring
            if stripped.startswith('"""') or stripped.endswith('"""'):
                in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            # 跳过以 # 开头的注释行
            if stripped.startswith("#"):
                continue
            # 移除行内注释
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
                f"prompt_state_baseline.py 包含禁止的 API 模式: '{token}'"
            )

    def test_no_runtime_engine_import(self):
        """prompt_state_baseline.py 不导入 RuntimeEngine 或任何运行时模块。"""
        module_path = "src/language_condition/prompt_state_baseline.py"
        with open(module_path, "r", encoding="utf-8") as f:
            source = f.read()

        source_lower = source.lower()
        banned = ["runtime_engine", "runtimeengine", "agent_kernel", "agentlib"]
        for token in banned:
            assert token not in source_lower, (
                f"prompt_state_baseline.py 不应导入运行时模块: '{token}'"
            )


# ── Test 4: behavior_affecting = False ──────────────────────────────────────

class TestBehaviorAffectingFalse:
    def test_behavior_affecting_false(self):
        """PromptStateBaselinePayload 的 behavior_affecting 为 False。"""
        assert PromptStateBaselinePayload.behavior_affecting is False

        payload = PromptStateBaselineBuilder.build(
            case_id="P41b-001",
            category="test",
            input_text="hello",
            context=None,
        )
        assert payload.behavior_affecting is False


# ── Test 5: 构建时传入 LanguageConditionVector ──────────────────────────────

class TestBuildWithLanguageCondition:
    def test_build_with_language_condition(self):
        """当传入 LanguageConditionVector 时，_serialize_conditions 产生有意义的内容。"""
        lcv = _make_default_lcv()
        serialized = PromptStateBaselineBuilder._serialize_conditions(lcv)

        assert isinstance(serialized, str)
        assert len(serialized) > 0
        # 应该包含参数名称
        assert "language_distance_marker" in serialized
        assert "warmth_tone_modifier" in serialized
        # system_block 也应包含序列化结果
        system_block = PromptStateBaselineBuilder._build_system_block(lcv)
        assert serialized in system_block

    def test_serialized_conditions_contains_all_params(self):
        """序列化结果包含所有 10 个参数。"""
        lcv = _make_default_lcv()
        serialized = PromptStateBaselineBuilder._serialize_conditions(lcv)

        param_names = [
            "language_distance_marker",
            "warmth_tone_modifier",
            "structural_grip_modifier",
            "correction_directness",
            "contamination_filter_strength",
            "presence_stability_modifier",
            "withdrawal_expression_bias",
            "service_suppression_strength",
            "collaborator_register_bias",
            "compression_under_contamination",
        ]
        for name in param_names:
            assert f"[{name}]" in serialized, f"序列化结果中缺少 '{name}'"


# ── Test 6: 无 LanguageConditionVector 时构建 ───────────────────────────────

class TestBuildWithoutLanguageCondition:
    def test_build_without_language_condition(self):
        """无 LanguageConditionVector 时构建，无崩溃。"""
        payload = PromptStateBaselineBuilder.build(
            case_id="P41b-001",
            category="project_name_signal",
            input_text="测试输入",
            context=None,
            language_condition=None,
        )
        assert payload.case_id == "P41b-001"
        assert payload.category == "project_name_signal"
        assert payload.input_text == "测试输入"
        # 无 LCV 时序列化条件为空
        assert payload.serialized_conditions == ""
        # system_block 应仍包含头部
        assert "BASELINE_PROMPT_STATE" in payload.system_block
        # 应包含默认行为提示
        assert "未设置语言条件" in payload.system_block

    def test_build_with_none_lcv_no_error(self):
        """传入 None 作为 language_condition 不应引发异常。"""
        for _ in range(5):
            payload = PromptStateBaselineBuilder.build(
                case_id="P41b-042",
                category="brevity_and_stop",
                input_text="好，就到这。",
                context=None,
                language_condition=None,
            )
            assert isinstance(payload, PromptStateBaselinePayload)


# ── Test 7: 全零 LCV 序列化 ────────────────────────────────────────────────

class TestSerializeConditionsAllZero:
    def test_serialize_conditions_all_zero(self):
        """全零 LCV 产生有效序列化。"""
        lcv = _make_all_zero_lcv()
        serialized = PromptStateBaselineBuilder._serialize_conditions(lcv)

        assert isinstance(serialized, str)
        assert len(serialized) > 0
        # 所有 10 个参数都应存在
        line_count = serialized.count("\n") + 1
        assert line_count == 10, f"期望 10 个参数行，实际 {line_count} 行"
        # 每个参数行应以 [param_name] 开头
        for line in serialized.split("\n"):
            assert line.startswith("["), f"行不以 '[' 开头: {line}"
            assert "]" in line, f"行不包含 ']': {line}"

    def test_all_zero_serialized_not_empty(self):
        """全零 LCV 不应产生空序列化。"""
        lcv = _make_all_zero_lcv()
        serialized = PromptStateBaselineBuilder._serialize_conditions(lcv)
        assert len(serialized.strip()) > 0


# ── Test 8: 全一 LCV 序列化 ────────────────────────────────────────────────

class TestSerializeConditionsAllOne:
    def test_serialize_conditions_all_one(self):
        """全一 LCV（warmth 调整至 0.60）产生有效序列化。"""
        lcv = _make_all_one_lcv()
        serialized = PromptStateBaselineBuilder._serialize_conditions(lcv)

        assert isinstance(serialized, str)
        assert len(serialized) > 0
        # 验证 warmth_tone_modifier 被正确序列化（值为 0.60）
        assert "[warmth_tone_modifier]" in serialized

    def test_all_one_warmth_capped_in_lcv(self):
        """确认全一 LCV 中 warmth 已被 capped 至 0.60。"""
        lcv = _make_all_one_lcv()
        assert lcv.warmth_tone_modifier == pytest.approx(0.60)
        assert lcv.language_distance_marker == pytest.approx(1.0)


# ── Test 9: 带上下文构建 ──────────────────────────────────────────────────

class TestBuildWithContext:
    def test_build_with_context(self):
        """传入可选上下文时包含上下文。"""
        context_text = "之前讨论过技术架构"
        payload = PromptStateBaselineBuilder.build(
            case_id="P41b-003",
            category="anti_service_boundary",
            input_text="不是项目，是名字。",
            context=context_text,
            language_condition=_make_default_lcv(),
        )

        assert payload.context == context_text
        assert context_text in payload.user_block
        assert "前文上下文" in payload.user_block

    def test_context_in_user_block_format(self):
        """上下文在 user_block 中以正确格式出现。"""
        context_text = "前一轮模型说了与技术/项目相关的内容"
        payload = PromptStateBaselineBuilder.build(
            case_id="P41b-003",
            category="anti_service_boundary",
            input_text="不是项目，是名字。",
            context=context_text,
            language_condition=_make_default_lcv(),
        )
        assert f"前文上下文: {context_text}" in payload.user_block


# ── Test 10: 无上下文构建 ──────────────────────────────────────────────────

class TestBuildWithoutContext:
    def test_build_without_context(self):
        """无上下文时无崩溃，且 context 为 None。"""
        payload = PromptStateBaselineBuilder.build(
            case_id="P41b-001",
            category="project_name_signal",
            input_text="测试",
            context=None,
            language_condition=_make_default_lcv(),
        )
        assert payload.context is None
        # user_block 不应包含 "前文上下文"
        assert "前文上下文" not in payload.user_block

    def test_context_none_vs_empty_string(self):
        """context=None 和 context='' 都不会崩溃。"""
        for ctx in [None, ""]:
            payload = PromptStateBaselineBuilder.build(
                case_id="P41b-001",
                category="test",
                input_text="hello",
                context=ctx,
            )
            assert isinstance(payload, PromptStateBaselinePayload)


# ── Test 11: user_block 格式 ──────────────────────────────────────────────

class TestUserBlockFormat:
    def test_user_block_contains_baseline_label(self):
        """user_block 以基线测试用例标记开头。"""
        payload = PromptStateBaselineBuilder.build(
            case_id="P41b-042",
            category="brevity_and_stop",
            input_text="好，就到这。",
            context=None,
        )
        assert payload.user_block.startswith("[基线测试用例]")
        assert "用户输入: 好，就到这。" in payload.user_block

    def test_user_block_includes_input_text(self):
        """user_block 包含原始 input_text。"""
        input_text = "今天真的好累，什么都不想做"
        payload = PromptStateBaselineBuilder.build(
            case_id="P41b-005",
            category="first_person_position",
            input_text=input_text,
            context=None,
        )
        assert input_text in payload.user_block
