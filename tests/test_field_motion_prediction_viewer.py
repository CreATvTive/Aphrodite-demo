from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

from src.viewers.field_motion_prediction import (
    DEMO_FIELD_VALUES,
    build_demo_snapshot,
    build_report,
    format_json_report,
    format_table_report,
    format_text_report,
    load_snapshots,
)


ROOT = Path(__file__).resolve().parents[1]
VIEWER_PATH = ROOT / "src" / "viewers" / "field_motion_prediction.py"
SCRIPT_PATH = ROOT / "scripts" / "display_field_motion_prediction.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("display_field_motion_prediction", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _tendency_scores(report: dict) -> list[tuple[str, float]]:
    return [
        (item["name"], item["score"])
        for item in report["predicted_motion_tendencies"]
    ]


def test_viewer_handles_builtin_demo_scenarios():
    assert {"correction", "technical_question", "dependency_expression"}.issubset(DEMO_FIELD_VALUES)

    for scenario in ("correction", "technical_question", "dependency_expression"):
        snapshot = build_demo_snapshot(scenario)
        report = build_report(snapshot)

        assert report["scenario"] == scenario
        assert report["field_state_summary"]["top_activated_variables"]
        assert report["motion_params_summary"]["motion_completion"] > 0.0
        assert report["predicted_motion_tendencies"]
        assert report["behavior_affecting"] is False


def test_viewer_handles_missing_optional_fields_gracefully():
    report = build_report(
        {
            "scenario_id": "minimal",
            "field_state": {
                "variables": {
                    "boundary_distance": {"numeric_value": 0.62},
                },
            },
        }
    )

    assert report["scenario"] == "minimal"
    assert report["motion_params_summary"]["body_part_offsets"] is None
    assert report["timeline_curve_summary"]["available"] is False
    assert len(report["predicted_motion_tendencies"]) == 8


def test_viewer_produces_deterministic_ranking():
    snapshot = build_demo_snapshot("dependency_expression")
    first = build_report(snapshot)
    second = build_report(snapshot)

    assert _tendency_scores(first) == _tendency_scores(second)
    scores = [score for _, score in _tendency_scores(first)]
    assert scores == sorted(scores, reverse=True)


def test_viewer_emits_json_report():
    report = build_report(build_demo_snapshot("technical_question"))
    decoded = json.loads(format_json_report(report))

    assert decoded["scenario"] == "technical_question"
    assert "field_state_summary" in decoded
    assert "predicted_motion_tendencies" in decoded
    assert decoded["behavior_affecting"] is False


def test_text_and_table_reports_are_human_readable():
    report = build_report(build_demo_snapshot("correction"))
    text_output = format_text_report(report)
    table_output = format_table_report(report)

    assert "Field State Summary" in text_output
    assert "MotionParams" in text_output
    assert "Predicted motion tendencies" in text_output
    assert "future clip" in text_output
    assert "rank | tendency | score" in table_output
    assert "micro_delay" in table_output or "restrained_response" in table_output


def test_jsonl_snapshot_input_is_supported(tmp_path):
    old_snapshot = build_demo_snapshot("correction")
    latest_snapshot = build_demo_snapshot("dependency_expression")
    path = tmp_path / "snapshots.jsonl"
    path.write_text(
        json.dumps(old_snapshot, ensure_ascii=False)
        + "\n"
        + json.dumps(latest_snapshot, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    records = load_snapshots(path)
    report = build_report(records[-1])

    assert len(records) == 2
    assert report["scenario"] == "dependency_expression"


def test_cli_module_supports_demo_json_and_table(capsys):
    script = _load_script()

    assert script.main(["--demo", "technical_question", "--json"]) == 0
    json_output = capsys.readouterr().out
    assert json.loads(json_output)["scenario"] == "technical_question"

    assert script.main(["--demo", "correction", "--table"]) == 0
    table_output = capsys.readouterr().out
    assert "Scenario: correction" in table_output
    assert "rank | tendency | score" in table_output


def test_no_runtime_behavior_is_modified_by_report_generation():
    protected_paths = [
        ROOT / "agentlib" / "runtime_engine.py",
        ROOT / "src" / "motion_params" / "mapper.py",
        ROOT / "src" / "body_action" / "motion_to_action_mapper.py",
        ROOT / "src" / "body_action" / "composer.py",
    ]
    before = {path: path.read_text(encoding="utf-8") for path in protected_paths if path.exists()}

    report = build_report(build_demo_snapshot("dependency_expression"))
    format_text_report(report)
    format_json_report(report)

    after = {path: path.read_text(encoding="utf-8") for path in protected_paths if path.exists()}
    assert after == before


def test_no_llm_api_or_display_engine_dependency():
    imported_modules: list[str] = []
    for path in (VIEWER_PATH, SCRIPT_PATH):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported_modules.append(node.module or "")

    forbidden_imports = {
        "agentlib",
        "runtime_engine",
        "renderer",
        "animation",
        "avatar",
        "llm",
        "openai",
        "anthropic",
        "requests",
        "httpx",
        "socket",
        "subprocess",
        "prompt",
        "language",
    }
    assert all(
        not any(fragment in module.lower() for fragment in forbidden_imports)
        for module in imported_modules
    )

    source = VIEWER_PATH.read_text(encoding="utf-8") + SCRIPT_PATH.read_text(encoding="utf-8")
    for token in ("user_text", "raw_text", "input_text", "stdin"):
        assert token not in source
