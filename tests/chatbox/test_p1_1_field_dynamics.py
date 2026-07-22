from __future__ import annotations

import ast
from dataclasses import dataclass, FrozenInstanceError, replace
import inspect
import math
from pathlib import Path
import statistics
import struct

import pytest

from app.chatbox.field_dynamics import (
    ATTRACTOR_DISPLACEMENT_RADIUS,
    AttractorMove,
    DimensionRegistration,
    DynamicsContractError,
    FieldDynamics,
    InvalidAttractorMoveError,
    InvalidRegistrationError,
    InvalidRngDrawError,
    RngDraw,
    SeededGaussianRngFactory,
    build_birth_registry,
)


PRODUCTION_SEED = 0xA9F0D17E
MILLION_TICK_COUNT = 1_000_000
MILLION_CHECKPOINT_STRIDE = 10_000


def _install_test_rngs(dynamics: FieldDynamics, rngs) -> FieldDynamics:
    """Test-private: install custom RNG objects after legal construction."""
    candidate_rngs = tuple(rngs)
    if len(candidate_rngs) != len(dynamics.registry):
        raise ValueError(
            f"rng count {len(candidate_rngs)} != registry length {len(dynamics.registry)}"
        )
    dynamics._rngs = candidate_rngs
    return dynamics


def _binary64_projection(value):
    """Project nested evidence with every float represented by exact bytes."""
    if isinstance(value, float):
        return ("binary64", struct.pack(">d", value))
    if isinstance(value, tuple):
        return tuple(_binary64_projection(item) for item in value)
    if isinstance(value, list):
        return tuple(_binary64_projection(item) for item in value)
    if hasattr(value, "__dataclass_fields__"):
        return (
            type(value).__name__,
            tuple(
                (name, _binary64_projection(getattr(value, name)))
                for name in value.__dataclass_fields__
            ),
        )
    return value


def _construct_test_dynamics(
    registry=None, *, rng_factory=None
) -> FieldDynamics:
    """Construct legally and centralize any test-private RNG installation."""
    if rng_factory is None or type(rng_factory) is SeededGaussianRngFactory:
        return FieldDynamics(registry, rng_factory=rng_factory)
    dynamics = FieldDynamics(registry)
    return _install_test_rngs(
        dynamics,
        (rng_factory.create(item.dim_id) for item in dynamics.registry),
    )


class _ConstantRng:
    def __init__(self, seed: int, stream: str, value: float) -> None:
        self.seed = seed
        self.stream = stream
        self.value = value
        self.index = 0

    def draw(self, draw_index: int) -> RngDraw:
        draw = RngDraw(self.seed, self.stream, draw_index, self.value)
        self.index = draw_index + 1
        return draw


class ConstantGaussianRngFactory:
    def __init__(self, value: float = 0.0, seed: int = -1) -> None:
        self.value = value
        self.seed = seed

    def create(self, stream: str) -> _ConstantRng:
        return _ConstantRng(self.seed, stream, self.value)


def _synthetic_registration(dim_id: str, bias: float = 0.0) -> DimensionRegistration:
    return DimensionRegistration(
        dim_id=dim_id,
        temporary_name=f"synthetic-{dim_id}",
        birth_time=17.0,
        strength=1.0,
        trigger_count=0,
        birth_bias=bias,
        fast_e_fold_s=600.0,
        ou_correlation_e_fold_s=10_800.0,
        ou_acceleration_sigma=4.0e-7,
        soft_boundary_start=1.0,
        soft_boundary_width=0.25,
        soft_boundary_strength=(1.0 / 120.0) ** 2,
    )


def _observation_numbers(observation) -> tuple[float, ...]:
    return (
        observation.before_value,
        observation.before_velocity,
        observation.before_attractor,
        observation.before_soft_restoring_baseline,
        observation.before_ou_acceleration,
        observation.spring_coefficient,
        observation.damping_coefficient,
        observation.spring_acceleration,
        observation.damping_acceleration,
        observation.ou_rho,
        observation.ou_innovation_scale,
        observation.rng_draw.value,
        observation.after_ou_acceleration,
        observation.acceleration_without_soft_restoring,
        observation.velocity_proposal,
        observation.pre_boundary_value_proposal,
        observation.soft_boundary_displacement,
        observation.soft_boundary_excess,
        observation.soft_restoring_acceleration,
        observation.after_value,
        observation.after_velocity,
        observation.after_attractor,
        observation.after_soft_restoring_baseline,
    )


def _independent_soft_restoring(
    proposal: float, baseline: float, registration: DimensionRegistration
) -> tuple[float, float, float]:
    """Test-side frozen soft restoring formula (independent of production helper)."""
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


def _independent_reconstruct_dimension(
    before_value: float,
    before_velocity: float,
    before_attractor: float,
    before_ou: float,
    before_baseline: float,
    z: float,
    registration: DimensionRegistration,
) -> dict[str, float]:
    """Independently reconstruct all tick dynamics from registration params and raw z."""
    lam = 1.0 / registration.fast_e_fold_s
    spring_coeff = lam * lam
    damping_coeff = 2.0 * lam
    rho = math.exp(-1.0 / registration.ou_correlation_e_fold_s)
    scale = registration.ou_acceleration_sigma * math.sqrt(1.0 - rho * rho)
    after_ou = rho * before_ou + scale * z
    spring = spring_coeff * (before_attractor - before_value)
    damping = -damping_coeff * before_velocity
    accel_no_soft = spring + damping + after_ou
    velocity_proposal = before_velocity + accel_no_soft
    value_proposal = before_value + velocity_proposal
    _, _, soft = _independent_soft_restoring(value_proposal, before_baseline, registration)
    after_velocity = velocity_proposal + soft
    after_value = value_proposal + soft
    return {
        "spring_coefficient": spring_coeff,
        "damping_coefficient": damping_coeff,
        "ou_rho": rho,
        "ou_innovation_scale": scale,
        "after_ou_acceleration": after_ou,
        "spring_acceleration": spring,
        "damping_acceleration": damping,
        "acceleration_without_soft_restoring": accel_no_soft,
        "velocity_proposal": velocity_proposal,
        "pre_boundary_value_proposal": value_proposal,
        "soft_restoring_acceleration": soft,
        "after_velocity": after_velocity,
        "after_value": after_value,
    }


class _ControlledRng:
    """RNG that returns deterministic draws with explicit seed/stream/index."""

    def __init__(self, seed: int, stream: str, value: float = 0.0) -> None:
        self._seed = seed
        self._stream = stream
        self._value = value
        self._index = 0

    def draw(self, draw_index: int) -> RngDraw:
        draw = RngDraw(self._seed, self._stream, draw_index, self._value)
        self._index = draw_index + 1
        return draw


class _ControlledRngFactory:
    """Factory producing _ControlledRng with per-stream value overrides."""

    def __init__(self, default_value: float = 0.0, seed: int = 42) -> None:
        self._default_value = default_value
        self._seed = seed
        self._overrides: dict[str, float] = {}

    def set_value(self, stream: str, value: float) -> None:
        self._overrides[stream] = value

    def create(self, stream: str) -> _ControlledRng:
        value = self._overrides.get(stream, self._default_value)
        return _ControlledRng(self._seed, stream, value)


class _LateFailureRng:
    """RNG that returns valid draws until a target index, then returns a bad draw."""

    def __init__(self, seed: int, stream: str, bad_value: float, bad_from_index: int) -> None:
        self._seed = seed
        self._stream = stream
        self._bad_value = bad_value
        self._bad_from_index = bad_from_index
        self._index = 0

    def draw(self, draw_index: int) -> RngDraw:
        value = self._bad_value if draw_index >= self._bad_from_index else 0.0
        draw = RngDraw(self._seed, self._stream, draw_index, value)
        self._index = draw_index + 1
        return draw


class _BadRngFactory:
    """Factory that produces a custom bad RNG for one stream, normal for others."""

    def __init__(self, fail_stream: str, bad_rng_cls: type, seed: int = 42) -> None:
        self._fail_stream = fail_stream
        self._bad_rng_cls = bad_rng_cls
        self._seed = seed

    def create(self, stream: str):
        if stream == self._fail_stream:
            return self._bad_rng_cls(self._seed, stream)
        return _ControlledRng(self._seed, stream, 0.0)


class _WrongTypeRng:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def draw(self, draw_index: int) -> object:
        return {"seed": 42, "stream": "x", "draw_index": 0, "value": 0.0}


class _MismatchedStreamRng:
    def __init__(self, seed: int, stream: str) -> None:
        self._seed = seed
        self._stream = stream
        self._index = 0

    def draw(self, draw_index: int) -> RngDraw:
        draw = RngDraw(self._seed, "wrong_stream", draw_index, 0.0)
        self._index = draw_index + 1
        return draw


class _MismatchedIndexRng:
    def __init__(self, seed: int, stream: str, offset: int = 100) -> None:
        self._seed = seed
        self._stream = stream
        self._offset = offset
        self._index = 0

    def draw(self, draw_index: int) -> RngDraw:
        draw = RngDraw(self._seed, self._stream, draw_index + self._offset, 0.0)
        self._index = draw_index + 1
        return draw


class _SeedFlipRng:
    def __init__(self, seed: int, stream: str) -> None:
        self._seed = seed
        self._stream = stream
        self._index = 0

    def draw(self, draw_index: int) -> RngDraw:
        seed = self._seed if draw_index == 0 else self._seed + 1
        draw = RngDraw(seed, self._stream, draw_index, 0.0)
        self._index = draw_index + 1
        return draw


class _CountingUnsupportedFactory:
    def __init__(self) -> None:
        self.create_calls = 0

    def create(self, stream: str):
        self.create_calls += 1
        raise AssertionError("unsupported factory must not be called")


class _SeededFactorySubclass(SeededGaussianRngFactory):
    pass


class _TypeNameBombMeta(type):
    def __getattribute__(cls, name: str):
        if name == "__name__":
            raise RuntimeError("untrusted type name must not be read")
        return super().__getattribute__(name)


class _NameBombUnsupportedFactory(metaclass=_TypeNameBombMeta):
    def __init__(self) -> None:
        self.create_calls = 0
        self.draw_calls = 0

    def create(self, stream: str):
        self.create_calls += 1

        class _Provider:
            def draw(inner_self, draw_index: int):
                self.draw_calls += 1
                raise AssertionError("unsupported factory provider must not draw")

        return _Provider()


class _NameBombUnsupportedProvider(metaclass=_TypeNameBombMeta):
    def __init__(self) -> None:
        self.draw_calls = 0

    def draw(self, draw_index: int):
        self.draw_calls += 1
        raise AssertionError("unsupported provider must not draw")


class _StringBombFactoryError(Exception):
    def __str__(self) -> str:
        raise RuntimeError("factory exception string must not be read")


def test_rng_factory_name_bomb_is_rejected_without_create_or_draw() -> None:
    registry = (_synthetic_registration("factory-name-bomb"),)
    unsupported = _NameBombUnsupportedFactory()
    dynamics = None

    with pytest.raises(DynamicsContractError) as caught:
        dynamics = FieldDynamics(registry, rng_factory=unsupported)

    assert caught.value.anomaly.code == "unsupported_rng_factory"
    assert caught.value.anomaly.stage == "rng_factory"
    assert caught.value.anomaly.dim_id is None
    assert caught.value.anomaly.detail == (
        "rng_factory must be an exact SeededGaussianRngFactory instance"
    )
    assert unsupported.create_calls == 0
    assert unsupported.draw_calls == 0
    assert dynamics is None


def test_rng_provider_name_bomb_stops_before_later_create_draw_or_publish(
    monkeypatch,
) -> None:
    registry = tuple(_synthetic_registration(f"name-bomb-{index}") for index in range(3))
    original_create = SeededGaussianRngFactory.create
    create_calls: list[str] = []
    unsupported = _NameBombUnsupportedProvider()
    dynamics = None

    def wrong_middle(self, stream: str):
        create_calls.append(stream)
        if stream == registry[1].dim_id:
            return unsupported
        return original_create(self, stream)

    monkeypatch.setattr(SeededGaussianRngFactory, "create", wrong_middle)
    with pytest.raises(DynamicsContractError) as caught:
        dynamics = FieldDynamics(registry, rng_factory=SeededGaussianRngFactory(17))

    assert caught.value.anomaly.code == "unsupported_rng_provider"
    assert caught.value.anomaly.stage == "rng_factory"
    assert caught.value.anomaly.dim_id == registry[1].dim_id
    assert caught.value.anomaly.detail == (
        "factory.create() returned an unsupported RNG provider"
    )
    assert create_calls == [registry[0].dim_id, registry[1].dim_id]
    assert registry[2].dim_id not in create_calls
    assert unsupported.draw_calls == 0
    assert dynamics is None


def test_rng_constructor_nominal_boundary_rejects_before_create_or_draw() -> None:
    registry = (_synthetic_registration("boundary"),)
    unsupported = _CountingUnsupportedFactory()
    cases = (unsupported, _SeededFactorySubclass(7), object(), 7, False)

    for factory in cases:
        with pytest.raises(Exception) as caught:
            FieldDynamics(registry, rng_factory=factory)
        assert caught.value.anomaly.code == "unsupported_rng_factory"
        assert caught.value.anomaly.stage == "rng_factory"
    assert unsupported.create_calls == 0

    assert FieldDynamics(registry).snapshot().tick == 0
    assert FieldDynamics(
        registry, rng_factory=SeededGaussianRngFactory(7)
    ).snapshot().tick == 0


