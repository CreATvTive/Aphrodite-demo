import json
import inspect

import pytest

from src.body_action.schema import (
    ACTION_PRIMITIVES,
    COMPLETION_MODES,
    DURATION_HINTS,
    WEIGHT_BANDS,
    ActionSequenceHint,
    BodyActionComposition,
    BodyActionWeight,
    BodyActionWeights,
)
from src.motion_params.schema import BodyPartOffsets


class TestActionPrimitives:
    """§1：所有批准的原语被接受；未知原语被拒绝。"""

    def test_all_approved_primitives_accepted(self):
        for name in sorted(ACTION_PRIMITIVES):
            w = BodyActionWeight(action_name=name, weight="medium")
            assert w.action_name == name

    def test_unknown_primitive_rejected(self):
        with pytest.raises(ValueError, match="unknown_action_xyz"):
            BodyActionWeight(action_name="unknown_action_xyz", weight="medium")

    def test_sequence_hint_unknown_primitive_rejected(self):
        with pytest.raises(ValueError, match="bogus_action"):
            ActionSequenceHint(action_name="bogus_action")


class TestWeightBands:
    """§2：有效权重带被接受；无效被拒绝；浮点数被拒绝。"""

    def test_all_weight_bands_accepted(self):
        for band in sorted(WEIGHT_BANDS):
            w = BodyActionWeight(action_name="pause", weight=band)
            assert w.weight == band

    def test_invalid_weight_rejected(self):
        with pytest.raises(ValueError, match="extreme"):
            BodyActionWeight(action_name="pause", weight="extreme")

    def test_float_weight_rejected(self):
        """浮点数权重不得被接受。"""
        with pytest.raises(ValueError, match="字符串"):
            BodyActionWeight(action_name="pause", weight=0.8)

    def test_int_weight_rejected(self):
        """整数权重也不得被接受——必须为字符串。"""
        with pytest.raises(ValueError, match="字符串"):
            BodyActionWeight(action_name="pause", weight=1)

    def test_default_weight_is_off(self):
        w = BodyActionWeight(action_name="pause")
        assert w.weight == "off"


class TestBehaviorAffectingDefaults:
    """§3：behavior_affecting 在所有数据类中默认为 False。"""

    def test_weight_default(self):
        w = BodyActionWeight(action_name="pause")
        assert w.behavior_affecting is False

    def test_weights_default(self):
        ws = BodyActionWeights()
        assert ws.behavior_affecting is False

    def test_sequence_hint_default(self):
        sh = ActionSequenceHint(action_name="pause")
        assert sh.behavior_affecting is False

    def test_composition_default(self):
        c = BodyActionComposition()
        assert c.behavior_affecting is False

    def test_weight_true_rejected(self):
        with pytest.raises(ValueError, match="behavior_affecting"):
            BodyActionWeight(action_name="pause", behavior_affecting=True)

    def test_weights_true_rejected(self):
        with pytest.raises(ValueError, match="behavior_affecting"):
            BodyActionWeights(behavior_affecting=True)


class TestSerialization:
    """§4：to_dict() 输出必须是 JSON 可序列化的。"""

    def test_weight_to_dict_json(self):
        w = BodyActionWeight(action_name="pause", weight="medium", rationale="测试")
        d = w.to_dict()
        s = json.dumps(d, ensure_ascii=False)
        assert "pause" in s
        assert "medium" in s

    def test_weights_to_dict_json(self):
        ws = BodyActionWeights(
            weights=[BodyActionWeight(action_name="pause", weight="high")],
            source_trace_id="test-001",
            body_note="测试集合",
        )
        s = json.dumps(ws.to_dict(), ensure_ascii=False)
        assert "pause" in s
        assert "test-001" in s

    def test_sequence_hint_to_dict_json(self):
        sh = ActionSequenceHint(
            action_name="stillness", order=1, duration_hint="short", completion="restrained"
        )
        s = json.dumps(sh.to_dict(), ensure_ascii=False)
        assert "stillness" in s
        assert "short" in s

    def test_composition_to_dict_json(self):
        c = BodyActionComposition(
            primary_actions=[ActionSequenceHint(action_name="pause", order=0)],
            composition_note="测试组合",
        )
        s = json.dumps(c.to_dict(), ensure_ascii=False)
        assert "pause" in s
        assert "测试组合" in s

    def test_weights_body_part_offsets_to_dict_json(self):
        offsets = BodyPartOffsets(
            gaze_offset_ms=0,
            head_offset_ms=40,
            shoulder_offset_ms=90,
            hand_offset_ms=140,
        )
        ws = BodyActionWeights(
            weights=[BodyActionWeight(action_name="look_away", weight="medium")],
            body_part_offsets=offsets,
            body_note="offset serialization",
        )

        payload = ws.to_dict()
        encoded = json.dumps(payload, ensure_ascii=False)

        assert payload["body_part_offsets"] == {
            "gaze_offset_ms": 0,
            "head_offset_ms": 40,
            "shoulder_offset_ms": 90,
            "hand_offset_ms": 140,
        }
        assert payload["behavior_affecting"] is False
        assert "body_part_offsets" in encoded

    def test_composition_nested_to_dict_uses_json_safe_dicts(self):
        c = BodyActionComposition(
            primary_actions=[
                ActionSequenceHint(
                    action_name="pause",
                    order=0,
                    duration_hint="sustained",
                    completion="restrained",
                )
            ],
            source_weights=[
                BodyActionWeight(
                    action_name="pause",
                    weight="high",
                    rationale="nested source weight",
                )
            ],
            composition_note="nested serialization",
        )

        payload = c.to_dict()
        json.dumps(payload, ensure_ascii=False)

        assert isinstance(payload["primary_actions"][0], dict)
        assert isinstance(payload["source_weights"][0], dict)
        assert payload["primary_actions"][0]["behavior_affecting"] is False
        assert payload["source_weights"][0]["behavior_affecting"] is False
        assert payload["behavior_affecting"] is False


