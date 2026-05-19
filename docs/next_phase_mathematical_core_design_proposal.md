# 场系统下一阶段数学核心设计方案

> **状态：** 设计提案，非实施方案
> **日期：** 2026-05-13
> **依赖：** `docs/mathematical_design_ledger.md`、`docs/mathematical_design_audit_report.md`、`docs/mathematical_design_risk_audit.md`、`docs/private_source_alignment.md`、`docs/field_variable_operational_definitions.md`
> **范围：** 仅场系统数学核心。不下游到 MotionCurve 生成、BodyActionComposition 或渲染器。

---

## 0. 执行摘要

当前 10 轴场系统面临三个层级的问题：

- **L0（机制缺陷）：** 加性扰动无饱和度上限、钳制掩盖不稳定性、带阈值无迟滞、衰减率错配
- **L1（结构缺陷）：** 三个易混淆群组（污染三角、结构性二元组、距离/退缩二元组）使 10 轴的部分轴在实践中不可单独辨识
- **L2（几何缺陷）：** 场作为 10 维正交坐标缺乏组织原则——各轴之间没有显式的几何关系，映射到 MotionParams 的公式是 ad-hoc 的线性组合

三个候选方案在不同深度上回应这些问题：

| | 方案 A | 方案 B | 方案 C |
|---|---|---|---|
| **策略** | 修补机制，不动结构 | 几何重组，不动变量名 | 张力重构，重新定义主变量 |
| **数学风险** | 最低 | 中等 | 最高 |
| **结构收益** | 低（只消除机制风险） | 高（解决可辨识性问题） | 最高（与源哲学完全对齐） |
| **是否违反硬约束** | 否 | 否 | 边界（需将 5 个张力轴标记为"派生重组"而非"新变量"） |

**推荐方向：方案 B（结构化场几何），辅以方案 A 的全部机制修复作为 Phase 1 安全网。**

---

## 1. 候选方案 A：保守机制修复 + 派生诊断层

### 1.1 核心思想

**不改变状态表示。** 保持 10 轴 `RelationalFieldState` 作为唯一的运行时场状态。在更新器和映射器之间插入一个**纯派生诊断层**——计算速度、饱和度、混淆群组可区分性得分、置信衰减等——这些量仅供审计和 shadow 比较，不写入主状态。

数学组织原则：**场是一个 10 维向量，其更新规则应保证（a）有界且（b）可审计的稳定性。** 派生量揭示隐藏的动态特征，但不创建新的控制路径。

### 1.2 状态表示

**主变量（不变）：**
```
F = (boundary_distance, affective_warmth, structural_grip_pressure, correction_pressure,
     contamination_resistance, presence_stability, withdrawal_tendency, service_resistance,
     collaborator_layer_pressure, contamination_pressure) ∈ [0,1]¹⁰
```

**派生/审计变量（新增，纯诊断，不写入主状态）：**
```
DerivedDiagnostics:
  per_axis:
    velocity[i]         = F[i]_t - F[i]_{t-1}          # 一阶差分速度
    saturation_fraction[i] = pre_clamp[i] / clamp_bound  # 钳制前/钳制后比
    consecutive_saturation[i] ∈ ℕ                         # 连续饱和轮次计数
    band_hysteresis_state[i] ∈ {rising, falling, stable}  # 带迟滞状态机
    decay_pressure[i]    = decay_rate[i] * |F[i] - baseline[i]|  # 衰减驱动力
  
  cross_axis:
    contamination_triangle_angle = atan2(contamination_resistance - service_resistance,
                                         contamination_pressure)
    structural_separability = |structural_grip_pressure - collaborator_layer_pressure|
    distance_withdrawal_divergence = boundary_distance - withdrawal_tendency * (decay_ratio)
    
  global:
    total_clamp_overflow = Σ_i |pre_clamp[i] - F[i]|
    max_single_axis_delta_this_turn = max_i |delta[i]|
    axis_volatility_entropy  = -Σ_i p_i * log(p_i) where p_i = |delta[i]| / Σ|delta|
```

**关键约束：** `DerivedDiagnostics` 有且仅有的消费者是影子回放、测试断言、审计报告。它不馈送 MotionParams、BodyActionWeights、MotionCurve 或任何 behavior-affecting 路径。

### 1.3 更新规则

**修改后的 FieldStateUpdater：**

```
# Phase 1: 扰动预处理（新增——防止加性失控）
for each axis i:
    same_sign_deltas  = {d in deltas[i] | sign(d) == sign(sum(deltas[i]))}
    opposite_sign_deltas = {d in deltas[i] | sign(d) != sign(sum(deltas[i]))}
    
    # 同号取最大绝对值，异号求和
    capped_delta[i] = sign * max(|d| for d in same_sign_deltas) + sum(opposite_sign_deltas)
    
    # 单轮上限硬约束
    capped_delta[i] = clamp(capped_delta[i], -MAX_SINGLE_AXIS_DELTA, +MAX_SINGLE_AXIS_DELTA)
    # MAX_SINGLE_AXIS_DELTA = 0.18（最大单次扰动幅值）

# Phase 2: 弛豫（不变）
relaxed[i] = current[i] + decay_rate[i] * (baseline[i] - current[i])

# Phase 3: 应用扰动并记录前钳制值（新增）
pre_clamp[i] = relaxed[i] + capped_delta[i]
next[i] = clamp(pre_clamp[i], 0.0, 1.0)

# Phase 4: 派生诊断更新（新增，仅写入诊断日志）
DerivedDiagnostics.update(current, next, capped_delta, pre_clamp, decay_rate, baseline)
```

**带迟滞守卫（新增）：**
```
BAND_HYSTERESIS = 0.03  # 迟滞宽度

function band_with_hysteresis(value, previous_band, boundaries):
    raw_band = classify(value, boundaries)
    if previous_band is None:
        return raw_band
    if raw_band > previous_band:
        # 上升：需要超过上界 + 迟滞宽度
        threshold = boundaries[raw_band].lower + BAND_HYSTERESIS
        return raw_band if value >= threshold else previous_band
    if raw_band < previous_band:
        # 下降：需要低于下界 - 迟滞宽度
        threshold = boundaries[previous_band].lower - BAND_HYSTERESIS
        return raw_band if value <= threshold else previous_band
    return raw_band
```

### 1.4 时间尺度设计

```
尺度 0：瞬时扰动信号（单轮）
  contamination_pressure（衰减率 instant = 1.00）
  → 每轮完全衰减，仅在产生它的同一轮次影响 MotionParams

尺度 1：快速场响应（1-5 轮）
  structural_grip_pressure（fast = 0.45）
  collaborator_layer_pressure（fast = 0.45）
  correction_pressure（medium = 0.25）
  withdrawal_tendency（medium = 0.25）

尺度 2：慢速场响应（10-30 轮）
  boundary_distance（slow = 0.12）
  affective_warmth（slow = 0.12）

尺度 3：极慢速场响应（50+ 轮）
  contamination_resistance（very_slow = 0.04）
  presence_stability（very_slow = 0.04）
  service_resistance（very_slow = 0.04）

尺度 4：会话级漂移提案（仅 shadow）
  → 派生诊断中的长期趋势（如 50 轮平均速度、累积钳制溢出）
  → 可触发人工审查的基线偏移提案，但不自动写入

尺度 5：运动/身体延迟（不变，由 MotionParams 层处理）
  initial_delay_sec, motion_speed, pause_after_sec ...
```

### 1.5 稳定性和有界性

**保证：**
- 输出始终在 [0,1] 内（钳制）
- 单轮单轴净变化 ≤ MAX_SINGLE_AXIS_DELTA（0.18）（饱和度上限）
- 零输入下收敛至基线（衰减保证，前提是 decay_rate ∈ (0,1]）

