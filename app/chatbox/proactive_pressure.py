"""P4 task-card 10: deterministic, field-adjacent expression-pressure accumulator.

This module is a *pure computation* boundary.  It owns the non-negative
``P_talk`` accumulation that turns the committed 1 Hz field tick into an
emergent "pressure crosses threshold" trigger, instead of a Bernoulli random
sample.  It contains no RNG, no runtime/provider/writer imports, no SQLite,
and no network — only the frozen formula from
[`phase-plan-v0.md`](docs/chatbox/phase-plan-v0.md) section C.4 and the
locked defaults from the task-card 10 brief.

Inputs are structural read-only registry/snapshot views, the committed field
tick, the last user-activity nanosecond stamp, and the current trusted
nanosecond stamp.
Outputs are a new pressure, whether the threshold was reached/crossed, and a
stable reject reason when the step was fail-closed.

The formula (frozen defaults, contact-time tunable but fixed by this card):

* ``θ = 1.0`` threshold;
* ``λ = 1/10800 s⁻¹`` decay;
* drive gain ``k = 1/1800 s⁻¹``;
* silence time constant ``τ_s = 7200 s``;
* toward term ``0.5·(tanh(x_toward)+1)``;
* expect term ``0.5·(tanh(x_expect)+1)``;
* silence term ``1 − exp(−silence/τ_s)``;
* ``g = k · toward · expect · silence``;
* exact constant-drive linear ODE step over ``dt``:
  ``P' = (g/λ) + (P − g/λ)·exp(−λ·dt)``.

The semantic dim ids are looked up by ``dim_id`` (``birth_03`` toward,
``birth_09`` expect), never by ordinal.  Registry/snapshot must align in
order and length; any missing dim, non-finite value, unknown/negative
silence, or non-strictly-increasing tick is fail-closed: no integration, no
trigger, no guess.  Pressure is never hard-clamped; it stays non-negative
because the exact step of ``dP/dt = g − λP`` with ``g ≥ 0`` and ``P ≥ 0``
keeps ``P ≥ 0`` for ``dt > 0``.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol, Sequence


class _Registration(Protocol):
    dim_id: str


class _Dimension(Protocol):
    dim_id: str
    value: float


class RegistryView(Protocol):
    """Structural read-only registry boundary used by the pure formula."""

    length: int
    registrations: Sequence[_Registration]


class SnapshotView(Protocol):
    """Structural read-only snapshot boundary used by the pure formula."""

    dimensions: Sequence[_Dimension]


# Frozen defaults locked by task-card 10 (contact-time tunable, fixed here).
DEFAULT_THRESHOLD = 1.0
DEFAULT_DECAY_LAMBDA = 1.0 / 10800.0
DEFAULT_DRIVE_GAIN = 1.0 / 1800.0
DEFAULT_SILENCE_TAU = 7200.0
DEFAULT_DT_SECONDS = 1.0
DEFAULT_TOWARD_DIM_ID = "birth_03"
DEFAULT_EXPECT_DIM_ID = "birth_09"


@dataclass(frozen=True, slots=True)
class PressureConfig:
    """Frozen pressure formula configuration.

    Defaults match the task-card 10 locked values.  A caller may construct a
    stricter/extended configuration, but the hard cap policy lives in the
    persistence/admission module, not here.
    """

    threshold: float = DEFAULT_THRESHOLD
    decay_lambda: float = DEFAULT_DECAY_LAMBDA
    drive_gain: float = DEFAULT_DRIVE_GAIN
    silence_tau: float = DEFAULT_SILENCE_TAU
    dt_seconds: float = DEFAULT_DT_SECONDS
    toward_dim_id: str = DEFAULT_TOWARD_DIM_ID
    expect_dim_id: str = DEFAULT_EXPECT_DIM_ID

    def __post_init__(self) -> None:
        if not isinstance(self.threshold, (int, float)) or isinstance(self.threshold, bool):
            raise ValueError("threshold must be a finite number")
        if not math.isfinite(float(self.threshold)) or float(self.threshold) <= 0.0:
            raise ValueError("threshold must be positive and finite")
        if not isinstance(self.decay_lambda, (int, float)) or isinstance(self.decay_lambda, bool):
            raise ValueError("decay_lambda must be a finite number")
        if not math.isfinite(float(self.decay_lambda)) or float(self.decay_lambda) <= 0.0:
            raise ValueError("decay_lambda must be positive and finite")
        if not isinstance(self.drive_gain, (int, float)) or isinstance(self.drive_gain, bool):
            raise ValueError("drive_gain must be a finite number")
        if not math.isfinite(float(self.drive_gain)) or float(self.drive_gain) <= 0.0:
            raise ValueError("drive_gain must be positive and finite")
        if not isinstance(self.silence_tau, (int, float)) or isinstance(self.silence_tau, bool):
            raise ValueError("silence_tau must be a finite number")
        if not math.isfinite(float(self.silence_tau)) or float(self.silence_tau) <= 0.0:
            raise ValueError("silence_tau must be positive and finite")
        if not isinstance(self.dt_seconds, (int, float)) or isinstance(self.dt_seconds, bool):
            raise ValueError("dt_seconds must be a finite number")
        if not math.isfinite(float(self.dt_seconds)) or float(self.dt_seconds) <= 0.0:
            raise ValueError("dt_seconds must be positive and finite")
        if not isinstance(self.toward_dim_id, str) or not self.toward_dim_id:
            raise ValueError("toward_dim_id must be a non-empty string")
        if not isinstance(self.expect_dim_id, str) or not self.expect_dim_id:
            raise ValueError("expect_dim_id must be a non-empty string")


@dataclass(frozen=True, slots=True)
class PressureState:
    """Persistable pressure state: the accumulator and the last integrated tick.

    ``last_field_tick`` is ``None`` before the first observation.  It is the
    integration-order authority: only a strictly-increasing, gap-free tick
    sequence integrates ``dt`` seconds.
    """

    pressure: float
    last_field_tick: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.pressure, (int, float)) or isinstance(self.pressure, bool):
            raise ValueError("pressure must be a finite number")
        if not math.isfinite(float(self.pressure)) or float(self.pressure) < 0.0:
            raise ValueError("pressure must be non-negative and finite")
        if self.last_field_tick is not None:
            if not isinstance(self.last_field_tick, int) or isinstance(self.last_field_tick, bool):
                raise ValueError("last_field_tick must be an int or None")
            if self.last_field_tick < 0:
                raise ValueError("last_field_tick must be non-negative")


@dataclass(frozen=True, slots=True)
class PressureStepResult:
    """Result of one pressure step observation.

    ``new_state`` is the state to persist (pressure + last_field_tick).  When
    ``reject_reason`` is not ``None`` the step was fail-closed: no integration
    happened, no trigger should fire, and the pressure is either unchanged or
    re-anchored without backfill.  ``at_threshold`` is True iff the new
    pressure is ``>= threshold`` after a *driven* (integrated) step; it is
    always False for fail-closed steps so the coordinator never triggers on a
    rejected observation.
    """

    new_state: PressureState
    pressure: float
    at_threshold: bool
    above_threshold: bool
    reject_reason: str | None
    driven: bool
    drive_g: float | None


def _aligned_dimension_value(
    registry: RegistryView,
    snapshot: SnapshotView,
    dim_id: str,
) -> float | None:
    """Return the finite value for ``dim_id`` if registry/snapshot align.

    Alignment requires equal length and matching dim ids in order.  Returns
    ``None`` when the dim is absent, the registry/snapshot are misaligned, or
    the value is non-finite.
    """
    if len(snapshot.dimensions) != registry.length:
        return None
    for registration, dimension in zip(registry.registrations, snapshot.dimensions):
        if dimension.dim_id != registration.dim_id:
            return None
    for dimension in snapshot.dimensions:
        if dimension.dim_id == dim_id:
            value = float(dimension.value)
            if not math.isfinite(value):
                return None
            return value
    return None


def _drive_gain(
    config: PressureConfig,
    registry: RegistryView,
    snapshot: SnapshotView,
    silence_seconds: float,
) -> float | None:
    """Compute the non-negative drive ``g`` or ``None`` on any validation failure."""
    if not math.isfinite(silence_seconds) or silence_seconds < 0.0:
        return None
    toward_value = _aligned_dimension_value(registry, snapshot, config.toward_dim_id)
    if toward_value is None:
        return None
    expect_value = _aligned_dimension_value(registry, snapshot, config.expect_dim_id)
    if expect_value is None:
        return None
    toward_term = 0.5 * (math.tanh(toward_value) + 1.0)
    expect_term = 0.5 * (math.tanh(expect_value) + 1.0)
    silence_term = 1.0 - math.exp(-silence_seconds / config.silence_tau)
    return float(config.drive_gain * toward_term * expect_term * silence_term)


def step_pressure(
    state: PressureState,
    config: PressureConfig,
    registry: RegistryView,
    snapshot: SnapshotView,
    *,
    field_tick: int,
    last_user_activity_ns: int,
    current_ns: int,
) -> PressureStepResult:
    """Advance pressure by one committed field tick observation.

    The function is pure and deterministic: identical inputs yield identical
    outputs.  It never raises for bad inputs — it returns a fail-closed
    ``PressureStepResult`` with a stable ``reject_reason`` so the coordinator
    can persist the decision without exception handling.
    """
    if not isinstance(field_tick, int) or isinstance(field_tick, bool) or field_tick < 0:
        return PressureStepResult(
            new_state=state,
            pressure=state.pressure,
            at_threshold=False,
            above_threshold=state.pressure >= config.threshold,
            reject_reason="invalid_field_tick",
            driven=False,
            drive_g=None,
        )
    if not isinstance(last_user_activity_ns, int) or isinstance(last_user_activity_ns, bool):
        return PressureStepResult(
            new_state=state,
            pressure=state.pressure,
            at_threshold=False,
            above_threshold=state.pressure >= config.threshold,
            reject_reason="invalid_activity_ns",
            driven=False,
            drive_g=None,
        )
    if not isinstance(current_ns, int) or isinstance(current_ns, bool):
        return PressureStepResult(
            new_state=state,
            pressure=state.pressure,
            at_threshold=False,
            above_threshold=state.pressure >= config.threshold,
            reject_reason="invalid_current_ns",
            driven=False,
            drive_g=None,
        )
    if current_ns < last_user_activity_ns:
        # Wall-clock rollback: silence would be negative.  Fail closed without
        # integrating; the trusted silence baseline is preserved for the next
        # normal tick.
        return PressureStepResult(
            new_state=state,
            pressure=state.pressure,
            at_threshold=False,
            above_threshold=state.pressure >= config.threshold,
            reject_reason="clock_rollback",
            driven=False,
            drive_g=None,
        )

    last_tick = state.last_field_tick
    if last_tick is not None:
        if field_tick == last_tick:
            return PressureStepResult(
                new_state=state,
                pressure=state.pressure,
                at_threshold=False,
                above_threshold=state.pressure >= config.threshold,
                reject_reason="duplicate_tick",
                driven=False,
                drive_g=None,
            )
        if field_tick < last_tick:
            return PressureStepResult(
                new_state=state,
                pressure=state.pressure,
                at_threshold=False,
                above_threshold=state.pressure >= config.threshold,
                reject_reason="out_of_order_tick",
                driven=False,
                drive_g=None,
            )
        if field_tick != last_tick + 1:
            # Gap: re-anchor the tick cursor without backfilling the skipped
            # interval.  Pressure is preserved; no trigger fires.
            reanchored = PressureState(
                pressure=state.pressure,
                last_field_tick=field_tick,
            )
            return PressureStepResult(
                new_state=reanchored,
                pressure=state.pressure,
                at_threshold=False,
                above_threshold=state.pressure >= config.threshold,
                reject_reason="tick_gap_reanchored",
                driven=False,
                drive_g=None,
            )

    silence_seconds = (current_ns - last_user_activity_ns) / 1_000_000_000.0
    g = _drive_gain(config, registry, snapshot, silence_seconds)
    if g is None:
        # Validation failure (missing dim, misalignment, non-finite value,
        # bad silence).  Anchor the tick cursor on the first observation, but
        # never integrate or trigger.
        anchored_tick = field_tick if last_tick is None else state.last_field_tick
        new_state = PressureState(pressure=state.pressure, last_field_tick=anchored_tick)
        return PressureStepResult(
            new_state=new_state,
            pressure=state.pressure,
            at_threshold=False,
            above_threshold=state.pressure >= config.threshold,
            reject_reason="drive_validation_failed",
            driven=False,
            drive_g=None,
        )

    if last_tick is None:
        # First ever observation: anchor the cursor, do not integrate a dt.
        new_state = PressureState(pressure=state.pressure, last_field_tick=field_tick)
        return PressureStepResult(
            new_state=new_state,
            pressure=state.pressure,
            at_threshold=False,
            above_threshold=state.pressure >= config.threshold,
            reject_reason=None,
            driven=False,
            drive_g=g,
        )

    dt = float(config.dt_seconds)
    decay = float(config.decay_lambda)
    steady = g / decay
    new_pressure = steady + (state.pressure - steady) * math.exp(-decay * dt)
    # Guard against a negative pressure from binary64 rounding when pressure
    # is already ~0 and g is ~0; this is a non-clamp floor of the accumulator
    # only, never of field state.
    if not math.isfinite(new_pressure) or new_pressure < 0.0:
        new_pressure = 0.0
    new_state = PressureState(pressure=new_pressure, last_field_tick=field_tick)
    at = new_pressure >= config.threshold
    return PressureStepResult(
        new_state=new_state,
        pressure=new_pressure,
        at_threshold=at,
        above_threshold=at,
        reject_reason=None,
        driven=True,
        drive_g=g,
    )


def initial_pressure_state() -> PressureState:
    """Return the pristine pressure state before any observation."""
    return PressureState(pressure=0.0, last_field_tick=None)
