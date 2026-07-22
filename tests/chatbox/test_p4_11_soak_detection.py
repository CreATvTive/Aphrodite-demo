"""P4.11 pure detector and evidence integrity tests (no formal 48h run)."""

from __future__ import annotations

from dataclasses import replace
import json
import math
import sqlite3

import pytest

from app.chatbox.field_dynamics import DimensionRegistration
from app.chatbox.field_persistence import TrajectoryFrame, TrajectoryPoint
from app.chatbox.field_runtime import RegistryProxy
from app.chatbox.soak_detection import (
    FORMAL_PROFILE,
    TEST_PROFILE,
    SoakState,
    StreamingSoakDetector,
    VARIANCE_THRESHOLD,
    WINDOW_BLOCKS,
    direct_autocorrelation,
    sample_variance,
)
from app.chatbox.soak_evidence import SoakEvidenceStore


def registration(dim_id: str, sigma: float = 4e-7) -> DimensionRegistration:
    return DimensionRegistration(
        dim_id=dim_id, temporary_name=dim_id, birth_time=1.0, strength=1.0,
        trigger_count=0, birth_bias=0.0, fast_e_fold_s=600.0,
        ou_correlation_e_fold_s=10_800.0, ou_acceleration_sigma=sigma,
        soft_boundary_start=1.0, soft_boundary_width=0.25,
        soft_boundary_strength=(1.0 / 120.0) ** 2,
    )


def registry(count: int = 1) -> RegistryProxy:
    return RegistryProxy(tuple(registration(f"dim-{index}") for index in range(count)))


def frame(cursor: int, values: tuple[float, ...], *, tick: int | None = None,
          ns: int | None = None, boot: str = "boot") -> TrajectoryFrame:
    return TrajectoryFrame(
        cursor=cursor, boot_id=boot, field_tick=tick if tick is not None else cursor,
        utc_unix_ns=ns if ns is not None else cursor * 1_000_000_000,
        dimensions=tuple(TrajectoryPoint(
            ordinal=index, dim_id=f"dim-{index}", value=value, velocity=0.0,
            attractor=0.0, slow_baseline=0.0, ou_acceleration=0.0,
        ) for index, value in enumerate(values)),
    )


def test_variance_exact_threshold_and_neighbors() -> None:
    # Alternating +/-a has sample variance n*a²/(n-1).
    amplitude = math.sqrt(VARIANCE_THRESHOLD * (WINDOW_BLOCKS - 1) / WINDOW_BLOCKS)
    values = [amplitude if i % 2 else -amplitude for i in range(WINDOW_BLOCKS)]
    variance, exact = sample_variance(values)
    assert variance == pytest.approx(VARIANCE_THRESHOLD, rel=2e-15)
    assert variance <= VARIANCE_THRESHOLD
    assert sample_variance([0.0] * WINDOW_BLOCKS) == (0.0, True)
    above = [math.nextafter(value, math.copysign(math.inf, value)) for value in values]
    assert sample_variance(above)[0] > VARIANCE_THRESHOLD


def test_direct_autocorrelation_matches_independent_definition() -> None:
    values = [0.2 * math.sin(2 * math.pi * index / 60.0) + 0.01 * math.cos(index) for index in range(720)]
    actual = direct_autocorrelation(values)
    mean = sum(values) / len(values)
    y = [value - mean for value in values]
    denominator = sum(value * value for value in y)
    oracle = [sum(y[i] * y[i - lag] for i in range(lag, len(y))) / denominator for lag in range(361)]
    assert max(abs(left - right) for left, right in zip(actual, oracle)) <= 1e-12


def test_two_consecutive_complete_windows_confirm_and_test_never_passes() -> None:
    detector = StreamingSoakDetector(registry(), profile=TEST_PROFILE)
    detector.ingest_block_means({"dim-0": [0.25] * 1080})
    assert detector.state is SoakState.FAIL
    windows = detector.report_primitive()["attempts"][0]["windows"]
    assert windows[0]["dimensions"]["dim-0"]["collapse_raw_hit"] is True
    assert windows[1]["dimensions"]["dim-0"]["collapse_confirmed"] is True
    assert detector.report_primitive()["formal_48h"] is False


