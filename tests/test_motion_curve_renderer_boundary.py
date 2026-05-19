"""测试 MotionCurve 渲染器边界 — Phase P0 数学强化（交付物 3 选项 B）。

验证：
1. MotionCurve 具有 behavior_affecting=False 守卫
2. MotionCurve schema 不导入渲染器、运行时或行为模块
3. MotionCurve 不调用任何 behavior_affecting 路径
"""

from __future__ import annotations

import ast
import inspect

import pytest

from src.motion_curve.schema import MotionCurve, CurvePoint


class TestMotionCurveRenderBoundary:
    """验证 MotionCurve 保持在渲染器边界之后。"""

    def test_behavior_affecting_defaults_to_false(self):
        """MotionCurve 的 behavior_affecting 字段必须默认为 False。"""
        curve = MotionCurve(
            scenario_name="test",
            gaze_curve=[],
            head_curve=[],
            torso_curve=[],
            expression_curve=[],
            posture_curve=[],
        )
        assert curve.behavior_affecting is False

    def test_behavior_affecting_true_rejected(self):
        """构造 behavior_affecting=True 必须引发 ValueError。"""
        with pytest.raises(ValueError, match="behavior_affecting must be False"):
            MotionCurve(
                scenario_name="test",
                gaze_curve=[],
                head_curve=[],
                torso_curve=[],
                expression_curve=[],
                posture_curve=[],
                behavior_affecting=True,
            )

    def test_to_dict_includes_behavior_affecting(self):
        """to_dict 必须包含 behavior_affecting 以供审计。"""
        curve = MotionCurve(
            scenario_name="test",
            gaze_curve=[CurvePoint(time_sec=0.0, amplitude=0.5, channel="gaze")],
            head_curve=[],
            torso_curve=[],
            expression_curve=[],
            posture_curve=[],
        )
        d = curve.to_dict()
        assert "behavior_affecting" in d
        assert d["behavior_affecting"] is False

    def test_scenario_intent_present_but_non_behavior_affecting(self):
        """scenario_intent 可以存在，但 MotionCurve 仍必须为 non-behavior-affecting。"""
        curve = MotionCurve(
            scenario_name="test",
            gaze_curve=[],
            head_curve=[],
            torso_curve=[],
            expression_curve=[],
            posture_curve=[],
            scenario_intent="diagnostic-only intent",
        )
        # scenario_intent 可以携带，但行为守卫必须保持
        assert curve.scenario_intent == "diagnostic-only intent"
        assert curve.behavior_affecting is False

    def test_schema_has_no_renderer_or_runtime_imports(self):
        """motion_curve/schema.py 不得导入渲染器、运行时或行为模块。"""
        import src.motion_curve.schema as mod

        source = inspect.getsource(mod).lower()

        forbidden = [
            "renderer", "runtime_engine", "runtime_state", "runtime_immediate",
            "live2d", "ue5", "animation", "avatar",
            "body_action", "body_action.policy", "body_action.composer",
            "llm", "language", "companion", "prompt",
        ]

        violations = []
        for name in forbidden:
            # 检查作为字符串的导入路径或模块名
            if name in source:
                violations.append(name)

        assert len(violations) == 0, f"Schema 包含禁止引用: {violations}"

    def test_schema_no_forbidden_imports_by_ast(self):
        """通过 AST 分析验证——schema.py 在语法级别上不导入禁止模块。"""
        schema_path = "src/motion_curve/schema.py"
        with open(schema_path, "r", encoding="utf-8") as f:
            source = f.read()

        tree = ast.parse(source)

        forbidden = {
            "renderer", "runtime", "live2d", "ue5",
            "animation", "avatar", "body_action",
            "llm", "language", "companion", "prompt",
            "memory", "speech",
        }

        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name.lower()
                    if any(fb in name for fb in forbidden):
                        violations.append(f"import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = (node.module or "").lower()
                if any(fb in module for fb in forbidden):
                    violations.append(f"from {node.module} import ...")

        assert len(violations) == 0, f"发现禁止导入: {violations}"

    def test_generator_has_no_renderer_or_runtime_imports(self):
        """生成器也不得导入渲染器、运行时或行为模块。"""
        import src.motion_curve.generator as mod

        source = inspect.getsource(mod).lower()

        forbidden = [
            "renderer", "runtime_engine", "runtime",
            "live2d", "ue5", "animation", "avatar",
            "body_action", "llm", "language",
            "companion", "prompt",
        ]

        for name in forbidden:
            assert name not in source, f"生成器包含禁止引用: {name}"

    def test_scenario_intent_not_consumed_as_behavior(self):
        """scenario_intent 不应被生成器作为行为参数消耗。
        
        具体而言：`scenario_intent` 以透传方式存入 MotionCurve，
        但不影响曲线生成。本测试验证注入不同的 scenario_intent
        值不改变生成的曲线。
        """
        from src.motion_curve.generator import MotionCurveGenerator
        from src.motion_params.schema import (
            BodyPartOffsets,
            HardMotionConstraints,
            MotionParams,
        )

        gen = MotionCurveGenerator()

        # 使用相同 MotionParams 的两个曲线，scenario_intent 不同
        params = MotionParams(
            initial_delay_sec=0.1,
            motion_speed=0.5,
            pause_after_sec=0.0,
            gaze_contact_sec=0.3,
            gaze_release_amplitude=0.5,
            head_turn_amplitude=0.2,
            head_turn_delay_sec=0.0,
            torso_lean=0.1,
            posture_stability=0.6,
            expression_amplitude=0.1,
            motion_completion=0.7,
            body_part_offsets=BodyPartOffsets(),
            hard_constraints=HardMotionConstraints(),
            behavior_affecting=False,
        )

        curve_a = gen.generate(params, "test_s", scenario_intent="intent_a")
        curve_b = gen.generate(params, "test_s", scenario_intent="intent_b")

        # 曲线点必须相同（scenario_intent 不影响生成）
        assert len(curve_a.gaze_curve) == len(curve_b.gaze_curve)
        for i, (pa, pb) in enumerate(zip(curve_a.gaze_curve, curve_b.gaze_curve)):
            assert pa.time_sec == pb.time_sec, f"时间分歧于索引 {i}"
            assert pa.amplitude == pytest.approx(pb.amplitude, abs=1e-9), (
                f"幅度分歧于索引 {i}: {pa.amplitude} != {pb.amplitude}"
            )

        # 场景意图应通过透传保留，但不影响曲线形状
        assert curve_a.scenario_intent == "intent_a"
        assert curve_b.scenario_intent == "intent_b"
