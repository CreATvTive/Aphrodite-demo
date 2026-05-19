from __future__ import annotations

import math

import numpy as np

from .schema import (
    FieldDynamicsConfig,
    FieldDynamicsInput,
    FieldDynamicsOutput,
    FieldDynamicsState,
)


class RelationalFieldDynamicsKernel:
    def __init__(self, config: FieldDynamicsConfig, initial_state: FieldDynamicsState):
        if not isinstance(config, FieldDynamicsConfig):
            raise TypeError("config must be a FieldDynamicsConfig instance")
        if not isinstance(initial_state, FieldDynamicsState):
            raise TypeError("initial_state must be a FieldDynamicsState instance")

        config.validate()
        initial_state.validate()
        self.config = _copy_config(config)
        self.state = _copy_state(initial_state)

    def step(self, input_data: FieldDynamicsInput) -> FieldDynamicsOutput:
        if not isinstance(input_data, FieldDynamicsInput):
            raise TypeError("input_data must be a FieldDynamicsInput instance")
        input_data.validate()
        if input_data.dt <= 0.0:
            raise ValueError("dt must be strictly positive")

        original_dt = input_data.dt
        if original_dt > self.config.dt_max:
            num_substeps = math.ceil(original_dt / self.config.dt_max)
            effective_dt = original_dt / num_substeps
        else:
            num_substeps = 1
            effective_dt = original_dt

        U_t = input_data.U_t.copy()
        spring_force_pre = self.config.K * (self.state.F_tilde - self.config.B)
        pre_conflict_tension = float(np.linalg.norm(spring_force_pre - U_t))

        A_current = np.zeros_like(self.state.V)
        overshoot_capped = np.zeros_like(self.state.F_tilde)

        for _ in range(num_substeps):
            spring_force = self.config.K * (self.state.F_tilde - self.config.B)
            damping_force = self.config.C * self.state.V
            total_force = U_t - spring_force - damping_force

            A_raw = total_force / self.config.M
            A_current = np.clip(A_raw, -self.config.A_max, self.config.A_max)

            V_next = self.state.V + A_current * effective_dt
            V_next = np.clip(V_next, -self.config.V_max, self.config.V_max)

            F_tilde_next = self.state.F_tilde + V_next * effective_dt
            F_bounded_temp = np.clip(F_tilde_next, 0.0, 1.0)
            overshoot = F_tilde_next - F_bounded_temp
            overshoot_capped = np.clip(
                overshoot,
                -self.config.overshoot_max,
                self.config.overshoot_max,
            )
            F_tilde_next = F_bounded_temp + overshoot_capped

            self.state = FieldDynamicsState(
                F_tilde=F_tilde_next,
                V=V_next,
            )

        F_bounded_final = np.clip(self.state.F_tilde, 0.0, 1.0)
        spring_force_post = self.config.K * (self.state.F_tilde - self.config.B)
        post_conflict_tension = float(np.linalg.norm(spring_force_post - U_t))
        kinetic_energy = float(0.5 * np.sum(self.config.M * (self.state.V ** 2)))

        tension_metrics = {
            "pre_conflict_tension": pre_conflict_tension,
            "post_conflict_tension": post_conflict_tension,
            "kinetic_energy": kinetic_energy,
            "overshoot_magnitude": float(np.linalg.norm(overshoot_capped)),
            "max_abs_velocity": float(np.max(np.abs(self.state.V))),
            "max_abs_acceleration": float(np.max(np.abs(A_current))),
        }

        trace = {
            "original_dt": original_dt,
            "effective_dt": effective_dt,
            "num_substeps": num_substeps,
            "U_t": U_t.copy(),
            "F_tilde": self.state.F_tilde.copy(),
            "F_bounded": F_bounded_final.copy(),
            "A": A_current.copy(),
            "integration_method": "semi_implicit_euler",
            "overshoot_tracking": "last_substep",
        }

        return FieldDynamicsOutput(
            F_bounded=F_bounded_final.copy(),
            V=self.state.V.copy(),
            A=A_current.copy(),
            tension_metrics=tension_metrics,
            trace=trace,
        )


def _copy_config(config: FieldDynamicsConfig) -> FieldDynamicsConfig:
    return FieldDynamicsConfig(
        M=config.M.copy(),
        C=config.C.copy(),
        K=config.K.copy(),
        B=config.B.copy(),
        dt_max=config.dt_max,
        V_max=config.V_max,
        A_max=config.A_max,
        overshoot_max=config.overshoot_max.copy(),
    )


def _copy_state(state: FieldDynamicsState) -> FieldDynamicsState:
    return FieldDynamicsState(
        F_tilde=state.F_tilde.copy(),
        V=state.V.copy(),
    )
