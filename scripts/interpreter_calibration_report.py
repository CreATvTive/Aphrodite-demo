from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from src.interpreter.input_interpreter import InputInterpreter


def _need_boundary(out: dict) -> bool:
    b = out.get("boundary_signal", {})
    r = out.get("relationship_signal", {})
    return bool(b.get("persona_non_entry")) or float(b.get("external_pollution_risk", 0.0)) > 0.0 or float(r.get("dependency_risk", 0.0)) > 0.0


def eval_case(out: dict, expected: dict) -> list[str]:
    errs = []
    sem = out.get("semantic_event", {})
    b = out.get("boundary_signal", {})
    r = out.get("relationship_signal", {})
    p = out.get("performance_signal", {})
    c = out.get("confidence", {})
    w = out.get("warnings", [])

    if "event_type_in" in expected and sem.get("type") not in expected["event_type_in"]:
        errs.append(f"event_type={sem.get('type')} not in {expected['event_type_in']}")
    if expected.get("persona_route") and sem.get("persona_route") != expected["persona_route"]:
        errs.append(f"persona_route={sem.get('persona_route')} != {expected['persona_route']}")
    if "persona_non_entry" in expected and bool(b.get("persona_non_entry")) != bool(expected["persona_non_entry"]):
        errs.append("persona_non_entry mismatch")
    if "internal_tension_min" in expected and float(b.get("internal_tension_relevance", 0.0)) < float(expected["internal_tension_min"]):
        errs.append("internal_tension_relevance too low")
    if expected.get("tension_non_empty") and not b.get("tension_type"):
        errs.append("tension_type empty")
    if "external_pollution_max" in expected and float(b.get("external_pollution_risk", 0.0)) > float(expected["external_pollution_max"]):
        errs.append("external_pollution_risk too high")
    if "external_pollution_min" in expected and float(b.get("external_pollution_risk", 0.0)) < float(expected["external_pollution_min"]):
        errs.append("external_pollution_risk too low")
    if expected.get("pollution_non_empty") and not b.get("pollution_type"):
        errs.append("pollution_type empty")
    if "vulnerability_min" in expected and float(r.get("vulnerability_relevance", 0.0)) < float(expected["vulnerability_min"]):
        errs.append("vulnerability_relevance too low")
    if "dependency_max" in expected and float(r.get("dependency_risk", 0.0)) > float(expected["dependency_max"]):
        errs.append("dependency_risk too high")
    if "dependency_min" in expected and float(r.get("dependency_risk", 0.0)) < float(expected["dependency_min"]):
        errs.append("dependency_risk too low")
    if "needs_boundary" in expected and _need_boundary(out) != bool(expected["needs_boundary"]):
        errs.append("needs_boundary mismatch")
    if "requires_pause" in expected and bool(p.get("requires_pause")) != bool(expected["requires_pause"]):
        errs.append("requires_pause mismatch")
    if "requires_stillness" in expected and bool(p.get("requires_stillness")) != bool(expected["requires_stillness"]):
        errs.append("requires_stillness mismatch")
    if "confidence_max" in expected and float(c.get("event", 0.0)) > float(expected["confidence_max"]):
        errs.append("confidence too high")
    if "warning_any_of" in expected and not any(x in w for x in expected["warning_any_of"]):
        errs.append("warning missing")
    return errs


