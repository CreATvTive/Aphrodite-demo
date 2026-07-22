"""Regression tests for the offline local-hot-path evidence tool."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).with_name("benchmark_local_hot_paths.py")
SPEC = importlib.util.spec_from_file_location("chatbox_hotpath_benchmark", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BENCHMARK = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BENCHMARK
SPEC.loader.exec_module(BENCHMARK)


def test_summary_requires_identical_outputs_and_reports_cpu_mad() -> None:
    samples = [
        {
            "wall_ns": wall,
            "cpu_ns": cpu,
            "artifact_bytes": 0,
            "output_checksum": "same",
        }
        for wall, cpu in ((100, 80), (110, 100), (200, 120))
    ]
    summary = BENCHMARK._summarize(samples)
    assert summary["wall_median_seconds"] == 110 / 1_000_000_000
    assert summary["wall_mad_seconds"] == 10 / 1_000_000_000
    assert summary["cpu_median_seconds"] == 100 / 1_000_000_000
    assert summary["cpu_mad_seconds"] == 20 / 1_000_000_000
    assert summary["output_validation"] == "identical_across_samples"

    samples[-1]["output_checksum"] = "different"
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        BENCHMARK._summarize(samples)


def test_dynamic_registry_and_static_probe_are_deterministic(tmp_path) -> None:
    registry = BENCHMARK._registry(257)
    assert registry.length == 257
    assert len(set(registry.dim_ids)) == 257

    result = BENCHMARK._static_check(tmp_path)
    assert result["syntax_compile"] == "passed"
    assert result["import_smoke"] == "passed_with_PYTHONDONTWRITEBYTECODE=1"
    assert result["invalid_probe"] == "rejected"
    assert "consumes the next token" in result["malformed_command_root_cause"]


def test_field_core_same_input_has_same_binary64_checksum(tmp_path) -> None:
    registry = BENCHMARK._registry(6)
    first = BENCHMARK._field_core_trial(tmp_path, registry, 20)
    second = BENCHMARK._field_core_trial(tmp_path, registry, 20)
    assert first["output_checksum"] == second["output_checksum"]
    assert first["artifact_bytes"] == second["artifact_bytes"] == 0
