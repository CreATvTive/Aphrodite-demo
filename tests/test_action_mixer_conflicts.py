from src.body.action_mixer import mix_action_weights


def test_mixer_resolves_gaze_and_posture_conflicts():
    weights = {
        "gaze_down": 0.8,
        "gaze_user": 0.7,
        "posture_withdraw": 0.6,
        "lean_forward": 0.6,
        "micro_smile": 0.8,
    }
    out = mix_action_weights(weights, {"motion_suppression": 0.9})
    assert out["gaze_down"] + out["gaze_user"] + out.get("gaze_away", 0.0) <= 1.0
    assert out["posture_withdraw"] == 0.6
    assert out["lean_forward"] == 0.0
    assert out["micro_smile"] <= 0.2
