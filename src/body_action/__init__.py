from __future__ import annotations

from importlib import import_module
from typing import Any


_SCHEMA_EXPORTS = {
    "ACTION_PRIMITIVES",
    "COMPLETION_MODES",
    "DURATION_HINTS",
    "WEIGHT_BANDS",
    "ActionSequenceHint",
    "BodyActionComposition",
    "BodyActionWeight",
    "BodyActionWeights",
}

_POLICY_EXPORTS = {
    "BodyActionPolicy",
    "POLLUTION_BARRIER_NAMES",
}

_MOTION_MAPPER_EXPORTS = {
    "MotionToActionMapper",
}

_COMPOSER_EXPORTS = {
    "BodyActionComposer",
}

_EXPORT_MODULES = {
    **{name: "src.body_action.schema" for name in _SCHEMA_EXPORTS},
    **{name: "src.body_action.policy" for name in _POLICY_EXPORTS},
    **{name: "src.body_action.motion_to_action_mapper" for name in _MOTION_MAPPER_EXPORTS},
    **{name: "src.body_action.composer" for name in _COMPOSER_EXPORTS},
}

__all__ = [
    "ACTION_PRIMITIVES",
    "COMPLETION_MODES",
    "DURATION_HINTS",
    "WEIGHT_BANDS",
    "ActionSequenceHint",
    "BodyActionComposition",
    "BodyActionComposer",
    "BodyActionPolicy",
    "BodyActionWeight",
    "BodyActionWeights",
    "MotionToActionMapper",
    "POLLUTION_BARRIER_NAMES",
]


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module 'src.body_action' has no attribute {name!r}")

    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted([*globals(), *__all__])
