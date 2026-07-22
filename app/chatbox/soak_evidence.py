"""P4.11 companion evidence store, replay audit, and read-only observer."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Sequence

from app.chatbox.field_persistence import TrajectoryFrame, TrajectoryPoint
from app.chatbox.soak_detection import (
    EvidenceCorruptError,
    FORMAL_PROFILE,
    REPORT_SCHEMA_VERSION,
    SOAK_CONTRACT_VERSION,
    SoakProfile,
    SoakState,
    StreamingSoakDetector,
    TEST_PROFILE,
    ValueFrame,
    canonical_json_bytes,
    canonical_sha256,
)


EVIDENCE_SCHEMA_VERSION = "aphrodite.chatbox.soak-evidence/1"
EVIDENCE_USER_VERSION = 1
CONTRACT_PATH = Path(__file__).parents[2] / "docs" / "chatbox" / "contracts" / "p4-task11-soak-detection.md"


class SoakEvidenceError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def contract_file_sha256() -> str:
    return hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()


def _frame_payload(
    frame: object,
    detector: StreamingSoakDetector,
) -> tuple[ValueFrame, bytes, str]:
    normalized = ValueFrame.from_input(frame, detector.registry)
    point_by_id = {getattr(point, "dim_id"): point for point in getattr(frame, "dimensions")}
    primitive = {
        "cursor": normalized.cursor,
        "boot_id": normalized.boot_id,
        "field_tick": normalized.field_tick,
        "utc_unix_ns": normalized.utc_unix_ns,
        "dimensions": [
            {
                "ordinal": int(getattr(point_by_id[dim_id], "ordinal")),
                "dim_id": dim_id,
                "value": float(getattr(point_by_id[dim_id], "value")),
                "velocity": float(getattr(point_by_id[dim_id], "velocity")),
                "attractor": float(getattr(point_by_id[dim_id], "attractor")),
                "slow_baseline": float(getattr(point_by_id[dim_id], "slow_baseline")),
                "ou_acceleration": float(getattr(point_by_id[dim_id], "ou_acceleration")),
            }
            for dim_id in normalized.order
        ],
    }
    payload = canonical_json_bytes(primitive)
    return normalized, payload, hashlib.sha256(payload).hexdigest()


def _decode_frame(payload: bytes) -> TrajectoryFrame:
    try:
        raw = json.loads(payload.decode("utf-8"))
        points = tuple(TrajectoryPoint(
            ordinal=int(item["ordinal"]), dim_id=str(item["dim_id"]),
            value=float(item["value"]), velocity=float(item["velocity"]),
            attractor=float(item["attractor"]), slow_baseline=float(item["slow_baseline"]),
            ou_acceleration=float(item["ou_acceleration"]),
        ) for item in raw["dimensions"])
        return TrajectoryFrame(
            cursor=int(raw["cursor"]), boot_id=str(raw["boot_id"]),
            field_tick=int(raw["field_tick"]), utc_unix_ns=int(raw["utc_unix_ns"]),
            dimensions=points,
        )
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise SoakEvidenceError("evidence_frame_decode_failed", "stored frame is malformed") from exc


class SoakEvidenceStore:
    _TABLES = {"soak_meta", "soak_frames", "soak_events"}
    _INDEXES = {"idx_soak_frames_boot_tick"}
    _TRIGGERS = {
        "trg_soak_meta_no_update", "trg_soak_meta_no_delete",
        "trg_soak_frames_no_update", "trg_soak_frames_no_delete",
        "trg_soak_events_no_update", "trg_soak_events_no_delete",
    }

    def __init__(
        self,
        db_path: str,
        report_path: str,
        registry: object,
        *,
        profile: SoakProfile = TEST_PROFILE,
        read_only: bool = False,
    ) -> None:
        if not db_path or not report_path:
            raise ValueError("db_path and report_path must be non-empty")
        if os.path.abspath(db_path) == os.path.abspath(report_path):
            raise ValueError("evidence DB and report paths must differ")
        self.db_path = db_path
        self.report_path = report_path
        self.profile = profile
        self._read_only = read_only
        self.detector = StreamingSoakDetector(registry, profile=profile)
        self._closed = False
        self._failed_detail: str | None = None
        self._lock = threading.Lock()
        self._last_published_block_count = -1
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            if read_only:
                uri = f"file:{Path(db_path).resolve().as_posix()}?mode=ro"
                self._conn = sqlite3.connect(uri, uri=True, isolation_level=None, check_same_thread=False)
            else:
                self._conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            quick = self._conn.execute("PRAGMA quick_check").fetchone()
            if quick is None or str(quick[0]).lower() != "ok":
                raise SoakEvidenceError("evidence_database_corrupt", "quick_check failed")
            self._ensure_schema()
            self._replay()
            self._audit_existing_report()
            if not read_only:
                self.detector.reopen_for_append()
            self.publish_report()
        except BaseException:
            if hasattr(self, "_conn"):
                self._conn.close()
            self._closed = True
            raise

    @property
    def state(self) -> SoakState:
        return self.detector.state

    def _names(self, kind: str) -> set[str]:
        return {str(row[0]) for row in self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type=? AND name NOT LIKE 'sqlite_%'", (kind,)
        ).fetchall()}

    def _ensure_schema(self) -> None:
        count = self._conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table','index','trigger') AND name NOT LIKE 'sqlite_%'"
        ).fetchone()
        if count is not None and int(count[0]) == 0:
            if self._read_only:
                raise SoakEvidenceError("evidence_schema_mismatch", "companion schema is absent")
            try:
                self._conn.executescript("""
                    BEGIN IMMEDIATE;
                    CREATE TABLE soak_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE soak_frames(
                        cursor INTEGER PRIMARY KEY,
                        boot_id TEXT NOT NULL,
                        field_tick INTEGER NOT NULL,
                        utc_unix_ns INTEGER NOT NULL,
                        payload BLOB NOT NULL,
                        frame_sha256 TEXT NOT NULL CHECK(length(frame_sha256)=64),
                        UNIQUE(boot_id, field_tick)
                    );
                    CREATE INDEX idx_soak_frames_boot_tick ON soak_frames(boot_id,field_tick);
                    CREATE TABLE soak_events(
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        kind TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        event_sha256 TEXT NOT NULL CHECK(length(event_sha256)=64)
                    );
                    CREATE TRIGGER trg_soak_meta_no_update BEFORE UPDATE ON soak_meta BEGIN SELECT RAISE(ABORT,'append-only'); END;
                    CREATE TRIGGER trg_soak_meta_no_delete BEFORE DELETE ON soak_meta BEGIN SELECT RAISE(ABORT,'append-only'); END;
                    CREATE TRIGGER trg_soak_frames_no_update BEFORE UPDATE ON soak_frames BEGIN SELECT RAISE(ABORT,'append-only'); END;
                    CREATE TRIGGER trg_soak_frames_no_delete BEFORE DELETE ON soak_frames BEGIN SELECT RAISE(ABORT,'append-only'); END;
                    CREATE TRIGGER trg_soak_events_no_update BEFORE UPDATE ON soak_events BEGIN SELECT RAISE(ABORT,'append-only'); END;
                    CREATE TRIGGER trg_soak_events_no_delete BEFORE DELETE ON soak_events BEGIN SELECT RAISE(ABORT,'append-only'); END;
                """)
                metadata = {
                    "schema_version": EVIDENCE_SCHEMA_VERSION,
                    "contract_version": SOAK_CONTRACT_VERSION,
                    "contract_file_sha256": contract_file_sha256(),
                    "registry_sha256": self.detector.registry.sha256,
                    "registry_json": canonical_json_bytes(self.detector.registry.primitive()).decode("utf-8").rstrip("\n"),
                    "profile_json": canonical_json_bytes(profile := self.profile.primitive()).decode("utf-8").rstrip("\n"),
                    "profile_sha256": canonical_sha256(profile),
                }
                self._conn.executemany("INSERT INTO soak_meta(key,value) VALUES(?,?)", metadata.items())
                self._conn.execute(
                    "INSERT INTO soak_meta(key,value) VALUES('schema_sha256',?)",
                    (self._schema_sha256(),),
                )
                self._conn.execute(f"PRAGMA user_version={EVIDENCE_USER_VERSION}")
                self._conn.execute("COMMIT")
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise
        self._verify_schema_and_meta()

    def _verify_schema_and_meta(self) -> None:
        if self._names("table") != self._TABLES or self._names("index") != self._INDEXES or self._names("trigger") != self._TRIGGERS:
            raise SoakEvidenceError("evidence_schema_mismatch", "schema object set mismatch")
        version = self._conn.execute("PRAGMA user_version").fetchone()
        if version is None or int(version[0]) != EVIDENCE_USER_VERSION:
            raise SoakEvidenceError("evidence_schema_mismatch", "user_version mismatch")
        meta = dict(self._conn.execute("SELECT key,value FROM soak_meta").fetchall())
        expected = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "contract_version": SOAK_CONTRACT_VERSION,
            "contract_file_sha256": contract_file_sha256(),
            "registry_sha256": self.detector.registry.sha256,
            "registry_json": canonical_json_bytes(self.detector.registry.primitive()).decode("utf-8").rstrip("\n"),
            "profile_json": canonical_json_bytes(self.profile.primitive()).decode("utf-8").rstrip("\n"),
            "profile_sha256": canonical_sha256(self.profile.primitive()),
            "schema_sha256": self._schema_sha256(),
        }
        if meta != expected:
            raise SoakEvidenceError("evidence_metadata_mismatch", "registry/profile/contract metadata mismatch")

    def _schema_sha256(self) -> str:
        objects = [
            {"type": str(row[0]), "name": str(row[1]), "sql": str(row[2])}
            for row in self._conn.execute(
                "SELECT type,name,sql FROM sqlite_master "
                "WHERE type IN ('table','index','trigger') AND name NOT LIKE 'sqlite_%' "
                "ORDER BY type,name"
            ).fetchall()
        ]
        return canonical_sha256(objects)

    def _replay(self) -> None:
        events_by_cursor: dict[int | None, list[tuple[int, str, dict[str, object]]]] = {}
        for event_id, kind, payload, event_hash in self._conn.execute(
            "SELECT event_id,kind,payload,event_sha256 FROM soak_events ORDER BY event_id"
        ):
            if hashlib.sha256(str(payload).encode("utf-8")).hexdigest() != str(event_hash):
                self.detector.mark_external_corruption("stored_event_hash_mismatch", f"event {event_id}")
                return
            try:
                parsed = json.loads(str(payload))
                canonical = canonical_json_bytes(parsed).decode("utf-8").rstrip("\n")
            except (TypeError, ValueError, json.JSONDecodeError):
                self.detector.mark_external_corruption("stored_event_decode_failed", f"event {event_id}")
                return
            if canonical != str(payload) or not isinstance(parsed, dict):
                self.detector.mark_external_corruption("stored_event_not_canonical", f"event {event_id}")
                return
            after_cursor = parsed.get("after_cursor")
            if after_cursor is not None and (
                not isinstance(after_cursor, int) or isinstance(after_cursor, bool) or after_cursor < 0
            ):
                self.detector.mark_external_corruption("stored_event_boundary_invalid", f"event {event_id}")
                return
            events_by_cursor.setdefault(after_cursor, []).append(
                (int(event_id), str(kind), parsed)
            )

        replayed_by_cursor: dict[int, TrajectoryFrame] = {}
        for row in self._conn.execute(
            "SELECT cursor,boot_id,field_tick,utc_unix_ns,payload,frame_sha256 FROM soak_frames ORDER BY cursor"
        ):
            payload = bytes(row[4])
            digest = hashlib.sha256(payload).hexdigest()
            if digest != str(row[5]):
                self.detector.mark_external_corruption("stored_frame_hash_mismatch", f"cursor {row[0]}")
                break
            frame = _decode_frame(payload)
            if (frame.cursor, frame.boot_id, frame.field_tick, frame.utc_unix_ns) != (int(row[0]), str(row[1]), int(row[2]), int(row[3])):
                self.detector.mark_external_corruption("stored_frame_column_mismatch", f"cursor {row[0]}")
                break
            try:
                normalized, canonical_payload, _ = _frame_payload(frame, self.detector)
            except EvidenceCorruptError as exc:
                self.detector.mark_external_corruption(exc.code, exc.detail)
                break
            if canonical_payload != payload:
                self.detector.mark_external_corruption("stored_frame_not_canonical", f"cursor {row[0]}")
                break
            self.detector.ingest_validated_frame(normalized)
            replayed_by_cursor[frame.cursor] = frame
            self._replay_events(events_by_cursor.pop(frame.cursor, ()), replayed_by_cursor)
            if self.detector.state is SoakState.EVIDENCE_CORRUPT:
                return
        self._replay_events(events_by_cursor.pop(None, ()), replayed_by_cursor)
        if events_by_cursor and self.detector.state is not SoakState.EVIDENCE_CORRUPT:
            event_id = min(item[0] for events in events_by_cursor.values() for item in events)
            self.detector.mark_external_corruption(
                "stored_event_boundary_missing", f"event {event_id} references an absent frame"
            )

    def _replay_events(
        self,
        events: Sequence[tuple[int, str, dict[str, object]]],
        frames: dict[int, TrajectoryFrame],
    ) -> None:
        for event_id, kind, parsed in events:
            if kind == "closed":
                reason = parsed.get("reason")
                if not isinstance(reason, str) or not reason:
                    self.detector.mark_external_corruption("stored_close_event_invalid", f"event {event_id}")
                    return
                self.detector.end_open_attempt(reason)
            elif kind == "duplicate":
                cursor = parsed.get("cursor")
                digest = parsed.get("frame_sha256")
                frame = frames.get(cursor) if isinstance(cursor, int) else None
                if frame is None or not isinstance(digest, str):
                    self.detector.mark_external_corruption("stored_duplicate_event_invalid", f"event {event_id}")
                    return
                normalized, _, actual_digest = _frame_payload(frame, self.detector)
                if actual_digest != digest:
                    self.detector.mark_external_corruption("stored_duplicate_event_invalid", f"event {event_id}")
                    return
                self.detector.ingest_validated_frame(normalized)
            elif kind == "corruption":
                code = parsed.get("code")
                if not isinstance(code, str) or not code:
                    self.detector.mark_external_corruption("stored_corruption_event_invalid", f"event {event_id}")
                    return
                self.detector.mark_external_corruption(code, f"persisted corruption event {event_id}")
            else:
                self.detector.mark_external_corruption("stored_event_kind_invalid", f"event {event_id}")
                return

    def _audit_existing_report(self) -> None:
        path = Path(self.report_path)
        if not path.exists():
            return
        try:
            payload = path.read_bytes()
            parsed = json.loads(payload.decode("utf-8"))
            if canonical_json_bytes(parsed) != payload:
                raise ValueError("report is not canonical")
            result_hash = parsed.pop("result_sha256")
            if not isinstance(result_hash, str) or canonical_sha256(parsed) != result_hash:
                raise ValueError("result hash mismatch")
            if parsed.get("schema_version") != REPORT_SCHEMA_VERSION:
                raise ValueError("report schema mismatch")
            if parsed.get("contract_version") != SOAK_CONTRACT_VERSION:
                raise ValueError("report contract mismatch")
            if parsed.get("contract_file_sha256") != contract_file_sha256():
                raise ValueError("report contract hash mismatch")
            if parsed.get("registry_sha256") != self.detector.registry.sha256:
                raise ValueError("report registry hash mismatch")
            if parsed.get("evidence_sha256") != self._evidence_hash():
                raise ValueError("report evidence hash mismatch")
        except BaseException as exc:
            self.detector.mark_external_corruption("existing_report_invalid", str(exc))

    def append_frame(self, frame: object) -> SoakState:
        if self._closed:
            raise SoakEvidenceError("evidence_closed", "store is closed")
        if self._read_only:
            raise SoakEvidenceError("evidence_read_only", "offline verifier cannot append evidence")
        with self._lock:
            try:
                normalized, payload, digest = _frame_payload(frame, self.detector)
                cursor_row = self._conn.execute(
                    "SELECT frame_sha256 FROM soak_frames WHERE cursor=?", (normalized.cursor,)
                ).fetchone()
                pair_row = self._conn.execute(
                    "SELECT frame_sha256 FROM soak_frames WHERE boot_id=? AND field_tick=?",
                    (normalized.boot_id, normalized.field_tick),
                ).fetchone()
                if cursor_row is not None or pair_row is not None:
                    if all(row is None or str(row[0]) == digest for row in (cursor_row, pair_row)):
                        self.detector.ingest_validated_frame(normalized)
                        self._append_event("duplicate", {
                            "after_cursor": normalized.cursor,
                            "cursor": normalized.cursor,
                            "frame_sha256": digest,
                        })
                        self.publish_report()
                        return self.state
                    self.detector.mark_external_corruption("conflicting_duplicate", "stored duplicate differs")
                    self._append_event("corruption", {
                        "after_cursor": normalized.cursor,
                        "code": "conflicting_duplicate",
                        "cursor": normalized.cursor,
                    })
                    self.publish_report()
                    return self.state
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    self._conn.execute(
                        "INSERT INTO soak_frames(cursor,boot_id,field_tick,utc_unix_ns,payload,frame_sha256) VALUES(?,?,?,?,?,?)",
                        (normalized.cursor, normalized.boot_id, normalized.field_tick, normalized.utc_unix_ns, payload, digest),
                    )
                    self._conn.execute("COMMIT")
                except BaseException:
                    self._conn.execute("ROLLBACK")
                    raise
                previous = self.state
                self.detector.ingest_validated_frame(normalized)
                attempt = self.detector.current_attempt
                block_count = 0 if attempt is None else len(next(iter(attempt.blocks.values()), ()))
                if block_count != self._last_published_block_count or self.state != previous:
                    self._last_published_block_count = block_count
                    self.publish_report()
                return self.state
            except (EvidenceCorruptError, SoakEvidenceError, sqlite3.DatabaseError, OSError) as exc:
                self._fail_closed("evidence_write_failed", str(exc))
                return self.state

    def _append_event(self, kind: str, primitive: dict[str, object]) -> None:
        payload = canonical_json_bytes(primitive).decode("utf-8").rstrip("\n")
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        self._conn.execute(
            "INSERT INTO soak_events(kind,payload,event_sha256) VALUES(?,?,?)", (kind, payload, digest)
        )

    def _evidence_hash(self) -> str:
        digest = hashlib.sha256()
        digest.update(canonical_json_bytes(dict(self._conn.execute("SELECT key,value FROM soak_meta ORDER BY key"))))
        for row in self._conn.execute("SELECT cursor,frame_sha256 FROM soak_frames ORDER BY cursor"):
            digest.update(f"{int(row[0])}:{row[1]}\n".encode("ascii"))
        for row in self._conn.execute("SELECT event_id,event_sha256 FROM soak_events ORDER BY event_id"):
            digest.update(f"{int(row[0])}:{row[1]}\n".encode("ascii"))
        return digest.hexdigest()

    def report_primitive(self) -> dict[str, object]:
        report = self.detector.report_primitive()
        report["evidence_schema_version"] = EVIDENCE_SCHEMA_VERSION
        report["contract_file_sha256"] = contract_file_sha256()
        report["evidence_sha256"] = self._evidence_hash()
        report["observer_failure"] = self._failed_detail
        report_without_hash = dict(report)
        report["result_sha256"] = canonical_sha256(report_without_hash)
        return report

    def publish_report(self) -> str:
        report = self.report_primitive()
        payload = canonical_json_bytes(report)
        digest = hashlib.sha256(payload).hexdigest()
        target = Path(self.report_path)
        temp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
        try:
            with open(temp, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            reread = temp.read_bytes()
            if reread != payload or hashlib.sha256(reread).hexdigest() != digest:
                raise SoakEvidenceError("report_verification_failed", "temporary report verification failed")
            os.replace(temp, target)
            if target.read_bytes() != payload:
                raise SoakEvidenceError("report_verification_failed", "published report verification failed")
            try:
                directory_fd = os.open(str(target.parent), os.O_RDONLY)
            except OSError:
                directory_fd = None
            if directory_fd is not None:
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            return digest
        except BaseException:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _fail_closed(self, code: str, detail: str) -> None:
        self._failed_detail = f"{code}: {detail}"
        self.detector.mark_external_corruption(code, detail)
        if not self._read_only and hasattr(self, "_conn"):
            try:
                current = self.detector.current_attempt
                self._append_event("corruption", {
                    "after_cursor": None if current is None else current.end_cursor,
                    "code": code,
                    "detail": detail,
                })
            except BaseException:
                pass
        try:
            self.publish_report()
        except BaseException:
            pass

    def close(self) -> None:
        if self._closed:
            return
        with self._lock:
            try:
                if not self._read_only and self.detector.current_attempt is not None \
                        and self.detector.current_attempt.end_reason is None \
                        and self.detector.state not in {SoakState.FAIL, SoakState.PASS, SoakState.EVIDENCE_CORRUPT}:
                    self._conn.execute("BEGIN IMMEDIATE")
                    try:
                        self._append_event("closed", {
                            "after_cursor": self.detector.current_attempt.end_cursor,
                            "reason": "observer_closed",
                        })
                        self._conn.execute("COMMIT")
                    except BaseException:
                        self._conn.execute("ROLLBACK")
                        raise
                self.detector.close()
                self.publish_report()
                if not self._read_only:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except BaseException as exc:
                self._fail_closed("evidence_close_failed", str(exc))
            finally:
                self._closed = True
                self._conn.close()


class SoakObserver:
    """Synchronous committed-frame observer; owns no timer or field mutation."""

    def __init__(self, store: SoakEvidenceStore) -> None:
        self._store = store

    @classmethod
    def open(
        cls,
        db_path: str,
        report_path: str,
        registry: object,
        *,
        profile: SoakProfile = TEST_PROFILE,
    ) -> "SoakObserver":
        return cls(SoakEvidenceStore(db_path, report_path, registry, profile=profile))

    @property
    def state(self) -> SoakState:
        return self._store.state

    def on_committed_frame(self, frame: object) -> SoakState:
        try:
            return self._store.append_frame(frame)
        except BaseException as exc:
            self._store._fail_closed("observer_unhandled_failure", str(exc))
            return self._store.state

    def close(self) -> None:
        self._store.close()


def profile_from_name(name: str) -> SoakProfile:
    if name == "formal":
        return FORMAL_PROFILE
    if name == "test":
        return TEST_PROFILE
    raise ValueError("profile must be 'formal' or 'test'")


def verify_and_summarize(db_path: str, report_path: str | None = None) -> dict[str, object]:
    """Offline strict audit of only the explicitly supplied companion DB."""
    conn = sqlite3.connect(f"file:{Path(db_path).resolve().as_posix()}?mode=ro", uri=True)
    try:
        meta = dict(conn.execute("SELECT key,value FROM soak_meta").fetchall())
        registry_raw = json.loads(meta["registry_json"])
        profile_raw = json.loads(meta["profile_json"])
    except BaseException as exc:
        conn.close()
        raise SoakEvidenceError("evidence_query_failed", "metadata query failed") from exc
    finally:
        try:
            conn.close()
        except BaseException:
            pass
    profile = profile_from_name(str(profile_raw["name"]))
    store = SoakEvidenceStore(
        db_path,
        report_path or f"{db_path}.report.json",
        registry_raw,
        profile=profile,
        read_only=True,
    )
    try:
        report = store.report_primitive()
        store.publish_report()
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "state": report["state"],
            "profile": report["profile"],
            "formal_48h": report["formal_48h"],
            "formal_48h_run": "not_run",
            "p4_human_gate": "not_run",
            "evidence_sha256": report["evidence_sha256"],
            "result_sha256": report["result_sha256"],
            "report_path": str(report_path or f"{db_path}.report.json"),
        }
    finally:
        store.close()
