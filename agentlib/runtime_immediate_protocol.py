from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


@dataclass
class ImmediateReplyPacket:
    action: str
    scope: str
    immediate: str

    def to_dict(self) -> Dict[str, Any]:
        return {"action": self.action, "scope": self.scope, "immediate": self.immediate}


class ImmediateReplyProtocol:
    """Explicit-consistency protocol: always emit immediate reply before slow paths."""

    @staticmethod
    def single_slot_clarify_question(user_text: str) -> str:
        text = str(user_text or "")
        low = text.lower()
        if not bool(re.search(r"(报错|error|bug|文件|模块|页面|接口|仓库|项目)", low, re.IGNORECASE)):
            return "你希望我先达成的具体结果是什么？"
        return "你希望我具体处理哪个对象？"

    def compose_immediate_reply(self, user_text: str, route: Dict[str, Any]) -> str:
        action = str((route or {}).get("action") or "CHAT").upper()
        if action == "ASK_CLARIFY":
            q = self.single_slot_clarify_question(user_text)
            return f"收到。{q}"
        if action in {"EXECUTE_LIGHT", "EXECUTE_HEAVY", "TOOL_LIGHT"}:
            return "收到。"
        return "嗯。"

    def send(
        self,
        *,
        user_text: str,
        msg_id: Optional[str],
        router: Any,
        state_machine: Any,
        emit_reply: Callable[..., None],
        mon: Dict[str, Any],
    ) -> ImmediateReplyPacket:
        text = str(user_text or "")

        # Prefer FastGate as the first routing decision.
        fast_gate = getattr(router, "fast_gate", None)
        gate_out = fast_gate.infer(text) if fast_gate is not None and hasattr(fast_gate, "infer") else None
        if gate_out is not None and str(getattr(gate_out, "route", "")).upper() == "CHAT":
            gated = type("_Route", (), {"action": "CHAT", "scope": "MAIN"})()
            mon["immediate_reply_route_source"] = "fast_gate"
        else:
            routed = router.route(user_message=text, user_profile={}, recent_context=[], persona_policy="")
            gated = state_machine.apply(routed, confirmed=False)
            mon["immediate_reply_route_source"] = "router"

        immediate = self.compose_immediate_reply(user_text, {"action": gated.action, "scope": gated.scope})
        emit_reply(msg_id=msg_id, reply_text=immediate, idle_tag=False, structured=True)

        now_ts = float(time.time())
        mon["immediate_reply_sent"] = 1
        mon["immediate_reply_sent_at_timestamp"] = now_ts
        mon["immediate_reply_route_decision"] = str(gated.action)
        mon["sent_at_timestamp"] = now_ts
        mon["route_decision"] = str(gated.action)
        return ImmediateReplyPacket(action=str(gated.action), scope=str(gated.scope), immediate=immediate)
