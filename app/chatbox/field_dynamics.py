"""P1.1 full-pool field dynamics for chatbox v0."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import random
from typing import Protocol, Sequence


ATTRACTOR_DISPLACEMENT_RADIUS = 1.801


@dataclass(frozen=True, slots=True)
class DynamicsAnomaly:
    code: str
    dim_id: str | None
    tick: int | None
    stage: str
    detail: str
    baseline: float | None = None
    current_attractor: float | None = None
    delta: float | None = None
    candidate_attractor: float | None = None
    candidate_displacement: float | None = None
    allowed_radius: float | None = None


class DynamicsContractError(ValueError):
    """A structured, explicit violation of the P1.1 dynamics contract."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        dim_id: str | None = None,
        tick: int | None = None,
        stage: str = "validation",
        baseline: float | None = None,
        current_attractor: float | None = None,
        delta: float | None = None,
        candidate_attractor: float | None = None,
        candidate_displacement: float | None = None,
        allowed_radius: float | None = None,
    ) -> None:
        self.anomaly = DynamicsAnomaly(
            code,
            dim_id,
            tick,
            stage,
            detail,
            baseline,
            current_attractor,
            delta,
            candidate_attractor,
            candidate_displacement,
            allowed_radius,
        )
        super().__init__(f"{code}: {detail}")


class InvalidRegistrationError(DynamicsContractError):
    pass


class InvalidAttractorMoveError(DynamicsContractError):
    pass


class InvalidRngDrawError(DynamicsContractError):
    pass


class NonFiniteDynamicsError(DynamicsContractError):
    pass


@dataclass(frozen=True, slots=True)
class DynamicsContractDeclaration:
    dim_id: str
    code: str
    detail: str


def _require_finite_registration(dim_id: str, field: str, value: float) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise InvalidRegistrationError(
            "invalid_registration_parameter",
            f"{field} must be finite",
            dim_id=dim_id or None,
            stage="registration",
        )


