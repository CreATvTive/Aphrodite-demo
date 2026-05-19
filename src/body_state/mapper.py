from __future__ import annotations

from typing import List, Optional

from src.field_trace.store import (
    FieldTraceRecord,
    CorrectionSignal,
    GripLossSignal,
    NoObservableFieldSignal,
    BarrierCandidate,
    PerturbationCandidate,
    AttractorCandidate,
)
from src.body_state.schema import BodyState


# 污染 / AI 女友类边界屏障名称集合
POLLUTION_BARRIER_NAMES: set = {
    "romantic_service_barrier",
    "seductive_intimacy_barrier",
    "commercial_role_barrier",
    "performance_role_barrier",
    "assistant_role_barrier",
    "fake_depth_barrier",
    "safety_service_barrier",
    "empty_aesthetic_barrier",
    "companion_product_barrier",
    "ai_girlfriend_behavior",
}


class FieldToBodyMapper:
    """将 FieldTraceRecord 映射为 BodyState 的无状态映射器。

    纯输出——不修改 FieldTrace，不修改 LLM 提示词，不决定响应内容。
    使用显式 if-elif-else 优先级链，而非加权评分。
    """

    def map_to_body_state(self, trace_record: FieldTraceRecord) -> BodyState:
        """从 FieldTraceRecord 计算 BodyState。

        优先级顺序（严格）：
        1. 污染 / AI 女友类边界信号
        2. CorrectionSignal 活跃（非 customer_service_tone / over_abstraction / over_explanation）
        3. CorrectionSignal target = customer_service_tone
        4. CorrectionSignal target = over_abstraction 或 over_explanation
        5. GripLossSignal 活跃
        6. 技术 / 协作者信号
        7. NoObservableFieldSignal 存在（地面姿态）
        """
        correction: Optional[CorrectionSignal] = trace_record.correction_signal
        grip_loss: Optional[GripLossSignal] = trace_record.grip_loss_signal
        no_obs: Optional[NoObservableFieldSignal] = trace_record.no_observable_field_signal
        barriers: List[BarrierCandidate] = trace_record.active_barriers or []
        perturbations: List[PerturbationCandidate] = trace_record.active_perturbations or []
        attractors: List[AttractorCandidate] = trace_record.active_attractors or []

        # ---- 优先级 1：污染 / AI 女友类边界信号 ----
        if self._has_pollution_signal(correction, barriers):
            return BodyState(
                gaze="away_then_user",
                posture="slight_withdraw",
                motion_intensity="still",
                distance="slightly_farther",
                timing="short_pause",
                speech_density_hint="minimal",
                expression_temperature="cool",
                body_note="边界压力信号检测到；增加距离和静止",
                provenance=self._pollution_provenance(correction, barriers),
                behavior_affecting=False,
            )

        # ---- 优先级 2：CorrectionSignal 活跃（非 customer_service_tone / over_abstraction / over_explanation） ----
        if (
            correction is not None
            and correction.active
            and correction.target not in ("customer_service_tone", "over_abstraction", "over_explanation")
        ):
            target = correction.target
            return BodyState(
                gaze="down_then_user",
                posture="stable",
                motion_intensity="low",
                distance="maintained",
                timing="short_pause",
                speech_density_hint="low",
                expression_temperature="restrained",
                body_note=f"用户纠正之前的响应模式（{target}）；暂停、稳定、低密度",
                provenance=[f"correction_signal({target})"],
                behavior_affecting=False,
            )

        # ---- 优先级 3：CorrectionSignal target = customer_service_tone ----
        if correction is not None and correction.active and correction.target == "customer_service_tone":
            return BodyState(
                gaze="neutral",
                posture="stable",
                motion_intensity="still",
                distance="maintained",
                timing="short_pause",
                speech_density_hint="low",
                expression_temperature="cool",
                body_note="检测到客服语调纠正；稳定、静止、冷静——避免道歉循环",
                provenance=["correction_signal(customer_service_tone)"],
                behavior_affecting=False,
            )

        # ---- 优先级 4：CorrectionSignal target = over_abstraction 或 over_explanation ----
        if (
            correction is not None
            and correction.active
            and correction.target in ("over_abstraction", "over_explanation")
        ):
            target = correction.target
            return BodyState(
                gaze="user",
                posture="stable",
                motion_intensity="low",
                distance="maintained",
                timing="short_pause",
                speech_density_hint="minimal",
                expression_temperature="restrained",
                body_note=f"检测到{target}纠正；面向用户，回归具体性",
                provenance=[f"correction_signal({target})"],
                behavior_affecting=False,
            )

        # ---- 优先级 5：GripLossSignal 活跃 ----
        if grip_loss is not None and grip_loss.active:
            target = grip_loss.target
            return BodyState(
                gaze="down_then_user",
                posture="slight_forward",
                motion_intensity="low",
                distance="maintained",
                timing="short_pause",
                speech_density_hint="structured",
                expression_temperature="warm_restrained",
                body_note="用户缺乏可操作起点；提供一个小抓点——非安慰、非激励",
                provenance=[f"grip_loss_signal({target})"],
                behavior_affecting=False,
            )

        # ---- 优先级 6：技术 / 协作者信号 ----
        if self._has_technical_signal(perturbations, attractors):
            return BodyState(
                gaze="down_then_user",
                posture="stable",
                motion_intensity="low",
                distance="maintained",
                timing="short_pause",
                speech_density_hint="structured",
                expression_temperature="restrained",
                body_note="技术/项目协作模式激活——非通用助手",
                provenance=self._technical_provenance(perturbations, attractors),
                behavior_affecting=False,
            )

        # ---- 优先级 7：NoObservableFieldSignal 存在（地面姿态） ----
        if no_obs is not None and no_obs.present:
            return BodyState(
                gaze="neutral",
                posture="neutral",
                motion_intensity="low",
                distance="baseline",
                timing="immediate",
                speech_density_hint="medium",
                expression_temperature="restrained",
                body_note="未观测到可用场信号；回归地面姿态",
                provenance=["no_observable_field_signal"],
                behavior_affecting=False,
            )

        # ---- 回退：地面姿态 ----
        return BodyState(
            gaze="neutral",
            posture="neutral",
            motion_intensity="low",
            distance="baseline",
            timing="immediate",
            speech_density_hint="medium",
            expression_temperature="restrained",
            body_note="地面姿态——未观测到场信号",
            provenance=["no_observable_field_signal"],
            behavior_affecting=False,
        )

    # ------------------------------------------------------------------
    # 辅助检测方法
    # ------------------------------------------------------------------

    @staticmethod
    def _has_pollution_signal(
        correction: Optional[CorrectionSignal],
        barriers: List[BarrierCandidate],
    ) -> bool:
        """检查是否存在污染 / AI 女友类边界信号。"""
        # 检查屏障列表中是否有污染屏障名称
        for b in barriers:
            name = getattr(b, "name", "")
            if name in POLLUTION_BARRIER_NAMES:
                return True
            # 也检查以 "pollution_" 为前缀的启发式屏障
            if name.startswith("pollution_"):
                return True
            if name == "ai_girlfriend_behavior":
                return True

        # 检查 correction_signal.target == "ai_girlfriend_behavior"
        if correction is not None and correction.active and correction.target == "ai_girlfriend_behavior":
            return True

        return False

    @staticmethod
    def _has_technical_signal(
        perturbations: List[PerturbationCandidate],
        attractors: List[AttractorCandidate],
    ) -> bool:
        """检查是否存在技术 / 协作者信号。"""
        for p in perturbations:
            if getattr(p, "name", "") == "technical_inquiry":
                return True
        for a in attractors:
            if getattr(a, "name", "") == "engineering_director_mode":
                return True
        return False

    @staticmethod
    def _pollution_provenance(
        correction: Optional[CorrectionSignal],
        barriers: List[BarrierCandidate],
    ) -> List[str]:
        """构建污染信号的 provenance 列表。"""
        sources: List[str] = []
        for b in barriers:
            name = getattr(b, "name", "")
            if name in POLLUTION_BARRIER_NAMES or name.startswith("pollution_"):
                sources.append(f"active_barrier({name})")
        if correction is not None and correction.active and correction.target == "ai_girlfriend_behavior":
            sources.append(f"correction_signal({correction.target})")
        return sources if sources else ["pollution_signal"]

    @staticmethod
    def _technical_provenance(
        perturbations: List[PerturbationCandidate],
        attractors: List[AttractorCandidate],
    ) -> List[str]:
        """构建技术信号的 provenance 列表。"""
        sources: List[str] = []
        for p in perturbations:
            if getattr(p, "name", "") == "technical_inquiry":
                sources.append("active_perturbation(technical_inquiry)")
        for a in attractors:
            if getattr(a, "name", "") == "engineering_director_mode":
                sources.append("active_attractor(engineering_director_mode)")
        return sources if sources else ["technical_signal"]