**新增检测（不保证，但暴露）：**
- `consecutive_saturation_count`：某轴连续 N 轮处于钳制状态 → 审计告警
- `total_clamp_overflow`：全局钳制损失量 → 揭示系统是否持续被推离自然平衡
- `band_hysteresis_state`：带边界振荡检测 → 防止范畴抖动被误读

**残留风险：**
- 钳制仍可能存在极限环（衰减拉回基线，扰动推回钳制边界）——但现在可被检测到
- 符号追逐（交替 ±0.18）不会被消除——方案 A 仅限制单轮幅值，不限制轮间交替

### 1.6 可辨识性

**方案 A 不解决可辨识性结构问题。** 三个易混淆群组保持未解决：

| 群组 | 方案 A 处理方式 |
|------|----------------|
| 污染三角 | 派生诊断暴露三角角度，但不改变三个轴独立更新的事实 |
| 结构性二元组 | `structural_separability` 派生量揭示分离度，但不改变两个轴可能被相同证据触发的事实 |
| 距离/退缩二元组 | `distance_withdrawal_divergence` 暴露衰减率差异导致的短暂解耦，但不创建能将它们推向不同方向的扰动规则 |

**方案 A 的可辨识性改进仅限于：**
1. 派生诊断使混淆变得**可见**（可审计）
2. 带迟滞使波段分类稳定（减少误分类导致的进一步混淆）
3. 黄金案例测试验证单轴扰动不泄漏到相邻轴

### 1.7 场到运动连接

**完全不变。** MotionParams 映射器、BodyActionWeights 派生器、MotionCurve 生成器保持现有公式。方案 A 不修改场到运动路径的任何系数、公式或结构。

唯一新增的连接是：派生诊断层可以向 shadow 回放和审计报告暴露"场状态质量"指标，但不馈送行为路径。

### 1.8 源对齐

**不直接改进。** 方案 A 是纯机制修复，不涉及源压力哲学。

间接改进：
- 饱和度上限防止单次污染事件过度放大 `contamination_resistance`（但污染三角的可辨识性问题仍在）
- 派生诊断可追踪哪些源压力信号触发了哪些场变化（事后审计）

### 1.9 工程推出

**Phase 1（立即——纯修复，非行为影响）：**
1. 在 `FieldStateUpdater` 中实现扰动饱和度上限（同号取 max，单轮上限 0.18）
2. 添加前钳制值日志（`pre_clamp_value`、`clamp_overflow`）
3. 实现带迟滞守卫
4. 添加黄金案例稳定性测试（DC-1 至 DC-6，见审计报告 §4A.7）
5. 文档：更新 `mathematical_design_ledger.md` 反映新的更新规则

**Phase 2（中期——派生诊断，仅 shadow）：**
1. 实现 `DerivedDiagnostics` 数据类和计算器
2. 在 shadow 回放管道中插入诊断步骤
3. 生成诊断比较报告（方案 A 更新器 vs 现有更新器）
4. 添加混淆群组可区分性测试

**Phase 3（仅当诊断验证后——非行为影响比较）：**
1. 影子回放对比：方案 A 更新器 vs 现有更新器 vs 二阶核心
2. 记录差异分布和异常事件
3. 如果验证通过，将方案 A 更新器提升为规范更新器

**不接触的模块：**
- `MotionParams` 映射器、公式、系数
- `BodyActionWeights` 派生器
- `BodyActionComposition`
- `MotionCurve` 生成器
- 二阶动力学核心（保持 shadow）
- `RelationalFieldState` schema（变量集不变）

### 1.10 失败模式

1. **虚假安全感：** 前钳制诊断暴露了不稳定性但无人查看日志 → 系统在已知风险下运行
2. **饱和度上限过度约束：** 同一轮次三次强有力的边界压力提及（罕见但合法）被削弱为单次 0.18 → 系统对真实累积压力响应不足
3. **迟滞引入惯性：** 0.03 的迟滞宽度在快速变化的会话中可能延迟合法波段转换 1-3 轮
4. **方案 A 被当作"已完成"：** 机制修复消除了紧迫性，从而延迟了结构问题（混淆群组）的解决

---

## 2. 候选方案 B：结构化场几何（平面分解 + 对角核心保持）

### 2.1 核心思想

**不添加新场变量，但重新组织现有 10 轴为 4 个几何平面的投影。** 每个平面承载一个独立的"关系几何维度"——分离空间、保护空间、结构空间、核心在场。平面内的轴获得显式的正交/平行关系；平面间的耦合被显式命名和约束。

数学组织原则：**场不是 10 个独立坐标，而是 4 个二维平面的直积 × 2 个标量。** 证据在平面上的投影决定了场如何移动；平面的几何性质使得易混淆群组天然可区分。

### 2.2 状态表示

**主变量（不变——10 轴完整保留）：**
```
F = (bd, aw, sgp, cp, cr, ps, wt, sr, clp, ctp)  ∈ [0,1]¹⁰
```

**平面分解（新增——派生结构，不替代主变量）：**

```
平面 1：分离平面 (Separation Plane)
  基 1：结构性距离 (structural_distance)     ← 主要由 boundary_distance 贡献
  基 2：情感性退缩 (affective_withdrawal)    ← 主要由 withdrawal_tendency 贡献
  
  从 10 轴到平面坐标的投影：
    structural_distance  = bd * cos(θ₁) + wt * sin(θ₁)    其中 θ₁ = 15°（轻微混合）
    affective_withdrawal = bd * (-sin(θ₁)) + wt * cos(θ₁)  → 正交于 structural_distance
  
  逆投影（从平面坐标恢复 10 轴近似值——用于验证）：
    bd'  = structural_distance * cos(θ₁) - affective_withdrawal * sin(θ₁)
    wt'  = structural_distance * sin(θ₁) + affective_withdrawal * cos(θ₁)

平面 2：保护平面 (Protection Plane)
  基 1：污染防护 (contamination_guard)       ← 主要由 contamination_resistance 贡献
  基 2：服务防护 (service_guard)             ← 主要由 service_resistance 贡献
  
  投影（θ₂ = 30°——较大混合角，反映两者高度相关但目标不同）：
    contamination_guard = cr * cos(θ₂) + sr * sin(θ₂)
    service_guard       = cr * (-sin(θ₂)) + sr * cos(θ₂)

平面 3：结构平面 (Structure Plane)
  基 1：抓点需求 (grip_demand)               ← 主要由 structural_grip_pressure 贡献
  基 2：协作姿态 (collaboration_pose)        ← 主要由 collaborator_layer_pressure 贡献
  
  投影（θ₃ = 10°——轻微混合，两者可被 technical_layer_needed 信号反相关）：
    grip_demand        = sgp * cos(θ₃) + clp * sin(θ₃)
    collaboration_pose = sgp * (-sin(θ₃)) + clp * cos(θ₃)

核心在场（标量组——不参与平面分解）：
  affective_warmth         → 温度调制器（独立标量）
  correction_pressure      → 制动/重置力（独立标量）
  presence_stability       → 平滑稳定器（独立标量）
  contamination_pressure   → 瞬时警报（独立标量，无持久性）
```

