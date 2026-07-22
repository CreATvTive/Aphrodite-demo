"""P3 task-card 7: hand-written, data-driven perception→dynamics mapping table.

This is the deterministic, single-testable mapping from a validated
[`PerceptionEvent`](perception_schema.py) to a bounded, finite list of
per-dimension attractor deltas.  It is **perception influence**, not a writer:
it never calls the provider, never logs a writer rationale, and never writes
field state.  The ingress applies the returned deltas via the runtime's
existing public ``move_attractor`` command, preserving owner/append-only/
recovery semantics and distinguishing the source as
[`PERCEPTION_SOURCE`](perception_config.py).

Mapping rules:

* targets only dimensions present in the live registry; unknown dims are
  reported and skipped, never indexed by position;
* each delta is finite, signed, and capped at
  [`PERCEPTION_AMPLITUDE_CAP`](perception_config.py); the runtime still
  validates the displacement domain and rejects anything out of bounds;
* no hard clamp on field state — the cap is on the *input delta*, not on the
  resulting attractor value;
* intensity normalization saturates at the configured thresholds so larger
  observations do not produce unbounded deltas.

Imports are restricted to the standard library and
[`perception_config`](perception_config.py) / [`perception_schema`](perception_schema.py).
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

from app.chatbox.perception_config import (
    DEFAULT_MAPPING,
    LENGTH_SATURATION_CHARS,
    NIGHT_END_HOUR,
    NIGHT_START_HOUR,
    PERCEPTION_AMPLITUDE_CAP,
    SILENCE_SATURATION_SECONDS,
    TYPING_SATURATION_SECONDS,
)
from app.chatbox.perception_schema import PerceptionEvent


@dataclass(frozen=True, slots=True)
class MappingTarget:
    """One per-dimension attractor delta produced by the mapping table."""

    dim_id: str
    delta: float
    intensity: float
    magnitude: float


@dataclass(frozen=True, slots=True)
class MappingResult:
    """Outcome of mapping one event.

    ``targets`` is the bounded, finite list of validated deltas.  ``skipped``
    lists dim_ids the mapping table referenced but the live registry does not
    contain, so callers can report them without indexing by position.
    ``intensity`` is the normalized signal strength in [0, 1] used for the
    event (useful for tests and error-band checks).
    """

    kind: str
    intensity: float
    targets: tuple[MappingTarget, ...]
    skipped: tuple[str, ...]


def _saturate(value: float, saturation: float) -> float:
    """Normalize ``value`` into [0, 1] with saturation at ``saturation``."""
    if saturation <= 0.0:
        return 0.0
    if value <= 0.0:
        return 0.0
    if value >= saturation:
        return 1.0
    return value / saturation


def _band_for_hour(local_hour: float) -> str:
    """Return 'night' for the configured night band, else 'day'.

    The night band wraps past midnight: [NIGHT_START_HOUR, 24) ∪ [0, NIGHT_END_HOUR).
    """
    if local_hour >= NIGHT_START_HOUR or local_hour < NIGHT_END_HOUR:
        return "night"
    return "day"


def _intensity_for(event: PerceptionEvent) -> float:
    """Compute the normalized intensity in [0, 1] for the event's kind."""
    kind = event.kind
    payload = event.payload
    if kind == "message_gap":
        if payload["is_first"] or payload["is_new_session"]:
            # First message or new session: no silence to measure yet.
            return 0.0
        return _saturate(float(payload["duration_seconds"]), SILENCE_SATURATION_SECONDS)
    if kind == "time_of_day":
        # Both bands carry full intensity; the sign flip in _sign_for_day turns
        # the same magnitude into the opposite nudge during the day.
        return 1.0
    if kind == "message_length":
        return _saturate(float(payload["char_count"]), LENGTH_SATURATION_CHARS)
    if kind == "session_lifecycle":
        return 1.0
    if kind == "typing":
        # Sustained typing is not measured here; the typing ingress tracks
        # duration and passes it via payload state.  For the mapping table the
        # intensity is a small constant per discrete state event.
        state = payload["state"]
        return 0.5 if state == "start" else (1.0 if state == "heartbeat" else 0.0)
    return 0.0


def _sign_for(event: PerceptionEvent) -> float:
    """Return the sign applied to the base magnitudes for this event.

    Most kinds use the magnitudes as-is.  ``time_of_day`` flips the sign in the
    day band so the same mapping entry produces the opposite nudge during the
    day.  ``message_length`` uses a short-message branch (玩兴) vs long-message
    branch (开放/好奇).
    """
    if event.kind == "time_of_day":
        return -1.0 if event.payload["band"] == "day" else 1.0
    return 1.0


def map_event(
    event: PerceptionEvent,
    *,
    registry_dim_ids: tuple[str, ...],
    mapping: Mapping[str, Mapping[str, float]] | None = None,
) -> MappingResult:
    """Map one validated event to bounded attractor deltas.

    ``registry_dim_ids`` is the live registry's dim_id tuple; any mapping
    target whose dim_id is not in it is reported in ``skipped`` and produces
    no delta.  ``mapping`` defaults to
    [`DEFAULT_MAPPING`](perception_config.py) and may be overridden in tests.
    """
    table = mapping if mapping is not None else DEFAULT_MAPPING
    if event.kind not in table:
        return MappingResult(event.kind, 0.0, (), ())
    intensity = _intensity_for(event)
    if not math.isfinite(intensity) or intensity <= 0.0:
        return MappingResult(event.kind, 0.0, (), ())
    sign = _sign_for(event)
    known = set(registry_dim_ids)
    targets: list[MappingTarget] = []
    skipped: list[str] = []
    for dim_id, magnitude in table[event.kind].items():
        if not isinstance(magnitude, (int, float)) or isinstance(magnitude, bool):
            continue
        mag = float(magnitude)
        if not math.isfinite(mag) or mag == 0.0:
            continue
        if dim_id not in known:
            skipped.append(dim_id)
            continue
        delta = sign * intensity * mag
        # Cap the per-event per-dimension delta.  This is a perception-side
        # safeguard on the input, not a hard clamp on field state; the runtime
        # still validates the resulting attractor displacement domain.
        if abs(delta) > PERCEPTION_AMPLITUDE_CAP:
            delta = math.copysign(PERCEPTION_AMPLITUDE_CAP, delta)
        if delta == 0.0:
            continue
        targets.append(MappingTarget(dim_id=dim_id, delta=delta, intensity=intensity, magnitude=mag))
    return MappingResult(
        kind=event.kind,
        intensity=intensity,
        targets=tuple(targets),
        skipped=tuple(skipped),
    )


def band_for_hour(local_hour: float) -> str:
    """Public helper used by the server-side time-of-day ingress."""
    return _band_for_hour(local_hour)
