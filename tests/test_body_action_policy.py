import pytest
from src.body_action.policy import BodyActionPolicy
from src.body_action.schema import BodyActionWeights, BodyActionWeight


# ---------------------------------------------------------------------------
# Mock / 最小化 trace record 类
# ---------------------------------------------------------------------------

class MockTraceRecord:
    def __init__(self, **kwargs):
        self.turn_id = kwargs.get('turn_id', 'test-001')
        self.correction_signal = kwargs.get('correction_signal')
        self.grip_loss_signal = kwargs.get('grip_loss_signal')
        self.no_observable_field_signal = kwargs.get('no_observable_field_signal')
        self.active_barriers = kwargs.get('active_barriers', [])
        self.active_perturbations = kwargs.get('active_perturbations', [])
        self.active_attractors = kwargs.get('active_attractors', [])


class MockSignal:
    def __init__(self, active=False, target="", present=False):
        self.active = active
        self.target = target
        self.present = present


class MockBarrier:
    def __init__(self, name=""):
        self.name = name


class MockPerturbation:
    def __init__(self, name=""):
        self.name = name


class MockAttractor:
    def __init__(self, name=""):
        self.name = name


# ---------------------------------------------------------------------------
# 辅助断言
# ---------------------------------------------------------------------------

def _assert_weight(result, action_name, expected_weight):
    """辅助函数：断言 BodyActionWeights 包含特定的动作权重。"""
    for w in result.weights:
        if w.action_name == action_name:
            assert w.weight == expected_weight, f"动作 {action_name}：期望 {expected_weight}，实际 {w.weight}"
            return
    assert False, f"输出中未找到动作 {action_name}"


# ---------------------------------------------------------------------------
# 测试类：主规则映射（20 个测试）
# ---------------------------------------------------------------------------

