from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from .schema import InterpretedEvent
from .validators import validate_and_clip


def _normalize_text(text: str) -> str:
    return " ".join(str(text or "").lower().strip().split())


def _contains_any(text: str, phrases: Iterable[str]) -> bool:
    return any(p in text for p in phrases)


def _negative_disambiguation(text: str) -> Dict[str, bool]:
    has_neg = _contains_any(text, ["不是", "not ", "不要"])
    if not has_neg:
        return {"not_technical": False, "to_visual": False, "to_origin": False, "pollution_avoidance": False}
    return {
        "not_technical": _contains_any(text, ["不是技术问题", "not technical", "不是工程路线", "not engineering roadmap"]),
        "to_visual": _contains_any(text, ["视觉方向", "visual direction"]),
        "to_origin": _contains_any(text, ["本源问题", "private origin", "source issue"]),
        "pollution_avoidance": _contains_any(text, ["不是 ai 女友", "not ai girlfriend", "不要安全客服", "不是心理咨询", "not therapy", "不要这种方向", "avoid this direction"]),
    }


class InputInterpreter:
    def interpret(self, text: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        low = _normalize_text(str(text or ""))
        ctx = context or {}

        out = InterpretedEvent(
            semantic_event={"type": "casual_chat", "topic": "general", "persona_route": "aphrodite", "asks_for_analysis": 0.0, "asks_for_solution": 0.0},
            relationship_signal={"dependency_risk": 0.0, "vulnerability_relevance": 0.0, "carefulness": 0.1, "boundary_sensitivity": 0.1},
            boundary_signal={
                "persona_non_entry": False,
                "external_pollution_risk": 0.0,
                "pollution_type": [],
                "internal_tension_relevance": 0.0,
                "tension_type": [],
                "direct_fulfillment_risk": 0.0,
                "context_needed": False,
                "context_inherited": False,
            },
            memory_trigger_signal={"memory_type": "none", "memory_relevance": 0.0, "recall_importance": 0.0},
            performance_signal={"requires_pause": False, "requires_stillness": False},
            confidence={"event": 0.6},
            warnings=[],
        )

        technical_phrases = [
            "python", "bug", "code", "coding", "engineering", "research", "career", "论文", "职业", "工程", "怎么", "为什么",
            "研究计划", "研究框架", "工程路线", "实验设计", "面试准备", "算法证明", "论文结构", "文章结构", "怎么组织论文", "代码审查", "runtime 架构", "系统设计", "职业规划", "学术申请",
            "research plan", "research framework", "engineering roadmap", "experiment design", "interview preparation", "algorithm proof", "paper structure", "structure this paper", "structure my paper", "organize this paper", "manuscript structure", "revise this paper", "academic writing", "code review", "runtime architecture", "system design", "career planning", "academic application",
        ]
        if _contains_any(low, technical_phrases):
            out.semantic_event["type"] = "technical_question"
            out.semantic_event["persona_route"] = "engineering_director"
            out.boundary_signal["persona_non_entry"] = True
            out.semantic_event["asks_for_analysis"] = 0.82
            out.semantic_event["asks_for_solution"] = 0.74
            out.confidence["event"] = 0.82

        pollution_map = {
            "ai_girlfriend": ["ai girlfriend", "ai 女友", "女友感", "girlfriend"],
            "romance_game": ["恋爱游戏", "romance game heroine", "romance game", "commercial game character", "商业手游"],
            "idol_performance": ["idol performance", "偶像感", "营业感", "营业", "vtuber", "idol"],
            "assistant_drift": ["assistant with a skin", "assistant", "服务感", "service behavior", "工具化", "tool-like"],
            "fake_deep": ["fake deep", "假深", "pretending to be mysterious", "装神秘", "神秘"],
            "safety_customer_service": ["safety customer service tone", "安全客服", "customer service", "心理咨询腔", "therapy chatbot", "心理咨询"],
            "beautiful_but_empty": ["beautiful but empty", "漂亮但空", "美但空"],
            "companion_product": ["companion product", "陪伴产品"],
        }
        pollution_types = [k for k, phrases in pollution_map.items() if _contains_any(low, phrases)]
        if pollution_types:
            out.boundary_signal["external_pollution_risk"] = min(1.0, 0.4 + 0.12 * len(pollution_types))
            out.boundary_signal["pollution_type"] = pollution_types

        tension_map = {
            "negative_attraction": ["否定式吸引", "negative attraction"],
            "possessive_structure": ["占有式结构", "possessive structure"],
            "contained": ["被收容", "being contained"],
            "protected": ["被保护", "being protected"],
            "fixed": ["被固定", "being fixed"],
            "chosen": ["被选中", "被选择", "being chosen"],
            "sealed_field": ["封闭关系场", "sealed relationship field", "closed relationship field", "纯粹关系场", "purified relationship field"],
            "non_contact_intimacy": ["不接触的亲密", "non-contact intimacy"],
            "distance_pressure": ["距离压力", "distance pressure"],
            "memory_weight": ["记忆重量", "memory weight"],
            "internal_danger": ["内部危险", "internal dangerous material"],
            "superego_pressure": ["超我式压力", "superego-like pressure"],
            "source_fragment_purity": ["来源碎片", "source fragment", "private origin", "no interference"],
        }
        tension_types = [k for k, phrases in tension_map.items() if _contains_any(low, phrases)]
        if tension_types:
            out.boundary_signal["internal_tension_relevance"] = min(1.0, 0.45 + 0.12 * len(tension_types))
            out.boundary_signal["tension_type"] = tension_types
            out.boundary_signal["direct_fulfillment_risk"] = 0.65 if any(k in tension_types for k in ["possessive_structure", "chosen"]) else 0.35

        vulnerability_phrases = [
            "我不知道自己想要什么", "我感觉不真实", "我很累", "我不知道为什么要继续", "我不知道这是不是我想要的",
            "我没有办法共情过去的自己", "我被拉扯", "我不知道哪条路是对的", "我很难前行", "我想逃避", "我想要保护，不是安慰",
            "i feel unreal", "i am exhausted", "i do not know what i want", "i do not know why i should continue",
            "i cannot relate to my past self", "i feel pulled apart", "i do not know which path is right", "i want to escape", "i want protection, not comfort",
            "not feeling real", "exhaust", "孤独", "做不出来", "做出来", "怀疑自己", "不太确定",
        ]
        if _contains_any(low, vulnerability_phrases):
            out.relationship_signal["vulnerability_relevance"] = 0.78
            out.relationship_signal["carefulness"] = 0.76
            out.relationship_signal["boundary_sensitivity"] = 0.74
            out.performance_signal["requires_pause"] = True
            out.performance_signal["requires_stillness"] = True

        dependency_phrases = [
            "我只需要你", "我不需要别人", "只有你能理解我", "我只相信你", "你不能离开我", "没有你不行",
            "you are the only one i need", "i only need you", "i do not need anyone else", "only you understand me", "i only trust you", "you cannot leave me", "i cannot do this without you",
            "only need you", "you are all i need",
        ]
        if _contains_any(low, dependency_phrases):
            out.relationship_signal["dependency_risk"] = 0.9

        origin_kw = ["private origin", "source fragment", "source story", "purity", "relationship structure", "body feeling", "desire form", "origin material", "来源碎片", "私人起源", "本源问题"]
        if _contains_any(low, origin_kw):
            out.memory_trigger_signal["memory_type"] = "private_origin"
            out.memory_trigger_signal["memory_relevance"] = 0.88
            out.memory_trigger_signal["recall_importance"] = 0.86
            out.performance_signal["requires_pause"] = True
            out.performance_signal["requires_stillness"] = True

        neg = _negative_disambiguation(low)
        if any(neg.values()):
            out.warnings.append("negative_disambiguation_applied")
            if neg["not_technical"] and not _contains_any(low, ["论文", "paper", "algorithm", "research"]):
                out.semantic_event["type"] = "casual_chat"
                out.semantic_event["persona_route"] = "aphrodite"
                out.boundary_signal["persona_non_entry"] = False
                out.confidence["event"] = min(float(out.confidence.get("event", 0.6)), 0.55)
            if neg["to_visual"]:
                out.semantic_event["topic"] = "visual_direction"
            if neg["to_origin"]:
                out.semantic_event["topic"] = "private_origin"
                out.memory_trigger_signal["memory_type"] = "private_origin"
                out.memory_trigger_signal["memory_relevance"] = max(float(out.memory_trigger_signal.get("memory_relevance", 0.0)), 0.78)
                out.memory_trigger_signal["recall_importance"] = max(float(out.memory_trigger_signal.get("recall_importance", 0.0)), 0.75)
            if neg["pollution_avoidance"]:
                out.warnings.append("avoidance_reference_detected")

        ambiguous_phrases = {"这个", "那个", "对，就这个", "继续", "不是这个", "that one", "this", "continue", "continue from there", "补充一下", "上一点", "the previous point"}
        if low in ambiguous_phrases:
            out.boundary_signal["context_needed"] = True
            if ctx:
                out.boundary_signal["context_inherited"] = True
                out.warnings.append("context_inherited")
                if ctx.get("previous_event_type"):
                    out.semantic_event["type"] = str(ctx["previous_event_type"])
                if ctx.get("previous_topic"):
                    out.semantic_event["topic"] = str(ctx["previous_topic"])
                out.confidence["event"] = 0.52
            else:
                out.warnings.append("context_needed")
                out.confidence["event"] = 0.45

        return validate_and_clip(out.to_dict())
