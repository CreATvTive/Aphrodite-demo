"""Append-only SQLite persistence boundary for P2 dialogue turns.

The field database has a frozen, fail-closed P1 schema.  Dialogue records live
in a small companion SQLite database so task card 6 can persist conversation
history without weakening or migrating that field schema.  Service code uses
this API and never executes SQL directly.
"""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Literal


DIALOGUE_SCHEMA_VERSION = "aphrodite.chatbox.dialogue-persistence/1"
DIALOGUE_USER_VERSION = 1


class DialoguePersistenceError(RuntimeError):
    """Credential-free, stable persistence-boundary failure."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class DialogueMessage:
    message_id: int
    client_turn_id: str
    role: Literal["user", "assistant"]
    segment_index: int
    content: str
    utc_unix_ns: int


class DialoguePersistenceStore:
    """Single-owner append-only dialogue and writer-audit store."""

    _TABLES = {"dialogue_meta", "dialogue_messages", "dialogue_audits"}
    _INDEXES = {"idx_dialogue_messages_turn", "idx_dialogue_audits_turn"}
    _TRIGGERS = {
        "trg_no_update_dialogue_messages",
        "trg_no_delete_dialogue_messages",
        "trg_no_update_dialogue_audits",
        "trg_no_delete_dialogue_audits",
    }
    _DDL = (
        "CREATE TABLE dialogue_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        """CREATE TABLE dialogue_messages (
            message_id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_turn_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user','assistant')),
            segment_index INTEGER NOT NULL CHECK(segment_index >= 0),
            content TEXT NOT NULL,
            utc_unix_ns INTEGER NOT NULL CHECK(utc_unix_ns >= 0),
            UNIQUE(client_turn_id, role, segment_index)
        )""",
        """CREATE TABLE dialogue_audits (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_turn_id TEXT NOT NULL,
            server_call_id TEXT NOT NULL,
            lifecycle TEXT NOT NULL,
            provider_id TEXT,
            parsed_ok INTEGER NOT NULL CHECK(parsed_ok IN (0,1)),
            writer_log_persisted INTEGER NOT NULL CHECK(writer_log_persisted IN (0,1)),
            writer_move_count INTEGER NOT NULL CHECK(writer_move_count >= 0),
            detail_code TEXT NOT NULL,
            utc_unix_ns INTEGER NOT NULL CHECK(utc_unix_ns >= 0)
        )""",
        "CREATE INDEX idx_dialogue_messages_turn ON dialogue_messages(client_turn_id, message_id)",
        "CREATE INDEX idx_dialogue_audits_turn ON dialogue_audits(client_turn_id, audit_id)",
        """CREATE TRIGGER trg_no_update_dialogue_messages BEFORE UPDATE ON dialogue_messages
        BEGIN SELECT RAISE(ABORT, 'dialogue_messages is append-only'); END""",
        """CREATE TRIGGER trg_no_delete_dialogue_messages BEFORE DELETE ON dialogue_messages
        BEGIN SELECT RAISE(ABORT, 'dialogue_messages is append-only'); END""",
        """CREATE TRIGGER trg_no_update_dialogue_audits BEFORE UPDATE ON dialogue_audits
        BEGIN SELECT RAISE(ABORT, 'dialogue_audits is append-only'); END""",
        """CREATE TRIGGER trg_no_delete_dialogue_audits BEFORE DELETE ON dialogue_audits
        BEGIN SELECT RAISE(ABORT, 'dialogue_audits is append-only'); END""",
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
                raise DialoguePersistenceError("dialogue_database_corrupt", "quick_check failed")
            self._ensure_schema()
        except DialoguePersistenceError:
            if hasattr(self, "_conn"):
                self._conn.close()
            self._closed = True
            raise
        except sqlite3.DatabaseError as exc:
            if hasattr(self, "_conn"):
                self._conn.close()
            self._closed = True
            raise DialoguePersistenceError("dialogue_open_failed", "database open failed") from exc

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
                    "INSERT INTO dialogue_meta(key,value) VALUES('schema_version',?)",
                    (DIALOGUE_SCHEMA_VERSION,),
                )
                self._conn.execute(f"PRAGMA user_version={DIALOGUE_USER_VERSION}")
                self._conn.execute("COMMIT")
            except sqlite3.DatabaseError as exc:
                self._conn.execute("ROLLBACK")
                raise DialoguePersistenceError(
                    "dialogue_schema_bootstrap_failed", "schema bootstrap failed"
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
            raise DialoguePersistenceError("dialogue_schema_mismatch", "table set mismatch")
        if self._names("index") != self._INDEXES:
            raise DialoguePersistenceError("dialogue_schema_mismatch", "index set mismatch")
        if self._names("trigger") != self._TRIGGERS:
            raise DialoguePersistenceError("dialogue_schema_mismatch", "trigger set mismatch")
        user_version = self._conn.execute("PRAGMA user_version").fetchone()
        schema_version = self._conn.execute(
            "SELECT value FROM dialogue_meta WHERE key='schema_version'"
        ).fetchone()
        if user_version is None or int(user_version[0]) != DIALOGUE_USER_VERSION:
            raise DialoguePersistenceError("dialogue_schema_version_mismatch", "user version mismatch")
        if schema_version is None or schema_version[0] != DIALOGUE_SCHEMA_VERSION:
            raise DialoguePersistenceError("dialogue_schema_version_mismatch", "meta version mismatch")

    def turn_exists(self, client_turn_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM dialogue_messages WHERE client_turn_id=? AND role='user' LIMIT 1",
            (client_turn_id,),
        ).fetchone()
        return row is not None

    def append_message(
        self,
        *,
        client_turn_id: str,
        role: Literal["user", "assistant"],
        segment_index: int,
        content: str,
        utc_unix_ns: int,
    ) -> DialogueMessage:
        if role not in {"user", "assistant"}:
            raise ValueError("role must be user or assistant")
        if not isinstance(segment_index, int) or isinstance(segment_index, bool) or segment_index < 0:
            raise ValueError("segment_index must be a non-negative int")
        if not isinstance(content, str) or not content:
            raise ValueError("content must be a non-empty string")
        try:
            cursor = self._conn.execute(
                "INSERT INTO dialogue_messages(client_turn_id,role,segment_index,content,utc_unix_ns) "
                "VALUES(?,?,?,?,?)",
                (client_turn_id, role, segment_index, content, utc_unix_ns),
            )
        except sqlite3.IntegrityError as exc:
            raise DialoguePersistenceError("dialogue_duplicate_message", "message already exists") from exc
        if cursor.lastrowid is None:
            raise DialoguePersistenceError("dialogue_write_failed", "message id was not returned")
        return DialogueMessage(
            int(cursor.lastrowid), client_turn_id, role, segment_index, content, utc_unix_ns
        )

    def append_audit(
        self,
        *,
        client_turn_id: str,
        server_call_id: str,
        lifecycle: str,
        provider_id: str | None,
        parsed_ok: bool,
        writer_log_persisted: bool,
        writer_move_count: int,
        detail_code: str,
        utc_unix_ns: int,
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO dialogue_audits(client_turn_id,server_call_id,lifecycle,provider_id,"
            "parsed_ok,writer_log_persisted,writer_move_count,detail_code,utc_unix_ns) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                client_turn_id,
                server_call_id,
                lifecycle,
                provider_id,
                int(parsed_ok),
                int(writer_log_persisted),
                writer_move_count,
                detail_code,
                utc_unix_ns,
            ),
        )
        if cursor.lastrowid is None:
            raise DialoguePersistenceError("dialogue_write_failed", "audit id was not returned")
        return int(cursor.lastrowid)

    def latest_user_message_ns(self) -> int | None:
        """Return the utc_unix_ns of the most recent persisted user message.

        Used at startup to restore the server-trusted silence baseline for
        the P4.10 proactive pressure accumulator.  Returns ``None`` when no
        user message exists (empty store).  Read-only; schema and append-only
        semantics are unchanged.
        """
        row = self._conn.execute(
            "SELECT utc_unix_ns FROM dialogue_messages WHERE role='user' "
            "ORDER BY message_id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return int(row[0])

    def read_messages(self, *, limit: int = 200) -> tuple[DialogueMessage, ...]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError("limit must be in [1, 1000]")
        rows = self._conn.execute(
            "SELECT message_id,client_turn_id,role,segment_index,content,utc_unix_ns FROM "
            "(SELECT * FROM dialogue_messages ORDER BY message_id DESC LIMIT ?) "
            "ORDER BY message_id",
            (limit,),
        ).fetchall()
        return tuple(
            DialogueMessage(int(row[0]), row[1], row[2], int(row[3]), row[4], int(row[5]))
            for row in rows
        )

    @property
    def db_path(self) -> str:
        return self._db_path

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            self._conn.close()
