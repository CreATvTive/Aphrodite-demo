from __future__ import annotations

import pytest

from src.field_state.schema import (
    FieldVariable,
    RelationalFieldState,
    create_ground_state_variables,
)
from src.language_condition import (
    FieldStateToLanguageConditionMapper,
    LanguageConditionVector,
    clamp01,
    is_valid_range,
)
from src.language_condition.schema import (
    _LANGUAGE_CONDITION_PARAM_NAMES,
    _PARAM_TO_SOURCE,
)


# ── Helper ────────────────────────────────────────────────────────────────────

_NUMERIC_TO_BAND: dict = {
    0.00: "low",
    0.05: "low",
    0.10: "low",
    0.30: "low",
    0.35: "baseline",
    0.40: "baseline",
    0.50: "baseline",
    0.55: "elevated",
    0.60: "elevated",
    0.80: "high",
    0.90: "high",
    1.00: "saturated",
    1.50: "saturated",  # clamped — band is just formality
    -0.50: "low",       # clamped
}


def _band_for(value: float) -> str:
    """Return a plausible value-band string for a given numeric value."""
    rounded = round(value, 2)
    if rounded in _NUMERIC_TO_BAND:
        return _NUMERIC_TO_BAND[rounded]
    if value <= 0.0:
        return "low"
    if value <= 0.25:
        return "low"
    if value <= 0.45:
        return "baseline"
    if value <= 0.65:
        return "elevated"
    if value <= 0.85:
        return "high"
    return "saturated"


def _make_field_variable(name: str, numeric_value: float) -> FieldVariable:
    """Create a minimal valid FieldVariable for testing."""
    band = _band_for(numeric_value)
    return FieldVariable(
        name=name,
        value=band,
        numeric_value=numeric_value,
        baseline_value=band,
        baseline_numeric_value=numeric_value,
        decay_profile="medium",
        description=f"test {name}",
        source_note="test fixture",
        behavior_affecting=False,
    )


def _make_field_state(numeric_values: dict) -> RelationalFieldState:
    """Build a RelationalFieldState from a dict of {variable_name: numeric_value}."""
    variables = {
        name: _make_field_variable(name, numeric_values.get(name, 0.0))
        for name in _PARAM_TO_SOURCE.values()
    }
    return RelationalFieldState(variables=variables, state_note="test fixture")


def _ground_state() -> RelationalFieldState:
    """Return the default F_0 ground-state RelationalFieldState."""
    return RelationalFieldState(variables=create_ground_state_variables())


# ── Tests: clamp01 / is_valid_range ──────────────────────────────────────────

class TestClampUtilities:
    def test_clamp01_mid(self):
        assert clamp01(0.5) == 0.5

    def test_clamp01_negative(self):
        assert clamp01(-0.2) == 0.0

    def test_clamp01_above_one(self):
        assert clamp01(1.5) == 1.0

    def test_is_valid_range_true(self):
        assert is_valid_range(0.0) is True
        assert is_valid_range(0.5) is True
        assert is_valid_range(1.0) is True

    def test_is_valid_range_false(self):
        assert is_valid_range(-0.01) is False
        assert is_valid_range(1.01) is False


# ── Test 1: default neutral field state ──────────────────────────────────────

class TestDefaultNeutralFieldState:
    def test_default_neutral_field_state(self):
        """Default / neutral field state produces expected intermediate values;
        warmth is below the clamp cap."""
        field_state = _ground_state()
        lcv = FieldStateToLanguageConditionMapper.map(field_state)

        # Ground-state expected values (identity from RelationalFieldState defaults)
        assert lcv.language_distance_marker == pytest.approx(0.50)
        assert lcv.warmth_tone_modifier == pytest.approx(0.35)  # 0.35 < 0.60 cap
        assert lcv.structural_grip_modifier == pytest.approx(0.05)
        assert lcv.correction_directness == pytest.approx(0.00)
        assert lcv.contamination_filter_strength == pytest.approx(0.40)
        assert lcv.presence_stability_modifier == pytest.approx(0.80)
        assert lcv.withdrawal_expression_bias == pytest.approx(0.10)
        assert lcv.service_suppression_strength == pytest.approx(0.55)
        assert lcv.collaborator_register_bias == pytest.approx(0.05)
        assert lcv.compression_under_contamination == pytest.approx(0.00)

        # Warmth is below the 0.60 cap
        assert lcv.warmth_tone_modifier <= 0.60


