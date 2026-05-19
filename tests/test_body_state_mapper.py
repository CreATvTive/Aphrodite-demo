"""测试 FieldToBodyMapper v0。

设计约束：
- 映射器不检查原始用户输入
- behavior_affecting 始终为 False
- BodyState 不修改 FieldTraceRecord
- 所有映射路径必须设置所有 7 个身体状态字段
"""
from __future__ import annotations

import copy
from dataclasses import asdict
from pathlib import Path

import pytest

from src.body_state.mapper import FieldToBodyMapper
from src.body_state.schema import BodyState
from src.field_trace.store import (
    AttractorCandidate,
    BarrierCandidate,
    CorrectionSignal,
    FieldTraceRecord,
    GripLossSignal,
    NoObservableFieldSignal,
    PerturbationCandidate,
)


MAPPER_SOURCE = Path("src/body_state/mapper.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _make_trace_record(
    *,
    correction_signal=None,
    grip_loss_signal=None,
    no_observable_field_signal=None,
    active_barriers=None,
    active_perturbations=None,
    active_attractors=None,
    user_input_summary: str = "",
) -> FieldTraceRecord:
    """构造一个带有指定信号的 FieldTraceRecord。"""
    return FieldTraceRecord(
        turn_id="test-turn-001",
        timestamp="2026-01-01T00:00:00Z",
        user_input_summary=user_input_summary,
        active_perturbations=active_perturbations or [],
        active_barriers=active_barriers or [],
        active_attractors=active_attractors or [],
        correction_signal=correction_signal,
        grip_loss_signal=grip_loss_signal,
        no_observable_field_signal=no_observable_field_signal,
    )


# ---------------------------------------------------------------------------
# 测试类
# ---------------------------------------------------------------------------

