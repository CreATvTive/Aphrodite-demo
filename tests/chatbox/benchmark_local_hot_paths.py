"""Reproducible, offline evidence for chatbox local hot paths.

This is an explicit benchmark tool, not a pytest test.  All databases, reports,
profiles, and bytecode are created below a temporary directory.  Every timed
sample carries a deterministic output checksum; a checksum disagreement aborts
the run rather than reporting a misleading speedup.

Windows examples (run from the repository root)::

    python tests/chatbox/benchmark_local_hot_paths.py --samples 7 --warmups 1
    python tests/chatbox/benchmark_local_hot_paths.py --workload field-core \
        --dimensions 32 --field-ticks 50000 --samples 9 --warmups 1 --profile

The previously reported command
``python -m compileall -q -x app/chatbox ...`` is malformed: ``-x`` consumes
``app/chatbox`` as a regular expression.  Use ``--static-check`` here (it
compiles to a temporary cache and performs no-bytecode import probes), or use
``python -m compileall -q app/chatbox tests/chatbox`` with an external
``PYTHONPYCACHEPREFIX``.
"""

from __future__ import annotations

import argparse
import cProfile
from dataclasses import asdict, replace
import gc
import hashlib
import json
import math
import os
import platform
import pstats
import py_compile
import random
import sqlite3
import statistics
import struct
import subprocess
import sys
import sysconfig
import tempfile
import time
import tracemalloc
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.chatbox.field_dynamics import (
    FieldDynamics,
    SeededGaussianRngFactory,
    build_birth_registry,
)
from app.chatbox.field_persistence import TrajectoryFrame, TrajectoryPoint
from app.chatbox.field_runtime import FieldRuntime, RegistryProxy
from app.chatbox.soak_detection import (
    TEST_PROFILE,
    StreamingSoakDetector,
    canonical_sha256,
)
from app.chatbox.soak_evidence import SoakEvidenceStore


SEED = 20_260_721
DEFAULT_DIMENSIONS = 32
DEFAULT_FIELD_TICKS = 5_000
DEFAULT_RUNTIME_TICKS = 180
DEFAULT_SOAK_FRAMES = 1_800
FORMAL_BLOCKS = 2_880
STATIC_IMPORTS = (
    "app.chatbox.field_dynamics",
    "app.chatbox.field_runtime",
    "app.chatbox.soak_detection",
    "app.chatbox.soak_evidence",
)


def _registry(dimension_count: int) -> RegistryProxy:
    if dimension_count < 1:
        raise ValueError("dimension_count must be positive")
    births = build_birth_registry()
    registrations = tuple(
        replace(
            births[index % len(births)],
            dim_id=f"benchmark_{index:04d}",
            temporary_name=f"benchmark-{index}",
        )
        for index in range(dimension_count)
    )
    return RegistryProxy(registrations)


def _frame(cursor: int, registry: RegistryProxy) -> TrajectoryFrame:
    points = tuple(
        TrajectoryPoint(
            ordinal=ordinal,
            dim_id=registration.dim_id,
            value=(
                0.18 * math.sin((cursor + ordinal * 7) * 0.013)
                + 0.07 * math.cos((cursor * (ordinal + 3)) * 0.0017)
            ),
            velocity=0.001 * math.sin((cursor + ordinal) * 0.021),
            attractor=registration.birth_bias,
            slow_baseline=registration.birth_bias,
            ou_acceleration=0.000_001 * math.cos((cursor + ordinal) * 0.031),
        )
        for ordinal, registration in enumerate(registry.registrations)
    )
    return TrajectoryFrame(
        cursor=cursor,
        boot_id="benchmark-boot",
        field_tick=cursor,
        utc_unix_ns=cursor * 1_000_000_000,
        dimensions=points,
    )


