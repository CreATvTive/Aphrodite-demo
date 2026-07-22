"""P4 task-card 10: companion SQLite store + atomic hard-cap admission.

This module owns the *proactive* companion SQLite database.  It persists the
single [`PressureState`](proactive_pressure.py) and an append-only
admission/decision audit, and it implements the fail-closed hard cap:

* daily admission limit (default ``2``, never configurable weaker than the
  phase-plan ``≤2``);
* minimum admission interval (default ``6h``, never weaker);
* curfew window ``[01:00, 09:00)`` local time (default, only extendable).

The cap is enforced *atomically* with pressure reset: a single
``BEGIN IMMEDIATE`` transaction checks the cap, appends an admission row, and
sets ``pressure = 0``.  Admission (including a failed/unsent admission)
counts toward the daily limit and the minimum interval, so a failed send can
never be retried around the cap.  A cap rejection never clears pressure.

The store never imports runtime/provider/writer/perception modules, never
mutates the frozen field/dialogue/perception schemas, and never calls the
provider.  It accepts an injectable local-time resolver so tests can drive
curfew/day/interval boundaries deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import sqlite3
import threading
from typing import Callable

from app.chatbox.proactive_pressure import PressureState


PROACTIVE_SCHEMA_VERSION = "aphrodite.chatbox.proactive/1"
PROACTIVE_USER_VERSION = 1

# Frozen hard-cap floor from phase-plan-v0.md section A "主动性".
MAX_DAILY_LIMIT_FLOOR = 2
MIN_INTERVAL_SECONDS_FLOOR = 6 * 3600
CURFEW_START_HOUR_FLOOR = 1
CURFEW_END_HOUR_CEILING = 9


class ProactiveStoreError(RuntimeError):
    """Stable, credential-free proactive persistence/cap failure."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class CapConfig:
    """Frozen hard-cap configuration.

    The defaults are the phase-plan floor.  A stricter configuration may lower
    the daily limit, raise the minimum interval, or widen the curfew window.
    Widening the curfew means making the forbidden local-hour window *larger*,
    i.e. lowering ``curfew_start_hour`` or raising ``curfew_end_hour``.  Any
    attempt to weaken the cap below the floor is rejected at construction.
    """

    daily_limit: int = MAX_DAILY_LIMIT_FLOOR
    min_interval_seconds: int = MIN_INTERVAL_SECONDS_FLOOR
    curfew_start_hour: int = CURFEW_START_HOUR_FLOOR
    curfew_end_hour: int = CURFEW_END_HOUR_CEILING

    def __post_init__(self) -> None:
        if not isinstance(self.daily_limit, int) or isinstance(self.daily_limit, bool):
            raise ValueError("daily_limit must be an int")
        if self.daily_limit < 0 or self.daily_limit > MAX_DAILY_LIMIT_FLOOR:
            raise ValueError(
                f"daily_limit must be in [0, {MAX_DAILY_LIMIT_FLOOR}]"
            )
        if not isinstance(self.min_interval_seconds, int) or isinstance(self.min_interval_seconds, bool):
            raise ValueError("min_interval_seconds must be an int")
        if self.min_interval_seconds < MIN_INTERVAL_SECONDS_FLOOR:
            raise ValueError(
                f"min_interval_seconds must be >= {MIN_INTERVAL_SECONDS_FLOOR}"
            )
        if not isinstance(self.curfew_start_hour, int) or isinstance(self.curfew_start_hour, bool):
            raise ValueError("curfew_start_hour must be an int")
        if not isinstance(self.curfew_end_hour, int) or isinstance(self.curfew_end_hour, bool):
            raise ValueError("curfew_end_hour must be an int")
        if not 0 <= self.curfew_start_hour <= 23:
            raise ValueError("curfew_start_hour must be in [0, 23]")
        if not 1 <= self.curfew_end_hour <= 24:
            raise ValueError("curfew_end_hour must be in [1, 24]")
        if self.curfew_start_hour >= self.curfew_end_hour:
            raise ValueError("curfew_start_hour must be < curfew_end_hour")
        # Only allow widening the curfew (lower start or higher end) relative
        # to the floor [1, 9).
        if self.curfew_start_hour > CURFEW_START_HOUR_FLOOR:
            raise ValueError("curfew_start_hour may only stay or widen (lower)")
        if self.curfew_end_hour < CURFEW_END_HOUR_CEILING:
            raise ValueError("curfew_end_hour may only stay or widen (raise)")


