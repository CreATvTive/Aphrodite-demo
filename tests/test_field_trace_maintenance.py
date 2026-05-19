"""测试两项 FieldTrace 维护修复：
1. 已禁止动作去重
2. 无可观测场信号标记
"""
from __future__ import annotations

import pytest

from src.field_trace.store import (
    CorrectionSignal,
    FieldTraceExtractor,
    FieldTraceRecord,
    ForbiddenMove,
    NoObservableFieldSignal,
)


# ---------------------------------------------------------------------------
# 辅助函数：构建模拟的 interpreted dict
# ---------------------------------------------------------------------------

def _make_interpreted(*, sem_type: str = "", persona_non_entry: bool = False,
                      persona_route: str = "", dep_risk: float = 0.0,
                      vuln_rel: float = 0.0, tension_rel: float = 0.0,
                      tension_types: list | None = None,
                      pollution_risk: float = 0.0, pollution_types: list | None = None,
                      dfr: float = 0.0, context_needed: bool = False,
                      context_inherited: bool = False, requires_pause: bool = False,
                      requires_stillness: bool = False,
                      memory_type: str = "", warnings: list | None = None,
                      ) -> dict:
    """构建与 InputInterpreter 输出匹配的 interpreted dict。"""
    return {
        "semantic_event": {
            "type": sem_type,
            "persona_route": persona_route,
        },
        "relationship_signal": {
            "dependency_risk": dep_risk,
            "vulnerability_relevance": vuln_rel,
        },
        "boundary_signal": {
            "persona_non_entry": persona_non_entry,
            "internal_tension_relevance": tension_rel,
            "tension_type": tension_types or [],
            "external_pollution_risk": pollution_risk,
            "pollution_type": pollution_types or [],
            "direct_fulfillment_risk": dfr,
            "context_needed": context_needed,
            "context_inherited": context_inherited,
        },
        "memory_trigger_signal": {
            "memory_type": memory_type,
        },
        "performance_signal": {
            "requires_pause": requires_pause,
            "requires_stillness": requires_stillness,
        },
        "confidence": {},
        "warnings": warnings or [],
    }


# ---------------------------------------------------------------------------
# 测试：去重
# ---------------------------------------------------------------------------

class TestForbiddenMovesDedup:
    """验证 _extract_forbidden_moves() 不会产生重复条目。"""

    def setup_method(self):
        self.extractor = FieldTraceExtractor()

    def test_single_condition_no_duplicate(self):
        """仅触发 persona_non_entry 时不应有重复。"""
        interp = _make_interpreted(persona_non_entry=True)
        result = self.extractor._extract_forbidden_moves(
            sem=interp["semantic_event"],
            bnd=interp["boundary_signal"],
            rel=interp["relationship_signal"],
        )
        names = [fm.name for fm in result]
        assert "aphrodite_in_character_response" in names
        assert names.count("aphrodite_in_character_response") == 1, \
            f"不应有重复，实际: {names}"

    def test_technical_question_only_no_duplicate(self):
        """仅触发 technical_question 时不应有重复。"""
        interp = _make_interpreted(sem_type="technical_question")
        result = self.extractor._extract_forbidden_moves(
            sem=interp["semantic_event"],
            bnd=interp["boundary_signal"],
            rel=interp["relationship_signal"],
        )
        names = [fm.name for fm in result]
        assert "aphrodite_in_character_response" in names
        assert names.count("aphrodite_in_character_response") == 1, \
            f"不应有重复，实际: {names}"

    def test_overlap_dedup(self):
        """同一个 name 出现在 persona_non_entry 和 technical_question 时应去重。"""
        interp = _make_interpreted(
            sem_type="technical_question",
            persona_non_entry=True,
            persona_route="engineering_director",
        )
        result = self.extractor._extract_forbidden_moves(
            sem=interp["semantic_event"],
            bnd=interp["boundary_signal"],
            rel=interp["relationship_signal"],
        )
        names = [fm.name for fm in result]
        # 每个 name 只出现一次
        assert len(names) == len(set(names)), \
            f"禁止动作名称重复: {names}"
        assert "aphrodite_in_character_response" in names
        assert names.count("aphrodite_in_character_response") == 1, \
            f"'aphrodite_in_character_response' 不应出现多次: {names}"

    def test_with_dependency_risk_additional(self):
        """依赖风险应作为独立条目添加，不影响去重。"""
        interp = _make_interpreted(
            sem_type="technical_question",
            persona_non_entry=True,
            persona_route="engineering_director",
            dep_risk=0.6,
        )
        result = self.extractor._extract_forbidden_moves(
            sem=interp["semantic_event"],
            bnd=interp["boundary_signal"],
            rel=interp["relationship_signal"],
        )
        names = [fm.name for fm in result]
        assert "aphrodite_in_character_response" in names
        assert "dependency_reinforcement" in names
        assert names.count("aphrodite_in_character_response") == 1
        assert names.count("dependency_reinforcement") == 1
        assert len(names) == len(set(names)), \
            f"禁止动作名称重复: {names}"


