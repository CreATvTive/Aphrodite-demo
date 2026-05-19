from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_core_pipeline_tests.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_core_pipeline_tests", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_dry_run_prints_all_groups(capsys):
    runner = _load_runner()

    exit_code = runner.main([])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "[field_state]" in output
    assert "[motion_params]" in output
    assert "[body_action]" in output
    assert "[replay_viewer]" in output
    assert "tests/test_field_state_schema.py" in output
    assert "tests/test_motion_params_mapper.py" in output
    assert "tests/test_body_action_composer.py" in output
    assert "tests/test_source_aligned_replay.py" in output
    assert "Dry run only" in output


def test_group_body_action_prints_only_body_action_command(capsys):
    runner = _load_runner()

    exit_code = runner.main(["--group", "body_action"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "[body_action]" in output
    assert "tests/test_body_action_schema.py" in output
    assert "[field_state]" not in output
    assert "[motion_params]" not in output
    assert "[replay_viewer]" not in output
    assert "tests/test_field_state_schema.py" not in output
    assert "tests/test_motion_params_mapper.py" not in output
    assert "tests/test_source_aligned_replay.py" not in output


def test_invalid_group_returns_nonzero_with_clear_message(capsys):
    runner = _load_runner()

    exit_code = runner.main(["--group", "not_a_group"])
    captured = capsys.readouterr()

    assert exit_code != 0
    assert "Unknown group" in captured.err
    assert "not_a_group" in captured.err


def test_run_uses_subprocess_run_only_in_run_mode(monkeypatch, capsys):
    runner = _load_runner()
    calls = []

    class Result:
        returncode = 0

    def fake_run(command, cwd=None):
        calls.append((command, cwd))
        return Result()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    dry_exit = runner.main(["--group", "body_action"])
    assert dry_exit == 0
    assert calls == []

    run_exit = runner.main(["--group", "body_action", "--run"])
    output = capsys.readouterr().out

    assert run_exit == 0
    assert len(calls) == 1
    assert calls[0][0][:3] == [sys.executable, "-m", "pytest"]
    assert "tests/test_body_action_schema.py" in calls[0][0]
    assert "[body_action]" in output


def test_no_forbidden_imports():
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.append(node.module or "")

    forbidden_fragments = {
        "agentlib",
        "runtime",
        "renderer",
        "animation",
        "avatar",
        "llm",
        "openai",
        "anthropic",
        "field_trace",
        "field_state",
        "motion_params",
        "body_action",
        "src.",
    }

    assert all(
        not any(fragment in module.lower() for fragment in forbidden_fragments)
        for module in imported_modules
    )
