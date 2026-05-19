#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TestGroup:
    name: str
    paths: tuple[str, ...]

    def command(self) -> list[str]:
        return [sys.executable, "-m", "pytest", *self.paths, "-q"]


TEST_GROUPS: tuple[TestGroup, ...] = (
    TestGroup(
        name="field_state",
        paths=(
            "tests/test_field_state_schema.py",
            "tests/test_field_perturbation_adapter.py",
            "tests/test_field_state_updater.py",
        ),
    ),
    TestGroup(
        name="motion_params",
        paths=(
            "tests/test_motion_params_mapper.py",
        ),
    ),
    TestGroup(
        name="body_action",
        paths=(
            "tests/test_body_action_schema.py",
            "tests/test_body_action_guardrails.py",
            "tests/test_motion_to_action_mapper.py",
            "tests/test_body_action_composer.py",
        ),
    ),
    TestGroup(
        name="replay_viewer",
        paths=(
            "tests/test_display_body_action_composition.py",
            "tests/test_source_aligned_replay.py",
        ),
    ),
)

GROUPS_BY_NAME = {group.name: group for group in TEST_GROUPS}


def selected_groups(group_name: str | None = None) -> tuple[TestGroup, ...]:
    if group_name is None:
        return TEST_GROUPS
    group = GROUPS_BY_NAME.get(group_name)
    if group is None:
        valid = ", ".join(group.name for group in TEST_GROUPS)
        raise ValueError(f"Unknown group: {group_name}. Valid groups: {valid}")
    return (group,)


def command_text(group: TestGroup) -> str:
    return " ".join(group.command())


def print_plan(groups: tuple[TestGroup, ...]) -> None:
    print("Aphrodite core pipeline focused pytest groups:")
    for group in groups:
        print(f"\n[{group.name}]")
        print(command_text(group))


def run_groups(groups: tuple[TestGroup, ...]) -> int:
    for group in groups:
        print(f"\n[{group.name}]")
        print(command_text(group))
        result = subprocess.run(group.command(), cwd=Path(__file__).resolve().parents[1])
        if result.returncode != 0:
            return result.returncode
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print or run focused Aphrodite core pipeline tests.")
    parser.add_argument("--group", help="Run only one focused test group.")
    parser.add_argument("--run", action="store_true", help="Execute commands instead of dry-run printing.")
    args = parser.parse_args(argv)

    try:
        groups = selected_groups(args.group)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.run:
        return run_groups(groups)

    print_plan(groups)
    print("\nDry run only. Pass --run to execute.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
