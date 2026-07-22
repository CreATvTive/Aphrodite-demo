"""Independent versioned JSON protocol for P2 dialogue WebSockets."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re

from app.chatbox.dialogue_persistence import DialogueMessage


DIALOGUE_PROTOCOL_VERSION = "aphrodite.chatbox.dialogue-ws/1"
MAX_CLIENT_MESSAGE_BYTES = 20_000
MAX_USER_TEXT_BYTES = 16_000
MAX_USER_TEXT_CHARS = 4_000
MAX_REPLY_TEXT_CHARS = 8_000
MAX_HISTORY_MESSAGES = 200
SEND_TIMEOUT_SECONDS = 3.0
_TURN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_TYPING_STATE = re.compile(r"^(start|heartbeat|stop)$")


class DialogueProtocolError(ValueError):
    def __init__(self, code: str, detail: str, *, fatal: bool = False) -> None:
        self.code = code
        self.detail = detail
        self.fatal = fatal
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class TurnCommand:
    type: str
    client_turn_id: str
    text: str | None = None
    typing_state: str | None = None


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise DialogueProtocolError("invalid_message", "duplicate JSON key", fatal=True)
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise DialogueProtocolError("invalid_message", "non-finite JSON is unsupported", fatal=True)


def parse_client_message(text: str) -> TurnCommand:
    if not isinstance(text, str):
        raise DialogueProtocolError("invalid_message", "message must be text", fatal=True)
    if len(text.encode("utf-8")) > MAX_CLIENT_MESSAGE_BYTES:
        raise DialogueProtocolError("oversize", "message exceeds the size limit", fatal=True)
    try:
        value = json.loads(
            text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant
        )
    except DialogueProtocolError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DialogueProtocolError("invalid_message", "malformed JSON", fatal=True) from exc
    if not isinstance(value, dict):
        raise DialogueProtocolError("invalid_message", "message must be an object", fatal=True)
    if value.get("version") != DIALOGUE_PROTOCOL_VERSION:
        raise DialogueProtocolError("unsupported_version", "unsupported protocol version", fatal=True)
    type_name = value.get("type")
    if type_name == "turn.submit":
        if set(value) != {"version", "type", "client_turn_id", "text"}:
            raise DialogueProtocolError("invalid_message", "submit keys do not match the contract")
        turn_id = value.get("client_turn_id")
        user_text = value.get("text")
        if not isinstance(turn_id, str) or _TURN_ID.fullmatch(turn_id) is None:
            raise DialogueProtocolError("invalid_turn_id", "client turn id is invalid")
        if not isinstance(user_text, str):
            raise DialogueProtocolError("invalid_text", "turn text must be a string")
        normalized = user_text.strip()
        if not normalized:
            raise DialogueProtocolError("empty_text", "turn text must not be empty")
        if len(normalized) > MAX_USER_TEXT_CHARS or len(normalized.encode("utf-8")) > MAX_USER_TEXT_BYTES:
            raise DialogueProtocolError("text_too_long", "turn text exceeds the size limit")
        return TurnCommand(type_name, turn_id, normalized)
    if type_name == "turn.cancel":
        if set(value) != {"version", "type", "client_turn_id"}:
            raise DialogueProtocolError("invalid_message", "cancel keys do not match the contract")
        turn_id = value.get("client_turn_id")
        if not isinstance(turn_id, str) or _TURN_ID.fullmatch(turn_id) is None:
            raise DialogueProtocolError("invalid_turn_id", "client turn id is invalid")
        return TurnCommand(type_name, turn_id)
    if type_name == "typing.submit":
        if set(value) != {"version", "type", "client_turn_id", "state"}:
            raise DialogueProtocolError("invalid_message", "typing keys do not match the contract")
        turn_id = value.get("client_turn_id")
        if not isinstance(turn_id, str) or _TURN_ID.fullmatch(turn_id) is None:
            raise DialogueProtocolError("invalid_turn_id", "client turn id is invalid")
        state = value.get("state")
        if not isinstance(state, str) or _TYPING_STATE.fullmatch(state) is None:
            raise DialogueProtocolError("invalid_typing_state", "typing state must be start|heartbeat|stop")
        return TurnCommand(type_name, turn_id, typing_state=state)
    raise DialogueProtocolError("invalid_message", "unknown message type")


def serialize_dialogue_message(message: dict) -> str:
    if not isinstance(message, dict) or list(message)[:2] != ["version", "type"]:
        raise ValueError("message must begin with version and type")
    return json.dumps(message, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def base_message(type_name: str) -> dict:
    return {"version": DIALOGUE_PROTOCOL_VERSION, "type": type_name}


def hello_message(*, connection_id: str, provider_state: str) -> dict:
    return {
        **base_message("hello"),
        "connection_id": connection_id,
        "provider_state": provider_state,
        "max_text_chars": MAX_USER_TEXT_CHARS,
    }


def history_message(messages: tuple[DialogueMessage, ...]) -> dict:
    return {
        **base_message("history"),
        "messages": [
            {
                "message_id": str(message.message_id),
                "client_turn_id": message.client_turn_id,
                "role": message.role,
                "segment_index": message.segment_index,
                "text": message.content,
                "utc_unix_ns": str(message.utc_unix_ns),
            }
            for message in messages
        ],
    }


def error_message(
    *, client_turn_id: str | None, code: str, detail: str, fatal: bool, retry: bool
) -> dict:
    return {
        **base_message("turn.error"),
        "client_turn_id": client_turn_id,
        "code": code,
        "detail": detail,
        "fatal": fatal,
        "retry": retry,
    }


# P4.10: strict server-only proactive message frame.  It is additive: it does
# not modify any existing turn message type and is never submitted by the
# client.  The proactive_id is a stable, unique, restart-safe admission id.
_PROACTIVE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def proactive_message(
    *,
    proactive_id: str,
    segment_index: int,
    segment_count: int,
    text: str,
    utc_unix_ns: int,
    typewriter_ms: int | None = None,
) -> dict:
    """Build a strict server-only proactive assistant message frame.

    The frame is emitted only by the proactive output boundary, never by a
    client.  It carries a stable proactive id, segment index/count, non-empty
    text, and a persisted utc unix ns stamp.  An optional typewriter interval
    may be attached for the view's reduced-motion semantics.
    """
    if not isinstance(proactive_id, str) or _PROACTIVE_ID.fullmatch(proactive_id) is None:
        raise ValueError("proactive_id is invalid")
    if not isinstance(segment_index, int) or isinstance(segment_index, bool) or segment_index < 0:
        raise ValueError("segment_index must be a non-negative int")
    if not isinstance(segment_count, int) or isinstance(segment_count, bool) or segment_count <= 0:
        raise ValueError("segment_count must be a positive int")
    if segment_index >= segment_count:
        raise ValueError("segment_index must be < segment_count")
    if not isinstance(text, str) or not text:
        raise ValueError("text must be a non-empty string")
    if not isinstance(utc_unix_ns, int) or isinstance(utc_unix_ns, bool) or utc_unix_ns < 0:
        raise ValueError("utc_unix_ns must be a non-negative int")
    frame = {
        **base_message("proactive.stream"),
        "proactive_id": proactive_id,
        "segment_index": segment_index,
        "segment_count": segment_count,
        "text": text,
        "utc_unix_ns": utc_unix_ns,
    }
    if typewriter_ms is not None:
        if not isinstance(typewriter_ms, int) or isinstance(typewriter_ms, bool) or typewriter_ms <= 0 or typewriter_ms > 1000:
            raise ValueError("typewriter_ms must be a positive int <= 1000")
        frame["typewriter_ms"] = typewriter_ms
    return frame
