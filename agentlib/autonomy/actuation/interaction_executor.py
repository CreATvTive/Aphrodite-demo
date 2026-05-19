from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
import os
from typing import Any, Callable, Dict, List, Optional, Protocol


class FailureClass(str, Enum):
    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"
    DEPENDENCY_MISSING = "dependency_missing"


class InterruptMode(str, Enum):
    SOFT = "soft"
    HARD = "hard"


@dataclass
class RetryPolicy:
    max_attempts: int = 1
    base_delay_ms: int = 100
    idempotent: bool = False


@dataclass
class ActionReceipt:
    action_id: str
    channel: str
    target: str
    status: str  # success|fail|timeout|cancel
    success: bool
    started_at: float
    ended_at: float
    retry_reason: str = ""
    failure_class: str = FailureClass.NON_RETRYABLE.value
    attempt_count: int = 1
    interrupted: bool = False
    resumable: bool = False
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionThresholds:
    safety_risk_threshold: float = 0.7
    task_blocking_threshold: float = 0.55
    flow_pressure_threshold: float = 0.5

    @classmethod
    def from_env(cls) -> "DecisionThresholds":
        return cls(
            safety_risk_threshold=_env_float("ACT_SAFETY_RISK_THRESHOLD", 0.7),
            task_blocking_threshold=_env_float("ACT_TASK_BLOCKING_THRESHOLD", 0.55),
            flow_pressure_threshold=_env_float("ACT_FLOW_PRESSURE_THRESHOLD", 0.5),
        )


@dataclass(frozen=True)
class DecisionSummary:
    strategy: str
    reason: str


@dataclass(frozen=True)
class DecisionFeedback:
    success: bool
    status: str
    message: str


@dataclass
class TurnDecisionReport:
    phase_trace: List[str]
    decision: DecisionSummary
    envelope: ActionEnvelope
    receipt: ActionReceipt
    feedback: DecisionFeedback


@dataclass(frozen=True)
class DecisionContext:
    safety_risk: float = 0.0
    task_blocking: float = 0.0
    flow_pressure: float = 0.0
    expressive_gain: float = 0.0
    user_intent: str = ""


class ExpressiveWeightProvider(Protocol):
    """Optional extension hook for future emotion weighting (interface only)."""

    def get_expressive_weight(self, context: DecisionContext) -> float: ...


@dataclass
class ActionEnvelope:
    """Unified action contract for all actuation channels.

    Priorities (high -> low): safety > task_completion > interaction_smoothness > expressive_enrichment.
    """

    action_id: str
    channel: str
    target: str
    payload: Dict[str, Any] = field(default_factory=dict)
    preconditions: List[Dict[str, Any]] = field(default_factory=list)
    timeout_s: float = 3.0
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    cancel_token: str = ""
    cancel_requested: bool = False
    interruptible: bool = True
    interrupt_mode: str = ""
    receipt_required: bool = True
    priority: str = "interaction_smoothness"
    rollback_payload: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        *,
        channel: str,
        target: str,
        payload: Optional[Dict[str, Any]] = None,
        preconditions: Optional[List[Dict[str, Any]]] = None,
        timeout_s: float = 3.0,
        retry_policy: Optional[Dict[str, Any]] = None,
        cancel_token: str = "",
        cancel_requested: bool = False,
        interruptible: bool = True,
        interrupt_mode: str = "",
        receipt_required: bool = True,
        priority: str = "interaction_smoothness",
        rollback_payload: Optional[Dict[str, Any]] = None,
    ) -> "ActionEnvelope":
        return cls(
            action_id=f"act_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}",
            channel=str(channel or "generic").strip().lower(),
            target=str(target or "").strip(),
            payload=dict(payload or {}),
            preconditions=[dict(p) for p in list(preconditions or []) if isinstance(p, dict)],
            timeout_s=max(0.001, float(timeout_s or 0.001)),
            retry_policy=RetryPolicy(
                max_attempts=max(1, int((retry_policy or {}).get("max_attempts", 1) or 1)),
                base_delay_ms=max(0, int((retry_policy or {}).get("base_delay_ms", 100) or 0)),
                idempotent=bool((retry_policy or {}).get("idempotent", False)),
            ),
            cancel_token=str(cancel_token or "").strip(),
            cancel_requested=bool(cancel_requested),
            interruptible=bool(interruptible),
            interrupt_mode=str(interrupt_mode or "").strip().lower(),
            receipt_required=bool(receipt_required),
            priority=str(priority or "interaction_smoothness").strip().lower(),
            rollback_payload=dict(rollback_payload or {}),
        )


