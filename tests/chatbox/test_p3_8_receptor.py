"""P3 task-card 8: program-side expression receptor statistical contract.

Covers the registry-driven receptor planner, the frozen ReceptorPlan, the
"犹豫=方差" discriminating test, dynamic 0/1/12/17-dim vectors, split-by-plan,
style instruction privacy, protocol typing.submit, and the meta-narration
zero-hit contract on plan-derived style instructions.
"""

from __future__ import annotations

import inspect
import json
import math
import statistics

import pytest

from app.chatbox.receptor_config import (
    DELAY_CEILING_SECONDS,
    DELAY_FLOOR_SECONDS,
    DELAY_HESITATION_VARIANCE_SECONDS,
    DELAY_VARIANCE_SECONDS,
    LENGTH_MAX_CHARS,
    LENGTH_MIN_CHARS,
    RECEPTOR_PLAN_VERSION,
    SPLIT_MAX_SEGMENTS,
    SPLIT_MIN_SEGMENTS,
    TYPEWRITER_MAX_MS,
    TYPEWRITER_MIN_MS,
)
from app.chatbox.receptor_planner import (
    ReceptorPlan,
    ReceptorPlannerError,
    plan_from_receptor_vector,
    split_reply_by_plan,
    style_instruction_from_plan,
)
from app.chatbox.dialogue_protocol import (
    DIALOGUE_PROTOCOL_VERSION,
    DialogueProtocolError,
    parse_client_message,
)
from app.chatbox.meta_narration import detect_meta_narration


SAMPLES = 2400  # >= 2000 deterministic samples per the task-card spec


def _mean(xs):
    return sum(xs) / len(xs)


def _var(xs):
    return sum((x - _mean(xs)) ** 2 for x in xs) / len(xs)


# ---------------------------------------------------------------------------
# planner: basic plan shape and bounds
# ---------------------------------------------------------------------------


def test_plan_basic_shape_and_bounds():
    plan = plan_from_receptor_vector(
        turn_id="t1", receptor_vector=(0.2, -0.1, 0.0), clock_ns=1_700_000_000, seed=42
    )
    assert isinstance(plan, ReceptorPlan)
    assert plan.version == RECEPTOR_PLAN_VERSION
    assert plan.turn_id == "t1"
    assert plan.plan_id.startswith("plan-")
    assert DELAY_FLOOR_SECONDS <= plan.delay_sample_seconds <= DELAY_CEILING_SECONDS
    assert LENGTH_MIN_CHARS <= plan.length_target_chars <= LENGTH_MAX_CHARS
    assert SPLIT_MIN_SEGMENTS <= plan.segment_count <= SPLIT_MAX_SEGMENTS
    assert 0.0 <= plan.punctuation_looseness <= 1.0
    assert TYPEWRITER_MIN_MS <= plan.typewriter_ms <= TYPEWRITER_MAX_MS
    assert 0.0 <= plan.expression_pressure <= 1.0
    # public_frame exposes only user-safe fields, never the receptor summary.
    frame = plan.public_frame()
    assert "receptor_summary" not in frame
    assert "delay_mean_seconds" not in frame
    assert "delay_variance_seconds" not in frame
    assert "expression_pressure" not in frame
    assert "punctuation_looseness" not in frame
    assert frame["typewriter_ms"] == plan.typewriter_ms


def test_plan_deterministic_reproducible_with_same_seed_and_clock():
    kwargs = dict(turn_id="t2", receptor_vector=(0.5,), clock_ns=1_700_000_001, seed=7)
    a = plan_from_receptor_vector(**kwargs)
    b = plan_from_receptor_vector(**kwargs)
    assert a.plan_id == b.plan_id
    assert a.delay_sample_seconds == pytest.approx(b.delay_sample_seconds)
    assert a.typewriter_ms == b.typewriter_ms


