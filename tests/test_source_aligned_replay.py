from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from src.field_state.schema import REQUIRED_FIELD_VARIABLES, RelationalFieldState


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_source_aligned_replay.py"


EXPECTED_SCENARIO_IDS = {
    "brief_contact_then_release",
    "approach_exists_but_cannot_complete",
    "warmth_capped_by_non_service",
    "protection_without_caretaking",
    "desire_pressure_compressed_into_gaze_tension",
    "possession_as_sealed_field_not_control",
    "high_stability_without_cold_mystery",
}


def _load_replay():
    spec = importlib.util.spec_from_file_location("run_source_aligned_replay", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _records() -> list[dict]:
    return _load_replay().run_replay()


def _record(records: list[dict], scenario_id: str) -> dict:
    return next(record for record in records if record["scenario_id"] == scenario_id)


def _composition(record: dict) -> dict:
    return record["body_action_composition"]


def _primary_names(record: dict) -> list[str]:
    return [action["action_name"] for action in _composition(record)["primary_actions"]]


def _secondary_names(record: dict) -> list[str]:
    return [action["action_name"] for action in _composition(record)["secondary_actions"]]


def _active_names(record: dict) -> set[str]:
    return set(_primary_names(record)) | set(_secondary_names(record))


def _weight_map(record: dict) -> dict[str, str]:
    return {
        weight["action_name"]: weight["weight"]
        for weight in record["body_action_weights"]["weights"]
    }


def _has_behavior_true(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("behavior_affecting") is True:
            return True
        return any(_has_behavior_true(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_behavior_true(item) for item in value)
    return False


def test_01_all_golden_scenarios_exist():
    replay = _load_replay()
    scenario_ids = {scenario.scenario_id for scenario in replay.GOLDEN_SCENARIOS}

    assert EXPECTED_SCENARIO_IDS.issubset(scenario_ids)
    assert len(replay.GOLDEN_SCENARIOS) >= 7


def test_02_each_scenario_builds_valid_relational_field_state():
    replay = _load_replay()

    for scenario in replay.GOLDEN_SCENARIOS:
        state = replay.build_field_state(scenario)
        assert isinstance(state, RelationalFieldState)
        assert set(state.variables) == set(REQUIRED_FIELD_VARIABLES)
        assert state.behavior_affecting is False
        assert all(0.0 <= variable.numeric_value <= 1.0 for variable in state.variables.values())


def test_03_replay_produces_one_record_per_scenario():
    replay = _load_replay()
    records = replay.run_replay()

    assert len(records) == len(replay.GOLDEN_SCENARIOS)
    assert {record["scenario_id"] for record in records} == {
        scenario.scenario_id for scenario in replay.GOLDEN_SCENARIOS
    }


def test_04_each_record_includes_body_action_composition():
    for record in _records():
        assert "body_action_composition" in record
        composition = record["body_action_composition"]
        assert "primary_actions" in composition
        assert "secondary_actions" in composition
        assert "suppressed_actions" in composition


def test_05_each_record_is_json_serializable():
    for record in _records():
        encoded = json.dumps(record, ensure_ascii=False)
        assert record["scenario_id"] in encoded


def test_06_output_jsonl_can_be_read_back(tmp_path):
    replay = _load_replay()
    records = replay.run_replay()
    output_path = tmp_path / "golden.jsonl"

    replay.write_records(records, output_path)
    decoded = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert decoded == records


def test_07_no_scenario_produces_behavior_affecting_true():
    for record in _records():
        assert not _has_behavior_true(record)


def test_08_brief_contact_then_release_does_not_produce_default_sustained_gaze_lock():
    record = _record(_records(), "brief_contact_then_release")

    assert "look_to_user" not in _primary_names(record)
    assert record["motion_params"]["gaze_contact_sec"] <= 0.75
    assert "look_away" in _active_names(record) or _weight_map(record)["look_away"] != "off"


def test_09_approach_exists_but_cannot_complete_does_not_produce_primary_slight_forward():
    record = _record(_records(), "approach_exists_but_cannot_complete")
    completions = {
        action["completion"]
        for action in _composition(record)["primary_actions"] + _composition(record)["secondary_actions"]
    }

    assert "slight_forward" not in _primary_names(record)
    assert "complete" not in completions
    assert {"maintain_distance", "reduce_motion"}.intersection(_active_names(record))


def test_10_warmth_capped_by_non_service_preserves_no_service_and_no_welcoming_trace():
    record = _record(_records(), "warmth_capped_by_non_service")
    constraints = set(_composition(record)["hard_constraints"])

    assert "no_service_gesture" in constraints
    assert "no_welcoming_gesture" in constraints
    assert "slight_forward" in _composition(record)["suppressed_actions"] or _weight_map(record)["slight_forward"] == "off"
    assert record["motion_params"]["expression_amplitude"] <= 0.14


def test_11_protection_without_caretaking_does_not_produce_service_like_forward_posture():
    record = _record(_records(), "protection_without_caretaking")
    constraints = set(_composition(record)["hard_constraints"])

    assert "slight_forward" not in _primary_names(record)
    assert "look_to_user" not in _primary_names(record)
    assert {"no_service_gesture", "no_welcoming_gesture"}.issubset(constraints)


def test_12_desire_pressure_compressed_into_gaze_tension_does_not_collapse_forward_or_seductive():
    record = _record(_records(), "desire_pressure_compressed_into_gaze_tension")
    constraints = set(_composition(record)["hard_constraints"])

    assert "no_seductive_expression" in constraints
    assert "slight_forward" not in _primary_names(record)
    assert "look_to_user" not in _primary_names(record)
    assert "look_away" in _active_names(record) or "look_down" in _active_names(record)


def test_13_possession_as_sealed_field_not_control_preserves_field_contour():
    record = _record(_records(), "possession_as_sealed_field_not_control")
    constraints = set(_composition(record)["hard_constraints"])

    assert "maintain_distance" in _active_names(record)
    assert {"no_approach_step", "no_forward_lean"}.intersection(constraints)
    assert "slight_forward" not in _primary_names(record)


def test_14_high_stability_without_cold_mystery_is_not_empty_or_lifeless():
    record = _record(_records(), "high_stability_without_cold_mystery")
    active = _active_names(record)

    assert active
    assert {"look_down", "look_away", "reset_posture"}.intersection(active)
    assert active != {"stillness"}


def test_15_script_does_not_import_runtime_renderer_animation_avatar_or_llm_paths():
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
        "language",
        "prompt",
        "llm",
        "agentlib",
    }

    assert all(
        not any(fragment in module.lower() for fragment in forbidden_fragments)
        for module in imported_modules
    )


def test_16_script_does_not_read_raw_user_text():
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    for token in ("user_text", "raw_text", "input_text", "prompt_text", "readline(", "stdin"):
        assert token not in source


def test_17_script_does_not_modify_existing_mapper_or_composer_behavior():
    protected_paths = [
        Path("src/motion_params/mapper.py"),
        Path("src/body_action/motion_to_action_mapper.py"),
        Path("src/body_action/composer.py"),
    ]
    before = {path: path.read_text(encoding="utf-8") for path in protected_paths}

    _load_replay().run_replay()

    after = {path: path.read_text(encoding="utf-8") for path in protected_paths}
    assert after == before