def _formal_block_means(registry: RegistryProxy) -> dict[str, tuple[float, ...]]:
    rng = random.Random(SEED)
    result: dict[str, tuple[float, ...]] = {}
    for ordinal, registration in enumerate(registry.registrations):
        value = 0.0
        series: list[float] = []
        for _ in range(FORMAL_BLOCKS):
            value = 0.93 * value + rng.gauss(0.0, 0.035 + ordinal * 0.0005)
            series.append(value)
        result[registration.dim_id] = tuple(series)
    return result


def _artifact_bytes(directory: Path) -> int:
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())


def _snapshot_checksum(dynamics: FieldDynamics) -> str:
    digest = hashlib.sha256()
    snapshot = dynamics.snapshot()
    digest.update(struct.pack(">Q", snapshot.tick))
    for item in snapshot.dimensions:
        encoded_id = item.dim_id.encode("utf-8")
        digest.update(struct.pack(">I", len(encoded_id)))
        digest.update(encoded_id)
        digest.update(
            struct.pack(
                ">5d",
                item.value,
                item.velocity,
                item.attractor,
                item.soft_restoring_baseline,
                item.ou_acceleration,
            )
        )
    return digest.hexdigest()


def _timed(body: Callable[[], None]) -> tuple[int, int]:
    start_cpu = time.process_time_ns()
    start_wall = time.perf_counter_ns()
    body()
    return time.perf_counter_ns() - start_wall, time.process_time_ns() - start_cpu


def _field_core_trial(
    root: Path, registry: RegistryProxy, tick_count: int
) -> dict[str, Any]:
    del root
    dynamics = FieldDynamics(
        registry.registrations, rng_factory=SeededGaussianRngFactory(SEED)
    )
    dynamics.tick()

    def run() -> None:
        for _ in range(tick_count):
            dynamics.tick()

    wall_ns, cpu_ns = _timed(run)
    return {
        "wall_ns": wall_ns,
        "cpu_ns": cpu_ns,
        "artifact_bytes": 0,
        "output_checksum": _snapshot_checksum(dynamics),
    }


def _field_runtime_trial(
    root: Path, registry: RegistryProxy, tick_count: int
) -> dict[str, Any]:
    trial_dir = root / f"runtime-{time.perf_counter_ns()}"
    trial_dir.mkdir()
    runtime = FieldRuntime.open(
        str(trial_dir / "field.sqlite3"),
        birth_registry=registry.registrations,
        birth_rng_factory=SeededGaussianRngFactory(SEED),
    )
    try:
        runtime.tick()

        def run() -> None:
            for _ in range(tick_count):
                runtime.tick()

        wall_ns, cpu_ns = _timed(run)
        checksum = canonical_sha256(asdict(runtime.snapshot_proxy()))
        artifact_bytes = _artifact_bytes(trial_dir)
    finally:
        runtime.close()
    return {
        "wall_ns": wall_ns,
        "cpu_ns": cpu_ns,
        "artifact_bytes": artifact_bytes,
        "output_checksum": checksum,
    }


def _soak_append_trial(
    root: Path, registry: RegistryProxy, frame_count: int
) -> dict[str, Any]:
    trial_dir = root / f"soak-{time.perf_counter_ns()}"
    trial_dir.mkdir()
    store = SoakEvidenceStore(
        str(trial_dir / "soak.sqlite3"),
        str(trial_dir / "soak-report.json"),
        registry,
        profile=TEST_PROFILE,
    )
    try:
        store.append_frame(_frame(1, registry))

        def run() -> None:
            for cursor in range(2, frame_count + 2):
                store.append_frame(_frame(cursor, registry))

        wall_ns, cpu_ns = _timed(run)
        checksum = canonical_sha256(store.report_primitive())
        artifact_bytes = _artifact_bytes(trial_dir)
    finally:
        store.close()
    return {
        "wall_ns": wall_ns,
        "cpu_ns": cpu_ns,
        "artifact_bytes": artifact_bytes,
        "output_checksum": checksum,
    }


