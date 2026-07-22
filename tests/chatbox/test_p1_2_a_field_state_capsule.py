from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, replace
import json
import math
from pathlib import Path

import pytest

import app.chatbox.field_dynamics as field_dynamics_module
import app.chatbox.field_state_capsule as capsule_module
from app.chatbox.field_dynamics import (
    ATTRACTOR_DISPLACEMENT_RADIUS,
    AttractorMove,
    DimensionRegistration,
    FieldDynamics,
    FieldSnapshot,
    RngDraw,
    SeededGaussianRngFactory,
)
from app.chatbox.field_state_capsule import (
    BINARY64_VERSION,
    CAPSULE_SCHEMA_VERSION,
    DYNAMICS_VERSION,
    RNG_ALGORITHM,
    RNG_PROVIDER,
    SLOW_STATE_VERSION,
    FieldStateCapsuleError,
    _capture_field_state_capsule,
    _construct_field_candidate,
    decode_field_state_capsule,
    encode_field_state_capsule,
)


ROOT_KEYS = (
    "schema_version",
    "dynamics_version",
    "binary64_version",
    "field_tick",
    "registry",
    "dimensions",
    "slow_state",
    "rng",
)
REGISTRY_KEYS = (
    "dim_id",
    "temporary_name",
    "birth_time",
    "strength",
    "trigger_count",
    "birth_bias",
    "fast_e_fold_s",
    "ou_correlation_e_fold_s",
    "ou_acceleration_sigma",
    "soft_boundary_start",
    "soft_boundary_width",
    "soft_boundary_strength",
)
DIMENSION_KEYS = ("dim_id", "value", "velocity", "attractor", "ou_acceleration")
BASELINE_KEYS = ("dim_id", "current_baseline")
RNG_STREAM_KEYS = ("stream", "seed", "next_cursor")


def _registration(dim_id: str, bias: float = 0.0) -> DimensionRegistration:
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


def _dynamics(count: int = 3, *, seed: int = 0x12A5) -> FieldDynamics:
    registry = tuple(
        _registration(f"custom-{index}", bias=(-0.125 if index == 0 else index / 10.0))
        for index in range(count)
    )
    return FieldDynamics(registry, rng_factory=SeededGaussianRngFactory(seed))


def _primitive(count: int = 3, *, ticks: int = 0) -> dict:
    dynamics = _dynamics(count)
    for _ in range(ticks):
        dynamics.tick()
    return encode_field_state_capsule(_capture_field_state_capsule(dynamics))


def _assert_code(primitive: object, code: str) -> FieldStateCapsuleError:
    with pytest.raises(FieldStateCapsuleError) as caught:
        decode_field_state_capsule(primitive)  # type: ignore[arg-type]
    assert caught.value.code == code
    return caught.value


def _assert_encode_code(capsule: object, code: str) -> FieldStateCapsuleError:
    with pytest.raises(FieldStateCapsuleError) as caught:
        encode_field_state_capsule(capsule)  # type: ignore[arg-type]
    assert caught.value.code == code
    return caught.value


def _set_path(primitive: dict, path: tuple[object, ...], value: object) -> None:
    target: object = primitive
    for part in path[:-1]:
        target = target[part]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]


def _stateful_primitive(count: int, tick: int) -> dict:
    primitive = _primitive(count)
    baseline = ATTRACTOR_DISPLACEMENT_RADIUS
    primitive["field_tick"] = tick
    for index, (dimension, baseline_item, stream) in enumerate(
        zip(
            primitive["dimensions"],
            primitive["slow_state"]["baselines"],
            primitive["rng"]["streams"],
        )
    ):
        baseline_item["current_baseline"] = baseline + index * 0.25
        dimension["value"] = 4.0 + index
        dimension["velocity"] = -2.0 - index * 0.125
        dimension["attractor"] = baseline_item["current_baseline"] + (0.5 - index * 0.1)
        dimension["ou_acceleration"] = 0.25 + index * 0.03125
        stream["next_cursor"] = tick
    return primitive


@pytest.mark.parametrize("ticks", [0, 1, 19])
def test_canonical_round_trip_is_exact_stable_ordered_and_unaliased(ticks: int) -> None:
    capsule = _capture_field_state_capsule(_dynamics())
    if ticks:
        live = _dynamics()
        for _ in range(ticks):
            live.tick()
        capsule = _capture_field_state_capsule(live)

    first = encode_field_state_capsule(capsule)
    second = encode_field_state_capsule(capsule)
    decoded = decode_field_state_capsule(first)

    assert decoded == capsule
    assert encode_field_state_capsule(decoded) == first == second
    assert json.dumps(first, ensure_ascii=False, separators=(",", ":")) == json.dumps(
        second, ensure_ascii=False, separators=(",", ":")
    )
    assert tuple(first) == ROOT_KEYS
    assert tuple(first["registry"][0]) == REGISTRY_KEYS
    assert tuple(first["dimensions"][0]) == DIMENSION_KEYS
    assert tuple(first["slow_state"]) == ("version", "baselines")
    assert tuple(first["slow_state"]["baselines"][0]) == BASELINE_KEYS
    assert tuple(first["rng"]) == ("provider", "provider_version", "algorithm", "streams")
    assert tuple(first["rng"]["streams"][0]) == RNG_STREAM_KEYS
    assert [item["dim_id"] for item in first["registry"]] == [
        item["dim_id"] for item in first["dimensions"]
    ]
    assert first is not second
    assert first["registry"] is not second["registry"]
    assert first["dimensions"][0] is not second["dimensions"][0]
    assert first["slow_state"]["baselines"] is not second["slow_state"]["baselines"]
    assert first["rng"]["streams"] is not second["rng"]["streams"]
    first["dimensions"][0]["value"] = 999.0
    first["registry"][0]["birth_bias"] = 888.0
    assert encode_field_state_capsule(capsule) == second


