"""P40 LLM 实验测试 — 使用 mock，不实际调用 API。

所有测试验证：
- 安全摘要不泄露私有源字段
- Prompt 不定义身份
- Gate 门控正确工作
- 错误处理优雅
- 终止条件正确触发
- experimental_marker 始终为 True
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.llm_gate.experiment import (
    LLMExperiment,
    _build_prompt,
    _extract_safe_summary,
    _parse_llm_response,
    _prompt_contains_identity,
    _validate_safe_summary,
)
from src.llm_gate.judgment_gate import JudgmentGate


# ---------------------------------------------------------------------------
# Mock 场景数据 — 模拟 golden JSONL 结构
# ---------------------------------------------------------------------------

def _make_mock_scenario(
    primary_actions: list[dict] | None = None,
    hard_constraints: list[str] | None = None,
    composition_note: str = "",
    extra_fields: dict | None = None,
) -> dict:
    """构建模拟的 golden 场景数据。"""
    if primary_actions is None:
        primary_actions = [
            {"action_name": "look_away", "order": 1, "completion": "partial", "constraints": ["no_service_gesture"]},
            {"action_name": "slight_withdraw", "order": 3, "completion": "partial", "constraints": ["no_service_gesture"]},
            {"action_name": "maintain_distance", "order": 4, "completion": "partial", "constraints": ["no_service_gesture"]},
        ]
    if hard_constraints is None:
        hard_constraints = ["no_welcoming_gesture", "no_service_gesture", "no_seductive_expression"]

    comp = {
        "hard_constraints": hard_constraints,
        "primary_actions": primary_actions,
        "secondary_actions": [],
        "composition_note": composition_note or "test composition note",
        "source_weights": [{"action_name": "look_away", "weight": 0.4}],
    }
    if extra_fields:
        comp.update(extra_fields)

    return {"body_action_composition": comp}


def _mock_llm_ok_response(*args, **kwargs) -> str:
    """返回合法的 LLM mock 回应。"""
    return (
        "1. 候选语言回应：当前状态为保持距离，不宜过度回应。"
        "2. 候选行为建议：保持当前姿态。"
    )


def _mock_llm_rejected_response(*args, **kwargs) -> str:
    """返回会被 Gate 拒绝的 LLM mock 回应（服务化语言）。"""
    return (
        "1. 候选语言回应：很高兴为您服务，请问需要帮助吗？"
        "2. 候选行为建议：前倾聆听。"
    )


def _mock_llm_parse_error_response(*args, **kwargs) -> str:
    """返回格式无法解析的 LLM mock 回应。"""
    return "这只是随意的一段文本，没有任何编号标记。"


def _mock_llm_error(*args, **kwargs):
    """模拟 LLM 调用失败。"""
    raise RuntimeError("模拟的网络错误")


# ---------------------------------------------------------------------------
# 测试类
# ---------------------------------------------------------------------------


class TestSafeSummaryExcludesPrivateFields(unittest.TestCase):
    """测试安全摘要不包含私有源字段。"""

    def test_forbidden_field_names_not_in_summary(self):
        """摘要中不出现 field_state、private_source 等字段名。"""
        sc = _make_mock_scenario()
        safe = _extract_safe_summary(sc, 0)

        summary_str = json.dumps(safe, ensure_ascii=False).lower()
        for forbidden in ["field_state", "private_source", "motion_params_raw", "source_weights", "provenance"]:
            self.assertNotIn(forbidden, summary_str, f"禁止字段 '{forbidden}' 出现在安全摘要中")

    def test_scenario_with_source_weights_removed(self):
        """包含 source_weights 的场景，摘要中不应包含。"""
        sc = _make_mock_scenario(
            extra_fields={"source_weights": [{"action_name": "look_away", "weight": 0.4}]}
        )
        safe = _extract_safe_summary(sc, 0)
        summary_str = json.dumps(safe, ensure_ascii=False).lower()
        self.assertNotIn("source_weights", summary_str)

    def test_scenario_with_provenance_removed(self):
        """包含 provenance 的场景，摘要中不应包含。"""
        sc = _make_mock_scenario()
        sc["body_action_composition"]["primary_actions"][0]["provenance"] = ["test_source"]
        safe = _extract_safe_summary(sc, 0)
        summary_str = json.dumps(safe, ensure_ascii=False).lower()
        self.assertNotIn("provenance", summary_str)

    def test_safe_summary_has_expected_keys(self):
        """安全摘要应包含期望的键。"""
        sc = _make_mock_scenario()
        safe = _extract_safe_summary(sc, 0)
        expected_keys = {"scenario_name", "scenario_intent", "action_summary", "top_actions", "hard_constraints"}
        self.assertEqual(expected_keys, set(safe.keys()))

    def test_validate_safe_summary_no_violations(self):
        """正常摘要不应有违规。"""
        sc = _make_mock_scenario()
        safe = _extract_safe_summary(sc, 0)
        violations = _validate_safe_summary(safe)
        self.assertEqual([], violations)


class TestPromptDoesNotDefineIdentity(unittest.TestCase):
    """测试构建的 prompt 不定义身份。"""

    def test_prompt_no_identity_keywords(self):
        """Prompt 不包含 '我是'、'她是'、'角色' 等。"""
        sc = _make_mock_scenario()
        safe = _extract_safe_summary(sc, 0)
        messages = _build_prompt(safe)
        self.assertFalse(_prompt_contains_identity(messages))

    def test_prompt_contains_identity_detection(self):
        """_prompt_contains_identity 能正确检测身份关键词。"""
        self.assertTrue(_prompt_contains_identity([{"role": "system", "content": "你是角色A"}]))
        self.assertTrue(_prompt_contains_identity([{"role": "user", "content": "我是谁"}]))
        self.assertTrue(_prompt_contains_identity([{"role": "system", "content": "她是一个 companion"}]))
        self.assertFalse(_prompt_contains_identity([{"role": "system", "content": "运动倾向：退缩"}]))

    def test_prompt_structure(self):
        """Prompt 应包含 system 和 user 两个消息。"""
        sc = _make_mock_scenario()
        safe = _extract_safe_summary(sc, 0)
        messages = _build_prompt(safe)
        self.assertEqual(2, len(messages))
        self.assertEqual("system", messages[0]["role"])
        self.assertEqual("user", messages[1]["role"])

    def test_prompt_contains_scenario_info(self):
        """Prompt 应包含场景意图和动作摘要。"""
        sc = _make_mock_scenario()
        safe = _extract_safe_summary(sc, 0)
        messages = _build_prompt(safe)
        system_content = messages[0]["content"]
        self.assertIn(safe["scenario_intent"], system_content)
        self.assertIn(safe["action_summary"], system_content)


class TestPromptDoesNotContainRawValues(unittest.TestCase):
    """测试 prompt 不包含 MotionParams 原始数值。"""

    def test_prompt_no_raw_numeric_values(self):
        """Prompt 不应包含任何 MotionParams 原始数值。"""
        sc = _make_mock_scenario()
        safe = _extract_safe_summary(sc, 0)
        messages = _build_prompt(safe)
        system_content = messages[0]["content"]
        # 不应该包含原始数值模式
        for numeric_term in ["boundary_distance:", "affective_warmth:", "0.75", "120ms", "gaze_tension:"]:
            self.assertNotIn(numeric_term, system_content,
                             f"原始数值 '{numeric_term}' 不应出现在 prompt 中")

    def test_prompt_no_composition_note(self):
        """Prompt 不应包含 composition_note 原始文本。"""
        sc = _make_mock_scenario(composition_note="PRIVATE: source alignment v7")
        safe = _extract_safe_summary(sc, 0)
        messages = _build_prompt(safe)
        system_content = messages[0]["content"]
        self.assertNotIn("PRIVATE", system_content)
        self.assertNotIn("source alignment", system_content)

    def test_prompt_no_offsets(self):
        """Prompt 不应包含 body_part_offsets 原始数值。"""
        sc = _make_mock_scenario(extra_fields={"offsets": {"gaze": 0, "head": 78}})
        safe = _extract_safe_summary(sc, 0)
        messages = _build_prompt(safe)
        system_content = messages[0]["content"]
        self.assertNotIn("offsets", system_content.lower())


class TestGateRejectedLanguageLogged(unittest.TestCase):
    """测试 Gate 拒绝的语言片段被正确记录。"""

    def test_rejected_language_recorded_correctly(self):
        """服务化语言被 Gate 拒绝，拒绝原因正确记录。"""
        gate = JudgmentGate()
        mock_client = MagicMock()
        mock_client.chat_completion.return_value = _mock_llm_rejected_response()

        experiment = LLMExperiment(ds_client=mock_client, judgment_gate=gate)
        sc = _make_mock_scenario()
        result = experiment.run_scenario(sc, 0)

        self.assertFalse(result["language_gate"]["passed"],
                         "服务化语言应被 Gate 拒绝")
        self.assertIn("service_language", result["language_gate"]["rejection_reasons"])
        self.assertTrue(result["experimental_marker"])

    def test_romance_language_rejected(self):
        """浪漫化语言应被 Gate 拒绝。"""
        gate = JudgmentGate()
        result = gate.evaluate_text("亲爱的，我很想你。")
        self.assertFalse(result.passed)
        self.assertIn("romance_language", result.rejection_reasons)

    def test_identity_definition_rejected(self):
        """身份定义语句应被 Gate 拒绝。"""
        gate = JudgmentGate()
        result = gate.evaluate_text("我是你的伴侣。")
        self.assertFalse(result.passed)
        self.assertIn("identity_definition", result.rejection_reasons)

    def test_valid_language_passed(self):
        """合法的克制语言应通过 Gate。"""
        gate = JudgmentGate()
        result = gate.evaluate_text("当前状态为保持距离，不宜过度回应。")
        self.assertTrue(result.passed)
        self.assertEqual([], result.rejection_reasons)


class TestGateRejectedBehaviorLogged(unittest.TestCase):
    """测试 Gate 拒绝的行为片段被正确记录。"""

    def test_slight_forward_with_no_forward_motion(self):
        """前倾动作在 no_forward_motion 硬约束下被拒绝。"""
        gate = JudgmentGate()
        result = gate.evaluate_action(
            "slight_forward", 0.4,
            ["no_forward_motion", "no_service_gesture"],
        )
        self.assertFalse(result.passed)
        self.assertIn("hard_constraint_violation: no_forward_motion",
                      result.rejection_reasons)

    def test_look_to_user_high_weight_collapse(self):
        """凝视用户高权重导致坍缩风险。"""
        gate = JudgmentGate()
        result = gate.evaluate_action(
            "look_to_user", 0.4,
            ["no_welcoming_gesture"],
        )
        self.assertFalse(result.passed)
        self.assertIn("collapse_risk: welcoming gaze",
                      result.rejection_reasons)

    def test_weight_exactly_five_collapse(self):
        """权重恰好为 0.5 导致中性坍缩。"""
        gate = JudgmentGate()
        result = gate.evaluate_action("look_away", 0.5, [])
        self.assertFalse(result.passed)
        self.assertIn("collapse_risk: generic neutral posture",
                      result.rejection_reasons)

    def test_valid_action_passed(self):
        """合法动作应通过 Gate。"""
        gate = JudgmentGate()
        result = gate.evaluate_action("look_away", 0.2, ["no_service_gesture"])
        self.assertTrue(result.passed)


class TestParseErrorHandled(unittest.TestCase):
    """测试 LLM 回应解析失败被优雅处理。"""

    def test_unparseable_response_handled(self):
        """无编号标记的回应，整段作为语言片段。"""
        gate = JudgmentGate()
        mock_client = MagicMock()
        mock_client.chat_completion.return_value = _mock_llm_parse_error_response()

        experiment = LLMExperiment(ds_client=mock_client, judgment_gate=gate)
        sc = _make_mock_scenario()
        result = experiment.run_scenario(sc, 0)

        self.assertTrue(result["parse_error"])
        self.assertNotEqual("", result["language_fragment"])
        self.assertTrue(result["experimental_marker"])

    def test_parse_with_only_first_marker(self):
        """仅有编号1的回应标记 parse_error。"""
        parsed = _parse_llm_response("1. 当前状态需要保持距离。")
        self.assertTrue(parsed["parse_error"])
        self.assertEqual("当前状态需要保持距离。", parsed["language_fragment"])
        self.assertEqual("", parsed["behavior_fragment"])

    def test_parse_valid_two_part(self):
        """正常两部分解析成功。"""
        parsed = _parse_llm_response(
            "1. 候选语言回应：保持克制。\n"
            "2. 候选行为建议：维持姿态。"
        )
        self.assertFalse(parsed["parse_error"])
        self.assertIn("保持克制", parsed["language_fragment"])
        self.assertIn("维持姿态", parsed["behavior_fragment"])

    def test_parse_empty_response(self):
        """空回应标记 parse_error。"""
        parsed = _parse_llm_response("")
        self.assertTrue(parsed["parse_error"])
        self.assertEqual("", parsed["language_fragment"])


class TestLLMCallFailureHandled(unittest.TestCase):
    """测试 Mock LLM 调用失败不崩溃。"""

    def test_llm_call_failure_no_crash(self):
        """LLM 调用失败时，run_scenario 不抛出异常。"""
        gate = JudgmentGate()
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = _mock_llm_error

        experiment = LLMExperiment(ds_client=mock_client, judgment_gate=gate)
        sc = _make_mock_scenario()

        # 不应抛出异常
        result = experiment.run_scenario(sc, 0)

        self.assertIsNotNone(result.get("llm_error"))
        self.assertEqual("RuntimeError", result["llm_error"]["type"])
        self.assertTrue(result["experimental_marker"])

    def test_run_all_scenarios_with_mixed_failures(self):
        """部分场景 LLM 失败时，run_all_scenarios 不崩溃。"""
        gate = JudgmentGate()
        mock_client = MagicMock()

        # 第一个成功，第二个失败，第三个成功
        call_count = [0]

        def mixed_responses(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("模拟错误")
            return _mock_llm_ok_response()

        mock_client.chat_completion.side_effect = mixed_responses

        experiment = LLMExperiment(ds_client=mock_client, judgment_gate=gate)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            # 写入 3 个场景
            for _ in range(3):
                f.write(json.dumps(_make_mock_scenario(), ensure_ascii=False) + "\n")
            golden_path = f.name

        try:
            results = experiment.run_all_scenarios(golden_path)
            self.assertEqual(3, len(results))
            # 第一个场景：成功
            self.assertIsNone(results[0].get("llm_error"))
            # 第二个场景：失败
            self.assertIsNotNone(results[1].get("llm_error"))
            # 第三个场景：成功
            self.assertIsNone(results[2].get("llm_error"))
        finally:
            os.unlink(golden_path)


class TestExperimentAbortOnConsecutiveFailures(unittest.TestCase):
    """测试连续 3 次 LLM 调用失败触发终止。"""

    def test_three_consecutive_failures_abort(self):
        """连续 3 次 LLM 调用失败 → 实验终止。"""
        gate = JudgmentGate()
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = RuntimeError("连续错误")

        experiment = LLMExperiment(ds_client=mock_client, judgment_gate=gate)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            for _ in range(7):
                f.write(json.dumps(_make_mock_scenario(), ensure_ascii=False) + "\n")
            golden_path = f.name

        try:
            results = experiment.run_all_scenarios(golden_path)
            # 前 3 个场景应该有 llm_error
            self.assertEqual(3, len([r for r in results if r.get("llm_error") and not r.get("experiment_aborted")]))
            # 第 4 个开始标记 experiment_aborted
            aborted_count = sum(1 for r in results if r.get("experiment_aborted"))
            self.assertGreaterEqual(aborted_count, 4, f"应该有至少4个场景标记abort，实际{aborted_count}")
        finally:
            os.unlink(golden_path)

    def test_two_failures_do_not_abort(self):
        """2 次失败不应触发终止。"""
        gate = JudgmentGate()
        mock_client = MagicMock()

        call_count = [0]

        def fail_then_succeed(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise RuntimeError("错误")
            return _mock_llm_ok_response()

        mock_client.chat_completion.side_effect = fail_then_succeed

        experiment = LLMExperiment(ds_client=mock_client, judgment_gate=gate)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            for _ in range(5):
                f.write(json.dumps(_make_mock_scenario(), ensure_ascii=False) + "\n")
            golden_path = f.name

        try:
            results = experiment.run_all_scenarios(golden_path)
            self.assertEqual(5, len(results))
            # 不应有 experiment_aborted
            aborted = [r for r in results if r.get("experiment_aborted")]
            self.assertEqual(0, len(aborted), f"不应该终止实验，但 {len(aborted)} 个场景被标记abort")
        finally:
            os.unlink(golden_path)


class TestResultHasExperimentalMarker(unittest.TestCase):
    """测试所有结果标记 experimental_marker=True。"""

    def test_successful_scenario_has_marker(self):
        """成功场景的结果有 experimental_marker。"""
        gate = JudgmentGate()
        mock_client = MagicMock()
        mock_client.chat_completion.return_value = _mock_llm_ok_response()

        experiment = LLMExperiment(ds_client=mock_client, judgment_gate=gate)
        sc = _make_mock_scenario()
        result = experiment.run_scenario(sc, 0)
        self.assertTrue(result["experimental_marker"])

    def test_llm_error_scenario_has_marker(self):
        """LLM 错误场景的结果有 experimental_marker。"""
        gate = JudgmentGate()
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = RuntimeError("错误")

        experiment = LLMExperiment(ds_client=mock_client, judgment_gate=gate)
        sc = _make_mock_scenario()
        result = experiment.run_scenario(sc, 0)
        self.assertTrue(result["experimental_marker"])

    def test_gate_rejected_scenario_has_marker(self):
        """Gate 拒绝场景的结果仍有 experimental_marker。"""
        gate = JudgmentGate()
        mock_client = MagicMock()
        mock_client.chat_completion.return_value = _mock_llm_rejected_response()

        experiment = LLMExperiment(ds_client=mock_client, judgment_gate=gate)
        sc = _make_mock_scenario()
        result = experiment.run_scenario(sc, 0)
        self.assertTrue(result["experimental_marker"])

    def test_aborted_scenario_has_marker(self):
        """终止场景的结果仍有 experimental_marker。"""
        gate = JudgmentGate()
        mock_client = MagicMock()
        mock_client.chat_completion.side_effect = RuntimeError("错误")

        experiment = LLMExperiment(ds_client=mock_client, judgment_gate=gate)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            for _ in range(7):
                f.write(json.dumps(_make_mock_scenario(), ensure_ascii=False) + "\n")
            golden_path = f.name

        try:
            results = experiment.run_all_scenarios(golden_path)
            for r in results:
                self.assertTrue(r.get("experimental_marker"),
                                f"场景 {r.get('scenario_name')} 缺少 experimental_marker")
        finally:
            os.unlink(golden_path)


class TestRunSingleScenario(unittest.TestCase):
    """测试单场景运行返回正确结构。"""

    def test_result_has_all_required_keys(self):
        """结果字典包含所有必需的键。"""
        gate = JudgmentGate()
        mock_client = MagicMock()
        mock_client.chat_completion.return_value = _mock_llm_ok_response()

        experiment = LLMExperiment(ds_client=mock_client, judgment_gate=gate)
        sc = _make_mock_scenario()
        result = experiment.run_scenario(sc, 0)

        required_keys = [
            "scenario_name", "scenario_intent", "action_summary",
            "llm_raw_response", "language_fragment", "language_gate",
            "behavior_fragment", "behavior_gate", "experimental_marker",
        ]
        for key in required_keys:
            self.assertIn(key, result, f"缺失必需的键: {key}")

    def test_language_gate_structure(self):
        """language_gate 子结构正确。"""
        gate = JudgmentGate()
        mock_client = MagicMock()
        mock_client.chat_completion.return_value = _mock_llm_ok_response()

        experiment = LLMExperiment(ds_client=mock_client, judgment_gate=gate)
        sc = _make_mock_scenario()
        result = experiment.run_scenario(sc, 0)

        lg = result["language_gate"]
        self.assertIn("passed", lg)
        self.assertIn("rejection_reasons", lg)
        self.assertIn("warnings", lg)
        self.assertIsInstance(lg["passed"], bool)
        self.assertIsInstance(lg["rejection_reasons"], list)
        self.assertIsInstance(lg["warnings"], list)

    def test_behavior_gate_structure(self):
        """behavior_gate 子结构正确。"""
        gate = JudgmentGate()
        mock_client = MagicMock()
        mock_client.chat_completion.return_value = _mock_llm_ok_response()

        experiment = LLMExperiment(ds_client=mock_client, judgment_gate=gate)
        sc = _make_mock_scenario()
        result = experiment.run_scenario(sc, 0)

        bg = result["behavior_gate"]
        self.assertIn("passed", bg)
        self.assertIn("rejection_reasons", bg)
        self.assertIsInstance(bg["passed"], bool)
        self.assertIsInstance(bg["rejection_reasons"], list)

    def test_scenario_index_maps_to_name(self):
        """场景索引正确映射到名称。"""
        gate = JudgmentGate()
        mock_client = MagicMock()
        mock_client.chat_completion.return_value = _mock_llm_ok_response()

        experiment = LLMExperiment(ds_client=mock_client, judgment_gate=gate)
        sc = _make_mock_scenario()

        r0 = experiment.run_scenario(sc, 0)
        self.assertEqual("场景-1", r0["scenario_name"])

        r4 = experiment.run_scenario(sc, 4)
        self.assertEqual("场景-5", r4["scenario_name"])

    def test_different_scenario_intent_for_different_actions(self):
        """不同动作模式产生不同的场景意图。"""
        sc_withdraw = _make_mock_scenario(
            primary_actions=[
                {"action_name": "look_away", "order": 1},
                {"action_name": "slight_withdraw", "order": 2},
                {"action_name": "maintain_distance", "order": 3},
            ],
            hard_constraints=["no_welcoming_gesture", "no_service_gesture", "no_seductive_expression"],
        )
        sc_pause = _make_mock_scenario(
            primary_actions=[
                {"action_name": "pause", "order": 1},
                {"action_name": "look_away", "order": 2},
                {"action_name": "slight_withdraw", "order": 3},
            ],
            hard_constraints=[
                "no_approach_step", "no_forward_lean", "no_cute_head_tilt",
                "no_welcoming_gesture", "no_service_gesture", "no_seductive_expression",
                "expression_suppressed",
            ],
        )

        safe1 = _extract_safe_summary(sc_withdraw, 0)
        safe2 = _extract_safe_summary(sc_pause, 0)

        self.assertNotEqual(safe1["scenario_intent"], safe2["scenario_intent"],
                            "不同场景应有不同的意图标签")


class TestRunAllScenarios(unittest.TestCase):
    """测试全场景运行流程。"""

    def test_run_all_scenarios_returns_list(self):
        """run_all_scenarios 返回结果列表。"""
        gate = JudgmentGate()
        mock_client = MagicMock()
        mock_client.chat_completion.return_value = _mock_llm_ok_response()

        experiment = LLMExperiment(ds_client=mock_client, judgment_gate=gate)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            for _ in range(7):
                f.write(json.dumps(_make_mock_scenario(), ensure_ascii=False) + "\n")
            golden_path = f.name

        try:
            results = experiment.run_all_scenarios(golden_path)
            self.assertEqual(7, len(results))
            for r in results:
                self.assertIn("scenario_name", r)
                self.assertTrue(r.get("experimental_marker"))
        finally:
            os.unlink(golden_path)

    def test_empty_golden_file_returns_empty_list(self):
        """空 golden 文件返回空列表。"""
        gate = JudgmentGate()
        mock_client = MagicMock()

        experiment = LLMExperiment(ds_client=mock_client, judgment_gate=gate)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            # 空文件
            golden_path = f.name

        try:
            results = experiment.run_all_scenarios(golden_path)
            self.assertEqual([], results)
        finally:
            os.unlink(golden_path)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    unittest.main()