@dataclass(frozen=True, slots=True)
class LocalTime:
    """Local time components used by the cap policy."""

    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: int


LocalTimeResolver = Callable[[int], LocalTime | None]
"""Injectable resolver from a UTC unix-nanosecond stamp to local time.

Returns ``None`` when the clock is unusable (rollback, DST gap, resolver
error).  The store treats ``None`` as fail-closed deny.
"""


def system_local_time(ns: int) -> LocalTime | None:
    """Resolve a Unix-nanosecond timestamp with the host local timezone.

    Conversion is range checked and fail-closed. Tests may inject a resolver
    for exact midnight/DST boundary control; production uses this resolver.
    """
    if not isinstance(ns, int) or isinstance(ns, bool) or ns < 0:
        return None
    try:
        local = datetime.fromtimestamp(ns / 1_000_000_000.0, tz=timezone.utc).astimezone()
    except (OverflowError, OSError, ValueError):
        return None
    return LocalTime(
        year=local.year,
        month=local.month,
        day=local.day,
        hour=local.hour,
        minute=local.minute,
        second=local.second,
    )


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    """Result of an admission attempt.

    ``admitted`` is True iff the cap allowed the admission and the pressure
    was reset to 0 in the same transaction.  ``admission_id`` is a stable,
    unique, restart-safe id for the admission row.  ``reject_reason`` is a
    short stable code when denied.
    """

    admitted: bool
    admission_id: str | None
    reject_reason: str | None
    pressure_after: float


@dataclass(frozen=True, slots=True)
class _StoredState:
    pressure: float
    last_field_tick: int | None


