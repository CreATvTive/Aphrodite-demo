from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agentlib.companion_prompt import render_system_prompt
from agentlib.runtime_engine import DecisionCoreResult, RuntimeEngine
from agentlib.runtime_state import RuntimeConfig
from agentlib.style_policy import ACTIONS, style_guidance_from_action


GUIDANCE_SNIPPETS = (
    "This turn should be comforting",
    "light and humorous",
    "calm, structured, and direct",
    "one clarifying question",
    "2-4 actionable suggestions",
)


def _engine(tmp_path: str, persona_name: str) -> RuntimeEngine:
    engine = RuntimeEngine(
        config=RuntimeConfig(
            db_path=str(Path(tmp_path) / "metrics.db"),
            state_path=str(Path(tmp_path) / "state.json"),
            memory_first_enabled=False,
            auto_persona_enabled=False,
            auto_web_search_enabled=False,
        )
    )
    engine.persona_name = persona_name
    engine.mon["persona"] = persona_name
    return engine


class _CapturingFakeClient:
    """Stand-in for GLMClient that captures the messages sent for inspection."""

    def __init__(self, captured: dict) -> None:
        self.captured = captured

    def stream_chat(self, messages, temperature=0.8):
        self.captured["messages"] = messages
        yield "ok"


def _drive_brain_loop_once(
    engine: RuntimeEngine,
    *,
    action: str,
    user_text: str = "please continue",
) -> dict:
    """Drive one user turn through the real `_brain_loop` and capture LLM messages."""

    class _FakePolicy:
        def __init__(self) -> None:
            self.act_calls = 0
            self.update_calls = 0

        def update(self, reward: float) -> None:
            self.update_calls += 1

        def act(self, user_text: str, state: dict, msg_id: str | None = None):
            self.act_calls += 1
            return SimpleNamespace(action=action)

    fake_policy = _FakePolicy()
    engine.style_policy = fake_policy  # type: ignore[assignment]
    engine._log_activity = lambda **kwargs: None  # type: ignore[method-assign]
    engine._emit_presence_reply = lambda **kwargs: None  # type: ignore[method-assign]
    engine._action_planner = lambda **kwargs: None  # type: ignore[method-assign]
    engine._decision_core = lambda perception: DecisionCoreResult(  # type: ignore[method-assign]
        trace_id=perception.trace_id,
        event_id=perception.event_id,
        mode="chat",
        action="llm_chat",
        reason="test",
    )
    engine.immediate_protocol = SimpleNamespace(
        send=lambda **kwargs: SimpleNamespace(
            to_dict=lambda: {"action": "EXECUTE_HEAVY", "immediate": ""}
        )
    )
    engine._should_route_debug_command = lambda user_text: False  # type: ignore[method-assign]
    engine._handle_video_summary_command = lambda user_text: None  # type: ignore[method-assign]
    engine._handle_natural_language_control = lambda user_text: None  # type: ignore[method-assign]
    engine._maybe_auto_switch_persona = lambda user_text: None  # type: ignore[method-assign]

    captured: dict = {}
    with patch("agentlib.runtime_engine.GLMClient", return_value=_CapturingFakeClient(captured)):
        engine.event_q.put({"type": "USER", "text": user_text})
        engine.event_q.put(None)
        engine._brain_loop()

    return {
        "captured": captured,
        "fake_policy": fake_policy,
    }


@pytest.mark.parametrize("action", ACTIONS)
def test_aphrodite_blocks_all_legacy_style_policy_hints(action: str) -> None:
    """For every style action, the live brain loop must NOT leak its guidance into aphrodite's prompt."""
    with tempfile.TemporaryDirectory() as tmp:
        engine = _engine(tmp, "aphrodite")
        result = _drive_brain_loop_once(engine, action=action)

    messages = result["captured"].get("messages")
    assert messages, "brain loop did not reach the LLM call"
    system_prompt = str(messages[0]["content"])

    assert style_guidance_from_action(action) not in system_prompt, (
        f"style guidance for action={action!r} leaked into aphrodite prompt"
    )
    for snippet in GUIDANCE_SNIPPETS:
        assert snippet not in system_prompt, (
            f"snippet {snippet!r} leaked into aphrodite prompt for action={action!r}"
        )


def test_non_protected_persona_keeps_legacy_style_guidance() -> None:
    """coach must still receive the guidance text when style_hint flows through _build_system_prompt_bundle."""
    with tempfile.TemporaryDirectory() as tmp:
        engine = _engine(tmp, "coach")

        hint = style_guidance_from_action("suggest")
        _, sections = engine._build_system_prompt_bundle(user_text="hello", style_hint=hint)
        assert sections is not None
        rendered = render_system_prompt(sections)

        assert "2-4 actionable suggestions" in rendered
        assert hint in rendered


def test_coach_brain_loop_keeps_comfort_guidance_when_action_is_comfort() -> None:
    """End-to-end proof that the gate is persona-scoped: coach with action=comfort still gets the guidance."""
    with tempfile.TemporaryDirectory() as tmp:
        engine = _engine(tmp, "coach")
        result = _drive_brain_loop_once(engine, action="comfort")

    messages = result["captured"].get("messages")
    assert messages, "brain loop did not reach the LLM call"
    system_prompt = str(messages[0]["content"])
    assert "This turn should be comforting" in system_prompt


def test_aphrodite_style_policy_runs_and_policy_action_is_recorded() -> None:
    """Aphrodite still samples + learns from style_policy; only the prompt injection is silenced."""
    with tempfile.TemporaryDirectory() as tmp:
        engine = _engine(tmp, "aphrodite")
        result = _drive_brain_loop_once(engine, action="comfort")

    fake_policy = result["fake_policy"]
    assert fake_policy.act_calls == 1
    assert fake_policy.update_calls == 1
    assert engine.mon["policy_action"] == "comfort"

    messages = result["captured"].get("messages")
    assert messages, "brain loop did not reach the LLM call"
    system_prompt = str(messages[0]["content"])
    assert "This turn should be comforting" not in system_prompt


def test_runtime_prompt_construction_still_returns_sections() -> None:
    """_build_system_prompt_bundle for a non-protected persona keeps its existing contract."""
    with tempfile.TemporaryDirectory() as tmp:
        engine = _engine(tmp, "coach")

        hint = style_guidance_from_action("calm")
        system_prompt, sections = engine._build_system_prompt_bundle(
            user_text="hello",
            style_hint=hint,
        )

        assert system_prompt is None
        assert sections is not None
        rendered = render_system_prompt(sections)
        assert "[style]" in rendered
        assert "calm, structured, and direct" in rendered


def test_runtime_does_not_import_language_condition_soft_prefix_pack() -> None:
    source = (Path(__file__).resolve().parents[1] / "agentlib" / "runtime_engine.py").read_text(
        encoding="utf-8"
    )

    assert "language_condition" not in source
    assert "soft_prefix_pack" not in source
