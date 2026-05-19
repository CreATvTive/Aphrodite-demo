# Aphrodite Relational Field Dynamics Kernel v0.1

## 0. 模块定位与工程范围 (Module Scope)

本规范定义了 Aphrodite 系统架构中的核心组件：**关系场动力学内核 (Relational Field Dynamics Kernel)**。

* **模块职责：** 作为确定性状态机引擎，接收离散外力输入（$U(t)$），基于二阶常微分方程（ODE）更新并结算 10 维关系场状态，输出受物理约束的场状态、运动学参数及张力度量。
* **边界隔离：** 在 Phase 1-2 阶段，禁止在此模块中引入大语言模型（LLM）、动画渲染器（Renderer）、文本生成层（Language Generation）或跨场耦合（Cross-coupling）。
* **表现域约束：** 旨在生成受控的戏剧化压缩（disciplined theatrical compression），如延迟、未完成动作、低振幅反馈和静止。禁止生成廉价、服务型或 AI-Girlfriend 风格的表现。
* **数据源定位：** 本模块输出的 $F(t), V(t), A(t)$ 是下游实施 Source 投影（如动作延迟、非服务姿态）的**唯一合法动力学源 (only legal dynamics source)**，不可被下游表现层直接绕过（但允许结合 mode/memory/language policy 等非动力学上下文）。

---

## 1. 数学模型 (Mathematical Model)

### 1.1 状态向量定义

核心关系场由 10 个独立轴构成：
边界距离 ($f^{bd}$)、情感温度 ($f^{aw}$)、抓握压力 ($f^{sg}$)、纠正压力 ($f^{cp}$)、污染抗拒 ($f^{cr}$)、在场稳定度 ($f^{ps}$)、后撤倾向 ($f^{wt}$)、服务抗拒 ($f^{sr}$)、协作者层压力 ($f^{cl}$)、污染压力 ($f^{ct}$)。

### 1.2 内置连续状态与有界输出状态

为保证积分运算的连贯性与下游输入的合法性，系统状态切分为：

* **内部连续态 (Internal Continuous State):** $\tilde F(t) \in \mathbb{R}^{10}$。动力学更新（ODE）仅在此空间内运行。
* **有界输出态 (Bounded Relational Field State):** $F(t) = \text{clip}(\tilde F(t), 0, 1) \in [0, 1]^{10}$。该状态暴露给下游模块消费。
* **运动学衍生量:**
* 速度: $V(t) = \frac{d\tilde F(t)}{dt}$
* 加速度: $A(t) = \frac{d^2\tilde F(t)}{dt^2}$



### 1.3 动力学控制方程

$$\text{diag}(M) \frac{d^2\tilde F(t)}{dt^2} + \text{diag}(C) \frac{d\tilde F(t)}{dt} + \text{diag}(K)(\tilde F(t) - B) = U(t)$$

---

## 2. 工程接口定义 (Data Structures & Interfaces)

### 2.1 数据结构 (Data Classes)

```python
from dataclasses import dataclass
import numpy as np
import math

@dataclass
class FieldDynamicsConfig:
    # Physical matrices (Phase 1: strictly diagonal, shape (10,))
    M: np.ndarray
    C: np.ndarray
    K: np.ndarray
    B: np.ndarray
    
    # Numerical limits
    dt_max: float               # Maximum allowed timestep before sub-stepping
    V_max: float                # Cap for absolute velocity
    A_max: float                # Cap for absolute acceleration
    overshoot_max: np.ndarray   # Cap for |F_tilde - F_bounded|, shape (10,) (accepts float broadcast)

    def validate(self):
        """
        Must raise ValueError if constraints are violated:
        - M > 0
        - C >= 0
        - K >= 0
        - B in [0, 1]
        - All arrays must be shape (10,)
        - dt_max, V_max, A_max > 0
        - overshoot_max >= 0
        - No NaN or Inf in any array
        """
        pass

@dataclass
class FieldDynamicsState:
    F_tilde: np.ndarray  # Shape: (10,)
    V: np.ndarray        # Shape: (10,)

@dataclass
class FieldDynamicsInput:
    U_t: np.ndarray      # Shape: (10,)
    dt: float

@dataclass
class FieldDynamicsOutput:
    F_bounded: np.ndarray
    V: np.ndarray
    A: np.ndarray
    tension_metrics: dict
    trace: dict

```

### 2.2 核心结算逻辑伪代码 (Semi-implicit Euler with Sub-stepping)