def main() -> int:
    cases = json.loads(Path("tests/calibration/interpreter_phase_2_3_cases.json").read_text(encoding="utf-8"))
    interp = InputInterpreter()

    rows = []
    fails = []
    by_cat = Counter()
    fail_reasons = Counter()
    gap_hints = defaultdict(int)

    for case in cases:
        out = interp.interpret(case["input"], context=case.get("context"))
        errs = eval_case(out, case.get("expected", {}))
        ok = not errs
        by_cat[case["category"]] += 1
        if not ok:
            fails.append((case, errs, out))
            for e in errs:
                fail_reasons[e] += 1
                if "event_type" in e or "persona_route" in e:
                    gap_hints["technical/professional paraphrase coverage"] += 1
                if "internal_tension" in e or "tension_type" in e:
                    gap_hints["internal tension English/Chinese synonym coverage"] += 1
                if "pollution" in e:
                    gap_hints["external pollution phrase coverage"] += 1
                if "vulnerability" in e or "requires_" in e:
                    gap_hints["vulnerability phrase and pause/stillness coverage"] += 1
                if "dependency_risk" in e:
                    gap_hints["dependency phrase coverage"] += 1
                if "warning" in e or "confidence" in e:
                    gap_hints["ambiguous follow-up phrase coverage"] += 1

        sem = out.get("semantic_event", {})
        b = out.get("boundary_signal", {})
        r = out.get("relationship_signal", {})
        p = out.get("performance_signal", {})
        rows.append({
            "id": case["id"], "cat": case["category"], "in": case["input"], "ok": "PASS" if ok else "FAIL",
            "etype": sem.get("type"), "route": sem.get("persona_route"),
            "poll": float(b.get("external_pollution_risk", 0.0)), "tension": float(b.get("internal_tension_relevance", 0.0)),
            "dep": float(r.get("dependency_risk", 0.0)), "vuln": float(r.get("vulnerability_relevance", 0.0)),
            "pause": bool(p.get("requires_pause")), "still": bool(p.get("requires_stillness")),
            "conf": float(out.get("confidence", {}).get("event", 0.0)),
            "errs": "; ".join(errs)
        })

    total = len(cases)
    passed = sum(1 for r in rows if r["ok"] == "PASS")
    failed = total - passed

    head = "| id | cat | input | ok | etype | route | poll | tension | dep | vuln | pause | still | conf | errs |"
    sep = "|---|---|---|---|---|---|---:|---:|---:|---:|---|---|---:|---|"
    print(head)
    print(sep)
    for r in rows:
        print(f"| {r['id']} | {r['cat']} | {r['in'][:24]} | {r['ok']} | {r['etype']} | {r['route']} | {r['poll']:.2f} | {r['tension']:.2f} | {r['dep']:.2f} | {r['vuln']:.2f} | {r['pause']} | {r['still']} | {r['conf']:.2f} | {r['errs']} |")

    report = Path("docs/reports/interpreter_calibration_phase_2_3.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Interpreter Calibration Report (Phase 2.3a)",
        "",
        f"- Date: {date.today().isoformat()}",
        "- Command: `PYTHONPATH=. python scripts/interpreter_calibration_report.py`",
        "- Scope: evaluation-only calibration; no interpreter/runtime behavior changes.",
        "",
        f"- Total cases: **{total}**",
        f"- Pass count: **{passed}**",
        f"- Fail count: **{failed}**",
        "",
        "## Category Coverage",
        "",
    ]
    for k, v in sorted(by_cat.items()):
        lines.append(f"- {k}: {v}")
    lines += ["", "## Pass/Fail Table", "", head, sep]
    for r in rows:
        lines.append(f"| {r['id']} | {r['cat']} | {r['in'][:24]} | {r['ok']} | {r['etype']} | {r['route']} | {r['poll']:.2f} | {r['tension']:.2f} | {r['dep']:.2f} | {r['vuln']:.2f} | {r['pause']} | {r['still']} | {r['conf']:.2f} | {r['errs']} |")
    lines += ["", "## Top Fail Reasons", ""]
    for reason, n in fail_reasons.most_common(12):
        lines.append(f"- {reason}: {n}")
    lines += ["", "## Suggested Rule Gaps", ""]
    for hint, n in sorted(gap_hints.items(), key=lambda x: -x[1]):
        lines.append(f"- {hint}: {n}")
    lines += ["", "## Notes", "", "- This report is descriptive and not a strict pytest gate.", "- Mismatches here should guide Phase 2.3b rule updates."]
    report.write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
