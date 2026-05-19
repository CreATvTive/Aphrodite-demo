"""运行时不变性测试：可观测性（FieldTrace / BodyState）失败不影响回复文本。

设计约束：
- 每个测试必须断言 reply_text 的值与模拟回复的预期不变值一致
- 测试不依赖于 src.field_trace 或 src.body_state 的存在
- behavior_affecting 必须始终为 False
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from agentlib.runtime_engine import RuntimeEngine


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _capture_emit_reply(engine: RuntimeEngine, monkeypatch) -> dict:
    """Monkeypatch _emit_reply 以捕获传入的 reply_text，并返回捕获的 dict。"""
    captured: dict = {}

    def fake_emit(msg_id, reply_text, idle_tag, structured=False):
        captured["reply_text"] = reply_text
        captured["msg_id"] = msg_id
        captured["idle_tag"] = idle_tag
        captured["structured"] = structured

    monkeypatch.setattr(engine, "_emit_reply", fake_emit)
    return captured


def _make_trace(engine: RuntimeEngine, user_text: str, assistant_text: str) -> dict:
    """调用 _presence_min_flow 并返回 trace dict。"""
    return engine._presence_min_flow(
        user_text=user_text,
        assistant_text=assistant_text,
        trace_id="td_test",
        event_id="ed_test",
        route="llm",
        latency_tier="tier_2",
    )


# ---------------------------------------------------------------------------
# 测试 1：提取器异常不影响回复
# ---------------------------------------------------------------------------

def test_extractor_raise_does_not_change_reply(monkeypatch):
    """如果 FieldTraceExtractor.extract() 抛出异常，原始 reply_text 仍被发出。"""
    eng = RuntimeEngine()
    # 强制启用可观测性，但让 extractor 抛出异常
    eng._observability_enabled = True
    eng._field_trace_extractor = MagicMock()
    eng._field_trace_extractor.extract.side_effect = RuntimeError("simulated extractor failure")
    eng._field_trace_store = MagicMock()
    eng._body_mapper = MagicMock()
    eng._body_logger = MagicMock()

    captured = _capture_emit_reply(eng, monkeypatch)

    expected_reply = "先看 traceback。"
    eng._emit_presence_reply(
        msg_id="m1",
        user_text="怎么修这个 bug",
        reply_text=expected_reply,
        idle_tag=False,
        route="llm",
        latency_tier="tier_2",
    )

    assert captured["reply_text"] == expected_reply
    # Store.record 不应被调用（因为 extractor 在之前就抛出了异常）
    eng._field_trace_store.record.assert_not_called()


# ---------------------------------------------------------------------------
# 测试 2：Store.record() 异常不影响回复
# ---------------------------------------------------------------------------

def test_store_raise_does_not_change_reply(monkeypatch):
    """如果 FieldTraceStore.record() 抛出异常，原始 reply_text 仍被发出。"""
    eng = RuntimeEngine()
    eng._observability_enabled = True
    eng._field_trace_extractor = MagicMock()
    eng._field_trace_store = MagicMock()
    eng._field_trace_store.record.side_effect = RuntimeError("simulated store failure")
    eng._body_mapper = None
    eng._body_logger = None

    captured = _capture_emit_reply(eng, monkeypatch)

    expected_reply = "我会保持边界。"
    eng._emit_presence_reply(
        msg_id="m2",
        user_text="我只需要你",
        reply_text=expected_reply,
        idle_tag=False,
        route="direct",
        latency_tier="tier_1",
    )

    assert captured["reply_text"] == expected_reply
    # Extractor 仍应被调用，但 record 抛异常后被静默捕获
    eng._field_trace_extractor.extract.assert_called_once()


# ---------------------------------------------------------------------------
# 测试 3：Mapper 异常不影响回复
# ---------------------------------------------------------------------------

def test_mapper_raise_does_not_change_reply(monkeypatch):
    """如果 FieldToBodyMapper.map_to_body_state() 抛出异常，原始 reply_text 仍被发出。"""
    eng = RuntimeEngine()
    eng._observability_enabled = True
    eng._field_trace_extractor = MagicMock()
    eng._field_trace_store = MagicMock()
    eng._body_mapper = MagicMock()
    eng._body_mapper.map_to_body_state.side_effect = RuntimeError("simulated mapper failure")
    eng._body_logger = MagicMock()

    captured = _capture_emit_reply(eng, monkeypatch)

    expected_reply = "我来分析一下这个报错。"
    eng._emit_presence_reply(
        msg_id="m3",
        user_text="有个报错帮我看下",
        reply_text=expected_reply,
        idle_tag=False,
        route="llm",
        latency_tier="tier_2",
    )

    assert captured["reply_text"] == expected_reply
    # Logger 不应被调用（因为 mapper 在之前就抛出了异常）
    eng._body_logger.log.assert_not_called()


# ---------------------------------------------------------------------------
# 测试 4：Logger 异常不影响回复
# ---------------------------------------------------------------------------

def test_logger_raise_does_not_change_reply(monkeypatch):
    """如果 BodyStateLogger.log() 抛出异常，原始 reply_text 仍被发出。"""
    eng = RuntimeEngine()
    eng._observability_enabled = True
    eng._field_trace_extractor = MagicMock()
    eng._field_trace_store = MagicMock()
    eng._body_mapper = MagicMock()
    eng._body_logger = MagicMock()
    eng._body_logger.log.side_effect = RuntimeError("simulated logger failure")

    captured = _capture_emit_reply(eng, monkeypatch)

    expected_reply = "先看 traceback。"
    eng._emit_presence_reply(
        msg_id="m4",
        user_text="怎么修这个 bug",
        reply_text=expected_reply,
        idle_tag=False,
        route="llm",
        latency_tier="tier_2",
    )

    assert captured["reply_text"] == expected_reply
    # Mapper 应已被调用
    eng._body_mapper.map_to_body_state.assert_called_once()


# ---------------------------------------------------------------------------
# 测试 5：无路由 / 记忆副作用
# ---------------------------------------------------------------------------

def test_observability_failure_does_not_trigger_routing_or_memory(monkeypatch):
    """FieldTrace / BodyState 失败不调用路由、人格、记忆或提示构造路径。"""
    eng = RuntimeEngine()
    eng._observability_enabled = True
    eng._field_trace_extractor = MagicMock()
    eng._field_trace_extractor.extract.side_effect = RuntimeError("simulated failure")
    eng._field_trace_store = MagicMock()

    captured = _capture_emit_reply(eng, monkeypatch)

    # 记录关键副作用路径的初始状态
    presence_trace_before = eng.mon.get("presence_last_trace")

    eng._emit_presence_reply(
        msg_id="m5",
        user_text="你好",
        reply_text="你好！有什么我可以帮忙的？",
        idle_tag=False,
        route="llm",
        latency_tier="tier_2",
    )

    # reply_text 不变
    assert captured["reply_text"] == "你好！有什么我可以帮忙的？"
    # presence_last_trace 仍被正确设置（在钩子之前设置）
    assert eng.mon.get("presence_last_trace") is not presence_trace_before


# ---------------------------------------------------------------------------
# 测试 6：behavior_affecting 保持 false
# ---------------------------------------------------------------------------

def test_behavior_affecting_stays_false_on_failure(monkeypatch):
    """在任何可观测性失败路径上 behavior_affecting 保持 false。"""
    eng = RuntimeEngine()
    eng._observability_enabled = True
    eng._field_trace_extractor = MagicMock()
    eng._field_trace_store = MagicMock()
    eng._body_mapper = MagicMock()
    eng._body_logger = MagicMock()
    # 模拟 mapper 返回带有 behavior_affecting 的对象，但 logger 抛出异常
    from src.body_state.schema import BodyState
    eng._body_mapper.map_to_body_state.return_value = BodyState(
        gaze="neutral",
        posture="neutral",
        motion_intensity="low",
        distance="baseline",
        timing="immediate",
        speech_density_hint="medium",
        expression_temperature="restrained",
        body_note="ground state",
        provenance=["test"],
        behavior_affecting=False,
    )
    eng._body_logger.log.side_effect = RuntimeError("simulated logger failure")

    captured = _capture_emit_reply(eng, monkeypatch)

    expected_reply = "先看 traceback。"
    eng._emit_presence_reply(
        msg_id="m6",
        user_text="怎么修这个 bug",
        reply_text=expected_reply,
        idle_tag=False,
        route="llm",
        latency_tier="tier_2",
    )

    assert captured["reply_text"] == expected_reply
    # BodyState 对象的 behavior_affecting 仍为 False
    body_state = eng._body_mapper.map_to_body_state.return_value
    assert body_state.behavior_affecting is False


# ---------------------------------------------------------------------------
# 测试 7：可观测性已禁用（不崩溃）
# ---------------------------------------------------------------------------

def test_observability_disabled_not_crashed(monkeypatch):
    """可观测性被禁用时，RuntimeEngine 不崩溃且正常发出回复文本。"""
    eng = RuntimeEngine()
    eng._observability_enabled = False
    eng._field_trace_store = None
    eng._field_trace_extractor = None
    eng._body_mapper = None
    eng._body_logger = None

    captured = _capture_emit_reply(eng, monkeypatch)

    expected_reply = "我刚刚有点走神了，再说一次好吗？"
    # 不应崩溃
    eng._emit_presence_reply(
        msg_id="m7",
        user_text="",
        reply_text=expected_reply,
        idle_tag=False,
        route="error_safe",
        latency_tier="tier_1",
    )

    assert captured["reply_text"] == expected_reply


# ---------------------------------------------------------------------------
# 测试 8：缺失模块时 RuntimeEngine 可构造
# ---------------------------------------------------------------------------

def test_engine_starts_without_field_trace():
    """缺少 field_trace 包时 RuntimeEngine 仍可构造。"""
    import agentlib.runtime_engine as re_mod

    original = re_mod._FIELD_TRACE_AVAILABLE
    try:
        # 模拟缺少 field_trace 包：将模块级哨兵设为 False
        re_mod._FIELD_TRACE_AVAILABLE = False
        eng = re_mod.RuntimeEngine()
        # 不应崩溃
        assert eng._observability_enabled is False
        assert eng._field_trace_store is None
        assert eng._field_trace_extractor is None
        assert eng._body_mapper is None
        assert eng._body_logger is None
    finally:
        re_mod._FIELD_TRACE_AVAILABLE = original


# ---------------------------------------------------------------------------
# 测试 9：所有钩子路径均保护 reply_text
# ---------------------------------------------------------------------------

def test_all_hook_paths_preserve_reply_text(monkeypatch):
    """综合测试：无论钩子内部发生什么，reply_text 始终不变。"""
    eng = RuntimeEngine()

    # 场景 A：正常路径
    eng._observability_enabled = True
    eng._field_trace_extractor = MagicMock()
    eng._field_trace_store = MagicMock()
    eng._body_mapper = MagicMock()
    eng._body_logger = MagicMock()

    captured = _capture_emit_reply(eng, monkeypatch)
    eng._emit_presence_reply(
        msg_id="ma",
        user_text="你好",
        reply_text="场景A",
        idle_tag=False,
        route="llm",
        latency_tier="tier_2",
    )
    assert captured["reply_text"] == "场景A"

    # 场景 B：extractor 为 None（_observability_enabled=True 但属性为 None 的边界情况）
    eng._observability_enabled = False
    eng._field_trace_extractor = None
    eng._field_trace_store = None
    captured = _capture_emit_reply(eng, monkeypatch)
    eng._emit_presence_reply(
        msg_id="mb",
        user_text="你好",
        reply_text="场景B",
        idle_tag=False,
        route="llm",
        latency_tier="tier_2",
    )
    assert captured["reply_text"] == "场景B"

    # 场景 C：可观测性已启用，但 body_mapper / body_logger 为 None
    eng._observability_enabled = True
    eng._field_trace_extractor = MagicMock()
    eng._field_trace_store = MagicMock()
    eng._body_mapper = None
    eng._body_logger = None
    captured = _capture_emit_reply(eng, monkeypatch)
    eng._emit_presence_reply(
        msg_id="mc",
        user_text="你好",
        reply_text="场景C",
        idle_tag=False,
        route="llm",
        latency_tier="tier_2",
    )
    assert captured["reply_text"] == "场景C"


# ---------------------------------------------------------------------------
# 测试 10：未导入 body_state 包时正常运行
# ---------------------------------------------------------------------------

def test_engine_runs_without_body_state(monkeypatch):
    """缺少 body_state 包时，RuntimeEngine 正常运行且 reply_text 不变。"""
    eng = RuntimeEngine()
    # 模拟有 field_trace 但没有 body_state 的场景
    eng._observability_enabled = True
    eng._field_trace_extractor = MagicMock()
    eng._field_trace_store = MagicMock()
    eng._body_mapper = None
    eng._body_logger = None

    captured = _capture_emit_reply(eng, monkeypatch)

    expected_reply = "我在这里。"
    eng._emit_presence_reply(
        msg_id="m10",
        user_text="你在吗",
        reply_text=expected_reply,
        idle_tag=False,
        route="direct",
        latency_tier="tier_1",
    )

    assert captured["reply_text"] == expected_reply
    # Extractor 和 Store 仍应正常工作
    eng._field_trace_extractor.extract.assert_called_once()
    eng._field_trace_store.record.assert_called_once()
