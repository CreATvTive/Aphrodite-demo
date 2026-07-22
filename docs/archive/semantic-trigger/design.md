# Semantic Intent Trigger Engine Design

> Archived semantic-trigger design; quarantined from chatbox v0.

## Goal
Given a user query, output:
- trigger / no-trigger / ask-clarification
- selected trigger and confidence
- top-k candidates with recall/rerank scores
- extracted/missing slots
- decision reasons + debug trace

## Layered Architecture
1. Layer 1 Recall (`retriever.py`)
- Embedding + lexical hybrid recall.
- Builds candidate list with `recall_score`.

2. Layer 2 Rerank (`reranker.py`)
- Baseline cross-encoder style interface.
- Uses alias hit, positive example similarity, negative penalties, and priority.

3. Layer 3 Calibration (`calibrator.py`)
- Uses top1/top2/margin, per-trigger thresholds, consistency, constraints, and slot completeness.
- Emits `trigger`, `ask_clarification`, or `no_trigger`.

4. Layer 4 Optional Adjudicator (`adjudicator.py`)
- Hook for boundary-case arbitration.
- Mock implementation provided.

## Core Modules
- `schemas.py`: dataclasses for trigger schema, candidate scores, result model.
- `registry.py`: load + validate trigger library.
- `slot_extractor.py`: rule baseline for required/optional slots.
- `constraints.py`: hard constraints checker.
- `engine.py`: orchestration pipeline + structured trace.
- `metrics.py`: batch evaluation metrics and confusion analysis.

## Extensibility Points
- Replace `EmbeddingProvider` with API embeddings.
- Replace `BaselineReranker` with cross-encoder/rerank API.
- Replace `RuleSlotExtractor` with model-based slot extraction.
- Enable `Adjudicator` for low-confidence or conflicting cases.
