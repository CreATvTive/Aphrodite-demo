#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.viewers.field_motion_prediction import (  # noqa: E402
    DEMO_FIELD_VALUES,
    build_demo_snapshot,
    build_report,
    format_json_report,
    format_table_report,
    format_text_report,
    load_latest_monitor_snapshot,
    load_snapshots,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Display a read-only field and motion prediction report.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Optional JSON or JSONL snapshot path. Defaults to the latest monitor snapshot.",
    )
    parser.add_argument(
        "--demo",
        choices=[*sorted(DEMO_FIELD_VALUES), "all"],
        help="Use a built-in deterministic demo scenario.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON report.")
    parser.add_argument("--table", action="store_true", help="Print compact tendency table.")
    args = parser.parse_args(argv)

    snapshots = _snapshots_from_args(args)
    if not snapshots:
        snapshots = [None]

    rendered: list[str] = []
    for snapshot in snapshots:
        report = build_report(snapshot)
        if args.json:
            rendered.append(format_json_report(report))
        elif args.table:
            rendered.append(f"Scenario: {report['scenario']}\n{format_table_report(report)}")
        else:
            rendered.append(format_text_report(report))

    print("\n\n".join(rendered))
    return 0


def _snapshots_from_args(args: argparse.Namespace) -> list[dict] | list[None]:
    if args.demo == "all":
        return [build_demo_snapshot(name) for name in sorted(DEMO_FIELD_VALUES)]
    if args.demo:
        return [build_demo_snapshot(args.demo)]
    if args.path:
        return load_snapshots(args.path)
    latest = load_latest_monitor_snapshot()
    return [latest] if latest is not None else [None]


if __name__ == "__main__":
    raise SystemExit(main())
