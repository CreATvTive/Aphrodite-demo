from __future__ import annotations

import pytest
import subprocess
import sys
from pathlib import Path


@pytest.mark.skip(reason="CLI output may vary with data; TODO: update assertions")
def test_run_trigger_demo_cli() -> None:
    root = Path(__file__).resolve().parents[2]
    cmd = [
        sys.executable,
        "cli/run_trigger_demo.py",
        "--query",
        "\u5f00\u59cbdebug",
    ]
    proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, check=True)
    assert "decision=trigger" in proc.stdout
    assert "selected=code_debug" in proc.stdout


def test_eval_trigger_engine_cli() -> None:
    root = Path(__file__).resolve().parents[2]
    report_path = root / "outputs" / "test_report.json"
    trace_path = root / "outputs" / "test_trace.jsonl"
    cmd = [
        sys.executable,
        "cli/eval_trigger_engine.py",
        "--dataset",
        "data/eval/eval_dataset.jsonl",
        "--save-report",
        str(report_path),
        "--save-trace",
        str(trace_path),
    ]
    subprocess.run(cmd, cwd=root, capture_output=True, text=True, check=True)
    assert report_path.exists()
    assert trace_path.exists()


def test_replay_run_cli_exports_reports(tmp_path) -> None:
    root = Path(__file__).resolve().parents[2]
    run_id = "run_test_cli"
    base_dir = tmp_path / "task_runs"
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / f"{run_id}.json").write_text(
        """{
  "run_id": "run_test_cli",
  "goal": "demo",
  "plan": [],
  "steps": [],
  "status": "done",
  "created_at": 1.0,
  "updated_at": 2.0
}
""",
        encoding="utf-8",
    )
    (base_dir / f"{run_id}.steps.jsonl").write_text(
        '{"step_id":"s0001","ts_start":1.0,"ts_end":2.0,"duration_ms":1000,"input_payload":{},"tool_calls":[],"output":{"summary":"ok"},"error":"","status":"ok"}\n',
        encoding="utf-8",
    )

    cmd = [
        sys.executable,
        "-m",
        "cli.replay_run",
        "--run-id",
        run_id,
        "--base-dir",
        str(base_dir),
    ]
    proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, check=True)
    assert "json_report=" in proc.stdout
    assert "markdown_report=" in proc.stdout
    assert (base_dir / "reports" / f"{run_id}.report.json").exists()
    assert (base_dir / "reports" / f"{run_id}.report.md").exists()
