"""Offline machine-readable query/verification CLI for P4.11 evidence."""

from __future__ import annotations

import argparse
import json
import sys

from app.chatbox.soak_evidence import SoakEvidenceError, verify_and_summarize


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify/query a P4.11 companion evidence database")
    parser.add_argument("--evidence-db", required=True)
    parser.add_argument("--report", default=None, help="atomic report output; defaults to <evidence-db>.report.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = verify_and_summarize(args.evidence_db, args.report)
        sys.stdout.write(json.dumps(summary, ensure_ascii=False, allow_nan=False,
                                    sort_keys=True, separators=(",", ":")) + "\n")
        sys.stdout.flush()
        return 0 if summary["state"] != "EVIDENCE_CORRUPT" else 2
    except Exception as exc:
        payload = {
            "type": "soak_detection_error",
            "code": getattr(exc, "code", "verification_failed"),
            "detail": getattr(exc, "detail", str(exc)),
            "formal_48h_run": "not_run",
            "p4_human_gate": "not_run",
        }
        sys.stderr.write(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")) + "\n")
        sys.stderr.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