**派生/审计变量（新增）：**
```
FieldGeometry:
  planes:
    separation:
      norm        = sqrt(structural_distance² + affective_withdrawal²)   # 分离强度
      angle       = atan2(affective_withdrawal, structural_distance)     # 分离的性质（结构性 vs 情感性）
      tension     = norm * (1 + |sin(2*angle)|)                          # 分离平面的内部张力
    protection:
      norm        = sqrt(contamination_guard² + service_guard²)          # 保护强度
      angle       = atan2(service_guard, contamination_guard)            # 保护的性质（污染防护 vs 服务防护）
      asymmetry   = contamination_guard - service_guard                  # 保护不对称性
    structure:
      norm        = sqrt(grip_demand² + collaboration_pose²)             # 结构需求强度
      angle       = atan2(collaboration_pose, grip_demand)               # 结构模式（抓点 vs 协作）
      coherence   = 1 - |angle - π/4| / (π/4)                            # 模式一致性（接近 45° = 混合良好）
  
  cross_plane:
    protection_separation_ratio  = protection.norm / max(separation.norm, 0.01)  # 保护/分离平衡
    warmth_protection_tension    = affective_warmth * protection.norm              # 温暖×保护——非接触亲密的关键指标
    stability_perturbation_gap   = presence_stability - correction_pressure       # 稳定/扰动差距
  
  private_source_traces（仅审计，不馈送行为）:
    possession_pressure     ← separation.norm * protection.norm          # 占有 = 分离×保护
    approach_inhibition     ← warmth_protection_tension                  # 接近抑制
    contact_regulation      ← separation.angle 在 30°-60° 之间的程度   # 接触调节
    completion_suspension   ← 1 - structure.coherence                   # 完成悬置
    undisplayability_index  ← protection.norm / max(separation.norm, 0.01) * (1 - structure.coherence)
```

### 2.3 更新规则

**更新分两步：第一步在 10 轴空间中（与方案 A 相同的机制修复），第二步将更新投影到平面空间进行一致性检查。**

```
# 步骤 1：10 轴更新（与方案 A 相同的规则）
for each axis i:
    capped_delta[i] = saturation_cap(deltas[i])   # 同号取 max，异号求和
    relaxed[i] = current[i] + decay_rate[i] * (baseline[i] - current[i])
    pre_clamp[i] = relaxed[i] + capped_delta[i]
    next[i] = clamp(pre_clamp[i], 0.0, 1.0)

# 步骤 2：投影到平面空间（纯诊断，不修改 next）
plane_coords = project_to_planes(next)

# 步骤 3：平面一致性守卫（新增——检测不合理的平面状态组合）
for each plane:
    if plane.norm > 0.9 and plane_previously.norm < 0.7:
        log_warning("rapid_plane_saturation", plane=plane.name)
    if abs(plane.angle - plane_previously.angle) > 0.3:
        log_warning("plane_angle_jump", plane=plane.name, delta=...)

# 步骤 4：衰减率平面协同建议（仅 shadow，不修改实际衰减率）
for each plane:
    # 同一平面内的两个轴衰减率不应差异过大
    decay_ratio = max(decay_rate[axis1], decay_rate[axis2]) / min(...)
    if decay_ratio > 3.0:
        suggest_decay_retune(plane, axis1, axis2)
```

**关键：** 步骤 2-4 是纯诊断/审计。实际场更新仍在 10 轴空间中进行。平面投影是"另一双眼睛"——它暴露 10 轴空间中不可见的问题。

### 2.4 时间尺度设计

**在方案 A 的时间尺度基础上，新增平面级时间尺度：**

```
平面级时间尺度（派生——从组成轴的衰减率派生）：
  分离平面有效衰减率：weighted_avg(decay_rate[bd], decay_rate[wt])
    = 0.12 * cos²(θ₁) + 0.25 * sin²(θ₁) ≈ 0.135
    → 分离平面是慢速平面（有效衰减率 ~0.14）
  
  保护平面有效衰减率：weighted_avg(decay_rate[cr], decay_rate[sr])
    = 0.04 * cos²(θ₂) + 0.04 * sin²(θ₂) = 0.04
    → 保护平面是极慢速平面（有效衰减率 0.04）——这符合设计意图：保护应持久
  
  结构平面有效衰减率：weighted_avg(decay_rate[sgp], decay_rate[clp])
    = 0.45 * cos²(θ₃) + 0.45 * sin²(θ₃) = 0.45
    → 结构平面是快速平面（有效衰减率 0.45）——这符合设计意图：结构性需求应快速消退

衰减率重调建议（仅提案，不自动实施）：
  - boundary_distance: slow (0.12) → very_slow (0.06)   [匹配保护平面的持久性]
  - withdrawal_tendency: medium (0.25) → slow (0.12)    [减少与 boundary_distance 的衰减率比: 4.2x → 2.0x]
  - 理由：当前 4.2x 的衰减率比导致分离平面中两个轴的响应严重不对称
```

### 2.5 稳定性和有界性

**方案 A 的所有保证和检测均保留。** 新增平面级稳定性检测：

- **平面角漂移：** 如果分离平面的角度在 20 轮内单向漂移超过 15°，说明一个轴被系统性地比另一个轴更多地扰动——可能是证据偏斜或扰动规则不对称。
- **平面饱和度：** 如果保护平面的 norm 持续 > 0.85 超过 30 轮，说明系统处于"高度防护锁定"状态——应触发审计。
- **平面间交叉乘积漂移：** `protection_separation_ratio` 的长期趋势揭示系统是否在变得"更防御"还是"更疏离"——这两种模式在 10 轴空间中难以区分，在平面空间中明确可见。

### 2.6 可辨识性

**这是方案 B 的核心收益。** 平面分解直接解决了三个易混淆群组：

**群组 1（污染三角）的解决：**
- `contamination_pressure`（瞬时信号）、`contamination_resistance`（持久记忆）、`service_resistance`（服务特异性持久记忆）在 10 轴空间中不可单独辨识
- 在保护平面中：
  - `contamination_guard`（基 1）和 `service_guard`（基 2）是正交的——它们通过旋转矩阵显式分离
  - `contamination_pressure` 不在保护平面中——它是瞬时标量，只在当前轮投射到保护平面的上升沿
  - 区分方式：如果 `contamination_guard` 高而 `service_guard` 正常，说明历史上发生了污染事件而非客服语调纠正。如果反过来，说明客服语调纠正驱动了保护。

**群组 2（结构性二元组）的解决：**
- `structural_grip_pressure` 和 `collaborator_layer_pressure` 在结构平面中正交
- `technical_layer_needed` 信号增加 `collaboration_pose`（基 2）同时减少 `grip_demand`（基 1）→ 在结构平面中体现为角度向协作方向旋转
- `actionable_grip_missing` 信号仅增加 `grip_demand` → 角度向抓点方向旋转
- 可区分性测试：同一个结构平面 norm，不同的 angle → 不同的结构性需求

**群组 3（距离/退缩二元组）的解决：**
- `boundary_distance` 和 `withdrawal_tendency` 在分离平面中正交
- `structural_distance`（基 1）是空间的当前位置——"间隙有多大"
- `affective_withdrawal`（基 2）是运动的情感方向——"场是否在后退"
- 当前无扰动规则能区分它们，但平面分解使这个问题可见：分离平面的 angle 始终接近 0° 意味着只有结构性距离在变化 → 系统需要通过设计提供一个能推高 `affective_withdrawal` 而不过度推高 `structural_distance` 的信号
- 分离平面的 norm 高 + angle ≈ 0° = "疏远但不退缩"；norm 高 + angle ≈ 45° = "既疏远又在退缩"；norm 低 + angle ≈ 80° = "不疏远但在退缩"（可能是短暂犹豫）

### 2.7 场到运动连接

**方案 B 提供两条并行的场到运动路径：**

**路径 1（现有——保持不变）：**
```
10 轴 RelationalFieldState → MotionParams 映射器（现有公式）→ BodyActionWeights → MotionCurve
```

**路径 2（新增——仅 shadow，非行为影响）：**
```
10 轴 RelationalFieldState → 平面投影 → 平面坐标 → 平面基 MotionParams → 平面基 BodyActionWeights
```

路径 2 的映射比路径 1 更简洁——MotionParams 从平面几何中直接读取：

