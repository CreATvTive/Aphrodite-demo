from dataclasses import dataclass, field, fields, is_dataclass
from typing import List, Optional

from src.motion_params.schema import BodyPartOffsets

# 批准的动作原语（恰好 10 个）
ACTION_PRIMITIVES = frozenset({
    "pause", "stillness", "look_down", "look_to_user", "look_away",
    "slight_forward", "slight_withdraw", "maintain_distance",
    "reduce_motion", "reset_posture",
})

# 批准的权重带（恰好 4 个）
WEIGHT_BANDS = frozenset({"off", "low", "medium", "high"})

# 批准的 duration_hint 值
DURATION_HINTS = frozenset({"instant", "short", "medium", "sustained"})

# 批准的 completion 值
COMPLETION_MODES = frozenset({"partial", "restrained", "complete"})


@dataclass
class BodyActionWeight:
    """单个身体动作原语的权重建议。

    使用粗粒度带（off/low/medium/high），而非精确浮点数。
    """
    action_name: str
    weight: str = "off"
    rationale: str = ""
    constraints: List[str] = field(default_factory=list)
    provenance: List[str] = field(default_factory=list)
    behavior_affecting: bool = False

    def __post_init__(self):
        if self.action_name not in ACTION_PRIMITIVES:
            raise ValueError(f"未知动作原语: '{self.action_name}'。合法值: {sorted(ACTION_PRIMITIVES)}")
        if not isinstance(self.weight, str):
            raise ValueError(f"weight 必须为字符串（off/low/medium/high），而非 {type(self.weight).__name__}")
        if self.weight not in WEIGHT_BANDS:
            raise ValueError(f"无效权重带: '{self.weight}'。合法值: {sorted(WEIGHT_BANDS)}")
        if self.behavior_affecting is not False:
            raise ValueError("behavior_affecting 必须为 False")

    def to_dict(self) -> dict:
        return _dataclass_to_dict(self)


@dataclass
class BodyActionWeights:
    """每轮的身体动作权重集合。"""
    weights: List[BodyActionWeight] = field(default_factory=list)
    body_part_offsets: Optional[BodyPartOffsets] = None
    source_trace_id: Optional[str] = None
    source_proposals: List[str] = field(default_factory=list)
    body_note: str = ""
    behavior_affecting: bool = False

    def __post_init__(self):
        if self.behavior_affecting is not False:
            raise ValueError("behavior_affecting 必须为 False")

    def to_dict(self) -> dict:
        return _dataclass_to_dict(self)


@dataclass
class ActionSequenceHint:
    """动作序列中的时序位置提示。

    这是渲染器的提示，而非硬性指令。
    """
    action_name: str
    order: int = 0
    duration_hint: str = "instant"
    completion: str = "partial"
    constraints: List[str] = field(default_factory=list)
    provenance: List[str] = field(default_factory=list)
    behavior_affecting: bool = False

    def __post_init__(self):
        if self.action_name not in ACTION_PRIMITIVES:
            raise ValueError(f"未知动作原语: '{self.action_name}'。合法值: {sorted(ACTION_PRIMITIVES)}")
        if self.duration_hint not in DURATION_HINTS:
            raise ValueError(f"无效 duration_hint: '{self.duration_hint}'。合法值: {sorted(DURATION_HINTS)}")
        if self.completion not in COMPLETION_MODES:
            raise ValueError(f"无效 completion: '{self.completion}'。合法值: {sorted(COMPLETION_MODES)}")
        if self.order < 0:
            raise ValueError(f"order 必须 >= 0，得到: {self.order}")
        if self.behavior_affecting is not False:
            raise ValueError("behavior_affecting 必须为 False")

    def to_dict(self) -> dict:
        return _dataclass_to_dict(self)


@dataclass
class BodyActionComposition:
    """每轮的完整身体动作组合。

    包含主要动作序列、次要动作序列、抑制的动作列表、
    硬约束和来源权重。这是未来渲染器的输入协议。
    """
    primary_actions: List[ActionSequenceHint] = field(default_factory=list)
    secondary_actions: List[ActionSequenceHint] = field(default_factory=list)
    suppressed_actions: List[str] = field(default_factory=list)
    hard_constraints: List[str] = field(default_factory=list)
    source_weights: List[BodyActionWeight] = field(default_factory=list)
    composition_note: str = ""
    behavior_affecting: bool = False

    def __post_init__(self):
        # 验证 suppressed_actions 中的每个名称
        for name in self.suppressed_actions:
            if name not in ACTION_PRIMITIVES:
                raise ValueError(f"suppressed_actions 中的未知动作: '{name}'。合法值: {sorted(ACTION_PRIMITIVES)}")
        if self.behavior_affecting is not False:
            raise ValueError("behavior_affecting 必须为 False")

    def to_dict(self) -> dict:
        return _dataclass_to_dict(self)


def _dataclass_to_dict(instance) -> dict:
    return {
        item.name: _jsonable(getattr(instance, item.name))
        for item in fields(instance)
    }


def _jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if is_dataclass(value):
        return {
            item.name: _jsonable(getattr(value, item.name))
            for item in fields(value)
        }
    return value
