"""Task-card 12 run ownership, append-only control audit, and strict summaries.

This module is deliberately outside the field tick path.  It owns only the
operation directory and its control metadata; the existing stores remain the
authorities for field, dialogue, perception, proactive, and soak evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import BinaryIO, Mapping
import uuid

from app.chatbox.proactive_store import CapConfig
from app.chatbox.soak_detection import canonical_json_bytes, canonical_sha256
from app.chatbox.soak_evidence import SoakEvidenceError, verify_and_summarize
from app.chatbox.trajectory_service import _loopback_host


RUN_SCHEMA_VERSION = "aphrodite.chatbox.formal-operation/1"
CONTROL_SCHEMA_VERSION = "aphrodite.chatbox.formal-control/1"
CONTROL_USER_VERSION = 1

MANUAL_GATES = (
    "p1_visual_one_hour",
    "p2_real_provider",
    "p2_owner_ten_turn",
    "p3_blind_pairing",
    "p3_two_hour_silence",
    "p4_proactive_expression",
)

ARTIFACT_NAMES = {
    "manifest": "manifest.json",
    "control": "control.sqlite3",
    "lease": "run.lock",
    "field": "field.sqlite3",
    "dialogue": "dialogue.sqlite3",
    "perception": "perception.sqlite3",
    "proactive": "proactive.sqlite3",
    "soak_evidence": "soak.sqlite3",
    "soak_report": "soak-report.json",
    "worker_stdout": "worker.stdout.jsonl",
    "worker_stderr": "worker.stderr.jsonl",
    "result": "result.json",
}


class FormalOperationError(RuntimeError):
    """Stable, credential-free operation failure."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class RunConfig:
    profile: str = "formal"
    host: str = "127.0.0.1"
    port: int = 8765
    provider_mode: str = "offline"
    temperature: float = 1.0
    proactive_daily_limit: int = 2
    proactive_min_interval_seconds: int = 21600
    proactive_curfew_start_hour: int = 1
    proactive_curfew_end_hour: int = 9

    def __post_init__(self) -> None:
        if self.profile not in {"formal", "smoke"}:
            raise FormalOperationError("invalid_profile", "profile must be formal or smoke")
        if not _loopback_host(self.host):
            raise FormalOperationError("non_loopback_host", "host must be loopback")
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 0 <= self.port <= 65535:
            raise FormalOperationError("invalid_port", "port must be in [0, 65535]")
        if self.provider_mode not in {"offline", "real"}:
            raise FormalOperationError("invalid_provider_mode", "provider mode must be offline or real")
        if isinstance(self.temperature, bool) or not isinstance(self.temperature, (int, float)) \
                or not float(self.temperature) > 0.0:
            raise FormalOperationError("invalid_temperature", "temperature must be positive")
        try:
            CapConfig(
                daily_limit=self.proactive_daily_limit,
                min_interval_seconds=self.proactive_min_interval_seconds,
                curfew_start_hour=self.proactive_curfew_start_hour,
                curfew_end_hour=self.proactive_curfew_end_hour,
            )
        except ValueError as exc:
            raise FormalOperationError("invalid_proactive_cap", str(exc)) from exc

    @property
    def soak_profile(self) -> str:
        return "formal" if self.profile == "formal" else "test"


