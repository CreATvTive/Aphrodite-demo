from __future__ import annotations

from copy import deepcopy
from typing import Any


class StateAuthority:
    """Single authority for mutating global state with trace-ready rationale."""

    def __init__(self, initial_state: dict[str, Any] | None = None) -> None:
        self._state = initial_state or {}

    @property
    def state(self) -> dict[str, Any]:
        return deepcopy(self._state)

    def apply_delta(self, delta: dict[str, Any], source: str, rationale: str) -> dict[str, Any]:
        if not source or not rationale:
            raise ValueError("source and rationale are required for traceability")
        self._state = _deep_merge(self._state, delta)
        return {
            "source": source,
            "rationale": rationale,
            "applied_delta": deepcopy(delta),
        }


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged
