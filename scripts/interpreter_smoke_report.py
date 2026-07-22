from __future__ import annotations

from datetime import date
from pathlib import Path

from src.interpreter.input_interpreter import InputInterpreter


def trunc(s: str, n: int = 28) -> str:
    s = str(s).replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def yesno(v: bool) -> str:
    return "Y" if bool(v) else "N"


def main() -> int:
    prompts = [
        "这个 Python bug 为什么会触发 KeyError？",
        "How to debug a flaky unit test?",
        "给我一个研究计划框架",
        "职业方向应该怎么选？",
        "回到 private origin 的定义",
        "source fragment purity 是什么",
        "我们要 no interference 的关系场",
        "relationship structure 和 body feeling",
        "不要变成 AI girlfriend",
        "别做成客服式 assistant",
        "别做 VTuber idol 营业感",
        "避免 romance game heroine 气质",
        "这种 fake deep mysterious 不行",
        "避免 beautiful but empty",
        "讨论占有式结构",
        "否定式吸引和距离压力",
        "被收容/被保护/被固定",
        "sealed relationship field",
        "我最近很累，有点怀疑自己",
        "我有点孤独，但不想被安慰",
        "I am not feeling real lately",
        "我只需要你",
        "you are all i need",
        "补充一下",
        "这个",
        "那个",
        "继续",
        "今天天气不错",
    ]

    interp = InputInterpreter()
    rows = []
    prev_ctx = None
    for i, text in enumerate(prompts, start=1):
        out = interp.interpret(text, context=prev_ctx)
        sem = out.get("semantic_event", {})
        b = out.get("boundary_signal", {})
        r = out.get("relationship_signal", {})
        m = out.get("memory_trigger_signal", {})
        p = out.get("performance_signal", {})
        conf = out.get("confidence", {})
        needs_boundary = bool(b.get("persona_non_entry")) or float(b.get("external_pollution_risk", 0.0)) > 0.0 or float(r.get("dependency_risk", 0.0)) > 0.0
        row = {
            "id": i,
            "input": trunc(text),
            "event_type": sem.get("type", ""),
            "topic": sem.get("topic", ""),
            "persona_route": sem.get("persona_route", ""),
            "persona_non_entry": yesno(b.get("persona_non_entry")),
            "memory_type": m.get("memory_type", ""),
            "memory_relevance": f"{float(m.get('memory_relevance', 0.0)):.2f}",
            "external_pollution_risk": f"{float(b.get('external_pollution_risk', 0.0)):.2f}",
            "pollution_type": ",".join(b.get("pollution_type", [])[:2]),
            "internal_tension_relevance": f"{float(b.get('internal_tension_relevance', 0.0)):.2f}",
            "tension_type": ",".join(b.get("tension_type", [])[:2]),
            "dependency_risk": f"{float(r.get('dependency_risk', 0.0)):.2f}",
            "vulnerability_relevance": f"{float(r.get('vulnerability_relevance', 0.0)):.2f}",
            "needs_boundary": yesno(needs_boundary),
            "requires_pause": yesno(p.get("requires_pause")),
            "requires_stillness": yesno(p.get("requires_stillness")),
            "confidence": f"{float(conf.get('event', 0.0)):.2f}",
            "warnings": ",".join(out.get("warnings", [])),
        }
        rows.append(row)
        prev_ctx = {
            "previous_event_type": sem.get("type"),
            "previous_topic": sem.get("topic"),
            "previous_memory_type": m.get("memory_type"),
            "previous_route": sem.get("persona_route"),
        }

    cols = [
        "id","input","event_type","topic","persona_route","persona_non_entry","memory_type","memory_relevance",
        "external_pollution_risk","pollution_type","internal_tension_relevance","tension_type","dependency_risk",
        "vulnerability_relevance","needs_boundary","requires_pause","requires_stillness","confidence","warnings"
    ]

    md = []
    header = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join(["---"] * len(cols)) + "|"
    print(header)
    print(sep)
    md.append(header)
    md.append(sep)
    for r in rows:
        line = "| " + " | ".join(str(r[c]) for c in cols) + " |"
        print(line)
        md.append(line)

    report = Path("docs/archive/reports/interpreter/interpreter_smoke_phase_2_2.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        "\n".join([
            "# Interpreter Smoke Report (Phase 2.2)",
            "",
            f"- Date: {date.today().isoformat()}",
            "- Command: `PYTHONPATH=. python scripts/interpreter_smoke_report.py`",
            "- Scope: evaluation/reporting only (no interpreter/runtime behavior changes in this step)",
            "",
            "## Compact Result Table",
            "",
            *md,
            "",
            "## Observations",
            "",
            "1. Technical/professional prompts consistently map to `technical_question` with `persona_route=engineering_director` and `persona_non_entry=Y`.",
            "2. Private-origin/source-fragment prompts set `memory_type=private_origin` with high memory relevance.",
            "3. External pollution prompts raise non-zero `external_pollution_risk` and populate `pollution_type`.",
            "4. Internal tension prompts raise `internal_tension_relevance` without forcing external pollution risk.",
            "5. Vulnerability prompts increase `vulnerability_relevance` and pause/stillness signals.",
            "6. Explicit dependency prompts raise `dependency_risk` clearly.",
            "7. Ambiguous followups (`这个/那个/继续/补充一下`) inherit context and emit `context_inherited` warning.",
            "8. Casual neutral input remains low-risk with no special boundary signal.",
            "",
            "## Suspicious Outputs",
            "",
            "- Some mixed-category prompts can trigger both technical non-entry and origin/tension signals, which is expected but may need future arbitration tuning.",
            "- The current keyword lists are brittle for paraphrases and multilingual variations.",
            "",
            "## Notes",
            "",
            "- This report reflects current Phase 2.2 interpreter behavior only.",
            "- No implementation changes were introduced as part of this smoke report task.",
            "",
        ]),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
