from dataclasses import dataclass, field
from typing import List, Optional
from src.body_action.schema import (
    BodyActionWeight, BodyActionWeights,
    ACTION_PRIMITIVES, WEIGHT_BANDS,
)

# ---------------------------------------------------------------------------
# 污染屏障名称集合 — 触发边界/污染压力规则
# ---------------------------------------------------------------------------
POLLUTION_BARRIER_NAMES = {
    "romantic_service_barrier", "seductive_intimacy_barrier",
    "ai_girlfriend_behavior", "assistant_role_barrier",
    "fake_intimacy_barrier", "generic_comfort_barrier",
}


class BodyActionPolicy:
    """将 FieldTraceRecord 原始信号转换为 BodyActionWeights 的规则引擎。
    
    纯转换层——不执行动画，不决定文本，不修改任何状态。
    """

    @staticmethod
    def map_to_action_weights(trace_record) -> BodyActionWeights:
        """将 FieldTraceRecord 映射为 BodyActionWeights。
        
        参数:
            trace_record: FieldTraceRecord —— 必须包含以下属性：
                - correction_signal
                - grip_loss_signal
                - no_observable_field_signal
                - active_barriers (List)
                - active_perturbations (List)
                - active_attractors (List)
        返回:
            BodyActionWeights
        """
        cs = getattr(trace_record, 'correction_signal', None)
        gls = getattr(trace_record, 'grip_loss_signal', None)
        nos = getattr(trace_record, 'no_observable_field_signal', None)

        # === 优先级 1：边界 / 污染 / AI 女友类压力 ===
        if (cs and cs.active and cs.target == "ai_girlfriend_behavior") or _has_pollution_barrier(trace_record):
            return _build_weights(
                trace_record,
                weights_map={
                    "stillness": "high", "reduce_motion": "high",
                    "slight_withdraw": "medium", "look_away": "medium",
                    "maintain_distance": "high", "slight_forward": "off",
                },
                body_note="边界压力信号检测到；增加距离和静止，不冷漠拒绝",
                source="boundary_pressure",
            )

        # === 优先级 2：纠正 + 抓点损失同时活跃 ===
        elif cs and cs.active and gls and gls.active:
            return _build_weights(
                trace_record,
                weights_map={
                    "pause": "high", "stillness": "high",
                    "reduce_motion": "medium", "look_down": "medium",
                    "look_to_user": "medium", "maintain_distance": "high",
                    "slight_forward": "low",
                },
                body_note="纠正优先于抓点损失；先修复模式，再提供微调方向",
                source="correction_plus_grip_loss",
            )

        # === 优先级 3：customer_service_tone 纠正 ===
        elif cs and cs.active and cs.target == "customer_service_tone":
            return _build_weights(
                trace_record,
                weights_map={
                    "pause": "high", "stillness": "high",
                    "reduce_motion": "high", "maintain_distance": "high",
                    "reset_posture": "medium", "slight_forward": "off",
                },
                body_note="检测到客服语调纠正；稳定、静止、保持距离——避免道歉循环",
                source="correction_customer_service_tone",
            )

        # === 优先级 4：over_abstraction / over_explanation 纠正 ===
        elif cs and cs.active and cs.target in ("over_abstraction", "over_explanation"):
            return _build_weights(
                trace_record,
                weights_map={
                    "pause": "medium", "look_to_user": "medium",
                    "reset_posture": "medium", "reduce_motion": "medium",
                    "stillness": "medium",
                },
                body_note=f"检测到{cs.target}纠正；面向用户，回归具体性",
                source="correction_over_abstraction",
            )

        # === 优先级 5：通用纠正 ===
        elif cs and cs.active:
            return _build_weights(
                trace_record,
                weights_map={
                    "pause": "high", "stillness": "medium",
                    "reduce_motion": "medium", "look_down": "medium",
                    "look_to_user": "medium", "maintain_distance": "high",
                },
                body_note=f"用户纠正之前的响应模式（{cs.target}）；暂停、稳定、低密度",
                source="correction_generic",
            )

        # === 优先级 6：抓点损失 ===
        elif gls and gls.active:
            return _build_weights(
                trace_record,
                weights_map={
                    "pause": "medium", "slight_forward": "medium",
                    "look_down": "medium", "look_to_user": "high",
                    "maintain_distance": "medium", "reduce_motion": "low",
                },
                body_note="用户缺乏可操作起点；提供一个小抓点——非安慰、非激励",
                source="grip_loss",
            )

        # === 优先级 7：技术 / 协作者 ===
        elif _has_technical_signal(trace_record):
            return _build_weights(
                trace_record,
                weights_map={
                    "look_down": "low", "look_to_user": "medium",
                    "maintain_distance": "high", "reduce_motion": "low",
                    "reset_posture": "low",
                },
                body_note="技术/项目协作模式激活——非通用助手",
                source="technical_collaborator",
            )

        # === 优先级 8：无可观测信号 ===
        elif nos and nos.present:
            return _build_weights(
                trace_record,
                weights_map={
                    "reset_posture": "high", "maintain_distance": "low",
                    "stillness": "low", "reduce_motion": "low",
                },
                body_note="未观测到可用场信号；回归地面姿态。不表示输入无意义",
                source="no_observable",
            )

        # === 默认回退 ===
        else:
            return _build_weights(
                trace_record,
                weights_map={
                    "reset_posture": "low", "maintain_distance": "low",
                    "stillness": "low",
                },
                body_note="默认基线姿态——无识别到的场信号",
                source="default_baseline",
            )


# ---------------------------------------------------------------------------
# 模块级辅助函数
# ---------------------------------------------------------------------------

def _has_pollution_barrier(trace_record) -> bool:
    """检查 active_barriers 是否包含任何污染屏障。"""
    try:
        barriers = getattr(trace_record, 'active_barriers', []) or []
        for b in barriers:
            name = getattr(b, 'name', '')
            if name in POLLUTION_BARRIER_NAMES:
                return True
    except Exception:
        pass
    return False


def _has_technical_signal(trace_record) -> bool:
    """检查是否激活了技术/协作者信号。"""
    try:
        perturbations = getattr(trace_record, 'active_perturbations', []) or []
        for p in perturbations:
            if getattr(p, 'name', '') == "technical_inquiry":
                return True
        attractors = getattr(trace_record, 'active_attractors', []) or []
        for a in attractors:
            if getattr(a, 'name', '') == "engineering_director_mode":
                return True
    except Exception:
        pass
    return False


def _build_weights(trace_record, weights_map: dict, body_note: str, source: str) -> BodyActionWeights:
    """从权重映射构建 BodyActionWeights。"""
    actions = []
    for action_name, weight in weights_map.items():
        actions.append(BodyActionWeight(
            action_name=action_name,
            weight=weight,
            rationale=f"来源: {source}",
            provenance=[source],
            behavior_affecting=False,
        ))
    
    source_trace_id = getattr(trace_record, 'turn_id', '')
    return BodyActionWeights(
        weights=actions,
        source_trace_id=source_trace_id or '',
        source_proposals=[],
        body_note=body_note,
        behavior_affecting=False,
    )