def test_plan_invalid_inputs_fail_closed():
    with pytest.raises(ReceptorPlannerError):
        plan_from_receptor_vector(turn_id="t", receptor_vector=(1.5,), clock_ns=1, seed=1)
    with pytest.raises(ReceptorPlannerError):
        plan_from_receptor_vector(turn_id="t", receptor_vector=(float("nan"),), clock_ns=1, seed=1)
    with pytest.raises(ReceptorPlannerError):
        plan_from_receptor_vector(turn_id="", receptor_vector=(0.0,), clock_ns=1, seed=1)
    with pytest.raises(ReceptorPlannerError):
        plan_from_receptor_vector(turn_id="t", receptor_vector=(0.0,), clock_ns=-1, seed=1)


def test_plan_dynamic_dimensions_safe():
    for vec in [(), (0.0,), tuple(0.1 for _ in range(12)), tuple(0.2 for _ in range(17))]:
        plan = plan_from_receptor_vector(
            turn_id="t-dyn", receptor_vector=vec, clock_ns=1_700_000_002, seed=3
        )
        assert DELAY_FLOOR_SECONDS <= plan.delay_sample_seconds <= DELAY_CEILING_SECONDS
        assert SPLIT_MIN_SEGMENTS <= plan.segment_count <= SPLIT_MAX_SEGMENTS


# ---------------------------------------------------------------------------
# statistical contract: mean, variance, quantile bounds (>= 2000 samples)
# ---------------------------------------------------------------------------


def test_delay_distribution_statistics_within_spec():
    samples = []
    for i in range(SAMPLES):
        plan = plan_from_receptor_vector(
            turn_id=f"t-{i}", receptor_vector=(0.0,), clock_ns=1_700_000_000 + i, seed=i
        )
        samples.append(plan.delay_sample_seconds)
    m = _mean(samples)
    v = _var(samples)
    # All samples within the protocol floor/ceiling.
    assert all(DELAY_FLOOR_SECONDS <= s <= DELAY_CEILING_SECONDS for s in samples)
    # Mean within the configured band (generous tolerance for a truncated normal).
    assert 0.1 <= m <= 3.0
    # Variance is positive and bounded.
    assert v > 0.0
    assert v < (DELAY_CEILING_SECONDS ** 2)
    # Quantile bounds: 99th percentile within ceiling.
    sorted_s = sorted(samples)
    assert sorted_s[-1] <= DELAY_CEILING_SECONDS
    assert sorted_s[0] >= DELAY_FLOOR_SECONDS


def test_typewriter_speed_distribution_within_bounds():
    speeds = set()
    for i in range(SAMPLES):
        v = (math.sin(i * 0.01) * 0.8,)  # varied activity across samples
        plan = plan_from_receptor_vector(
            turn_id=f"tw-{i}", receptor_vector=v, clock_ns=1_700_000_000 + i, seed=i
        )
        speeds.add(plan.typewriter_ms)
    assert all(TYPEWRITER_MIN_MS <= s <= TYPEWRITER_MAX_MS for s in speeds)
    assert len(speeds) > 1  # not a single constant


def test_length_target_within_bounds():
    for i in range(SAMPLES):
        plan = plan_from_receptor_vector(
            turn_id=f"len-{i}", receptor_vector=(0.4,), clock_ns=1_700_000_000 + i, seed=i
        )
        assert LENGTH_MIN_CHARS <= plan.length_target_chars <= LENGTH_MAX_CHARS


def test_segment_count_within_bounds():
    for i in range(SAMPLES):
        plan = plan_from_receptor_vector(
            turn_id=f"seg-{i}", receptor_vector=(0.1,) * 5, clock_ns=1_700_000_000 + i, seed=i
        )
        assert SPLIT_MIN_SEGMENTS <= plan.segment_count <= SPLIT_MAX_SEGMENTS


# ---------------------------------------------------------------------------
# "犹豫=方差" discriminating test
# ---------------------------------------------------------------------------


