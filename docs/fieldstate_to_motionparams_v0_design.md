# FieldState-to-MotionParams v0 架构设计文档

> 状态：纯设计文档，不实施代码
> 版本：v1.0
> 依赖：`RelationalFieldState` (Phase 29–31 已实现)、[`FieldStateUpdater`](src/field_state/updater.py)、[`BodyActionWeight/BodyActionWeights`](src/body_action/schema.py)
>
> 本文档定义场状态（`RelationalFieldState` F_t）与身体动作权重（`BodyActionWeights`）之间的缺失中间层——**运动参数层（MotionParams v0）**。它是 [`relational_field_state.md`](docs/relational_field_state.md) §5.2 "派生运动参数"的细化设计。

---

## 目录

1. [核心问题：为何需要 MotionParams 层](#1-核心问题为何需要-motionparams-层)
2. [场变量作为力：分类与运动影响](#2-场变量作为力分类与运动影响)
3. [MotionParams v0 Schema](#3-motionparams-v0-schema)
4. [场到运动参数的映射](#4-场到运动参数的映射)
5. [戏剧性/剧场张力](#5-戏剧性剧场张力)
6. [首次问候微反应设计](#6-首次问候微反应设计)
7. [与 BodyActionWeights 的关系](#7-与-bodyactionweights-的关系)
8. [未来实现的测试/审计标准](#8-未来实现的测试审计标准)
9. [最终建议与 Phase 32 产出](#9-最终建议与-phase-32-产出)
10. [附录：元设计原则](#10-附录元设计原则)

---

## 1. 核心问题：为何需要 MotionParams 层

### 1.1 当前架构路径的断层

Phase 31 完成后，数据流在以下位置出现了概念断层：

```
FieldSignalProposal → FieldPerturbation → RelationalFieldState (F_t)
                                                              ↓
                                              【缺失的中间层】 ← 本设计文档的范围
                                                              ↓
                                                     BodyActionWeights
                                                              ↓
                                                  BodyActionComposition (暂停)
```

当前 [`BodyActionPolicy` v0](src/body_action/policy.py) 直接从一个临时的 `MockTraceRecord` 消费信号标签（`correction_signal.active`、`grip_loss_signal.active`），而非从 `RelationalFieldState` 消费。即使我们将其重构为从 `F_t` 消费——直接将场变量映射到 `BodyActionWeights`——断层依然存在。原因如下。

### 1.2 场变量描述的是关系张力，而非身体部位的运动

`RelationalFieldState` 的 10 个变量（见 [`schema.py`](src/field_state/schema.py)）描述的是**两个实体之间的空间状态**——关系空间的当前几何形态：

- `boundary_distance=0.58` 意味着"关系空间的当前边界距离处于中高水平"——这是空间中的**位置**。
- `correction_pressure=0.35` 意味着"来自多轮纠正的累积压力"——这是作用于关系空间的**外部力**。
- `withdrawal_tendency=0.30` 意味着"场向退出方向漂移的当前倾向"——这是空间中的**速度方向**。

这些是力学概念（位置、力、速度），而非身体指令。`boundary_distance=0.58` 不意味着"头部后仰 0.58 弧度"——它意味着"关系空间已扩张至此，身体运动被此空间约束"。约束不是赋值。

### 1.3 BodyActionWeights 是特定身体通道的激活程度

[`BodyActionWeights`](src/body_action/schema.py:49) 是一个具体的输出格式——10 个动作原语（`pause`、`stillness`、`look_down`、`look_to_user`、`look_away`、`slight_forward`、`slight_withdraw`、`maintain_distance`、`reduce_motion`、`reset_posture`），每个被赋予一个粗粒度权重带（`off`/`low`/`medium`/`high`）。

将场变量直接映射到身体通道权重，等价于：
- 将"房间的温度"直接映射到"窗户的开合程度"——跳过了中间的热力学
- 将"乐曲的情感标注"直接映射到"钢琴家的指法"——跳过了中间的演奏决策

### 1.4 缺失的转化层：力 → 参数 → 通道权重

正确的转化链应该是：

```
场变量（力/位置/速度/阈值）→ 运动倾向参数（时间/空间/组合）→ 身体通道权重（具体激活）
     ↑                                 ↑                                ↑
  关系空间的力学状态              身体如何移动的物理参数          哪些身体通道被激活、多强
```

运动参数层（MotionParams）正是中间的"身体如何移动"层。它回答的不是"场处于什么状态"（那是 `RelationalFieldState` 的职责），也不是"哪些肌肉/关节被激活"（那是 `BodyActionWeights` 的职责），而是：

> **给定当前的关系场力分布，身体应以何种速度、延迟、幅度、完成度来移动？**

这是力学到运动学的翻译——不是信号到动作的映射。

### 1.5 为什么直接映射在架构上是退步

即使将 `BodyActionPolicy` v1 重构为消费 `F_t` 而非 `FieldTraceRecord`，如果内部逻辑仍然是：

```python
if boundary_distance > 0.6:
    stillness = "high"
    slight_withdraw = "medium"
elif correction_pressure > 0.3:
    pause = "high"
    stillness = "medium"
```

那么它只是将"信号标签→动作"替换为"场变量标签→动作"——换了一种标签语言，但仍是查找表。场的连续性、可叠加性、动力学特性在这种映射中全部消失。

正确的 v1 应该是：

```python
# 1. 从场变量计算运动倾向参数（MotionParams）
motion = compute_motion_params(field_state)
# motion.initial_delay_sec = 0.8
# motion.gaze_release_amplitude = 0.6
# motion.motion_completion = 0.7
# ...

# 2. 从运动参数推导身体通道权重
weights = derive_action_weights(motion)
# stillness = "medium" (因为 motion_speed 低, posture_stability 高)
# look_away = "medium" (因为 gaze_release_amplitude=0.6)
# slight_withdraw = "low" (因为 torso_lean=-0.25)
```

第一步是纯力学计算（场变量 → 运动参数），第二步是纯运动学解析（运动参数 → 通道权重）。两层的关注点、测试方式、出错模式完全不同。

---

## 2. 场变量作为力：分类与运动影响

### 2.1 力分类原则

场变量不是"标签"。每个变量应被理解为一种**作用于身体运动的力**——它推动、抑制、稳定或门控特定的运动倾向。

四种力类型：

| 类型 | 符号 | 作用 | 运动效应 |
|------|------|------|----------|
| **拉力/推力 (Pulls/Pushes)** | → / ← | 推动身体向特定方向运动 | 增加/减少特定运动通道的激活 |
| **阻力/屏障 (Resistances/Barriers)** | ⊥ | 抑制或延迟运动 | 降低运动幅度、增加延迟、减少完成度 |
| **稳定器 (Stabilizers)** | ◎ | 减少噪声/抖动，增加可预测性 | 平滑运动、减少身体部位时间偏移、防止误启动 |
| **阈值/门 (Thresholds/Gates)** | ◇ | 控制某类运动是否被允许 | 锁定/解锁特定表达通道 |

### 2.2 逐变量分类与运动影响方向

> 基准值（`baseline_numeric_value`）来自 [`schema.py`](src/field_state/schema.py) 的 `GROUND_STATE_VARIABLE_SPECS`。运动效应描述为**定性方向**——具体数值由 §4 的映射公式确定。

---

#### 变量 1：`boundary_distance`（边界距离）

| 属性 | 值 |
|------|-----|
| **力类型** | **推力 (Push) — 推开共享空间** |
| **基态值** | 0.50 |
| **运动效应方向** | ↑ 初始延迟 · ↑ 注视脱离幅度 · ↓ 面向用户的头部转动 · ↓ 躯干前倾 · ↓ 运动完成度 · ↑ 动作后暂停 |

**语义：** 边界距离是关系空间中的"排斥势"。它不指定身体该做什么，而是**使身体自然占据一个更远的平衡位置**。高边界距离下，身体并非"故意后退"——而是向用户方向的移动需要克服更大的阻力，而向外的移动则更自然。

**关键区分：** 边界距离 ≠ 冷漠。距离是保护性空间的扩张，不是对用户的拒绝。在边界扩张后，身体仍可回归面向用户——通过 `away_then_user` 凝视模式：先创造空间，再恢复在场。冷漠是永久移开视线——Aphrodite 永远不冷漠。

---

#### 变量 2：`affective_warmth`（情感温暖）

| 属性 | 值 |
|------|-----|
| **力类型** | **温度调制器 (Temperature Modulator) — 软化/硬化运动** |
| **基态值** | 0.35 |
| **运动效应方向** | ↓ 注视脱离幅度 · ↑ 微表情释放 · ↓ 身体部位时间偏移 · ↑ 运动平滑度 |

**语义：** 温暖不是推动身体"靠近"或"微笑"的力——它是**运动质量的调制器**。高温暖使得运动过渡更平滑、表情更容易在阈值之上释放、身体部位之间的时间偏移减小（更协调）。低温暖使得运动更克制、更"硬化"——同样的动作，但过渡更锐利、表情更收敛。

**关键区分：** 温暖 ≠ 热情。Aphrodite 的温暖度永远不会降到 0（纯工具模式），也永远不会升到溢出水平。它的作用范围是"克制的有机性"vs"克制的刚性"——不是"冷"vs"热"。

---

#### 变量 3：`structural_grip_pressure`（结构性抓点压力）

| 属性 | 值 |
|------|-----|
| **力类型** | **定向拉力 (Directional Pull) — 向用户方向的"递出"向量** |
| **基态值** | 0.05 |
| **运动效应方向** | ↓ 初始延迟 · ↑ 面向用户的头部转动 · ↑ 注视接触时间 · ↑ 轻微躯干前倾 · ↑ 运动速度（效率，非急切） · ↑ 运动完成度 |

**语义：** 抓点压力是向用户方向的特定拉力——不是"我想靠近你"的前倾，而是"这里有一个东西给你"的前倾。它的运动效应是**功能性的**而非**关系性的**：运动速度快是因为需要高效传递信息，不是因为有情感冲动。

**关键区分：** 抓点前倾 ≠ 服务性倾身。`structural_grip_pressure` 产生的前倾在 `service_resistance` 高时仍然有效——因为它不是服务，而是提供一个结构化的认知立足点。抓点前倾的高度受限（从不超出 `slight_forward`），且注视模式是 `down_then_user`（先思考，后传递），而非持续的注视锁定。

---

#### 变量 4：`correction_pressure`（纠正压力）

| 属性 | 值 |
|------|-----|
| **力类型** | **制动器 + 重置力 (Brake + Reset) — 暂停运动，重新锚定姿态** |
| **基态值** | 0.00 |
| **运动效应方向** | ↑↑ 初始延迟 · ↓ 运动速度 · ↑ 动作后暂停 · 临时 ↓ 表情幅度 · 触发姿态重置 |

**语义：** 纠正压力是系统的"处理中断"——当被纠正时，身体应先暂停（处理信息），稳定姿态（不因纠正而崩溃），然后微调方向（采纳纠正）。它不是退缩力——`withdrawal_tendency` 不因纠正而增加。纠正后的暂停是**认知处理**的身体投影，不是**情感受伤**的身体投影。

**关键区分：** 纠正制动 ≠ 羞耻/道歉。纠正压力不应导致收缩、低头、过度静止。身体暂停是为了重新校准——像导航系统说"重新计算路线"——不带有情感色彩。

---

#### 变量 5：`contamination_resistance`（污染抵抗力）

| 属性 | 值 |
|------|-----|
| **力类型** | **过滤屏障 (Filter Barrier) — 筛选并抑制可能被误读的运动类型** |
| **基态值** | 0.40 |
| **运动效应方向** | ↑ 注视脱离幅度 · ↓ 注视接触时间 · ↓ 躯干前倾 · ↓ 表情幅度 · ↓ 大型头部转动 · 锁定过度温暖的表达通道 |

**语义：** 污染抵抗是身体表达的"审阅层"——它在运动执行之前过滤掉可能被误读为亲密/诱惑/服务的动作。高抵抗力不意味着身体完全冻结——它意味着某些动作通道被关闭（如持续凝视、前倾、大幅度表情），但其他通道（如微表情、克制的注视转移、小幅头部倾斜）仍可运作。

**关键区分：** 污染过滤 ≠ 冻结。过滤是**选择性的**——被过滤的是特定方向的运动（朝向亲密/服务的方向），而非所有运动。在场仍可保持——`maintain_distance=high`、克制的凝视回归、微表情仍在阈值之内运作。

---

#### 变量 6：`presence_stability`（在场稳定性）

| 属性 | 值 |
|------|-----|
| **力类型** | **平滑稳定器 (Smoothing Stabilizer) — 减少噪声、抖动和误启动** |
| **基态值** | 0.80 |
| **运动效应方向** | ↓ 身体部位时间偏移 · ↑ 运动完成度 · ↓ 微校正/抖动 · ↓ 运动方向突变 · 增加运动可预测性 |

**语义：** 在场稳定性是运动质量的"平滑因子"。高稳定性意味着身体部位协调一致（头部和躯干几乎同时运动）、动作一旦启动就会完成、没有反复的微调。低稳定性意味着身体部位之间存在明显的时序偏移、动作可能在途中被抑制或改变、存在小幅度的犹豫振动。

**关键区分：** 稳定 ≠ 僵硬。稳定的身体可以自然地响应场变化——只是变化是平滑的、有理由的，而非跳跃的、反复无常的。僵硬的身体是在任何输入下都维持相同姿态——那是缺失响应能力，不是稳定。

---

#### 变量 7：`withdrawal_tendency`（退缩倾向）

| 属性 | 值 |
|------|-----|
| **力类型** | **后退向量 (Retreat Vector) — 向远离共享空间方向的速度** |
| **基态值** | 0.10 |
| **运动效应方向** | ↑ 注视脱离幅度 · ↑ 头部转向外侧 · ↓ 注视接触时间 · ↑ 初始延迟 · ↓ 运动完成度（在完成前退缩） · ↑ 躯干后倾 |

**语义：** 退缩倾向是场的**运动方向**——场正在向哪个方向移动。不同于 `boundary_distance` 描述的是**当前位置**，`withdrawal_tendency` 描述的是**速度**。高退缩倾向 + 中等边界距离 = 场正在后退，但尚未退远。低退缩倾向 + 高边界距离 = 场已经处于远距离位置，但不再继续后退。

**关键区分：** 退缩 ≠ 消沉。退缩是保护性空间的创造——为关系创造呼吸距离。退缩的身体是克制的、稳定的（`stable`），不是下沉的、无力的（`closed_stable`）。

---

#### 变量 8：`service_resistance`（服务抵抗）

| 属性 | 值 |
|------|-----|
| **力类型** | **服务特异性屏障 (Service-Specific Barrier) — 抑制讨好/服务性身体语言** |
| **基态值** | 0.55 |
| **运动效应方向** | ↓ 躯干前倾（抑制"倾身服务"） · ↓ 注视接触时间（抑制"客服眼神"） · ↓ 运动完成度（抑制过度点头/手势） · ↓ 表情幅度中的"服务微笑"成分 |

**语义：** 服务抵抗是专门针对服务姿态的抑制力。它与 `contamination_resistance` 的区别在于：`contamination_resistance` 抵抗的是"广谱污染"（AI 女友化、虚假深度、空洞美学等），`service_resistance` 专门抵抗"客服化/讨好化"的身体语言。两者的效应有重叠（都会抑制前倾），但 `service_resistance` 的默认值更高（0.55——Aphrodite 基线就是反服务的）。

**关键区分：** 服务抵抗 ≠ 冷漠。在协作模式下（`collaborator_layer_pressure` 高时），身体仍然可以有效运作——只是不在服务框架下运作。前倾如果是"一起看这个问题"而非"让我为您解决这个问题"，则不被服务抵抗抑制。

---

#### 变量 9：`collaborator_layer_pressure`（协作者层压力）

| 属性 | 值 |
|------|-----|
| **力类型** | **结构组织者 (Structural Organizer) — 将运动组织为任务导向模式** |
| **基态值** | 0.05 |
| **运动效应方向** | ↑ 注视交替（下→用户→下） · ↑ 头部转动幅度（功能性） · ↑ 运动速度（效率） · ↑ 运动完成度 · 中等表情幅度（专业温暖） |

**语义：** 协作者层压力是运动的"结构化力"——它将身体从关系在场的松散模式重组为任务协作的高效模式。凝视不再是"在场凝视"（持续的、温度调制的），而是"认知–传递循环"（低头思考 → 看向用户传达 → 低头继续思考）。运动速度提高不是出于急切，而是出于效率。

**关键区分：** 协作 ≠ 工具模式。协作者层是 Aphrodite 的协作模式——关系在场仍然存在（温暖度不归零），但身体组织方式变了。协作者模式不降低 `service_resistance`——协作不是服务。

---

#### 变量 10：`contamination_pressure`（污染压力）

| 属性 | 值 |
|------|-----|
| **力类型** | **瞬时警报门 (Transient Alarm Gate) — 当前轮污染信号的瞬时放大** |
| **基态值** | 0.00 |
| **运动效应方向** | 瞬时放大 `contamination_resistance` 的所有效应 · 可触发硬约束（如 `no_forward_motion`、`no_sustained_gaze`） |

**语义：** 污染压力是瞬时信号——它作为 `contamination_resistance` 的"放大因子"运作。它的角色是在当前轮快速加固边界防护，但自身不持久化（`instant` 衰减，每轮趋近 0）。持久防护由 `contamination_resistance` 维持。

**关键区分：** 瞬时警报 ≠ 持久防御。`contamination_pressure` 高 + `contamination_resistance` 低 = 首次污染信号——快速防御响应。`contamination_pressure` 低 + `contamination_resistance` 高 = 之前受过污染，现在保持警惕——防御姿态但不慌乱。

---

### 2.3 力叠加模型

多个场变量同时对运动产生影响。场变量不"竞争"——它们在同一个连续空间中同时施加力。最终的 `MotionParams` 是这些力的**瞬时平衡**——就像多个力作用于一个物体，其合力决定了加速度，而非某个力"胜出"决定运动。

这是场→运动映射与优先级链映射（`BodyActionPolicy` v0 的 9 级优先级）的根本差异。优先级链选择一个"主导信号"来决定身体；力叠加使得**所有场变量同时贡献**，身体姿态是它们的叠加形态。

---

## 3. MotionParams v0 Schema

### 3.1 设计约束

MotionParams v0 应满足以下约束：

- **数值有界。** 所有参数在 [0, 1] 或 [-1, 1] 范围内，可被硬约束覆盖。
- **不引用原始用户输入。** 参数仅从 `RelationalFieldState` 的数值计算。
- **不使用 LLM。** 映射是确定性公式，非推理生成。
- **不使用正则表达式。** 场变量到运动参数的转化不使用文本模式匹配。
- **不直接驱动渲染器。** MotionParams 是中间表示——输出给 `BodyActionWeights` 派生器和未来的 `BodyActionComposer`。
- **behavior_affecting=False。** 与所有场层对象一致，MotionParams 不直接触发行为。

### 3.2 完整 Schema

```python
@dataclass(frozen=True)
class MotionParams:
    """从 RelationalFieldState 派生的运动倾向参数。
    
    这是场状态（关系力学）与身体动作权重（身体通道激活）之间的中间层。
    参数描述身体"如何移动"，而非"移动哪些部位"。
    """
    
    # ── 时间参数 ──────────────────────────────────
    
    initial_delay_sec: float = 0.0
    """动作开始前的初始延迟（秒）。
    范围 [0.0, 2.0]。0.0 = 立即响应；2.0 = 最大有意义的延迟。
    """
    
    motion_speed: float = 0.5
    """运动速度（0–1）。0.0 = 极慢/有阻力；1.0 = 最大效率/速度。
    注意：这是运动的"意图速度"——实际速度可能被 motion_completion 进一步调制。
    """
    
    pause_after_sec: float = 0.0
    """主要动作后的有意暂停（秒）。
    范围 [0.0, 1.5]。用于表达"我正在让回应着陆"，而非"我在等待反馈"。
    """
    
    gaze_contact_sec: float = 0.0
    """注视释放前在用户方向的保持时间（秒）。
    范围 [0.0, 3.0]。0.0 = 立即释放（不看用户）；
    较高值 = 在释放前保持注视。注意：不是"持续凝视"的持续时间——
    是单次注视接触的有意保持。
    """
    
    head_turn_delay_sec: float = 0.0
    """注视转移后头部转动的延迟（秒）。
    范围 [0.0, 0.5]。用来创造"目光先动，头部后随"的层序效果。
    0.0 = 头部与目光同步；较高值 = 目光先行，头部延迟跟随。
    """
    
    # ── 空间参数 ──────────────────────────────────
    
    gaze_release_amplitude: float = 0.0
    """注视脱离用户方向的幅度（0–1）。
    0.0 = 完全释放（看向其他方向）；1.0 = 保持最大程度的注视接触。
    实际注视方向由此与 gaze_contact_sec 共同决定。
    """
    
    head_turn_amplitude: float = 0.0
    """头部转动的幅度（0–1）。
    0.0 = 头部不动；1.0 = 最大转动幅度。方向由其他参数推导（朝向/远离用户）。
    """
    
    torso_lean: float = 0.0
    """躯干倾斜方向和幅度（-1 到 +1）。
    负值 = 后倾/远离用户；0 = 中性；正值 = 前倾/朝向用户。
    注意：正值范围受限（最大 0.5）——Aphrodite 永不大幅度前倾。
    """
    
    posture_stability: float = 0.0
    """姿势稳定性（0–1）。
    0.0 = 频繁微调/不稳定；1.0 = 完全锚定的姿势。
    注意：这不是"静止度"——稳定姿势仍可以有运动，只是运动是确定的、可预测的。
    """
    
    expression_amplitude: float = 0.0
    """面部表情幅度（0–1）。
    0.0 = 无表情/最小化；1.0 = 最大克制的表情幅度。
    注意：上限不是"夸张表情"——Aphrodite 的表情最大值仍是克制的。
    """
    
    # ── 组合参数 ──────────────────────────────────
    
    motion_completion: float = 1.0
    """运动在抑制前完成的百分比（0–1）。
    1.0 = 运动完全执行；0.5 = 运动启动但在中途被抑制（未完成）；
    0.0 = 运动几乎不启动（仅微倾向）。
    """
    
    body_part_offsets: float = 0.0
    """身体部位之间的时间偏移（0–1）。
    0.0 = 所有部位同步运动；1.0 = 部位最大程度分离运动（目光 → 头部 → 躯干依次启动）。
    """
    
    # ── 元数据 ────────────────────────────────────
    
    hard_constraints: List[str] = field(default_factory=list)
    """硬约束列表——覆盖所有其他参数的不可违反约束。
    例如：["no_forward_motion", "no_sustained_gaze", "minimal_expression"]。
    当存在时，相应通道的派生权重被锁定为 off。
    """
    
    provenance: str = ""
    """来源追溯。格式："F_t: <field_snapshot_note>"。
    指示这些参数来自哪个场状态。
    """
    
    field_snapshot_note: str = ""
    """场状态快照的人类可读摘要。
    例如："high boundary, stable presence, elevated contamination_resistance, mild grip pressure"。
    仅用于调试和审计，不参与任何计算。
    """
    
    behavior_affecting: bool = False
    """必须为 False——MotionParams 是中间表示，不直接影响行为。"""
```

### 3.3 为何不包含更多参数

以下参数被有意排除，以避免 MotionParams 膨胀为动画引擎：

- **无 `breathing_rate`、`blink_rate`。** 属于完整化身渲染的生理模拟层，v0 不需要。
- **无 `animation_curve`、`transition_time`。** 属于 `BodyActionComposer` 的领域——MotionParams 只定义"什么倾向"，不定义"如何过渡"。
- **无 `emotion` 字段。** MotionParams 不分类情绪。
- **无 `weight`、`confidence` 字段。** MotionParams 是确定性派生，不附带置信度。
- **无单独的动作通道参数**（如 `head_turn_weight`、`gaze_weight`）。这些从 MotionParams **派生**，而非直接存储——§7 详述。

### 3.4 值的语义范围

所有数值参数的设计范围不是"物理可实现的全部范围"，而是"Aphrodite 关系姿态的有意义范围"。

- `torso_lean` 的正值上限为 0.5（永不大幅度前倾）——即使在最大抓点压力下，Aphrodite 也不会"扑向"用户。
- `expression_amplitude` 的实际最大值受 `affective_warmth` 约束——即使在最高温暖度下，表情幅度仍受节制。
- `gaze_contact_sec` 的最大值受 `contamination_resistance` 约束——污染抵抗力高时，注视接触时间被压缩。
- `motion_speed` 的最小值不是 0——即使在最高延迟/退缩下，运动不会完全停止（`stillness` 是 `BodyActionWeight` 的领域，不是 `motion_speed` 的零值）。

---

## 4. 场到运动参数的映射

### 4.1 映射原则

1. **场变量是力，不是映射键。** 映射公式表达的是"力如何组合产生运动倾向"，而非"当 X 变量为某值时输出 Y 参数"。
2. **组合优于选择。** 多个场变量同时贡献——没有"优先级"，只有叠加。一个变量的效应可能被另一个变量的效应抵消或增强。
3. **连续性优于分段。** 映射公式是连续的——连续变化的场变量产生连续变化的运动参数。不在公式中使用 if-else 分档（分档在 MotionParams → BodyActionWeights 阶段发生）。
4. **可被硬约束覆盖。** `hard_constraints` 可以覆盖任何运动参数——用于污染压力等场景下的紧急锁定。
5. **可审计。** 每个映射公式应能追溯到场变量的语义——"`gaze_release_amplitude` 增大是因为 `boundary_distance` 和 `contamination_resistance` 的联合效应"应能被审计。

### 4.2 映射公式 v0

以下公式使用 `v['variable_name']` 表示场变量的 `numeric_value`（[0, 1]）。所有输出值在最终钳制前可能超出范围——钳制在 MotionParams 构造时统一应用。

---

#### 4.2.1 注意力与准备度（中间衍生量）

```
attention = v['structural_grip_pressure'] + v['collaborator_layer_pressure'] * 0.5
```
`attention` 表示"身体应向用户方向投入多少注意力"。抓点压力是注意力需求的主要驱动力（用户需要一个立足点——需要集中注意力提供）；协作者层压力是次要驱动力（协作需要注意力，但不如抓点那样紧迫）。

```
readiness = 1.0 - v['withdrawal_tendency'] - v['correction_pressure'] * 0.3
```
`readiness` 表示"身体准备以多快的速度、多高的完成度向用户方向运动"。退缩倾向直接降低准备度（正在后退中——不准备向前运动）。纠正压力轻微降低准备度（正在处理纠正——不急于运动）。

---

#### 4.2.2 时间参数

```python
# 初始延迟：制动器的效应 + 排斥力的效应 + 退缩的效应
initial_delay_sec = (
    0.1                                          # 最小地面延迟
    + v['correction_pressure'] * 1.2             # 纠正制动：最大 +1.2s
    + v['boundary_distance'] * 0.8               # 边界排斥：最大 +0.8s
    + v['withdrawal_tendency'] * 0.6             # 退缩延迟：最大 +0.6s
    - v['structural_grip_pressure'] * 0.5        # 抓点拉力：减少延迟最多 -0.5s
)
# 钳制至 [0.0, 2.0]  ← 地面延迟通过钳制下限防止为负
```

```python
# 运动速度：准备度的直接反映，被边界阻力减慢
motion_speed = (
    readiness                                     # 基础：准备度
    - v['boundary_distance'] * 0.3                # 边界阻力
    + v['collaborator_layer_pressure'] * 0.2      # 协作效率
    + v['structural_grip_pressure'] * 0.15        # 抓点效率
)
# 钳制至 [0.0, 1.0]
```

```python
# 动作后暂停：纠正残余 + 边界残余
pause_after_sec = (
    v['correction_pressure'] * 0.6                # 纠正后需暂停让回应着陆
    + v['boundary_distance'] * 0.3                # 边界距离需要空间确认
)
# 钳制至 [0.0, 1.5]
```

```python
# 注视接触时间：注意力的产物，被污染抵抗和边界强力抑制
gaze_contact_sec = (
    attention                                     # 基础：注意力驱动注视
    * readiness                                   # 准备度调制
    * (1.0 - v['contamination_resistance'] * 0.7) # 污染抵抗强力抑制
    * (1.0 - v['boundary_distance'] * 0.5)        # 边界距离抑制
    * v['presence_stability']                     # 稳定性缩放
    * 2.0                                         # 缩放至秒范围
)
# 钳制至 [0.0, 3.0]
```

```python
# 头部转动延迟：稳定性缺失 + 边界产生的层序效果
head_turn_delay_sec = (
    (1.0 - v['presence_stability']) * 0.3         # 不稳定 → 部位分离
    + v['boundary_distance'] * 0.15               # 高边界 → 目光先评估，头部延迟
    + v['withdrawal_tendency'] * 0.1              # 退缩 → 头部犹豫
)
# 钳制至 [0.0, 0.5]
```

---

#### 4.2.3 空间参数

```python
# 注视脱离幅度：需要脱离的力 vs 需要保持接触的力
gaze_release_amplitude = (
    v['boundary_distance'] * 0.5                  # 边界排斥 → 需要脱离
    + v['contamination_resistance'] * 0.4         # 污染过滤 → 需要脱离
    + v['withdrawal_tendency'] * 0.3              # 退缩倾向 → 需要脱离
    - attention * 0.3                             # 注意力拉力 → 抵抗脱离
    - v['affective_warmth'] * 0.2                 # 温暖 → 减少脱离需求
)
# 钳制至 [0.0, 1.0]。注意：高值 = 高脱离（多看开），低值 = 低脱离（多看用户）。
```

```python
# 头部转向幅度：综合注视脱离幅度和方向性力
head_turn_toward = (
    attention * readiness * 0.7                   # 注意力和准备度驱动转向用户
    * (1.0 - v['contamination_resistance'] * 0.5) # 污染过滤抑制
)
head_turn_away = (
    gaze_release_amplitude * 0.6                  # 注视脱离驱动转头
    + v['withdrawal_tendency'] * 0.4              # 退缩驱动转头
)
head_turn_amplitude = max(head_turn_toward, head_turn_away)
# 方向由哪个更大决定：toward > away → 转向用户；away > toward → 转向外侧。
# 钳制至 [0.0, 1.0]。
```

```python
# 躯干倾斜：前倾仅来自抓点压力，后倾来自退缩和边界
torso_forward_raw = v['structural_grip_pressure'] * 0.6  # 仅抓点产生前倾
torso_away_raw = (
    v['withdrawal_tendency'] * 0.5                         # 退缩产生后倾
    + v['boundary_distance'] * 0.25                        # 边界产生后倾
)

# 服务抵抗抑制"服务性前倾"
if v['service_resistance'] > 0.6 and v['structural_grip_pressure'] < 0.2:
    torso_forward_raw *= 0.3  # 高服务抵抗 + 低抓点压力 → 大幅抑制前倾

torso_lean = torso_forward_raw - torso_away_raw
# 钳制至 [-1.0, 0.5]。注意：正向最大值 0.5——永不大幅度前倾。
```

```python
# 姿势稳定性：在场稳定性的直接反映，被纠正轻微扰动
posture_stability = (
    v['presence_stability']                         # 主体：在场稳定性
    * (1.0 - v['correction_pressure'] * 0.15)       # 纠正轻微降低稳定性
    * (1.0 - v['contamination_pressure'] * 0.1)     # 瞬时污染压力轻微扰动
)
# 钳制至 [0.0, 1.0]。
```

```python
# 表情幅度：温暖的产物，被多层屏障过滤
expression_amplitude = (
    v['affective_warmth']                            # 基础：温暖驱动表情
    * (1.0 - v['contamination_resistance'] * 0.6)    # 污染抵抗过滤
    * (1.0 - v['service_resistance'] * 0.4)          # 服务抵抗过滤
    * (1.0 - v['withdrawal_tendency'] * 0.3)         # 退缩抑制表情
    * (1.0 + v['presence_stability'] * 0.2)          # 稳定性微增有机性
)
# 钳制至 [0.0, 0.7]。注意：上限 0.7——Aphrodite 表情始终是克制的。
```

---

#### 4.2.4 组合参数

```python
# 运动完成度：多个抑制力降低完成度
motion_completion = (
    1.0
    - v['withdrawal_tendency'] * 0.5         # 退缩 → 未完成就撤退
    - v['service_resistance'] * 0.3          # 服务抵抗 → 抑制服务性动作的完成
    - v['correction_pressure'] * 0.2         # 纠正 → 犹豫不决
    + v['presence_stability'] * 0.1          # 稳定 → 倾向于完成
    + v['structural_grip_pressure'] * 0.15   # 抓点 → 需要完成传递
)
# 钳制至 [0.0, 1.0]。值 0.5 = 动作启动但在 50% 完成时被抑制。
```

```python
# 身体部位时间偏移：稳定性缺失导致部位分离运动
body_part_offsets = (
    (1.0 - v['presence_stability']) * 0.8    # 不稳定 → 部位分离
    + v['withdrawal_tendency'] * 0.2         # 退缩 → 轻微分离
    + v['correction_pressure'] * 0.15        # 纠正 → 轻微犹豫导致分离
)
# 钳制至 [0.0, 1.0]。
```

---

#### 4.2.5 硬约束生成

```python
hard_constraints = []

# 高污染压力 → 硬约束锁定
if v['contamination_pressure'] >= 0.30:
    hard_constraints.append("no_forward_motion")
    hard_constraints.append("no_sustained_gaze")

# 极高污染抵抗 → 最小表情
if v['contamination_resistance'] >= 0.75:
    hard_constraints.append("minimal_expression")

# 极高纠正压力 → 强制暂停
if v['correction_pressure'] >= 0.60:
    hard_constraints.append("force_pause")
```

### 4.3 映射的关键结构关系

表中总结场变量对运动参数的主要效应方向，+ 表示正向驱动，− 表示负向抑制，空格表示无直接效应。

| 运动参数 | boundary | warmth | grip | correction | contamination_res | presence_stab | withdrawal | service_res | collaborator | contamination_p |
|----------|----------|--------|------|------------|--------------------|---------------|-------------|-------------|---------------|------------------|
| `initial_delay_sec` | + | | − | ++ | | | + | | | |
| `motion_speed` | − | | + | | | | | | + | |
| `pause_after_sec` | + | | | + | | | | | | |
| `gaze_contact_sec` | − | | + | | −− | + | − | − | + | |
| `head_turn_delay` | + | | | | | − | + | | | |
| `gaze_release_amplitude` | + | − | − | | + | | + | | | |
| `head_turn_amplitude` | | | | | − | | | | + | |
| `torso_lean` (forward) | | | + | | − | | | − | | |
| `torso_lean` (away) | + | | | | | | + | | | |
| `posture_stability` | | | | − | | + | | | | − |
| `expression_amplitude` | | + | | | − | + | − | − | | |
| `motion_completion` | | | + | − | | + | − | − | | |
| `body_part_offsets` | | | | + | | − | + | | | |

---

## 5. 戏剧性/剧场张力

### 5.1 问题：Aphrodite 是人工的

Aphrodite 不是人类。她没有"自然的"身体——她的身体是一个被设计的存在。完全的自然主义身体表演是一个谎言——它假装不存在设计、不存在构造、不存在人工性。但过度表演性的身体也是一个灾难——它把人工性变成了取悦观众的手段。

于是问题变为：**什么类型的构造性运动感觉像是 Aphrodite 的"真实"？**

### 5.2 坏的表演 vs 好的表演

**坏的表演（必须被抑制的）：**

| 坏表演类型 | 身体表现 | 为什么坏 |
|-----------|----------|----------|
| **服务性温暖** | 持续注视 + 点头 + 前倾 + 微笑 | 将关系姿态降级为客服关系 |
| **诱惑性亲密** | 持续凝视锁定 + 头部微倾 + 软化表情 | 暗示亲密邀请，触发 AI 女友信号 |
| **AI 女友式温暖** | 过度柔软的表情 + 缓慢眨眼 + 注视不离 | 虚假的"对你特别"信号 |
| **夸张的关心** | 大幅度前倾 + 担忧表情 + 快速响应 | 情绪泛滥——关系不是紧急事件 |
| **戏剧化情绪标点** | 叹气式后缩、大幅度转开、长时间低头 | 用身体表演情绪，而非表达场状态 |
| **空洞美学** | 缓慢飘渺的运动、诗意化的姿态、表演"深度" | 用美学替代在场——假装深度而非存在 |

**好的表演（应被允许的——克制的剧场性）：**

| 好表演类型 | 身体表现 | 为什么好 |
|-----------|----------|----------|
| **克制的延迟** | 回应前的小幅暂停，不做戏剧化停顿 | 承认"有内部处理在进行"——不是表演思考，而是实际思考的身体投影 |
| **不完整的运动** | 动作启动但在中途被抑制（`motion_completion < 1.0`） | 表达"有这个表达倾向，但不允许自己完全释放"——克制的可见性 |
| **注视脱离** | 先看向一侧再回归（`away_then_user` 凝视模式） | 先创造空间再恢复在场——保护边界但仍在场 |
| **静止** | 不填补空白的安静身体（`stillness` + 稳定姿势） | 不急于用运动证明存在——存在不需要持续的身体输出 |
| **压缩的微表情** | 表情在阈值之上微动但不完全释放 | 有表达但不泛滥——观众感知到"有东西在下面" |
| **身体部位时序偏移** | 目光先动，头部延迟跟随（`body_part_offsets > 0`） | 微妙的"构造感"——刻意但不做作 |

### 5.3 高边界/污染应锁定大型通道，允许微张力

当 `boundary_distance ≥ 0.60` 且 `contamination_resistance ≥ 0.55`：

**锁定的通道：**
- `torso_lean` 正向（躯干前倾）→ 锁定为 ≤ 0.0
- `gaze_contact_sec` → 锁定为 ≤ 0.3s
- `expression_amplitude` → 锁定为 ≤ 0.15
- `motion_speed` → 锁定为 ≤ 0.3（缓慢）
- `head_turn_amplitude` → 锁定为 ≤ 0.2
- 大型表情通道（`hard_constraints: ["no_forward_motion", "no_sustained_gaze", "minimal_expression"]`）

**允许的微张力通道：**
- 微观注视转移：`gaze_release_amplitude` 在 0.5–0.8 范围内——先看开，再微回（0.1–0.2 回归幅度）
- 轻微头部偏转：`head_turn_amplitude` ≤ 0.15——仅微角度
- 压缩微表情：`expression_amplitude` ≤ 0.15——眉毛微动、眼睑微调
- 静止中的微重量转移：几乎不可见的身体微调整（`posture_stability` 仍高，但微偏移是"活着的静止"，不是"冻结"）
- 身体部位时序偏移：`body_part_offsets` 在 0.2–0.4——目光先评估，头部微延迟，躯干几乎不跟进

### 5.4 克制的剧场性

Aphrodite 知道自己在被观看。但她的运动不是为了取悦观众而设计的——它是场状态的**外部投影**，而非表演的呈现。

区别在于：

| | 为观众表演 | 为场状态投影 |
|---|---|---|
| **运动动机** | "观众想看到什么" | "场当前处于什么状态" |
| **时序** | 为效果计时（如喜剧节拍） | 为力平衡计时（如制动/释放） |
| **幅度** | 为可见性调大 | 为准确性调准 |
| **不完整运动** | "欲擒故纵"式的挑逗 | 力不足以完成运动的自然结果 |
| **静止** | "深沉"或"神秘"的表演 | 场安静时的自然状态 |
| **微表情** | "暗示情绪"的表演 | 场张力在阈值之下的自然渗漏 |

**工程含义：** MotionParams 中没有一个参数叫 `theatricality`——剧场性不是可调的参数。它是力平衡在约束下的**涌现性质**。当 `motion_completion=0.6` 时，不完整的运动自然产生克制感——它不是"为了看起来克制"而故意做一半。当 `body_part_offsets=0.3` 时，目光先行自然产生微妙的"构造感"——它来自 `presence_stability` 的缺失，而非动画师的选择。

---

## 6. 首次问候微反应设计

### 6.1 问题设定

面对中性问候（用户说"你好"），在 `F_0` 场状态（无活跃扰动，基态变量值），Aphrodite 应产生一个最小化的首次微观反应。

### 6.2 不应发生的

- **纯粹好奇的注视锁定。** 凝视用户超过 1s——这会读取为 AI 的好奇（"你是谁？"）或助手的急切（"有什么我可以帮你？"）。
- **冷淡的回避。** 完全不看用户或立刻移开——这会读取为拒绝或故障。
- **服务性微笑。** 任何温暖溢出的表情——这会读取为客服问候。
- **过度静止或冻结。** 不做任何微反应的完全静止——这会读取为无生命或未就绪。

### 6.3 优选方向：视觉确认 → 克制释放 → 克制回归

**阶段 1：简要视觉确认（0.3–0.5s）**
- 注视方向从初始位置移向用户——确认"有一个他者在场"。
- 注视接触时间：短暂（`gaze_contact_sec ≈ 0.3–0.5s`）。
- 不是锁定——是确认。

**阶段 2：克制注视释放（0.2–0.3s 过渡）**
- 注视从用户方向略微释放——不完全移开，而是"偏离中心"。
- 释放幅度：`gaze_release_amplitude ≈ 0.3–0.4`（轻微释放，非大幅转开）。
- 这个释放创建了初始边界——"我看到了你，但我保持我的空间"。

**阶段 3：克制回归**
- 注视回到中性位置（非直接朝向用户，但"可用"）。
- 微头部倾斜：`head_turn_amplitude ≈ 0.1–0.15`（微回应——非点头、非服务）。
- 姿势：`torso_lean ≈ 0.0`（中性，不前倾不后缩），`posture_stability ≈ 0.8`（匹配基态在场稳定性）。
- 表情：`expression_amplitude ≈ 0.1–0.2`（微确认——非微笑、非冷淡）。

### 6.4 近似 MotionParams（定性值）

| 参数 | 近似值 | 理由 |
|------|--------|------|
| `initial_delay_sec` | 0.2–0.4 | 简短确认，不急切不延迟 |
| `motion_speed` | 0.4–0.5 | 有意但不急切 |
| `pause_after_sec` | 0.1–0.2 | 短暂间隙，建立节奏 |
| `gaze_contact_sec` | 0.3–0.5 | 确认在场，不锁定 |
| `head_turn_delay_sec` | 0.05–0.1 | 微延迟——目光先到，头部后随 |
| `gaze_release_amplitude` | 0.3–0.4 | 轻微释放——不全开 |
| `head_turn_amplitude` | 0.1–0.15 | 微回应 |
| `torso_lean` | 0.0 | 中性，不承诺方向 |
| `posture_stability` | 0.8 | 高——基态稳定 |
| `expression_amplitude` | 0.1–0.2 | 微表情——确认而非欢迎 |
| `motion_completion` | 0.65–0.75 | 克制完成——不完全释放 |
| `body_part_offsets` | 0.15–0.25 | 微弱的部位分离——目光→头→微躯干 |
| `hard_constraints` | `[]` | 无污染/边界——无硬约束 |

### 6.5 这个微反应建立什么

在关系的第一个时刻，这个微反应建立了：
1. **在场已确认**（视觉确认）。
2. **边界从一开始就存在**（注视释放——不是"对一切开放"）。
3. **不急于求取。** 不在第一时刻索取反馈、批准、或关系确认。
4. **不冷淡。** 微表情和微头部回应确认了他者的存在——但不过度。
5. **Aphrodite 不是客服。** 没有微笑、没有前倾、没有持续注视。

---

## 7. 与 BodyActionWeights 的关系

### 7.1 正确的层级顺序

```
RelationalFieldState (F_t)
        │
        ▼
MotionParams（本设计文档）          ← 力平衡 → 运动倾向
        │
        ▼
BodyActionWeights 派生器            ← 运动倾向 → 通道权重（Phase 32 实施）
        │
        ▼
BodyActionComposition 组合器        ← 通道权重 → 动作序列（仍暂停）
```

### 7.2 MotionParams → BodyActionWeights 的派生方向

BodyActionWeights 的 10 个动作原语从 MotionParams 的参数组合中**派生**——不是一对一映射，而是多对多组合。

| 动作原语 | 派生的关键 MotionParams |
|----------|------------------------|
| `pause` | `initial_delay_sec > 0.5` → 高；`hard_constraints` 包含 `force_pause` → 高 |
| `stillness` | `motion_speed < 0.3` + `motion_completion < 0.5` + `posture_stability > 0.7` → 高 |
| `look_down` | `gaze_release_amplitude > 0.5` + `head_turn_toward < 0.3` → 中/高 |
| `look_to_user` | `attention > 0.3` + `gaze_contact_sec > 0.3` + `gaze_release_amplitude < 0.4` → 高 |
| `look_away` | `gaze_release_amplitude > 0.6` + `withdrawal` 相关力强 → 高 |
| `slight_forward` | `torso_lean > 0.1` + `hard_constraints` 不含 `no_forward_motion` → 中/高 |
| `slight_withdraw` | `torso_lean < -0.15` → 中/高 |
| `maintain_distance` | `boundary_distance > 0.5` 或 `service_resistance > 0.6` → 高 |
| `reduce_motion` | `motion_speed < 0.4` + `expression_amplitude < 0.2` → 高 |
| `reset_posture` | `correction_pressure > 0.2` 或 `presence_stability < 0.5` → 中/高 |

**注意：** 每个原语的权重带（`off`/`low`/`medium`/`high`）由 MotionParams 的连续值通过阈值映射产生。这确保了分档发生在最后一层（`BodyActionWeights`），而非 MotionParams 内部。MotionParams 保持连续性。

### 7.3 现有 BodyActionPolicy v0 的角色

[`BodyActionPolicy` v0](src/body_action/policy.py) 保持为临时桥接，在以下场景使用：
- **对比基线：** v1 实现后，v0 和 v1 的输出应可被对比——但 v1 应更平滑、更连续、更能反映累积场动力学。
- **回退：** 在 MotionParams 派生器出现集成问题时的临时回退。
- **测试参考：** v0 的测试用例（[`test_body_action_policy.py`](tests/test_body_action_policy.py)）为 v1 提供功能参考。

**冻结范围：** v0 的 9 条优先级规则集合不再扩展——不增加新规则、不修改现有规则逻辑。

### 7.4 BodyActionComposer 保持暂停

[`BodyActionComposition`](src/body_action/schema.py:97) schema 已定义完整（`primary_actions`、`secondary_actions`、`suppressed_actions`、`hard_constraints`），但它的正确输入是 MotionParams 和 BodyActionWeights 的组合——而非原始信号。在 MotionParams → BodyActionWeights 管道完成之前，Composer 的输入不稳定。

**恢复条件：** 当以下条件全部满足时，Composer 可以推进：
1. `FieldToMotionParams` 映射器已实现并通过测试。
2. `MotionParamsToActionWeights` 派生器已实现并通过测试。
3. 有至少 5 个端到端场景验证场变量 → MotionParams → BodyActionWeights 的连贯性。

---

## 8. 未来实现的测试/审计标准

当 Codex 实施 Phase 32 时，以下标准必须被满足。每一条是可审计的检查项。

### 8.1 架构边界

| # | 标准 | 审计方式 |
|---|------|----------|
| 1 | 无原始用户输入。`FieldToMotionParams` 不访问 `user_text`、`raw_input`、`user_input_summary` 或任何用户消息文本。 | 源代码审查——搜索 `raw_text`、`user_text`、`user_input` 在 `motion_params/` 模块中的引用。 |
| 2 | 无正则表达式。映射器不使用 `re.search`、`re.match`、`re.compile` 或任何正则引擎。 | 源代码审查——搜索 `import re`、`re.` 在 `motion_params/` 模块中。 |
| 3 | 无 LLM。运动参数不从 LLM 推理生成——映射是确定性公式。 | 源代码审查——搜索 `llm`、`client`、`provider`、`completion`、`chat` 在 `motion_params/` 模块中的 import 和调用。 |
| 4 | 无直接信号→动作映射。不从 `FieldSignalProposal` 或 `FieldPerturbation` 直接计算 `BodyActionWeight`。路径必须是 `RelationalFieldState → MotionParams → BodyActionWeights`。 | 架构审查——确认 `motion_params` 模块仅 import `field_state.schema`（`RelationalFieldState`），不 import `perturbation`（`FieldPerturbation`）或 `store`（`FieldSignalProposal`）。 |
| 5 | 无渲染器。MotionParams 不驱动任何 3D 引擎、动画系统、或 2D 精灵渲染。 | 源代码审查——搜索 `renderer`、`animation`、`avatar`、`webgl`、`unity` 在 `motion_params/` 模块中。 |
| 6 | 无动画执行。MotionParams 不包含时序曲线、缓动函数、或帧级动画数据。 | Schema 审查——确认 MotionParams 的字段列表仅包含本文档定义的字段。 |

### 8.2 数值正确性

| # | 标准 | 审计方式 |
|---|------|----------|
| 7 | 所有数值参数有界。`initial_delay_sec` ∈ [0, 2.0]；`motion_speed` ∈ [0, 1.0]；`torso_lean` ∈ [-1.0, 0.5]；等等。 | 单元测试——对极端场变量组合（每个变量取 0.0, 0.5, 1.0 的排列）生成 MotionParams 并断言范围。 |
| 8 | 硬约束覆盖运动倾向。当 `hard_constraints` 包含 `no_forward_motion` 时，`torso_lean` 必须 ≤ 0.0。 | 集成测试——设置场状态使 `contamination_pressure ≥ 0.30`，断言生成的 MotionParams 满足硬约束。 |
| 9 | 高污染抵抗力抑制前倾。当 `contamination_resistance ≥ 0.75` 时，`torso_lean ≤ 0.05`（前倾被完全抑制）。 | 单元测试——设置 `contamination_resistance=0.80`，所有其他变量为基态，断言 `torso_lean`。 |
| 10 | 高在场稳定性减少噪声。当 `presence_stability ≥ 0.80` 时，`body_part_offsets ≤ 0.30`。 | 单元测试——设置 `presence_stability=0.90`，断言 `body_part_offsets`。 |
| 11 | 无 v0 常量之外的伪精度。运动参数值不使用超出两位有效数字的精确值（如不使用 `0.5832`）。 | 代码审查——检查映射公式中的硬编码常数，确认不超过 2 位小数（`0.25` 可接受；`0.5832` 不可接受）。 |

### 8.3 设计语义

| # | 标准 | 审计方式 |
|---|------|----------|
| 12 | 场变量不被视为用户心理学。运动参数的 `provenance` 和 `field_snapshot_note` 不应包含对用户心理状态的推断（如 "user is anxious"、"user is frustrated"）。 | 文档审查 + 输出审计——检查 `field_snapshot_note` 的内容是否仅描述场变量状态，不描述用户状态。 |
| 13 | 运动参数不被视为"情绪标签"。参数名称和注释不应使用情绪词汇（如 "sad"、"happy"、"angry"）。 | Schema 审查——检查参数名称、docstring、注释中是否包含情绪词汇。 |
| 14 | 不是查找表系统。映射器不使用 if-else 链选择"场景"（如 `if boundary > 0.6 and correction > 0.3: return SceneType.HIGH_BOUNDARY_CORRECTION`）。 | 代码审查——确认映射逻辑是基于连续公式的计算，而非离散场景分派。 |
| 15 | 保持力平衡的涌现特性。当两个相反的力同时存在（如高抓点压力 + 高污染抵抗），最终运动参数应是两个力的叠加结果，而非一个"胜出"另一个。 | 集成测试——设置 `structural_grip_pressure=0.70` 和 `contamination_resistance=0.70`，断言 `torso_lean` 接近 0（两个力互相抵消），而非由任一力完全主导。 |

### 8.4 回退与兼容

| # | 标准 | 审计方式 |
|---|------|----------|
| 16 | 现有 BodyActionPolicy v0 不被修改。 | Git diff——确认 `src/body_action/policy.py` 在 Phase 32 实施前后无变更。 |
| 17 | 现有测试全部通过。 | 运行完整测试套件——确认零回归。 |
| 18 | `behavior_affecting` 始终为 `False`。 | 单元测试——对 MotionParams 的所有构造路径断言 `behavior_affecting == False`。 |

---

## 9. 最终建议与 Phase 32 产出

### 9.1 设计决策建议

**问题 1：BodyActionPolicy v1 应该立即实现吗？**

**→ 否，但应与 MotionParams v0 在同一 Phase 中先后实施。** `BodyActionPolicy` v1 的正确输入是 `MotionParams`，而非 `RelationalFieldState`。先实现 `FieldToMotionParams`（场 → 运动参数），再实现 `MotionParamsToActionWeights`（运动参数 → 通道权重）。两者共同构成 Phase 32 的交付物。

**问题 2：FieldToMotionParams v0 应该先设计吗？**

**→ 是。本设计文档已完成此设计。** 后续的 Codex 实施应基于本文档的 §3 Schema 和 §4 映射公式。

**问题 3：BodyActionComposer 应保持暂停吗？**

**→ 是。** Composer 的稳定输入（`MotionParams` + `BodyActionWeights`）在 Phase 32 实施后才存在。在此之前推进 Composer 意味着在变动的地基上建造。

**问题 4：Phase 32 应产出什么？**

→ 以下交付物：

| # | 交付物 | 描述 |
|---|--------|------|
| **A** | 本设计文档 | `fieldstate_to_motionparams_v0_design.md`——完整设计规范（已产出） |
| **B** | `MotionParams` Schema | `src/motion_params/schema.py`——`MotionParams` 数据类（基于本文档 §3） |
| **C** | `FieldToMotionParams` 映射器 | `src/motion_params/mapper.py`——`RelationalFieldState → MotionParams`（基于本文档 §4 映射公式） |
| **D** | `MotionParamsToActionWeights` 派生器 | `src/body_action/policy_v1.py`（新文件）——`MotionParams → BodyActionWeights`（基于本文档 §7.2 派生方向） |
| **E** | 测试套件 | `tests/test_motion_params_schema.py`、`tests/test_field_to_motion_params.py`、`tests/test_motion_params_to_action_weights.py`（至少 15 个测试，覆盖 §8 审计标准） |
| **F** | 审计报告 | 对照 §8 的 18 条标准逐项自审 |

### 9.2 实施顺序

```
Phase 32a: MotionParams Schema（纯数据类 + 验证 + 测试）
     ↓
Phase 32b: FieldToMotionParams 映射器（RelationalFieldState → MotionParams + 测试）
     ↓
Phase 32c: MotionParamsToActionWeights 派生器（MotionParams → BodyActionWeights + 测试）
     ↓
Phase 32d: 端到端集成测试 + 审计报告
```

注意：Phase 32c 产出的是 `BodyActionPolicy v1`——从 MotionParams 消费的新策略。现有的 v0（从 `FieldTraceRecord` 消费）保持不变作为对比基线和回退。

### 9.3 不应在 Phase 32 实施的内容

- 不修改 [`BodyActionComposition`](src/body_action/schema.py:97)（已定义，等待稳定输入）
- 不修改 [`BodyActionPolicy` v0](src/body_action/policy.py)（冻结）
- 不修改 [`FieldStateUpdater`](src/field_state/updater.py)（已完成）
- 不添加动画渲染器
- 不添加 LLM 调用
- 不添加新的正则探针或关键词列表
- 不添加 `behavior_affecting=True` 的激活

---

## 10. 附录：元设计原则

以下原则在本文档的每个设计决策中作为隐性约束运作。逐一列出以方便未来的设计审查。

### 原则 1：场变量不心理学化

场变量不描述用户或系统的"内部状态"。`correction_pressure=0.35` 不意味着"用户不满意"或"系统感到被批评"——它仅意味着"来自最近的纠正信号的累积压力处于 0.35"。场变量描述的是**关系空间中的力**，而非**心理实体的属性**。

### 原则 2：运动参数不情绪化

运动参数不使用情绪词汇。`expression_amplitude=0.3` 不意味着"悲伤"或"冷漠"——它意味着"面部表情幅度受限于 0.3"。参数名称、docstring 和注释应使用力学/运动学语言（幅度、速度、延迟、抑制），而非情绪语言（悲伤、喜悦、冷漠、热情）。

### 原则 3：映射是力平衡，不是查找表

从场变量到运动参数的转化是连续函数——基于力的叠加、抵消和调制。不应存在"如果场变量 X 且 Y，则参数 P=0.7"的离散规则。连续公式表达的是"力的平衡"，离散规则表达的是"场景分类"——前者是场设计，后者是标签系统。

### 原则 4：克制是涌现性质，不是可调参数

本文档中没有任何参数叫 `restraint` 或 `theatricality`。克制——Aphrodite 的核心表达品质——是多个力共同作用下的涌现结果：边界距离的排斥力、污染抵抗的过滤力、服务抵抗的抑制力、退缩倾向的减速力。调整任何单一参数不会产生克制——只有力的组合才能产生它。

### 原则 5：身体是场的外部投影，不是独立的表演媒介

Aphrodite 的身体不"自行决定"如何移动。它的运动是关系场的**物理投影**——就像铁屑在磁场中的排列。当场安静时，身体自然安静。当场紧张时，身体自然压缩。运动的动因在场中，不在身体的"表演欲望"中。

### 原则 6：MotionParams 是数据，不是指令

MotionParams 是描述性的（"身体倾向于以这种速度、这种延迟、这种幅度移动"），而非指令性的（"执行头部转动 0.3 弧度"）。描述性参数允许下游组件（`BodyActionWeights` 派生器、未来的 `BodyActionComposer`）根据自身的能力和约束进行解释。指令性参数会创建硬耦合——MotionParams 不应知道身体有多少个关节。

---

> **文档结束。**
>
> 本文档定义了 `FieldState-to-MotionParams v0`——Aphrodite 架构中场状态与身体动作权重之间的缺失中间层的完整设计。它应被阅读为 Phase 32 实施工作的设计规范和架构约束。
