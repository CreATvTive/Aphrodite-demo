"""P3 task-card 8: registry-driven program-side expression receptor planner.

Given an abstract *receptor vector* (a gated, expressible field projection) the
planner produces a frozen, executable [`ReceptorPlan`](receptor_planner.py) for
one dialogue turn.  The plan carries:

* reply-delay distribution parameters (mean + variance) and a sampled delay;
* a target length tendency (prompt-side + post-hoc observation band);
* a message-splitting plan (segment count) produced by the server, not by
  accidental ``\\n\\n`` formatting;
* a punctuation-looseness style index that flows into the style instruction
  only (never into JSON or internal structure);
* a typewriter per-character stream interval (ms) sent to the browser;
* an expression-pressure program receptor output in [0,1] (observable; this
  card does **not** implement P4 ``P_talk`` accumulation/trigger/caps).

Key contracts (frozen by [`docs/chatbox/phase-plan-v0.md`](../../docs/chatbox/phase-plan-v0.md)
C.5 + task-card 8):

* the planner input is an **abstract receptor vector** — a sequence of finite
  floats in [-1,1] that already passed the expression gate.  It never receives
  dim ids, temporary labels, attractor/OU/baseline values, or causal
  explanations.  It dynamically traverses whatever vector it gets; 0/1/12/17
  dimensions and missing/unknown entries are all safe.
* mean and variance are **independently** controllable.  "Hesitation" is
  expressed as higher **variance** at approximately the same mean, not as a
  longer fixed pause.  The statistical test compares two plans with
  near-equal means and asserts the high-hesitation group has materially
  higher observed variance.
* production draws randomly but always within the centralized bounds in
  [`receptor_config`](receptor_config.py).  Identical seed+clock reproduces
  exactly.
* the plan is frozen per turn with a stable plan id; only user-safe execution
  parameters are exposed to the client (delay level / typewriter ms), never
  internal成因.
* the planner never imports the provider, the writer, the runtime, or any
  quarantined code.  It is pure functions + a seeded RNG so tests are
  deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import secrets
from typing import Sequence

from app.chatbox.receptor_config import (
    DELAY_CEILING_SECONDS,
    DELAY_FLOOR_SECONDS,
    DELAY_HESITATION_VARIANCE_SECONDS,
    DELAY_MAX_SECONDS,
    DELAY_MEAN_SECONDS,
    DELAY_MIN_SECONDS,
    DELAY_VARIANCE_SECONDS,
    EXPRESSION_PRESSURE_DEFAULT,
    EXPRESSION_PRESSURE_MAX,
    EXPRESSION_PRESSURE_MIN,
    LENGTH_MAX_CHARS,
    LENGTH_MIN_CHARS,
    LENGTH_TARGET_CHARS,
    LENGTH_TOLERANCE_CHARS,
    PUNCTUATION_LOOSENESS_DEFAULT,
    PUNCTUATION_LOOSENESS_MAX,
    PUNCTUATION_LOOSENESS_MIN,
    RECEPTOR_PLAN_VERSION,
    SPLIT_MAX_SEGMENTS,
    SPLIT_MIN_SEGMENTS,
    SPLIT_TARGET_SEGMENTS,
    TYPEWRITER_DEFAULT_MS,
    TYPEWRITER_MAX_MS,
    TYPEWRITER_MIN_MS,
    clamp01,
    clamp_range,
)


class ReceptorPlannerError(ValueError):
    """Stable planner-level error (invalid input or sampling failure)."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _finite(x: float, label: str) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ReceptorPlannerError("invalid_input", f"{label} must be a finite number")
    if not math.isfinite(float(x)):
        raise ReceptorPlannerError("invalid_input", f"{label} must be finite")
    return float(x)


def _validate_receptor_vector(values: Sequence[float]) -> tuple[float, ...]:
    """Return a validated tuple of finite floats in [-1,1].

    Empty, 1, 12, 17, or any other count is accepted.  Non-finite, bool, or
    out-of-range entries are rejected fail-closed.
    """
    if isinstance(values, (str, bytes)):
        raise ReceptorPlannerError("invalid_input", "receptor vector must be a sequence of numbers")
    try:
        seq = list(values)
    except TypeError as exc:
        raise ReceptorPlannerError("invalid_input", "receptor vector must be iterable") from exc
    out: list[float] = []
    for index, raw in enumerate(seq):
        v = _finite(raw, f"receptor[{index}]")
        if v < -1.0 or v > 1.0:
            raise ReceptorPlannerError(
                "invalid_input", f"receptor[{index}]={v} out of [-1,1]"
            )
        out.append(v)
    return tuple(out)


