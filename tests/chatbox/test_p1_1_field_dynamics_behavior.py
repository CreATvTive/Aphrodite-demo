from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
import math

import pytest

from app.chatbox.field_dynamics import (
    ATTRACTOR_DISPLACEMENT_RADIUS,
    AttractorMove,
    FieldDynamics,
    InvalidAttractorMoveError,
    RngDraw,
)


BASELINE_COMMIT = "32cf4fa7f971be6ca41de0be5efe38f3ee2980d2"
MASTER_SEED = 0xA9F0D17E
GENERATOR_VERSION = "p1.1-settled-release-sha256-strata-v1"
RANDOM_CASE_COUNT = 1008
SETTLE_TICKS = 20_000
RELEASE_TICKS = 50_000
MAX_OVERSHOOT_RATIO = 0.15
MAX_CROSSINGS = 2
MAX_END_ERROR = 1.0e-10


class _ZeroRng:
    def __init__(self, stream: str) -> None:
        self._stream = stream

    def draw(self, draw_index: int) -> RngDraw:
        return RngDraw(MASTER_SEED, self._stream, draw_index, 0.0)


class _ZeroRngFactory:
    def create(self, stream: str) -> _ZeroRng:
        return _ZeroRng(stream)


@dataclass(frozen=True, slots=True)
class _CaseSpec:
    case_id: int | str
    digest: str
    dim_id: str
    baseline: float
    displacement: float


@dataclass(slots=True)
class _Extrema:
    max_abs_value: float = 0.0
    max_abs_velocity: float = 0.0
    max_abs_ou: float = 0.0
    max_abs_proposal: float = 0.0
    max_abs_soft_restoring: float = 0.0

    def update(self, dimension, baseline: float) -> None:
        self.max_abs_value = max(
            self.max_abs_value,
            abs(dimension.before_value - baseline),
            abs(dimension.after_value - baseline),
        )
        self.max_abs_velocity = max(
            self.max_abs_velocity,
            abs(dimension.before_velocity),
            abs(dimension.after_velocity),
        )
        self.max_abs_ou = max(
            self.max_abs_ou,
            abs(dimension.before_ou_acceleration),
            abs(dimension.after_ou_acceleration),
        )
        self.max_abs_proposal = max(
            self.max_abs_proposal,
            abs(dimension.pre_boundary_value_proposal - baseline),
            abs(dimension.velocity_proposal),
        )
        self.max_abs_soft_restoring = max(
            self.max_abs_soft_restoring,
            abs(dimension.soft_restoring_acceleration),
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "max_abs_value": self.max_abs_value,
            "max_abs_velocity": self.max_abs_velocity,
            "max_abs_ou": self.max_abs_ou,
            "max_abs_proposal": self.max_abs_proposal,
            "max_abs_soft_restoring": self.max_abs_soft_restoring,
        }


@dataclass(slots=True)
class _CaseRuntime:
    spec: _CaseSpec
    A0: float
    value_start: float
    tol: float
    initial_sign: int
    extrema: _Extrema
    crossings: int = 0
    crossing_ticks: list[int] = field(default_factory=list)
    last_nonzero_sign: int = 0
    measuring_first_lobe: bool = False
    first_lobe_peak: float = 0.0
    first_overshoot: float = 0.0

    @property
    def ratio(self) -> float:
        peak = self.first_overshoot
        if self.crossings == 1:
            peak = self.first_lobe_peak
        return peak / self.A0 if self.A0 > 0.0 else 0.0


@dataclass(frozen=True, slots=True)
class _CaseResult:
    spec: _CaseSpec
    A0: float
    tol: float
    ratio: float
    crossings: int
    crossing_ticks: tuple[int, ...]
    end_error: float
    extrema: _Extrema


def _default_parameters() -> tuple[FieldDynamics, float, float, float, float, float]:
    dynamics = FieldDynamics(rng_factory=_ZeroRngFactory())
    first = dynamics.registry[0]
    S0 = first.soft_boundary_start
    W = first.soft_boundary_width
    S1 = S0 + W
    B_alarm = 4.0 * (S0 + W)
    for registration in dynamics.registry:
        assert registration.soft_boundary_start == S0
        assert registration.soft_boundary_width == W
        assert registration.soft_boundary_strength == first.soft_boundary_strength
    assert B_alarm == 5.0
    return dynamics, ATTRACTOR_DISPLACEMENT_RADIUS, S0, W, S1, B_alarm


