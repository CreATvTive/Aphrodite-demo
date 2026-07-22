"""P1.2-B SQLite persistence store for chatbox v0 field dynamics.

Single-connection, single-threaded owner store.  Implements the frozen P1.2-B
contract: WAL + FULL durability, startup integrity checks, schema v2 bootstrap
and exact-v1 migration,
only on truly empty or correctly-initialized-empty databases, canonical capsule
snapshots with strict JSON + SHA-256 + transaction-internal read-back, per-tick
append-only trajectory rows, append-only event log enforced by database
triggers, and latest-2 snapshot retention.

This module never owns field state.  It only serializes, persists, and reads
back canonical primitives produced by ``field_state_capsule``.  It never
imports, copies, or patches field-private state from ``FieldDynamics``.

Imports are restricted to the Python standard library plus
``field_state_capsule`` (for canonical decode/re-encode verification).  No
quarantined modules, no pickle/marshal/eval/exec, no hardcoded dimension count.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import sqlite3
from typing import Sequence

from app.chatbox.field_state_capsule import (
    FieldStateCapsuleError,
    decode_field_state_capsule,
    encode_field_state_capsule,
)


PERSISTENCE_SCHEMA_VERSION = "aphrodite.chatbox.field-persistence/2"
PERSISTENCE_USER_VERSION = 2
_LEGACY_PERSISTENCE_SCHEMA_VERSION = "aphrodite.chatbox.field-persistence/1"
_LEGACY_PERSISTENCE_USER_VERSION = 1
EVENT_PAYLOAD_VERSION = "aphrodite.chatbox.field-event/1"
ATTRACTOR_BATCH_REQUEST_VERSION = "aphrodite.chatbox.attractor-batch-request/1"
ATTRACTOR_BATCH_RECEIPT_VERSION = "aphrodite.chatbox.attractor-batch-receipt/1"

SNAPSHOT_RETENTION_COUNT = 2

_EXPECTED_TABLES: tuple[str, ...] = (
    "chatbox_meta",
    "field_snapshots",
    "field_events",
    "trajectory_points",
    "field_operation_receipts",
)


class FieldPersistenceError(Exception):
    """Stable, structured persistence-boundary error.

    Carries a stable ``code``, the ``operation`` that failed, the ``db_path``,
    a human-readable ``detail``, an optional ``stage``, and an optional
    ``field_tick`` when available.  The runtime maps these into its structured
    stderr JSON without leaking keys or internal exception types.
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
class TrajectoryRowInput:
    """Per-dimension trajectory row input for a tick event.

    Built by the runtime from ``TickObservation.dimensions``; the store never
    assumes a dimension count and writes exactly the rows it receives.
    """

    dimension_ordinal: int
    dim_id: str
    after_value: float
    after_velocity: float
    after_attractor: float
    after_slow_baseline: float
    after_ou_acceleration: float


@dataclass(frozen=True, slots=True)
class TrajectoryPoint:
    """One immutable, validated dimension point exposed to read-only clients."""

    ordinal: int
    dim_id: str
    value: float
    velocity: float
    attractor: float
    slow_baseline: float
    ou_acceleration: float


@dataclass(frozen=True, slots=True)
class TrajectoryFrame:
    """One committed tick event and all of its dimension points."""

    cursor: int
    boot_id: str
    field_tick: int
    utc_unix_ns: int
    dimensions: tuple[TrajectoryPoint, ...]


@dataclass(frozen=True, slots=True)
class AttractorBatchMoveInput:
    """One ordered, persistence-neutral attractor batch command."""

    dim_id: str
    delta: float
    source: str
    rationale: str


@dataclass(frozen=True, slots=True)
class AttractorBatchMoveResult:
    """Stable result of one command in an atomically committed batch."""

    dim_id: str
    delta: float
    source: str
    rationale: str
    applied: bool
    before_attractor: float | None
    after_attractor: float | None
    event_id: int | None = None
    error_code: str | None = None
    error_detail: str | None = None


@dataclass(frozen=True, slots=True)
class AttractorBatchReceipt:
    operation_id: str
    request_sha256: str
    field_tick: int
    results: tuple[AttractorBatchMoveResult, ...]
    deduplicated: bool


# ---------------------------------------------------------------------------
# Strict canonical JSON helpers
# ---------------------------------------------------------------------------


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise ValueError(f"duplicate JSON key: {key!r}")
        seen.add(key)
    return dict(pairs)


def _reject_non_finite_constant(value: str) -> float:
    raise ValueError(f"non-finite JSON constant rejected: {value}")


def _strict_json_dumps(obj: object) -> str:
    """Serialize to compact, UTF-8, order-preserving JSON; reject NaN/Infinity."""
    return json.dumps(
        obj,
        separators=(",", ":"),
        ensure_ascii=False,
        sort_keys=False,
        allow_nan=False,
    )


def _strict_json_loads(text: str) -> object:
    """Parse JSON; reject duplicate keys and NaN/Infinity constants."""
    return json.loads(
        text,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_non_finite_constant,
    )


def _canonical_json_text(obj: object) -> tuple[str, str]:
    """Prove ``obj`` is canonical JSON and return (text, sha256_hex).

    The object is dumped, parsed back, and re-dumped; the two text forms must
    be byte-identical to protect field order and signed zero.  Returns the
    canonical text and its SHA-256 hex digest.
    """
    text1 = _strict_json_dumps(obj)
    parsed = _strict_json_loads(text1)
    text2 = _strict_json_dumps(parsed)
    if text1 != text2:
        raise ValueError("canonical JSON round-trip text mismatch")
    digest = hashlib.sha256(text1.encode("utf-8")).hexdigest()
    return text1, digest


