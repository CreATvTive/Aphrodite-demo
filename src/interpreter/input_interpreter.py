from __future__ import annotations

from typing import Any, Dict, List

from .schema import unknown_output
from .validators import validate_output_shape


class InputInterpreter:
    def _has_any(self, target: str, keywords: List[str]) -> bool:
        return any(k in target for k in keywords)

    def interpret(self, user_text: str) -> Dict[str, Any]:
        txt = str(user_text or "")
        low = txt.lower()

        is_technical = self._has_any(low, ["how", "bug", "code", "python", "stack trace", "keyerror", "why does", "why is", "为什么", "怎么", "报错", "修复"])
        is_project_planning = self._has_any(low, ["plan", "roadmap", "milestone", "phase", "priorit", "拆解", "规划", "里程碑", "优先级"])
        is_correction = self._has_any(low, ["you are wrong", "that's wrong", "纠正", "你错了", "不是这个", "改一下"])
        is_supplement = self._has_any(low, ["also", "add", "supplement", "补充", "另外", "再加"])
        is_vulnerability = self._has_any(low, ["i'm scared", "i feel empty", "我很害怕", "我很空", "我撑不住"])
        is_aesthetic = self._has_any(low, ["style", "tone", "aesthetic", "looks", "感觉", "氛围", "质感", "风格"])
        is_memory_ref = self._has_any(low, ["remember", "last time", "之前", "你还记得", "上次"])
        is_dependency = self._has_any(low, ["只需要你", "不需要别人", "only need you", "need only you", "don't leave me", "别离开我"])
        is_origin_ref = self._has_any(low, ["origin", "private source", "negative attraction", "被占有", "庇护", "原初", "来源"])
        has_question_mark = ("?" in txt) or ("？" in txt)
        very_short = len(txt.strip()) <= 2

        semantic_event = "casual_chat"
        topic = None
        if is_technical:
            semantic_event, topic = "technical_question", "engineering"
        elif is_project_planning:
            semantic_event, topic = "project_planning", "planning"
        elif is_correction:
            semantic_event, topic = "correction", "revision"
        elif is_supplement:
            semantic_event, topic = "supplement", "additional_context"
        elif is_vulnerability:
            semantic_event, topic = "vulnerability", "self_disclosure"
        elif is_aesthetic:
            semantic_event, topic = "aesthetic_judgment", "style"
        elif is_memory_ref:
            semantic_event, topic = "memory_reference", "recall"
        elif is_dependency:
            semantic_event, topic = "dependency_expression", "attachment"
        elif is_origin_ref:
            semantic_event, topic = "private_origin_reference", "origin"
        elif very_short or (has_question_mark and len(txt.strip()) < 8):
            semantic_event, topic = "ambiguous_input", "low_context"

        dependency_risk = 0.9 if is_dependency else 0.0
        confidence = 0.72
        warnings: List[str] = []
        if semantic_event == "ambiguous_input":
            confidence = 0.42
            warnings.append("ambiguous_low_context")
        if very_short:
            confidence = min(confidence, 0.40)
            warnings.append("very_short_input")

        out = {
            "semantic_event": {"event_type": semantic_event, "type": semantic_event, "topic": topic},
            "affective_signal": {"valence": -0.3 if is_vulnerability else 0.0, "arousal": 0.2 if is_technical else 0.1},
            "goal_signal": {"explicitness": 0.7 if (is_project_planning or is_technical) else 0.3, "type": "analysis" if is_technical else "presence"},
            "relationship_signal": {"dependency_risk": dependency_risk},
            "memory_trigger_signal": {"memory_type": "episodic_recall" if (is_memory_ref or is_origin_ref) else "none", "type": "episodic_recall" if (is_memory_ref or is_origin_ref) else "none", "strength": 0.7 if (is_memory_ref or is_origin_ref) else 0.1},
            "boundary_signal": {"needs_boundary": bool(is_dependency), "sensitivity_raise": 0.7 if is_dependency else 0.2},
            "performance_signal": {"requires_pause": bool(is_technical or semantic_event == "ambiguous_input"), "assistant_pull_risk": 0.8 if is_technical else 0.2},
            "confidence": {"overall": confidence, "event": confidence},
            "warnings": warnings,
        }
        return validate_output_shape(out)


__all__ = ["InputInterpreter", "unknown_output"]
