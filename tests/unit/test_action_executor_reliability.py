from __future__ import annotations

from agentlib.autonomy.actuation import (  # noqa: E402
    ActionEnvelope,
    DialogueExecutor,
    FailureClass,
    InteractionExecutor,
    InterruptMode,
    SceneEffectExecutor,
)


def test_idempotent_action_retries_until_success() -> None:
    calls = {"n": 0}

    def flaky_sink(payload):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")
        return {"ok": True}

    executor = InteractionExecutor(action_sink=flaky_sink)
    envelope = ActionEnvelope.build(
        channel="interaction_feedback",
        target="ui",
        payload={"msg": "hello"},
        retry_policy={"max_attempts": 3, "base_delay_ms": 1, "idempotent": True},
    )

    receipt = executor.execute(envelope)

    assert receipt.success is True
    assert receipt.status == "success"
    assert receipt.attempt_count == 2
    assert calls["n"] == 2


def test_non_idempotent_action_does_not_auto_retry() -> None:
    calls = {"n": 0}

    def failing_sink(payload):
        calls["n"] += 1
        raise RuntimeError("always")

    executor = InteractionExecutor(action_sink=failing_sink)
    envelope = ActionEnvelope.build(
        channel="interaction_feedback",
        target="ui",
        payload={"msg": "hello"},
        retry_policy={"max_attempts": 3, "base_delay_ms": 1, "idempotent": False},
    )

    receipt = executor.execute(envelope)

    assert receipt.success is False
    assert receipt.failure_class == FailureClass.RETRYABLE.value
    assert receipt.attempt_count == 1
    assert calls["n"] == 1


def test_soft_interrupt_is_resumable() -> None:
    executor = InteractionExecutor(action_sink=lambda payload: {"ok": True})
    executor.request_interrupt(InterruptMode.SOFT.value)

    receipt = executor.execute(ActionEnvelope.build(channel="interaction_feedback", target="ui", payload={"a": 1}))

    assert receipt.status == "cancel"
    assert receipt.interrupted is True
    assert receipt.resumable is True
    assert "resume_state" in receipt.details


def test_hard_interrupt_clears_resume_state() -> None:
    executor = InteractionExecutor(action_sink=lambda payload: {"ok": True})
    executor.request_interrupt(InterruptMode.HARD.value)

    receipt = executor.execute(ActionEnvelope.build(channel="interaction_feedback", target="ui", payload={"a": 1}))

    assert receipt.status == "cancel"
    assert receipt.resumable is False
    assert executor.get_resume_state() == {}


def test_dependency_missing_failure_classification() -> None:
    executor = SceneEffectExecutor(effect_sink=None)
    receipt = executor.execute(ActionEnvelope.build(channel="scene_action", target="avatar", payload={"fx": "wave"}))

    assert receipt.success is False
    assert receipt.failure_class == FailureClass.DEPENDENCY_MISSING.value


def test_action_sla_metrics_exposed() -> None:
    delivered = []

    def text_sink(text, payload):
        delivered.append(text)

    executor = DialogueExecutor(text_sink=text_sink)
    ok_receipt = executor.execute(
        ActionEnvelope.build(channel="dialog_utterance", target="user", payload={"text": "hi"})
    )
    failed_receipt = executor.execute(
        ActionEnvelope.build(channel="dialog_utterance", target="user", payload={"text": ""})
    )

    assert ok_receipt.success is True
    assert failed_receipt.success is False
    metrics = executor.get_sla_metrics()
    assert metrics["total_actions"] == 2
    assert 0.0 <= metrics["success_rate"] <= 1.0
    assert isinstance(metrics["avg_latency_ms"], float)


def test_timeout_receipt_status_is_unified() -> None:
    def text_sink(_text, _payload):
        import time

        time.sleep(0.02)

    executor = DialogueExecutor(text_sink=text_sink)
    receipt = executor.execute(
        ActionEnvelope.build(
            channel="dialog_utterance",
            target="user",
            payload={"text": "slow"},
            timeout_s=0.001,
            retry_policy={"max_attempts": 1, "idempotent": True},
        )
    )
    assert receipt.status == "timeout"


def test_cancel_requested_short_circuit() -> None:
    executor = InteractionExecutor(action_sink=lambda payload: {"ok": True})
    receipt = executor.execute(
        ActionEnvelope.build(
            channel="interaction_feedback",
            target="ui",
            payload={"a": 1},
            cancel_requested=True,
        )
    )
    assert receipt.status == "cancel"
    assert receipt.success is False
