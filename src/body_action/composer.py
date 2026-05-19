from __future__ import annotations

from src.body_action.schema import (
    ActionSequenceHint,
    BodyActionComposition,
    BodyActionWeight,
    BodyActionWeights,
)


BAND_STRENGTH: dict[str, int] = {
    "off": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
}

ACTION_SEQUENCE_ORDER: dict[str, int] = {
    "pause": 0,
    "look_down": 1,
    "look_to_user": 1,
    "look_away": 1,
    "reset_posture": 2,
    "slight_forward": 3,
    "slight_withdraw": 3,
    "maintain_distance": 4,
    "stillness": 4,
    "reduce_motion": 4,
}

PRIMARY_PRIORITY: tuple[str, ...] = (
    "pause",
    "stillness",
    "reduce_motion",
    "look_away",
    "look_down",
    "look_to_user",
    "maintain_distance",
    "slight_withdraw",
    "reset_posture",
    "slight_forward",
)

PROVENANCE = "BodyActionWeights->BodyActionComposition v0"


class BodyActionComposer:
    def compose(self, action_weights: BodyActionWeights) -> BodyActionComposition:
        if not isinstance(action_weights, BodyActionWeights):
            raise TypeError("action_weights must be a BodyActionWeights instance")

        by_name = {weight.action_name: weight for weight in action_weights.weights}
        constraints = _collect_constraints(action_weights.weights)
        suppressed_actions = _suppressed_actions(action_weights.weights, constraints)

        primary_names = _select_primary_actions(by_name, suppressed_actions)
        secondary_names = _select_secondary_actions(by_name, primary_names, suppressed_actions)

        completion = _completion_mode(by_name, suppressed_actions)
        primary_actions = [
            _hint(by_name[name], completion)
            for name in _sort_sequence(primary_names)
        ]
        secondary_actions = [
            _hint(by_name[name], completion)
            for name in _sort_sequence(secondary_names)
        ]

        return BodyActionComposition(
            primary_actions=primary_actions,
            secondary_actions=secondary_actions,
            suppressed_actions=suppressed_actions,
            hard_constraints=constraints,
            source_weights=list(action_weights.weights),
            composition_note=_composition_note(action_weights, primary_names, secondary_names, suppressed_actions),
            behavior_affecting=False,
        )

    def map(self, action_weights: BodyActionWeights) -> BodyActionComposition:
        return self.compose(action_weights)


def _select_primary_actions(
    by_name: dict[str, BodyActionWeight],
    suppressed_actions: list[str],
) -> list[str]:
    active = [
        name
        for name, weight in by_name.items()
        if BAND_STRENGTH[weight.weight] >= BAND_STRENGTH["medium"]
        and name not in suppressed_actions
    ]

    active = _resolve_primary_conflicts(active, by_name)

    if (
        "reset_posture" in active
        and by_name["reset_posture"].weight == "medium"
        and _has_posture_anchor(set(active) - {"reset_posture"})
    ):
        active.remove("reset_posture")

    return sorted(
        active,
        key=lambda name: (
            -BAND_STRENGTH[by_name[name].weight],
            PRIMARY_PRIORITY.index(name) if name in PRIMARY_PRIORITY else len(PRIMARY_PRIORITY),
        ),
    )


def _select_secondary_actions(
    by_name: dict[str, BodyActionWeight],
    primary_names: list[str],
    suppressed_actions: list[str],
) -> list[str]:
    primary = set(primary_names)
    secondary: list[str] = []

    for name, weight in by_name.items():
        if name in primary or name in suppressed_actions:
            continue
        if weight.weight == "low":
            secondary.append(name)
        elif name == "reset_posture" and weight.weight == "medium" and _has_posture_anchor(primary):
            secondary.append(name)
        elif name == "look_down" and weight.weight == "medium" and _has_restraint_anchor(primary):
            secondary.append(name)

    secondary = _resolve_secondary_conflicts(secondary, primary, by_name)
    return sorted(secondary, key=lambda name: ACTION_SEQUENCE_ORDER.get(name, 9))


def _resolve_primary_conflicts(
    active: list[str],
    by_name: dict[str, BodyActionWeight],
) -> list[str]:
    result = set(active)

    _keep_stronger_pair(result, by_name, "look_to_user", "look_away", tie_keep="look_away")
    _keep_stronger_pair(result, by_name, "slight_forward", "slight_withdraw", tie_keep="slight_withdraw")

    if "stillness" in result and "slight_forward" in result:
        result.remove("slight_forward")

    if "reduce_motion" in result:
        for micro_action in ("look_down", "look_away", "look_to_user", "reset_posture"):
            if micro_action in active and by_name[micro_action].weight == "medium":
                result.add(micro_action)

    return list(result)


def _resolve_secondary_conflicts(
    secondary: list[str],
    primary: set[str],
    by_name: dict[str, BodyActionWeight],
) -> list[str]:
    result = set(secondary)

    if "look_to_user" in primary:
        result.discard("look_away")
    if "look_away" in primary:
        result.discard("look_to_user")
    if "slight_forward" in primary:
        result.discard("slight_withdraw")
    if "slight_withdraw" in primary:
        result.discard("slight_forward")
    if "stillness" in primary and "slight_forward" in result and by_name["slight_forward"].weight != "low":
        result.discard("slight_forward")

    return list(result)


def _keep_stronger_pair(
    actions: set[str],
    by_name: dict[str, BodyActionWeight],
    left: str,
    right: str,
    tie_keep: str,
) -> None:
    if left not in actions or right not in actions:
        return

    left_strength = BAND_STRENGTH[by_name[left].weight]
    right_strength = BAND_STRENGTH[by_name[right].weight]
    if left_strength > right_strength:
        actions.discard(right)
    elif right_strength > left_strength:
        actions.discard(left)
    else:
        actions.discard(right if tie_keep == left else left)


def _hint(weight: BodyActionWeight, completion: str) -> ActionSequenceHint:
    return ActionSequenceHint(
        action_name=weight.action_name,
        order=ACTION_SEQUENCE_ORDER.get(weight.action_name, 9),
        duration_hint=_duration_hint(weight),
        completion=completion,
        constraints=list(weight.constraints),
        provenance=[PROVENANCE],
        behavior_affecting=False,
    )


def _duration_hint(weight: BodyActionWeight) -> str:
    name = weight.action_name
    if name == "pause":
        return "sustained" if weight.weight == "high" else "medium"
    if name in {"look_down", "look_to_user", "look_away"}:
        return "short"
    if name in {"slight_forward", "slight_withdraw"}:
        return "medium" if weight.weight == "high" else "short"
    if name in {"maintain_distance", "stillness", "reduce_motion"}:
        return "sustained"
    if name == "reset_posture":
        return "short"
    return "instant"


def _completion_mode(
    by_name: dict[str, BodyActionWeight],
    suppressed_actions: list[str],
) -> str:
    reduce_motion = by_name.get("reduce_motion")
    stillness = by_name.get("stillness")
    if (
        reduce_motion is not None and reduce_motion.weight == "high"
    ) or (
        stillness is not None and stillness.weight == "high"
    ):
        return "restrained"

    off_with_constraints = [
        weight for weight in by_name.values()
        if weight.weight == "off" and weight.constraints
    ]
    if len(suppressed_actions) >= 2 or len(off_with_constraints) >= 3:
        return "partial"

    return "complete"


def _suppressed_actions(
    weights: list[BodyActionWeight],
    constraints: list[str],
) -> list[str]:
    suppressed: list[str] = []
    constraint_set = set(constraints)
    by_name = {weight.action_name: weight for weight in weights}

    if _is_off(by_name, "slight_forward") and constraint_set.intersection({
        "no_approach_step",
        "no_forward_lean",
        "no_welcoming_gesture",
        "no_service_gesture",
        "no_seductive_expression",
    }):
        suppressed.append("slight_forward")

    if _is_off(by_name, "look_to_user") and constraint_set.intersection({
        "no_cute_head_tilt",
        "no_welcoming_gesture",
        "no_seductive_expression",
        "expression_suppressed",
    }):
        suppressed.append("look_to_user")

    if _is_off(by_name, "look_away") and "look_to_user" in by_name and by_name["look_to_user"].weight != "off":
        suppressed.append("look_away")

    return [name for name in ACTION_SEQUENCE_ORDER if name in suppressed]


def _is_off(by_name: dict[str, BodyActionWeight], action_name: str) -> bool:
    weight = by_name.get(action_name)
    return weight is not None and weight.weight == "off"


def _collect_constraints(weights: list[BodyActionWeight]) -> list[str]:
    constraints: list[str] = []
    for weight in weights:
        for constraint in weight.constraints:
            if constraint not in constraints:
                constraints.append(constraint)
    return constraints


def _has_posture_anchor(primary: set[str]) -> bool:
    return bool(primary.intersection({"maintain_distance", "reduce_motion", "stillness"}))


def _has_restraint_anchor(primary: set[str]) -> bool:
    return bool(primary.intersection({"reduce_motion", "stillness", "pause"}))


def _sort_sequence(action_names: list[str]) -> list[str]:
    return sorted(action_names, key=lambda name: (ACTION_SEQUENCE_ORDER.get(name, 9), name))


def _composition_note(
    action_weights: BodyActionWeights,
    primary_names: list[str],
    secondary_names: list[str],
    suppressed_actions: list[str],
) -> str:
    offset_note = "offsets=none"
    offsets = action_weights.body_part_offsets
    if offsets is not None:
        offset_note = (
            "offsets="
            f"gaze:{offsets.gaze_offset_ms}ms,"
            f"head:{offsets.head_offset_ms}ms,"
            f"shoulder:{offsets.shoulder_offset_ms}ms,"
            f"hand:{offsets.hand_offset_ms}ms"
        )

    return (
        f"{PROVENANCE}; "
        f"primary={','.join(_sort_sequence(primary_names)) or 'none'}; "
        f"secondary={','.join(_sort_sequence(secondary_names)) or 'none'}; "
        f"suppressed={','.join(suppressed_actions) or 'none'}; "
        f"{offset_note}"
    )


__all__ = [
    "BodyActionComposer",
]
