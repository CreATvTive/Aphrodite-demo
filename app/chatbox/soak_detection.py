"""P4.11 pure, streaming analysis over committed field values.

This module owns no files, database connections, clocks, runtime references, or
mutation callbacks.  It consumes immutable registry/frame-shaped inputs and
produces JSON primitives.  ``TrajectoryPoint.value`` is the only statistical
observation; the remaining point fields are retained solely as evidence-layer
audit context.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Mapping, Sequence, cast


SOAK_CONTRACT_VERSION = "p4-task11-soak-detection/2026-07-21"
REPORT_SCHEMA_VERSION = "aphrodite.chatbox.soak-report/1"
BLOCK_FRAMES = 60
WINDOW_BLOCKS = 720
WINDOW_STRIDE_BLOCKS = 360
VARIANCE_THRESHOLD = 1.0e-8
MIN_CANDIDATE_LAG = 15
MAX_CANDIDATE_LAG = 180
MAX_AUTOCORRELATION_LAG = 360
HEIGHT_THRESHOLD = 0.60
PROMINENCE_THRESHOLD = 0.30
HARMONIC_THRESHOLD = 0.40
FORMAL_INTERVALS = 172_800
FORMAL_FRAMES = FORMAL_INTERVALS + 1
FORMAL_ELAPSED_NS = 48 * 60 * 60 * 1_000_000_000
MAX_CADENCE_ANOMALIES = 1440

THRESHOLDS = {
    "block_frames": BLOCK_FRAMES,
    "window_blocks": WINDOW_BLOCKS,
    "window_stride_blocks": WINDOW_STRIDE_BLOCKS,
    "variance_threshold": VARIANCE_THRESHOLD,
    "candidate_lag_min_minutes": MIN_CANDIDATE_LAG,
    "candidate_lag_max_minutes": MAX_CANDIDATE_LAG,
    "autocorrelation_max_lag_minutes": MAX_AUTOCORRELATION_LAG,
    "height_threshold": HEIGHT_THRESHOLD,
    "prominence_threshold": PROMINENCE_THRESHOLD,
    "harmonic_threshold": HARMONIC_THRESHOLD,
    "formal_frames": FORMAL_FRAMES,
    "formal_intervals": FORMAL_INTERVALS,
    "formal_elapsed_ns": FORMAL_ELAPSED_NS,
    "max_cadence_anomalies": MAX_CADENCE_ANOMALIES,
}


class SoakState(str, Enum):
    RUNNING = "RUNNING"
    PASS = "PASS"
    FAIL = "FAIL"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    EVIDENCE_CORRUPT = "EVIDENCE_CORRUPT"


@dataclass(frozen=True, slots=True)
class SoakProfile:
    name: str
    formal_48h: bool

    def __post_init__(self) -> None:
        if self.name not in {"formal", "test"}:
            raise ValueError("profile name must be 'formal' or 'test'")
        if self.formal_48h != (self.name == "formal"):
            raise ValueError("formal_48h must agree with profile name")

    def primitive(self) -> dict[str, object]:
        return {"name": self.name, "formal_48h": self.formal_48h}


FORMAL_PROFILE = SoakProfile("formal", True)
TEST_PROFILE = SoakProfile("test", False)


class EvidenceCorruptError(ValueError):
    """A stable corruption marker used by the pure core and evidence adapter."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ) + "\n").encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _primitive_object(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if is_dataclass(value):
        return asdict(cast(object, value))  # type: ignore[arg-type]
    result: dict[str, object] = {}
    for item in fields(RegistryEntry):
        if hasattr(value, item.name):
            result[item.name] = getattr(value, item.name)
    return result