def test_canonical_codec_preserves_finite_binary64_and_signed_zero() -> None:
    primitive = _primitive(1)
    values = {
        "value": -0.0,
        "velocity": math.nextafter(0.0, 1.0),
        "attractor": 0.0,
        "ou_acceleration": -math.nextafter(0.0, 1.0),
    }
    primitive["dimensions"][0].update(values)
    primitive["slow_state"]["baselines"][0]["current_baseline"] = -0.0

    encoded = encode_field_state_capsule(decode_field_state_capsule(primitive))

    for key, expected in values.items():
        actual = encoded["dimensions"][0][key]
        assert actual == expected
        if actual == 0.0:
            assert math.copysign(1.0, actual) == math.copysign(1.0, expected)
    baseline = encoded["slow_state"]["baselines"][0]["current_baseline"]
    assert math.copysign(1.0, baseline) == -1.0


@pytest.mark.parametrize("ticks", [0, 23], ids=["tick-zero", "real-tick-n"])
def test_strict_encode_valid_capture_and_decoded_capsules_are_canonical_and_unaliased(
    ticks: int,
) -> None:
    dynamics = _dynamics(3, seed=0xC0DEC)
    for _ in range(ticks):
        dynamics.tick()
    captured = _capture_field_state_capsule(dynamics)

    captured_first = encode_field_state_capsule(captured)
    captured_second = encode_field_state_capsule(captured)
    decoded = decode_field_state_capsule(captured_first)
    decoded_first = encode_field_state_capsule(decoded)
    decoded_second = encode_field_state_capsule(decoded)

    assert decoded == captured
    assert captured_first == captured_second == decoded_first == decoded_second
    assert captured_first is not captured_second
    assert decoded_first is not decoded_second
    assert captured_first["registry"] is not captured_second["registry"]
    assert decoded_first["dimensions"][0] is not decoded_second["dimensions"][0]
    assert decoded_first["slow_state"]["baselines"] is not decoded_second["slow_state"]["baselines"]
    assert decoded_first["rng"]["streams"] is not decoded_second["rng"]["streams"]
    decoded_first["dimensions"][0]["value"] = 999.0
    decoded_first["rng"]["streams"][0]["next_cursor"] = 999
    assert encode_field_state_capsule(decoded) == decoded_second
    assert encode_field_state_capsule(captured) == captured_second


def test_strict_encode_valid_decoded_capsule_preserves_signed_zero_without_aliasing() -> None:
    primitive = _primitive(1)
    primitive["dimensions"][0].update(
        value=-0.0,
        velocity=math.nextafter(0.0, 1.0),
        attractor=0.0,
        ou_acceleration=-math.nextafter(0.0, 1.0),
    )
    primitive["slow_state"]["baselines"][0]["current_baseline"] = -0.0
    capsule = decode_field_state_capsule(primitive)

    first = encode_field_state_capsule(capsule)
    second = encode_field_state_capsule(capsule)

    assert first == second
    assert first is not second
    assert first["dimensions"][0] is not second["dimensions"][0]
    assert first["slow_state"]["baselines"][0] is not second["slow_state"]["baselines"][0]
    signed_values: tuple[tuple[float, float], ...] = (
        (first["dimensions"][0]["value"], primitive["dimensions"][0]["value"]),
        (first["dimensions"][0]["attractor"], primitive["dimensions"][0]["attractor"]),
        (
            first["slow_state"]["baselines"][0]["current_baseline"],
            primitive["slow_state"]["baselines"][0]["current_baseline"],
        ),
    )
    for actual, expected in signed_values:
        assert math.copysign(1.0, actual) == math.copysign(1.0, expected)


class _AttributeFailure:
    def __init__(self, error: BaseException) -> None:
        object.__setattr__(self, "_error", error)

    def __getattribute__(self, name: str):
        if name == "_error":
            return object.__getattribute__(self, name)
        raise object.__getattribute__(self, "_error")


class _IterationFailure:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def __iter__(self):
        raise self._error


class _EqualityFailureDimension:
    def __init__(self, source) -> None:
        self.dim_id = source.dim_id
        self.value = source.value
        self.velocity = source.velocity
        self.attractor = source.attractor
        self.ou_acceleration = source.ou_acceleration

    def __eq__(self, other: object) -> bool:
        raise RuntimeError("dimension equality blocked")


class _MaliciousDiagnosticError(Exception):
    def __init__(self) -> None:
        super().__init__("diagnostic protocols must not be called")
        self.calls = {"str": 0, "repr": 0, "format": 0}

    def __str__(self) -> str:
        self.calls["str"] += 1
        raise RuntimeError("malicious __str__ called")

    def __repr__(self) -> str:
        self.calls["repr"] += 1
        raise RuntimeError("malicious __repr__ called")

    def __format__(self, format_spec: str) -> str:
        self.calls["format"] += 1
        raise RuntimeError("malicious __format__ called")


class _ClassAttributeFailure:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    @property
    def __class__(self):
        raise self._error


class _EqualityDiagnosticFailureDimension:
    def __init__(self, source, error: Exception) -> None:
        self.dim_id = source.dim_id
        self.value = source.value
        self.velocity = source.velocity
        self.attractor = source.attractor
        self.ou_acceleration = source.ou_acceleration
        self._error = error

    def __eq__(self, other: object) -> bool:
        raise self._error


