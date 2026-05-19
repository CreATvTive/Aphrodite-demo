#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_LOG_PATH = "monitor/body_action_composition.jsonl"

ACTION_LABELS = {
    "pause": "停顿",
    "stillness": "静止",
    "look_down": "视线下落",
    "look_to_user": "看向用户",
    "look_away": "视线释放",
    "slight_forward": "轻微前倾",
    "slight_withdraw": "轻微后撤",
    "maintain_distance": "保持距离",
    "reduce_motion": "降低动作幅度",
    "reset_posture": "重置姿态",
}

DURATION_LABELS = {
    "instant": "瞬时",
    "short": "短",
    "medium": "中等",
    "sustained": "持续",
}

COMPLETION_LABELS = {
    "partial": "部分完成",
    "restrained": "受抑制完成",
    "complete": "完成",
}


def read_latest_record(log_path: str | Path = DEFAULT_LOG_PATH) -> dict[str, Any] | None:
    path = Path(log_path)
    if not path.exists() or not path.is_file():
        return None

    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return None

    json_value = _parse_json_value(text)
    if isinstance(json_value, dict):
        return json_value
    if isinstance(json_value, list):
        for item in reversed(json_value):
            if isinstance(item, dict):
                return item
        return None

    latest: dict[str, Any] | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = _parse_json_value(line)
        if isinstance(parsed, dict):
            latest = parsed
    return latest


def render_panel(record: dict[str, Any] | None, *, viewer_card: bool = False, path: str | Path = DEFAULT_LOG_PATH) -> str:
    if not record:
        return "\n".join([
            "========================================",
            "  身体动作组合调试面板",
            "========================================",
            "",
            "没有可显示的 BodyActionComposition 记录。",
            f"读取路径：{Path(path)}",
            "",
            "========================================",
        ])

    record = _composition_record(record)
    if viewer_card:
        return _render_viewer_card(record)
    return _render_debug_panel(record)


def _render_viewer_card(record: dict[str, Any]) -> str:
    lines = [
        "========================================",
        "  当前身体组合",
        "========================================",
        "",
        "主要动作：",
        _simple_action_line(record.get("primary_actions")),
        "",
        "辅助动作：",
        _simple_action_line(record.get("secondary_actions")),
        "",
        "被抑制动作：",
        _simple_suppressed_line(record.get("suppressed_actions")),
        "",
        "说明：",
        f"  {_text_or_empty(record.get('composition_note'))}",
        "",
        "========================================",
    ]
    return "\n".join(lines)


def _render_debug_panel(record: dict[str, Any]) -> str:
    lines = [
        "========================================",
        "  身体动作组合调试面板",
        "========================================",
        "",
        "主动作 primary_actions：",
        _detailed_action_block(record.get("primary_actions")),
        "",
        "辅助动作 secondary_actions：",
        _detailed_action_block(record.get("secondary_actions")),
        "",
        "被抑制动作 suppressed_actions：",
        _simple_suppressed_line(record.get("suppressed_actions")),
        "",
        "约束 / suppression reason：",
        _constraints_line(record),
        "",
        "动作完成方式 completion：",
        _completion_summary(record),
        "",
        "持续时间 duration_hint：",
        _duration_summary(record),
        "",
        "组合说明 composition_note：",
        f"  {_text_or_empty(record.get('composition_note'))}",
        "",
        "body_part_offsets：",
        _offset_line(record),
        "",
        "provenance：",
        _provenance_line(record),
        "",
        "behavior_affecting：",
        f"  {record.get('behavior_affecting', False)}",
        "",
        "========================================",
    ]
    return "\n".join(lines)


def _detailed_action_block(value: Any) -> str:
    actions = _as_list(value)
    if not actions:
        return "  无"

    lines: list[str] = []
    for action in actions:
        if isinstance(action, dict):
            name = str(action.get("action_name", "unknown"))
            label = ACTION_LABELS.get(name, name)
            order = action.get("order", "-")
            duration = str(action.get("duration_hint", "-"))
            completion = str(action.get("completion", "-"))
            constraints = _join_list(action.get("constraints"))
            provenance = _join_list(action.get("provenance"))
            behavior = action.get("behavior_affecting", False)
            lines.append(
                f"  - {label} ({name}) | order={order} | "
                f"duration_hint={duration}（{DURATION_LABELS.get(duration, duration)}） | "
                f"completion={completion}（{COMPLETION_LABELS.get(completion, completion)}） | "
                f"constraints={constraints or '无'} | provenance={provenance or '无'} | "
                f"behavior_affecting={behavior}"
            )
        else:
            name = str(action)
            lines.append(f"  - {ACTION_LABELS.get(name, name)} ({name})")
    return "\n".join(lines)