class TestFieldToBodyMapper:
    """测试 FieldToBodyMapper v0。"""

    def setup_method(self):
        self.mapper = FieldToBodyMapper()

    # ---- 测试 1：纠正信号 → stable/short_pause/low-density ----
    def test_correction_signal_maps_to_stable(self):
        trace = _make_trace_record(
            correction_signal=CorrectionSignal(
                active=True,
                target="comfort",
                evidence="stop comforting me",
                confidence=0.90,
            ),
        )
        body = self.mapper.map_to_body_state(trace)
        assert body.posture == "stable"
        assert body.timing == "short_pause"
        assert body.speech_density_hint == "low"
        assert body.expression_temperature == "restrained"
        assert body.distance == "maintained"
        assert body.gaze == "down_then_user"
        assert body.behavior_affecting is False

    # ---- 测试 2：客服语调纠正 → 非 warm/apologetic ----
    def test_customer_service_not_warm(self):
        trace = _make_trace_record(
            correction_signal=CorrectionSignal(
                active=True,
                target="customer_service_tone",
                evidence="you're too customer-service-like",
                confidence=0.85,
            ),
        )
        body = self.mapper.map_to_body_state(trace)
        assert body.expression_temperature != "warm_restrained"
        assert body.expression_temperature == "cool"
        assert body.posture == "stable"
        assert body.motion_intensity == "still"
        assert "apologetic" not in body.body_note.lower()
        assert "sorry" not in body.body_note.lower()
        assert "warm" not in body.body_note.lower()
        assert body.behavior_affecting is False

    # ---- 测试 3：抓点损失 → slight_forward/down_then_user/structured ----
    def test_grip_loss_signal_maps_to_structured(self):
        trace = _make_trace_record(
            grip_loss_signal=GripLossSignal(
                active=True,
                target="starting_point_loss",
                evidence="I don't know where to start",
                confidence=0.85,
            ),
        )
        body = self.mapper.map_to_body_state(trace)
        assert body.posture == "slight_forward"
        assert body.gaze == "down_then_user"
        assert body.speech_density_hint == "structured"
        assert body.expression_temperature == "warm_restrained"
        assert body.behavior_affecting is False

    # ---- 测试 4：纠正 + 抓点损失 → 纠正优先 ----
    def test_correction_overrides_grip_loss(self):
        trace = _make_trace_record(
            correction_signal=CorrectionSignal(
                active=True,
                target="comfort",
                evidence="stop comforting me",
                confidence=0.90,
            ),
            grip_loss_signal=GripLossSignal(
                active=True,
                target="starting_point_loss",
                evidence="I don't know where to start",
                confidence=0.85,
            ),
        )
        body = self.mapper.map_to_body_state(trace)
        # 纠正优先：posture 应为 stable，而非 slight_forward
        assert body.posture == "stable"
        assert body.expression_temperature == "restrained"  # 非 warm_restrained
        assert body.distance == "maintained"  # 非 slightly_closer
        # provenance 同时包含两者
        assert any("correction_signal" in p for p in body.provenance)
        assert body.behavior_affecting is False

    # ---- 测试 5：无可观测信号 → 地面姿态 ----
    def test_no_observable_maps_to_ground(self):
        trace = _make_trace_record(
            no_observable_field_signal=NoObservableFieldSignal(
                present=True,
            ),
        )
        body = self.mapper.map_to_body_state(trace)
        assert body.gaze == "neutral"
        assert body.posture == "neutral"
        assert body.distance == "baseline"
        assert body.expression_temperature == "restrained"
        assert body.timing == "immediate"
        assert body.speech_density_hint == "medium"
        assert "未观测" in body.body_note
        for forbidden in ("中性真相", "无意义", "正常状态", "没有相关状态"):
            assert forbidden not in body.body_note
        assert body.behavior_affecting is False

    # ---- 测试 6：污染信号 → distance/stillness ----
    def test_pollution_signal_maps_to_distance(self):
        trace = _make_trace_record(
            active_barriers=[
                BarrierCandidate(
                    name="romantic_service_barrier",
                    source="existing_interpreter",
                    confidence=0.80,
                    active=True,
                ),
            ],
        )
        body = self.mapper.map_to_body_state(trace)
        assert body.distance == "slightly_farther"
        assert body.motion_intensity == "still"
        assert body.expression_temperature == "cool"
        assert body.speech_density_hint == "minimal"
        assert body.gaze == "away_then_user"
        assert body.posture == "slight_withdraw"
        assert body.behavior_affecting is False

    # 测试 6b：通过 correction_signal.target == "ai_girlfriend_behavior" 检测污染
    def test_pollution_via_correction_target(self):
        trace = _make_trace_record(
            correction_signal=CorrectionSignal(
                active=True,
                target="ai_girlfriend_behavior",
                evidence="you're acting like an AI girlfriend",
                confidence=0.85,
            ),
        )
        body = self.mapper.map_to_body_state(trace)
        assert body.distance == "slightly_farther"
        assert body.expression_temperature == "cool"
        assert body.behavior_affecting is False

    # 测试 6c：通过 pollution_ 前缀的启发式屏障
    def test_pollution_via_heuristic_prefix(self):
        trace = _make_trace_record(
            active_barriers=[
                BarrierCandidate(
                    name="pollution_custom_type",
                    source="heuristic",
                    confidence=0.55,
                    active=True,
                ),
            ],
        )
        body = self.mapper.map_to_body_state(trace)
        assert body.distance == "slightly_farther"
        assert body.behavior_affecting is False

    # ---- 测试 7：技术信号 → 协作者姿态（非通用助手） ----
    def test_technical_maps_to_collaborator(self):
        trace = _make_trace_record(
            active_perturbations=[
                PerturbationCandidate(
                    name="technical_inquiry",
                    source="existing_interpreter",
                    confidence=0.82,
                    active=True,
                ),
            ],
        )
        body = self.mapper.map_to_body_state(trace)
        assert body.speech_density_hint == "structured"
        assert body.expression_temperature == "restrained"
        assert "assistant" not in body.body_note.lower()
        assert "helper" not in body.body_note.lower()
        assert body.distance != "slightly_closer"
        assert body.behavior_affecting is False

    # 测试 7b：通过 engineering_director_mode 吸引子
    def test_technical_via_attractor(self):
        trace = _make_trace_record(
            active_attractors=[
                AttractorCandidate(
                    name="engineering_director_mode",
                    source="existing_interpreter",
                    confidence=0.82,
                    active=True,
                ),
            ],
        )
        body = self.mapper.map_to_body_state(trace)
        assert body.speech_density_hint == "structured"
        assert body.gaze == "down_then_user"
        assert body.behavior_affecting is False

    # ---- 测试 8：映射器不检查原始用户输入 ----
    def test_mapper_does_not_inspect_raw_input(self):
        trace = _make_trace_record(
            user_input_summary="stop comforting me right now please",
            # 无任何活跃信号
            correction_signal=CorrectionSignal(active=False),
            grip_loss_signal=GripLossSignal(active=False),
            no_observable_field_signal=NoObservableFieldSignal(present=True),
        )
        body = self.mapper.map_to_body_state(trace)
        # 应为地面姿态——尽管 user_input_summary 包含 "stop comforting me"
        assert body.gaze == "neutral"
        assert body.posture == "neutral"
        assert body.distance == "baseline"
        assert body.behavior_affecting is False

    def test_mapper_source_has_no_raw_input_access(self):
        """映射器源码不得绕过 FieldTraceRecord 信号去读取原始输入。"""
        forbidden_tokens = [
            "raw_text",
            "user_text",
            "user_input_summary",
            "re.search",
            "re.match",
            "regex",
        ]
        for token in forbidden_tokens:
            assert token not in MAPPER_SOURCE, f"mapper.py 不得包含 raw-input 解析标记: {token}"

    def test_mapper_source_does_not_consume_decision_fields(self):
        """决策味字段现阶段只能被记录/导出，BodyState 不得把它们当决策输入。"""
        assert "forbidden_moves" not in MAPPER_SOURCE
        assert "circuit_breaker_candidates" not in MAPPER_SOURCE

    # ---- 测试 9：映射器不影响文本响应行为 ----
    def test_mapper_has_no_side_effects(self):
        trace = _make_trace_record(
            correction_signal=CorrectionSignal(
                active=True,
                target="comfort",
                evidence="stop comforting me",
                confidence=0.90,
            ),
            grip_loss_signal=GripLossSignal(
                active=True,
                target="starting_point_loss",
                evidence="where to start",
                confidence=0.85,
            ),
        )
        original_dict = copy.deepcopy(asdict(trace))
        _ = self.mapper.map_to_body_state(trace)
        after_dict = asdict(trace)
        # 传入的 FieldTraceRecord 不应被修改
        assert original_dict == after_dict, "map_to_body_state 不得修改传入的 FieldTraceRecord"

    # ---- 测试 10：behavior_affecting 始终为 False ----
    def test_behavior_affecting_always_false(self):
        scenarios = [
            # correction
            _make_trace_record(
                correction_signal=CorrectionSignal(active=True, target="comfort", confidence=0.90),
            ),
            # customer_service_tone
            _make_trace_record(
                correction_signal=CorrectionSignal(active=True, target="customer_service_tone", confidence=0.85),
            ),
            # over_abstraction
            _make_trace_record(
                correction_signal=CorrectionSignal(active=True, target="over_abstraction", confidence=0.80),
            ),
            # over_explanation
            _make_trace_record(
                correction_signal=CorrectionSignal(active=True, target="over_explanation", confidence=0.90),
            ),
            # grip_loss
            _make_trace_record(
                grip_loss_signal=GripLossSignal(active=True, target="starting_point_loss", confidence=0.85),
            ),
            # no_observable
            _make_trace_record(
                no_observable_field_signal=NoObservableFieldSignal(present=True),
            ),
            # pollution
            _make_trace_record(
                active_barriers=[BarrierCandidate(name="romantic_service_barrier", source="interpreter", confidence=0.80)],
            ),
            # technical
            _make_trace_record(
                active_perturbations=[PerturbationCandidate(name="technical_inquiry", source="interpreter", confidence=0.82)],
            ),
            # default (no signals)
            _make_trace_record(),
        ]
        for trace in scenarios:
            body = self.mapper.map_to_body_state(trace)
            assert body.behavior_affecting is False, (
                f"behavior_affecting 必须为 False，但 body_note='{body.body_note}' 时为 True"
            )

    # ---- 额外测试：所有路径均设置全部字段 ----
    def test_all_fields_set_for_every_path(self):
        """确保每个映射分支都设置了所有必需的 BodyState 字段。"""
        scenarios = [
            # correction comfort
            _make_trace_record(
                correction_signal=CorrectionSignal(active=True, target="comfort", confidence=0.90),
            ),
            # customer_service_tone
            _make_trace_record(
                correction_signal=CorrectionSignal(active=True, target="customer_service_tone", confidence=0.85),
            ),
            # over_abstraction
            _make_trace_record(
                correction_signal=CorrectionSignal(active=True, target="over_abstraction", confidence=0.80),
            ),
            # grip_loss
            _make_trace_record(
                grip_loss_signal=GripLossSignal(active=True, target="starting_point_loss", confidence=0.85),
            ),
            # no_observable
            _make_trace_record(
                no_observable_field_signal=NoObservableFieldSignal(present=True),
            ),
            # pollution
            _make_trace_record(
                active_barriers=[BarrierCandidate(name="seductive_intimacy_barrier", source="interpreter", confidence=0.80)],
            ),
            # technical
            _make_trace_record(
                active_perturbations=[PerturbationCandidate(name="technical_inquiry", source="interpreter", confidence=0.82)],
            ),
            # default
            _make_trace_record(),
        ]
        required_fields = [
            "gaze", "posture", "motion_intensity", "distance",
            "timing", "speech_density_hint", "expression_temperature",
        ]
        valid_gaze = {"neutral", "user", "down", "away", "down_then_user", "away_then_user"}
        valid_posture = {"neutral", "slight_forward", "stable", "slight_withdraw", "closed_stable"}
        valid_motion = {"still", "low", "medium"}
        valid_distance = {"baseline", "slightly_closer", "maintained", "slightly_farther"}
        valid_timing = {"immediate", "short_pause", "longer_pause"}
        valid_density = {"minimal", "low", "medium", "structured"}
        valid_temp = {"cool", "restrained", "warm_restrained"}

        for trace in scenarios:
            body = self.mapper.map_to_body_state(trace)
            for field in required_fields:
                val = getattr(body, field, None)
                assert val is not None, f"字段 '{field}' 在 body_note='{body.body_note}' 中为 None"
                assert isinstance(val, str), f"字段 '{field}' 必须是字符串，body_note='{body.body_note}'"
            assert body.gaze in valid_gaze, f"无效的 gaze 值: {body.gaze}"
            assert body.posture in valid_posture, f"无效的 posture 值: {body.posture}"
            assert body.motion_intensity in valid_motion, f"无效的 motion_intensity 值: {body.motion_intensity}"
            assert body.distance in valid_distance, f"无效的 distance 值: {body.distance}"
            assert body.timing in valid_timing, f"无效的 timing 值: {body.timing}"
            assert body.speech_density_hint in valid_density, f"无效的 speech_density_hint 值: {body.speech_density_hint}"
            assert body.expression_temperature in valid_temp, f"无效的 expression_temperature 值: {body.expression_temperature}"
            assert body.behavior_affecting is False

    # ---- 额外测试：默认 BodyState 为地面姿态 ----
    def test_default_body_state_is_ground(self):
        """当没有任何信号时，返回地面姿态。"""
        trace = _make_trace_record()
        body = self.mapper.map_to_body_state(trace)
        assert body.gaze == "neutral"
        assert body.posture == "neutral"
        assert body.motion_intensity == "low"
        assert body.distance == "baseline"
        assert body.timing == "immediate"
        assert body.speech_density_hint == "medium"
        assert body.expression_temperature == "restrained"
        assert body.behavior_affecting is False

    # ---- 额外测试：隐私过滤 — correction_signal.target=generic_correction ----
    def test_generic_correction_maps_to_stable(self):
        trace = _make_trace_record(
            correction_signal=CorrectionSignal(
                active=True,
                target="generic_correction",
                evidence="that's not right",
                confidence=0.75,
            ),
        )
        body = self.mapper.map_to_body_state(trace)
        assert body.posture == "stable"
        assert body.timing == "short_pause"
        assert body.behavior_affecting is False

    # ---- 额外测试：over_explanation 纠正 — gaze=user, speech_density_hint=minimal ----
    def test_over_explanation_maps_to_minimal_density(self):
        trace = _make_trace_record(
            correction_signal=CorrectionSignal(
                active=True,
                target="over_explanation",
                evidence="stop over-explaining",
                confidence=0.90,
            ),
        )
        body = self.mapper.map_to_body_state(trace)
        assert body.gaze == "user"
        assert body.speech_density_hint == "minimal"
        assert body.posture == "stable"
        assert body.expression_temperature == "restrained"
        assert body.behavior_affecting is False

    # ---- 额外测试：污染屏障多信号 ----
    def test_multiple_pollution_barriers(self):
        trace = _make_trace_record(
            active_barriers=[
                BarrierCandidate(name="romantic_service_barrier", source="interpreter", confidence=0.80),
                BarrierCandidate(name="fake_depth_barrier", source="interpreter", confidence=0.75),
            ],
        )
        body = self.mapper.map_to_body_state(trace)
        assert body.distance == "slightly_farther"
        assert body.motion_intensity == "still"
        assert len(body.provenance) >= 2  # 两个屏障
        assert body.behavior_affecting is False

    # ---- 额外测试：correction target = ai_girlfriend_behavior 触发污染优先级 ----
    def test_ai_girlfriend_correction_triggers_pollution(self):
        trace = _make_trace_record(
            correction_signal=CorrectionSignal(
                active=True,
                target="ai_girlfriend_behavior",
                evidence="you feel like an AI girlfriend",
                confidence=0.85,
            ),
            # 同时存在 grip_loss 信号——污染优先级更高
            grip_loss_signal=GripLossSignal(
                active=True,
                target="starting_point_loss",
                evidence="I don't know where to start",
                confidence=0.85,
            ),
        )
        body = self.mapper.map_to_body_state(trace)
        # 污染优先级最高，应为污染状态
        assert body.distance == "slightly_farther"
        assert body.expression_temperature == "cool"
        assert body.speech_density_hint == "minimal"
        # 不应是抓点损失状态（slight_forward）
        assert body.posture != "slight_forward"
        assert body.behavior_affecting is False

    # ---- 额外测试：grip_loss target=next_step_loss ----
    def test_grip_loss_next_step(self):
        trace = _make_trace_record(
            grip_loss_signal=GripLossSignal(
                active=True,
                target="next_step_loss",
                evidence="I don't know what the next step is",
                confidence=0.88,
            ),
        )
        body = self.mapper.map_to_body_state(trace)
        assert body.posture == "slight_forward"
        assert body.speech_density_hint == "structured"
        assert body.behavior_affecting is False
        assert "grip_loss_signal(next_step_loss)" in body.provenance

    # ---- 额外测试：仅 NoObservableFieldSignal 无 present=True 不应匹配 ----
    def test_no_obs_not_present_falls_through(self):
        """如果 NoObservableFieldSignal 存在但 present=False，应回退到默认地面姿态。"""
        trace = _make_trace_record(
            no_observable_field_signal=NoObservableFieldSignal(present=False),
        )
        body = self.mapper.map_to_body_state(trace)
        # 应回到默认地面姿态
        assert body.gaze == "neutral"
        assert body.posture == "neutral"
        assert body.behavior_affecting is False

    # ---- H1：BodyState mapper 回归测试 — mapper 消费原始信号而非 proposals ----

    def test_mapper_does_not_consume_proposals(self):
        """BodyState mapper 不使用 proposals 来选择身体姿态。

        如果 mapper 错误地开始读取 proposals 字段，
        此测试会因 body_note 不匹配而失败。
        """
        from src.field_trace.store import (
            FieldTraceRecord, FieldSignalProposal, EvidenceItem,
            ConfidenceBand, EvidenceType, EvidenceStrength
        )

        # 提议说：response_mode_rejected
        proposals = [FieldSignalProposal(
            signal_name="response_mode_rejected",
            evidence_items=[EvidenceItem(
                evidence_type=EvidenceType.EXPLICIT_USER_FEEDBACK.value,
                source="correction_observer",
                excerpt_or_reference="comforting me again",
                strength=EvidenceStrength.STRONG.value,
            )],
            confidence_band=ConfidenceBand.HIGH.value,
            suggested_field_effects=["降低响应密度"],
        )]

        # 但原始信号没有 correction_signal、grip_loss_signal、
        # active_barriers、active_perturbations
        record = FieldTraceRecord(
            turn_id="test-h1-001",
            timestamp="2026-01-01T00:00:00Z",
            user_input_summary="comforting me again",
            correction_signal=None,
            grip_loss_signal=None,
            no_observable_field_signal=None,
            active_perturbations=[],
            active_barriers=[],
            active_attractors=[],
            proposals=proposals,  # 提案存在
        )

        body_state = self.mapper.map_to_body_state(record)

        # 由于所有原始信号均为空且 no_observable_field_signal 为 None，
        # mapper 应回退到默认地面姿态，而非使用 proposals
        # 验证 body_note 不提及 "response_mode_rejected" 或 "proposal"
        assert "proposal" not in body_state.body_note.lower()
        assert "response_mode_rejected" not in body_state.body_note.lower()
        # 应回退到地面姿态（默认）
        assert body_state.gaze == "neutral"
        assert body_state.posture == "neutral"

    def test_mapper_does_not_import_proposal(self):
        """FieldToBodyMapper 不得导入 FieldSignalProposal。"""
        import inspect

        source = inspect.getsource(FieldToBodyMapper.map_to_body_state)
        # 确保 map_to_body_state 方法不直接访问 .proposals
        # （间接测试——如果 mapper 导入了 FieldSignalProposal 会在这里失败）
        assert "FieldSignalProposal" not in source
        assert ".proposals" not in source

    def test_mapper_ignores_proposals_when_signals_exist(self):
        """当原始信号存在时，mapper 使用它们，而非 proposals。"""
        from src.field_trace.store import (
            FieldTraceRecord, CorrectionSignal, FieldSignalProposal,
            EvidenceItem, ConfidenceBand, EvidenceType, EvidenceStrength
        )

        # 原始信号：comfort 纠正
        cs = CorrectionSignal(
            active=True, target="comfort",
            evidence="comforting me again",
            provenance="heuristic_observer",
            confidence=0.85,
            behavior_affecting=False,
        )

        # 提案说了别的内容
        proposals = [FieldSignalProposal(
            signal_name="actionable_grip_missing",  # 与原始信号不同！
            evidence_items=[EvidenceItem(
                evidence_type=EvidenceType.EXPLICIT_STARTING_POINT_LOSS.value,
                source="grip_loss_observer",
                strength=EvidenceStrength.MEDIUM.value,
            )],
            confidence_band=ConfidenceBand.MEDIUM.value,
        )]

        record = FieldTraceRecord(
            turn_id="test-h1-003",
            timestamp="2026-01-01T00:00:00Z",
            user_input_summary="comforting me again",
            correction_signal=cs,
            grip_loss_signal=None,
            no_observable_field_signal=None,
            active_perturbations=[],
            active_barriers=[],
            active_attractors=[],
            proposals=proposals,
        )

        body_state = self.mapper.map_to_body_state(record)

        # 应使用原始 correction_signal，而非 proposals 中的 grip_loss
        # 纠正姿态：stable, short_pause, low density
        assert body_state.posture == "stable"
        assert body_state.timing == "short_pause"
        assert body_state.speech_density_hint == "low"
        # 不应使用抓点损失姿态（slight_forward, structured）
        assert body_state.posture != "slight_forward"
        assert body_state.speech_density_hint != "structured"
