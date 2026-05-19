from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class BodyState:
    """从 FieldTrace 观测信号映射的身体状态。

    纯输出——不修改 FieldTrace，不修改 LLM 提示词，不决定响应内容。
    """

    # 凝视
    gaze: str = "neutral"  # neutral / user / down / away / down_then_user / away_then_user

    # 姿态
    posture: str = "neutral"  # neutral / slight_forward / stable / slight_withdraw / closed_stable

    # 运动强度
    motion_intensity: str = "low"  # still / low / medium

    # 距离
    distance: str = "baseline"  # baseline / slightly_closer / maintained / slightly_farther

    # 时机
    timing: str = "immediate"  # immediate / short_pause / longer_pause

    # 语音密度提示
    speech_density_hint: str = "medium"  # minimal / low / medium / structured

    # 表达温度
    expression_temperature: str = "restrained"  # cool / restrained / warm_restrained

    # 人类可读说明
    body_note: str = "地面姿态——未观测到场信号"

    # 来源 FieldTrace 信号名称
    provenance: List[str] = field(default_factory=lambda: ["no_observable_field_signal"])

    # 必须始终为 False
    behavior_affecting: bool = False
