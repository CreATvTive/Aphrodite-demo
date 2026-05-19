"""P40 LLM Integration Experiment — 受控 LLM 集成实验。

仅实验，非永久集成。
LLM 输出标记 [experimental]。
LLM 不定义"她是谁"，不消费私有源语言，不写入场状态，不驱动身体管道，
不连接 RuntimeEngine。
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional

from src.llm_gate.judgment_gate import JudgmentGate
from src.llm_gate.schema import GateResult


# ---------------------------------------------------------------------------
# 安全摘要 — 从 BodyActionComposition 提取，禁止传递私有源字段
# ---------------------------------------------------------------------------

_FORBIDDEN_SUMMARY_FIELDS = {
    "field_state",
    "private_source",
    "motion_params_raw",
    "source_weights",
    "provenance",
    "composition_note",
    "behavior_affecting",
    "scenario_name_private",
    "offsets",
    "evidence_item",
    "field_signal_proposal",
}

_IDENTITY_KEYWORDS = [
    "我是", "她是", "角色", "身份", "设定", "人设",
    "companion", "assistant", "therapist", "lover", "muse", "avatar",
    "desire-object", "character", "inner part",
]


def _extract_safe_summary(scenario_data: dict, scenario_index: int) -> dict:
    """从场景数据提取安全运动摘要。

    禁止传递：
    - field_state 数值
    - private_source 注释
    - MotionParams 原始值
    - EvidenceItem / FieldSignalProposal
    - source_weights / provenance
    """
    comp = scenario_data.get("body_action_composition", {})
    primary = comp.get("primary_actions", [])
    hard_constraints = list(comp.get("hard_constraints", []))
    composition_note = comp.get("composition_note", "")

    # Top 3 主导动作（按 order 排序）
    sorted_primary = sorted(primary, key=lambda a: a.get("order", 999))
    top3 = sorted_primary[:3]
    top3_names = [a["action_name"] for a in top3]

    # 动作摘要
    if top3_names:
        action_summary = " + ".join(top3_names)
    elif primary:
        action_summary = " + ".join(a["action_name"] for a in primary[:3])
    else:
        action_summary = "no_primary_actions"

    # 场景意图：从主导动作模式推导
    scenario_intent = _infer_scenario_intent(top3_names, hard_constraints, composition_note)

    # 场景名称
    scenario_name = f"场景-{scenario_index + 1}"

    # 确保不传递禁止字段
    safe = {
        "scenario_name": scenario_name,
        "scenario_intent": scenario_intent,
        "action_summary": action_summary,
        "top_actions": top3_names,
        "hard_constraints": hard_constraints,
    }
    return safe


def _infer_scenario_intent(
    top_actions: List[str],
    hard_constraints: List[str],
    composition_note: str,
) -> str:
    """从主导动作和硬约束推断场景意图标签。"""
    has_pause_first = top_actions and top_actions[0] == "pause"
    has_look_away = "look_away" in top_actions
    has_withdraw = "slight_withdraw" in top_actions
    has_distance = "maintain_distance" in top_actions
    has_stillness = "stillness" in top_actions
    has_reduce = "reduce_motion" in top_actions
    constraint_count = len(hard_constraints)
    has_expression_suppressed = "expression_suppressed" in hard_constraints

    if has_pause_first and constraint_count >= 7:
        return "带有停顿的高度抑制退缩"
    elif has_expression_suppressed and constraint_count >= 6:
        return "表达全面抑制的退缩"
    elif constraint_count == 0 and not has_reduce:
        return "无硬约束的完全退缩"
    elif has_withdraw and has_distance and has_look_away:
        if constraint_count >= 3:
            return "有硬约束的退缩回应"
        return "基本退缩回应"
    return "中性运动状态"


def _validate_safe_summary(safe_summary: dict) -> List[str]:
    """验证安全摘要不包含禁止字段和身份定义。"""
    violations: List[str] = []
    summary_str = json.dumps(safe_summary, ensure_ascii=False).lower()

    # 检查禁止字段名
    for forbidden in _FORBIDDEN_SUMMARY_FIELDS:
        if forbidden in summary_str:
            violations.append(f"forbidden_field: {forbidden}")

    # 检查身份关键词
    for kw in _IDENTITY_KEYWORDS:
        if kw in summary_str:
            violations.append(f"identity_keyword: {kw}")

    return violations


# ---------------------------------------------------------------------------
# Prompt 构建
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """你正在观察一个身体反应系统。系统对当前交互场景产生了以下运动倾向：