# ── Test 2: all-zero field state ─────────────────────────────────────────────

class TestAllZeroFieldState:
    def test_all_zero_field_state(self):
        """All-zero field state produces all-zero vector.
        compression_under_contamination is also 0.0 (identity from contamination_pressure=0)."""
        zeros = {name: 0.0 for name in _PARAM_TO_SOURCE.values()}
        field_state = _make_field_state(zeros)
        lcv = FieldStateToLanguageConditionMapper.map(field_state)

        assert lcv.language_distance_marker == 0.0
        assert lcv.warmth_tone_modifier == 0.0
        assert lcv.structural_grip_modifier == 0.0
        assert lcv.correction_directness == 0.0
        assert lcv.contamination_filter_strength == 0.0
        assert lcv.presence_stability_modifier == 0.0
        assert lcv.withdrawal_expression_bias == 0.0
        assert lcv.service_suppression_strength == 0.0
        assert lcv.collaborator_register_bias == 0.0
        assert lcv.compression_under_contamination == 0.0


# ── Test 3: all-one field state ──────────────────────────────────────────────

class TestAllOneFieldState:
    def test_all_one_field_state(self):
        """All-one field state caps warmth at 0.60; other params are 1.0."""
        ones = {name: 1.0 for name in _PARAM_TO_SOURCE.values()}
        field_state = _make_field_state(ones)
        lcv = FieldStateToLanguageConditionMapper.map(field_state)

        assert lcv.warmth_tone_modifier == pytest.approx(0.60)  # capped
        assert lcv.language_distance_marker == pytest.approx(1.0)
        assert lcv.structural_grip_modifier == pytest.approx(1.0)
        assert lcv.correction_directness == pytest.approx(1.0)
        assert lcv.contamination_filter_strength == pytest.approx(1.0)
        assert lcv.presence_stability_modifier == pytest.approx(1.0)
        assert lcv.withdrawal_expression_bias == pytest.approx(1.0)
        assert lcv.service_suppression_strength == pytest.approx(1.0)
        assert lcv.collaborator_register_bias == pytest.approx(1.0)
        assert lcv.compression_under_contamination == pytest.approx(1.0)


# ── Tests 4-6: warmth cap ────────────────────────────────────────────────────

class TestWarmthCap:
    def test_warmth_cap_applied(self):
        """affective_warmth = 0.90 → warmth_tone_modifier = 0.60."""
        values = _ground_numeric()
        values["affective_warmth"] = 0.90
        field_state = _make_field_state(values)
        lcv = FieldStateToLanguageConditionMapper.map(field_state)
        assert lcv.warmth_tone_modifier == pytest.approx(0.60)

    def test_warmth_cap_not_applied(self):
        """affective_warmth = 0.30 → warmth_tone_modifier = 0.30 (no clamping)."""
        values = _ground_numeric()
        values["affective_warmth"] = 0.30
        field_state = _make_field_state(values)
        lcv = FieldStateToLanguageConditionMapper.map(field_state)
        assert lcv.warmth_tone_modifier == pytest.approx(0.30)

    def test_warmth_cap_boundary(self):
        """affective_warmth = 0.60 → warmth_tone_modifier = 0.60 (boundary value)."""
        values = _ground_numeric()
        values["affective_warmth"] = 0.60
        field_state = _make_field_state(values)
        lcv = FieldStateToLanguageConditionMapper.map(field_state)
        assert lcv.warmth_tone_modifier == pytest.approx(0.60)


