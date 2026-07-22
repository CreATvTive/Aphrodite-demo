"""P1.2-A versioned field-state capsule and strict canonical codec.

This module is a pure-data, versioned boundary for capturing and restoring
the field dynamics state owned by ``FieldDynamics``.  It never owns field
state itself; it only serializes, validates, and constructs isolated
recovery candidates.  The capsule layer is intentionally separate from the
runtime owner so that capture/restore can be reasoned about offline without
touching the live field.

Frozen version constants pin the exact schema, dynamics, binary64, slow
state, RNG provider and RNG algorithm this capsule layer will accept.  Any
mismatch is fail-closed: unknown versions, providers, algorithms, or
non-seeded live RNG are rejected, never silently coerced.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import platform
import sys

from app.chatbox.field_dynamics import (
    ATTRACTOR_DISPLACEMENT_RADIUS,
    DimensionRegistration,
    DynamicsContractError,
    FieldDynamics,
)


CAPSULE_SCHEMA_VERSION = "aphrodite.chatbox.field-state-capsule/1"
DYNAMICS_VERSION = "aphrodite.chatbox.field-dynamics/p1.1"
BINARY64_VERSION = "ieee754-binary64/1"
SLOW_STATE_VERSION = "aphrodite.chatbox.field-slow-state/1"
RNG_PROVIDER = "python.stdlib.random"
RNG_ALGORITHM = "sha256-prefix128be-mt19937-gauss/1"


def _current_rng_provider_version() -> str:
    """Return the current interpreter implementation and full version.

    The decode/restore path only accepts exact equality against the version
    captured at encode time, so cross-interpreter or cross-version restore
    is fail-closed rather than silently approximated.
    """
    implementation = platform.python_implementation()
    version = sys.version_info
    return f"{implementation}/{version.major}.{version.minor}.{version.micro}"


class FieldStateCapsuleError(ValueError):
    """Stable, structured capsule-boundary error.

    Inherits ``ValueError`` for compatibility with the dynamics contract
    error hierarchy.  The underlying ``DynamicsContractError`` may be kept as
    a cause, but the capsule boundary always maps failures to a stable
    ``code`` so callers do not depend on internal exception types.
    """

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        stage: str = "capsule",
        path: str | None = None,
    ) -> None:
        self.code = code
        self.stage = stage
        self.path = path
        self.detail = detail
        message = f"{code}: {detail}"
        if path is not None:
            message = f"{message} (at {path})"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CapsuleDimensionState:
    dim_id: str
    value: float
    velocity: float
    attractor: float
    ou_acceleration: float


@dataclass(frozen=True, slots=True)
class CapsuleBaselineState:
    dim_id: str
    current_baseline: float


@dataclass(frozen=True, slots=True)
class CapsuleSlowState:
    version: str
    baselines: tuple[CapsuleBaselineState, ...]


@dataclass(frozen=True, slots=True)
class CapsuleRngStream:
    stream: str
    seed: int
    next_cursor: int


@dataclass(frozen=True, slots=True)
class CapsuleRngState:
    provider: str
    provider_version: str
    algorithm: str
    streams: tuple[CapsuleRngStream, ...]


@dataclass(frozen=True, slots=True)
class FieldStateCapsule:
    schema_version: str
    dynamics_version: str
    binary64_version: str
    registry: tuple[DimensionRegistration, ...]
    field_tick: int
    dimensions: tuple[CapsuleDimensionState, ...]
    slow_state: CapsuleSlowState
    rng: CapsuleRngState


# ---------------------------------------------------------------------------
# Canonical primitive mapping
# ---------------------------------------------------------------------------

_REGISTRATION_FIELDS: tuple[str, ...] = (
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

_DIMENSION_FIELDS: tuple[str, ...] = (
    "dim_id",
    "value",
    "velocity",
    "attractor",
    "ou_acceleration",
)

_BASELINE_FIELDS: tuple[str, ...] = ("dim_id", "current_baseline")

_RNG_STREAM_FIELDS: tuple[str, ...] = ("stream", "seed", "next_cursor")

_ROOT_FIELDS: tuple[str, ...] = (
    "schema_version",
    "dynamics_version",
    "binary64_version",
    "field_tick",
    "registry",
    "dimensions",
    "slow_state",
    "rng",
)

_SLOW_STATE_FIELDS: tuple[str, ...] = ("version", "baselines")

_RNG_FIELDS: tuple[str, ...] = ("provider", "provider_version", "algorithm", "streams")


def _is_real_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_real_str(value: object) -> bool:
    return isinstance(value, str)


def _require_keys(
    mapping: object,
    expected: tuple[str, ...],
    *,
    path: str,
    stage: str,
) -> None:
    if not isinstance(mapping, dict):
        raise FieldStateCapsuleError(
            "capsule_shape_invalid",
            "expected a dict",
            stage=stage,
            path=path,
        )
    non_string_keys = [key for key in mapping.keys() if not _is_real_str(key)]
    if non_string_keys:
        raise FieldStateCapsuleError(
            "capsule_shape_invalid",
            f"non-string keys: {len(non_string_keys)} (first type: {type(non_string_keys[0]).__name__})",
            stage=stage,
            path=path,
        )
    expected_set = set(expected)
    actual_set = set(mapping.keys())
    missing = expected_set - actual_set
    extra = actual_set - expected_set
    if missing:
        raise FieldStateCapsuleError(
            "capsule_shape_invalid",
            f"missing keys: {sorted(missing)}",
            stage=stage,
            path=path,
        )
    if extra:
        raise FieldStateCapsuleError(
            "capsule_shape_invalid",
            f"unexpected keys: {sorted(extra)}",
            stage=stage,
            path=path,
        )


def _require_list(
    value: object,
    *,
    path: str,
    stage: str,
) -> None:
    if not isinstance(value, list):
        raise FieldStateCapsuleError(
            "capsule_shape_invalid",
            "expected a list",
            stage=stage,
            path=path,
        )


def _require_str(
    value: object,
    *,
    field: str,
    path: str,
    stage: str,
) -> None:
    if not _is_real_str(value):
        raise FieldStateCapsuleError(
            "capsule_numeric_type_invalid",
            f"{field} must be a str",
            stage=stage,
            path=path,
        )


def _require_int(
    value: object,
    *,
    field: str,
    path: str,
    stage: str,
) -> None:
    if not _is_real_int(value):
        raise FieldStateCapsuleError(
            "capsule_numeric_type_invalid",
            f"{field} must be an int",
            stage=stage,
            path=path,
        )


def _require_binary64_float(
    value: object,
    *,
    field: str,
    path: str,
    stage: str,
) -> None:
    if isinstance(value, bool):
        raise FieldStateCapsuleError(
            "capsule_numeric_type_invalid",
            f"{field} must not be a bool",
            stage=stage,
            path=path,
        )
    if isinstance(value, int):
        raise FieldStateCapsuleError(
            "capsule_numeric_type_invalid",
            f"{field} must be a float, not an int",
            stage=stage,
            path=path,
        )
    if not isinstance(value, float):
        raise FieldStateCapsuleError(
            "capsule_numeric_type_invalid",
            f"{field} must be a float",
            stage=stage,
            path=path,
        )
    if not math.isfinite(value):
        raise FieldStateCapsuleError(
            "non_finite_capsule_value",
            f"{field} must be finite",
            stage=stage,
            path=path,
        )


def _validate_registration_primitive(
    item: object,
    *,
    path: str,
    stage: str,
) -> None:
    _require_keys(item, _REGISTRATION_FIELDS, path=path, stage=stage)
    raw = item  # type: ignore[assignment]
    _require_str(raw["dim_id"], field="dim_id", path=path, stage=stage)
    _require_str(raw["temporary_name"], field="temporary_name", path=path, stage=stage)
    _require_int(raw["trigger_count"], field="trigger_count", path=path, stage=stage)
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
        _require_binary64_float(
            raw[field], field=field, path=path, stage=stage
        )


def _validate_dimension_primitive(
    item: object,
    *,
    path: str,
    stage: str,
) -> None:
    _require_keys(item, _DIMENSION_FIELDS, path=path, stage=stage)
    raw = item  # type: ignore[assignment]
    _require_str(raw["dim_id"], field="dim_id", path=path, stage=stage)
    for field in ("value", "velocity", "attractor", "ou_acceleration"):
        _require_binary64_float(
            raw[field], field=field, path=path, stage=stage
        )


def _validate_baseline_primitive(
    item: object,
    *,
    path: str,
    stage: str,
) -> None:
    _require_keys(item, _BASELINE_FIELDS, path=path, stage=stage)
    raw = item  # type: ignore[assignment]
    _require_str(raw["dim_id"], field="dim_id", path=path, stage=stage)
    _require_binary64_float(
        raw["current_baseline"], field="current_baseline", path=path, stage=stage
    )


def _validate_rng_stream_primitive(
    item: object,
    *,
    path: str,
    stage: str,
) -> None:
    _require_keys(item, _RNG_STREAM_FIELDS, path=path, stage=stage)
    raw = item  # type: ignore[assignment]
    _require_str(raw["stream"], field="stream", path=path, stage=stage)
    _require_int(raw["seed"], field="seed", path=path, stage=stage)
    _require_int(raw["next_cursor"], field="next_cursor", path=path, stage=stage)


def decode_field_state_capsule(primitive: dict) -> FieldStateCapsule:
    """Decode and fully validate a canonical primitive into a capsule.

    Validation order follows the frozen contract: shape and exact key sets,
    exact version match, strict type checks (rejecting bool and int-as-float),
    finiteness, ordered alignment of registry/dimensions/baselines/RNG
    streams, field_tick non-negative int with each next_cursor equal to it,
    common seed across streams, and attractor displacement within the closed
    ``[-1.801, +1.801]`` baseline-relative domain.  No value is coerced; any
    violation is fail-closed.
    """
    stage = "decode"
    _require_keys(primitive, _ROOT_FIELDS, path="$", stage=stage)

    schema_version = primitive["schema_version"]
    dynamics_version = primitive["dynamics_version"]
    binary64_version = primitive["binary64_version"]
    field_tick = primitive["field_tick"]
    registry_raw = primitive["registry"]
    dimensions_raw = primitive["dimensions"]
    slow_state_raw = primitive["slow_state"]
    rng_raw = primitive["rng"]

    _require_str(schema_version, field="schema_version", path="$.schema_version", stage=stage)
    _require_str(dynamics_version, field="dynamics_version", path="$.dynamics_version", stage=stage)
    _require_str(binary64_version, field="binary64_version", path="$.binary64_version", stage=stage)

    if schema_version != CAPSULE_SCHEMA_VERSION:
        raise FieldStateCapsuleError(
            "unsupported_schema_version",
            f"expected {CAPSULE_SCHEMA_VERSION!r}, got {schema_version!r}",
            stage=stage,
            path="$.schema_version",
        )
    if dynamics_version != DYNAMICS_VERSION:
        raise FieldStateCapsuleError(
            "unsupported_dynamics_version",
            f"expected {DYNAMICS_VERSION!r}, got {dynamics_version!r}",
            stage=stage,
            path="$.dynamics_version",
        )
    if binary64_version != BINARY64_VERSION:
        raise FieldStateCapsuleError(
            "unsupported_binary64_version",
            f"expected {BINARY64_VERSION!r}, got {binary64_version!r}",
            stage=stage,
            path="$.binary64_version",
        )

    _require_int(field_tick, field="field_tick", path="$.field_tick", stage=stage)
    if field_tick < 0:
        raise FieldStateCapsuleError(
            "field_tick_invalid",
            "field_tick must be non-negative",
            stage=stage,
            path="$.field_tick",
        )

    _require_list(registry_raw, path="$.registry", stage=stage)
    _require_list(dimensions_raw, path="$.dimensions", stage=stage)
    if len(registry_raw) == 0:
        raise FieldStateCapsuleError(
            "registry_invalid",
            "registry must contain at least one dimension",
            stage=stage,
            path="$.registry",
        )
    if len(dimensions_raw) != len(registry_raw):
        raise FieldStateCapsuleError(
            "registry_alignment_invalid",
            "dimensions length must equal registry length",
            stage=stage,
            path="$.dimensions",
        )

    registry_path = "$.registry[%d]"
    registry_items: list[DimensionRegistration] = []
    registry_dim_ids: list[str] = []
    for index, item in enumerate(registry_raw):
        path = registry_path % index
        _validate_registration_primitive(item, path=path, stage=stage)
        raw = item  # type: ignore[assignment]
        try:
            registration = DimensionRegistration(
                dim_id=raw["dim_id"],
                temporary_name=raw["temporary_name"],
                birth_time=raw["birth_time"],
                strength=raw["strength"],
                trigger_count=raw["trigger_count"],
                birth_bias=raw["birth_bias"],
                fast_e_fold_s=raw["fast_e_fold_s"],
                ou_correlation_e_fold_s=raw["ou_correlation_e_fold_s"],
                ou_acceleration_sigma=raw["ou_acceleration_sigma"],
                soft_boundary_start=raw["soft_boundary_start"],
                soft_boundary_width=raw["soft_boundary_width"],
                soft_boundary_strength=raw["soft_boundary_strength"],
            )
        except DynamicsContractError as exc:
            raise FieldStateCapsuleError(
                "registry_invalid",
                f"registration rejected: {exc.anomaly.code}",
                stage=stage,
                path=path,
            ) from exc
        registry_items.append(registration)
        registry_dim_ids.append(registration.dim_id)

    if len(set(registry_dim_ids)) != len(registry_dim_ids):
        raise FieldStateCapsuleError(
            "registry_invalid",
            "registry dim_id values must be unique",
            stage=stage,
            path="$.registry",
        )

    dimensions_path = "$.dimensions[%d]"
    dimension_items: list[CapsuleDimensionState] = []
    for index, item in enumerate(dimensions_raw):
        path = dimensions_path % index
        _validate_dimension_primitive(item, path=path, stage=stage)
        raw = item  # type: ignore[assignment]
        dimension_items.append(
            CapsuleDimensionState(
                dim_id=raw["dim_id"],
                value=raw["value"],
                velocity=raw["velocity"],
                attractor=raw["attractor"],
                ou_acceleration=raw["ou_acceleration"],
            )
        )

    dimension_dim_ids = [dim.dim_id for dim in dimension_items]
    if dimension_dim_ids != registry_dim_ids:
        raise FieldStateCapsuleError(
            "registry_alignment_invalid",
            "dimensions dim_id order must match registry order",
            stage=stage,
            path="$.dimensions",
        )

    _require_keys(slow_state_raw, _SLOW_STATE_FIELDS, path="$.slow_state", stage=stage)
    slow_version = slow_state_raw["version"]  # type: ignore[index]
    baselines_raw = slow_state_raw["baselines"]  # type: ignore[index]
    _require_str(slow_version, field="version", path="$.slow_state.version", stage=stage)
    if slow_version != SLOW_STATE_VERSION:
        raise FieldStateCapsuleError(
            "unsupported_slow_state_version",
            f"expected {SLOW_STATE_VERSION!r}, got {slow_version!r}",
            stage=stage,
            path="$.slow_state.version",
        )
    _require_list(baselines_raw, path="$.slow_state.baselines", stage=stage)
    if len(baselines_raw) != len(registry_raw):
        raise FieldStateCapsuleError(
            "registry_alignment_invalid",
            "baselines length must equal registry length",
            stage=stage,
            path="$.slow_state.baselines",
        )
    baselines_path = "$.slow_state.baselines[%d]"
    baseline_items: list[CapsuleBaselineState] = []
    for index, item in enumerate(baselines_raw):
        path = baselines_path % index
        _validate_baseline_primitive(item, path=path, stage=stage)
        raw = item  # type: ignore[assignment]
        baseline_items.append(
            CapsuleBaselineState(
                dim_id=raw["dim_id"],
                current_baseline=raw["current_baseline"],
            )
        )
    baseline_dim_ids = [baseline.dim_id for baseline in baseline_items]
    if baseline_dim_ids != registry_dim_ids:
        raise FieldStateCapsuleError(
            "registry_alignment_invalid",
            "baselines dim_id order must match registry order",
            stage=stage,
            path="$.slow_state.baselines",
        )

    _require_keys(rng_raw, _RNG_FIELDS, path="$.rng", stage=stage)
    provider = rng_raw["provider"]  # type: ignore[index]
    provider_version = rng_raw["provider_version"]  # type: ignore[index]
    algorithm = rng_raw["algorithm"]  # type: ignore[index]
    streams_raw = rng_raw["streams"]  # type: ignore[index]
    _require_str(provider, field="provider", path="$.rng.provider", stage=stage)
    _require_str(
        provider_version,
        field="provider_version",
        path="$.rng.provider_version",
        stage=stage,
    )
    _require_str(algorithm, field="algorithm", path="$.rng.algorithm", stage=stage)
    if provider != RNG_PROVIDER:
        raise FieldStateCapsuleError(
            "unsupported_rng_provider",
            f"expected {RNG_PROVIDER!r}, got {provider!r}",
            stage=stage,
            path="$.rng.provider",
        )
    if provider_version != _current_rng_provider_version():
        raise FieldStateCapsuleError(
            "unsupported_rng_provider_version",
            f"expected {_current_rng_provider_version()!r}, got {provider_version!r}",
            stage=stage,
            path="$.rng.provider_version",
        )
    if algorithm != RNG_ALGORITHM:
        raise FieldStateCapsuleError(
            "unsupported_rng_algorithm",
            f"expected {RNG_ALGORITHM!r}, got {algorithm!r}",
            stage=stage,
            path="$.rng.algorithm",
        )
    _require_list(streams_raw, path="$.rng.streams", stage=stage)
    if len(streams_raw) != len(registry_raw):
        raise FieldStateCapsuleError(
            "rng_stream_alignment_invalid",
            "rng streams length must equal registry length",
            stage=stage,
            path="$.rng.streams",
        )
    streams_path = "$.rng.streams[%d]"
    stream_items: list[CapsuleRngStream] = []
    stream_ids: list[str] = []
    seeds: list[int] = []
    cursors: list[int] = []
    for index, item in enumerate(streams_raw):
        path = streams_path % index
        _validate_rng_stream_primitive(item, path=path, stage=stage)
        raw = item  # type: ignore[assignment]
        stream_items.append(
            CapsuleRngStream(
                stream=raw["stream"],
                seed=raw["seed"],
                next_cursor=raw["next_cursor"],
            )
        )
        stream_ids.append(raw["stream"])
        seeds.append(raw["seed"])
        cursors.append(raw["next_cursor"])
    if stream_ids != registry_dim_ids:
        raise FieldStateCapsuleError(
            "rng_stream_alignment_invalid",
            "rng stream id order must match registry dim_id order",
            stage=stage,
            path="$.rng.streams",
        )
    if len(set(seeds)) != 1:
        raise FieldStateCapsuleError(
            "rng_seed_inconsistent",
            "all rng streams must share the same seed",
            stage=stage,
            path="$.rng.streams",
        )
    for cursor in cursors:
        if cursor != field_tick:
            raise FieldStateCapsuleError(
                "rng_cursor_mismatch",
                "each next_cursor must equal field_tick",
                stage=stage,
                path="$.rng.streams",
            )

    for index, (dim, baseline) in enumerate(zip(dimension_items, baseline_items)):
        displacement = dim.attractor - baseline.current_baseline
        lower_bound = baseline.current_baseline - ATTRACTOR_DISPLACEMENT_RADIUS
        upper_bound = baseline.current_baseline + ATTRACTOR_DISPLACEMENT_RADIUS
        if dim.attractor < lower_bound or dim.attractor > upper_bound:
            raise FieldStateCapsuleError(
                "attractor_displacement_out_of_domain",
                f"attractor displacement {displacement} outside closed "
                f"[-{ATTRACTOR_DISPLACEMENT_RADIUS}, +{ATTRACTOR_DISPLACEMENT_RADIUS}]",
                stage=stage,
                path=dimensions_path % index,
            )

    return FieldStateCapsule(
        schema_version=schema_version,
        dynamics_version=dynamics_version,
        binary64_version=binary64_version,
        registry=tuple(registry_items),
        field_tick=field_tick,
        dimensions=tuple(dimension_items),
        slow_state=CapsuleSlowState(
            version=slow_version,
            baselines=tuple(baseline_items),
        ),
        rng=CapsuleRngState(
            provider=provider,
            provider_version=provider_version,
            algorithm=algorithm,
            streams=tuple(stream_items),
        ),
    )


def _registration_to_primitive(
    registration: DimensionRegistration,
) -> dict:
    return {
        "dim_id": registration.dim_id,
        "temporary_name": registration.temporary_name,
        "birth_time": registration.birth_time,
        "strength": registration.strength,
        "trigger_count": registration.trigger_count,
        "birth_bias": registration.birth_bias,
        "fast_e_fold_s": registration.fast_e_fold_s,
        "ou_correlation_e_fold_s": registration.ou_correlation_e_fold_s,
        "ou_acceleration_sigma": registration.ou_acceleration_sigma,
        "soft_boundary_start": registration.soft_boundary_start,
        "soft_boundary_width": registration.soft_boundary_width,
        "soft_boundary_strength": registration.soft_boundary_strength,
    }


def _capsule_to_primitive(capsule: FieldStateCapsule) -> dict:
    """Map a capsule to a fresh canonical primitive dict.

    Only newly-built plain dict/list/string/int/float are produced, with
    fixed key insertion order and fixed registry array order, and no object
    aliasing of the capsule's internal tuples.
    """
    registry_items: list[dict] = []
    for registration in capsule.registry:
        registry_items.append(_registration_to_primitive(registration))
    dimension_items: list[dict] = []
    for dim in capsule.dimensions:
        dimension_items.append(
            {
                "dim_id": dim.dim_id,
                "value": dim.value,
                "velocity": dim.velocity,
                "attractor": dim.attractor,
                "ou_acceleration": dim.ou_acceleration,
            }
        )
    baseline_items: list[dict] = []
    for baseline in capsule.slow_state.baselines:
        baseline_items.append(
            {
                "dim_id": baseline.dim_id,
                "current_baseline": baseline.current_baseline,
            }
        )
    stream_items: list[dict] = []
    for stream in capsule.rng.streams:
        stream_items.append(
            {
                "stream": stream.stream,
                "seed": stream.seed,
                "next_cursor": stream.next_cursor,
            }
        )
    return {
        "schema_version": capsule.schema_version,
        "dynamics_version": capsule.dynamics_version,
        "binary64_version": capsule.binary64_version,
        "field_tick": capsule.field_tick,
        "registry": registry_items,
        "dimensions": dimension_items,
        "slow_state": {
            "version": capsule.slow_state.version,
            "baselines": baseline_items,
        },
        "rng": {
            "provider": capsule.rng.provider,
            "provider_version": capsule.rng.provider_version,
            "algorithm": capsule.rng.algorithm,
            "streams": stream_items,
        },
    }


def encode_field_state_capsule(capsule: FieldStateCapsule) -> dict:
    """Encode a capsule into a fresh canonical primitive dict.

    The input capsule is never mutated; a brand-new primitive tree is built.
    A directly-constructed capsule that violates the frozen decoder invariants
    is rejected fail-closed at the encode boundary: the capsule is projected to
    its canonical primitive, then validated by round-tripping through the strict
    decoder, and the decoded capsule must equal the input.  This shares the
    exact same invariant semantics as ``decode_field_state_capsule`` so the two
    boundaries cannot drift.  Projection failures from non-canonical internal
    structure (e.g. lists instead of tuples, wrong nested object types) are
    mapped to a stable capsule shape error rather than leaking bare exceptions.
    """
    try:
        is_capsule = isinstance(capsule, FieldStateCapsule)
    except Exception as exc:
        raise FieldStateCapsuleError(
            "capsule_shape_invalid",
            "encode input must be a FieldStateCapsule",
            stage="encode",
            path="$",
        ) from exc
    if not is_capsule:
        raise FieldStateCapsuleError(
            "capsule_shape_invalid",
            "encode input must be a FieldStateCapsule",
            stage="encode",
            path="$",
        )
    try:
        primitive = _capsule_to_primitive(capsule)
    except Exception as exc:
        raise FieldStateCapsuleError(
            "capsule_shape_invalid",
            "capsule internal structure is not canonical",
            stage="encode",
            path="$",
        ) from exc
    try:
        decoded = decode_field_state_capsule(primitive)
        if decoded != capsule:
            raise FieldStateCapsuleError(
                "capsule_shape_invalid",
                "capsule does not round-trip through the canonical codec",
                stage="encode",
                path="$",
            )
    except FieldStateCapsuleError:
        raise
    except Exception as exc:
        raise FieldStateCapsuleError(
            "capsule_shape_invalid",
            "capsule failed strict decode or equality comparison",
            stage="encode",
            path="$",
        ) from exc
    return primitive


def _capture_field_state_capsule(dynamics: FieldDynamics) -> FieldStateCapsule:
    """Lifecycle-private capture from a live FieldDynamics owner.

    Reads field-owned recovery state via the field-owned export hook, then
    wraps it with the frozen version constants to form a capsule.  The live
    RNG object is never serialized; only its deterministic seed, stream id
    and draw cursor are captured.  Non-seeded live RNG providers are rejected
    fail-closed by the field-owned hook.
    """
    if not isinstance(dynamics, FieldDynamics):
        raise FieldStateCapsuleError(
            "capsule_shape_invalid",
            "capture source must be a FieldDynamics",
            stage="capture",
            path="$",
        )
    try:
        exported = dynamics._export_field_recovery_state()
    except DynamicsContractError as exc:
        raise FieldStateCapsuleError(
            "unsupported_live_rng",
            f"live RNG capture rejected: {exc.anomaly.code}",
            stage="capture",
            path="$",
        ) from exc
    registry = exported["registry"]
    dimensions = exported["dimensions"]
    slow_state = exported["slow_state"]
    rng = exported["rng"]
    field_tick = exported["field_tick"]

    registry_items: list[DimensionRegistration] = []
    for registration in registry:
        if not isinstance(registration, DimensionRegistration):
            raise FieldStateCapsuleError(
                "registry_invalid",
                "registry entry must be a DimensionRegistration",
                stage="capture",
                path="$.registry",
            )
        registry_items.append(registration)

    dimension_items: list[CapsuleDimensionState] = []
    for dim in dimensions:
        dimension_items.append(
            CapsuleDimensionState(
                dim_id=dim["dim_id"],
                value=dim["value"],
                velocity=dim["velocity"],
                attractor=dim["attractor"],
                ou_acceleration=dim["ou_acceleration"],
            )
        )

    baseline_items: list[CapsuleBaselineState] = []
    for baseline in slow_state["baselines"]:
        baseline_items.append(
            CapsuleBaselineState(
                dim_id=baseline["dim_id"],
                current_baseline=baseline["current_baseline"],
            )
        )

    stream_items: list[CapsuleRngStream] = []
    for stream in rng["streams"]:
        stream_items.append(
            CapsuleRngStream(
                stream=stream["stream"],
                seed=stream["seed"],
                next_cursor=stream["next_cursor"],
            )
        )

    return FieldStateCapsule(
        schema_version=CAPSULE_SCHEMA_VERSION,
        dynamics_version=DYNAMICS_VERSION,
        binary64_version=BINARY64_VERSION,
        registry=tuple(registry_items),
        field_tick=field_tick,
        dimensions=tuple(dimension_items),
        slow_state=CapsuleSlowState(
            version=SLOW_STATE_VERSION,
            baselines=tuple(baseline_items),
        ),
        rng=CapsuleRngState(
            provider=RNG_PROVIDER,
            provider_version=_current_rng_provider_version(),
            algorithm=RNG_ALGORITHM,
            streams=tuple(stream_items),
        ),
    )


def _construct_field_candidate(capsule: FieldStateCapsule) -> FieldDynamics:
    """Lifecycle-private offline candidate construction.

    Validates the capsule by round-tripping through the canonical codec,
    then builds a brand-new, unpublished FieldDynamics via the field-owned
    construction hook.  After construction the candidate is privately
    captured and read-back; the read-back capsule and its canonical mapping
    must be exactly equal to the input, otherwise the candidate is discarded
    and ``candidate_readback_mismatch`` is raised.  The candidate is returned
    isolated and is never installed into any live runtime.
    """
    if not isinstance(capsule, FieldStateCapsule):
        raise FieldStateCapsuleError(
            "capsule_shape_invalid",
            "construct input must be a FieldStateCapsule",
            stage="construct",
            path="$",
        )
    primitive = _capsule_to_primitive(capsule)
    decoded = decode_field_state_capsule(primitive)
    if decoded != capsule:
        raise FieldStateCapsuleError(
            "candidate_readback_mismatch",
            "decoded capsule does not equal input capsule",
            stage="construct",
            path="$",
        )

    construction_primitive = {
        "field_tick": decoded.field_tick,
        "registry": list(decoded.registry),
        "dimensions": [
            {
                "dim_id": dim.dim_id,
                "value": dim.value,
                "velocity": dim.velocity,
                "attractor": dim.attractor,
                "ou_acceleration": dim.ou_acceleration,
            }
            for dim in decoded.dimensions
        ],
        "slow_state": {
            "baselines": [
                {
                    "dim_id": baseline.dim_id,
                    "current_baseline": baseline.current_baseline,
                }
                for baseline in decoded.slow_state.baselines
            ]
        },
        "rng": {
            "streams": [
                {
                    "stream": stream.stream,
                    "seed": stream.seed,
                    "next_cursor": stream.next_cursor,
                }
                for stream in decoded.rng.streams
            ]
        },
    }
    try:
        candidate = FieldDynamics._build_field_recovery_candidate(
            construction_primitive
        )
    except DynamicsContractError as exc:
        raise FieldStateCapsuleError(
            "candidate_construction_failed",
            f"field-owned construction rejected: {exc.anomaly.code}",
            stage="construct",
            path="$",
        ) from exc
    except Exception as exc:
        raise FieldStateCapsuleError(
            "candidate_construction_failed",
            f"field-owned construction failed: {exc}",
            stage="construct",
            path="$",
        ) from exc

    readback = _capture_field_state_capsule(candidate)
    if readback != capsule:
        raise FieldStateCapsuleError(
            "candidate_readback_mismatch",
            "read-back capsule does not equal input capsule",
            stage="construct",
            path="$",
        )
    readback_primitive = _capsule_to_primitive(readback)
    if readback_primitive != primitive:
        raise FieldStateCapsuleError(
            "candidate_readback_mismatch",
            "read-back canonical mapping does not equal input mapping",
            stage="construct",
            path="$",
        )
    return candidate
