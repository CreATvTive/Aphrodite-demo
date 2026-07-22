# Chatbox v0 contracts index

Status: CURRENT CONTRACT INDEX
Authority-Scope: Navigation for frozen chatbox v0 contracts
Supersedes: —
Superseded-By: —
Last-Verified: 2026-07-14

The authoritative contract text remains in section C and the acceptance tasks of [`../phase-plan-v0.md`](../phase-plan-v0.md). This directory does not duplicate or reinterpret it.

## Frozen contract areas

- dimension registry and no hard-coded dimension count;
- gated expression versus full-pool dynamics;
- writer moves attractor only and never writes state directly;
- writer parse failure and per-dimension delta handling;
- emergent `P_talk` with external caps and no scheduled proactive messages;
- prompt-side receptor style injection and meta-narration prohibition;
- P1–P4 acceptance gates.

## Current supplemental contracts

- [`p4-task11-soak-detection.md`](p4-task11-soak-detection.md) - P4 task-card 11 soak detection numeric/state acceptance contract (subordinate to the Phase; fills windows, thresholds, integrity, state machine, and calibration-recall semantics without amending frozen text).

## Future split policy

Create a dedicated contract file only when a task produces stable text that can be copied from or explicitly approved against the Phase without semantic change. Potential splits include protocol/schema, persistence, dynamics, writer, perception, receptors, proactivity, and acceptance. File names, test paths, and ownership are Owner decisions; `tests/contract/` is only the Phase's suggested directory and is not documented as implemented.

Do not create empty design-rationale, risk-register, operations, or fine-grained contract placeholders.
