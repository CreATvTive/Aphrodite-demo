"""P3 task-card 7: server-trusted perception signal derivation + typing ingress.

This module derives the four server-trusted signals (message gap, time of day,
message length, session lifecycle) from authoritative server state — never
from client-supplied durations — and provides the typing state machine that
validates, dedups, and times out client-reported typing events.

Key invariants:

* ``duration_seconds`` and ``gap_seconds`` are computed from the injected
  monotonic clock, never from client payloads;
* clock rollback is defended: a non-monotonic observed_at never produces a
  negative duration (max(prev, now) is used);
* typing state is per-session, deduped, and cleared after
  [`TYPING_HEARTBEAT_TIMEOUT_SECONDS`](perception_config.py) of inactivity;
* the typing ingress only emits discrete start/heartbeat/stop events; it
  never submits a field delta directly — that is the bus's job.

Imports are restricted to the standard library,
[`perception_config`](perception_config.py), and
[`perception_schema`](perception_schema.py).
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable

from app.chatbox.perception_config import (
    TYPING_HEARTBEAT_TIMEOUT_SECONDS,
)
from app.chatbox.perception_mapping import band_for_hour


@dataclass(slots=True)
class SessionState:
    """Per-session server-trusted state for signal derivation."""

    session_id: str
    started_at_unix: int
    last_message_at_unix: int | None = None
    typing_active: bool = False
    typing_last_heartbeat_unix: int | None = None


class PerceptionIngress:
    """Server-trusted signal derivation and typing state machine.

    Construct with an injected ``utc_clock`` (returns int unix seconds) and a
    ``local_hour_for_unix`` callable (returns the local solar hour in [0, 24))
    so time-of-day bands are deterministic in tests.  The caller (dialogue
    service) invokes the ``derive_*`` helpers at the right lifecycle points
    and feeds the returned envelopes to [`PerceptionBus.ingest`](perception_bus.py).
    """

    def __init__(
        self,
        *,
        utc_clock: Callable[[], int] = lambda: int(time.time()),
        local_hour_for_unix: Callable[[int], float] | None = None,
    ) -> None:
        self._utc_clock = utc_clock
        self._local_hour_for_unix = local_hour_for_unix or (lambda _ts: 12.0)
        self._sessions: dict[str, SessionState] = {}

    # -- session lifecycle ------------------------------------------------

    def start_session(self, session_id: str) -> dict:
        """Idempotent session start.  Returns a session_lifecycle envelope."""
        now = self._utc_clock()
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionState(
                session_id=session_id, started_at_unix=now
            )
            return self._session_lifecycle_envelope(session_id, now, phase="start")
        # Already started: idempotent, no new event.
        return self._session_lifecycle_envelope(session_id, now, phase="start", event_id_suffix="dup")

    def end_session(self, session_id: str) -> dict | None:
        """End a session.  Returns an envelope or None if the session was unknown."""
        now = self._utc_clock()
        state = self._sessions.pop(session_id, None)
        if state is None:
            return None
        return self._session_lifecycle_envelope(session_id, now, phase="end")

    # -- message-derived signals -----------------------------------------

    def derive_message_signals(
        self,
        *,
        session_id: str,
        text: str,
        event_id: str,
    ) -> tuple[dict, dict, dict]:
        """Derive (message_gap, time_of_day, message_length) envelopes for one user message.

        ``duration_seconds`` and ``gap_seconds`` are server-trusted: computed
        from the injected clock and the session's last-message timestamp, not
        from any client payload.  The first message in a session (or a new
        session) yields a zero-duration gap event so the mapping table can
        skip it cleanly.
        """
        now = self._utc_clock()
        state = self._sessions.get(session_id)
        is_first = state is None or state.last_message_at_unix is None
        is_new_session = state is None
        if state is None:
            # Implicit session start: record it so subsequent messages have a baseline.
            state = SessionState(session_id=session_id, started_at_unix=now)
            self._sessions[session_id] = state
        # Defensive: never produce a negative duration on clock rollback.
        prev = state.last_message_at_unix if state.last_message_at_unix is not None else now
        duration = max(0, now - prev)
        state.last_message_at_unix = now

        gap_envelope = {
            "version": "aphrodite.chatbox.perception-event/1",
            "event_id": f"{event_id}:gap",
            "session_id": session_id,
            "kind": "message_gap",
            "observed_at": now,
            "payload": {
                "duration_seconds": float(duration),
                "is_first": bool(is_first),
                "is_new_session": bool(is_new_session),
            },
            "source": "server.derived",
        }
        local_hour = self._local_hour_for_unix(now)
        band = band_for_hour(local_hour)
        tod_envelope = {
            "version": "aphrodite.chatbox.perception-event/1",
            "event_id": f"{event_id}:tod",
            "session_id": session_id,
            "kind": "time_of_day",
            "observed_at": now,
            "payload": {"local_hour": float(local_hour), "band": band},
            "source": "server.derived",
        }
        length_envelope = {
            "version": "aphrodite.chatbox.perception-event/1",
            "event_id": f"{event_id}:len",
            "session_id": session_id,
            "kind": "message_length",
            "observed_at": now,
            "payload": {"char_count": int(len(text)), "gap_seconds": float(duration)},
            "source": "server.derived",
        }
        return gap_envelope, tod_envelope, length_envelope

    # -- typing state machine --------------------------------------------

    def ingest_typing(self, *, session_id: str, state: str, event_id: str) -> dict | None:
        """Validate and record a client-reported typing state.

        Returns a typing envelope to feed to the bus, or None if the event is
        a duplicate/no-op (e.g. start when already active, stop when idle).
        The server never lets the client submit a field delta; it only emits
        a discrete typing event.
        """
        now = self._utc_clock()
        session = self._sessions.setdefault(
            session_id, SessionState(session_id=session_id, started_at_unix=now)
        )
        if state == "start":
            if session.typing_active:
                return None
            session.typing_active = True
            session.typing_last_heartbeat_unix = now
            return self._typing_envelope(session_id, now, "start", event_id)
        if state == "heartbeat":
            if not session.typing_active:
                # Heartbeat without a preceding start: treat as an implicit start.
                session.typing_active = True
            session.typing_last_heartbeat_unix = now
            return self._typing_envelope(session_id, now, "heartbeat", event_id)
        if state == "stop":
            if not session.typing_active:
                return None
            session.typing_active = False
            session.typing_last_heartbeat_unix = None
            return self._typing_envelope(session_id, now, "stop", event_id)
        return None

    def expire_typing(self, *, session_id: str, event_id: str) -> dict | None:
        """Clear typing state if the heartbeat timeout has elapsed.

        Called by the dialogue service on a periodic sweep and on disconnect.
        Returns a stop envelope if typing was active and timed out, else None.
        """
        now = self._utc_clock()
        session = self._sessions.get(session_id)
        if session is None or not session.typing_active:
            return None
        last = session.typing_last_heartbeat_unix
        if last is None:
            return None
        if (now - last) < TYPING_HEARTBEAT_TIMEOUT_SECONDS:
            return None
        session.typing_active = False
        session.typing_last_heartbeat_unix = None
        return self._typing_envelope(session_id, now, "stop", event_id)

    def clear_typing_on_disconnect(self, *, session_id: str, event_id: str) -> dict | None:
        """Force-clear typing state when a socket disconnects."""
        session = self._sessions.get(session_id)
        if session is None or not session.typing_active:
            return None
        now = self._utc_clock()
        session.typing_active = False
        session.typing_last_heartbeat_unix = None
        return self._typing_envelope(session_id, now, "stop", event_id)

    # -- helpers ----------------------------------------------------------

    def _session_lifecycle_envelope(
        self, session_id: str, now: int, *, phase: str, event_id_suffix: str = ""
    ) -> dict:
        suffix = f":{event_id_suffix}" if event_id_suffix else ""
        return {
            "version": "aphrodite.chatbox.perception-event/1",
            "event_id": f"session:{session_id}:{phase}{suffix}",
            "session_id": session_id,
            "kind": "session_lifecycle",
            "observed_at": now,
            "payload": {"phase": phase},
            "source": "server.derived",
        }

    def _typing_envelope(self, session_id: str, now: int, state: str, event_id: str) -> dict:
        return {
            "version": "aphrodite.chatbox.perception-event/1",
            "event_id": event_id,
            "session_id": session_id,
            "kind": "typing",
            "observed_at": now,
            "payload": {"state": state},
            "source": "client.typing",
        }

    def has_session(self, session_id: str) -> bool:
        return session_id in self._sessions

    def is_typing(self, session_id: str) -> bool:
        state = self._sessions.get(session_id)
        return state is not None and state.typing_active