@dataclass(frozen=True, slots=True)
class RegistryEntry:
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

    @classmethod
    def from_input(cls, value: object) -> "RegistryEntry":
        raw = _primitive_object(value)
        expected = {item.name for item in fields(cls)}
        if set(raw) != expected:
            raise EvidenceCorruptError("registry_schema_mismatch", "registration fields mismatch")
        try:
            entry = cls(**raw)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise EvidenceCorruptError("registry_schema_mismatch", "registration is malformed") from exc
        if not isinstance(entry.dim_id, str) or not entry.dim_id:
            raise EvidenceCorruptError("registry_invalid_dim_id", "dim_id must be non-empty")
        if not isinstance(entry.temporary_name, str) or not entry.temporary_name:
            raise EvidenceCorruptError("registry_invalid_name", "temporary_name must be non-empty")
        if not isinstance(entry.trigger_count, int) or isinstance(entry.trigger_count, bool):
            raise EvidenceCorruptError("registry_invalid_trigger_count", "trigger_count must be an integer")
        for item in fields(cls):
            if item.name in {"dim_id", "temporary_name", "trigger_count"}:
                continue
            scalar = getattr(entry, item.name)
            if not isinstance(scalar, (int, float)) or isinstance(scalar, bool) or not math.isfinite(float(scalar)):
                raise EvidenceCorruptError("registry_non_finite", f"{item.name} must be finite")
        return entry

    @property
    def available(self) -> bool:
        return math.isfinite(float(self.ou_acceleration_sigma)) and self.ou_acceleration_sigma > 0.0

    def primitive(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FrozenRegistry:
    registrations: tuple[RegistryEntry, ...]

    @classmethod
    def from_input(cls, value: object) -> "FrozenRegistry":
        registrations = getattr(value, "registrations", value)
        try:
            entries = tuple(RegistryEntry.from_input(item) for item in registrations)  # type: ignore[union-attr]
        except TypeError as exc:
            raise EvidenceCorruptError("registry_schema_mismatch", "registry is not iterable") from exc
        ids = tuple(item.dim_id for item in entries)
        if len(set(ids)) != len(ids):
            raise EvidenceCorruptError("registry_duplicate_dim_id", "registry dim_id values must be unique")
        return cls(entries)

    @property
    def dim_ids(self) -> tuple[str, ...]:
        return tuple(item.dim_id for item in self.registrations)

    @property
    def unavailable_dim_ids(self) -> tuple[str, ...]:
        return tuple(item.dim_id for item in self.registrations if not item.available)

    def primitive(self) -> list[dict[str, object]]:
        return [item.primitive() for item in self.registrations]

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.primitive())