def _soak_analysis_trial(
    root: Path,
    registry: RegistryProxy,
    block_means: dict[str, tuple[float, ...]],
) -> dict[str, Any]:
    del root
    detector = StreamingSoakDetector(registry, profile=TEST_PROFILE)

    def run() -> None:
        detector.ingest_block_means(block_means)

    wall_ns, cpu_ns = _timed(run)
    report = detector.report_primitive()
    return {
        "wall_ns": wall_ns,
        "cpu_ns": cpu_ns,
        "artifact_bytes": len(
            json.dumps(report, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ),
        "output_checksum": canonical_sha256(report),
        "state": detector.state.value,
    }


def _median_absolute_deviation(values: Sequence[int]) -> float:
    median = statistics.median(values)
    return float(statistics.median(abs(value - median) for value in values))


def _summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    wall = [int(sample["wall_ns"]) for sample in samples]
    cpu = [int(sample["cpu_ns"]) for sample in samples]
    artifacts = [int(sample["artifact_bytes"]) for sample in samples]
    checksums = [str(sample["output_checksum"]) for sample in samples]
    if len(set(checksums)) != 1:
        raise RuntimeError(f"output checksum mismatch across samples: {checksums}")
    summary: dict[str, Any] = {
        "wall_seconds": [value / 1_000_000_000 for value in wall],
        "wall_median_seconds": statistics.median(wall) / 1_000_000_000,
        "wall_mad_seconds": _median_absolute_deviation(wall) / 1_000_000_000,
        "wall_range_seconds": [min(wall) / 1_000_000_000, max(wall) / 1_000_000_000],
        "cpu_seconds": [value / 1_000_000_000 for value in cpu],
        "cpu_median_seconds": statistics.median(cpu) / 1_000_000_000,
        "cpu_mad_seconds": _median_absolute_deviation(cpu) / 1_000_000_000,
        "cpu_range_seconds": [min(cpu) / 1_000_000_000, max(cpu) / 1_000_000_000],
        "artifact_bytes_range": [min(artifacts), max(artifacts)],
        "output_checksum": checksums[0],
        "output_validation": "identical_across_samples",
        "dispersion": "MAD = median(abs(sample - sample_median))",
    }
    states = [sample.get("state") for sample in samples if "state" in sample]
    if states:
        if len(set(states)) != 1:
            raise RuntimeError(f"state mismatch across samples: {states}")
        summary["state"] = states[0]
    return summary


def _measure_peak_python_bytes(
    trial: Callable[[Path], dict[str, Any]], root: Path
) -> int:
    gc.collect()
    tracemalloc.start()
    try:
        trial(root)
        _, peak = tracemalloc.get_traced_memory()
        return peak
    finally:
        tracemalloc.stop()


def _profile_trial(
    trial: Callable[[Path], dict[str, Any]], root: Path
) -> dict[str, Any]:
    profiler = cProfile.Profile()
    profiler.enable()
    result = trial(root)
    profiler.disable()
    stats = pstats.Stats(profiler)
    total = float(stats.total_tt)
    raw_stats = cast(dict[tuple[str, int, str], tuple[int, int, float, float, Any]], stats.stats)  # type: ignore[attr-defined]
    top_level_cumulative = max(
        (float(value[3]) for value in raw_stats.values()), default=total
    )
    rows: list[dict[str, Any]] = []
    for (filename, line, function), value in sorted(
        raw_stats.items(), key=lambda item: item[1][3], reverse=True
    )[:20]:
        primitive_calls, calls, own_seconds, cumulative_seconds, _ = value
        rows.append(
            {
                "location": f"{Path(filename).name}:{line}:{function}",
                "calls": calls,
                "primitive_calls": primitive_calls,
                "own_seconds": own_seconds,
                "cumulative_seconds": cumulative_seconds,
                "cumulative_percent": (
                    0.0
                    if top_level_cumulative == 0.0
                    else 100.0 * cumulative_seconds / top_level_cumulative
                ),
            }
        )
    return {
        "profiler": "cProfile deterministic single trial outside measured samples",
        "total_primitive_seconds": total,
        "top_level_cumulative_seconds": top_level_cumulative,
        "output_checksum": result["output_checksum"],
        "top_cumulative": rows,
    }


def _static_check(root: Path) -> dict[str, Any]:
    cache_root = root / "pyc"
    sources = tuple(
        sorted((REPOSITORY_ROOT / "app" / "chatbox").rglob("*.py"))
        + sorted((REPOSITORY_ROOT / "tests" / "chatbox").rglob("*.py"))
    )
    failures: list[str] = []
    for index, source in enumerate(sources):
        try:
            py_compile.compile(
                str(source),
                cfile=str(cache_root / f"{index}.pyc"),
                doraise=True,
            )
        except py_compile.PyCompileError as exc:
            failures.append(f"{source.relative_to(REPOSITORY_ROOT)}: {exc.msg}")
    if failures:
        raise RuntimeError("syntax compile failed: " + " | ".join(failures))

    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    smoke = subprocess.run(
        [sys.executable, "-c", "; ".join(f"import {name}" for name in STATIC_IMPORTS)],
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if smoke.returncode != 0:
        raise RuntimeError(f"import smoke failed: {smoke.stderr.strip()}")

    invalid = root / "invalid_probe.py"
    invalid.write_text("def invalid(:\n", encoding="utf-8")
    try:
        py_compile.compile(
            str(invalid), cfile=str(cache_root / "invalid.pyc"), doraise=True
        )
    except py_compile.PyCompileError as exc:
        invalid_diagnostic = str(exc.msg).splitlines()[-1]
    else:
        raise RuntimeError("invalid syntax probe unexpectedly compiled")

    return {
        "source_count": len(sources),
        "syntax_compile": "passed",
        "compile_destination": "temporary_directory",
        "import_modules": list(STATIC_IMPORTS),
        "import_smoke": "passed_with_PYTHONDONTWRITEBYTECODE=1",
        "invalid_probe": "rejected",
        "invalid_probe_diagnostic": invalid_diagnostic,
        "correct_compileall_equivalent": (
            "python -m compileall -q app/chatbox tests/chatbox "
            "(set PYTHONPYCACHEPREFIX to an external temporary directory)"
        ),
        "malformed_command_root_cause": (
            "compileall -x consumes the next token as a regex; app/chatbox was not a target"
        ),
    }


def _compilation_mode() -> dict[str, Any]:
    return {
        "optimize": sys.flags.optimize,
        "debug_build": bool(sysconfig.get_config_var("Py_DEBUG")),
        "gil_disabled": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
        "compiler": platform.python_compiler(),
        "byteorder": sys.byteorder,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--dimensions", type=int, default=DEFAULT_DIMENSIONS)
    parser.add_argument("--field-ticks", type=int, default=DEFAULT_FIELD_TICKS)
    parser.add_argument("--runtime-ticks", type=int, default=DEFAULT_RUNTIME_TICKS)
    parser.add_argument("--soak-frames", type=int, default=DEFAULT_SOAK_FRAMES)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--static-check", action="store_true")
    parser.add_argument(
        "--workload",
        choices=("all", "field-core", "field-runtime", "soak-append", "soak-analysis"),
        default="all",
    )
    args = parser.parse_args(argv)
    if args.samples < 3 or args.warmups < 0:
        parser.error("--samples must be >= 3 and --warmups must be >= 0")
    if min(args.dimensions, args.field_ticks, args.runtime_ticks, args.soak_frames) < 1:
        parser.error("dimensions and iteration counts must be positive")

    registry = _registry(args.dimensions)
    block_means = _formal_block_means(registry)
    workloads: dict[
        str, tuple[Callable[[Path], dict[str, Any]], dict[str, Any], dict[str, str]]
    ] = {
        "field-core": (
            lambda root: _field_core_trial(root, registry, args.field_ticks),
            {
                "dimensions": args.dimensions,
                "seed": SEED,
                "timed_ticks": args.field_ticks,
                "warmup_ticks": 1,
            },
            {
                "primary": "CPU/Python allocation",
                "candidate_surface": "FieldDynamics.tick per-dimension arithmetic only",
                "includes": "Python RNG, validation, state and observation construction, commit",
                "excludes": "SQLite, filesystem, JSON/hash, provider/network",
            },
        ),
        "field-runtime": (
            lambda root: _field_runtime_trial(root, registry, args.runtime_ticks),
            {
                "dimensions": args.dimensions,
                "seed": SEED,
                "timed_ticks": args.runtime_ticks,
                "warmup_ticks": 1,
            },
            {
                "primary": "SQLite/WAL and CPU",
                "candidate_surface": "none (end-to-end control workload)",
                "includes": "field tick, SQLite transaction/readback, trajectory allocation",
                "excludes": "provider/network",
            },
        ),
        "soak-append": (
            lambda root: _soak_append_trial(root, registry, args.soak_frames),
            {
                "dimensions": args.dimensions,
                "seed": SEED,
                "timed_frames": args.soak_frames,
                "warmup_frames": 1,
            },
            {
                "primary": "SQLite/filesystem plus JSON/hash and Python allocation",
                "candidate_surface": "none",
                "includes": "canonical JSON, SHA-256, SQLite/WAL, report publication",
                "excludes": "provider/network",
            },
        ),
        "soak-analysis": (
            lambda root: _soak_analysis_trial(root, registry, block_means),
            {
                "dimensions": args.dimensions,
                "seed": SEED,
                "block_means_per_dimension": FORMAL_BLOCKS,
                "window_blocks": 720,
                "window_stride_blocks": 360,
            },
            {
                "primary": "CPU/Python allocation",
                "candidate_surface": "none (frozen numerical contract)",
                "includes": "variance, direct autocorrelation, report construction",
                "excludes": "SQLite, filesystem, provider/network",
            },
        ),
    }
    selected = workloads if args.workload == "all" else {args.workload: workloads[args.workload]}

    results: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="chatbox-benchmark-") as raw_root:
        root = Path(raw_root)
        static_result = _static_check(root) if args.static_check else "not_requested"
        for name, (trial, shape, classification) in selected.items():
            for _ in range(args.warmups):
                trial(root)
            samples = [trial(root) for _ in range(args.samples)]
            entry: dict[str, Any] = {
                "shape": shape,
                "classification": classification,
                "samples": _summarize(samples),
                "peak_python_bytes": _measure_peak_python_bytes(trial, root),
            }
            if args.profile:
                entry["profile"] = _profile_trial(trial, root)
            results[name] = entry

    payload = {
        "benchmark": "chatbox-local-hot-paths/2",
        "environment": {
            "python": sys.version,
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "processor": platform.processor(),
            "logical_cpus": os.cpu_count(),
            "sqlite": sqlite3.sqlite_version,
            "compilation_mode": _compilation_mode(),
            "perf_counter_resolution_seconds": time.get_clock_info("perf_counter").resolution,
            "process_time_resolution_seconds": time.get_clock_info("process_time").resolution,
        },
        "method": {
            "sample_order": "sequential; compare candidates by interleaving in one process",
            "warmups": args.warmups,
            "sample_count": args.samples,
            "central_tendency": "median",
            "dispersion": "MAD = median(abs(sample - sample_median))",
            "native_gate": (
                "same-checksum end-to-end CPU median improvement >= 10% and absolute "
                "delta > 3 * max(baseline CPU MAD, candidate CPU MAD)"
            ),
        },
        "static_check": static_result,
        "results": results,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
