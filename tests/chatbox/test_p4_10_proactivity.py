"""P4 task-card 10 focused tests: tick-driven emergent proactivity.

All tests are offline and use temp SQLite + fake providers.  No real provider,
user production DB, Owner blind test, two-hour silence, 48h soak, or P4 human
gate is run here.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import threading
from pathlib import Path

import pytest

from app.chatbox.dialogue_persistence import DialoguePersistenceStore
from app.chatbox.dialogue_protocol import DIALOGUE_PROTOCOL_VERSION, proactive_message
from app.chatbox.field_dynamics import DimensionRegistration, FieldSnapshot, SeededGaussianRngFactory
from app.chatbox.field_runtime import FieldRuntime, RegistryProxy
from app.chatbox.proactive_coordinator import ProactiveCoordinator
from app.chatbox.proactive_pressure import (
    DEFAULT_DECAY_LAMBDA,
    DEFAULT_DRIVE_GAIN,
    DEFAULT_SILENCE_TAU,
    DEFAULT_THRESHOLD,
    PressureConfig,
    PressureState,
    initial_pressure_state,
    step_pressure,
)
from app.chatbox.proactive_store import (
    CapConfig,
    LocalTime,
    ProactiveStore,
    MAX_DAILY_LIMIT_FLOOR,
    MIN_INTERVAL_SECONDS_FLOOR,
)


# -- helpers ---------------------------------------------------------------


def _reg(dim_id: str, bias: float = 0.0) -> DimensionRegistration:
    return DimensionRegistration(
        dim_id=dim_id,
        temporary_name=f"temp-{dim_id}",
        birth_time=1.0,
        strength=1.0,
        trigger_count=0,
        birth_bias=bias,
        fast_e_fold_s=600.0,
        ou_correlation_e_fold_s=10_800.0,
        ou_acceleration_sigma=4e-7,
        soft_boundary_start=1.0,
        soft_boundary_width=0.25,
        soft_boundary_strength=(1.0 / 120.0) ** 2,
    )


def _registry_proxy(dim_ids: tuple[str, ...]) -> RegistryProxy:
    return RegistryProxy(registrations=tuple(_reg(d) for d in dim_ids))


def _snapshot(dim_ids: tuple[str, ...], values: dict[str, float], tick: int = 1) -> FieldSnapshot:
    from app.chatbox.field_dynamics import DimensionSnapshot
    return FieldSnapshot(
        tick=tick,
        dimensions=tuple(
            DimensionSnapshot(
                registration=_reg(d),
                value=float(values.get(d, 0.0)),
                velocity=0.0,
                attractor=float(values.get(d, 0.0)),
                soft_restoring_baseline=float(values.get(d, 0.0)),
                ou_acceleration=0.0,
            )
            for d in dim_ids
        ),
    )


def _ns(seconds: float) -> int:
    return int(seconds * 1_000_000_000)


def _resolver(local: LocalTime):
    def resolve(ns: int) -> LocalTime | None:
        return local
    return resolve


# -- pressure formula ------------------------------------------------------


class TestPressureFormula:
    def test_first_observation_anchors_without_integration(self) -> None:
        cfg = PressureConfig()
        state = initial_pressure_state()
        reg = _registry_proxy(("birth_03", "birth_09"))
        snap = _snapshot(("birth_03", "birth_09"), {"birth_03": 1.0, "birth_09": 1.0})
        result = step_pressure(
            state, cfg, reg, snap,
            field_tick=1, last_user_activity_ns=0, current_ns=_ns(7200),
        )
        assert result.driven is False
        assert result.at_threshold is False
        assert result.new_state.last_field_tick == 1
        assert result.new_state.pressure == 0.0

    def test_constant_drive_exact_step_matches_hand_computation(self) -> None:
        cfg = PressureConfig()
        state = PressureState(pressure=0.0, last_field_tick=1)
        reg = _registry_proxy(("birth_03", "birth_09"))
        # toward=expect=1 -> 0.5*(tanh(1)+1) ~ 0.8808; silence=7200 ->
        # 1-exp(-1)=0.6321; g = (1/1800)*0.8808^2*0.6321
        snap = _snapshot(("birth_03", "birth_09"), {"birth_03": 1.0, "birth_09": 1.0})
        result = step_pressure(
            state, cfg, reg, snap,
            field_tick=2, last_user_activity_ns=0, current_ns=_ns(7200),
        )
        assert result.driven is True
        toward = 0.5 * (math.tanh(1.0) + 1.0)
        expect = toward
        silence_term = 1.0 - math.exp(-7200.0 / DEFAULT_SILENCE_TAU)
        g = DEFAULT_DRIVE_GAIN * toward * expect * silence_term
        steady = g / DEFAULT_DECAY_LAMBDA
        expected = steady + (0.0 - steady) * math.exp(-DEFAULT_DECAY_LAMBDA * 1.0)
        assert math.isclose(result.pressure, expected, rel_tol=1e-12)
        assert result.drive_g == pytest.approx(g, rel=1e-12)

    def test_threshold_cross_and_admission_reset_to_zero(self) -> None:
        cfg = PressureConfig()
        # Pre-charge pressure just below threshold, then drive one tick that
        # crosses it.  Use a large toward/expect + long silence so g is big.
        state = PressureState(pressure=0.9999, last_field_tick=1)
        reg = _registry_proxy(("birth_03", "birth_09"))
        snap = _snapshot(("birth_03", "birth_09"), {"birth_03": 10.0, "birth_09": 10.0})
        result = step_pressure(
            state, cfg, reg, snap,
            field_tick=2, last_user_activity_ns=0, current_ns=_ns(7200),
        )
        assert result.at_threshold is True
        assert result.pressure >= DEFAULT_THRESHOLD

    def test_cap_deny_preserves_pressure(self, tmp_path: Path) -> None:
        store = ProactiveStore(
            str(tmp_path / "pro.sqlite3"),
            cap=CapConfig(),
            local_time_resolver=_resolver(LocalTime(2026, 7, 20, 12, 0, 0)),
        )
        # First admission at noon succeeds and resets pressure.
        d1 = store.try_admit(current_ns=_ns(0), pressure=1.5)
        assert d1.admitted is True
        assert d1.pressure_after == 0.0
        # Second admission 1 second later: min_interval denies, pressure preserved.
        d2 = store.try_admit(current_ns=_ns(1), pressure=1.2)
        assert d2.admitted is False
        assert d2.reject_reason == "min_interval"
        assert d2.pressure_after == 1.2
        store.close()

    def test_reproducible_identical_inputs(self) -> None:
        cfg = PressureConfig()
        reg = _registry_proxy(("birth_03", "birth_09"))
        snap = _snapshot(("birth_03", "birth_09"), {"birth_03": 0.7, "birth_09": 0.3})
        results = []
        for _ in range(2):
            state = PressureState(pressure=0.0, last_field_tick=1)
            r = step_pressure(
                state, cfg, reg, snap,
                field_tick=2, last_user_activity_ns=0, current_ns=_ns(3600),
            )
            results.append(r)
        assert results[0].pressure == results[1].pressure
        assert results[0].at_threshold == results[1].at_threshold
        assert results[0].drive_g == results[1].drive_g

    def test_no_rng_in_pressure_module(self) -> None:
        import app.chatbox.proactive_pressure as mod
        src = open(mod.__file__, encoding="utf-8").read()
        assert "import random" not in src
        assert "from random" not in src
        assert "randint" not in src
        assert "random()" not in src


# -- registry-driven validation -------------------------------------------


class TestRegistryDriven:
    def test_missing_toward_dim_fail_closed(self) -> None:
        cfg = PressureConfig()
        state = PressureState(pressure=0.0, last_field_tick=1)
        reg = _registry_proxy(("birth_09", "other"))
        snap = _snapshot(("birth_09", "other"), {"birth_09": 1.0, "other": 1.0})
        result = step_pressure(
            state, cfg, reg, snap,
            field_tick=2, last_user_activity_ns=0, current_ns=_ns(7200),
        )
        assert result.driven is False
        assert result.at_threshold is False
        assert result.reject_reason == "drive_validation_failed"

    def test_missing_expect_dim_fail_closed(self) -> None:
        cfg = PressureConfig()
        state = PressureState(pressure=0.0, last_field_tick=1)
        reg = _registry_proxy(("birth_03", "other"))
        snap = _snapshot(("birth_03", "other"), {"birth_03": 1.0, "other": 1.0})
        result = step_pressure(
            state, cfg, reg, snap,
            field_tick=2, last_user_activity_ns=0, current_ns=_ns(7200),
        )
        assert result.driven is False
        assert result.reject_reason == "drive_validation_failed"

    def test_misaligned_snapshot_fail_closed(self) -> None:
        cfg = PressureConfig()
        state = PressureState(pressure=0.0, last_field_tick=1)
        reg = _registry_proxy(("birth_03", "birth_09"))
        # snapshot dims in different order than registry
        snap = _snapshot(("birth_09", "birth_03"), {"birth_03": 1.0, "birth_09": 1.0})
        result = step_pressure(
            state, cfg, reg, snap,
            field_tick=2, last_user_activity_ns=0, current_ns=_ns(7200),
        )
        assert result.driven is False
        assert result.reject_reason == "drive_validation_failed"

    def test_nan_value_fail_closed(self) -> None:
        cfg = PressureConfig()
        state = PressureState(pressure=0.0, last_field_tick=1)
        reg = _registry_proxy(("birth_03", "birth_09"))
        snap = _snapshot(("birth_03", "birth_09"), {"birth_03": float("nan"), "birth_09": 1.0})
        result = step_pressure(
            state, cfg, reg, snap,
            field_tick=2, last_user_activity_ns=0, current_ns=_ns(7200),
        )
        assert result.driven is False
        assert result.reject_reason == "drive_validation_failed"

    def test_dim_id_lookup_not_ordinal(self) -> None:
        # 17 dims in shuffled order; toward/expect must be found by id.
        ids = [f"birth_{i:02d}" for i in range(17)]
        cfg = PressureConfig()
        state = PressureState(pressure=0.0, last_field_tick=1)
        reg = _registry_proxy(tuple(ids))
        values = {d: 0.0 for d in ids}
        values["birth_03"] = 2.0
        values["birth_09"] = 2.0
        snap = _snapshot(tuple(ids), values)
        result = step_pressure(
            state, cfg, reg, snap,
            field_tick=2, last_user_activity_ns=0, current_ns=_ns(7200),
        )
        assert result.driven is True
        assert result.drive_g is not None and result.drive_g > 0.0


# -- tick ordering ---------------------------------------------------------


class TestTickOrdering:
    def test_duplicate_tick_no_change(self) -> None:
        cfg = PressureConfig()
        state = PressureState(pressure=0.5, last_field_tick=5)
        reg = _registry_proxy(("birth_03", "birth_09"))
        snap = _snapshot(("birth_03", "birth_09"), {"birth_03": 1.0, "birth_09": 1.0})
        result = step_pressure(
            state, cfg, reg, snap,
            field_tick=5, last_user_activity_ns=0, current_ns=_ns(7200),
        )
        assert result.driven is False
        assert result.reject_reason == "duplicate_tick"
        assert result.new_state.pressure == 0.5

    def test_out_of_order_tick_no_change(self) -> None:
        cfg = PressureConfig()
        state = PressureState(pressure=0.5, last_field_tick=5)
        reg = _registry_proxy(("birth_03", "birth_09"))
        snap = _snapshot(("birth_03", "birth_09"), {"birth_03": 1.0, "birth_09": 1.0})
        result = step_pressure(
            state, cfg, reg, snap,
            field_tick=4, last_user_activity_ns=0, current_ns=_ns(7200),
        )
        assert result.reject_reason == "out_of_order_tick"
        assert result.new_state.pressure == 0.5

    def test_tick_gap_reanchors_without_backfill(self) -> None:
        cfg = PressureConfig()
        state = PressureState(pressure=0.5, last_field_tick=5)
        reg = _registry_proxy(("birth_03", "birth_09"))
        snap = _snapshot(("birth_03", "birth_09"), {"birth_03": 1.0, "birth_09": 1.0})
        result = step_pressure(
            state, cfg, reg, snap,
            field_tick=8, last_user_activity_ns=0, current_ns=_ns(7200),
        )
        assert result.driven is False
        assert result.reject_reason == "tick_gap_reanchored"
        assert result.new_state.last_field_tick == 8
        assert result.new_state.pressure == 0.5

    def test_clock_rollback_fail_closed(self) -> None:
        cfg = PressureConfig()
        state = PressureState(pressure=0.5, last_field_tick=5)
        reg = _registry_proxy(("birth_03", "birth_09"))
        snap = _snapshot(("birth_03", "birth_09"), {"birth_03": 1.0, "birth_09": 1.0})
        result = step_pressure(
            state, cfg, reg, snap,
            field_tick=6, last_user_activity_ns=_ns(7200), current_ns=_ns(3600),
        )
        assert result.driven is False
        assert result.reject_reason == "clock_rollback"


# -- hard cap --------------------------------------------------------------


class TestHardCap:
    def test_curfew_01_to_09_denied(self, tmp_path: Path) -> None:
        store = ProactiveStore(
            str(tmp_path / "p.sqlite3"),
            local_time_resolver=_resolver(LocalTime(2026, 7, 20, 1, 0, 0)),
        )
        d = store.try_admit(current_ns=_ns(0), pressure=1.5)
        assert d.admitted is False
        assert d.reject_reason == "curfew"
        store.close()

    def test_curfew_0859_denied_0900_allowed(self, tmp_path: Path) -> None:
        store = ProactiveStore(str(tmp_path / "p.sqlite3"))
        # Inject resolver returning 08:59:59 -> curfew
        store._resolver = _resolver(LocalTime(2026, 7, 20, 8, 59, 59))
        d1 = store.try_admit(current_ns=_ns(0), pressure=1.5)
        assert d1.reject_reason == "curfew"
        store._resolver = _resolver(LocalTime(2026, 7, 20, 9, 0, 0))
        d2 = store.try_admit(current_ns=_ns(0), pressure=1.5)
        assert d2.admitted is True
        store.close()

    def test_daily_limit_two_then_third_denied(self, tmp_path: Path) -> None:
        store = ProactiveStore(
            str(tmp_path / "p.sqlite3"),
            local_time_resolver=_resolver(LocalTime(2026, 7, 20, 12, 0, 0)),
        )
        # Two admissions 7h apart (>= min interval 6h).
        d1 = store.try_admit(current_ns=_ns(0), pressure=1.5)
        d2 = store.try_admit(current_ns=_ns(7 * 3600), pressure=1.5)
        assert d1.admitted and d2.admitted
        d3 = store.try_admit(current_ns=_ns(14 * 3600), pressure=1.5)
        assert d3.admitted is False
        assert d3.reject_reason == "daily_limit"
        store.close()

    def test_min_interval_exact_6h_allowed(self, tmp_path: Path) -> None:
        store = ProactiveStore(
            str(tmp_path / "p.sqlite3"),
            local_time_resolver=_resolver(LocalTime(2026, 7, 20, 12, 0, 0)),
        )
        d1 = store.try_admit(current_ns=_ns(0), pressure=1.5)
        assert d1.admitted
        # Exactly 6h later -> allowed (>= min_interval).
        d2 = store.try_admit(current_ns=_ns(6 * 3600), pressure=1.5)
        assert d2.admitted
        store.close()

    def test_min_interval_one_ns_before_denied(self, tmp_path: Path) -> None:
        store = ProactiveStore(
            str(tmp_path / "p.sqlite3"),
            local_time_resolver=_resolver(LocalTime(2026, 7, 20, 12, 0, 0)),
        )
        d1 = store.try_admit(current_ns=_ns(0), pressure=1.5)
        assert d1.admitted
        d2 = store.try_admit(current_ns=_ns(6 * 3600) - 1, pressure=1.5)
        assert d2.admitted is False
        assert d2.reject_reason == "min_interval"
        store.close()

    def test_clock_unresolved_denied(self, tmp_path: Path) -> None:
        store = ProactiveStore(str(tmp_path / "p.sqlite3"), local_time_resolver=lambda ns: None)
        d = store.try_admit(current_ns=_ns(0), pressure=1.5)
        assert d.admitted is False
        assert d.reject_reason == "clock_unresolved"
        store.close()

    def test_capconfig_rejects_looser_than_floor(self) -> None:
        with pytest.raises(ValueError):
            CapConfig(daily_limit=3)
        with pytest.raises(ValueError):
            CapConfig(min_interval_seconds=3600)
        with pytest.raises(ValueError):
            CapConfig(curfew_start_hour=2)  # narrower curfew
        with pytest.raises(ValueError):
            CapConfig(curfew_end_hour=8)  # narrower curfew

    def test_capconfig_allows_stricter(self) -> None:
        CapConfig(daily_limit=0)
        CapConfig(daily_limit=1)
        CapConfig(min_interval_seconds=10 * 3600)
        CapConfig(curfew_start_hour=0, curfew_end_hour=10)  # wider curfew

    def test_concurrent_admissions_at_most_one(self, tmp_path: Path) -> None:
        store = ProactiveStore(
            str(tmp_path / "p.sqlite3"),
            local_time_resolver=_resolver(LocalTime(2026, 7, 20, 12, 0, 0)),
        )
        results: list = []
        lock = threading.Lock()

        def attempt() -> None:
            d = store.try_admit(current_ns=_ns(0), pressure=1.5)
            with lock:
                results.append(d)

        threads = [threading.Thread(target=attempt) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        admitted = sum(1 for r in results if r.admitted)
        assert admitted == 1
        store.close()

    def test_restart_preserves_pressure_and_admissions(self, tmp_path: Path) -> None:
        path = str(tmp_path / "p.sqlite3")
        store = ProactiveStore(
            path,
            local_time_resolver=_resolver(LocalTime(2026, 7, 20, 12, 0, 0)),
        )
        store.save_pressure_state(PressureState(pressure=0.42, last_field_tick=17), updated_ns=_ns(100))
        d = store.try_admit(current_ns=_ns(200), pressure=0.42)
        assert d.admitted
        store.close()
        # Reopen
        store2 = ProactiveStore(
            path,
            local_time_resolver=_resolver(LocalTime(2026, 7, 20, 12, 0, 0)),
        )
        state = store2.load_pressure_state()
        assert state.pressure == 0.0  # reset by admission
        # The admission counted; a new attempt within interval is denied.
        d2 = store2.try_admit(current_ns=_ns(201), pressure=0.5)
        assert d2.reject_reason == "min_interval"
        store2.close()


# -- coordinator -----------------------------------------------------------


class _FakeOutput:
    def __init__(self, ready: bool = True, emit_ok: bool = True, activity_ns: int = 0) -> None:
        self._ready = ready
        self._emit_ok = emit_ok
        self.last_user_activity_ns = activity_ns
        self.emit_calls = 0

    def proactive_ready(self) -> bool:
        return self._ready

    async def emit_proactive(self, admission_id: str) -> bool:
        self.emit_calls += 1
        return self._emit_ok


class TestCoordinator:
    def test_no_admission_when_not_ready(self, tmp_path: Path) -> None:
        store = ProactiveStore(
            str(tmp_path / "p.sqlite3"),
            local_time_resolver=_resolver(LocalTime(2026, 7, 20, 12, 0, 0)),
        )
        out = _FakeOutput(ready=False)
        coord = ProactiveCoordinator(
            store,
            output=out,
            utc_clock=lambda: _ns(7200),
            runtime_registry_proxy=lambda: _registry_proxy(("birth_03", "birth_09")),
            runtime_snapshot_proxy=lambda: _snapshot(("birth_03", "birth_09"), {"birth_03": 5.0, "birth_09": 5.0}),
            field_tick_proxy=lambda: 1,
        )
        # Pre-charge above threshold; ready=False so no admission.
        coord._state = PressureState(pressure=2.0, last_field_tick=1)
        coord.on_committed_tick()
        assert coord.stats.admissions_attempted == 0
        assert coord.stats.admissions_admitted == 0
        store.close()

    def test_at_most_one_in_flight_emission(self, tmp_path: Path) -> None:
        store = ProactiveStore(
            str(tmp_path / "p.sqlite3"),
            local_time_resolver=_resolver(LocalTime(2026, 7, 20, 12, 0, 0)),
        )

        class SlowOutput(_FakeOutput):
            def __init__(self) -> None:
                super().__init__(ready=True, emit_ok=True)
                self.gate = asyncio.Event()

            async def emit_proactive(self, admission_id: str) -> bool:
                self.emit_calls += 1
                await self.gate.wait()
                return True

        out = SlowOutput()
        coord = ProactiveCoordinator(
            store,
            output=out,
            utc_clock=lambda: _ns(7200),
            runtime_registry_proxy=lambda: _registry_proxy(("birth_03", "birth_09")),
            runtime_snapshot_proxy=lambda: _snapshot(("birth_03", "birth_09"), {"birth_03": 5.0, "birth_09": 5.0}),
            field_tick_proxy=lambda: 1,
        )

        async def run() -> None:
            coord._state = PressureState(pressure=2.0, last_field_tick=1)
            coord._field_tick_proxy = lambda: 2
            coord.on_committed_tick()
            assert coord.has_in_flight_emission is True
            # Second tick while emission in flight: no new admission.
            coord._state = PressureState(pressure=2.0, last_field_tick=2)
            coord._field_tick_proxy = lambda: 3
            coord.on_committed_tick()
            assert coord.stats.admissions_admitted == 1
            out.gate.set()
            await asyncio.sleep(0.01)
            await coord.stop()

        asyncio.run(run())
        store.close()


# -- protocol --------------------------------------------------------------


class TestProtocol:
    def test_proactive_message_strict_fields(self) -> None:
        msg = proactive_message(
            proactive_id="proactive:1",
            segment_index=0,
            segment_count=1,
            text="hi",
            utc_unix_ns=17,
        )
        assert msg["type"] == "proactive.stream"
        assert msg["proactive_id"] == "proactive:1"

    def test_proactive_message_rejects_empty_text(self) -> None:
        with pytest.raises(ValueError):
            proactive_message(
                proactive_id="p:1", segment_index=0, segment_count=1, text="", utc_unix_ns=17,
            )

    def test_proactive_message_rejects_out_of_range_segment(self) -> None:
        with pytest.raises(ValueError):
            proactive_message(
                proactive_id="p:1", segment_index=2, segment_count=2, text="x", utc_unix_ns=17,
            )

    def test_proactive_message_rejects_bad_id(self) -> None:
        with pytest.raises(ValueError):
            proactive_message(
                proactive_id="bad id!", segment_index=0, segment_count=1, text="x", utc_unix_ns=17,
            )


# -- dialogue persistence latest user ns -----------------------------------


class TestDialoguePersistenceLatestUser:
    def test_empty_store_returns_none(self, tmp_path: Path) -> None:
        store = DialoguePersistenceStore(str(tmp_path / "d.sqlite3"))
        assert store.latest_user_message_ns() is None
        store.close()

    def test_returns_latest_user_ns(self, tmp_path: Path) -> None:
        store = DialoguePersistenceStore(str(tmp_path / "d.sqlite3"))
        store.append_message(client_turn_id="t1", role="user", segment_index=0, content="hi", utc_unix_ns=100)
        store.append_message(client_turn_id="t1", role="assistant", segment_index=0, content="hey", utc_unix_ns=200)
        store.append_message(client_turn_id="t2", role="user", segment_index=0, content="bye", utc_unix_ns=300)
        assert store.latest_user_message_ns() == 300
        store.close()


# -- isolation / no-timer source inspection --------------------------------


class TestIsolation:
    def test_no_quarantine_imports_in_proactive_modules(self) -> None:
        import app.chatbox.proactive_pressure as pressure
        import app.chatbox.proactive_store as store
        import app.chatbox.proactive_coordinator as coord
        for mod in (pressure, store, coord):
            src = open(mod.__file__, encoding="utf-8").read()
            assert "agentlib" not in src
            assert "agent_kernel" not in src
            assert "semantic_trigger" not in src
            assert "demos.scenarios" not in src

    def test_no_independent_timer_in_coordinator(self) -> None:
        import app.chatbox.proactive_coordinator as coord
        src = open(coord.__file__, encoding="utf-8").read()
        assert "asyncio.sleep" not in src
        assert "call_later" not in src
        assert "Timer" not in src
        # The only asyncio.create_task is the emission task, not a timer.
        assert src.count("asyncio.create_task") == 1

    def test_writer_only_moves_attractor_in_emit_path(self) -> None:
        # Source inspection: DialogueService.emit_proactive must call
        # writer.apply and never write state directly.
        import app.chatbox.dialogue_service as ds
        src = open(ds.__file__, encoding="utf-8").read()
        emit_section = src[src.index("async def emit_proactive"):]
        assert "self.writer.apply" in emit_section
        assert "move_attractor" not in emit_section  # writer handles that
        assert "self.runtime._dynamics" not in emit_section
