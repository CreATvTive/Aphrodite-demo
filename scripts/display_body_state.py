#!/usr/bin/env python3
"""BodyState 调试显示面板。

从 monitor/body_state.jsonl 读取最新的 BodyState 记录并以中文文本面板显示。
纯显示——不修改任何文件，不影响运行时行为。
"""
import json
import sys
from pathlib import Path

# 字段的中文标签
FIELD_LABELS = {
    "gaze": "视线",
    "posture": "姿态",
    "motion_intensity": "动作幅度",
    "distance": "距离",
    "timing": "停顿",
    "speech_density_hint": "语言密度提示",
    "expression_temperature": "表达温度",
    "body_note": "说明",
    "provenance": "来源",
    "behavior_affecting": "是否影响行为",
}

# 枚举值的中文翻译
VALUE_TRANSLATIONS = {
    # gaze
    "neutral": "中性",
    "user": "注视用户",
    "down": "向下",
    "away": "偏离",
    "down_then_user": "向下后注视用户",
    "away_then_user": "偏离后注视用户",
    # posture
    "slight_forward": "略前倾",
    "stable": "稳定",
    "slight_withdraw": "略后撤",
    "closed_stable": "闭合稳定",
    # motion_intensity
    "still": "静止",
    "low": "低",
    "medium": "中",
    # distance
    "baseline": "基线",
    "slightly_closer": "略近",
    "maintained": "保持",
    "slightly_farther": "略远",
    # timing
    "immediate": "即时",
    "short_pause": "短停顿",
    "longer_pause": "长停顿",
    # speech_density_hint
    "minimal": "极简",
    "structured": "结构化",
    # expression_temperature
    "cool": "冷静",
    "restrained": "克制",
    "warm_restrained": "温暖克制",
}

VIEWER_VALUE_TRANSLATIONS = {
    # gaze
    "neutral": "中性",
    "user": "注视用户",
    "down": "低头",
    "away": "看向一侧",
    "down_then_user": "低头后回看用户",
    "away_then_user": "看向一侧后回看用户",
    # posture
    "slight_forward": "略前倾",
    "stable": "稳定",
    "slight_withdraw": "略后撤",
    "closed_stable": "闭合稳定",
    # distance
    "baseline": "基线距离",
    "slightly_closer": "略微靠近",
    "maintained": "保持距离",
    "slightly_farther": "略微拉开距离",
    # motion_intensity
    "still": "静止",
    "low": "低幅度动作",
    "medium": "中等幅度动作",
    # timing
    "immediate": "立即回应",
    "short_pause": "短暂停顿",
    "longer_pause": "较长停顿",
}


def translate_value(field: str, value) -> str:
    """将枚举值翻译为中文。"""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return VALUE_TRANSLATIONS.get(str(value), str(value))


def read_latest_body_state(log_path: str = "monitor/body_state.jsonl") -> dict | None:
    """从 JSONL 文件中读取最新的 BodyState 记录。"""
    path = Path(log_path)
    if not path.exists():
        return None

    last_line = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                last_line = line

    if last_line is None:
        return None

    try:
        return json.loads(last_line)
    except json.JSONDecodeError:
        return None


def read_body_state_records(log_path: str = "monitor/body_state.jsonl") -> list[dict]:
    """从 JSONL 文件读取所有可解析的 BodyState 记录。"""
    path = Path(log_path)
    if not path.exists():
        return []

    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _body(record: dict | None) -> dict:
    if not isinstance(record, dict):
        return {}
    body = record.get("body_state", record)
    return body if isinstance(body, dict) else {}


def _viewer_value(value) -> str:
    return VIEWER_VALUE_TRANSLATIONS.get(str(value), str(value))


def _viewer_field(body: dict, field: str, default: str = "未知") -> str:
    return _viewer_value(body.get(field, default))


def _distance_motion(body: dict) -> str:
    distance = _viewer_field(body, "distance")
    motion = _viewer_field(body, "motion_intensity")
    return f"{distance} / {motion}"


def _change_line(previous: dict | None, current: dict, field: str) -> str:
    current_value = _viewer_field(current, field)
    if previous is None:
        return current_value
    return f"{_viewer_field(previous, field)} → {current_value}"


def _distance_motion_change(previous: dict | None, current: dict) -> str:
    current_value = _distance_motion(current)
    if previous is None:
        return current_value
    return f"{_distance_motion(previous)} → {current_value}"