class ProactiveStore:
    """Single-owner proactive companion store + atomic cap admission."""

    _TABLES = {"proactive_meta", "proactive_pressure", "proactive_admissions"}
    _INDEXES = {"idx_proactive_admissions_ns"}
    _TRIGGERS = {
        "trg_no_update_proactive_admissions",
        "trg_no_delete_proactive_admissions",
    }
    _DDL = (
        "CREATE TABLE proactive_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        """CREATE TABLE proactive_pressure (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            pressure REAL NOT NULL CHECK(pressure >= 0.0),
            last_field_tick INTEGER,
            updated_ns INTEGER NOT NULL CHECK(updated_ns >= 0)
        )""",
        """CREATE TABLE proactive_admissions (
            admission_id TEXT PRIMARY KEY,
            admitted_ns INTEGER NOT NULL CHECK(admitted_ns >= 0),
            local_year INTEGER NOT NULL,
            local_month INTEGER NOT NULL CHECK(local_month BETWEEN 1 AND 12),
            local_day INTEGER NOT NULL CHECK(local_day BETWEEN 1 AND 31),
            local_hour INTEGER NOT NULL CHECK(local_hour BETWEEN 0 AND 23),
            outcome TEXT NOT NULL CHECK(outcome IN ('admitted','send_failed','send_succeeded')),
            detail TEXT NOT NULL
        )""",
        "CREATE INDEX idx_proactive_admissions_ns ON proactive_admissions(admitted_ns)",
        """CREATE TRIGGER trg_no_update_proactive_admissions BEFORE UPDATE ON proactive_admissions
        BEGIN SELECT RAISE(ABORT, 'proactive_admissions is append-only'); END""",
        """CREATE TRIGGER trg_no_delete_proactive_admissions BEFORE DELETE ON proactive_admissions
        BEGIN SELECT RAISE(ABORT, 'proactive_admissions is append-only'); END""",
    )

    def __init__(
        self,
        db_path: str,
        *,
        cap: CapConfig | None = None,
        local_time_resolver: LocalTimeResolver | None = None,
    ) -> None:
        if not isinstance(db_path, str) or not db_path:
            raise ValueError("db_path must be a non-empty string")
        self._db_path = db_path
        self._cap = cap if cap is not None else CapConfig()
        self._resolver = local_time_resolver if local_time_resolver is not None else system_local_time
        self._closed = False
        self._lock = threading.Lock()
        try:
            self._conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            quick = self._conn.execute("PRAGMA quick_check").fetchone()
            if quick is None or str(quick[0]).lower() != "ok":
                raise ProactiveStoreError("proactive_database_corrupt", "quick_check failed")
            self._ensure_schema()
        except ProactiveStoreError:
            if hasattr(self, "_conn"):
                self._conn.close()
            self._closed = True
            raise
        except sqlite3.DatabaseError as exc:
            if hasattr(self, "_conn"):
                self._conn.close()
            self._closed = True
            raise ProactiveStoreError("proactive_open_failed", "database open failed") from exc

    # -- schema ----------------------------------------------------------

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
                    "INSERT INTO proactive_meta(key,value) VALUES('schema_version',?)",
                    (PROACTIVE_SCHEMA_VERSION,),
                )
                self._conn.execute(
                    "INSERT INTO proactive_pressure(id,pressure,last_field_tick,updated_ns) "
                    "VALUES(1,0.0,NULL,0)"
                )
                self._conn.execute(f"PRAGMA user_version={PROACTIVE_USER_VERSION}")
                self._conn.execute("COMMIT")
            except sqlite3.DatabaseError as exc:
                self._conn.execute("ROLLBACK")
                raise ProactiveStoreError(
                    "proactive_schema_bootstrap_failed", "schema bootstrap failed"
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
            raise ProactiveStoreError("proactive_schema_mismatch", "table set mismatch")
        if self._names("index") != self._INDEXES:
            raise ProactiveStoreError("proactive_schema_mismatch", "index set mismatch")
        if self._names("trigger") != self._TRIGGERS:
            raise ProactiveStoreError("proactive_schema_mismatch", "trigger set mismatch")
        user_version = self._conn.execute("PRAGMA user_version").fetchone()
        schema_version = self._conn.execute(
            "SELECT value FROM proactive_meta WHERE key='schema_version'"
        ).fetchone()
        if user_version is None or int(user_version[0]) != PROACTIVE_USER_VERSION:
            raise ProactiveStoreError("proactive_schema_version_mismatch", "user version mismatch")
        if schema_version is None or schema_version[0] != PROACTIVE_SCHEMA_VERSION:
            raise ProactiveStoreError("proactive_schema_version_mismatch", "meta version mismatch")

    # -- pressure state --------------------------------------------------

    def load_pressure_state(self) -> PressureState:
        row = self._conn.execute(
            "SELECT pressure, last_field_tick FROM proactive_pressure WHERE id=1"
        ).fetchone()
        if row is None:
            raise ProactiveStoreError("proactive_state_missing", "pressure row missing")
        pressure = float(row[0])
        last_tick = None if row[1] is None else int(row[1])
        return PressureState(pressure=pressure, last_field_tick=last_tick)

    def save_pressure_state(self, state: PressureState, *, updated_ns: int) -> None:
        if not isinstance(updated_ns, int) or isinstance(updated_ns, bool) or updated_ns < 0:
            raise ValueError("updated_ns must be a non-negative int")
        try:
            self._conn.execute(
                "UPDATE proactive_pressure SET pressure=?, last_field_tick=?, updated_ns=? WHERE id=1",
                (float(state.pressure), state.last_field_tick, updated_ns),
            )
        except sqlite3.DatabaseError as exc:
            raise ProactiveStoreError("proactive_write_failed", "pressure update failed") from exc

    # -- cap policy ------------------------------------------------------

    def _resolve_local(self, ns: int) -> LocalTime | None:
        try:
            local = self._resolver(ns)
        except Exception:
            return None
        if local is None:
            return None
        fields = (local.year, local.month, local.day, local.hour, local.minute, local.second)
        if any(not isinstance(value, int) or isinstance(value, bool) for value in fields):
            return None
        try:
            datetime(local.year, local.month, local.day, local.hour, local.minute, local.second)
        except ValueError:
            return None
        return local

    def _in_curfew(self, local: LocalTime) -> bool:
        start = self._cap.curfew_start_hour
        end = self._cap.curfew_end_hour
        hour = local.hour
        if start < end:
            return start <= hour < end
        # Wrap-around curfew (e.g. 22..6) — not used by defaults but supported.
        return hour >= start or hour < end

    def _admission_count_for_local_day(self, local: LocalTime) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM proactive_admissions "
            "WHERE outcome='admitted' AND local_year=? AND local_month=? AND local_day=?",
            (local.year, local.month, local.day),
        ).fetchone()
        return int(row[0]) if row is not None else 0

    def _last_admission_ns(self) -> int | None:
        row = self._conn.execute(
            "SELECT MAX(admitted_ns) FROM proactive_admissions WHERE outcome='admitted'"
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return int(row[0])

    # -- atomic admission ------------------------------------------------

    def try_admit(
        self,
        *,
        current_ns: int,
        pressure: float,
    ) -> AdmissionDecision:
        """Atomically check the cap, append an admission, and reset pressure.

        On any cap rejection or resolver/clock/schema/SQLite failure the
        pressure is *preserved* and no admission row is written.  On success
        the pressure is set to 0 in the same ``BEGIN IMMEDIATE`` transaction
        that appends the admission row, so the cap and the reset are atomic.
        """
        if not isinstance(current_ns, int) or isinstance(current_ns, bool) or current_ns < 0:
            return AdmissionDecision(
                admitted=False,
                admission_id=None,
                reject_reason="invalid_clock",
                pressure_after=pressure,
            )
        if not isinstance(pressure, (int, float)) or isinstance(pressure, bool):
            return AdmissionDecision(
                admitted=False,
                admission_id=None,
                reject_reason="invalid_pressure",
                pressure_after=pressure,
            )
        pressure_f = float(pressure)
        if not _is_finite_nonneg(pressure_f):
            return AdmissionDecision(
                admitted=False,
                admission_id=None,
                reject_reason="invalid_pressure",
                pressure_after=pressure_f,
            )
        local = self._resolve_local(current_ns)
        if local is None:
            return AdmissionDecision(
                admitted=False,
                admission_id=None,
                reject_reason="clock_unresolved",
                pressure_after=pressure_f,
            )
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
            except sqlite3.DatabaseError as exc:
                # Cannot acquire write lock — fail closed, preserve pressure.
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.DatabaseError:
                    pass
                return AdmissionDecision(
                    admitted=False,
                    admission_id=None,
                    reject_reason="busy",
                    pressure_after=pressure_f,
                )
            try:
                if self._in_curfew(local):
                    self._conn.execute("ROLLBACK")
                    return AdmissionDecision(
                        admitted=False,
                        admission_id=None,
                        reject_reason="curfew",
                        pressure_after=pressure_f,
                    )
                count_today = self._admission_count_for_local_day(local)
                if count_today >= self._cap.daily_limit:
                    self._conn.execute("ROLLBACK")
                    return AdmissionDecision(
                        admitted=False,
                        admission_id=None,
                        reject_reason="daily_limit",
                        pressure_after=pressure_f,
                    )
                last_ns = self._last_admission_ns()
                if last_ns is not None and (current_ns - last_ns) < self._cap.min_interval_seconds * 1_000_000_000:
                    self._conn.execute("ROLLBACK")
                    return AdmissionDecision(
                        admitted=False,
                        admission_id=None,
                        reject_reason="min_interval",
                        pressure_after=pressure_f,
                    )
                admission_id = f"proactive:{current_ns}:{local.year:04d}{local.month:02d}{local.day:02d}{local.hour:02d}{local.minute:02d}{local.second:02d}"
                # Guard against a pathological id collision within the same second.
                existing = self._conn.execute(
                    "SELECT 1 FROM proactive_admissions WHERE admission_id=?",
                    (admission_id,),
                ).fetchone()
                if existing is not None:
                    admission_id = f"{admission_id}:{count_today}"
                self._conn.execute(
                    "INSERT INTO proactive_admissions("
                    "admission_id,admitted_ns,local_year,local_month,local_day,local_hour,outcome,detail"
                    ") VALUES(?,?,?,?,?,?,?,?)",
                    (
                        admission_id,
                        current_ns,
                        local.year,
                        local.month,
                        local.day,
                        local.hour,
                        "admitted",
                        json.dumps({"pressure_before": pressure_f}, separators=(",", ":"), ensure_ascii=False),
                    ),
                )
                self._conn.execute(
                    "UPDATE proactive_pressure SET pressure=0.0, updated_ns=? WHERE id=1",
                    (current_ns,),
                )
                self._conn.execute("COMMIT")
                return AdmissionDecision(
                    admitted=True,
                    admission_id=admission_id,
                    reject_reason=None,
                    pressure_after=0.0,
                )
            except sqlite3.DatabaseError as exc:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.DatabaseError:
                    pass
                return AdmissionDecision(
                    admitted=False,
                    admission_id=None,
                    reject_reason="database_error",
                    pressure_after=pressure_f,
                )
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.DatabaseError:
                    pass
                return AdmissionDecision(
                    admitted=False,
                    admission_id=None,
                    reject_reason="internal_error",
                    pressure_after=pressure_f,
                )

    # -- audit -----------------------------------------------------------

    def record_outcome(
        self,
        *,
        admission_id: str,
        outcome: str,
        detail: dict,
        ns: int,
    ) -> None:
        """Append a second audit row for the send outcome of an admission.

        ``outcome`` is ``send_succeeded`` or ``send_failed``.  This does not
        affect the cap (the admission already counted); it only records the
        observable send result for restart-safe auditing.
        """
        if outcome not in {"send_succeeded", "send_failed"}:
            raise ValueError("outcome must be send_succeeded or send_failed")
        if not isinstance(admission_id, str) or not admission_id:
            raise ValueError("admission_id must be a non-empty string")
        if not isinstance(detail, dict):
            raise ValueError("detail must be a dict")
        if not isinstance(ns, int) or isinstance(ns, bool) or ns < 0:
            raise ValueError("ns must be a non-negative int")
        local = self._resolve_local(ns)
        if local is None:
            # Cannot place an outcome row without a local day; drop it
            # silently rather than raising — the admission row already
            # records the cap decision.
            return
        outcome_id = f"{admission_id}:outcome:{outcome}"
        try:
            self._conn.execute(
                "INSERT INTO proactive_admissions("
                "admission_id,admitted_ns,local_year,local_month,local_day,local_hour,outcome,detail"
                ") VALUES(?,?,?,?,?,?,?,?)",
                (
                    outcome_id,
                    ns,
                    local.year,
                    local.month,
                    local.day,
                    local.hour,
                    outcome,
                    json.dumps(detail, separators=(",", ":"), ensure_ascii=False),
                ),
            )
        except sqlite3.DatabaseError:
            # Audit is best-effort relative to the cap; never block the
            # coordinator on an audit write failure.
            return

    def admission_count_today(self, *, ns: int) -> int:
        local = self._resolve_local(ns)
        if local is None:
            return 0
        return self._admission_count_for_local_day(local)

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def cap(self) -> CapConfig:
        return self._cap

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            self._conn.close()


def _is_finite_nonneg(value: float) -> bool:
    return math.isfinite(value) and value >= 0.0
