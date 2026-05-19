from __future__ import annotations

import json
import tempfile
from pathlib import Path

from scripts.display_motion_curves import main, read_records, render_records

GOLDEN_PATH = Path("monitor/body_action_composition_golden.jsonl")


def _temp_jsonl(records: list[dict]) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return f.name


def _golden_records(count: int = 7) -> list[dict]:
    return read_records(GOLDEN_PATH)[:count]


def _curve_box_count(output: str) -> int:
    return output.count("┌")


def test_limit_1_renders_single_curve_box():
    records = _golden_records(3)
    path = _temp_jsonl(records)
    try:
        output_lines = []
        import sys
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            exit_code = main([path, "--limit", "1"])
        finally:
            output = sys.stdout.getvalue()
            sys.stdout = old_stdout

        assert exit_code == 0
        assert _curve_box_count(output) == 1
    finally:
        Path(path).unlink(missing_ok=True)


def test_limit_exceeds_available_records_renders_all():
    records = _golden_records(3)
    path = _temp_jsonl(records)
    try:
        import sys
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            exit_code = main([path, "--limit", "999"])
        finally:
            output = sys.stdout.getvalue()
            sys.stdout = old_stdout

        assert exit_code == 0
        assert _curve_box_count(output) == 3
    finally:
        Path(path).unlink(missing_ok=True)


def test_limit_zero_renders_all():
    records = _golden_records(3)
    path = _temp_jsonl(records)
    try:
        import sys
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            exit_code = main([path, "--limit", "0"])
        finally:
            output = sys.stdout.getvalue()
            sys.stdout = old_stdout

        assert exit_code == 0
        assert _curve_box_count(output) == 3
    finally:
        Path(path).unlink(missing_ok=True)


def test_no_limit_flag_renders_all():
    records = _golden_records(2)
    path = _temp_jsonl(records)
    try:
        import sys
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            exit_code = main([path])
        finally:
            output = sys.stdout.getvalue()
            sys.stdout = old_stdout

        assert exit_code == 0
        assert _curve_box_count(output) == 2
    finally:
        Path(path).unlink(missing_ok=True)


def test_default_path_still_works():
    exit_code = main([])
    assert exit_code == 0


def test_render_records_unaffected():
    records = _golden_records(2)
    output = render_records(records)
    assert _curve_box_count(output) == 2
    assert "┌" in output
