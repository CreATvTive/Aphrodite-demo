from agentlib.router.fast_gate import FastGate
from agentlib.router.llm_router import LLMRouter, RouterStateMachine, RouterOutput


def test_fast_gate_task_routes_execute() -> None:
    g = FastGate()
    out = g.infer("帮我排查这个报错")
    assert out.route == "EXECUTE"
    assert out.request is True
    assert out.request_type == "task"


def test_fast_gate_emotional_support_stays_chat() -> None:
    g = FastGate()
    out = g.infer("你能安慰我一下吗")
    assert out.route == "CHAT"
    assert out.request is True
    assert out.request_type == "emotional_support"


def test_router_output_actions_in_5_classes() -> None:
    r = LLMRouter()
    out = r.route(user_message="帮我删除仓库里的临时文件并提交")
    assert out.action in {"CHAT", "ASK_CLARIFY", "TOOL_LIGHT", "EXECUTE_LIGHT", "EXECUTE_HEAVY"}


def test_router_gate_requires_confirm_for_restricted_execute() -> None:
    sm = RouterStateMachine()
    out = RouterOutput(
        action="EXECUTE_HEAVY",
        scope="PROJECT_ONLY",
        needs_confirm=True,
        reason="sensitive",
        confidence=0.9,
    )
    gated = sm.apply(out, confirmed=False)
    # actual behaviour: action is preserved, confirm_required_gate is appended to reason
    assert gated.action == "EXECUTE_HEAVY"
    assert gated.needs_confirm is True
    assert "confirm_required_gate" in gated.reason


def test_router_gate_passes_when_confirmed() -> None:
    sm = RouterStateMachine()
    out = RouterOutput(
        action="EXECUTE_LIGHT",
        scope="ISOLATED",
        needs_confirm=True,
        reason="sensitive",
        confidence=0.8,
    )
    gated = sm.apply(out, confirmed=True)
    assert gated.action == "EXECUTE_LIGHT"


def test_router_hard_rule_insufficient_info_forces_ask_clarify() -> None:
    r = LLMRouter()
    out = r.route(user_message="帮我弄这个")
    assert out.action == "ASK_CLARIFY"
    assert out.reason == "insufficient_info_hard_rule"


def test_router_stage_a_biases_to_stay_chat() -> None:
    r = LLMRouter()
    out = r.route(user_message="你怎么看现在的 AI agent 方向？")
    assert out.action == "CHAT"
    assert out.reason == "fast_gate_stay_chat"


def test_need_act_execute_heavy() -> None:
    r = LLMRouter()
    out = r.route(user_message="请帮我运行评测并合并分支")
    assert out.action == "EXECUTE_HEAVY"
    assert "need_act_execute_heavy" in out.reason


def test_need_act_tool_light() -> None:
    r = LLMRouter()
    out = r.route(user_message="帮我整理一下并写文案")
    assert out.action == "TOOL_LIGHT"
    assert "need_act_tool_light" in out.reason


def test_router_two_stage_chat_then_reply_type() -> None:
    r = LLMRouter()
    stay_chat, req_type = r._decide_chat_route("我今天有点烦")
    assert stay_chat is True
    assert req_type in {"unknown", "emotional_support"}

    stay_chat2, _ = r._decide_chat_route("请帮我修一下这个报错")
    assert stay_chat2 is False
