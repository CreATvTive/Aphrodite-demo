"""P3 task-card 7: versioned perception event envelope and fail-closed schema.

The envelope is the stable transport contract for the perception bus:

```json
{
  "version": "aphrodite.chatbox.perception-event/1",
  "event_id": "...",        // client-supplied, idempotency key
  "session_id": "...",      // idempotent session lifecycle id
  "kind": "message_gap",    // one of KNOWN_KINDS
  "observed_at": 1700000000, // int unix seconds, server-trusted
  "payload": { ... },       // kind-specific, strictly validated
  "source": "server.derived" // or "client.typing" etc.
}
```

Adding a new ``kind`` does not change this envelope; it only extends the
payload validators and the mapping table.  Unknown version / kind / payload
are fail-closed: the bus rejects the event and records nothing.

Imports are restricted to the Python standard library and
[`perception_config`](perception_config.py).  No provider, writer, runtime,
or quarantined imports.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

from app.chatbox.perception_config import (
    KNOWN_KINDS,
    PERCEPTION_EVENT_VERSION,
)


class PerceptionSchemaError(ValueError):
    """Stable, fail-closed envelope validation error."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class PerceptionEvent:
    """Validated, immutable perception event envelope."""

    event_id: str
    session_id: str
    kind: str
    observed_at: int
    payload: Mapping[str, object]
    source: str

    def to_primitive(self) -> dict:
        """Return a fresh canonical dict for persistence/transport."""
        return {
            "version": PERCEPTION_EVENT_VERSION,
            "event_id": self.event_id,
            "session_id": self.session_id,
            "kind": self.kind,
            "observed_at": self.observed_at,
            "payload": dict(self.payload),
            "source": self.source,
        }


_EVENT_ID_MAX = 128
_SESSION_ID_MAX = 128
_SOURCE_MAX = 64
_PAYLOAD_MAX_BYTES = 4096
_OBSERVED_AT_MAX = 2_000_000_000_000  # year ~2603, generous


def _require_str(value: object, *, field: str, max_len: int) -> str:
    if not isinstance(value, str):
        raise PerceptionSchemaError("invalid_type", f"{field} must be a string")
    if not value:
        raise PerceptionSchemaError("empty_value", f"{field} must be non-empty")
    if len(value) > max_len:
        raise PerceptionSchemaError("oversize", f"{field} exceeds {max_len} chars")
    return value


def _require_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PerceptionSchemaError("invalid_type", f"{field} must be an int")
    return value


