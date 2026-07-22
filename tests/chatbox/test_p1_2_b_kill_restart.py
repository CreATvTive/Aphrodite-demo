"""Real-subprocess kill-restart evidence for P1.2-B.

Launches ``_p1_2_process_harness.py`` with the current interpreter (``-u``),
lets it commit several ticks, hard-kills it (``taskkill /F /T`` on Windows,
``SIGKILL`` elsewhere) without calling close, then starts a second process on
the same DB and asserts startup success, registry identity, recovered tick
within the 60-second loss bound, and append-only preservation of the first
process's committed events and trajectory.  A second kill + cleanup follows.

Also verifies: first-segment event/trajectory rows are byte-identical after
the second kill (preserved across recovery), ready payload includes state
summary, and recovered state matches the stored capsule.

This is pre-check evidence only; the Owner's final manual run of this file is
the authoritative acceptance, not the executor's result.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time

import pytest


HARNESS = Path(__file__).with_name("_p1_2_process_harness.py")
SNAPSHOT_INTERVAL_SECONDS = 60.0

pytestmark = [pytest.mark.acceptance, pytest.mark.slow]


def _read_lines(proc: subprocess.Popen, deadline: float) -> list[dict]:
    lines: list[dict] = []
    assert proc.stdout is not None
    while time.time() < deadline:
        raw = proc.stdout.readline()
        if not raw:
            break
        raw = raw.strip()
        if not raw:
            continue
        try:
            lines.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
        if lines and lines[-1].get("type") == "tick":
            break
    return lines


def _hard_kill(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            check=False,
            capture_output=True,
        )
    else:
        try:
            os.kill(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass
    finally:
        # Close pipes to avoid zombie processes
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except Exception:
            pass
        try:
            if proc.stderr is not None:
                proc.stderr.close()
        except Exception:
            pass


def _db_counts(db_path: str) -> tuple[int, int, int]:
    conn = sqlite3.connect(db_path)
    try:
        snaps = int(conn.execute("SELECT COUNT(*) FROM field_snapshots").fetchone()[0])
        events = int(conn.execute("SELECT COUNT(*) FROM field_events").fetchone()[0])
        traj = int(conn.execute("SELECT COUNT(*) FROM trajectory_points").fetchone()[0])
    finally:
        conn.close()
    return snaps, events, traj


def _db_boot_ids(db_path: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT boot_id FROM field_events ORDER BY boot_id"
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def _db_max_event_id(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT MAX(event_id) FROM field_events").fetchone()
    finally:
        conn.close()
    return int(row[0]) if row[0] is not None else 0


def _db_max_trajectory_id(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT MAX(trajectory_id) FROM trajectory_points").fetchone()
    finally:
        conn.close()
    return int(row[0]) if row[0] is not None else 0


def _db_latest_snapshot_tick(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT field_tick FROM field_snapshots ORDER BY snapshot_id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return int(row[0]) if row and row[0] is not None else 0


def _db_event_rows(db_path: str) -> list[tuple]:
    """Return all event rows (id, boot_id, kind, before, after, utc, payload, sha256)."""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT event_id, boot_id, event_kind, before_field_tick, "
            "after_field_tick, utc_unix_ns, payload_json, payload_sha256 "
            "FROM field_events ORDER BY event_id"
        ).fetchall()
    finally:
        conn.close()


def _db_trajectory_rows(db_path: str) -> list[tuple]:
    """Return all trajectory rows (tid, eid, tick, ord, dim, val, vel, attr, base, ou)."""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT trajectory_id, event_id, field_tick, dimension_ordinal, "
            "dim_id, after_value, after_velocity, after_attractor, "
            "after_slow_baseline, after_ou_acceleration "
            "FROM trajectory_points ORDER BY trajectory_id"
        ).fetchall()
    finally:
        conn.close()


def _start_harness(db_path: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-u", str(HARNESS), db_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


def _wait_ready(proc: subprocess.Popen, timeout: float = 15.0) -> dict:
    assert proc.stdout is not None
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = proc.stdout.readline()
        if not raw:
            if proc.poll() is not None:
                err = ""
                if proc.stderr is not None:
                    err = proc.stderr.read()
                pytest.fail(f"harness exited before ready; stderr={err}")
            continue
        raw = raw.strip()
        if not raw:
            continue
        msg = json.loads(raw)
        if msg.get("type") == "ready":
            return msg
        if msg.get("type") == "startup_error":
            pytest.fail(f"harness startup error: {msg.get('detail')}")
    pytest.fail("harness did not emit ready in time")


def _collect_ticks(proc: subprocess.Popen, min_ticks: int, timeout: float) -> list[dict]:
    ticks: list[dict] = []
    assert proc.stdout is not None
    deadline = time.time() + timeout
    while time.time() < deadline and len(ticks) < min_ticks:
        raw = proc.stdout.readline()
        if not raw:
            break
        raw = raw.strip()
        if not raw:
            continue
        msg = json.loads(raw)
        if msg.get("type") == "tick":
            ticks.append(msg)
    return ticks


def test_kill_restart_preserves_state_and_appends(tmp_path):
    """Real-subprocess kill-restart with full state and row preservation.

    First process commits at least 3 ticks and is hard-killed.  All
    first-segment event and trajectory rows are saved.  A second process
    recovers and ticks; after its kill the first-segment rows are verified
    byte-identical, and new rows are strictly appended with higher IDs.
    The ready payload includes a state summary from snapshot_proxy() which is
    verified against the stored snapshot capsule."""
    db_path = str(tmp_path / "kill_restart.db")

    # --- First process ---
    proc1 = _start_harness(db_path)
    try:
        ready1 = _wait_ready(proc1)
        boot1 = ready1["boot_id"]
        registry_ids = ready1["registry_ids"]
        ticks1 = _collect_ticks(proc1, min_ticks=3, timeout=30.0)
        assert len(ticks1) >= 3, (
            f"first process must commit at least 3 ticks; got {len(ticks1)}"
        )
        pre_kill_tick = max(int(t["tick_after"]) for t in ticks1)
    finally:
        _hard_kill(proc1)

    # Save first-segment rows
    seg1_events = _db_event_rows(db_path)
    seg1_trajectory = _db_trajectory_rows(db_path)
    max_event_before = _db_max_event_id(db_path)
    max_traj_before = _db_max_trajectory_id(db_path)
    latest_snap_tick_before = _db_latest_snapshot_tick(db_path)

    # --- Second process (recovery) ---
    proc2 = _start_harness(db_path)
    try:
        ready2 = _wait_ready(proc2)
        boot2 = ready2["boot_id"]
        recovered_tick = int(ready2["field_tick"])
        recovered_registry = ready2["registry_ids"]

        # Verify ready state summary matches recovered capsule snapshot
        # Strict exact comparison: value, velocity, attractor,
        # ou_acceleration from capsule dimensions; soft_restoring_baseline
        # from slow_state.baselines. No tolerance.
        snap_conn = sqlite3.connect(db_path)
        try:
            snap_row = snap_conn.execute(
                "SELECT capsule_json FROM field_snapshots "
                "ORDER BY snapshot_id DESC LIMIT 1"
            ).fetchone()
            assert snap_row is not None, "no snapshot found for state_match"
            snap_primitive = json.loads(snap_row[0])
            capsule_dims = snap_primitive.get("dimensions", [])
            slow_baselines = snap_primitive.get("slow_state", {}).get(
                "baselines", []
            )
            # Build dim_id -> baseline mapping
            baseline_map = {b.get("dim_id"): b.get("current_baseline")
                            for b in slow_baselines}
            ready_summary = ready2.get("state_summary", {})
            assert len(ready_summary) == len(capsule_dims), (
                f"ready summary dim count {len(ready_summary)} != "
                f"capsule dim count {len(capsule_dims)}"
            )
            state_match = True
            for i, dim in enumerate(capsule_dims):
                ordinal_key = str(i)
                assert ordinal_key in ready_summary, (
                    f"ready summary missing ordinal {ordinal_key}"
                )
                rd = ready_summary[ordinal_key]
                assert rd.get("dim_id") == dim.get("dim_id"), (
                    f"dim {i}: ready dim_id {rd.get('dim_id')!r} != "
                    f"capsule dim_id {dim.get('dim_id')!r}"
                )
                # Compare 4 fields from capsule dimensions
                for field in ("value", "velocity", "attractor",
                              "ou_acceleration"):
                    rv = rd.get(field)
                    cv = dim.get(field)
                    if rv != cv:
                        state_match = False
                        break
                if not state_match:
                    break
                # Compare soft_restoring_baseline from slow_state
                if rd.get("soft_restoring_baseline") != baseline_map.get(
                    dim.get("dim_id")
                ):
                    state_match = False
                    break
            assert state_match, (
                "recovered state does not match persisted capsule snapshot"
            )
        finally:
            snap_conn.close()

        ticks2 = _collect_ticks(proc2, min_ticks=1, timeout=30.0)
        assert len(ticks2) >= 1, "second process committed no ticks"
        post_tick = max(int(t["tick_after"]) for t in ticks2)
    finally:
        _hard_kill(proc2)

    # --- Verification ---
    boot_ids = _db_boot_ids(db_path)
    seg2_events = _db_event_rows(db_path)
    seg2_trajectory = _db_trajectory_rows(db_path)

    # Boot identity
    assert boot1 != boot2
    assert recovered_registry == registry_ids
    assert recovered_tick <= pre_kill_tick
    loss_ticks = pre_kill_tick - recovered_tick
    assert 0 <= loss_ticks <= SNAPSHOT_INTERVAL_SECONDS
    assert boot1 in boot_ids
    assert boot2 in boot_ids
    assert len(boot_ids) >= 2

    # First-segment rows must be byte-identical after second kill
    assert len(seg2_events) >= len(seg1_events)
    for i, seg1_row in enumerate(seg1_events):
        assert seg2_events[i] == seg1_row, (
            f"event row {i} changed after second kill"
        )
    assert len(seg2_trajectory) >= len(seg1_trajectory)
    for i, seg1_row in enumerate(seg1_trajectory):
        assert seg2_trajectory[i] == seg1_row, (
            f"trajectory row {i} changed after second kill"
        )

    # New rows must be strictly appended
    assert _db_max_event_id(db_path) > max_event_before
    assert _db_max_trajectory_id(db_path) > max_traj_before

    # Machine-readable evidence
    evidence = {
        "pre_kill_tick": pre_kill_tick,
        "recovered_tick": recovered_tick,
        "loss_ticks": loss_ticks,
        "boot1": boot1,
        "boot2": boot2,
        "events_before_kill": len(seg1_events),
        "events_after_restart": len(seg2_events),
        "trajectory_before_kill": len(seg1_trajectory),
        "trajectory_after_restart": len(seg2_trajectory),
        "latest_snapshot_tick_before_kill": latest_snap_tick_before,
        "post_restart_tick": post_tick,
        "preserved_event_rows": True,
        "preserved_trajectory_rows": True,
        "state_match": True,
        "note": "executor pre-check evidence; Owner manual run is authoritative",
    }
    print("P1_2_B_KILL_RESTART_EVIDENCE=" + json.dumps(evidence, sort_keys=True))


# ---------------------------------------------------------------------------
# P1.2-B correction: cross-process lock and hard-kill release
# ---------------------------------------------------------------------------


def test_cross_process_lock_prevents_open_then_hard_kill_releases(tmp_path):
    """Cross-process owner lock: parent denied, hard-kill releases.

    Start a harness (child process holds the lock).  Assert that the parent
    process cannot open the same database (owner_lock_held).  Hard-kill the
    child, then assert the parent can open and close successfully.
    """
    from app.chatbox.field_runtime import FieldRuntime, FieldRuntimeError

    db_path = str(tmp_path / "cross_lock.db")
    proc = _start_harness(db_path)
    try:
        ready = _wait_ready(proc)
        assert ready["type"] == "ready"

        # Parent must NOT be able to open while child holds lock
        with pytest.raises(FieldRuntimeError) as caught:
            FieldRuntime.open(db_path)
        assert caught.value.code == "owner_lock_held"
    finally:
        _hard_kill(proc)

    # After kill, parent can open
    runtime = FieldRuntime.open(db_path)
    try:
        assert runtime.field_tick >= 0
    finally:
        runtime.close()


# ---------------------------------------------------------------------------
# P1.2-B correction: non-trivial real-time kill-restart (>60s boundary)
# ---------------------------------------------------------------------------


@pytest.mark.real_time
def test_nontrivial_kill_restart_across_60s_snapshot_boundary(tmp_path):
    """Real-time kill-restart crossing the 60-second snapshot boundary.

    Launches a real harness, waits for a second committed snapshot with
    field_tick > 0, collects at least 3 additional confirmed ticks, hard-kills,
    saves pre-snapshot event/trajectory rows, restarts, and verifies:
      - recovered_tick == latest_snapshot_tick (both > 0)
      - registry identical
      - tick loss (pre_kill_tick - recovered_tick) >= 0 and <= 60
      - real-time loss (kill_wall_ns - snapshot_utc_unix_ns) / 1e9 in [0, 60]
      - pre-snapshot old rows byte-identical after restart
      - new rows strictly appended
      - owner_lock_reacquired_after_kill == true

    Total timeout ~100 s; typical real duration ~65-75 s.
    """
    db_path = str(tmp_path / "nontrivial_kill_restart.db")
    timeout = 100.0
    overall_deadline = time.time() + timeout

    # --- Launch first harness ---
    proc1 = _start_harness(db_path)
    try:
        ready1 = _wait_ready(proc1)
        boot1 = ready1["boot_id"]
        registry_ids = ready1["registry_ids"]

        # Collect ticks and poll DB for a second committed snapshot with tick > 0
        ticks1: list[dict] = []
        second_snapshot_seen = False

        while time.time() < overall_deadline:
            # Read next tick (non-blocking read would hang, so read with short timeout)
            if proc1.stdout is not None:
                raw = proc1.stdout.readline()
                if raw:
                    raw = raw.strip()
                    if raw:
                        try:
                            msg = json.loads(raw)
                            if msg.get("type") == "tick":
                                ticks1.append(msg)
                        except json.JSONDecodeError:
                            pass

            # Poll DB for snapshot count
            conn = sqlite3.connect(db_path)
            try:
                snap_rows = conn.execute(
                    "SELECT snapshot_id, field_tick, utc_unix_ns FROM field_snapshots "
                    "ORDER BY snapshot_id"
                ).fetchall()
                if len(snap_rows) >= 2 and any(int(r[1]) > 0 for r in snap_rows):
                    second_snapshot_seen = True
                    # Check if we have enough ticks past the latest snapshot
                    latest_snap_tick = max(int(r[1]) for r in snap_rows)
                    latest_tick = max(
                        int(t["tick_after"]) for t in ticks1
                    ) if ticks1 else 0
                    if latest_tick >= latest_snap_tick + 3:
                        break
            finally:
                conn.close()

            if proc1.poll() is not None:
                err = ""
                if proc1.stderr is not None:
                    err = proc1.stderr.read()
                pytest.fail(f"harness exited before snapshot evidence: stderr={err}")

            time.sleep(0.5)

        if not second_snapshot_seen:
            _hard_kill(proc1)
            pytest.fail("timed out waiting for second snapshot with tick > 0")

        # Re-read latest snapshot from DB
        conn = sqlite3.connect(db_path)
        try:
            snap_rows = conn.execute(
                "SELECT snapshot_id, field_tick, utc_unix_ns FROM field_snapshots "
                "ORDER BY snapshot_id"
            ).fetchall()
            latest_snapshot_tick = max(int(r[1]) for r in snap_rows)
            # Find the UTC of the latest snapshot
            snapshot_utc_unix_ns = max(
                int(r[2]) for r in snap_rows if int(r[1]) == latest_snapshot_tick
            )
        finally:
            conn.close()

        pre_kill_tick = max(int(t["tick_after"]) for t in ticks1)
        kill_wall_ns = int(time.time_ns())

        assert latest_snapshot_tick > 0, "latest snapshot tick must be > 0"
        assert pre_kill_tick >= latest_snapshot_tick + 3, (
            f"need at least 3 ticks past snapshot; "
            f"pre_kill={pre_kill_tick}, snap={latest_snapshot_tick}"
        )
    finally:
        _hard_kill(proc1)

    # Save pre-snapshot event/trajectory rows (first boot, tick <= snapshot)
    all_events = _db_event_rows(db_path)
    all_traj = _db_trajectory_rows(db_path)
    pre_snap_events = [
        row for row in all_events
        if int(row[4]) <= latest_snapshot_tick  # after_field_tick
    ]
    pre_snap_event_ids = {row[0] for row in pre_snap_events}
    pre_snap_traj = [
        row for row in all_traj
        if row[1] in pre_snap_event_ids  # event_id
    ]

    # --- Launch second harness (recovery) ---
    proc2 = _start_harness(db_path)
    try:
        ready2 = _wait_ready(proc2)
        boot2 = ready2["boot_id"]
        recovered_tick = int(ready2["field_tick"])
        recovered_registry = ready2["registry_ids"]

        assert recovered_tick == latest_snapshot_tick, (
            f"recovered_tick {recovered_tick} != latest_snapshot_tick {latest_snapshot_tick}"
        )
        assert recovered_tick > 0
        assert recovered_registry == registry_ids

        # Collect at least 1 tick from second boot
        ticks2 = _collect_ticks(proc2, min_ticks=1, timeout=30.0)
        assert len(ticks2) >= 1
        post_tick = max(int(t["tick_after"]) for t in ticks2)
    finally:
        _hard_kill(proc2)

    # --- Verification ---
    real_time_loss_seconds = (kill_wall_ns - snapshot_utc_unix_ns) / 1e9
    assert real_time_loss_seconds >= 0.0
    assert real_time_loss_seconds <= 60.0, (
        f"real time loss {real_time_loss_seconds:.1f}s exceeds 60s bound"
    )

    loss_ticks = pre_kill_tick - recovered_tick
    assert loss_ticks >= 0
    assert loss_ticks <= 60, f"tick loss {loss_ticks} exceeds 60"

    # Pre-snapshot rows must be byte-identical
    all_events_after = _db_event_rows(db_path)
    all_traj_after = _db_trajectory_rows(db_path)
    for i, seg1_row in enumerate(pre_snap_events):
        assert all_events_after[i] == seg1_row, (
            f"pre-snapshot event row {i} changed after restart"
        )
    for i, seg1_row in enumerate(pre_snap_traj):
        assert all_traj_after[i] == seg1_row, (
            f"pre-snapshot trajectory row {i} changed after restart"
        )

    # New rows appended
    assert len(all_events_after) > len(pre_snap_events)
    assert len(all_traj_after) > len(pre_snap_traj)

    # Boot identity
    assert boot1 != boot2
    boot_ids = _db_boot_ids(db_path)
    assert boot1 in boot_ids
    assert boot2 in boot_ids

    # Machine-readable evidence
    evidence = {
        "pre_kill_tick": pre_kill_tick,
        "latest_snapshot_tick": latest_snapshot_tick,
        "recovered_tick": recovered_tick,
        "loss_ticks": loss_ticks,
        "real_time_loss_seconds": real_time_loss_seconds,
        "ticks_after_snapshot_before_kill": pre_kill_tick - latest_snapshot_tick,
        "pre_snapshot_event_rows_preserved": len(pre_snap_events),
        "pre_snapshot_trajectory_rows_preserved": len(pre_snap_traj),
        "boot1": boot1,
        "boot2": boot2,
        "owner_lock_reacquired_after_kill": True,
    }
    print("P1_2_B_NONTRIVIAL_KILL_RESTART_EVIDENCE=" + json.dumps(evidence, sort_keys=True))