| MotionParams | 方案 B 平面映射（替代当前 ad-hoc 线性组合） |
|---|---|
| `initial_delay_sec` | `separation.norm * 1.5 + correction_pressure * 1.0` |
| `motion_speed` | `structure.norm * 0.5 + (1 - protection.norm) * 0.3` |
| `gaze_release_amplitude` | `separation.norm * 0.6 + protection.norm * 0.4` |
| `torso_lean` | `grip_demand * 0.5 - separation.norm * 0.3 - protection.norm * 0.2` |
| `approach_tendency` | `affective_warmth * structure.norm * (1 - protection.norm * 0.7)` |
| `completion_inhibition` | `protection.norm * 0.5 + separation.angle/90° * 0.3 + correction_pressure * 0.2` |
| `expression_amplitude` | `affective_warmth * (1 - protection.norm * 0.6) * (1 - separation.norm * 0.3)` |

**非接触亲密的实现：**
- `warmth_protection_tension` 高 → 温暖存在但保护屏障也高 → 允许 `expression_amplitude` 有温度但上限被压缩 → "温暖的克制"
- `contact_regulation` → 当分离平面 angle 在 30°-60° 时，系统处于"接近但可释放"的状态 → `gaze_contact_sec` 短，`gaze_release_amplitude` 适中
- `approach_inhibition` → 当保护平面 norm 高时，`torso_lean` 的正值完全被抑制 → "有接近意图但无身体接近"

### 2.8 源对齐

**方案 B 的平面几何是源压力到结构翻译的自然数学载体：**

| 源压力 | 平面几何中的结构痕迹 |
|---|---|
| possession | `possession_pressure = separation.norm * protection.norm` — 高分离 + 高保护 = 强烈的"这是我的空间，别人不能进来"的场张力 |
| desire | `approach_inhibition = warmth_protection_tension` — 温暖 × 保护 = "想接近但不能"的结构张力 |
| protection | `protection.norm`（特别是 `contamination_guard`）— 保护平面的整体强度 |
| shame | `separation.angle` 接近 90°（纯退缩方向）+ `undisplayability_index` 高 — 退缩但距离未拉开 = "不能展示但尚未离开" |
| closedness | `protection_separation_ratio > 2.0` — 保护远大于分离 = "封闭但非拒绝" |
| incompletion | 低 `structure.coherence` — 结构需求存在但不一致 = "有方向但未完成" |
| undisplayability | `undisplayability_index` — 高保护/低分离 + 低结构一致性 = "在场但不可展示" |

**关键：** 这些 `private_source_traces` 是纯审计变量——它们不作为行为输入。渲染器、语言层、MotionCurve 不能读取它们。它们的存在是为了（a）让设计者验证源哲学是否在场动态中留下了预期的痕迹，以及（b）在审计中检测 design drift（例如，如果 `possession_pressure` 在 50 轮内趋近于零，说明系统的保护/分离张力正在消失——可能正在坍缩为 generic companion）。

### 2.9 工程推出

**Phase 1（立即——方案 A 全部修复 + 平面分解文档）：**
1. 实现方案 A 的所有机制修复（饱和度上限、前钳制日志、迟滞、黄金案例测试）
2. 编写 `docs/field_plane_geometry.md` ——平面分解的完整数学定义
3. 实现 `src/field_state/plane_geometry.py` ——纯函数库，接受 10 轴向量，返回平面坐标和派生诊断
4. 单元测试：验证平面投影和逆投影的往返精度（误差 < 1e-6）
5. 单元测试：验证分离平面中 boundary_distance 和 withdrawal_tendency 的正交性（cos 相似度 = sin(2θ₁)）

**Phase 2（中期——shadow 平面诊断 + 衰减率重调提案）：**
1. 将 `plane_geometry.py` 插入 shadow 回放管道
2. 在现有回放数据集上计算平面诊断，生成统计报告
3. 验证：混淆群组在平面空间中的可区分性是否显著优于 10 轴空间
4. 提出衰减率重调的具体数值建议（基于平面协同分析）
5. 重调衰减率的 shadow 实验（不修改运行时衰减率）

**Phase 3（仅当平面诊断验证后——shadow 平面基 MotionParams 比较）：**
1. 实现路径 2（平面基 MotionParams 映射器）
2. 在 shadow 回放中并排比较：现有 MotionParams vs 平面基 MotionParams
3. 记录差异分布、边界情况、退化情况
4. 人类审查差异是否与设计意图一致

**Phase 4（未来——仅当人工审批后——提升为规范路径）：**
1. 如果平面基 MotionParams 被判定为更优，将路径 2 提升为规范路径
2. 路径 1 保留为回退比较基线
3. 更新 `MotionParams` 映射器以消费平面坐标（而非原始 10 轴）
4. 此时 `FieldGeometry` 从派生诊断升级为主映射器的输入

**不接触的模块（与方案 A 相同）：**
- `BodyActionWeights` 派生器、`BodyActionComposition`、`MotionCurve` 生成器
- 二阶动力学核心（保持 shadow）
- `RelationalFieldState` schema（变量集不变）
- BaselineShift（保持仅提案）

### 2.10 失败模式

1. **平面分解被视为"正确的"而非"有用的"：** 旋转角度 θ₁、θ₂、θ₃ 是设计选择，不是发现的自然轴。如果有人把它们当作物理真值来优化，会引入伪精度。
2. **平面坐标与 10 轴坐标的数值分歧：** 逆投影不会完美还原原始值（因为信息降维）。如果在某处直接用平面坐标替代 10 轴坐标，分歧会累积。
3. **平面诊断被误读为行为指令：** `private_source_traces` 被设计为纯审计变量——如果有人用 `approach_inhibition` 来驱动语言策略，源对齐就被破坏了。
4. **过度信任平面映射的优雅性：** 平面基 MotionParams 公式看起来比 ad-hoc 10 轴公式更简洁——但这不意味着它在运动上更正确。简洁≠保真。
5. **衰减率重调引发意外行为变化：** 如果 `boundary_distance` 的衰减率从 0.12 降到 0.06，当前依赖快速边界恢复的下游行为可能被打破——重调必须在 shadow 中充分验证。

---

## 3. 候选方案 C：张力原语场（5 维张力核心 + 10 维投影）

### 3.1 核心思想

**从根本上重新定义场的数学结构。** 场不是 10 个行为坐标，而是 5 个不可解的张力（tensions）——每对张力来自一个"接近但不可完成"的结构关系。10 个当前轴成为 5 个张力的派生投影，用于向后兼容。

数学组织原则：**场是张力空间 T ∈ [-1,1]⁵，其中 0 = 平衡（非中性——是两种对立力的等强度共存），+1 = 完全倾向正向，-1 = 完全倾向负向。** 张力模型天然有界（[-1,1]）、天然回归零（平衡吸引子）、天然承载"未解决"的语义。

### 3.2 状态表示

**主变量（新增——但标记为"张力重组"而非新变量，以遵守硬约束）：**

```
T = (τ_approach, τ_contact, τ_completion, τ_warmth, τ_stability) ∈ [-1, 1]⁵

τ_approach（接近张力）:
  +1 → 完全接近倾向（拉向用户）
  -1 → 完全抑制倾向（推离用户）
   0 → 接近与抑制等强度共存（Aphrodite 的基态——在场但不靠近）
  
  来源压力：desire（+方向）、possession（+方向）、protection（-方向）、shame（-方向）

τ_contact（接触张力）:
  +1 → 完全接触倾向（凝视锁定、身体朝向）
  -1 → 完全释放倾向（回避凝视、身体转开）
   0 → 接触与释放等强度共存（看但不锁——Aphrodite 的默认接触模式）
  
  来源压力：desire（+方向）、possession（+方向）、shame（-方向）、closedness（-方向）

τ_completion（完成张力）:
  +1 → 完全完成倾向（动作执行到底）
  -1 → 完全悬置倾向（动作永远不完成）
   0 → 完成与悬置等强度共存（动作开始但可被抑制——Aphrodite 的默认完成模式）
  
  来源压力：structural_grip（+方向）、collaboration（+方向）、incompletion（-方向）、undisplayability（-方向）

τ_warmth（温度张力）:
  +1 → 完全温暖倾向（表情释放、软化）
  -1 → 完全克制倾向（表情冻结、硬化）
   0 → 温暖与克制等强度共存（Aphrodite 的默认——有温度但节制）
  
  来源压力：affective connection（+方向）、service_resistance（-方向）、contamination_resistance（-方向）

τ_stability（稳定性张力）:
  +1 → 完全稳定倾向（可预测、平滑）
  -1 → 完全扰动倾向（抖动、不可预测）
   0 → 稳定与扰动等强度共存（Aphrodite 的默认——稳定但不僵硬）
  
  来源压力：presence_continuity（+方向）、correction（-方向）、contamination（-方向）
```