@dataclass(frozen=True, slots=True)
class DimensionRegistration:
    dim_id: str
    temporary_name: str
    birth_time: float
    strength: float
    trigger_count: int
    birth_bias: float
    fast_e_fold_s: float
    ou_correlation_e_fold_s: float
    ou_acceleration_sigma: float
    soft_boundary_start: float
    soft_boundary_width: float
    soft_boundary_strength: float

    @property
    def contract_declarations(self) -> tuple[DynamicsContractDeclaration, ...]:
        if self.soft_boundary_strength == 0.0:
            return (
                DynamicsContractDeclaration(
                    self.dim_id,
                    "soft_restoring_disabled",
                    "soft restoring disabled；不承诺 baseline 恢复",
                ),
            )
        return ()

    def __post_init__(self) -> None:
        if not isinstance(self.dim_id, str) or not self.dim_id.strip():
            raise InvalidRegistrationError(
                "invalid_dim_id", "dim_id must be non-empty", stage="registration"
            )
        if not isinstance(self.temporary_name, str) or not self.temporary_name.strip():
            raise InvalidRegistrationError(
                "invalid_temporary_name",
                "temporary_name must be non-empty",
                dim_id=self.dim_id,
                stage="registration",
            )
        for field in (
            "birth_time",
            "strength",
            "birth_bias",
            "fast_e_fold_s",
            "ou_correlation_e_fold_s",
            "ou_acceleration_sigma",
            "soft_boundary_start",
            "soft_boundary_width",
            "soft_boundary_strength",
        ):
            _require_finite_registration(self.dim_id, field, getattr(self, field))
        if not isinstance(self.trigger_count, int) or isinstance(self.trigger_count, bool):
            raise InvalidRegistrationError(
                "invalid_trigger_count",
                "trigger_count must be an integer",
                dim_id=self.dim_id,
                stage="registration",
            )
        if self.trigger_count < 0:
            raise InvalidRegistrationError(
                "invalid_trigger_count",
                "trigger_count must be non-negative",
                dim_id=self.dim_id,
                stage="registration",
            )
        if self.fast_e_fold_s <= 0.0:
            raise InvalidRegistrationError(
                "invalid_fast_e_fold",
                "fast_e_fold_s must be positive",
                dim_id=self.dim_id,
                stage="registration",
            )
        if self.ou_correlation_e_fold_s <= 0.0:
            raise InvalidRegistrationError(
                "invalid_ou_correlation_e_fold",
                "ou_correlation_e_fold_s must be positive",
                dim_id=self.dim_id,
                stage="registration",
            )
        if self.ou_acceleration_sigma < 0.0:
            raise InvalidRegistrationError(
                "invalid_ou_sigma",
                "ou_acceleration_sigma must be non-negative",
                dim_id=self.dim_id,
                stage="registration",
            )
        if self.soft_boundary_start < 0.0:
            raise InvalidRegistrationError(
                "invalid_soft_boundary_start",
                "soft_boundary_start must be non-negative",
                dim_id=self.dim_id,
                stage="registration",
            )
        if self.soft_boundary_width <= 0.0:
            raise InvalidRegistrationError(
                "invalid_soft_boundary_width",
                "soft_boundary_width must be positive",
                dim_id=self.dim_id,
                stage="registration",
            )
        if self.soft_boundary_strength < 0.0:
            raise InvalidRegistrationError(
                "invalid_soft_boundary_strength",
                "soft_boundary_strength must be non-negative",
                dim_id=self.dim_id,
                stage="registration",
            )
        spring_coefficient = (1.0 / self.fast_e_fold_s) ** 2
        damping_coefficient = 2.0 / self.fast_e_fold_s
        if (
            not math.isfinite(spring_coefficient)
            or spring_coefficient <= 0.0
            or 1.0 - spring_coefficient == 1.0
            or not math.isfinite(damping_coefficient)
            or damping_coefficient <= 0.0
            or 1.0 - damping_coefficient == 1.0
        ):
            raise InvalidRegistrationError(
                "degenerate_spring_damper_coefficients",
                "derived spring and damping coefficients must be finite, positive, and "
                "distinguishable in the 1-second binary64 update",
                dim_id=self.dim_id,
                stage="registration",
            )
        ou_rho = math.exp(-1.0 / self.ou_correlation_e_fold_s)
        ou_innovation_variance = 1.0 - ou_rho * ou_rho
        ou_innovation_scale = self.ou_acceleration_sigma * math.sqrt(
            ou_innovation_variance
        )
        if (
            not math.isfinite(ou_rho)
            or not 0.0 < ou_rho < 1.0
            or not math.isfinite(ou_innovation_variance)
            or ou_innovation_variance <= 0.0
            or not math.isfinite(ou_innovation_scale)
            or (self.ou_acceleration_sigma > 0.0 and ou_innovation_scale <= 0.0)
        ):
            raise InvalidRegistrationError(
                "degenerate_ou_coefficients",
                "derived OU rho and innovation must be finite, non-degenerate, and "
                "distinguishable in the binary64 update",
                dim_id=self.dim_id,
                stage="registration",
            )
        # Derived parameter domain: 1-second semi-implicit critical spring-damper
        # no-ringing.  With dt=1, omega = 1/fast_e_fold_s, k = omega^2, c = 2*omega,
        # the semi-implicit Euler eigenvalues are real and in [0, 1) iff omega <= 0.5.
        if (1.0 / self.fast_e_fold_s) > 0.5:
            raise InvalidRegistrationError(
                "spring_damper_ringing",
                "fast_e_fold_s must be >= 2.0 for 1-second semi-implicit "
                "critical spring-damper no-ringing",
                dim_id=self.dim_id,
                stage="registration",
            )
        # Derived parameter domain: proposal-sampled soft restoring mapping must
        # remain strictly monotonic and numerically distinguishable in its linear tail.
        soft_mapping_slope = 1.0 - self.soft_boundary_strength
        if self.soft_boundary_strength > 0.0 and (
            not 0.0 < soft_mapping_slope < 1.0
            or 1.0 + soft_mapping_slope == 1.0
        ):
            raise InvalidRegistrationError(
                "soft_mapping_not_strict_monotonic",
                "soft_boundary_strength must preserve a strict monotonic/non-degenerate mapping",
                dim_id=self.dim_id,
                stage="registration",
            )


