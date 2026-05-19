from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import pytest

import src.body_action as body_action
from src.body_action import (
    ACTION_PRIMITIVES,
    COMPLETION_MODES,
    DURATION_HINTS,
    WEIGHT_BANDS,
    ActionSequenceHint,
    BodyActionComposer,
    BodyActionComposition,
    BodyActionPolicy,
    BodyActionWeight,
    BodyActionWeights,
    MotionToActionMapper,
    POLLUTION_BARRIER_NAMES,
)


BODY_ACTION_SOURCE_FILES = (
    Path("src/body_action/schema.py"),
    Path("src/body_action/__init__.py"),
)


def _source_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in BODY_ACTION_SOURCE_FILES
    )


def _import_lines() -> list[tuple[Path, str]]:
    lines: list[tuple[Path, str]] = []
    for path in BODY_ACTION_SOURCE_FILES:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                lines.append((path, stripped))
    return lines


def _weight(action_name: str = "pause", weight: str = "low") -> BodyActionWeight:
    return BodyActionWeight(
        action_name=action_name,
        weight=weight,
        rationale="declarative guardrail weight",
        constraints=["display_only"],
        provenance=["guardrail_test"],
    )


def _hint(action_name: str = "pause", order: int = 0) -> ActionSequenceHint:
    return ActionSequenceHint(
        action_name=action_name,
        order=order,
        duration_hint="short",
        completion="restrained",
        constraints=["display_only"],
        provenance=["guardrail_test"],
    )


def test_body_action_source_import_guardrail():
    forbidden_import_terms = (
        "agentlib.runtime_engine",
        "runtime_engine",
        "src.interpreter",
        "inputinterpreter",
        "input_interpreter",
        "src.field_trace",
        "field_trace",
        "src.body_state.mapper",
        "body_state.mapper",
        "router",
        "memory",
        "persona",
        "renderer",
        "animation",
        "avatar",
        "llm",
        "client",
        "provider",
        "openai",
        "anthropic",
    )
    for path, line in _import_lines():
        lowered = line.lower()
        for term in forbidden_import_terms:
            assert term not in lowered, f"{path} must not import {term}: {line}"


def test_body_action_source_has_no_raw_user_input_or_regex_parsing():
    source = _source_text()
    forbidden_tokens = (
        "raw_text",
        "user_text",
        "user_input_summary",
        "re.search",
        "re.match",
        "regex",
    )
    for token in forbidden_tokens:
        assert token not in source


def test_body_action_behavior_affecting_defaults_false_and_serializes_false():
    weight = _weight()
    weights = BodyActionWeights(
        weights=[weight],
        source_trace_id="trace-1",
        source_proposals=["proposal-1"],
        body_note="declarative weights only",
    )
    hint = _hint()
    composition = BodyActionComposition(
        primary_actions=[hint],
        secondary_actions=[_hint("look_down", order=1)],
        suppressed_actions=["look_away"],
        hard_constraints=["display_only"],
        source_weights=[weight],
        composition_note="declarative composition only",
    )

    for item in (weight, weights, hint, composition):
        payload = item.to_dict()
        assert item.behavior_affecting is False
        assert payload["behavior_affecting"] is False
        assert json.loads(json.dumps(payload)) == payload


def test_body_action_rejects_behavior_affecting_true():
    with pytest.raises(ValueError):
        BodyActionWeight(action_name="pause", weight="low", behavior_affecting=True)
    with pytest.raises(ValueError):
        BodyActionWeights(behavior_affecting=True)
    with pytest.raises(ValueError):
        ActionSequenceHint(
            action_name="pause",
            order=0,
            duration_hint="short",
            completion="restrained",
            behavior_affecting=True,
        )
    with pytest.raises(ValueError):
        BodyActionComposition(behavior_affecting=True)


def test_body_action_rejects_numeric_fake_precision():
    for numeric_weight in (0.0, 0.7, 1.0):
        with pytest.raises(ValueError):
            BodyActionWeight(
                action_name="pause",
                weight=numeric_weight,
                rationale="numeric precision is not approved",
            )
    assert isinstance(WEIGHT_BANDS, frozenset)
    assert WEIGHT_BANDS == frozenset({"off", "low", "medium", "high"})
    assert all(isinstance(weight, str) for weight in WEIGHT_BANDS)