def test_rng_provider_boundary_is_checked_immediately_and_publishes_nothing(
    monkeypatch,
) -> None:
    registry = tuple(_synthetic_registration(f"provider-{index}") for index in range(3))
    original_create = SeededGaussianRngFactory.create
    create_calls: list[str] = []

    def wrong_middle(self, stream: str):
        create_calls.append(stream)
        if stream == registry[1].dim_id:
            return _ControlledRng(self.seed, stream, 0.0)
        return original_create(self, stream)

    monkeypatch.setattr(SeededGaussianRngFactory, "create", wrong_middle)
    with pytest.raises(Exception) as caught:
        FieldDynamics(registry, rng_factory=SeededGaussianRngFactory(17))

    assert caught.value.anomaly.code == "unsupported_rng_provider"
    assert caught.value.anomaly.stage == "rng_factory"
    assert caught.value.anomaly.dim_id == registry[1].dim_id
    assert create_calls == [registry[0].dim_id, registry[1].dim_id]


def test_exact_rng_factory_exception_string_bomb_maps_stably_and_preserves_cause(
    monkeypatch,
) -> None:
    registry = (_synthetic_registration("factory-exception-bomb"),)
    original = _StringBombFactoryError()
    dynamics = None

    def raise_string_bomb(self, stream: str):
        raise original

    monkeypatch.setattr(SeededGaussianRngFactory, "create", raise_string_bomb)
    with pytest.raises(DynamicsContractError) as caught:
        dynamics = FieldDynamics(registry, rng_factory=SeededGaussianRngFactory(17))

    assert caught.value.anomaly.code == "rng_factory_failure"
    assert caught.value.anomaly.stage == "rng_factory"
    assert caught.value.anomaly.dim_id is None
    assert caught.value.anomaly.detail == "factory.create() raised an exception"
    assert caught.value.__cause__ is original
    assert dynamics is None


@pytest.mark.parametrize("dimension_count", [1, 3, 7, 12, 17])
def test_birth_registry_exact_order_parameters_and_synthetic_dimension_count(
    dimension_count: int,
) -> None:
    registry = build_birth_registry()
    assert [(item.dim_id, item.temporary_name, item.birth_bias) for item in registry] == [
        ("birth_00", "能量", 0.0),
        ("birth_01", "开放", -0.2),
        ("birth_02", "稳定", -0.1),
        ("birth_03", "朝向你", 0.0),
        ("birth_04", "好奇", 0.0),
        ("birth_05", "愉悦", 0.0),
        ("birth_06", "紧张", 0.0),
        ("birth_07", "疲惫", 0.0),
        ("birth_08", "安全感", 0.0),
        ("birth_09", "期待", 0.0),
        ("birth_10", "沉郁", 0.0),
        ("birth_11", "玩兴", 0.0),
    ]
    for item in registry:
        assert item.strength == 1.0
        assert item.trigger_count == 0
        assert item.fast_e_fold_s == 600.0
        assert item.ou_correlation_e_fold_s == 10_800.0
        assert item.ou_acceleration_sigma == 4.0e-7
        assert item.soft_boundary_start == 1.0
        assert item.soft_boundary_width == 0.25
        assert item.soft_boundary_strength == (1.0 / 120.0) ** 2

    synthetic = tuple(
        _synthetic_registration(f"s-{index}", index / 10)
        for index in range(dimension_count)
    )
    dynamics = _construct_test_dynamics(synthetic, rng_factory=ConstantGaussianRngFactory())
    assert tuple(item.dim_id for item in dynamics.registry) == tuple(
        f"s-{index}" for index in range(dimension_count)
    )
    assert len(dynamics.tick().dimensions) == dimension_count
    assert len(dynamics.snapshot().dimensions) == dimension_count


def test_birth_initialization_and_registration_metadata_do_not_change_on_tick() -> None:
    dynamics = _construct_test_dynamics(rng_factory=ConstantGaussianRngFactory())
    before = dynamics.snapshot()
    for dimension in before.dimensions:
        assert dimension.value == dimension.attractor == dimension.soft_restoring_baseline
        assert dimension.value == dimension.registration.birth_bias
        assert dimension.velocity == 0.0
        assert dimension.ou_acceleration == 0.0
    dynamics.tick()
    after = dynamics.snapshot()
    assert [(item.strength, item.trigger_count) for item in after.dimensions] == [
        (item.strength, item.trigger_count) for item in before.dimensions
    ]


def test_zero_innovation_is_the_only_ou_off_mechanism_and_equilibrium_is_exact() -> None:
    dynamics = _construct_test_dynamics(rng_factory=ConstantGaussianRngFactory())
    observation = dynamics.tick()
    assert observation.tick_before == 0
    assert observation.tick_after == 1
    assert observation.anomalies == ()
    for dimension in observation.dimensions:
        assert dimension.rng_draw.value == 0.0
        assert dimension.after_ou_acceleration == 0.0
        assert dimension.spring_acceleration == 0.0
        assert dimension.damping_acceleration == 0.0
        assert dimension.acceleration_without_soft_restoring == 0.0
        assert dimension.soft_restoring_acceleration == 0.0
        assert dimension.after_value == dimension.before_value
        assert dimension.after_velocity == 0.0
        assert dimension.anomalies == ()
    assert "ou" not in inspect.signature(FieldDynamics.tick).parameters


def test_seeded_ou_is_reproducible_traceable_zero_mean_long_correlated_and_independent() -> None:
    left = _construct_test_dynamics(rng_factory=SeededGaussianRngFactory(PRODUCTION_SEED))
    right = _construct_test_dynamics(rng_factory=SeededGaussianRngFactory(PRODUCTION_SEED))
    innovation_by_dim = [[] for _ in left.registry]
    ou_series = []
    for tick_index in range(40_000):
        left_observation = left.tick()
        right_observation = right.tick()
        assert left_observation == right_observation
        for dim_index, dimension in enumerate(left_observation.dimensions):
            assert dimension.rng_draw.seed == PRODUCTION_SEED
            assert dimension.rng_draw.stream == dimension.dim_id
            assert dimension.rng_draw.draw_index == tick_index
            innovation_by_dim[dim_index].append(dimension.rng_draw.value)
            reconstructed = (
                dimension.ou_rho * dimension.before_ou_acceleration
                + dimension.ou_innovation_scale * dimension.rng_draw.value
            )
            assert dimension.after_ou_acceleration == pytest.approx(reconstructed, abs=1e-24)
        ou_series.append(left_observation.dimensions[0].after_ou_acceleration)

    first_innovations = innovation_by_dim[0]
    assert any(value != 0.0 for value in first_innovations)
    assert abs(statistics.fmean(first_innovations)) < 0.02
    assert statistics.pstdev(first_innovations) == pytest.approx(1.0, abs=0.02)
    expected_rho = math.exp(-1.0 / 10_800.0)
    pairs = zip(ou_series[:-100], ou_series[100:])
    left_lag, right_lag = zip(*pairs)
    assert statistics.correlation(left_lag, right_lag) == pytest.approx(expected_rho**100, abs=0.03)
    for other in innovation_by_dim[1:]:
        assert abs(statistics.correlation(first_innovations, other)) < 0.025
    final_snapshot = left.snapshot()
    assert all(
        dimension.attractor == dimension.soft_restoring_baseline == dimension.registration.birth_bias
        for dimension in final_snapshot.dimensions
    )
    ou_mean = statistics.fmean(first_innovations)
    ou_std = statistics.pstdev(first_innovations)
    lag100_corr = statistics.correlation(left_lag, right_lag)
    max_cross_corr = max(abs(statistics.correlation(first_innovations, other)) for other in innovation_by_dim[1:])
    rho_err = abs(lag100_corr - expected_rho**100)
    expected_scale = 4.0e-7 * math.sqrt(1.0 - expected_rho * expected_rho)
    scale_err = abs(left_observation.dimensions[0].ou_innovation_scale - expected_scale)
    print(
        f"ou_innovation mean={ou_mean:.6g} std={ou_std:.6g} "
        f"lag100_corr={lag100_corr:.6g} max_cross_corr={max_cross_corr:.6g} "
        f"rho_err={rho_err:.6g} scale_err={scale_err:.6g}"
    )


@pytest.mark.parametrize("direction", [-1.0, 1.0])
def test_all_dimensions_critical_step_response_has_no_overshoot_and_matches_analytic(
    direction: float,
) -> None:
    dynamics = _construct_test_dynamics(rng_factory=ConstantGaussianRngFactory())
    step = 0.2 * direction
    for registration in dynamics.registry:
        dynamics.move_attractor(AttractorMove(registration.dim_id, step, "test", "small step"))
    checkpoints = {60, 300, 600, 1200, 2400}
    previous_remaining = [abs(step)] * len(dynamics.registry)
    measured: dict[int, tuple[float, ...]] = {}
    for tick in range(1, max(checkpoints) + 1):
        observation = dynamics.tick()
        for index, dimension in enumerate(observation.dimensions):
            assert dimension.soft_restoring_acceleration == 0.0
            remaining = abs(dimension.after_attractor - dimension.after_value)
            assert remaining <= previous_remaining[index] + 1e-15
            previous_remaining[index] = remaining
            if direction > 0:
                assert dimension.after_value <= dimension.after_attractor
            else:
                assert dimension.after_value >= dimension.after_attractor
        if tick in checkpoints:
            measured[tick] = tuple(previous_remaining)

    max_rel_errors = []
    for tick, remaining_by_dim in measured.items():
        analytic_remaining = abs(step) * (1.0 + tick / 600.0) * math.exp(-tick / 600.0)
        for remaining in remaining_by_dim:
            rel_err = abs(remaining - analytic_remaining) / max(abs(analytic_remaining), 1e-18)
            max_rel_errors.append(rel_err)
            assert remaining == pytest.approx(analytic_remaining, rel=0.20)
    print(
        f"step_response direction={direction} "
        f"max_rel_error={max(max_rel_errors):.6g} "
        f"monotonic=True overshoot=False"
    )


def test_soft_boundary_is_continuous_unclamped_reconstructable_and_has_linear_tail() -> None:
    registration = replace(
        _synthetic_registration("soft"),
        fast_e_fold_s=2.0,
        soft_boundary_start=0.1,
        soft_boundary_width=0.05,
    )
    spring_coefficient = (1.0 / registration.fast_e_fold_s) ** 2

    def evaluate(displacement: float):
        dynamics = _construct_test_dynamics((registration,), rng_factory=ConstantGaussianRngFactory())
        dynamics.move_attractor(
            AttractorMove("soft", displacement / spring_coefficient, "test", "boundary pressure")
        )
        return dynamics.tick().dimensions[0]

    inside = evaluate(registration.soft_boundary_start - 1e-6)
    at_start = evaluate(registration.soft_boundary_start)
    just_outside = evaluate(registration.soft_boundary_start + 1e-6)
    at_join = evaluate(registration.soft_boundary_start + registration.soft_boundary_width)
    after_join = evaluate(registration.soft_boundary_start + registration.soft_boundary_width + 1e-6)
    positive_tail = evaluate(registration.soft_boundary_start + registration.soft_boundary_width + 0.1)
    farther_tail = evaluate(registration.soft_boundary_start + registration.soft_boundary_width + 0.2)
    negative_tail = evaluate(-(registration.soft_boundary_start + registration.soft_boundary_width + 0.1))

    assert inside.soft_restoring_acceleration == 0.0
    assert at_start.soft_restoring_acceleration == 0.0
    assert just_outside.soft_restoring_acceleration == pytest.approx(
        -registration.soft_boundary_strength * 1e-12 / (2.0 * registration.soft_boundary_width)
    )
    expected_join = -registration.soft_boundary_strength * registration.soft_boundary_width / 2.0
    assert at_join.soft_restoring_acceleration == pytest.approx(expected_join)
    assert after_join.soft_restoring_acceleration == pytest.approx(
        expected_join - registration.soft_boundary_strength * 1e-6
    )
    assert positive_tail.soft_restoring_acceleration < 0.0
    assert negative_tail.soft_restoring_acceleration > 0.0
    tail_slope = (
        farther_tail.soft_restoring_acceleration - positive_tail.soft_restoring_acceleration
    ) / (
        farther_tail.pre_boundary_value_proposal - positive_tail.pre_boundary_value_proposal
    )
    assert tail_slope == pytest.approx(-registration.soft_boundary_strength)
    for observation in (inside, at_start, just_outside, at_join, after_join, positive_tail, farther_tail, negative_tail):
        assert observation.after_value == pytest.approx(
            observation.pre_boundary_value_proposal + observation.soft_restoring_acceleration
        )
        assert abs(observation.after_value) >= registration.soft_boundary_start or observation is inside
    assert positive_tail.after_value != farther_tail.after_value
    continuity_err = abs(
        just_outside.soft_restoring_acceleration
        - (-registration.soft_boundary_strength * 1e-12 / (2.0 * registration.soft_boundary_width))
    )
    expected_slope = -registration.soft_boundary_strength
    slope_err = abs(tail_slope - expected_slope)
    print(
        f"soft_boundary continuity_err={continuity_err:.6g} "
        f"actual_tail_slope={tail_slope:.6g} expected_slope={expected_slope:.6g} "
        f"slope_err={slope_err:.6g} "
        f"positive_tail_negative=True negative_tail_positive=True"
    )