@pytest.mark.parametrize("branch", ["projection", "equality"], ids=["projection", "equality"])
def test_strict_encode_fixed_diagnostic_does_not_invoke_original_exception_protocols(
    branch: str,
) -> None:
    capsule = decode_field_state_capsule(_primitive(2))
    cause = _MaliciousDiagnosticError()
    if branch == "projection":
        invalid = replace(capsule, dimensions=(_AttributeFailure(cause),) + capsule.dimensions[1:])
        expected_detail = "capsule internal structure is not canonical"
    else:
        malicious = _EqualityDiagnosticFailureDimension(capsule.dimensions[0], cause)
        invalid = replace(capsule, dimensions=(malicious,) + capsule.dimensions[1:])
        expected_detail = "capsule failed strict decode or equality comparison"

    error = _assert_encode_code(invalid, "capsule_shape_invalid")

    assert error.stage == "encode"
    assert error.path == "$"
    assert error.detail == expected_detail
    assert error.__cause__ is cause
    assert cause.calls == {"str": 0, "repr": 0, "format": 0}


def test_strict_encode_maps_isinstance_failure_without_invoking_diagnostic_protocols() -> None:
    cause = _MaliciousDiagnosticError()
    invalid = _ClassAttributeFailure(cause)

    with pytest.raises(FieldStateCapsuleError) as caught:
        encode_field_state_capsule(invalid)  # type: ignore[arg-type]

    assert caught.value.code == "capsule_shape_invalid"
    assert caught.value.detail == "encode input must be a FieldStateCapsule"
    assert caught.value.stage == "encode"
    assert caught.value.path == "$"
    assert caught.value.__cause__ is cause
    assert cause.calls == {"str": 0, "repr": 0, "format": 0}


def test_strict_encode_maps_dimension_attribute_failure_to_stable_shape_error_with_cause() -> None:
    capsule = decode_field_state_capsule(_primitive(2))
    cause = ValueError("dimension attribute blocked")
    invalid = replace(capsule, dimensions=(_AttributeFailure(cause),) + capsule.dimensions[1:])

    error = _assert_encode_code(invalid, "capsule_shape_invalid")

    assert error.stage == "encode"
    assert error.__cause__ is cause


@pytest.mark.parametrize("section", ["registry", "dimensions"])
def test_strict_encode_maps_container_iteration_failure_to_stable_shape_error_with_cause(
    section: str,
) -> None:
    capsule = decode_field_state_capsule(_primitive(2))
    cause = RuntimeError(f"{section} iteration blocked")
    invalid = replace(capsule, **{section: _IterationFailure(cause)})

    error = _assert_encode_code(invalid, "capsule_shape_invalid")

    assert error.stage == "encode"
    assert error.__cause__ is cause


def test_strict_encode_maps_equality_failure_to_stable_shape_error_with_cause() -> None:
    capsule = decode_field_state_capsule(_primitive(2))
    malicious = _EqualityFailureDimension(capsule.dimensions[0])
    invalid = replace(capsule, dimensions=(malicious,) + capsule.dimensions[1:])

    error = _assert_encode_code(invalid, "capsule_shape_invalid")

    assert error.stage == "encode"
    assert isinstance(error.__cause__, RuntimeError)
    assert str(error.__cause__) == "dimension equality blocked"


def test_strict_encode_preserves_exact_decoder_code_for_future_schema() -> None:
    capsule = decode_field_state_capsule(_primitive(1))

    error = _assert_encode_code(
        replace(capsule, schema_version="future"),
        "unsupported_schema_version",
    )

    assert error.stage == "decode"
    assert error.path == "$.schema_version"
    assert error.__cause__ is None


@pytest.mark.parametrize(
    "exception_type",
    [KeyboardInterrupt, SystemExit, GeneratorExit],
    ids=["keyboard-interrupt", "system-exit", "generator-exit"],
)
def test_strict_encode_does_not_wrap_process_control_exceptions(exception_type) -> None:
    capsule = decode_field_state_capsule(_primitive(1))
    process_control = exception_type("process control")
    invalid = replace(capsule, dimensions=(_AttributeFailure(process_control),))

    with pytest.raises(exception_type) as caught:
        encode_field_state_capsule(invalid)

    assert caught.value is process_control