def _require_finite_float(value: float, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a real float, got {type(value).__name__}")
    if isinstance(value, int):
        raise ValueError(f"{field} must be a float, not an int")
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    return value


def _validate_canonical_capsule_primitive(
    primitive: dict,
    *,
    operation: str,
    db_path: str,
    stage: str,
) -> tuple[str, str, int]:
    """Validate a capsule primitive is canonical; return (json_text, sha256, field_tick).

    Verifies strict JSON byte-identical round-trip, then decodes via the frozen
    capsule codec and re-encodes.  The re-encoded compact JSON text must be
    byte-for-byte identical to the originally stored text — not just dict-equal.
    Returns the canonical JSON text, its SHA-256 hex digest, and the
    field_tick extracted from the primitive.
    """
    try:
        text, digest = _canonical_json_text(primitive)
    except ValueError as exc:
        raise FieldPersistenceError(
            "persistence_snapshot_not_canonical",
            operation,
            db_path,
            f"canonical JSON validation failed: {exc}",
            stage=stage,
        ) from exc

    try:
        parsed = _strict_json_loads(text)
    except ValueError as exc:
        raise FieldPersistenceError(
            "persistence_snapshot_not_canonical",
            operation,
            db_path,
            f"strict JSON parse failed: {exc}",
            stage=stage,
        ) from exc

    try:
        capsule = decode_field_state_capsule(parsed)
    except FieldStateCapsuleError as exc:
        raise FieldPersistenceError(
            "persistence_snapshot_not_canonical",
            operation,
            db_path,
            f"capsule decode rejected: {exc.code}: {exc.detail}",
            stage=stage,
        ) from exc

    try:
        re_encoded = encode_field_state_capsule(capsule)
    except FieldStateCapsuleError as exc:
        raise FieldPersistenceError(
            "persistence_snapshot_not_canonical",
            operation,
            db_path,
            f"capsule re-encode rejected: {exc.code}: {exc.detail}",
            stage=stage,
        ) from exc

    if re_encoded != parsed:
        raise FieldPersistenceError(
            "persistence_snapshot_not_canonical",
            operation,
            db_path,
            "re-encoded primitive does not equal input primitive",
            stage=stage,
        )

    # Byte-for-byte text identity: re-encode must produce the exact text
    re_text = _strict_json_dumps(re_encoded)
    if re_text != text:
        raise FieldPersistenceError(
            "persistence_snapshot_not_canonical",
            operation,
            db_path,
            "re-encoded canonical text differs byte-for-byte from stored text "
            "(field order, whitespace, or escape non-canonical)",
            stage=stage,
        )

    field_tick = parsed["field_tick"]
    if not isinstance(field_tick, int) or isinstance(field_tick, bool):
        raise FieldPersistenceError(
            "persistence_snapshot_not_canonical",
            operation,
            db_path,
            "field_tick must be an int",
            stage=stage,
        )
    return text, digest, field_tick


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class FieldPersistenceStore:
    """Single-owner SQLite persistence store for field dynamics.

    The store owns one SQLite connection used by one thread.  It validates
    durability pragmas, runs integrity checks on every open, bootstraps schema
    v1 only when safe, and provides a narrow write/read interface for
    snapshots, tick events, attractor events, and per-dimension trajectory
    rows.  All writes are synchronous, explicit-transaction, and verified by
    in-transaction read-back before commit.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._closed = False
        try:
            self._conn = sqlite3.connect(db_path, isolation_level=None)
        except Exception as exc:
            self._closed = True
            raise FieldPersistenceError(
                "persistence_connect_failed",
                "open",
                db_path,
                f"sqlite3.connect failed: {exc}",
                stage="open",
            ) from exc
        try:
            self._apply_and_verify_pragmas()
            self._run_integrity_checks()
        except FieldPersistenceError:
            self._conn.close()
            self._closed = True
            raise
        except sqlite3.DatabaseError as exc:
            self._conn.close()
            self._closed = True
            raise FieldPersistenceError(
                "persistence_not_a_database",
                "open",
                db_path,
                f"sqlite database error: {exc}",
                stage="open",
            ) from exc

    # -- connection / pragmas / integrity ---------------------------------

    def _apply_and_verify_pragmas(self) -> None:
        operation = "pragma"
        db_path = self._db_path

        row = self._conn.execute("PRAGMA journal_mode=WAL").fetchone()
        if row is None or str(row[0]).lower() != "wal":
            raise FieldPersistenceError(
                "persistence_pragma_not_effective",
                operation,
                db_path,
                f"journal_mode=WAL not effective (got {row[0] if row else None!r})",
                stage="pragma.journal_mode",
            )

        self._conn.execute("PRAGMA synchronous=FULL")
        row = self._conn.execute("PRAGMA synchronous").fetchone()
        if row is None or int(row[0]) != 2:
            raise FieldPersistenceError(
                "persistence_pragma_not_effective",
                operation,
                db_path,
                f"synchronous=FULL not effective (got {row[0] if row else None!r})",
                stage="pragma.synchronous",
            )

        self._conn.execute("PRAGMA foreign_keys=ON")
        row = self._conn.execute("PRAGMA foreign_keys").fetchone()
        if row is None or int(row[0]) != 1:
            raise FieldPersistenceError(
                "persistence_pragma_not_effective",
                operation,
                db_path,
                f"foreign_keys=ON not effective (got {row[0] if row else None!r})",
                stage="pragma.foreign_keys",
            )

        self._conn.execute("PRAGMA busy_timeout=5000")
        row = self._conn.execute("PRAGMA busy_timeout").fetchone()
        if row is None or int(row[0]) != 5000:
            raise FieldPersistenceError(
                "persistence_pragma_not_effective",
                operation,
                db_path,
                f"busy_timeout=5000 not effective (got {row[0] if row else None!r})",
                stage="pragma.busy_timeout",
            )

    def _run_integrity_checks(self) -> None:
        operation = "integrity"
        db_path = self._db_path
        row = self._conn.execute("PRAGMA quick_check").fetchone()
        if row is None or str(row[0]).lower() != "ok":
            raise FieldPersistenceError(
                "persistence_database_corrupt",
                operation,
                db_path,
                f"quick_check failed: {row[0] if row else None!r}",
                stage="integrity.quick_check",
            )
        fk_rows = self._conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_rows:
            raise FieldPersistenceError(
                "persistence_foreign_key_violation",
                operation,
                db_path,
                f"foreign_key_check returned {len(fk_rows)} violation(s)",
                stage="integrity.foreign_key_check",
            )

    # -- schema -----------------------------------------------------------

    # Expected full schema objects for fail-closed verification.
    _EXPECTED_TABLE_COLUMNS: dict[str, tuple[tuple[str, str, bool, bool], ...]] = {
        "chatbox_meta": (
            ("key", "TEXT", True, True),
            ("value", "TEXT", True, False),
        ),
        "field_snapshots": (
            ("snapshot_id", "INTEGER", True, True),
            ("field_tick", "INTEGER", True, False),
            ("utc_unix_ns", "INTEGER", True, False),
            ("capsule_json", "TEXT", True, False),
            ("capsule_sha256", "TEXT", True, False),
        ),
        "field_events": (
            ("event_id", "INTEGER", True, True),
            ("boot_id", "TEXT", True, False),
            ("event_kind", "TEXT", True, False),
            ("before_field_tick", "INTEGER", True, False),
            ("after_field_tick", "INTEGER", True, False),
            ("utc_unix_ns", "INTEGER", True, False),
            ("payload_json", "TEXT", True, False),
            ("payload_sha256", "TEXT", True, False),
        ),
        "trajectory_points": (
            ("trajectory_id", "INTEGER", True, True),
            ("event_id", "INTEGER", True, False),
            ("field_tick", "INTEGER", True, False),
            ("dimension_ordinal", "INTEGER", True, False),
            ("dim_id", "TEXT", True, False),
            ("after_value", "REAL", True, False),
            ("after_velocity", "REAL", True, False),
            ("after_attractor", "REAL", True, False),
            ("after_slow_baseline", "REAL", True, False),
            ("after_ou_acceleration", "REAL", True, False),
        ),
        "field_operation_receipts": (
            ("operation_id", "TEXT", True, True),
            ("request_sha256", "TEXT", True, False),
            ("receipt_json", "TEXT", True, False),
            ("receipt_sha256", "TEXT", True, False),
        ),
    }

    _LEGACY_TABLE_COLUMNS = {
        key: value
        for key, value in _EXPECTED_TABLE_COLUMNS.items()
        if key != "field_operation_receipts"
    }

    _EXPECTED_INDEXES: tuple[str, ...] = (
        "idx_trajectory_event_order",
        "idx_trajectory_dim_event",
        "idx_events_field_tick",
        "idx_trajectory_field_tick_event",
        "idx_field_receipts_request_sha256",
    )

    # Expected index semantics: name -> (table, columns)
    _EXPECTED_INDEX_SPECS: dict[str, tuple[str, tuple[str, ...]]] = {
        "idx_trajectory_event_order": ("trajectory_points", ("event_id", "trajectory_id")),
        "idx_trajectory_dim_event": ("trajectory_points", ("dim_id", "event_id")),
        "idx_events_field_tick": ("field_events", ("after_field_tick", "event_id")),
        "idx_trajectory_field_tick_event": ("trajectory_points", ("field_tick", "event_id")),
        "idx_field_receipts_request_sha256": ("field_operation_receipts", ("request_sha256",)),
    }

    _LEGACY_INDEX_SPECS = {
        key: value
        for key, value in _EXPECTED_INDEX_SPECS.items()
        if key != "idx_field_receipts_request_sha256"
    }

    _EXPECTED_TRIGGERS: tuple[str, ...] = (
        "trg_no_update_field_events",
        "trg_no_delete_field_events",
        "trg_no_update_trajectory_points",
        "trg_no_delete_trajectory_points",
        "trg_no_update_field_operation_receipts",
        "trg_no_delete_field_operation_receipts",
    )

    # Expected trigger semantics: name -> (timing, operation, table, must_contain)
    _EXPECTED_TRIGGER_SPECS: dict[str, tuple[str, str, str, str]] = {
        "trg_no_update_field_events": ("BEFORE", "UPDATE", "field_events", "append-only"),
        "trg_no_delete_field_events": ("BEFORE", "DELETE", "field_events", "append-only"),
        "trg_no_update_trajectory_points": ("BEFORE", "UPDATE", "trajectory_points", "append-only"),
        "trg_no_delete_trajectory_points": ("BEFORE", "DELETE", "trajectory_points", "append-only"),
        "trg_no_update_field_operation_receipts": ("BEFORE", "UPDATE", "field_operation_receipts", "append-only"),
        "trg_no_delete_field_operation_receipts": ("BEFORE", "DELETE", "field_operation_receipts", "append-only"),
    }

    _LEGACY_TRIGGER_SPECS = {
        key: value
        for key, value in _EXPECTED_TRIGGER_SPECS.items()
        if "field_operation_receipts" not in key
    }

    _RECEIPT_DDL = (
        """CREATE TABLE field_operation_receipts (
            operation_id TEXT PRIMARY KEY,
            request_sha256 TEXT NOT NULL,
            receipt_json TEXT NOT NULL,
            receipt_sha256 TEXT NOT NULL
        )""",
        """CREATE INDEX idx_field_receipts_request_sha256
            ON field_operation_receipts(request_sha256)""",
        """CREATE TRIGGER trg_no_update_field_operation_receipts
            BEFORE UPDATE ON field_operation_receipts
        BEGIN
            SELECT RAISE(ABORT, 'field_operation_receipts is append-only');
        END""",
        """CREATE TRIGGER trg_no_delete_field_operation_receipts
            BEFORE DELETE ON field_operation_receipts
        BEGIN
            SELECT RAISE(ABORT, 'field_operation_receipts is append-only');
        END""",
    )

    _BOOTSTRAP_DDL = (
        # Tables
        """CREATE TABLE chatbox_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""",
        """CREATE TABLE field_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            field_tick INTEGER NOT NULL,
            utc_unix_ns INTEGER NOT NULL,
            capsule_json TEXT NOT NULL,
            capsule_sha256 TEXT NOT NULL
        )""",
        """CREATE TABLE field_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            boot_id TEXT NOT NULL,
            event_kind TEXT NOT NULL,
            before_field_tick INTEGER NOT NULL,
            after_field_tick INTEGER NOT NULL,
            utc_unix_ns INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL
        )""",
        """CREATE TABLE trajectory_points (
            trajectory_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            field_tick INTEGER NOT NULL,
            dimension_ordinal INTEGER NOT NULL,
            dim_id TEXT NOT NULL,
            after_value REAL NOT NULL,
            after_velocity REAL NOT NULL,
            after_attractor REAL NOT NULL,
            after_slow_baseline REAL NOT NULL,
            after_ou_acceleration REAL NOT NULL,
            FOREIGN KEY (event_id) REFERENCES field_events(event_id)
        )""",
        *_RECEIPT_DDL,
        # Indexes
        """CREATE INDEX idx_trajectory_event_order
            ON trajectory_points(event_id, trajectory_id)""",
        """CREATE INDEX idx_trajectory_dim_event
            ON trajectory_points(dim_id, event_id)""",
        """CREATE INDEX idx_events_field_tick
            ON field_events(after_field_tick, event_id)""",
        """CREATE INDEX idx_trajectory_field_tick_event
            ON trajectory_points(field_tick, event_id)""",
        # Triggers (append-only enforcement)
        """CREATE TRIGGER trg_no_update_field_events
            BEFORE UPDATE ON field_events
        BEGIN
            SELECT RAISE(ABORT, 'field_events is append-only');
        END""",
        """CREATE TRIGGER trg_no_delete_field_events
            BEFORE DELETE ON field_events
        BEGIN
            SELECT RAISE(ABORT, 'field_events is append-only');
        END""",
        """CREATE TRIGGER trg_no_update_trajectory_points
            BEFORE UPDATE ON trajectory_points
        BEGIN
            SELECT RAISE(ABORT, 'trajectory_points is append-only');
        END""",
        """CREATE TRIGGER trg_no_delete_trajectory_points
            BEFORE DELETE ON trajectory_points
        BEGIN
            SELECT RAISE(ABORT, 'trajectory_points is append-only');
        END""",
    )

    def ensure_schema(self) -> None:
        """Create schema v2, migrate an exact v1 DB, or verify an existing v2 DB.

        Only allows bootstrap when NO user objects (tables/triggers/indexes)
        exist.  An existing database must have the exact expected set of
        tables, columns (name/order/PK/notnull), triggers, and indexes;
        any unknown, extra, or missing object is fail-closed.
        """
        operation = "schema"
        db_path = self._db_path

        # Count ALL user objects (non sqlite_)
        row = self._conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type IN ('table','index','trigger') "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchone()
        user_objects = int(row[0])

        if user_objects == 0:
            self._bootstrap_schema()
            return

        version_row = self._conn.execute("PRAGMA user_version").fetchone()
        user_version = int(version_row[0]) if version_row is not None else -1
        if user_version == _LEGACY_PERSISTENCE_USER_VERSION:
            self._verify_schema_objects(
                expected_columns=self._LEGACY_TABLE_COLUMNS,
                expected_indexes=self._LEGACY_INDEX_SPECS,
                expected_triggers=self._LEGACY_TRIGGER_SPECS,
            )
            self._verify_schema_version(
                expected_user_version=_LEGACY_PERSISTENCE_USER_VERSION,
                expected_schema_version=_LEGACY_PERSISTENCE_SCHEMA_VERSION,
            )
            self._check_no_events_without_snapshots(include_receipts=False)
            self._migrate_v1_to_v2()
        else:
            self._verify_schema_objects()
            self._verify_schema_version()
        self._check_no_events_without_snapshots()

    def _migrate_v1_to_v2(self) -> None:
        """Upgrade an already-proven exact v1 schema in one transaction."""
        try:
            self._conn.execute("BEGIN")
            for ddl in self._RECEIPT_DDL:
                self._conn.execute(ddl)
            self._conn.execute(
                "UPDATE chatbox_meta SET value=? WHERE key='schema_version'",
                (PERSISTENCE_SCHEMA_VERSION,),
            )
            self._conn.execute(f"PRAGMA user_version={PERSISTENCE_USER_VERSION}")
            self._conn.execute("COMMIT")
        except sqlite3.DatabaseError as exc:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.DatabaseError:
                pass
            raise FieldPersistenceError(
                "persistence_schema_migration_failed",
                "schema",
                self._db_path,
                f"v1 to v2 migration failed: {exc}",
                stage="schema.migrate_v1_v2",
            ) from exc
        self._verify_schema_objects()
        self._verify_schema_version()

    def _bootstrap_schema(self) -> None:
        db_path = self._db_path
        operation = "schema"
        # All DDL, PRAGMA user_version, and meta insert in ONE explicit
        # transaction.  No executescript (which auto-commits).
        try:
            self._conn.execute("BEGIN")
            for ddl in self._BOOTSTRAP_DDL:
                self._conn.execute(ddl)
            self._conn.execute(
                f"PRAGMA user_version = {PERSISTENCE_USER_VERSION}"
            )
            self._conn.execute(
                "INSERT INTO chatbox_meta (key, value) VALUES (?, ?)",
                ("schema_version", PERSISTENCE_SCHEMA_VERSION),
            )
            self._conn.execute("COMMIT")
        except sqlite3.DatabaseError as exc:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.DatabaseError:
                pass
            raise FieldPersistenceError(
                "persistence_bootstrap_failed",
                operation,
                db_path,
                f"bootstrap transaction failed: {exc}",
                stage="schema.bootstrap",
            ) from exc

        # Verify bootstrap produced the correct objects in one commit.
        self._verify_schema_objects()
        self._verify_schema_version()

    def _verify_schema_objects(
        self,
        *,
        expected_columns: dict[str, tuple[tuple[str, str, bool, bool], ...]] | None = None,
        expected_indexes: dict[str, tuple[str, tuple[str, ...]]] | None = None,
        expected_triggers: dict[str, tuple[str, str, str, str]] | None = None,
    ) -> None:
        """Fail-closed: demand exact tables, columns, indexes, triggers."""
        db_path = self._db_path
        operation = "schema"

        # -- tables ----------------------------------------------------------
        tables = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        actual_table_names = {row[0] for row in tables}
        columns_contract = expected_columns or self._EXPECTED_TABLE_COLUMNS
        index_contract = expected_indexes or self._EXPECTED_INDEX_SPECS
        trigger_contract = expected_triggers or self._EXPECTED_TRIGGER_SPECS
        expected_table_names = set(columns_contract.keys())

        if actual_table_names != expected_table_names:
            extra = sorted(actual_table_names - expected_table_names)
            missing = sorted(expected_table_names - actual_table_names)
            parts = []
            if extra:
                parts.append(f"extra tables: {extra}")
            if missing:
                parts.append(f"missing tables: {missing}")
            raise FieldPersistenceError(
                "persistence_schema_mismatch",
                operation,
                db_path,
                "; ".join(parts),
                stage="schema.verify.tables",
            )

        for table_name in sorted(expected_table_names):
            cols = self._conn.execute(
                f"PRAGMA table_info('{table_name}')"
            ).fetchall()
            expected = columns_contract[table_name]
            if len(cols) != len(expected):
                raise FieldPersistenceError(
                    "persistence_schema_mismatch",
                    operation,
                    db_path,
                    f"table {table_name}: expected {len(expected)} columns, "
                    f"got {len(cols)}",
                    stage="schema.verify.columns",
                )
            for idx, (col_info, (exp_name, exp_type, exp_notnull, exp_pk)) in enumerate(
                zip(cols, expected)
            ):
                cid, cname, ctype, cnotnull, cdefault, cpk = col_info
                if str(cname) != exp_name:
                    raise FieldPersistenceError(
                        "persistence_schema_mismatch",
                        operation,
                        db_path,
                        f"table {table_name} col {idx}: expected name "
                        f"{exp_name!r}, got {str(cname)!r}",
                        stage="schema.verify.columns",
                    )
                actual_type = str(ctype).upper()
                if actual_type != exp_type:
                    raise FieldPersistenceError(
                        "persistence_schema_mismatch",
                        operation,
                        db_path,
                        f"table {table_name} col {exp_name}: expected type "
                        f"{exp_type!r}, got {actual_type!r}",
                        stage="schema.verify.columns",
                    )
                # PRIMARY KEY columns in SQLite have implicit NOT NULL;
                # PRAGMA table_info may report notnull=0 for them.
                # Accept cnotnull=0 when cpk=1.
                is_pk = bool(cpk)
                is_explicit_notnull = bool(cnotnull)
                effective_notnull = is_pk or is_explicit_notnull
                if effective_notnull != exp_notnull:
                    raise FieldPersistenceError(
                        "persistence_schema_mismatch",
                        operation,
                        db_path,
                        f"table {table_name} col {exp_name}: expected "
                        f"NOT NULL={exp_notnull}, got effective={effective_notnull} "
                        f"(pk={is_pk}, explicit_notnull={is_explicit_notnull})",
                        stage="schema.verify.columns",
                    )
                if bool(cpk) != exp_pk:
                    raise FieldPersistenceError(
                        "persistence_schema_mismatch",
                        operation,
                        db_path,
                        f"table {table_name} col {exp_name}: expected "
                        f"PK={exp_pk}, got {bool(cpk)}",
                        stage="schema.verify.columns",
                    )

        # -- indexes (semantic) ----------------------------------------------
        indexes = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        actual_index_names = {row[0] for row in indexes}
        expected_index_set = set(index_contract)

        if actual_index_names != expected_index_set:
            extra = sorted(actual_index_names - expected_index_set)
            missing = sorted(expected_index_set - actual_index_names)
            parts = []
            if extra:
                parts.append(f"extra indexes: {extra}")
            if missing:
                parts.append(f"missing indexes: {missing}")
            raise FieldPersistenceError(
                "persistence_schema_mismatch",
                operation,
                db_path,
                "; ".join(parts),
                stage="schema.verify.indexes",
            )

        for idx_name in sorted(expected_index_set):
            expected_table, expected_cols = index_contract[idx_name]
            info_rows = self._conn.execute(
                f"PRAGMA index_info('{idx_name}')"
            ).fetchall()
            if not info_rows:
                raise FieldPersistenceError(
                    "persistence_schema_mismatch",
                    operation,
                    db_path,
                    f"PRAGMA index_info('{idx_name}') returned no rows",
                    stage="schema.verify.index_info",
                )
            # PRAGMA index_info returns (rank, cid, col_name)
            actual_cols = tuple(str(info[2]) for info in info_rows)
            if actual_cols != expected_cols:
                raise FieldPersistenceError(
                    "persistence_schema_mismatch",
                    operation,
                    db_path,
                    f"index {idx_name}: expected columns {expected_cols}, "
                    f"got {actual_cols}",
                    stage="schema.verify.index_info",
                )
            # Verify the index is actually on the expected table
            tbl_rows = self._conn.execute(
                "SELECT tbl_name FROM sqlite_master WHERE name = ? AND type = 'index'",
                (idx_name,),
            ).fetchone()
            if tbl_rows is None or tbl_rows[0] != expected_table:
                raise FieldPersistenceError(
                    "persistence_schema_mismatch",
                    operation,
                    db_path,
                    f"index {idx_name}: expected table {expected_table!r}, "
                    f"got {tbl_rows[0] if tbl_rows else None!r}",
                    stage="schema.verify.index_info",
                )

        # -- triggers (semantic) ---------------------------------------------
        triggers = self._conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        actual_trigger_names = {row[0] for row in triggers}
        expected_trigger_set = set(trigger_contract)

        if actual_trigger_names != expected_trigger_set:
            extra = sorted(actual_trigger_names - expected_trigger_set)
            missing = sorted(expected_trigger_set - actual_trigger_names)
            parts = []
            if extra:
                parts.append(f"extra triggers: {extra}")
            if missing:
                parts.append(f"missing triggers: {missing}")
            raise FieldPersistenceError(
                "persistence_schema_mismatch",
                operation,
                db_path,
                "; ".join(parts),
                stage="schema.verify.triggers",
            )

        for trig_name, trig_sql in triggers:
            if trig_name not in expected_trigger_set:
                continue
            if not trig_sql:
                raise FieldPersistenceError(
                    "persistence_schema_mismatch",
                    operation,
                    db_path,
                    f"trigger {trig_name}: sql is null/empty",
                    stage="schema.verify.trigger_sql",
                )
            norm_sql = " ".join(str(trig_sql).upper().split())
            exp_timing, exp_op, exp_table, exp_phrase = trigger_contract[trig_name]
            exp_norm_phrase = exp_phrase.upper()
            if exp_timing.upper() not in norm_sql:
                raise FieldPersistenceError(
                    "persistence_schema_mismatch",
                    operation,
                    db_path,
                    f"trigger {trig_name}: missing timing {exp_timing}",
                    stage="schema.verify.trigger_sql",
                )
            if exp_op.upper() not in norm_sql:
                raise FieldPersistenceError(
                    "persistence_schema_mismatch",
                    operation,
                    db_path,
                    f"trigger {trig_name}: missing operation {exp_op}",
                    stage="schema.verify.trigger_sql",
                )
            if exp_table.upper() not in norm_sql:
                raise FieldPersistenceError(
                    "persistence_schema_mismatch",
                    operation,
                    db_path,
                    f"trigger {trig_name}: missing target table {exp_table}",
                    stage="schema.verify.trigger_sql",
                )
            if exp_norm_phrase not in norm_sql:
                raise FieldPersistenceError(
                    "persistence_schema_mismatch",
                    operation,
                    db_path,
                    f"trigger {trig_name}: missing phrase {exp_phrase!r}",
                    stage="schema.verify.trigger_sql",
                )
            if "RAISE(ABORT" not in norm_sql and "RAISE (ABORT" not in norm_sql:
                raise FieldPersistenceError(
                    "persistence_schema_mismatch",
                    operation,
                    db_path,
                    f"trigger {trig_name}: missing RAISE(ABORT, ...)",
                    stage="schema.verify.trigger_sql",
                )

        # -- foreign keys ----------------------------------------------------
        fk_rows = self._conn.execute(
            "PRAGMA foreign_key_list('trajectory_points')"
        ).fetchall()
        if not fk_rows:
            raise FieldPersistenceError(
                "persistence_schema_mismatch",
                operation,
                db_path,
                "trajectory_points: missing foreign key on event_id",
                stage="schema.verify.foreign_key",
            )
        found_fk = False
        for fk in fk_rows:
            # PRAGMA foreign_key_list: (id, seq, table, from, to, on_update, on_delete, match)
            fk_table = str(fk[2])
            fk_from = str(fk[3])
            fk_to = str(fk[4])
            fk_on_update = str(fk[5])
            fk_on_delete = str(fk[6])
            if fk_table == "field_events" and fk_from == "event_id" and fk_to == "event_id":
                if fk_on_update != "NO ACTION" or fk_on_delete != "NO ACTION":
                    raise FieldPersistenceError(
                        "persistence_schema_mismatch",
                        operation,
                        db_path,
                        f"trajectory_points FK to field_events: expected "
                        f"NO ACTION on update/delete, got "
                        f"on_update={fk_on_update!r} on_delete={fk_on_delete!r}",
                        stage="schema.verify.foreign_key",
                    )
                found_fk = True
                break
        if not found_fk:
            raise FieldPersistenceError(
                "persistence_schema_mismatch",
                operation,
                db_path,
                "trajectory_points: foreign key on event_id must reference "
                "field_events(event_id)",
                stage="schema.verify.foreign_key",
            )

    def _verify_schema_version(
        self,
        *,
        expected_user_version: int = PERSISTENCE_USER_VERSION,
        expected_schema_version: str = PERSISTENCE_SCHEMA_VERSION,
    ) -> None:
        db_path = self._db_path
        row = self._conn.execute("PRAGMA user_version").fetchone()
        if row is None or int(row[0]) != expected_user_version:
            raise FieldPersistenceError(
                "persistence_schema_version_mismatch",
                "schema",
                db_path,
                f"user_version expected {expected_user_version}, "
                f"got {row[0] if row else None!r}",
                stage="schema.verify.user_version",
            )
        row = self._conn.execute(
            "SELECT value FROM chatbox_meta WHERE key = ?",
            ("schema_version",),
        ).fetchone()
        if row is None or row[0] != expected_schema_version:
            raise FieldPersistenceError(
                "persistence_schema_version_mismatch",
                "schema",
                db_path,
                f"meta schema_version expected {expected_schema_version!r}, "
                f"got {row[0] if row else None!r}",
                stage="schema.verify.meta",
            )

    def _check_no_events_without_snapshots(
        self, *, include_receipts: bool = True
    ) -> None:
        db_path = self._db_path
        snap = self._conn.execute(
            "SELECT COUNT(*) FROM field_snapshots"
        ).fetchone()[0]
        ev = self._conn.execute(
            "SELECT COUNT(*) FROM field_events"
        ).fetchone()[0]
        traj = self._conn.execute(
            "SELECT COUNT(*) FROM trajectory_points"
        ).fetchone()[0]
        receipts = (
            self._conn.execute(
                "SELECT COUNT(*) FROM field_operation_receipts"
            ).fetchone()[0]
            if include_receipts
            else 0
        )
        if snap == 0 and (ev > 0 or traj > 0 or receipts > 0):
            raise FieldPersistenceError(
                "persistence_events_without_snapshots",
                "schema",
                db_path,
                f"found {ev} event(s), {traj} trajectory row(s), and "
                f"{receipts} receipt(s) but no snapshots",
                stage="schema.verify.consistency",
            )

    def is_empty(self) -> bool:
        """True when all durable field-state tables are empty."""
        snap = self._conn.execute(
            "SELECT COUNT(*) FROM field_snapshots"
        ).fetchone()[0]
        ev = self._conn.execute(
            "SELECT COUNT(*) FROM field_events"
        ).fetchone()[0]
        traj = self._conn.execute(
            "SELECT COUNT(*) FROM trajectory_points"
        ).fetchone()[0]
        receipts = self._conn.execute(
            "SELECT COUNT(*) FROM field_operation_receipts"
        ).fetchone()[0]
        return (
            int(snap) == 0 and int(ev) == 0 and int(traj) == 0
            and int(receipts) == 0
        )

    # -- snapshots --------------------------------------------------------

    def write_snapshot(
        self,
        capsule_primitive: dict,
        *,
        utc_unix_ns: int,
    ) -> int:
        """Persist a canonical capsule snapshot with read-back and retention.

        The caller must pass ``encode_field_state_capsule()``'s primitive.
        The store validates canonical JSON + codec round-trip, inserts the row,
        reads it back within the same transaction, verifies metadata/text/digest
        and canonical form, deletes older snapshots to keep the latest
        ``SNAPSHOT_RETENTION_COUNT``, then commits.  Returns the snapshot id.
        """
        operation = "snapshot_write"
        db_path = self._db_path
        if not isinstance(capsule_primitive, dict):
            raise FieldPersistenceError(
                "persistence_snapshot_not_canonical",
                operation,
                db_path,
                "capsule primitive must be a dict",
                stage="snapshot_write.input",
            )
        if not isinstance(utc_unix_ns, int) or isinstance(utc_unix_ns, bool):
            raise FieldPersistenceError(
                "persistence_snapshot_not_canonical",
                operation,
                db_path,
                "utc_unix_ns must be an int",
                stage="snapshot_write.input",
            )

        text, digest, field_tick = _validate_canonical_capsule_primitive(
            capsule_primitive,
            operation=operation,
            db_path=db_path,
            stage="snapshot_write.canonical",
        )

        self._conn.execute("BEGIN")
        try:
            cur = self._conn.execute(
                "INSERT INTO field_snapshots "
                "(field_tick, utc_unix_ns, capsule_json, capsule_sha256) "
                "VALUES (?, ?, ?, ?)",
                (field_tick, utc_unix_ns, text, digest),
            )
            snapshot_id = int(cur.lastrowid)

            row = self._conn.execute(
                "SELECT snapshot_id, field_tick, utc_unix_ns, capsule_json, "
                "capsule_sha256 FROM field_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
            if row is None:
                raise FieldPersistenceError(
                    "persistence_snapshot_readback_mismatch",
                    operation,
                    db_path,
                    "read-back row missing",
                    stage="snapshot_write.readback",
                    field_tick=field_tick,
                )
            r_id, r_tick, r_utc, r_json, r_digest = row
            if int(r_id) != snapshot_id:
                raise FieldPersistenceError(
                    "persistence_snapshot_readback_mismatch",
                    operation,
                    db_path,
                    f"read-back snapshot_id mismatch: {r_id} != {snapshot_id}",
                    stage="snapshot_write.readback",
                    field_tick=field_tick,
                )
            if int(r_tick) != field_tick:
                raise FieldPersistenceError(
                    "persistence_snapshot_readback_mismatch",
                    operation,
                    db_path,
                    f"read-back field_tick mismatch: {r_tick} != {field_tick}",
                    stage="snapshot_write.readback",
                    field_tick=field_tick,
                )
            if int(r_utc) != utc_unix_ns:
                raise FieldPersistenceError(
                    "persistence_snapshot_readback_mismatch",
                    operation,
                    db_path,
                    f"read-back utc_unix_ns mismatch: {r_utc} != {utc_unix_ns}",
                    stage="snapshot_write.readback",
                    field_tick=field_tick,
                )
            if r_json != text:
                raise FieldPersistenceError(
                    "persistence_snapshot_readback_mismatch",
                    operation,
                    db_path,
                    "read-back capsule_json text mismatch",
                    stage="snapshot_write.readback",
                    field_tick=field_tick,
                )
            if r_digest != digest:
                raise FieldPersistenceError(
                    "persistence_snapshot_digest_mismatch",
                    operation,
                    db_path,
                    f"read-back digest mismatch: {r_digest} != {digest}",
                    stage="snapshot_write.readback",
                    field_tick=field_tick,
                )

            try:
                parsed_back = _strict_json_loads(r_json)
                capsule_back = decode_field_state_capsule(parsed_back)
                re_encoded = encode_field_state_capsule(capsule_back)
                if re_encoded != parsed_back:
                    raise ValueError("re-encoded primitive differs from read-back")
            except FieldStateCapsuleError as exc:
                raise FieldPersistenceError(
                    "persistence_snapshot_not_canonical",
                    operation,
                    db_path,
                    f"read-back capsule decode rejected: {exc.code}: {exc.detail}",
                    stage="snapshot_write.readback",
                    field_tick=field_tick,
                ) from exc
            except ValueError as exc:
                raise FieldPersistenceError(
                    "persistence_snapshot_not_canonical",
                    operation,
                    db_path,
                    f"read-back canonical check failed: {exc}",
                    stage="snapshot_write.readback",
                    field_tick=field_tick,
                ) from exc

            self._conn.execute(
                "DELETE FROM field_snapshots WHERE snapshot_id NOT IN "
                "(SELECT snapshot_id FROM field_snapshots "
                "ORDER BY snapshot_id DESC LIMIT ?)",
                (SNAPSHOT_RETENTION_COUNT,),
            )
            self._conn.execute("COMMIT")
        except FieldPersistenceError:
            self._conn.execute("ROLLBACK")
            raise
        except sqlite3.DatabaseError as exc:
            self._conn.execute("ROLLBACK")
            raise FieldPersistenceError(
                "persistence_snapshot_write_failed",
                operation,
                db_path,
                f"sqlite error: {exc}",
                stage="snapshot_write",
                field_tick=field_tick,
            ) from exc
        return snapshot_id

    def read_latest_snapshot(self) -> dict | None:
        """Read and fully verify the latest committed snapshot primitive.

        Returns ``None`` when no snapshot exists.  Otherwise verifies the
        SHA-256 digest, strict JSON, canonical codec round-trip, and that the
        re-encoded compact canonical text equals the stored text byte-for-byte.
        Any failure is fail-closed; the store never falls back to an older
        snapshot.
        """
        operation = "snapshot_read"
        db_path = self._db_path
        row = self._conn.execute(
            "SELECT snapshot_id, field_tick, utc_unix_ns, capsule_json, "
            "capsule_sha256 FROM field_snapshots "
            "ORDER BY snapshot_id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        r_id, r_tick, r_utc, r_json, r_digest = row

        computed = hashlib.sha256(r_json.encode("utf-8")).hexdigest()
        if computed != r_digest:
            raise FieldPersistenceError(
                "persistence_snapshot_digest_mismatch",
                operation,
                db_path,
                f"stored digest {r_digest!r} != computed {computed!r}",
                stage="snapshot_read.digest",
            )

        try:
            parsed = _strict_json_loads(r_json)
        except ValueError as exc:
            raise FieldPersistenceError(
                "persistence_snapshot_not_canonical",
                operation,
                db_path,
                f"strict JSON parse failed: {exc}",
                stage="snapshot_read.json",
            ) from exc

        try:
            capsule = decode_field_state_capsule(parsed)
            re_encoded = encode_field_state_capsule(capsule)
            if re_encoded != parsed:
                raise ValueError("re-encoded primitive differs from stored")
            # Byte-for-byte text identity: re-encode must yield the exact stored text
            re_text = _strict_json_dumps(re_encoded)
            if re_text != r_json:
                raise ValueError(
                    "re-encoded canonical text differs byte-for-byte from "
                    "stored text (field order, whitespace, or escape non-canonical)"
                )
        except FieldStateCapsuleError as exc:
            raise FieldPersistenceError(
                "persistence_snapshot_not_canonical",
                operation,
                db_path,
                f"capsule decode rejected: {exc.code}: {exc.detail}",
                stage="snapshot_read.codec",
            ) from exc
        except ValueError as exc:
            raise FieldPersistenceError(
                "persistence_snapshot_not_canonical",
                operation,
                db_path,
                f"canonical codec check failed: {exc}",
                stage="snapshot_read.codec",
            ) from exc

        capsule_tick = parsed["field_tick"]
        if int(r_tick) != capsule_tick:
            raise FieldPersistenceError(
                "persistence_snapshot_tick_mismatch",
                operation,
                db_path,
                f"row field_tick {r_tick} != capsule field_tick {capsule_tick}",
                stage="snapshot_read.tick",
                field_tick=capsule_tick,
            )
        return parsed

    # -- events -----------------------------------------------------------

    def write_tick_event(
        self,
        *,
        boot_id: str,
        before_field_tick: int,
        after_field_tick: int,
        utc_unix_ns: int,
        trajectory_rows: Sequence[TrajectoryRowInput],
    ) -> int:
        """Atomically commit a tick event and all its per-dimension trajectory rows.

        The event payload is a versioned canonical JSON document.  The tick
        event and every trajectory row are inserted in one transaction,
        read back within that transaction, and committed only when all
        verifications pass.  Returns the event id.
        """
        operation = "tick_event"
        db_path = self._db_path
        self._require_event_inputs(
            boot_id=boot_id,
            before_field_tick=before_field_tick,
            after_field_tick=after_field_tick,
            utc_unix_ns=utc_unix_ns,
            operation=operation,
        )
        if after_field_tick != before_field_tick + 1:
            raise FieldPersistenceError(
                "persistence_event_not_canonical",
                operation,
                db_path,
                f"tick after_field_tick {after_field_tick} != before+1 "
                f"{before_field_tick + 1}",
                stage="tick_event.input",
                field_tick=after_field_tick,
            )

        payload = {
            "version": EVENT_PAYLOAD_VERSION,
            "kind": "tick",
            "before_tick": before_field_tick,
            "after_tick": after_field_tick,
        }
        text, digest = self._canonical_event_payload(
            payload, operation=operation, stage="tick_event.canonical"
        )

        rows_validated = self._validate_trajectory_rows(
            trajectory_rows,
            operation=operation,
            stage="tick_event.trajectory",
            field_tick=after_field_tick,
        )

        self._conn.execute("BEGIN")
        try:
            cur = self._conn.execute(
                "INSERT INTO field_events "
                "(boot_id, event_kind, before_field_tick, after_field_tick, "
                "utc_unix_ns, payload_json, payload_sha256) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    boot_id,
                    "tick",
                    before_field_tick,
                    after_field_tick,
                    utc_unix_ns,
                    text,
                    digest,
                ),
            )
            event_id = int(cur.lastrowid)

            for row_input in rows_validated:
                self._conn.execute(
                    "INSERT INTO trajectory_points "
                    "(event_id, field_tick, dimension_ordinal, dim_id, "
                    "after_value, after_velocity, after_attractor, "
                    "after_slow_baseline, after_ou_acceleration) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event_id,
                        after_field_tick,
                        row_input.dimension_ordinal,
                        row_input.dim_id,
                        row_input.after_value,
                        row_input.after_velocity,
                        row_input.after_attractor,
                        row_input.after_slow_baseline,
                        row_input.after_ou_acceleration,
                    ),
                )

            self._readback_event(
                event_id=event_id,
                boot_id=boot_id,
                event_kind="tick",
                before_field_tick=before_field_tick,
                after_field_tick=after_field_tick,
                utc_unix_ns=utc_unix_ns,
                text=text,
                digest=digest,
                operation=operation,
                stage="tick_event.readback",
            )
            self._readback_trajectory(
                event_id=event_id,
                field_tick=after_field_tick,
                rows=rows_validated,
                operation=operation,
                stage="tick_event.readback.trajectory",
            )
            self._conn.execute("COMMIT")
        except FieldPersistenceError:
            self._conn.execute("ROLLBACK")
            raise
        except sqlite3.DatabaseError as exc:
            self._conn.execute("ROLLBACK")
            raise FieldPersistenceError(
                "persistence_event_write_failed",
                operation,
                db_path,
                f"sqlite error: {exc}",
                stage="tick_event",
                field_tick=after_field_tick,
            ) from exc
        return event_id

    def write_attractor_event(
        self,
        *,
        boot_id: str,
        field_tick: int,
        utc_unix_ns: int,
        dim_id: str,
        delta: float,
        source: str,
        rationale: str,
        before_attractor: float,
        after_attractor: float,
    ) -> int:
        """Commit a successful attractor-move event.

        Only called for accepted moves; rejected moves are not persisted and
        keep the P1.1 atomic rejection semantics.  The payload records the
        command, source, rationale, before/after attractor, and field tick.
        Returns the event id.
        """
        operation = "attractor_event"
        db_path = self._db_path
        self._require_event_inputs(
            boot_id=boot_id,
            before_field_tick=field_tick,
            after_field_tick=field_tick,
            utc_unix_ns=utc_unix_ns,
            operation=operation,
        )
        if not isinstance(dim_id, str) or not dim_id:
            raise FieldPersistenceError(
                "persistence_event_not_canonical",
                operation,
                db_path,
                "dim_id must be a non-empty str",
                stage="attractor_event.input",
                field_tick=field_tick,
            )
        if not isinstance(source, str) or not source.strip():
            raise FieldPersistenceError(
                "persistence_event_not_canonical",
                operation,
                db_path,
                "source must be a non-empty str",
                stage="attractor_event.input",
                field_tick=field_tick,
            )
        if not isinstance(rationale, str) or not rationale.strip():
            raise FieldPersistenceError(
                "persistence_event_not_canonical",
                operation,
                db_path,
                "rationale must be a non-empty str",
                stage="attractor_event.input",
                field_tick=field_tick,
            )
        delta = _require_finite_float(delta, field="delta")
        before_attractor = _require_finite_float(
            before_attractor, field="before_attractor"
        )
        after_attractor = _require_finite_float(
            after_attractor, field="after_attractor"
        )

        payload = {
            "version": EVENT_PAYLOAD_VERSION,
            "kind": "attractor_move",
            "dim_id": dim_id,
            "delta": delta,
            "source": source,
            "rationale": rationale,
            "before_attractor": before_attractor,
            "after_attractor": after_attractor,
            "field_tick": field_tick,
        }
        text, digest = self._canonical_event_payload(
            payload, operation=operation, stage="attractor_event.canonical"
        )

        self._conn.execute("BEGIN")
        try:
            cur = self._conn.execute(
                "INSERT INTO field_events "
                "(boot_id, event_kind, before_field_tick, after_field_tick, "
                "utc_unix_ns, payload_json, payload_sha256) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    boot_id,
                    "attractor_move",
                    field_tick,
                    field_tick,
                    utc_unix_ns,
                    text,
                    digest,
                ),
            )
            event_id = int(cur.lastrowid)
            self._readback_event(
                event_id=event_id,
                boot_id=boot_id,
                event_kind="attractor_move",
                before_field_tick=field_tick,
                after_field_tick=field_tick,
                utc_unix_ns=utc_unix_ns,
                text=text,
                digest=digest,
                operation=operation,
                stage="attractor_event.readback",
            )
            self._conn.execute("COMMIT")
        except FieldPersistenceError:
            self._conn.execute("ROLLBACK")
            raise
        except sqlite3.DatabaseError as exc:
            self._conn.execute("ROLLBACK")
            raise FieldPersistenceError(
                "persistence_event_write_failed",
                operation,
                db_path,
                f"sqlite error: {exc}",
                stage="attractor_event",
                field_tick=field_tick,
            ) from exc
        return event_id

    # -- idempotent attractor batches ------------------------------------

    def _canonical_batch_request(
        self, moves: Sequence[AttractorBatchMoveInput], *, operation: str
    ) -> tuple[dict, str]:
        if isinstance(moves, (str, bytes)) or not isinstance(moves, Sequence):
            raise FieldPersistenceError(
                "persistence_batch_not_canonical", operation, self._db_path,
                "moves must be an ordered sequence", stage=f"{operation}.request",
            )
        encoded: list[dict] = []
        for index, move in enumerate(moves):
            if not isinstance(move, AttractorBatchMoveInput):
                raise FieldPersistenceError(
                    "persistence_batch_not_canonical", operation, self._db_path,
                    f"move {index} must be AttractorBatchMoveInput",
                    stage=f"{operation}.request",
                )
            if not isinstance(move.dim_id, str) or not move.dim_id:
                raise FieldPersistenceError(
                    "persistence_batch_not_canonical", operation, self._db_path,
                    f"move {index} dim_id must be non-empty",
                    stage=f"{operation}.request",
                )
            if not isinstance(move.source, str) or not move.source.strip():
                raise FieldPersistenceError(
                    "persistence_batch_not_canonical", operation, self._db_path,
                    f"move {index} source must be non-empty",
                    stage=f"{operation}.request",
                )
            if not isinstance(move.rationale, str) or not move.rationale.strip():
                raise FieldPersistenceError(
                    "persistence_batch_not_canonical", operation, self._db_path,
                    f"move {index} rationale must be non-empty",
                    stage=f"{operation}.request",
                )
            delta = _require_finite_float(move.delta, field=f"moves[{index}].delta")
            encoded.append({
                "dim_id": move.dim_id,
                "delta": delta,
                "source": move.source,
                "rationale": move.rationale,
            })
        request = {"version": ATTRACTOR_BATCH_REQUEST_VERSION, "moves": encoded}
        _, fingerprint = _canonical_json_text(request)
        return request, fingerprint

    def _decode_batch_receipt_row(
        self,
        row: tuple,
        *,
        operation: str,
        expected_operation_id: str,
        expected_request_sha256: str | None = None,
    ) -> AttractorBatchReceipt:
        row_operation_id, row_request_sha, receipt_text, receipt_sha = row
        sha_chars = frozenset("0123456789abcdef")
        if (
            not isinstance(row_operation_id, str)
            or not row_operation_id.strip()
            or row_operation_id != expected_operation_id
        ):
            raise FieldPersistenceError(
                "persistence_receipt_not_canonical", operation, self._db_path,
                "operation id readback mismatch", stage=f"{operation}.receipt",
            )
        if (
            not isinstance(row_request_sha, str)
            or len(row_request_sha) != 64
            or any(char not in sha_chars for char in row_request_sha)
            or not isinstance(receipt_sha, str)
            or len(receipt_sha) != 64
            or any(char not in sha_chars for char in receipt_sha)
            or not isinstance(receipt_text, str)
        ):
            raise FieldPersistenceError(
                "persistence_receipt_not_canonical", operation, self._db_path,
                f"receipt {expected_operation_id!r} row metadata invalid",
                stage=f"{operation}.receipt",
            )
        computed = hashlib.sha256(receipt_text.encode("utf-8")).hexdigest()
        if computed != receipt_sha:
            raise FieldPersistenceError(
                "persistence_receipt_hash_mismatch", operation, self._db_path,
                f"receipt {expected_operation_id!r} hash mismatch",
                stage=f"{operation}.receipt",
            )
        try:
            receipt = _strict_json_loads(receipt_text)
            canonical_text, _ = _canonical_json_text(receipt)
        except (TypeError, ValueError) as exc:
            raise FieldPersistenceError(
                "persistence_receipt_not_canonical", operation, self._db_path,
                f"receipt {expected_operation_id!r} invalid JSON: {exc}",
                stage=f"{operation}.receipt",
            ) from exc
        receipt_keys = frozenset({
            "version", "operation_id", "request_sha256", "request",
            "field_tick", "snapshot_id", "results",
        })
        if (
            not isinstance(receipt, dict)
            or canonical_text != receipt_text
            or frozenset(receipt) != receipt_keys
            or receipt.get("version") != ATTRACTOR_BATCH_RECEIPT_VERSION
            or receipt.get("operation_id") != expected_operation_id
            or receipt.get("request_sha256") != row_request_sha
        ):
            raise FieldPersistenceError(
                "persistence_receipt_not_canonical", operation, self._db_path,
                f"receipt {expected_operation_id!r} contract mismatch",
                stage=f"{operation}.receipt",
            )
        request = receipt.get("request")
        if not isinstance(request, dict):
            raise FieldPersistenceError(
                "persistence_receipt_not_canonical", operation, self._db_path,
                "receipt request must be an object", stage=f"{operation}.request",
            )
        try:
            request_text, request_sha = _canonical_json_text(request)
        except (TypeError, ValueError) as exc:
            raise FieldPersistenceError(
                "persistence_receipt_not_canonical", operation, self._db_path,
                f"receipt request invalid: {exc}", stage=f"{operation}.request",
            ) from exc
        if request_sha != row_request_sha or _strict_json_loads(request_text) != request:
            raise FieldPersistenceError(
                "persistence_receipt_request_mismatch", operation, self._db_path,
                "receipt request fingerprint mismatch", stage=f"{operation}.request",
            )
        if expected_request_sha256 is not None and row_request_sha != expected_request_sha256:
            raise FieldPersistenceError(
                "persistence_operation_conflict", operation, self._db_path,
                f"operation {expected_operation_id!r} already exists with a different request",
                stage=f"{operation}.conflict",
            )
        moves_raw = request.get("moves")
        results_raw = receipt.get("results")
        if (
            request.get("version") != ATTRACTOR_BATCH_REQUEST_VERSION
            or frozenset(request) != frozenset({"version", "moves"})
            or not isinstance(moves_raw, list)
            or not isinstance(results_raw, list)
            or len(moves_raw) != len(results_raw)
        ):
            raise FieldPersistenceError(
                "persistence_receipt_not_canonical", operation, self._db_path,
                "receipt request/result shape mismatch", stage=f"{operation}.results",
            )
        field_tick = receipt.get("field_tick")
        snapshot_id = receipt.get("snapshot_id")
        if (
            not isinstance(field_tick, int) or isinstance(field_tick, bool) or field_tick < 0
            or not isinstance(snapshot_id, int) or isinstance(snapshot_id, bool) or snapshot_id <= 0
        ):
            raise FieldPersistenceError(
                "persistence_receipt_not_canonical", operation, self._db_path,
                "receipt field_tick/snapshot_id invalid", stage=f"{operation}.metadata",
            )
        decoded: list[AttractorBatchMoveResult] = []
        result_keys = frozenset({
            "dim_id", "delta", "source", "rationale", "status", "event_id",
            "before_attractor", "after_attractor", "error_code", "error_detail",
        })
        move_keys = frozenset({"dim_id", "delta", "source", "rationale"})
        for index, (move, result) in enumerate(zip(moves_raw, results_raw)):
            if (
                not isinstance(move, dict) or frozenset(move) != move_keys
                or not isinstance(result, dict) or frozenset(result) != result_keys
                or any(result.get(key) != move.get(key) for key in move_keys)
            ):
                raise FieldPersistenceError(
                    "persistence_receipt_not_canonical", operation, self._db_path,
                    f"receipt result {index} does not bind its request move",
                    stage=f"{operation}.results",
                )
            dim_id = move.get("dim_id")
            source = move.get("source")
            rationale = move.get("rationale")
            if (
                not isinstance(dim_id, str) or not dim_id
                or not isinstance(source, str) or not source.strip()
                or not isinstance(rationale, str) or not rationale.strip()
            ):
                raise FieldPersistenceError(
                    "persistence_receipt_not_canonical", operation, self._db_path,
                    f"receipt move {index} text fields invalid",
                    stage=f"{operation}.results",
                )
            delta = move.get("delta")
            if isinstance(delta, bool) or not isinstance(delta, float) or not math.isfinite(delta):
                raise FieldPersistenceError(
                    "persistence_receipt_not_canonical", operation, self._db_path,
                    f"receipt move {index} delta invalid", stage=f"{operation}.results",
                )
            status = result.get("status")
            applied = status == "applied"
            rejected = status == "rejected"
            event_id = result.get("event_id")
            before = result.get("before_attractor")
            after = result.get("after_attractor")
            error_code = result.get("error_code")
            error_detail = result.get("error_detail")
            if applied:
                valid = (
                    isinstance(event_id, int) and not isinstance(event_id, bool) and event_id > 0
                    and isinstance(before, float) and math.isfinite(before)
                    and isinstance(after, float) and math.isfinite(after)
                    and error_code is None and error_detail is None
                )
            elif rejected:
                valid = (
                    event_id is None and before is None and after is None
                    and isinstance(error_code, str) and bool(error_code)
                    and isinstance(error_detail, str) and bool(error_detail)
                )
            else:
                valid = False
            if not valid:
                raise FieldPersistenceError(
                    "persistence_receipt_not_canonical", operation, self._db_path,
                    f"receipt result {index} status fields invalid",
                    stage=f"{operation}.results",
                )
            decoded.append(AttractorBatchMoveResult(
                dim_id=dim_id, delta=delta, source=source,
                rationale=rationale, applied=applied,
                before_attractor=before, after_attractor=after, event_id=event_id,
                error_code=error_code, error_detail=error_detail,
            ))
        return AttractorBatchReceipt(
            operation_id=expected_operation_id,
            request_sha256=str(row_request_sha), field_tick=field_tick,
            results=tuple(decoded), deduplicated=True,
        )

    def read_attractor_batch_receipt(
        self,
        operation_id: str,
        moves: Sequence[AttractorBatchMoveInput] | None = None,
    ) -> AttractorBatchReceipt | None:
        operation = "attractor_batch_read"
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise FieldPersistenceError(
                "persistence_batch_not_canonical", operation, self._db_path,
                "operation_id must be non-empty", stage=f"{operation}.input",
            )
        expected_sha = None
        if moves is not None:
            _, expected_sha = self._canonical_batch_request(moves, operation=operation)
        row = self._conn.execute(
            "SELECT operation_id,request_sha256,receipt_json,receipt_sha256 "
            "FROM field_operation_receipts WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        return self._decode_batch_receipt_row(
            tuple(row), operation=operation, expected_operation_id=operation_id,
            expected_request_sha256=expected_sha,
        )

    def commit_attractor_batch(
        self,
        *,
        operation_id: str,
        boot_id: str,
        utc_unix_ns: int,
        moves: Sequence[AttractorBatchMoveInput],
        candidate_capsule_primitive: dict,
        results: Sequence[AttractorBatchMoveResult],
    ) -> AttractorBatchReceipt:
        """Atomically commit applied events, final snapshot, and operation receipt."""
        operation = "attractor_batch_commit"
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise FieldPersistenceError(
                "persistence_batch_not_canonical", operation, self._db_path,
                "operation_id must be non-empty", stage=f"{operation}.input",
            )
        request, request_sha = self._canonical_batch_request(moves, operation=operation)
        if len(results) != len(moves):
            raise FieldPersistenceError(
                "persistence_batch_not_canonical", operation, self._db_path,
                "results length must equal moves length", stage=f"{operation}.results",
            )
        capsule_text, capsule_sha, field_tick = _validate_canonical_capsule_primitive(
            candidate_capsule_primitive, operation=operation, db_path=self._db_path,
            stage=f"{operation}.snapshot",
        )
        self._require_event_inputs(
            boot_id=boot_id, before_field_tick=field_tick, after_field_tick=field_tick,
            utc_unix_ns=utc_unix_ns, operation=operation,
        )
        try:
            self._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.DatabaseError as exc:
            raise FieldPersistenceError(
                "persistence_batch_commit_failed", operation, self._db_path,
                f"batch transaction could not begin: {exc}", stage=f"{operation}.begin",
                field_tick=field_tick,
            ) from exc
        try:
            existing = self._conn.execute(
                "SELECT operation_id,request_sha256,receipt_json,receipt_sha256 "
                "FROM field_operation_receipts WHERE operation_id=?", (operation_id,),
            ).fetchone()
            if existing is not None:
                stored = self._decode_batch_receipt_row(
                    tuple(existing), operation=operation,
                    expected_operation_id=operation_id,
                    expected_request_sha256=request_sha,
                )
                self._conn.execute("COMMIT")
                return stored

            receipt_results: list[dict] = []
            persisted_results: list[AttractorBatchMoveResult] = []
            final_applied_attractors: dict[str, float] = {}
            for index, (move, result) in enumerate(zip(moves, results)):
                if (
                    not isinstance(result, AttractorBatchMoveResult)
                    or result.dim_id != move.dim_id or result.delta != move.delta
                    or result.source != move.source or result.rationale != move.rationale
                ):
                    raise FieldPersistenceError(
                        "persistence_batch_not_canonical", operation, self._db_path,
                        f"result {index} does not bind its move",
                        stage=f"{operation}.results", field_tick=field_tick,
                    )
                event_id: int | None = None
                if result.applied:
                    if result.event_id is not None or result.error_code is not None or result.error_detail is not None:
                        raise FieldPersistenceError(
                            "persistence_batch_not_canonical", operation, self._db_path,
                            f"applied result {index} contains invalid metadata",
                            stage=f"{operation}.results", field_tick=field_tick,
                        )
                    before = _require_finite_float(result.before_attractor, field="before_attractor")  # type: ignore[arg-type]
                    after = _require_finite_float(result.after_attractor, field="after_attractor")  # type: ignore[arg-type]
                    previous_after = final_applied_attractors.get(move.dim_id)
                    if previous_after is not None and before != previous_after:
                        raise FieldPersistenceError(
                            "persistence_batch_candidate_mismatch", operation, self._db_path,
                            f"applied result {index} does not continue the prior move",
                            stage=f"{operation}.results", field_tick=field_tick,
                        )
                    final_applied_attractors[move.dim_id] = after
                    payload = {
                        "version": EVENT_PAYLOAD_VERSION, "kind": "attractor_move",
                        "dim_id": move.dim_id, "delta": move.delta,
                        "source": move.source, "rationale": move.rationale,
                        "before_attractor": before, "after_attractor": after,
                        "field_tick": field_tick,
                    }
                    event_text, event_sha = self._canonical_event_payload(
                        payload, operation=operation, stage=f"{operation}.event",
                    )
                    cursor = self._conn.execute(
                        "INSERT INTO field_events "
                        "(boot_id,event_kind,before_field_tick,after_field_tick,utc_unix_ns,payload_json,payload_sha256) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (boot_id, "attractor_move", field_tick, field_tick, utc_unix_ns,
                         event_text, event_sha),
                    )
                    if cursor.lastrowid is None:
                        raise FieldPersistenceError(
                            "persistence_event_write_failed", operation, self._db_path,
                            "event insert returned no id", stage=f"{operation}.event",
                            field_tick=field_tick,
                        )
                    event_id = int(cursor.lastrowid)
                    self._readback_event(
                        event_id=event_id, boot_id=boot_id, event_kind="attractor_move",
                        before_field_tick=field_tick, after_field_tick=field_tick,
                        utc_unix_ns=utc_unix_ns, text=event_text, digest=event_sha,
                        operation=operation, stage=f"{operation}.event_readback",
                    )
                    error_code = error_detail = None
                else:
                    before = after = None
                    if (
                        result.event_id is not None
                        or not isinstance(result.error_code, str) or not result.error_code
                        or not isinstance(result.error_detail, str) or not result.error_detail
                    ):
                        raise FieldPersistenceError(
                            "persistence_batch_not_canonical", operation, self._db_path,
                            f"rejected result {index} metadata invalid",
                            stage=f"{operation}.results", field_tick=field_tick,
                        )
                    error_code, error_detail = result.error_code, result.error_detail
                receipt_results.append({
                    "dim_id": move.dim_id, "delta": move.delta, "source": move.source,
                    "rationale": move.rationale,
                    "status": "applied" if result.applied else "rejected",
                    "event_id": event_id, "before_attractor": before,
                    "after_attractor": after, "error_code": error_code,
                    "error_detail": error_detail,
                })
                persisted_results.append(AttractorBatchMoveResult(
                    dim_id=move.dim_id, delta=move.delta, source=move.source,
                    rationale=move.rationale, applied=result.applied,
                    before_attractor=before, after_attractor=after,
                    event_id=event_id, error_code=error_code, error_detail=error_detail,
                ))

            candidate_attractors = {
                dimension["dim_id"]: dimension["attractor"]
                for dimension in candidate_capsule_primitive["dimensions"]
            }
            for dim_id, expected_attractor in final_applied_attractors.items():
                if candidate_attractors.get(dim_id) != expected_attractor:
                    raise FieldPersistenceError(
                        "persistence_batch_candidate_mismatch", operation, self._db_path,
                        f"candidate attractor for {dim_id!r} does not match final result",
                        stage=f"{operation}.snapshot", field_tick=field_tick,
                    )

            snapshot_cursor = self._conn.execute(
                "INSERT INTO field_snapshots(field_tick,utc_unix_ns,capsule_json,capsule_sha256) "
                "VALUES(?,?,?,?)", (field_tick, utc_unix_ns, capsule_text, capsule_sha),
            )
            if snapshot_cursor.lastrowid is None:
                raise FieldPersistenceError(
                    "persistence_snapshot_write_failed", operation, self._db_path,
                    "snapshot insert returned no id", stage=f"{operation}.snapshot",
                    field_tick=field_tick,
                )
            snapshot_id = int(snapshot_cursor.lastrowid)
            snapshot_row = self._conn.execute(
                "SELECT field_tick,utc_unix_ns,capsule_json,capsule_sha256 "
                "FROM field_snapshots WHERE snapshot_id=?", (snapshot_id,),
            ).fetchone()
            if snapshot_row != (field_tick, utc_unix_ns, capsule_text, capsule_sha):
                raise FieldPersistenceError(
                    "persistence_snapshot_readback_mismatch", operation, self._db_path,
                    "batch snapshot readback mismatch", stage=f"{operation}.snapshot_readback",
                    field_tick=field_tick,
                )
            self._conn.execute(
                "DELETE FROM field_snapshots WHERE snapshot_id NOT IN "
                "(SELECT snapshot_id FROM field_snapshots ORDER BY snapshot_id DESC LIMIT ?)",
                (SNAPSHOT_RETENTION_COUNT,),
            )
            receipt = {
                "version": ATTRACTOR_BATCH_RECEIPT_VERSION,
                "operation_id": operation_id, "request_sha256": request_sha,
                "request": request, "field_tick": field_tick,
                "snapshot_id": snapshot_id, "results": receipt_results,
            }
            receipt_text, receipt_sha = _canonical_json_text(receipt)
            self._conn.execute(
                "INSERT INTO field_operation_receipts"
                "(operation_id,request_sha256,receipt_json,receipt_sha256) VALUES(?,?,?,?)",
                (operation_id, request_sha, receipt_text, receipt_sha),
            )
            receipt_row = self._conn.execute(
                "SELECT operation_id,request_sha256,receipt_json,receipt_sha256 "
                "FROM field_operation_receipts WHERE operation_id=?", (operation_id,),
            ).fetchone()
            if receipt_row is None:
                raise FieldPersistenceError(
                    "persistence_receipt_readback_mismatch", operation, self._db_path,
                    "receipt readback missing", stage=f"{operation}.receipt",
                    field_tick=field_tick,
                )
            verified = self._decode_batch_receipt_row(
                tuple(receipt_row), operation=operation,
                expected_operation_id=operation_id,
                expected_request_sha256=request_sha,
            )
            self._conn.execute("COMMIT")
            return AttractorBatchReceipt(
                operation_id=verified.operation_id,
                request_sha256=verified.request_sha256,
                field_tick=verified.field_tick,
                results=tuple(persisted_results), deduplicated=False,
            )
        except FieldPersistenceError:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.DatabaseError:
                pass
            raise
        except (sqlite3.DatabaseError, TypeError, ValueError) as exc:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.DatabaseError:
                pass
            raise FieldPersistenceError(
                "persistence_batch_commit_failed", operation, self._db_path,
                f"batch transaction failed: {exc}", stage=operation,
                field_tick=field_tick,
            ) from exc

    # -- trajectory reads -------------------------------------------------

    def latest_tick_cursor(self) -> int | None:
        """Return the greatest committed tick ``event_id``, if one exists."""
        self._require_open_for_read("trajectory_head")
        row = self._conn.execute(
            "SELECT MAX(event_id) FROM field_events WHERE event_kind = 'tick'"
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return int(row[0])

    def tick_cursor_exists(self, cursor: int) -> bool:
        """Whether ``cursor`` identifies an existing tick event."""
        self._require_cursor(cursor, operation="trajectory_cursor")
        row = self._conn.execute(
            "SELECT 1 FROM field_events WHERE event_id = ? AND event_kind = 'tick'",
            (cursor,),
        ).fetchone()
        return row is not None

    def read_trajectory_frames(
        self,
        *,
        registry_dim_ids: tuple[str, ...],
        after_cursor: int | None,
        cutoff_cursor: int | None,
        limit: int,
    ) -> tuple[TrajectoryFrame, ...]:
        """Read a bounded, event-id ordered trajectory window in one query.

        With ``after_cursor=None`` this returns the latest ``limit`` tick
        events at or before ``cutoff_cursor``.  Otherwise it returns the first
        ``limit`` tick events in ``(after_cursor, cutoff_cursor]``.  Event ids
        need not be contiguous because attractor events share the sequence.
        Every selected frame is validated fail-closed against the registry.
        """
        operation = "trajectory_read"
        self._require_open_for_read(operation)
        if not isinstance(registry_dim_ids, tuple) or not registry_dim_ids:
            raise FieldPersistenceError(
                "persistence_trajectory_registry_invalid",
                operation,
                self._db_path,
                "registry_dim_ids must be a non-empty tuple",
                stage="trajectory_read.input",
            )
        if len(set(registry_dim_ids)) != len(registry_dim_ids) or any(
            not isinstance(dim_id, str) or not dim_id for dim_id in registry_dim_ids
        ):
            raise FieldPersistenceError(
                "persistence_trajectory_registry_invalid",
                operation,
                self._db_path,
                "registry_dim_ids must contain unique non-empty strings",
                stage="trajectory_read.input",
            )
        if after_cursor is not None:
            self._require_cursor(after_cursor, operation=operation)
        if cutoff_cursor is not None:
            self._require_cursor(cutoff_cursor, operation=operation)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise FieldPersistenceError(
                "persistence_trajectory_limit_invalid",
                operation,
                self._db_path,
                "limit must be a positive int",
                stage="trajectory_read.input",
            )
        if cutoff_cursor is None:
            return ()

        event_columns = (
            "e.event_id,e.boot_id,e.before_field_tick,e.after_field_tick,"
            "e.utc_unix_ns,e.payload_json,e.payload_sha256"
        )
        point_columns = (
            "t.trajectory_id,t.event_id,t.field_tick,t.dimension_ordinal,t.dim_id,"
            "t.after_value,t.after_velocity,t.after_attractor,"
            "t.after_slow_baseline,t.after_ou_acceleration"
        )
        if after_cursor is None:
            selected_sql = (
                "SELECT event_id FROM field_events "
                "WHERE event_kind='tick' AND event_id<=? "
                "ORDER BY event_id DESC LIMIT ?"
            )
            params: tuple[int, ...] = (cutoff_cursor, limit)
        else:
            selected_sql = (
                "SELECT event_id FROM field_events "
                "WHERE event_kind='tick' AND event_id>? AND event_id<=? "
                "ORDER BY event_id ASC LIMIT ?"
            )
            params = (after_cursor, cutoff_cursor, limit)
        rows = self._conn.execute(
            "WITH selected AS (" + selected_sql + ") "
            "SELECT " + event_columns + "," + point_columns + " "
            "FROM selected s JOIN field_events e ON e.event_id=s.event_id "
            "LEFT JOIN trajectory_points t ON t.event_id=e.event_id "
            "ORDER BY e.event_id ASC,t.dimension_ordinal ASC",
            params,
        ).fetchall()

        grouped: list[tuple[tuple, list[tuple]]] = []
        for row in rows:
            event_row = tuple(row[:7])
            point_row = tuple(row[7:])
            if not grouped or int(grouped[-1][0][0]) != int(event_row[0]):
                grouped.append((event_row, []))
            if point_row[0] is not None:
                grouped[-1][1].append(point_row)

        frames: list[TrajectoryFrame] = []
        for event_row, point_rows in grouped:
            eid, boot_id, before_tick, after_tick, utc_ns, payload_text, digest = event_row
            event_id = int(eid)
            payload = self._validate_tick_payload_for_read(
                event_id=event_id,
                before_field_tick=before_tick,
                after_field_tick=after_tick,
                payload_text=payload_text,
                payload_digest=digest,
            )
            self._audit_tick_event(
                event_id=event_id,
                before_field_tick=int(before_tick),
                after_field_tick=int(after_tick),
                payload=payload,
                registry_dim_ids=registry_dim_ids,
                trajectory_rows=point_rows,
                operation=operation,
            )
            frames.append(
                TrajectoryFrame(
                    cursor=event_id,
                    boot_id=str(boot_id),
                    field_tick=int(after_tick),
                    utc_unix_ns=int(utc_ns),
                    dimensions=tuple(
                        TrajectoryPoint(
                            ordinal=int(point[3]),
                            dim_id=str(point[4]),
                            value=float(point[5]),
                            velocity=float(point[6]),
                            attractor=float(point[7]),
                            slow_baseline=float(point[8]),
                            ou_acceleration=float(point[9]),
                        )
                        for point in point_rows
                    ),
                )
            )
        return tuple(frames)

    def _require_open_for_read(self, operation: str) -> None:
        if self._closed:
            raise FieldPersistenceError(
                "persistence_closed",
                operation,
                self._db_path,
                "store is closed",
                stage=f"{operation}.closed",
            )

    def _require_cursor(self, cursor: int, *, operation: str) -> None:
        self._require_open_for_read(operation)
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
            raise FieldPersistenceError(
                "persistence_trajectory_cursor_invalid",
                operation,
                self._db_path,
                "cursor must be a non-negative int",
                stage=f"{operation}.input",
            )

    def _validate_tick_payload_for_read(
        self,
        *,
        event_id: int,
        before_field_tick: object,
        after_field_tick: object,
        payload_text: object,
        payload_digest: object,
    ) -> dict:
        operation = "trajectory_read"
        if (
            not isinstance(before_field_tick, int)
            or isinstance(before_field_tick, bool)
            or not isinstance(after_field_tick, int)
            or isinstance(after_field_tick, bool)
            or before_field_tick < 0
            or after_field_tick < 0
            or not isinstance(payload_text, str)
            or not isinstance(payload_digest, str)
        ):
            raise FieldPersistenceError(
                "persistence_event_payload_not_canonical",
                operation,
                self._db_path,
                f"event {event_id}: invalid tick event row metadata",
                stage="trajectory_read.event",
            )
        computed = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
        if computed != payload_digest:
            raise FieldPersistenceError(
                "persistence_event_payload_hash_mismatch",
                operation,
                self._db_path,
                f"event {event_id}: payload hash mismatch",
                stage="trajectory_read.payload_hash",
            )
        try:
            payload = _strict_json_loads(payload_text)
            canonical_text, _ = _canonical_json_text(payload)
        except (TypeError, ValueError) as exc:
            raise FieldPersistenceError(
                "persistence_event_payload_not_canonical",
                operation,
                self._db_path,
                f"event {event_id}: invalid canonical payload: {exc}",
                stage="trajectory_read.payload",
            ) from exc
        expected_keys = frozenset({"version", "kind", "before_tick", "after_tick"})
        if (
            not isinstance(payload, dict)
            or canonical_text != payload_text
            or frozenset(payload) != expected_keys
            or payload.get("version") != EVENT_PAYLOAD_VERSION
            or payload.get("kind") != "tick"
        ):
            raise FieldPersistenceError(
                "persistence_event_payload_not_canonical",
                operation,
                self._db_path,
                f"event {event_id}: tick payload contract mismatch",
                stage="trajectory_read.payload",
            )
        return payload

    # -- event helpers ----------------------------------------------------

    def _require_event_inputs(
        self,
        *,
        boot_id: str,
        before_field_tick: int,
        after_field_tick: int,
        utc_unix_ns: int,
        operation: str,
    ) -> None:
        db_path = self._db_path
        if not isinstance(boot_id, str) or not boot_id:
            raise FieldPersistenceError(
                "persistence_event_not_canonical",
                operation,
                db_path,
                "boot_id must be a non-empty str",
                stage=f"{operation}.input",
            )
        if not isinstance(before_field_tick, int) or isinstance(
            before_field_tick, bool
        ):
            raise FieldPersistenceError(
                "persistence_event_not_canonical",
                operation,
                db_path,
                "before_field_tick must be an int",
                stage=f"{operation}.input",
            )
        if not isinstance(after_field_tick, int) or isinstance(
            after_field_tick, bool
        ):
            raise FieldPersistenceError(
                "persistence_event_not_canonical",
                operation,
                db_path,
                "after_field_tick must be an int",
                stage=f"{operation}.input",
            )
        if not isinstance(utc_unix_ns, int) or isinstance(utc_unix_ns, bool):
            raise FieldPersistenceError(
                "persistence_event_not_canonical",
                operation,
                db_path,
                "utc_unix_ns must be an int",
                stage=f"{operation}.input",
            )

    def _canonical_event_payload(
        self,
        payload: dict,
        *,
        operation: str,
        stage: str,
    ) -> tuple[str, str]:
        try:
            text, digest = _canonical_json_text(payload)
        except ValueError as exc:
            raise FieldPersistenceError(
                "persistence_event_not_canonical",
                operation,
                self._db_path,
                f"canonical JSON validation failed: {exc}",
                stage=stage,
            ) from exc
        return text, digest

    def _validate_trajectory_rows(
        self,
        rows: Sequence[TrajectoryRowInput],
        *,
        operation: str,
        stage: str,
        field_tick: int,
    ) -> list[TrajectoryRowInput]:
        db_path = self._db_path
        if not isinstance(rows, Sequence):
            raise FieldPersistenceError(
                "persistence_event_not_canonical",
                operation,
                db_path,
                "trajectory_rows must be a sequence",
                stage=stage,
                field_tick=field_tick,
            )
        validated: list[TrajectoryRowInput] = []
        seen_ordinals: set[int] = set()
        seen_dim_ids: set[str] = set()
        for row in rows:
            if not isinstance(row, TrajectoryRowInput):
                raise FieldPersistenceError(
                    "persistence_event_not_canonical",
                    operation,
                    db_path,
                    "trajectory row must be a TrajectoryRowInput",
                    stage=stage,
                    field_tick=field_tick,
                )
            if (
                not isinstance(row.dimension_ordinal, int)
                or isinstance(row.dimension_ordinal, bool)
                or row.dimension_ordinal < 0
            ):
                raise FieldPersistenceError(
                    "persistence_event_not_canonical",
                    operation,
                    db_path,
                    f"dimension_ordinal must be a non-negative int, got {row.dimension_ordinal!r}",
                    stage=stage,
                    field_tick=field_tick,
                )
            if not isinstance(row.dim_id, str) or not row.dim_id:
                raise FieldPersistenceError(
                    "persistence_event_not_canonical",
                    operation,
                    db_path,
                    "dim_id must be a non-empty str",
                    stage=stage,
                    field_tick=field_tick,
                )
            if row.dimension_ordinal in seen_ordinals:
                raise FieldPersistenceError(
                    "persistence_event_not_canonical",
                    operation,
                    db_path,
                    f"duplicate dimension_ordinal {row.dimension_ordinal}",
                    stage=stage,
                    field_tick=field_tick,
                )
            if row.dim_id in seen_dim_ids:
                raise FieldPersistenceError(
                    "persistence_event_not_canonical",
                    operation,
                    db_path,
                    f"duplicate dim_id {row.dim_id!r}",
                    stage=stage,
                    field_tick=field_tick,
                )
            seen_ordinals.add(row.dimension_ordinal)
            seen_dim_ids.add(row.dim_id)
            after_value = _require_finite_float(row.after_value, field="after_value")
            after_velocity = _require_finite_float(
                row.after_velocity, field="after_velocity"
            )
            after_attractor = _require_finite_float(
                row.after_attractor, field="after_attractor"
            )
            after_slow_baseline = _require_finite_float(
                row.after_slow_baseline, field="after_slow_baseline"
            )
            after_ou_acceleration = _require_finite_float(
                row.after_ou_acceleration, field="after_ou_acceleration"
            )
            validated.append(
                TrajectoryRowInput(
                    dimension_ordinal=row.dimension_ordinal,
                    dim_id=row.dim_id,
                    after_value=after_value,
                    after_velocity=after_velocity,
                    after_attractor=after_attractor,
                    after_slow_baseline=after_slow_baseline,
                    after_ou_acceleration=after_ou_acceleration,
                )
            )
        return validated

    def _readback_event(
        self,
        *,
        event_id: int,
        boot_id: str,
        event_kind: str,
        before_field_tick: int,
        after_field_tick: int,
        utc_unix_ns: int,
        text: str,
        digest: str,
        operation: str,
        stage: str,
    ) -> None:
        db_path = self._db_path
        row = self._conn.execute(
            "SELECT event_id, boot_id, event_kind, before_field_tick, "
            "after_field_tick, utc_unix_ns, payload_json, payload_sha256 "
            "FROM field_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            raise FieldPersistenceError(
                "persistence_event_readback_mismatch",
                operation,
                db_path,
                "read-back event row missing",
                stage=stage,
                field_tick=after_field_tick,
            )
        r_id, r_boot, r_kind, r_before, r_after, r_utc, r_json, r_digest = row
        if int(r_id) != event_id:
            raise FieldPersistenceError(
                "persistence_event_readback_mismatch",
                operation,
                db_path,
                f"read-back event_id mismatch: {r_id} != {event_id}",
                stage=stage,
                field_tick=after_field_tick,
            )
        if r_boot != boot_id:
            raise FieldPersistenceError(
                "persistence_event_readback_mismatch",
                operation,
                db_path,
                f"read-back boot_id mismatch: {r_boot!r} != {boot_id!r}",
                stage=stage,
                field_tick=after_field_tick,
            )
        if r_kind != event_kind:
            raise FieldPersistenceError(
                "persistence_event_readback_mismatch",
                operation,
                db_path,
                f"read-back event_kind mismatch: {r_kind!r} != {event_kind!r}",
                stage=stage,
                field_tick=after_field_tick,
            )
        if int(r_before) != before_field_tick:
            raise FieldPersistenceError(
                "persistence_event_readback_mismatch",
                operation,
                db_path,
                f"read-back before_field_tick mismatch: {r_before} != {before_field_tick}",
                stage=stage,
                field_tick=after_field_tick,
            )
        if int(r_after) != after_field_tick:
            raise FieldPersistenceError(
                "persistence_event_readback_mismatch",
                operation,
                db_path,
                f"read-back after_field_tick mismatch: {r_after} != {after_field_tick}",
                stage=stage,
                field_tick=after_field_tick,
            )
        if int(r_utc) != utc_unix_ns:
            raise FieldPersistenceError(
                "persistence_event_readback_mismatch",
                operation,
                db_path,
                f"read-back utc_unix_ns mismatch: {r_utc} != {utc_unix_ns}",
                stage=stage,
                field_tick=after_field_tick,
            )
        if r_json != text:
            raise FieldPersistenceError(
                "persistence_event_readback_mismatch",
                operation,
                db_path,
                "read-back payload_json text mismatch",
                stage=stage,
                field_tick=after_field_tick,
            )
        if r_digest != digest:
            raise FieldPersistenceError(
                "persistence_event_readback_mismatch",
                operation,
                db_path,
                f"read-back payload digest mismatch: {r_digest} != {digest}",
                stage=stage,
                field_tick=after_field_tick,
            )

    def _readback_trajectory(
        self,
        *,
        event_id: int,
        field_tick: int,
        rows: list[TrajectoryRowInput],
        operation: str,
        stage: str,
    ) -> None:
        db_path = self._db_path
        count_row = self._conn.execute(
            "SELECT COUNT(*) FROM trajectory_points WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if int(count_row[0]) != len(rows):
            raise FieldPersistenceError(
                "persistence_trajectory_readback_mismatch",
                operation,
                db_path,
                f"read-back trajectory count {int(count_row[0])} != {len(rows)}",
                stage=stage,
                field_tick=field_tick,
            )
        db_rows = self._conn.execute(
            "SELECT trajectory_id, event_id, field_tick, dimension_ordinal, "
            "dim_id, after_value, after_velocity, after_attractor, "
            "after_slow_baseline, after_ou_acceleration "
            "FROM trajectory_points WHERE event_id = ? "
            "ORDER BY dimension_ordinal",
            (event_id,),
        ).fetchall()
        if len(db_rows) != len(rows):
            raise FieldPersistenceError(
                "persistence_trajectory_readback_mismatch",
                operation,
                db_path,
                f"read-back trajectory row count {len(db_rows)} != {len(rows)}",
                stage=stage,
                field_tick=field_tick,
            )
        for input_row, db_row in zip(rows, db_rows):
            (
                r_tid,
                r_eid,
                r_tick,
                r_ord,
                r_dim,
                r_val,
                r_vel,
                r_attr,
                r_base,
                r_ou,
            ) = db_row
            if int(r_eid) != event_id:
                raise FieldPersistenceError(
                    "persistence_trajectory_readback_mismatch",
                    operation,
                    db_path,
                    f"read-back event_id mismatch: {r_eid} != {event_id}",
                    stage=stage,
                    field_tick=field_tick,
                )
            if int(r_tick) != field_tick:
                raise FieldPersistenceError(
                    "persistence_trajectory_readback_mismatch",
                    operation,
                    db_path,
                    f"read-back field_tick mismatch: {r_tick} != {field_tick}",
                    stage=stage,
                    field_tick=field_tick,
                )
            if int(r_ord) != input_row.dimension_ordinal:
                raise FieldPersistenceError(
                    "persistence_trajectory_readback_mismatch",
                    operation,
                    db_path,
                    f"read-back ordinal mismatch: {r_ord} != {input_row.dimension_ordinal}",
                    stage=stage,
                    field_tick=field_tick,
                )
            if r_dim != input_row.dim_id:
                raise FieldPersistenceError(
                    "persistence_trajectory_readback_mismatch",
                    operation,
                    db_path,
                    f"read-back dim_id mismatch: {r_dim!r} != {input_row.dim_id!r}",
                    stage=stage,
                    field_tick=field_tick,
                )
            if float(r_val) != input_row.after_value:
                raise FieldPersistenceError(
                    "persistence_trajectory_readback_mismatch",
                    operation,
                    db_path,
                    f"read-back after_value mismatch for {input_row.dim_id!r}",
                    stage=stage,
                    field_tick=field_tick,
                )
            if float(r_vel) != input_row.after_velocity:
                raise FieldPersistenceError(
                    "persistence_trajectory_readback_mismatch",
                    operation,
                    db_path,
                    f"read-back after_velocity mismatch for {input_row.dim_id!r}",
                    stage=stage,
                    field_tick=field_tick,
                )
            if float(r_attr) != input_row.after_attractor:
                raise FieldPersistenceError(
                    "persistence_trajectory_readback_mismatch",
                    operation,
                    db_path,
                    f"read-back after_attractor mismatch for {input_row.dim_id!r}",
                    stage=stage,
                    field_tick=field_tick,
                )
            if float(r_base) != input_row.after_slow_baseline:
                raise FieldPersistenceError(
                    "persistence_trajectory_readback_mismatch",
                    operation,
                    db_path,
                    f"read-back after_slow_baseline mismatch for {input_row.dim_id!r}",
                    stage=stage,
                    field_tick=field_tick,
                )
            if float(r_ou) != input_row.after_ou_acceleration:
                raise FieldPersistenceError(
                    "persistence_trajectory_readback_mismatch",
                    operation,
                    db_path,
                    f"read-back after_ou_acceleration mismatch for {input_row.dim_id!r}",
                    stage=stage,
                    field_tick=field_tick,
                )

    # -- history audit ----------------------------------------------------

    def audit_event_history(
        self,
        *,
        registry_dim_ids: tuple[str, ...],
    ) -> None:
        """Narrow startup audit of append-only event/trajectory integrity.

        Called after successful snapshot decode/construct but before runtime
        install.  Traverses all rows in ``field_events`` and
        ``trajectory_points``, verifies payload SHA-256, strict JSON,
        canonical compact text, payload version/kind/field set, tick
        consistency, trajectory ordinal continuity and registry alignment,
        finiteness, and per-kind invariants.

        Different boot segments may have overlapping or non-monotonic ticks;
        no global field_tick monotonicity is required.  Empty databases skip
        the audit entirely (the caller must check for emptiness first).
        """
        db_path = self._db_path
        operation = "history_audit"

        # Expected exact key sets for each kind
        _TICK_PAYLOAD_KEYS = frozenset({"version", "kind", "before_tick", "after_tick"})
        _ATTRACTOR_PAYLOAD_KEYS = frozenset({
            "version", "kind", "dim_id", "delta", "source", "rationale",
            "before_attractor", "after_attractor", "field_tick",
        })

        events = self._conn.execute(
            "SELECT event_id, boot_id, event_kind, before_field_tick, "
            "after_field_tick, utc_unix_ns, payload_json, payload_sha256 "
            "FROM field_events ORDER BY event_id"
        ).fetchall()

        seen_event_ids: set[int] = set()

        # For each event, validate payload and cross-check with trajectory
        for row in events:
            eid, eboot, ekind, ebefore, eafter, eutc, etext, edigest = row
            event_id = int(eid)

            # Row metadata type checks
            if not isinstance(eboot, str) or not eboot:
                raise FieldPersistenceError(
                    "persistence_event_payload_not_canonical",
                    operation,
                    db_path,
                    f"event {event_id}: boot_id must be non-empty str, "
                    f"got {type(eboot).__name__}={eboot!r}",
                    stage="history_audit.row_boot_id",
                )
            if not isinstance(ebefore, int) or isinstance(ebefore, bool):
                raise FieldPersistenceError(
                    "persistence_event_payload_not_canonical",
                    operation,
                    db_path,
                    f"event {event_id}: before_field_tick must be int, "
                    f"got {type(ebefore).__name__}={ebefore!r}",
                    stage="history_audit.row_tick",
                )
            if not isinstance(eafter, int) or isinstance(eafter, bool):
                raise FieldPersistenceError(
                    "persistence_event_payload_not_canonical",
                    operation,
                    db_path,
                    f"event {event_id}: after_field_tick must be int, "
                    f"got {type(eafter).__name__}={eafter!r}",
                    stage="history_audit.row_tick",
                )
            if int(ebefore) < 0 or int(eafter) < 0:
                raise FieldPersistenceError(
                    "persistence_event_payload_not_canonical",
                    operation,
                    db_path,
                    f"event {event_id}: ticks must be non-negative, "
                    f"got before={ebefore} after={eafter}",
                    stage="history_audit.row_tick",
                )
            if not isinstance(eutc, int) or isinstance(eutc, bool):
                raise FieldPersistenceError(
                    "persistence_event_payload_not_canonical",
                    operation,
                    db_path,
                    f"event {event_id}: utc_unix_ns must be int, "
                    f"got {type(eutc).__name__}={eutc!r}",
                    stage="history_audit.row_utc",
                )
            if int(eutc) < 0:
                raise FieldPersistenceError(
                    "persistence_event_payload_not_canonical",
                    operation,
                    db_path,
                    f"event {event_id}: utc_unix_ns must be non-negative, "
                    f"got {eutc}",
                    stage="history_audit.row_utc",
                )
            if not isinstance(ekind, str) or not ekind:
                raise FieldPersistenceError(
                    "persistence_event_payload_not_canonical",
                    operation,
                    db_path,
                    f"event {event_id}: event_kind must be non-empty str, "
                    f"got {type(ekind).__name__}={ekind!r}",
                    stage="history_audit.row_kind",
                )

            seen_event_ids.add(event_id)

            # SHA-256 integrity
            computed = hashlib.sha256(etext.encode("utf-8")).hexdigest()
            if computed != edigest:
                raise FieldPersistenceError(
                    "persistence_event_payload_hash_mismatch",
                    operation,
                    db_path,
                    f"event {event_id}: payload hash mismatch "
                    f"(stored={edigest!r} computed={computed!r})",
                    stage="history_audit.payload_hash",
                )

            # Strict JSON parse (reject duplicate keys, non-finite)
            try:
                payload = _strict_json_loads(etext)
            except ValueError as exc:
                raise FieldPersistenceError(
                    "persistence_event_payload_not_canonical",
                    operation,
                    db_path,
                    f"event {event_id}: strict JSON parse failed: {exc}",
                    stage="history_audit.payload_json",
                ) from exc

            # Canonical compact text must match stored text byte-for-byte
            try:
                re_text, re_digest = _canonical_json_text(payload)
            except ValueError as exc:
                raise FieldPersistenceError(
                    "persistence_event_payload_not_canonical",
                    operation,
                    db_path,
                    f"event {event_id}: canonical re-serialize failed: {exc}",
                    stage="history_audit.payload_canonical",
                ) from exc
            if re_text != etext:
                raise FieldPersistenceError(
                    "persistence_event_payload_not_canonical",
                    operation,
                    db_path,
                    f"event {event_id}: re-serialized payload text differs "
                    "byte-for-byte from stored text",
                    stage="history_audit.payload_canonical",
                )

            # Payload version
            pver = payload.get("version")
            if not isinstance(pver, str) or pver != EVENT_PAYLOAD_VERSION:
                raise FieldPersistenceError(
                    "persistence_event_payload_version_mismatch",
                    operation,
                    db_path,
                    f"event {event_id}: expected payload version "
                    f"{EVENT_PAYLOAD_VERSION!r}, got {pver!r}",
                    stage="history_audit.payload_version",
                )

            pkind = payload.get("kind")
            if not isinstance(pkind, str):
                raise FieldPersistenceError(
                    "persistence_event_payload_not_canonical",
                    operation,
                    db_path,
                    f"event {event_id}: payload kind must be str, "
                    f"got {type(pkind).__name__}={pkind!r}",
                    stage="history_audit.payload_kind",
                )
            if pkind != ekind:
                raise FieldPersistenceError(
                    "persistence_event_payload_kind_mismatch",
                    operation,
                    db_path,
                    f"event {event_id}: payload kind {pkind!r} != "
                    f"row kind {ekind!r}",
                    stage="history_audit.payload_kind",
                )

            # Strict payload key set enforcement
            payload_keys = frozenset(payload.keys())
            if ekind == "tick":
                if payload_keys != _TICK_PAYLOAD_KEYS:
                    extra = sorted(payload_keys - _TICK_PAYLOAD_KEYS)
                    missing = sorted(_TICK_PAYLOAD_KEYS - payload_keys)
                    raise FieldPersistenceError(
                        "persistence_event_payload_not_canonical",
                        operation,
                        db_path,
                        f"event {event_id}: tick payload keys mismatch; "
                        f"extra={extra} missing={missing}",
                        stage="history_audit.payload_keys",
                    )
                self._audit_tick_event(
                    event_id=event_id,
                    before_field_tick=int(ebefore),
                    after_field_tick=int(eafter),
                    payload=payload,
                    registry_dim_ids=registry_dim_ids,
                )
            elif ekind == "attractor_move":
                if payload_keys != _ATTRACTOR_PAYLOAD_KEYS:
                    extra = sorted(payload_keys - _ATTRACTOR_PAYLOAD_KEYS)
                    missing = sorted(_ATTRACTOR_PAYLOAD_KEYS - payload_keys)
                    raise FieldPersistenceError(
                        "persistence_event_payload_not_canonical",
                        operation,
                        db_path,
                        f"event {event_id}: attractor payload keys mismatch; "
                        f"extra={extra} missing={missing}",
                        stage="history_audit.payload_keys",
                    )
                self._audit_attractor_event(
                    event_id=event_id,
                    before_field_tick=int(ebefore),
                    after_field_tick=int(eafter),
                    payload=payload,
                    registry_dim_ids=registry_dim_ids,
                )
            else:
                raise FieldPersistenceError(
                    "persistence_event_unknown_kind",
                    operation,
                    db_path,
                    f"event {event_id}: unknown event_kind {ekind!r}",
                    stage="history_audit.unknown_kind",
                )

        # After all events: check for orphan/unlinked trajectory rows
        all_traj_event_ids = self._conn.execute(
            "SELECT DISTINCT event_id FROM trajectory_points ORDER BY event_id"
        ).fetchall()
        orphan_ids = set()
        for (teid,) in all_traj_event_ids:
            if int(teid) not in seen_event_ids:
                orphan_ids.add(int(teid))
        if orphan_ids:
            raise FieldPersistenceError(
                "persistence_trajectory_orphan",
                operation,
                db_path,
                f"trajectory points reference non-existent event_ids: "
                f"{sorted(orphan_ids)}",
                stage="history_audit.orphan_trajectory",
            )
        self.audit_operation_receipts(registry_dim_ids=registry_dim_ids)

    def audit_operation_receipts(self, *, registry_dim_ids: tuple[str, ...]) -> None:
        """Verify every receipt and its event/snapshot references fail-closed."""
        operation = "receipt_audit"
        rows = self._conn.execute(
            "SELECT operation_id,request_sha256,receipt_json,receipt_sha256 "
            "FROM field_operation_receipts ORDER BY operation_id"
        ).fetchall()
        for raw_row in rows:
            operation_id = raw_row[0]
            if not isinstance(operation_id, str) or not operation_id:
                raise FieldPersistenceError(
                    "persistence_receipt_not_canonical", operation, self._db_path,
                    "receipt operation_id must be non-empty", stage="receipt_audit.row",
                )
            receipt = self._decode_batch_receipt_row(
                tuple(raw_row), operation=operation,
                expected_operation_id=operation_id,
            )
            text = raw_row[2]
            parsed = _strict_json_loads(text)
            if not isinstance(parsed, dict):
                raise FieldPersistenceError(
                    "persistence_receipt_not_canonical", operation, self._db_path,
                    f"receipt {operation_id!r} must be an object",
                    stage="receipt_audit.shape",
                )
            snapshot_id = parsed["snapshot_id"]
            snapshot_row = self._conn.execute(
                "SELECT field_tick,capsule_json,capsule_sha256 FROM field_snapshots "
                "WHERE snapshot_id=?", (snapshot_id,),
            ).fetchone()
            # Retention may have removed this batch's snapshot; in that case a
            # newer retained snapshot is the authoritative recovery point.
            if snapshot_row is not None:
                s_tick, s_text, s_sha = snapshot_row
                try:
                    snapshot_primitive = _strict_json_loads(s_text)
                    if not isinstance(snapshot_primitive, dict):
                        raise ValueError("snapshot root must be an object")
                    canonical_text, canonical_sha, canonical_tick = (
                        _validate_canonical_capsule_primitive(
                            snapshot_primitive,
                            operation=operation,
                            db_path=self._db_path,
                            stage="receipt_audit.snapshot",
                        )
                    )
                except (TypeError, ValueError) as exc:
                    raise FieldPersistenceError(
                        "persistence_receipt_snapshot_mismatch", operation, self._db_path,
                        f"receipt {operation_id!r} snapshot is invalid: {exc}",
                        stage="receipt_audit.snapshot", field_tick=receipt.field_tick,
                    ) from exc
                if (
                    int(s_tick) != receipt.field_tick
                    or canonical_tick != receipt.field_tick
                    or canonical_text != s_text
                    or canonical_sha != s_sha
                ):
                    raise FieldPersistenceError(
                        "persistence_receipt_snapshot_mismatch", operation, self._db_path,
                        f"receipt {operation_id!r} snapshot mismatch",
                        stage="receipt_audit.snapshot", field_tick=receipt.field_tick,
                    )
                snapshot_attractors = {
                    dimension["dim_id"]: dimension["attractor"]
                    for dimension in snapshot_primitive["dimensions"]
                }
                final_applied: dict[str, float] = {}
                for result in receipt.results:
                    if result.applied:
                        final_applied[result.dim_id] = result.after_attractor  # type: ignore[assignment]
                if any(
                    snapshot_attractors.get(dim_id) != attractor
                    for dim_id, attractor in final_applied.items()
                ):
                    raise FieldPersistenceError(
                        "persistence_receipt_snapshot_mismatch", operation, self._db_path,
                        f"receipt {operation_id!r} snapshot does not contain final results",
                        stage="receipt_audit.snapshot", field_tick=receipt.field_tick,
                    )
            for result in receipt.results:
                if result.dim_id not in registry_dim_ids:
                    raise FieldPersistenceError(
                        "persistence_receipt_unknown_dim", operation, self._db_path,
                        f"receipt {operation_id!r} dim {result.dim_id!r} is not registered",
                        stage="receipt_audit.dim", field_tick=receipt.field_tick,
                    )
                if not result.applied:
                    continue
                event_row = self._conn.execute(
                    "SELECT event_kind,before_field_tick,after_field_tick,payload_json,payload_sha256 "
                    "FROM field_events WHERE event_id=?", (result.event_id,),
                ).fetchone()
                if event_row is None:
                    raise FieldPersistenceError(
                        "persistence_receipt_event_missing", operation, self._db_path,
                        f"receipt {operation_id!r} references missing event {result.event_id}",
                        stage="receipt_audit.event", field_tick=receipt.field_tick,
                    )
                kind, before_tick, after_tick, payload_text, payload_sha = event_row
                if (
                    kind != "attractor_move"
                    or before_tick != receipt.field_tick or after_tick != receipt.field_tick
                    or hashlib.sha256(payload_text.encode("utf-8")).hexdigest() != payload_sha
                ):
                    raise FieldPersistenceError(
                        "persistence_receipt_event_mismatch", operation, self._db_path,
                        f"receipt {operation_id!r} event {result.event_id} metadata mismatch",
                        stage="receipt_audit.event", field_tick=receipt.field_tick,
                    )
                payload = _strict_json_loads(payload_text)
                expected = {
                    "version": EVENT_PAYLOAD_VERSION, "kind": "attractor_move",
                    "dim_id": result.dim_id, "delta": result.delta,
                    "source": result.source, "rationale": result.rationale,
                    "before_attractor": result.before_attractor,
                    "after_attractor": result.after_attractor,
                    "field_tick": receipt.field_tick,
                }
                if payload != expected:
                    raise FieldPersistenceError(
                        "persistence_receipt_event_mismatch", operation, self._db_path,
                        f"receipt {operation_id!r} event {result.event_id} payload mismatch",
                        stage="receipt_audit.event", field_tick=receipt.field_tick,
                    )

    def _audit_tick_event(
        self,
        *,
        event_id: int,
        before_field_tick: int,
        after_field_tick: int,
        payload: dict,
        registry_dim_ids: tuple[str, ...],
        trajectory_rows: Sequence[tuple] | None = None,
        operation: str = "history_audit",
    ) -> None:
        db_path = self._db_path

        # after = before + 1
        if after_field_tick != before_field_tick + 1:
            raise FieldPersistenceError(
                "persistence_event_tick_invariant",
                operation,
                db_path,
                f"event {event_id}: tick after={after_field_tick} != "
                f"before+1={before_field_tick + 1}",
                stage="history_audit.tick_after",
                field_tick=after_field_tick,
            )

        # Payload before_tick/after_tick must match row
        p_before = payload.get("before_tick")
        p_after = payload.get("after_tick")
        if not isinstance(p_before, int) or isinstance(p_before, bool):
            raise FieldPersistenceError(
                "persistence_event_payload_not_canonical",
                operation,
                db_path,
                f"event {event_id}: payload before_tick invalid",
                stage="history_audit.payload_tick",
            )
        if not isinstance(p_after, int) or isinstance(p_after, bool):
            raise FieldPersistenceError(
                "persistence_event_payload_not_canonical",
                operation,
                db_path,
                f"event {event_id}: payload after_tick invalid",
                stage="history_audit.payload_tick",
            )
        if p_before != before_field_tick:
            raise FieldPersistenceError(
                "persistence_event_payload_tick_mismatch",
                operation,
                db_path,
                f"event {event_id}: payload before_tick {p_before} "
                f"!= row {before_field_tick}",
                stage="history_audit.payload_tick",
            )
        if p_after != after_field_tick:
            raise FieldPersistenceError(
                "persistence_event_payload_tick_mismatch",
                operation,
                db_path,
                f"event {event_id}: payload after_tick {p_after} "
                f"!= row {after_field_tick}",
                stage="history_audit.payload_tick",
            )

        # Trajectory rows must exist, count = registry length
        traj_rows = (
            list(trajectory_rows)
            if trajectory_rows is not None
            else self._conn.execute(
                "SELECT trajectory_id, event_id, field_tick, dimension_ordinal, "
                "dim_id, after_value, after_velocity, after_attractor, "
                "after_slow_baseline, after_ou_acceleration "
                "FROM trajectory_points WHERE event_id = ? "
                "ORDER BY dimension_ordinal",
                (event_id,),
            ).fetchall()
        )

        registry_len = len(registry_dim_ids)
        if len(traj_rows) != registry_len:
            raise FieldPersistenceError(
                "persistence_trajectory_count_mismatch",
                operation,
                db_path,
                f"event {event_id}: trajectory row count {len(traj_rows)} "
                f"!= registry length {registry_len}",
                stage="history_audit.trajectory_count",
                field_tick=after_field_tick,
            )

        for idx, tr in enumerate(traj_rows):
            (
                tid, teid, ttick, tord, tdim,
                tval, tvel, tattr, tbase, tou,
            ) = tr
            # event_id must match current event
            if int(teid) != event_id:
                raise FieldPersistenceError(
                    "persistence_trajectory_orphan",
                    operation,
                    db_path,
                    f"trajectory_id {tid}: event_id {teid} != {event_id}",
                    stage="history_audit.trajectory_event",
                    field_tick=after_field_tick,
                )
            # Ordinals must be 0..len-1 consecutive
            if not isinstance(tord, int) or isinstance(tord, bool):
                raise FieldPersistenceError(
                    "persistence_trajectory_ordinal_invalid",
                    operation,
                    db_path,
                    f"event {event_id}: trajectory ordinal non-int: {tord!r}",
                    stage="history_audit.trajectory_ordinal",
                    field_tick=after_field_tick,
                )
            if int(tord) != idx:
                raise FieldPersistenceError(
                    "persistence_trajectory_ordinal_invalid",
                    operation,
                    db_path,
                    f"event {event_id}: trajectory ordinal {tord} != "
                    f"expected {idx}",
                    stage="history_audit.trajectory_ordinal",
                    field_tick=after_field_tick,
                )
            # dim_id must be a non-empty str
            if not isinstance(tdim, str) or not tdim:
                raise FieldPersistenceError(
                    "persistence_trajectory_dim_mismatch",
                    operation,
                    db_path,
                    f"event {event_id}: dim_id not a non-empty str: {tdim!r}",
                    stage="history_audit.trajectory_dim",
                    field_tick=after_field_tick,
                )
            # dim_id must match registry at this position
            if tdim != registry_dim_ids[idx]:
                raise FieldPersistenceError(
                    "persistence_trajectory_dim_mismatch",
                    operation,
                    db_path,
                    f"event {event_id}: trajectory dim_id {tdim!r} "
                    f"!= registry {registry_dim_ids[idx]!r} at ordinal {idx}",
                    stage="history_audit.trajectory_dim",
                    field_tick=after_field_tick,
                )
            # field_tick must be non-bool int and equal event after_tick
            if not isinstance(ttick, int) or isinstance(ttick, bool):
                raise FieldPersistenceError(
                    "persistence_trajectory_tick_mismatch",
                    operation,
                    db_path,
                    f"event {event_id}: trajectory field_tick non-int: {ttick!r}",
                    stage="history_audit.trajectory_tick",
                    field_tick=after_field_tick,
                )
            if int(ttick) != after_field_tick:
                raise FieldPersistenceError(
                    "persistence_trajectory_tick_mismatch",
                    operation,
                    db_path,
                    f"event {event_id}: trajectory field_tick {ttick} "
                    f"!= event after_tick {after_field_tick}",
                    stage="history_audit.trajectory_tick",
                    field_tick=after_field_tick,
                )
            # All numeric fields must be actual float (SQLite REAL reads as float) and finite
            for fname, fval in (
                ("after_value", tval),
                ("after_velocity", tvel),
                ("after_attractor", tattr),
                ("after_slow_baseline", tbase),
                ("after_ou_acceleration", tou),
            ):
                if not isinstance(fval, float):
                    raise FieldPersistenceError(
                        "persistence_trajectory_non_finite",
                        operation,
                        db_path,
                        f"event {event_id}: trajectory {fname} not a float "
                        f"(got {type(fval).__name__}={fval!r}) for dim {tdim!r}",
                        stage="history_audit.trajectory_finite",
                        field_tick=after_field_tick,
                    )
                if not math.isfinite(fval):
                    raise FieldPersistenceError(
                        "persistence_trajectory_non_finite",
                        operation,
                        db_path,
                        f"event {event_id}: trajectory {fname} non-finite "
                        f"for dim {tdim!r}",
                        stage="history_audit.trajectory_finite",
                        field_tick=after_field_tick,
                    )

    def _audit_attractor_event(
        self,
        *,
        event_id: int,
        before_field_tick: int,
        after_field_tick: int,
        payload: dict,
        registry_dim_ids: tuple[str, ...],
    ) -> None:
        db_path = self._db_path
        operation = "history_audit"

        # before == after == payload field_tick
        if before_field_tick != after_field_tick:
            raise FieldPersistenceError(
                "persistence_event_attractor_tick_invariant",
                operation,
                db_path,
                f"event {event_id}: attractor before={before_field_tick} "
                f"!= after={after_field_tick}",
                stage="history_audit.attractor_tick",
                field_tick=after_field_tick,
            )

        p_tick = payload.get("field_tick")
        if not isinstance(p_tick, int) or isinstance(p_tick, bool):
            raise FieldPersistenceError(
                "persistence_event_payload_not_canonical",
                operation,
                db_path,
                f"event {event_id}: attractor payload field_tick invalid",
                stage="history_audit.payload_tick",
            )
        if p_tick != after_field_tick:
            raise FieldPersistenceError(
                "persistence_event_payload_tick_mismatch",
                operation,
                db_path,
                f"event {event_id}: attractor payload field_tick {p_tick} "
                f"!= row after {after_field_tick}",
                stage="history_audit.payload_tick",
            )

        # dim_id must be in registry
        dim_id = payload.get("dim_id")
        if not isinstance(dim_id, str) or not dim_id:
            raise FieldPersistenceError(
                "persistence_event_payload_not_canonical",
                operation,
                db_path,
                f"event {event_id}: attractor dim_id must be non-empty str, "
                f"got {type(dim_id).__name__}={dim_id!r}",
                stage="history_audit.attractor_dim",
                field_tick=after_field_tick,
            )
        if dim_id not in registry_dim_ids:
            raise FieldPersistenceError(
                "persistence_event_unknown_dim",
                operation,
                db_path,
                f"event {event_id}: attractor dim_id {dim_id!r} not in registry",
                stage="history_audit.attractor_dim",
                field_tick=after_field_tick,
            )

        # source and rationale must be non-empty str
        p_source = payload.get("source")
        if not isinstance(p_source, str) or not p_source.strip():
            raise FieldPersistenceError(
                "persistence_event_payload_not_canonical",
                operation,
                db_path,
                f"event {event_id}: attractor source must be non-empty "
                f"str, got {type(p_source).__name__}={p_source!r}",
                stage="history_audit.attractor_source",
                field_tick=after_field_tick,
            )
        p_rationale = payload.get("rationale")
        if not isinstance(p_rationale, str) or not p_rationale.strip():
            raise FieldPersistenceError(
                "persistence_event_payload_not_canonical",
                operation,
                db_path,
                f"event {event_id}: attractor rationale must be non-empty "
                f"str, got {type(p_rationale).__name__}={p_rationale!r}",
                stage="history_audit.attractor_rationale",
                field_tick=after_field_tick,
            )

        # delta, before_attractor, after_attractor must be strictly float (not int) and finite
        for fname in ("delta", "before_attractor", "after_attractor"):
            val = payload.get(fname)
            if isinstance(val, bool) or not isinstance(val, float) or isinstance(val, int):
                raise FieldPersistenceError(
                    "persistence_event_payload_not_canonical",
                    operation,
                    db_path,
                    f"event {event_id}: attractor {fname} must be float, "
                    f"got {type(val).__name__}={val!r}",
                    stage="history_audit.attractor_fields",
                    field_tick=after_field_tick,
                )
            if not math.isfinite(val):
                raise FieldPersistenceError(
                    "persistence_event_payload_not_canonical",
                    operation,
                    db_path,
                    f"event {event_id}: attractor {fname} non-finite",
                    stage="history_audit.attractor_finite",
                    field_tick=after_field_tick,
                )

        # attractor events must have no trajectory rows
        traj_count = self._conn.execute(
            "SELECT COUNT(*) FROM trajectory_points WHERE event_id = ?",
            (event_id,),
        ).fetchone()[0]
        if int(traj_count) != 0:
            raise FieldPersistenceError(
                "persistence_event_trajectory_for_attractor",
                operation,
                db_path,
                f"event {event_id}: attractor event has {traj_count} "
                "trajectory row(s)",
                stage="history_audit.attractor_trajectory",
                field_tick=after_field_tick,
            )

    # -- lifecycle --------------------------------------------------------

    @property
    def db_path(self) -> str:
        return self._db_path

    def close(self) -> None:
        """Checkpoint WAL with TRUNCATE and close the connection.

        WAL checkpoint failure (exception or busy) is fail-loud: it maps to a
        stable ``FieldPersistenceError``.  The connection is always closed in
        ``finally`` and ``_closed`` is always set.  If a checkpoint error is
        raised, it is re-raised after the connection is closed.
        """
        if self._closed:
            return
        first_error: Exception | None = None
        try:
            row = self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if row is not None:
                # PRAGMA wal_checkpoint(TRUNCATE) returns (busy, log, checkpointed)
                if len(row) != 3:
                    first_error = FieldPersistenceError(
                        "persistence_checkpoint_failed",
                        "close",
                        self._db_path,
                        f"wal_checkpoint(TRUNCATE) returned unexpected "
                        f"shape={len(row)} expected 3 columns",
                        stage="close.checkpoint",
                    )
                else:
                    busy = int(row[0])
                    if busy != 0:
                        first_error = FieldPersistenceError(
                            "persistence_checkpoint_busy",
                            "close",
                            self._db_path,
                            f"wal_checkpoint(TRUNCATE) reported busy={busy}",
                            stage="close.checkpoint",
                        )
        except sqlite3.DatabaseError as exc:
            first_error = FieldPersistenceError(
                "persistence_checkpoint_failed",
                "close",
                self._db_path,
                f"wal_checkpoint(TRUNCATE) error: {exc}",
                stage="close.checkpoint",
            )
            first_error.__cause__ = exc
        finally:
            try:
                self._conn.close()
            except Exception:
                pass
            self._closed = True
        if first_error is not None:
            raise first_error

    def __enter__(self) -> "FieldPersistenceStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
