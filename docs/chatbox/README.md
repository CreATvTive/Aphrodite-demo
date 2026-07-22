# Aphrodite chatbox v0

Status: CURRENT ARCHITECTURE INDEX
Authority-Scope: Chatbox v0 navigation and implementation status
Supersedes: Previous runtime/demo entry guidance
Superseded-By: —
Last-Verified: 2026-07-14

## Authority

[`phase-plan-v0.md`](phase-plan-v0.md) is the byte-faithful authoritative source for scope, architecture, frozen contracts, P1–P4 gates, and acceptance. This index does not amend it.

Identity and anti-drift interpretation is separately governed by [`../design/README.md`](../design/README.md). If the two domains appear to conflict, do not let identity material invent engineering details and do not let implementation framing erase Aphrodite's subject-position or presence boundaries.

## Current implementation status

The Phase freezes the new entry as `app/chatbox/`. No implementation under that path is documented as complete here. Existing `agentlib/`, `agent_kernel/`, `src/semantic_trigger/`, and `demos/scenarios/` assets remain quarantined and are not fallback implementations.

## Contracts

The frozen C-section contracts currently remain in the Phase source. [`contracts/README.md`](contracts/README.md) records the split policy and future owner decisions without creating empty contract files.

Review governance is in [`../governance/pr-governance.md`](../governance/pr-governance.md). Historical predecessors are indexed from [`../archive/README.md`](../archive/README.md).