@pytest.mark.parametrize(
    "exception_type",
    [KeyboardInterrupt, SystemExit, GeneratorExit],
    ids=["keyboard-interrupt", "system-exit", "generator-exit"],
)
def test_strict_encode_isinstance_propagates_process_control_exceptions(exception_type) -> None:
    process_control = exception_type("process control")
    invalid = _ClassAttributeFailure(process_control)

    with pytest.raises(exception_type) as caught:
        encode_field_state_capsule(invalid)  # type: ignore[arg-type]

    assert caught.value is process_control


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("schema", "unsupported_schema_version"),
        ("dynamics", "unsupported_dynamics_version"),
        ("binary64", "unsupported_binary64_version"),
        ("slow-state", "unsupported_slow_state_version"),
        ("rng-provider", "unsupported_rng_provider"),
        ("rng-provider-version", "unsupported_rng_provider_version"),
        ("rng-algorithm", "unsupported_rng_algorithm"),
        ("negative-tick", "field_tick_invalid"),
        ("bool-tick", "capsule_numeric_type_invalid"),
        ("float-tick", "capsule_numeric_type_invalid"),
    ],
)
def test_strict_encode_rejects_direct_capsule_version_identity_and_tick_violations(
    case: str, code: str
) -> None:
    capsule = decode_field_state_capsule(_primitive(2, ticks=3))
    if case == "schema":
        invalid = replace(capsule, schema_version="future")
    elif case == "dynamics":
        invalid = replace(capsule, dynamics_version="future")
    elif case == "binary64":
        invalid = replace(capsule, binary64_version="future")
    elif case == "slow-state":
        invalid = replace(capsule, slow_state=replace(capsule.slow_state, version="future"))
    elif case == "rng-provider":
        invalid = replace(capsule, rng=replace(capsule.rng, provider="future"))
    elif case == "rng-provider-version":
        invalid = replace(capsule, rng=replace(capsule.rng, provider_version="future"))
    elif case == "rng-algorithm":
        invalid = replace(capsule, rng=replace(capsule.rng, algorithm="future"))
    elif case == "negative-tick":
        invalid = replace(capsule, field_tick=-1)
    elif case == "bool-tick":
        invalid = replace(capsule, field_tick=True)
    else:
        invalid = replace(capsule, field_tick=3.0)

    _assert_encode_code(invalid, code)


@pytest.mark.parametrize(
    ("bad_value", "code"),
    [
        (math.nan, "non_finite_capsule_value"),
        (math.inf, "non_finite_capsule_value"),
        (-math.inf, "non_finite_capsule_value"),
        (True, "capsule_numeric_type_invalid"),
        (1, "capsule_numeric_type_invalid"),
    ],
    ids=["nan", "positive-infinity", "negative-infinity", "bool", "int-as-float"],
)
def test_strict_encode_rejects_direct_capsule_noncanonical_binary64_values(
    bad_value: object, code: str
) -> None:
    capsule = decode_field_state_capsule(_primitive(2))
    dimensions = (replace(capsule.dimensions[0], value=bad_value),) + capsule.dimensions[1:]

    _assert_encode_code(replace(capsule, dimensions=dimensions), code)


def _replace_capsule_sequence(capsule, section: str, items):
    if section == "registry":
        return replace(capsule, registry=items)
    if section == "dimensions":
        return replace(capsule, dimensions=items)
    if section == "baselines":
        return replace(capsule, slow_state=replace(capsule.slow_state, baselines=items))
    return replace(capsule, rng=replace(capsule.rng, streams=items))


@pytest.mark.parametrize("section", ["registry", "dimensions", "baselines", "streams"])
@pytest.mark.parametrize("change", ["missing", "extra", "reversed", "id-mismatch"])
def test_strict_encode_rejects_direct_capsule_sequence_alignment_violations(
    section: str, change: str
) -> None:
    capsule = decode_field_state_capsule(_primitive(3))
    if section == "registry":
        items = capsule.registry
        code = "registry_alignment_invalid"
    elif section == "dimensions":
        items = capsule.dimensions
        code = "registry_alignment_invalid"
    elif section == "baselines":
        items = capsule.slow_state.baselines
        code = "registry_alignment_invalid"
    else:
        items = capsule.rng.streams
        code = "rng_stream_alignment_invalid"

    if change == "missing":
        changed = items[:-1]
    elif change == "extra":
        extra = _registration("extra") if section == "registry" else items[-1]
        changed = items + (extra,)
    elif change == "reversed":
        changed = tuple(reversed(items))
    elif section == "registry":
        changed = (replace(items[0], dim_id="misaligned"),) + items[1:]
    elif section == "dimensions":
        changed = (replace(items[0], dim_id="misaligned"),) + items[1:]
    elif section == "baselines":
        changed = (replace(items[0], dim_id="misaligned"),) + items[1:]
    else:
        changed = (replace(items[0], stream="misaligned"),) + items[1:]

    _assert_encode_code(_replace_capsule_sequence(capsule, section, changed), code)


@pytest.mark.parametrize("section", ["registry", "dimensions", "baselines", "streams"])
def test_strict_encode_rejects_lists_in_direct_capsule_tuple_fields(section: str) -> None:
    capsule = decode_field_state_capsule(_primitive(2))
    if section == "registry":
        items = capsule.registry
    elif section == "dimensions":
        items = capsule.dimensions
    elif section == "baselines":
        items = capsule.slow_state.baselines
    else:
        items = capsule.rng.streams

    invalid = _replace_capsule_sequence(capsule, section, list(items))

    _assert_encode_code(invalid, "capsule_shape_invalid")


