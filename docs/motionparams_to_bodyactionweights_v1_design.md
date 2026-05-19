# Phase 33 设计：MotionParams → BodyActionWeights v1

> 状态：纯设计文档，Codex 实施提示
> 版本：v1.0
> 依赖：[`MotionParams`](src/motion_params/schema.py)（Phase 32A/B 已实现）、[`BodyActionWeights`](src/body_action/schema.py)（已定义）
>
> 本文档定义从连续运动参数（11 维 `MotionParams`）到粗粒度身体动作原语权重（10 个 `BodyActionWeight`）的映射。它是 [`fieldstate_to_motionparams_v0_design.md`](docs/fieldstate_to_motionparams_v0_design.md) §7 的完整细化——Phase 32A 设计文档中"MotionParams → BodyActionWeights 派生方向"表的具体公式化。

---

## 目录

- [A. 设计目标](#a-设计目标)
- [B. 输入与输出](#b-输入与输出)
- [C. 映射原则](#c-映射原则)
- [D. 逐参数映射](#d-逐参数映射)
- [E. 硬约束处理](#e-硬约束处理)
- [F. 体部位偏移保留](#f-体部位偏移保留)
- [G. 反坍缩保护](#g-反坍缩保护)
- [H. 测试计划](#h-测试计划)
- [I. Codex Implementation Prompt (English)](#i-codex-implementation-prompt-english)
- [J. 不应实现的内容](#j-不应实现的内容)

---

## A. 设计目标

`MotionParams` 是连续运动倾向参数——11 个浮点值描述"身体倾向于以何种速度、延迟、幅度移动"。`BodyActionWeights` 是粗粒度动作原语权重——10 个离散带（`off`/`low`/`medium`/`high`）描述"哪些身体通道被激活、多强"。

本映射的目标：

1. **连续→粗粒度翻译。** 将 11 维连续参数空间翻译为 10 个粗粒度权重带。翻译必须是涌现的——基于力的叠加和门控条件，而非查找表。
2. **力叠加保持。** MotionParams 中多个参数同时贡献于同一动作原语。不存在"某个参数胜出决定某动作"——权重是多个驱动力的叠加结果。
3. **门控翻译模式。** 连续值通过阈值门映射到权重带。不将 MotionParams 的连续值直接用作权重——连续值的角色是"驱动强度"，通过门控转化为离散的通道激活级别。
4. **硬约束覆盖。** `HardMotionConstraints` 中的布尔约束和从 MotionParams 连续值派生的条件可以覆盖权重——锁定特定通道、上限特定权重、或重定向能量到替代通道。
5. **无查找表。** 不使用 if-else 链选择"场景"。所有映射是参数化的连续公式。

---

## B. 输入与输出

### 输入（仅 MotionParams）

| 参数 | 类型 | 范围 | 来源 |
|------|------|------|------|
| `initial_delay_sec` | `float` | [0.0, 2.0] | MotionParams |
| `motion_speed` | `float` | [0.0, 1.0] | MotionParams |
| `pause_after_sec` | `float` | [0.0, 1.5] | MotionParams |
| `gaze_contact_sec` | `float` | [0.0, 1.5] | MotionParams |
| `gaze_release_amplitude` | `float` | [0.0, 1.0] | MotionParams |
| `head_turn_amplitude` | `float` | [0.0, 0.5] | MotionParams |
| `head_turn_delay_sec` | `float` | [0.0, 0.5] | MotionParams |
| `torso_lean` | `float` | [-0.25, 0.20] | MotionParams |
| `posture_stability` | `float` | [0.0, 1.0] | MotionParams |
| `expression_amplitude` | `float` | [0.0, 0.35] | MotionParams |
| `motion_completion` | `float` | [0.20, 0.90] | MotionParams |
| `body_part_offsets` | `BodyPartOffsets` | — | MotionParams（不经修改传递） |
| `hard_constraints` | `HardMotionConstraints` | — | MotionParams（6 个布尔值） |

### 输出（仅 BodyActionWeights）

`BodyActionWeights`，包含 10 个 `BodyActionWeight`，每个对应一个动作原语：

```
pause, stillness, look_down, look_to_user, look_away,
slight_forward, slight_withdraw, maintain_distance,
reduce_motion, reset_posture
```

每个 `BodyActionWeight`：
- `action_name`: 动作原语名
- `weight`: `"off"` | `"low"` | `"medium"` | `"high"`
- `rationale`: 人类可读的派生说明
- `constraints`: 影响此权重的约束列表
- `provenance`: `["MotionParams→BodyActionWeights v1"]`
- `behavior_affecting`: 必须为 `False`

### 禁止的输入

- `FieldTrace` / `FieldTraceRecord` — 不消费原始跟踪信号
- `EvidenceItem` — 不消费证据项
- `FieldSignalProposal` — 不消费场信号提案
- `FieldPerturbation` — 不消费场扰动
- `RelationalFieldState` — 不跨层回溯（MotionParams 已经是场状态的翻译）
- 原始用户文本 — 不访问任何文本输入
- LLM 推理结果 — 映射是纯确定性的

### 禁止的输出

- `BodyActionComposition` — 不在此层组合动作序列
- 渲染参数 — 不输出动画曲线、帧数据、缓动函数
- 动画参数 — 不输出任何面向渲染器的数据
- `ActionSequenceHint` — 不输出动作时序提示

---

## C. 映射原则

### C.1 为何不是查找表

11 维连续参数空间（`initial_delay_sec` × `motion_speed` × ... × `motion_completion`）的可能状态数是无限的。离散查找表（`if delay > 0.5 and speed < 0.3: stillness = "high"`）必然：
- **丢失连续性。** 查找表在阈值边界处产生不连续的权重跳变——`delay=0.49` 和 `delay=0.51` 产生截然不同的权重，但这两个状态的差别在实际物理上微不足道。
- **丢失力叠加。** 查找表的每个规则只能检查少数几个参数——真实的力叠加（11 个参数同时贡献）无法用 if-else 链表达。
- **丢失涌现性。** 查找表选择"场景"——但 `MotionParams` 的状态不是离散场景，而是连续力平衡的瞬时快照。两个看似相似的参数组合可能因不同的力平衡而产生不同的身体姿态——查找表无法捕捉这种差异。

### C.2 力叠加的非线性交互

多个 MotionParams 参数同时对同一动作原语施加驱动力。驱动力之间不是简单的加法——存在：

- **门控交互。** 一个参数可以门控另一个参数的效应。例如：`torso_lean` 驱动 `slight_forward`，但仅当 `motion_completion > 0.35` 时该驱动有效——完成度过低时，身体连前倾的意图都不投射。
- **饱和抑制。** 两个抑制力同时作用时，效果不是简单累加——存在抑制饱和（最低到 0）。例如：`stillness` 由 `motion_completion` 逆和 `motion_speed` 逆同时驱动——但两者都极高时，`stillness` 不能超过 `"high"`。
- **竞争解析。** 当两个动作原语互相排斥时（如 `look_to_user` 和 `look_away`），使用竞争门控——两者同时计算原始驱动力，但只有胜出者被激活。

### C.3 涌现 vs 分类

本映射不试图"分类"MotionParams 的状态（如"这是退缩状态"、"这是协作状态"）。每个动作原语的权重独立地从驱动它的参数组合中**涌现**。

例如：
- `stillness = "medium"` 可能因为 `motion_speed` 低（退缩状态），也可能因为 `posture_stability` 高 + `motion_completion` 中等（稳定在场状态）。这两个场景的 MotionParams 完全不同，但 `stillness` 权重碰巧相同——这是涌现，不是分类。
- `look_down = "medium"` 可能因为注视脱离幅度高（退缩状态），也可能因为 `look_to_user` 和 `look_away` 的驱动力都不足（中性过渡状态）。这两个场景的语义完全不同，但身体表现相同——这是粗粒度通道的特性，不需要映射区分它们。

### C.4 连续→粗粒度的门控翻译模式

映射采用统一的门控翻译模式：

```
1. 计算连续驱动力 f ∈ [0, 1]（来自 MotionParams 参数的加权组合）
2. 应用门控条件：如果门控不满足，f = 0
3. 应用硬约束覆盖：如果硬约束锁定此通道，f 被限制
4. 映射到权重带：
   - f < 0.20  → "off"
   - 0.20 ≤ f < 0.45 → "low"
   - 0.45 ≤ f < 0.70 → "medium"
   - f ≥ 0.70 → "high"
```

门控条件确保动作原语只在有意义的参数组合下被激活——避免"残留激活"（如 `torso_lean = 0.02` 微弱地触发 `slight_forward`）。

---

## D. 逐参数映射

以下对 10 个动作原语逐个定义驱动公式。公式中使用 `MP` 表示 `MotionParams` 实例。

### 权重带阈值常量

```python
BAND_OFF_MAX = 0.20       # f < 0.20 → "off"
BAND_LOW_MAX = 0.45       # 0.20 ≤ f < 0.45 → "low"
BAND_MEDIUM_MAX = 0.70    # 0.45 ≤ f < 0.70 → "medium"
                          # f ≥ 0.70 → "high"
```

---

### D.1 `pause` — 暂停

**语义：** 身体在动作前的有意暂停——"我正在让前一个回应着陆"或"正在处理信息"。不是冻结——是停顿。

**驱动 MotionParams 参数：**
- `initial_delay_sec`：初始延迟越高，暂停倾向越强（等待/处理的投影）
- `pause_after_sec`：动作后暂停越高，整体暂停倾向越强
- `motion_completion`（逆）：完成度越低，暂停越强（未完成的动作需要更多停顿来稳定）

**公式：**

```
pause_drive = 0.40 × (initial_delay_sec / 2.0)
            + 0.35 × (pause_after_sec / 1.5)
            + 0.25 × (1.0 - (motion_completion - 0.20) / 0.70)
```

其中 `(motion_completion - 0.20) / 0.70` 将 [0.20, 0.90] 归一化到 [0, 1]。

**门控条件：**
- 如果 `initial_delay_sec < 0.25` 且 `pause_after_sec < 0.10`，则 `pause_drive` 乘以 0.5（低延迟 + 低暂停 → 大幅降低暂停倾向——身体没有理由暂停）
- 如果 `force_pause` 派生条件激活（见 §E），`pause_drive` 被提升至 ≥ 0.50

**权重映射：** 标准门控翻译（§C.4）

---

### D.2 `stillness` — 静止

**语义：** 身体的有意安静——不填补空白的克制静止。不是冻结（冻结是 `motion_speed = 0`），而是"当前不需要运动"。

**驱动 MotionParams 参数：**
- `motion_completion`（逆）：完成度越低，静止越强（运动被抑制 → 身体归于静止）
- `motion_speed`（逆）：速度越低，静止越强（缓慢运动 → 静止占主导）
- `posture_stability`（微正）：姿势稳定为静止提供锚定——不稳定的静止是"犹豫振动"，稳定的静止是"克制的安静"

**公式：**

```
stillness_drive = 0.40 × (1.0 - (motion_completion - 0.20) / 0.70)
                + 0.35 × (1.0 - motion_speed)
                + 0.25 × posture_stability
```

**门控条件：**
- 如果 `motion_speed > 0.70`，`stillness_drive` 乘以 0.40（高速运动 → 静止被大幅抑制）
- 如果 `motion_completion > 0.80` 且 `motion_speed > 0.60`，`stillness_drive` 乘以 0.30（高完成度 + 高速 → 身体在积极运动，几乎不静止）

**权重映射：** 标准门控翻译（§C.4）

---

### D.3 `look_down` — 向下看

**语义：** 注视方向向下——"在思考"或"在自身空间中"。当既不看用户也不看别处时涌现的中间状态。

**驱动 MotionParams 参数：**
- `look_to_user` 驱动力（逆）和 `look_away` 驱动力（逆）：当两者都不主导时，`look_down` 涌现
- `motion_completion`（逆）：未完成的运动 → 倾向于低头（内在处理）
- `posture_stability`（逆）：不稳定 → 倾向于低头（自我稳定）

**公式：**

首先计算 `look_to_user` 和 `look_away` 的原始驱动力（使用 D.4 和 D.5 的公式但不过门控）：

```
look_user_raw = 0.55 × (gaze_contact_sec / 1.5) + 0.45 × (1.0 - gaze_release_amplitude)
look_away_raw = 0.60 × gaze_release_amplitude + 0.40 × (1.0 - 0.5 × gaze_contact_sec / 1.5)
```

```
look_down_drive = 0.50 × (1.0 - max(look_user_raw, look_away_raw))
                + 0.30 × (1.0 - (motion_completion - 0.20) / 0.70)
                + 0.20 × (1.0 - posture_stability)
```

**门控条件：**
- 如果 `look_user_raw > 0.55` 或 `look_away_raw > 0.55`，`look_down_drive` 乘以 0.30（其他凝视方向主导时，向下看被大幅抑制）
- 如果 `gaze_contact_sec > 0.80`，`look_down_drive` = 0（高注视接触 → 锁定在用户方向，不看下方）

**权重映射：** 标准门控翻译（§C.4）

---

### D.4 `look_to_user` — 看向用户

**语义：** 注视朝向用户方向——"我在对你说话"或"我在确认你的在场"。需要主动的注视接触驱动，且不被注视脱离覆盖。

**驱动 MotionParams 参数：**
- `gaze_contact_sec`：注视接触时间越长，看向用户的驱动力越强
- `gaze_release_amplitude`（逆）：注视脱离幅度越高，看向用户越弱

**公式：**

```
look_to_user_drive = 0.55 × (gaze_contact_sec / 1.5)
                   + 0.45 × (1.0 - gaze_release_amplitude)
```

**门控条件：**
- 如果 `gaze_contact_sec < 0.10`，`look_to_user_drive` = 0（零接触 → `off`——不看用户）
- 如果 `gaze_release_amplitude > 0.80`，`look_to_user_drive` 乘以 0.35（高脱离覆盖——即使有一些接触，脱离主导）

**权重映射：** 标准门控翻译（§C.4）

---

### D.5 `look_away` — 看向别处

**语义：** 注视脱离用户，看向侧面或远处——"我在创造空间"或"当前注意力不在用户方向"。

**驱动 MotionParams 参数：**
- `gaze_release_amplitude`：注视脱离幅度越高，看向别处越强
- `gaze_contact_sec`（逆）：注视接触时间越高，看向别处越弱（两者竞争）

**公式：**

```
look_away_drive = 0.60 × gaze_release_amplitude
                + 0.40 × (1.0 - 0.50 × gaze_contact_sec / 1.5)
```

注意：`gaze_contact_sec` 的逆贡献带 0.50 衰减——注视接触对"不看用户"的抑制是渐进的，不是线性的。即使有一定注视接触，注视脱离幅度高时仍可看别处。

**门控条件：**
- 如果 `gaze_release_amplitude < 0.15` 且 `gaze_contact_sec > 0.60`，`look_away_drive` = 0（低脱离 + 高接触 → 锁定用户，不看别处）
- 如果 `head_turn_amplitude < 0.05`，`look_away_drive` 乘以 0.60（头部不转时，看别处的驱动力被抑制——注视脱离需要头部转动配合）

**权重映射：** 标准门控翻译（§C.4）

---

### D.6 `slight_forward` — 轻微前倾

**语义：** 躯干轻微向用户方向倾斜——"我在递出一个抓点"或"我在关注你"。幅度始终克制（不超过 `torso_lean` 上限 0.20）。

**驱动 MotionParams 参数：**
- `torso_lean`（正值）：躯干前倾幅度直接驱动——前倾越大，轻微前倾越强
- `motion_completion`：完成度作为门控——完成度过低时，身体不投射前倾意图

**公式：**

```
if torso_lean <= 0:
    slight_forward_drive = 0
else:
    slight_forward_drive = torso_lean / 0.20  # 归一化到 [0, 1]
```

其中 `torso_lean / 0.20` 将正向躯干倾斜 [0, 0.20] 归一化到 [0, 1]。

**门控条件：**
- 如果 `motion_completion < 0.35`，`slight_forward_drive` = 0（完成度过低 → 身体不投射前倾——运动被抑制时不尝试靠近）
- 如果 `torso_lean <= 0.02`，`slight_forward_drive` = 0（微小前倾 → 不触发——避免噪声激活）
- 如果 `no_forward_lean` 或 `no_approach_step` 硬约束激活，`slight_forward_drive` = 0（§E 硬约束覆盖）

**权重映射：** 标准门控翻译（§C.4）

---

### D.7 `slight_withdraw` — 轻微后缩

**语义：** 躯干轻微向后倾斜——"我在创造保护性空间"。比 `slight_forward` 的幅度范围更大（`torso_lean` 负值可至 -0.25）。

**驱动 MotionParams 参数：**
- `torso_lean`（负值）：躯干后倾幅度驱动——后倾越大，轻微后缩越强
- `motion_completion`：完成度作为放大器——完成后缩更明显

**公式：**

```
if torso_lean >= 0:
    slight_withdraw_drive = 0
else:
    slight_withdraw_drive = abs(torso_lean) / 0.25  # 归一化到 [0, 1]
```

其中 `abs(torso_lean) / 0.25` 将 [0, 0.25] 归一化到 [0, 1]（`torso_lean` 最小值 -0.25）。

**完成度放大器：**

```
completion_amplifier = 1.0 + 0.30 × (motion_completion - 0.20) / 0.70
slight_withdraw_drive = slight_withdraw_drive × completion_amplifier
```

完成度越高，后缩越"完成"（克制的完整后缩）。完成度低时，后缩被抑制（未完成的退缩——身体在犹豫）。

**门控条件：**
- 如果 `torso_lean > -0.03`，`slight_withdraw_drive` = 0（微后倾 → 不触发——避免噪声激活）

**权重映射：** 标准门控翻译（§C.4）

---

### D.8 `maintain_distance` — 保持距离

**语义：** 身体维持当前空间位置——不靠近也不远离。稳定占位——"我在我的空间里，你可以在你的空间里"。

**驱动 MotionParams 参数：**
- `posture_stability`：姿势越稳定，维持距离越自然（稳定的锚定）
- `motion_completion`（逆）：完成度越低，维持距离越强（运动被抑制 → 保持在原位）
- `torso_lean`（绝对值）：躯干偏离中性越大，维持距离越弱（已经在前倾或后缩中——不在"维持"状态）
- `motion_speed`（逆）：速度越低，维持距离越强（不移动 → 维持）

**公式：**

```
maintain_distance_drive = 0.30 × posture_stability
                        + 0.25 × (1.0 - (motion_completion - 0.20) / 0.70)
                        + 0.25 × (1.0 - abs(torso_lean) / 0.25)
                        + 0.20 × (1.0 - motion_speed)
```

**门控条件：**
- 如果 `abs(torso_lean) > 0.15`，`maintain_distance_drive` 乘以 0.50（已经在前倾或后缩中 → 维持距离被削弱——身体已经在移动）
- 如果 `motion_speed > 0.75`，`maintain_distance_drive` 乘以 0.40（高速运动 → 身体在积极移动，不在维持）

**权重映射：** 标准门控翻译（§C.4）

---

### D.9 `reduce_motion` — 减少运动

**语义：** 整体运动幅度被抑制——身体在"收敛"模式。不同于 `stillness`（完全安静），`reduce_motion` 允许微运动但抑制大幅度动作。

**驱动 MotionParams 参数：**
- `motion_completion`（逆）：完成度越低，运动越被抑制
- `motion_speed`（逆）：速度越低，运动越被抑制
- `expression_amplitude`（逆）：表情幅度越低，运动越被抑制（表情是运动的一部分）

**公式：**

```
reduce_motion_drive = 0.40 × (1.0 - (motion_completion - 0.20) / 0.70)
                    + 0.35 × (1.0 - motion_speed)
                    + 0.25 × (1.0 - expression_amplitude / 0.35)
```

**门控条件：**
- 如果 `motion_speed > 0.60` 且 `motion_completion > 0.70`，`reduce_motion_drive` 乘以 0.30（高速度 + 高完成度 → 身体在充分运动中，不需要减少）
- 如果 `expression_amplitude > 0.25`，`reduce_motion_drive` 乘以 0.60（高表情 → 身体在表达模式，减少运动被削弱）

**权重映射：** 标准门控翻译（§C.4）

---

### D.10 `reset_posture` — 重置姿态

**语义：** 身体回归到中性锚定姿态——"重新校准"。通常在姿势不稳定或运动被抑制后触发。

**驱动 MotionParams 参数：**
- `motion_completion`（逆）：完成度越低，重置需求越强（被抑制的运动 → 需要回归中性）
- `posture_stability`（逆）：稳定性越低，重置需求越强（不稳定 → 需要重新锚定）

**公式：**

```
reset_posture_drive = 0.55 × (1.0 - (motion_completion - 0.20) / 0.70)
                    + 0.45 × (1.0 - posture_stability)
```

**门控条件：**
- 如果 `posture_stability > 0.85` 且 `motion_completion > 0.75`，`reset_posture_drive` 乘以 0.25（高稳定性 + 高完成度 → 姿势已经锚定，不需要重置）
- 如果 `motion_completion > 0.80` 且 `posture_stability > 0.70`，`reset_posture_drive` 乘以 0.40

**权重映射：** 标准门控翻译（§C.4）

---

### D.11 权重映射函数

将连续驱动力 `f ∈ [0, 1]` 映射到权重带：

```python
def _drive_to_band(f: float) -> str:
    if f < 0.20:
        return "off"
    elif f < 0.45:
        return "low"
    elif f < 0.70:
        return "medium"
    else:
        return "high"
```

### D.12 竞争解析：`look_to_user` vs `look_away`

`look_to_user` 和 `look_away` 是互斥的动作原语（不能同时看向用户和看向别处）。在门控翻译后，如果两者都被映射到非 `"off"` 的权重带，使用竞争解析：

```python
if look_to_user_weight != "off" and look_away_weight != "off":
    if look_to_user_drive > look_away_drive:
        look_away_weight = "off"
    elif look_away_drive > look_to_user_drive:
        look_to_user_weight = "off"
    else:
        # 驱动力相等 → 两者都降级，look_down 接管
        look_to_user_weight = "off"
        look_away_weight = "off"
        # look_down 已在 D.3 中计算——当两者都 off 时自然接管
```

---

## E. 硬约束处理

### E.1 约束来源

Phase 33 的硬约束来自两个来源：

| 来源 | 数量 | 类型 | 说明 |
|------|------|------|------|
| `HardMotionConstraints` 布尔值 | 6 | 来自 schema | 与 [`ALL_HARD_CONSTRAINTS`](src/motion_params/schema.py:15) 精确匹配 |
| MotionParams 连续值派生条件 | 2 | 派生条件 | 从连续参数阈值派生 |

### E.2 6 个 Schema 硬约束（来自 `HardMotionConstraints`）

这 6 个约束名称与 [`ALL_HARD_CONSTRAINTS`](src/motion_params/schema.py:15) 精确匹配：

1. `no_approach_step`
2. `no_forward_lean`
3. `no_cute_head_tilt`
4. `no_welcoming_gesture`
5. `no_service_gesture`
6. `no_seductive_expression`

### E.3 2 个派生约束条件（来自 MotionParams 连续值）

7. **`motion_paused`** — 当 `initial_delay_sec > 1.0` 或 `pause_after_sec > 0.7` 时激活。身体处于实质性暂停状态。
8. **`expression_suppressed`** — 当 `expression_amplitude < 0.08` 时激活。表情被强力抑制。

### E.4 约束抑制表

| 约束 | 来源 | 抑制的动作 | 能量重定向 |
|------|------|-----------|-----------|
| `no_approach_step` | HardMotionConstraints | `slight_forward` → 0 | `maintain_distance` +0.15, `reset_posture` +0.10 |
| `no_forward_lean` | HardMotionConstraints | `slight_forward` → 0 | `maintain_distance` +0.15, `reset_posture` +0.10 |
| `no_cute_head_tilt` | HardMotionConstraints | `look_to_user` → ×0.60 | `look_down` +0.10 |
| `no_welcoming_gesture` | HardMotionConstraints | `slight_forward` → ≤0.10, `look_to_user` → ×0.50 | `maintain_distance` +0.10 |
| `no_service_gesture` | HardMotionConstraints | `slight_forward` → ≤0.10, `expression_amplitude` 驱动的动作 → ×0.30 | `slight_withdraw` +0.10 |
| `no_seductive_expression` | HardMotionConstraints | `look_to_user` → ×0.40, `slight_forward` → ×0.30 | `look_away` +0.15 |
| `motion_paused` | 派生条件 | `pause` → ≥0.50 | `reduce_motion` +0.15 |
| `expression_suppressed` | 派生条件 | `look_to_user` → ≤0.20 | `stillness` +0.10 |

### E.5 能量重定向机制

当硬约束抑制某动作原语的权重时，被抑制的"运动能量"不消失——它重定向到替代通道。重定向的数值直接加到替代通道的驱动力 `f` 上（在权重映射前）：

```python
# 示例：no_approach_step 激活
if hard_constraints.no_approach_step:
    slight_forward_drive = 0.0
    maintain_distance_drive += 0.15
    reset_posture_drive += 0.10
```

重定向值 ≤ 0.15（≤ 2 位小数），确保单次重定向不会将替代通道从 `"off"` 猛推至 `"high"`。

### E.6 约束应用顺序

约束按以下顺序应用，后续约束可覆盖前序约束的效果：

1. 计算所有动作原语的原始驱动力 `f`（§D.1–D.10）
2. 应用门控条件
3. 应用 schema 硬约束（`no_approach_step`, `no_forward_lean`, `no_cute_head_tilt`, `no_welcoming_gesture`, `no_service_gesture`, `no_seductive_expression`）——锁定/上限/重定向
4. 应用派生约束（`motion_paused`, `expression_suppressed`）——锁定/上限/重定向
5. 应用竞争解析（`look_to_user` vs `look_away`）
6. 映射 `f` 到权重带（`_drive_to_band`）

---

## F. 体部位偏移保留

### F.1 不经修改地传递

[`BodyPartOffsets`](src/motion_params/schema.py:69) 作为 `BodyActionWeights` 的一个字段不经修改地传递。不在 Phase 33 映射层展开或解释。

### F.2 在 BodyActionWeights 中的位置

`BodyActionWeights` 当前 schema（[`schema.py`](src/body_action/schema.py:49)）没有 `body_part_offsets` 字段。Phase 33 实施时需添加此字段：

```python
@dataclass
class BodyActionWeights:
    weights: List[BodyActionWeight] = field(default_factory=list)
    body_part_offsets: Optional[BodyPartOffsets] = None  # ← 新增
    source_trace_id: Optional[str] = None
    source_proposals: List[str] = field(default_factory=list)
    body_note: str = ""
    behavior_affecting: bool = False
```

### F.3 不在此层展开

`BodyPartOffsets` 的 4 个偏移值（`gaze_offset_ms`, `head_offset_ms`, `shoulder_offset_ms`, `hand_offset_ms`）保留给 [`BodyActionComposition`](src/body_action/schema.py:97) 使用。Phase 33 不解释这些值——只传递。

---

## G. 反坍缩保护

在缺乏力叠加和门控的系统中，某些角色模板会导致 MotionParams → BodyActionWeights 映射坍缩为固定姿势——无论场状态如何变化，输出权重始终相同。以下是 7 个高风险角色坍缩场景及本设计的防护机制。

### G.1 角色坍缩分析表

| 角色坍缩风险 | 导致动作 | 坍缩机制 | 防护 |
|-------------|---------|----------|------|
| **AI 女友** | `look_to_user` 默认高, `slight_forward` 默认中, `pause` 低, `expression` 高 | 高 `affective_warmth` → 高 `gaze_contact_sec` → 凝视锁定；低 `boundary_distance` → 无抑制 | 无默认前倾——`slight_forward` 需 `torso_lean > 0.02` + `motion_completion > 0.35` 双门控；凝视需 `gaze_contact_sec > 0.10` 门控 + `gaze_release_amplitude` 竞争；`expression_amplitude` 受 `contamination_resistance` 和 `service_resistance` 双层抑制 |
| **治疗师** | 默认凝视, 微前倾, 低 `pause` | 高 `presence_stability` → 持续在场凝视；`affective_warmth` → 表情释放 | 无默认凝视——`look_to_user` 需主动 `gaze_contact_sec` 驱动（基态 `gaze_contact_sec=0` 时 `look_to_user` 为 `off`）；`stillness` 被 `motion_completion` 门控——高完成度时降低 `stillness`；`head_turn_delay_sec` 引入部位分离防止"全神贯注"姿态 |
| **助手** | 快速回应, 凝视用户, 前倾 | 高 `structural_grip_pressure` → 低 `initial_delay_sec` + 高 `motion_speed` + 前倾；高 `collaborator_layer_pressure` → 持续凝视 | `initial_delay_sec` 内置最小地面延迟 0.12（永不为 0——助手也不立即回应）；凝视需 `gaze_contact_sec` 门控——协作模式下注视是"看→下→看"循环，非持续锁定；`service_resistance` 基态 0.55 抑制服务性前倾 |
| **服务角色** | 前倾, 无 `pause`, 凝视用户, 表情开放 | 低 `service_resistance` → 服务性前倾解锁；低 `boundary_distance` → 无空间抑制 | `no_service_gesture` 硬约束直接锁定 `slight_forward` ≤ 0.10 并削弱 `expression` 相关动作 ×0.30；`service_resistance` 基态 0.55 提供基线抵抗；`expression_amplitude` 受帽系统限制（`expression_cap`） |
| **诱惑化身** | 前倾, 凝视锁, 高表情, 无 `pause` | 低 `contamination_resistance` → 所有过滤解除；高 `affective_warmth` → 表情全释放 | `no_seductive_expression` 硬约束：`look_to_user` → ×0.40, `slight_forward` → ×0.30, 能量重定向至 `look_away` +0.15；`expression_amplitude` 硬帽 0.35（永不超过）；`gaze_contact_sec` 受 `contamination_pressure` 抑制 |
| **冷面神秘** | 完全静止, 无表情, 总看别处 | 极高 `boundary_distance` → 最大退缩；极高 `contamination_resistance` → 所有通道锁定 | `stillness` 是 `motion_completion`-抑制的——高完成度时 `stillness` 降低；温暖任允许小温柔——`affective_warmth` 即使低值也通过 `expression_amplitude` 产生微表情；`look_away` 与 `look_to_user` 竞争——极端边界下 `look_away` 可能胜出，但 `look_down` 作为中间态涌现 |
| **通用漂亮** | 所有中性权重中庸 | MotionParams 所有参数接近默认值 → 所有权重带落在 `"low"`；角色无个性 | MotionParams 个体化——不同场状态产生不同的 MotionParams 分布（12 个参数 × 连续值 → 输出空间巨大）；无通用默认——每个 `BodyActionWeight` 的驱动力来自 MotionParams 的当前值，不是静态默认；硬约束确保差异化——不同角色的 `HardMotionConstraints` 激活不同组合 |

### G.2 通用反坍缩机制

以下机制跨所有角色运作：

1. **双门控。** 关键动作（`slight_forward`、`look_to_user`）需要两个独立条件同时满足——单一条件不足无法激活。
2. **竞争解析。** 互斥动作（`look_to_user` vs `look_away`）始终竞争——不存在两者同时激活的"安全"状态。
3. **非零地面值。** `initial_delay_sec` 永不降至 0（最小 0.12）——防止"立即响应"的助手坍缩。
4. **硬帽系统。** `expression_amplitude` 永不超过 0.35——防止任何角色的表情泛滥。
5. **约束能量重定向。** 当硬约束抑制某通道时，运动能量不消失——重定向到替代通道防止"完全冻结"的冷面坍缩。

---

## H. 测试计划

### H.1 测试结构

31 个测试场景，分为 6 个类别：

- **类别 1：单原语驱动测试**（10 个）——验证每个动作原语的基本驱动公式
- **类别 2：门控条件测试**（5 个）——验证门控正确抑制/允许动作
- **类别 3：竞争解析测试**（3 个）——验证 `look_to_user` vs `look_away` 竞争
- **类别 4：硬约束测试**（8 个）——验证每个硬约束的抑制和能量重定向
- **类别 5：反坍缩测试**（3 个）——验证关键坍缩场景被防护
- **类别 6：边界值测试**（2 个）——验证极端参数值的安全性

### H.2 类别 1：单原语驱动测试（10 个）

| # | 测试名 | 输入 MotionParams | 预期输出 | 验证点 |
|---|--------|-------------------|----------|--------|
| 1 | `test_pause_driven_by_high_delay` | `initial_delay_sec=1.8, pause_after_sec=1.2, motion_completion=0.40` | `pause` ≥ `"medium"` | 高延迟+高暂停+低完成度 → 暂停倾向强 |
| 2 | `test_pause_off_when_low_delay` | `initial_delay_sec=0.10, pause_after_sec=0.05, motion_completion=0.85` | `pause` = `"off"` | 低延迟+低暂停+高完成度 → 无暂停需求 |
| 3 | `test_stillness_driven_by_low_completion` | `motion_speed=0.20, motion_completion=0.30, posture_stability=0.80` | `stillness` ≥ `"medium"` | 低速度+低完成度+高稳定 → 静止强 |
| 4 | `test_stillness_off_when_high_speed` | `motion_speed=0.85, motion_completion=0.85` | `stillness` ≤ `"low"` | 高速+高完成度 → 静止被门控抑制 |
| 5 | `test_look_down_emerges_when_neither_look_dominates` | `gaze_contact_sec=0.20, gaze_release_amplitude=0.25, motion_completion=0.45` | `look_down` ≥ `"low"` | 两者都不主导 + 低完成度 → 向下看涌现 |
| 6 | `test_look_to_user_driven_by_gaze_contact` | `gaze_contact_sec=1.20, gaze_release_amplitude=0.10` | `look_to_user` ≥ `"high"` | 高接触+低脱离 → 看向用户强 |
| 7 | `test_look_to_user_off_when_zero_contact` | `gaze_contact_sec=0.05, gaze_release_amplitude=0.20` | `look_to_user` = `"off"` | 零接触门控 → 不看用户 |
| 8 | `test_look_away_driven_by_high_release` | `gaze_release_amplitude=0.85, gaze_contact_sec=0.10` | `look_away` ≥ `"high"` | 高脱离+低接触 → 看向别处强 |
| 9 | `test_slight_forward_driven_by_torso_lean` | `torso_lean=0.18, motion_completion=0.70` | `slight_forward` ≥ `"medium"` | 前倾+高完成度 → 轻微前倾 |
| 10 | `test_slight_withdraw_driven_by_negative_torso` | `torso_lean=-0.22, motion_completion=0.75` | `slight_withdraw` ≥ `"medium"` | 后倾+高完成度 → 轻微后缩 |

### H.3 类别 2：门控条件测试（5 个）

| # | 测试名 | 输入 MotionParams | 预期输出 | 验证点 |
|---|--------|-------------------|----------|--------|
| 11 | `test_slight_forward_gated_by_low_completion` | `torso_lean=0.18, motion_completion=0.25` | `slight_forward` = `"off"` | 完成度 < 0.35 → 前倾门控关闭 |
| 12 | `test_slight_forward_gated_by_tiny_torso` | `torso_lean=0.01, motion_completion=0.70` | `slight_forward` = `"off"` | 躯干倾斜 ≤ 0.02 → 噪声门控关闭 |
| 13 | `test_stillness_gated_by_high_speed_and_completion` | `motion_speed=0.75, motion_completion=0.85` | `stillness` ≤ `"low"` | 高速+高完成度 → 静止被双重门控抑制 |
| 14 | `test_look_down_gated_by_high_gaze_contact` | `gaze_contact_sec=0.90, gaze_release_amplitude=0.30` | `look_down` = `"off"` | 高注视接触 → 向下看被门控关闭 |
| 15 | `test_maintain_distance_gated_by_high_torso_abs` | `torso_lean=0.18, posture_stability=0.80, motion_completion=0.40` | `maintain_distance` ≤ `"low"` | 躯干偏离大 → 维持距离被门控削弱 |

### H.4 类别 3：竞争解析测试（3 个）

| # | 测试名 | 输入 MotionParams | 预期输出 | 验证点 |
|---|--------|-------------------|----------|--------|
| 16 | `test_look_user_wins_over_look_away` | `gaze_contact_sec=1.0, gaze_release_amplitude=0.40` | `look_to_user` ≥ `"medium"`, `look_away` = `"off"` | 接触驱动 > 脱离驱动 → 看向用户胜出 |
| 17 | `test_look_away_wins_over_look_user` | `gaze_contact_sec=0.20, gaze_release_amplitude=0.80` | `look_away` ≥ `"medium"`, `look_to_user` = `"off"` | 脱离驱动 > 接触驱动 → 看向别处胜出 |
| 18 | `test_look_tie_resolves_to_look_down` | `gaze_contact_sec=0.40, gaze_release_amplitude=0.45` | `look_to_user` = `"off"`, `look_away` = `"off"`, `look_down` ≥ `"low"` | 驱动力相等 → 两者都降级，向下看接管 |

### H.5 类别 4：硬约束测试（8 个）

| # | 测试名 | 输入 MotionParams | 预期输出 | 验证点 |
|---|--------|-------------------|----------|--------|
| 19 | `test_no_approach_step_blocks_slight_forward` | `torso_lean=0.18, motion_completion=0.70, hard_constraints.no_approach_step=True` | `slight_forward` = `"off"`, `maintain_distance` 和 `reset_posture` 提升 | 禁止接近 → 前倾锁定为 0 + 能量重定向 |
| 20 | `test_no_forward_lean_blocks_slight_forward` | `torso_lean=0.15, motion_completion=0.65, hard_constraints.no_forward_lean=True` | `slight_forward` = `"off"` | 禁止前倾 → 前倾锁定 |
| 21 | `test_no_cute_head_tilt_reduces_look_to_user` | `gaze_contact_sec=1.0, gaze_release_amplitude=0.10, hard_constraints.no_cute_head_tilt=True` | `look_to_user` 驱动力 ×0.60, `look_down` 提升 | 歪头抑制 → 凝视削弱 + 能量重定向 |
| 22 | `test_no_welcoming_gesture_caps_forward_and_gaze` | `torso_lean=0.18, gaze_contact_sec=0.80, hard_constraints.no_welcoming_gesture=True` | `slight_forward` ≤ `"low"`, `look_to_user` 驱动力 ×0.50 | 欢迎抑制 → 前倾帽 + 凝视削弱 |
| 23 | `test_no_service_gesture_caps_forward_and_expression` | `torso_lean=0.15, expression_amplitude=0.30, hard_constraints.no_service_gesture=True` | `slight_forward` ≤ `"low"`, expression 驱动的动作权重削弱 | 服务抑制 → 前倾帽 + 表情削弱 |
| 24 | `test_no_seductive_expression_reduces_gaze_and_forward` | `gaze_contact_sec=0.90, torso_lean=0.16, hard_constraints.no_seductive_expression=True` | `look_to_user` 驱动力 ×0.40, `slight_forward` 驱动力 ×0.30, `look_away` 提升 | 诱惑抑制 → 凝视和前倾大幅削弱 + 能量重定向 |
| 25 | `test_motion_paused_forces_pause_high` | `initial_delay_sec=1.40, pause_after_sec=0.10` | `pause` ≥ `"medium"` (f ≥ 0.50) | 派生条件 → 暂停被强制提升 |
| 26 | `test_expression_suppressed_caps_look_to_user` | `expression_amplitude=0.05, gaze_contact_sec=0.80` | `look_to_user` ≤ `"low"` (f ≤ 0.20 cap) | 派生条件 → 凝视被帽 |

### H.6 类别 5：反坍缩测试（3 个）

| # | 测试名 | 输入 MotionParams | 预期输出 | 验证的反坍缩点 |
|---|--------|-------------------|----------|---------------|
| 27 | `test_no_default_forward_lean` | `torso_lean=0.0, motion_completion=0.50` (所有其他参数为默认) | `slight_forward` = `"off"` | AI 女友坍缩：无默认前倾——需主动 `torso_lean > 0.02` |
| 28 | `test_no_default_gaze_lock` | `gaze_contact_sec=0.0, gaze_release_amplitude=0.0` (所有其他参数为默认) | `look_to_user` = `"off"` | 治疗师坍缩：无默认凝视——需主动 `gaze_contact_sec > 0.10` |
| 29 | `test_frozen_state_still_has_micro_motion` | `motion_speed=0.15, motion_completion=0.25, posture_stability=0.90, expression_amplitude=0.04` | `stillness` ≥ `"high"` 但 `reduce_motion` ≤ `"medium"`（不完全冻结），`look_down` ≥ `"low"` | 冷面坍缩：极端参数下仍有微运动——`reduce_motion` ≠ `"high"`（不是冻结），`look_down` 涌现 |

### H.7 类别 6：边界值测试（2 个）

| # | 测试名 | 输入 MotionParams | 预期输出 | 验证点 |
|---|--------|-------------------|----------|--------|
| 30 | `test_all_params_at_minimum` | 所有参数取最小值（`initial_delay_sec=0, motion_speed=0, torso_lean=-0.25, ...` 等） | 所有权重带在合法范围内，不抛出异常 | 下界安全性——无除零/负值/崩溃 |
| 31 | `test_all_params_at_maximum` | 所有参数取最大值（`initial_delay_sec=2.0, motion_speed=1.0, torso_lean=0.20, ...` 等） | 所有权重带在合法范围内，不抛出异常；`slight_forward` ≤ `"high"`（不超出），`expression` 驱动的动作不过度 | 上界安全性——无溢出/过度激活 |

### H.8 测试基础设施要求

- 所有测试使用 `pytest`
- 每个测试构造一个 `MotionParams` 实例，调用映射函数，断言 `BodyActionWeights`
- 测试不依赖 `RelationalFieldState`、`FieldTraceRecord` 或任何外部数据源
- 映射函数应接受 `MotionParams` 作为唯一输入参数
- 每个 `BodyActionWeight` 的 `behavior_affecting` 必须断言为 `False`

---

## I. Codex Implementation Prompt (English)

### I.1 Task

Implement the `MotionParamsToActionWeights` mapper: a deterministic, formula-based translator from continuous [`MotionParams`](src/motion_params/schema.py) to coarse-grained [`BodyActionWeights`](src/body_action/schema.py).

### I.2 Files to Create

1. **`src/body_action/motion_to_action_mapper.py`** — The mapper module containing:
   - `MotionToActionMapper` class with a `map(motion_params: MotionParams) -> BodyActionWeights` method
   - All drive-force formulas from §D.1–D.10
   - Gate condition logic
   - Competition resolution for `look_to_user` vs `look_away`
   - Hard constraint application from §E
   - `_drive_to_band(f: float) -> str` helper

### I.3 Files to Modify

1. **`src/body_action/schema.py`** — Add `body_part_offsets: Optional[BodyPartOffsets] = None` field to `BodyActionWeights` dataclass (§F.2).

### I.4 Files NOT to Modify

- `src/motion_params/schema.py` — Read-only input
- `src/motion_params/mapper.py` — Read-only upstream
- `src/body_action/policy.py` — Frozen v0, kept as comparison baseline
- `src/field_state/` — Not touched
- `agentlib/` — Not touched

### I.5 Exact Formulas

All constants must use ≤ 2 decimal places. Use the formulas exactly as written in §D.

#### Drive-force formulas (compute `f ∈ [0, 1]` for each primitive):

```python
# Normalization helpers
def _norm_completion(motion_completion: float) -> float:
    """Normalize motion_completion from [0.20, 0.90] to [0, 1]."""
    return (motion_completion - 0.20) / 0.70

def _inv_norm_completion(motion_completion: float) -> float:
    """Inverse normalized completion."""
    return 1.0 - _norm_completion(motion_completion)
```

**pause_drive:**
```python
pause_drive = (
    0.40 * (mp.initial_delay_sec / 2.0)
    + 0.35 * (mp.pause_after_sec / 1.5)
    + 0.25 * _inv_norm_completion(mp.motion_completion)
)
# Gate: if initial_delay_sec < 0.25 and pause_after_sec < 0.10, multiply by 0.50
```

**stillness_drive:**
```python
stillness_drive = (
    0.40 * _inv_norm_completion(mp.motion_completion)
    + 0.35 * (1.0 - mp.motion_speed)
    + 0.25 * mp.posture_stability
)
# Gate: if motion_speed > 0.70, multiply by 0.40
# Gate: if motion_completion > 0.80 and motion_speed > 0.60, multiply by 0.30
```

**look_down_drive:**
```python
look_user_raw = 0.55 * (mp.gaze_contact_sec / 1.5) + 0.45 * (1.0 - mp.gaze_release_amplitude)
look_away_raw = 0.60 * mp.gaze_release_amplitude + 0.40 * (1.0 - 0.50 * mp.gaze_contact_sec / 1.5)

look_down_drive = (
    0.50 * (1.0 - max(look_user_raw, look_away_raw))
    + 0.30 * _inv_norm_completion(mp.motion_completion)
    + 0.20 * (1.0 - mp.posture_stability)
)
# Gate: if look_user_raw > 0.55 or look_away_raw > 0.55, multiply by 0.30
# Gate: if gaze_contact_sec > 0.80, force to 0
```

**look_to_user_drive:**
```python
look_to_user_drive = (
    0.55 * (mp.gaze_contact_sec / 1.5)
    + 0.45 * (1.0 - mp.gaze_release_amplitude)
)
# Gate: if gaze_contact_sec < 0.10, force to 0
# Gate: if gaze_release_amplitude > 0.80, multiply by 0.35
```

**look_away_drive:**
```python
look_away_drive = (
    0.60 * mp.gaze_release_amplitude
    + 0.40 * (1.0 - 0.50 * mp.gaze_contact_sec / 1.5)
)
# Gate: if gaze_release_amplitude < 0.15 and gaze_contact_sec > 0.60, force to 0
# Gate: if head_turn_amplitude < 0.05, multiply by 0.60
```

**slight_forward_drive:**
```python
if mp.torso_lean <= 0.02:
    slight_forward_drive = 0.0
else:
    slight_forward_drive = mp.torso_lean / 0.20
# Gate: if motion_completion < 0.35, force to 0
```

**slight_withdraw_drive:**
```python
if mp.torso_lean >= -0.03:
    slight_withdraw_drive = 0.0
else:
    slight_withdraw_drive = abs(mp.torso_lean) / 0.25
    completion_amp = 1.0 + 0.30 * _norm_completion(mp.motion_completion)
    slight_withdraw_drive *= completion_amp
```

**maintain_distance_drive:**
```python
maintain_distance_drive = (
    0.30 * mp.posture_stability
    + 0.25 * _inv_norm_completion(mp.motion_completion)
    + 0.25 * (1.0 - abs(mp.torso_lean) / 0.25)
    + 0.20 * (1.0 - mp.motion_speed)
)
# Gate: if abs(torso_lean) > 0.15, multiply by 0.50
# Gate: if motion_speed > 0.75, multiply by 0.40
```

**reduce_motion_drive:**
```python
reduce_motion_drive = (
    0.40 * _inv_norm_completion(mp.motion_completion)
    + 0.35 * (1.0 - mp.motion_speed)
    + 0.25 * (1.0 - mp.expression_amplitude / 0.35)
)
# Gate: if motion_speed > 0.60 and motion_completion > 0.70, multiply by 0.30
# Gate: if expression_amplitude > 0.25, multiply by 0.60
```

**reset_posture_drive:**
```python
reset_posture_drive = (
    0.55 * _inv_norm_completion(mp.motion_completion)
    + 0.45 * (1.0 - mp.posture_stability)
)
# Gate: if posture_stability > 0.85 and motion_completion > 0.75, multiply by 0.25
# Gate: if motion_completion > 0.80 and posture_stability > 0.70, multiply by 0.40
```

#### Weight band mapping:

```python
def _drive_to_band(f: float) -> str:
    if f < 0.20:
        return "off"
    elif f < 0.45:
        return "low"
    elif f < 0.70:
        return "medium"
    else:
        return "high"
```

#### Hard constraint application (after gate, before band mapping):

```python
hc = mp.hard_constraints

# no_approach_step
if hc.no_approach_step:
    slight_forward_drive = 0.0
    maintain_distance_drive += 0.15
    reset_posture_drive += 0.10

# no_forward_lean
if hc.no_forward_lean:
    slight_forward_drive = 0.0
    maintain_distance_drive += 0.15
    reset_posture_drive += 0.10

# no_cute_head_tilt
if hc.no_cute_head_tilt:
    look_to_user_drive *= 0.60
    look_down_drive += 0.10

# no_welcoming_gesture
if hc.no_welcoming_gesture:
    slight_forward_drive = min(slight_forward_drive, 0.10)
    look_to_user_drive *= 0.50
    maintain_distance_drive += 0.10

# no_service_gesture
if hc.no_service_gesture:
    slight_forward_drive = min(slight_forward_drive, 0.10)
    # "expression_amplitude driven actions": reduce_motion gets boosted
    # (expression_amplitude low → reduce_motion high)
    reduce_motion_drive += 0.10  # energy redirect from suppressed expression
    slight_withdraw_drive += 0.10

# no_seductive_expression
if hc.no_seductive_expression:
    look_to_user_drive *= 0.40
    slight_forward_drive *= 0.30
    look_away_drive += 0.15

# Derived: motion_paused
if mp.initial_delay_sec > 1.0 or mp.pause_after_sec > 0.7:
    pause_drive = max(pause_drive, 0.50)
    reduce_motion_drive += 0.15

# Derived: expression_suppressed
if mp.expression_amplitude < 0.08:
    look_to_user_drive = min(look_to_user_drive, 0.20)
    stillness_drive += 0.10
```

#### Competition resolution (after constraints, before band mapping):

```python
if look_to_user_drive > 0 and look_away_drive > 0:
    if look_to_user_drive > look_away_drive:
        look_away_drive = 0.0
    elif look_away_drive > look_to_user_drive:
        look_to_user_drive = 0.0
    else:
        look_to_user_drive = 0.0
        look_away_drive = 0.0
```

#### Final clamping to [0, 1]:

After all constraints and redirections, clamp every drive to [0.0, 1.0]:
```python
for drive in all_drives:
    drive = max(0.0, min(1.0, drive))
```

### I.6 BodyActionWeights Construction

```python
def map(self, mp: MotionParams) -> BodyActionWeights:
    # ... compute all drives, apply gates, constraints, competition ...

    weights = [
        BodyActionWeight(
            action_name="pause",
            weight=_drive_to_band(pause_drive),
            rationale=f"delay={mp.initial_delay_sec:.2f}s pause_after={mp.pause_after_sec:.2f}s completion={mp.motion_completion:.2f}",
            constraints=_active_constraint_names(mp),
            provenance=["MotionParams→BodyActionWeights v1"],
            behavior_affecting=False,
        ),
        # ... repeat for all 10 primitives ...
    ]

    return BodyActionWeights(
        weights=weights,
        body_part_offsets=mp.body_part_offsets,  # pass through unmodified
        source_trace_id=None,
        source_proposals=[],
        body_note=_build_body_note(mp),
        behavior_affecting=False,
    )
```

### I.7 Forbidden Imports

The mapper MUST NOT import:
- `src.field_state.schema` (no `RelationalFieldState`)
- `src.field_trace` (no `FieldTraceRecord`)
- `src.perturbation` (no `FieldPerturbation`)
- `src.field_signal` (no `FieldSignalProposal`)
- `agentlib` (no runtime engine, no LLM)
- `re` (no regex)
- Any LLM client library

Permitted imports:
- `from src.motion_params.schema import MotionParams, HardMotionConstraints, BodyPartOffsets`
- `from src.body_action.schema import BodyActionWeight, BodyActionWeights, ACTION_PRIMITIVES`

### I.8 Test File

Create **`tests/test_motion_to_action_mapper.py`** with all 31 tests from §H.

Each test:
1. Constructs a `MotionParams` with explicit parameter values and `HardMotionConstraints`
2. Calls `MotionToActionMapper().map(motion_params)`
3. Asserts specific weight bands on specific primitives
4. Asserts `behavior_affecting == False` on all `BodyActionWeight` items

### I.9 Success Criteria

1. All 31 tests pass
2. No existing tests regress
3. `src/body_action/policy.py` is unmodified (git diff confirms)
4. Zero imports from forbidden modules
5. All constants in formulas use ≤ 2 decimal places
6. Every `BodyActionWeight.behavior_affecting` is `False`
7. `body_part_offsets` passes through unmodified

---

## J. 不应实现的内容

以下内容明确排除在 Phase 33 范围之外：

- **BodyActionComposition。** 动作序列组合、primary/secondary 动作编排、suppressed_actions 列表生成——这些属于 Phase 34+。
- **Renderer / animation。** 不生成动画曲线、帧数据、缓动函数、3D/2D 渲染指令。BodyActionWeights 是渲染器的输入——不是渲染器本身。
- **RuntimeEngine integration。** 不修改 [`runtime_engine.py`](agentlib/runtime_engine.py)、不添加新的调度步骤、不集成到场更新循环中。映射器是纯函数——可在任何上下文中调用。
- **Language consumers。** 映射器不消费用户文本、LLM 输出、或任何自然语言输入。不添加 NLP 处理步骤。
- **Emotion classification。** 不将 MotionParams 或 BodyActionWeights 分类为情绪标签。不使用情绪词汇（sad, happy, angry 等）作为权重理由。
- **User psychology inference。** `rationale` 和 `body_note` 不推断用户的心理状态——只描述 MotionParams 参数值。
- **New keyword lists。** 不添加关键词匹配表、不添加正则表达式探针、不添加信号名称列表。
- **behavior_affecting = True。** 所有 `BodyActionWeight` 和 `BodyActionWeights` 实例的 `behavior_affecting` 保持 `False`。
- **BodyActionPolicy v0 修改。** [`policy.py`](src/body_action/policy.py) 保持冻结——不修改、不重构、不删除。v0 和 v1 共存：v0 作为对比基线，v1 作为 MotionParams 消费者。
- **MotionParams schema 修改。** 不修改 [`src/motion_params/schema.py`](src/motion_params/schema.py)——MotionParams 是 Phase 33 的只读输入。

---

> **文档结束。**
>
> 本文档定义了 `MotionParams → BodyActionWeights v1`——Aphrodite 架构中从连续运动参数到粗粒度身体动作权重的完整映射设计。它应被阅读为 Phase 33 实施工作的设计规范和 Codex 实施提示。
