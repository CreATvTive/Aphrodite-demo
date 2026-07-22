"""P3 task-card 7: registry-driven perception event bus and dynamics ingress.

The bus is the single entry point for perception events.  It:

* validates every event via [`perception_schema.validate_event`](perception_schema.py);
* uses persistence UNIQUE for ingress deduplication and field-side durable
  receipts for mutation deduplication;
* persists append-only via [`perception_persistence`](perception_persistence.py);
* maps the event to bounded attractor deltas via
  [`perception_mapping.map_event`](perception_mapping.py);
* applies the complete ordered delta set through the runtime's idempotent
  ``move_attractor_batch`` command, tagging the source as
  [`PERCEPTION_SOURCE`](perception_config.py) so perception influence is
  distinguishable from writer influence in the event log;
* records consumption so a restart does not re-apply already-consumed events;
* supports a bounded synchronous dispatch with a bounded in-process queue and
  backpressure: a full queue drops the newest event and reports it rather than
  blocking the caller or flooding the runtime;
* isolates subscriber exceptions so one bad subscriber cannot poison the main
  runtime.

This module never imports the provider, the writer, or quarantined code.  It
touches field state only through [`FieldRuntime.move_attractor`](field_runtime.py),
preserving the single-owner / append-only / recovery semantics.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import time
from typing import Callable, Sequence

from app.chatbox.field_dynamics import AttractorMove
from app.chatbox.field_runtime import FieldRuntime, FieldRuntimeError
from app.chatbox.perception_config import (
    BUS_QUEUE_MAX,
    PERCEPTION_SOURCE,
    REPLAY_BATCH_LIMIT,
)
from app.chatbox.perception_mapping import MappingResult, map_event
from app.chatbox.perception_persistence import (
    PerceptionPersistenceError,
    PerceptionPersistenceStore,
)
from app.chatbox.perception_schema import (
    PerceptionEvent,
    PerceptionSchemaError,
    validate_event,
)


class PerceptionBusError(RuntimeError):
    """Stable bus-level error (validation, backpressure, persistence)."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class IngressOutcome:
    """Result of ingesting one event."""

    event_id: str
    kind: str
    accepted: bool
    deduplicated: bool
    dropped_backpressure: bool
    mapping: MappingResult | None
    applied_dim_ids: tuple[str, ...]
    rejected_dim_ids: tuple[str, ...]
    skipped_dim_ids: tuple[str, ...]
    error_code: str | None
    error_detail: str | None
    field_application_deduplicated: bool = False
    consumption_recorded: bool = False