def test_invalid_inputs_and_nonfinite_rng_are_explicit_and_tick_state_is_atomic() -> None:
    template = _synthetic_registration("valid")
    invalid_changes = (
        {"fast_e_fold_s": 0.0},
        {"ou_correlation_e_fold_s": math.inf},
        {"ou_acceleration_sigma": -1.0},
        {"soft_boundary_width": 0.0},
        {"soft_boundary_strength": math.nan},
    )
    for changes in invalid_changes:
        with pytest.raises(InvalidRegistrationError):
            replace(template, **changes)

    dynamics = _construct_test_dynamics((template,), rng_factory=ConstantGaussianRngFactory())
    before_move = dynamics.snapshot()
    with pytest.raises(InvalidAttractorMoveError) as move_error:
        dynamics.move_attractor(AttractorMove("valid", math.nan, "test", "invalid"))
    assert move_error.value.anomaly.code == "non_finite_attractor_delta"
    assert dynamics.snapshot() == before_move

    inf_rng_dynamics = _construct_test_dynamics(
        (template,), rng_factory=ConstantGaussianRngFactory(math.inf)
    )
    before_tick = inf_rng_dynamics.snapshot()
    with pytest.raises(InvalidRngDrawError) as rng_error:
        inf_rng_dynamics.tick()
    assert rng_error.value.anomaly.stage == "rng_draw"
    assert inf_rng_dynamics.snapshot() == before_tick
    assert inf_rng_dynamics.snapshot().tick == 0
    print(
        "anomaly_atomicity first_dim_failure: "
        f"move_error_code={move_error.value.anomaly.code} snapshot_preserved=True "
        f"rng_error_stage={rng_error.value.anomaly.stage} tick_preserved=True"
    )


def test_snapshot_and_registration_are_deeply_read_only_and_no_direct_write_api_exists() -> None:
    dynamics = _construct_test_dynamics(rng_factory=ConstantGaussianRngFactory())
    snapshot = dynamics.snapshot()
    with pytest.raises(FrozenInstanceError):
        setattr(snapshot, "tick", 9)
    with pytest.raises(FrozenInstanceError):
        setattr(snapshot.dimensions[0], "value", 9.0)
    with pytest.raises(FrozenInstanceError):
        setattr(snapshot.dimensions[0].registration, "strength", 9.0)
    with pytest.raises(AttributeError):
        getattr(snapshot.dimensions, "__setitem__")(0, snapshot.dimensions[1])

    public_members = {
        name for name, _ in inspect.getmembers(FieldDynamics) if not name.startswith("_")
    }
    assert public_members == {"move_attractor", "registry", "snapshot", "tick"}
    forbidden_write_names = {
        "set_value",
        "set_velocity",
        "set_ou_acceleration",
        "set_baseline",
        "patch",
        "update",
        "merge",
    }
    assert public_members.isdisjoint(forbidden_write_names)


def test_runtime_imports_only_standard_library_and_contains_no_quarantine_path() -> None:
    source_path = Path(__file__).parents[2] / "app" / "chatbox" / "field_dynamics.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module is not None
            imported_roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"__import__", "exec", "eval"}
    assert imported_roots <= {"__future__", "dataclasses", "hashlib", "math", "random", "typing"}
    assert "sys.path" not in source
    for quarantined in (
        "agentlib",
        "agent_kernel",
        "semantic_trigger",
        "demos/scenarios",
        "docs/archive",
    ):
        assert quarantined not in source


class _RetryRng:
    """RNG that returns a bad value on the first call at a given index, then finite."""

    def __init__(self, seed: int, stream: str, bad_value: float, bad_at_index: int = 0) -> None:
        self._seed = seed
        self._stream = stream
        self._bad_value = bad_value
        self._bad_at_index = bad_at_index
        self._index = 0

    def draw(self, draw_index: int) -> RngDraw:
        if draw_index == self._bad_at_index and self._index <= self._bad_at_index:
            value = self._bad_value
        else:
            value = 0.0
        draw = RngDraw(self._seed, self._stream, draw_index, value)
        self._index = draw_index + 1
        return draw


class _RetryRngFactory:
    """Factory producing _RetryRng for one stream, _ControlledRng for others."""

    def __init__(self, fail_stream: str, bad_value: float, bad_at_index: int = 0, seed: int = 42) -> None:
        self._fail_stream = fail_stream
        self._bad_value = bad_value
        self._bad_at_index = bad_at_index
        self._seed = seed

    def create(self, stream: str):
        if stream == self._fail_stream:
            return _RetryRng(self._seed, stream, self._bad_value, self._bad_at_index)
        return _ControlledRng(self._seed, stream, 0.0)


class _RetryWrongTypeRng:
    """RNG that returns wrong type on first call at bad_at_index, then valid RngDraw."""

    def __init__(self, seed: int, stream: str, bad_at_index: int = 0) -> None:
        self._seed = seed
        self._stream = stream
        self._bad_at_index = bad_at_index
        self._index = 0

    def draw(self, draw_index: int):
        if draw_index == self._bad_at_index and self._index <= self._bad_at_index:
            self._index = draw_index + 1
            return {"seed": 42, "stream": "x", "draw_index": 0, "value": 0.0}
        draw = RngDraw(self._seed, self._stream, draw_index, 0.0)
        self._index = draw_index + 1
        return draw


class _RetryWrongTypeRngFactory:
    def __init__(self, fail_stream: str, bad_at_index: int = 0, seed: int = 42) -> None:
        self._fail_stream = fail_stream
        self._bad_at_index = bad_at_index
        self._seed = seed

    def create(self, stream: str):
        if stream == self._fail_stream:
            return _RetryWrongTypeRng(self._seed, stream, self._bad_at_index)
        return _ControlledRng(self._seed, stream, 0.0)


def _snapshot_tuple(snapshot) -> tuple:
    return (snapshot.tick, tuple(
        (d.value, d.velocity, d.attractor, d.soft_restoring_baseline, d.ou_acceleration)
        for d in snapshot.dimensions
    ))


def test_rng_transaction_retry_after_late_nonfinite_rng_draw(capsys) -> None:
    from app.chatbox.field_dynamics import DynamicsContractError, NonFiniteDynamicsError
    registry = (
        _synthetic_registration("d-0"),
        _synthetic_registration("d-1"),
        _synthetic_registration("d-2"),
    )
    factory = _RetryRngFactory("d-2", bad_value=math.inf, bad_at_index=0)
    dynamics = _construct_test_dynamics(registry, rng_factory=factory)
    before = dynamics.snapshot()
    assert before.tick == 0
    with pytest.raises((InvalidRngDrawError, NonFiniteDynamicsError)) as exc:
        dynamics.tick()
    assert isinstance(exc.value, DynamicsContractError)
    after_fail = dynamics.snapshot()
    assert after_fail == before
    assert after_fail.tick == 0
    retry_obs = dynamics.tick()
    assert retry_obs.tick_before == 0
    assert retry_obs.tick_after == 1
    for dim in retry_obs.dimensions:
        assert dim.rng_draw.draw_index == 0
    retry_snapshot = dynamics.snapshot()
    assert retry_snapshot.tick == 1
    control_factory = _ControlledRngFactory(default_value=0.0, seed=42)
    control = _construct_test_dynamics(registry, rng_factory=control_factory)
    control.tick()
    control_equal = _snapshot_tuple(retry_snapshot) == _snapshot_tuple(control.snapshot())
    assert control_equal
    next_obs = dynamics.tick()
    assert next_obs.tick_before == 1
    assert next_obs.tick_after == 2
    for dim in next_obs.dimensions:
        assert dim.rng_draw.draw_index == 1
    control.tick()
    next_control_equal = _snapshot_tuple(dynamics.snapshot()) == _snapshot_tuple(control.snapshot())
    assert next_control_equal
    print(
        f"rng_transaction_retry stage=rng_draw failure_tick=0 "
        f"retry_draw_indexes={[d.rng_draw.draw_index for d in retry_obs.dimensions]} "
        f"control_equal={control_equal} "
        f"next_tick_draw_indexes={[d.rng_draw.draw_index for d in next_obs.dimensions]} "
        f"next_control_equal={next_control_equal}"
    )


def test_rng_transaction_retry_after_late_nonfinite_candidate(capsys) -> None:
    from app.chatbox.field_dynamics import DynamicsContractError, NonFiniteDynamicsError
    registry = (
        _synthetic_registration("d-0"),
        _synthetic_registration("d-1"),
        DimensionRegistration(
            dim_id="d-2",
            temporary_name="bad-ou",
            birth_time=17.0,
            strength=1.0,
            trigger_count=0,
            birth_bias=0.0,
            fast_e_fold_s=600.0,
            ou_correlation_e_fold_s=10_800.0,
            ou_acceleration_sigma=1e308,
            soft_boundary_start=1.0,
            soft_boundary_width=0.25,
            soft_boundary_strength=(1.0 / 120.0) ** 2,
        ),
    )
    factory = _RetryRngFactory("d-2", bad_value=1e3, bad_at_index=0)
    dynamics = _construct_test_dynamics(registry, rng_factory=factory)
    before = dynamics.snapshot()
    assert before.tick == 0
    with pytest.raises((InvalidRngDrawError, NonFiniteDynamicsError)) as exc:
        dynamics.tick()
    assert isinstance(exc.value, DynamicsContractError)
    after_fail = dynamics.snapshot()
    assert after_fail == before
    assert after_fail.tick == 0
    retry_obs = dynamics.tick()
    assert retry_obs.tick_before == 0
    assert retry_obs.tick_after == 1
    for dim in retry_obs.dimensions:
        assert dim.rng_draw.draw_index == 0
    retry_snapshot = dynamics.snapshot()
    assert retry_snapshot.tick == 1
    control_factory = _ControlledRngFactory(default_value=0.0, seed=42)
    control = _construct_test_dynamics(registry, rng_factory=control_factory)
    control.tick()
    control_equal = _snapshot_tuple(retry_snapshot) == _snapshot_tuple(control.snapshot())
    assert control_equal
    next_obs = dynamics.tick()
    for dim in next_obs.dimensions:
        assert dim.rng_draw.draw_index == 1
    control.tick()
    next_control_equal = _snapshot_tuple(dynamics.snapshot()) == _snapshot_tuple(control.snapshot())
    assert next_control_equal
    print(
        f"rng_transaction_retry stage=candidate_validation failure_tick=0 "
        f"retry_draw_indexes={[d.rng_draw.draw_index for d in retry_obs.dimensions]} "
        f"control_equal={control_equal} "
        f"next_tick_draw_indexes={[d.rng_draw.draw_index for d in next_obs.dimensions]} "
        f"next_control_equal={next_control_equal}"
    )


def test_rng_transaction_seed_trace_after_failure_and_retry(capsys) -> None:
    from app.chatbox.field_dynamics import DynamicsContractError, NonFiniteDynamicsError
    registry = (
        _synthetic_registration("d-0"),
        _synthetic_registration("d-1"),
        _synthetic_registration("d-2"),
    )
    factory = _RetryRngFactory("d-1", bad_value=math.inf, bad_at_index=0)
    dynamics = _construct_test_dynamics(registry, rng_factory=factory)
    before = dynamics.snapshot()
    assert before.tick == 0
    with pytest.raises((InvalidRngDrawError, NonFiniteDynamicsError)) as exc:
        dynamics.tick()
    assert isinstance(exc.value, DynamicsContractError)
    after_fail = dynamics.snapshot()
    assert after_fail == before
    assert after_fail.tick == 0
    retry_obs = dynamics.tick()
    assert retry_obs.tick_after == 1
    for dim in retry_obs.dimensions:
        assert dim.rng_draw.seed == 42
        assert dim.rng_draw.stream == dim.dim_id
        assert dim.rng_draw.draw_index == 0
    next_obs = dynamics.tick()
    assert next_obs.tick_after == 2
    for dim in next_obs.dimensions:
        assert dim.rng_draw.seed == 42
        assert dim.rng_draw.stream == dim.dim_id
        assert dim.rng_draw.draw_index == 1
    print(
        f"rng_transaction_retry stage=seed_trace failure_tick=0 "
        f"retry_draw_indexes={[d.rng_draw.draw_index for d in retry_obs.dimensions]} "
        f"retry_seeds={[d.rng_draw.seed for d in retry_obs.dimensions]} "
        f"next_tick_draw_indexes={[d.rng_draw.draw_index for d in next_obs.dimensions]} "
        f"next_seeds={[d.rng_draw.seed for d in next_obs.dimensions]}"
    )