def _soft_map_join_image(dynamics: FieldDynamics) -> float:
    registration = dynamics.registry[0]
    return (
        registration.soft_boundary_start
        + registration.soft_boundary_width
        - registration.soft_boundary_strength * registration.soft_boundary_width / 2.0
    )


def _release_active_command_threshold(dynamics: FieldDynamics) -> float:
    registration = dynamics.registry[0]
    spring_coefficient = (1.0 / registration.fast_e_fold_s) ** 2
    start = registration.soft_boundary_start
    target_value = start / (1.0 - spring_coefficient)
    alpha = registration.soft_boundary_strength / (2.0 * registration.soft_boundary_width)
    shifted_value = target_value - start
    excess = 2.0 * shifted_value / (
        1.0 + math.sqrt(1.0 - 4.0 * alpha * shifted_value)
    )
    proposal = start + excess
    beta = alpha * (1.0 / spring_coefficient - 1.0)
    return proposal + beta * excess * excess


def _deterministic_grid() -> tuple[float, ...]:
    dynamics, R, S0, W, S1, _ = _default_parameters()
    join_image = _soft_map_join_image(dynamics)
    release_active = _release_active_command_threshold(dynamics)
    nodes = {R * k / 8.0 for k in range(-8, 9)}

    def add_symmetric(value: float) -> None:
        nodes.add(value)
        nodes.add(-value)

    for boundary in (S0, S1, release_active):
        add_symmetric(math.nextafter(boundary, 0.0))
        add_symmetric(boundary)
        add_symmetric(math.nextafter(boundary, math.inf))
    add_symmetric(S0 + W / 2.0)
    add_symmetric(join_image)
    add_symmetric(math.nextafter(R, 0.0))
    nodes.update((-R, 0.0, R))
    result = tuple(sorted(nodes))
    assert len(result) == 41, result
    return result


def _random_cases() -> tuple[_CaseSpec, ...]:
    dynamics, R, _, _, _, _ = _default_parameters()
    dimension_count = len(dynamics.registry)
    assert RANDOM_CASE_COUNT % dimension_count == 0
    digests: list[bytes] = []
    dim_ids: list[str] = []
    for case_id in range(RANDOM_CASE_COUNT):
        dim_id = dynamics.registry[case_id % dimension_count].dim_id
        payload = (
            f"{GENERATOR_VERSION}\n{MASTER_SEED:#x}\n{case_id}\n{dim_id}"
        ).encode("utf-8")
        digests.append(hashlib.sha256(payload).digest())
        dim_ids.append(dim_id)
    ordered_case_ids = sorted(range(RANDOM_CASE_COUNT), key=lambda item: (digests[item], item))
    stratum_by_case_id = {
        case_id: stratum for stratum, case_id in enumerate(ordered_case_ids)
    }
    cases = []
    for case_id, (digest, dim_id) in enumerate(zip(digests, dim_ids)):
        high_53 = int.from_bytes(digest[:8], "big") >> 11
        jitter = (high_53 + 0.5) / float(1 << 53)
        assert 0.0 < jitter < 1.0
        stratum = stratum_by_case_id[case_id]
        displacement = -R + 2.0 * R * (stratum + jitter) / RANDOM_CASE_COUNT
        registration = dynamics.registry[case_id % dimension_count]
        cases.append(
            _CaseSpec(
                case_id,
                digest.hex(),
                dim_id,
                registration.birth_bias,
                displacement,
            )
        )
    assert all(math.isfinite(case.displacement) for case in cases)
    assert all(-R < case.displacement < R for case in cases)
    assert len({case.displacement for case in cases}) == RANDOM_CASE_COUNT
    return tuple(cases)


