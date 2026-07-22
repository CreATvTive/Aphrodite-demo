"""P1.3 read-only v0 expression-gate projection."""

from __future__ import annotations

from dataclasses import dataclass
import math

from app.chatbox.field_runtime import RegistryProxy


EXPRESSION_GATE_VERSION = "aphrodite.chatbox.expression-gate/1"
V0_GATE_MODE = "v0_all_open"
EXPERIMENTAL_FORCED_GATE_MODE = "experimental_forced"


@dataclass(frozen=True, slots=True)
class GateWeight:
    ordinal: int
    dim_id: str
    weight: float


@dataclass(frozen=True, slots=True)
class GateProjection:
    version: str
    mode: str
    temperature: float
    temperature_applied: bool
    bandwidth: int
    weights: tuple[GateWeight, ...]


class AllOpenGateProjector:
    """Project every registered dimension at weight 1 without state mutation."""

    def __init__(self, *, temperature: float = 1.0, bandwidth: int = 4) -> None:
        if (
            not isinstance(temperature, (int, float))
            or isinstance(temperature, bool)
            or not math.isfinite(temperature)
            or temperature <= 0.0
        ):
            raise ValueError("temperature must be a positive finite number")
        if not isinstance(bandwidth, int) or isinstance(bandwidth, bool) or bandwidth <= 0:
            raise ValueError("bandwidth must be a positive int")
        self._temperature = float(temperature)
        self._bandwidth = bandwidth

    def project(self, registry: RegistryProxy) -> GateProjection:
        if not isinstance(registry, RegistryProxy):
            raise TypeError("registry must be RegistryProxy")
        return GateProjection(
            version=EXPRESSION_GATE_VERSION,
            mode=V0_GATE_MODE,
            temperature=self._temperature,
            temperature_applied=False,
            bandwidth=self._bandwidth,
            weights=tuple(
                GateWeight(index, registration.dim_id, 1.0)
                for index, registration in enumerate(registry.registrations)
            ),
        )


class ForcedTargetGateProjector:
    """P3.9 read-only experimental forced-gate projector.

    Produces a [`GateProjection`](expression_gate.py) of the same shape as
    [`AllOpenGateProjector.project()`](expression_gate.py:48) but with a single
    target ``dim_id`` at weight ``1.0`` and every other registered dimension at
    weight ``0.0``.  Registry order and ordinals are preserved exactly; the
    input [`RegistryProxy`](field_runtime.py:98) is never mutated.

    This projector is *experimental* and lives outside the production dialogue
    path.  It never writes field state, value, velocity, attractor, baseline,
    or OU; it only projects weights.  An unknown ``target_dim_id`` raises a
    stable [`ForcedTargetGateError`](expression_gate.py) so callers can record
    it as ``skipped`` rather than silently producing an all-zero case.
    """

    def __init__(self, *, temperature: float = 1.0, bandwidth: int = 4) -> None:
        if (
            not isinstance(temperature, (int, float))
            or isinstance(temperature, bool)
            or not math.isfinite(temperature)
            or temperature <= 0.0
        ):
            raise ValueError("temperature must be a positive finite number")
        if not isinstance(bandwidth, int) or isinstance(bandwidth, bool) or bandwidth <= 0:
            raise ValueError("bandwidth must be a positive int")
        self._temperature = float(temperature)
        self._bandwidth = bandwidth

    def project(self, registry: RegistryProxy, target_dim_id: str) -> GateProjection:
        if not isinstance(registry, RegistryProxy):
            raise TypeError("registry must be RegistryProxy")
        if not isinstance(target_dim_id, str) or not target_dim_id:
            raise ForcedTargetGateError("invalid_target", "target_dim_id must be a non-empty string")
        registrations = registry.registrations
        if not any(registration.dim_id == target_dim_id for registration in registrations):
            raise ForcedTargetGateError(
                "unknown_target", f"target_dim_id {target_dim_id!r} not registered"
            )
        weights = tuple(
            GateWeight(
                index,
                registration.dim_id,
                1.0 if registration.dim_id == target_dim_id else 0.0,
            )
            for index, registration in enumerate(registrations)
        )
        return GateProjection(
            version=EXPRESSION_GATE_VERSION,
            mode=EXPERIMENTAL_FORCED_GATE_MODE,
            temperature=self._temperature,
            temperature_applied=False,
            bandwidth=self._bandwidth,
            weights=weights,
        )


class ForcedTargetGateError(ValueError):
    """Stable error for the experimental forced-gate projector."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")