def test_rng_transaction_retry_after_malformed_rng_draw(capsys) -> None:
    from app.chatbox.field_dynamics import DynamicsContractError
    registry = tuple(_synthetic_registration(f"d-{i}") for i in range(3))
    factory = _RetryWrongTypeRngFactory("d-2", bad_at_index=0)
    dynamics = _construct_test_dynamics(registry, rng_factory=factory)
    before = dynamics.snapshot()
    with pytest.raises(InvalidRngDrawError) as exc:
        dynamics.tick()
    assert exc.value.anomaly.code == "invalid_rng_draw_type"
    assert exc.value.anomaly.dim_id == "d-2"
    after_fail = dynamics.snapshot()
    assert after_fail == before
    assert after_fail.tick == 0
    control_factory = _ControlledRngFactory(default_value=0.0, seed=42)
    control = _construct_test_dynamics(registry, rng_factory=control_factory)
    retry_obs = dynamics.tick()
    assert retry_obs.tick_after == 1
    for dim in retry_obs.dimensions:
        assert dim.rng_draw.draw_index == 0
    control.tick()
    control_equal = _snapshot_tuple(dynamics.snapshot()) == _snapshot_tuple(control.snapshot())
    assert control_equal
    print(
        f"rng_transaction_retry stage=malformed_rng failure_tick=0 "
        f"retry_draw_indexes={[d.rng_draw.draw_index for d in retry_obs.dimensions]} "
        f"control_equal={control_equal}"
    )


class _OneShotFaultRng:
    """Test wrapper whose fault branch never consumes its canonical delegate."""

    def __init__(self, delegate, seed: int, stream: str, fault_kind: str) -> None:
        self.delegate = delegate
        self.seed = seed
        self.stream = stream
        self.fault_kind = fault_kind
        self.armed = True
        self.delegate_calls = 0

    def draw(self, draw_index: int):
        if self.armed and draw_index == 1:
            self.armed = False
            if self.fault_kind == "exception":
                raise RuntimeError("one-shot draw failure")
            if self.fault_kind == "wrong-type":
                return {"draw_index": draw_index}
            if self.fault_kind == "nan":
                return RngDraw(self.seed, self.stream, draw_index, math.nan)
            if self.fault_kind == "inf":
                return RngDraw(self.seed, self.stream, draw_index, math.inf)
            if self.fault_kind == "seed-flip":
                return RngDraw(self.seed + 1, self.stream, draw_index, 0.0)
            if self.fault_kind == "candidate-nonfinite":
                return RngDraw(self.seed, self.stream, draw_index, 1e308)
            raise AssertionError(self.fault_kind)
        self.delegate_calls += 1
        return self.delegate.draw(draw_index)


@pytest.mark.parametrize("fault_ordinal", [0, 1, 2], ids=["first", "middle", "last"])
@pytest.mark.parametrize(
    "fault_kind",
    ["exception", "wrong-type", "nan", "inf", "seed-flip", "candidate-nonfinite"],
)
def test_rng_failure_retry_is_binary64_atomic_across_3_by_6_matrix(
    fault_ordinal: int, fault_kind: str
) -> None:
    seed = 0x51A7
    registry = tuple(_synthetic_registration(f"matrix-{index}") for index in range(3))
    if fault_kind == "candidate-nonfinite":
        registry = tuple(
            replace(item, ou_acceleration_sigma=1e308)
            if index == fault_ordinal
            else item
            for index, item in enumerate(registry)
        )
    faulted = FieldDynamics(registry, rng_factory=SeededGaussianRngFactory(seed))
    control = FieldDynamics(registry, rng_factory=SeededGaussianRngFactory(seed))
    wrappers = []
    for index, (registration, delegate) in enumerate(zip(registry, faulted._rngs)):
        if index == fault_ordinal:
            wrapper = _OneShotFaultRng(delegate, seed, registration.dim_id, fault_kind)
            wrappers.append(wrapper)
        else:
            wrappers.append(delegate)
    _install_test_rngs(faulted, wrappers)

    assert _binary64_projection(faulted.tick()) == _binary64_projection(control.tick())
    before_failure = _binary64_projection(faulted.snapshot())
    target_wrapper = wrappers[fault_ordinal]
    assert isinstance(target_wrapper, _OneShotFaultRng)
    calls_before_failure = target_wrapper.delegate_calls

    with pytest.raises(Exception):
        faulted.tick()

    assert target_wrapper.delegate_calls == calls_before_failure
    assert _binary64_projection(faulted.snapshot()) == before_failure
    retry = faulted.tick()
    control_next = control.tick()
    assert _binary64_projection(retry) == _binary64_projection(control_next)
    assert _binary64_projection(faulted.snapshot()) == _binary64_projection(
        control.snapshot()
    )


def test_seed_and_registry_parameter_snapshot(capsys) -> None:
    registry = build_birth_registry()
    print(f"seed={PRODUCTION_SEED}")
    for registration in registry:
        print(
            registration.dim_id,
            registration.temporary_name,
            registration.birth_bias,
            registration.fast_e_fold_s,
            registration.ou_correlation_e_fold_s,
            registration.ou_acceleration_sigma,
            registration.soft_boundary_start,
            registration.soft_boundary_width,
            registration.soft_boundary_strength,
        )
    output = capsys.readouterr().out
    assert f"seed={PRODUCTION_SEED}" in output
    assert len(output.splitlines()) == len(registry) + 1
    with capsys.disabled():
        print(output, end="")


@pytest.mark.acceptance
@pytest.mark.slow
def test_million_tick_production_pool_is_finite_bounded_reconstructable_and_nonrandom_walking() -> None:
    """Canonical P1 10^6-tick acceptance through the real production path.

    ``FieldDynamics.tick`` validates every candidate scalar on every tick.
    Test-side reconstruction and trace assertions are sampled deterministically
    because their formulas and edge behavior are covered independently by the
    fast numerical tests; quarter statistics still consume every produced
    state, so no part of the million-tick trajectory is skipped.
    """

    dynamics = _construct_test_dynamics(rng_factory=SeededGaussianRngFactory(PRODUCTION_SEED))
    dimension_count = len(dynamics.registry)
    minima = [math.inf] * dimension_count
    maxima = [-math.inf] * dimension_count
    max_residual = [0.0] * dimension_count
    quarter_sums = [[0.0] * dimension_count for _ in range(4)]
    quarter_squares = [[0.0] * dimension_count for _ in range(4)]
    quarter_size = MILLION_TICK_COUNT // 4

    for tick_index in range(MILLION_TICK_COUNT):
        observation = dynamics.tick()
        quarter = tick_index // quarter_size
        for dim_index, dimension in enumerate(observation.dimensions):
            displacement = (
                dimension.after_value - dimension.after_soft_restoring_baseline
            )
            assert abs(displacement) < 4.0 * (
                dynamics.registry[dim_index].soft_boundary_start
                + dynamics.registry[dim_index].soft_boundary_width
            )
            minima[dim_index] = min(minima[dim_index], displacement)
            maxima[dim_index] = max(maxima[dim_index], displacement)
            quarter_sums[quarter][dim_index] += displacement
            quarter_squares[quarter][dim_index] += displacement * displacement

        if (
            tick_index < 10
            or (tick_index + 1) % MILLION_CHECKPOINT_STRIDE == 0
            or tick_index + 1 == MILLION_TICK_COUNT
        ):
            assert observation.tick_before == tick_index
            assert observation.tick_after == tick_index + 1
            assert observation.anomalies == ()
            for dim_index, dimension in enumerate(observation.dimensions):
                assert dimension.anomalies == ()
                assert all(
                    math.isfinite(value) for value in _observation_numbers(dimension)
                )
                assert dimension.rng_draw.seed == PRODUCTION_SEED
                assert dimension.rng_draw.stream == dimension.dim_id
                assert dimension.rng_draw.draw_index == tick_index
                reconstructed_ou = (
                    dimension.ou_rho * dimension.before_ou_acceleration
                    + dimension.ou_innovation_scale * dimension.rng_draw.value
                )
                reconstructed_velocity = (
                    dimension.before_velocity
                    + dimension.spring_acceleration
                    + dimension.damping_acceleration
                    + reconstructed_ou
                    + dimension.soft_restoring_acceleration
                )
                reconstructed_value = (
                    dimension.pre_boundary_value_proposal
                    + dimension.soft_restoring_acceleration
                )
                residual = max(
                    abs(dimension.after_ou_acceleration - reconstructed_ou),
                    abs(dimension.after_velocity - reconstructed_velocity),
                    abs(dimension.after_value - reconstructed_value),
                )
                max_residual[dim_index] = max(max_residual[dim_index], residual)

    variances = []
    for quarter in range(4):
        variances.append(
            [
                quarter_squares[quarter][index] / quarter_size
                - (quarter_sums[quarter][index] / quarter_size) ** 2
                for index in range(dimension_count)
            ]
        )
    for index, registration in enumerate(dynamics.registry):
        early_scale = max(variances[1][index], 1e-18)
        assert variances[3][index] < early_scale * 8.0
        print(
            f"million_tick {registration.dim_id} "
            f"min={minima[index]:.9g} max={maxima[index]:.9g} "
            f"quarter_variances={[round(item[index], 12) for item in variances]} "
            f"max_reconstruction_residual={max_residual[index]:.3g}"
        )
    assert dynamics.snapshot().tick == MILLION_TICK_COUNT


def test_late_dimension_nonfinite_candidate_preserves_entire_snapshot_and_tick() -> None:
    from app.chatbox.field_dynamics import DynamicsContractError, NonFiniteDynamicsError
    registry = (
        _synthetic_registration("d-0"),
        _synthetic_registration("d-1"),
        DimensionRegistration(
            dim_id="d-2",
            temporary_name="bad-ou",
            birth_time=17.0,
            strength=1.0,
            trigger_count=0,
            birth_bias=0.0,
            fast_e_fold_s=600.0,
            ou_correlation_e_fold_s=10_800.0,
            ou_acceleration_sigma=1e308,
            soft_boundary_start=1.0,
            soft_boundary_width=0.25,
            soft_boundary_strength=(1.0 / 120.0) ** 2,
        ),
    )
    factory = _ControlledRngFactory(default_value=1e3, seed=42)
    dynamics = _construct_test_dynamics(registry, rng_factory=factory)
    before = dynamics.snapshot()
    assert before.tick == 0
    with pytest.raises((InvalidRngDrawError, NonFiniteDynamicsError)) as exc:
        dynamics.tick()
    assert isinstance(exc.value, DynamicsContractError)
    after = dynamics.snapshot()
    assert after == before
    assert after.tick == 0
    print(
        f"anomaly_atomicity late_dim_failure: "
        f"error_code={exc.value.anomaly.code} "
        f"snapshot_preserved=True tick_preserved=True"
    )


def test_move_attractor_invalid_inputs_are_structured_and_atomic() -> None:
    from app.chatbox.field_dynamics import DynamicsContractError, NonFiniteDynamicsError
    registry = tuple(_synthetic_registration(f"d-{i}") for i in range(2))
    dynamics = _construct_test_dynamics(registry, rng_factory=ConstantGaussianRngFactory())
    before = dynamics.snapshot()

    with pytest.raises(InvalidAttractorMoveError) as exc:
        dynamics.move_attractor(AttractorMove("d-0", 0.1, "", "rationale"))
    assert exc.value.anomaly.code == "empty_attractor_source"
    assert dynamics.snapshot() == before

    with pytest.raises(InvalidAttractorMoveError) as exc:
        dynamics.move_attractor(AttractorMove("d-0", 0.1, "   ", "rationale"))
    assert exc.value.anomaly.code == "empty_attractor_source"
    assert dynamics.snapshot() == before

    with pytest.raises(InvalidAttractorMoveError) as exc:
        dynamics.move_attractor(AttractorMove("d-0", 0.1, "src", ""))
    assert exc.value.anomaly.code == "empty_attractor_rationale"
    assert dynamics.snapshot() == before

    with pytest.raises(InvalidAttractorMoveError) as exc:
        dynamics.move_attractor(AttractorMove("d-0", 0.1, "src", "   "))
    assert exc.value.anomaly.code == "empty_attractor_rationale"
    assert dynamics.snapshot() == before

    with pytest.raises(InvalidAttractorMoveError) as exc:
        dynamics.move_attractor(AttractorMove("unknown", 0.1, "src", "rationale"))
    assert exc.value.anomaly.code == "unknown_attractor_dim_id"
    assert dynamics.snapshot() == before

    with pytest.raises(InvalidAttractorMoveError) as exc:
        dynamics.move_attractor(AttractorMove("d-0", math.nan, "src", "rationale"))
    assert exc.value.anomaly.code == "non_finite_attractor_delta"
    assert dynamics.snapshot() == before

    with pytest.raises(InvalidAttractorMoveError) as exc:
        dynamics.move_attractor(AttractorMove("d-0", math.inf, "src", "rationale"))
    assert exc.value.anomaly.code == "non_finite_attractor_delta"
    assert dynamics.snapshot() == before

    big_registry = (_synthetic_registration("big", bias=1e308),)
    big_dynamics = _construct_test_dynamics(big_registry, rng_factory=ConstantGaussianRngFactory())
    big_before = big_dynamics.snapshot()
    with pytest.raises(InvalidAttractorMoveError) as exc:
        big_dynamics.move_attractor(AttractorMove("big", 1e308, "src", "rationale"))
    assert exc.value.anomaly.code == "non_finite_attractor_result"
    assert big_dynamics.snapshot() == big_before

    after_move = dynamics.move_attractor(AttractorMove("d-1", 0.3, "src", "rationale"))
    moved = after_move.dimensions[1]
    unmoved = after_move.dimensions[0]
    assert moved.attractor == 0.3
    assert unmoved.attractor == registry[0].birth_bias
    assert moved.value == registry[1].birth_bias
    assert moved.velocity == 0.0
    assert moved.ou_acceleration == 0.0
    assert moved.soft_restoring_baseline == registry[1].birth_bias


