from __future__ import annotations

import math
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple

import numpy as np
try:
    import faiss  # type: ignore
except BaseException as _faiss_err:
    faiss = None  # type: ignore[assignment]
    _FAISS_IMPORT_ERROR = _faiss_err
else:
    _FAISS_IMPORT_ERROR = None

try:
    from sentence_transformers import SentenceTransformer as _SentenceTransformer
except BaseException as _st_err:
    _SentenceTransformer = None  # type: ignore[assignment]
    _SENTENCE_TRANSFORMERS_IMPORT_ERROR = _st_err
else:
    _SENTENCE_TRANSFORMERS_IMPORT_ERROR = None

from ._utils import _dedup_keep_order_values
from .learned_lists import LearnedLists, ListState, refresh_state


_ZH_RE = re.compile(r"[\u4e00-\u9fff]+")
_REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATED_ARTIFACT_ROOT = _REPO_ROOT / "var"
DEFAULT_MEMORY_DB_PATH = GENERATED_ARTIFACT_ROOT / "db" / "memory.sqlite"
DEFAULT_MEMORY_INDEX_PATH = GENERATED_ARTIFACT_ROOT / "indexes" / "memory.faiss"
DEFAULT_MEMORY_IDS_PATH = GENERATED_ARTIFACT_ROOT / "indexes" / "memory_ids.npy"


@dataclass(frozen=True)
class PhraseFilter:
    stop_phrases: set[str]
    stop_chars: set[str]
    allow_single: set[str]

    @staticmethod
    def from_state(state: ListState) -> "PhraseFilter":
        return PhraseFilter(
            stop_phrases=set(state.stop_phrases),
            stop_chars=set(state.stop_chars),
            allow_single=set(state.allow_single),
        )


def memory_weight(now_ts: int, last_seen: int, seen_count: int, strength: float) -> float:
    if last_seen <= 0:
        return 0.0
    age = max(0, now_ts - last_seen)
    half_life_days = 14.0
    lam = math.log(2) / (half_life_days * 86400.0)
    decay = math.exp(-lam * age)
    seen_boost = 1.0 + 0.08 * max(0, int(seen_count))
    return float(decay * seen_boost * float(strength))