def test_body_action_source_has_no_animation_or_runtime_execution_calls():
    source = _source_text()
    forbidden_execution_tokens = (
        "animate(",
        ".animate(",
        "render(",
        ".render(",
        "drive(",
        ".drive(",
        "move(",
        ".move(",
        "execute(",
        ".execute(",
        "animation",
        "renderer",
        "avatar",
    )
    for token in forbidden_execution_tokens:
        assert token not in source


def test_body_action_composition_is_declarative_only():
    composition_fields = {field.name for field in fields(BodyActionComposition)}
    assert composition_fields == {
        "primary_actions",
        "secondary_actions",
        "suppressed_actions",
        "hard_constraints",
        "source_weights",
        "composition_note",
        "behavior_affecting",
    }

    payload = BodyActionComposition(
        primary_actions=[_hint("pause", order=0)],
        secondary_actions=[_hint("look_to_user", order=1)],
        suppressed_actions=["look_away"],
        source_weights=[_weight("pause", "medium")],
        composition_note="declarative hints only",
    ).to_dict()
    assert set(payload) == composition_fields
    assert payload["primary_actions"][0]["duration_hint"] == "short"
    assert payload["primary_actions"][0]["completion"] == "restrained"
    assert payload["behavior_affecting"] is False


def test_body_action_approved_sets_are_frozen():
    expected_action_primitives = {
        "pause",
        "stillness",
        "look_down",
        "look_to_user",
        "look_away",
        "slight_forward",
        "slight_withdraw",
        "maintain_distance",
        "reduce_motion",
        "reset_posture",
    }
    expected_weight_bands = {"off", "low", "medium", "high"}
    expected_duration_hints = {"instant", "short", "medium", "sustained"}
    expected_completions = {"partial", "restrained", "complete"}

    assert isinstance(ACTION_PRIMITIVES, frozenset)
    assert ACTION_PRIMITIVES == frozenset(expected_action_primitives)
    assert isinstance(WEIGHT_BANDS, frozenset)
    assert WEIGHT_BANDS == frozenset(expected_weight_bands)
    assert isinstance(DURATION_HINTS, frozenset)
    assert DURATION_HINTS == frozenset(expected_duration_hints)
    assert isinstance(COMPLETION_MODES, frozenset)
    assert COMPLETION_MODES == frozenset(expected_completions)


def test_body_action_public_exports_are_stable_and_intentional():
    expected_exports = {
        "ACTION_PRIMITIVES",
        "COMPLETION_MODES",
        "DURATION_HINTS",
        "WEIGHT_BANDS",
        "ActionSequenceHint",
        "BodyActionComposition",
        "BodyActionComposer",
        "BodyActionPolicy",
        "BodyActionWeight",
        "BodyActionWeights",
        "MotionToActionMapper",
        "POLLUTION_BARRIER_NAMES",
    }

    assert set(body_action.__all__) == expected_exports
    for name in expected_exports:
        assert hasattr(body_action, name)

    for private_name in (
        "_drive_to_band",
        "_compute_drives",
        "_compute_raw_drives",
        "_apply_gates",
        "_apply_hard_constraints",
        "_resolve_gaze_competition",
        "_build_weights",
        "_has_pollution_barrier",
    ):
        assert private_name not in body_action.__all__
        assert not hasattr(body_action, private_name)

    assert BodyActionComposer is body_action.BodyActionComposer
    assert BodyActionPolicy is body_action.BodyActionPolicy
    assert MotionToActionMapper is body_action.MotionToActionMapper
    assert POLLUTION_BARRIER_NAMES is body_action.POLLUTION_BARRIER_NAMES


def test_importing_body_action_package_does_not_load_forbidden_runtime_paths():
    code = r'''
import json
import sys

import src.body_action  # noqa: F401

forbidden = (
    "agentlib.runtime_engine",
    "runtime_engine",
    "renderer",
    "animation",
    "avatar",
    "llm",
    "openai",
    "anthropic",
    "field_trace",
    "field_state",
)
loaded = [
    module
    for module in sys.modules
    if any(fragment in module.lower() for fragment in forbidden)
]
print(json.dumps(loaded))
'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == []
