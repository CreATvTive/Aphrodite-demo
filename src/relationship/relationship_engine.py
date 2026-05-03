from __future__ import annotations


def apply_dependency_guard(field: dict, dependency_risk: float) -> dict:
    next_field = dict(field)
    risk = max(0.0, min(1.0, dependency_risk))
    if risk > 0.5:
        next_field["boundary_sensitivity"] = min(1.0, next_field.get("boundary_sensitivity", 0.0) + 0.25 * risk)
        next_field["carefulness"] = min(1.0, next_field.get("carefulness", 0.0) + 0.2 * risk)
        next_field["distance_preference"] = min(1.0, next_field.get("distance_preference", 0.0) + 0.2 * risk)
        next_field["permission_to_approach"] = max(0.0, next_field.get("permission_to_approach", 0.0) - 0.2 * risk)
    return next_field
