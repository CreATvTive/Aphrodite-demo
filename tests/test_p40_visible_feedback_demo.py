from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_p40_visible_feedback_demo.py"


def _load_demo():
    spec = importlib.util.spec_from_file_location("run_p40_visible_feedback_demo", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _mock_result(module):
    return module._mock_result()


def _sample_input() -> str:
    return (
        "我现在有点失望，因为工程推了很多，但我还是没有看到她真的动起来。"
        "我想知道这个系统现在到底能不能给我一点真实反馈。"
    )


def _has_behavior_true(value) -> bool:
    if isinstance(value, dict):
        if value.get("behavior_affecting") is True:
            return True
        return any(_has_behavior_true(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_behavior_true(item) for item in value)
    return False


def test_shadow_projection_returns_all_required_field_variables():
    module = _load_demo()
    proposal = _mock_result(module)["proposal"]
    snapshot = module.build_shadow_projection_snapshot(_sample_input(), proposal)

    assert snapshot["projection_mode"] == "shadow_projection_only"
    assert set(snapshot["field_state"]["variables"]) == set(module.FIELD_VARIABLES)
    assert set(snapshot["motion_params"]).issuperset({
        "initial_delay_sec",
        "pause_after_sec",
        "motion_completion",
        "motion_speed",
        "gaze_release_amplitude",
        "gaze_contact_sec",
        "posture_stability",
        "expression_amplitude",
        "torso_lean",
        "body_part_offsets",
    })


def test_behavior_affecting_stays_false_through_record(tmp_path):
    module = _load_demo()
    output_path = tmp_path / "demo.jsonl"
    record = module.run_visible_feedback_demo(
        _sample_input(),
        output_path=output_path,
        proposal_result=_mock_result(module),
    )

    assert record["behavior_affecting"] is False
    assert not _has_behavior_true(record)


def test_jsonl_append_works_with_mocked_proposal(tmp_path):
    module = _load_demo()
    output_path = tmp_path / "demo.jsonl"

    module.run_visible_feedback_demo(
        _sample_input(),
        output_path=output_path,
        proposal_result=_mock_result(module),
    )
    module.run_visible_feedback_demo(
        "第二条 shadow-only 输入。",
        output_path=output_path,
        proposal_result=_mock_result(module),
    )

    records = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) == 2
    assert all(record["shadow_field_snapshot"]["projection_mode"] == "shadow_projection_only" for record in records)


def test_demo_can_run_with_mocked_proposal_without_append(capsys):
    module = _load_demo()

    exit_code = module.main([
        "--text",
        _sample_input(),
        "--mock-proposal",
        "--no-append",
    ])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "P40 visible feedback / P40 可见反馈" in output
    assert "shadow_projection_only" in output
    assert "behavior_affecting=False" in output


def test_no_runtime_files_modified_by_mock_demo(tmp_path):
    module = _load_demo()
    protected_paths = [
        ROOT / "agentlib" / "runtime_engine.py",
        ROOT / "src" / "field_dynamics" / "kernel.py",
        ROOT / "src" / "motion_params" / "mapper.py",
        ROOT / "src" / "body_action" / "motion_to_action_mapper.py",
        ROOT / "src" / "body_action" / "composer.py",
    ]
    before = {path: path.read_text(encoding="utf-8") for path in protected_paths if path.exists()}

    module.run_visible_feedback_demo(
        _sample_input(),
        output_path=tmp_path / "demo.jsonl",
        proposal_result=_mock_result(module),
    )

    after = {path: path.read_text(encoding="utf-8") for path in protected_paths if path.exists()}
    assert after == before


def test_script_does_not_import_runtime_renderer_animation_or_regulator_paths():
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.append(node.module or "")

    forbidden = {
        "runtime_engine",
        "field_dynamics",
        "force_adapter",
        "regulator",
        "renderer",
        "animation",
        "avatar",
        "prompt",
        "language",
    }
    assert all(
        not any(fragment in module.lower() for fragment in forbidden)
        for module in imported_modules
    )
