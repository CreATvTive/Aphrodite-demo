"""Generate the frozen, detector-independent P4.11 calibration corpus.

The artifact contains one-minute block-mean traces.  It deliberately does not
import the runtime detector, copy detector thresholds, or solve amplitudes from
acceptance cut-offs.  All signals use fixed physical amplitudes/formulas and a
fixed master seed.  Re-running this file is byte deterministic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import random


SCHEMA = "aphrodite.chatbox.p4-task11-calibration/1"
GENERATOR_VERSION = "p4-task11-corpus-generator/1"
MASTER_SEED = 0xA4112026
MINUTES = 1800
ANOMALY_START_MINUTE = 137
ANOMALY_DURATION_MINUTES = MINUTES - ANOMALY_START_MINUTE


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _normal(rng: random.Random, sigma: float) -> float:
    return rng.gauss(0.0, sigma)


def _nominal(rng: random.Random) -> list[float]:
    """Stable OU-like minute means with an independent weak restoring term."""
    x = 0.0
    velocity = 0.0
    result: list[float] = []
    for _ in range(MINUTES):
        velocity = 0.82 * velocity - 0.018 * x + _normal(rng, 0.006)
        x += velocity
        result.append(x)
    return result


def _triangle(phase: float) -> float:
    return 2.0 * abs(2.0 * (phase - math.floor(phase + 0.5))) - 1.0


def _positive_trace(kind: str, seed: int, **params: float | int | str) -> list[float]:
    rng = random.Random(seed)
    values = _nominal(rng)
    phase = rng.random()
    for minute in range(ANOMALY_START_MINUTE, MINUTES):
        t = minute - ANOMALY_START_MINUTE
        if kind == "exact_freeze":
            value = float(params["level"])
        elif kind == "near_freeze":
            # Fixed 5e-5 physical amplitude; variance is recorded, not targeted
            # from the detector threshold.  The long wave prevents bit equality.
            value = float(params["level"]) + 5.0e-5 * math.sin(2.0 * math.pi * (t / 173.0 + phase))
        else:
            period = int(params["period_minutes"])
            amplitude = float(params["amplitude"])
            p = t / period + phase
            if kind == "sinusoid":
                carrier = math.sin(2.0 * math.pi * p)
            elif kind == "square":
                carrier = 1.0 if (p % 1.0) < 0.5 else -1.0
            elif kind == "triangle":
                carrier = _triangle(p)
            else:
                raise AssertionError(kind)
            value = amplitude * carrier + _normal(rng, float(params["noise_sigma"]))
        values[minute] = value
    return values


def _negative_trace(kind: str, seed: int) -> list[float]:
    rng = random.Random(seed)
    if kind == "nominal_ou_spring":
        return _nominal(rng)
    if kind == "critical_step":
        # Critically damped monotone settling, with ordinary measurement noise.
        return [0.7 * (1.0 - (1.0 + t / 90.0) * math.exp(-t / 90.0)) + _normal(rng, 0.004) for t in range(MINUTES)]
    if kind == "slow_drift":
        x = 0.0
        out = []
        for _ in range(MINUTES):
            x = 0.997 * x + _normal(rng, 0.004)
            out.append(x)
        return out
    if kind == "isolated_spike":
        out = _nominal(rng)
        out[713] += 1.0
        return out
    if kind == "short_burst":
        out = _nominal(rng)
        phase = rng.random()
        for t in range(571, 571 + 240):
            out[t] += 0.25 * math.sin(2.0 * math.pi * (t / 45.0 + phase))
        return out
    if kind == "single_low_window":
        out = _nominal(rng)
        # Window at 360 is low, adjacent windows retain normal portions.
        for t in range(360, 1080):
            out[t] = 0.13 + 4.0e-5 * math.sin(2.0 * math.pi * t / 173.0)
        return out
    if kind == "white_noise":
        return [_normal(rng, 0.12) for _ in range(MINUTES)]
    if kind == "colored_noise":
        x = 0.0
        out = []
        for _ in range(MINUTES):
            x = 0.72 * x + _normal(rng, 0.08)
            out.append(x)
        return out
    if kind == "dimension_reorder":
        return _nominal(rng)
    raise AssertionError(kind)


def _placement(index: int) -> tuple[int, str, int]:
    options = ((1, "first", 0), (12, "middle", 6), (17, "last", 16))
    return options[index % len(options)]


def _case(case_id: str, *, label: str, family: str, kind: str, seed: int, index: int,
          params: dict[str, float | int | str] | None = None) -> dict[str, object]:
    dimension_count, position, ordinal = _placement(index)
    settings = dict(params or {})
    if label == "positive":
        values = _positive_trace(kind, seed, **settings)
    else:
        values = _negative_trace(kind, seed)
    return {
        "case_id": case_id,
        "label": label,
        "family": family,
        "kind": kind,
        "seed": seed,
        "dimension_count": dimension_count,
        "anomalous_position": position,
        "anomalous_ordinal": ordinal,
        "expected_dim_id": f"dim-{ordinal}",
        "start_offset_minutes": ANOMALY_START_MINUTE if label == "positive" else None,
        "duration_minutes": ANOMALY_DURATION_MINUTES if label == "positive" else MINUTES,
        "parameters": settings,
        "block_means": values,
    }


def build() -> dict[str, object]:
    cases: list[dict[str, object]] = []
    index = 0

    def add_positive(kind: str, family: str, variants: list[dict[str, float | int | str]]) -> None:
        nonlocal index
        for seed_offset, params in enumerate(variants):
            seed = MASTER_SEED + 1000 + index * 17 + seed_offset
            case_id = f"positive-{kind}-{params.get('period_minutes', 'base')}-{seed_offset}"
            cases.append(_case(case_id, label="positive", family=family, kind=kind,
                               seed=seed, index=index, params=params))
            index += 1

    add_positive("exact_freeze", "freeze", [{"level": v} for v in (-0.21, 0.07, 0.33)])
    add_positive("near_freeze", "freeze", [{"level": v} for v in (-0.18, 0.11, 0.29)])
    for period in (30, 60, 120):
        add_positive("sinusoid", "periodic", [
            {"period_minutes": period, "amplitude": 0.18 + 0.02 * i, "noise_sigma": 0.012}
            for i in range(3)
        ])
    for shape in ("square", "triangle"):
        for period in (45, 90):
            add_positive(shape, "periodic", [
                {"period_minutes": period, "amplitude": 0.16 + 0.02 * i, "noise_sigma": 0.01}
                for i in range(3)
            ])

    for kind in (
        "nominal_ou_spring", "critical_step", "slow_drift", "isolated_spike",
        "short_burst", "single_low_window", "white_noise", "colored_noise",
        "dimension_reorder",
    ):
        for seed_offset in range(3):
            seed = MASTER_SEED + 500_000 + index * 19 + seed_offset
            cases.append(_case(f"negative-{kind}-{seed_offset}", label="negative",
                               family="negative", kind=kind, seed=seed, index=index))
            index += 1

    source_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return {
        "schema_version": SCHEMA,
        "generator_version": GENERATOR_VERSION,
        "generator_sha256": source_hash,
        "master_seed": MASTER_SEED,
        "sample_kind": "non_overlapping_60_frame_arithmetic_block_mean",
        "timeline_minutes": MINUTES,
        "anomaly_start_minute": ANOMALY_START_MINUTE,
        "minimum_positive_duration_minutes": 24 * 60,
        "case_order": [case["case_id"] for case in cases],
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("p4_task11_corpus.json"))
    parser.add_argument("--sha256-output", type=Path, default=Path(__file__).with_name("p4_task11_corpus.sha256"))
    args = parser.parse_args(argv)
    payload = _canonical(build())
    digest = hashlib.sha256(payload).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    args.sha256_output.write_text(digest + "  " + args.output.name + "\n", encoding="ascii", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