@dataclass(frozen=True, slots=True)
class ValueFrame:
    cursor: int
    boot_id: str
    field_tick: int
    utc_unix_ns: int
    values: tuple[tuple[str, float], ...]
    order: tuple[str, ...]
    frame_hash: str

    @classmethod
    def from_input(cls, frame: object, registry: FrozenRegistry) -> "ValueFrame":
        try:
            cursor = getattr(frame, "cursor")
            boot_id = getattr(frame, "boot_id")
            field_tick = getattr(frame, "field_tick")
            utc_unix_ns = getattr(frame, "utc_unix_ns")
            points = tuple(getattr(frame, "dimensions"))
        except (AttributeError, TypeError) as exc:
            raise EvidenceCorruptError("partial_frame", "frame fields are incomplete") from exc
        for name, value in (("cursor", cursor), ("field_tick", field_tick), ("utc_unix_ns", utc_unix_ns)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise EvidenceCorruptError("frame_schema_mismatch", f"{name} must be a non-negative integer")
        if not isinstance(boot_id, str) or not boot_id:
            raise EvidenceCorruptError("frame_schema_mismatch", "boot_id must be non-empty")
        if len(points) != len(registry.registrations):
            raise EvidenceCorruptError("partial_frame", "frame dimension count differs from registry")
        by_id: dict[str, float] = {}
        ordinals: set[int] = set()
        order: list[str] = []
        audit_points: list[dict[str, object]] = []
        for point in points:
            try:
                ordinal = getattr(point, "ordinal")
                dim_id = getattr(point, "dim_id")
                value = getattr(point, "value")
                audit = {
                    "ordinal": ordinal,
                    "dim_id": dim_id,
                    "value": value,
                    "velocity": getattr(point, "velocity"),
                    "attractor": getattr(point, "attractor"),
                    "slow_baseline": getattr(point, "slow_baseline"),
                    "ou_acceleration": getattr(point, "ou_acceleration"),
                }
            except AttributeError as exc:
                raise EvidenceCorruptError("partial_frame", "point fields are incomplete") from exc
            if not isinstance(ordinal, int) or isinstance(ordinal, bool):
                raise EvidenceCorruptError("frame_alignment_failure", "ordinal must be an integer")
            if ordinal in ordinals or ordinal < 0 or ordinal >= len(points):
                raise EvidenceCorruptError("frame_alignment_failure", "ordinals must be unique 0..D-1")
            ordinals.add(ordinal)
            if not isinstance(dim_id, str) or dim_id in by_id:
                raise EvidenceCorruptError("frame_alignment_failure", "dim_id must be unique")
            for scalar_name in ("value", "velocity", "attractor", "slow_baseline", "ou_acceleration"):
                scalar = audit[scalar_name]
                if not isinstance(scalar, (int, float)) or isinstance(scalar, bool) or not math.isfinite(float(scalar)):
                    raise EvidenceCorruptError("frame_non_finite", f"{scalar_name} is non-finite")
            by_id[dim_id] = float(value)
            order.append(dim_id)
            audit_points.append({key: (float(item) if key not in {"ordinal", "dim_id"} else item)
                                 for key, item in audit.items()})
        if set(by_id) != set(registry.dim_ids):
            raise EvidenceCorruptError("frame_alignment_failure", "frame dim_id set differs from registry")
        audit_points.sort(key=lambda item: cast(int, item["ordinal"]))
        primitive = {
            "cursor": cursor,
            "boot_id": boot_id,
            "field_tick": field_tick,
            "utc_unix_ns": utc_unix_ns,
            "dimensions": audit_points,
        }
        return cls(
            cursor=cursor,
            boot_id=boot_id,
            field_tick=field_tick,
            utc_unix_ns=utc_unix_ns,
            values=tuple((dim_id, by_id[dim_id]) for dim_id in registry.dim_ids),
            order=tuple(order),
            frame_hash=canonical_sha256(primitive),
        )

    def value_map(self) -> dict[str, float]:
        return dict(self.values)


def sample_variance(values: Sequence[float]) -> tuple[float, bool]:
    if len(values) != WINDOW_BLOCKS:
        raise ValueError(f"variance requires exactly {WINDOW_BLOCKS} values")
    mean = math.fsum(values) / WINDOW_BLOCKS
    variance = math.fsum((value - mean) ** 2 for value in values) / (WINDOW_BLOCKS - 1)
    return variance, all(value == values[0] for value in values[1:])


def direct_autocorrelation(values: Sequence[float], max_lag: int = MAX_AUTOCORRELATION_LAG) -> tuple[float, ...]:
    if len(values) != WINDOW_BLOCKS:
        raise ValueError(f"autocorrelation requires exactly {WINDOW_BLOCKS} values")
    if not isinstance(max_lag, int) or not 0 <= max_lag < len(values):
        raise ValueError("invalid max_lag")
    mean = math.fsum(values) / len(values)
    demeaned = tuple(value - mean for value in values)
    denominator = math.fsum(value * value for value in demeaned)
    if not math.isfinite(denominator) or denominator <= 0.0:
        raise ValueError("autocorrelation denominator unavailable")
    result = [1.0]
    for lag in range(1, max_lag + 1):
        numerator = math.fsum(demeaned[index] * demeaned[index - lag] for index in range(lag, len(demeaned)))
        result.append(numerator / denominator)
    return tuple(result)


def qualifying_periodic_candidates(autocorrelation: Sequence[float]) -> list[dict[str, object]]:
    if len(autocorrelation) <= MAX_AUTOCORRELATION_LAG:
        raise ValueError("autocorrelation must include lags 0..360")
    candidates: list[dict[str, object]] = []
    for lag in range(MIN_CANDIDATE_LAG, MAX_CANDIDATE_LAG + 1):
        if lag == MIN_CANDIDATE_LAG:
            local_maximum = autocorrelation[lag] > autocorrelation[lag + 1]
        else:
            local_maximum = autocorrelation[lag] >= autocorrelation[lag - 1] and autocorrelation[lag] > autocorrelation[lag + 1]
        half = lag // 2
        trough_start = max(1, half - 5)
        trough_end = min(MAX_AUTOCORRELATION_LAG, half + 5)
        trough = min(autocorrelation[trough_start:trough_end + 1])
        prominence = autocorrelation[lag] - trough
        harmonics = [
            {"multiple": multiple, "lag": multiple * lag, "value": autocorrelation[multiple * lag]}
            for multiple in (2, 3) if multiple * lag <= MAX_AUTOCORRELATION_LAG
        ]
        supported = any(float(item["value"]) >= HARMONIC_THRESHOLD for item in harmonics)
        height_ok = autocorrelation[lag] >= HEIGHT_THRESHOLD
        prominence_ok = prominence >= PROMINENCE_THRESHOLD
        if local_maximum and height_ok and prominence_ok and supported:
            candidates.append({
                "lag_minutes": lag,
                "height": autocorrelation[lag],
                "local_maximum": local_maximum,
                "trough_range": [trough_start, trough_end],
                "trough": trough,
                "prominence": prominence,
                "height_ok": height_ok,
                "prominence_ok": prominence_ok,
                "harmonics": harmonics,
                "harmonic_support_ok": supported,
                "four_periods_fit": True,
            })
    return candidates


def _periodic_match(left: int, right: int) -> tuple[bool, float]:
    tolerance = max(2.0, 0.05 * min(left, right))
    return abs(left - right) <= tolerance, tolerance


@dataclass(slots=True)
class _Attempt:
    attempt_id: int
    segment_id: int
    registry_order: tuple[str, ...]
    start_cursor: int
    start_tick: int
    start_utc_ns: int
    end_cursor: int
    end_tick: int
    end_utc_ns: int
    boot_id: str
    frame_count: int = 1
    interval_count: int = 0
    duplicate_count: int = 0
    anomalies: list[dict[str, object]] = field(default_factory=list)
    partial_count: int = 0
    partial_sums: dict[str, float] = field(default_factory=dict)
    blocks: dict[str, list[float]] = field(default_factory=dict)
    windows: list[dict[str, object]] = field(default_factory=list)
    warnings: list[dict[str, object]] = field(default_factory=list)
    confirmations: list[dict[str, object]] = field(default_factory=list)
    previous_hits: dict[str, dict[str, object]] = field(default_factory=dict)
    state: str = SoakState.RUNNING.value
    end_reason: str | None = None

    def primitive(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id,
            "segment_id": self.segment_id,
            "state": self.state,
            "end_reason": self.end_reason,
            "boot_id": self.boot_id,
            "registry_order": list(self.registry_order),
            "cursor_bounds": [self.start_cursor, self.end_cursor],
            "tick_bounds": [self.start_tick, self.end_tick],
            "utc_unix_ns_bounds": [self.start_utc_ns, self.end_utc_ns],
            "frame_count": self.frame_count,
            "interval_count": self.interval_count,
            "block_count": len(next(iter(self.blocks.values()), ())),
            "incomplete_tail_frames": self.partial_count,
            "window_count": len(self.windows),
            "cadence_anomaly_count": len(self.anomalies),
            "duplicate_count": self.duplicate_count,
            "anomalies": self.anomalies,
            "windows": self.windows,
            "warnings": self.warnings,
            "confirmations": self.confirmations,
        }


class StreamingSoakDetector:
    """Incremental O(D)-per-frame analyzer with stride-only window work."""

    def __init__(self, registry: object, *, profile: SoakProfile = TEST_PROFILE) -> None:
        self.registry = FrozenRegistry.from_input(registry)
        self.profile = profile
        self._attempts: list[_Attempt] = []
        self._current: _Attempt | None = None
        self._last: ValueFrame | None = None
        self._cursor_hashes: dict[int, str] = {}
        self._pair_hashes: dict[tuple[str, int], str] = {}
        self._state = SoakState.RUNNING
        self._terminal_statistical: SoakState | None = None
        self._corruption: dict[str, object] | None = None
        self._terminal_events: list[dict[str, object]] = []
        self._duplicate_count = 0
        self._closed = False
        if not self.registry.registrations or self.registry.unavailable_dim_ids:
            self._state = SoakState.INSUFFICIENT_EVIDENCE

    @property
    def state(self) -> SoakState:
        return self._state

    @property
    def current_attempt(self) -> _Attempt | None:
        return self._current

    def _mark_corrupt(self, error: EvidenceCorruptError) -> None:
        if self._state is SoakState.EVIDENCE_CORRUPT:
            return
        previous = self._state.value
        self._state = SoakState.EVIDENCE_CORRUPT
        self._corruption = {"code": error.code, "detail": error.detail}
        self._terminal_events.append({
            "event": "corruption_precedence", "previous_state": previous,
            "state": self._state.value, "code": error.code,
        })

    def mark_external_corruption(self, code: str, detail: str) -> None:
        self._mark_corrupt(EvidenceCorruptError(code, detail))

    def _new_attempt(self, frame: ValueFrame) -> _Attempt:
        attempt = _Attempt(
            attempt_id=len(self._attempts) + 1,
            segment_id=len(self._attempts) + 1,
            registry_order=frame.order,
            start_cursor=frame.cursor,
            start_tick=frame.field_tick,
            start_utc_ns=frame.utc_unix_ns,
            end_cursor=frame.cursor,
            end_tick=frame.field_tick,
            end_utc_ns=frame.utc_unix_ns,
            boot_id=frame.boot_id,
            partial_sums={dim_id: 0.0 for dim_id in self.registry.dim_ids},
            blocks={dim_id: [] for dim_id in self.registry.dim_ids},
        )
        self._attempts.append(attempt)
        self._current = attempt
        if (
            self._terminal_statistical is None
            and self._state is not SoakState.EVIDENCE_CORRUPT
            and self.registry.registrations
            and not self.registry.unavailable_dim_ids
        ):
            self._state = SoakState.RUNNING
        self._add_values(attempt, frame.value_map())
        return attempt

    def _break_before_frame(self, reason: str, frame: ValueFrame) -> None:
        self._end_attempt(reason)
        self._new_attempt(frame)

    def _end_attempt(self, reason: str) -> None:
        if self._current is None or self._current.end_reason is not None:
            return
        self._current.end_reason = reason
        if self._current.state not in {SoakState.FAIL.value, SoakState.PASS.value}:
            self._current.state = SoakState.INSUFFICIENT_EVIDENCE.value
        if self._terminal_statistical is None and self._state is not SoakState.EVIDENCE_CORRUPT:
            self._state = SoakState.INSUFFICIENT_EVIDENCE

    def ingest_frame(self, frame_input: object) -> SoakState:
        if self._closed:
            raise RuntimeError("detector is closed")
        try:
            frame = ValueFrame.from_input(frame_input, self.registry)
        except EvidenceCorruptError as exc:
            self._mark_corrupt(exc)
            return self._state
        return self.ingest_validated_frame(frame)

    def ingest_validated_frame(self, frame: ValueFrame) -> SoakState:
        """Ingest a frame already validated against this detector's registry.

        The companion evidence store uses this after creating its canonical
        persisted payload from the same ``ValueFrame``. Other callers should
        use :meth:`ingest_frame`, which performs validation first.
        """
        if not isinstance(frame, ValueFrame):
            raise TypeError("frame must be a ValueFrame")
        if self._closed:
            raise RuntimeError("detector is closed")
        try:
            cursor_seen = self._cursor_hashes.get(frame.cursor)
            pair = (frame.boot_id, frame.field_tick)
            pair_seen = self._pair_hashes.get(pair)
            if cursor_seen is not None or pair_seen is not None:
                if (cursor_seen is None or cursor_seen == frame.frame_hash) and (pair_seen is None or pair_seen == frame.frame_hash):
                    self._duplicate_count += 1
                    if self._current is not None:
                        self._current.duplicate_count += 1
                    return self._state
                raise EvidenceCorruptError("conflicting_duplicate", "cursor or boot/tick duplicate differs")
            if self._last is not None:
                if frame.cursor <= self._last.cursor:
                    raise EvidenceCorruptError("reverse_cursor", "cursor is not strictly increasing")
                if frame.boot_id == self._last.boot_id and frame.field_tick <= self._last.field_tick:
                    raise EvidenceCorruptError("reverse_tick", "field tick reversed within boot")
                if frame.utc_unix_ns <= self._last.utc_unix_ns:
                    raise EvidenceCorruptError("reverse_utc", "UTC is not strictly increasing")

            self._cursor_hashes[frame.cursor] = frame.frame_hash
            self._pair_hashes[pair] = frame.frame_hash
            if self._last is None:
                self._new_attempt(frame)
            elif self._current is not None and self._current.end_reason is not None:
                self._new_attempt(frame)
            else:
                delta_ns = frame.utc_unix_ns - self._last.utc_unix_ns
                delta_seconds = delta_ns / 1_000_000_000.0
                break_reason: str | None = None
                if frame.boot_id != self._last.boot_id:
                    break_reason = "boot_change"
                elif frame.field_tick != self._last.field_tick + 1:
                    break_reason = "missing_tick"
                elif frame.order != self._last.order:
                    break_reason = "registry_ordinal_reorder"
                elif delta_seconds > 30.0:
                    break_reason = "process_suspension"
                else:
                    anomaly = (0.0 < delta_seconds < 0.2) or (2.0 < delta_seconds <= 30.0)
                    if anomaly and self._current is not None:
                        self._current.anomalies.append({
                            "after_cursor": self._last.cursor,
                            "cursor": frame.cursor,
                            "delta_ns": delta_ns,
                            "delta_seconds": delta_seconds,
                        })
                        if len(self._current.anomalies) > MAX_CADENCE_ANOMALIES:
                            break_reason = "cadence_anomaly_limit_exceeded"
                if break_reason is not None:
                    self._break_before_frame(break_reason, frame)
                else:
                    assert self._current is not None
                    self._current.frame_count += 1
                    self._current.interval_count += 1
                    self._current.end_cursor = frame.cursor
                    self._current.end_tick = frame.field_tick
                    self._current.end_utc_ns = frame.utc_unix_ns
                    self._add_values(self._current, frame.value_map())
            self._last = frame
            self._update_formal_pass()
        except EvidenceCorruptError as exc:
            self._mark_corrupt(exc)
        return self._state

    def end_open_attempt(self, reason: str = "observer_closed") -> SoakState:
        """Persist an attempt boundary while allowing a later replay/resume."""
        if not isinstance(reason, str) or not reason:
            raise ValueError("reason must be non-empty")
        if self._state is not SoakState.EVIDENCE_CORRUPT and self._terminal_statistical is None:
            self._end_attempt(reason)
        return self._state

    def reopen_for_append(self) -> None:
        """Allow a replayed, ended artifact to accept a future new attempt."""
        if self._state is SoakState.EVIDENCE_CORRUPT or self._terminal_statistical is not None:
            return
        if self._current is not None and self._current.end_reason is None:
            raise RuntimeError("cannot reopen an active attempt")
        self._closed = False

    def ingest_block_means(self, values_by_dim: Mapping[str, Sequence[float]]) -> SoakState:
        """Independent calibration/test adapter over already frozen 60-frame means."""
        if self._closed:
            raise RuntimeError("detector is closed")
        try:
            if set(values_by_dim) != set(self.registry.dim_ids):
                raise EvidenceCorruptError("block_alignment_failure", "block dim_id set differs from registry")
            lengths = {len(values_by_dim[dim_id]) for dim_id in self.registry.dim_ids}
            if len(lengths) > 1:
                raise EvidenceCorruptError("block_alignment_failure", "block series lengths differ")
            for dim_id in self.registry.dim_ids:
                if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value))
                       for value in values_by_dim[dim_id]):
                    raise EvidenceCorruptError("block_non_finite", "block value is non-finite")
            if self._current is None:
                count = next(iter(lengths), 0)
                self._current = _Attempt(
                    attempt_id=1, segment_id=1, registry_order=self.registry.dim_ids,
                    start_cursor=1, start_tick=1, start_utc_ns=0,
                    end_cursor=count * BLOCK_FRAMES, end_tick=count * BLOCK_FRAMES,
                    end_utc_ns=count * BLOCK_FRAMES * 1_000_000_000,
                    boot_id="calibration", frame_count=count * BLOCK_FRAMES,
                    interval_count=max(0, count * BLOCK_FRAMES - 1), partial_sums={},
                    blocks={dim_id: [float(value) for value in values_by_dim[dim_id]]
                            for dim_id in self.registry.dim_ids},
                )
                self._attempts.append(self._current)
                count = next(iter(lengths), 0)
                for block_count in range(WINDOW_BLOCKS, count + 1, WINDOW_STRIDE_BLOCKS):
                    self._analyze_window(self._current, block_count)
            else:
                raise EvidenceCorruptError("block_adapter_reused", "block adapter accepts one artifact")
        except EvidenceCorruptError as exc:
            self._mark_corrupt(exc)
        return self._state

    def _add_values(self, attempt: _Attempt, values: Mapping[str, float]) -> None:
        if not self.registry.registrations or self.registry.unavailable_dim_ids:
            return
        for dim_id in self.registry.dim_ids:
            attempt.partial_sums[dim_id] += values[dim_id]
        attempt.partial_count += 1
        if attempt.partial_count != BLOCK_FRAMES:
            return
        for dim_id in self.registry.dim_ids:
            attempt.blocks[dim_id].append(attempt.partial_sums[dim_id] / BLOCK_FRAMES)
            attempt.partial_sums[dim_id] = 0.0
        attempt.partial_count = 0
        block_count = len(attempt.blocks[self.registry.dim_ids[0]])
        if block_count >= WINDOW_BLOCKS and (block_count - WINDOW_BLOCKS) % WINDOW_STRIDE_BLOCKS == 0:
            self._analyze_window(attempt, block_count)

    def _analyze_window(self, attempt: _Attempt, block_count: int) -> None:
        start = block_count - WINDOW_BLOCKS
        window_index = len(attempt.windows)
        dimensions: dict[str, object] = {}
        for dim_id in self.registry.dim_ids:
            values = attempt.blocks[dim_id][start:block_count]
            variance, exact_freeze = sample_variance(values)
            collapse_raw = variance <= VARIANCE_THRESHOLD
            periodic_candidates: list[dict[str, object]] = []
            fundamental: int | None = None
            if variance > VARIANCE_THRESHOLD:
                autocorrelation = direct_autocorrelation(values)
                periodic_candidates = qualifying_periodic_candidates(autocorrelation)
                if periodic_candidates:
                    fundamental = cast(int, periodic_candidates[0]["lag_minutes"])
            previous = attempt.previous_hits.get(dim_id)
            collapse_confirmed = bool(
                collapse_raw and previous is not None and previous.get("collapse_raw")
                and cast(int, previous["window_index"]) == window_index - 1
            )
            periodic_confirmed = False
            tolerance: float | None = None
            if fundamental is not None and previous is not None and previous.get("fundamental") is not None \
                    and cast(int, previous["window_index"]) == window_index - 1:
                periodic_confirmed, tolerance = _periodic_match(
                    cast(int, previous["fundamental"]), fundamental
                )
            result = {
                "availability": "AVAILABLE",
                "variance": variance,
                "exact_freeze": exact_freeze,
                "collapse_raw_hit": collapse_raw,
                "collapse_confirmed": collapse_confirmed,
                "periodic_raw_hit": fundamental is not None,
                "periodic_fundamental_minutes": fundamental,
                "periodic_candidates": periodic_candidates,
                "periodic_confirmation_tolerance_minutes": tolerance,
                "periodic_confirmed": periodic_confirmed,
            }
            dimensions[dim_id] = result
            if collapse_raw or fundamental is not None:
                warning = {
                    "dim_id": dim_id, "window_index": window_index,
                    "family": "variance_collapse" if collapse_raw else "periodic",
                    "confirmed": collapse_confirmed or periodic_confirmed,
                }
                attempt.warnings.append(warning)
            if collapse_confirmed or periodic_confirmed:
                confirmation = {
                    "dim_id": dim_id, "window_index": window_index,
                    "previous_window_index": window_index - 1,
                    "family": "variance_collapse" if collapse_confirmed else "periodic",
                    "fundamental_minutes": fundamental if periodic_confirmed else None,
                    "tolerance_minutes": tolerance if periodic_confirmed else None,
                }
                attempt.confirmations.append(confirmation)
                self._set_fail(attempt, confirmation)
            attempt.previous_hits[dim_id] = {
                "window_index": window_index,
                "collapse_raw": collapse_raw,
                "fundamental": fundamental,
            }
        attempt.windows.append({
            "window_index": window_index,
            "start_block": start,
            "end_block_exclusive": block_count,
            "start_minute": start,
            "duration_minutes": WINDOW_BLOCKS,
            "dimensions": dimensions,
        })

    def _set_fail(self, attempt: _Attempt, confirmation: Mapping[str, object]) -> None:
        attempt.state = SoakState.FAIL.value
        if self._state is SoakState.EVIDENCE_CORRUPT:
            return
        if self._terminal_statistical is None:
            previous = self._state.value
            self._terminal_statistical = SoakState.FAIL
            self._state = SoakState.FAIL
            self._terminal_events.append({
                "event": "confirmed_failure", "previous_state": previous,
                "state": SoakState.FAIL.value, "attempt_id": attempt.attempt_id,
                "confirmation": dict(confirmation),
            })

    def _update_formal_pass(self) -> None:
        if not self.profile.formal_48h or self._current is None:
            return
        if self._terminal_statistical is not None or self._state is SoakState.EVIDENCE_CORRUPT:
            return
        attempt = self._current
        elapsed = attempt.end_utc_ns - attempt.start_utc_ns
        eligible = (
            attempt.end_reason is None
            and attempt.frame_count == FORMAL_FRAMES
            and attempt.interval_count == FORMAL_INTERVALS
            and elapsed >= FORMAL_ELAPSED_NS
            and len(attempt.windows) == 7
            and len(attempt.anomalies) <= MAX_CADENCE_ANOMALIES
            and not self.registry.unavailable_dim_ids
            and bool(self.registry.registrations)
            and not attempt.confirmations
        )
        if eligible:
            previous = self._state.value
            self._terminal_statistical = SoakState.PASS
            self._state = SoakState.PASS
            attempt.state = SoakState.PASS.value
            self._terminal_events.append({
                "event": "formal_pass", "previous_state": previous,
                "state": SoakState.PASS.value, "attempt_id": attempt.attempt_id,
            })

    def close(self) -> SoakState:
        if self._closed:
            return self._state
        self._closed = True
        if self._state is SoakState.EVIDENCE_CORRUPT:
            return self._state
        if self._terminal_statistical is not None:
            return self._state
        self.end_open_attempt("observer_closed")
        self._state = SoakState.INSUFFICIENT_EVIDENCE
        return self._state

    def report_primitive(self) -> dict[str, object]:
        availability = {
            entry.dim_id: {
                "status": "AVAILABLE" if entry.available else "DIMENSION_UNAVAILABLE",
                "ou_acceleration_sigma": entry.ou_acceleration_sigma,
            }
            for entry in self.registry.registrations
        }
        current = self._current
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "contract_version": SOAK_CONTRACT_VERSION,
            "profile": self.profile.primitive(),
            "formal_48h": self.profile.formal_48h,
            "formal_48h_run": "not_run",
            "p4_human_gate": "not_run",
            "state": self._state.value,
            "thresholds": dict(THRESHOLDS),
            "registry": self.registry.primitive(),
            "registry_sha256": self.registry.sha256,
            "dimension_availability": availability,
            "attempt_count": len(self._attempts),
            "segment_count": len(self._attempts),
            "duplicate_count": self._duplicate_count,
            "cursor_bounds": None if current is None else [current.start_cursor, current.end_cursor],
            "tick_bounds": None if current is None else [current.start_tick, current.end_tick],
            "utc_unix_ns_bounds": None if current is None else [current.start_utc_ns, current.end_utc_ns],
            "attempts": [attempt.primitive() for attempt in self._attempts],
            "corruption": self._corruption,
            "terminal_precedence_events": self._terminal_events,
        }