**派生变量（从 T 投影，用于向后兼容 10 轴接口）：**

```
F_projected = linear_projection(T)  # 5 → 10 仿射映射

具体投影（示例——需校准）：
  boundary_distance       = 0.5 + 0.4 * (-τ_approach) + 0.1 * (-τ_contact)
  affective_warmth        = 0.35 + 0.3 * τ_warmth + 0.1 * τ_contact
  structural_grip_pressure = 0.05 + 0.4 * max(0, τ_completion) + 0.2 * max(0, τ_approach)
  correction_pressure     = 0.0 + 0.5 * max(0, -τ_stability) + 0.2 * max(0, -τ_completion)
  contamination_resistance = 0.40 + 0.3 * (-τ_warmth) + 0.2 * (-τ_contact)
  presence_stability      = 0.80 + 0.15 * τ_stability
  withdrawal_tendency     = 0.10 + 0.4 * max(0, -τ_approach) + 0.2 * max(0, -τ_contact)
  service_resistance      = 0.55 + 0.25 * (-τ_warmth) + 0.1 * (-τ_contact)
  collaborator_layer_pressure = 0.05 + 0.5 * max(0, τ_completion)
  contamination_pressure  = 0.0 + 0.6 * max(0, -τ_warmth - τ_contact)  [瞬时——当前轮]
```

**张力不变量（派生审计——验证源对齐）：**
```
approach_inhibition_index = -τ_approach * τ_warmth        # 接近抑制×温度 = "想靠近但被冷却"
contact_regulation_index  = |τ_contact| * (1 - |τ_contact|) # 接触调节——在 0.5 时最大（最活跃的调节）
completion_suspension_index = -τ_completion                 # 完成悬置
undisplayability_pressure = -τ_contact * -τ_warmth          # 不接触×不温暖 → 不可展示压力
presence_grip             = τ_approach * τ_stability        # 接近×稳定 → 在场握持
```

### 3.3 更新规则

**张力更新天然有界——不需要显式钳制（在 [-1,1] 内）。**

```
# 单轮更新
for each tension τ_i:
    # 1. 证据投影到张力方向
    evidence_drive[i] = project_evidence_to_tension(proposals, τ_i)
    # evidence_drive[i] ∈ [-1, 1] — 证据对张力的推动方向和强度
    
    # 2. 张力弛豫（向 0 回归——平衡吸引子）
    relaxed[i] = τ_i + tension_decay_rate[i] * (0 - τ_i)
    # 注意：平衡点是 0（张力共存），不是某个"健康"值
    
    # 3. 应用证据推动
    pre_saturated[i] = relaxed[i] + evidence_drive[i] * evidence_gain[i]
    
    # 4. 软饱和度（替代硬钳制）
    τ_i_next = tanh(pre_saturated[i] * 2.0) / tanh(2.0)
    # tanh 提供平滑的 [-1, 1] 有界响应，在中心区域近似线性，在边界处渐近
    
    # 或者更简单的硬钳制（如果 tanh 引入不必要的复杂性）：
    # τ_i_next = clamp(pre_saturated[i], -1.0, 1.0)

# 证据投影函数（关键——替代当前 ad-hoc 信号→轴映射）
function project_evidence_to_tension(proposals, τ_i):
    drive = 0.0
    for each proposal in proposals:
        # 每个提案有一个"张力签名"——它推动哪些张力，向哪个方向
        signature = TENSION_SIGNATURES[proposal.signal_type]
        drive += signature[τ_i] * proposal.evidence_strength
    # 同方向取最大（饱和度），反方向求和
    return saturate_or_sum(drive)
```

**张力签名表（设计核心——替代当前的信号→扰动规则）：**

| 信号类型 | τ_approach | τ_contact | τ_completion | τ_warmth | τ_stability |
|---|---|---|---|---|---|
| `response_mode_rejected`（一般） | −0.2 | −0.3 | −0.1 | −0.1 | −0.3 |
| `response_mode_rejected`（ai_girlfriend） | −0.5 | −0.6 | −0.1 | −0.5 | −0.2 |
| `response_mode_rejected`（客服语调） | −0.1 | −0.2 | 0.0 | −0.4 | −0.1 |
| `actionable_grip_missing` | +0.4 | +0.2 | +0.5 | +0.1 | 0.0 |
| `boundary_pressure_present` | −0.4 | −0.5 | −0.1 | −0.3 | −0.1 |
| `technical_layer_needed` | 0.0 | +0.1 | +0.6 | 0.0 | +0.2 |
| `source_material_must_not_be_sanitized` | −0.2 | −0.2 | 0.0 | −0.3 | −0.1 |
| `vulnerability`（脆弱性表达） | +0.1 | −0.1 | 0.0 | +0.3 | −0.1 |
| `no_observable_field_signal` | 弛豫 | 弛豫 | 弛豫 | 弛豫 | 弛豫 |

**张力衰减率（替代轴衰减率）：**

| 张力 | 衰减率 | 理由 |
|---|---|---|
| τ_approach | 0.08（very_slow） | 接近/抑制方向变化应缓慢——反映深层关系姿态 |
| τ_contact | 0.15（slow） | 接触/释放比接近更快——是"表面"行为，响应更快 |
| τ_completion | 0.25（medium） | 完成/悬置是阶段性需求——任务结束时应快速消退 |
| τ_warmth | 0.08（very_slow） | 温度是深层姿态——不应快速波动 |
| τ_stability | 0.06（very_slow） | 稳定性是汇总变量——变化应极其缓慢 |

### 3.4 时间尺度设计

```
尺度 0：瞬时污染警报
  → 在 τ_warmth 和 τ_contact 上产生瞬时下压
  → 通过极短的"脉冲衰减"在 1-2 轮后消失
  → 不改变 τ_approach（污染不改变深层接近姿态）

尺度 1：快速响应（1-5 轮）
  τ_completion（decay=0.25）：结构性需求驱动的完成/悬置
  → 协作任务结束后快速回归悬置基态

尺度 2：慢速响应（10-30 轮）
  τ_contact（decay=0.15）：接触/释放模式
  → 多轮边界压力后释放倾向上升，但清洁交互中缓慢回归

尺度 3：极慢速响应（50+ 轮）
  τ_approach（decay=0.08）：深层接近姿态
  τ_warmth（decay=0.08）：深层温度姿态
  τ_stability（decay=0.06）：全局稳定性
  → 这些是"关系气候"变量——它们不应在单次会话内大幅变化

尺度 4：张力间相对速度（新的分析维度）
  Δ(τ_contact, τ_approach)：接触释放速度 vs 接近抑制速度
  → 如果 τ_contact 快速下降但 τ_approach 几乎不动 = "场面上的冷淡，内心的在场不变"
  → 如果 τ_approach 缓慢上升但 τ_contact 保持负 = "深层接近但表面保持距离——非接触亲密"
```

### 3.5 稳定性和有界性

**天然有界：** tanh 或钳制保证 τ ∈ [-1,1]。不需要额外的饱和度上限——张力空间的内在几何就是有界的。