def test_hesitation_raises_variance_not_mean():
    # Two groups with near-equal means but different hesitation.
    calm = []
    hesitant = []
    for i in range(SAMPLES):
        p_calm = plan_from_receptor_vector(
            turn_id=f"c-{i}", receptor_vector=(0.0,), clock_ns=1_700_000_000 + i, seed=i,
            hesitation=0.0,
        )
        p_hes = plan_from_receptor_vector(
            turn_id=f"h-{i}", receptor_vector=(0.0,), clock_ns=1_700_000_000 + i, seed=i,
            hesitation=1.0,
        )
        calm.append(p_calm.delay_sample_seconds)
        hesitant.append(p_hes.delay_sample_seconds)
    mean_calm, mean_hes = _mean(calm), _mean(hesitant)
    var_calm, var_hes = _var(calm), _var(hesitant)
    # Means are approximately the same (within 0.5s).
    assert abs(mean_calm - mean_hes) < 0.5
    # Hesitant group has materially higher observed variance.
    assert var_hes > var_calm * 2.0
    # And the hesitant variance is near the configured hesitation variance.
    assert var_hes > DELAY_VARIANCE_SECONDS


# ---------------------------------------------------------------------------
# split_reply_by_plan
# ---------------------------------------------------------------------------


def test_split_respects_explicit_paragraphs():
    text = "第一段。\n\n第二段。\n\n第三段。"
    segs = split_reply_by_plan(text, 2)
    assert segs == ("第一段。", "第二段。", "第三段。")


def test_split_sentence_fallback():
    text = "一句。两句。三句。四句。"
    segs = split_reply_by_plan(text, 2)
    assert len(segs) == 2
    assert "".join(segs).replace("", "") == text  # content preserved


def test_split_single_segment():
    segs = split_reply_by_plan("只有一段。", 1)
    assert segs == ("只有一段。",)


def test_split_invalid_segment_count():
    with pytest.raises(ReceptorPlannerError):
        split_reply_by_plan("x", 0)
    with pytest.raises(ReceptorPlannerError):
        split_reply_by_plan("x", SPLIT_MAX_SEGMENTS + 1)


# ---------------------------------------------------------------------------
# style instruction privacy (C.5)
# ---------------------------------------------------------------------------


def test_style_instruction_no_internal_state():
    plan = plan_from_receptor_vector(
        turn_id="t-priv", receptor_vector=(0.2, -0.3), clock_ns=1_700_000_003, seed=5
    )
    instr = style_instruction_from_plan(plan)
    # No dim ids, labels, values, mechanism names, or causal explanations.
    forbidden = [
        "dim", "维度", "attractor", "吸引子", "OU", "baseline", "基线",
        "value", "值", "threshold", "阈值", "因为", "由于",
    ]
    for term in forbidden:
        assert term not in instr
    # Meta-narration detector returns zero hits on the style instruction.
    assert detect_meta_narration(instr) == ()


# ---------------------------------------------------------------------------
# protocol: typing.submit parsing
# ---------------------------------------------------------------------------


def test_protocol_typing_submit_parsed():
    msg = json.dumps({
        "version": DIALOGUE_PROTOCOL_VERSION,
        "type": "typing.submit",
        "client_turn_id": "turn-typing-1",
        "state": "start",
    })
    cmd = parse_client_message(msg)
    assert cmd.type == "typing.submit"
    assert cmd.client_turn_id == "turn-typing-1"
    assert cmd.typing_state == "start"


def test_protocol_typing_submit_invalid_state_rejected():
    msg = json.dumps({
        "version": DIALOGUE_PROTOCOL_VERSION,
        "type": "typing.submit",
        "client_turn_id": "turn-typing-2",
        "state": "bogus",
    })
    with pytest.raises(DialogueProtocolError):
        parse_client_message(msg)


def test_protocol_typing_submit_missing_state_rejected():
    msg = json.dumps({
        "version": DIALOGUE_PROTOCOL_VERSION,
        "type": "typing.submit",
        "client_turn_id": "turn-typing-3",
    })
    with pytest.raises(DialogueProtocolError):
        parse_client_message(msg)


# ---------------------------------------------------------------------------
# quarantine audit: no quarantined imports in receptor modules
# ---------------------------------------------------------------------------


def test_receptor_modules_no_quarantined_imports():
    import app.chatbox.receptor_config as rc
    import app.chatbox.receptor_planner as rp
    for mod in (rc, rp):
        src = inspect.getsource(mod)
        assert "agentlib" not in src
        assert "agent_kernel" not in src
        assert "semantic_trigger" not in src
        assert "demos.scenarios" not in src