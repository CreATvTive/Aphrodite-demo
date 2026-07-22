"""Versioned JSON protocol for the P1.3 trajectory WebSocket."""

from __future__ import annotations

import json
import math
from typing import Iterable

from app.chatbox.expression_gate import GateProjection
from app.chatbox.field_dynamics import FieldSnapshot
from app.chatbox.field_persistence import TrajectoryFrame
from app.chatbox.field_runtime import RegistryProxy


TRAJECTORY_PROTOCOL_VERSION = "aphrodite.chatbox.trajectory-ws/1"
INITIAL_HISTORY_FRAMES = 900
MAX_RESUME_FRAMES = 3600
HISTORY_BATCH_FRAMES = 50
CLIENT_LIVE_QUEUE_FRAMES = 32
HEARTBEAT_SECONDS = 15.0
SUBSCRIBE_TIMEOUT_SECONDS = 5.0
MAX_CLIENT_TEXT_BYTES = 4096


class ProtocolError(ValueError):
    def __init__(self, code: str, detail: str, *, retry: str = "none") -> None:
        self.code = code
        self.detail = detail
        self.retry = retry
        super().__init__(f"{code}: {detail}")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("invalid_message", f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ProtocolError("invalid_message", f"non-finite JSON constant {value}")


def parse_cursor(value: object, *, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if (
        not isinstance(value, str)
        or not value
        or not value.isascii()
        or not value.isdecimal()
        or (len(value) > 1 and value[0] == "0")
    ):
        raise ProtocolError("invalid_cursor", "cursor must be a canonical decimal string")
    return int(value)


def parse_subscribe(text: str) -> int | None:
    if not isinstance(text, str):
        raise ProtocolError("invalid_message", "subscribe message must be text")
    if len(text.encode("utf-8")) > MAX_CLIENT_TEXT_BYTES:
        raise ProtocolError("oversize", "client text exceeds 4096 bytes")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except ProtocolError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid_message", "malformed JSON") from exc
    expected = {"version", "type", "after_cursor"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ProtocolError("invalid_message", "subscribe keys must match the v1 contract")
    if value["version"] != TRAJECTORY_PROTOCOL_VERSION:
        raise ProtocolError("unsupported_version", "unsupported protocol version")
    if value["type"] != "subscribe":
        raise ProtocolError("invalid_message", "first message type must be subscribe")
    return parse_cursor(value["after_cursor"], nullable=True)


def serialize_message(message: dict) -> str:
    if not isinstance(message, dict) or list(message)[:2] != ["version", "type"]:
        raise ValueError("server message must begin with version and type")
    return json.dumps(
        message,
        separators=(",", ":"),
        ensure_ascii=False,
        sort_keys=False,
        allow_nan=False,
    )


def _base(type_name: str) -> dict:
    return {"version": TRAJECTORY_PROTOCOL_VERSION, "type": type_name}


def frame_message(frame: TrajectoryFrame) -> dict:
    message = {
        "cursor": str(frame.cursor),
        "boot_id": frame.boot_id,
        "field_tick": str(frame.field_tick),
        "utc_unix_ns": str(frame.utc_unix_ns),
        "dimensions": [
            {
                "ordinal": point.ordinal,
                "dim_id": point.dim_id,
                "value": point.value,
                "velocity": point.velocity,
                "attractor": point.attractor,
                "slow_baseline": point.slow_baseline,
                "ou_acceleration": point.ou_acceleration,
            }
            for point in frame.dimensions
        ],
    }
    _validate_finite_tree(message)
    return message


def hello_message(*, connection_id: str, head_cursor: int | None) -> dict:
    return {
        **_base("hello"),
        "connection_id": connection_id,
        "head_cursor": None if head_cursor is None else str(head_cursor),
        "tick_interval_seconds": 1.0,
        "initial_history_frames": INITIAL_HISTORY_FRAMES,
        "max_resume_frames": MAX_RESUME_FRAMES,
        "history_batch_frames": HISTORY_BATCH_FRAMES,
    }


def registry_message(registry: RegistryProxy) -> dict:
    return {
        **_base("registry"),
        "dimensions": [
            {
                "ordinal": ordinal,
                "dim_id": registration.dim_id,
                "temporary_name": registration.temporary_name,
                "birth_time": registration.birth_time,
                "strength": registration.strength,
                "trigger_count": str(registration.trigger_count),
            }
            for ordinal, registration in enumerate(registry.registrations)
        ],
    }


def gate_message(projection: GateProjection) -> dict:
    return {
        **_base("gate"),
        "gate_version": projection.version,
        "mode": projection.mode,
        "temperature": projection.temperature,
        "temperature_applied": projection.temperature_applied,
        "bandwidth": projection.bandwidth,
        "weights": [
            {"ordinal": item.ordinal, "dim_id": item.dim_id, "weight": item.weight}
            for item in projection.weights
        ],
    }


def history_begin_message(
    *, mode: str, after_cursor: int | None, cutoff_cursor: int | None, truncated_before: bool
) -> dict:
    return {
        **_base("history_begin"),
        "mode": mode,
        "after_cursor": None if after_cursor is None else str(after_cursor),
        "cutoff_cursor": None if cutoff_cursor is None else str(cutoff_cursor),
        "truncated_before": truncated_before,
    }


def history_batch_message(frames: Iterable[TrajectoryFrame]) -> dict:
    items = list(frames)
    if len(items) > HISTORY_BATCH_FRAMES:
        raise ValueError("history batch exceeds contract limit")
    return {**_base("history_batch"), "frames": [frame_message(frame) for frame in items]}


def current_message(snapshot: FieldSnapshot) -> dict:
    message = {
        **_base("current"),
        "field_tick": str(snapshot.tick),
        "dimensions": [
            {
                "ordinal": ordinal,
                "dim_id": item.dim_id,
                "value": item.value,
                "velocity": item.velocity,
                "attractor": item.attractor,
                "slow_baseline": item.soft_restoring_baseline,
                "ou_acceleration": item.ou_acceleration,
            }
            for ordinal, item in enumerate(snapshot.dimensions)
        ],
    }
    _validate_finite_tree(message)
    return message


def history_end_message(*, cutoff_cursor: int | None, frames: tuple[TrajectoryFrame, ...]) -> dict:
    return {
        **_base("history_end"),
        "cutoff_cursor": None if cutoff_cursor is None else str(cutoff_cursor),
        "first_cursor": None if not frames else str(frames[0].cursor),
        "last_cursor": None if not frames else str(frames[-1].cursor),
        "frame_count": len(frames),
    }


def live_message(frame: TrajectoryFrame) -> dict:
    return {**_base("live"), "frame": frame_message(frame)}


def error_message(*, code: str, fatal: bool, retry: str, detail: str) -> dict:
    if retry not in {"none", "fresh", "later"}:
        raise ValueError("invalid retry")
    return {**_base("error"), "code": code, "fatal": fatal, "retry": retry, "detail": detail}


def _validate_finite_tree(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("protocol numeric value must be finite")
    if isinstance(value, dict):
        for item in value.values():
            _validate_finite_tree(item)
    elif isinstance(value, list):
        for item in value:
            _validate_finite_tree(item)
