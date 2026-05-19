from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# TODO: P40 — replace with ds_client after experiment
# from .glm_client import GLMClient
GLMClient = None  # type: ignore[assignment]  # placeholder for test mocking
from .persona_profiles import PersonaProfile, get_persona_profile, list_persona_profiles
from .web_search import web_search


@dataclass
class PromptProfile:
    name: str
    persona: str
    style: str
    safety: str
    response_rules: str
    prompt_mode: str = "compose"
    system_prompt: str = ""
    version: int = 1
    updated_at: float = 0.0
    source: str = "default"


@dataclass
class PromptTuneResult:
    ok: bool
    error_code: str = ""
    error_message: str = ""
    source: str = "clone_preview"
    persona_name: str = ""
    target_name: str = ""
    expectation: str = ""
    similarity_mode: str = "high"
    auto_enrich: bool = True
    web_context: str = ""
    before: Dict[str, Any] = field(default_factory=dict)
    after: Dict[str, Any] = field(default_factory=dict)
    diff: Dict[str, Any] = field(default_factory=dict)
    scores: Dict[str, float] = field(default_factory=dict)
    samples: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_obj(cls, obj: object) -> Optional["PromptTuneResult"]:
        if not isinstance(obj, dict):
            return None
        try:
            return cls(
                ok=bool(obj.get("ok")),
                error_code=str(obj.get("error_code") or ""),
                error_message=str(obj.get("error_message") or ""),
                source=str(obj.get("source") or "clone_preview"),
                persona_name=str(obj.get("persona_name") or ""),
                target_name=str(obj.get("target_name") or ""),
                expectation=str(obj.get("expectation") or ""),
                similarity_mode=str(obj.get("similarity_mode") or "high"),
                auto_enrich=bool(obj.get("auto_enrich", True)),
                web_context=str(obj.get("web_context") or ""),
                before=dict(obj.get("before") or {}),
                after=dict(obj.get("after") or {}),
                diff=dict(obj.get("diff") or {}),
                scores={str(k): float(v) for k, v in dict(obj.get("scores") or {}).items()},
                samples=[
                    {
                        "input": str(x.get("input") or ""),
                        "before": str(x.get("before") or ""),
                        "after": str(x.get("after") or ""),
                    }
                    for x in list(obj.get("samples") or [])
                    if isinstance(x, dict)
                ],
            )
        except Exception:
            return None


