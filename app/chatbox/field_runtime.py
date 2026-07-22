"""P1.2-B single-owner field runtime for chatbox v0.

Owns exactly one ``FieldDynamics`` and one ``FieldPersistenceStore``.  The
runtime is the only live owner of field state; it never exposes the raw
``FieldDynamics`` or any setter/patch/restore API.  Startup is empty-or-recover:
on an empty DB it births a candidate, captures and persists the tick-0 capsule,
then installs it; on a non-empty DB it decodes the latest snapshot, constructs a
candidate via the frozen capsule boundary, read-backs it, then installs it.

The 1 Hz loop uses a monotonic deadline.  Every mutating API first checks
runtime health, then checks the 60-second snapshot deadline; if expired, it
captures and synchronously writes a snapshot before the next mutation, so the
60-second bound is never breached by "mutate first, snapshot later".  If a
mutation succeeds but persistence fails, the runtime poisons itself, emits one
structured JSON error to stderr, and raises; it never silently swallows the
error or pretends to roll back in-memory state.

Imports are restricted to the Python standard library plus
``field_dynamics`` and ``field_state_capsule`` (and ``field_persistence``).
No quarantined modules, no pickle/marshal/eval/exec, no hardcoded dimension
count, no direct writes to field-private state.
"""

from __future__ import annotations

from dataclasses import dataclass
import io as _io
import json as _json
import os
import secrets
import sys
import time
from typing import Callable, Sequence

if os.name == "nt":
    import msvcrt as _msvcrt  # type: ignore[import-untyped]
else:
    import fcntl as _fcntl  # type: ignore[import-untyped]

from app.chatbox.field_dynamics import (
    AttractorMove,
    DimensionRegistration,
    DynamicsContractError,
    FieldDynamics,
    FieldSnapshot,
    SeededGaussianRngFactory,
    TickObservation,
    build_birth_registry,
)
from app.chatbox.field_persistence import (
    AttractorBatchMoveInput,
    AttractorBatchMoveResult,
    AttractorBatchReceipt,
    FieldPersistenceError,
    FieldPersistenceStore,
    TrajectoryFrame,
    TrajectoryPoint,
    TrajectoryRowInput,
)
from app.chatbox.field_state_capsule import (
    FieldStateCapsuleError,
    _capture_field_state_capsule,
    _construct_field_candidate,
    decode_field_state_capsule,
    encode_field_state_capsule,
)


SNAPSHOT_INTERVAL_SECONDS = 60.0
TICK_INTERVAL_SECONDS = 1.0


class FieldRuntimeError(Exception):
    """Stable, structured runtime error.

    Carries a stable ``code``, the ``operation``/``stage`` that failed, the
    ``db_path``, an optional ``field_tick``, and a human-readable ``detail``.
    The runtime emits a structured JSON form to stderr before raising.
    """

    def __init__(
        self,
        code: str,
        operation: str,
        db_path: str,
        detail: str,
        *,
        stage: str | None = None,
        field_tick: int | None = None,
    ) -> None:
        self.code = code
        self.operation = operation
        self.db_path = db_path
        self.detail = detail
        self.stage = stage
        self.field_tick = field_tick
        message = f"{code}: {detail} (operation={operation}, db={db_path})"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class RegistryProxy:
    """Read-only proxy over the installed field registry."""

    registrations: tuple[DimensionRegistration, ...]

    @property
    def dim_ids(self) -> tuple[str, ...]:
        return tuple(registration.dim_id for registration in self.registrations)

    @property
    def length(self) -> int:
        return len(self.registrations)