def config_from_mapping(raw: Mapping[str, object]) -> RunConfig:
    """Decode the exact persisted config shape without permissive coercion."""
    expected = {
        "profile", "host", "port", "provider_mode", "temperature",
        "proactive_daily_limit", "proactive_min_interval_seconds",
        "proactive_curfew_start_hour", "proactive_curfew_end_hour",
    }
    if set(raw) != expected:
        raise FormalOperationError("invalid_config", "config field set mismatch")
    try:
        profile = raw["profile"]
        host = raw["host"]
        port = raw["port"]
        provider_mode = raw["provider_mode"]
        temperature = raw["temperature"]
        daily = raw["proactive_daily_limit"]
        interval = raw["proactive_min_interval_seconds"]
        curfew_start = raw["proactive_curfew_start_hour"]
        curfew_end = raw["proactive_curfew_end_hour"]
        if not isinstance(profile, str) or not isinstance(host, str) or not isinstance(provider_mode, str):
            raise TypeError("string field")
        if not isinstance(port, int) or isinstance(port, bool):
            raise TypeError("port")
        if not isinstance(temperature, (int, float)) or isinstance(temperature, bool):
            raise TypeError("temperature")
        if any(not isinstance(item, int) or isinstance(item, bool) for item in (
            daily, interval, curfew_start, curfew_end,
        )):
            raise TypeError("cap integer")
        assert isinstance(daily, int) and isinstance(interval, int)
        assert isinstance(curfew_start, int) and isinstance(curfew_end, int)
        return RunConfig(
            profile=profile, host=host, port=port, provider_mode=provider_mode,
            temperature=float(temperature), proactive_daily_limit=daily,
            proactive_min_interval_seconds=interval,
            proactive_curfew_start_hour=curfew_start, proactive_curfew_end_hour=curfew_end,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise FormalOperationError("invalid_config", "persisted config is malformed") from exc


@dataclass(frozen=True, slots=True)
class RunManifest:
    schema_version: str
    run_id: str
    created_utc_ns: int
    config: Mapping[str, object]
    artifacts: Mapping[str, str]
    lease_token: str
    manifest_sha256: str

    def primitive_without_hash(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "created_utc_ns": self.created_utc_ns,
            "config": dict(self.config),
            "artifacts": dict(self.artifacts),
            "lease_token": self.lease_token,
        }


def _atomic_publish(path: Path, value: object) -> None:
    payload = canonical_json_bytes(value)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        with open(temporary, "xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _safe_run_path(raw: str, *, must_be_new: bool) -> Path:
    if not raw or "\x00" in raw:
        raise FormalOperationError("unsafe_run_dir", "run directory must be non-empty")
    supplied = Path(raw)
    if ".." in supplied.parts:
        raise FormalOperationError("unsafe_run_dir", "parent traversal is forbidden")
    path = supplied.absolute()
    forbidden = {Path(path.anchor), Path.cwd().absolute(), Path.home().absolute()}
    if path in forbidden:
        raise FormalOperationError("unsafe_run_dir", "root, current directory, and home are forbidden")
    parent = path.parent
    if not parent.is_dir():
        raise FormalOperationError("unsafe_run_dir", "run directory parent must already exist")
    for candidate in (parent, *parent.parents):
        if candidate.is_symlink():
            raise FormalOperationError("unsafe_run_dir", "symlink in managed parent chain")
    if must_be_new and path.exists():
        raise FormalOperationError("run_dir_exists", "start requires a new leaf directory")
    if not must_be_new and (not path.is_dir() or path.is_symlink()):
        raise FormalOperationError("unsafe_run_dir", "existing run directory must be a real directory")
    return path


def artifact_paths(run_dir: Path, manifest: RunManifest | None = None) -> dict[str, Path]:
    names = ARTIFACT_NAMES if manifest is None else manifest.artifacts
    paths = {key: run_dir / str(name) for key, name in names.items()}
    canonical = [os.path.normcase(os.path.abspath(path)) for path in paths.values()]
    if len(set(canonical)) != len(canonical):
        raise FormalOperationError("artifact_path_alias", "managed artifact paths overlap")
    for path in paths.values():
        if path.parent != run_dir or path.name in {"", ".", ".."}:
            raise FormalOperationError("artifact_path_escape", "managed artifact escapes run directory")
    return paths


def create_run(run_dir_raw: str, config: RunConfig) -> tuple[Path, RunManifest]:
    run_dir = _safe_run_path(run_dir_raw, must_be_new=True)
    try:
        os.mkdir(run_dir)
    except OSError as exc:
        raise FormalOperationError("run_dir_create_failed", "cannot create run directory") from exc
    try:
        probe = run_dir / ".write-probe"
        with open(probe, "xb") as handle:
            handle.write(b"ok")
            handle.flush()
            os.fsync(handle.fileno())
        probe.unlink()
        lease_token = uuid.uuid4().hex
        lease_path = run_dir / ARTIFACT_NAMES["lease"]
        with open(lease_path, "xb") as handle:
            # The final byte is the Windows byte-range lock target.  Keeping
            # identity in the preceding bytes lets status validate a held
            # lease without reading the locked range.
            handle.write((lease_token + "\n0").encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())
        body = {
            "schema_version": RUN_SCHEMA_VERSION,
            "run_id": uuid.uuid4().hex,
            "created_utc_ns": time.time_ns(),
            "config": asdict(config),
            "artifacts": dict(ARTIFACT_NAMES),
            "lease_token": lease_token,
        }
        manifest = RunManifest(**body, manifest_sha256=canonical_sha256(body))
        _atomic_publish(run_dir / ARTIFACT_NAMES["manifest"], {
            **manifest.primitive_without_hash(), "manifest_sha256": manifest.manifest_sha256,
        })
        artifacts = artifact_paths(run_dir, manifest)
        control = ControlStore.create(artifacts["control"], manifest)
        control.close()
        return run_dir, manifest
    except BaseException:
        # The leaf was created by this call and has never been returned as a
        # managed run.  Leave it in place for fail-closed forensic inspection;
        # start will never reuse or overwrite it.
        raise


def load_manifest(run_dir_raw: str) -> tuple[Path, RunManifest]:
    run_dir = _safe_run_path(run_dir_raw, must_be_new=False)
    path = run_dir / ARTIFACT_NAMES["manifest"]
    try:
        payload = path.read_bytes()
        raw = json.loads(payload.decode("utf-8"))
        if canonical_json_bytes(raw) != payload or not isinstance(raw, dict):
            raise ValueError("manifest is not canonical")
        digest = raw.pop("manifest_sha256")
        if not isinstance(digest, str) or canonical_sha256(raw) != digest:
            raise ValueError("manifest hash mismatch")
        if raw.get("schema_version") != RUN_SCHEMA_VERSION:
            raise ValueError("manifest schema mismatch")
        if raw.get("artifacts") != ARTIFACT_NAMES:
            raise ValueError("artifact contract mismatch")
        config_raw = raw.get("config")
        if not isinstance(config_raw, dict):
            raise ValueError("config is malformed")
        config_from_mapping(config_raw)
        manifest = RunManifest(**raw, manifest_sha256=digest)
        artifact_paths(run_dir, manifest)
        return run_dir, manifest
    except FormalOperationError:
        raise
    except BaseException as exc:
        raise FormalOperationError("manifest_invalid", "manifest validation failed") from exc


class ControlStore:
    _TABLES = {"control_meta", "control_events"}
    _INDEXES: set[str] = set()
    _TRIGGERS = {
        "trg_control_meta_no_update", "trg_control_meta_no_delete",
        "trg_control_events_no_update", "trg_control_events_no_delete",
    }

    def __init__(self, path: Path, manifest: RunManifest, *, read_only: bool = False) -> None:
        self.path = path
        self.manifest = manifest
        self.read_only = read_only
        try:
            if read_only:
                uri = f"file:{path.resolve().as_posix()}?mode=ro"
                self._conn = sqlite3.connect(uri, uri=True, isolation_level=None)
            else:
                self._conn = sqlite3.connect(path, isolation_level=None)
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            quick = self._conn.execute("PRAGMA quick_check").fetchone()
            if quick is None or str(quick[0]).lower() != "ok":
                raise FormalOperationError("control_corrupt", "control quick_check failed")
            self._validate()
        except FormalOperationError:
            if hasattr(self, "_conn"):
                self._conn.close()
            raise
        except BaseException as exc:
            if hasattr(self, "_conn"):
                self._conn.close()
            raise FormalOperationError("control_invalid", "control database validation failed") from exc

    @classmethod
    def create(cls, path: Path, manifest: RunManifest) -> "ControlStore":
        if path.exists():
            raise FormalOperationError("control_exists", "control database already exists")
        conn = sqlite3.connect(path, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE control_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE control_events(
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    utc_unix_ns INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    launch_id TEXT,
                    payload TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
                    previous_sha256 TEXT NOT NULL,
                    event_sha256 TEXT NOT NULL CHECK(length(event_sha256)=64)
                );
                CREATE TRIGGER trg_control_meta_no_update BEFORE UPDATE ON control_meta BEGIN SELECT RAISE(ABORT,'append-only'); END;
                CREATE TRIGGER trg_control_meta_no_delete BEFORE DELETE ON control_meta BEGIN SELECT RAISE(ABORT,'append-only'); END;
                CREATE TRIGGER trg_control_events_no_update BEFORE UPDATE ON control_events BEGIN SELECT RAISE(ABORT,'append-only'); END;
                CREATE TRIGGER trg_control_events_no_delete BEFORE DELETE ON control_events BEGIN SELECT RAISE(ABORT,'append-only'); END;
                COMMIT;
            """)
            objects = cls._schema_objects(conn)
            metadata = {
                "schema_version": CONTROL_SCHEMA_VERSION,
                "manifest_sha256": manifest.manifest_sha256,
                "run_id": manifest.run_id,
                "schema_sha256": canonical_sha256(objects),
            }
            conn.executemany("INSERT INTO control_meta(key,value) VALUES(?,?)", metadata.items())
            conn.execute(f"PRAGMA user_version={CONTROL_USER_VERSION}")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except BaseException:
            conn.close()
            raise
        conn.close()
        return cls(path, manifest)

    @staticmethod
    def _schema_objects(conn: sqlite3.Connection) -> list[dict[str, str]]:
        return [
            {"type": str(row[0]), "name": str(row[1]), "sql": str(row[2])}
            for row in conn.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE type IN ('table','index','trigger') "
                "AND name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )
        ]

    def _names(self, kind: str) -> set[str]:
        return {str(row[0]) for row in self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type=? AND name NOT LIKE 'sqlite_%'", (kind,)
        )}

    def _validate(self) -> None:
        if self._names("table") != self._TABLES or self._names("index") != self._INDEXES \
                or self._names("trigger") != self._TRIGGERS:
            raise FormalOperationError("control_schema_mismatch", "control object set mismatch")
        version = self._conn.execute("PRAGMA user_version").fetchone()
        if version is None or int(version[0]) != CONTROL_USER_VERSION:
            raise FormalOperationError("control_schema_mismatch", "control user_version mismatch")
        expected = {
            "schema_version": CONTROL_SCHEMA_VERSION,
            "manifest_sha256": self.manifest.manifest_sha256,
            "run_id": self.manifest.run_id,
            "schema_sha256": canonical_sha256(self._schema_objects(self._conn)),
        }
        actual = dict(self._conn.execute("SELECT key,value FROM control_meta").fetchall())
        if actual != expected:
            raise FormalOperationError("control_metadata_mismatch", "control metadata mismatch")
        previous = ""
        for row in self._conn.execute(
            "SELECT event_id,utc_unix_ns,kind,launch_id,payload,payload_sha256,previous_sha256,event_sha256 "
            "FROM control_events ORDER BY event_id"
        ):
            event_id, utc_ns, kind, launch_id, payload, payload_hash, prior, event_hash = row
            try:
                parsed = json.loads(str(payload))
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                raise FormalOperationError("control_event_invalid", f"event {event_id} payload") from exc
            canonical = canonical_json_bytes(parsed).decode("utf-8").rstrip("\n")
            if canonical != str(payload) or hashlib.sha256(canonical.encode("utf-8")).hexdigest() != payload_hash:
                raise FormalOperationError("control_event_invalid", f"event {event_id} payload hash")
            primitive = {
                "event_id": int(event_id), "utc_unix_ns": int(utc_ns), "kind": str(kind),
                "launch_id": launch_id, "payload_sha256": str(payload_hash), "previous_sha256": str(prior),
            }
            if prior != previous or canonical_sha256(primitive) != event_hash:
                raise FormalOperationError("control_event_invalid", f"event {event_id} chain hash")
            previous = str(event_hash)

    def events(self) -> list[dict[str, object]]:
        self._validate()
        return [
            {
                "event_id": int(row[0]), "utc_unix_ns": int(row[1]), "kind": str(row[2]),
                "launch_id": row[3], "payload": json.loads(str(row[4])), "event_sha256": str(row[5]),
            }
            for row in self._conn.execute(
                "SELECT event_id,utc_unix_ns,kind,launch_id,payload,event_sha256 "
                "FROM control_events ORDER BY event_id"
            )
        ]

    def append(self, kind: str, *, launch_id: str | None, payload: Mapping[str, object] | None = None) -> dict[str, object]:
        if self.read_only:
            raise FormalOperationError("control_read_only", "cannot append through read-only control")
        body = dict(payload or {})
        payload_text = canonical_json_bytes(body).decode("utf-8").rstrip("\n")
        payload_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
        utc_ns = time.time_ns()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            last = self._conn.execute(
                "SELECT event_id,event_sha256 FROM control_events ORDER BY event_id DESC LIMIT 1"
            ).fetchone()
            event_id = 1 if last is None else int(last[0]) + 1
            previous = "" if last is None else str(last[1])
            event_hash = canonical_sha256({
                "event_id": event_id, "utc_unix_ns": utc_ns, "kind": kind,
                "launch_id": launch_id, "payload_sha256": payload_hash, "previous_sha256": previous,
            })
            self._conn.execute(
                "INSERT INTO control_events(event_id,utc_unix_ns,kind,launch_id,payload,payload_sha256,previous_sha256,event_sha256) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (event_id, utc_ns, kind, launch_id, payload_text, payload_hash, previous, event_hash),
            )
            self._conn.execute("COMMIT")
            return {"event_id": event_id, "event_sha256": event_hash}
        except BaseException as exc:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            if isinstance(exc, FormalOperationError):
                raise
            raise FormalOperationError("control_append_failed", "control event append failed") from exc

    def stop_requested(self, launch_id: str) -> bool:
        self._validate()
        row = self._conn.execute(
            "SELECT 1 FROM control_events WHERE kind='stop_requested' AND launch_id=? LIMIT 1",
            (launch_id,),
        ).fetchone()
        return row is not None

    def close(self) -> None:
        if not self.read_only:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except sqlite3.DatabaseError:
                pass
        self._conn.close()


class RunLease:
    """Cross-platform advisory lease; the open locked handle is authoritative."""

    def __init__(self, path: Path, token: str) -> None:
        self.path = path
        self.token = token
        self._handle: BinaryIO | None = None

    def acquire(self, *, blocking: bool = False) -> bool:
        if self._handle is not None:
            return True
        try:
            handle = open(self.path, "r+b", buffering=0)
            token_bytes = self.token.encode("ascii")
            if handle.read(len(token_bytes)) != token_bytes or handle.read(1) != b"\n":
                handle.close()
                raise FormalOperationError("lease_invalid", "lease identity mismatch")
            if os.name == "nt":
                import msvcrt
                handle.seek(len(token_bytes) + 1)
                mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
                try:
                    msvcrt.locking(handle.fileno(), mode, 1)
                except OSError:
                    handle.close()
                    return False
            else:
                import fcntl
                flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
                try:
                    fcntl.flock(handle.fileno(), flags)
                except BlockingIOError:
                    handle.close()
                    return False
            self._handle = handle
            return True
        except FormalOperationError:
            raise
        except OSError as exc:
            raise FormalOperationError("lease_unavailable", "lease file cannot be opened") from exc

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(len(self.token.encode("ascii")) + 1)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._handle = None

    def __enter__(self) -> "RunLease":
        if not self.acquire():
            raise FormalOperationError("run_active", "another worker owns the run lease")
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


def reduce_status(manifest: RunManifest, events: list[dict[str, object]], lease_state: str) -> dict[str, object]:
    latest_launch: str | None = None
    claimed = "never_started"
    pid: int | None = None
    ready: Mapping[str, object] | None = None
    exit_reason: str | None = None
    gates = {gate: "not_run" for gate in MANUAL_GATES}
    for event in events:
        kind = event["kind"]
        launch_id = event["launch_id"]
        payload = event["payload"]
        if not isinstance(payload, dict):
            continue
        if kind == "launch_requested":
            latest_launch = str(launch_id)
            claimed = "starting"
            pid = payload.get("pid") if isinstance(payload.get("pid"), int) else None
            ready = None
            exit_reason = None
        elif launch_id == latest_launch and kind == "worker_started":
            claimed = "starting"
            pid = payload.get("pid") if isinstance(payload.get("pid"), int) else pid
        elif launch_id == latest_launch and kind == "ready":
            claimed = "running"
            ready = payload
        elif launch_id == latest_launch and kind == "stop_requested" and claimed in {"starting", "running"}:
            claimed = "stop_requested"
        elif launch_id == latest_launch and kind in {"exited", "failed"}:
            claimed = "stopped" if kind == "exited" else "failed"
            exit_reason = str(payload.get("reason") or kind)
        elif kind == "manual_gate" and payload.get("gate") in gates and payload.get("state") in {"passed", "failed"}:
            gates[str(payload["gate"])] = str(payload["state"])
    process_state = claimed
    if lease_state == "held":
        process_state = claimed if claimed in {"starting", "running", "stop_requested"} else "control_inconsistent"
    elif lease_state == "idle" and claimed in {"starting", "running", "stop_requested"}:
        process_state = "interrupted"
    elif lease_state == "invalid":
        process_state = "control_invalid"
    config = config_from_mapping(manifest.config)
    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": manifest.run_id,
        "control_state": claimed,
        "process_state": process_state,
        "lease_state": lease_state,
        "launch_id": latest_launch,
        "pid_audit": pid,
        "exit_reason": exit_reason,
        "ready": ready,
        "profile": config.profile,
        "soak_profile": config.soak_profile,
        "provider_mode": config.provider_mode,
        "formal_48h": "not_run",
        "manual_gates": gates,
    }


def operation_status(run_dir_raw: str) -> dict[str, object]:
    run_dir, manifest = load_manifest(run_dir_raw)
    paths = artifact_paths(run_dir, manifest)
    control = ControlStore(paths["control"], manifest, read_only=True)
    try:
        events = control.events()
    finally:
        control.close()
    lease = RunLease(paths["lease"], manifest.lease_token)
    try:
        acquired = lease.acquire()
        lease_state = "idle" if acquired else "held"
    except FormalOperationError:
        lease_state = "invalid"
    finally:
        lease.release()
    return reduce_status(manifest, events, lease_state)


def append_stop(run_dir_raw: str) -> dict[str, object]:
    run_dir, manifest = load_manifest(run_dir_raw)
    status = operation_status(run_dir_raw)
    launch_id = status["launch_id"]
    if launch_id is None or status["process_state"] not in {
        "starting", "running", "stop_requested", "interrupted",
    }:
        return {**status, "stop_operation": "already_stopped"}
    paths = artifact_paths(run_dir, manifest)
    control = ControlStore(paths["control"], manifest)
    already_requested = False
    try:
        already_requested = control.stop_requested(str(launch_id))
        if not already_requested:
            control.append("stop_requested", launch_id=str(launch_id), payload={"source": "owner"})
    finally:
        control.close()
    latest = operation_status(run_dir_raw)
    operation = "already_requested" if already_requested else "requested"
    return {**latest, "stop_operation": operation}


def append_gate(run_dir_raw: str, gate: str, state: str) -> dict[str, object]:
    if gate not in MANUAL_GATES:
        raise FormalOperationError("invalid_gate", "gate is not an Owner manual gate")
    if state not in {"passed", "failed"}:
        raise FormalOperationError("invalid_gate_state", "gate state must be passed or failed")
    run_dir, manifest = load_manifest(run_dir_raw)
    paths = artifact_paths(run_dir, manifest)
    control = ControlStore(paths["control"], manifest)
    try:
        control.append("manual_gate", launch_id=None, payload={"gate": gate, "state": state})
    finally:
        control.close()
    return operation_status(run_dir_raw)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def _audit_sqlite(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {"status": "missing", "path": path.name}
    try:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            quick = conn.execute("PRAGMA quick_check").fetchone()
        finally:
            conn.close()
        if quick is None or str(quick[0]).lower() != "ok":
            raise sqlite3.DatabaseError("quick_check failed")
        return {"status": "verified", "path": path.name, "size": path.stat().st_size, "sha256": _sha256_file(path)}
    except (OSError, sqlite3.DatabaseError) as exc:
        return {"status": "corrupt", "path": path.name, "detail": type(exc).__name__}


def build_result(run_dir_raw: str) -> dict[str, object]:
    run_dir, manifest = load_manifest(run_dir_raw)
    status = operation_status(run_dir_raw)
    if status["lease_state"] != "idle":
        return {
            "schema_version": RUN_SCHEMA_VERSION,
            "run_id": manifest.run_id,
            "result_state": "running" if status["lease_state"] == "held" else "invalid",
            "process_state": status["process_state"],
            "profile": status["profile"],
            "provider_mode": status["provider_mode"],
            "formal_48h": "not_run",
            "manual_gates": status["manual_gates"],
            "sources": {},
        }
    paths = artifact_paths(run_dir, manifest)
    sources = {
        name: _audit_sqlite(paths[name])
        for name in ("field", "dialogue", "perception", "proactive", "soak_evidence")
    }
    soak: dict[str, object]
    try:
        soak = verify_and_summarize(str(paths["soak_evidence"]), str(paths["soak_report"]))
        sources["soak_report"] = {
            "status": "verified", "path": paths["soak_report"].name,
            "size": paths["soak_report"].stat().st_size,
            "sha256": _sha256_file(paths["soak_report"]),
            "registry_evidence_result": {
                "evidence_sha256": soak["evidence_sha256"], "result_sha256": soak["result_sha256"],
            },
        }
    except (SoakEvidenceError, OSError, KeyError, ValueError) as exc:
        soak = {"state": "EVIDENCE_CORRUPT", "error": type(exc).__name__}
        sources["soak_report"] = {"status": "corrupt", "path": paths["soak_report"].name}
    config = config_from_mapping(manifest.config)
    soak_state = str(soak.get("state", "EVIDENCE_CORRUPT"))
    formal_state = "not_run"
    if config.profile == "formal":
        formal_state = {
            "PASS": "passed",
            "FAIL": "failed",
            "EVIDENCE_CORRUPT": "evidence_corrupt",
        }.get(soak_state, "incomplete")
    all_verified = all(item.get("status") == "verified" for item in sources.values())
    result = {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": manifest.run_id,
        "manifest_sha256": manifest.manifest_sha256,
        "result_state": "verified" if all_verified else "invalid",
        "control_state": status["control_state"],
        "process_state": status["process_state"],
        "profile": config.profile,
        "provider_mode": config.provider_mode,
        "soak_state": soak_state,
        "soak_profile": config.soak_profile,
        "formal_48h": formal_state,
        "manual_gates": status["manual_gates"],
        "sources": sources,
    }
    result["result_sha256"] = canonical_sha256(result)
    _atomic_publish(paths["result"], result)
    return result


def ensure_restartable(run_dir_raw: str) -> tuple[Path, RunManifest]:
    run_dir, manifest = load_manifest(run_dir_raw)
    status = operation_status(run_dir_raw)
    if status["lease_state"] != "idle":
        raise FormalOperationError("run_active", "restart requires an idle valid lease")
    paths = artifact_paths(run_dir, manifest)
    if paths["soak_evidence"].exists():
        try:
            summary = verify_and_summarize(str(paths["soak_evidence"]), str(paths["soak_report"]))
        except SoakEvidenceError as exc:
            raise FormalOperationError("soak_invalid", "soak evidence validation failed") from exc
        if summary["state"] in {"PASS", "FAIL", "EVIDENCE_CORRUPT"}:
            raise FormalOperationError("soak_terminal", "terminal soak artifacts require a new run directory")
    return run_dir, manifest


def canonical_output(value: object) -> str:
    return canonical_json_bytes(value).decode("utf-8").rstrip("\n")
