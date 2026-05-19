from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


FIELD_DIMENSION = 10


@dataclass
class FieldDynamicsConfig:
    M: np.ndarray
    C: np.ndarray
    K: np.ndarray
    B: np.ndarray
    dt_max: float
    V_max: float
    A_max: float
    overshoot_max: np.ndarray

    def __post_init__(self) -> None:
        self.M = _array10(self.M, "M")
        self.C = _array10(self.C, "C")
        self.K = _array10(self.K, "K")
        self.B = _array10(self.B, "B")
        self.overshoot_max = _overshoot_array10(self.overshoot_max)
        self.dt_max = _finite_positive_scalar(self.dt_max, "dt_max")
        self.V_max = _finite_positive_scalar(self.V_max, "V_max")
        self.A_max = _finite_positive_scalar(self.A_max, "A_max")
        self.validate()

    def validate(self) -> None:
        _require_shape10(self.M, "M")
        _require_shape10(self.C, "C")
        _require_shape10(self.K, "K")
        _require_shape10(self.B, "B")
        _require_shape10(self.overshoot_max, "overshoot_max")

        for name, value in (
            ("M", self.M),
            ("C", self.C),
            ("K", self.K),
            ("B", self.B),
            ("overshoot_max", self.overshoot_max),
        ):
            if not np.all(np.isfinite(value)):
                raise ValueError(f"{name} must not contain NaN or Inf")

        if not np.all(self.M > 0.0):
            raise ValueError("M must be > 0")
        if not np.all(self.C >= 0.0):
            raise ValueError("C must be >= 0")
        if not np.all(self.K >= 0.0):
            raise ValueError("K must be >= 0")
        if not np.all((0.0 <= self.B) & (self.B <= 1.0)):
            raise ValueError("B must be in [0, 1]")
        if not np.all(self.overshoot_max >= 0.0):
            raise ValueError("overshoot_max must be >= 0")

        _finite_positive_scalar(self.dt_max, "dt_max")
        _finite_positive_scalar(self.V_max, "V_max")
        _finite_positive_scalar(self.A_max, "A_max")


@dataclass
class FieldDynamicsState:
    F_tilde: np.ndarray
    V: np.ndarray

    def __post_init__(self) -> None:
        self.F_tilde = _array10(self.F_tilde, "F_tilde")
        self.V = _array10(self.V, "V")
        self.validate()

    def validate(self) -> None:
        _require_shape10(self.F_tilde, "F_tilde")
        _require_shape10(self.V, "V")
        if not np.all(np.isfinite(self.F_tilde)):
            raise ValueError("F_tilde must not contain NaN or Inf")
        if not np.all(np.isfinite(self.V)):
            raise ValueError("V must not contain NaN or Inf")


@dataclass
class FieldDynamicsInput:
    U_t: np.ndarray
    dt: float

    def __post_init__(self) -> None:
        self.U_t = _array10(self.U_t, "U_t")
        self.dt = _finite_scalar(self.dt, "dt")
        self.validate()

    def validate(self) -> None:
        _require_shape10(self.U_t, "U_t")
        if not np.all(np.isfinite(self.U_t)):
            raise ValueError("U_t must not contain NaN or Inf")
        if self.dt <= 0.0:
            raise ValueError("dt must be strictly positive")


@dataclass
class FieldDynamicsOutput:
    F_bounded: np.ndarray
    V: np.ndarray
    A: np.ndarray
    tension_metrics: dict[str, float] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.F_bounded = _array10(self.F_bounded, "F_bounded")
        self.V = _array10(self.V, "V")
        self.A = _array10(self.A, "A")
        self.tension_metrics = dict(self.tension_metrics)
        self.trace = _copy_trace(self.trace)


def _array10(value: Any, name: str) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.shape != (FIELD_DIMENSION,):
        raise ValueError(f"{name} must have shape ({FIELD_DIMENSION},), got {array.shape}")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must not contain NaN or Inf")
    return array.copy()


def _overshoot_array10(value: Any) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.shape == ():
        array = np.full(FIELD_DIMENSION, float(array), dtype=float)
    elif array.shape != (FIELD_DIMENSION,):
        raise ValueError(f"overshoot_max must be scalar-like or shape ({FIELD_DIMENSION},), got {array.shape}")
    if not np.all(np.isfinite(array)):
        raise ValueError("overshoot_max must not contain NaN or Inf")
    return array.copy()


def _require_shape10(value: np.ndarray, name: str) -> None:
    if value.shape != (FIELD_DIMENSION,):
        raise ValueError(f"{name} must have shape ({FIELD_DIMENSION},), got {value.shape}")


def _finite_scalar(value: Any, name: str) -> float:
    scalar = float(value)
    if not np.isfinite(scalar):
        raise ValueError(f"{name} must not be NaN or Inf")
    return scalar


def _finite_positive_scalar(value: Any, name: str) -> float:
    scalar = _finite_scalar(value, name)
    if scalar <= 0.0:
        raise ValueError(f"{name} must be > 0")
    return scalar


def _copy_trace(trace: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in trace.items():
        if isinstance(value, np.ndarray):
            result[key] = value.copy()
        elif isinstance(value, dict):
            result[key] = _copy_trace(value)
        else:
            result[key] = value
    return result
