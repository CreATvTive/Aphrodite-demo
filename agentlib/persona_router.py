from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np


@dataclass(frozen=True)
class PersonaRouteDecision:
    persona: str
    confidence: float
    reason: str
    scores: Dict[str, float]


AUTO_ROUTE_CANDIDATES: frozenset = frozenset({"coach", "analyst", "codex5.2"})


def detect_persona_from_text(user_text: str, state: Dict[str, Any]) -> PersonaRouteDecision:
    t = (user_text or "").strip().lower()
    scores: Dict[str, float] = {
        "coach": 0.25,
        "analyst": 0.25,
        "codex5.2": 0.25,
    }

    # 1) Embedding-first routing — only contributes to auto-route candidates.
    emb_scores = _embedding_scores(t)
    if emb_scores:
        for k, v in emb_scores.items():
            if k not in AUTO_ROUTE_CANDIDATES:
                continue
            scores[k] = scores.get(k, 0.0) + float(v) * 0.75

    # 2) Light keyword fallback/boost — aphrodite is NOT an auto-route target.
    #    Emotional/comfort signals must never auto-select aphrodite; that persona
    #    is reserved for explicit configuration.
    _apply_keywords(scores, t, "coach", ["plan", "todo", "checklist", "deadline", "execute", "schedule"], 0.10)
    _apply_keywords(scores, t, "analyst", ["tradeoff", "compare", "risk", "assumption", "evidence", "analyze"], 0.10)
    _apply_keywords(
        scores,
        t,
        "codex5.2",
        ["codex", "agent", "refactor", "fix", "patch", "debug", "implement", "code review", "test"],
        0.10,
    )

    # 3) Topic prior — no emotion-topic boost for aphrodite.
    topic = str(state.get("topic") or "").lower()
    if topic in {"planning", "work"}:
        scores["coach"] += 0.08
    if topic in {"tech"}:
        scores["analyst"] += 0.08
    if topic in {"tech"}:
        scores["codex5.2"] += 0.10

    best, best_score = max(scores.items(), key=lambda kv: kv[1])
    second_score = sorted(scores.values(), reverse=True)[1]
    confidence = max(0.0, min(1.0, (best_score - second_score) + 0.55))
    reason = (
        f"best={best}:{best_score:.2f}, margin={(best_score-second_score):.2f}, "
        f"topic={topic or '-'}, emb={int(bool(emb_scores))}"
    )
    return PersonaRouteDecision(
        persona=best,
        confidence=float(confidence),
        reason=reason,
        scores={k: float(v) for k, v in scores.items()},
    )


def _apply_keywords(scores: Dict[str, float], text: str, target: str, keywords: List[str], weight: float) -> None:
    hits = 0
    for kw in keywords:
        if kw and kw in text:
            hits += 1
    if hits <= 0:
        return
    scores[target] += min(0.25, hits * weight)


_MODEL_LOCK = threading.RLock()
_MODEL = None
_PERSONA_EMBS: Optional[Dict[str, np.ndarray]] = None


def _embedding_scores(text: str) -> Dict[str, float]:
    if not text:
        return {}
    model = _get_model()
    if model is None:
        return {}
    persona_embs = _get_persona_embeddings(model)
    if not persona_embs:
        return {}
    try:
        q = model.encode([text], show_progress_bar=False, normalize_embeddings=True)
        qv = np.asarray(q[0], dtype=np.float32)
    except Exception:
        return {}

    out: Dict[str, float] = {}
    for persona, pv in persona_embs.items():
        sim = float(np.dot(qv, pv))
        out[persona] = max(0.0, min(1.0, (sim + 1.0) * 0.5))
    return out


def _get_model():
    global _MODEL
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        try:
            from sentence_transformers import SentenceTransformer

            model_name = (os.getenv("RAG_EMBED_MODEL") or "BAAI/bge-small-zh-v1.5").strip()
            _MODEL = SentenceTransformer(model_name)
        except Exception:
            _MODEL = None
        return _MODEL


def _get_persona_embeddings(model) -> Dict[str, np.ndarray]:
    global _PERSONA_EMBS
    with _MODEL_LOCK:
        if _PERSONA_EMBS is not None:
            return _PERSONA_EMBS
        prototypes = {
            "coach": (
                "execution coach, planning checklist priorities deadlines, concrete action, "
                "accountability and progress"
            ),
            "analyst": (
                "rigorous analyst, tradeoffs assumptions evidence risk evaluation, "
                "structured reasoning and recommendation"
            ),
            "codex5.2": (
                "coding agent, patch code, debug and test, tool usage, implementation quality, "
                "pragmatic engineering execution"
            ),
        }
        try:
            vecs = model.encode(
                [prototypes["coach"], prototypes["analyst"], prototypes["codex5.2"]],
                show_progress_bar=False,
                normalize_embeddings=True,
            )
            _PERSONA_EMBS = {
                "coach": np.asarray(vecs[0], dtype=np.float32),
                "analyst": np.asarray(vecs[1], dtype=np.float32),
                "codex5.2": np.asarray(vecs[2], dtype=np.float32),
            }
        except Exception:
            _PERSONA_EMBS = {}
        return _PERSONA_EMBS