# ── Tests 7-9: specific identity mappings ────────────────────────────────────

class TestSpecificIdentityMappings:
    def test_service_resistance_to_suppression(self):
        """service_resistance = 0.55 → service_suppression_strength = 0.55."""
        values = _ground_numeric()
        values["service_resistance"] = 0.55
        field_state = _make_field_state(values)
        lcv = FieldStateToLanguageConditionMapper.map(field_state)
        assert lcv.service_suppression_strength == pytest.approx(0.55)

    def test_contamination_pressure_to_compression(self):
        """contamination_pressure = 0.30 → compression_under_contamination = 0.30."""
        values = _ground_numeric()
        values["contamination_pressure"] = 0.30
        field_state = _make_field_state(values)
        lcv = FieldStateToLanguageConditionMapper.map(field_state)
        assert lcv.compression_under_contamination == pytest.approx(0.30)

    def test_collaborator_layer_to_register_bias(self):
        """collaborator_layer_pressure = 0.05 → collaborator_register_bias = 0.05."""
        values = _ground_numeric()
        values["collaborator_layer_pressure"] = 0.05
        field_state = _make_field_state(values)
        lcv = FieldStateToLanguageConditionMapper.map(field_state)
        assert lcv.collaborator_register_bias == pytest.approx(0.05)


# ── Tests 10-11: out-of-range clamping ───────────────────────────────────────

class TestOutOfRangeClamping:
    def test_out_of_range_negative_clamped(self):
        """Negative values are clamped to 0.0 via clamp01 and from_dict."""
        # clamp01 utility
        assert clamp01(-0.50) == 0.0
        assert clamp01(-9999.0) == 0.0

        # from_dict clamps negative inputs
        d = {name: -0.50 for name in _LANGUAGE_CONDITION_PARAM_NAMES}
        lcv = LanguageConditionVector.from_dict(d)
        for name in _LANGUAGE_CONDITION_PARAM_NAMES:
            assert getattr(lcv, name) == 0.0, f"{name} should be clamped to 0.0"

        # mapper clamps as well (RelationalFieldState already enforces [0,1],
        # so use ground state with valid values — mapper applies clamp01 idempotently)
        field_state = _ground_state()
        lcv2 = FieldStateToLanguageConditionMapper.map(field_state)
        for name in _LANGUAGE_CONDITION_PARAM_NAMES:
            assert 0.0 <= getattr(lcv2, name) <= 1.0

    def test_out_of_range_above_one_clamped(self):
        """Values > 1.0 are clamped to 1.0 (warmth additionally capped at 0.60)."""
        # clamp01 utility
        assert clamp01(1.50) == 1.0
        assert clamp01(9999.0) == 1.0

        # from_dict clamps >1.0 inputs, warmth capped at 0.60
        d = {name: 1.50 for name in _LANGUAGE_CONDITION_PARAM_NAMES}
        lcv = LanguageConditionVector.from_dict(d)
        for name in _LANGUAGE_CONDITION_PARAM_NAMES:
            if name == "warmth_tone_modifier":
                assert getattr(lcv, name) == pytest.approx(0.60), f"{name} should be 0.60"
            else:
                assert getattr(lcv, name) == pytest.approx(1.0), f"{name} should be clamped to 1.0"

        # mapper: RelationalFieldState already validates [0,1], so test idempotency
        field_state = _ground_state()
        lcv2 = FieldStateToLanguageConditionMapper.map(field_state)
        assert lcv2.warmth_tone_modifier <= 0.60
        for name in _LANGUAGE_CONDITION_PARAM_NAMES:
            assert 0.0 <= getattr(lcv2, name) <= 1.0


# ── Test 12: audit trace ─────────────────────────────────────────────────────

