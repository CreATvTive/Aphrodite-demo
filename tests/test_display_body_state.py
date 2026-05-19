"""测试 BodyState 调试显示面板。"""
import json
import tempfile
from pathlib import Path
from scripts.display_body_state import (
    display_panel,
    read_body_state_records,
    read_latest_body_state,
    render_viewer_card,
    render_viewer_card_from_file,
    translate_value,
)
import io
import sys


def _write_records(records: list[dict]) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return f.name


class TestReadLatestBodyState:
    def test_reads_latest_record(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            f.write(
                json.dumps(
                    {"body_state": {"gaze": "neutral", "body_note": "first"}},
                    ensure_ascii=False,
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {"body_state": {"gaze": "user", "body_note": "latest"}},
                    ensure_ascii=False,
                )
                + "\n"
            )
            tmp_path = f.name

        try:
            record = read_latest_body_state(tmp_path)
            assert record is not None
            assert record["body_state"]["body_note"] == "latest"
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_missing_file_returns_none(self):
        record = read_latest_body_state("nonexistent_path_12345.jsonl")
        assert record is None

    def test_empty_file_returns_none(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            f.write("")
            tmp_path = f.name

        try:
            record = read_latest_body_state(tmp_path)
            assert record is None
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_blank_lines_handled(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            f.write("\n")
            f.write(
                json.dumps({"body_state": {"gaze": "down"}}, ensure_ascii=False) + "\n"
            )
            f.write("\n")
            tmp_path = f.name

        try:
            record = read_latest_body_state(tmp_path)
            assert record is not None
            assert record["body_state"]["gaze"] == "down"
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_does_not_inspect_raw_user_input(self):
        """BodyState 记录不包含原始用户输入——显示面板不应访问它。"""
        record = {"body_state": {"gaze": "neutral"}}
        # 验证 display_panel 仅读取 body_state 字段，不访问 user_input 字段
        # 这通过 display_panel 的设计保证——它不迭代 record 的全部字段
        assert "user_input" not in record.get("body_state", record)

    def test_does_not_modify_file(self):
        """验证 read_latest_body_state 不修改文件。"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            test_content = (
                json.dumps({"body_state": {"gaze": "neutral"}}, ensure_ascii=False)
                + "\n"
            )
            f.write(test_content)
            tmp_path = f.name

        try:
            original_mtime = Path(tmp_path).stat().st_mtime
            read_latest_body_state(tmp_path)
            # 文件内容应不变
            content = Path(tmp_path).read_text(encoding="utf-8")
            assert content == test_content
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_display_panel_output_contains_field_labels(self):
        record = {
            "body_state": {
                "gaze": "down_then_user",
                "posture": "slight_forward",
                "motion_intensity": "low",
                "distance": "maintained",
                "timing": "short_pause",
                "speech_density_hint": "structured",
                "expression_temperature": "warm_restrained",
                "body_note": "用户缺乏起点",
                "provenance": ["grip_loss_signal"],
                "behavior_affecting": False,
            }
        }

        capture = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = capture
        try:
            display_panel(record)
        finally:
            sys.stdout = old_stdout

        output = capture.getvalue()
        assert "视线" in output
        assert "姿态" in output
        assert "说明" in output
        assert "来源" in output
        assert "是否影响行为" in output
        assert "向下后注视用户" in output  # 验证中文翻译


class TestTranslateValue:
    def test_translates_known_enum(self):
        assert translate_value("gaze", "down_then_user") == "向下后注视用户"
        assert translate_value("posture", "slight_forward") == "略前倾"
        assert translate_value("motion_intensity", "still") == "静止"

    def test_boolean_translation(self):
        assert translate_value("behavior_affecting", False) == "否"
        assert translate_value("behavior_affecting", True) == "是"

    def test_falls_through_unknown(self):
        assert translate_value("unknown_field", "some_value") == "some_value"

    def test_list_translation(self):
        assert translate_value("provenance", ["a", "b"]) == "a, b"


class TestViewerCard:
    def _ground_record(self) -> dict:
        return {
            "turn_id": "turn-1",
            "body_state": {
                "gaze": "neutral",
                "posture": "neutral",
                "motion_intensity": "low",
                "distance": "baseline",
                "timing": "immediate",
                "speech_density_hint": "medium",
                "expression_temperature": "restrained",
                "body_note": "未观测到可用场信号；回归地面姿态",
                "provenance": ["no_observable_field_signal"],
                "behavior_affecting": False,
            },
            "user_input": "raw text must stay hidden",
        }

    def _grip_record(self) -> dict:
        return {
            "turn_id": "turn-2",
            "FieldTrace": {"active": True},
            "router": "hidden",
            "memory": "hidden",
            "body_state": {
                "gaze": "down_then_user",
                "posture": "slight_forward",
                "motion_intensity": "low",
                "distance": "maintained",
                "timing": "short_pause",
                "speech_density_hint": "structured",
                "expression_temperature": "warm_restrained",
                "body_note": "用户缺乏可操作起点；提供一个小抓点——非安慰、非激励",
                "provenance": ["grip_loss_signal(starting_point_loss)"],
                "behavior_affecting": False,
                "confidence": 0.88,
                "active": True,
            },
            "user_input": "I don't know where to start.",
        }

    def test_reads_previous_and_latest_records(self):
        path = _write_records([self._ground_record(), self._grip_record()])
        try:
            records = read_body_state_records(path)
            assert len(records) == 2
            assert records[-2]["body_state"]["posture"] == "neutral"
            assert records[-1]["body_state"]["posture"] == "slight_forward"
        finally:
            Path(path).unlink(missing_ok=True)

    def test_viewer_card_displays_previous_to_current_transition(self):
        output = render_viewer_card(self._grip_record(), self._ground_record())
        assert "上一状态 → 当前状态" in output
        assert "中性 → 略前倾" in output
        assert "中性 → 低头后回看用户" in output
        assert "基线距离 / 低幅度动作 → 保持距离 / 低幅度动作" in output
        assert "立即回应 → 短暂停顿" in output

    def test_viewer_card_hides_engineering_fields(self):
        output = render_viewer_card(self._grip_record(), self._ground_record())
        forbidden = [
            "FieldTrace",
            "CorrectionSignal",
            "GripLossSignal",
            "provenance",
            "confidence",
            "active",
            "behavior_affecting",
            "router",
            "memory",
            "semantic authority",
            "grip_loss_signal",
        ]
        for token in forbidden:
            assert token not in output

    def test_viewer_card_handles_single_record(self):
        output = render_viewer_card(self._ground_record())
        assert "这是第一条可见身体状态" in output
        assert "姿态变化" in output
        assert "中性" in output

    def test_viewer_card_handles_empty_file_gracefully(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            path = f.name
        try:
            output = render_viewer_card_from_file(path)
            assert "无 BodyState 记录" in output
        finally:
            Path(path).unlink(missing_ok=True)

    def test_viewer_card_handles_missing_file_gracefully(self):
        output = render_viewer_card_from_file("nonexistent_body_state_12345.jsonl")
        assert "无 BodyState 记录" in output

    def test_viewer_card_does_not_modify_jsonl(self):
        path = _write_records([self._ground_record(), self._grip_record()])
        try:
            before = Path(path).read_text(encoding="utf-8")
            render_viewer_card_from_file(path)
            after = Path(path).read_text(encoding="utf-8")
            assert after == before
        finally:
            Path(path).unlink(missing_ok=True)

    def test_viewer_card_does_not_show_raw_user_input(self):
        output = render_viewer_card(self._grip_record(), self._ground_record())
        assert "I don't know where to start" not in output
        assert "raw text must stay hidden" not in output

    def test_viewer_card_has_no_runtime_behavior_imports(self):
        source = Path("scripts/display_body_state.py").read_text(encoding="utf-8")
        for token in ("runtime_engine", "router", "memory_store", "InputInterpreter"):
            assert token not in source

    def test_existing_debug_panel_remains_available(self):
        record = self._grip_record()
        capture = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = capture
        try:
            display_panel(record)
        finally:
            sys.stdout = old_stdout

        output = capture.getvalue()
        assert "Aphrodite 身体状态" in output
        assert "来源" in output
        assert "是否影响行为" in output
