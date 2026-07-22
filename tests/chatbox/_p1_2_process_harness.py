"""Real-subprocess harness for the P1.2-B kill-restart test.

This harness only calls the production ``FieldRuntime``.  It does NOT
implement its own persistence or recovery.  It is launched as a real child
process by ``test_p1_2_b_kill_restart.py``; it opens the runtime at the DB
path given as argv[1], emits a ``ready`` JSON line (including a read-only
state summary via ``snapshot_proxy()``), then ticks in a 1 Hz loop
and emits a ``tick`` JSON line per tick, flushing every line so the parent can
observe progress before killing the process.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.chatbox.field_runtime import FieldRuntime  # noqa: E402


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _state_summary(runtime: FieldRuntime) -> dict:
    """Read-only state summary via snapshot_proxy() only."""
    snap = runtime.snapshot_proxy()
    summary: dict = {}
    for i, dim in enumerate(snap.dimensions):
        summary[str(i)] = {
            "dim_id": dim.dim_id,
            "value": dim.value,
            "velocity": dim.velocity,
            "attractor": dim.attractor,
            "soft_restoring_baseline": dim.soft_restoring_baseline,
            "ou_acceleration": dim.ou_acceleration,
        }
    return summary


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("usage: _p1_2_process_harness.py <db_path>\n")
        return 2
    db_path = sys.argv[1]
    try:
        runtime = FieldRuntime.open(db_path)
    except Exception as exc:
        _emit({"type": "startup_error", "detail": str(exc)})
        return 1

    _emit(
        {
            "type": "ready",
            "boot_id": runtime.boot_id,
            "field_tick": runtime.field_tick,
            "registry_ids": list(runtime.registry_proxy().dim_ids),
            "state_summary": _state_summary(runtime),
        }
    )

    try:
        while True:
            observation = runtime.tick()
            _emit(
                {
                    "type": "tick",
                    "boot_id": runtime.boot_id,
                    "tick_after": observation.tick_after,
                    "field_tick": runtime.field_tick,
                }
            )
            time.sleep(1.0)
    except Exception as exc:
        _emit({"type": "runtime_error", "detail": str(exc)})
        return 1
    finally:
        try:
            runtime.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