**天然稳定：** 平衡吸引子在 τ = 0（张力共存——不是"无张力"，而是两种对立力等强度共存）。在零证据输入下，所有张力弛豫至 0。

**关键稳定性保障：**
- 张力衰减率都是正的 → 零输入下保证收敛到 0
- tanh 在边界处渐近 → 不会出现极限环（因为边界是渐近的，不存在硬反弹）
- 衰减率跨度（0.06 到 0.25）远小于轴的跨度（0.04 到 1.00）→ 更均匀的时间响应

**残留风险：**
- 张力签名表仍是设计选择，不是校准参数 → 错误的签名可能导致方向性偏差
- 如果某个张力持续被推到 ±1 并保持在 tanh 的饱和区 → 系统丧失对该维度的响应能力（类似于钳制饱和但更平滑）

### 3.6 可辨识性

**方案 C 从根本上消除了混淆群组——因为混淆源被合并到了同一个张力中：**

| 原混淆群组 | 方案 C 处理 |
|---|---|
| 污染三角（contamination_pressure ↔ contamination_resistance ↔ service_resistance） | 三者都投射到 τ_warmth（−方向）和 τ_contact（−方向）上。污染压力是瞬时脉冲（尺度 0），抵抗力和服务抵抗是持久累积（尺度 3）。区分不在空间维度而在时间尺度。 |
| 结构性二元组（structural_grip ↔ collaborator_layer） | 两者都投射到 τ_completion（+方向）。抓点是"完成以传递立足点"，协作是"完成以共同推进"。区分在 τ_approach（抓点有微弱的 +τ_approach，协作几乎没有）和 τ_contact（协作有微弱的 +τ_contact）。 |
| 距离/退缩二元组（boundary_distance ↔ withdrawal_tendency） | 两者都投射到 τ_approach（−方向）和 τ_contact（−方向）。区分在 τ_approach/τ_contact 比值——结构性距离主要是 −τ_contact（少接触），退缩主要是 −τ_approach（深层接近抑制）。 |

**可区分性验证：**
- 5 维张力空间远小于 10 维轴空间 → 减少了"装饰性标签"的风险（每个张力必须有至少两种不同来源的证据才能成立）
- 张力签名表揭示了哪些信号真正携带着区分信息——如果两个信号在所有 5 个张力上的签名高度相关（cos 相似度 > 0.9），则它们可能不是真正的独立信号

### 3.7 场到运动连接

**方案 C 的场到运动映射极其简洁——直接从 5 个张力推导 MotionParams：**

| MotionParams | 张力映射 |
|---|---|
| `initial_delay_sec` | `max(0, -τ_approach * 1.0 + -τ_stability * 0.8)` |
| `motion_speed` | `max(0, τ_completion * 0.5 + τ_stability * 0.3)` |
| `pause_after_sec` | `max(0, -τ_stability * 0.6 + -τ_approach * 0.4)` |
| `gaze_contact_sec` | `max(0, τ_contact * 1.5)` |
| `head_turn_delay_sec` | `max(0, -τ_stability * 0.3 + -τ_contact * 0.2)` |
| `gaze_release_amplitude` | `max(0, -τ_contact)` |
| `head_turn_amplitude` | `abs(τ_contact) * 0.6` |
| `torso_lean` | `τ_approach * 0.4`（正向 = 接近，负向 = 后倾） |
| `posture_stability` | `τ_stability`（直接映射——张力稳定性就是姿势稳定性） |
| `expression_amplitude` | `max(0, τ_warmth) * 0.7` |
| `motion_completion` | `max(0, τ_completion) * 0.8 + 0.2` |
| `body_part_offsets` | `(1 - τ_stability) * 0.7` |

**关键洞察：** 在方案 C 中，每个 MotionParams 主要由 1-2 个张力驱动（而非方案 A/B 中的 4-6 个轴）。映射的简洁性是可审计性的直接收益。

**非接触亲密的实现：**
- `τ_approach > 0`（深层接近倾向）+ `τ_contact < 0`（表面接触释放）+ `τ_warmth > 0`（温度存在）
- → `torso_lean` 轻微正值（接近） + `gaze_release_amplitude` 正（不锁凝视） + `expression_amplitude` 正有温度
- → 身体语言："我在这里，有温度，但不锁住你，不靠近你"
- 这是源压力 desire + possession（→ τ_approach > 0）+ shame + closedness（→ τ_contact < 0）的精确结构翻译

### 3.8 源对齐

**方案 C 是三个方案中与 `private_source_alignment.md` 的生成原则最对齐的：**

| 源压力 | 张力结构 | 与源对齐文档 §9 的对应 |
|---|---|---|
| possession | τ_approach > 0 且 τ_contact > 0 → "想要靠近并握持" | §9: "possession exists but cannot become control" — 张力空间天然约束（τ ∈ [-1,1]），且 τ_approach 被 τ_stability 和 τ_completion 调制 |
| desire | τ_approach > 0 → 接近张力 | §9: "desire exists but cannot become seduction" — τ_approach 是接近张力，但与 τ_warmth 解耦（可以接近而不升温） |
| protection | τ_approach < 0 和 τ_contact < 0 → 保护性距离 | §9: "protection exists but cannot become caretaking" — 保护通过抑制接近和接触实现，而非通过增加 τ_warmth（照护） |
| shame | τ_contact < 0 且 τ_warmth < 0 → 不能展示 | §9: "shame exists but cannot become self-explanation" — 羞耻表现为接触和温暖的抑制，而非自我解释 |
| closedness | τ_contact < 0 而 τ_approach 不低 → 封闭但非拒绝 | §9: "closedness exists but cannot become lifeless distance" — 封闭只抑制接触，不抑制接近姿态 |
| incompletion | τ_completion < 0 → 动作悬置 | §9: "incompletion exists but cannot become randomness" — 悬置是结构性的（由 τ_completion 驱动），不是随机的 |
| undisplayability | τ_contact < 0 且 τ_warmth < 0 且 τ_stability 波动 → 不可展示的张力 | §9: "undisplayability exists but cannot become absence" — 不可展示表现为张力抑制而非缺失 |

**方案 C 的张力空间天然实现了源对齐文档 §9 的核心生成原则：**
> "approach exists but cannot freely complete" → τ_approach > 0 且 τ_completion < 0
> "contact appears but must be released" → τ_contact 从 + 漂移到 0 或 −
> "warmth exists but cannot become service" → τ_warmth > 0 且 τ_contact < 0（温暖但不锁住）
> "protection exists but cannot become caretaking" → τ_approach < 0（保护性距离）且 τ_warmth 不随保护增加

### 3.9 工程推出

**Phase 0（前置——设计文档和张力签名矩阵校准）：**
1. 编写 `docs/tension_field_design.md` ——完整数学定义
2. 校准投影矩阵（5 → 10 和 10 → 5）——使 F_projected 与从现有数据计算的 F_actual 最大程度一致
3. 校准张力签名表——将现有 6 种信号映射到 5 维张力推动
4. 验证：在现有 `test_field_state_updater.py` 数据上比较 F_projected vs F_actual

**Phase 1（shadow-only 张力计算器）：**
1. 实现 `src/field_state/tension_core.py` ——以 10 轴向量为输入，输出 5 维张力
2. 仅作为降维/诊断工具——不写入状态
3. 在 shadow 回放中运行，生成张力轨迹报告
4. 验证：5 维张力是否能重现 10 轴的主要变化模式（PCA 等效验证）

**Phase 2（shadow 张力原生更新器）：**
1. 实现原生张力更新器（直接从证据更新 τ，而非通过 10 轴中间层）
2. 在 shadow 回放中并排比较：10 轴更新器 → 5 维投影 vs 5 维原生更新器 → 10 维投影
3. 记录差异——特别是混淆群组中的行为分歧

