# Trigger Protocol Schema

This document describes the shared protocol contract for trigger data and engine output.

## TriggerDef

Required semantic fields:
- `trigger_id: str`
- `name: str`
- `description: str`
- `aliases: list[str]`
- `positive_examples: list[str]`
- `negative_examples: list[str]`
- `required_slots: list[dict]`
- `optional_slots: list[dict]`
- `enabled: bool`
- `tags: list[str]`

Optional extension fields (implementation-specific, backward compatible):
- `hard_constraints: list[dict]`
- `priority: int`
- `metadata: dict`

## CandidateScore

Canonical contract fields:
- `trigger_id: str`
- `recall_score: float | None`
- `rerank_score: float | None`
- `final_score: float | None`
- `notes: str | None`

Compatibility note:
- some implementations may expose `combined_score` as alias of `final_score`.

## EngineResult

Canonical output fields:
- `user_query: str`
- `decision: str`  (`trigger | no_trigger | ask_clarification`)
- `selected_trigger: str | None`
- `confidence: float`
- `candidates: list[CandidateScore]`
- `extracted_slots: dict`
- `missing_slots: list[str]`
- `clarification_question: str | None`
- `reasons: list[str]`
- `debug: dict`

Compatibility note:
- some implementations may still expose `debug_trace`; treat it as backward-compatible alias for `debug`.

## Slot Dict Shape (recommended)

- `slot_name: str`
- `slot_type: str`  (`string/int/date/time/location/enum/json`)
- `required: bool`
- `extraction_hints: list[str]` (optional)
- `validation_rules: dict` (optional)

## Decision Semantics

- `trigger`: direct execution is safe
- `ask_clarification`: candidate exists but missing critical info or ambiguity is high
- `no_trigger`: out-of-scope, weak relevance, or non-actionable smalltalk