def test_single_low_window_then_recovery_does_not_fail() -> None:
    low = [0.1] * 720
    recovery = [0.2 * math.sin(2 * math.pi * index / 53.0) for index in range(360)]
    detector = StreamingSoakDetector(registry(), profile=TEST_PROFILE)
    detector.ingest_block_means({"dim-0": low + recovery})
    assert detector.state is SoakState.RUNNING


@pytest.mark.parametrize("count", [0, 1, 12, 17])
def test_registry_dimension_counts(count: int) -> None:
    detector = StreamingSoakDetector(registry(count), profile=TEST_PROFILE)
    assert detector.state is (SoakState.INSUFFICIENT_EVIDENCE if count == 0 else SoakState.RUNNING)


def test_continuity_duplicates_and_corruption_precedence() -> None:
    detector = StreamingSoakDetector(registry())
    first = frame(1, (0.0,))
    detector.ingest_frame(first)
    detector.ingest_frame(first)
    assert detector.report_primitive()["duplicate_count"] == 1
    detector.ingest_frame(frame(2, (0.1,), tick=3))
    assert len(detector.report_primitive()["attempts"]) == 2
    detector.ingest_frame(frame(2, (0.2,), tick=3))
    assert detector.state is SoakState.EVIDENCE_CORRUPT


def test_exact_cadence_boundaries() -> None:
    detector = StreamingSoakDetector(registry())
    detector.ingest_frame(frame(1, (0.0,), ns=1_000_000_000))
    detector.ingest_frame(frame(2, (0.0,), ns=1_200_000_000))  # exact 0.2 is not anomaly
    detector.ingest_frame(frame(3, (0.0,), ns=3_200_000_000))  # exact 2.0 is not anomaly
    detector.ingest_frame(frame(4, (0.0,), ns=33_200_000_000))  # exact 30 is anomaly, no break
    report = detector.report_primitive()
    assert report["attempt_count"] == 1
    assert report["attempts"][0]["cadence_anomaly_count"] == 1


def test_store_reopen_replay_and_row_hash_tamper(tmp_path) -> None:
    db = str(tmp_path / "soak.sqlite3")
    report = str(tmp_path / "soak.json")
    store = SoakEvidenceStore(db, report, registry())
    store.append_frame(frame(1, (0.1,)))
    store.close()
    closed_report = json.loads(open(report, encoding="utf-8").read())
    before = closed_report["evidence_sha256"]
    reopened = SoakEvidenceStore(db, report, registry())
    assert reopened.report_primitive()["evidence_sha256"] == before
    assert reopened.state is SoakState.INSUFFICIENT_EVIDENCE
    reopened.close()
    conn = sqlite3.connect(db)
    # Reopen must reject altered schema objects before considering row caches.
    conn.execute("DROP TRIGGER trg_soak_frames_no_update")
    conn.commit()
    conn.close()
    with pytest.raises(Exception):
        SoakEvidenceStore(db, report, registry())


def test_reopen_keeps_closed_attempt_insufficient_until_successor_frame(tmp_path) -> None:
    db = str(tmp_path / "soak.sqlite3")
    report = str(tmp_path / "soak.json")
    store = SoakEvidenceStore(db, report, registry())
    store.append_frame(frame(1, (0.1,)))
    store.close()

    reopened = SoakEvidenceStore(db, report, registry())
    assert reopened.state is SoakState.INSUFFICIENT_EVIDENCE
    assert reopened.report_primitive()["attempts"][0]["end_reason"] == "observer_closed"
    reopened.append_frame(frame(2, (0.2,)))
    assert reopened.state is SoakState.RUNNING
    attempts = reopened.report_primitive()["attempts"]
    assert [attempt["state"] for attempt in attempts] == ["INSUFFICIENT_EVIDENCE", "RUNNING"]
    assert attempts[1]["cursor_bounds"] == [2, 2]
    reopened.close()


def test_non_finite_frame_is_corrupt() -> None:
    detector = StreamingSoakDetector(registry())
    detector.ingest_frame(frame(1, (float("nan"),)))
    assert detector.state is SoakState.EVIDENCE_CORRUPT