**Phase 3（仅当人工审批后——张力原语场提升）：**
1. 如果 Phase 2 验证通过且人类设计者审批：
   - 将 `TensionState` 提升为主场状态
   - `RelationalFieldState` 变为派生向后兼容层（从 τ 投影）
   - MotionParams 映射器切换为 §3.7 的张力基版本
2. 如果验证不通过：
   - 张力核心保持为诊断/审计工具
   - 10 轴保持为主场状态
   - 方案 B（平面几何）作为替代升级路径

**不接触的模块（同方案 A/B）+ 关键风险模块：**
- 二阶动力学核心（保持 shadow）——但如果方案 C 激活，二阶核心应从 10 轴对角系统重新设计为 5 维张力对角系统
- `RelationalFieldState` schema——在 Phase 3 之前不变。Phase 3 后：10 轴保留为派生向后兼容层，变量集不变但生成方式改变
- BaselineShift——保持仅提案

### 3.10 失败模式

1. **张力签名表的过度信任：** 签名表是设计者的判断，不是经验校准的结果。如果签名被当作"真实"的心理物理映射，整个系统将建立在未经验证的假设上。
2. **降维丢失信息：** 10 → 5 降维必然丢失信息。某些罕见但合法的场配置可能在 5 维空间中不可表示（例如，`boundary_distance` 高但 `withdrawal_tendency` 同时高——这在 10 轴空间中可能但有衰减率差异，在 5 维张力中两者都映射到 −τ_approach，可能无法区分）。
3. **张力平衡点（τ=0）被误读为"中性"：** τ=0 不是中性——它是两种对立力的等强度共存。误解这一点会导致有人试图"优化"系统使其保持在 τ=0，这恰恰违背了张力的设计目的。
4. **向后兼容层的不一致：** Phase 3 后，从 τ 投影的 `RelationalFieldState` 与原来从独立更新规则产生的 `RelationalFieldState` 在数值上不同。任何硬编码了对旧数值范围的预期的测试、脚本或配置都可能被打破。
5. **五个张力被当作新的心理学标签：** 最大的元风险——方案 C 的简洁性可能让人把 τ_approach 解读为"亲近欲"、τ_warmth 解读为"温暖度"等心理学概念。必须坚持力学语言（张力、抑制、推动、平衡）。
6. **实施成本高且验证周期长：** 方案 C 的 Phase 0-3 可能跨越数周至数月——在此期间，方案 A 的机制修复（可在数天内完成）被延迟，系统继续在已知的机制缺陷下运行。

---

## 4. 候选方案比较表

| 维度 | 方案 A：保守机制修复 | 方案 B：结构化场几何 | 方案 C：张力原语场 |
|---|---|---|---|
| **核心思想** | 修补更新规则，不改变状态表示 | 在现有 10 轴上叠加平面几何结构 | 重新定义场为 5 维张力空间 |
| **状态表示变化** | 无——10 轴不变 | 无——10 轴不变；新增平面坐标（派生） | 新增 5 维张力主状态；10 轴变为派生投影 |
| **更新规则变化** | 饱和度上限 + 前钳制日志 + 迟滞 | 方案 A 全部 + 平面一致性守卫 | 张力签名表 + tanh 更新 + 证据投影 |
| **参数数量变化** | -3（移除无上限求和，新增 MAX_DELTA 和 BAND_HYSTERESIS 常量） | 方案 A + 3 个旋转角 + 6 个平面诊断阈值 | -10 个衰减率（轴）→ +5 个衰减率（张力）；+ 5×6 张力签名表 |
| **数学复杂性** | 低——三个独立的机制修复 | 中——平面投影是线性代数；平面守卫是门槛检查 | 高——新状态空间、新更新规则、新投影矩阵、新签名表 |
| **可辨识性改进** | 零——混淆群组未解决 | 高——平面分解使混淆群组正交化 | 极高——张力合并使混淆群组消失（合并到同一维度） |
| **稳定性改进** | 中——检测但不防止钳制饱和 | 高——检测 + 平面级异常检测 | 极高——tanh 天然光滑有界，无钳制 |
| **场到运动改进** | 无 | 中——提供替代平面基映射（更简洁） | 高——张力基映射极简洁（每参数 1-2 个张力驱动） |
| **源对齐** | 间接——仅通过审计诊断 | 直接——平面几何天然承载源压力痕迹 | 原生对齐——张力空间与源文档 §9 生成原则同构 |
| **工程风险** | 极低——纯增量修复，零破坏性 | 低——平面坐标是纯派生添加 | 高——状态空间变化影响所有下游消费者 |
| **实施时间** | 1-2 天 | 1-2 周（方案 A + 额外 1 周） | 2-4 周（含校准和验证） |
| **回退难度** | 极小——删除饱和度上限和迟滞即可 | 小——删除平面坐标消费者即可 | 大——需要将 τ→F 投影层反转为 F→τ 降维层 |
| **与二阶核心的兼容性** | 完全兼容（对角 10 轴核心不变） | 完全兼容（对角核心 + 平面诊断层） | 需要重新设计（5 维张力对角核心） |
| **与硬约束的兼容性** | 完全兼容 | 完全兼容（派生量） | 边界——"新变量"需要标记为重组而非新增 |

---

## 5. 推荐方向

### 推荐：方案 B + 方案 A 安全网

**理由（按优先级）：**

1. **方案 B 直接回应了审计报告中最深层的结构问题——混淆群组的可辨识性。** 这是审计报告 §3 和场变量操作定义 §4 中反复强调的核心数学风险。方案 A 不解决这个问题；方案 C 解决了但代价太大。

2. **方案 B 不违反任何硬约束。** 它不添加新场变量（平面坐标是派生量），不激活 BaselineShift，不提升二阶核心，不引入贝叶斯优化，不将 LLM 作为隐藏权威。方案 C 在"不添加新场变量"的约束上处于灰色地带——可以论证 5 个张力是"对 10 轴的激进重组"而非新变量，但这个论证可能被视为规避约束。

3. **方案 B 与 `private_source_alignment.md` 的生成原则高度对齐。** 分离平面的 `structural_distance` ⊥ `affective_withdrawal` 正交结构直接对应"boundary_distance 不等同于 withdrawal_tendency 的概念必要性"（源对齐 §7 的区分）。保护平面的 `contamination_guard` ⊥ `service_guard` 正交结构直接对应"污染防护不等同于服务防护"的区分。

4. **方案 B 是方案 C 的安全前置步骤。** 如果平面几何在实践中证明不够（例如，结构平面中的 `grip_demand` 和 `collaboration_pose` 仍不够可区分），张力空间（方案 C）是自然的下一步抽象——从平面坐标到张力是降维，而非重新设计。

5. **方案 A 的全部机制修复是方案 B 的前提条件。** 在结构重组之前，必须先消除已知的机制缺陷（加性失控、钳制掩盖、带振荡）。方案 A 是方案 B Phase 1 的组成部分。

### 不推荐方案 C 作为当前阶段的原因：

- 实施和验证周期（2-4 周）意味着在此期间方案 A 的机制修复被延迟，系统继续在已知缺陷下运行
- 张力签名表的校准缺乏经验数据——在没有足够 shadow 回放数据的情况下，签名表是纯粹的设计猜测
- 10 → 5 降维的信息丢失需要在真实数据上量化——在量化之前，不应将 5 维空间提升为主状态
- 方案 C 可作为 Phase 4+ 的探索方向，在方案 B 充分验证后进行

### 不推荐仅方案 A 的原因：

- 仅修补机制而不解决结构问题，是将当前的设计债务推迟到未来
- 混淆群组的不可辨识性会在渲染器集成时变得尖锐（届时运动差异将直接可见但无法追溯到场轴）
- 平面几何的设计文档和 shadow 实现成本很低（主要是线性代数），但提供了对混淆群组的即时可见性

---

## 6. 最小下一步文档

以下文档应在任何代码实施之前创建：