@pytest.mark.parametrize("dim_id", ["birth_01", "birth_02"])
@pytest.mark.parametrize("direction", [-1.0, 1.0])
def test_move_attractor_closed_domain_is_cumulative_and_baseline_relative(
    dim_id: str, direction: float
) -> None:
    dynamics = _construct_test_dynamics(rng_factory=ConstantGaussianRngFactory())
    initial = dynamics.snapshot()
    target = next(item for item in initial.dimensions if item.dim_id == dim_id)
    baseline = target.soft_restoring_baseline
    boundary = baseline + direction * ATTRACTOR_DISPLACEMENT_RADIUS

    first = direction * 0.9
    dynamics.move_attractor(AttractorMove(dim_id, first, "test", "first cumulative move"))
    current = next(item for item in dynamics.snapshot().dimensions if item.dim_id == dim_id)
    accepted = dynamics.move_attractor(
        AttractorMove(dim_id, boundary - current.attractor, "test", "exact contract boundary")
    )
    moved = next(item for item in accepted.dimensions if item.dim_id == dim_id)
    assert moved.attractor == boundary
    assert moved.soft_restoring_baseline == baseline
    assert moved.attractor != direction * ATTRACTOR_DISPLACEMENT_RADIUS
    assert accepted.tick == 0


@pytest.mark.parametrize("direction", [-1.0, 1.0])
@pytest.mark.parametrize("obvious", [False, True], ids=["nextafter", "obvious"])
def test_move_attractor_out_of_domain_diagnostic_is_complete_and_atomic(
    direction: float, obvious: bool
) -> None:
    registry = build_birth_registry()
    dynamics = _construct_test_dynamics(registry, rng_factory=ConstantGaussianRngFactory())
    baseline = registry[1].birth_bias
    boundary = baseline + direction * ATTRACTOR_DISPLACEMENT_RADIUS
    candidate = (
        baseline + direction * 2.0
        if obvious
        else math.nextafter(boundary, math.inf if direction > 0.0 else -math.inf)
    )
    before = dynamics.snapshot()
    with pytest.raises(InvalidAttractorMoveError) as exc:
        dynamics.move_attractor(
            AttractorMove("birth_01", candidate - baseline, "test", "reject out-of-domain move")
        )
    anomaly = exc.value.anomaly
    assert anomaly.code == "attractor_displacement_out_of_domain"
    assert anomaly.dim_id == "birth_01"
    assert anomaly.tick == 0
    assert anomaly.stage == "attractor_command"
    assert anomaly.baseline == baseline
    assert anomaly.current_attractor == baseline
    assert anomaly.delta == candidate - baseline
    assert anomaly.candidate_attractor == candidate
    assert anomaly.candidate_displacement == candidate - baseline
    assert anomaly.allowed_radius == ATTRACTOR_DISPLACEMENT_RADIUS
    assert not hasattr(anomaly, "clamped_value")
    assert not hasattr(anomaly, "applied_value")
    assert dynamics.snapshot() == before


def test_malformed_rng_draw_type_stream_index_and_seed_are_structured_and_atomic() -> None:
    from app.chatbox.field_dynamics import DynamicsContractError
    registry = tuple(_synthetic_registration(f"d-{i}") for i in range(3))

    factory_bad_type = _BadRngFactory("d-1", _WrongTypeRng)
    dynamics = _construct_test_dynamics(registry, rng_factory=factory_bad_type)
    before = dynamics.snapshot()
    with pytest.raises(InvalidRngDrawError) as exc:
        dynamics.tick()
    assert exc.value.anomaly.code == "invalid_rng_draw_type"
    assert exc.value.anomaly.dim_id == "d-1"
    assert dynamics.snapshot() == before
    assert dynamics.snapshot().tick == 0

    factory_bad_stream = _BadRngFactory("d-2", _MismatchedStreamRng)
    dynamics2 = _construct_test_dynamics(registry, rng_factory=factory_bad_stream)
    before2 = dynamics2.snapshot()
    with pytest.raises(InvalidRngDrawError) as exc:
        dynamics2.tick()
    assert exc.value.anomaly.code == "invalid_rng_draw"
    assert exc.value.anomaly.dim_id == "d-2"
    assert dynamics2.snapshot() == before2
    assert dynamics2.snapshot().tick == 0

    factory_bad_index = _BadRngFactory("d-0", _MismatchedIndexRng)
    dynamics3 = _construct_test_dynamics(registry, rng_factory=factory_bad_index)
    before3 = dynamics3.snapshot()
    with pytest.raises(InvalidRngDrawError) as exc:
        dynamics3.tick()
    assert exc.value.anomaly.code == "invalid_rng_draw"
    assert exc.value.anomaly.dim_id == "d-0"
    assert dynamics3.snapshot() == before3
    assert dynamics3.snapshot().tick == 0

    factory_seed_flip = _BadRngFactory("d-1", _SeedFlipRng)
    dynamics4 = _construct_test_dynamics(registry, rng_factory=factory_seed_flip)
    dynamics4.tick()
    before4 = dynamics4.snapshot()
    assert before4.tick == 1
    with pytest.raises(InvalidRngDrawError) as exc:
        dynamics4.tick()
    assert exc.value.anomaly.code == "invalid_rng_draw"
    assert exc.value.anomaly.dim_id == "d-1"
    assert dynamics4.snapshot() == before4
    assert dynamics4.snapshot().tick == 1


def test_independent_numerical_reconstruction_of_non_equilibrium_trajectory() -> None:
    registry = tuple(_synthetic_registration(f"d-{i}", bias=float(i) * 0.1) for i in range(3))
    factory = _ControlledRngFactory(default_value=0.7, seed=99)
    factory.set_value("d-1", -0.5)
    factory.set_value("d-2", 1.3)
    dynamics = _construct_test_dynamics(registry, rng_factory=factory)
    dynamics.move_attractor(AttractorMove("d-0", 0.4, "test", "displace"))
    dynamics.move_attractor(AttractorMove("d-2", -0.3, "test", "displace"))

    max_indep_residual = 0.0
    for tick_index in range(500):
        observation = dynamics.tick()
        assert observation.tick_after == tick_index + 1
        for dim_index, dim in enumerate(observation.dimensions):
            indep = _independent_reconstruct_dimension(
                dim.before_value,
                dim.before_velocity,
                dim.before_attractor,
                dim.before_ou_acceleration,
                dim.before_soft_restoring_baseline,
                dim.rng_draw.value,
                registry[dim_index],
            )
            assert dim.spring_coefficient == pytest.approx(indep["spring_coefficient"])
            assert dim.damping_coefficient == pytest.approx(indep["damping_coefficient"])
            assert dim.ou_rho == pytest.approx(indep["ou_rho"])
            assert dim.ou_innovation_scale == pytest.approx(indep["ou_innovation_scale"])
            assert dim.after_ou_acceleration == pytest.approx(indep["after_ou_acceleration"])
            assert dim.spring_acceleration == pytest.approx(indep["spring_acceleration"])
            assert dim.damping_acceleration == pytest.approx(indep["damping_acceleration"])
            assert dim.acceleration_without_soft_restoring == pytest.approx(
                indep["acceleration_without_soft_restoring"]
            )
            assert dim.velocity_proposal == pytest.approx(indep["velocity_proposal"])
            assert dim.pre_boundary_value_proposal == pytest.approx(
                indep["pre_boundary_value_proposal"]
            )
            assert dim.soft_restoring_acceleration == pytest.approx(
                indep["soft_restoring_acceleration"]
            )
            assert dim.after_velocity == pytest.approx(indep["after_velocity"])
            assert dim.after_value == pytest.approx(indep["after_value"])
            residual = max(
                abs(dim.spring_coefficient - indep["spring_coefficient"]),
                abs(dim.damping_coefficient - indep["damping_coefficient"]),
                abs(dim.ou_rho - indep["ou_rho"]),
                abs(dim.ou_innovation_scale - indep["ou_innovation_scale"]),
                abs(dim.after_ou_acceleration - indep["after_ou_acceleration"]),
                abs(dim.spring_acceleration - indep["spring_acceleration"]),
                abs(dim.damping_acceleration - indep["damping_acceleration"]),
                abs(
                    dim.acceleration_without_soft_restoring
                    - indep["acceleration_without_soft_restoring"]
                ),
                abs(dim.velocity_proposal - indep["velocity_proposal"]),
                abs(dim.pre_boundary_value_proposal - indep["pre_boundary_value_proposal"]),
                abs(dim.soft_restoring_acceleration - indep["soft_restoring_acceleration"]),
                abs(dim.after_velocity - indep["after_velocity"]),
                abs(dim.after_value - indep["after_value"]),
            )
            max_indep_residual = max(max_indep_residual, residual)
    print(f"independent_reconstruction max_residual={max_indep_residual:.3g}")
    assert max_indep_residual < 1e-12


def test_registry_reordering_is_trajectory_equivariant() -> None:
    registry = tuple(
        _synthetic_registration(f"perm-{index}", bias=(index - 3) / 20.0)
        for index in range(7)
    )
    permuted = tuple(registry[index] for index in (4, 0, 6, 2, 5, 1, 3))
    original = _construct_test_dynamics(
        registry, rng_factory=SeededGaussianRngFactory(PRODUCTION_SEED)
    )
    reordered = _construct_test_dynamics(
        permuted, rng_factory=SeededGaussianRngFactory(PRODUCTION_SEED)
    )
    for index, registration in enumerate(registry):
        delta = ((index % 3) - 1) * 0.17
        original.move_attractor(
            AttractorMove(registration.dim_id, delta, "test", "permutation equivariance")
        )
        reordered.move_attractor(
            AttractorMove(registration.dim_id, delta, "test", "permutation equivariance")
        )

    for _ in range(250):
        original_observation = original.tick()
        reordered_observation = reordered.tick()
        original_by_id = {
            item.dim_id: item for item in original_observation.dimensions
        }
        reordered_by_id = {
            item.dim_id: item for item in reordered_observation.dimensions
        }
        assert reordered_by_id == original_by_id
    assert {
        item.dim_id: item for item in reordered.snapshot().dimensions
    } == {
        item.dim_id: item for item in original.snapshot().dimensions
    }


def test_ast_dynamic_import_check_rejects_attribute_form_dynamic_loading() -> None:
    source_path = Path(__file__).parents[2] / "app" / "chatbox" / "field_dynamics.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"__import__", "exec", "eval"}
        if isinstance(node, ast.Attribute):
            assert node.attr not in {
                "import_module",
                "importlib",
                "exec",
                "eval",
                "load_module",
                "loads",
            }
    assert "sys.path" not in source
    for quarantined in (
        "agentlib",
        "agent_kernel",
        "semantic_trigger",
        "demos/scenarios",
        "docs/archive",
    ):
        assert quarantined not in source


def test_derived_parameter_domain_legal_boundary_and_default_passes() -> None:
    """Default registry and boundary-legal derived parameters must construct."""
    for registration in build_birth_registry():
        assert (1.0 / registration.fast_e_fold_s) <= 0.5
        assert registration.soft_boundary_strength < 1.0
    boundary_fast = replace(_synthetic_registration("boundary"), fast_e_fold_s=2.0)
    assert (1.0 / boundary_fast.fast_e_fold_s) == 0.5
    boundary_strength = replace(
        _synthetic_registration("boundary_s"), soft_boundary_strength=0.999999
    )
    assert boundary_strength.soft_boundary_strength < 1.0
    dynamics = _construct_test_dynamics(
        (boundary_fast, boundary_strength),
        rng_factory=ConstantGaussianRngFactory(),
    )
    assert len(dynamics.registry) == 2


def test_zero_soft_restoring_has_immutable_dimension_contract_declaration() -> None:
    registration = replace(_synthetic_registration("disabled"), soft_boundary_strength=0.0)
    declarations = registration.contract_declarations
    assert len(declarations) == 1
    declaration = declarations[0]
    assert declaration.dim_id == registration.dim_id
    assert declaration.code == "soft_restoring_disabled"
    assert "soft restoring disabled" in declaration.detail
    assert "不承诺 baseline 恢复" in declaration.detail
    with pytest.raises(FrozenInstanceError):
        setattr(declaration, "detail", "changed")
    dynamics = _construct_test_dynamics((registration,), rng_factory=ConstantGaussianRngFactory())
    assert dynamics.registry[0].contract_declarations == declarations


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"fast_e_fold_s": 1e308}, "degenerate_spring_damper_coefficients"),
        ({"ou_correlation_e_fold_s": 1e308}, "degenerate_ou_coefficients"),
        (
            {"ou_acceleration_sigma": math.nextafter(0.0, 1.0)},
            "degenerate_ou_coefficients",
        ),
    ],
)
def test_derived_binary64_coefficients_reject_actual_degeneracy(
    changes: dict[str, float], code: str
) -> None:
    with pytest.raises(InvalidRegistrationError) as exc:
        replace(_synthetic_registration("degenerate"), **changes)
    assert exc.value.anomaly.code == code
    assert exc.value.anomaly.dim_id == "degenerate"
    assert exc.value.anomaly.stage == "registration"


