from __future__ import annotations

from typing import Dict

from src.field_state.schema import RelationalFieldState

from .schema import (
    _LANGUAGE_CONDITION_PARAM_NAMES,
    _PARAM_TO_SOURCE,
    LanguageConditionVector,
    clamp01,
)


class FieldStateToLanguageConditionMapper:
    """Deterministic v0 mapper: RelationalFieldState → LanguageConditionVector.

    v0 mapping strategy (design baseline):
    - All parameters use identity mapping (f(x) = x) except warmth_tone_modifier
    - warmth_tone_modifier = clamp(affective_warmth, 0.0, 0.60), i.e. capped at 0.60
    - v0 identity mapping is a baseline — future phases may replace it with
      non-linear, learned, or calibrated functions
    - No randomness. No inference calls. No keyword rules. No prompt generation.
    - Not behaviour-affecting (behavior_affecting=False)
    """

    # ── Public static API ─────────────────────────────────────────────────────

    @staticmethod
    def map(field_state: RelationalFieldState) -> LanguageConditionVector:
        """Convert a RelationalFieldState into a LanguageConditionVector.

        Reads ``numeric_value`` from each of the 10 required field variables,
        applies ``clamp01``, and creates the language condition vector.
        The only non-identity transformation is warmth cap at 0.60.
        """
        if not isinstance(field_state, RelationalFieldState):
            raise TypeError(
                f"field_state must be a RelationalFieldState, "
                f"got {type(field_state).__name__}"
            )

        # Extract raw numeric values from the field state
        raw: Dict[str, float] = {
            name: float(field_state.variables[name].numeric_value)
            for name in _PARAM_TO_SOURCE.values()
        }

        # Build kwargs with identity mapping + special warmth cap
        kwargs: Dict[str, float] = {}
        for idx, param_name in enumerate(_LANGUAGE_CONDITION_PARAM_NAMES):
            source_name = _PARAM_TO_SOURCE[idx]
            clamped = clamp01(raw[source_name])
            if param_name == "warmth_tone_modifier":
                clamped = min(clamped, 0.60)
            kwargs[param_name] = clamped

        return LanguageConditionVector(**kwargs)

    @staticmethod
    def audit_trace(field_state: RelationalFieldState) -> dict:
        """Return a dictionary showing each field variable → language condition
        parameter mapping with raw, clamped, and cap information.
        """
        if not isinstance(field_state, RelationalFieldState):
            raise TypeError(
                f"field_state must be a RelationalFieldState, "
                f"got {type(field_state).__name__}"
            )

        raw: Dict[str, float] = {
            name: float(field_state.variables[name].numeric_value)
            for name in _PARAM_TO_SOURCE.values()
        }

        trace: dict = {}
        for idx, param_name in enumerate(_LANGUAGE_CONDITION_PARAM_NAMES):
            source_name = _PARAM_TO_SOURCE[idx]
            source_raw = raw[source_name]
            clamped = clamp01(source_raw)

            entry: dict = {
                "value": source_raw,
                "maps_to": param_name,
            }

            if param_name == "warmth_tone_modifier":
                entry["mapped_value"] = min(clamped, 0.60)
                entry["cap_applied"] = bool(source_raw > 0.60)
                if source_raw > 0.60:
                    entry["uncapped_value"] = source_raw
            else:
                entry["mapped_value"] = clamped
                entry["cap_applied"] = False

            trace[source_name] = entry

        return trace
