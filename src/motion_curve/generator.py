from __future__ import annotations

import math

from src.motion_params.schema import MotionParams

from .schema import CurvePoint, MotionCurve


BUCKET_COUNT = 20
CHANNEL_DELAY_FACTORS = {
    "gaze": 0.00,
    "head": 0.15,
    "torso": 0.30,
    "expression": 0.20,
    "posture": 0.00,
}


class MotionCurveGenerator:
    def generate(
        self,
        params: MotionParams,
        scenario_name: str,
        scenario_intent: str = "",
    ) -> MotionCurve:
        if not isinstance(params, MotionParams):
            raise TypeError("params must be a MotionParams instance")

        body_part_offsets_sec = _body_part_offsets_sec(params)
        times = _timeline(params)

        return MotionCurve(
            scenario_name=scenario_name,
            gaze_curve=[
                CurvePoint(time_sec=time_sec, amplitude=_gaze_amplitude(params, time_sec, body_part_offsets_sec), channel="gaze")
                for time_sec in times
            ],
            head_curve=[
                CurvePoint(time_sec=time_sec, amplitude=_head_amplitude(params, time_sec, body_part_offsets_sec), channel="head")
                for time_sec in times
            ],
            torso_curve=[
                CurvePoint(time_sec=time_sec, amplitude=_torso_amplitude(params, time_sec, body_part_offsets_sec), channel="torso")
                for time_sec in times
            ],
            expression_curve=[
                CurvePoint(time_sec=time_sec, amplitude=_expression_amplitude(params, time_sec, body_part_offsets_sec, times[-1]), channel="expression")
                for time_sec in times
            ],
            posture_curve=[
                CurvePoint(time_sec=time_sec, amplitude=_posture_amplitude(params, time_sec, times[0], times[-1]), channel="posture")
                for time_sec in times
            ],
            body_part_offsets_sec=body_part_offsets_sec,
            motion_completion=params.motion_completion,
            scenario_intent=scenario_intent,
        )


def _timeline(params: MotionParams) -> list[float]:
    duration = 1.5 / max(params.motion_speed, 0.10)
    end_time = min(params.initial_delay_sec + duration, 5.0)
    if BUCKET_COUNT == 1:
        return [0.0]
    return [
        round(end_time * index / (BUCKET_COUNT - 1), 3)
        for index in range(BUCKET_COUNT)
    ]


def _body_part_offsets_sec(params: MotionParams) -> float:
    return round(params.body_part_offsets.hand_offset_ms / 1000, 3)


def _channel_delay(body_part_offsets_sec: float, channel: str) -> float:
    return body_part_offsets_sec * CHANNEL_DELAY_FACTORS[channel]


def _gaze_amplitude(params: MotionParams, time_sec: float, body_part_offsets_sec: float) -> float:
    start_time = params.initial_delay_sec + _channel_delay(body_part_offsets_sec, "gaze")
    if time_sec < start_time:
        return 0.0

    contact_amplitude = 1.0 - params.gaze_release_amplitude * 0.50
    contact_end = start_time + params.gaze_contact_sec
    ramp_start = contact_end + params.head_turn_delay_sec
    ramp_end = ramp_start + 0.30

    if time_sec < ramp_start:
        return contact_amplitude
    if time_sec >= ramp_end:
        return params.gaze_release_amplitude

    progress = (time_sec - ramp_start) / 0.30
    return _lerp(contact_amplitude, params.gaze_release_amplitude, progress)


def _head_amplitude(params: MotionParams, time_sec: float, body_part_offsets_sec: float) -> float:
    start_time = params.initial_delay_sec + _channel_delay(body_part_offsets_sec, "head")
    ramp_start = start_time + params.gaze_contact_sec + params.head_turn_delay_sec
    ramp_end = ramp_start + 0.30

    if time_sec < ramp_start:
        return 0.0
    if time_sec >= ramp_end:
        return params.head_turn_amplitude

    return params.head_turn_amplitude * ((time_sec - ramp_start) / 0.30)


def _torso_amplitude(params: MotionParams, time_sec: float, body_part_offsets_sec: float) -> float:
    start_time = params.initial_delay_sec + _channel_delay(body_part_offsets_sec, "torso")
    if time_sec < start_time:
        return 0.0

    amplitude = min(abs(params.torso_lean) / 0.50, 1.0)
    if params.motion_completion < 0.50:
        amplitude *= params.motion_completion / 0.50
    return amplitude


def _expression_amplitude(
    params: MotionParams,
    time_sec: float,
    body_part_offsets_sec: float,
    end_time: float,
) -> float:
    start_time = params.initial_delay_sec + _channel_delay(body_part_offsets_sec, "expression")
    if time_sec < start_time:
        return 0.0

    if params.motion_completion >= 0.30:
        return params.expression_amplitude

    fade_start = start_time + 0.70 * max(end_time - start_time, 0.0)
    if time_sec <= fade_start:
        return params.expression_amplitude

    fade_duration = max(end_time - fade_start, 0.10)
    fade_progress = (time_sec - fade_start) / fade_duration
    return params.expression_amplitude * (1.0 - fade_progress)


def _posture_amplitude(params: MotionParams, time_sec: float, start_time: float, end_time: float) -> float:
    duration = max(end_time - start_time, 0.10)
    phase = (time_sec - start_time) / duration
    oscillation = 0.02 * (1.0 - params.posture_stability) * math.sin(2 * math.pi * 3 * phase)
    return params.posture_stability + oscillation


def _lerp(start: float, end: float, progress: float) -> float:
    progress = max(0.0, min(1.0, progress))
    return start + (end - start) * progress
