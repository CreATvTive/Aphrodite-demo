from __future__ import annotations

import os
import re
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, cast, runtime_checkable

from ._utils import _dedup_keep_order_stripped_nonempty


@dataclass(frozen=True)
class RagCandidate:
    text: str
    score: float
    source: str = "keyword"


@dataclass(frozen=True)
class RagConfig:
    mode: str = "hybrid"
    corrective_enabled: bool = True
    corrective_min_score: float = 0.08
    iterative_enabled: bool = True
    iterative_max_queries: int = 2
    candidate_pool_size: int = 8
    diversity_enabled: bool = True
    diversity_min_jaccard_gap: float = 0.08
    self_rag_enabled: bool = True
    self_min_query_chars: int = 3
    self_second_pass_enabled: bool = True
    self_second_pass_min_top_score: float = 0.26
    hybrid_embed_weight: float = 0.7
    hybrid_keyword_weight: float = 0.3
    debug_enabled: bool = False
    memory_enabled: bool = True


@dataclass(frozen=True)
class RagResult:
    items: List[str] = field(default_factory=list)
    trace: List[str] = field(default_factory=list)
    queries: List[str] = field(default_factory=list)
    mode_used: str = "keyword"
    retrieval_used: bool = False
    skip_reason: str = ""


_ENGINE_LOCK = threading.RLock()
_ENGINE = None
_MEMORY_LOCK = threading.RLock()
_MEMORY_STORE = None


@runtime_checkable
class _MemoryStoreLike(Protocol):
    def add_many(self, texts: List[str]) -> None: ...

    def retrieve(self, query: str, k: int = 4) -> List[str]: ...


@runtime_checkable
class _RagEngineLike(Protocol):
    def sync_docs(self, texts: Sequence[str], persist: bool = False) -> None: ...

    def retrieve_scored(self, query: str, top_k: int) -> Sequence[Dict[str, Any]]: ...


class _EphemeralMemoryStore:
    def __init__(self) -> None:
        self._items: List[str] = []

    def add_many(self, texts: List[str]) -> None:
        for t in texts:
            s = str(t).strip()
            if s:
                self._items.append(s)

    def retrieve(self, query: str, k: int = 4) -> List[str]:
        q = str(query or "").strip().lower()
        if not q:
            return self._items[-max(1, int(k)) :]
        out = [x for x in self._items if q in x.lower()]
        if not out:
            out = list(self._items)
        return out[-max(1, int(k)) :]