def render_viewer_card(current_record: dict | None, previous_record: dict | None = None) -> str:
    """渲染面向非技术观众的身体状态变化卡片。"""
    current = _body(current_record)
    previous = _body(previous_record) if previous_record is not None else None
    if not current:
        return "\n".join([
            "=" * 30,
            "  Aphrodite 当前身体状态",
            "=" * 30,
            "",
            "无 BodyState 记录。文件缺失或为空。",
            "",
            "=" * 30,
        ])

    posture = _viewer_field(current, "posture")
    gaze = _viewer_field(current, "gaze")
    if previous is None:
        state_change = "这是第一条可见身体状态。"
    else:
        state_change = (
            f"{_viewer_field(previous, 'posture')} / {_viewer_field(previous, 'gaze')}"
            f" → {posture} / {gaze}"
        )

    lines = [
        "=" * 30,
        "  Aphrodite 当前身体状态",
        "=" * 30,
        "",
        "当前身体状态：",
        f"  {posture} / {gaze} / {_distance_motion(current)}",
        "",
        "上一状态 → 当前状态：",
        f"  {state_change}",
        "",
        "姿态变化：",
        f"  {_change_line(previous, current, 'posture')}",
        "",
        "视线变化：",
        f"  {_change_line(previous, current, 'gaze')}",
        "",
        "距离与动作：",
        f"  {_distance_motion_change(previous, current)}",
        "",
        "节奏 / 停顿：",
        f"  {_change_line(previous, current, 'timing')}",
        "",
        "说明：",
        f"  {current.get('body_note') or '暂无说明'}",
        "",
        "=" * 30,
    ]
    return "\n".join(lines)


def render_viewer_card_from_file(log_path: str = "monitor/body_state.jsonl") -> str:
    """读取 JSONL 并渲染最新的非技术观众卡片。"""
    records = read_body_state_records(log_path)
    if not records:
        return render_viewer_card(None)
    previous = records[-2] if len(records) >= 2 else None
    return render_viewer_card(records[-1], previous)


def display_viewer_card(current_record: dict | None, previous_record: dict | None = None) -> None:
    print(render_viewer_card(current_record, previous_record))


def display_panel(record: dict) -> None:
    """以中文文本面板形式显示 BodyState 记录。"""
    body = record.get("body_state", record)

    print("=" * 50)
    print("  Aphrodite 身体状态")
    print("=" * 50)

    # 元数据（如有）
    if "turn_id" in record:
        print(f"  轮次: {record['turn_id']}")
    if "timestamp" in record:
        print(f"  时间: {record['timestamp']}")
    print()

    # 8 个身体状态字段
    display_fields = [
        "gaze",
        "posture",
        "motion_intensity",
        "distance",
        "timing",
        "speech_density_hint",
        "expression_temperature",
    ]
    for field in display_fields:
        label = FIELD_LABELS.get(field, field)
        raw_value = body.get(field, "未知")
        translated = translate_value(field, raw_value)
        print(f"  {label}: {translated}")

    # body_note
    if body.get("body_note"):
        print(f"\n  {FIELD_LABELS['body_note']}: {body['body_note']}")

    # provenance
    if body.get("provenance"):
        prov = body["provenance"]
        if isinstance(prov, list):
            prov_str = ", ".join(prov)
        else:
            prov_str = str(prov)
        print(f"  {FIELD_LABELS['provenance']}: {prov_str}")

    # behavior_affecting
    ba = body.get("behavior_affecting", False)
    print(f"  {FIELD_LABELS['behavior_affecting']}: {translate_value('behavior_affecting', ba)}")

    print("=" * 50)


def main():
    args = list(sys.argv[1:])
    viewer_card = False
    if "--viewer-card" in args:
        viewer_card = True
        args.remove("--viewer-card")

    log_path = args[0] if args else "monitor/body_state.jsonl"
    if viewer_card:
        print(render_viewer_card_from_file(log_path))
        if not read_body_state_records(log_path):
            sys.exit(1)
        return

    record = read_latest_body_state(log_path)

    if record is None:
        print("无 BodyState 记录。文件缺失或为空。")
        print(f"预期路径: {Path(log_path).resolve()}")
        sys.exit(1)

    display_panel(record)


if __name__ == "__main__":
    main()