```python
class RelationalFieldDynamicsKernel:
    def __init__(self, config: FieldDynamicsConfig, initial_state: FieldDynamicsState):
        self.config.validate()
        self.config = config
        self.state = initial_state

    def step(self, input_data: FieldDynamicsInput) -> FieldDynamicsOutput:
        # 1. dt validation and sub-stepping calculation
        if input_data.dt <= 0:
            raise ValueError("dt must be strictly positive.")
            
        num_substeps = 1
        if input_data.dt > self.config.dt_max:
            num_substeps = math.ceil(input_data.dt / self.config.dt_max)
            
        effective_dt = input_data.dt / num_substeps

        # 2. Pre-step tension calculation
        spring_force_pre = self.config.K * (self.state.F_tilde - self.config.B)
        pre_conflict_tension = float(np.linalg.norm(spring_force_pre - input_data.U_t))

        A_current = np.zeros_like(self.state.V)
        overshoot_capped = np.zeros_like(self.state.F_tilde)

        # 3. Sub-stepping integration loop
        for _ in range(num_substeps):
            # Calculate Forces
            spring_force = self.config.K * (self.state.F_tilde - self.config.B)
            damping_force = self.config.C * self.state.V
            total_force = input_data.U_t - spring_force - damping_force
            
            # Acceleration with cap
            A_raw = total_force / self.config.M
            A_current = np.clip(A_raw, -self.config.A_max, self.config.A_max)
            
            # Velocity update
            V_next = self.state.V + A_current * effective_dt
            V_next = np.clip(V_next, -self.config.V_max, self.config.V_max)
            
            # Position update (Internal continuous state)
            F_tilde_next = self.state.F_tilde + V_next * effective_dt
            
            # Restrict internal overshoot
            F_bounded_temp = np.clip(F_tilde_next, 0.0, 1.0)
            overshoot = F_tilde_next - F_bounded_temp
            overshoot_capped = np.clip(overshoot, -self.config.overshoot_max, self.config.overshoot_max)
            F_tilde_next = F_bounded_temp + overshoot_capped
            
            # Apply state updates
            self.state.F_tilde = F_tilde_next
            self.state.V = V_next

        # 4. Post-step tension and metrics calculation
        spring_force_post = self.config.K * (self.state.F_tilde - self.config.B)
        post_conflict_tension = float(np.linalg.norm(spring_force_post - input_data.U_t))
        kinetic_energy = float(0.5 * np.sum(self.config.M * (self.state.V ** 2)))
        
        tension_metrics = {
            "pre_conflict_tension": pre_conflict_tension,
            "post_conflict_tension": post_conflict_tension,
            "kinetic_energy": kinetic_energy,
            "overshoot_magnitude": float(np.linalg.norm(overshoot_capped)),
            "max_abs_velocity": float(np.max(np.abs(self.state.V))),
            "max_abs_acceleration": float(np.max(np.abs(A_current)))
        }

        # 5. Final Bounded Output and Trace
        F_bounded_final = np.clip(self.state.F_tilde, 0.0, 1.0)
        
        trace_dict = {
            "original_dt": input_data.dt,
            "effective_dt": effective_dt,
            "num_substeps": num_substeps,
            "U_t": input_data.U_t.copy(),
            "F_tilde": self.state.F_tilde.copy()
        }
        
        return FieldDynamicsOutput(
            F_bounded=F_bounded_final,
            V=self.state.V.copy(),
            A=A_current.copy(),
            tension_metrics=tension_metrics,
            trace=trace_dict
        )

```

---

## 3. Phase 1-2 执行规范 (Execution Constraints)

* **纯对角要求 (Diagonal Matrices):** $M, C, K$ 仅允许为 shape (10,) 的一维数组。禁止跨场耦合计算。
* **隔离要求 (Module Isolation):** 严禁在此模块内导入或调用任何 LLM 库、渲染包或自然语言处理依赖。
* **责任隔离:** 本模块负责并仅负责数值动力学计算，不对外显动画或具体词汇选择负责。

---

## 4. 测试要求 (Test Requirements)

系统交付前必须通过以下单元测试：

1. **Config Validation:** 注入包含非法值（如 $M \le 0, \text{NaN}, B \notin [0,1]$）的 Config，需正确引发校验异常。
2. **Invalid dt & Sub-stepping:** 测试 $dt \le 0$ 时抛出错误；测试 $dt > dt_{max}$ 时，`trace['num_substeps']` $> 1$，且输出与多次显式小步长调用的结果近似。
3. **No-Input Relaxation:** 当持续输入 $U(t) = \vec{0}$ 时，$F(t)$ 应平滑收敛至基准点 $B$。
4. **Delayed Motion via Impulse:** 给定短促脉冲输入 $U(t)$，$F(t)$ 不得发生瞬间跳变，必须观测到 $V(t)$ 平滑积分后的迟滞位移。
5. **Caps & Bounds Constraint:**
* 测试在极端输入下，输出的 $V$ 与 $A$ 严格受限于配置的上限。
* 输出状态 `F_bounded` 严格处于 $[0,1]$ 内。
* 内部越界量严格受限于 `overshoot_max` 向量。


6. **High Warmth vs. Boundary Distance Invariant:**
* *Dynamics Unit Test:* 对角矩阵模型下，施加高情感温度 ($f^{aw}$) 的受力，不会直接降低边界距离 ($f^{bd}$)。
* *Downstream Integration Test (Placeholder):* 高 $f^{aw}$ 与不坍缩的 $f^{bd}$ 组合输出，在下游不得生成强烈靠近、调情或服务型姿态。


7. **No LLM Dependency:** 静态扫描确保核心文件内无 LLM 相关引用。