@pytest.fixture(scope="module", autouse=True)
def _print_behavior_version_header() -> None:
    dynamics, R, S0, _, S1, B_alarm = _default_parameters()
    grid = _deterministic_grid()
    dimension_count = len(dynamics.registry)
    header = {
        "type": "p1_1_behavior_evidence_header",
        "baseline_commit": BASELINE_COMMIT,
        "master_seed": f"0x{MASTER_SEED:08X}",
        "generator_version": GENERATOR_VERSION,
        "R": R,
        "S0": S0,
        "S1": S1,
        "grid_count": len(grid),
        "grid_nodes": grid,
        "random_count": RANDOM_CASE_COUNT,
        "batch_count": RANDOM_CASE_COUNT // dimension_count,
        "settle_ticks": SETTLE_TICKS,
        "release_ticks": RELEASE_TICKS,
        "B_alarm": B_alarm,
        "evidence_scope": "behavior evidence only",
        "proof_scope": "proof certificate excluded",
    }
    print(json.dumps(header, sort_keys=True, separators=(",", ":")))
    print(
        "D=1.3 and D=1.801 are linear-tail attractor-command region points; "
        "legal-domain actual settled proposals remain in the quadratic transition."
    )


def _observation_numbers(dimension) -> tuple[float, ...]:
    return (
        dimension.before_value,
        dimension.before_velocity,
        dimension.before_attractor,
        dimension.before_soft_restoring_baseline,
        dimension.before_ou_acceleration,
        dimension.spring_coefficient,
        dimension.damping_coefficient,
        dimension.spring_acceleration,
        dimension.damping_acceleration,
        dimension.ou_rho,
        dimension.ou_innovation_scale,
        dimension.rng_draw.value,
        dimension.after_ou_acceleration,
        dimension.acceleration_without_soft_restoring,
        dimension.velocity_proposal,
        dimension.pre_boundary_value_proposal,
        dimension.soft_boundary_displacement,
        dimension.soft_boundary_excess,
        dimension.soft_restoring_acceleration,
        dimension.after_value,
        dimension.after_velocity,
        dimension.after_attractor,
        dimension.after_soft_restoring_baseline,
    )


def _failure_payload(
    reason: str,
    phase: str,
    tick: int,
    spec: _CaseSpec | None,
    runtime: _CaseRuntime | None,
    extrema: _Extrema,
    **details,
) -> str:
    payload = {
        "reason": reason,
        "phase": phase,
        "tick": tick,
        "case_id": None if spec is None else spec.case_id,
        "digest": None if spec is None else spec.digest,
        "dim_id": None if spec is None else spec.dim_id,
        "baseline": None if spec is None else spec.baseline,
        "D": None if spec is None else spec.displacement,
        "A0": None if runtime is None else runtime.A0,
        "tol": None if runtime is None else runtime.tol,
        "crossing_ticks": [] if runtime is None else runtime.crossing_ticks,
        "ratio": None if runtime is None else runtime.ratio,
        "extrema": extrema.as_dict(),
    }
    payload.update(details)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _inspect_tick(
    observation,
    baselines: dict[str, float],
    B_alarm: float,
    phase: str,
    relative_tick: int,
    specs_by_dim: dict[str, _CaseSpec],
    runtimes: dict[str, _CaseRuntime],
    case_extrema: dict[str, _Extrema],
    pool_extrema: _Extrema,
) -> None:
    assert observation.anomalies == ()
    for dimension in observation.dimensions:
        baseline = baselines[dimension.dim_id]
        spec = specs_by_dim.get(dimension.dim_id)
        runtime = runtimes.get(dimension.dim_id)
        extrema = case_extrema.get(dimension.dim_id, pool_extrema)
        pool_extrema.update(dimension, baseline)
        if extrema is not pool_extrema:
            extrema.update(dimension, baseline)
        context = lambda reason, **details: _failure_payload(
            reason,
            phase,
            relative_tick,
            spec,
            runtime,
            extrema,
            **details,
        )
        assert dimension.anomalies == (), context("dimension anomaly")
        assert all(math.isfinite(value) for value in _observation_numbers(dimension)), context(
            "non-finite observation"
        )
        assert dimension.rng_draw.value == 0.0, context("nonzero RNG innovation")
        assert dimension.before_ou_acceleration == 0.0, context("nonzero before OU")
        assert dimension.after_ou_acceleration == 0.0, context("nonzero after OU")
        assert abs(dimension.before_value - baseline) <= B_alarm, context(
            "before value exceeded B_alarm", observed=dimension.before_value
        )
        assert abs(dimension.after_value - baseline) <= B_alarm, context(
            "after value exceeded B_alarm", observed=dimension.after_value
        )
        assert abs(dimension.pre_boundary_value_proposal - baseline) <= B_alarm, context(
            "pre-boundary proposal exceeded B_alarm",
            observed=dimension.pre_boundary_value_proposal,
        )
        assert abs(dimension.before_velocity) <= B_alarm, context(
            "before velocity exceeded B_alarm", observed=dimension.before_velocity
        )
        assert abs(dimension.after_velocity) <= B_alarm, context(
            "after velocity exceeded B_alarm", observed=dimension.after_velocity
        )
        assert abs(dimension.velocity_proposal) <= B_alarm, context(
            "velocity proposal exceeded B_alarm", observed=dimension.velocity_proposal
        )
        assert dimension.after_value == (
            dimension.pre_boundary_value_proposal + dimension.soft_restoring_acceleration
        ), context("after value contains silent correction")
        assert dimension.after_velocity == (
            dimension.velocity_proposal + dimension.soft_restoring_acceleration
        ), context("after velocity contains silent correction")