# ---------------------------------------------------------------------------
# 测试：无可观测场信号标记
# ---------------------------------------------------------------------------

class TestNoObservableFieldSignal:
    """验证当所有子提取器均不产生输出时，no_observable_field_signal 被设置。"""

    def setup_method(self):
        self.extractor = FieldTraceExtractor()

    def _extract(self, interp: dict, user_text: str = "") -> FieldTraceRecord:
        return self.extractor.extract(
            interpreted=interp,
            runtime_state={},
            router_output={},
            turn_index=0,
            user_text=user_text,
        )

    # --- 中性输入产生标记 ---

    def test_neutral_input_produces_marker_hello(self):
        """'你好' 不触发任何提取器 → no_observable_field_signal 应存在。"""
        interp = _make_interpreted()
        record = self._extract(interp, user_text="你好")
        assert record.no_observable_field_signal is not None, \
            "中性输入应设置 no_observable_field_signal"
        assert record.no_observable_field_signal.present is True
        assert record.no_observable_field_signal.provenance == "trace_absence_marker"
        assert record.no_observable_field_signal.confidence == 0.1
        assert record.no_observable_field_signal.behavior_affecting is False

    def test_neutral_input_produces_marker_how_are_you(self):
        """'最近怎么样？' 不触发任何提取器 → no_observable_field_signal 应存在。"""
        interp = _make_interpreted()
        record = self._extract(interp, user_text="最近怎么样？")
        assert record.no_observable_field_signal is not None
        assert record.no_observable_field_signal.present is True

    def test_uncertainty_note_added_for_marker(self):
        """当标记存在时，uncertainty_notes 应包含说明。"""
        interp = _make_interpreted()
        record = self._extract(interp, user_text="你好")
        marker_note_found = any(
            "no_observable_field_signal" in note
            for note in record.uncertainty_notes
        )
        assert marker_note_found, \
            f"uncertainty_notes 应包含 no_observable_field_signal 说明，实际: {record.uncertainty_notes}"

    def test_no_observable_note_is_not_neutral_truth(self):
        """no_observable 只表示未观测到场信号，不表示中性真相或输入无意义。"""
        interp = _make_interpreted()
        record = self._extract(interp, user_text="你好")
        notes = "\n".join(record.uncertainty_notes)
        assert "未观测到场信号" in notes
        assert "不表示输入无意义" in notes
        assert "中性真相" not in notes
        assert "正常状态" not in notes
        assert "用户没有相关状态" not in notes

    def test_empty_perturbations_barriers_attractors_breakers(self):
        """中性输入下所有候选列表应为空。"""
        interp = _make_interpreted()
        record = self._extract(interp, user_text="你好")
        assert record.active_perturbations == []
        assert record.active_barriers == []
        assert record.active_attractors == []
        assert record.circuit_breaker_candidates == []

    # --- 修正信号抑制标记 ---

    def test_correction_suppresses_marker(self):
        """'You're just comforting me again.' 触发 correction_signal → 不设置标记。"""
        interp = _make_interpreted()
        record = self._extract(interp, user_text="You're just comforting me again.")
        assert record.correction_signal is not None
        assert record.correction_signal.active is True
        assert record.no_observable_field_signal is None, \
            "当 correction_signal 活跃时不应设置 no_observable_field_signal"

    # --- 技术输入抑制标记 ---

    def test_technical_input_suppresses_marker(self):
        """'Write the prompt for Codex.' 触发 technical_question 扰动 → 不设置标记。"""
        interp = _make_interpreted(sem_type="technical_question")
        record = self._extract(interp, user_text="Write the prompt for Codex.")
        assert len(record.active_perturbations) > 0, \
            "技术问题应产生扰动"
        assert record.no_observable_field_signal is None, \
            "当 active_perturbations 非空时不应设置 no_observable_field_signal"

    def test_persona_non_entry_suppresses_marker(self):
        """persona_non_entry 触发扰动和屏障 → 不设置标记。"""
        interp = _make_interpreted(
            persona_non_entry=True,
            persona_route="engineering_director",
        )
        record = self._extract(interp, user_text="Can Aphrodite answer technical questions?")
        assert record.no_observable_field_signal is None

    # --- behavior_affecting 始终为 False ---

    def test_correction_behavior_affecting_always_false(self):
        """无论输入如何，correction_signal.behavior_affecting 始终为 False。"""
        interp = _make_interpreted()
        record = self._extract(interp, user_text="You're just comforting me again.")
        assert record.correction_signal is not None
        assert record.correction_signal.behavior_affecting is False

    def test_no_signal_marker_behavior_affecting_always_false(self):
        """no_observable_field_signal.behavior_affecting 始终为 False。"""
        interp = _make_interpreted()
        record = self._extract(interp, user_text="你好")
        assert record.no_observable_field_signal is not None
        assert record.no_observable_field_signal.behavior_affecting is False

    # --- to_dict 序列化 ---

    def test_marker_in_to_dict(self):
        """验证 to_dict() 包含 no_observable_field_signal。"""
        interp = _make_interpreted()
        record = self._extract(interp, user_text="你好")
        d = record.to_dict()
        assert "no_observable_field_signal" in d
        assert d["no_observable_field_signal"] is not None
        assert d["no_observable_field_signal"]["present"] is True
        assert d["no_observable_field_signal"]["provenance"] == "trace_absence_marker"

    def test_null_marker_in_to_dict(self):
        """当标记不存在时，to_dict() 返回 None。"""
        interp = _make_interpreted(sem_type="technical_question")
        record = self._extract(interp, user_text="Write the prompt for Codex.")
        d = record.to_dict()
        assert d["no_observable_field_signal"] is None

    # --- 边界情况 ---

    def test_vulnerability_input_suppresses_marker(self):
        """脆弱性输入触发吸引子 → 不设置标记。"""
        interp = _make_interpreted(vuln_rel=0.7)
        record = self._extract(interp, user_text="I feel vulnerable about this.")
        assert record.no_observable_field_signal is None

    def test_empty_string_input_behaves_like_neutral(self):
        """空字符串输入等同于中性 — 标记应出现（前提是 interpreted 也为空）。"""
        interp = _make_interpreted()
        record = self._extract(interp, user_text="")
        assert record.no_observable_field_signal is not None
        assert record.no_observable_field_signal.present is True


