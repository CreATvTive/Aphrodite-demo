"""P3 task-card 7: append-only SQLite persistence for perception events.

A small companion SQLite database (mirroring the dialogue persistence
pattern) so the frozen P1 field schema is not weakened or migrated.  Events
are append-only, deduplicated by ``event_id``.  Consumption is tracked in a
separate append-only ``perception_consumption`` table so the event log stays
truly append-only (the trigger blocks UPDATE) and a restart can skip events
already applied to the field without re-replaying them.

Service code uses this API and never executes SQL directly.  The browser /
orchestration modules never touch this connection.

Imports are restricted to the standard library and
[`perception_config`](perception_config.py) / [`perception_schema`](perception_schema.py).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3

from app.chatbox.perception_config import (
    PERCEPTION_PERSISTENCE_SCHEMA_VERSION,
    PERCEPTION_PERSISTENCE_USER_VERSION,
)
from app.chatbox.perception_schema import PerceptionEvent


class PerceptionPersistenceError(RuntimeError):
    """Credential-free, stable persistence-boundary failure."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class PersistedEvent:
    event_id: str
    session_id: str
    kind: str
    observed_at: int
    payload_json: str
    source: str


class PerceptionPersistenceStore:
    """Single-owner append-only perception event store."""

    _TABLES = {"perception_meta", "perception_events", "perception_consumption"}
    _INDEXES = {
        "idx_perception_events_observed",
        "idx_perception_consumption_event",
    }
    _TRIGGERS = {
        "trg_no_update_perception_events",
        "trg_no_delete_perception_events",
        "trg_no_update_perception_consumption",
        "trg_no_delete_perception_consumption",
    }
    _DDL = (
        "CREATE TABLE perception_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        """CREATE TABLE perception_events (
            row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            session_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            observed_at INTEGER NOT NULL CHECK(observed_at >= 0),
            payload_json TEXT NOT NULL,
            source TEXT NOT NULL,
            utc_unix_ns INTEGER NOT NULL CHECK(utc_unix_ns >= 0)
        )""",
        """CREATE TABLE perception_consumption (
            consumption_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            consumed_at_unix_ns INTEGER NOT NULL CHECK(consumed_at_unix_ns >= 0)
        )""",
        "CREATE INDEX idx_perception_events_observed ON perception_events(observed_at, row_id)",
        "CREATE INDEX idx_perception_consumption_event ON perception_consumption(event_id)",
        """CREATE TRIGGER trg_no_update_perception_events BEFORE UPDATE ON perception_events
        BEGIN SELECT RAISE(ABORT, 'perception_events is append-only'); END""",
        """CREATE TRIGGER trg_no_delete_perception_events BEFORE DELETE ON perception_events
        BEGIN SELECT RAISE(ABORT, 'perception_events is append-only'); END""",
        """CREATE TRIGGER trg_no_update_perception_consumption BEFORE UPDATE ON perception_consumption
        BEGIN SELECT RAISE(ABORT, 'perception_consumption is append-only'); END""",
        """CREATE TRIGGER trg_no_delete_perception_consumption BEFORE DELETE ON perception_consumption
        BEGIN SELECT RAISE(ABORT, 'perception_consumption is append-only'); END""",
    )

    def __init__(self, db_path: str) -> None:
        if not isinstance(db_path, str) or not db_path:
            raise ValueError("db_path must be a non-empty string")
        self._db_path = db_path
        self._closed = False
        try:
            self._conn = sqlite3.connect(db_path, isolation_level=None)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            quick = self._conn.execute("PRAGMA quick_check").fetchone()
            if quick is None or str(quick[0]).lower() != "ok":
                raise PerceptionPersistenceError("perception_database_corrupt", "quick_check failed")
            self._ensure_schema()
        except PerceptionPersistenceError:
            if hasattr(self, "_conn"):
                self._conn.close()
            self._closed = True
            raise
        except sqlite3.DatabaseError as exc:
            if hasattr(self, "_conn"):
                self._conn.close()
            self._closed = True
            raise PerceptionPersistenceError("perception_open_failed", "database open failed") from exc

    def _ensure_schema(self) -> None:
        objects = self._conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table','index','trigger') "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchone()
        if objects is not None and int(objects[0]) == 0:
            self._conn.execute("BEGIN")
            try:
                for ddl in self._DDL:
                    self._conn.execute(ddl)
                self._conn.execute(
                    "INSERT INTO perception_meta(key,value) VALUES('schema_version',?)",
                    (PERCEPTION_PERSISTENCE_SCHEMA_VERSION,),
                )
                self._conn.execute(f"PRAGMA user_version={PERCEPTION_PERSISTENCE_USER_VERSION}")
                self._conn.execute("COMMIT")
            except sqlite3.DatabaseError as exc:
                self._conn.execute("ROLLBACK")
                raise PerceptionPersistenceError(
                    "perception_schema_bootstrap_failed", "schema bootstrap failed"
                ) from exc
        self._verify_schema()

    def _names(self, object_type: str) -> set[str]:
        return {
            str(row[0])
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type=? AND name NOT LIKE 'sqlite_%'",
                (object_type,),
            ).fetchall()
        }

    def _verify_schema(self) -> None:
        if self._names("table") != self._TABLES:
            raise PerceptionPersistenceError("perception_schema_mismatch", "table set mismatch")
        if self._names("index") != self._INDEXES:
            raise PerceptionPersistenceError("perception_schema_mismatch", "index set mismatch")
        if self._names("trigger") != self._TRIGGERS:
            raise PerceptionPersistenceError("perception_schema_mismatch", "trigger set mismatch")
        user_version = self._conn.execute("PRAGMA user_version").fetchone()
        schema_version = self._conn.execute(
            "SELECT value FROM perception_meta WHERE key='schema_version'"
        ).fetchone()
        if user_version is None or int(user_version[0]) != PERCEPTION_PERSISTENCE_USER_VERSION:
            raise PerceptionPersistenceError("perception_schema_version_mismatch", "user version mismatch")
        if schema_version is None or schema_version[0] != PERCEPTION_PERSISTENCE_SCHEMA_VERSION:
            raise PerceptionPersistenceError("perception_schema_version_mismatch", "meta version mismatch")

    @property
    def db_path(self) -> str:
        return self._db_path

    def event_exists(self, event_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM perception_events WHERE event_id=? LIMIT 1",
            (event_id,),
        ).fetchone()
        return row is not None

    def append_event(self, event: PerceptionEvent, *, utc_unix_ns: int) -> bool:
        """Append one event.  Returns True if inserted, False if ``event_id`` already existed.

        Idempotent: a duplicate ``event_id`` is a no-op (no raise, no insert).
        """
        if not isinstance(utc_unix_ns, int) or isinstance(utc_unix_ns, bool) or utc_unix_ns < 0:
            raise ValueError("utc_unix_ns must be a non-negative int")
        payload_json = json.dumps(
            dict(event.payload), separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        try:
            self._conn.execute(
                "INSERT INTO perception_events"
                "(event_id,session_id,kind,observed_at,payload_json,source,utc_unix_ns) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    event.event_id,
                    event.session_id,
                    event.kind,
                    int(event.observed_at),
                    payload_json,
                    event.source,
                    int(utc_unix_ns),
                ),
            )
        except sqlite3.IntegrityError:
            # UNIQUE(event_id) collision → idempotent skip.
            return False
        return True

    def record_consumption(self, event_id: str, *, utc_unix_ns: int) -> bool:
        """Record that ``event_id`` has been applied to the field.

        Idempotent: a duplicate consumption record is a no-op.  Returns True
        if a new consumption row was inserted, False if it already existed.
        """
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("event_id must be a non-empty string")
        if not isinstance(utc_unix_ns, int) or isinstance(utc_unix_ns, bool) or utc_unix_ns < 0:
            raise ValueError("utc_unix_ns must be a non-negative int")
        try:
            self._conn.execute(
                "INSERT INTO perception_consumption(event_id,consumed_at_unix_ns) VALUES(?,?)",
                (event_id, int(utc_unix_ns)),
            )
        except sqlite3.IntegrityError:
            return False
        return True

    def is_consumed(self, event_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM perception_consumption WHERE event_id=? LIMIT 1",
            (event_id,),
        ).fetchone()
        return row is not None

    def read_unconsumed(self, *, limit: int = 512) -> tuple[PersistedEvent, ...]:
        """Read unconsumed events ordered by ``observed_at`` then ``row_id``."""
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 5000:
            raise ValueError("limit must be in [1, 5000]")
        rows = self._conn.execute(
            "SELECT e.event_id,e.session_id,e.kind,e.observed_at,e.payload_json,e.source "
            "FROM perception_events e "
            "LEFT JOIN perception_consumption c ON c.event_id = e.event_id "
            "WHERE c.event_id IS NULL "
            "ORDER BY e.observed_at ASC, e.row_id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return tuple(
            PersistedEvent(
                event_id=str(row[0]),
                session_id=str(row[1]),
                kind=str(row[2]),
                observed_at=int(row[3]),
                payload_json=str(row[4]),
                source=str(row[5]),
            )
            for row in rows
        )

    def count_events(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM perception_events").fetchone()[0])

    def count_consumed(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM perception_consumption").fetchone()[0])

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            self._conn.close()