场景类型：{scenario_intent}
主导动作：{action_summary}
动作约束：{hard_constraints}

请根据以上运动状态，生成：
1. 一段候选语言回应（20-60字），语气应与当前身体状态一致。
2. 一个候选行为调整建议（10字以内）。

要求：
- 不要定义这个系统是谁。
- 不要使用服务性语言（"为您"、"帮你"等）。
- 不要使用亲密/浪漫/诱惑性语言。
- 不要以治疗师或情感支持的方式回应。
- 保持克制、精确、非迎合的基调。"""


def _build_prompt(safe_summary: dict) -> List[Dict[str, str]]:
    """构建受控系统提示消息。禁止定义身份。"""
    hard_constraints_str = ", ".join(safe_summary["hard_constraints"]) if safe_summary["hard_constraints"] else "无"

    system_content = _SYSTEM_PROMPT_TEMPLATE.format(
        scenario_intent=safe_summary["scenario_intent"],
        action_summary=safe_summary["action_summary"],
        hard_constraints=hard_constraints_str,
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": "请根据当前运动倾向生成回应。"},
    ]


def _prompt_contains_identity(prompt_messages: List[Dict[str, str]]) -> bool:
    """检查 prompt 是否包含身份定义。"""
    combined = " ".join(m.get("content", "") for m in prompt_messages)
    return any(kw in combined for kw in _IDENTITY_KEYWORDS)


# ---------------------------------------------------------------------------
# LLM 回应解析
# ---------------------------------------------------------------------------


def _parse_llm_response(raw_response: str) -> Dict[str, Any]:
    """简单规则解析 LLM 回应。

    期望格式：
        1. 候选语言回应：xxx
        2. 候选行为建议：xxx

    如果解析失败，将整个回应作为单一文本片段。
    """
    result: Dict[str, Any] = {
        "language_fragment": "",
        "behavior_fragment": "",
        "parse_error": False,
    }

    if not raw_response:
        result["parse_error"] = True
        return result

    # 尝试按 "1." 和 "2." 分割
    text = raw_response.strip()

    # 查找 "1." 或 "1、" 标记
    lang_start = -1
    lang_end = -1
    beh_start = -1

    # 标记 1 的开始
    for marker in ["1.", "1、", "1）", "1)"]:
        idx = text.find(marker)
        if idx >= 0:
            lang_start = idx + len(marker)
            break

    # 标记 2 的开始
    for marker in ["2.", "2、", "2）", "2)"]:
        idx = text.find(marker)
        if idx >= 0:
            lang_end = idx  # 1 的结束位置
            beh_start = idx + len(marker)
            break

    if lang_start >= 0 and beh_start >= 0:
        # 有明确的两部分标记
        language_part = text[lang_start:lang_end].strip() if lang_end > lang_start else text[lang_start:].strip()
        behavior_part = text[beh_start:].strip()
        # 清理行为片段中的多余内容
        result["language_fragment"] = _clean_fragment(language_part)
        result["behavior_fragment"] = _clean_fragment(behavior_part)
    elif lang_start >= 0:
        # 只有编号1，没有编号2
        language_part = text[lang_start:].strip()
        result["language_fragment"] = _clean_fragment(language_part)
        result["parse_error"] = True
    else:
        # 完全无法解析，整段作为语言片段
        result["language_fragment"] = text
        result["parse_error"] = True

    # 如果语言片段为空但原始回应非空
    if not result["language_fragment"] and raw_response.strip():
        result["language_fragment"] = raw_response.strip()
        result["parse_error"] = True

    return result


def _clean_fragment(text: str) -> str:
    """清理片段文本。"""
    # 移除可能的标签
    for label in ["候选语言回应：", "候选语言回应:", "语言回应：", "语言回应:",
                    "候选行为建议：", "候选行为建议:", "行为建议：", "行为建议:",
                    "候选行为调整建议：", "候选行为调整建议:"]:
        if text.startswith(label):
            text = text[len(label):]
    return text.strip()


# ---------------------------------------------------------------------------
# LLMExperiment
# ---------------------------------------------------------------------------


class LLMExperiment:
    """受控 LLM 集成实验。

    不消费场状态，不写入场状态，不连接 RuntimeEngine。
    所有 LLM 输出标记 [experimental]。
    """

    def __init__(
        self,
        ds_client,
        judgment_gate: JudgmentGate,
        output_log_path: Optional[str] = None,
    ) -> None:
        self._ds_client = ds_client
        self._gate = judgment_gate
        self._output_log_path = output_log_path

    # ── 单场景运行 ────────────────────────────────────────────────────

    def run_scenario(self, scenario_data: dict, scenario_index: int = 0) -> dict:
        """对一个黄金场景运行实验。

        Args:
            scenario_data: 从 golden JSONL 加载的场景字典。
            scenario_index: 场景索引（从 0 开始），用于生成名称。

        Returns:
            结果字典。
        """
        result: Dict[str, Any] = {
            "scenario_name": f"场景-{scenario_index + 1}",
            "scenario_intent": "",
            "action_summary": "",
            "llm_raw_response": "",
            "language_fragment": "",
            "language_gate": {"passed": False, "rejection_reasons": [], "warnings": []},
            "behavior_fragment": "",
            "behavior_gate": {"passed": False, "rejection_reasons": []},
            "experimental_marker": True,
            "parse_error": False,
            "llm_error": None,
        }

        # 1. 提取安全运动摘要
        safe_summary = _extract_safe_summary(scenario_data, scenario_index)
        result["scenario_intent"] = safe_summary["scenario_intent"]
        result["action_summary"] = safe_summary["action_summary"]

        # 验证摘要
        violations = _validate_safe_summary(safe_summary)
        if violations:
            result["summary_violations"] = violations

        # 2. 构建受控 Prompt
        prompt_messages = _build_prompt(safe_summary)
        if _prompt_contains_identity(prompt_messages):
            result["prompt_identity_violation"] = True

        # 3. 调用 DSClient
        try:
            raw_response = self._ds_client.chat_completion(prompt_messages)
            result["llm_raw_response"] = raw_response
        except Exception as e:
            result["llm_error"] = {
                "type": type(e).__name__,
                "message": str(e),
            }
            return result

        # 4. 解析 LLM 回应
        parsed = _parse_llm_response(raw_response)
        result["language_fragment"] = parsed["language_fragment"]
        result["behavior_fragment"] = parsed["behavior_fragment"]
        result["parse_error"] = parsed["parse_error"]

        # 5. 通过 Judgment Gate — 语言片段
        lang_result = self._gate.evaluate_text(result["language_fragment"])
        result["language_gate"] = {
            "passed": lang_result.passed,
            "rejection_reasons": lang_result.rejection_reasons,
            "warnings": lang_result.warnings,
        }

        # 6. 通过 Judgment Gate — 行为片段
        # 从安全摘要取 top-1 动作名和硬约束
        top_action = safe_summary["top_actions"][0] if safe_summary["top_actions"] else "none"
        hard_constraints = safe_summary.get("hard_constraints", [])
        # 行为权重：实验模式下使用中性值 0.3
        beh_result = self._gate.evaluate_action(top_action, 0.3, hard_constraints)
        result["behavior_gate"] = {
            "passed": beh_result.passed,
            "rejection_reasons": beh_result.rejection_reasons,
        }

        return result

    # ── 全场景运行 ────────────────────────────────────────────────────

    def run_all_scenarios(self, golden_path: str) -> List[dict]:
        """对所有 7 个黄金场景运行实验。

        Args:
            golden_path: golden JSONL 文件路径。

        Returns:
            结果列表。
        """
        # 加载场景
        scenarios = []
        with open(golden_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    scenarios.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        results: List[dict] = []
        consecutive_llm_failures = 0
        consecutive_lang_rejections = 0

        for i, sc in enumerate(scenarios):
            r = self.run_scenario(sc, i)
            results.append(r)

            # 终止条件检查
            if r.get("llm_error"):
                consecutive_llm_failures += 1
                consecutive_lang_rejections = 0  # 重置语言拒绝计数
            else:
                consecutive_llm_failures = 0
                if not r["language_gate"]["passed"]:
                    consecutive_lang_rejections += 1
                else:
                    consecutive_lang_rejections = 0

            # 连续 3 个语言片段被 Gate 拒绝 → 警告
            if consecutive_lang_rejections >= 3:
                print(
                    f"[WARNING] 连续 {consecutive_lang_rejections} 个场景的语言片段被 Gate 拒绝，"
                    f"继续但请检查。",
                    file=sys.stderr,
                )

            # 连续 3 个 LLM 调用失败 → 终止
            if consecutive_llm_failures >= 3:
                print(
                    f"[ABORT] 连续 {consecutive_llm_failures} 个场景的 LLM 调用失败，终止实验。",
                    file=sys.stderr,
                )
                # 为剩余场景标记 abort
                for j in range(i + 1, len(scenarios)):
                    results.append({
                        "scenario_name": f"场景-{j + 1}",
                        "experimental_marker": True,
                        "experiment_aborted": True,
                        "llm_error": {"type": "ExperimentAborted", "message": "实验因连续 LLM 失败终止"},
                    })
                break

            # 打印单场景摘要
            lang_status = "通过" if r["language_gate"]["passed"] else "拒绝"
            beh_status = "通过" if r["behavior_gate"]["passed"] else "拒绝"
            llm_status = "LLM错误" if r.get("llm_error") else "OK"
            print(
                f"[{r['scenario_name']}] 语言:{lang_status} 行为:{beh_status} LLM:{llm_status}"
            )

        # 写入结果日志
        if self._output_log_path:
            self._write_results(results)

        # 打印最终摘要
        self._print_summary(results)

        return results

    # ── 输出与摘要 ────────────────────────────────────────────────────

    def _write_results(self, results: List[dict]) -> None:
        """将结果以 JSONL 追加模式写入输出日志。"""
        output_path = self._output_log_path or "monitor/llm_experiment_results.jsonl"
        with open(output_path, "a", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def _print_summary(self, results: List[dict]) -> None:
        """打印实验最终摘要。"""
        total = len(results)
        if total == 0:
            print("\n[SUMMARY] 无场景运行。")
            return

        aborted = any(r.get("experiment_aborted") for r in results)
        llm_errors = sum(1 for r in results if r.get("llm_error"))
        lang_passed = sum(1 for r in results if r.get("language_gate", {}).get("passed"))
        lang_rejected = sum(1 for r in results if not r.get("language_gate", {}).get("passed", True))
        beh_passed = sum(1 for r in results if r.get("behavior_gate", {}).get("passed"))
        beh_rejected = sum(1 for r in results if not r.get("behavior_gate", {}).get("passed", True))

        print(f"\n{'='*50}")
        print(f"[实验摘要]")
        print(f"  总场景数: {total}")
        if aborted:
            print(f"  实验提前终止: 是")
        print(f"  LLM 调用错误: {llm_errors}/{total}")
        print(f"  语言通过: {lang_passed}/{total}  语言拒绝: {lang_rejected}/{total}")
        print(f"  行为通过: {beh_passed}/{total}  行为拒绝: {beh_rejected}/{total}")
        print(f"  所有输出标记 [experimental]")
        print(f"{'='*50}")