def test_derived_parameter_domain_rejects_spring_damper_ringing() -> None:
    """fast_e_fold_s < 2.0 causes 1-second semi-implicit critical spring-damper ringing."""
    template = _synthetic_registration("ring")
    with pytest.raises(InvalidRegistrationError) as exc:
        replace(template, fast_e_fold_s=1.999999)
    assert exc.value.anomaly.code == "spring_damper_ringing"
    assert exc.value.anomaly.dim_id == "ring"
    assert exc.value.anomaly.stage == "registration"
    with pytest.raises(InvalidRegistrationError) as exc2:
        replace(template, fast_e_fold_s=1.0)
    assert exc2.value.anomaly.code == "spring_damper_ringing"


def test_soft_mapping_rejects_non_strict_or_degenerate_boundary() -> None:
    """Proposal-sampled soft mapping must remain strictly monotonic and non-degenerate."""
    template = _synthetic_registration("unstable")
    with pytest.raises(InvalidRegistrationError) as exc:
        replace(template, soft_boundary_strength=1.0)
    assert exc.value.anomaly.code == "soft_mapping_not_strict_monotonic"
    assert exc.value.anomaly.dim_id == "unstable"
    assert exc.value.anomaly.stage == "registration"
    assert "strict monotonic/non-degenerate mapping" in exc.value.anomaly.detail
    assert "instability" not in exc.value.anomaly.detail
    with pytest.raises(InvalidRegistrationError) as exc2:
        replace(template, soft_boundary_strength=2.0)
    assert exc2.value.anomaly.code == "soft_mapping_not_strict_monotonic"
    with pytest.raises(InvalidRegistrationError) as exc3:
        replace(template, soft_boundary_strength=math.nextafter(1.0, 0.0))
    assert exc3.value.anomaly.code == "soft_mapping_not_strict_monotonic"


def test_derived_parameter_domain_rejection_is_atomic_no_runnable_object() -> None:
    """Failed construction must not produce a usable DimensionRegistration or FieldDynamics."""
    template = _synthetic_registration("atomic")
    illegal_variants = (
        {"fast_e_fold_s": 1.5},
        {"soft_boundary_strength": 1.5},
    )
    for changes in illegal_variants:
        try:
            bad = replace(template, **changes)
        except InvalidRegistrationError:
            continue
        pytest.fail(f"Expected InvalidRegistrationError for {changes}, got {bad}")
    with pytest.raises(InvalidRegistrationError):
        _construct_test_dynamics(
            (replace(template, fast_e_fold_s=1.5),),
            rng_factory=ConstantGaussianRngFactory(),
        )


def _soft_restoring_join_value(registration: DimensionRegistration) -> float:
    return (
        registration.soft_boundary_start
        + registration.soft_boundary_width
        - registration.soft_boundary_strength * registration.soft_boundary_width / 2.0
    )


@dataclass(frozen=True, slots=True)
class _TaylorBall:
    """c + j*d + [-r,r], for d in a centered command interval."""

    center: float
    jacobian: float
    remainder: float

    def enclosure(self, radius: float) -> tuple[float, float]:
        span = _outward_up(
            _outward_up(abs(self.jacobian) * radius) + self.remainder
        )
        return (
            math.nextafter(self.center - span, -math.inf),
            math.nextafter(self.center + span, math.inf),
        )


@dataclass(frozen=True, slots=True)
class _SettledReleaseCertificateLeaf:
    lower_d: float
    upper_d: float
    certificate_tick: int
    settled_value: tuple[float, float]
    settled_velocity: tuple[float, float]
    equilibrium_residual: tuple[float, float]
    proposal: tuple[float, float]
    cone_margin: tuple[float, float]
    jacobian: tuple[float, float]
    remainder: tuple[float, float]


def _outward_up(value: float) -> float:
    return math.nextafter(value, math.inf)


def _roundoff(value: float) -> float:
    return _outward_up(math.ulp(value))


def _operation_roundoff(*values: float) -> float:
    scale = max((abs(value) for value in values), default=0.0)
    return _outward_up(math.ulp(scale))


def _ball_add(left: _TaylorBall, right: _TaylorBall, radius: float) -> _TaylorBall:
    center = left.center + right.center
    jacobian = left.jacobian + right.jacobian
    remainder = _outward_up(left.remainder + right.remainder)
    remainder = _outward_up(
        remainder + _operation_roundoff(left.center, right.center, center)
    )
    remainder = _outward_up(
        remainder + _outward_up(_roundoff(jacobian) * radius)
    )
    return _TaylorBall(center, jacobian, remainder)


def _ball_scale(scalar: float, value: _TaylorBall, radius: float) -> _TaylorBall:
    center = scalar * value.center
    jacobian = scalar * value.jacobian
    remainder = _outward_up(abs(scalar) * value.remainder)
    remainder = _outward_up(
        remainder + _operation_roundoff(scalar, value.center, center)
    )
    remainder = _outward_up(
        remainder + _outward_up(_roundoff(jacobian) * radius)
    )
    return _TaylorBall(center, jacobian, remainder)


def _certificate_soft_value(q: float, registration: DimensionRegistration) -> float:
    """Independent public-parameter formula; the production private helper is not used."""
    start = registration.soft_boundary_start
    width = registration.soft_boundary_width
    strength = registration.soft_boundary_strength
    excess = abs(q) - start
    if excess <= 0.0:
        return q
    if excess < width:
        restoring = strength * excess * excess / (2.0 * width)
    else:
        restoring = strength * (excess - width / 2.0)
    return q - math.copysign(restoring, q)


def _certificate_soft_derivative(q: float, registration: DimensionRegistration) -> float:
    excess = abs(q) - registration.soft_boundary_start
    if excess <= 0.0:
        return 1.0
    if excess < registration.soft_boundary_width:
        return 1.0 - (
            registration.soft_boundary_strength
            * excess
            / registration.soft_boundary_width
        )
    return 1.0 - registration.soft_boundary_strength


def _ball_soft_map(
    proposal: _TaylorBall, radius: float, registration: DimensionRegistration
) -> _TaylorBall:
    """Outward C1 Taylor enclosure, valid even when a leaf straddles a soft join."""
    center = _certificate_soft_value(proposal.center, registration)
    derivative = _certificate_soft_derivative(proposal.center, registration)
    jacobian = derivative * proposal.jacobian
    proposal_lo, proposal_hi = proposal.enclosure(radius)
    start = registration.soft_boundary_start
    join = start + registration.soft_boundary_width
    inactive_branch = proposal_lo >= -start and proposal_hi <= start
    quadratic_branch = (
        proposal_lo >= start
        and proposal_hi <= join
        or proposal_lo >= -join
        and proposal_hi <= -start
    )
    linear_branch = proposal_lo >= join or proposal_hi <= -join
    displacement = _outward_up(
        _outward_up(abs(proposal.jacobian) * radius) + proposal.remainder
    )
    alpha = (
        registration.soft_boundary_strength
        / (2.0 * registration.soft_boundary_width)
    )
    if inactive_branch or linear_branch:
        nonlinear_remainder = 0.0
    elif quadratic_branch:
        nonlinear_remainder = _outward_up(
            _outward_up(alpha * displacement) * displacement
        )
    else:
        nonlinear_remainder = _outward_up(2.0 * displacement)
    remainder = _outward_up(abs(derivative) * proposal.remainder)
    remainder = _outward_up(remainder + nonlinear_remainder)
    remainder = _outward_up(
        remainder + _operation_roundoff(proposal.center, center)
    )
    remainder = _outward_up(
        remainder + _outward_up(_roundoff(jacobian) * radius)
    )
    return _TaylorBall(center, jacobian, remainder)


def _equilibrium_value_and_proposal(
    registration: DimensionRegistration, command: float
) -> tuple[float, float]:
    """Unique positive equilibrium, derived from D=q+beta*(q-start)^2."""
    assert command >= 0.0
    start = registration.soft_boundary_start
    if command <= start:
        return command, command
    k = (1.0 / registration.fast_e_fold_s) ** 2
    alpha = (
        registration.soft_boundary_strength
        / (2.0 * registration.soft_boundary_width)
    )
    beta = alpha * (1.0 / k - 1.0)
    shifted_command = command - start
    excess = 2.0 * shifted_command / (
        1.0 + math.sqrt(1.0 + 4.0 * beta * shifted_command)
    )
    proposal = start + excess
    value = proposal - alpha * excess * excess
    return value, proposal


def _equilibrium_ball(
    registration: DimensionRegistration, center_d: float, radius: float
) -> _TaylorBall:
    start = registration.soft_boundary_start
    if center_d + radius <= start:
        return _TaylorBall(center_d, 1.0, 0.0)
    if center_d - radius < start:
        center_value, _ = _equilibrium_value_and_proposal(registration, center_d)
        lower_value = center_d - radius
        upper_value, _ = _equilibrium_value_and_proposal(
            registration, center_d + radius
        )
        secant_slope = (upper_value - lower_value) / (2.0 * radius)
        remainder = max(
            abs(lower_value - (center_value - secant_slope * radius)),
            abs(upper_value - (center_value + secant_slope * radius)),
        )
        remainder = _outward_up(remainder + 8.0 * math.ulp(center_value))
        return _TaylorBall(center_value, secant_slope, remainder)
    k = (1.0 / registration.fast_e_fold_s) ** 2
    alpha = (
        registration.soft_boundary_strength
        / (2.0 * registration.soft_boundary_width)
    )
    beta = alpha * (1.0 / k - 1.0)
    value, proposal = _equilibrium_value_and_proposal(registration, center_d)
    excess = proposal - start
    proposal_prime = 1.0 / (1.0 + 2.0 * beta * excess)
    value_prime = proposal_prime * (1.0 - 2.0 * alpha * excess)
    _, lower_proposal = _equilibrium_value_and_proposal(
        registration, center_d - radius
    )
    lower_excess = lower_proposal - start
    lower_prime = 1.0 / (1.0 + 2.0 * beta * lower_excess)
    curvature_bound = (
        2.0
        * beta
        * lower_prime**3
        * (1.0 - 2.0 * alpha * lower_excess)
        + 2.0 * alpha * lower_prime**2
    )
    remainder = _outward_up(
        _outward_up(curvature_bound * radius) * radius / 2.0
    )
    remainder = _outward_up(remainder + _roundoff(value))
    remainder = _outward_up(
        remainder + _outward_up(_roundoff(value_prime) * radius)
    )
    return _TaylorBall(value, value_prime, remainder)


def _release_roots(registration: DimensionRegistration) -> tuple[float, float]:
    k = (1.0 / registration.fast_e_fold_s) ** 2
    c = 2.0 / registration.fast_e_fold_s
    trace = 2.0 - c - k
    discriminant = trace * trace - 4.0 * (1.0 - c)
    assert discriminant > 0.0
    root_delta = math.sqrt(discriminant)
    return ((trace - root_delta) / 2.0, (trace + root_delta) / 2.0)


def _certificate_tick(
    x: _TaylorBall,
    velocity: _TaylorBall,
    attractor: _TaylorBall | None,
    radius: float,
    registration: DimensionRegistration,
) -> tuple[_TaylorBall, _TaylorBall, _TaylorBall]:
    k = (1.0 / registration.fast_e_fold_s) ** 2
    c = 2.0 / registration.fast_e_fold_s
    spring_error = _ball_scale(-1.0, x, radius)
    if attractor is not None:
        spring_error = _ball_add(attractor, spring_error, radius)
    velocity_proposal = _ball_add(
        _ball_scale(1.0 - c, velocity, radius),
        _ball_scale(k, spring_error, radius),
        radius,
    )
    proposal = _ball_add(x, velocity_proposal, radius)
    next_x = _ball_soft_map(proposal, radius, registration)
    next_velocity = _ball_add(next_x, _ball_scale(-1.0, x, radius), radius)
    return next_x, next_velocity, proposal


def _recenter_roundoff(
    x: _TaylorBall, velocity: _TaylorBall, radius: float
) -> tuple[_TaylorBall, _TaylorBall]:
    """Wrap accumulated binary64 error using the registered stability margin."""
    registration = build_birth_registry()[0]
    k = (1.0 / registration.fast_e_fold_s) ** 2
    c = 2.0 / registration.fast_e_fold_s
    # The proposal soft map is non-expansive.  The critical spring-damper has
    # spectral radius below one; use its parameter-derived gap to cap the
    # accumulated one-tick outward roundoff rather than repeatedly boxing it.
    _, lambda_plus = _release_roots(registration)
    stability_gap = 1.0 - lambda_plus
    assert stability_gap > 0.0
    fresh_roundoff = _outward_up(
        64.0
        * math.ulp(
            max(
                1.0,
                abs(x.center),
                abs(velocity.center),
                abs(x.jacobian) * radius,
                abs(velocity.jacobian) * radius,
            )
        )
    )
    condition_factor = _outward_up((2.0 + c) / stability_gap)
    enclosed = _outward_up(condition_factor * fresh_roundoff)
    return (
        _TaylorBall(x.center, x.jacobian, enclosed),
        _TaylorBall(velocity.center, velocity.jacobian, enclosed),
    )


def _release_active_command_threshold(registration: DimensionRegistration) -> float:
    """Command whose exact equilibrium release proposal is exactly soft start."""
    k = (1.0 / registration.fast_e_fold_s) ** 2
    start = registration.soft_boundary_start
    target_value = start / (1.0 - k)
    alpha = (
        registration.soft_boundary_strength
        / (2.0 * registration.soft_boundary_width)
    )
    shifted_value = target_value - start
    excess = 2.0 * shifted_value / (
        1.0 + math.sqrt(1.0 - 4.0 * alpha * shifted_value)
    )
    proposal = start + excess
    beta = alpha * (1.0 / k - 1.0)
    return proposal + beta * excess * excess


