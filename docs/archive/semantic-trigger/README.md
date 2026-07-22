# Semantic-trigger archive

The semantic-trigger path is quarantined from chatbox v0. This archive preserves its design, schema, and unique debugging knowledge without presenting the subsystem as current runtime.

## Consolidated debugging record

Historical diagnosis used a contract-first loop: reproduce one query with trigger debug output; inspect top candidates, recall/rerank/final scores, top1/top2 margin, hard constraints, extracted/missing slots, and decision reasons; classify false trigger, missed trigger, or over-clarification; then adjust aliases/positive examples, hard negatives, slot requirements, or per-trigger thresholds before batch evaluation.

Unique retained guidance:

- hard negatives should be lexically similar but out of scope, such as “message queue tutorial” versus a send-message trigger;
- tune per-trigger thresholds before global thresholds and inspect TP/FP/FN score distributions;
- keep fallback triggers constrained and preserve per-sample traces for replay/confusion analysis;
- historical runtime integration and environment flags were implementation snapshots, not current guarantees.

The two former root debugging guides were consolidated here because they substantially overlapped and referenced stale commands/data paths. Their unique failure taxonomy, scoring checks, hard-negative guidance, and historical integration note are preserved above; the duplicate originals were removed.

Current authority: [`../../chatbox/phase-plan-v0.md`](../../chatbox/phase-plan-v0.md).