def build_birth_registry() -> tuple[DimensionRegistration, ...]:
    """Return the frozen, ordered P1.1 birth registry."""

    return (
        DimensionRegistration("birth_00", "能量", 0.0, 1.0, 0, 0.0, 600.0, 10_800.0, 4.0e-7, 1.0, 0.25, (1.0 / 120.0) ** 2),
        DimensionRegistration("birth_01", "开放", 0.0, 1.0, 0, -0.2, 600.0, 10_800.0, 4.0e-7, 1.0, 0.25, (1.0 / 120.0) ** 2),
        DimensionRegistration("birth_02", "稳定", 0.0, 1.0, 0, -0.1, 600.0, 10_800.0, 4.0e-7, 1.0, 0.25, (1.0 / 120.0) ** 2),
        DimensionRegistration("birth_03", "朝向你", 0.0, 1.0, 0, 0.0, 600.0, 10_800.0, 4.0e-7, 1.0, 0.25, (1.0 / 120.0) ** 2),
        DimensionRegistration("birth_04", "好奇", 0.0, 1.0, 0, 0.0, 600.0, 10_800.0, 4.0e-7, 1.0, 0.25, (1.0 / 120.0) ** 2),
        DimensionRegistration("birth_05", "愉悦", 0.0, 1.0, 0, 0.0, 600.0, 10_800.0, 4.0e-7, 1.0, 0.25, (1.0 / 120.0) ** 2),
        DimensionRegistration("birth_06", "紧张", 0.0, 1.0, 0, 0.0, 600.0, 10_800.0, 4.0e-7, 1.0, 0.25, (1.0 / 120.0) ** 2),
        DimensionRegistration("birth_07", "疲惫", 0.0, 1.0, 0, 0.0, 600.0, 10_800.0, 4.0e-7, 1.0, 0.25, (1.0 / 120.0) ** 2),
        DimensionRegistration("birth_08", "安全感", 0.0, 1.0, 0, 0.0, 600.0, 10_800.0, 4.0e-7, 1.0, 0.25, (1.0 / 120.0) ** 2),
        DimensionRegistration("birth_09", "期待", 0.0, 1.0, 0, 0.0, 600.0, 10_800.0, 4.0e-7, 1.0, 0.25, (1.0 / 120.0) ** 2),
        DimensionRegistration("birth_10", "沉郁", 0.0, 1.0, 0, 0.0, 600.0, 10_800.0, 4.0e-7, 1.0, 0.25, (1.0 / 120.0) ** 2),
        DimensionRegistration("birth_11", "玩兴", 0.0, 1.0, 0, 0.0, 600.0, 10_800.0, 4.0e-7, 1.0, 0.25, (1.0 / 120.0) ** 2),
    )


@dataclass(frozen=True, slots=True)
class DimensionSnapshot:
    registration: DimensionRegistration
    value: float
    velocity: float
    attractor: float
    soft_restoring_baseline: float
    ou_acceleration: float

    @property
    def dim_id(self) -> str:
        return self.registration.dim_id

    @property
    def temporary_name(self) -> str:
        return self.registration.temporary_name

    @property
    def strength(self) -> float:
        return self.registration.strength

    @property
    def trigger_count(self) -> int:
        return self.registration.trigger_count


@dataclass(frozen=True, slots=True)
class FieldSnapshot:
    tick: int
    dimensions: tuple[DimensionSnapshot, ...]


@dataclass(frozen=True, slots=True)
class AttractorMove:
    dim_id: str
    delta: float
    source: str
    rationale: str


@dataclass(frozen=True, slots=True)
class RngDraw:
    seed: int
    stream: str
    draw_index: int
    value: float


class GaussianRng(Protocol):
    def draw(self, draw_index: int) -> RngDraw:
        ...