def _simple_action_line(value: Any) -> str:
    names = [_action_name(action) for action in _as_list(value)]
    names = [name for name in names if name]
    if not names:
        return "  无"
    return "  " + "、".join(f"{ACTION_LABELS.get(name, name)} ({name})" for name in names)


def _simple_suppressed_line(value: Any) -> str:
    names = [str(item) for item in _as_list(value) if str(item)]
    if not names:
        return "  无"
    return "  " + "、".join(f"{ACTION_LABELS.get(name, name)} ({name})" for name in names)


def _constraints_line(record: dict[str, Any]) -> str:
    constraints = _as_list(record.get("hard_constraints"))
    action_constraints: list[str] = []
    for action in _as_list(record.get("primary_actions")) + _as_list(record.get("secondary_actions")):
        if isinstance(action, dict):
            action_constraints.extend(str(item) for item in _as_list(action.get("constraints")))
    merged: list[str] = []
    for item in [*constraints, *action_constraints]:
        text = str(item)
        if text and text not in merged:
            merged.append(text)
    return "  " + ("、".join(merged) if merged else "无")


def _completion_summary(record: dict[str, Any]) -> str:
    completions = _unique_action_field(record, "completion")
    if not completions:
        return "  未记录"
    return "  " + "、".join(f"{item}（{COMPLETION_LABELS.get(item, item)}）" for item in completions)


def _duration_summary(record: dict[str, Any]) -> str:
    durations = _unique_action_field(record, "duration_hint")
    if not durations:
        return "  未记录"
    return "  " + "、".join(f"{item}（{DURATION_LABELS.get(item, item)}）" for item in durations)


def _offset_line(record: dict[str, Any]) -> str:
    offsets = record.get("body_part_offsets")
    if isinstance(offsets, dict):
        return (
            "  "
            f"gaze={offsets.get('gaze_offset_ms', '-')}ms, "
            f"head={offsets.get('head_offset_ms', '-')}ms, "
            f"shoulder={offsets.get('shoulder_offset_ms', '-')}ms, "
            f"hand={offsets.get('hand_offset_ms', '-')}ms"
        )

    note = str(record.get("composition_note", ""))
    if "offset" in note or "gaze:" in note or "head:" in note:
        return f"  {note}"
    return "  未记录"


def _provenance_line(record: dict[str, Any]) -> str:
    provenance = record.get("provenance")
    if provenance is not None:
        return "  " + (_join_list(provenance) or str(provenance))

    action_provenance: list[str] = []
    for action in _as_list(record.get("primary_actions")) + _as_list(record.get("secondary_actions")):
        if isinstance(action, dict):
            action_provenance.extend(str(item) for item in _as_list(action.get("provenance")))
    return "  " + (_join_list(action_provenance) or "无")


def _unique_action_field(record: dict[str, Any], field: str) -> list[str]:
    result: list[str] = []
    for action in _as_list(record.get("primary_actions")) + _as_list(record.get("secondary_actions")):
        if isinstance(action, dict):
            value = action.get(field)
            if value is not None and str(value) not in result:
                result.append(str(value))
    return result


def _action_name(action: Any) -> str:
    if isinstance(action, dict):
        return str(action.get("action_name", ""))
    return str(action)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _join_list(value: Any) -> str:
    return "、".join(str(item) for item in _as_list(value) if str(item))


def _text_or_empty(value: Any) -> str:
    text = "" if value is None else str(value)
    return text or "无"


def _composition_record(record: dict[str, Any]) -> dict[str, Any]:
    for key in ("body_action_composition", "composition"):
        value = record.get(key)
        if isinstance(value, dict):
            return value
    return record


def _parse_json_value(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="显示 BodyActionComposition 调试面板")
    parser.add_argument("path", nargs="?", default=DEFAULT_LOG_PATH)
    parser.add_argument("--viewer-card", action="store_true", help="显示简化查看卡片")
    args = parser.parse_args(argv)

    record = read_latest_record(args.path)
    print(render_panel(record, viewer_card=args.viewer_card, path=args.path))
    return 0 if record else 1


if __name__ == "__main__":
    raise SystemExit(main())