class PerceptionBus:
    """Registry-driven perception event bus with bounded synchronous dispatch.

    Construct with a live [`FieldRuntime`](field_runtime.py) and a
    [`PerceptionPersistenceStore`](perception_persistence.py).  ``utc_clock``
    is injected so tests are deterministic.  ``subscribers`` are optional
    callables that receive the validated event; an exception in a subscriber
    is reported via ``subscriber_errors`` and never propagates to the caller
    or the runtime.
    """

    def __init__(
        self,
        runtime: FieldRuntime,
        persistence: PerceptionPersistenceStore,
        *,
        utc_clock: Callable[[], int] = time.time_ns,
    ) -> None:
        if not isinstance(runtime, FieldRuntime):
            raise TypeError("runtime must be a FieldRuntime instance")
        if not isinstance(persistence, PerceptionPersistenceStore):
            raise TypeError("persistence must be a PerceptionPersistenceStore")
        self._runtime = runtime
        self._persistence = persistence
        self._utc_clock = utc_clock
        self._queue: deque[PerceptionEvent] = deque(maxlen=BUS_QUEUE_MAX)
        self._seen_event_ids: set[str] = set()
        self._subscribers: list[Callable[[PerceptionEvent], None]] = []
        self.subscriber_errors: list[tuple[str, str]] = []
        # Pre-load seen ids so a restart stays idempotent across the in-memory
        # dedup set without re-reading the whole log on every ingest.
        self._reload_seen_ids()

    def _reload_seen_ids(self) -> None:
        # Load all persisted event_ids into the in-memory dedup set so a
        # restart does not re-validate / re-persist an already-known event.
        try:
            rows = self._persistence._conn.execute(  # noqa: SLF001
                "SELECT event_id FROM perception_events"
            ).fetchall()
        except PerceptionPersistenceError:
            return
        for (event_id,) in rows:
            self._seen_event_ids.add(str(event_id))

    @property
    def runtime(self) -> FieldRuntime:
        return self._runtime

    @property
    def persistence(self) -> PerceptionPersistenceStore:
        return self._persistence

    @property
    def queued(self) -> int:
        return len(self._queue)

    def subscribe(self, callback: Callable[[PerceptionEvent], None]) -> None:
        if not callable(callback):
            raise TypeError("subscriber must be callable")
        self._subscribers.append(callback)

    def ingest(self, envelope: dict) -> IngressOutcome:
        """Validate, persist, map, and apply one event envelope.

        Returns an [`IngressOutcome`](perception_bus.py) describing the result.
        Fail-closed: any validation or persistence error is reported in the
        outcome and never silently swallowed.
        """
        # 1. Validate the envelope.
        try:
            event = validate_event(envelope)
        except PerceptionSchemaError as exc:
            return IngressOutcome(
                event_id="",
                kind="",
                accepted=False,
                deduplicated=False,
                dropped_backpressure=False,
                mapping=None,
                applied_dim_ids=(),
                rejected_dim_ids=(),
                skipped_dim_ids=(),
                error_code=exc.code,
                error_detail=exc.detail,
            )

        # 2. Persisted dedup fast path.  The set is only a read optimization;
        #    the durable store remains authoritative for restart/race safety.
        if (
            event.event_id in self._seen_event_ids
            and self._persistence.event_exists(event.event_id)
        ):
            return IngressOutcome(
                event_id=event.event_id,
                kind=event.kind,
                accepted=False,
                deduplicated=True,
                dropped_backpressure=False,
                mapping=None,
                applied_dim_ids=(),
                rejected_dim_ids=(),
                skipped_dim_ids=(),
                error_code=None,
                error_detail=None,
            )

        # 3. Bounded queue / backpressure.  If the in-process queue is full
        #    (deque maxlen drops the oldest), we still accept the event into
        #    persistence but report backpressure so the caller can throttle.
        dropped = len(self._queue) >= BUS_QUEUE_MAX
        self._queue.append(event)

        # 4. Persist append-only (idempotent on event_id).
        utc_ns = self._utc_clock()
        try:
            inserted = self._persistence.append_event(event, utc_unix_ns=utc_ns)
        except PerceptionPersistenceError as exc:
            return IngressOutcome(
                event_id=event.event_id,
                kind=event.kind,
                accepted=False,
                deduplicated=False,
                dropped_backpressure=dropped,
                mapping=None,
                applied_dim_ids=(),
                rejected_dim_ids=(),
                skipped_dim_ids=(),
                error_code=exc.code,
                error_detail=exc.detail,
            )
        if not inserted:
            # Persistence already had this event_id (e.g. restart race).
            self._seen_event_ids.add(event.event_id)
            return IngressOutcome(
                event_id=event.event_id,
                kind=event.kind,
                accepted=False,
                deduplicated=True,
                dropped_backpressure=dropped,
                mapping=None,
                applied_dim_ids=(),
                rejected_dim_ids=(),
                skipped_dim_ids=(),
                error_code=None,
                error_detail=None,
            )
        self._seen_event_ids.add(event.event_id)

        # 5. Notify subscribers (isolated).
        self._notify_subscribers(event)

        # 6. Map + apply.
        return self._apply_event(event, dropped_backpressure=dropped)

    def _notify_subscribers(self, event: PerceptionEvent) -> None:
        for callback in tuple(self._subscribers):
            try:
                callback(event)
            except Exception as exc:  # noqa: BLE001 - isolate subscriber
                self.subscriber_errors.append(
                    (type(exc).__name__, str(exc))
                )

    def _apply_event(self, event: PerceptionEvent, *, dropped_backpressure: bool) -> IngressOutcome:
        try:
            registry = self._runtime.registry_proxy()
            dim_ids = registry.dim_ids
        except FieldRuntimeError as exc:
            return IngressOutcome(
                event_id=event.event_id,
                kind=event.kind,
                accepted=True,
                deduplicated=False,
                dropped_backpressure=dropped_backpressure,
                mapping=None,
                applied_dim_ids=(),
                rejected_dim_ids=(),
                skipped_dim_ids=(),
                error_code=exc.code,
                error_detail=exc.detail,
            )

        result = map_event(event, registry_dim_ids=dim_ids)
        utc_ns = self._utc_clock()
        rationale = f"perception:{event.kind}:intensity={result.intensity:.4f}"
        moves = tuple(
            AttractorMove(
                dim_id=target.dim_id,
                delta=float(target.delta),
                source=PERCEPTION_SOURCE,
                rationale=rationale,
            )
            for target in result.targets
        )
        try:
            receipt = self._runtime.move_attractor_batch(
                f"perception-event:{event.event_id}", moves
            )
        except (FieldRuntimeError, ValueError) as exc:
            return IngressOutcome(
                event_id=event.event_id,
                kind=event.kind,
                accepted=True,
                deduplicated=False,
                dropped_backpressure=dropped_backpressure,
                mapping=result,
                applied_dim_ids=(),
                rejected_dim_ids=(),
                skipped_dim_ids=result.skipped,
                error_code=getattr(exc, "code", "field_batch_rejected"),
                error_detail=getattr(exc, "detail", str(exc)),
            )
        applied = tuple(item.dim_id for item in receipt.results if item.applied)
        rejected = tuple(item.dim_id for item in receipt.results if not item.applied)

        # The field receipt is the durable commit authority.  Consumption is a
        # separately retryable cross-database completion marker.
        try:
            self._persistence.record_consumption(event.event_id, utc_unix_ns=utc_ns)
        except PerceptionPersistenceError as exc:
            return IngressOutcome(
                event_id=event.event_id,
                kind=event.kind,
                accepted=True,
                deduplicated=False,
                dropped_backpressure=dropped_backpressure,
                mapping=result,
                applied_dim_ids=applied,
                rejected_dim_ids=rejected,
                skipped_dim_ids=result.skipped,
                error_code=exc.code,
                error_detail=exc.detail,
                field_application_deduplicated=receipt.deduplicated,
                consumption_recorded=False,
            )

        return IngressOutcome(
            event_id=event.event_id,
            kind=event.kind,
            accepted=True,
            deduplicated=False,
            dropped_backpressure=dropped_backpressure,
            mapping=result,
            applied_dim_ids=applied,
            rejected_dim_ids=rejected,
            skipped_dim_ids=result.skipped,
            error_code=None,
            error_detail=None,
            field_application_deduplicated=receipt.deduplicated,
            consumption_recorded=True,
        )

    def replay_unconsumed(self, *, limit: int = REPLAY_BATCH_LIMIT) -> tuple[IngressOutcome, ...]:
        """Replay persisted-but-unconsumed events in observed_at order.

        Used on startup to re-apply any events that were persisted but whose
        consumption was not recorded before the previous shutdown.  Each
        replayed event is re-mapped and re-applied; consumption is recorded
        again.
        """
        try:
            unconsumed = self._persistence.read_unconsumed(limit=limit)
        except PerceptionPersistenceError as exc:
            raise PerceptionBusError(exc.code, exc.detail) from exc
        outcomes: list[IngressOutcome] = []
        for persisted in unconsumed:
            import json as _json
            try:
                payload = _json.loads(persisted.payload_json)
            except ValueError as exc:
                raise PerceptionBusError(
                    "persisted_event_payload_invalid",
                    f"event {persisted.event_id!r} payload is not valid JSON: {exc}",
                ) from exc
            envelope = {
                "version": "aphrodite.chatbox.perception-event/1",
                "event_id": persisted.event_id,
                "session_id": persisted.session_id,
                "kind": persisted.kind,
                "observed_at": persisted.observed_at,
                "payload": payload,
                "source": persisted.source,
            }
            try:
                event = validate_event(envelope)
            except PerceptionSchemaError as exc:
                raise PerceptionBusError(
                    "persisted_event_invalid",
                    f"event {persisted.event_id!r} failed validation: {exc.code}: {exc.detail}",
                ) from exc
            # Mark as seen so ingest() does not try to re-persist.
            self._seen_event_ids.add(event.event_id)
            outcomes.append(self._apply_event(event, dropped_backpressure=False))
        return tuple(outcomes)

    def drain(self) -> tuple[PerceptionEvent, ...]:
        """Drain and return the current in-process queue (test/inspection helper)."""
        drained = tuple(self._queue)
        self._queue.clear()
        return drained