@pytest.mark.parametrize(
    "section",
    ["registry-item", "dimension-item", "slow-state", "baseline-item", "rng", "stream-item"],
)
def test_strict_encode_maps_wrong_nested_object_types_to_stable_shape_error(
    section: str,
) -> None:
    capsule = decode_field_state_capsule(_primitive(2))
    if section == "registry-item":
        invalid = replace(capsule, registry=(None,) + capsule.registry[1:])
    elif section == "dimension-item":
        invalid = replace(capsule, dimensions=(None,) + capsule.dimensions[1:])
    elif section == "slow-state":
        invalid = replace(capsule, slow_state=None)
    elif section == "baseline-item":
        invalid = replace(
            capsule,
            slow_state=replace(capsule.slow_state, baselines=(None,) + capsule.slow_state.baselines[1:]),
        )
    elif section == "rng":
        invalid = replace(capsule, rng=None)
    else:
        invalid = replace(
            capsule,
            rng=replace(capsule.rng, streams=(None,) + capsule.rng.streams[1:]),
        )

    error = _assert_encode_code(invalid, "capsule_shape_invalid")
    assert error.stage == "encode"


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("cursor-mismatch", "rng_cursor_mismatch"),
        ("negative-cursor", "rng_cursor_mismatch"),
        ("seed-noninteger", "capsule_numeric_type_invalid"),
        ("seed-inconsistent", "rng_seed_inconsistent"),
        ("stream-misaligned", "rng_stream_alignment_invalid"),
    ],
)
def test_strict_encode_rejects_direct_capsule_rng_contract_violations(
    case: str, code: str
) -> None:
    capsule = decode_field_state_capsule(_primitive(2, ticks=4))
    streams = capsule.rng.streams
    if case == "cursor-mismatch":
        changed = streams[:-1] + (replace(streams[-1], next_cursor=5),)
    elif case == "negative-cursor":
        changed = streams[:-1] + (replace(streams[-1], next_cursor=-1),)
    elif case == "seed-noninteger":
        changed = (replace(streams[0], seed=1.0),) + streams[1:]
    elif case == "seed-inconsistent":
        changed = streams[:-1] + (replace(streams[-1], seed=streams[-1].seed + 1),)
    else:
        changed = (replace(streams[0], stream="misaligned"),) + streams[1:]
    invalid = replace(capsule, rng=replace(capsule.rng, streams=changed))

    _assert_encode_code(invalid, code)


@pytest.mark.parametrize("direction", [-1.0, 1.0], ids=["negative", "positive"])
def test_strict_encode_rejects_direct_capsule_attractor_outside_closed_domain(
    direction: float,
) -> None:
    capsule = decode_field_state_capsule(_primitive(1))
    baseline = capsule.slow_state.baselines[0].current_baseline
    boundary = baseline + direction * ATTRACTOR_DISPLACEMENT_RADIUS
    outside = math.nextafter(boundary, math.inf if direction > 0.0 else -math.inf)
    invalid = replace(
        capsule,
        dimensions=(replace(capsule.dimensions[0], attractor=outside),),
    )

    _assert_encode_code(invalid, "attractor_displacement_out_of_domain")


@pytest.mark.parametrize("direction", [-1.0, 1.0], ids=["negative", "positive"])
def test_strict_encode_accepts_direct_capsule_attractor_at_exact_closed_boundary(
    direction: float,
) -> None:
    capsule = decode_field_state_capsule(_primitive(1))
    baseline = capsule.slow_state.baselines[0].current_baseline
    boundary = baseline + direction * ATTRACTOR_DISPLACEMENT_RADIUS
    exact = replace(
        capsule,
        dimensions=(replace(capsule.dimensions[0], attractor=boundary),),
    )

    first = encode_field_state_capsule(exact)
    second = encode_field_state_capsule(exact)

    assert first == second
    assert first["dimensions"][0]["attractor"] == boundary
    assert first is not second
    assert first["dimensions"][0] is not second["dimensions"][0]


@pytest.mark.parametrize("value", [None, {}, [], object()], ids=["none", "dict", "list", "object"])
def test_strict_encode_rejects_non_capsule_input_with_stable_code(value: object) -> None:
    error = _assert_encode_code(value, "capsule_shape_invalid")
    assert error.stage == "encode"
    assert error.__cause__ is None


def test_capsule_data_is_frozen() -> None:
    capsule = decode_field_state_capsule(_primitive(1))
    with pytest.raises(FrozenInstanceError):
        capsule.field_tick = 1  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        capsule.dimensions[0].value = 1.0  # type: ignore[misc]
    with pytest.raises(TypeError):
        capsule.dimensions[0] = capsule.dimensions[0]  # type: ignore[index]


@pytest.mark.parametrize("count", [1, 3, 7, 12, 17])
@pytest.mark.parametrize("tick", [0, 11])
def test_restored_candidate_has_exact_next_tick_continuity(
    count: int, tick: int
) -> None:
    initial = decode_field_state_capsule(_stateful_primitive(count, tick))
    uninterrupted = _construct_field_candidate(initial)
    restored = _construct_field_candidate(
        decode_field_state_capsule(encode_field_state_capsule(_capture_field_state_capsule(uninterrupted)))
    )

    uninterrupted_observation = uninterrupted.tick()
    restored_observation = restored.tick()

    assert restored_observation == uninterrupted_observation
    assert restored.snapshot() == uninterrupted.snapshot()
    assert _capture_field_state_capsule(restored) == _capture_field_state_capsule(uninterrupted)
    assert all(item.rng_draw.draw_index == tick for item in restored_observation.dimensions)


@pytest.mark.parametrize("count", [1, 3, 7, 12, 17])
def test_synthetic_registry_count_and_custom_order_round_trip(count: int) -> None:
    registry = tuple(_registration(f"ordered-{index}", index / 16.0) for index in range(count))
    source = FieldDynamics(registry, rng_factory=SeededGaussianRngFactory(9876))
    source.tick()
    capsule = _capture_field_state_capsule(source)
    candidate = _construct_field_candidate(decode_field_state_capsule(encode_field_state_capsule(capsule)))

    expected_order = tuple(item.dim_id for item in registry)
    assert tuple(item.dim_id for item in capsule.registry) == expected_order
    assert tuple(item.dim_id for item in candidate.registry) == expected_order
    assert len(candidate.snapshot().dimensions) == count
    assert candidate.tick() == source.tick()


