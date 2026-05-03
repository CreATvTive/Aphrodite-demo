from __future__ import annotations


def mix_action_weights(weights: dict, body_influence: dict) -> dict:
    out = dict(weights)

    # gaze exclusivity
    gaze_keys = ["gaze_user", "gaze_down", "gaze_away"]
    gaze_total = sum(max(0.0, out.get(k, 0.0)) for k in gaze_keys)
    if gaze_total > 1.0:
        for k in gaze_keys:
            out[k] = max(0.0, out.get(k, 0.0)) / gaze_total

    # posture conflict: lean_forward vs posture_withdraw
    lean = max(0.0, out.get("lean_forward", 0.0))
    withdraw = max(0.0, out.get("posture_withdraw", 0.0))
    if lean > 0 and withdraw > 0:
        if withdraw >= lean:
            out["lean_forward"] = 0.0
        else:
            out["posture_withdraw"] = 0.0

    suppression = max(0.0, min(1.0, body_influence.get("motion_suppression", 0.0)))
    if suppression > 0.6:
        out["micro_smile"] = min(out.get("micro_smile", 0.0), 0.2)
        out["hand_pause"] = max(out.get("hand_pause", 0.0), 0.5)

    return out