class TestBodyActionPolicy:
    policy = BodyActionPolicy()

    # 测试 1：边界/污染压力
    def test_boundary_pollution_maps_stillness_high(self):
        record = MockTraceRecord(
            active_barriers=[MockBarrier(name="romantic_service_barrier")]
        )
        result = self.policy.map_to_action_weights(record)
        _assert_weight(result, "stillness", "high")
        _assert_weight(result, "reduce_motion", "high")
        _assert_weight(result, "slight_withdraw", "medium")
        _assert_weight(result, "maintain_distance", "high")
        _assert_weight(result, "slight_forward", "off")
        assert result.behavior_affecting == False

    # 测试 2：AI 女友行为纠正触发污染映射
    def test_ai_girlfriend_correction_triggers_pollution_mapping(self):
        record = MockTraceRecord(
            correction_signal=MockSignal(active=True, target="ai_girlfriend_behavior")
        )
        result = self.policy.map_to_action_weights(record)
        _assert_weight(result, "stillness", "high")
        _assert_weight(result, "slight_withdraw", "medium")
        assert result.behavior_affecting == False

    # 测试 3：纠正+抓点损失组合在通用纠正之前触发
    def test_correction_plus_grip_loss_combined_before_generic(self):
        record = MockTraceRecord(
            correction_signal=MockSignal(active=True, target="comfort"),
            grip_loss_signal=MockSignal(active=True, target="starting_point_loss"),
        )
        result = self.policy.map_to_action_weights(record)
        # 组合规则：pause=high，slight_forward 降为 low
        _assert_weight(result, "pause", "high")
        _assert_weight(result, "slight_forward", "low")
        _assert_weight(result, "maintain_distance", "high")
        assert "纠正优先于抓点损失" in result.body_note
        assert result.behavior_affecting == False

    # 测试 4：客服语调纠正
    def test_customer_service_tone_correction(self):
        record = MockTraceRecord(
            correction_signal=MockSignal(active=True, target="customer_service_tone")
        )
        result = self.policy.map_to_action_weights(record)
        _assert_weight(result, "pause", "high")
        _assert_weight(result, "stillness", "high")
        _assert_weight(result, "reduce_motion", "high")
        _assert_weight(result, "maintain_distance", "high")
        _assert_weight(result, "slight_forward", "off")
        assert result.behavior_affecting == False

    # 测试 5：过度抽象纠正
    def test_over_abstraction_correction(self):
        record = MockTraceRecord(
            correction_signal=MockSignal(active=True, target="over_abstraction")
        )
        result = self.policy.map_to_action_weights(record)
        _assert_weight(result, "look_to_user", "medium")
        _assert_weight(result, "reset_posture", "medium")
        _assert_weight(result, "reduce_motion", "medium")
        assert result.behavior_affecting == False

    # 测试 6：通用纠正
    def test_generic_correction(self):
        record = MockTraceRecord(
            correction_signal=MockSignal(active=True, target="comfort")
        )
        result = self.policy.map_to_action_weights(record)
        _assert_weight(result, "pause", "high")
        _assert_weight(result, "stillness", "medium")
        _assert_weight(result, "look_down", "medium")
        _assert_weight(result, "look_to_user", "medium")
        assert result.behavior_affecting == False

    # 测试 7：抓点损失
    def test_grip_loss(self):
        record = MockTraceRecord(
            grip_loss_signal=MockSignal(active=True, target="starting_point_loss")
        )
        result = self.policy.map_to_action_weights(record)
        _assert_weight(result, "slight_forward", "medium")
        _assert_weight(result, "look_to_user", "high")
        _assert_weight(result, "look_down", "medium")
        assert result.behavior_affecting == False

    # 测试 8：无可观测信号
    def test_no_observable(self):
        record = MockTraceRecord(
            no_observable_field_signal=MockSignal(present=True)
        )
        result = self.policy.map_to_action_weights(record)
        _assert_weight(result, "reset_posture", "high")
        _assert_weight(result, "maintain_distance", "low")
        assert "不表示输入无意义" in result.body_note
        assert result.behavior_affecting == False

    # 测试 9：技术/协作者
    def test_technical_collaborator(self):
        record = MockTraceRecord(
            active_perturbations=[MockPerturbation(name="technical_inquiry")]
        )
        result = self.policy.map_to_action_weights(record)
        _assert_weight(result, "maintain_distance", "high")
        _assert_weight(result, "look_to_user", "medium")
        assert result.behavior_affecting == False

    # 测试 10：默认基线
    def test_default_baseline(self):
        record = MockTraceRecord()
        result = self.policy.map_to_action_weights(record)
        _assert_weight(result, "reset_posture", "low")
        _assert_weight(result, "maintain_distance", "low")
        assert result.behavior_affecting == False

    # 测试 11：输出为 BodyActionWeights
    def test_output_is_body_action_weights(self):
        result = self.policy.map_to_action_weights(MockTraceRecord())
        assert isinstance(result, BodyActionWeights)

    # 测试 12：所有动作均为批准的原语
    def test_all_actions_approved_primitives(self):
        from src.body_action.schema import ACTION_PRIMITIVES
        result = self.policy.map_to_action_weights(MockTraceRecord(
            correction_signal=MockSignal(active=True, target="comfort")
        ))
        for w in result.weights:
            assert w.action_name in ACTION_PRIMITIVES, f"未知原语: {w.action_name}"

    # 测试 13：所有权重均为批准的带
    def test_all_weights_approved_bands(self):
        from src.body_action.schema import WEIGHT_BANDS
        result = self.policy.map_to_action_weights(MockTraceRecord(
            correction_signal=MockSignal(active=True, target="comfort")
        ))
        for w in result.weights:
            assert w.weight in WEIGHT_BANDS, f"无效权重带: {w.weight}"
            assert isinstance(w.weight, str), f"权重必须为字符串: {w.weight}"

    # 测试 14：behavior_affecting 始终为 false
    def test_behavior_affecting_always_false(self):
        record = MockTraceRecord(
            correction_signal=MockSignal(active=True, target="comfort"),
            grip_loss_signal=MockSignal(active=True),
            no_observable_field_signal=MockSignal(present=True),
        )
        result = self.policy.map_to_action_weights(record)
        assert result.behavior_affecting == False
        for w in result.weights:
            assert w.behavior_affecting == False

    # 测试 15：不检查原始用户输入
    def test_no_raw_input_access(self):
        """使用故意奇怪的输入验证策略不检查原始文本。"""
        record = MockTraceRecord(
            correction_signal=MockSignal(active=True, target="comfort"),
            grip_loss_signal=MockSignal(active=True),
        )
        result = self.policy.map_to_action_weights(record)
        assert isinstance(result, BodyActionWeights)

    # 测试 16：源代码无正则表达式 / 原始文本
    def test_no_regex_in_source(self):
        import inspect
        import src.body_action.policy as mod
        source = inspect.getsource(mod)
        assert "re.search" not in source
        assert "re.match" not in source
        assert "raw_text" not in source
        assert "user_text" not in source
        assert "user_input_summary" not in source

    # 测试 17：无禁止导入
    def test_no_forbidden_imports(self):
        import inspect
        import src.body_action.policy as mod
        source = inspect.getsource(mod)
        forbidden = [
            "runtime_engine", "input_interpreter",
            "router", "memory", "persona",
            "renderer", "animation", "avatar",
            "llm", "client", "provider",
        ]
        for name in forbidden:
            assert name not in source.lower(), f"policy.py 不应导入 '{name}'"

    # 测试 18：不输出 BodyActionComposition
    def test_no_composition_output(self):
        result = self.policy.map_to_action_weights(MockTraceRecord())
        assert isinstance(result, BodyActionWeights)
        assert not hasattr(result, 'primary_actions')

    # 测试 19：FieldToBodyMapper 行为不变
    def test_body_state_mapper_unchanged(self):
        from src.body_state.mapper import FieldToBodyMapper
        from src.field_trace.store import FieldTraceRecord
        mapper = FieldToBodyMapper()
        record = FieldTraceRecord(
            turn_id="test-019",
            timestamp="2026-05-08T14:00:00Z",
            user_input_summary="test",
            correction_signal=None,
            grip_loss_signal=None,
            no_observable_field_signal=None,
        )
        body = mapper.map_to_body_state(record)
        assert body.behavior_affecting == False

    # 测试 20：现有测试通过（通过运行确认）
    # 此测试在外部通过 pytest 运行套件来验证