def _certify_settled_release_leaf(
    registration: DimensionRegistration,
    lower_d: float,
    upper_d: float,
    *,
    settle_ticks: int = 20_000,
    release_tick_limit: int = 280,
) -> _SettledReleaseCertificateLeaf | tuple[
    int, tuple[float, float], tuple[float, float], _TaylorBall, _TaylorBall
]:
    center_d = (lower_d + upper_d) / 2.0
    radius = (upper_d - lower_d) / 2.0
    attractor = _TaylorBall(center_d, 1.0, 0.0)
    x = _TaylorBall(0.0, 0.0, 0.0)
    velocity = _TaylorBall(0.0, 0.0, 0.0)
    proposal = x
    for _ in range(settle_ticks):
        x, velocity, proposal = _certificate_tick(
            x, velocity, attractor, radius, registration
        )
        x, velocity = _recenter_roundoff(x, velocity, radius)
    settled_value = x.enclosure(radius)
    settled_velocity = velocity.enclosure(radius)
    lower_equilibrium = _equilibrium_value_and_proposal(registration, lower_d)[0]
    upper_equilibrium = _equilibrium_value_and_proposal(registration, upper_d)[0]
    equilibrium_residual = (
        math.nextafter(
            x.center
            - _equilibrium_value_and_proposal(registration, center_d)[0]
            - x.remainder,
            -math.inf,
        ),
        math.nextafter(
            x.center
            - _equilibrium_value_and_proposal(registration, center_d)[0]
            + x.remainder,
            math.inf,
        ),
    )
    lambda_minus, _ = _release_roots(registration)
    start = registration.soft_boundary_start
    for release_tick in range(1, release_tick_limit + 1):
        next_x, next_velocity, proposal = _certificate_tick(
            x, velocity, None, radius, registration
        )
        next_x, next_velocity = _recenter_roundoff(
            next_x, next_velocity, radius
        )
        proposal_enclosure = proposal.enclosure(radius)
        margin = _ball_add(
            next_x, _ball_scale(-lambda_minus, x, radius), radius
        ).enclosure(radius)
        if (
            proposal_enclosure[0] >= -start
            and proposal_enclosure[1] <= start
            and x.enclosure(radius)[0] > 0.0
            and margin[0] > 0.0
        ):
            return _SettledReleaseCertificateLeaf(
                lower_d,
                upper_d,
                release_tick,
                settled_value,
                settled_velocity,
                equilibrium_residual,
                proposal_enclosure,
                margin,
                (next_x.jacobian, next_velocity.jacobian),
                (next_x.remainder, next_velocity.remainder),
            )
        x, velocity = next_x, next_velocity
    return release_tick_limit, proposal_enclosure, margin, x, velocity


def test_default_settled_release_analytic_invariants_cover_exact_equilibria() -> None:
    registration = build_birth_registry()[0]
    k = (1.0 / registration.fast_e_fold_s) ** 2
    c = 2.0 / registration.fast_e_fold_s
    alpha = (
        registration.soft_boundary_strength
        / (2.0 * registration.soft_boundary_width)
    )
    beta = alpha * (1.0 / k - 1.0)
    start = registration.soft_boundary_start
    width = registration.soft_boundary_width
    radius = ATTRACTOR_DISPLACEMENT_RADIUS
    lambda_minus, lambda_plus = _release_roots(registration)

    assert k > 0.0 and 0.0 < c < 1.0 and alpha > 0.0 and beta > 0.0
    assert 0.0 < lambda_minus < lambda_plus < 1.0
    assert lambda_minus + lambda_plus == pytest.approx(
        2.0 - c - k, abs=8.0 * math.ulp(2.0)
    )
    assert lambda_minus * lambda_plus == pytest.approx(
        1.0 - c, abs=8.0 * math.ulp(1.0)
    )

    start_value, start_proposal = _equilibrium_value_and_proposal(registration, start)
    right_value = _equilibrium_value_and_proposal(
        registration, math.nextafter(start, math.inf)
    )[0]
    max_value, max_proposal = _equilibrium_value_and_proposal(registration, radius)
    assert start_value == start_proposal == start
    assert start_value < right_value < max_value < max_proposal < radius
    assert start < max_proposal < start + width
    for command in (
        0.0,
        start,
        _release_active_command_threshold(registration),
        radius,
    ):
        value, proposal = _equilibrium_value_and_proposal(registration, command)
        excess = max(0.0, proposal - start)
        reconstructed_command = proposal + beta * excess * excess
        reconstruction_ulp = (
            math.ulp(proposal)
            + math.ulp(beta) * excess * excess
            + 2.0 * beta * excess * math.ulp(proposal)
            + math.ulp(beta * excess * excess)
            + math.ulp(reconstructed_command)
        )
        assert command == pytest.approx(
            reconstructed_command, abs=8.0 * reconstruction_ulp
        )
        assert value == pytest.approx(
            proposal - alpha * excess * excess,
            abs=8.0 * math.ulp(max(1.0, proposal)),
        )
        assert (-value, -proposal) == (-value, -proposal)  # odd extension

    # D(q)'=1+2*beta*(q-start)>0 proves uniqueness/strict monotonicity;
    # A'(D)>0 and the matching value at start prove continuity and monotonicity.
    assert 1.0 + 2.0 * beta * (max_proposal - start) > 1.0
    assert 1.0 - 2.0 * alpha * (max_proposal - start) > 0.0

    min_margin = math.inf
    max_entry_tick = 0
    for command in (
        start,
        _release_active_command_threshold(registration),
        radius,
    ):
        x, _ = _equilibrium_value_and_proposal(registration, command)
        velocity = 0.0
        for release_tick in range(1, 281):
            u = (1.0 - c) * velocity - k * x
            q = x + u
            next_x = _certificate_soft_value(q, registration)
            margin = next_x - lambda_minus * x
            if q <= start and x > 0.0 and margin > 0.0:
                min_margin = min(min_margin, margin)
                max_entry_tick = max(max_entry_tick, release_tick)
                break
            velocity = next_x - x
            x = next_x
        else:
            pytest.fail(f"exact equilibrium failed to enter inactive cone: D={command}")
    assert max_entry_tick <= 280
    assert min_margin > 0.0

    zero = _construct_test_dynamics((registration,), rng_factory=ConstantGaussianRngFactory())
    zero.move_attractor(AttractorMove(registration.dim_id, 0.0, "test", "zero command"))
    for _ in range(20_000 + 280):
        observation = zero.tick().dimensions[0]
        assert observation.after_value == registration.birth_bias
        assert observation.after_velocity == 0.0
    assert 0.0 <= 0.15
    assert 0 <= 2


@pytest.mark.skip(reason="known invalid enclosure; P1.1-proof only; not behavior evidence")
def test_p1_1_proof_invalid_enclosure_is_quarantined() -> None:
    registration = build_birth_registry()[0]
    start = registration.soft_boundary_start
    release_active = _release_active_command_threshold(registration)
    domain_radius = ATTRACTOR_DISPLACEMENT_RADIUS
    minimum_width = domain_radius / (2**20)
    pending = [(start, release_active), (release_active, domain_radius)]
    leaves: list[_SettledReleaseCertificateLeaf] = []
    maximum_leaf_width = domain_radius / 64.0

    # The inactive [0,start] system is homogeneous.  A strict point certificate
    # at D=start therefore scales to every D>0; D=0 is proved separately above.
    inner = _certify_settled_release_leaf(registration, start, start)
    assert isinstance(inner, _SettledReleaseCertificateLeaf)
    leaves.append(
        replace(
            inner,
            lower_d=0.0,
            settled_value=(0.0, inner.settled_value[1]),
            settled_velocity=(
                min(0.0, inner.settled_velocity[0]),
                max(0.0, inner.settled_velocity[1]),
            ),
            equilibrium_residual=(
                min(0.0, inner.equilibrium_residual[0]),
                max(0.0, inner.equilibrium_residual[1]),
            ),
            proposal=(0.0, inner.proposal[1]),
        )
    )

    while pending:
        lower_d, upper_d = pending.pop()
        if upper_d - lower_d > maximum_leaf_width:
            midpoint = (lower_d + upper_d) / 2.0
            pending.append((midpoint, upper_d))
            pending.append((lower_d, midpoint))
            continue
        result = _certify_settled_release_leaf(registration, lower_d, upper_d)
        if isinstance(result, _SettledReleaseCertificateLeaf):
            leaves.append(result)
            continue
        width = upper_d - lower_d
        if width <= minimum_width:
            tick, proposal, margin, x, velocity = result
            pytest.fail(
                "outward certificate did not converge; "
                f"leaf=[{lower_d:.17g},{upper_d:.17g}] width={width:.3e} tick={tick} "
                f"state=({x.enclosure(width / 2.0)},{velocity.enclosure(width / 2.0)}) "
                f"proposal={proposal} jacobian=({x.jacobian:.17g},{velocity.jacobian:.17g}) "
                f"remainder=({x.remainder:.17g},{velocity.remainder:.17g}) margin={margin}"
            )
        midpoint = (lower_d + upper_d) / 2.0
        assert lower_d < midpoint < upper_d
        pending.append((midpoint, upper_d))
        pending.append((lower_d, midpoint))

    leaves.sort(key=lambda leaf: leaf.lower_d)
    assert leaves[0].lower_d == 0.0
    assert leaves[-1].upper_d == domain_radius
    for left, right in zip(leaves, leaves[1:]):
        assert left.upper_d == right.lower_d
    assert all(
        leaf.proposal[0] >= -start and leaf.proposal[1] <= start
        for leaf in leaves
    )
    assert all(leaf.cone_margin[0] > 0.0 for leaf in leaves)
    assert max(leaf.certificate_tick for leaf in leaves) <= 280

    max_a0 = max(leaf.settled_value[1] for leaf in leaves)
    max_value_residual = max(
        max(abs(bound) for bound in leaf.equilibrium_residual) for leaf in leaves
    )
    max_velocity_residual = max(
        max(abs(bound) for bound in leaf.settled_velocity) for leaf in leaves
    )
    min_cone_margin = min(leaf.cone_margin[0] for leaf in leaves)
    finest_width = min(
        leaf.upper_d - leaf.lower_d for leaf in leaves if leaf.lower_d >= start
    )
    assert start < max_a0 < start + registration.soft_boundary_width
    assert max_value_residual < 1.0e-7
    assert max_velocity_residual < 1.0e-8
    assert min_cone_margin > 0.0
    assert 0.0 <= 0.15
    assert 0 <= 2
    print(
        "settled_release_global_certificate "
        f"domain=[0,{domain_radius:.17g}] leaves={len(leaves)} "
        f"finest_width={finest_width:.3e} "
        f"max_certificate_tick={max(leaf.certificate_tick for leaf in leaves)} "
        f"max_A0_enclosure={max_a0:.17g} "
        f"max_settle_value_residual={max_value_residual:.3e} "
        f"max_settle_velocity_residual={max_velocity_residual:.3e} "
        f"min_cone_margin={min_cone_margin:.3e} "
        "first_overshoot_ratio=0 crossing_count=0"
    )


@pytest.mark.parametrize("dim_id", ["birth_01", "birth_02"])
@pytest.mark.parametrize(
    "command",
    [
        -ATTRACTOR_DISPLACEMENT_RADIUS,
        -1.0,
        1.0,
        ATTRACTOR_DISPLACEMENT_RADIUS,
    ],
)
def test_default_nonzero_baselines_match_relative_endpoint_and_branch_trajectories(
    dim_id: str, command: float
) -> None:
    """Birth biases -0.2/-0.1 are covered by translation plus binary64 ULP error."""
    registry = build_birth_registry()
    biased_registration = next(item for item in registry if item.dim_id == dim_id)
    zero_registration = replace(
        biased_registration, dim_id=f"{dim_id}-zero", birth_bias=0.0
    )
    biased = _construct_test_dynamics(
        (biased_registration,), rng_factory=ConstantGaussianRngFactory()
    )
    zero = _construct_test_dynamics((zero_registration,), rng_factory=ConstantGaussianRngFactory())
    biased.move_attractor(
        AttractorMove(dim_id, command, "test", "nonzero-baseline certificate anchor")
    )
    zero.move_attractor(
        AttractorMove(
            zero_registration.dim_id,
            command,
            "test",
            "zero-baseline certificate anchor",
        )
    )
    max_value_error = 0.0
    max_velocity_error = 0.0
    for tick in range(1, 20_000 + 270 + 1):
        biased_observation = biased.tick().dimensions[0]
        zero_observation = zero.tick().dimensions[0]
        if tick == 20_000:
            biased.move_attractor(
                AttractorMove(dim_id, -command, "test", "return to biased baseline")
            )
            zero.move_attractor(
                AttractorMove(
                    zero_registration.dim_id,
                    -command,
                    "test",
                    "return to zero baseline",
                )
            )
        relative_biased_value = (
            biased_observation.after_value
            - biased_observation.after_soft_restoring_baseline
        )
        value_error = abs(relative_biased_value - zero_observation.after_value)
        velocity_error = abs(
            biased_observation.after_velocity - zero_observation.after_velocity
        )
        value_scale = max(
            1.0,
            abs(biased_observation.after_value),
            abs(zero_observation.after_value),
            abs(biased_registration.birth_bias),
        )
        velocity_scale = max(
            1.0,
            abs(biased_observation.after_velocity),
            abs(zero_observation.after_velocity),
        )
        value_ulp_bound = 64.0 * tick * math.ulp(value_scale)
        velocity_ulp_bound = 128.0 * tick * math.ulp(velocity_scale)
        assert value_error <= value_ulp_bound
        assert velocity_error <= velocity_ulp_bound
        max_value_error = max(max_value_error, value_error)
        max_velocity_error = max(max_velocity_error, velocity_error)
    print(
        f"nonzero_baseline_translation dim_id={dim_id} "
        f"baseline={biased_registration.birth_bias:.17g} command={command:.17g} "
        f"max_value_error={max_value_error:.3e} "
        f"max_velocity_error={max_velocity_error:.3e}"
    )


