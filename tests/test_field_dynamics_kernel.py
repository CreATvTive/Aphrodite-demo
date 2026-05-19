from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest

from src.field_dynamics import (
    FieldDynamicsConfig,
    FieldDynamicsInput,
    FieldDynamicsOutput,
    FieldDynamicsState,
    RelationalFieldDynamicsKernel,
)


REQUIRED_METRIC_KEYS = {
    "pre_conflict_tension",
    "post_conflict_tension",
    "kinetic_energy",
    "overshoot_magnitude",
    "max_abs_velocity",
    "max_abs_acceleration",
}

FIELD_DYNAMICS_SOURCE_FILES = (
    Path("src/field_dynamics/__init__.py"),
    Path("src/field_dynamics/schema.py"),
    Path("src/field_dynamics/kernel.py"),
)


def _config(**overrides) -> FieldDynamicsConfig:
    values = {
        "M": np.ones(10),
        "C": 0.4 * np.ones(10),
        "K": 0.8 * np.ones(10),
        "B": 0.2 * np.ones(10),
        "dt_max": 0.05,
        "V_max": 2.0,
        "A_max": 5.0,
        "overshoot_max": 0.1 * np.ones(10),
    }
    values.update(overrides)
    return FieldDynamicsConfig(**values)


def _state(F_tilde=None, V=None) -> FieldDynamicsState:
    return FieldDynamicsState(
        F_tilde=np.array(F_tilde if F_tilde is not None else 0.2 * np.ones(10), dtype=float),
        V=np.array(V if V is not None else np.zeros(10), dtype=float),
    )


def _input(U_t=None, dt: float = 0.05) -> FieldDynamicsInput:
    return FieldDynamicsInput(
        U_t=np.array(U_t if U_t is not None else np.zeros(10), dtype=float),
        dt=dt,
    )


def _kernel(config: FieldDynamicsConfig | None = None, state: FieldDynamicsState | None = None):
    return RelationalFieldDynamicsKernel(config or _config(), state or _state())


def test_01_valid_config_passes():
    config = _config()

    config.validate()
    assert config.M.shape == (10,)
    assert config.overshoot_max.shape == (10,)


def test_02_invalid_config_shape_raises_value_error():
    with pytest.raises(ValueError):
        _config(M=np.ones(9))


def test_03_m_less_than_or_equal_zero_raises_value_error():
    values = np.ones(10)
    values[0] = 0.0

    with pytest.raises(ValueError):
        _config(M=values)


def test_04_c_less_than_zero_raises_value_error():
    values = 0.4 * np.ones(10)
    values[0] = -0.1

    with pytest.raises(ValueError):
        _config(C=values)


def test_05_k_less_than_zero_raises_value_error():
    values = 0.8 * np.ones(10)
    values[0] = -0.1

    with pytest.raises(ValueError):
        _config(K=values)


def test_06_b_outside_unit_interval_raises_value_error():
    values = 0.2 * np.ones(10)
    values[0] = 1.2

    with pytest.raises(ValueError):
        _config(B=values)


def test_07_nan_or_inf_in_arrays_raises_value_error():
    nan_values = np.ones(10)
    nan_values[0] = np.nan
    inf_values = np.ones(10)
    inf_values[0] = np.inf

    with pytest.raises(ValueError):
        _config(M=nan_values)
    with pytest.raises(ValueError):
        _config(B=inf_values)


def test_08_nonpositive_dt_v_a_limits_raise_value_error():
    with pytest.raises(ValueError):
        _config(dt_max=0.0)
    with pytest.raises(ValueError):
        _config(V_max=-1.0)
    with pytest.raises(ValueError):
        _config(A_max=0.0)


def test_09_overshoot_max_scalar_or_shape10_is_accepted_and_negative_raises():
    scalar_config = _config(overshoot_max=0.2)
    array_config = _config(overshoot_max=0.2 * np.ones(10))

    assert scalar_config.overshoot_max.shape == (10,)
    assert np.allclose(scalar_config.overshoot_max, 0.2)
    assert array_config.overshoot_max.shape == (10,)
    with pytest.raises(ValueError):
        _config(overshoot_max=-0.1)