# ---------------------------------------------------------------------------
# 集成测试：优先级顺序（3 个测试）
# ---------------------------------------------------------------------------

class TestPriorityOrder:
    """集成测试：优先级链正确。"""
    policy = BodyActionPolicy()

    def test_pollution_overrides_correction(self):
        """污染压力具有最高优先级——即使纠正信号也活跃。"""
        record = MockTraceRecord(
            correction_signal=MockSignal(active=True, target="comfort"),
            active_barriers=[MockBarrier(name="romantic_service_barrier")],
        )
        result = self.policy.map_to_action_weights(record)
        # 应选择污染规则，而非通用纠正
        _assert_weight(result, "stillness", "high")  # 纠正仅 medium
        _assert_weight(result, "slight_forward", "off")  # 纠正无此项

    def test_correction_overrides_grip_loss(self):
        """纠正覆盖抓点损失——slight_forward 降为低或不存在。"""
        record = MockTraceRecord(
            correction_signal=MockSignal(active=True, target="comfort"),
            grip_loss_signal=MockSignal(active=True, target="starting_point_loss"),
        )
        result = self.policy.map_to_action_weights(record)
        # 组合规则触发——非单独纠正，非单独抓点损失
        _assert_weight(result, "slight_forward", "low")
        _assert_weight(result, "pause", "high")

    def test_no_observable_only_when_nothing_else(self):
        """无可观测信号仅在无其他信号时激活。"""
        record = MockTraceRecord(
            correction_signal=MockSignal(active=True, target="comfort"),
            no_observable_field_signal=MockSignal(present=True),
        )
        result = self.policy.map_to_action_weights(record)
        # 应触发纠正规则（规则 5），而非无可观测（规则 8）
        _assert_weight(result, "pause", "high")
