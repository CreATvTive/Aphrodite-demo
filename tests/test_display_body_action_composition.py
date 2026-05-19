from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "display_body_action_composition.py"


def _load_viewer():
    spec = importlib.util.spec_from_file_location("display_body_action_composition", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _action(
    action_name: str,
    *,
    order: int = 1,
    duration_hint: str = "short",
    completion: str = "restrained",
    constraints: list[str] | None = None,
) -> dict:
    return {
        "action_name": action_name,
        "order": order,
        "duration_hint": duration_hint,
        "completion": completion,
        "constraints": constraints or [],
        "provenance": ["BodyActionWeights->BodyActionComposition v0"],
        "behavior_affecting": False,
    }


def _composition_record() -> dict:
    return {
        "primary_actions": [
            _action("pause", order=0, duration_hint="sustained"),
            _action("look_away", order=1, duration_hint="short"),
        ],
        "secondary_actions": [
            _action("reset_posture", order=2, duration_hint="short"),
        ],
        "suppressed_actions": ["slight_forward"],
        "hard_constraints": ["no_forward_lean"],
        "source_weights": [],
        "composition_note": (
            "BodyActionWeights->BodyActionComposition v0; "
            "offsets=gaze:0ms,head:40ms,shoulder:90ms,hand:140ms"
        ),
        "body_part_offsets": {
            "gaze_offset_ms": 0,
            "head_offset_ms": 40,
            "shoulder_offset_ms": 90,
            "hand_offset_ms": 140,
        },
        "behavior_affecting": False,
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )


def test_01_reads_latest_jsonl_record(tmp_path):
    viewer = _load_viewer()
    old_record = _composition_record()
    old_record["composition_note"] = "old"
    latest_record = _composition_record()
    latest_record["composition_note"] = "latest"
    path = tmp_path / "composition.jsonl"
    path.write_text(
        "not-json\n"
        + json.dumps(old_record, ensure_ascii=False)
        + "\n"
        + json.dumps(latest_record, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    assert viewer.read_latest_record(path)["composition_note"] == "latest"


def test_02_handles_empty_and_missing_file_gracefully(tmp_path):
    viewer = _load_viewer()
    missing_path = tmp_path / "missing.jsonl"
    empty_path = tmp_path / "empty.jsonl"
    empty_path.write_text("", encoding="utf-8")

    assert viewer.read_latest_record(missing_path) is None
    assert viewer.read_latest_record(empty_path) is None
    output = viewer.render_panel(None, path=missing_path)

    assert "没有可显示" in output
    assert str(missing_path) in output


def test_03_displays_primary_actions():
    viewer = _load_viewer()
    output = viewer.render_panel(_composition_record())

    assert "主动作" in output
    assert "pause" in output
    assert "停顿" in output


def test_04_displays_secondary_actions():
    viewer = _load_viewer()
    output = viewer.render_panel(_composition_record())

    assert "辅助动作" in output
    assert "reset_posture" in output
    assert "重置姿态" in output


def test_05_displays_suppressed_actions():
    viewer = _load_viewer()
    output = viewer.render_panel(_composition_record())

    assert "被抑制动作" in output
    assert "slight_forward" in output
    assert "轻微前倾" in output


def test_06_displays_duration_hint_and_completion():
    viewer = _load_viewer()
    output = viewer.render_panel(_composition_record())

    assert "duration_hint" in output
    assert "completion" in output
    assert "sustained" in output
    assert "restrained" in output
    assert "持续" in output
    assert "受抑制完成" in output


def test_07_viewer_card_hides_internal_fields():
    viewer = _load_viewer()
    output = viewer.render_panel(_composition_record(), viewer_card=True)

    assert "当前身体组合" in output
    assert "主要动作" in output
    assert "辅助动作" in output
    assert "被抑制动作" in output
    assert "provenance" not in output
    assert "behavior_affecting" not in output
    assert "duration_hint" not in output
    assert "completion=" not in output
    assert "order=" not in output


def test_08_debug_mode_includes_useful_details():
    viewer = _load_viewer()
    output = viewer.render_panel(_composition_record())

    assert "order=0" in output
    assert "provenance" in output
    assert "behavior_affecting" in output
    assert "约束 / suppression reason" in output


def test_09_does_not_modify_input_file(tmp_path):
    viewer = _load_viewer()
    path = tmp_path / "composition.jsonl"
    _write_jsonl(path, [_composition_record()])
    before = path.read_bytes()

    record = viewer.read_latest_record(path)
    viewer.render_panel(record)

    assert path.read_bytes() == before


def test_10_does_not_import_forbidden_runtime_or_renderer_paths():
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.append(node.module or "")

    forbidden_fragments = {
        "runtime",
        "renderer",
        "animation",
        "avatar",
        "llm",
        "router",
        "memory",
        "field_state",
        "field_trace",
        "motion_params",
        "motion_to_action_mapper",
        "composer",
        "agentlib",
        "src.",
    }

    assert all(
        not any(fragment in module.lower() for fragment in forbidden_fragments)
        for module in imported_modules
    )


def test_11_handles_composition_note_with_body_part_offsets_text():
    viewer = _load_viewer()
    record = _composition_record()
    record.pop("body_part_offsets")
    output = viewer.render_panel(record)

    assert "body_part_offsets" in output
    assert "offsets=gaze:0ms" in output
    assert "head:40ms" in output


def test_12_output_remains_chinese_readable():
    viewer = _load_viewer()
    output = viewer.render_panel(_composition_record())
    card_output = viewer.render_panel(_composition_record(), viewer_card=True)

    for text in ("身体动作组合调试面板", "主动作", "辅助动作", "被抑制动作", "说明"):
        assert text in output or text in card_output


def test_nested_body_action_composition_record_is_supported():
    viewer = _load_viewer()
    output = viewer.render_panel({"body_action_composition": _composition_record()})

    assert "主动作" in output
    assert "pause" in output