def test_10_invalid_state_shape_raises_value_error():
    with pytest.raises(ValueError):
        FieldDynamicsState(F_tilde=np.zeros(9), V=np.zeros(10))


def test_11_invalid_input_u_shape_raises_value_error():
    with pytest.raises(ValueError):
        FieldDynamicsInput(U_t=np.zeros(9), dt=0.05)


def test_12_dt_less_than_or_equal_zero_raises_value_error():
    with pytest.raises(ValueError):
        FieldDynamicsInput(U_t=np.zeros(10), dt=0.0)
    with pytest.raises(ValueError):
        FieldDynamicsInput(U_t=np.zeros(10), dt=-0.1)


def test_13_dt_greater_than_dt_max_produces_substeps_in_trace():
    kernel = _kernel()
    output = kernel.step(_input(dt=0.20))

    assert output.trace["num_substeps"] > 1
    assert output.trace["effective_dt"] <= kernel.config.dt_max


def test_14_large_dt_substeps_close_to_repeated_smaller_steps():
    config = _config()
    U_t = np.zeros(10)
    U_t[1] = 0.5
    large_kernel = _kernel(config=config, state=_state())
    repeated_kernel = _kernel(config=config, state=_state())

    large_output = large_kernel.step(FieldDynamicsInput(U_t=U_t, dt=0.20))
    for _ in range(4):
        repeated_output = repeated_kernel.step(FieldDynamicsInput(U_t=U_t, dt=0.05))

    assert np.allclose(large_output.F_bounded, repeated_output.F_bounded)
    assert np.allclose(large_output.V, repeated_output.V)


def test_15_no_input_relaxes_smoothly_toward_baseline():
    config = _config(B=0.2 * np.ones(10))
    kernel = _kernel(config=config, state=_state(F_tilde=0.8 * np.ones(10)))
    initial_distance = np.linalg.norm(kernel.state.F_tilde - config.B)

    for _ in range(20):
        output = kernel.step(_input(dt=0.05))

    final_distance = np.linalg.norm(output.F_bounded - config.B)
    assert final_distance < initial_distance


def test_16_relaxation_does_not_instantly_jump_to_baseline():
    config = _config(B=0.2 * np.ones(10))
    kernel = _kernel(config=config, state=_state(F_tilde=0.8 * np.ones(10)))

    output = kernel.step(_input(dt=0.05))

    assert not np.allclose(output.F_bounded, config.B)


def test_17_impulse_produces_velocity_mediated_delayed_movement():
    U_t = np.zeros(10)
    U_t[1] = 1.0
    kernel = _kernel()

    output = kernel.step(FieldDynamicsInput(U_t=U_t, dt=0.05))

    assert output.V[1] != 0.0
    assert output.trace["F_tilde"][1] != pytest.approx(0.2)
    assert output.F_bounded[1] != pytest.approx(U_t[1])


def test_18_extreme_input_respects_a_max():
    config = _config(A_max=1.5)
    U_t = 100.0 * np.ones(10)
    output = _kernel(config=config).step(FieldDynamicsInput(U_t=U_t, dt=0.01))

    assert np.max(np.abs(output.A)) <= config.A_max


def test_19_extreme_input_respects_v_max():
    config = _config(V_max=0.3)
    U_t = 100.0 * np.ones(10)
    kernel = _kernel(config=config)

    for _ in range(10):
        output = kernel.step(FieldDynamicsInput(U_t=U_t, dt=0.05))

    assert np.max(np.abs(output.V)) <= config.V_max


def test_20_f_bounded_is_always_in_unit_interval():
    config = _config(V_max=10.0)
    kernel = _kernel(config=config, state=_state(F_tilde=0.95 * np.ones(10), V=10.0 * np.ones(10)))
    output = kernel.step(FieldDynamicsInput(U_t=100.0 * np.ones(10), dt=0.20))

    assert np.all(output.F_bounded >= 0.0)
    assert np.all(output.F_bounded <= 1.0)