def _soft_restoring_region_tolerance(
    registration: DimensionRegistration, displacement: float
) -> float:
    magnitude = abs(displacement)
    comparison_scale = max(
        magnitude,
        abs(registration.soft_boundary_start),
        abs(_soft_restoring_join_value(registration)),
    )
    return 32.0 * math.ulp(comparison_scale)


def _soft_restoring_region(
    registration: DimensionRegistration,
    displacement: float,
) -> str:
    magnitude = abs(displacement)
    tolerance = _soft_restoring_region_tolerance(registration, displacement)
    if magnitude <= registration.soft_boundary_start + tolerance:
        return "inactive"
    actual_join = _soft_restoring_join_value(registration)
    if magnitude < actual_join - tolerance:
        return "quadratic_transition"
    if abs(magnitude - actual_join) <= tolerance:
        return "join"
    return "linear_tail"


def _soft_restoring_proposal_region(
    registration: DimensionRegistration, displacement: float
) -> str:
    magnitude = abs(displacement)
    start = registration.soft_boundary_start
    proposal_join = start + registration.soft_boundary_width
    tolerance = 32.0 * math.ulp(max(magnitude, abs(start), abs(proposal_join)))
    if magnitude <= start + tolerance:
        return "inactive"
    if magnitude < proposal_join - tolerance:
        return "quadratic_transition"
    if abs(magnitude - proposal_join) <= tolerance:
        return "join"
    return "linear_tail"


def _equilibrium_offsets_for_displacement(
    registration: DimensionRegistration, displacement: float
) -> tuple[float, float]:
    """Return attractor and proposal offsets for a requested value equilibrium."""
    spring_coefficient = (1.0 / registration.fast_e_fold_s) ** 2
    strength = registration.soft_boundary_strength
    start = registration.soft_boundary_start
    width = registration.soft_boundary_width
    target = abs(displacement)
    actual_join = _soft_restoring_join_value(registration)

    # At equilibrium, with proposal-sampled restoring, q is the proposal
    # displacement, m(q) is the restoring magnitude, d = q - m(q), and
    # k * (attractor - d) = m(q).  Invert the active branch for q, then
    # derive the attractor instead of applying the linear-tail inverse globally.
    if target <= start:
        proposal = target
    elif target < actual_join:
        transition_target = target - start
        quadratic_coefficient = strength / (2.0 * width)
        discriminant = 1.0 - 4.0 * quadratic_coefficient * transition_target
        assert discriminant >= 0.0
        proposal = start + 2.0 * transition_target / (
            1.0 + math.sqrt(discriminant)
        )
    else:
        proposal = (
            target - strength * (start + width / 2.0)
        ) / (1.0 - strength)
    restoring_magnitude = proposal - target
    attractor = target + restoring_magnitude / spring_coefficient
    sign = -1.0 if displacement < 0.0 else 1.0
    return sign * attractor, sign * proposal


def _measure_return_oscillation(
    registration: DimensionRegistration,
    initial_attractor_displacement: float,
    settle_ticks: int = 20_000,
    measure_ticks: int = 50_000,
) -> dict:
    """Run a single-dimension return-to-baseline trajectory and measure oscillation metrics.

    Uses the real FieldDynamics with zero-OU (constant 0 RNG) and attractor moved
    to create the initial displacement, then attractor returned to baseline.
    """
    attractor_offset = initial_attractor_displacement
    dynamics = _construct_test_dynamics(
        (registration,),
        rng_factory=ConstantGaussianRngFactory(),
    )
    dynamics.move_attractor(
        AttractorMove(
            registration.dim_id,
            attractor_offset,
            "test",
            "displace to requested soft-restoring region",
        )
    )
    settle_observation = None
    for _ in range(settle_ticks):
        settle_observation = dynamics.tick()
    applied_attractor_offset = attractor_offset
    actual_settle_ticks = settle_ticks
    snapshot = dynamics.snapshot()
    d0 = snapshot.dimensions[0].value
    baseline = snapshot.dimensions[0].soft_restoring_baseline
    dynamics.move_attractor(
        AttractorMove(
            registration.dim_id,
            -applied_attractor_offset,
            "test",
            "return to baseline",
        )
    )
    A0 = abs(d0 - baseline)
    assert A0 > 0.0, "initial displacement must be nonzero"
    case_scale = max(1.0, abs(d0), abs(baseline), A0)
    tol = 32.0 * math.ulp(case_scale)
    assert tol < A0 * 1.0e-12, (
        f"zero tolerance {tol:.3e} is not negligible relative to A0={A0:.3e}"
    )
    errors: list[float] = []
    for _ in range(measure_ticks):
        obs = dynamics.tick()
        err = obs.dimensions[0].after_value - baseline
        errors.append(err)
    crossings = 0
    prev_sign = 1 if (d0 - baseline) > 0 else -1
    first_overshoot = 0.0
    peak_after_first = 0.0
    measuring_first_overshoot = False
    crossing_ticks: list[int] = []
    for tick, err in enumerate(errors, start=1):
        if abs(err) > tol:
            curr_sign = 1 if err > 0 else -1
            if curr_sign != prev_sign:
                crossings += 1
                crossing_ticks.append(tick)
                if crossings == 1:
                    measuring_first_overshoot = True
                    peak_after_first = 0.0
                elif crossings == 2:
                    first_overshoot = peak_after_first
                    measuring_first_overshoot = False
            prev_sign = curr_sign
        if measuring_first_overshoot and abs(err) > peak_after_first:
            peak_after_first = abs(err)
    if crossings == 0:
        first_overshoot = 0.0
    elif crossings == 1:
        first_overshoot = peak_after_first
    end_err = abs(errors[-1])
    ratio = first_overshoot / A0 if A0 > 0 else 0.0
    return {
        "A0": A0,
        "first_overshoot": first_overshoot,
        "ratio": ratio,
        "crossings": crossings,
        "crossing_ticks": tuple(crossing_ticks),
        "end_err": end_err,
        "ticks": measure_ticks,
        "seconds": float(measure_ticks),
        "fast_time_constants": measure_ticks / registration.fast_e_fold_s,
        "scanned_ticks": len(errors),
        "zero_tolerance": tol,
        "value_start": d0,
        "baseline": baseline,
        "direction": 1.0 if (d0 - baseline) > 0.0 else -1.0,
        "actual_region": _soft_restoring_region(registration, d0 - baseline),
        "region_tolerance": _soft_restoring_region_tolerance(
            registration, d0 - baseline
        ),
        "inactive_threshold": registration.soft_boundary_start,
        "join_threshold": _soft_restoring_join_value(registration),
        "join_delta": A0 - _soft_restoring_join_value(registration),
        "attractor_offset": applied_attractor_offset,
        "attractor_correction": 0.0,
        "equilibrium_proposal_offset": None,
        "actual_proposal_offset": abs(
            settle_observation.dimensions[0].pre_boundary_value_proposal - baseline
        ),
        "actual_proposal_region": _soft_restoring_proposal_region(
            registration,
            settle_observation.dimensions[0].pre_boundary_value_proposal - baseline,
        ),
        "settle_ticks": actual_settle_ticks,
    }


def test_soft_restoring_region_join_neighborhood_is_not_absorbed() -> None:
    registration = _synthetic_registration("join-neighborhood")
    join = _soft_restoring_join_value(registration)
    join_tolerance = _soft_restoring_region_tolerance(registration, join)
    neighbor_distance = 8.0 * join_tolerance
    below = join - neighbor_distance
    above = join + neighbor_distance
    below_tolerance = _soft_restoring_region_tolerance(registration, below)
    above_tolerance = _soft_restoring_region_tolerance(registration, above)

    assert join - below > below_tolerance
    assert above - join > above_tolerance
    assert _soft_restoring_region(registration, below) == "quadratic_transition"
    assert _soft_restoring_region(registration, join) == "join"
    assert _soft_restoring_region(registration, above) == "linear_tail"
    print(
        f"join neighborhood join={join:.17g} tolerance={join_tolerance:.3e} "
        f"relative_tolerance={join_tolerance / join:.3e} "
        f"below={below:.17g} below_distance={join - below:.3e} "
        f"below_tolerance_multiple={(join - below) / below_tolerance:.3f} "
        f"below_region={_soft_restoring_region(registration, below)} "
        f"join_region={_soft_restoring_region(registration, join)} "
        f"above={above:.17g} above_distance={above - join:.3e} "
        f"above_tolerance_multiple={(above - join) / above_tolerance:.3f} "
        f"above_region={_soft_restoring_region(registration, above)}"
    )


@pytest.mark.parametrize("direction", [-1.0, 1.0])
@pytest.mark.parametrize(
    "target_region,initial_displacement",
    [
        ("inactive", 0.5),
        ("quadratic_transition", 1.10),
        ("linear_tail_attractor_command", 1.30),
        ("linear_tail_attractor_command", ATTRACTOR_DISPLACEMENT_RADIUS),
    ],
    ids=[
        "inner",
        "transition",
        "linear_tail",
        "contract_boundary",
    ],
)
def test_total_system_return_oscillation_bounds(
    direction: float, target_region: str, initial_displacement: float | None
) -> None:
    """Per-case total system return-to-baseline oscillation must satisfy approved bounds.

    Approved bounds: first_overshoot / A0 <= 0.15 and crossing_count <= 2.
    The named regions classify attractor commands.  Legal-domain settled
    equilibrium proposals remain in the quadratic transition, not the linear tail.
    """
    registration = _synthetic_registration("osc")
    assert initial_displacement is not None
    target_displacement = initial_displacement
    metrics = _measure_return_oscillation(
        registration,
        target_displacement * direction,
    )
    assert metrics["direction"] == direction, (
        f"actual direction {metrics['direction']:+.0f} != requested {direction:+.0f}"
    )
    expected_command_region = target_region.removesuffix("_attractor_command")
    assert _soft_restoring_proposal_region(registration, target_displacement) == expected_command_region
    if target_region.endswith("_attractor_command"):
        assert metrics["actual_proposal_region"] == "quadratic_transition"
    assert metrics["attractor_offset"] == target_displacement * direction
    assert metrics["scanned_ticks"] == metrics["ticks"]
    assert metrics["fast_time_constants"] >= 10.0
    assert metrics["ratio"] <= 0.15, (
        f"overshoot ratio {metrics['ratio']:.6f} > 0.15 "
        f"A0={metrics['A0']:.6f} overshoot={metrics['first_overshoot']:.6f}"
    )
    assert metrics["crossings"] <= 2, (
        f"crossing_count {metrics['crossings']} > 2; "
        f"all crossing ticks={metrics['crossing_ticks']}"
    )
    assert metrics["end_err"] < 1e-10, (
        f"end_err {metrics['end_err']:.3e} not close to baseline"
    )
    print(
        f"oscillation case target_region={target_region} "
        f"target_A0={target_displacement:.9g} value_start={metrics['value_start']:.9g} "
        f"baseline={metrics['baseline']:.9g} A0={metrics['A0']:.9g} "
        f"direction={metrics['direction']:+.0f} actual_region={metrics['actual_region']} "
        f"region_tolerance={metrics['region_tolerance']:.3e} "
        f"inactive_threshold={metrics['inactive_threshold']:.9g} "
        f"join_threshold={metrics['join_threshold']:.9g} "
        f"join_delta={metrics['join_delta']:+.3e} "
        f"join_delta_tolerance_ratio={metrics['join_delta'] / metrics['region_tolerance']:+.6f} "
        f"attractor_offset={metrics['attractor_offset']:.9g} "
        f"attractor_correction={metrics['attractor_correction']:+.3e} "
        f"proposal_offset={metrics['equilibrium_proposal_offset']} "
        f"actual_proposal={metrics['actual_proposal_offset']:.17g} "
        f"actual_proposal_region={metrics['actual_proposal_region']} "
        f"overshoot={metrics['first_overshoot']:.9g} ratio={metrics['ratio']:.9g} "
        f"crossing_count={metrics['crossings']} crossing_ticks={metrics['crossing_ticks']} "
        f"end_err={metrics['end_err']:.3e} ticks={metrics['ticks']} "
        f"settle_ticks={metrics['settle_ticks']} "
        f"seconds={metrics['seconds']:.0f} "
        f"fast_time_constants={metrics['fast_time_constants']:.6f} "
        f"zero_tolerance={metrics['zero_tolerance']:.3e}"
    )
