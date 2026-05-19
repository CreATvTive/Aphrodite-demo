#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.motion_curve import MotionCurveGenerator
from src.motion_params.schema import BodyPartOffsets, HardMotionConstraints, MotionParams


DEFAULT_GOLDEN_PATH = "monitor/body_action_composition_golden.jsonl"
CHANNEL_LABELS = (
    ("gaze", "gaze      "),
    ("head", "head      "),
    ("torso", "torso     "),
    ("expression", "expression"),
    ("posture", "posture   "),
)


def read_records(path: str | Path = DEFAULT_GOLDEN_PATH) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8")
    return [
        json.loads(line)
        for line in text.splitlines()
        if line.strip()
    ]


def motion_params_from_record(record: dict) -> MotionParams:
    payload = dict(record["motion_params"])
    offsets = BodyPartOffsets(**payload.pop("body_part_offsets", {}))
    constraints = HardMotionConstraints(**payload.pop("hard_constraints", {}))
    return MotionParams(
        **payload,
        body_part_offsets=offsets,
        hard_constraints=constraints,
    )


def render_curve(record: dict) -> str:
    params = motion_params_from_record(record)
    curve = MotionCurveGenerator().generate(
        params,
        scenario_name=str(record.get("scenario_id", "unknown")),
        scenario_intent=str(record.get("source_alignment_note", "")),
    )
    curves = curve.curves_by_channel()

    lines = [
        "┌" + "─" * 94 + "┐",
        f"│ {curve.scenario_name[:90]:<90} │",
        f"│ {curve.scenario_intent[:90]:<90} │",
        "├" + "─" * 94 + "┤",
    ]
    for channel, label in CHANNEL_LABELS:
        points = curves[channel]
        bars = "".join(_bucket(point.amplitude) for point in points)
        max_amp = max(point.amplitude for point in points)
        avg_amp = sum(point.amplitude for point in points) / len(points)
        lines.append(f"│ {label:<10} {bars}   max={max_amp:.2f} avg={avg_amp:.2f} │")
    lines.append("└" + "─" * 94 + "┘")
    return "\n".join(lines)


def render_records(records: list[dict]) -> str:
    return "\n\n".join(render_curve(record) for record in records)


def _bucket(amplitude: float) -> str:
    if amplitude > 0.70:
        return "█"
    if amplitude >= 0.30:
        return "▓"
    return "░"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Display MotionParams curves for golden scenarios.")
    parser.add_argument("path", nargs="?", default=DEFAULT_GOLDEN_PATH)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Only render the first N records (0 = no limit).",
    )
    args = parser.parse_args(argv)

    records = read_records(args.path)
    if args.limit and args.limit > 0:
        records = records[: args.limit]
    print(render_records(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
