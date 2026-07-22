"""P4 task-card 10: committed-tick observer that drives one proactive emission.

The [`ProactiveCoordinator`](proactive_coordinator.py) is the field-adjacent
owner that wires the pure pressure accumulator, the atomic cap admission
store, and the production proactive output boundary on
[`DialogueService`](dialogue_service.py).  It is driven *only* by committed
field ticks from [`TrajectoryHub._tick_loop()`](trajectory_service.py); it
contains no independent sleep/timer/interval/schedule.

Per committed tick:

1. read the current registry/snapshot from the runtime proxies;
2. step the pressure accumulator (pure, deterministic);
3. persist the new pressure state (constant SQLite work);
4. if the pressure is at/above threshold *and* the dialogue service reports
   ready (provider available + at least one open output socket), attempt an
   atomic admission via the store;
5. on a successful admission, spawn *one* async emission task that reuses the
   dialogue service's proactive entry (provider + writer + persistence + WS),
   then record the send outcome audit.  No retry on failure.

The coordinator never blocks the 1 Hz tick loop on the provider: the emission
runs as a background task, and at most one emission task exists at a time.
Any proactive-side error is fail-closed: it is swallowed so the field ticker
never produces runaway output because of proactive functionality.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Protocol

from app.chatbox.proactive_pressure import (
    PressureConfig,
    PressureState,
    step_pressure,
)
from app.chatbox.proactive_store import AdmissionDecision, ProactiveStore


class _ProactiveOutput(Protocol):
    """Minimal interface the coordinator needs from DialogueService."""

    @property
    def last_user_activity_ns(self) -> int | None: ...

    def proactive_ready(self) -> bool: ...
    async def emit_proactive(self, admission_id: str) -> bool: ...


@dataclass(slots=True)
class CoordinatorStats:
    """Observable counters for tests/diagnostics.  Not persisted."""

    ticks_observed: int = 0
    pressure_steps_driven: int = 0
    admissions_attempted: int = 0
    admissions_admitted: int = 0
    admissions_denied: int = 0
    emissions_started: int = 0
    emissions_succeeded: int = 0
    emissions_failed: int = 0


class ProactiveCoordinator:
    """Committed-tick observer + at-most-one async emission owner."""

    def __init__(
        self,
        store: ProactiveStore,
        *,
        output: _ProactiveOutput,
        pressure_config: PressureConfig | None = None,
        utc_clock: Callable[[], int],
        runtime_registry_proxy: Callable[[], object],
        runtime_snapshot_proxy: Callable[[], object],
        field_tick_proxy: Callable[[], int],
    ) -> None:
        self._store = store
        self._output = output
        self._pressure_config = pressure_config if pressure_config is not None else PressureConfig()
        self._utc_clock = utc_clock
        self._registry_proxy = runtime_registry_proxy
        self._snapshot_proxy = runtime_snapshot_proxy
        self._field_tick_proxy = field_tick_proxy
        self._state: PressureState = store.load_pressure_state()
        self._emission_task: asyncio.Task[None] | None = None
        self._stopping = False
        self.stats = CoordinatorStats()

    # -- observation -----------------------------------------------------

    def on_committed_tick(self) -> None:
        """Synchronous hook called by the trajectory ticker after a commit.

        Constant SQLite work + O(n) registry scan.  Never raises: any
        proactive-side failure is fail-closed and swallowed so the field
        ticker stays healthy.
        """
        if self._stopping:
            return
        self.stats.ticks_observed += 1
        try:
            registry = self._registry_proxy()
            snapshot = self._snapshot_proxy()
            field_tick = self._field_tick_proxy()
            current_ns = self._utc_clock()
            # The dialogue service owns the trusted last-user-activity ns; we
            # read it through the output boundary so the coordinator never
            # touches dialogue persistence directly. Unknown/invalid activity
            # stays invalid and is rejected by the pure step; never substitute
            # the current clock and pretend the silence baseline is known.
            last_activity_ns = self._output.last_user_activity_ns
            result = step_pressure(
                self._state,
                self._pressure_config,
                registry,
                snapshot,
                field_tick=field_tick,
                last_user_activity_ns=last_activity_ns,
                current_ns=current_ns,
            )
            self._state = result.new_state
            try:
                self._store.save_pressure_state(self._state, updated_ns=current_ns)
            except Exception:
                # Persistence failure: keep the in-memory state but do not
                # trigger; the next tick re-attempts persistence.
                return
            if result.driven:
                self.stats.pressure_steps_driven += 1
            if not result.at_threshold:
                return
            if not self._output.proactive_ready():
                return
            if self._emission_task is not None and not self._emission_task.done():
                # An emission is already in flight; do not start a second one.
                return
            self.stats.admissions_attempted += 1
            decision = self._store.try_admit(
                current_ns=current_ns,
                pressure=result.pressure,
            )
            if not decision.admitted:
                self.stats.admissions_denied += 1
                # Cap rejection preserves pressure (the store did not reset).
                # Reload the persisted state so the in-memory view matches.
                try:
                    self._state = self._store.load_pressure_state()
                except Exception:
                    pass
                return
            self.stats.admissions_admitted += 1
            # Admission succeeded: pressure was reset to 0 atomically while
            # preserving the committed field-tick cursor.
            self._state = PressureState(pressure=0.0, last_field_tick=result.new_state.last_field_tick)
            self.stats.emissions_started += 1
            self._emission_task = asyncio.create_task(
                self._run_emission(decision),
                name=f"proactive-emission:{decision.admission_id}",
            )
        except Exception:
            # Fail-closed: never let proactive logic poison the field ticker.
            return

    async def _run_emission(self, decision: AdmissionDecision) -> None:
        try:
            success = await self._output.emit_proactive(decision.admission_id or "")
            ns = self._utc_clock()
            if success:
                self.stats.emissions_succeeded += 1
                self._store.record_outcome(
                    admission_id=decision.admission_id or "",
                    outcome="send_succeeded",
                    detail={"ok": True},
                    ns=ns,
                )
            else:
                self.stats.emissions_failed += 1
                self._store.record_outcome(
                    admission_id=decision.admission_id or "",
                    outcome="send_failed",
                    detail={"ok": False},
                    ns=ns,
                )
        except asyncio.CancelledError:
            # Shutdown cancellation: record a failed outcome (admission still
            # counts toward the cap; no retry).
            try:
                ns = self._utc_clock()
                self._store.record_outcome(
                    admission_id=decision.admission_id or "",
                    outcome="send_failed",
                    detail={"ok": False, "reason": "cancelled"},
                    ns=ns,
                )
            except Exception:
                pass
            raise
        except Exception:
            self.stats.emissions_failed += 1
            try:
                ns = self._utc_clock()
                self._store.record_outcome(
                    admission_id=decision.admission_id or "",
                    outcome="send_failed",
                    detail={"ok": False, "reason": "exception"},
                    ns=ns,
                )
            except Exception:
                pass

    # -- lifecycle -------------------------------------------------------

    async def stop(self) -> None:
        self._stopping = True
        task = self._emission_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._emission_task = None

    @property
    def pressure_state(self) -> PressureState:
        return self._state

    @property
    def has_in_flight_emission(self) -> bool:
        return self._emission_task is not None and not self._emission_task.done()