@pytest.mark.parametrize("direction", [-1.0, 1.0])
def test_attractor_domain_is_closed_baseline_relative_and_nextafter_exact(direction: float) -> None:
    baseline = ATTRACTOR_DISPLACEMENT_RADIUS
    boundary = baseline + direction * ATTRACTOR_DISPLACEMENT_RADIUS

    exact = _primitive(1)
    exact["slow_state"]["baselines"][0]["current_baseline"] = baseline
    exact["dimensions"][0]["attractor"] = boundary
    assert decode_field_state_capsule(exact).dimensions[0].attractor == boundary

    outward = deepcopy(exact)
    outward["dimensions"][0]["attractor"] = math.nextafter(
        boundary, math.inf if direction > 0.0 else -math.inf
    )
    _assert_code(outward, "attractor_displacement_out_of_domain")

    inward = deepcopy(exact)
    inward_value = math.nextafter(boundary, baseline)
    inward["dimensions"][0]["attractor"] = inward_value
    assert decode_field_state_capsule(inward).dimensions[0].attractor == inward_value


def test_large_finite_non_attractor_state_is_not_domain_rejected_or_clamped() -> None:
    primitive = _primitive(1)
    primitive["dimensions"][0].update(
        value=9.25,
        velocity=-8.5,
        attractor=0.0,
        ou_acceleration=7.75,
    )

    capsule = decode_field_state_capsule(primitive)
    candidate = _construct_field_candidate(capsule)

    assert capsule.dimensions[0].value == 9.25
    assert capsule.dimensions[0].velocity == -8.5
    assert capsule.dimensions[0].ou_acceleration == 7.75
    assert _capture_field_state_capsule(candidate) == capsule


def _missing_root() -> dict:
    value = _primitive(2)
    del value["dimensions"]
    return value


def _extra_root() -> dict:
    value = _primitive(2)
    value["extra"] = None
    return value


def _bad_nested_shape() -> dict:
    value = _primitive(2)
    del value["rng"]["provider"]
    return value


def _extra_nested_shape() -> dict:
    value = _primitive(2)
    value["slow_state"]["extra"] = None
    return value


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (_missing_root, "capsule_shape_invalid"),
        (_extra_root, "capsule_shape_invalid"),
        (_bad_nested_shape, "capsule_shape_invalid"),
        (_extra_nested_shape, "capsule_shape_invalid"),
        (lambda: [], "capsule_shape_invalid"),
        (lambda: {**_primitive(2), "registry": ()}, "capsule_shape_invalid"),
        (lambda: {**_primitive(2), "registry": []}, "registry_invalid"),
    ],
)
def test_strict_root_and_nested_shape(mutate, code: str) -> None:
    _assert_code(mutate(), code)


@pytest.mark.parametrize("section", [None, "rng"], ids=["root", "nested-rng"])
def test_mixed_string_and_integer_mapping_keys_are_shape_rejected(section: str | None) -> None:
    primitive = _primitive(2)
    target = primitive if section is None else primitive[section]
    target[0] = "non-string-key"

    _assert_code(primitive, "capsule_shape_invalid")


@pytest.mark.parametrize("section", ["dimensions", "baselines", "streams"])
@pytest.mark.parametrize("change", ["missing", "extra", "reversed"])
def test_registry_aligned_arrays_reject_missing_extra_and_wrong_order(
    section: str, change: str
) -> None:
    primitive = _primitive(3)
    if section == "dimensions":
        items = primitive["dimensions"]
        code = "registry_alignment_invalid"
    elif section == "baselines":
        items = primitive["slow_state"]["baselines"]
        code = "registry_alignment_invalid"
    else:
        items = primitive["rng"]["streams"]
        code = "rng_stream_alignment_invalid"
    if change == "missing":
        items.pop()
    elif change == "extra":
        items.append(deepcopy(items[-1]))
    else:
        items.reverse()
    _assert_code(primitive, code)


def test_duplicate_registry_id_is_rejected() -> None:
    primitive = _primitive(2)
    duplicate = primitive["registry"][0]["dim_id"]
    primitive["registry"][1]["dim_id"] = duplicate
    primitive["dimensions"][1]["dim_id"] = duplicate
    primitive["slow_state"]["baselines"][1]["dim_id"] = duplicate
    primitive["rng"]["streams"][1]["stream"] = duplicate
    _assert_code(primitive, "registry_invalid")


@pytest.mark.parametrize(
    ("path", "replacement", "code"),
    [
        (("schema_version",), "future", "unsupported_schema_version"),
        (("dynamics_version",), "future", "unsupported_dynamics_version"),
        (("binary64_version",), "future", "unsupported_binary64_version"),
        (("slow_state", "version"), "future", "unsupported_slow_state_version"),
        (("rng", "provider"), "future", "unsupported_rng_provider"),
        (("rng", "provider_version"), "future", "unsupported_rng_provider_version"),
        (("rng", "algorithm"), "future", "unsupported_rng_algorithm"),
    ],
)
def test_unknown_versions_and_rng_identity_fail_closed(path, replacement: str, code: str) -> None:
    primitive = _primitive(1)
    _set_path(primitive, path, replacement)
    _assert_code(primitive, code)


FLOAT_PATHS = tuple(
    [("registry", 0, field) for field in REGISTRY_KEYS if field not in {"dim_id", "temporary_name", "trigger_count"}]
    + [("dimensions", 0, field) for field in DIMENSION_KEYS if field != "dim_id"]
    + [("slow_state", "baselines", 0, "current_baseline")]
)