def _emit_structured_error(
    *,
    type_name: str,
    code: str,
    operation: str,
    db_path: str,
    detail: str,
    stage: str | None,
    field_tick: int | None,
) -> None:
    payload = {
        "type": type_name,
        "code": code,
        "operation": operation,
        "stage": stage,
        "db_path": db_path,
        "field_tick": field_tick,
        "detail": detail,
    }
    try:
        sys.stderr.write(_json.dumps(payload, separators=(",", ":")) + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def _map_persistence_error(
    exc: FieldPersistenceError,
    *,
    operation: str,
    stage: str,
) -> FieldRuntimeError:
    return FieldRuntimeError(
        exc.code,
        operation,
        exc.db_path,
        exc.detail,
        stage=stage,
        field_tick=exc.field_tick,
    )


def _map_capsule_error(
    exc: FieldStateCapsuleError,
    *,
    operation: str,
    db_path: str,
    stage: str,
) -> FieldRuntimeError:
    return FieldRuntimeError(
        exc.code,
        operation,
        db_path,
        exc.detail,
        stage=stage,
    )


def _safe_store_close(store: FieldPersistenceStore, operation: str) -> None:
    """Close the store, emitting any close failure to stderr without masking.

    Used on startup error paths so a close() failure never replaces the
    primary error that is about to be raised.  If close fails, the failure
    is written to stderr as a second structured error but not re-raised.
    """
    try:
        store.close()
    except FieldPersistenceError as exc:
        _emit_structured_error(
            type_name="FieldPersistenceError",
            code=exc.code,
            operation=operation,
            db_path=exc.db_path,
            detail=(
                f"close failed during startup error recovery; "
                f"primary error will be raised: {exc.detail}"
            ),
            stage="startup.close_on_error",
            field_tick=None,
        )
    except Exception as exc:
        _emit_structured_error(
            type_name="Exception",
            code="startup_close_failed",
            operation=operation,
            db_path=store.db_path,
            detail=f"close raised unexpected exception during startup "
            f"error recovery: {exc}",
            stage="startup.close_on_error",
            field_tick=None,
        )


_owner_lock_token = object()


def _normalize_db_path(db_path: str) -> str:
    """Return canonical absolute real-path form of ``db_path``."""
    canonical = os.path.realpath(os.path.abspath(db_path))
    if os.name == "nt":
        canonical = os.path.normcase(canonical)
    return canonical


def _ensure_db_parent_dir(canonical_db_path: str, *, operation: str) -> None:
    """Create the database's parent directory if it does not yet exist.

    The sidecar owner-lock file lives beside the database file, so its parent
    directory must exist before ``_OwnerLock.acquire()`` opens it.  This helper
    creates exactly that one parent chain (nothing beside the database) using
    ``exist_ok=True`` so concurrent creation by another process is tolerated.
    Failures (parent path is a file, permission denied, other OS errors) are
    mapped to a structured ``FieldRuntimeError`` and never swallowed.
    """
    parent = os.path.dirname(canonical_db_path)
    if not parent:
        # Canonical paths are absolute, so this is unreachable in practice;
        # keep the guard so a future caller cannot bypass directory creation.
        return
    if os.path.isdir(parent):
        return
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as exc:
        raise FieldRuntimeError(
            "startup_db_parent_dir_failed",
            operation,
            canonical_db_path,
            f"cannot create database parent directory {parent!r}: {exc}",
            stage="startup.ensure_parent_dir",
        ) from exc
    if not os.path.isdir(parent):
        raise FieldRuntimeError(
            "startup_db_parent_dir_failed",
            operation,
            canonical_db_path,
            f"database parent directory {parent!r} was not created and is not a directory",
            stage="startup.ensure_parent_dir",
        )


class _OwnerLock:
    """Cross-platform non-blocking exclusive file lock (sidecar lock file)."""

    def __init__(self, canonical_db_path: str) -> None:
        self._lock_path = canonical_db_path + ".owner.lock"
        self._handle: _io.FileIO | None = None

    def acquire(self) -> None:
        """Acquire the exclusive non-blocking lock."""
        try:
            self._handle = _io.FileIO(self._lock_path, "a+b")
        except OSError as exc:
            raise FieldRuntimeError(
                "owner_lock_open_failed",
                "startup",
                self._lock_path,
                f"cannot open owner-lock file: {exc}",
                stage="startup.owner_lock",
            ) from exc
        try:
            if os.name == "nt":
                try:
                    self._handle.seek(0, os.SEEK_END)
                    if self._handle.tell() == 0:
                        self._handle.write(b"\x00")
                        self._handle.flush()
                except OSError:
                    pass
                self._handle.seek(0)
                _msvcrt.locking(self._handle.fileno(), _msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            else:
                _fcntl.flock(self._handle.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)  # type: ignore[attr-defined]
        except OSError as exc:
            self._close_handle()
            import errno as _errno
            err = getattr(exc, "errno", 0)
            eagain = getattr(_errno, "EAGAIN", None)
            eacces = getattr(_errno, "EACCES", None)
            if err in (eagain, eacces) and err != 0:
                code = "owner_lock_held"
                detail = "another owner holds the database lock"
            else:
                code = "owner_lock_acquire_failed"
                detail = f"OS lock acquire failed: {exc}"
            raise FieldRuntimeError(
                code,
                "startup",
                self._lock_path,
                detail,
                stage="startup.owner_lock",
            ) from exc

    def release(self) -> None:
        """Release the exclusive lock (idempotent)."""
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                _msvcrt.locking(self._handle.fileno(), _msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            else:
                _fcntl.flock(self._handle.fileno(), _fcntl.LOCK_UN)  # type: ignore[attr-defined]
        except OSError:
            pass
        finally:
            self._close_handle()

    def _close_handle(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None


class FieldRuntime:
    """Single-owner field runtime with empty-or-recover startup.

    Construct via ``FieldRuntime.open(...)``.  Once opened, the runtime owns
    one ``FieldDynamics`` and one ``FieldPersistenceStore``.  All field and
    SQLite operations are serial on the owning thread.  Direct construction
    is forbidden -- only the ``open()`` factory can legally produce an
    instance.
    """

    def __init__(
        self,
        store: FieldPersistenceStore,
        dynamics: FieldDynamics,
        boot_id: str,
        *,
        _token: object = None,
        _owner_lock: _OwnerLock | None = None,
        clock: Callable[[], float] | None = None,
        utc_clock: Callable[[], int] | None = None,
    ) -> None:
        if _token is not _owner_lock_token:
            raise FieldRuntimeError(
                "runtime_construction_forbidden",
                "construct",
                "",
                "FieldRuntime must be constructed via FieldRuntime.open(); "
                "direct construction is forbidden",
                stage="construct.guard",
            )
        if _owner_lock is None:
            raise FieldRuntimeError(
                "runtime_construction_forbidden",
                "construct",
                "",
                "FieldRuntime must own the database lock",
                stage="construct.guard",
            )
        self._owner_lock = _owner_lock
        self._store = store
        self._dynamics = dynamics
        self._boot_id = boot_id
        self._clock = clock if clock is not None else time.monotonic
        self._utc_clock = utc_clock if utc_clock is not None else _default_utc_ns
        self._healthy = True
        self._poisoned = False
        self._closed = False
        self._last_snapshot_monotonic = self._clock()
        self._dirty_since_snapshot = False
        self._last_committed_frame: TrajectoryFrame | None = None

    # -- factory ----------------------------------------------------------

    @classmethod
    def open(
        cls,
        db_path: str,
        *,
        birth_registry: Sequence[DimensionRegistration] | None = None,
        birth_rng_factory: SeededGaussianRngFactory | None = None,
        clock: Callable[[], float] | None = None,
        utc_clock: Callable[[], int] | None = None,
    ) -> "FieldRuntime":
        """Open the store and install the field owner.

        Acquires the exclusive owner lock BEFORE any database access,
        including constructing the ``FieldPersistenceStore``.  On an empty DB,
        a birth candidate is created using the optional birth registry and the
        nominal ``SeededGaussianRngFactory`` only; the dynamics constructor owns
        the exact factory/provider boundary.  Its tick-0 capsule is captured and
        persisted before installation.  On a non-empty DB, the latest snapshot
        is decoded and birth-only parameters are rejected before recovery.
        """
        operation = "startup"
        utc = utc_clock if utc_clock is not None else _default_utc_ns
        canonical = _normalize_db_path(db_path)
        # The sidecar owner-lock file lives beside the database file, so its
        # parent directory must exist before _OwnerLock.acquire() opens it.
        # This runs before any database access and before lock acquisition, so
        # a missing parent directory on a fresh checkout no longer aborts the
        # default README startup command.
        _ensure_db_parent_dir(canonical, operation=operation)
        owner_lock = _OwnerLock(canonical)
        try:
            owner_lock.acquire()
        except FieldRuntimeError:
            raise
        except Exception as exc:
            raise FieldRuntimeError(
                "owner_lock_acquire_failed",
                operation,
                canonical,
                f"unexpected error acquiring owner lock: {exc}",
                stage="startup.owner_lock",
            ) from exc

        store: FieldPersistenceStore | None = None
        try:
            try:
                store = FieldPersistenceStore(db_path)
            except FieldPersistenceError as exc:
                raise _map_persistence_error(exc, operation=operation, stage="startup.store") from exc

            try:
                store.ensure_schema()
                empty = store.is_empty()
            except FieldPersistenceError as exc:
                _safe_store_close(store, operation)
                raise _map_persistence_error(exc, operation=operation, stage="startup.schema") from exc

            if empty:
                dynamics = cls._birth_candidate(
                    birth_registry=birth_registry,
                    birth_rng_factory=birth_rng_factory,
                    store=store,
                    db_path=db_path,
                    operation=operation,
                    utc_clock=utc,
                )
            else:
                if birth_registry is not None or birth_rng_factory is not None:
                    _safe_store_close(store, operation)
                    raise FieldRuntimeError(
                        "startup_birth_params_on_nonempty_db",
                        operation,
                        db_path,
                        "birth-only registry/rng_factory must not be supplied when "
                        "the database is non-empty; recovery uses the persisted "
                        "registry only",
                        stage="startup.reject_birth_params",
                    )
                dynamics = cls._recover_candidate(
                    store=store,
                    db_path=db_path,
                    operation=operation,
                )

            boot_id = _new_boot_id()
            runtime = cls(
                store,
                dynamics,
                boot_id,
                _token=_owner_lock_token,
                _owner_lock=owner_lock,
                clock=clock,
                utc_clock=utc,
            )
            runtime._last_snapshot_monotonic = runtime._clock()
            runtime._dirty_since_snapshot = False
            return runtime

        except Exception:
            # On any failure after lock acquisition, safely close the store
            # (if constructed) and release the owner lock.  Secondary errors
            # from close/unlock must not mask the primary exception.
            if store is not None:
                try:
                    _safe_store_close(store, operation)
                except Exception:
                    pass
            try:
                owner_lock.release()
            except Exception:
                pass
            raise

    @staticmethod
    def _birth_candidate(
        *,
        birth_registry: Sequence[DimensionRegistration] | None,
        birth_rng_factory: SeededGaussianRngFactory | None,
        store: FieldPersistenceStore,
        db_path: str,
        operation: str,
        utc_clock: Callable[[], int],
    ) -> FieldDynamics:
        registry = (
            tuple(birth_registry)
            if birth_registry is not None
            else build_birth_registry()
        )
        try:
            dynamics = FieldDynamics(
                registry,
                rng_factory=birth_rng_factory,
            )
        except Exception as exc:
            _safe_store_close(store, operation)
            raise FieldRuntimeError(
                "startup_birth_failed",
                operation,
                db_path,
                f"birth candidate construction failed: {exc}",
                stage="startup.birth",
            ) from exc

        try:
            capsule = _capture_field_state_capsule(dynamics)
            primitive = encode_field_state_capsule(capsule)
        except FieldStateCapsuleError as exc:
            _safe_store_close(store, operation)
            raise _map_capsule_error(
                exc, operation=operation, db_path=db_path, stage="startup.birth.capture"
            ) from exc
        except Exception as exc:
            _safe_store_close(store, operation)
            raise FieldRuntimeError(
                "startup_birth_failed",
                operation,
                db_path,
                f"tick-0 capture/encode failed: {exc}",
                stage="startup.birth.capture",
            ) from exc

        try:
            store.write_snapshot(primitive, utc_unix_ns=utc_clock())
        except FieldPersistenceError as exc:
            _safe_store_close(store, operation)
            raise _map_persistence_error(
                exc, operation=operation, stage="startup.birth.snapshot"
            ) from exc
        return dynamics

    @staticmethod
    def _recover_candidate(
        *,
        store: FieldPersistenceStore,
        db_path: str,
        operation: str,
    ) -> FieldDynamics:
        try:
            primitive = store.read_latest_snapshot()
        except FieldPersistenceError as exc:
            _safe_store_close(store, operation)
            raise _map_persistence_error(
                exc, operation=operation, stage="startup.recover.read"
            ) from exc

        if primitive is None:
            _safe_store_close(store, operation)
            raise FieldRuntimeError(
                "startup_no_snapshot",
                operation,
                db_path,
                "non-empty database reported no readable snapshot",
                stage="startup.recover.read",
            )

        try:
            capsule = decode_field_state_capsule(primitive)
        except FieldStateCapsuleError as exc:
            _safe_store_close(store, operation)
            raise _map_capsule_error(
                exc, operation=operation, db_path=db_path, stage="startup.recover.decode"
            ) from exc

        try:
            candidate = _construct_field_candidate(capsule)
        except FieldStateCapsuleError as exc:
            _safe_store_close(store, operation)
            raise _map_capsule_error(
                exc, operation=operation, db_path=db_path, stage="startup.recover.construct"
            ) from exc
        except Exception as exc:
            _safe_store_close(store, operation)
            raise FieldRuntimeError(
                "startup_construct_failed",
                operation,
                db_path,
                f"candidate construction failed: {exc}",
                stage="startup.recover.construct",
            ) from exc

        # Audit event/trajectory history before installing live owner.
        registry_dim_ids = tuple(r.dim_id for r in candidate.registry)
        try:
            store.audit_event_history(registry_dim_ids=registry_dim_ids)
        except FieldPersistenceError as exc:
            _safe_store_close(store, operation)
            raise _map_persistence_error(
                exc, operation=operation, stage="startup.recover.audit"
            ) from exc

        return candidate

    # -- read-only proxies ------------------------------------------------

    @property
    def boot_id(self) -> str:
        return self._boot_id

    @property
    def field_tick(self) -> int:
        self._require_healthy("field_tick")
        return self._dynamics.snapshot().tick

    @property
    def healthy(self) -> bool:
        return self._healthy and not self._poisoned and not self._closed

    def registry_proxy(self) -> RegistryProxy:
        self._require_healthy("registry_proxy")
        return RegistryProxy(self._dynamics.registry)

    def snapshot_proxy(self) -> FieldSnapshot:
        self._require_healthy("snapshot_proxy")
        return self._dynamics.snapshot()

    def latest_tick_cursor_proxy(self) -> int | None:
        self._require_healthy("latest_tick_cursor_proxy")
        return self._read_store("latest_tick_cursor_proxy", self._store.latest_tick_cursor)

    def tick_cursor_exists_proxy(self, cursor: int) -> bool:
        self._require_healthy("tick_cursor_exists_proxy")
        return self._read_store(
            "tick_cursor_exists_proxy", lambda: self._store.tick_cursor_exists(cursor)
        )

    def trajectory_frames_proxy(
        self,
        *,
        after_cursor: int | None,
        cutoff_cursor: int | None,
        limit: int,
    ) -> tuple[TrajectoryFrame, ...]:
        self._require_healthy("trajectory_frames_proxy")
        registry_dim_ids = self.registry_proxy().dim_ids
        return self._read_store(
            "trajectory_frames_proxy",
            lambda: self._store.read_trajectory_frames(
                registry_dim_ids=registry_dim_ids,
                after_cursor=after_cursor,
                cutoff_cursor=cutoff_cursor,
                limit=limit,
            ),
        )

    def last_committed_frame_proxy(self) -> TrajectoryFrame | None:
        self._require_healthy("last_committed_frame_proxy")
        return self._last_committed_frame

    def _read_store(self, operation: str, reader: Callable[[], object]):
        try:
            return reader()
        except FieldPersistenceError as exc:
            raise _map_persistence_error(
                exc, operation=operation, stage=f"{operation}.store"
            ) from exc

    @property
    def db_path(self) -> str:
        return self._store.db_path

    # -- mutating APIs ----------------------------------------------------

    def tick(self) -> TickObservation:
        self._require_healthy("tick")
        self._maybe_snapshot_before_mutation(operation="tick")
        before_tick = self._dynamics.snapshot().tick
        try:
            observation = self._dynamics.tick()
        except Exception as exc:
            self._poison(
                code="tick_dynamics_failed",
                operation="tick",
                stage="tick.dynamics",
                detail=f"FieldDynamics.tick raised: {exc}",
                field_tick=before_tick,
            )
            raise
        after_tick = observation.tick_after
        rows = self._trajectory_rows(observation)
        utc_unix_ns = self._utc_clock()
        try:
            event_id = self._store.write_tick_event(
                boot_id=self._boot_id,
                before_field_tick=before_tick,
                after_field_tick=after_tick,
                utc_unix_ns=utc_unix_ns,
                trajectory_rows=rows,
            )
        except FieldPersistenceError as exc:
            self._poison(
                code=exc.code,
                operation="tick",
                stage="tick.persist",
                detail=exc.detail,
                field_tick=after_tick,
            )
            raise _map_persistence_error(
                exc, operation="tick", stage="tick.persist"
            ) from exc
        except Exception as exc:
            self._poison(
                code="tick_persist_failed",
                operation="tick",
                stage="tick.persist",
                detail=f"unexpected persistence failure: {exc}",
                field_tick=after_tick,
            )
            raise
        self._last_committed_frame = TrajectoryFrame(
            cursor=event_id,
            boot_id=self._boot_id,
            field_tick=after_tick,
            utc_unix_ns=utc_unix_ns,
            dimensions=tuple(
                TrajectoryPoint(
                    ordinal=row.dimension_ordinal,
                    dim_id=row.dim_id,
                    value=row.after_value,
                    velocity=row.after_velocity,
                    attractor=row.after_attractor,
                    slow_baseline=row.after_slow_baseline,
                    ou_acceleration=row.after_ou_acceleration,
                )
                for row in rows
            ),
        )
        self._dirty_since_snapshot = True
        return observation

    def move_attractor(self, move: AttractorMove) -> FieldSnapshot:
        """Apply an attractor move with P1.1 atomic rejection contract.

        Validation is performed entirely by ``FieldDynamics.move_attractor()``.
        The runtime reads a read-only before snapshot strictly for the
        persistence event record; it never pre-validates or pre-lookups the
        command fields.  Invalid types or unknown dim_id are rejected by
        ``FieldDynamics`` as ``InvalidAttractorMoveError`` (preserved from
        P1.1), not by the runtime.  Only a successful mutation produces a
        persisted event."""
        self._require_healthy("move_attractor")
        self._maybe_snapshot_before_mutation(operation="move_attractor")
        field_tick = self._dynamics.snapshot().tick
        # Read the before snapshot BEFORE mutation for evidence purposes.
        state_before = self._dynamics.snapshot()
        # Let FieldDynamics validate and apply; it raises invalid-type and
        # unknown-dim as InvalidAttractorMoveError (preserving P1.1 contract).
        try:
            snapshot = self._dynamics.move_attractor(move)
        except Exception:
            raise
        # Only read move fields after successful validation + mutation.
        before_attractor = self._attractor_for(state_before, move.dim_id)
        after_attractor = self._attractor_for(snapshot, move.dim_id)
        try:
            self._store.write_attractor_event(
                boot_id=self._boot_id,
                field_tick=field_tick,
                utc_unix_ns=self._utc_clock(),
                dim_id=move.dim_id,
                delta=float(move.delta),
                source=move.source,
                rationale=move.rationale,
                before_attractor=before_attractor,
                after_attractor=after_attractor,
            )
        except FieldPersistenceError as exc:
            self._poison(
                code=exc.code,
                operation="move_attractor",
                stage="move_attractor.persist",
                detail=exc.detail,
                field_tick=field_tick,
            )
            raise _map_persistence_error(
                exc, operation="move_attractor", stage="move_attractor.persist"
            ) from exc
        except Exception as exc:
            self._poison(
                code="move_attractor_persist_failed",
                operation="move_attractor",
                stage="move_attractor.persist",
                detail=f"unexpected persistence failure: {exc}",
                field_tick=field_tick,
            )
            raise
        self._dirty_since_snapshot = True
        return snapshot

    def move_attractor_batch(
        self, operation_id: str, moves: Sequence[AttractorMove]
    ) -> AttractorBatchReceipt:
        """Apply an ordered attractor batch exactly once by durable operation id.

        All moves execute against an unpublished capsule-derived candidate.  Its
        applied events, final snapshot, and receipt commit atomically before the
        candidate replaces live dynamics.  P1 command rejections are recorded
        per move and do not abort other moves.
        """
        operation = "move_attractor_batch"
        self._require_healthy(operation)
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id must be non-empty")
        if isinstance(moves, (str, bytes)) or not isinstance(moves, Sequence):
            raise ValueError("moves must be an ordered sequence")
        ordered = tuple(moves)
        request_moves: tuple[AttractorBatchMoveInput, ...] = tuple(
            AttractorBatchMoveInput(
                dim_id=move.dim_id,
                delta=float(move.delta),
                source=move.source,
                rationale=move.rationale,
            )
            for move in ordered
        )
        try:
            stored = self._store.read_attractor_batch_receipt(operation_id, request_moves)
        except FieldPersistenceError as exc:
            raise _map_persistence_error(
                exc, operation=operation, stage=f"{operation}.receipt"
            ) from exc
        if stored is not None:
            return stored

        field_tick = self._dynamics.snapshot().tick
        try:
            live_capsule = _capture_field_state_capsule(self._dynamics)
            candidate = _construct_field_candidate(live_capsule)
        except FieldStateCapsuleError as exc:
            self._poison(
                code=exc.code, operation=operation, stage=f"{operation}.candidate",
                detail=exc.detail, field_tick=field_tick,
            )
            raise _map_capsule_error(
                exc, operation=operation, db_path=self.db_path,
                stage=f"{operation}.candidate",
            ) from exc

        results: list[AttractorBatchMoveResult] = []
        for move, request_move in zip(ordered, request_moves):
            before_snapshot = candidate.snapshot()
            try:
                after_snapshot = candidate.move_attractor(move)
            except DynamicsContractError as exc:
                results.append(AttractorBatchMoveResult(
                    dim_id=request_move.dim_id, delta=request_move.delta,
                    source=request_move.source, rationale=request_move.rationale,
                    applied=False, before_attractor=None, after_attractor=None,
                    event_id=None, error_code=exc.anomaly.code,
                    error_detail=exc.anomaly.detail,
                ))
                continue
            results.append(AttractorBatchMoveResult(
                dim_id=request_move.dim_id, delta=request_move.delta,
                source=request_move.source, rationale=request_move.rationale,
                applied=True,
                before_attractor=self._attractor_for(before_snapshot, move.dim_id),
                after_attractor=self._attractor_for(after_snapshot, move.dim_id),
                event_id=None, error_code=None, error_detail=None,
            ))
        try:
            candidate_primitive = encode_field_state_capsule(
                _capture_field_state_capsule(candidate)
            )
            receipt = self._store.commit_attractor_batch(
                operation_id=operation_id, boot_id=self._boot_id,
                utc_unix_ns=self._utc_clock(), moves=request_moves,
                candidate_capsule_primitive=candidate_primitive,
                results=tuple(results),
            )
        except FieldPersistenceError as exc:
            self._poison(
                code=exc.code, operation=operation, stage=f"{operation}.persist",
                detail=exc.detail, field_tick=field_tick,
            )
            raise _map_persistence_error(
                exc, operation=operation, stage=f"{operation}.persist"
            ) from exc
        except FieldStateCapsuleError as exc:
            self._poison(
                code=exc.code, operation=operation, stage=f"{operation}.capture",
                detail=exc.detail, field_tick=field_tick,
            )
            raise _map_capsule_error(
                exc, operation=operation, db_path=self.db_path,
                stage=f"{operation}.capture",
            ) from exc

        if receipt.deduplicated:
            # Another transaction cannot normally race the single owner, but a
            # store-level duplicate remains authoritative and must not publish
            # the locally computed candidate.
            return receipt
        self._dynamics = candidate
        self._last_snapshot_monotonic = self._clock()
        self._dirty_since_snapshot = False
        return receipt

    def run_loop(self, *, stop_flag: Callable[[], bool] | None = None) -> None:
        """Run the 1 Hz tick loop until ``stop_flag`` returns True.

        Uses a monotonic deadline; never bursts to catch up after a delay.
        All field and SQLite operations stay serial on the calling thread.
        """
        self._require_healthy("run_loop")
        while True:
            if stop_flag is not None and stop_flag():
                return
            if not self.healthy:
                return
            loop_start = self._clock()
            try:
                self.tick()
            except FieldRuntimeError:
                raise
            elapsed = self._clock() - loop_start
            remaining = TICK_INTERVAL_SECONDS - elapsed
            if remaining > 0.0:
                time.sleep(remaining)

    # -- snapshot cadence -------------------------------------------------

    def _maybe_snapshot_before_mutation(self, *, operation: str) -> None:
        if not self._dirty_since_snapshot:
            return
        now = self._clock()
        if now - self._last_snapshot_monotonic < SNAPSHOT_INTERVAL_SECONDS:
            return
        self._write_periodic_snapshot(operation=operation)

    def _write_periodic_snapshot(self, *, operation: str) -> None:
        field_tick = self._dynamics.snapshot().tick
        try:
            capsule = _capture_field_state_capsule(self._dynamics)
            primitive = encode_field_state_capsule(capsule)
        except FieldStateCapsuleError as exc:
            self._poison(
                code=exc.code,
                operation=operation,
                stage=f"{operation}.snapshot.capture",
                detail=exc.detail,
                field_tick=field_tick,
            )
            raise _map_capsule_error(
                exc, operation=operation, db_path=self.db_path, stage=f"{operation}.snapshot.capture"
            ) from exc
        except Exception as exc:
            self._poison(
                code="snapshot_capture_failed",
                operation=operation,
                stage=f"{operation}.snapshot.capture",
                detail=f"capture/encode failed: {exc}",
                field_tick=field_tick,
            )
            raise
        try:
            self._store.write_snapshot(primitive, utc_unix_ns=self._utc_clock())
        except FieldPersistenceError as exc:
            self._poison(
                code=exc.code,
                operation=operation,
                stage=f"{operation}.snapshot.write",
                detail=exc.detail,
                field_tick=field_tick,
            )
            raise _map_persistence_error(
                exc, operation=operation, stage=f"{operation}.snapshot.write"
            ) from exc
        except Exception as exc:
            self._poison(
                code="snapshot_write_failed",
                operation=operation,
                stage=f"{operation}.snapshot.write",
                detail=f"unexpected persistence failure: {exc}",
                field_tick=field_tick,
            )
            raise FieldRuntimeError(
                "snapshot_write_failed",
                operation,
                self.db_path,
                f"unexpected persistence failure: {exc}",
                stage=f"{operation}.snapshot.write",
                field_tick=field_tick,
            ) from exc
        self._last_snapshot_monotonic = self._clock()
        self._dirty_since_snapshot = False

    # -- close ------------------------------------------------------------

    def close(self) -> None:
        """Close the runtime with structured error reporting.

        If healthy and dirty, a final snapshot is attempted while the lock is
        still held.  Store close (including WAL checkpoint) is always attempted;
        its failure is also emitted to stderr.  The owner lock is released last
        so that no other process can obtain the lock until the store is fully
        closed and checkpointed.  The first error encountered is raised; if both
        snapshot and close fail, the first error is raised with the second
        chained via ``__cause__``.  Idempotent: a second close does not
        duplicate unlock.

        All cleanup steps (set closed, store close, lock release) are
        guaranteed regardless of how the final snapshot exits, including
        plain ``Exception`` types that are not ``FieldRuntimeError``.
        """
        if self._closed:
            return
        first_error: Exception | None = None
        # Final snapshot while lock is still held.
        if not self._poisoned and self.healthy and self._dirty_since_snapshot:
            try:
                self._write_periodic_snapshot(operation="close")
            except FieldRuntimeError as exc:
                first_error = exc
                _emit_structured_error(
                    type_name="FieldRuntimeError",
                    code=exc.code,
                    operation="close",
                    db_path=self.db_path,
                    detail=str(exc.detail),
                    stage="close.final_snapshot",
                    field_tick=exc.field_tick,
                )
            except Exception as exc:
                first_error = exc
                _emit_structured_error(
                    type_name="Exception",
                    code="final_snapshot_failed",
                    operation="close",
                    db_path=self.db_path,
                    detail=str(exc),
                    stage="close.final_snapshot",
                    field_tick=None,
                )
        # -- guaranteed cleanup: closed -> store.close -> lock.release -----
        self._closed = True
        store_error: Exception | None = None
        try:
            self._store.close()
        except FieldPersistenceError as exc:
            _emit_structured_error(
                type_name="FieldPersistenceError",
                code=exc.code,
                operation="close",
                db_path=self.db_path,
                detail=str(exc.detail),
                stage="close.store",
                field_tick=None,
            )
            store_error = _map_persistence_error(exc, operation="close", stage="close.store")
        except Exception as exc:
            _emit_structured_error(
                type_name="Exception",
                code="close_failed",
                operation="close",
                db_path=self.db_path,
                detail=str(exc),
                stage="close.store",
                field_tick=None,
            )
            store_error = exc
        finally:
            # Release the owner lock after store is fully closed/checkpointed.
            # Nested finally guarantees lock release even if store close
            # raises an exception.
            lock_error: Exception | None = None
            try:
                self._owner_lock.release()
            except Exception as exc:
                _emit_structured_error(
                    type_name="Exception",
                    code="owner_lock_release_failed",
                    operation="close",
                    db_path=self.db_path,
                    detail=f"failed to release owner lock: {exc}",
                    stage="close.owner_lock",
                    field_tick=None,
                )
                lock_error = exc
            # -- raise errors in priority: snapshot > store > lock ----------
            # Build cause chain snapshot -> store -> lock (skip None links).
            if store_error is not None and lock_error is not None:
                store_error.__cause__ = lock_error
            elif store_error is None:
                store_error = lock_error
            if first_error is not None:
                if store_error is not None:
                    first_error.__cause__ = store_error
                raise first_error
            if store_error is not None:
                raise store_error
    def __enter__(self) -> "FieldRuntime":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- internals --------------------------------------------------------

    def _require_healthy(self, operation: str) -> None:
        if self._closed:
            raise FieldRuntimeError(
                "runtime_closed",
                operation,
                self.db_path,
                "runtime is closed",
                stage=f"{operation}.guard",
            )
        if self._poisoned or not self._healthy:
            raise FieldRuntimeError(
                "runtime_poisoned",
                operation,
                self.db_path,
                "runtime is poisoned and cannot be used",
                stage=f"{operation}.guard",
            )

    def _poison(
        self,
        *,
        code: str,
        operation: str,
        stage: str,
        detail: str,
        field_tick: int | None,
    ) -> None:
        self._poisoned = True
        self._healthy = False
        _emit_structured_error(
            type_name="FieldRuntimeError",
            code=code,
            operation=operation,
            db_path=self.db_path,
            detail=detail,
            stage=stage,
            field_tick=field_tick,
        )

    def _trajectory_rows(self, observation: TickObservation) -> list[TrajectoryRowInput]:
        rows: list[TrajectoryRowInput] = []
        for ordinal, dim in enumerate(observation.dimensions):
            rows.append(
                TrajectoryRowInput(
                    dimension_ordinal=ordinal,
                    dim_id=dim.dim_id,
                    after_value=dim.after_value,
                    after_velocity=dim.after_velocity,
                    after_attractor=dim.after_attractor,
                    after_slow_baseline=dim.after_soft_restoring_baseline,
                    after_ou_acceleration=dim.after_ou_acceleration,
                )
            )
        return rows

    @staticmethod
    def _attractor_for(snapshot: FieldSnapshot, dim_id: str) -> float:
        for dim in snapshot.dimensions:
            if dim.dim_id == dim_id:
                return dim.attractor
        raise FieldRuntimeError(
            "attractor_lookup_failed",
            "move_attractor",
            "",
            f"dim_id {dim_id!r} not found in snapshot",
            stage="move_attractor.lookup",
        )


def _new_boot_id() -> str:
    return secrets.token_hex(16)


def _default_utc_ns() -> int:
    return int(time.time_ns())