class TestAuditTrace:
    def test_audit_trace_present(self):
        """audit_trace() returns a dict with all 10 field variables."""
        field_state = _ground_state()
        trace = FieldStateToLanguageConditionMapper.audit_trace(field_state)

        expected_sources = set(_PARAM_TO_SOURCE.values())
        assert set(trace.keys()) == expected_sources

        for source_name, entry in trace.items():
            assert "value" in entry
            assert "maps_to" in entry
            assert "mapped_value" in entry
            assert "cap_applied" in entry

        # Verify warmth cap not applied for ground state (affective_warmth = 0.35 < 0.60)
        assert trace["affective_warmth"]["cap_applied"] is False

    def test_audit_trace_cap_detected(self):
        """When affective_warmth > 0.60, cap_applied is True."""
        values = _ground_numeric()
        values["affective_warmth"] = 0.90
        field_state = _make_field_state(values)
        trace = FieldStateToLanguageConditionMapper.audit_trace(field_state)
        assert trace["affective_warmth"]["cap_applied"] is True
        assert trace["affective_warmth"]["uncapped_value"] == pytest.approx(0.90)
        assert trace["affective_warmth"]["mapped_value"] == pytest.approx(0.60)


# ── Test 13: no prompt text generated ────────────────────────────────────────

class TestNoPromptTextGenerated:
    def test_no_prompt_text_generated(self):
        """The mapper does not generate any prompt text."""
        field_state = _ground_state()
        lcv = FieldStateToLanguageConditionMapper.map(field_state)

        # LanguageConditionVector contains only floats, no strings
        for name in _LANGUAGE_CONDITION_PARAM_NAMES:
            val = getattr(lcv, name)
            assert isinstance(val, float), f"{name} is not a float: {type(val)}"

        # No .prompt, .text, .system_message, etc. attributes
        assert not hasattr(lcv, "prompt")
        assert not hasattr(lcv, "text")
        assert not hasattr(lcv, "system_message")

        # Mapper itself has no prompt-generating methods
        assert not hasattr(FieldStateToLanguageConditionMapper, "generate_prompt")
        assert not hasattr(FieldStateToLanguageConditionMapper, "build_system_message")


# ── Test 14: no LLM imports in mapper ────────────────────────────────────────

class TestNoLLMImportsInMapper:
    def test_no_llm_imports_in_mapper(self):
        """mapper.py does not import LLM / model / runtime modules."""
        import inspect
        import sys

        # Read the mapper source directly to check imports
        mapper_path = "src/language_condition/mapper.py"
        with open(mapper_path, "r", encoding="utf-8") as f:
            source = f.read()

        forbidden_tokens = [
            "llm",
            "transformers",
            "torch",
            "tensorflow",
            "openai",
            "anthropic",
            "langchain",
            "prompt_template",
            "generate_text",
            "tokenizer",
            "model",
            "runtime_engine",
            "RuntimeEngine",
            "qlora",
            "dpo",
            "lora",
            "peft",
            "finetune",
        ]
        source_lower = source.lower()
        for token in forbidden_tokens:
            assert token not in source_lower, (
                f"mapper.py contains forbidden token: '{token}'"
            )


# ── Test 15: ABST dimensions are not inputs ──────────────────────────────────

class TestAbstDimensionsNotInputs:
    def test_abst_dimensions_not_inputs(self):
        """ABST dimension names (e.g. 'salience_focus', 'non_service_posture')
        do not appear in the schema or mapper source."""
        import os

        abst_terms = [
            "salience_focus",
            "non_service_posture",
            "intimacy_resistance",
            "containment_expression",
            "silence_tolerance",
            "abst",
        ]

        files_to_check = [
            "src/language_condition/schema.py",
            "src/language_condition/mapper.py",
        ]

        for filepath in files_to_check:
            with open(filepath, "r", encoding="utf-8") as f:
                source = f.read()
            source_lower = source.lower()
            for term in abst_terms:
                assert term.lower() not in source_lower, (
                    f"'{term}' found in {filepath} — ABST dimensions must not appear"
                )


