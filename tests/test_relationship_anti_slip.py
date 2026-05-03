from src.relationship.relationship_engine import apply_dependency_guard


def test_dependency_risk_raises_boundaries_and_reduces_approach():
    base = {
        "boundary_sensitivity": 0.2,
        "carefulness": 0.2,
        "distance_preference": 0.2,
        "permission_to_approach": 0.6,
    }
    out = apply_dependency_guard(base, dependency_risk=0.9)
    assert out["boundary_sensitivity"] > base["boundary_sensitivity"]
    assert out["carefulness"] > base["carefulness"]
    assert out["distance_preference"] > base["distance_preference"]
    assert out["permission_to_approach"] < base["permission_to_approach"]