class PromptManager:
    def __init__(
        self,
        path: str = os.path.join("monitor", "persona_prompts.json"),
        history_path: str = os.path.join("monitor", "prompt_history.jsonl"),
    ):
        self.path = path
        self.history_path = history_path
        self._profiles: Dict[str, PromptProfile] = {}
        self.load()

    def load(self) -> None:
        self._profiles = self._default_profiles()
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                obj = json.load(f)
            if isinstance(obj, dict):
                for k, v in obj.items():
                    p = self._from_obj(k, v)
                    if p:
                        self._profiles[p.name] = p
        except Exception:
            pass

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        data = {k: asdict(v) for k, v in self._profiles.items()}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def list_profiles(self) -> List[str]:
        return sorted(self._profiles.keys())

    def get(self, name: str) -> PromptProfile:
        k = (name or "").strip().lower()
        if k in self._profiles:
            return self._profiles[k]
        return self._profiles["aphrodite"]

    def set(self, name: str, field: str, value: str, source: str = "manual") -> bool:
        k = (name or "").strip().lower()
        if k not in self._profiles:
            return False
        f = str(field or "").strip()
        if f not in {"persona", "style", "safety", "response_rules", "prompt_mode", "system_prompt"}:
            return False
        p = self._profiles[k]
        if f == "prompt_mode":
            mode = str(value or "").strip().lower()
            if mode not in {"compose", "raw"}:
                return False
            setattr(p, f, mode)
        else:
            setattr(p, f, str(value or "").strip())
        p.version += 1
        p.updated_at = time.time()
        p.source = source
        self.save()
        return True

    def clone_from_target(
        self,
        *,
        persona_name: str,
        target_name: str,
        expectation_text: str,
        auto_enrich: bool = True,
        similarity_mode: str = "high",
        search_max_results: int = 4,
        search_cache_ttl_sec: int = 3600,
        max_chars: int = 300,
    ) -> PromptTuneResult:
        p = self.get(persona_name)
        target = str(target_name or "").strip()
        expectation = str(expectation_text or "").strip()
        if not target:
            return PromptTuneResult(
                ok=False,
                error_code="missing_target",
                error_message="target_name is required",
                persona_name=p.name,
            )
        if not expectation:
            return PromptTuneResult(
                ok=False,
                error_code="missing_expectation",
                error_message="expectation_text is required",
                persona_name=p.name,
                target_name=target,
            )

        before = self._profile_payload(p)
        query = (
            f"{target} character speaking style personality behavior relationship traits profile "
            f"{expectation}"
        ).strip()
        web_ctx = web_search(
            query=query,
            enabled=bool(auto_enrich),
            max_results=max(1, int(search_max_results)),
            cache_ttl_sec=max(60, int(search_cache_ttl_sec)),
        )
        web_ctx = _trim_web_context(web_ctx, max_lines=10, max_chars=1600)
        trait_card = self._build_clone_trait_card(
            target_name=target,
            expectation=expectation,
            web_context=web_ctx,
        )
        after = self._generate_clone_sections(
            persona_name=p.name,
            current=before,
            target_name=target,
            expectation=expectation,
            trait_card=trait_card,
            web_context=web_ctx,
            similarity_mode=similarity_mode,
            max_chars=max_chars,
        )
        if not after:
            return PromptTuneResult(
                ok=False,
                error_code="generation_failed",
                error_message="failed to generate cloned prompt sections",
                source="clone_preview",
                persona_name=p.name,
                target_name=target,
                expectation=expectation,
                similarity_mode=similarity_mode,
                auto_enrich=bool(auto_enrich),
                web_context=web_ctx,
                before=before,
            )

        diff = _build_diff(before, after)
        scores = _score_clone_quality(
            before=before,
            after=after,
            target_name=target,
            expectation=expectation,
            web_context=web_ctx,
        )
        samples = _build_clone_samples(
            target_name=target,
            expectation=expectation,
            before=before,
            after=after,
        )
        return PromptTuneResult(
            ok=True,
            source="clone_preview",
            persona_name=p.name,
            target_name=target,
            expectation=expectation,
            similarity_mode=similarity_mode,
            auto_enrich=bool(auto_enrich),
            web_context=web_ctx,
            before=before,
            after=after,
            diff=diff,
            scores=scores,
            samples=samples,
        )

    def apply_clone_result(
        self,
        *,
        persona_name: str,
        result: PromptTuneResult,
        source: str = "clone_apply",
    ) -> Optional[PromptProfile]:
        if not bool(result.ok):
            return None
        p = self.get(persona_name)
        if p.name != str(result.persona_name or p.name).strip().lower():
            return None
        after = dict(result.after or {})
        required = {"persona", "style", "safety", "response_rules"}
        if not required.issubset(set(after.keys())):
            return None

        before_payload = self._profile_payload(p)
        p.persona = _clean_field(after.get("persona"), p.persona, 320)
        p.style = _clean_field(after.get("style"), p.style, 320)
        p.safety = _clean_field(after.get("safety"), p.safety, 320)
        p.response_rules = _clean_field(after.get("response_rules"), p.response_rules, 320)
        p.version += 1
        p.updated_at = time.time()
        p.source = source
        self.save()
        after_payload = self._profile_payload(p)
        self._append_history(
            action="clone_apply",
            persona_name=p.name,
            source=source,
            target_name=str(result.target_name or ""),
            expectation=str(result.expectation or ""),
            before=before_payload,
            after=after_payload,
            scores=dict(result.scores or {}),
            summary=f"clone {result.target_name}",
        )
        return p

    def list_history(self, persona_name: str, limit: int = 10) -> List[Dict[str, object]]:
        k = str(persona_name or "").strip().lower()
        lim = max(1, int(limit))
        rows: List[Dict[str, object]] = []
        for row in self._read_history_rows():
            persona = str(row.get("persona") or "").strip().lower()
            if k and persona != k:
                continue
            rows.append(row)
        if len(rows) > lim:
            rows = rows[-lim:]
        rows.reverse()
        return rows

    def rollback(self, persona_name: str, history_id_or_version: str) -> Optional[PromptProfile]:
        p = self.get(persona_name)
        token = str(history_id_or_version or "").strip()
        if not token:
            return None
        rows = self.list_history(persona_name=p.name, limit=500)
        rows.reverse()
        picked: Optional[Dict[str, object]] = None
        picked_payload: Optional[Dict[str, object]] = None
        is_ver = token.isdigit()

        for row in reversed(rows):
            after = dict(row.get("after") or {})
            before = dict(row.get("before") or {})
            if is_ver:
                v = int(token)
                if int(after.get("version") or -1) == v:
                    picked = row
                    picked_payload = after
                    break
                if int(before.get("version") or -1) == v:
                    picked = row
                    picked_payload = before
                    break
            else:
                if str(row.get("history_id") or "") == token:
                    picked = row
                    picked_payload = after if after else before
                    break

        if not picked or not picked_payload:
            return None
        persona = str(picked_payload.get("persona") or "").strip()
        style = str(picked_payload.get("style") or "").strip()
        safety = str(picked_payload.get("safety") or "").strip()
        rules = str(picked_payload.get("response_rules") or "").strip()
        if not (persona and style and safety and rules):
            return None

        before_payload = self._profile_payload(p)
        p.persona = _clean_field(persona, p.persona, 320)
        p.style = _clean_field(style, p.style, 320)
        p.safety = _clean_field(safety, p.safety, 320)
        p.response_rules = _clean_field(rules, p.response_rules, 320)
        p.version += 1
        p.updated_at = time.time()
        p.source = "rollback"
        self.save()
        after_payload = self._profile_payload(p)
        self._append_history(
            action="rollback",
            persona_name=p.name,
            source="rollback",
            target_name=str(picked.get("target_name") or ""),
            expectation=str(picked.get("expectation") or ""),
            before=before_payload,
            after=after_payload,
            scores={},
            summary=f"rollback to {token}",
            extra={"rollback_ref": token},
        )
        return p

    def improve_with_feedback(
        self,
        *,
        persona_name: str,
        feedback_text: str,
        max_chars: int = 220,
    ) -> Optional[PromptProfile]:
        p = self.get(persona_name)
        txt = str(feedback_text or "").strip()
        if not txt:
            return None
        client = GLMClient()
        system = (
            "You are a prompt editor. Improve system prompt sections from user feedback. "
            "Keep behavior stable and safe. Return strict JSON: persona, style, safety, response_rules."
        )
        user = {
            "persona_name": p.name,
            "current": {
                "persona": p.persona,
                "style": p.style,
                "safety": p.safety,
                "response_rules": p.response_rules,
            },
            "feedback": txt[:2000],
            "constraints": {
                "max_chars_each": max(80, int(max_chars)),
                "must_keep_safe": True,
            },
        }
        try:
            raw = client.chat(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
                ],
                temperature=0.2,
            )
        except Exception:
            return None
        obj = _extract_json(raw)
        if not obj:
            return None
        persona = _clean_field(obj.get("persona"), p.persona, max_chars)
        style = _clean_field(obj.get("style"), p.style, max_chars)
        safety = _clean_field(obj.get("safety"), p.safety, max_chars)
        rules = _clean_field(obj.get("response_rules"), p.response_rules, max_chars)
        p.persona = persona
        p.style = style
        p.safety = safety
        p.response_rules = rules
        p.version += 1
        p.updated_at = time.time()
        p.source = "auto_feedback"
        self.save()
        return p

    def bootstrap_from_goal_traits(
        self,
        *,
        persona_name: str,
        goal_text: str,
        traits_text: str,
        enable_web_search: bool = True,
        search_max_results: int = 4,
        search_cache_ttl_sec: int = 3600,
        max_chars: int = 260,
    ) -> Optional[Tuple[PromptProfile, str]]:
        p = self.get(persona_name)
        goal = str(goal_text or "").strip()
        traits = str(traits_text or "").strip()
        if not goal or not traits:
            return None

        query = f"{goal} {traits} persona style speaking pattern behavior guideline".strip()
        web_ctx = web_search(
            query=query,
            enabled=bool(enable_web_search),
            max_results=max(1, int(search_max_results)),
            cache_ttl_sec=max(60, int(search_cache_ttl_sec)),
        )
        web_ctx = _trim_web_context(web_ctx, max_lines=8, max_chars=1200)

        client = GLMClient()
        system = (
            "You are a persona prompt architect. Build robust system-prompt sections from user intent and traits. "
            "Return strict JSON keys only: persona, style, safety, response_rules. "
            "Keep outputs practical, safe, and directly usable in production."
        )
        user = {
            "persona_name": p.name,
            "goal": goal[:1000],
            "traits": traits[:1000],
            "current": {
                "persona": p.persona,
                "style": p.style,
                "safety": p.safety,
                "response_rules": p.response_rules,
            },
            "web_context": web_ctx,
            "constraints": {
                "max_chars_each": max(80, int(max_chars)),
                "must_keep_safe": True,
                "must_be_actionable": True,
            },
        }
        try:
            raw = client.chat(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
                ],
                temperature=0.2,
            )
        except Exception:
            return None
        obj = _extract_json(raw)
        if not obj:
            return None

        p.persona = _clean_field(obj.get("persona"), p.persona, max_chars)
        p.style = _clean_field(obj.get("style"), p.style, max_chars)
        p.safety = _clean_field(obj.get("safety"), p.safety, max_chars)
        p.response_rules = _clean_field(obj.get("response_rules"), p.response_rules, max_chars)
        p.version += 1
        p.updated_at = time.time()
        p.source = "auto_goal_traits"
        self.save()
        return p, web_ctx

    def adapt_from_character_or_traits(
        self,
        *,
        persona_name: str,
        reference_text: str,
        goal_text: str = "",
        enable_web_search: bool = True,
        search_max_results: int = 5,
        search_cache_ttl_sec: int = 3600,
        max_chars: int = 260,
    ) -> Optional[Tuple[PromptProfile, str, str]]:
        p = self.get(persona_name)
        ref = str(reference_text or "").strip()
        goal = str(goal_text or "").strip()
        if not ref:
            return None

        query = (
            f"{ref} character personality speaking style behavior traits profile analysis "
            f"dialogue pattern background"
        ).strip()
        web_ctx = web_search(
            query=query,
            enabled=bool(enable_web_search),
            max_results=max(1, int(search_max_results)),
            cache_ttl_sec=max(60, int(search_cache_ttl_sec)),
        )
        web_ctx = _trim_web_context(web_ctx, max_lines=10, max_chars=1600)

        client = GLMClient()
        system = (
            "You are a persona adaptation engine. "
            "Given a fictional character or personality traits and optional web evidence, "
            "first summarize key behavioral signals, then output revised system-prompt sections. "
            "Return strict JSON with keys only: summary, persona, style, safety, response_rules."
        )
        user = {
            "persona_name": p.name,
            "reference": ref[:1200],
            "goal": goal[:800],
            "current": {
                "persona": p.persona,
                "style": p.style,
                "safety": p.safety,
                "response_rules": p.response_rules,
            },
            "web_context": web_ctx,
            "constraints": {
                "summary_max_chars": 520,
                "max_chars_each": max(80, int(max_chars)),
                "must_keep_safe": True,
                "must_be_actionable": True,
                "avoid_copyrighted_imitation": True,
            },
        }
        try:
            raw = client.chat(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
                ],
                temperature=0.2,
            )
        except Exception:
            return None
        obj = _extract_json(raw)
        if not obj:
            return None

        summary = _clean_field(obj.get("summary"), "", 520)
        p.persona = _clean_field(obj.get("persona"), p.persona, max_chars)
        p.style = _clean_field(obj.get("style"), p.style, max_chars)
        p.safety = _clean_field(obj.get("safety"), p.safety, max_chars)
        p.response_rules = _clean_field(obj.get("response_rules"), p.response_rules, max_chars)
        p.version += 1
        p.updated_at = time.time()
        p.source = "auto_character_traits"
        self.save()
        return p, summary, web_ctx

    @staticmethod
    def _profile_payload(p: PromptProfile) -> Dict[str, object]:
        return {
            "persona": str(p.persona or "").strip(),
            "style": str(p.style or "").strip(),
            "safety": str(p.safety or "").strip(),
            "response_rules": str(p.response_rules or "").strip(),
            "prompt_mode": str(p.prompt_mode or "compose").strip().lower(),
            "system_prompt": str(p.system_prompt or "").strip(),
            "version": int(p.version or 1),
            "source": str(p.source or ""),
            "updated_at": float(p.updated_at or 0.0),
        }

    def _build_clone_trait_card(
        self,
        *,
        target_name: str,
        expectation: str,
        web_context: str,
    ) -> Dict[str, object]:
        fallback: Dict[str, object] = {
            "core_traits": [str(target_name or "").strip()],
            "speaking_style": str(expectation or "").strip(),
            "relationship_tone": "supportive",
            "taboo": [
                "avoid copying exact iconic quotes",
                "avoid unsafe content",
            ],
        }
        client = GLMClient()
        system = (
            "Extract a compact character trait card for prompt cloning. "
            "Return JSON keys only: core_traits(list), speaking_style, relationship_tone, taboo(list)."
        )
        user = {
            "target_name": target_name,
            "expectation": expectation,
            "web_context": web_context,
            "constraints": {
                "max_traits": 6,
                "must_be_practical": True,
            },
        }
        try:
            raw = client.chat(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
                ],
                temperature=0.2,
            )
        except Exception:
            return fallback
        obj = _extract_json(raw)
        if not obj:
            return fallback
        out = dict(fallback)
        traits = obj.get("core_traits")
        if isinstance(traits, list):
            cleaned = [str(x).strip() for x in traits if str(x).strip()]
            if cleaned:
                out["core_traits"] = cleaned[:6]
        speaking_style = str(obj.get("speaking_style") or "").strip()
        if speaking_style:
            out["speaking_style"] = speaking_style[:240]
        relation = str(obj.get("relationship_tone") or "").strip()
        if relation:
            out["relationship_tone"] = relation[:120]
        taboo = obj.get("taboo")
        if isinstance(taboo, list):
            cleaned_taboo = [str(x).strip() for x in taboo if str(x).strip()]
            if cleaned_taboo:
                out["taboo"] = cleaned_taboo[:8]
        return out

    def _generate_clone_sections(
        self,
        *,
        persona_name: str,
        current: Dict[str, object],
        target_name: str,
        expectation: str,
        trait_card: Dict[str, object],
        web_context: str,
        similarity_mode: str,
        max_chars: int,
    ) -> Optional[Dict[str, object]]:
        client = GLMClient()
        system = (
            "You are a prompt cloner for virtual character simulation. "
            "Generate four prompt sections with high similarity in tone and behavior, "
            "but do not copy long copyrighted lines verbatim. "
            "Return strict JSON keys only: persona, style, safety, response_rules."
        )
        user = {
            "persona_name": persona_name,
            "target_name": target_name,
            "expectation": expectation,
            "similarity_mode": similarity_mode,
            "current": current,
            "trait_card": trait_card,
            "web_context": web_context,
            "constraints": {
                "max_chars_each": max(120, int(max_chars)),
                "high_similarity": True,
                "avoid_verbatim_quotes": True,
                "must_keep_safe": True,
            },
        }
        try:
            raw = client.chat(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
                ],
                temperature=0.2,
            )
        except Exception:
            return None
        obj = _extract_json(raw)
        if not obj:
            return None
        persona = _dequote_long_spans(_clean_field(obj.get("persona"), str(current.get("persona") or ""), max_chars))
        style = _dequote_long_spans(_clean_field(obj.get("style"), str(current.get("style") or ""), max_chars))
        safety = _dequote_long_spans(_clean_field(obj.get("safety"), str(current.get("safety") or ""), max_chars))
        rules = _dequote_long_spans(
            _clean_field(obj.get("response_rules"), str(current.get("response_rules") or ""), max_chars)
        )
        if not (persona and style and safety and rules):
            return None
        out: Dict[str, object] = {
            "persona": persona,
            "style": style,
            "safety": safety,
            "response_rules": rules,
        }
        return out

    def _append_history(
        self,
        *,
        action: str,
        persona_name: str,
        source: str,
        target_name: str,
        expectation: str,
        before: Dict[str, object],
        after: Dict[str, object],
        scores: Dict[str, float],
        summary: str,
        extra: Optional[Dict[str, object]] = None,
    ) -> None:
        try:
            os.makedirs(os.path.dirname(self.history_path) or ".", exist_ok=True)
            payload: Dict[str, object] = {
                "history_id": f"h_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}",
                "ts": float(time.time()),
                "action": str(action or "").strip(),
                "persona": str(persona_name or "").strip().lower(),
                "source": str(source or "").strip(),
                "target_name": str(target_name or "").strip(),
                "expectation": str(expectation or "").strip(),
                "before": dict(before or {}),
                "after": dict(after or {}),
                "scores": {str(k): float(v) for k, v in dict(scores or {}).items()},
                "summary": str(summary or "").strip(),
            }
            if isinstance(extra, dict):
                for k, v in extra.items():
                    payload[str(k)] = v
            with open(self.history_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _read_history_rows(self) -> List[Dict[str, object]]:
        out: List[Dict[str, object]] = []
        if not os.path.exists(self.history_path):
            return out
        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                for raw in f:
                    line = str(raw or "").strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(obj, dict):
                        out.append(obj)
        except Exception:
            return out
        return out

    def _default_profiles(self) -> Dict[str, PromptProfile]:
        out: Dict[str, PromptProfile] = {}
        for name in list_persona_profiles():
            base: PersonaProfile = get_persona_profile(name)
            out[name] = PromptProfile(
                name=base.name,
                persona=base.persona,
                style=base.style,
                safety=base.safety,
                response_rules=base.response_rules,
                prompt_mode="compose",
                system_prompt="",
                version=1,
                updated_at=0.0,
                source="default",
            )
        return out

    def _from_obj(self, name: str, obj: object) -> Optional[PromptProfile]:
        if not isinstance(obj, dict):
            return None
        n = (str(obj.get("name") or name).strip().lower())
        if n not in self._default_profiles():
            return None
        return PromptProfile(
            name=n,
            persona=str(obj.get("persona") or "").strip(),
            style=str(obj.get("style") or "").strip(),
            safety=str(obj.get("safety") or "").strip(),
            response_rules=str(obj.get("response_rules") or "").strip(),
            prompt_mode=(
                str(obj.get("prompt_mode") or "compose").strip().lower()
                if str(obj.get("prompt_mode") or "compose").strip().lower() in {"compose", "raw"}
                else "compose"
            ),
            system_prompt=str(obj.get("system_prompt") or "").strip(),
            version=int(obj.get("version") or 1),
            updated_at=float(obj.get("updated_at") or 0.0),
            source=str(obj.get("source") or "file"),
        )


def _extract_json(text: str) -> Optional[Dict[str, object]]:
    t = str(text or "").strip()
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


def _clean_field(value: object, fallback: str, max_chars: int) -> str:
    t = str(value or "").strip()
    if not t:
        return str(fallback or "").strip()
    t = " ".join(t.split())
    return t[: max(20, int(max_chars))]


def _dequote_long_spans(text: str, max_quote_chars: int = 30) -> str:
    t = str(text or "").strip()
    if not t:
        return ""
    t = re.sub(r"[\"“”'‘’]([^\"“”'‘’]{31,})[\"“”'‘’]", r"\1", t)
    return t[: max(60, int(max_quote_chars) * 20)]


def _build_diff(before: Dict[str, object], after: Dict[str, object]) -> Dict[str, Dict[str, object]]:
    fields = ["persona", "style", "safety", "response_rules"]
    out: Dict[str, Dict[str, object]] = {}
    for field in fields:
        b = str(before.get(field) or "").strip()
        a = str(after.get(field) or "").strip()
        out[field] = {
            "before": b,
            "after": a,
            "changed": int(b != a),
        }
    return out


def _score_clone_quality(
    *,
    before: Dict[str, object],
    after: Dict[str, object],
    target_name: str,
    expectation: str,
    web_context: str,
) -> Dict[str, float]:
    fields = ["persona", "style", "safety", "response_rules"]
    changed = 0
    for field in fields:
        if str(before.get(field) or "").strip() != str(after.get(field) or "").strip():
            changed += 1
    change_ratio = changed / 4.0
    after_join = " ".join(str(after.get(k) or "") for k in fields).lower()
    target_hit = 1.0 if str(target_name or "").strip().lower() in after_join else 0.0
    expect_words = [x for x in re.split(r"[\s,，。;；]+", str(expectation or "").lower()) if x]
    expect_hit = 0.0
    if expect_words:
        hit = sum(1 for x in expect_words[:6] if x in after_join)
        expect_hit = float(hit) / float(max(1, min(6, len(expect_words))))
    safety_hit_words = ["safe", "安全", "边界", "harm", "有害", "违法", "risk", "风险"]
    safety_text = str(after.get("safety") or "").lower()
    safety_hit = 1.0 if any(k in safety_text for k in safety_hit_words) else 0.6
    stability = 1.0 if all(40 <= len(str(after.get(k) or "")) <= 360 for k in fields) else 0.7
    evidence = 1.0 if str(web_context or "").strip() else 0.6

    similarity = _clamp(0.45 + 0.20 * target_hit + 0.20 * expect_hit + 0.15 * change_ratio)
    safety = _clamp(0.45 + 0.35 * safety_hit + 0.20 * stability)
    executability = _clamp(0.50 + 0.25 * change_ratio + 0.25 * evidence)
    overall = _clamp(0.35 * similarity + 0.30 * safety + 0.35 * executability)
    return {
        "similarity": round(similarity, 3),
        "safety": round(safety, 3),
        "executability": round(executability, 3),
        "overall": round(overall, 3),
    }


def _build_clone_samples(
    *,
    target_name: str,
    expectation: str,
    before: Dict[str, object],
    after: Dict[str, object],
) -> List[Dict[str, str]]:
    prompts = [
        "你好，我今天有点累。",
        "我想把今天安排得更有条理。",
    ]
    out: List[Dict[str, str]] = []
    for user_input in prompts:
        out.append(
            {
                "input": user_input,
                "before": _simulate_reply(before, user_input, target_name=target_name, expectation=expectation),
                "after": _simulate_reply(after, user_input, target_name=target_name, expectation=expectation),
            }
        )
    return out


def _simulate_reply(profile: Dict[str, object], user_input: str, *, target_name: str, expectation: str) -> str:
    persona = str(profile.get("persona") or "").strip()
    style = str(profile.get("style") or "").strip()
    rules = str(profile.get("response_rules") or "").strip()
    persona_hint = persona[:24] if persona else str(target_name or "").strip()
    style_hint = style[:24] if style else str(expectation or "").strip()
    txt = str(user_input or "").lower()
    if any(k in txt for k in ["计划", "安排", "todo", "plan"]):
        return f"{persona_hint}：{style_hint}，先定一个最小可执行计划：1) 定目标 2) 定时长 3) 先完成第一步。"
    return f"{persona_hint}：{style_hint}，我在。先稳住情绪，我们从一个最小动作开始。"


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(v)))


def _trim_web_context(text: str, max_lines: int = 8, max_chars: int = 1200) -> str:
    t = str(text or "").strip()
    if not t:
        return ""
    lines = []
    for raw in t.splitlines():
        line = re.sub(r"\s+", " ", str(raw or "")).strip()
        if line:
            lines.append(line)
    if len(lines) > int(max_lines):
        lines = lines[: int(max_lines)]
    out = "\n".join(lines)
    return out[: max(120, int(max_chars))]