# ── Test 16: MAPPING_TABLE completeness ──────────────────────────────────────

class TestMappingTableCompleteness:
    def test_mapping_table_completeness(self):
        """MAPPING_TABLE contains all 10 params with their source field variables."""
        table = LanguageConditionVector.MAPPING_TABLE
        assert len(table) == 10

        params_in_table = {entry["param"] for entry in table}
        assert params_in_table == set(_LANGUAGE_CONDITION_PARAM_NAMES)

        sources_in_table = {entry["source_field"] for entry in table}
        assert sources_in_table == set(_PARAM_TO_SOURCE.values())

        # Verify each entry maps correctly (param index → source)
        for idx, param_name in enumerate(_LANGUAGE_CONDITION_PARAM_NAMES):
            entry = table[idx]
            assert entry["param"] == param_name
            assert entry["source_field"] == _PARAM_TO_SOURCE[idx]


# ── Test 17: from_dict / to_dict roundtrip ───────────────────────────────────

class TestFromDictToDictRoundtrip:
    def test_from_dict_to_dict_roundtrip(self):
        """to_dict() → from_dict() produces an equivalent LanguageConditionVector."""
        original = LanguageConditionVector(
            language_distance_marker=0.50,
            warmth_tone_modifier=0.35,
            structural_grip_modifier=0.05,
            correction_directness=0.00,
            contamination_filter_strength=0.40,
            presence_stability_modifier=0.80,
            withdrawal_expression_bias=0.10,
            service_suppression_strength=0.55,
            collaborator_register_bias=0.05,
            compression_under_contamination=0.00,
        )

        d = original.to_dict()
        reconstructed = LanguageConditionVector.from_dict(d)

        for name in _LANGUAGE_CONDITION_PARAM_NAMES:
            assert getattr(reconstructed, name) == pytest.approx(getattr(original, name))

    def test_from_dict_clamps(self):
        """from_dict clamps out-of-range values."""
        d = {name: 1.5 for name in _LANGUAGE_CONDITION_PARAM_NAMES}
        lcv = LanguageConditionVector.from_dict(d)
        # warmth is additionally capped at 0.60
        assert lcv.warmth_tone_modifier == pytest.approx(0.60)
        for name in _LANGUAGE_CONDITION_PARAM_NAMES:
            if name == "warmth_tone_modifier":
                continue
            assert getattr(lcv, name) == pytest.approx(1.0)


# ── Test 18: behavior_affecting flag ─────────────────────────────────────────

class TestBehaviorAffectingFlag:
    def test_behavior_affecting_flag(self):
        """behavior_affecting is False on LanguageConditionVector."""
        assert LanguageConditionVector.behavior_affecting is False

        # Instance also reflects the class attribute
        lcv = LanguageConditionVector()
        assert lcv.behavior_affecting is False

    def test_mapper_does_not_set_behavior_affecting(self):
        """Mapper output respects behavior_affecting=False."""
        field_state = _ground_state()
        lcv = FieldStateToLanguageConditionMapper.map(field_state)
        assert lcv.behavior_affecting is False


# ── Additional: to_tuple and repr ────────────────────────────────────────────

class TestSerialisation:
    def test_to_tuple(self):
        lcv = LanguageConditionVector()
        t = lcv.to_tuple()
        assert len(t) == 10
        assert all(isinstance(v, float) for v in t)

    def test_repr_format(self):
        lcv = LanguageConditionVector()
        r = repr(lcv)
        assert r.startswith("LanguageConditionVector(")
        assert r.endswith(")")
        for name in _LANGUAGE_CONDITION_PARAM_NAMES:
            assert name in r


# ── Helper: ground numeric values dict ───────────────────────────────────────

def _ground_numeric() -> dict:
    """Return the ground-state numeric values as a plain dict."""
    gs = create_ground_state_variables()
    return {name: float(var.numeric_value) for name, var in gs.items()}