def load_rag_config() -> RagConfig:
    def _b(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    def _i(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except Exception:
            return default

    def _f(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, str(default)))
        except Exception:
            return default

    return RagConfig(
        mode=(os.getenv("RAG_MODE") or "hybrid").strip().lower(),
        corrective_enabled=_b("RAG_CORRECTIVE_ENABLED", True),
        corrective_min_score=_f("RAG_CORRECTIVE_MIN_SCORE", 0.08),
        iterative_enabled=_b("RAG_ITERATIVE_ENABLED", True),
        iterative_max_queries=max(1, _i("RAG_ITERATIVE_MAX_QUERIES", 2)),
        candidate_pool_size=max(1, _i("RAG_CANDIDATE_POOL_SIZE", 8)),
        diversity_enabled=_b("RAG_DIVERSITY_ENABLED", True),
        diversity_min_jaccard_gap=_f("RAG_DIVERSITY_MIN_JACCARD_GAP", 0.08),
        self_rag_enabled=_b("RAG_SELF_ENABLED", True),
        self_min_query_chars=max(1, _i("RAG_SELF_MIN_QUERY_CHARS", 3)),
        self_second_pass_enabled=_b("RAG_SELF_SECOND_PASS_ENABLED", True),
        self_second_pass_min_top_score=_f("RAG_SELF_SECOND_PASS_MIN_TOP_SCORE", 0.26),
        hybrid_embed_weight=_f("RAG_HYBRID_EMBED_WEIGHT", 0.7),
        hybrid_keyword_weight=_f("RAG_HYBRID_KEYWORD_WEIGHT", 0.3),
        debug_enabled=_b("RAG_DEBUG", False),
        memory_enabled=_b("RAG_MEMORY_ENABLED", True),
    )


def build_rag_context(
    user_text: str,
    knowledge_base: Optional[Sequence[str]] = None,
    top_k: int = 3,
    rag_mode: Optional[str] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    config: Optional[RagConfig] = None,
) -> List[str]:
    result = build_rag_package(
        user_text=user_text,
        knowledge_base=knowledge_base,
        top_k=top_k,
        rag_mode=rag_mode,
        history=history,
        config=config,
    )
    return list(result.items)


def build_rag_package(
    user_text: str,
    knowledge_base: Optional[Sequence[str]] = None,
    top_k: int = 3,
    rag_mode: Optional[str] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    config: Optional[RagConfig] = None,
) -> RagResult:
    cfg = config or load_rag_config()
    mode = (rag_mode or cfg.mode or "keyword").strip().lower()
    kb = [str(x).strip() for x in (knowledge_base or []) if str(x).strip()]
    if not kb:
        return RagResult(mode_used=mode, retrieval_used=False, skip_reason="empty_kb")

    query = str(user_text or "").strip()
    if cfg.self_rag_enabled and len(query) < int(cfg.self_min_query_chars):
        return RagResult(mode_used=mode, retrieval_used=False, skip_reason="self_rag_low_info")

    queries = [query]
    trace: List[str] = []
    if cfg.iterative_enabled and int(cfg.iterative_max_queries) > 1:
        for q in _build_second_pass_queries(query, history=history):
            if q and q not in queries:
                queries.append(q)
            if len(queries) >= int(cfg.iterative_max_queries):
                break
    trace.append(f"queries={len(queries)}")

    scored_all: List[Dict[str, Any]] = []
    pool_k = max(int(top_k), int(cfg.candidate_pool_size))
    for q in queries:
        scored = _retrieve_scored(mode=mode, query=q, kb=kb, top_k=pool_k, cfg=cfg)
        scored_all.extend(scored)

    scored_all = _merge_dedup_scored(scored_all)
    scored_all = _apply_corrective_filter_scored(
        user_text=query,
        scored_items=scored_all,
        top_k=pool_k,
        min_score=float(cfg.corrective_min_score),
        enabled=bool(cfg.corrective_enabled),
    )

    top_score = float(scored_all[0]["score"]) if scored_all else 0.0
    if (
        cfg.self_second_pass_enabled
        and top_score < float(cfg.self_second_pass_min_top_score)
        and query
    ):
        trace.append("self_rag_second_pass=1")
        extra_scored: List[Dict[str, Any]] = []
        for q2 in _build_second_pass_queries(query, history=history):
            if not q2:
                continue
            extra_scored.extend(_retrieve_scored(mode=mode, query=q2, kb=kb, top_k=pool_k, cfg=cfg))
        if extra_scored:
            scored_all = _merge_dedup_scored(scored_all + extra_scored)
            scored_all = _apply_corrective_filter_scored(
                user_text=query,
                scored_items=scored_all,
                top_k=pool_k,
                min_score=float(cfg.corrective_min_score),
                enabled=bool(cfg.corrective_enabled),
            )

    if cfg.diversity_enabled:
        scored_all = _apply_diversity(scored_all, min_jaccard_gap=float(cfg.diversity_min_jaccard_gap))

    items = [str(x["text"]) for x in scored_all[: max(0, int(top_k))]]
    if not items:
        return RagResult(
            items=[],
            trace=trace,
            queries=queries,
            mode_used=mode,
            retrieval_used=False,
            skip_reason="no_match",
        )
    return RagResult(
        items=items,
        trace=trace,
        queries=queries,
        mode_used=mode,
        retrieval_used=True,
        skip_reason="",
    )


def render_rag_block(rag_items: Sequence[str]) -> str:
    clean = [str(x).strip() for x in rag_items if str(x).strip()]
    if not clean:
        return ""
    lines = ["[retrieval_context]", "Use these as soft context, not strict constraints:"]
    for item in clean:
        lines.append(f"- {item}")
    return "\n".join(lines).strip()


def render_rag_trace(trace: Sequence[str]) -> str:
    clean = [str(x).strip() for x in trace if str(x).strip()]
    if not clean:
        return ""
    return "\n".join(f"- {x}" for x in clean)


def is_memory_enabled() -> bool:
    raw = os.getenv("RAG_MEMORY_ENABLED")
    if raw is None:
        return True
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def get_memory_store() -> Optional[_MemoryStoreLike]:
    global _MEMORY_STORE
    if not is_memory_enabled():
        return None
    with _MEMORY_LOCK:
        if _MEMORY_STORE is not None:
            return _MEMORY_STORE
        try:
            from .memory_store import MemoryStore

            _MEMORY_STORE = MemoryStore()
        except Exception:
            _MEMORY_STORE = _EphemeralMemoryStore()
        return _MEMORY_STORE


def get_memory_status() -> Dict[str, Any]:
    s = get_memory_store()
    return {"enabled": is_memory_enabled(), "ready": bool(s)}


def retrieve_memory_context(
    user_text: str,
    history: Optional[List[Dict[str, Any]]] = None,
    k: int = 4,
) -> List[str]:
    store = _runtime_get_memory_store()
    if store is None:
        return []
    query = str(user_text or "").strip()
    if not query and history:
        for item in reversed(history):
            if str(item.get("role") or "") == "user":
                query = str(item.get("content") or "").strip()
                if query:
                    break
    if not query:
        return []
    try:
        items = store.retrieve(query, k=max(1, int(k)))
    except Exception:
        return []
    return [str(x).strip() for x in (items or []) if str(x).strip()]


def record_turn_memory(
    user_text: str,
    assistant_text: str,
    explicit_items: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    store = _runtime_get_memory_store()
    if store is None:
        return {"ok": False, "stored": 0, "reason": "memory_unavailable"}
    candidates: List[str] = []
    candidates.extend([str(x).strip() for x in (explicit_items or []) if str(x).strip()])
    candidates.extend(_extract_user_memory_candidates(user_text))
    if not candidates:
        return {"ok": True, "stored": 0}
    candidates = _dedup_keep_order(candidates)

    filtered: List[str] = []
    should_store = None
    try:
        from .memory_store import should_store_memory as _should_store_memory

        should_store = _should_store_memory
    except Exception:
        should_store = None

    for c in candidates:
        if should_store is not None:
            try:
                if not bool(should_store(c)):
                    continue
            except Exception:
                pass
        filtered.append(c)

    if not filtered:
        return {"ok": True, "stored": 0}
    try:
        store.add_many(filtered)
        return {"ok": True, "stored": len(filtered), "items": filtered}
    except Exception as e:
        return {"ok": False, "stored": 0, "reason": f"{type(e).__name__}: {e}"}


def _retrieve_scored(mode: str, query: str, kb: List[str], top_k: int, cfg: RagConfig) -> List[Dict[str, Any]]:
    m = (mode or "keyword").lower()
    if m == "embedding":
        out = _retrieve_embedding_scored(query, kb, top_k=top_k)
        if out:
            return out
        return _retrieve_keyword_scored(query, kb, top_k=top_k)
    if m == "hybrid":
        ek = _retrieve_embedding_scored(query, kb, top_k=top_k)
        kk = _retrieve_keyword_scored(query, kb, top_k=top_k)
        if not ek:
            return kk
        merged: Dict[str, Dict[str, Any]] = {}
        for row in ek:
            merged[str(row["text"])] = {
                "text": str(row["text"]),
                "score": float(row["score"]) * float(cfg.hybrid_embed_weight),
            }
        for row in kk:
            t = str(row["text"])
            s = float(row["score"]) * float(cfg.hybrid_keyword_weight)
            if t in merged:
                merged[t]["score"] = float(merged[t]["score"]) + s
            else:
                merged[t] = {"text": t, "score": s}
        out = list(merged.values())
        out.sort(key=lambda x: float(x["score"]), reverse=True)
        return out[: max(1, int(top_k))]
    return _retrieve_keyword_scored(query, kb, top_k=top_k)


def _retrieve_keyword_scored(query: str, kb: List[str], top_k: int) -> List[Dict[str, Any]]:
    q_terms = _tokenize(query)
    if not q_terms:
        return [{"text": t, "score": 0.0} for t in kb[: max(1, int(top_k))]]
    out: List[Dict[str, Any]] = []
    for text in kb:
        t_terms = _tokenize(text)
        if not t_terms:
            continue
        overlap = len(q_terms.intersection(t_terms))
        if overlap <= 0:
            continue
        score = overlap / max(1.0, float(len(q_terms)))
        out.append({"text": text, "score": float(score)})
    out.sort(key=lambda x: float(x["score"]), reverse=True)
    return out[: max(1, int(top_k))]


def _retrieve_embedding_scored(query: str, kb: List[str], top_k: int) -> List[Dict[str, Any]]:
    eng = _get_engine()
    if eng is None:
        return []
    try:
        eng.sync_docs(kb, persist=False)
        rows = eng.retrieve_scored(query, top_k=max(1, int(top_k)))
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for r in rows or []:
        t = str((r or {}).get("text") or "").strip()
        if not t:
            continue
        s = float((r or {}).get("score") or 0.0)
        out.append({"text": t, "score": s})
    out.sort(key=lambda x: float(x["score"]), reverse=True)
    return out[: max(1, int(top_k))]


def _get_engine() -> Optional[_RagEngineLike]:
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is not None:
            return _ENGINE
        try:
            from .memory_store import MemoryStore

            _ENGINE = MemoryStore()
        except Exception:
            _ENGINE = None
        if _ENGINE is None:
            return None
        return cast(Optional[_RagEngineLike], _ENGINE)


def _merge_dedup_scored(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, float] = {}
    for row in items:
        t = str((row or {}).get("text") or "").strip()
        if not t:
            continue
        s = float((row or {}).get("score") or 0.0)
        if t in merged:
            merged[t] = max(merged[t], s)
        else:
            merged[t] = s
    out = [{"text": k, "score": v} for k, v in merged.items()]
    out.sort(key=lambda x: float(x["score"]), reverse=True)
    return out


def _apply_corrective_filter_scored(
    user_text: str,
    scored_items: Sequence[Dict[str, Any]],
    top_k: int,
    min_score: float,
    enabled: bool,
) -> List[Dict[str, Any]]:
    rows = [{"text": str(x["text"]), "score": float(x["score"])} for x in scored_items if str(x.get("text", "")).strip()]
    rows.sort(key=lambda x: float(x["score"]), reverse=True)
    if not enabled:
        return rows[: max(1, int(top_k))]
    out = [x for x in rows if float(x["score"]) >= float(min_score)]
    if not out and rows:
        out = rows[:1]
    return out[: max(1, int(top_k))]


def _apply_diversity(scored_items: Sequence[Dict[str, Any]], min_jaccard_gap: float = 0.08) -> List[Dict[str, Any]]:
    picked: List[Dict[str, Any]] = []
    for row in scored_items:
        text = str(row.get("text") or "")
        terms = _tokenize(text)
        keep = True
        for p in picked:
            gap = 1.0 - _jaccard(terms, _tokenize(str(p.get("text") or "")))
            if gap < float(min_jaccard_gap):
                keep = False
                break
        if keep:
            picked.append({"text": text, "score": float(row.get("score") or 0.0)})
    return picked


def _build_second_pass_queries(user_text: str, history: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    t = str(user_text or "").strip()
    out: List[str] = []
    if len(t) >= 2:
        terms = list(_tokenize(t))
        if terms:
            out.append(" ".join(terms[: min(4, len(terms))]))
        if len(terms) >= 2:
            out.append(" ".join(terms[-2:]))
    if history:
        for msg in reversed(history):
            if str(msg.get("role") or "") == "user":
                prev = str(msg.get("content") or "").strip()
                if prev and prev != t:
                    out.append(prev[:120])
                    break
    return _dedup_keep_order([x for x in out if x.strip()])


def _extract_user_memory_candidates(user_text: str) -> List[str]:
    t = str(user_text or "").strip()
    if not t:
        return []
    parts = re.split(r"[。！？!?；;\n]+", t)
    out: List[str] = []
    for p in parts:
        s = p.strip()
        if not s:
            continue
        if len(s) <= 3:
            continue
        # Keep personal preference/fact style lines first.
        if any(k in s for k in ["我", "喜欢", "不喜欢", "过敏", "习惯", "偏好"]):
            out.append(s)
    if not out and len(t) > 6:
        out.append(t[:120])
    return _dedup_keep_order(out)


def _runtime_get_memory_store() -> Optional[_MemoryStoreLike]:
    mod = sys.modules.get("agentlib.companion_rag")
    if mod is not None:
        fn = getattr(mod, "get_memory_store", None)
        if callable(fn) and fn is not _runtime_get_memory_store:
            try:
                store = cast(Callable[[], Optional[_MemoryStoreLike]], fn)()
                if store is None:
                    return None
                if isinstance(store, _MemoryStoreLike):
                    return store
                return None
            except Exception:
                return None
    return get_memory_store()


def _tokenize(text: str) -> set[str]:
    parts = str(text).lower().replace("\n", " ").split(" ")
    return {p.strip(".,!?;:()[]{}\"'") for p in parts if p.strip(".,!?;:()[]{}\"'")}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return float(len(a.intersection(b))) / float(len(a.union(b)))


def _dedup_keep_order(items: List[str]) -> List[str]:
    return _dedup_keep_order_stripped_nonempty(items)