# ---------------------------------------------------------------------------
# 集成测试：extract() 去重 + 标记共存
# ---------------------------------------------------------------------------

class TestExtractIntegration:
    """验证 extract() 中所有修改的集成正确性。"""

    def setup_method(self):
        self.extractor = FieldTraceExtractor()

    def _extract(self, interp: dict, user_text: str = "") -> FieldTraceRecord:
        return self.extractor.extract(
            interpreted=interp,
            runtime_state={},
            router_output={},
            turn_index=0,
            user_text=user_text,
        )

    def test_overlap_case_no_duplicate_in_record(self):
        """当两个条件都满足时，记录中不应有重复的 forbidden_moves。"""
        interp = _make_interpreted(
            sem_type="technical_question",
            persona_non_entry=True,
            persona_route="engineering_director",
        )
        record = self._extract(interp, user_text="请帮我分析这个 Python bug")
        names = [fm.name for fm in record.forbidden_moves]
        assert len(names) == len(set(names)), \
            f"record.forbidden_moves 不应有重复 name: {names}"
        # 此时有扰动和屏障 → 标记应为 None
        assert record.no_observable_field_signal is None

    def test_full_roundtrip_to_dict_for_overlap(self):
        """完整往返：重叠输入 to_dict → JSON 可序列化且无重复。"""
        import json
        interp = _make_interpreted(
            sem_type="technical_question",
            persona_non_entry=True,
            persona_route="engineering_director",
            dep_risk=0.6,
        )
        record = self._extract(interp, user_text="请帮我分析这个 Python bug")
        d = record.to_dict()
        # 必须可 JSON 序列化
        json_str = json.dumps(d, ensure_ascii=False)
        parsed = json.loads(json_str)
        fm_names = [fm["name"] for fm in parsed["forbidden_moves"]]
        assert len(fm_names) == len(set(fm_names)), \
            f"序列化后的 forbidden_moves 不应有重复: {fm_names}"

    def test_neutral_to_dict_includes_marker_with_signal_sources(self):
        """中性输入的 to_dict 包含 marker 和 signal_sources。"""
        import json
        interp = _make_interpreted()
        record = self._extract(interp, user_text="你好")
        d = record.to_dict()
        json_str = json.dumps(d, ensure_ascii=False)
        parsed = json.loads(json_str)
        assert parsed["no_observable_field_signal"] is not None
        assert "no_observable_field_signal" in parsed["signal_sources"]


# ---------------------------------------------------------------------------
# 修复 2：_has_any_active_signal() 单元测试
# ---------------------------------------------------------------------------

