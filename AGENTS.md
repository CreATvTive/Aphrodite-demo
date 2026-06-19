# AGENTS.md - Aphrodite Project Workspace

## Current Scope

This repository is the Aphrodite project workspace. Determine current context from the task, current runtime code, tests, and explicitly authoritative design documentation.

- Consult `docs/design/README.md` before work that affects Aphrodite design continuity or persona boundaries.
- Treat `agentlib/runtime_engine.py`, `agent_kernel/`, `agentlib/autonomy/`, `agentlib/companion_rag.py`, `src/interpreter/`, `src/core/`, `src/memory/`, `src/relationship/`, and `src/body/` as current runtime anchors unless repository evidence indicates otherwise.
- Treat `monitor/` persona or runtime-state data as sensitive; do not modify it unless the task explicitly requires it.

## Archived Continuity Material

Legacy assistant workspace-continuity material is preserved under `docs/archive/legacy-continuity/`.

- Do not treat archived `USER.md`, `MEMORY.md`, `SOUL.md`, `IDENTITY.md`, `HEARTBEAT.md`, `TOOLS.md`, or dated memory notes as required current project context.
- Do not load archived continuity material into runtime/persona decisions unless the user explicitly asks for historical review.

## Working Rules

- Preserve current runtime behavior unless the task calls for implementation changes.
- Preserve authoritative design materials; archive or mark non-mainline material before considering deletion.
- Do not expose private data or move sensitive data into public-facing artifacts without explicit direction.