def test_21_internal_overshoot_beyond_unit_interval_is_capped():
    config = _config(C=np.zeros(10), K=np.zeros(10), V_max=10.0, A_max=5.0, overshoot_max=0.05)
    kernel = _kernel(config=config, state=_state(F_tilde=np.ones(10), V=10.0 * np.ones(10)))

    kernel.step(FieldDynamicsInput(U_t=100.0 * np.ones(10), dt=0.05))

    assert np.all(kernel.state.F_tilde <= 1.05)
    assert np.all(kernel.state.F_tilde >= -0.05)


def test_22_output_includes_all_required_tension_metric_keys():
    output = _kernel().step(_input())

    assert set(output.tension_metrics) == REQUIRED_METRIC_KEYS


def test_23_kinetic_energy_is_nonnegative():
    U_t = np.zeros(10)
    U_t[1] = 1.0
    output = _kernel().step(FieldDynamicsInput(U_t=U_t, dt=0.05))

    assert output.tension_metrics["kinetic_energy"] >= 0.0


def test_24_velocity_and_acceleration_metrics_are_within_caps():
    config = _config(V_max=0.4, A_max=1.2)
    output = _kernel(config=config).step(FieldDynamicsInput(U_t=100.0 * np.ones(10), dt=0.10))

    assert output.tension_metrics["max_abs_velocity"] <= config.V_max
    assert output.tension_metrics["max_abs_acceleration"] <= config.A_max


def test_25_output_arrays_are_copies_not_kernel_internal_references():
    kernel = _kernel()
    output = kernel.step(_input())

    output.F_bounded[0] = 999.0
    output.V[0] = 999.0
    output.A[0] = 999.0
    output.trace["F_tilde"][0] = 999.0

    assert kernel.state.F_tilde[0] != 999.0
    assert kernel.state.V[0] != 999.0


def test_26_constructor_copies_config_and_state_arrays():
    config = _config()
    state = _state()
    kernel = RelationalFieldDynamicsKernel(config, state)

    config.M[0] = 999.0
    config.B[0] = 999.0
    state.F_tilde[0] = 999.0
    state.V[0] = 999.0

    assert kernel.config.M[0] != 999.0
    assert kernel.config.B[0] != 999.0
    assert kernel.state.F_tilde[0] != 999.0
    assert kernel.state.V[0] != 999.0


def test_27_diagonal_dynamics_do_not_directly_change_boundary_from_warmth_force():
    kernel = _kernel()
    U_t = np.zeros(10)
    U_t[1] = 2.0
    output = kernel.step(FieldDynamicsInput(U_t=U_t, dt=0.05))

    assert output.trace["F_tilde"][0] == pytest.approx(0.2)
    assert output.trace["F_tilde"][1] > 0.2


def test_28_no_forbidden_imports_in_field_dynamics_modules():
    forbidden = (
        "agentlib",
        "runtime_engine",
        "field_trace",
        "motion_params",
        "body_action",
        "llm",
        "prompt",
        "language",
        "renderer",
        "animation",
        "avatar",
        "memory",
        "router",
        "persona",
        "re",
        "regex",
    )
    for path in FIELD_DYNAMICS_SOURCE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        for module in imported:
            lowered = module.lower()
            for token in forbidden:
                if token == "re":
                    assert lowered != "re", f"{path} must not import re"
                else:
                    assert token not in lowered, f"{path} must not import {token}: {module}"


def test_29_trace_uses_numpy_arrays_for_numeric_vectors_and_plain_scalars_for_metadata():
    output = _kernel().step(_input())

    assert isinstance(output, FieldDynamicsOutput)
    assert isinstance(output.trace["U_t"], np.ndarray)
    assert isinstance(output.trace["F_tilde"], np.ndarray)
    assert isinstance(output.trace["F_bounded"], np.ndarray)
    assert isinstance(output.trace["A"], np.ndarray)
    assert isinstance(output.trace["original_dt"], float)
    assert isinstance(output.trace["effective_dt"], float)
    assert isinstance(output.trace["num_substeps"], int)
    assert output.trace["integration_method"] == "semi_implicit_euler"
    assert output.trace["overshoot_tracking"] == "last_substep"