class TestHasAnyActiveSignal:
    """验证 _has_any_active_signal() 辅助方法。"""

    def setup_method(self):
        self.extractor = FieldTraceExtractor()

    def test_has_any_active_signal_empty(self):
        """全部为空输入 → False。"""
        from src.field_trace.store import CorrectionSignal
        result = self.extractor._has_any_active_signal(
            perturbations=[],
            barriers=[],
            attractors=[],
            correction_signal=CorrectionSignal(),
            circuit_breakers=[],
        )
        assert result is False

    def test_has_any_active_signal_with_perturbation(self):
        """扰动存在 → True。"""
        from src.field_trace.store import CorrectionSignal, PerturbationCandidate
        result = self.extractor._has_any_active_signal(
            perturbations=[PerturbationCandidate(name="test", source="test")],
            barriers=[],
            attractors=[],
            correction_signal=CorrectionSignal(),
            circuit_breakers=[],
        )
        assert result is True

    def test_has_any_active_signal_with_correction(self):
        """修正活跃 → True。"""
        from src.field_trace.store import CorrectionSignal
        result = self.extractor._has_any_active_signal(
            perturbations=[],
            barriers=[],
            attractors=[],
            correction_signal=CorrectionSignal(active=True, target="comfort", confidence=0.85),
            circuit_breakers=[],
        )
        assert result is True

    def test_has_any_active_signal_with_circuit_breaker(self):
        """断路器存在 → True。"""
        from src.field_trace.store import CircuitBreakerCandidate, CorrectionSignal
        result = self.extractor._has_any_active_signal(
            perturbations=[],
            barriers=[],
            attractors=[],
            correction_signal=CorrectionSignal(),
            circuit_breakers=[CircuitBreakerCandidate(name="test", triggered=False)],
        )
        assert result is True

    def test_has_any_active_signal_with_barrier(self):
        """屏障存在 → True。"""
        from src.field_trace.store import BarrierCandidate, CorrectionSignal
        result = self.extractor._has_any_active_signal(
            perturbations=[],
            barriers=[BarrierCandidate(name="test", source="test")],
            attractors=[],
            correction_signal=CorrectionSignal(),
            circuit_breakers=[],
        )
        assert result is True

    def test_has_any_active_signal_with_attractor(self):
        """吸引子存在 → True。"""
        from src.field_trace.store import AttractorCandidate, CorrectionSignal
        result = self.extractor._has_any_active_signal(
            perturbations=[],
            barriers=[],
            attractors=[AttractorCandidate(name="test", source="test")],
            correction_signal=CorrectionSignal(),
            circuit_breakers=[],
        )
        assert result is True

    def test_has_any_active_signal_with_inactive_correction(self):
        """修正不活跃，其他全空 → False。"""
        from src.field_trace.store import CorrectionSignal
        result = self.extractor._has_any_active_signal(
            perturbations=[],
            barriers=[],
            attractors=[],
            correction_signal=CorrectionSignal(active=False),
            circuit_breakers=[],
        )
        assert result is False


# ---------------------------------------------------------------------------
# 修复 1/2：集成测试 — 舒适拒绝/请求与 no_observable_field_signal 的交互
# ---------------------------------------------------------------------------

class TestComfortNoSignalIntegration:
    """验证舒适模式拆分与 no_observable_field_signal 标记的集成正确性。"""

    def setup_method(self):
        self.extractor = FieldTraceExtractor()

    def _extract(self, interp: dict, user_text: str = "") -> FieldTraceRecord:
        return self.extractor.extract(
            interpreted=interp,
            runtime_state={},
            router_output={},
            turn_index=0,
            user_text=user_text,
        )

    def test_integration_comfort_rejection_suppresses_no_signal(self):
        """'You're just comforting me again.' 触发修正 → no_observable_field_signal=None。"""
        interp = _make_interpreted()
        record = self._extract(interp, user_text="You're just comforting me again.")
        assert record.correction_signal is not None
        assert record.correction_signal.active is True
        assert record.correction_signal.target == "comfort"
        assert record.no_observable_field_signal is None, \
            "修正信号活跃时应抑制 no_observable_field_signal"

    def test_integration_comfort_request_still_produces_no_signal(self):
        """'I need comforting now' 不触发修正 → no_observable_field_signal 应被设置。"""
        interp = _make_interpreted()
        record = self._extract(interp, user_text="I need comforting now")
        assert record.correction_signal is not None
        assert record.correction_signal.active is False
        assert record.no_observable_field_signal is not None, \
            "无修正信号时应设置 no_observable_field_signal"
        assert record.no_observable_field_signal.present is True