def _batch_summary(
    label: str,
    batch_index: int,
    specs: tuple[_CaseSpec, ...],
    results: tuple[_CaseResult, ...],
    pool_extrema: _Extrema,
    actual_ticks: int,
) -> None:
    print(
        json.dumps(
            {
                "type": "p1_1_behavior_batch_summary",
                "label": label,
                "batch": batch_index,
                "case_range": [specs[0].case_id, specs[-1].case_id],
                "case_count": len(specs),
                "dim_id_range": [min(item.dim_id for item in specs), max(item.dim_id for item in specs)],
                "baseline_range": [min(item.baseline for item in specs), max(item.baseline for item in specs)],
                "command_extrema": [
                    min(item.displacement for item in specs),
                    max(item.displacement for item in specs),
                ],
                "max_ratio": max(item.ratio for item in results),
                "max_crossings": max(item.crossings for item in results),
                **pool_extrema.as_dict(),
                "max_end_error": max(item.end_error for item in results),
                "actual_ticks": actual_ticks,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _run_settled_release_batch(
    label: str, batch_index: int, specs: tuple[_CaseSpec, ...]
) -> tuple[_CaseResult, ...]:
    dynamics, _, _, _, _, B_alarm = _default_parameters()
    registry_by_dim = {item.dim_id: item for item in dynamics.registry}
    assert len({item.dim_id for item in specs}) == len(specs)
    assert all(item.dim_id in registry_by_dim for item in specs)
    assert all(registry_by_dim[item.dim_id].birth_bias == item.baseline for item in specs)
    specs_by_dim = {item.dim_id: item for item in specs}
    baselines = {item.dim_id: item.birth_bias for item in dynamics.registry}
    case_extrema = {item.dim_id: _Extrema() for item in specs}
    pool_extrema = _Extrema()

    for spec in specs:
        dynamics.move_attractor(
            AttractorMove(spec.dim_id, spec.displacement, "p1.1-behavior", label)
        )

    for tick in range(1, SETTLE_TICKS + 1):
        observation = dynamics.tick()
        assert observation.tick_after == tick
        _inspect_tick(
            observation,
            baselines,
            B_alarm,
            "settle",
            tick,
            specs_by_dim,
            {},
            case_extrema,
            pool_extrema,
        )

    settled = {item.dim_id: item for item in dynamics.snapshot().dimensions}
    runtimes: dict[str, _CaseRuntime] = {}
    for spec in specs:
        state = settled[spec.dim_id]
        value_start = state.value
        A0 = abs(value_start - spec.baseline)
        tol = 32.0 * math.ulp(max(1.0, abs(value_start), abs(spec.baseline), A0))
        if spec.displacement == 0.0:
            runtime = _CaseRuntime(spec, 0.0, value_start, tol, 0, case_extrema[spec.dim_id])
            assert value_start == spec.baseline, _failure_payload(
                "zero command value not at exact equilibrium",
                "settle",
                SETTLE_TICKS,
                spec,
                runtime,
                runtime.extrema,
            )
            assert state.velocity == 0.0 and state.ou_acceleration == 0.0, _failure_payload(
                "zero command velocity or OU not zero",
                "settle",
                SETTLE_TICKS,
                spec,
                runtime,
                runtime.extrema,
            )
        else:
            initial_sign = 1 if value_start > spec.baseline else -1
            runtime = _CaseRuntime(
                spec,
                A0,
                value_start,
                tol,
                initial_sign,
                case_extrema[spec.dim_id],
                last_nonzero_sign=initial_sign,
            )
            assert tol / A0 <= 1.0e-9, _failure_payload(
                "deadband is not negligible relative to A0",
                "settle",
                SETTLE_TICKS,
                spec,
                runtime,
                runtime.extrema,
            )
        runtimes[spec.dim_id] = runtime
        dynamics.move_attractor(
            AttractorMove(
                spec.dim_id,
                spec.baseline - state.attractor,
                "p1.1-behavior",
                "settled release to baseline",
            )
        )

    for tick in range(1, RELEASE_TICKS + 1):
        observation = dynamics.tick()
        _inspect_tick(
            observation,
            baselines,
            B_alarm,
            "release",
            tick,
            specs_by_dim,
            runtimes,
            case_extrema,
            pool_extrema,
        )
        observations_by_dim = {item.dim_id: item for item in observation.dimensions}
        for dim_id, runtime in runtimes.items():
            dimension = observations_by_dim[dim_id]
            error = dimension.after_value - runtime.spec.baseline
            if runtime.spec.displacement == 0.0:
                assert error == 0.0, _failure_payload(
                    "zero command value left exact equilibrium",
                    "release",
                    tick,
                    runtime.spec,
                    runtime,
                    runtime.extrema,
                )
                assert dimension.after_velocity == 0.0 and dimension.after_ou_acceleration == 0.0, (
                    _failure_payload(
                        "zero command velocity or OU left zero",
                        "release",
                        tick,
                        runtime.spec,
                        runtime,
                        runtime.extrema,
                    )
                )
                continue
            if abs(error) > runtime.tol:
                sign = 1 if error > 0.0 else -1
                if sign != runtime.last_nonzero_sign:
                    runtime.crossings += 1
                    runtime.crossing_ticks.append(tick)
                    if runtime.crossings == 1:
                        runtime.measuring_first_lobe = True
                        runtime.first_lobe_peak = 0.0
                    elif runtime.crossings == 2:
                        runtime.first_overshoot = runtime.first_lobe_peak
                        runtime.measuring_first_lobe = False
                    assert runtime.crossings <= MAX_CROSSINGS, _failure_payload(
                        "crossing count exceeded bound",
                        "release",
                        tick,
                        runtime.spec,
                        runtime,
                        runtime.extrema,
                    )
                runtime.last_nonzero_sign = sign
            if runtime.measuring_first_lobe:
                runtime.first_lobe_peak = max(runtime.first_lobe_peak, abs(error))
                assert runtime.ratio <= MAX_OVERSHOOT_RATIO, _failure_payload(
                    "first overshoot ratio exceeded bound",
                    "release",
                    tick,
                    runtime.spec,
                    runtime,
                    runtime.extrema,
                )

    final = {item.dim_id: item for item in dynamics.snapshot().dimensions}
    results = []
    for runtime in runtimes.values():
        end_error = abs(final[runtime.spec.dim_id].value - runtime.spec.baseline)
        result = _CaseResult(
            runtime.spec,
            runtime.A0,
            runtime.tol,
            runtime.ratio,
            runtime.crossings,
            tuple(runtime.crossing_ticks),
            end_error,
            runtime.extrema,
        )
        assert result.ratio <= MAX_OVERSHOOT_RATIO, _failure_payload(
            "first overshoot ratio exceeded bound",
            "release-final",
            RELEASE_TICKS,
            runtime.spec,
            runtime,
            runtime.extrema,
            end_error=end_error,
        )
        assert result.crossings <= MAX_CROSSINGS, _failure_payload(
            "crossing count exceeded bound",
            "release-final",
            RELEASE_TICKS,
            runtime.spec,
            runtime,
            runtime.extrema,
            end_error=end_error,
        )
        assert end_error < MAX_END_ERROR, _failure_payload(
            "end error exceeded bound",
            "release-final",
            RELEASE_TICKS,
            runtime.spec,
            runtime,
            runtime.extrema,
            end_error=end_error,
        )
        results.append(result)
    result_tuple = tuple(results)
    actual_ticks = dynamics.snapshot().tick
    assert actual_ticks == SETTLE_TICKS + RELEASE_TICKS
    _batch_summary(label, batch_index, specs, result_tuple, pool_extrema, actual_ticks)
    return result_tuple


def _print_global_summary(label: str, results: tuple[_CaseResult, ...]) -> None:
    worst_ratio = max(results, key=lambda item: item.ratio)
    worst_end = max(results, key=lambda item: item.end_error)
    worst_value = max(results, key=lambda item: item.extrema.max_abs_value)
    print(
        json.dumps(
            {
                "type": "p1_1_behavior_global_summary",
                "label": label,
                "case_count": len(results),
                "max_ratio_case": {
                    "case_id": worst_ratio.spec.case_id,
                    "digest": worst_ratio.spec.digest,
                    "dim_id": worst_ratio.spec.dim_id,
                    "baseline": worst_ratio.spec.baseline,
                    "D": worst_ratio.spec.displacement,
                    "A0": worst_ratio.A0,
                    "tol": worst_ratio.tol,
                    "crossing_ticks": worst_ratio.crossing_ticks,
                    "ratio": worst_ratio.ratio,
                    "extrema": worst_ratio.extrema.as_dict(),
                },
                "max_crossings": max(item.crossings for item in results),
                "max_end_error_case": [worst_end.spec.case_id, worst_end.end_error],
                "max_abs_value_case": [
                    worst_value.spec.case_id,
                    worst_value.extrema.max_abs_value,
                ],
                "result": "behavior evidence only",
                "proof": "proof certificate excluded",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def test_p1_1_behavior_command_domain_is_closed_and_rejection_is_atomic() -> None:
    for direction in (-1.0, 1.0):
        accepted = FieldDynamics(rng_factory=_ZeroRngFactory())
        dim_id = accepted.registry[1].dim_id
        baseline = accepted.registry[1].birth_bias
        accepted_snapshot = accepted.move_attractor(
            AttractorMove(
                dim_id,
                direction * ATTRACTOR_DISPLACEMENT_RADIUS,
                "p1.1-behavior",
                "exact closed command boundary",
            )
        )
        accepted_dimension = next(
            item for item in accepted_snapshot.dimensions if item.dim_id == dim_id
        )
        assert accepted_dimension.attractor == baseline + direction * ATTRACTOR_DISPLACEMENT_RADIUS

        rejected = FieldDynamics(rng_factory=_ZeroRngFactory())
        control = FieldDynamics(rng_factory=_ZeroRngFactory())
        boundary = baseline + direction * ATTRACTOR_DISPLACEMENT_RADIUS
        outward = math.nextafter(boundary, math.inf if direction > 0.0 else -math.inf)
        before = rejected.snapshot()
        with pytest.raises(InvalidAttractorMoveError) as caught:
            rejected.move_attractor(
                AttractorMove(
                    dim_id,
                    outward - baseline,
                    "p1.1-behavior",
                    "reject outward nextafter",
                )
            )
        anomaly = caught.value.anomaly
        assert anomaly.code == "attractor_displacement_out_of_domain"
        assert "clamped" not in anomaly.code and "applied" not in anomaly.code
        assert "clamped" not in anomaly.detail and "applied" not in anomaly.detail
        assert not hasattr(anomaly, "clamped_value")
        assert not hasattr(anomaly, "applied_value")
        assert rejected.snapshot() == before == control.snapshot()
        assert rejected.tick() == control.tick()
        assert rejected.snapshot() == control.snapshot()


def test_p1_1_behavior_settled_release_deterministic_grid() -> None:
    dynamics, _, S0, _, S1, _ = _default_parameters()
    grid = _deterministic_grid()
    zero_baseline = tuple(item for item in dynamics.registry if item.birth_bias == 0.0)
    assert len(zero_baseline) == len(dynamics.registry) - 2
    results: list[_CaseResult] = []
    for batch_index, start in enumerate(range(0, len(grid), len(zero_baseline))):
        nodes = grid[start : start + len(zero_baseline)]
        specs = tuple(
            _CaseSpec(
                f"grid-{start + offset:02d}",
                "deterministic-grid",
                registration.dim_id,
                registration.birth_bias,
                displacement,
            )
            for offset, (registration, displacement) in enumerate(zip(zero_baseline, nodes))
        )
        results.extend(_run_settled_release_batch("deterministic-grid", batch_index, specs))

    nonzero_nodes = (
        -ATTRACTOR_DISPLACEMENT_RADIUS,
        -S1,
        -S0,
        0.0,
        S0,
        S1,
        ATTRACTOR_DISPLACEMENT_RADIUS,
    )
    biased = tuple(item for item in dynamics.registry if item.dim_id in {"birth_01", "birth_02"})
    assert {item.birth_bias for item in biased} == {-0.2, -0.1}
    for node_index, displacement in enumerate(nonzero_nodes):
        specs = tuple(
            _CaseSpec(
                f"biased-{registration.dim_id}-{node_index}",
                "deterministic-nonzero-baseline",
                registration.dim_id,
                registration.birth_bias,
                displacement,
            )
            for registration in biased
        )
        results.extend(
            _run_settled_release_batch("deterministic-nonzero-baseline", node_index, specs)
        )
    assert len(results) == len(grid) + len(biased) * len(nonzero_nodes)
    _print_global_summary("deterministic-grid-and-nonzero-baseline", tuple(results))


def test_p1_1_behavior_settled_release_fixed_seed_random_1008() -> None:
    dynamics, _, _, _, _, _ = _default_parameters()
    dimension_count = len(dynamics.registry)
    cases = _random_cases()
    counts = Counter(case.dim_id for case in cases)
    assert set(counts) == {item.dim_id for item in dynamics.registry}
    assert set(counts.values()) == {RANDOM_CASE_COUNT // dimension_count}
    results: list[_CaseResult] = []
    for batch_index, start in enumerate(range(0, RANDOM_CASE_COUNT, dimension_count)):
        batch = cases[start : start + dimension_count]
        assert all(case.case_id // dimension_count == batch_index for case in batch)
        assert all(
            case.dim_id == dynamics.registry[case.case_id % dimension_count].dim_id
            for case in batch
            if isinstance(case.case_id, int)
        )
        results.extend(_run_settled_release_batch("fixed-seed-random", batch_index, batch))
    assert len(results) == RANDOM_CASE_COUNT
    _print_global_summary("fixed-seed-random-1008", tuple(results))


def test_p1_1_behavior_full_pool_concurrent_corners() -> None:
    dynamics, R, _, _, _, _ = _default_parameters()
    dimension_count = len(dynamics.registry)
    vectors = (
        tuple(R for _ in dynamics.registry),
        tuple(-R for _ in dynamics.registry),
        tuple(R if index % 2 == 0 else -R for index in range(dimension_count)),
        tuple(-R if index % 2 == 0 else R for index in range(dimension_count)),
    )
    results: list[_CaseResult] = []
    for corner_index, vector in enumerate(vectors):
        specs = tuple(
            _CaseSpec(
                f"corner-{corner_index}-{registration.dim_id}",
                "full-pool-corner",
                registration.dim_id,
                registration.birth_bias,
                displacement,
            )
            for registration, displacement in zip(dynamics.registry, vector)
        )
        results.extend(_run_settled_release_batch("full-pool-corner", corner_index, specs))
    assert len(results) == len(vectors) * dimension_count
    print("full-pool corners exercise public API and tick paths; they are not an axis-coupling proof.")
    _print_global_summary("full-pool-concurrent-corners", tuple(results))


def test_p1_1_behavior_zero_command_remains_exact_equilibrium() -> None:
    dynamics, _, _, _, _, _ = _default_parameters()
    specs = tuple(
        _CaseSpec(
            f"zero-{registration.dim_id}",
            "exact-equilibrium",
            registration.dim_id,
            registration.birth_bias,
            0.0,
        )
        for registration in dynamics.registry
    )
    results = _run_settled_release_batch("zero-command-equilibrium", 0, specs)
    assert all(
        item.A0 == 0.0
        and item.ratio == 0.0
        and item.crossings == 0
        and item.end_error == 0.0
        for item in results
    )
    _print_global_summary("zero-command-equilibrium", results)