def should_store_memory(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 6:
        return False

    bad = ["你好", "谢谢", "哈哈", "我不理解", "我在听", "抱抱", "呜呜", "好的"]
    if any(b in t for b in bad):
        return False

    good = [
        "我喜欢", "我不喜欢", "我讨厌", "我过敏", "我不能", "我希望", "我计划", "我工作", "我学习",
        "用户喜欢", "用户不喜欢", "用户讨厌", "用户过敏", "用户对", "用户不能", "用户希望", "用户计划", "用户工作", "用户学习",
    ]
    return any(g in t for g in good)


def _dedup_keep_order(items: List[str]) -> List[str]:
    return _dedup_keep_order_values(items)


def phrase_candidates(text: str, phrase_filter: PhraseFilter, max_candidates: int = 80) -> List[str]:
    t = (text or "").strip()
    if not t:
        return []

    chunks = _ZH_RE.findall(t)
    if not chunks:
        return []
    s = "".join(chunks)

    for bad in ("用户", "我", "你"):
        if s.startswith(bad):
            s = s[len(bad):]
    for bad in ("喜欢", "不喜欢", "讨厌", "过敏", "不能", "希望", "计划"):
        s = s.replace(bad, "")
    s = s.replace("的", "")

    s = s.strip()
    if not s:
        return []

    cands: List[str] = []
    L = len(s)
    for n in range(1, 6):
        for i in range(0, L - n + 1):
            p = s[i:i+n].strip()
            if not p:
                continue
            if p in phrase_filter.stop_phrases:
                continue
            if len(p) == 1 and (p in phrase_filter.stop_chars) and (p not in phrase_filter.allow_single):
                continue
            if all(ch in phrase_filter.stop_chars for ch in p) and p not in phrase_filter.allow_single:
                continue
            cands.append(p)

    cands = _dedup_keep_order(cands)
    cands.sort(key=lambda x: (-len(x), x))
    return cands[:max_candidates]


def _learning_tokens_from_text(text: str, phrase_filter: PhraseFilter) -> List[str]:
    if not text:
        return []
    cands = phrase_candidates(text, phrase_filter, max_candidates=60)
    ascii_words = re.findall(r"[A-Za-z]{2,16}", text)
    cands.extend([w.lower() for w in ascii_words])
    return _dedup_keep_order([c for c in cands if c])


# ========== Hybrid RAG: 混合检索（关键词 + 向量） ==========

def hybrid_retrieve(
    query: str,
    memory_items: List[Dict[str, Any]],
    phrase_filter: PhraseFilter,
    vector_index: Optional[Any] = None,
    embeddings: Optional[np.ndarray] = None,
    k: int = 6,
    keyword_weight: float = 0.4,
    vector_weight: float = 0.6,
) -> List[Dict[str, Any]]:
    """
    Hybrid RAG: 混合检索（关键词 + 向量）
    
    Args:
        query: 查询文本
        memory_items: 记忆列表
        phrase_filter: 关键词过滤器
        vector_index: FAISS 索引（可选）
        embeddings: 记忆嵌入向量（可选）
        k: 返回数量
        keyword_weight: 关键词权重（默认 0.4）
        vector_weight: 向量权重（默认 0.6）
    
    Returns:
        检索结果（按混合分数排序）
    """
    if not memory_items:
        return []
    
    # 1. 关键词检索
    query_keywords = set(phrase_candidates(query, phrase_filter, max_candidates=20))
    keyword_scores = []
    
    for item in memory_items:
        text = item.get('text', '')
        item_keywords = set(phrase_candidates(text, phrase_filter, max_candidates=40))
        
        # Jaccard 相似度
        if query_keywords and item_keywords:
            intersection = len(query_keywords & item_keywords)
            union = len(query_keywords | item_keywords)
            keyword_score = intersection / union if union > 0 else 0.0
        else:
            keyword_score = 0.0
        
        keyword_scores.append(keyword_score)
    
    # 2. 向量检索（如果可用）
    vector_scores = np.zeros(len(memory_items))
    if vector_index is not None and embeddings is not None and len(embeddings) > 0:
        try:
            from sentence_transformers import SentenceTransformer
            # 查询嵌入
            model = SentenceTransformer('BAAI/bge-small-zh-v1.5')
            query_emb = model.encode([query], normalize_embeddings=True, show_progress_bar=False)
            query_vec = np.asarray(query_emb[0], dtype=np.float32)
            
            # FAISS 检索
            D, I = vector_index.search(query_vec.reshape(1, -1), min(k * 2, len(memory_items)))
            
            # 归一化向量分数到 0-1
            if len(D[0]) > 0:
                max_score = D[0][0] if D[0][0] > 0 else 1.0
                for i, idx in enumerate(I[0]):
                    if idx < len(memory_items):
                        vector_scores[idx] = D[0][i] / max_score
        except Exception as e:
            print(f"[Hybrid RAG] 向量检索失败：{e}，降级到关键词检索")
    
    # 3. 混合分数
    hybrid_scores = []
    for i in range(len(memory_items)):
        score = keyword_weight * keyword_scores[i] + vector_weight * vector_scores[i]
        hybrid_scores.append(score)
    
    # 4. 排序并返回 top-k
    sorted_indices = np.argsort(hybrid_scores)[::-1][:k]
    
    results = []
    for idx in sorted_indices:
        item = memory_items[idx].copy()
        item['hybrid_score'] = float(hybrid_scores[idx])
        item['keyword_score'] = float(keyword_scores[idx])
        item['vector_score'] = float(vector_scores[idx])
        results.append(item)
    
    return results


def learn_lists_from_feedback(
    text: str,
    rating: float,
    learned_lists: LearnedLists,
    list_state: ListState,
) -> ListState:
    if not text:
        return list_state
    try:
        r = float(rating)
    except Exception:
        return list_state
    if r == 0:
        return list_state
    phrase_filter = PhraseFilter.from_state(list_state)
    tokens = _learning_tokens_from_text(text, phrase_filter)
    if not tokens:
        return list_state
    if r > 0:
        learned_lists.add_items("pos_words", tokens, min_len=2, max_len=12, max_add=40)
        single_chars = [t for t in tokens if len(t) == 1]
        if single_chars:
            learned_lists.add_items("allow_single", single_chars, min_len=1, max_len=1, max_add=10)
    else:
        learned_lists.add_items("neg_words", tokens, min_len=2, max_len=12, max_add=40)
    return refresh_state(learned_lists)


def _softmax(xs: np.ndarray) -> np.ndarray:
    x = xs.astype(np.float64)
    x = x - np.max(x)
    ex = np.exp(x)
    s = np.sum(ex)
    if s <= 0:
        return np.ones_like(xs, dtype=np.float64) / max(1, len(xs))
    return ex / s


class FaissIndex(Protocol):
    ntotal: int

    def add(self, x: np.ndarray) -> None: ...
    def search(self, x: np.ndarray, k: int) -> Tuple[np.ndarray, np.ndarray]: ...


class MemoryStore:
    """
    SQLite + FAISS + sentence-transformers for long-term memory.
    """

    def __init__(
        self,
        db_path: str = str(DEFAULT_MEMORY_DB_PATH),
        index_path: str = str(DEFAULT_MEMORY_INDEX_PATH),
        ids_path: str = str(DEFAULT_MEMORY_IDS_PATH),
        model_name: str = "BAAI/bge-small-zh-v1.5",
        device: Optional[str] = None,
        phrase_filter: Optional[PhraseFilter] = None,
    ):
        self.db_path = db_path
        self.index_path = index_path
        self.ids_path = ids_path
        self.phrase_filter = phrase_filter or PhraseFilter(set(), set(), set())

        if faiss is None:
            raise RuntimeError(f"faiss unavailable: {_FAISS_IMPORT_ERROR}")
        if _SentenceTransformer is None:
            raise RuntimeError(
                "sentence-transformers unavailable: "
                f"{_SENTENCE_TRANSFORMERS_IMPORT_ERROR}"
            )

        for artifact_path in (self.db_path, self.index_path, self.ids_path):
            Path(artifact_path).parent.mkdir(parents=True, exist_ok=True)

        self.model = _SentenceTransformer(model_name, device=device)
        self.dim = self.model.get_sentence_embedding_dimension()

        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db_lock = threading.RLock()
        self._init_table()

        self.index: FaissIndex = faiss.IndexFlatIP(self.dim)
        self.id_map: List[int] = []
        self.tag_cache: Dict[int, List[Tuple[str, float]]] = {}

        self._load_or_rebuild_index()
        self._ensure_tags_for_all()
        self._load_tags_cache()

    def _init_table(self) -> None:
        with self._db_lock:
            cur = self.conn.cursor()
            cur.execute(
                """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL UNIQUE,
                last_seen INTEGER NOT NULL,
                created_at INTEGER NOT NULL DEFAULT 0,
                seen_count INTEGER NOT NULL DEFAULT 0,
                strength REAL NOT NULL DEFAULT 0.7,
                archived INTEGER NOT NULL DEFAULT 0
            )
            """
            )
            cur.execute(
                """
            CREATE TABLE IF NOT EXISTS memory_tags (
                memory_id INTEGER NOT NULL,
                phrase TEXT NOT NULL,
                weight REAL NOT NULL,
                PRIMARY KEY (memory_id, phrase)
            )
            """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_memory_tags_mid ON memory_tags(memory_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_memory_tags_phrase ON memory_tags(phrase)")

            cur.execute("PRAGMA table_info(memories)")
            cols = {row[1] for row in cur.fetchall()}
            if "created_at" not in cols:
                cur.execute("ALTER TABLE memories ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0")
            if "seen_count" not in cols:
                cur.execute("ALTER TABLE memories ADD COLUMN seen_count INTEGER NOT NULL DEFAULT 0")
            if "strength" not in cols:
                cur.execute("ALTER TABLE memories ADD COLUMN strength REAL NOT NULL DEFAULT 0.6")
            if "archived" not in cols:
                cur.execute("ALTER TABLE memories ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")

            self.conn.commit()

    def _embed(self, texts: List[str]) -> np.ndarray:
        vecs = self.model.encode(
            texts,
            batch_size=32,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        return np.asarray(vecs, dtype=np.float32)

    def _fetch_all(self) -> List[Tuple[int, str]]:
        cur = self.conn.cursor()
        rows = cur.execute("SELECT id, text FROM memories ORDER BY id ASC").fetchall()
        return [(int(r[0]), str(r[1])) for r in rows]

    def _extract_weighted_tags(self, text: str, max_tags: int = 6) -> List[Tuple[str, float]]:
        t = (text or "").strip()
        if not t:
            return []

        cands = phrase_candidates(t, self.phrase_filter)
        if not cands:
            return []

        sv = self._embed([t])[0]
        cv = self._embed(cands)
        sims = cv @ sv

        n = min(int(max_tags), len(cands))
        top_idx = np.argsort(-sims)[:n]
        top_phrases = [cands[i] for i in top_idx]
        top_sims = sims[top_idx]

        weights = _softmax(top_sims * 8.0)
        out = [(p, float(w)) for p, w in zip(top_phrases, weights)]
        out.sort(key=lambda x: x[1], reverse=True)
        return out

    def _save_tags(self, memory_id: int, tags: List[Tuple[str, float]]) -> None:
        if not tags:
            return
        cur = self.conn.cursor()
        for p, w in tags:
            cur.execute(
                "INSERT OR REPLACE INTO memory_tags(memory_id, phrase, weight) VALUES (?, ?, ?)",
                (int(memory_id), str(p), float(w)),
            )
        self.conn.commit()
        self.tag_cache[int(memory_id)] = [(str(p), float(w)) for p, w in tags]

    def _load_tags_cache(self) -> None:
        cur = self.conn.cursor()
        rows = cur.execute("SELECT memory_id, phrase, weight FROM memory_tags").fetchall()
        cache: Dict[int, List[Tuple[str, float]]] = {}
        for mid, phrase, w in rows:
            cache.setdefault(int(mid), []).append((str(phrase), float(w)))
        for mid in list(cache.keys()):
            cache[mid].sort(key=lambda x: x[1], reverse=True)
        self.tag_cache = cache

    def _ensure_tags_for_all(self) -> None:
        cur = self.conn.cursor()
        rows = cur.execute(
            "SELECT m.id, m.text FROM memories m "
            "LEFT JOIN memory_tags t ON m.id = t.memory_id "
            "WHERE t.memory_id IS NULL"
        ).fetchall()
        if not rows:
            return
        for mid, mem_text in rows:
            tags = self._extract_weighted_tags(str(mem_text), max_tags=6)
            if tags:
                self._save_tags(int(mid), tags)

    @staticmethod
    def _phrase_match(a: str, b: str) -> bool:
        if a == b:
            return True
        if len(a) >= 2 and a in b:
            return True
        if len(b) >= 2 and b in a:
            return True
        return False

    def _load_or_rebuild_index(self) -> None:
        if os.path.exists(self.index_path) and os.path.exists(self.ids_path):
            try:
                self.index = faiss.read_index(self.index_path)
                self.id_map = np.load(self.ids_path).astype(np.int64).tolist()
                if self.index.ntotal != len(self.id_map):
                    raise ValueError("Index and id_map size mismatch")
                return
            except Exception:
                pass

        all_rows = self._fetch_all()
        self.index = faiss.IndexFlatIP(self.dim)
        self.id_map = []

        if not all_rows:
            self._persist_index()
            return

        ids = [rid for rid, _ in all_rows]
        texts = [txt for _, txt in all_rows]
        vecs = self._embed(texts)
        self.index.add(vecs)
        self.id_map = ids
        self._persist_index()

    def _persist_index(self) -> None:
        faiss.write_index(self.index, self.index_path)
        np.save(self.ids_path, np.asarray(self.id_map, dtype=np.int64))

    def count(self) -> int:
        cur = self.conn.cursor()
        return int(cur.execute("SELECT COUNT(*) FROM memories").fetchone()[0])

    def retrieve(self, query: str, k: int = 6) -> List[str]:
        q = (query or "").strip()
        if not q or self.index.ntotal == 0:
            return []

        qv = self._embed([q])
        cand_k = min(max(30, k * 5), int(self.index.ntotal))
        sims, idxs = self.index.search(qv, cand_k)
        sims_list = sims[0].tolist()

        idxs_list = idxs[0].tolist()
        ids = []
        sims_keep = []
        for j, faiss_i in enumerate(idxs_list):
            if 0 <= faiss_i < len(self.id_map):
                ids.append(self.id_map[faiss_i])
                sims_keep.append(float(sims_list[j]))
        sims_list = sims_keep
        if not ids:
            return []

        cur = self.conn.cursor()
        rows = cur.execute(
            f"SELECT id, text FROM memories WHERE id IN ({','.join(['?'] * len(ids))})",
            ids,
        ).fetchall()
        id2text = {int(r[0]): str(r[1]) for r in rows}

        cur = self.conn.cursor()
        qmarks = ",".join(["?"] * len(ids))
        rows_ts = cur.execute(
            f"SELECT id, COALESCE(last_seen, 0), COALESCE(seen_count, 0), COALESCE(strength, 0.7), COALESCE(archived, 0) "
            f"FROM memories WHERE id IN ({qmarks})",
            ids,
        ).fetchall()
        id2meta = {int(i): (int(ts), int(sc), float(st), int(ar)) for (i, ts, sc, st, ar) in rows_ts}

        now = int(time.time())

        def recency_score(last_seen: int, half_life_days: float = 7.0) -> float:
            if last_seen <= 0:
                return 0.0
            age = max(0, now - last_seen)
            tau = (half_life_days * 86400.0) / math.log(2.0)
            return float(math.exp(-age / tau))

        scores = []
        for i, mid in enumerate(ids):
            sim = float(sims_list[i])
            last_seen, seen_count, strength, archived = id2meta.get(mid, (0, 0, 0.7, 0))
            if archived:
                continue
            rec = recency_score(last_seen)
            w = memory_weight(now, last_seen, seen_count, strength)

            kw = 0.0
            tags = self.tag_cache.get(int(mid), [])
            if tags:
                kw = sum(float(tw) for phrase, tw in tags if phrase and phrase in q)
                if kw > 1.0:
                    kw = 1.0

            final = 0.75 * sim + 0.15 * rec + 0.07 * w + 0.03 * kw
            scores.append((final, mid))

        scores.sort(reverse=True, key=lambda x: x[0])
        ids = [mid for _, mid in scores[:k]]

        if ids:
            ts = int(time.time())
            cur = self.conn.cursor()
            for mid in ids:
                cur.execute(
                    "UPDATE memories SET last_seen=?, seen_count=seen_count+1, strength=MIN(1.0, strength+0.01) WHERE id=?",
                    (ts, int(mid)),
                )
            self.conn.commit()

        return [id2text[i] for i in ids if i in id2text]

    def add_many(self, texts: List[str]) -> None:
        cleaned = [t.strip() for t in texts if isinstance(t, str) and t.strip()]
        if not cleaned:
            return

        ts = int(time.time())
        cur = self.conn.cursor()

        new_texts: List[str] = []
        new_ids: List[int] = []

        for t in cleaned:
            try:
                cur.execute(
                    "INSERT INTO memories (text, last_seen, created_at, seen_count, strength, archived) VALUES (?, ?, ?, ?, ?, ?)",
                    (t, ts, ts, 1, 0.70, 0),
                )
                rid_raw = cur.lastrowid
                if rid_raw is None:
                    raise RuntimeError("SQLite insert succeeded but lastrowid is None")
                new_texts.append(t)
                new_ids.append(int(rid_raw))
            except sqlite3.IntegrityError:
                cur.execute(
                    "UPDATE memories SET last_seen=?, seen_count=seen_count+1, strength=MIN(1.0, strength+0.02), archived=0 WHERE text=?",
                    (ts, t),
                )

        self.conn.commit()

        if new_texts:
            for rid, txt in zip(new_ids, new_texts):
                tags = self._extract_weighted_tags(txt, max_tags=6)
                self._save_tags(int(rid), tags)

        if new_texts:
            vecs = self._embed(new_texts)
            self.index.add(vecs)
            self.id_map.extend(new_ids)
            self._persist_index()