@pytest.mark.parametrize("path", FLOAT_PATHS, ids=lambda path: ".".join(map(str, path)))
@pytest.mark.parametrize(
    ("bad_value", "code"),
    [
        (math.nan, "non_finite_capsule_value"),
        (math.inf, "non_finite_capsule_value"),
        (-math.inf, "non_finite_capsule_value"),
        (True, "capsule_numeric_type_invalid"),
        (1, "capsule_numeric_type_invalid"),
    ],
)
def test_all_registry_state_and_baseline_float_fields_are_strict_binary64(
    path, bad_value: object, code: str
) -> None:
    primitive = _primitive(1)
    _set_path(primitive, path, bad_value)
    _assert_code(primitive, code)


@pytest.mark.parametrize(
    ("path", "bad_value", "code"),
    [
        (("field_tick",), -1, "field_tick_invalid"),
        (("field_tick",), True, "capsule_numeric_type_invalid"),
        (("registry", 0, "trigger_count"), True, "capsule_numeric_type_invalid"),
        (("registry", 0, "trigger_count"), 1.0, "capsule_numeric_type_invalid"),
        (("rng", "streams", 0, "seed"), True, "capsule_numeric_type_invalid"),
        (("rng", "streams", 0, "seed"), 1.0, "capsule_numeric_type_invalid"),
        (("rng", "streams", 0, "next_cursor"), True, "capsule_numeric_type_invalid"),
    ],
)
def test_integer_fields_reject_bool_float_and_negative_tick(path, bad_value, code: str) -> None:
    primitive = _primitive(1)
    _set_path(primitive, path, bad_value)
    _assert_code(primitive, code)


@pytest.mark.parametrize("offset", [-1, 1])
def test_rng_cursor_must_equal_field_tick(offset: int) -> None:
    primitive = _primitive(2, ticks=4)
    primitive["rng"]["streams"][-1]["next_cursor"] += offset
    _assert_code(primitive, "rng_cursor_mismatch")


def test_rng_stream_order_seed_consistency_and_nonnegative_cursor_are_strict() -> None:
    wrong_stream = _primitive(2)
    wrong_stream["rng"]["streams"].reverse()
    _assert_code(wrong_stream, "rng_stream_alignment_invalid")

    inconsistent_seed = _primitive(2)
    inconsistent_seed["rng"]["streams"][-1]["seed"] += 1
    _assert_code(inconsistent_seed, "rng_seed_inconsistent")

    negative_cursor = _primitive(1)
    negative_cursor["field_tick"] = -1
    negative_cursor["rng"]["streams"][0]["next_cursor"] = -1
    _assert_code(negative_cursor, "field_tick_invalid")


def test_tick_zero_capture_does_not_consume_rng_draw() -> None:
    dynamics = _dynamics(3, seed=31337)
    before = dynamics.snapshot()

    capsule = _capture_field_state_capsule(dynamics)
    after = dynamics.snapshot()
    observation = dynamics.tick()

    assert capsule.field_tick == 0
    assert before == after
    assert all(stream.next_cursor == 0 for stream in capsule.rng.streams)
    assert all(item.rng_draw.draw_index == 0 for item in observation.dimensions)


class _CustomRng:
    def __init__(self, stream: str) -> None:
        self.stream = stream

    def draw(self, draw_index: int) -> RngDraw:
        return RngDraw(1, self.stream, draw_index, 0.0)


class _CustomRngFactory:
    def create(self, stream: str) -> _CustomRng:
        return _CustomRng(stream)


class _OverridingSeededGaussianRng(field_dynamics_module._SeededGaussianRng):
    def draw(self, draw_index: int) -> RngDraw:
        return replace(super().draw(draw_index), value=0.0)


class _OverridingSeededGaussianRngFactory:
    def __init__(self, seed: int) -> None:
        self.seed = seed

    def create(self, stream: str) -> _OverridingSeededGaussianRng:
        return _OverridingSeededGaussianRng(self.seed, stream)


def _install_capture_test_rngs(dynamics: FieldDynamics, factory) -> FieldDynamics:
    """Test-private assembly for capsule live-RNG rejection evidence."""
    dynamics._rngs = tuple(factory.create(item.dim_id) for item in dynamics.registry)
    return dynamics


def test_custom_live_rng_capture_is_rejected_with_stable_code() -> None:
    dynamics = _install_capture_test_rngs(
        FieldDynamics((_registration("custom"),)), _CustomRngFactory()
    )
    before = dynamics.snapshot()
    with pytest.raises(FieldStateCapsuleError) as caught:
        _capture_field_state_capsule(dynamics)
    assert caught.value.code == "unsupported_live_rng"
    assert dynamics.snapshot() == before


def test_overriding_seeded_rng_subclass_capture_is_rejected_with_stable_code() -> None:
    dynamics = _install_capture_test_rngs(
        FieldDynamics((_registration("overridden"),)),
        _OverridingSeededGaussianRngFactory(0xA11CE),
    )
    dynamics.tick()
    before = dynamics.snapshot()

    with pytest.raises(FieldStateCapsuleError) as caught:
        _capture_field_state_capsule(dynamics)

    assert caught.value.code == "unsupported_live_rng"
    assert dynamics.snapshot() == before


