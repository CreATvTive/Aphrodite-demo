"""P3 task-card 8: centralized, reversible expression-receptor calibration.

All bounds and default ranges here are ⏳ calibration defaults frozen by
[`docs/chatbox/phase-plan-v0.md`](../../docs/chatbox/phase-plan-v0.md) section A
("语言受体 v0").  They are intentionally small, stable, and reversible so a
later v0.x revisit may retune them in one place.  No value here is a frozen
persona truth.

This module never imports the provider, the writer, the runtime, or any
quarantined code.  It is pure data + helpers so the receptor planner and its
tests can reason about it offline.

Statistical contract (frozen by task-card 8):

* ``DELAY_MEAN_SECONDS`` / ``DELAY_VARIANCE_SECONDS`` bound the reply-delay
  distribution.  Mean and variance are *independently* controllable so
  "hesitation" is expressed as higher **variance** at approximately the same
  mean, not as a longer fixed pause.
* ``TYPEWRITER_MS`` bounds the per-character stream interval sent to the
  browser.  Reduced-motion clients ignore it and render immediately.
* ``LENGTH_TARGET_CHARS`` / ``LENGTH_TOLERANCE_CHARS`` bound the prompt-side
  length tendency and the post-hoc observation band.
* ``SPLIT_MAX_SEGMENTS`` caps message splitting to avoid flooding.
* ``PUNCTUATION_LOOSENESS`` is a [0,1] style index that flows into the style
  instruction only; it never alters JSON or internal structure.
* ``EXPRESSION_PRESSURE`` is a program-side receptor output in [0,1]; it is
  observable but this card does **not** implement P4 ``P_talk`` accumulation,
  triggering, or hard caps.
"""

from __future__ import annotations

import math


RECEPTOR_PLAN_VERSION = "aphrodite.chatbox.receptor-plan/1"

# -- Reply-delay distribution (seconds) -------------------------------------
# Truncated normal over [DELAY_MIN_SECONDS, DELAY_MAX_SECONDS].  Mean and
# variance are independent inputs; the planner samples with a seeded RNG so
# identical seed+clock reproduces, while production draws randomly but always
# within these bounds.
DELAY_MIN_SECONDS = 0.18
DELAY_MAX_SECONDS = 6.0
DELAY_MEAN_SECONDS = 1.2          # default center
DELAY_VARIANCE_SECONDS = 0.36     # default variance (std ~= 0.6s)
DELAY_HESITATION_VARIANCE_SECONDS = 1.44  # high-hesitation variance (std ~= 1.2s)
# A safety cap on a single sampled delay so a pathological draw cannot stall
# the turn; the truncation already bounds this, but we keep an explicit floor
# and ceiling for protocol validation.
DELAY_FLOOR_SECONDS = 0.0
DELAY_CEILING_SECONDS = 12.0

# -- Typewriter stream rate (milliseconds per character) --------------------
TYPEWRITER_MIN_MS = 16
TYPEWRITER_MAX_MS = 140
TYPEWRITER_DEFAULT_MS = 42

# -- Length tendency (characters) -------------------------------------------
LENGTH_MIN_CHARS = 12
LENGTH_MAX_CHARS = 1200
LENGTH_TARGET_CHARS = 180
LENGTH_TOLERANCE_CHARS = 120

# -- Message splitting ------------------------------------------------------
SPLIT_MIN_SEGMENTS = 1
SPLIT_MAX_SEGMENTS = 4
SPLIT_TARGET_SEGMENTS = 1

# -- Punctuation looseness (style index in [0,1]) --------------------------
PUNCTUATION_LOOSENESS_MIN = 0.0
PUNCTUATION_LOOSENESS_MAX = 1.0
PUNCTUATION_LOOSENESS_DEFAULT = 0.4

# -- Expression pressure (program receptor output in [0,1]) ----------------
EXPRESSION_PRESSURE_MIN = 0.0
EXPRESSION_PRESSURE_MAX = 1.0
EXPRESSION_PRESSURE_DEFAULT = 0.2


def _finite(x: float, label: str) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    if not math.isfinite(float(x)):
        raise ValueError(f"{label} must be finite")
    return float(x)


def clamp01(x: float) -> float:
    x = _finite(x, "clamp01 value")
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def clamp_range(x: float, lo: float, hi: float, label: str) -> float:
    x = _finite(x, label)
    lo = _finite(lo, f"{label}.lo")
    hi = _finite(hi, f"{label}.hi")
    if lo > hi:
        raise ValueError(f"{label} range lo>hi")
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
