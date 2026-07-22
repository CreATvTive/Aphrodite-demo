# AGENTS.md — Aphrodite chatbox v0

Status: CURRENT PROJECT RULE
Authority-Scope: All repository work
Supersedes: Previous workspace runtime-anchor guidance
Superseded-By: —
Last-Verified: 2026-07-14

## Current mainline

- Read [`docs/chatbox/phase-plan-v0.md`](docs/chatbox/phase-plan-v0.md) first. It is authoritative for chatbox v0 scope, architecture, contracts, P1–P4 gates, and acceptance.
- Use [`docs/design/README.md`](docs/design/README.md) only for identity, persona, relationship posture, expression tendency, and anti-drift boundaries.
- The new implementation entry is `app/chatbox/`; its absence or incompleteness means "not implemented yet", not permission to reactivate an older runtime.

## Quarantine and authority

- `app/chatbox/` may depend only on `src/core/`, evaluated portions of `src/relationship/`, standard persistence facilities, and `config/`, as frozen by the Phase plan.
- `agentlib/`, `agent_kernel/`, `src/semantic_trigger/`, and `demos/scenarios/` are quarantined from chatbox v0. Do not import, adapt, or use their metrics as v0 authority.
- [`docs/archive/README.md`](docs/archive/README.md) indexes historical material. Archive content and [`docs/archive/legacy-continuity/`](docs/archive/legacy-continuity/) never define current runtime or persona.

## Universal prohibitions

- Do not change frozen Phase decisions, acceptance gates, or contracts through implementation or documentation drift.
- Do not make writer code write state directly; writer may move attractor only.
- Do not hard-code the dimension count, add hard clamp behavior, or replace emergent `P_talk` with scheduled proactive messages.
- Do not present Aphrodite as a generic agent, assistant, NPC, emotion-label engine, companion product, or productivity tool.

## Review gate

Every review must check: changes under `tests/`; imports beyond the quarantine whitelist; any writer path that writes state directly; and any modification or weakening of frozen acceptance contracts. Detailed governance: [`docs/governance/pr-governance.md`](docs/governance/pr-governance.md).