def _require_finite_float(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PerceptionSchemaError("invalid_type", f"{field} must be a number")
    f = float(value)
    if not math.isfinite(f):
        raise PerceptionSchemaError("non_finite", f"{field} must be finite")
    return f


def _require_payload_dict(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise PerceptionSchemaError("invalid_type", "payload must be an object")
    return value


# Per-kind payload validators.  Each returns the validated payload as a fresh
# dict of typed values.  Unknown keys are rejected (fail-closed).  Every
# numeric field is required to be a real finite number (bool rejected).


def _validate_message_gap(payload: Mapping[str, object]) -> dict:
    keys = frozenset({"duration_seconds", "is_first", "is_new_session"})
    if frozenset(payload) != keys:
        raise PerceptionSchemaError(
            "payload_keys_mismatch",
            "message_gap payload keys must be {duration_seconds,is_first,is_new_session}",
        )
    duration = _require_finite_float(payload["duration_seconds"], field="duration_seconds")
    if duration < 0.0:
        raise PerceptionSchemaError("negative_duration", "duration_seconds must be >= 0")
    is_first = payload["is_first"]
    if not isinstance(is_first, bool):
        raise PerceptionSchemaError("invalid_type", "is_first must be bool")
    is_new_session = payload["is_new_session"]
    if not isinstance(is_new_session, bool):
        raise PerceptionSchemaError("invalid_type", "is_new_session must be bool")
    return {
        "duration_seconds": duration,
        "is_first": is_first,
        "is_new_session": is_new_session,
    }


def _validate_time_of_day(payload: Mapping[str, object]) -> dict:
    keys = frozenset({"local_hour", "band"})
    if frozenset(payload) != keys:
        raise PerceptionSchemaError(
            "payload_keys_mismatch",
            "time_of_day payload keys must be {local_hour,band}",
        )
    local_hour = _require_finite_float(payload["local_hour"], field="local_hour")
    if not 0.0 <= local_hour < 24.0:
        raise PerceptionSchemaError("hour_out_of_range", "local_hour must be in [0, 24)")
    band = payload["band"]
    if not isinstance(band, str) or band not in {"day", "night"}:
        raise PerceptionSchemaError("invalid_band", "band must be 'day' or 'night'")
    return {"local_hour": local_hour, "band": band}


def _validate_message_length(payload: Mapping[str, object]) -> dict:
    keys = frozenset({"char_count", "gap_seconds"})
    if frozenset(payload) != keys:
        raise PerceptionSchemaError(
            "payload_keys_mismatch",
            "message_length payload keys must be {char_count,gap_seconds}",
        )
    char_count = _require_int(payload["char_count"], field="char_count")
    if char_count < 0:
        raise PerceptionSchemaError("negative_count", "char_count must be >= 0")
    gap = _require_finite_float(payload["gap_seconds"], field="gap_seconds")
    if gap < 0.0:
        raise PerceptionSchemaError("negative_duration", "gap_seconds must be >= 0")
    return {"char_count": char_count, "gap_seconds": gap}


def _validate_session_lifecycle(payload: Mapping[str, object]) -> dict:
    keys = frozenset({"phase"})
    if frozenset(payload) != keys:
        raise PerceptionSchemaError(
            "payload_keys_mismatch",
            "session_lifecycle payload keys must be {phase}",
        )
    phase = payload["phase"]
    if not isinstance(phase, str) or phase not in {"start", "end"}:
        raise PerceptionSchemaError("invalid_phase", "phase must be 'start' or 'end'")
    return {"phase": phase}


def _validate_typing(payload: Mapping[str, object]) -> dict:
    keys = frozenset({"state"})
    if frozenset(payload) != keys:
        raise PerceptionSchemaError(
            "payload_keys_mismatch",
            "typing payload keys must be {state}",
        )
    state = payload["state"]
    if not isinstance(state, str) or state not in {"start", "heartbeat", "stop"}:
        raise PerceptionSchemaError("invalid_state", "state must be start|heartbeat|stop")
    return {"state": state}


_PAYLOAD_VALIDATORS = {
    "message_gap": _validate_message_gap,
    "time_of_day": _validate_time_of_day,
    "message_length": _validate_message_length,
    "session_lifecycle": _validate_session_lifecycle,
    "typing": _validate_typing,
}


def validate_event(envelope: Mapping[str, object]) -> PerceptionEvent:
    """Validate a raw envelope dict and return an immutable [`PerceptionEvent`](perception_schema.py).

    Fail-closed on any structural, type, range, or unknown-kind violation.
    """
    if not isinstance(envelope, dict):
        raise PerceptionSchemaError("invalid_type", "envelope must be an object")
    required = ("version", "event_id", "session_id", "kind", "observed_at", "payload", "source")
    missing = [k for k in required if k not in envelope]
    if missing:
        raise PerceptionSchemaError("missing_keys", f"missing: {missing}")
    extra = [k for k in envelope if k not in required]
    if extra:
        raise PerceptionSchemaError("unexpected_keys", f"unexpected: {extra}")
    version = envelope["version"]
    if not isinstance(version, str) or version != PERCEPTION_EVENT_VERSION:
        raise PerceptionSchemaError("unsupported_version", "version mismatch")
    event_id = _require_str(envelope["event_id"], field="event_id", max_len=_EVENT_ID_MAX)
    session_id = _require_str(envelope["session_id"], field="session_id", max_len=_SESSION_ID_MAX)
    kind = envelope["kind"]
    if not isinstance(kind, str) or kind not in KNOWN_KINDS:
        raise PerceptionSchemaError("unknown_kind", f"kind must be one of {KNOWN_KINDS}")
    observed_at = _require_int(envelope["observed_at"], field="observed_at")
    if observed_at < 0 or observed_at > _OBSERVED_AT_MAX:
        raise PerceptionSchemaError("observed_at_out_of_range", "observed_at out of range")
    source = _require_str(envelope["source"], field="source", max_len=_SOURCE_MAX)
    raw_payload = _require_payload_dict(envelope["payload"])
    validator = _PAYLOAD_VALIDATORS[kind]
    payload = validator(raw_payload)
    # Bound the serialized payload size defensively.
    import json as _json
    try:
        text = _json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PerceptionSchemaError("payload_not_serializable", str(exc)) from exc
    if len(text.encode("utf-8")) > _PAYLOAD_MAX_BYTES:
        raise PerceptionSchemaError("payload_oversize", "payload exceeds size limit")
    return PerceptionEvent(
        event_id=event_id,
        session_id=session_id,
        kind=kind,
        observed_at=observed_at,
        payload=payload,
        source=source,
    )