class TestSequenceHintValidation:
    """§5：duration_hint 和 completion 验证。"""

    def test_all_duration_hints_accepted(self):
        for dh in sorted(DURATION_HINTS):
            sh = ActionSequenceHint(action_name="pause", duration_hint=dh)
            assert sh.duration_hint == dh

    def test_invalid_duration_hint_rejected(self):
        with pytest.raises(ValueError, match="unknown_dur"):
            ActionSequenceHint(action_name="pause", duration_hint="unknown_dur")

    def test_all_completions_accepted(self):
        for cm in sorted(COMPLETION_MODES):
            sh = ActionSequenceHint(action_name="pause", completion=cm)
            assert sh.completion == cm

    def test_invalid_completion_rejected(self):
        with pytest.raises(ValueError, match="unknown_comp"):
            ActionSequenceHint(action_name="pause", completion="unknown_comp")

    def test_negative_order_rejected(self):
        with pytest.raises(ValueError, match="order"):
            ActionSequenceHint(action_name="pause", order=-1)


class TestComposition:
    """§6：BodyActionComposition 结构验证。"""

    def test_primary_secondary_suppressed(self):
        c = BodyActionComposition(
            primary_actions=[ActionSequenceHint(action_name="pause", order=0)],
            secondary_actions=[ActionSequenceHint(action_name="look_down", order=1)],
            suppressed_actions=["look_away"],
            source_weights=[BodyActionWeight(action_name="stillness", weight="high")],
        )
        assert len(c.primary_actions) == 1
        assert len(c.secondary_actions) == 1
        assert c.suppressed_actions == ["look_away"]
        assert len(c.source_weights) == 1

    def test_invalid_suppressed_action_rejected(self):
        with pytest.raises(ValueError, match="bogus_suppress"):
            BodyActionComposition(suppressed_actions=["bogus_suppress"])

    def test_composition_does_not_imply_animation(self):
        """BodyActionComposition 是数据容器，不执行动画。"""
        c = BodyActionComposition()
        assert hasattr(c, "to_dict")
        # 不能有任何渲染/动画方法
        assert not hasattr(c, "render")
        assert not hasattr(c, "animate")
        assert not hasattr(c, "execute")


class TestModuleIsolation:
    """§7：schema 模块不导入运行时模块。"""

    def test_no_runtime_imports(self):
        """验证 src/body_action 不导入禁止的模块。"""
        import src.body_action.schema as mod
        source = inspect.getsource(mod)
        forbidden = [
            "runtime_engine",
            "input_interpreter",
            "llm",
            "router",
            "memory",
            "persona",
            "companion",
            "renderer",
            "animation",
            "avatar",
            "field_trace",
        ]
        for name in forbidden:
            assert name not in source.lower(), f"schema.py 不应导入 '{name}'"


class TestRegression:
    """§8：现有测试无回归。"""

    def test_action_primitives_count(self):
        """恰好 10 个批准的原语。"""
        assert len(ACTION_PRIMITIVES) == 10

    def test_weight_bands_count(self):
        """恰好 4 个权重带。"""
        assert len(WEIGHT_BANDS) == 4

    def test_completion_modes_count(self):
        """恰好 3 个 completion 模式。"""
        assert len(COMPLETION_MODES) == 3
