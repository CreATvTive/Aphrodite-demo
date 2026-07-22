"""P3 task-card 7: centralized, reversible perception mapping defaults.

All thresholds and magnitudes here are ⏳ calibration defaults frozen by
[`docs/chatbox/phase-plan-v0.md`](../../docs/chatbox/phase-plan-v0.md) section A
("感知信号 v0" / "感知入径").  They are intentionally small, stable, and
explainable so a synthetic event sequence cannot flood the attractor past its
soft-boundary domain.  No value here is a frozen persona truth; every constant
is a reversible local default that a later v0.x revisit may retune.

This module never imports provider, writer, runtime, or quarantined code.  It
is pure data + helpers so the mapping table and tests can reason about it
offline.
"""

from __future__ import annotations


PERCEPTION_EVENT_VERSION = "aphrodite.chatbox.perception-event/1"
PERCEPTION_PERSISTENCE_SCHEMA_VERSION = "aphrodite.chatbox.perception-persistence/1"
PERCEPTION_PERSISTENCE_USER_VERSION = 1

# Source tag carried on every attractor move issued by the perception ingress.
# Distinguishes perception influence from writer influence in the event log
# without reusing the writer source string.
PERCEPTION_SOURCE = "chatbox.perception"

# Per-event per-dimension amplitude cap.  The writer cap (0.3) is the frozen
# C.3 attractor-move bound; perception moves are intentionally far smaller so a
# full five-signal sequence cannot push the attractor outside its closed
# [-1.801, +1.801] baseline-relative domain in one tick.  The runtime still
# validates the displacement and rejects anything out of domain; this cap is a
# perception-side safeguard, not a hard clamp on field state.
PERCEPTION_AMPLITUDE_CAP = 0.05

# Saturation thresholds for the five signals.  These map raw observations to
# normalized intensities in [0, 1]; the mapping table multiplies the intensity
# by per-dimension magnitudes below.  "Saturate" means larger observations do
# not produce larger deltas — they clip to the saturation value, which is the
# only clipping the perception layer does (on its own internal intensity, never
# on field state).
SILENCE_SATURATION_SECONDS = 7200.0  # 2h: P3 gate's "沉默两小时" target
LENGTH_SATURATION_CHARS = 400.0
TYPING_SATURATION_SECONDS = 30.0

# Time-of-day bands (local solar hour, [0, 24)).  Used only to pick which
# small nudge applies; the clock is injected so tests are deterministic.
NIGHT_START_HOUR = 23.0
NIGHT_END_HOUR = 6.0  # exclusive band [23, 6) wraps past midnight

# Per-dimension default magnitudes (intensity-scaled).  Each entry is a
# reversible default; the mapping table reads this dict so retuning lives in
# one place.  dim_id values match the P1 birth registry; the ingress still
# validates against the live registry and skips any dim that is absent.
DEFAULT_MAPPING: dict[str, dict[str, float]] = {
    "message_gap": {
        # Long silence → 朝向你 + 期待 gently up, 愉悦 slightly down.
        "birth_03": 0.04,  # 朝向你
        "birth_09": 0.03,  # 期待
        "birth_05": -0.02,  # 愉悦
    },
    "time_of_day": {
        # Night band → 疲惫 + 沉郁 up, 能量 down.  Day band → the reverse.
        "birth_07": 0.02,  # 疲惫
        "birth_10": 0.02,  # 沉郁
        "birth_00": -0.02,  # 能量
    },
    "message_length": {
        # Long message → 开放 + 好奇 up.  Short → 玩兴 slight up.
        "birth_01": 0.03,  # 开放
        "birth_04": 0.02,  # 好奇
        "birth_11": 0.02,  # 玩兴
    },
    "session_lifecycle": {
        # start → 期待 up; end → 朝向你 down + 沉郁 up.
        "birth_09": 0.03,  # 期待
        "birth_03": -0.02,  # 朝向你
        "birth_10": 0.02,  # 沉郁
    },
    "typing": {
        # Sustained typing → 期待 up + 紧张 slight up.
        "birth_09": 0.02,  # 期待
        "birth_06": 0.01,  # 紧张
    },
}

# Kinds the registry currently knows.  Adding a kind here, in the schema
# payload validators, and in DEFAULT_MAPPING is the only change needed to
# introduce a new signal — the bus transport interface stays stable.
KNOWN_KINDS: tuple[str, ...] = (
    "message_gap",
    "time_of_day",
    "message_length",
    "session_lifecycle",
    "typing",
)

# Bounded queue / replay limits.
BUS_QUEUE_MAX = 256
REPLAY_BATCH_LIMIT = 512

# Typing heartbeat / timeout (seconds).  The server clears a typing state if
# no heartbeat arrives within this window; the client sends heartbeats at
# roughly half this interval.
TYPING_HEARTBEAT_TIMEOUT_SECONDS = 8.0