def test_standard_live_capture_candidate_preserves_next_tick_continuity() -> None:
    live_control = _dynamics(3, seed=0xA11CE)
    live_control.move_attractor(
        AttractorMove(live_control.registry[1].dim_id, 0.25, "p1.2-a-test", "continuity")
    )
    for _ in range(17):
        live_control.tick()

    capsule = _capture_field_state_capsule(live_control)
    candidate = _construct_field_candidate(
        decode_field_state_capsule(encode_field_state_capsule(capsule))
    )

    control_next = live_control.tick()
    candidate_next = candidate.tick()

    assert candidate_next == control_next
    assert candidate.snapshot() == live_control.snapshot()
    assert _capture_field_state_capsule(candidate) == _capture_field_state_capsule(live_control)
    assert all(item.rng_draw.draw_index == 17 for item in candidate_next.dimensions)


def test_candidate_is_new_and_mutations_do_not_affect_source() -> None:
    source = _dynamics(3)
    source.tick()
    source_capsule = _capture_field_state_capsule(source)
    candidate = _construct_field_candidate(source_capsule)

    assert candidate is not source
    assert candidate.snapshot() == source.snapshot()
    candidate.move_attractor(
        AttractorMove(candidate.registry[0].dim_id, 0.125, "p1.2-a-test", "candidate isolation")
    )
    candidate.tick()

    assert _capture_field_state_capsule(source) == source_capsule
    assert candidate.snapshot() != source.snapshot()


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        (("schema_version",), "bad", "unsupported_schema_version"),
        (("dimensions", -1, "value"), math.nan, "non_finite_capsule_value"),
        (("rng", "streams", -1, "next_cursor"), 2, "rng_cursor_mismatch"),
        (("dimensions", -1, "attractor"), 100.0, "attractor_displacement_out_of_domain"),
    ],
)
def test_all_decode_failures_leave_source_exactly_unchanged(path, value, code: str) -> None:
    source = _dynamics(3)
    source.tick()
    before = _capture_field_state_capsule(source)
    invalid = encode_field_state_capsule(before)
    _set_path(invalid, path, value)

    _assert_code(invalid, code)

    assert _capture_field_state_capsule(source) == before


def test_invalid_last_dimension_never_returns_partial_candidate() -> None:
    source = _dynamics(3)
    before = _capture_field_state_capsule(source)
    invalid = replace(
        before,
        dimensions=before.dimensions[:-1]
        + (replace(before.dimensions[-1], value=math.nan),),
    )

    with pytest.raises(FieldStateCapsuleError) as caught:
        _construct_field_candidate(invalid)

    assert caught.value.code == "non_finite_capsule_value"
    assert _capture_field_state_capsule(source) == before


def test_candidate_readback_mismatch_is_discarded(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _dynamics(2)
    source.tick()
    before = _capture_field_state_capsule(source)
    real_capture = capsule_module._capture_field_state_capsule

    def mismatching_capture(candidate: FieldDynamics):
        captured = real_capture(candidate)
        return replace(captured, field_tick=captured.field_tick + 1)

    monkeypatch.setattr(capsule_module, "_capture_field_state_capsule", mismatching_capture)
    with pytest.raises(FieldStateCapsuleError) as caught:
        _construct_field_candidate(before)

    assert caught.value.code == "candidate_readback_mismatch"
    assert real_capture(source) == before


def test_field_dynamics_public_surface_and_snapshot_boundary_remain_frozen() -> None:
    dynamics = _dynamics(1)
    public_members = {name for name in dir(dynamics) if not name.startswith("_")}
    assert public_members == {"registry", "snapshot", "move_attractor", "tick"}
    assert not any(
        token in name.lower()
        for name in public_members
        for token in ("set", "patch", "update", "merge", "restore", "recover", "capsule")
    )
    assert tuple(field.name for field in fields(FieldSnapshot)) == ("tick", "dimensions")
    snapshot_members = {name for name in dir(dynamics.snapshot()) if not name.startswith("_")}
    assert not snapshot_members.intersection(
        {"rng", "rng_state", "rng_seed", "next_cursor", "slow_state", "capsule"}
    )


def _import_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_production_files_remain_quarantine_and_unsafe_primitive_free() -> None:
    paths = (
        Path("app/chatbox/field_dynamics.py"),
        Path("app/chatbox/field_state_capsule.py"),
    )
    quarantine_tokens = ("agentlib", "agent_kernel", "semantic_trigger", "demos.scenarios")
    forbidden_imports = {"pickle", "marshal", "sqlite3", "websocket", "websockets"}
    forbidden_calls = {"eval", "exec", "__import__"}
    forbidden_feature_tokens = ("checkpoint", "live_swap", "scheduler", "websocket", "sqlite")

    for path in paths:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        assert not any(token in source for token in quarantine_tokens)
        assert not (_import_roots(tree) & forbidden_imports)
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in forbidden_calls
            for node in ast.walk(tree)
        )
        defined_names = {
            node.name.lower()
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        assert not any(token in name for token in forbidden_feature_tokens for name in defined_names)


def test_field_dynamics_import_roots_do_not_expand() -> None:
    source = Path("app/chatbox/field_dynamics.py").read_text(encoding="utf-8")
    assert _import_roots(ast.parse(source)) == {
        "__future__",
        "dataclasses",
        "hashlib",
        "math",
        "random",
        "typing",
    }


def test_frozen_version_constants_are_emitted_exactly() -> None:
    primitive = _primitive(1)
    assert primitive["schema_version"] == CAPSULE_SCHEMA_VERSION
    assert primitive["dynamics_version"] == DYNAMICS_VERSION
    assert primitive["binary64_version"] == BINARY64_VERSION
    assert primitive["slow_state"]["version"] == SLOW_STATE_VERSION
    assert primitive["rng"]["provider"] == RNG_PROVIDER
    assert primitive["rng"]["algorithm"] == RNG_ALGORITHM
