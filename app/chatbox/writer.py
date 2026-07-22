"""P2 task card 5: writer application boundary for chatbox v0.

The writer is the single bounded entry that receives a [`ParsedReply`](provider/structure_a.py)
from the provider (task card 4) and applies the validated increment to the field
attractor **only** through [`FieldRuntime.move_attractor()`](field_runtime.py).
It never touches field capsule state, value, velocity, OU, or slow-baseline
directly, and it never accesses SQLite.

Writer authority contract (Phase C.3 + AGENTS.md):

* writer code may only call ``FieldRuntime.move_attractor`` — no state mutation;
* single-move per-dimension amplitude is capped at 0.3 by the parser; the writer
  re-validates and drops anything out of range or non-finite;
* on parse failure / empty increment: the natural-language log is still
  persisted (via a delta=0 no-op attractor event), but no attractor moves;
* on provider degradation: no writes at all (no log, no moves);
* duplicate ``call_id`` submissions are idempotent (in-memory dedup);
* no API key, system prompt, or sensitive info is logged.

The natural-language writer log is carried in the ``rationale`` field of the
attractor event(s), reusing the existing append-only persistence contract
without schema changes.  When there are valid increments, each produces one
attractor event.  When there are none (parse failure / empty), a single
delta=0 no-op event persists the log without moving any attractor.

Imports are restricted to the Python standard library plus
``field_dynamics``, ``field_runtime``, and ``provider.structure_a``.  No
quarantined modules, no SQLite, no capsule access, no direct field-private
state mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

from app.chatbox.field_dynamics import AttractorMove, FieldSnapshot
from app.chatbox.field_runtime import FieldRuntime, FieldRuntimeError
from app.chatbox.provider.structure_a import (
    INCREMENT_AMPLITUDE_CAP,
    ParsedReply,
)


WRITER_SOURCE = "chatbox.writer"
# Re-export the frozen amplitude cap so tests import it from one place.
WRITER_AMPLITUDE_CAP = INCREMENT_AMPLITUDE_CAP
_REPLY_TRUNCATION_LIMIT = 2000


@dataclass(frozen=True, slots=True)
class WriterMoveResult:
    """Result of attempting one dimension's attractor move.

    ``applied`` is True iff ``FieldRuntime.move_attractor`` accepted the move
    and persisted the event.  ``reject_reason`` is empty when applied, or a
    short stable error string when the runtime rejected the command.
    """

    dim_id: str
    requested_delta: float
    applied: bool
    reject_reason: str


@dataclass(frozen=True, slots=True)
class WriterOutcome:
    """Result of one writer [`Writer.apply()`](writer.py) call.

    ``moves`` is empty when the call was deduplicated, degraded, or produced no
    valid increment.  ``log_persisted`` is True iff at least one attractor
    event was committed for this call (either real moves or a no-op log event).
    ``deduplicated`` is True iff the ``call_id`` was already processed and this
    call performed no writes.
    """

    call_id: str
    reply_text: str
    degraded: bool
    parse_ok: bool
    parse_note: str
    moves: tuple[WriterMoveResult, ...]
    log_persisted: bool
    deduplicated: bool


def _truncate_reply(text: str, limit: int = _REPLY_TRUNCATION_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…(truncated)"


def _build_rationale(call_id: str, reply_text: str, parse_note: str) -> str:
    """Build a non-empty rationale string for the attractor event.

    The rationale carries the writer call id, the parse note, and the
    (truncated) natural-language reply.  It never contains API keys or system
    prompts — only the model's reply text, which is the writer's natural-language
    output.
    """
    reply = _truncate_reply(reply_text)
    if not reply.strip():
        reply = "(empty reply)"
    return f"call={call_id}|note={parse_note}|reply={reply}"


def _attractor_for_dim(snapshot: FieldSnapshot, dim_id: str) -> float | None:
    for dim in snapshot.dimensions:
        if dim.dim_id == dim_id:
            return float(dim.attractor)
    return None


class Writer:
    """Writer application boundary — attractor-only state mutation.

    Construct with a live [`FieldRuntime`](field_runtime.py).  Each
    [`apply()`](writer.py) call receives a [`ParsedReply`](provider/structure_a.py)
    and a caller-supplied ``call_id``, validates the increment batch against the
    live registry, and applies each valid entry via
    ``FieldRuntime.move_attractor``.  The writer never obtains or mutates
    capsule state, value, velocity, OU, or slow-baseline; it only calls the
    public attractor command API.

    Idempotency: a ``call_id`` that has already been processed by this
    ``Writer`` instance is a no-op — no moves, no log event, ``deduplicated=True``.
    The dedup set is in-memory and per-instance; it does not survive restarts.
    """

    __slots__ = ("_runtime", "_processed_call_ids")

    def __init__(self, runtime: FieldRuntime) -> None:
        if not isinstance(runtime, FieldRuntime):
            raise TypeError("runtime must be a FieldRuntime instance")
        self._runtime = runtime
        self._processed_call_ids: set[str] = set()

    @property
    def runtime(self) -> FieldRuntime:
        return self._runtime

    def apply(self, parsed: ParsedReply, *, call_id: str) -> WriterOutcome:
        """Apply a parsed provider reply to the field attractor.

        Raises ``ValueError`` if ``call_id`` is not a non-empty string.
        Raises ``FieldRuntimeError`` only if the runtime is closed/poisoned at
        entry and a log write is attempted; rejected individual moves are
        recorded in the outcome, not raised.
        """
        if not isinstance(call_id, str) or not call_id.strip():
            raise ValueError("call_id must be a non-empty string")

        # Idempotency: duplicate call_id is a no-op.
        if call_id in self._processed_call_ids:
            return WriterOutcome(
                call_id=call_id,
                reply_text=parsed.reply_text,
                degraded=parsed.degraded,
                parse_ok=parsed.parsed_ok,
                parse_note=parsed.parse_note,
                moves=(),
                log_persisted=False,
                deduplicated=True,
            )

        # Provider degradation: no writes at all (no log, no moves).
        if parsed.degraded:
            self._processed_call_ids.add(call_id)
            return WriterOutcome(
                call_id=call_id,
                reply_text=parsed.reply_text,
                degraded=True,
                parse_ok=parsed.parsed_ok,
                parse_note=parsed.parse_note,
                moves=(),
                log_persisted=False,
                deduplicated=False,
            )

        registry = self._runtime.registry_proxy()
        known_dims = frozenset(registry.dim_ids)

        # Validate the entire batch before executing any move.
        validated: list[tuple[str, float]] = []
        if parsed.parsed_ok:
            for dim_id, delta in parsed.increment_items:
                if not isinstance(dim_id, str) or dim_id not in known_dims:
                    continue
                if isinstance(delta, bool) or not isinstance(delta, (int, float)):
                    continue
                if not math.isfinite(delta):
                    continue
                f_delta = float(delta)
                if abs(f_delta) > WRITER_AMPLITUDE_CAP + 1e-12:
                    continue
                validated.append((dim_id, f_delta))

        rationale = _build_rationale(call_id, parsed.reply_text, parsed.parse_note)
        moves: list[WriterMoveResult] = []
        any_applied = False

        # Execute validated moves one by one via the runtime's public API.
        # InvalidAttractorMoveError is a ValueError subclass; the runtime may
        # also raise FieldRuntimeError on persistence failure.  Both are
        # recorded as rejected moves rather than re-raised, so a single bad
        # dimension does not abort the whole batch.
        for dim_id, delta in validated:
            move = AttractorMove(
                dim_id=dim_id,
                delta=delta,
                source=WRITER_SOURCE,
                rationale=rationale,
            )
            try:
                self._runtime.move_attractor(move)
                moves.append(
                    WriterMoveResult(
                        dim_id=dim_id,
                        requested_delta=delta,
                        applied=True,
                        reject_reason="",
                    )
                )
                any_applied = True
            except (FieldRuntimeError, ValueError) as exc:
                reason = getattr(exc, "code", "rejected")
                moves.append(
                    WriterMoveResult(
                        dim_id=dim_id,
                        requested_delta=delta,
                        applied=False,
                        reject_reason=str(reason),
                    )
                )
                # If the runtime poisoned itself (persistence failure), stop.
                if isinstance(exc, FieldRuntimeError) and not self._runtime.healthy:
                    break

        # If no moves were applied, persist a delta=0 no-op log event so the
        # natural-language writer log survives even when the increment is
        # empty, malformed, or fully rejected.  The attractor does not move.
        log_persisted = any_applied
        if not any_applied and registry.dim_ids:
            first_dim = registry.dim_ids[0]
            noop_move = AttractorMove(
                dim_id=first_dim,
                delta=0.0,
                source=WRITER_SOURCE,
                rationale=rationale,
            )
            try:
                self._runtime.move_attractor(noop_move)
                log_persisted = True
            except (FieldRuntimeError, ValueError):
                log_persisted = False

        self._processed_call_ids.add(call_id)
        return WriterOutcome(
            call_id=call_id,
            reply_text=parsed.reply_text,
            degraded=False,
            parse_ok=parsed.parsed_ok,
            parse_note=parsed.parse_note,
            moves=tuple(moves),
            log_persisted=log_persisted,
            deduplicated=False,
        )
