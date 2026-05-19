from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from src.body_state.schema import BodyState


class BodyStateLogger:
    """将 BodyState 记录写入 JSONL 日志文件。"""

    def __init__(self, log_path: str = "monitor/body_state.jsonl") -> None:
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, body_state: BodyState, turn_id: str = "", timestamp: str = "") -> None:
        record = {
            "turn_id": turn_id,
            "timestamp": timestamp,
            "body_state": {
                "gaze": body_state.gaze,
                "posture": body_state.posture,
                "motion_intensity": body_state.motion_intensity,
                "distance": body_state.distance,
                "timing": body_state.timing,
                "speech_density_hint": body_state.speech_density_hint,
                "expression_temperature": body_state.expression_temperature,
                "body_note": body_state.body_note,
                "provenance": body_state.provenance,
                "behavior_affecting": body_state.behavior_affecting,
            },
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