class GaussianRngFactory(Protocol):
    def create(self, stream: str) -> GaussianRng:
        ...


class _SeededGaussianRng:
    __slots__ = ("_master_seed", "_stream", "_digest", "_random", "_draw_index")

    def __init__(self, master_seed: int, stream: str) -> None:
        digest = hashlib.sha256(f"{master_seed}:{stream}".encode("utf-8")).digest()
        self._master_seed = master_seed
        self._stream = stream
        self._digest = digest
        self._random = random.Random(int.from_bytes(digest[:16], "big"))
        self._draw_index = 0

    def draw(self, draw_index: int) -> RngDraw:
        if draw_index != self._draw_index:
            self._random = random.Random(int.from_bytes(self._digest[:16], "big"))
            for _ in range(draw_index):
                self._random.gauss(0.0, 1.0)
            self._draw_index = draw_index
        draw = RngDraw(
            seed=self._master_seed,
            stream=self._stream,
            draw_index=self._draw_index,
            value=self._random.gauss(0.0, 1.0),
        )
        self._draw_index += 1
        return draw


class SeededGaussianRngFactory:
    __slots__ = ("seed",)

    def __init__(self, seed: int) -> None:
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise DynamicsContractError("invalid_rng_seed", "seed must be an integer", stage="rng_factory")
        self.seed = seed

    def create(self, stream: str) -> GaussianRng:
        if not isinstance(stream, str) or not stream:
            raise DynamicsContractError("invalid_rng_stream", "stream must be non-empty", stage="rng_factory")
        return _SeededGaussianRng(self.seed, stream)


@dataclass(frozen=True, slots=True)
class DimensionTickObservation:
    dim_id: str
    before_value: float
    before_velocity: float
    before_attractor: float
    before_soft_restoring_baseline: float
    before_ou_acceleration: float
    spring_coefficient: float
    damping_coefficient: float
    spring_acceleration: float
    damping_acceleration: float
    ou_rho: float
    ou_innovation_scale: float
    rng_draw: RngDraw
    after_ou_acceleration: float
    acceleration_without_soft_restoring: float
    velocity_proposal: float
    pre_boundary_value_proposal: float
    soft_boundary_displacement: float
    soft_boundary_excess: float
    soft_restoring_acceleration: float
    after_value: float
    after_velocity: float
    after_attractor: float
    after_soft_restoring_baseline: float
    anomalies: tuple[DynamicsAnomaly, ...]


@dataclass(frozen=True, slots=True)
class TickObservation:
    tick_before: int
    tick_after: int
    dimensions: tuple[DimensionTickObservation, ...]
    anomalies: tuple[DynamicsAnomaly, ...]


@dataclass(slots=True)
class _DimensionState:
    registration: DimensionRegistration
    value: float
    velocity: float
    attractor: float
    soft_restoring_baseline: float
    ou_acceleration: float


def _soft_restoring_acceleration(
    proposal: float, baseline: float, registration: DimensionRegistration
) -> tuple[float, float, float]:
    displacement = proposal - baseline
    excess = abs(displacement) - registration.soft_boundary_start
    if excess <= 0.0:
        return displacement, excess, 0.0
    if excess < registration.soft_boundary_width:
        magnitude = (
            registration.soft_boundary_strength
            * excess
            * excess
            / (2.0 * registration.soft_boundary_width)
        )
    else:
        magnitude = registration.soft_boundary_strength * (
            excess - registration.soft_boundary_width / 2.0
        )
    return displacement, excess, -math.copysign(magnitude, displacement)


