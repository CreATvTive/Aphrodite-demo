from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# TODO: P40 — replace with ds_client after experiment
# from .glm_client import GLMClient


@dataclass(frozen=True)
class AdvancedDecisionConfig:
    enabled: bool = False
    samples: int = 3
    base_temperature: float = 0.8
    sample_temperature_step: float = 0.12
    divergence_high: float = 0.35
    uncertainty_high: float = 0.60
    critic_enabled: bool = True


@dataclass
class AdvancedDecisionResult:
    text: str
    used: bool
    strategy: str
    divergence: float
    uncertainty: float
    candidates: List[str] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)


def load_advanced_decision_config() -> AdvancedDecisionConfig:
    return AdvancedDecisionConfig(
        enabled=_env_bool("ADV_DECISION_ENABLED", False),
        samples=max(1, _env_int("ADV_DECISION_SAMPLES", 3)),
        base_temperature=_env_float("ADV_DECISION_BASE_TEMP", 0.8),
        sample_temperature_step=max(0.0, _env_float("ADV_DECISION_TEMP_STEP", 0.12)),
        divergence_high=max(0.0, _env_float("ADV_DECISION_DIVERGENCE_HIGH", 0.35)),
        uncertainty_high=max(0.0, _env_float("ADV_DECISION_UNCERTAINTY_HIGH", 0.60)),
        critic_enabled=_env_bool("ADV_DECISION_CRITIC_ENABLED", True),
    )


def generate_reply(
    *,
    client: GLMClient,
    messages: Sequence[Dict[str, Any]],
    config: Optional[AdvancedDecisionConfig] = None,
) -> AdvancedDecisionResult:
    cfg = config or load_advanced_decision_config()
    if not cfg.enabled:
        text = client.chat(messages=list(messages), temperature=float(cfg.base_temperature)).strip()
        return AdvancedDecisionResult(
            text=text,
            used=False,
            strategy="single",
            divergence=0.0,
            uncertainty=0.0,
            candidates=[text] if text else [],
        )

    candidates: List[str] = []
    temps = [
        max(0.1, float(cfg.base_temperature) + float(i) * float(cfg.sample_temperature_step))
        for i in range(max(1, int(cfg.samples)))
    ]
    for t in temps:
        try:
            cand = client.chat(messages=list(messages), temperature=t).strip()
        except Exception:
            cand = ""
        if cand:
            candidates.append(cand)
    if not candidates:
        text = client.chat(messages=list(messages), temperature=float(cfg.base_temperature)).strip()
        return AdvancedDecisionResult(
            text=text,
            used=True,
            strategy="fallback_single",
            divergence=0.0,
            uncertainty=0.0,
            candidates=[],
            diagnostics={"reason": "sample_failed"},
        )
    if len(candidates) == 1:
        return AdvancedDecisionResult(
            text=candidates[0],
            used=True,
            strategy="single_after_sampling",
            divergence=0.0,
            uncertainty=0.0,
            candidates=list(candidates),
        )

    divergence = _compute_divergence(candidates)
    uncertainty = min(1.0, divergence / max(1e-6, float(cfg.divergence_high)))

    best = _select_best_by_overlap(candidates)
    strategy = "consensus"
    diag: Dict[str, Any] = {
        "divergence": divergence,
        "uncertainty": uncertainty,
        "sample_count": len(candidates),
    }

    if cfg.critic_enabled and (divergence >= cfg.divergence_high or uncertainty >= cfg.uncertainty_high):
        picked, critic_diag = _run_critic(client=client, messages=messages, candidates=candidates)
        if picked:
            best = picked
            strategy = "critic"
            diag["critic"] = critic_diag
        else:
            strategy = "consensus_high_divergence"

    return AdvancedDecisionResult(
        text=best,
        used=True,
        strategy=strategy,
        divergence=divergence,
        uncertainty=uncertainty,
        candidates=list(candidates),
        diagnostics=diag,
    )


def _run_critic(
    *,
    client: GLMClient,
    messages: Sequence[Dict[str, Any]],
    candidates: Sequence[str],
) -> Tuple[Optional[str], Dict[str, Any]]:
    user_text = ""
    for m in reversed(list(messages)):
        if str(m.get("role")) == "user":
            user_text = str(m.get("content") or "")
            break

    choices = [{"id": i + 1, "text": str(t)} for i, t in enumerate(candidates)]
    critic_sys = (
        "You are a strict response critic. Pick the safest and most useful candidate. "
        "Prefer concrete, concise, non-hallucinated replies."
    )
    critic_user = {
        "user_input": user_text,
        "candidates": choices,
        "format": {"pick_id": "int", "reason": "short string"},
    }
    critic_messages = [
        {"role": "system", "content": critic_sys},
        {"role": "user", "content": json.dumps(critic_user, ensure_ascii=False)},
    ]
    try:
        raw = client.chat(messages=critic_messages, temperature=0.1)
    except Exception as e:
        return None, {"ok": False, "error": f"{type(e).__name__}: {e}"}
    parsed = _extract_json(raw)
    if not parsed:
        return None, {"ok": False, "error": "critic_non_json", "raw": raw}
    pick_raw = parsed.get("pick_id")
    if pick_raw is None:
        return None, {"ok": False, "error": "critic_missing_pick", "raw": parsed}
    try:
        idx = int(str(pick_raw).strip()) - 1
    except Exception:
        return None, {"ok": False, "error": "critic_invalid_pick", "raw": parsed}
    if idx < 0 or idx >= len(candidates):
        return None, {"ok": False, "error": "critic_pick_out_of_range", "raw": parsed}
    return str(candidates[idx]), {"ok": True, "pick_id": int(idx + 1), "reason": parsed.get("reason", "")}


def _extract_json(raw: str) -> Optional[Dict[str, Any]]:
    t = str(raw or "").strip()
    if not t:
        return None
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    l = t.find("{")
    r = t.rfind("}")
    if l >= 0 and r > l:
        try:
            obj = json.loads(t[l : r + 1])
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None


def _normalize_for_similarity(s: str) -> str:
    return " ".join(str(s or "").lower().split())


def _text_similarity(a: str, b: str) -> float:
    sa = set(_normalize_for_similarity(a).split())
    sb = set(_normalize_for_similarity(b).split())
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return float(len(sa & sb) / max(1, len(sa | sb)))


def _compute_divergence(replies: Sequence[str]) -> float:
    arr = [str(x) for x in replies if str(x).strip()]
    if len(arr) < 2:
        return 0.0
    sims: List[float] = []
    for i in range(len(arr)):
        for j in range(i + 1, len(arr)):
            sims.append(_text_similarity(arr[i], arr[j]))
    if not sims:
        return 0.0
    return float(max(0.0, 1.0 - (sum(sims) / len(sims))))


def _select_best_by_overlap(candidates: Sequence[str]) -> str:
    arr = [str(x) for x in candidates if str(x).strip()]
    if not arr:
        return ""
    if len(arr) == 1:
        return arr[0]
    scores: List[Tuple[float, int, str]] = []
    for i, c in enumerate(arr):
        s = 0.0
        for j, d in enumerate(arr):
            if i == j:
                continue
            s += _text_similarity(c, d)
        scores.append((s, -len(c), c))
    scores.sort(reverse=True)
    return scores[0][2]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    try:
        return int(raw)
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)
