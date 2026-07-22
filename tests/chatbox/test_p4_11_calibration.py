"""Frozen independent P4.11 calibration corpus checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys

from app.chatbox.field_dynamics import DimensionRegistration
from app.chatbox.field_runtime import RegistryProxy
from app.chatbox.soak_detection import SoakState, StreamingSoakDetector


CALIBRATION = Path(__file__).with_name("calibration")
CORPUS = CALIBRATION / "p4_task11_corpus.json"


def _registration(dim_id: str) -> DimensionRegistration:
    return DimensionRegistration(
        dim_id=dim_id, temporary_name=dim_id, birth_time=1.0, strength=1.0,
        trigger_count=0, birth_bias=0.0, fast_e_fold_s=600.0,
        ou_correlation_e_fold_s=10_800.0, ou_acceleration_sigma=4e-7,
        soft_boundary_start=1.0, soft_boundary_width=0.25,
        soft_boundary_strength=(1 / 120) ** 2,
    )


def test_generator_is_byte_deterministic_independent_and_inventory_complete(tmp_path) -> None:
    output = tmp_path / "corpus.json"
    checksum = tmp_path / "corpus.sha256"
    subprocess.run([
        sys.executable, str(CALIBRATION / "generate_p4_task11_corpus.py"),
        "--output", str(output), "--sha256-output", str(checksum),
    ], check=True)
    assert output.read_bytes() == CORPUS.read_bytes()
    digest = hashlib.sha256(CORPUS.read_bytes()).hexdigest()
    assert checksum.read_text("ascii").split()[0] == digest
    assert (CALIBRATION / "p4_task11_corpus.sha256").read_text("ascii").split()[0] == digest
    source = (CALIBRATION / "generate_p4_task11_corpus.py").read_text("utf-8")
    assert "soak_detection" not in source
    artifact = json.loads(CORPUS.read_text("utf-8"))
    assert artifact["case_order"] == [case["case_id"] for case in artifact["cases"]]
    assert all(case["duration_minutes"] >= 1440 for case in artifact["cases"] if case["label"] == "positive")
    assert all(sum(case["kind"] == kind for case in artifact["cases"]) >= 3 for kind in {
        "exact_freeze", "near_freeze", "sinusoid", "square", "triangle",
        "nominal_ou_spring", "critical_step", "slow_drift", "isolated_spike",
        "short_burst", "single_low_window", "white_noise", "colored_noise", "dimension_reorder",
    })


def test_frozen_corpus_recall_and_negatives() -> None:
    artifact = json.loads(CORPUS.read_text("utf-8"))
    detected: dict[str, list[bool]] = {"freeze": [], "periodic": []}
    false_positives: list[str] = []
    for case in artifact["cases"]:
        count = case["dimension_count"]
        target = case["expected_dim_id"]
        registry = RegistryProxy(tuple(_registration(f"dim-{index}") for index in range(count)))
        # Other dimensions are independent seeded white traces: available and
        # high variance, but not a second periodic detector target.
        series = {}
        for index in range(count):
            rng = random.Random(0x411000 + index)
            series[f"dim-{index}"] = [rng.gauss(0.0, 0.03) for _ in case["block_means"]]
        series[target] = case["block_means"]
        detector = StreamingSoakDetector(registry)
        detector.ingest_block_means(series)
        failed = detector.state is SoakState.FAIL
        if case["label"] == "positive":
            detected[case["family"]].append(failed)
        elif failed:
            false_positives.append(case["case_id"])
    assert detected["freeze"] and all(detected["freeze"]), [i for i, ok in enumerate(detected["freeze"]) if not ok]
    assert detected["periodic"] and all(detected["periodic"]), [i for i, ok in enumerate(detected["periodic"]) if not ok]
    assert all(detected["freeze"] + detected["periodic"])
    assert false_positives == []
