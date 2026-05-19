from __future__ import annotations

from dataclasses import astuple, dataclass
from typing import ClassVar, Dict, List, Tuple, Sequence


def clamp01(value: float) -> float:
    """Clamp a float value to the range [0.0, 1.0]."""
    if value <= 0.0:
        return 0.0
    if value >= 1.0:
        return 1.0
    return value


def is_valid_range(value: float) -> bool:
    """Return True if value is within the valid domain [0.0, 1.0]."""
    return 0.0 <= value <= 1.0


# ── Ordered list of 10 language condition parameters ──────────────────────────
_LANGUAGE_CONDITION_PARAM_NAMES: Tuple[str, ...] = (
    "language_distance_marker",        # 0
    "warmth_tone_modifier",            # 1
    "structural_grip_modifier",        # 2
    "correction_directness",           # 3
    "contamination_filter_strength",   # 4
    "presence_stability_modifier",     # 5
    "withdrawal_expression_bias",      # 6
    "service_suppression_strength",    # 7
    "collaborator_register_bias",      # 8
    "compression_under_contamination", # 9
)

# Mapping from language condition param index to source field variable name
_PARAM_TO_SOURCE: Dict[int, str] = {
    0: "boundary_distance",
    1: "affective_warmth",
    2: "structural_grip_pressure",
    3: "correction_pressure",
    4: "contamination_resistance",
    5: "presence_stability",
    6: "withdrawal_tendency",
    7: "service_resistance",
    8: "collaborator_layer_pressure",
    9: "contamination_pressure",
}


@dataclass(frozen=True)
class LanguageConditionVector:
    """Frozen vector of 10 language condition parameters (v0 schema).

    Each value is a float in [0.0, 1.0] derived deterministically from
    ``RelationalFieldState``.  This vector describes *how* language should
    be produced — not what to say.

    ``behavior_affecting`` is always ``False``: the vector is a pure
    structural descriptor and does not drive runtime decisions.
    """

    language_distance_marker: float = 0.50
    """Language distance marker — controls degree of indirectness, ambiguity,
    and relational-distance phrasing."""

    warmth_tone_modifier: float = 0.35
    """Warmth tone modifier — influences perceived warmth in language.
    Capped at 0.60 to protect the public-expression cap."""

    structural_grip_modifier: float = 0.05
    """Structural grip modifier — influences syntactic certainty and whether
    a reply offers concrete footholds."""

    correction_directness: float = 0.00
    """Correction directness — controls how direct corrective responses are."""

    contamination_filter_strength: float = 0.40
    """Contamination filter strength — suppresses AI-girlfriend,
    false-intimacy, and service-drift language patterns."""

    presence_stability_modifier: float = 0.80
    """Presence stability modifier — influences cross-turn syntactic
    consistency and predictability."""

    withdrawal_expression_bias: float = 0.10
    """Withdrawal expression bias — increases expression of silence,
    distance, and unresolvedness."""

    service_suppression_strength: float = 0.55
    """Service suppression strength — suppresses assistant / customer-service
    completion patterns and service offerings."""

    collaborator_register_bias: float = 0.05
    """Collaborator register bias — controls permission for technical detail
    and collaborator-style interaction."""

    compression_under_contamination: float = 0.00
    """Compression under contamination — whether a reply should be compressed
    or minimized under current-turn contamination pressure."""

    # ── Class constants ───────────────────────────────────────────────────────

    behavior_affecting: ClassVar[bool] = False

    MAPPING_TABLE: ClassVar[List[Dict[str, str]]] = [
        {"param": "language_distance_marker",      "source_field": "boundary_distance"},
        {"param": "warmth_tone_modifier",          "source_field": "affective_warmth"},
        {"param": "structural_grip_modifier",      "source_field": "structural_grip_pressure"},
        {"param": "correction_directness",         "source_field": "correction_pressure"},
        {"param": "contamination_filter_strength", "source_field": "contamination_resistance"},
        {"param": "presence_stability_modifier",   "source_field": "presence_stability"},
        {"param": "withdrawal_expression_bias",    "source_field": "withdrawal_tendency"},
        {"param": "service_suppression_strength",  "source_field": "service_resistance"},
        {"param": "collaborator_register_bias",    "source_field": "collaborator_layer_pressure"},
        {"param": "compression_under_contamination", "source_field": "contamination_pressure"},
    ]

    # ── Validation ────────────────────────────────────────────────────────────

    def __post_init__(self) -> None:
        # frozen dataclass forces use of object.__setattr__
        for idx, name in enumerate(_LANGUAGE_CONDITION_PARAM_NAMES):
            raw = getattr(self, name)
            if not isinstance(raw, (int, float)):
                raise TypeError(
                    f"LanguageConditionVector.{name} must be a float, "
                    f"got {type(raw).__name__}"
                )
            clamped = clamp01(float(raw))
            if clamped != float(raw):
                object.__setattr__(self, name, clamped)

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, float]:
        """Return the vector as a plain dictionary."""
        return {
            name: getattr(self, name)
            for name in _LANGUAGE_CONDITION_PARAM_NAMES
        }

    @classmethod
    def from_dict(cls, d: dict) -> LanguageConditionVector:
        """Construct a LanguageConditionVector from a dictionary.

        All values are clamped to [0.0, 1.0].  ``warmth_tone_modifier`` is
        additionally capped at 0.60 (the public-expression cap).
        """
        kwargs: Dict[str, float] = {}
        for name in _LANGUAGE_CONDITION_PARAM_NAMES:
            raw = clamp01(float(d.get(name, 0.0)))
            if name == "warmth_tone_modifier":
                raw = min(raw, 0.60)
            kwargs[name] = raw
        return cls(**kwargs)

    def to_tuple(self) -> Tuple[float, ...]:
        """Return the 10 values as a tuple (useful for indexing)."""
        return astuple(self)

    # ── Human-readable representation ─────────────────────────────────────────

    def __repr__(self) -> str:
        items = ", ".join(
            f"{name}={getattr(self, name):.2f}"
            for name in _LANGUAGE_CONDITION_PARAM_NAMES
        )
        return f"LanguageConditionVector({items})"