def _mean(values: tuple[float, ...]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _abs_mean(values: tuple[float, ...]) -> float:
    if not values:
        return 0.0
    return sum(abs(v) for v in values) / len(values)


def _variance(values: tuple[float, ...]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return sum((v - m) ** 2 for v in values) / len(values)


class _SeededRng:
    """Deterministic Gaussian + uniform RNG (Box–Muller, stdlib only).

    Reproduces exactly for a given seed.  Never uses ``random`` so production
    and tests share one code path.
    """

    __slots__ = ("_state",)

    def __init__(self, seed: int) -> None:
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise ReceptorPlannerError("invalid_input", "seed must be an int")
        # Mix the seed into a 64-bit state.
        h = hashlib.sha256(f"receptor:{seed}".encode("utf-8")).digest()
        self._state = int.from_bytes(h[:8], "big") & ((1 << 64) - 1)

    def _next_u64(self) -> int:
        # xorshift64*
        x = self._state
        x ^= (x >> 12) & ((1 << 64) - 1)
        x ^= (x << 25) & ((1 << 64) - 1)
        x ^= (x >> 27) & ((1 << 64) - 1)
        self._state = x & ((1 << 64) - 1)
        return (x * 0x2545F4914F6CDD1D) & ((1 << 64) - 1)

    def uniform(self) -> float:
        return (self._next_u64() >> 11) * (1.0 / (1 << 53))

    def gaussian(self) -> float:
        # Box–Muller; keep two but only expose one at a time for simplicity.
        while True:
            u1 = self.uniform()
            u2 = self.uniform()
            if u1 > 1e-12:
                break
        r = math.sqrt(-2.0 * math.log(u1))
        return r * math.cos(2.0 * math.pi * u2)


def _truncated_normal(rng: _SeededRng, mean: float, std: float, lo: float, hi: float) -> float:
    """Sample a truncated normal in [lo, hi] via rejection (bounded loops)."""
    if std <= 0.0:
        return clamp_range(mean, lo, hi, "truncated_normal")
    for _ in range(64):
        x = mean + std * rng.gaussian()
        if lo <= x <= hi:
            return x
    # Fall back to the nearest bound after 64 rejects (pathological std).
    return clamp_range(mean, lo, hi, "truncated_normal.fallback")


@dataclass(frozen=True, slots=True)
class ReceptorPlan:
    """Frozen, executable receptor plan for one dialogue turn.

    All fields are user-safe to expose except ``receptor_summary`` which is a
    private audit digest (never sent to the client or provider prompt).
    """

    plan_id: str
    version: str
    turn_id: str
    # Delay distribution parameters (seconds).  ``delay_sample_seconds`` is the
    # concrete draw the server will wait before streaming the first segment.
    delay_mean_seconds: float
    delay_variance_seconds: float
    delay_sample_seconds: float
    # Length tendency (characters).  ``length_target`` flows into the style
    # instruction; ``length_tolerance`` is the post-hoc observation band.
    length_target_chars: int
    length_tolerance_chars: int
    # Server-produced split plan.
    segment_count: int
    # Punctuation looseness style index in [0,1] (style instruction only).
    punctuation_looseness: float
    # Typewriter per-character stream interval (ms) sent to the browser.
    typewriter_ms: int
    # Expression pressure program receptor output in [0,1].
    expression_pressure: float
    # Private audit digest of the receptor vector (SHA-256, first 16 hex).
    # Never exposed to the client or provider prompt.
    receptor_summary: str

    def public_frame(self) -> dict:
        """Return only user-safe execution parameters for the client."""
        return {
            "plan_id": self.plan_id,
            "version": self.version,
            "turn_id": self.turn_id,
            "delay_level": _delay_level(self.delay_sample_seconds),
            "typewriter_ms": int(self.typewriter_ms),
            "segment_count": int(self.segment_count),
        }

    def audit_record(self) -> dict:
        """Return a private, append-only audit record (no dim ids/values)."""
        return {
            "plan_id": self.plan_id,
            "turn_id": self.turn_id,
            "version": self.version,
            "delay_mean_seconds": self.delay_mean_seconds,
            "delay_variance_seconds": self.delay_variance_seconds,
            "delay_sample_seconds": self.delay_sample_seconds,
            "length_target_chars": self.length_target_chars,
            "segment_count": self.segment_count,
            "punctuation_looseness": self.punctuation_looseness,
            "typewriter_ms": self.typewriter_ms,
            "expression_pressure": self.expression_pressure,
            "receptor_summary": self.receptor_summary,
        }


def _delay_level(sample: float) -> str:
    if sample < 0.6:
        return "quick"
    if sample < 1.8:
        return "normal"
    if sample < 3.5:
        return "slow"
    return "held"


def _plan_id(turn_id: str, receptor_summary: str, clock_ns: int) -> str:
    h = hashlib.sha256(
        f"{turn_id}|{receptor_summary}|{clock_ns}".encode("utf-8")
    ).hexdigest()
    return f"plan-{h[:24]}"


def _receptor_summary(values: tuple[float, ...]) -> str:
    h = hashlib.sha256(repr(values).encode("utf-8")).hexdigest()
    return h[:16]


def plan_from_receptor_vector(
    *,
    turn_id: str,
    receptor_vector: Sequence[float],
    clock_ns: int,
    seed: int | None = None,
    hesitation: float = 0.0,
) -> ReceptorPlan:
    """Produce a frozen [`ReceptorPlan`](receptor_planner.py) for one turn.

    ``receptor_vector`` is an abstract sequence of finite floats in [-1,1]
    (the gated, expressible projection).  ``hesitation`` in [0,1] raises the
    delay **variance** independently of the mean so "犹豫=方差" is measurable.
    ``seed`` makes the draw deterministic; when omitted a random seed is used
    (production path).
    """
    if not isinstance(turn_id, str) or not turn_id:
        raise ReceptorPlannerError("invalid_input", "turn_id must be a non-empty string")
    if not isinstance(clock_ns, int) or isinstance(clock_ns, bool) or clock_ns < 0:
        raise ReceptorPlannerError("invalid_input", "clock_ns must be a non-negative int")
    values = _validate_receptor_vector(receptor_vector)
    hesitation = clamp01(hesitation)

    # --- derive plan parameters from the abstract vector -------------------
    center = _mean(values)
    activity = _abs_mean(values)
    spread = math.sqrt(_variance(values))

    # Delay mean: gentle function of activity (more active → quicker).
    # Bounded into [DELAY_MIN_SECONDS, DELAY_MAX_SECONDS].
    base_mean = DELAY_MEAN_SECONDS - 0.6 * activity
    delay_mean = clamp_range(base_mean, DELAY_MIN_SECONDS, DELAY_MAX_SECONDS, "delay_mean")

    # Delay variance: hesitation raises variance independently of mean.
    # Map hesitation in [0,1] to [DELAY_VARIANCE_SECONDS, HESITATION_VARIANCE].
    var_lo = DELAY_VARIANCE_SECONDS
    var_hi = DELAY_HESITATION_VARIANCE_SECONDS
    delay_variance = var_lo + (var_hi - var_lo) * hesitation
    # Also let vector spread nudge variance a little, but keep hesitation dominant.
    delay_variance = clamp_range(
        delay_variance + 0.2 * spread, 1e-3, var_hi * 1.5, "delay_variance"
    )

    std = math.sqrt(delay_variance)

    # Length target: more active/positive → longer, but bounded.
    length_target = int(
        clamp_range(
            LENGTH_TARGET_CHARS + 220.0 * max(0.0, center) + 140.0 * activity,
            LENGTH_MIN_CHARS,
            LENGTH_MAX_CHARS,
            "length_target",
        )
    )
    length_tolerance = LENGTH_TOLERANCE_CHARS

    # Segment count: spread + length push toward splitting, bounded.
    seg_float = SPLIT_TARGET_SEGMENTS + 1.2 * spread + (length_target / 600.0)
    segment_count = int(
        clamp_range(round(seg_float), SPLIT_MIN_SEGMENTS, SPLIT_MAX_SEGMENTS, "segment_count")
    )

    # Punctuation looseness: higher activity → looser; bounded [0,1].
    punctuation_looseness = clamp01(
        PUNCTUATION_LOOSENESS_DEFAULT + 0.3 * activity - 0.2 * max(0.0, -center)
    )

    # Typewriter ms: higher activity → quicker stream; bounded.
    tw = TYPEWRITER_DEFAULT_MS - 18.0 * activity + 10.0 * hesitation
    typewriter_ms = int(
        clamp_range(round(tw), TYPEWRITER_MIN_MS, TYPEWRITER_MAX_MS, "typewriter_ms")
    )

    # Expression pressure: activity + center + hesitation, bounded [0,1].
    expression_pressure = clamp01(
        0.25 * activity + 0.35 * max(0.0, center) + 0.20 * hesitation
        + EXPRESSION_PRESSURE_DEFAULT * 0.2
    )

    # --- sample the concrete delay -----------------------------------------
    if seed is None:
        seed = int.from_bytes(secrets.token_bytes(8), "big")
    rng = _SeededRng(seed)
    delay_sample = _truncated_normal(
        rng, delay_mean, std, DELAY_FLOOR_SECONDS, DELAY_CEILING_SECONDS
    )
    # Final safety: the sample must be within the protocol ceiling/floor.
    delay_sample = clamp_range(
        delay_sample, DELAY_FLOOR_SECONDS, DELAY_CEILING_SECONDS, "delay_sample"
    )

    receptor_summary = _receptor_summary(values)
    plan_id = _plan_id(turn_id, receptor_summary, clock_ns)
    return ReceptorPlan(
        plan_id=plan_id,
        version=RECEPTOR_PLAN_VERSION,
        turn_id=turn_id,
        delay_mean_seconds=delay_mean,
        delay_variance_seconds=delay_variance,
        delay_sample_seconds=delay_sample,
        length_target_chars=length_target,
        length_tolerance_chars=length_tolerance,
        segment_count=segment_count,
        punctuation_looseness=punctuation_looseness,
        typewriter_ms=typewriter_ms,
        expression_pressure=expression_pressure,
        receptor_summary=receptor_summary,
    )


def split_reply_by_plan(reply_text: str, segment_count: int) -> tuple[str, ...]:
    """Split a reply into ``segment_count`` server-planned segments.

    Existing explicit ``\\n\\n`` boundaries are respected first; if there are
    fewer explicit boundaries than requested, the remainder is split on
    sentence boundaries (。！？.!?); if still insufficient, on length.  Always
    returns between 1 and ``segment_count`` non-empty segments.
    """
    if not isinstance(reply_text, str):
        raise ReceptorPlannerError("invalid_input", "reply_text must be a string")
    if (
        not isinstance(segment_count, int)
        or isinstance(segment_count, bool)
        or segment_count < SPLIT_MIN_SEGMENTS
        or segment_count > SPLIT_MAX_SEGMENTS
    ):
        raise ReceptorPlannerError(
            "invalid_input", f"segment_count must be in [{SPLIT_MIN_SEGMENTS},{SPLIT_MAX_SEGMENTS}]"
        )
    normalized = reply_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return ()
    # 1. Explicit double-newline paragraphs.  Respect ALL explicit boundaries;
    #    the plan's segment_count is a target, not a cap that drops content.
    paragraphs = tuple(p.strip() for p in normalized.split("\n\n") if p.strip())
    if len(paragraphs) >= segment_count:
        return paragraphs
    # 2. Sentence split of the whole text.
    if segment_count > 1:
        sentences: list[str] = []
        buf: list[str] = []
        for ch in normalized:
            buf.append(ch)
            if ch in "。！？.!?":
                sentences.append("".join(buf).strip())
                buf = []
        if buf:
            sentences.append("".join(buf).strip())
        sentences = [s for s in sentences if s]
        if len(sentences) >= segment_count:
            # Merge tail into the last requested segment to avoid tiny tails.
            out: list[str] = []
            for i in range(segment_count - 1):
                out.append(sentences[i])
            out.append("".join(sentences[segment_count - 1:]))
            return tuple(out)
        # 3. Length split fallback.
        if len(sentences) <= 1:
            target = max(1, len(normalized) // segment_count)
            parts = [normalized[i:i + target] for i in range(0, len(normalized), target)]
            parts = [p for p in parts if p]
            if len(parts) > segment_count:
                merged = parts[:segment_count - 1] + ["".join(parts[segment_count - 1:])]
                return tuple(merged)
            return tuple(parts) or (normalized,)
    return paragraphs or (normalized,)


def style_instruction_from_plan(plan: ReceptorPlan) -> str:
    """Build a non-diagnostic style instruction fragment from a plan.

    Contains no dim id, label, value, threshold, mechanism name, or causal
    explanation.  Only describes *how* to speak.
    """
    if plan.length_target_chars <= 60:
        length_clause = "保持简短。"
    elif plan.length_target_chars <= 320:
        length_clause = "适度展开。"
    else:
        length_clause = "可以从容展开，但不要变成说明文。"
    if plan.punctuation_looseness < 0.33:
        punct_clause = "标点收得紧一些，句子短而稳。"
    elif plan.punctuation_looseness < 0.66:
        punct_clause = "标点节奏自然。"
    else:
        punct_clause = "标点可以松一些，允许停顿和余韵。"
    return f"表达方式：{length_clause}{punct_clause}"