class FieldDynamics:
    """Own and atomically advance every registered field dimension."""

    __slots__ = (
        "_registrations",
        "_states",
        "_rngs",
        "_tick",
        "_rng_seeds",
        "_coefficients",
    )

    def __init__(
        self,
        registry: Sequence[DimensionRegistration] | None = None,
        *,
        rng_factory: SeededGaussianRngFactory | None = None,
    ) -> None:
        registrations = tuple(build_birth_registry() if registry is None else registry)
        if not registrations:
            raise InvalidRegistrationError(
                "empty_registry", "registry must contain at least one dimension", stage="registry"
            )
        dim_ids = tuple(registration.dim_id for registration in registrations)
        if len(set(dim_ids)) != len(dim_ids):
            raise InvalidRegistrationError(
                "duplicate_dim_id", "registry dim_id values must be unique", stage="registry"
            )
        factory = rng_factory if rng_factory is not None else SeededGaussianRngFactory(0)
        # P1-RNG-BOUNDARY-001: exact nominal type gate — factory must be the
        # production SeededGaussianRngFactory (not a subclass, Protocol, or
        # duck-typed substitute).  This gate fires before any create() call.
        if type(factory) is not SeededGaussianRngFactory:
            raise DynamicsContractError(
                "unsupported_rng_factory",
                "rng_factory must be an exact SeededGaussianRngFactory instance",
                stage="rng_factory",
            )
        candidate_rngs: list[_SeededGaussianRng] = []
        for dim_id in dim_ids:
            try:
                rng = factory.create(dim_id)
            except DynamicsContractError:
                raise
            except Exception as exc:
                raise DynamicsContractError(
                    "rng_factory_failure",
                    "factory.create() raised an exception",
                    stage="rng_factory",
                ) from exc
            # P1-RNG-BOUNDARY-001: validate each result immediately, before
            # requesting the next stream.  Candidate providers remain local
            # until every stream succeeds, so no partial field is published.
            if type(rng) is not _SeededGaussianRng:
                raise DynamicsContractError(
                    "unsupported_rng_provider",
                    "factory.create() returned an unsupported RNG provider",
                    dim_id=dim_id,
                    stage="rng_factory",
                )
            candidate_rngs.append(rng)
        rngs = tuple(candidate_rngs)
        self._registrations = registrations
        self._rngs = rngs
        self._states = [
            _DimensionState(
                registration=registration,
                value=registration.birth_bias,
                velocity=0.0,
                attractor=registration.birth_bias,
                soft_restoring_baseline=registration.birth_bias,
                ou_acceleration=0.0,
            )
            for registration in registrations
        ]
        # Registrations are frozen for the lifetime of this P1 field.  Derive
        # their invariant binary64 coefficients once rather than repeating
        # four divisions/transcendentals per dimension on every tick.  Keep
        # the exact expression order used by the numerical update contract.
        coefficients: list[tuple[float, float, float, float]] = []
        for registration in registrations:
            spring_coefficient = (1.0 / registration.fast_e_fold_s) ** 2
            damping_coefficient = 2.0 / registration.fast_e_fold_s
            ou_rho = math.exp(-1.0 / registration.ou_correlation_e_fold_s)
            ou_innovation_scale = registration.ou_acceleration_sigma * math.sqrt(
                1.0 - ou_rho * ou_rho
            )
            coefficients.append(
                (
                    spring_coefficient,
                    damping_coefficient,
                    ou_rho,
                    ou_innovation_scale,
                )
            )
        self._coefficients = tuple(coefficients)
        self._tick = 0
        self._rng_seeds: list[int | None] = [None] * len(registrations)

    @property
    def registry(self) -> tuple[DimensionRegistration, ...]:
        return self._registrations

    def snapshot(self) -> FieldSnapshot:
        return FieldSnapshot(
            tick=self._tick,
            dimensions=tuple(
                DimensionSnapshot(
                    registration=state.registration,
                    value=state.value,
                    velocity=state.velocity,
                    attractor=state.attractor,
                    soft_restoring_baseline=state.soft_restoring_baseline,
                    ou_acceleration=state.ou_acceleration,
                )
                for state in self._states
            ),
        )

    def move_attractor(self, move: AttractorMove) -> FieldSnapshot:
        if not isinstance(move, AttractorMove):
            raise InvalidAttractorMoveError(
                "invalid_attractor_command", "move must be an AttractorMove", stage="attractor_command"
            )
        if not isinstance(move.dim_id, str) or not move.dim_id:
            raise InvalidAttractorMoveError(
                "invalid_attractor_dim_id", "dim_id must be non-empty", stage="attractor_command"
            )
        if not isinstance(move.delta, (int, float)) or isinstance(move.delta, bool) or not math.isfinite(move.delta):
            raise InvalidAttractorMoveError(
                "non_finite_attractor_delta",
                "delta must be finite",
                dim_id=move.dim_id,
                tick=self._tick,
                stage="attractor_command",
            )
        if not isinstance(move.source, str) or not move.source.strip():
            raise InvalidAttractorMoveError(
                "empty_attractor_source",
                "source must be non-empty",
                dim_id=move.dim_id,
                tick=self._tick,
                stage="attractor_command",
            )
        if not isinstance(move.rationale, str) or not move.rationale.strip():
            raise InvalidAttractorMoveError(
                "empty_attractor_rationale",
                "rationale must be non-empty",
                dim_id=move.dim_id,
                tick=self._tick,
                stage="attractor_command",
            )
        index = next(
            (index for index, state in enumerate(self._states) if state.registration.dim_id == move.dim_id),
            None,
        )
        if index is None:
            raise InvalidAttractorMoveError(
                "unknown_attractor_dim_id",
                "dim_id is not registered",
                dim_id=move.dim_id,
                tick=self._tick,
                stage="attractor_command",
            )
        state = self._states[index]
        candidate = state.attractor + float(move.delta)
        if not math.isfinite(candidate):
            raise InvalidAttractorMoveError(
                "non_finite_attractor_result",
                "attractor result must be finite",
                dim_id=move.dim_id,
                tick=self._tick,
                stage="attractor_command",
            )
        lower_bound = state.soft_restoring_baseline - ATTRACTOR_DISPLACEMENT_RADIUS
        upper_bound = state.soft_restoring_baseline + ATTRACTOR_DISPLACEMENT_RADIUS
        candidate_displacement = candidate - state.soft_restoring_baseline
        if candidate < lower_bound or candidate > upper_bound:
            raise InvalidAttractorMoveError(
                "attractor_displacement_out_of_domain",
                "candidate attractor displacement exceeds the closed baseline-relative contract domain",
                dim_id=move.dim_id,
                tick=self._tick,
                stage="attractor_command",
                baseline=state.soft_restoring_baseline,
                current_attractor=state.attractor,
                delta=float(move.delta),
                candidate_attractor=candidate,
                candidate_displacement=candidate_displacement,
                allowed_radius=ATTRACTOR_DISPLACEMENT_RADIUS,
            )
        state.attractor = candidate
        return self.snapshot()

    def tick(self) -> TickObservation:
        candidate_states: list[_DimensionState] = []
        observations: list[DimensionTickObservation] = []
        # A successful tick observes every stream atomically, so seeds are
        # either all unknown (before the first committed tick) or all known.
        # Only the first case needs a transactional copy.
        candidate_seeds = (
            list(self._rng_seeds) if self._rng_seeds[0] is None else None
        )
        next_tick = self._tick + 1
        for dim_index, (state, rng, coefficients) in enumerate(
            zip(self._states, self._rngs, self._coefficients)
        ):
            registration = state.registration
            try:
                draw = rng.draw(self._tick)
            except DynamicsContractError:
                raise
            except Exception as exc:
                raise InvalidRngDrawError(
                    "rng_draw_failure",
                    str(exc),
                    dim_id=registration.dim_id,
                    tick=next_tick,
                    stage="rng_draw",
                ) from exc
            if not isinstance(draw, RngDraw):
                raise InvalidRngDrawError(
                    "invalid_rng_draw_type",
                    "draw must be RngDraw",
                    dim_id=registration.dim_id,
                    tick=next_tick,
                    stage="rng_draw",
                )
            if (
                not isinstance(draw.seed, int)
                or isinstance(draw.seed, bool)
                or draw.stream != registration.dim_id
                or not isinstance(draw.draw_index, int)
                or isinstance(draw.draw_index, bool)
                or draw.draw_index < 0
                or draw.draw_index != self._tick
                or not isinstance(draw.value, (int, float))
                or isinstance(draw.value, bool)
                or not math.isfinite(draw.value)
            ):
                raise InvalidRngDrawError(
                    "invalid_rng_draw",
                    "seed, stream, draw_index, and value must match the registered finite stream",
                    dim_id=registration.dim_id,
                    tick=next_tick,
                    stage="rng_draw",
                )
            known_seed = (
                self._rng_seeds[dim_index]
                if candidate_seeds is None
                else candidate_seeds[dim_index]
            )
            if known_seed is not None and draw.seed != known_seed:
                raise InvalidRngDrawError(
                    "invalid_rng_draw",
                    "seed does not match previously observed seed",
                    dim_id=registration.dim_id,
                    tick=next_tick,
                    stage="rng_draw",
                )
            if candidate_seeds is not None:
                candidate_seeds[dim_index] = draw.seed

            (
                spring_coefficient,
                damping_coefficient,
                ou_rho,
                ou_innovation_scale,
            ) = coefficients
            after_ou = (
                ou_rho * state.ou_acceleration
                + ou_innovation_scale * draw.value
            )
            spring = spring_coefficient * (state.attractor - state.value)
            damping = -damping_coefficient * state.velocity
            acceleration_without_soft = spring + damping + after_ou
            velocity_proposal = state.velocity + acceleration_without_soft
            value_proposal = state.value + velocity_proposal
            displacement, excess, soft_restoring = _soft_restoring_acceleration(
                value_proposal, state.soft_restoring_baseline, registration
            )
            after_velocity = velocity_proposal + soft_restoring
            after_value = value_proposal + soft_restoring

            if not (
                math.isfinite(spring_coefficient)
                and math.isfinite(damping_coefficient)
                and math.isfinite(ou_rho)
                and math.isfinite(ou_innovation_scale)
                and math.isfinite(after_ou)
                and math.isfinite(spring)
                and math.isfinite(damping)
                and math.isfinite(acceleration_without_soft)
                and math.isfinite(velocity_proposal)
                and math.isfinite(value_proposal)
                and math.isfinite(displacement)
                and math.isfinite(excess)
                and math.isfinite(soft_restoring)
                and math.isfinite(after_velocity)
                and math.isfinite(after_value)
            ):
                raise NonFiniteDynamicsError(
                    "non_finite_dynamics_candidate",
                    "tick candidate contains NaN or infinity",
                    dim_id=registration.dim_id,
                    tick=next_tick,
                    stage="candidate_validation",
                )

            candidate_states.append(
                _DimensionState(
                    registration=registration,
                    value=after_value,
                    velocity=after_velocity,
                    attractor=state.attractor,
                    soft_restoring_baseline=state.soft_restoring_baseline,
                    ou_acceleration=after_ou,
                )
            )
            observations.append(
                DimensionTickObservation(
                    dim_id=registration.dim_id,
                    before_value=state.value,
                    before_velocity=state.velocity,
                    before_attractor=state.attractor,
                    before_soft_restoring_baseline=state.soft_restoring_baseline,
                    before_ou_acceleration=state.ou_acceleration,
                    spring_coefficient=spring_coefficient,
                    damping_coefficient=damping_coefficient,
                    spring_acceleration=spring,
                    damping_acceleration=damping,
                    ou_rho=ou_rho,
                    ou_innovation_scale=ou_innovation_scale,
                    rng_draw=draw,
                    after_ou_acceleration=after_ou,
                    acceleration_without_soft_restoring=acceleration_without_soft,
                    velocity_proposal=velocity_proposal,
                    pre_boundary_value_proposal=value_proposal,
                    soft_boundary_displacement=displacement,
                    soft_boundary_excess=excess,
                    soft_restoring_acceleration=soft_restoring,
                    after_value=after_value,
                    after_velocity=after_velocity,
                    after_attractor=state.attractor,
                    after_soft_restoring_baseline=state.soft_restoring_baseline,
                    anomalies=(),
                )
            )

        self._states = candidate_states
        if candidate_seeds is not None:
            self._rng_seeds = candidate_seeds
        self._tick = next_tick
        return TickObservation(
            tick_before=next_tick - 1,
            tick_after=next_tick,
            dimensions=tuple(observations),
            anomalies=(),
        )

    def _export_field_recovery_state(self) -> dict:
        """Field-owned lifecycle export hook (P1.2-A).

        Returns a fresh dict carrying only field-owned recovery state
        (field_tick, registry, dimensions, slow_state baselines, rng
        streams).  Version constants are intentionally omitted; the capsule
        layer owns them.  The registry is emitted as the ordered
        DimensionRegistration tuple directly, without copying registration
        semantics.  The live RNG object is never exposed; only its
        deterministic master seed, stream id and draw cursor are emitted.
        Non-seeded live RNG providers are rejected fail-closed.  This hook
        only reads live state; it never mutates it.
        """
        dimension_items: list[dict] = []
        baseline_items: list[dict] = []
        rng_streams: list[dict] = []
        for index, state in enumerate(self._states):
            registration = state.registration
            rng = self._rngs[index]
            if type(rng) is not _SeededGaussianRng:
                raise DynamicsContractError(
                    "unsupported_live_rng",
                    "live RNG is not the seeded stdlib provider",
                    dim_id=registration.dim_id,
                    tick=self._tick,
                    stage="capture",
                )
            dimension_items.append(
                {
                    "dim_id": registration.dim_id,
                    "value": state.value,
                    "velocity": state.velocity,
                    "attractor": state.attractor,
                    "ou_acceleration": state.ou_acceleration,
                }
            )
            baseline_items.append(
                {
                    "dim_id": registration.dim_id,
                    "current_baseline": state.soft_restoring_baseline,
                }
            )
            rng_streams.append(
                {
                    "stream": registration.dim_id,
                    "seed": rng._master_seed,
                    "next_cursor": self._tick,
                }
            )
        return {
            "field_tick": self._tick,
            "registry": self._registrations,
            "dimensions": dimension_items,
            "slow_state": {"baselines": baseline_items},
            "rng": {"streams": rng_streams},
        }

    @classmethod
    def _build_field_recovery_candidate(cls, primitive: dict) -> "FieldDynamics":
        """Field-owned lifecycle candidate construction hook (P1.2-A).

        Builds a brand-new, unpublished FieldDynamics from a validated
        primitive, then installs the verified private state, tick and RNG
        cursor trace directly on the unpublished object.  The registry is
        consumed as the ordered DimensionRegistration tuple directly, without
        re-copying registration semantics.  ``move_attractor`` is never used
        to simulate recovery.  The candidate is returned isolated; it is
        never installed into any live runtime.
        """
        registry = tuple(primitive["registry"])
        streams = primitive["rng"]["streams"]
        common_seed = streams[0]["seed"]
        candidate = cls(registry, rng_factory=SeededGaussianRngFactory(common_seed))
        registration_by_id = {
            state.registration.dim_id: state.registration
            for state in candidate._states
        }
        baseline_by_id = {
            item["dim_id"]: item["current_baseline"]
            for item in primitive["slow_state"]["baselines"]
        }
        new_states: list[_DimensionState] = []
        for dim_item in primitive["dimensions"]:
            new_states.append(
                _DimensionState(
                    registration=registration_by_id[dim_item["dim_id"]],
                    value=dim_item["value"],
                    velocity=dim_item["velocity"],
                    attractor=dim_item["attractor"],
                    soft_restoring_baseline=baseline_by_id[dim_item["dim_id"]],
                    ou_acceleration=dim_item["ou_acceleration"],
                )
            )
        candidate._states = new_states
        field_tick = primitive["field_tick"]
        candidate._tick = field_tick
        candidate._rng_seeds = [
            common_seed if field_tick > 0 else None for _ in streams
        ]
        return candidate