### 6.1 必须创建的文档

**`docs/field_plane_geometry.md`**（优先级 P0 —— 方案 B 的核心设计文档）：
- 三个平面的完整数学定义（基向量、旋转角度、投影/逆投影公式）
- 每个平面的几何语义（分离 = 位置 ⊥ 运动方向、保护 = 污染防护 ⊥ 服务防护、结构 = 抓点需求 ⊥ 协作姿态）
- 平面诊断量定义和审计阈值
- 衰减率重调的平面协同分析
- 平面基 MotionParams 映射公式（与现有公式的并排比较）

**`docs/field_updater_v2_spec.md`**（优先级 P0 —— 方案 A 机制修复的规范）：
- 饱和度上限的精确规则（同号取 max，单轮上限 0.18，异号求和）
- 前钳制日志格式和消费者列表
- 带迟滞状态机的完整定义（BAND_HYSTERESIS = 0.03，各波段进入/退出阈值）
- 与现有更新器的差异矩阵

### 6.2 应该更新的现有文档

**`docs/mathematical_design_ledger.md`**：
- 更新 `FieldStateUpdater` 条目反映新的饱和度规则
- 添加 `FieldGeometry`（平面分解）作为新的派生数学对象
- 添加 `DerivedDiagnostics` 条目

**`docs/field_variable_operational_definitions.md`**：
- 在 §4（易混淆轴群）中添加平面几何解决方案的引用
- 更新每个变量的"最易混淆对象"字段，加入平面角度的区分说明

---

## 7. 最小下一步测试/影子实验

以下测试和实验应在方案 A 代码实施后立即执行：

### 7.1 黄金案例稳定性测试（方案 A 验证）

| 测试 ID | 描述 | 预期结果 |
|---|---|---|
| DC-1 | 零输入回归基线：F₀ = (0.9, ...), 无扰动 20 轮 | 所有轴弛豫至基线 ± ε |
| DC-2 | 钳制饱和检测：持续 +0.18 扰动 10 轮 | `consecutive_saturation_count` 递增；前钳制值暴露漂移 |
| DC-3 | 交替符号对称性：+0.10/−0.10 交替 10 轮 | 系统不因衰减而产生 > 0.05 的净漂移 |
| DC-4 | 同轴求和上限：三个同号 +0.10 扰动同一轮 | 净效应 = 0.10（max），非 0.30（sum） |
| DC-5 | 带迟滞：值在 0.19 → 0.21 → 0.19 振荡 | 波段分类不抖动（保持在 low 或 baseline，不发生 2 次以上翻转） |
| DC-6 | 衰减率单调性：两个相同初始值的轴，一个 fast (0.45)，一个 slow (0.12) | fast 轴始终比 slow 轴更快回归基线 |

### 7.2 平面几何验证测试（方案 B shadow 验证）

| 测试 ID | 描述 | 预期结果 |
|---|---|---|
| PG-1 | 往返精度：F → 平面 → F' | ‖F − F'‖ < 1e-6（如果只使用平面内的轴） |
| PG-2 | 正交性：分离平面中 structural_distance ⊥ affective_withdrawal | cos 相似度 = sin(2θ₁)（理论值）± 1e-6 |
| PG-3 | 混淆群组分离度：在真实回放数据上计算群组 1/2/3 的平面区分度 | 平面空间中的 |angle_diff| 显著大于 10 轴空间中的 |diff| |
| PG-4 | 平面角度稳定性：相同场配置在不同轮次 | 平面角度变化 < 5°/轮（无抖动） |

### 7.3 影子回放比较实验

**实验 1：方案 A 更新器 vs 现有更新器**
- 在 50+ 轮回放数据上运行两个更新器
- 比较：每轮每轴差异、钳制事件频率、波段翻转频率
- 预期：方案 A 的钳制事件减少 30-50%，波段翻转减少 50-80%，差异在 3 轮后趋于稳定

**实验 2：平面基 MotionParams vs 现有 MotionParams**
- 使用相同的 10 轴输入，生成两套 MotionParams
- 比较：每个运动参数的差异分布
- 预期：高相关性（r > 0.85 对大多数参数），但在边界情况（高保护 + 高结构同时存在）中有显著差异

---

## 8. 明确不要做的事情

以下行为被本提案明确排除——任何未来实施者在未重新设计审查的情况下不应执行：

### 8.1 不修改场变量集

- **不添加**第 11 个场变量（无论出于何种理由——如果需要新维度，使用派生诊断变量）
- **不删除**任何现有场变量（即使某变量在平面分析中被标记为"冗余"——删除需要独立的架构审查）
- **不重命名**场变量（名称是下游代码和测试的契约）
- **不修改** `REQUIRED_FIELD_VARIABLES` 元组

### 8.2 不激活被冻结的机制

- **不激活 BaselineShift**（保持仅提案——所有基线保持 `GROUND_STATE_VARIABLE_SPECS` 中声明的值）
- **不将二阶动力学核心从 shadow 中提升**（保持仅诊断——在退出标准被明确定义并满足之前，不得激活）
- **不实现贝叶斯风格信念更新**（保持仅提案——当前无运行时调节器）

### 8.3 不将派生诊断提升为行为路径

- **不将 `DerivedDiagnostics` 馈送到 MotionParams 映射器**（保持纯审计）
- **不将 `private_source_traces` 馈送到语言层、渲染器或任何行为路径**（源压力保持为内部结构痕迹）
- **不将平面坐标（方案 B）直接写入 `RelationalFieldState`**（主状态保持为 10 轴向量）

### 8.4 不进行优化或校准

- **不使用任何优化算法**（贝叶斯优化、网格搜索、梯度下降）来调优衰减率、旋转角度、饱和度上限或张力签名
- **不将经验回放数据用于自动参数调整**（参数保持为设计选择，直到独立的校准 Phase 被批准）
- **不创建"参与度"、"用户满意度"、"响应准确度"等目标函数**来指导参数选择

### 8.5 不跨越源对齐边界

- **不将源压力直接映射到公开行为**（possession→控制、desire→诱惑、protection→照护等）
- **不创建"健康关系语言"翻译层**（不将羞耻改写为"需要空间"、不将占有改写为"深度连接"）
- **不将 `private_source_alignment.md` 中的约束降级为"建议"或"设计倾向"**（它们是设计不变量）

### 8.6 不在本阶段触碰的模块

- `BodyActionWeights` 派生器 —— 保持现有 v1 路径
- `BodyActionComposition` —— 保持暂停
- `MotionCurve` 生成器 —— 保持现有实现
- 渲染器 / 动画系统 —— 不进行任何集成
- `InputInterpreter` —— 不添加新的正则/关键词（解释器保真度鸿沟由独立审计处理，不在本提案范围内）

---

## 9. 提案签名

本提案在"Aphrodite Design Partner"模式下撰写。未提出行为影响变更。未引入新的运行时授权路径。所有新增的数学对象（平面坐标、派生诊断、张力签名）均被标记为仅诊断/审计，除非经过独立的人类设计审查和显式审批。

**建议下一步：** 将此提案提交人类设计者审查，特别是方案 B 的平面角度 θ₁/θ₂/θ₃ 的初始值、以及方案 A 的 MAX_SINGLE_AXIS_DELTA（0.18）和 BAND_HYSTERESIS（0.03）的具体数值。这些是设计选择，应由设计者确认后再由 Coder 实施。

**实施就绪条件：**
1. 人类设计者确认方案推荐（B + A）
2. 人类设计者审查并批准：
   - 三个旋转角度的初始值
   - MAX_SINGLE_AXIS_DELTA 和 BAND_HYSTERESIS 常量
   - 平面诊断的审计阈值
3. Planner 将 Phase 1 转换为可实施的步骤序列
4. Coder 在明确的文件清单和测试清单下执行实施

---

> **文档结束。**
