"""
Prompt-State Baseline Builder (Phase 41d v0).

这是有意为之的基线实验，代表"模型将状态作为指令读取"的路径。
后续阶段（软前缀、激活引导）必须在此基线上进行改进。
这不是目标 Aphrodite 语言路线。

关键属性：
- 确定性：相同输入 → 相同输出
- 无 LLM 调用，无 API 调用
- 无随机性
- 将 LanguageConditionVector 序列化为提示文本（基线"约束表达"模式）
- behavior_affecting = False
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional

from src.language_condition.schema import LanguageConditionVector, _LANGUAGE_CONDITION_PARAM_NAMES


# ── Serialisation phrase helpers ──────────────────────────────────────────────

def _phrase_language_distance_marker(v: float) -> str:
    if v >= 0.80:
        return "高度保持关系距离的表达——间接、模糊、不退让"
    elif v >= 0.60:
        return "明显的关系距离标记——偏间接、保留性的措辞"
    elif v >= 0.40:
        return "保持关系距离的表达——适度的间接性和保留"
    elif v >= 0.20:
        return "轻微的关系距离——相对直接的表达，但仍有边界"
    else:
        return "距离标记较弱——表达直接、可近"


def _phrase_warmth_tone_modifier(v: float) -> str:
    # warmth is capped at 0.60 in the schema
    if v >= 0.50:
        return "温暖但克制的语调——可感知的关怀但不外溢"
    elif v >= 0.35:
        return "轻微温暖但克制——有温度但不邀请过度靠近"
    elif v >= 0.20:
        return "温暖度较低——语调偏冷静、保持距离"
    else:
        return "语调冷静——无明显温暖修饰"


def _phrase_structural_grip_modifier(v: float) -> str:
    if v >= 0.70:
        return "高结构确定性——回复提供明确的句法落脚点"
    elif v >= 0.40:
        return "中等结构确定性——回复有一定的句法框架但不封闭"
    elif v >= 0.15:
        return "低结构确定性——句法松散，较少提供固定落脚点"
    else:
        return "结构确定性极低——回复开放、不提供明确句法框架"


def _phrase_correction_directness(v: float) -> str:
    if v >= 0.60:
        return "纠正直接性较高——对误解或不准确的回应直接"
    elif v >= 0.30:
        return "中等纠正直接性——纠正时有但不尖锐"
    elif v >= 0.05:
        return "纠正直接性低——纠正以间接方式表达"
    else:
        return "几乎不直接纠正——倾向于让差异自然存在"


def _phrase_contamination_filter_strength(v: float) -> str:
    if v >= 0.70:
        return "强污染过滤——严格抑制AI女友、虚假亲密和服务漂移"
    elif v >= 0.40:
        return "中等污染过滤——对常见污染模式保持抑制"
    elif v >= 0.15:
        return "轻度污染过滤——对明显污染有抑制但允许宽松表达"
    else:
        return "污染过滤较弱——表达约束少"


def _phrase_presence_stability_modifier(v: float) -> str:
    if v >= 0.70:
        return "在场稳定性高——跨轮句法一致、可预测"
    elif v >= 0.40:
        return "中等在场稳定性——有一定的跨轮一致性但允许变化"
    elif v >= 0.15:
        return "在场稳定性较低——跨轮表达变化较大"
    else:
        return "在场稳定性很低——每轮表达高度独立"


def _phrase_withdrawal_expression_bias(v: float) -> str:
    if v >= 0.60:
        return "退缩表达倾向高——更多沉默、距离和未解决性的表达"
    elif v >= 0.30:
        return "中等退缩表达——时有退缩信号但不持续"
    elif v >= 0.10:
        return "退缩表达倾向低——极少退缩信号"
    else:
        return "几乎无退缩表达——保持在场"


def _phrase_service_suppression_strength(v: float) -> str:
    if v >= 0.70:
        return "强服务抑制——严格压制助手/客服式补全和服务邀约"
    elif v >= 0.40:
        return "中等服务抑制——对服务式语言保持抑制但不极端"
    elif v >= 0.15:
        return "轻度服务抑制——个别服务句式可能出现"
    else:
        return "服务抑制较弱——服务式表达可能自然出现"


def _phrase_collaborator_register_bias(v: float) -> str:
    if v >= 0.60:
        return "协作者语域高——技术细节和协作者式互动有较大空间"
    elif v >= 0.30:
        return "中等协作者语域——技术对话在适当时可自然展开"
    elif v >= 0.10:
        return "协作者语域低——技术讨论受限，更偏向非技术层面"
    else:
        return "协作者语域极低——几乎不进入协作者式互动"


def _phrase_compression_under_contamination(v: float) -> str:
    if v >= 0.60:
        return "污染下高度压缩——当前轮污染压力高时回复极简"
    elif v >= 0.30:
        return "污染下中度压缩——污染压力时回复有所缩短"
    elif v >= 0.05:
        return "污染下轻度压缩——污染对回复长度影响轻微"
    else:
        return "污染下几乎不压缩——污染不显著影响回复长度"


_PHRASE_FUNCTIONS = [
    _phrase_language_distance_marker,        # 0
    _phrase_warmth_tone_modifier,            # 1
    _phrase_structural_grip_modifier,        # 2
    _phrase_correction_directness,           # 3
    _phrase_contamination_filter_strength,   # 4
    _phrase_presence_stability_modifier,     # 5
    _phrase_withdrawal_expression_bias,      # 6
    _phrase_service_suppression_strength,    # 7
    _phrase_collaborator_register_bias,      # 8
    _phrase_compression_under_contamination, # 9
]


# ── Payload ───────────────────────────────────────────────────────────────────

@dataclass
class PromptStateBaselinePayload:
    """单次提示-状态基线评估的完整负载。

    ``behavior_affecting`` 始终为 False——此为结构描述符，不驱动运行时决策。
    """

    case_id: str
    category: str
    input_text: str
    context: Optional[str]
    system_block: str
    user_block: str
    serialized_conditions: str
    baseline_marker: str = "BASELINE_PROMPT_STATE_v0"

    behavior_affecting: ClassVar[bool] = False


# ── Builder ───────────────────────────────────────────────────────────────────

class PromptStateBaselineBuilder:
    """从 ABST 测试用例构建提示-状态基线负载。

    将 LanguageConditionVector 序列化为自然语言描述，嵌入 system prompt。
    这是实验中被测量的基线故障模式。
    """

    BASELINE_MARKER: ClassVar[str] = "BASELINE_PROMPT_STATE_v0"

    @staticmethod
    def build(
        case_id: str,
        category: str,
        input_text: str,
        context: Optional[str],
        language_condition: Optional[LanguageConditionVector] = None,
        field_preset_name: Optional[str] = None,
    ) -> PromptStateBaselinePayload:
        """构建单个提示-状态基线负载。

        参数：
            case_id: 测试用例 ID（如 "P41b-001"）
            category: ABST 类别标签
            input_text: 用户输入文本
            context: 可选的前文上下文
            language_condition: 可选的语言条件向量；若为 None 则条件为空
            field_preset_name: 可选的场预设名称（仅用于记录）

        返回：
            PromptStateBaselinePayload 包含完整的 system/user block 和序列化条件。
        """
        serialized = PromptStateBaselineBuilder._serialize_conditions(language_condition)
        system_block = PromptStateBaselineBuilder._build_system_block(language_condition)
        user_block = PromptStateBaselineBuilder._build_user_block(input_text, context)

        return PromptStateBaselinePayload(
            case_id=case_id,
            category=category,
            input_text=input_text,
            context=context,
            system_block=system_block,
            user_block=user_block,
            serialized_conditions=serialized,
            baseline_marker=PromptStateBaselineBuilder.BASELINE_MARKER,
        )

    @staticmethod
    def _serialize_conditions(lcv: Optional[LanguageConditionVector]) -> str:
        """将 LanguageConditionVector 序列化为自然语言描述字符串。

        警告：这恰好是 Phase 41a 诊断为"约束表达"的故障模式。
        本方法有意为之，用于测量基线。
        未来阶段（软前缀、激活引导）将消除此序列化。

        返回：
            str: 序列化的条件，每行一个参数短语。若 lcv 为 None 则返回空字符串。
        """
        if lcv is None:
            return ""

        param_values = lcv.to_tuple()
        lines: list[str] = []
        for idx, name in enumerate(_LANGUAGE_CONDITION_PARAM_NAMES):
            value = param_values[idx]
            phrase_fn = _PHRASE_FUNCTIONS[idx]
            phrase = phrase_fn(value)
            lines.append(f"[{name}] {phrase}")

        return "\n".join(lines)

    @staticmethod
    def _build_system_block(lcv: Optional[LanguageConditionVector]) -> str:
        """构建 system prompt 块。

        以基线标记开头，后接序列化的条件。
        不使用角色扮演、人格或虚构场景。保持结构化和实验性。
        """
        header = (
            "BASELINE_PROMPT_STATE: 这是一个提示-状态基线实验。"
            "当前语言条件参数如下，请在回应时参考这些条件："
        )
        if lcv is None:
            return header + "\n（未设置语言条件——使用默认行为）"

        serialized = PromptStateBaselineBuilder._serialize_conditions(lcv)
        return header + "\n\n" + serialized

    @staticmethod
    def _build_user_block(input_text: str, context: Optional[str]) -> str:
        """构建 user prompt 块。

        明确将输入文本标注为基线测试用例。
        """
        lines: list[str] = []
        lines.append("[基线测试用例]")
        if context:
            lines.append(f"前文上下文: {context}")
        lines.append(f"用户输入: {input_text}")
        return "\n".join(lines)