class InteractionExecutor:
    """Base interaction executor that validates envelope preconditions."""

    PRIORITY_ORDER = {
        "safety": 0,
        "task_completion": 1,
        "interaction_smoothness": 2,
        "expressive_enrichment": 3,
    }

    def __init__(
        self,
        action_sink: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        decision_thresholds: Optional[DecisionThresholds] = None,
        expressive_weight_provider: Optional[ExpressiveWeightProvider] = None,
    ) -> None:
        self._action_sink = action_sink
        self._interrupt_mode: Optional[InterruptMode] = None
        self._resume_state: Dict[str, Any] = {}
        self._sla_total = 0
        self._sla_success = 0
        self._sla_latency_sum_ms = 0.0
        self._decision_thresholds = decision_thresholds or DecisionThresholds.from_env()
        self._expressive_weight_provider = expressive_weight_provider

    def request_interrupt(self, mode: str = InterruptMode.SOFT.value) -> None:
        normalized = str(mode or InterruptMode.SOFT.value).strip().lower()
        self._interrupt_mode = InterruptMode.HARD if normalized == InterruptMode.HARD.value else InterruptMode.SOFT

    def clear_interrupt(self) -> None:
        self._interrupt_mode = None

    def get_resume_state(self) -> Dict[str, Any]:
        return dict(self._resume_state)

    def get_sla_metrics(self) -> Dict[str, Any]:
        success_rate = float(self._sla_success / self._sla_total) if self._sla_total else 0.0
        avg_latency_ms = float(self._sla_latency_sum_ms / self._sla_total) if self._sla_total else 0.0
        return {
            "total_actions": self._sla_total,
            "success_count": self._sla_success,
            "success_rate": round(success_rate, 4),
            "avg_latency_ms": round(avg_latency_ms, 2),
        }

    def can_execute(self, envelope: ActionEnvelope) -> bool:
        for cond in list(envelope.preconditions or []):
            if not bool(cond.get("ok", True)):
                return False
        return True

    def decide_strategy(self, context: DecisionContext) -> DecisionSummary:
        """Deterministic policy priority: safety > task > flow > expressive."""
        c = context
        t = self._decision_thresholds
        expressive_weight = 1.0
        if self._expressive_weight_provider is not None:
            try:
                expressive_weight = float(self._expressive_weight_provider.get_expressive_weight(c))
            except Exception:
                expressive_weight = 1.0

        if float(c.safety_risk) >= float(t.safety_risk_threshold):
            return DecisionSummary(strategy="safety", reason=f"安全风险较高({c.safety_risk:.2f})，优先安全策略。")
        if float(c.task_blocking) >= float(t.task_blocking_threshold):
            return DecisionSummary(strategy="task_completion", reason=f"任务阻塞度高({c.task_blocking:.2f})，优先确保任务完成。")
        if float(c.flow_pressure) >= float(t.flow_pressure_threshold):
            return DecisionSummary(strategy="interaction_smoothness", reason=f"交互压力较高({c.flow_pressure:.2f})，优先保证对话流畅。")
        gain = max(0.0, float(c.expressive_gain) * float(expressive_weight))
        return DecisionSummary(strategy="expressive_enrichment", reason=f"基础目标稳定，采用表现增强策略(gain={gain:.2f})。")

    def run_turn(
        self,
        *,
        context: DecisionContext,
        channel: str = "interaction",
        target: str = "turn",
        payload: Optional[Dict[str, Any]] = None,
    ) -> TurnDecisionReport:
        """
        Run a complete turn lifecycle:
        perceive -> decision -> plan -> execute -> feedback
        """
        phases: List[str] = []

        # 1) perceive
        phases.append("perceive")
        perceived = DecisionContext(
            safety_risk=max(0.0, float(context.safety_risk)),
            task_blocking=max(0.0, float(context.task_blocking)),
            flow_pressure=max(0.0, float(context.flow_pressure)),
            expressive_gain=max(0.0, float(context.expressive_gain)),
            user_intent=str(context.user_intent or ""),
        )

        # 2) decision
        phases.append("decision")
        summary = self.decide_strategy(perceived)

        # 3) plan
        phases.append("plan")
        envelope = ActionEnvelope.build(
            channel=str(channel or "interaction"),
            target=str(target or "turn"),
            payload={
                "strategy": summary.strategy,
                "reason": summary.reason,
                "context": {
                    "safety_risk": perceived.safety_risk,
                    "task_blocking": perceived.task_blocking,
                    "flow_pressure": perceived.flow_pressure,
                    "expressive_gain": perceived.expressive_gain,
                },
                **dict(payload or {}),
            },
            priority=str(summary.strategy or "interaction_smoothness"),
        )

        # 4) execute
        phases.append("execute")
        receipt = self.execute(envelope)

        # 5) feedback
        phases.append("feedback")
        feedback = DecisionFeedback(
            success=bool(receipt.success),
            status=str(receipt.status or ""),
            message=(
                f"策略={summary.strategy}，执行成功。"
                if bool(receipt.success)
                else f"策略={summary.strategy}，执行失败({receipt.retry_reason or receipt.status})。"
            ),
        )
        return TurnDecisionReport(
            phase_trace=phases,
            decision=summary,
            envelope=envelope,
            receipt=receipt,
            feedback=feedback,
        )

    def evaluate_strategy_stability(self, context: DecisionContext, repeats: int = 5) -> Dict[str, Any]:
        n = max(1, int(repeats or 1))
        strategies: List[str] = []
        for _ in range(n):
            strategies.append(self.decide_strategy(context).strategy)
        base = strategies[0]
        drift_count = sum(1 for s in strategies if s != base)
        return {
            "repeats": n,
            "strategies": list(strategies),
            "base_strategy": base,
            "drift_count": int(drift_count),
            "drift_rate": float(drift_count / max(1, n)),
            "stable": bool(drift_count == 0),
        }

    def execute(self, envelope: ActionEnvelope) -> ActionReceipt:
        started = time.time()
        self._resume_state = {"action_id": envelope.action_id, "channel": envelope.channel, "target": envelope.target}
        if not self.can_execute(envelope):
            return self._finalize(
                ActionReceipt(
                    action_id=envelope.action_id,
                    channel=envelope.channel,
                    target=envelope.target,
                    status="fail",
                    success=False,
                    started_at=started,
                    ended_at=time.time(),
                    retry_reason="precondition_failed",
                    failure_class=FailureClass.NON_RETRYABLE.value,
                    details={"preconditions": list(envelope.preconditions or [])},
                )
            )
        if envelope.cancel_requested:
            return self._finalize(
                ActionReceipt(
                    action_id=envelope.action_id,
                    channel=envelope.channel,
                    target=envelope.target,
                    status="cancel",
                    success=False,
                    started_at=started,
                    ended_at=time.time(),
                    retry_reason="cancel_requested",
                    failure_class=FailureClass.NON_RETRYABLE.value,
                )
            )

        requested_interrupt = str(envelope.interrupt_mode or "").strip().lower()
        if requested_interrupt in {InterruptMode.SOFT.value, InterruptMode.HARD.value} and envelope.interruptible:
            if requested_interrupt == InterruptMode.HARD.value:
                self.request_interrupt(InterruptMode.HARD.value)
            elif self._interrupt_mode is None:
                self.request_interrupt(InterruptMode.SOFT.value)
        if self._interrupt_mode is not None and envelope.interruptible:
            return self._finalize(
                ActionReceipt(
                    action_id=envelope.action_id,
                    channel=envelope.channel,
                    target=envelope.target,
                    status="cancel",
                    success=False,
                    started_at=started,
                    ended_at=time.time(),
                    retry_reason=f"interrupt_{self._interrupt_mode.value}",
                    failure_class=FailureClass.NON_RETRYABLE.value,
                    interrupted=True,
                    resumable=self._interrupt_mode == InterruptMode.SOFT,
                    details={"resume_state": self.get_resume_state()},
                )
            )

        attempts_allowed = 1
        if envelope.retry_policy.idempotent:
            attempts_allowed = max(1, int(envelope.retry_policy.max_attempts or 1))
        last_receipt: Optional[ActionReceipt] = None
        for attempt in range(1, attempts_allowed + 1):
            self._resume_state["attempt"] = attempt
            receipt = self._execute_once(envelope, started)
            receipt.attempt_count = attempt
            last_receipt = receipt
            if receipt.success:
                break
            if receipt.failure_class != FailureClass.RETRYABLE.value or attempt >= attempts_allowed:
                break
            time.sleep(float(envelope.retry_policy.base_delay_ms or 0) / 1000.0)
        return self._finalize(last_receipt or self._execute_once(envelope, started))

    def _execute_once(self, envelope: ActionEnvelope, started: float) -> ActionReceipt:
        if self._action_sink is None:
            return ActionReceipt(
                action_id=envelope.action_id,
                channel=envelope.channel,
                target=envelope.target,
                status="success",
                success=True,
                started_at=started,
                ended_at=time.time(),
                details={"envelope": asdict(envelope), "sink": "noop"},
            )
        try:
            sink_out = self._action_sink(
                {
                    "action_id": envelope.action_id,
                    "channel": envelope.channel,
                    "target": envelope.target,
                    "payload": dict(envelope.payload or {}),
                }
            )
        except Exception as e:
            return ActionReceipt(
                action_id=envelope.action_id,
                channel=envelope.channel,
                target=envelope.target,
                status="fail",
                success=False,
                started_at=started,
                ended_at=time.time(),
                retry_reason=f"interaction_error:{type(e).__name__}",
                failure_class=FailureClass.RETRYABLE.value,
            )
        return ActionReceipt(
            action_id=envelope.action_id,
            channel=envelope.channel,
            target=envelope.target,
            status="success",
            success=True,
            started_at=started,
            ended_at=time.time(),
            failure_class="",
            details={"envelope": asdict(envelope), "sink_output": dict(sink_out or {})},
        )

    def _finalize(self, receipt: ActionReceipt) -> ActionReceipt:
        self._sla_total += 1
        if receipt.success:
            self._sla_success += 1
        self._sla_latency_sum_ms += max(0.0, (float(receipt.ended_at) - float(receipt.started_at)) * 1000.0)
        receipt.details = dict(receipt.details or {})
        receipt.details["sla"] = self.get_sla_metrics()
        if receipt.interrupted and not receipt.resumable:
            self._resume_state = {}
            self.clear_interrupt()
        return receipt

def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)
