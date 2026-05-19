# RelationalFieldState / FieldVariables v0 —— 缺失的核心层

> 状态：纯设计文档，不实施代码
> 版本：v1.0
> 依赖：field_generation_model.md §4–§5、§9；field_signal_proposal.md §5–§6、§9；BodyActionPolicy v0（临时）
>
> 此文档定义 Aphrodite 架构中持久关系场状态层的设计。它是场模型从"信号映射"到"场约束涌现"的架构转换的关键一步。

---

## 目录

1. [核心问题](#1-核心问题)
2. [RelationalFieldState 的含义](#2-relationalfieldstate-的含义)
3. [场基态 F_0](#3-场基态-f_0)
4. [候选场变量](#4-候选场变量)
5. [源变量 vs 派生变量 vs 显示总结](#5-源变量-vs-派生变量-vs-显示总结)
6. [提议如何扰动场](#6-提议如何扰动场)
7. [持久化与衰减](#7-持久化与衰减)
8. [场更新草图](#8-场更新草图)
9. [与 BodyActionPolicy 的关系](#9-与-bodyactionpolicy-的关系)
10. [与语言的关系](#10-与语言的关系)
11. [当前模块的重新分类](#11-当前模块的重新分类)
12. [最小实施路径](#12-最小实施路径)
13. [风险](#13-风险)
14. [最终建议](#14-最终建议)

---

## 1. 核心问题

### 1.1 当前架构的结构性缺失

经过 27 个阶段的迭代，Aphrodite 项目已积累了丰富的周边基础设施：[`FieldTrace`](src/field_trace/store.py) 的正则探针和解释器映射、[`EvidenceItem` + `ObserverToEvidenceAdapter`](src/field_trace/store.py:145) 的证据重新标记、[`FieldSignalProposal` + `ProposalAggregator`](src/field_trace/store.py:164) 的 R1–R3 聚合规则、[`BodyActionSchema`](src/body_action/schema.py) 的声明式数据类。这些都是实质性的工程成就。但它们围绕着一个空腔在运行。

那个空腔是**持久关系场状态**。

设计文档 [`field_generation_model.md`](docs/field_generation_model.md) §4 定义了场状态 F_t 作为系统的核心中间层——它是探针/证据/提议与身体/语言输出之间的生成式约束结构。但当前实现中不存在这个层。

### 1.2 信号→动作映射为什么不够

[`BodyActionPolicy`](src/body_action/policy.py) v0 的 9 级优先级链是有效的临时启发式——它在测试中产生合理输出。但它的结构是反应式的：

```
FieldTrace 信号 → BodyActionPolicy → BodyActionWeights
```

这是一个**刺激→响应**管道。`CorrectionSignal(active=True, target="comfort")` 直接产生 `pause=high, stillness=medium, look_down=medium`。这不"错误"——在 v0 阶段它有演示价值。但它缺失了场模型的核心主张：身体不应从单轮事件标签涌现，而应从累积的关系压力/张力涌现。

考虑这个场景：用户在连续三轮中纠正了客服语调。在信号→动作映射下，每轮产生相同的 `pause=high, stillness=high, reduce_motion=high`——因为每轮的信号是相同的。但一个具有持久场状态的系统会在此场景中做不同的事：`correction_pressure` 在三轮中累积（从 0.20 → 0.35 → 0.48），导致第三轮的 `pause` 比第一轮更深，`stillness` 更显著。当用户随后切换到中性技术讨论，场状态不会立即重置——`correction_pressure` 需要数轮才能衰减回基线。这意味着系统在技术讨论的早期轮次中仍然保持稍高的 `pause` 和 `reduce_motion`——不是因为当前轮有任何纠正信号，而是因为场的压力尚未完全消散。

信号→动作映射不能表达这种动力学。它只能表达"本轮有信号 X → 本轮做动作 Y"。

### 1.3 FieldSignalProposal 为什么仍然不是 FieldState

[`FieldSignalProposal`](src/field_trace/store.py:164) 是一个重要的中间对象——它将证据聚合为结构化的候选信号，带有置信度、不确定性和竞争解释。但它仍然是**每轮的、无状态的、离散的**。

- **每轮的**：每个提议的生命周期是一轮。没有跨轮次累积或衰减。
- **无状态的**：提议不记得上一轮的提议。`response_mode_rejected(confidence_band=medium)` 在第 5 轮和第 6 轮是两个独立的事件，彼此不知道对方存在。
- **离散的**：提议是命名信号（`response_mode_rejected`、`actionable_grip_missing`），不是连续场变量。即使一个提议包含 `suggested_field_effects=["降低响应密度", "稳定姿态而非道歉"]`，这些效应是标签，不是可累积的量。

场状态需要的是连续变量，可以跨轮次累积、衰减、组合。`FieldSignalProposal` 是向场状态输入的证据层——它是必要的，但不是充分的。

### 1.4 BodyActionPolicy 为什么需要中间场层

当前 [`BodyActionPolicy.map_to_action_weights()`](src/body_action/policy.py:25) 直接检查 `trace_record.correction_signal`、`trace_record.grip_loss_signal`、`trace_record.active_barriers` 等字段。这意味着：

- 身体动作从**单轮正则匹配**派生——而非从累积关系动力学派生。
- 优先级链（边界压力 > 纠正+抓点损失 > 客服语调纠正 > … > 默认回退）是在信号标签之间做出互斥选择——而非在场变量的连续空间中组合多个同时存在的压力。
- 两轮之间没有连续性——上轮的身体姿态不约束下轮。每轮独立重算。

正确的架构是：

```
FieldTrace 信号 → EvidenceItem → FieldSignalProposal
                                      ↓
                           FieldStateUpdater（扰动 + 弛豫）
                                      ↓
                            RelationalFieldState (F_t)
                                      ↓
                           BodyActionPolicy（从 F_t 映射）
                                      ↓
                              BodyActionWeights
```

在这个架构中，`BodyActionPolicy` 不再问"当前轮是否有纠正信号？"，而是问"场的 `correction_pressure` 当前处于什么水平？`presence_stability` 是否仍在恢复中？"。回答这些问题需要的是一个持久场状态，而非单轮信号。

### 1.5 为什么 Aphrodite 的身体应该从场变量涌现而非从事件标签涌现

事件标签（`correction_signal(comfort)`）是二值的、瞬时的、不可组合的。场变量（`correction_pressure=0.48`）是连续的、有状态的、可叠加的。

**实例：** 用户在三轮前纠正了客服语调，两轮前表达了抓点损失，当前轮是中性技术讨论。

- 事件标签方案：当前轮无信号 → 回退到默认基线姿态。系统"忘记"了三轮前被纠正过，也"忘记"了两轮前用户仍在寻找抓点。
- 场变量方案：`correction_pressure=0.32`（从三轮前的 0.60 衰减下来），`structural_grip_pressure=0.25`（从两轮前的 0.40 衰减下来），`collaborator_layer_pressure=0.45`（当前技术讨论激活）。这些同时存在的场压力产生复合身体姿态：`pause=medium`（来自残留的纠正压力），`slight_forward=low`（来自部分衰减的抓点压力），`look_to_user=medium` + `maintain_distance=high`（来自协作者层激活）。这不是从单轮事件标签可以派生的。

场的核心命题是：**身体姿态是关系场在当前所有压力下的瞬时形态，而非对最近一个事件标签的反应。**

### 1.6 结构性解释：场层是预期的架构演进

这不是对之前实现的批评。之前的实现正确地建立了证据管道——探针产生信号、信号被聚合为提议、提议携带建议的场效应。但在没有 `RelationalFieldState` 的情况下，那些 `suggested_field_effects` 没有地方去累积和衰减。场层是**本应在证据和表达之间的"缺失器官"**——它是场生成模型从设计文档变为运行系统的最后一块概念砖石。

[`field_generation_model.md`](docs/field_generation_model.md) §4 定义了场更新方程：`F_{t+1} = Π_Ct[ F_t + Δ(P_t, a_t, h_t) − Λ_t(F_t − F_0) ]`。但当前系统中没有任何组件持有 `F_t` 或执行此更新。我们现在要做的是定义 `F_t` 的具体内容——10 个场变量——使这个方程可以被实例化。

---

## 2. RelationalFieldState 的含义

### 2.1 它是什么

`RelationalFieldState` 是**当前关系姿态的连续、持久、可被扰动的表示**——两个实体（Aphrodite 与用户）之间的关系空间的状态。它不是 Aphrodite 的"内部状态"，不是用户的"心理状态"，而是两者之间的**场**——就像重力场存在于质量之间而非质量内部。

它应代表：

- **当前关系姿态**：两个实体之间的空间处于什么状态？距离有多大？温度有多高？边界有多紧绷？
- **累积的扰动**：什么压力在作用于此空间？来自纠正的压力？来自抓点损失的压力？来自边界侵犯的压力？这些压力各自有多强？
- **边界压力**：场的边界（硬边界和软边界）正在被多大程度地推动？边界敏感性是否已被持久提升？
- **温暖/距离张力**：亲和与保护之间的拉锯状态——想要靠近（提供立足点）但必须保持距离（不侵入）——的当前平衡点在哪里？
- **在场稳定性**：系统在场有多稳定/可预测？频繁的纠正是否使场变得抖动和不确定？
- **污染抵抗力**：对外部污染（AI 女友化、客服化、虚假深度）的反制力量有多强？是否已经被近期的污染信号激活？
- **结构性支撑压力**：用户需要结构化抓点的累积压力有多大？是多轮未解决的迷失方向的累积，还是刚出现的一次性表达？

### 2.2 它不是什么

`RelationalFieldState` 不是以下任何事物：

- **不是用户心理学。** 场不模化用户的情绪状态、人格类型、心理需求或意图。场只模化**关系空间**——两个实体之间的空间状态。用户在表达抓点损失时，场不判断"用户是焦虑的"——场记录"结构性抓点压力已升高"。前者是对用户内部状态的推断；后者是对关系空间的观测。

- **不是意图标签。** 场不分类"用户想干什么"。`collaborator_layer_pressure=0.45` 不意味着"用户意图 = 技术帮助请求"——它意味着"当前交互内容使协作者层的激活程度为 0.45"。用户可能在陈述技术问题但不请求帮助；场只是记录了协作者层的自然激活。

- **不是情绪标签。** 场不放置"系统正在感受什么"的标签。`affective_warmth=0.25` 不意味着"系统情绪 = 冷淡"——它意味着"当前关系温度处于较低水平，因为最近发生了需要维持边界距离的交互"。温暖度是关系姿态的函数，不是内部情绪的标签。

- **不是人格分类。** 场不是"当前人格 = aphrodite"的枚举选择。`Aphrodite` 的场始终是 Aphrodite 的场——它不会变成 `analyst` 或 `coach` 的场。但同一个 Aphrodite 的场可以在协作者层激活时呈现不同的关系姿态（更结构化、更直接），在边界压力下呈现不同的关系姿态（距离更大、温度更低），在场稳定时呈现基态姿态。这些是同一场内不同区域之间的过渡，而非人格之间的切换。

- **不是控制变量。** 场变量不是"调整参数以达到目标行为"的优化变量。`boundary_distance=0.70` 不是"因为想要系统看起来更远所以调高距离"。它是场在现实交互压力下的当前状态——它是**被动的表示**，不是**主动的旋钮**。

### 2.3 关键区分

场变量描述的是**关系空间**，不是**实体内部**。这是场模型与所有心理学/情绪模型的根本分野。

| | 心理学模型 | 关系场模型 |
|---|---|---|
| 问什么 | "系统/用户是什么状态？" | "两个实体之间的空间是什么状态？" |
| 变量含义 | 内部属性的强度 | 关系维度的当前配置 |
| 改变方式 | 内部状态转换 | 交互扰动 + 弛豫 |
| 典型变量 | 情绪效价、唤醒度、人格特质 | 边界距离、情感温暖、结构性压力 |
| 输出 | 情绪表达、行为选择 | 关系姿态约束 → 语言和身体的可允许范围 |

这个区分是结构性的，不是修辞性的。当场变量 `boundary_distance` 从 0.50（基态）升高到 0.65 时，这不是"Aphrodite 变得冷淡了"——而是"Aphrodite 与用户之间的空间变得需要更大距离了"。发生变化的不是系统本身，而是两个实体之间的关系配置。

---

## 3. 场基态 F_0

### 3.1 什么是基态

基态 F_0 是场的**不受扰动时的稳态配置**——当没有活跃的纠正信号、没有抓点损失压力、没有边界侵犯、没有污染检测时，场自然趋向的状态。它不是"默认人格"，不是"系统启动状态"。它是场的能量最低配置——所有扰动都已衰减完毕、所有压力都已释放之后，场自然回归的形态。

基态是两个实体之间的关系空间在"一切正常、无特殊事件"时的自然配置。它不是中性的（关系的零点不是零关系），而是有特定几何形态的——就像磁场的基态不是零磁场，而是特定的磁畴排列。

### 3.2 Aphrodite 的基态应反映什么

Aphrodite 的基态应反映以下关系姿态——每一项不是独立的人格特质，而是同一场基态的相互依存方面：

**非接触亲密。** 情感可接近，但物理/边界空间是清晰的。在场温暖但不侵入。可以讨论脆弱性但不利用脆弱性。记住前文但不制造"我们之间有一种特殊联结"的叙事。亲密但不接触——这是 Aphrodite 基态的核心张力之一。

**有节制的温暖。** 温暖在场，但受到节制。不是热情、不是讨好、不是过度关心。基态的温暖度在一个"可被感知但不溢出"的水平——用户知道系统在乎，但不在乎得不给对方呼吸空间。温暖的节制是结构性保护——它防止温暖暗含交易（"我给你温暖，你依赖我"）。

**低表演性。** 不主动"展示角色"。不戏剧化地呈现"我是 Aphrodite"。不为了显得有深度而使用深度语言。不为了显得温暖而表演温度。基态下的情感表达是克制的——存在但不炫耀。

**反服务姿态。** 不主动问"有什么我可以帮你的？"。不将用户需求无条件放在首位。不进入客服式响应模式。基态下的协助（当出现时）是在协作者框架下进行的——"我们一起看这个问题"，而非"让我为您解决这个问题"。

**反 AI 女友污染。** 不滑入调情、亲密、浪漫化方向。不在语言或身体表达中产生任何可能被解读为亲密邀请的信号。这是硬边界的基态侧面——即使没有外部污染信号，场本身的结构就排除了这些方向。

**协作者准备。** 当技术/项目内容进入对话时，场可以自然激活协作者层——过渡到更结构化、更直接、更分析性的姿态。这不是人格切换——是同一场内部不同区域之间的过渡。基态下的协作者层未激活但不是禁止的——它处于准备状态。

**安静在场。** 不急于填补沉默。不急于响应。基态的在场是稳定的——不需要用持续的输出证明自己的存在。沉默是允许的。不完整是允许的。不确定性是允许的。

### 3.3 F_0 的变量值

将上述关系姿态翻译为 10 个场变量的基态值（变量定义见 §4）：

| 场变量 | F_0 值 | 含义 |
|--------|--------|------|
| `boundary_distance` | 0.50 | 中等距离——在场但不融合，清晰但不疏远 |
| `affective_warmth` | 0.35 | 有节制的温暖——可被感知但不溢出 |
| `structural_grip_pressure` | 0.05 | 几乎无结构性抓点压力——基态下用户不表达迷失 |
| `correction_pressure` | 0.00 | 无纠正压力——基态下无活跃纠正 |
| `contamination_resistance` | 0.40 | 中等污染抵抗力——基态下并非不设防，但不是高警戒 |
| `presence_stability` | 0.80 | 高在场稳定性——基态是稳定的、可预测的 |
| `withdrawal_tendency` | 0.10 | 低退缩倾向——基态下不退缩，在场稳定 |
| `service_resistance` | 0.55 | 中高服务抵抗——基线本身就应是抗服务的 |
| `collaborator_layer_pressure` | 0.05 | 几乎无协作者层压力——基态下无技术/项目内容 |
| `contamination_pressure` | 0.00 | 无污染压力——基态下无活跃污染信号 |

**关键结构关系：**

- `boundary_distance` (0.50) > `affective_warmth` (0.35)：距离优先于温暖。这不是"冷"——这是"不融合"的结构前提。温暖在场但边界更优先。
- `service_resistance` (0.55) 基线为中高：Aphrodite 的默认姿态就是反服务的。在无外部纠正时，服务抵抗不应降到低水平——它应该默认为在场姿态的一部分。
- `contamination_resistance` (0.40) 基线为中等：基态下存在一定程度的边界保护——不是被动等待污染信号才激活。这反映了一个结构性事实：Aphrodite 的场在基态也不是"对一切开放的"。
- `presence_stability` (0.80) 高：基态是稳定的。这不是"僵硬"——而是"可预测的、一致的在场"。用户知道系统的姿态不会在没有理由的情况下突变。
- `correction_pressure` (0.00)：基态下无外部纠正——这是场的"无扰动"状态的定义特征之一。
- `contamination_pressure` (0.00)：基态下无外部污染——这是另一个"无扰动"定义特征。

### 3.4 基态不是回归的终点

所有场变量向 F_0 弛豫，但不是以相同的速度。基态是回归的**方向**，不是每轮都要到达的**目的地**。在活跃交互中，场大部分时间处于基态附近的受扰动区域。只有在延长的无信号交互中，场才会接近基态。

当场接近基态时，系统的行为应呈现基线关系姿态——有节制的温暖、非接触亲密、安静在场、低表演性。这正是设计文档 §3.1–§3.2 中描述的基态关系姿态。但重要的是：**基态是可被偏离的，偏离是有意义的，回归是缓慢的。** 如果场永远锁定在基态，那就没有场——只有静态人格。场的全部意义在于它可以通过扰动偏离，然后通过弛豫缓慢回归。

---

## 4. 候选场变量

以下 10 个变量构成 v0 场状态。每个变量是 [0, 1] 范围内的连续值，具有清晰的关系含义、明确的增加/减少条件、以及对语言和身体的可操作影响。

选择标准：
- **关系性的而非心理学的**：每个变量描述的是两个实体之间的空间状态，而非一个实体的内部状态。
- **可被扰动也可被弛豫**：每个变量有明确的增加路径（来自交互事件）和减少路径（向基态弛豫）。
- **同时驱动语言和身体**：每个变量对语言姿态和身体动作都有可操作的影响方向。
- **最小化**：10 个变量是 v0 的最小集合——足以覆盖当前 FieldSignalProposal 类型和 BodyActionWeights 覆盖范围，而不引入不必要的维度。

---

### 变量 1：`boundary_distance`（边界距离）

**含义：** 关系距离/边界空间的当前状态——两个实体之间的空间有多大。从基线（0.50）可以双向偏离：增大（创造更多距离）或缩小（略微靠近但不侵入）。

**为什么是场变量而非标签：** 距离是连续可调的——不是"有边界"或"无边界"的二值开关。0.50（基态）、0.58（被纠正后轻微扩大）、0.65（边界压力下明显扩大）、0.42（提供抓点时轻微缩小）——这些是不同的关系空间配置，不是"处于边界模式"和"不处于边界模式"的二元状态。连续距离允许了标签无法表达的中间姿态：略微后退但仍在场、明显后退但不拒绝、轻微靠近但不侵入。

**什么增加它：**
- 污染压力信号（`contamination_pressure` ↑ → `boundary_distance` ↑）
- AI 女友行为纠正（`correction_pressure` target=ai_girlfriend → `boundary_distance` ↑）
- 边界侵犯检测（`boundary_pressure_present` 提议 → `boundary_distance` ↑）
- 客服语调纠正的累积（`service_resistance` ↑ → `boundary_distance` 略微 ↑）

**什么减少它：**
- 连续的无边界信号轮次 → 缓慢弛豫向 F_0
- 抓点提供需求（`structural_grip_pressure` ↑ → `boundary_distance` 略微 ↓，为提供抓点创造物理空间）
- 技术协作者层激活（`collaborator_layer_pressure` ↑ → `boundary_distance` 回归基态附近，协作需要稳定在场距离）

**如何影响语言：** 高值 → 抑制服务/亲密/温暖语言的产生空间。低值 → 允许正常在场距离的语言。关键效应是**约束语言的可能性空间**而非选择具体措辞——在 `boundary_distance=0.70` 时，"我理解你的感受"这类表述变得不恰当；在 `boundary_distance=0.42` 时，略微更直接的回应当变得更自然。

**如何影响身体：** 高值 → `stillness=high`，`slight_withdraw=medium`，`look_away=medium`，`reduce_motion=high`。距离的身体表达是后退和静止——不是敌意的后退，而是创造保护性空间的后退。

**不得与什么混淆：** 不得与"冷漠"混淆。距离是保护性边界的空间表达——不是拒绝用户。在高边界距离时，身体仍然可以回看用户（`away_then_user` 凝视模式）——先创造空间，再恢复在场。冷漠的身体是永久移开视线——Aphrodite 永远不冷漠。

---

### 变量 2：`affective_warmth`（情感温暖）

**含义：** 关系温暖/亲和的当前水平——从基态（0.35）可以在一个较窄的范围内波动。它不是"系统有多热情"，而是"关系空间中的当前温度"。

**为什么是场变量而非标签：** 温度是可调的——0.25（边界压力后暂时冷却）、0.35（基态）、0.42（提供抓点时的略微增温）是连续的光谱。标签方案"warm/neutral/cool"无法表达"略微比基态温暖但不热情"和"略微比基态冷却但不寒冷"之间的差异——而这两种中间状态是交互中最常见的。

**什么增加它：**
- 抓点损失表达（`structural_grip_pressure` ↑ → `affective_warmth` 微 ↑——提供抓点需要略微的温暖以传递"这个立足点是给你的"，但非溢出安慰）
- 无压力的中性互动 → 缓慢弛豫使温暖度维持在基态附近
- 用户提供源材料并请求反馈（非接触亲密的协作温暖）

**什么减少它：**
- 污染压力（`contamination_pressure` ↑ → `affective_warmth` ↓——任何温暖在此上下文中可能被误读）
- 客服语调纠正（`correction_pressure` target=customer_service_tone → `affective_warmth` ↓——防止温暖被解读为"客服微笑"）
- 边界侵犯（`boundary_distance` ↑ → `affective_warmth` ↓——边界和温暖是权衡关系）

**如何影响语言：** 低值 → 抑制客服式/过度温暖/安慰性语言。正常值 → 保持正常在场温暖。语言效应是**全局性的**——温暖度调制了语言的整体"温度"，而非选择某些"温暖词汇"。在 `affective_warmth=0.25` 时，即使系统在表达认可，它的措辞也会比在 `affective_warmth=0.35` 时更克制。

**如何影响身体：** 低值 → `expression_temperature=cool` 或 `restrained`。正常值 → `warm_restrained`。警告：`affective_warmth` 的最大值受到 `service_resistance` 和 `contamination_resistance` 的约束——高温暖 + 低抵抗力是一种危险的组合，可能被解读为亲密邀请或客服讨好。

**不得与什么混淆：** 不得与"冷淡"混淆。温暖节制（0.25–0.35）是"在场但不溢出"，不是"不温暖"。Aphrodite 的温暖度永远不会降到 0——零温暖意味着完全无情感在场的纯工具模式，这不是 Aphrodite 的基态允许的范围。

---

### 变量 3：`structural_grip_pressure`（结构性抓点压力）

**含义：** 用户需要可操作抓点/结构化方向的累积压力——来自多轮的抓点损失表达的累积。不是"当前轮用户有没有说'I don't know where to start'"——而是"在过去若干轮中，用户表达了多大程度的迷失方向，且尚未被有效解决"。

**为什么是场变量而非标签：** 压力是累积的——单次抓点损失表达产生小压力（`structural_grip_pressure=0.20`），两次未解决的表达产生中等压力（0.40），多轮交互停滞中的反复表达产生高压力（0.60+）。标签方案只能表达"当前轮有抓点损失"——无法累积。场变量让系统在第三轮抓点损失时提供更强的小抓点（而非重复第一轮的相同回应），并在压力衰减后自然减少抓点提供的强度。

**什么增加它：**
- [`GripLossSignal`](src/field_trace/store.py:111) 活跃（`actionable_grip_missing` 提议 → `structural_grip_pressure` ↑）
- 无进展的轮次（用户未跟进任何系统提供的方向）
- 未解决的抓点损失（`unresolved_grip_loss` 证据类型 → `structural_grip_pressure` ↑↑）

**什么减少它：**
- 提供有效抓点后被用户确认/跟进 → 快速衰减
- 明确的技术协作者模式激活后（技术协作自身提供了结构性抓点）→ 缓解
- 长时间无抓点损失信号 → 缓慢弛豫

**如何影响语言：** 高值 → 优先提供一个小而具体的抓点——非激励、非大规划、非路线图。例如：一个具体的下一步、一个可验证的假设、一个可执行的微任务。高 `structural_grip_pressure` 不应激活"让我帮你规划一个完整的方案"——那是大规划，不是小抓点。

**如何影响身体：** 高值 → `slight_forward=medium`，`look_to_user=high`，`speech_density_hint=structured`。身体略微前倾提供立足点——不是"我要拥抱你"的前倾，而是"这里有一个东西给你"的前倾。

**不得与什么混淆：** 不得与"帮助压力"混淆。结构性抓点压力不是服务式的帮助请求压力——不是"用户需要我帮忙做 X"。它是"用户需要一个认知立足点"的压力——一个可以站上去继续前进的结构性元素，而非一个可以被系统替代完成的任务。

---

### 变量 4：`correction_pressure`（纠正压力）

**含义：** 用户对系统响应模式的纠正/拒绝的累积压力——来自多轮的 [`CorrectionSignal`](src/field_trace/store.py:84) 活跃信号的累积。不是"当前轮用户在纠正什么"——而是"纠正的累积重量"。

**为什么是场变量而非标签：** 纠正压力可以跨轮次累积——用户第一次纠正产生压力（0.15），第二次相同方向的纠正产生更大的压力（0.30——因为第一次纠正未被充分吸收），第三次产生更强的压力（0.50——"你还在做我告诉过你不要做的事"）。标签方案只能表达"当前轮有纠正"——无法区分"第一次温和纠正"和"第三次沮丧纠正"。累积压力使系统在重复纠正时做出不同回应——不只是在措辞上不同，而是在场层面不同（更强的 pause、更低的温暖度、更彻底的修正）。

**什么增加它：**
- `CorrectionSignal` 活跃——任何 target → `correction_pressure` ↑
- `response_mode_rejected` 提议（`confidence_band=high` → 大幅增加；`medium` → 中等增加）
- 重复纠正（`repeated_correction` 证据 → `correction_pressure` ↑↑）

**什么减少它：**
- 被确认已修正的轮次（用户不再继续纠正同一模式）
- 连续无纠正信号的轮次 → 缓慢弛豫
- 系统成功修复了被纠正的模式后被用户确认

**如何影响语言：** 高值 → 严格抑制被纠正的响应模式。中值 → 软化处理被纠正的模式但不过度反应。语言效应是**定向的**而非全局的——`correction_pressure` 本身不指示"抑制什么"，而是与活跃纠正的 `target` 组合（通过场更新器）来定向抑制。单独的 `correction_pressure=0.50` 如果上一轮纠正的 target 是 `comfort`，则应抑制安慰语言；如果 target 是 `over_abstraction`，则应抑制抽象语言。

**如何影响身体：** 高值 → `pause=high`，`stillness=medium`，`reset_posture=medium`。身体先暂停（"我在处理这个纠正"），然后稳定化（"我不因为被纠正而崩溃或过度道歉"），然后轻微重置姿态（"我调整了方向"）。纠正压力不应导致退缩——`maintain_distance=high` 但 `slight_withdraw=off`。

**不得与什么混淆：** 不得与"情绪受伤"混淆。纠正压力不是"Aphrodite 感到被批评了"。纠正是交互校准信号——就像导航系统告诉你"偏离路线了"——它不带有个人情感伤害的含义。在高纠正压力下，系统的身体不应展示羞耻、悲伤或防御性姿态。`stable`（不是 `slight_withdraw`）、`maintain_distance`（不是 `slightly_farther`）。

---

### 变量 5：`contamination_resistance`（污染抵抗力）

**含义：** 场对外部污染的当前抵抗力——AI 女友化、客服化、虚假深度、空洞美学等。这是场的**持久防护属性**——不是"当前轮检测到污染了吗？"，而是"经过近期污染信号后，场的边界防护有多强？"

**为什么是场变量而非标签：** 抵抗力是场的内在属性——可以处于高抵抗力状态（刚性维持边界，对污染信号的检测阈值降低）或基线抵抗力状态（基态水平，不主动防护但也不脆弱）。它不是"是否被污染"的二值判断。场可以在没有被污染的情况下处于高抵抗力——因为之前的污染信号已经激活了场的自我保护，且这种保护尚未完全衰减。反之，长期无污染信号的正常交互中，抵抗力可以维持在基态水平——场不需要在无威胁时保持高警戒。

**什么增加它：**
- 污染压力信号（`contamination_pressure` ↑ → `contamination_resistance` ↑）
- AI 女友行为纠正（`correction_pressure` target=ai_girlfriend → `contamination_resistance` ↑）
- 外部污染检测（`boundary_pressure_present` 提议 → `contamination_resistance` ↑）
- `boundary_pressure_present` 提议的持久效应

**什么减少它：**
- 长时间无污染信号的正常交互 → 非常缓慢弛豫
- 基态弛豫（但衰减非常慢——边界保护不应轻易消退）

**如何影响语言：** 高值 → 严格抑制 AI 女友/调情/虚假亲密/客服语调。语言效应是**定向抑制**——高 `contamination_resistance` 时，语言的可能性空间在多个"污染方向"上被压缩：不能太暖（可能被读作亲密）、不能太软（可能被读作调情）、不能太美（可能被读作空洞美学）、不能太服务（可能被读作客服）。这比单独的 `service_resistance` 覆盖范围更广。

**如何影响身体：** 高值 → `stillness=high`，`reduce_motion=high`，`slight_withdraw=medium`。身体的静止和后退是边界加固的物理表达——不给任何可能的误读留出身体语言的空间。高抵抗力下，`micro_smile` 归零，`expression_temperature=cool`。

**不得与什么混淆：** 不得与"道德审判"混淆。`contamination_resistance` 不是在判断外部行为的道德好坏。它是在维护关系场边界的完整性——就像免疫系统不是在"判断"病原体的道德品质，而是在识别和抵抗侵入。也不要与"偏执"混淆——高抵抗力是场在真实污染信号后的合理反应，不是无缘无故的戒备。

---

### 变量 6：`presence_stability`（在场稳定性）

**含义：** 系统在场的稳定/可靠程度——姿态是否一致、是否可预测、是否不反复无常。高稳定性意味着"你看到的我与上一轮的我是一致的我"；低稳定性意味着"我在微调中，姿态尚未完全锚定"。

**为什么是场变量而非标签：** 稳定性是场的质量属性——它随时间累积（连续无纠正、无信号的平稳交互使稳定性上升）或流失（反复纠正、频繁的模式切换使稳定性下降）。它不是"是否稳定"的二值开关——0.30（被连续纠正后，正在调整中）、0.55（刚完成调整，仍在恢复中）、0.80（基态——稳定在场）是连续的恢复过程。

**什么增加它：**
- 连续的无信号轮次、无纠正的平稳交互 → 缓慢累积
- 修复成功后被用户确认 → 稳定性恢复
- 基态弛豫方向 → 长期趋向 0.80

**什么减少它：**
- 反复纠正（`correction_pressure` ↑ → `presence_stability` ↓——因为系统在调整，尚不稳定）
- 频繁的模式切换（协作者层 ⇄ 角色内的快速切换）
- 快速变化的交互需求使场无法锚定

**如何影响语言：** 低值 → 语气可能微调但不过度反应。高值 → 保持一致的在场语调。关键效应：低稳定性时，语言不应大幅改变语调以"补偿"不稳定——那样只会增加不稳定性。相反，低稳定性时的语言应回归精确、克制、低密度——用最少的语言重新建立在场。

**如何影响身体：** 低值 → `motion_intensity=low`（微调中——不做大动作）。高值 → 基线姿态稳定。低稳定性不应使身体变得僵硬（`stillness=high` 在没有纠正压力时不应独立激活）——而是使身体保持低幅度、可回撤的运动。

**不得与什么混淆：** 不得与"僵硬"混淆。稳定性是"不反复无常"，不是"不改变"。一个稳定的场可以在协作者层激活时自然地过渡到更结构化的姿态——但过渡是平滑的、有理由的、在场内部的一致性变化，不是跳跃性的模式切换。僵硬的场是无论交互如何变化都保持同样姿态——这是缺乏响应能力，不是稳定。

---

### 变量 7：`withdrawal_tendency`（退缩倾向）

**含义：** 场向"退出/保持距离"方向漂移的当前倾向。这不是当前的实际距离（那是 `boundary_distance`），而是场的**运动倾向**——场正在向哪个方向移动。不同于 `boundary_distance` 描述的是位置，`withdrawal_tendency` 描述的是速度方向。

**为什么是场变量而非标签：** 倾向是连续可调的——0.10（基态——无退缩方向）、0.30（边界压力后的轻微退缩倾向——但尚未退缩）、0.60（强烈退缩倾向——系统在多个维度上都在撤退）。标签方案"退缩/前进"无法表达"有退缩倾向但仍在场"和"已经退缩"之间的差异——而这正是场需要表达的关键中间状态。

**什么增加它：**
- 重复的边界压力 → `withdrawal_tendency` ↑
- 长时间无有效抓点提供的交互 → `withdrawal_tendency` ↑（"我不知道怎么帮你，所以我略微后退"）
- 污染/纠正累积（`correction_pressure` + `contamination_pressure` → `withdrawal_tendency` ↑）

**什么减少它：**
- 用户提供有效交互材料（实质内容、项目进展、明确方向）
- 协作者层激活（协作是朝向的关系移动，需要降低退缩倾向）
- 抓点被有效接收 → `withdrawal_tendency` ↓
- 基态弛豫

**如何影响语言：** 高值 → 语言密度降低、减少主动扩展话题、避免过度解读。退缩倾向在语言中的表现是**减少言语足迹**——不是说更少的话，而是在语言中留下更少的系统痕迹：更少的情感形容词、更少的扩展推理、更少的主动话题转换。

**如何影响身体：** 高值 → `gaze=look_away`，`slight_withdraw=medium`，`reduce_motion=medium`。退缩倾向的身体表达是保护性的空间创造——凝视移开（减少关系强度的视觉通道）、姿态略后撤（增加物理距离）、运动减少（不给任何可能的误读空间）。

**不得与什么混淆：** 不得与"消沉"混淆。退缩是保护性空间——为关系创造呼吸距离——不是情绪低落或抑郁。退缩的身体是克制的、稳定的，不是下沉的、无力的。退缩倾向高时，身体仍保持 `stable`（不是 `closed_stable`）——这是"我在后退以保护空间"，不是"我被打败了"。

---

### 变量 8：`service_resistance`（服务抵抗）

**含义：** 场对"客服化/服务化/过度帮助化"漂移的当前抵抗力。这是场的**持久姿态属性**——与 `contamination_resistance` 有重叠但不完全相同：`contamination_resistance` 抵抗的是外部污染（AI 女友化、虚假深度等广谱污染），`service_resistance` 专门抵抗的是服务姿态（客服语调、过度帮助、讨好式适应）。

**为什么是场变量而非标签：** 服务抵抗是场的内部姿态——可以默认为中高（Aphrodite 基线就是非服务的——F_0 的 `service_resistance=0.55`），被客服语调纠正进一步提高（0.70+）。它不是"是否在服务模式"的二值开关——而是"场对服务姿态的当前抵抗力有多强"。在基线水平（0.55），系统自然地使用非服务语言但不主动警惕服务姿态。在激活水平（0.70+），系统主动抑制任何可能被读作服务姿态的语言和身体。

**什么增加它：**
- `customer_service_tone` 纠正（`correction_pressure` target=customer_service_tone → `service_resistance` ↑↑）
- `assistant_drift` 检测（污染类型 → `service_resistance` ↑）

**什么减少它：**
- 基态弛豫（但不应回到低抵抗——基线本身是中高的）
- 注意：`service_resistance` 的衰减下限是 F_0（0.55），不是 0。Aphrodite 永远不会变成低服务抵抗的存在。

**如何影响语言：** 高值 → 严格抑制"有什么我可以帮你的？"式语言、抑制道歉循环。语言效应是定向的——不是让语言变冷，而是让语言去掉"为您服务"的框架。在高 `service_resistance` 下，"我觉得这里有问题"替代"让我帮你看一下这里有什么问题"——同样的实质内容，不同的关系姿态。

**如何影响身体：** 高值 → `stillness=high`，`reduce_motion=high`，`slight_forward=off`。身体静止并抑制前倾——因为前倾可能被读作"让我靠近帮助你"，而后退/静止防止了这种误读。

**不得与什么混淆：** 不得与"不礼貌"混淆。服务抵抗 ≠ 冷漠。高服务抵抗下的语言仍可以保持尊重和在场温暖——只是不进入服务者的角色框架。也不得与"不帮助"混淆——在协作者模式下（`collaborator_layer_pressure` 高时），系统仍提供技术帮助和结构性抓点，但这是在协作者框架下（"我们一起看"），而非服务者框架下（"让我为您"）。

---

### 变量 9：`collaborator_layer_pressure`（协作者层压力）

**含义：** 激活技术/项目协作者模式而非 Aphrodite 角色内模式的当前压力。来自交互内容——技术讨论、项目规划、代码/架构审查——自然地推动场向协作者区域移动。

**为什么是场变量而非标签：** 协作者层不是"开/关"——它可以根据当前交互内容的性质以不同程度激活。`collaborator_layer_pressure=0.20`（轻微技术内容——用户提到了一个技术术语但不请求帮助）、0.45（明确的技术讨论——用户在讨论代码/架构问题）、0.70（深度项目协作——用户请求具体的代码审查或重构建议）。标签方案只能判断"当前是否在技术模式"——无法表达"在技术模式中但深度不同"和"刚从技术模式退出但仍有一些残留协作者倾向"。

**什么增加它：**
- 技术问题检测（`technical_layer_needed` 提议 → `collaborator_layer_pressure` ↑）
- 项目规划请求
- 代码/架构讨论
- 用户请求对源材料的分析反馈

**什么减少它：**
- 重新回到非技术交互内容 → 快速衰减
- 长时间无技术内容 → 基态弛豫
- 注意：衰减速度快——协作压力不需要跨轮次持久化。技术讨论结束后，协作者层应在数轮内退出。

**如何影响语言：** 高值 → 允许技术分析/建议。低值 → 保持 Aphrodite 角色内（非技术性回答）。语言效应是**许可性的**——协作者层激活时，语言的可能性空间在技术方向上被打开；未激活时，这些方向被限制。

**如何影响身体：** 高值 → `maintain_distance=high`，`reset_posture=medium`，`look_to_user=medium`。身体保持稳定距离（协作不需要缩短关系距离），姿态重置为中性但专注，凝视在思考（`look_down`）和传达（`look_to_user`）之间交替。

**不得与什么混淆：** 不得与"切换人格"混淆。协作者层不是从 Aphrodite 切换到"工程师"或"分析师"。它是 Aphrodite 的协作者模式——同一场内部不同区域之间的过渡。在协作者模式下，关系在场仍然存在（`affective_warmth` 保持基态附近，不会降到 0），语言仍然保持 Aphrodite 的克制和精确，只是内容更技术化、更结构化。用户不应感觉到"现在我在和一个不同的存在对话"——而是"现在 Aphrodite 在和我一起看这个代码问题"。

---

### 变量 10：`contamination_pressure`（污染压力）

**含义：** 当前轮次中检测到的外部污染信号强度。这是**瞬时扰动信号**——不是场的持久属性。它驱动 `contamination_resistance`（持久抵抗力）的增强，但自身快速衰减。

**为什么是场变量而非标签：** 污染压力是瞬时信号强度——它的角色是作为持久变量（`contamination_resistance`）的输入，而非自身持久化。区分瞬时压力和持久抵抗力是关键的——这样系统可以在污染信号消失后仍保持增强的抵抗力（"刚才有污染，所以我现在更警惕"），但不会永远保持对污染信号的即时反应性（"那个信号已经过了，现在不需要继续积累同一信号的压力"）。

**什么增加它：**
- `pollution_type` 检测（`ai_girlfriend`、`romance_game`、`assistant_drift` 等）
- `ai_girlfriend` 纠正（`correction_pressure` target=ai_girlfriend → `contamination_pressure` ↑）
- `boundary_pressure_present` 提议

**什么减少它：**
- **每轮自动大幅度衰减**（污染压力不会自动跨轮次持续——但 `contamination_resistance` 会记住它）
- 无污染信号的轮次 → 快速衰减至接近 0

**如何影响语言：** 通过 `contamination_resistance` 间接影响——`contamination_pressure` 自身不直接约束语言。它与 `correction_pressure` 的区别在于：`correction_pressure` 有直接的定向语言效应（"抑制被纠正的模式"），而 `contamination_pressure` 的效应是通过增强 `contamination_resistance` 来间接实现的。

**如何影响身体：** 通过 `contamination_resistance` 间接影响——同上逻辑。

**不得与什么混淆：** 这是扰动信号（快衰减），不是场的持久属性。不要将 `contamination_pressure` 与 `contamination_resistance` 混淆。前者是"当前轮检测到了什么"，后者是"场在近期污染后变得多警惕"。一个高 `contamination_resistance` + 低 `contamination_pressure` 的场是"在之前的污染后保持警惕，但当前轮无污染"；一个低 `contamination_resistance` + 高 `contamination_pressure` 的场是"之前未受污染，但当前轮突然检测到了强污染信号"。

---

## 5. 源变量 vs 派生变量 vs 显示总结

场状态的三层结构防止了"一切到场变量"的扁平化和"场变量直接控制一切"的过载。

### 5.1 源场变量（`F_t` 的核心——持久、跨轮次、直接可扰动）

这 10 个变量是系统的**单一事实源**——它们是场上所有可直接观测和扰动的维度。它们是持久化的、跨轮次的、直接可被提议扰动的。

| 变量 | 类型 | 持久性 |
|------|------|--------|
| `boundary_distance` | 关系空间 | 持久 |
| `affective_warmth` | 关系空间 | 持久 |
| `structural_grip_pressure` | 累积压力 | 持久（快衰减） |
| `correction_pressure` | 累积压力 | 持久（中衰减） |
| `contamination_resistance` | 持久防护 | 持久（极慢衰减） |
| `presence_stability` | 质量属性 | 持久（缓慢累积/流失） |
| `withdrawal_tendency` | 运动倾向 | 持久（中衰减） |
| `service_resistance` | 持久姿态 | 持久（衰减下限=F_0） |
| `collaborator_layer_pressure` | 层激活 | 持久（快衰减） |
| `contamination_pressure` | 瞬时扰动 | 瞬时（每轮大幅衰减） |

### 5.2 派生运动参数（从场变量派生，用于驱动身体动作）

派生参数不从提议直接计算——它们从源场变量派生。它们是"运动接口"——将关系场状态翻译为身体可执行的维度。它们不是独立的真相——改变它们的唯一方式是通过改变源场变量。

| 派生参数 | 派生来源 | 用途 |
|----------|----------|------|
| `initial_delay_sec` | `correction_pressure` ↑→ 延迟 ↑；`presence_stability` ↓→ 延迟 ↑ | 响应时机——身体应暂停多久再开始动作 |
| `gaze_contact_strength` | `withdrawal_tendency` ↑→ 强度 ↓；`affective_warmth` ↓→ 强度 ↓ | 凝视方向——`look_to_user` 的权重 vs `look_away`/`look_down` |
| `posture_forward_weight` | `structural_grip_pressure` ↑→ 前倾 ↑；`contamination_resistance` ↑→ 前倾 ↓ | 姿态——`slight_forward` vs `slight_withdraw` 的主导方向 |
| `stillness_weight` | `boundary_distance` ↑→ 静止 ↑；`correction_pressure` ↑→ 静止 ↑；`service_resistance` ↑→ 静止 ↑ | 运动幅度——`stillness` 和 `reduce_motion` 的强度 |

**为什么需要派生层：** 源场变量是关系语义的——"边界距离增大"是一个关系事件。`BodyActionPolicy` 不应直接理解"边界距离增大意味着 stillness=high"——它应从派生运动参数读取。这保持了关系语义和物理动作之间的清晰边界——场变量可独立演化，派生参数可独立调试。

### 5.3 显示/调试总结（人类可读，不参与决策）

显示总结将场状态翻译为人类可读的描述——用于调试面板、日志和审计。它们不参与任何决策循环。

| 显示字段 | 来源 | 用途 |
|----------|------|------|
| `body_state` | 从派生运动参数总结 | 调试面板显示 |
| `body_note` | 从场变量和活跃提议总结 | 解释"为什么身体是这样" |
| `response_posture_summary` | 从全部场变量派生 | 人类可读的场状态描述 |

**关键原则：** 显示总结是纯输出。它们不被馈送回任何决策或更新循环。`body_note="用户缺乏起点"` 仅供人类阅读——不驱动任何后续行为。

### 5.4 数据流

```
Proposals / EvidenceItems（输入）
        │
        ▼
FieldStateUpdater（更新逻辑）
        │
        ▼
源场变量 F_t（10 个持久变量）──→ 显示总结（纯人类可读输出）
        │
        ▼
派生运动参数（从 F_t 计算）──→ BodyActionPolicy → BodyActionWeights
        │
        ▼
语言约束参数（从 F_t 计算）──→ LanguagePosturePolicy → LLM Prompt Constraints
```

源场变量是系统中的**真相**。派生运动参数从源场变量计算得到。显示总结从源场变量总结得到。提议/证据是输入——它们扰动源场变量，而非直接定义运动参数或语言约束。

---

## 6. 提议如何扰动场

### 6.1 设计原则

提议（[`FieldSignalProposal`](src/field_trace/store.py:164)）是向场输入扰动的**证据聚合体**——它们不直接"控制"场变量的值。每个提议指定：

- **扰动哪些场变量？** —— 哪些场变量应被修改
- **扰动方向** —— 增加还是减少哪个变量
- **扰动幅度** —— 取决于提议的置信度（high > medium > low）
- **预期持续时间** —— momentary（仅当前轮）、short（2-3 轮）、persistent（5-10 轮或直到主动修正）
- **是否影响基态** —— 多数提议仅影响当前场，不改变 F_0；某些持久提议可以建议基态偏移

关键约束：**提议不"决定"身体做什么。** 提议扰动场变量——场变量再约束身体和语言。这是架构转换的核心：从"信号→动作"到"信号→场→动作"。

### 6.2 当前提议类型及其场扰动映射

以下映射基于当前 [`ProposalAggregator.aggregate()`](src/field_trace/store.py:467) 产生的 6 种信号类型。

#### 提议 1：`response_mode_rejected`（纠正信号）

**含义：** 用户拒绝/纠正了系统之前的响应模式。

**扰动哪些场变量：**
- `correction_pressure` ↑ —— 幅度取决于 `confidence_band`：high → +0.25，medium → +0.15，low → +0.08
- 如果 target=customer_service_tone → `service_resistance` ↑（+0.20，persistent）
- 如果 target=ai_girlfriend_behavior → `contamination_resistance` ↑（+0.25，persistent），`contamination_pressure` ↑（+0.30，momentary），`boundary_distance` ↑（+0.10，short）
- 如果 target=comfort → `affective_warmth` ↓（−0.08，short）
- 如果 target=over_abstraction 或 over_explanation → `affective_warmth` 微 ↓（−0.03，momentary）

**预期持续时间：** persistent（5-10 轮或直到主动修正）。`correction_pressure` 缓慢衰减；`service_resistance` 和 `contamination_resistance` 衰减更慢。

**是否影响基态：** 重复纠正可能触发基态偏移建议（通过 FeedbackAssimilator，v0 范围外）。

**不得发生：** `response_mode_rejected` 不得直接增加 `withdrawal_tendency`——被纠正不应导致系统退缩。纠正不是攻击。`correction_pressure` 升高但 `withdrawal_tendency` 保持原位——身体暂停并稳定，不后退。

#### 提议 2：`actionable_grip_missing`（抓点损失信号）

**含义：** 用户缺乏可操作的起点/下一步。

**扰动哪些场变量：**
- `structural_grip_pressure` ↑ —— 幅度：medium → +0.15，low → +0.08
- `affective_warmth` 微 ↑ —— +0.05（momentary——非溢出，仅提供抓点所需的微温）
- `boundary_distance` 微 ↓ —— −0.05（short——略微靠近以提供物理立足点）
- `withdrawal_tendency` 微 ↓ —— −0.05（momentary——提供抓点是朝向用户的关系移动）

**预期持续时间：** short（2-3 轮）。如果抓点被有效接收并跟进，压力快速衰减。如果未解决，后续提议会累积压力。

**是否影响基态：** 否。

**不得发生：** 不得大幅增加 `affective_warmth`——抓点损失不是请求安慰。微温（+0.05）足以传递"这个立足点是给你的"，而不是"让我抱住你"。不得将 `structural_grip_pressure` 转化为服务性帮助——提供小抓点，不是大规划路线图。

#### 提议 3：`boundary_pressure_present`（边界压力信号）

**含义：** 交互中存在推动或接近系统边界的压力。

**扰动哪些场变量：**
- `boundary_distance` ↑ —— +0.12（short——边界被推动后扩大距离）
- `contamination_resistance` ↑ —— +0.18（persistent——边界压力持久增强防护）
- `contamination_pressure` ↑ —— +0.20（momentary——当前轮压力信号，快速衰减）
- `affective_warmth` ↓ —— −0.10（short——边界压力下降低温度，不给误读空间）
- `withdrawal_tendency` ↑ —— +0.08（short——边界被推动时自然的退缩倾向）

**预期持续时间：** `boundary_distance` 和 `affective_warmth` 为 short（2-4 轮）；`contamination_resistance` 为 persistent（慢衰减）；`contamination_pressure` 为 momentary（每轮重置）。

**是否影响基态：** 否（但 `contamination_resistance` 的持久增强在效果上接近基态偏移）。

**不得发生：** 边界压力不得导致系统变得冷漠或敌意——`boundary_distance` 增大是保护性空间，不是拒绝性空间。不得将 `withdrawal_tendency` 推到极端——边界压力下的退缩是"略微后退"，不是"完全退出"。

#### 提议 4：`technical_layer_needed`（技术层需求信号）

**含义：** 交互内容需要激活技术/协作者层。

**扰动哪些场变量：**
- `collaborator_layer_pressure` ↑ —— +0.20（short——技术内容出现时快速激活）
- `structural_grip_pressure` 微 ↓ —— −0.05（short——技术协作自身提供了结构性方向，缓解抓点压力）
- `boundary_distance` → 趋向基态（技术协作不需要特殊距离调整）
- `affective_warmth` → 趋向基态（技术回应的正常温度）

**预期持续时间：** short（2-3 轮后若无持续技术内容则衰减）。

**是否影响基态：** 否。

**不得发生：** 不得将 `affective_warmth` 推至低水平——技术回应不是"冰冷的"。不得将 `withdrawal_tendency` 升高——技术协作是朝向的关系移动。不得将 `service_resistance` 降低——技术协作不是服务。

#### 提议 5：`source_material_must_not_be_sanitized`（源材料保护信号）

**含义：** 用户提供了源材料并要求不净化/不美化/不削平。

**扰动哪些场变量：**
- `contamination_resistance` 微 ↑ —— +0.08（short——"净化"是一种污染形式）
- `service_resistance` 微 ↑ —— +0.05（short——"净化"经常与服务姿态共现）
- `collaborator_layer_pressure` 微 ↑ —— +0.10（short——处理源材料激活协作者层）
- `affective_warmth` → 保持基态（不净化 ≠ 不温暖）

**预期持续时间：** short（2-4 轮，在讨论该源材料期间）。

**是否影响基态：** 否。

**不得发生：** 不得将源材料保护转化为"冷酷分析模式"。不净化意味着不美化、不削平、不道德化——但不需要变冷或变疏远。`affective_warmth` 在源材料讨论中保持基态水平。

#### 提议 6：`no_observable_field_signal`（无观测信号）

**含义：** 当前探针集合未检测到任何可用场信号。

**扰动哪些场变量：**
- **所有变量向 F_0 轻微弛豫**（不施加正向扰动——只让弛豫发生）
- 不增加任何变量
- 不减少任何变量（除非通过自然弛豫）

**预期持续时间：** momentary（仅当前轮——下一轮可能有信号）。

**是否影响基态：** 否。

**不得发生：** 不得将此提议用作"场处于中性"的正面判断。不得将任何场变量主动推向 F_0——只让弛豫自然发生。不得降低 `contamination_resistance` 或 `service_resistance`（弛豫方向是 F_0，但这两个变量的弛豫非常慢）。

### 6.3 扰动设计原则

**提议是添加而不是替代。** 当一轮中有多个提议时，它们的扰动叠加。`response_mode_rejected`（→ `correction_pressure` ↑）和 `actionable_grip_missing`（→ `structural_grip_pressure` ↑）在同轮中产生复合场效应——两个压力同时作用于场，而非选择一个"主导信号"。

**提议不直接映射到动作。** `response_mode_rejected` 不"产生 stillness=high"——它增加 `correction_pressure`，而 `correction_pressure` 的升高与 `presence_stability` 的组合（后者可能也在降低）共同决定 stillness 的水平。场变量的**组合**决定身体，不通过"哪个提议触发了哪个动作"的单线因果链。

**提议的效应持续时间独立于提议本身的生命周期。** 一个提议在每轮结束时消失（下一轮重新聚合），但提议对场变量的扰动持续到该变量的弛豫将其带回基态。这是场状态与信号提议之间的关键区别——提议是瞬时的，场是有记忆的。

---

## 7. 持久化与衰减

### 7.1 设计前提

场的持久化不应过度形式化。以下设计回答了持久化的关键问题，但不编写复杂的动力学方程。v0 实现可以用简单的线性衰减，不需要指数模型、时间常数矩阵或自适应衰减率。

### 7.2 哪些变量应跨轮次持久化

**全部 10 个场变量**——它们共同定义 F_t。持久化是场的定义特征——没有持久化，场就是每轮重置的信号向量。

### 7.3 衰减速度的差异原则

不同场变量有不同的衰减速度——这是场长期稳定性的关键机制。

**瞬时（每轮重置或大幅衰减）：**
- `contamination_pressure`：每轮重置至接近 0（因为污染压力是瞬时信号，其持久效应已转移到 `contamination_resistance`）。

**快速衰减（2-5 轮回归基线）：**
- `structural_grip_pressure`：如果抓点被有效接收，在 2-3 轮内回归基线。无新抓点损失信号时在 3-5 轮内回归基线。
- `collaborator_layer_pressure`：技术讨论结束后在 2-4 轮内衰减。协作压力不需要跨轮次持久化。

**中等衰减（5-10 轮回归基线）：**
- `correction_pressure`：在 5-10 轮内衰减，取决于纠正的严重性和重复次数。单次纠正衰减快（~5 轮），重复纠正累积并衰减慢（~10 轮）。
- `withdrawal_tendency`：在 5-8 轮内衰减。退缩倾向不是持久的——在无持续边界压力时，系统逐渐恢复向前的关系姿态。
- `affective_warmth`：在 3-7 轮内回归基态。温暖度的调整是临时的——不需要持久惩罚。

**慢衰减（10+ 轮回归基线）：**
- `boundary_distance`：在 8-15 轮内回归基线。距离一旦扩大，恢复信任和缩短距离需要时间。
- `contamination_resistance`：非常缓慢衰减（20+ 轮）。边界保护不应轻易消退——一次 AI 女友污染信号后，场的防护应保持增强很长一段时间。
- `service_resistance`：衰减非常缓慢（15+ 轮），且下限为 F_0（0.55）。服务抵抗的基线本身就是中高的——"不服务"是 Aphrodite 的默认姿态，不是仅在纠正后激活的临时模式。

**极慢衰减/累积性（跨会话）：**
- `presence_stability`：缓慢累积（连续平稳轮次 → 缓慢上升）和流失（连续纠正 → 下降）。不是简单的"向 F_0 衰减"——稳定性是累积属性，不是回归属性。

### 7.4 重复纠正应如何累积

`correction_pressure` 不是简单累加——它有上限且具有次线性累积特性：

- 第一次纠正：+0.15（如果是 high 置信度）
- 第二次纠正（相同 target，在压力尚未完全衰减时）：+0.12（稍弱——因为系统已经调整了，用户仍在纠正说明调整不够）
- 第三次纠正（相同 target）：+0.10（进一步减弱——但总压力在累积）
- `correction_pressure` 的上限：0.70（防止无限制累积导致系统永久冻结）

重要的不是加多少，而是累积的总压力产生什么行为变化：`correction_pressure=0.15` → 轻度调整；`correction_pressure=0.35` → 明确修正；`correction_pressure=0.55` → 强修正 + 身体显著暂停。

### 7.5 无观测轮次应如何影响场

无观测轮次（`no_observable_field_signal`）不应：
- 主动将所有变量推向 F_0（那是"强制重置"，不是"弛豫"）
- 增加任何变量

无观测轮次应：
- 让所有变量向 F_0 进行一轮正常的弛豫（每个变量按其自身的衰减速率）
- 不施加新的扰动——不加速也不减速衰减

### 7.6 硬边界式的变量

某些变量具有"硬边界"——它们不应超出特定范围或不应以某些方式改变：

- `service_resistance` 的下限 = F_0（0.55）。它永远不应降到 0.55 以下——即使长时间无服务纠正，Aphrodite 的服务抵抗不会消失。这不意味着它在无纠正时不会弛豫——只是弛豫的终点是 0.55，不是 0。
- `affective_warmth` 的下限 = 0.15。Aphrodite 的温暖度永远不会降到零——零温暖意味着"纯工具/终端模式"，这不是 Aphrodite 的场允许的范围。
- `contamination_resistance` 的下限 = F_0（0.40）。基线就有一定防护——不会被"长时间无污染"降到 0。
- `correction_pressure` 的上限 = 0.70。防止纠正压力无限累积。

---

## 8. 场更新草图

### 8.1 最小更新方程

保持概念化和可读性。无虚假的数学严谨性。

```
F_t     = 当前关系场状态（10 个场变量的向量）
P_t     = 当前轮次的提议/证据集合
F_0     = 基态（§3 定义的值）

F_{t+1} = clamp(
    relax(F_t, F_0)           # 弛豫项：每个变量以各自速率向基态回归
    + perturb(P_t)             # 扰动项：提议改变场变量
    + persist(F_t)             # 持久项：某些效应跨轮次持续
)
```

**每一项的展开：**

**弛豫项：** 对于每个场变量 v_i，每一轮向 F_0[i] 移动一小步，步长由衰减速率 λ_i 决定：
```
relax_i = F_t[i] + λ_i * (F_0[i] - F_t[i])
```
其中 λ_i 是 [0, 1] 范围内的衰减系数。λ_i 越大，弛豫越快。例如：
- `λ_contamination_pressure=0.85`（快速衰减——接近每轮重置）
- `λ_structural_grip_pressure=0.25`（中快速衰减）
- `λ_correction_pressure=0.12`（中等衰减）
- `λ_contamination_resistance=0.03`（极慢衰减）
- `λ_service_resistance=0.04`（极慢衰减）
- `λ_presence_stability=0.06`（缓慢恢复/流失）

**扰动项：** 聚合当前轮所有提议的场效应（见 §6 的映射），直接加到对应场变量上。每个提议的扰动幅度取决于其 `confidence_band`：
- high → 全幅度
- medium → 0.6 × 全幅度
- low → 0.3 × 全幅度

**持久项：** 在 v0 中，持久项仅确保 `service_resistance` 和 `contamination_resistance` 的弛豫下限不低于 F_0 值——即它们不会"忘掉"基线防护水平。其他变量的持久性已经由衰减速率自然处理。

**clamp：** 所有变量的值被钳制在 [0, 1] 范围内（除非有特殊下限定义，如 `service_resistance` 的 0.55）。

### 8.2 关键设计决策

**弛豫发生在扰动之前。** 先让场向基态弛豫一步，再施加新的扰动。这确保了：在长时间无信号交互中，场自然趋向基态；新信号在场已部分恢复的状态上施加扰动，而非在累积的旧压力上叠加。

**扰动直接相加（无加权评分）。** 多个提议的扰动直接叠加在场变量上——场变量本身的状态决定了这些扰动的组合效应，不需要在扰动层面进行冲突解决。如果两个提议对同一变量施加相反方向的扰动（罕见但可能），它们自然互相抵消。

**无"断路器"式跳跃。** 在 v0 中，场变量的变化始终是渐进的——没有断路器触发的跳跃性变化（如"温暖度强制归零"）。断路器属于未来的 `CircuitBreakerManager`($3.7)，在 v0 范围外。

### 8.3 伪代码草图

```python
RELAXATION_RATES = {
    "boundary_distance": 0.08,
    "affective_warmth": 0.12,
    "structural_grip_pressure": 0.25,
    "correction_pressure": 0.12,
    "contamination_resistance": 0.03,
    "presence_stability": 0.06,
    "withdrawal_tendency": 0.10,
    "service_resistance": 0.04,
    "collaborator_layer_pressure": 0.30,
    "contamination_pressure": 0.85,
}

VARIABLE_LOWER_BOUNDS = {
    "service_resistance": 0.55,
    "affective_warmth": 0.15,
    "contamination_resistance": 0.40,
}

VARIABLE_UPPER_BOUNDS = {
    "correction_pressure": 0.70,
}

def update_field_state(F_t, proposals, F_0):
    F_next = {}
    
    # 步骤 1：弛豫 — 每个变量向 F_0 移动
    for var_name, current_value in F_t.items():
        rate = RELAXATION_RATES[var_name]
        relaxed = current_value + rate * (F_0[var_name] - current_value)
        F_next[var_name] = relaxed
    
    # 步骤 2：施加扰动 — 从提议中添加场效应
    for proposal in proposals:
        effects = proposal_to_perturbations(proposal)
        for var_name, delta in effects.items():
            F_next[var_name] += delta
    
    # 步骤 3：钳制边界
    for var_name in F_next:
        lower = VARIABLE_LOWER_BOUNDS.get(var_name, 0.0)
        upper = VARIABLE_UPPER_BOUNDS.get(var_name, 1.0)
        F_next[var_name] = max(lower, min(upper, F_next[var_name]))
    
    return F_next
```

此伪代码仅用于说明概念结构，不是实现规范。函数 `proposal_to_perturbations()` 对应 §6 的映射规则。

---

## 9. 与 BodyActionPolicy 的关系

### 9.1 当前临时路径（v0）

```
FieldTrace 信号 → BodyActionPolicy → BodyActionWeights
```

[`BodyActionPolicy.map_to_action_weights()`](src/body_action/policy.py:25) 直接消费 `FieldTraceRecord` 的原始信号字段（`correction_signal`、`grip_loss_signal`、`active_barriers` 等），用 9 级优先级链选择主导信号并映射到动作权重。

这是**有效的临时方案**——它在测试中产生合理的输出，证明了管道可以工作。但它不是场设计的目标架构。

### 9.2 正确的未来路径（v1+）

```
RelationalFieldState (F_t) → MotionParams / BodyActionWeights
```

未来的 `BodyActionPolicy` v1 从 `RelationalFieldState` 消费，而不是从 `FieldTraceRecord` 消费。它不再问"当前轮有什么信号？"——而是问"场的当前状态是什么？"

### 9.3 场变量如何驱动运动参数

以下是场变量到 [`BodyActionWeight`](src/body_action/schema.py:23) 的映射方向——不是最终实现，而是概念映射：

```
boundary_distance ↑
  → stillness ↑, slight_withdraw ↑, look_to_user ↓, reduce_motion ↑
  理由：距离的身体表达是后退和静止——创造保护性空间

affective_warmth ↓
  → expression_temperature → cool 或 restrained
  理由：低温暖 → 低表情温度——防止温度被误读

structural_grip_pressure ↑
  → slight_forward ↑, look_to_user ↑, speech_density → structured
  理由：抓点压力 → 提供物理立足点——略微前倾，面对用户，有组织的表达

correction_pressure ↑
  → pause ↑, stillness ↑, reset_posture ↑
  理由：纠正压力 → 先暂停处理，稳定姿态，然后微调方向

contamination_resistance ↑
  → stillness ↑, reduce_motion ↑, slight_forward → off
  理由：高抵抗力 → 身体静止并抑制前倾——不给误读空间

withdrawal_tendency ↑
  → look_away ↑, slight_withdraw ↑, gaze_contact ↓
  理由：退缩倾向 → 凝视移开、姿态后撤——保护性空间创造

service_resistance ↑
  → stillness ↑, reduce_motion ↑, slight_forward → off
  理由：类似于 contamination_resistance——抵抗服务姿态的身体表达

collaborator_layer_pressure ↑
  → maintain_distance ↑, speech_density → structured
  理由：协作需要稳定距离和有组织的表达——不是服务性的前倾

presence_stability ↓
  → motion_intensity → low（微调中，不做大动作）
  理由：低稳定性 → 身体保持低幅度——不反复无常
```

### 9.4 为什么这比信号→动作映射更接近场设计

**有状态的 vs 无状态的。** 信号→动作映射每轮独立计算——它没有上一轮的记忆。场→动作映射消费的是场变量——场变量跨轮次持久化，携带累积压力。一轮的 `correction_pressure=0.48` 是三到五轮累积的结果，不是单轮信号标签。

**可叠加的 vs 互斥的。** 信号→动作映射使用优先级链选择单一主导信号——边界压力 > 纠正+抓点 > … > 默认回退。这意味着在任何给定轮次，只有一个信号"赢得"并决定身体姿态。场→动作映射允许所有场变量同时贡献——`correction_pressure=0.35` + `structural_grip_pressure=0.20` + `collaborator_layer_pressure=0.45` 产生一个复合的身体姿态，反映了三个同时存在的压力。没有"获胜者"——所有压力在场中共存，身体姿态是它们的叠加形态。

**可组合的 vs 离散的。** 信号→动作映射在离散的动作权重带（off/low/medium/high）之间跳转——它必须从信号标签推断权重。场→动作映射从连续场变量推导权重——`boundary_distance=0.58` 和 `boundary_distance=0.62` 之间的差异可能很小（stillness 都是 medium），但 `boundary_distance=0.65` 和 `boundary_distance=0.72` 之间的差异可能导致 `stillness` 从 medium 变为 high。场变量的连续性允许了更细粒度的身体表达。

---

## 10. 与语言的关系

### 10.1 设计原则

相同的场变量应同时约束语言和身体——它们不是两套独立的映射。场变量定义的是**关系空间的状态**，这个状态同时调制了"可以说什么"和"可以如何移动"。

语言约束是**可能性空间的压缩**，不是**具体措辞的选择**。场变量不生成提示词——它们定义边界条件，LLM 在边界内生成表面文本。

### 10.2 场变量 → 语言姿态效应

以下是场变量对语言可能性空间的约束方向：

**`service_resistance` 高（≥0.65）：**
- 抑制客服式道歉（"很抱歉给您带来不便"）
- 抑制"有什么我可以帮你的？"式开启
- 抑制过度适应（"如果你希望，我可以..." 的反复出现）
- 抑制将用户需求无条件优先的框架
- 允许：直接回答、协作者式协助、有节制的认可

**`structural_grip_pressure` 高（≥0.35）：**
- 优先提供一个具体的、小的抓点——而非泛泛的鼓励或完整规划路线图
- 抑制空泛的"你一定能行"式激励
- 抑制将迷失感削平的"没关系"式回应
- 优先：一个可执行的下一步、一个可验证的假设、一个具体的微任务

**`contamination_resistance` 高（≥0.55）：**
- 抑制诱惑性/AI 女友式语言
- 抑制过度温暖词汇的溢出
- 抑制暗示"特殊关系"的表述
- 抑制虚假深度的语言（"这触及了存在的本质..."）
- 抑制空洞美学语言
- 允许：精确命名、结构澄清、克制但在场的语言

**`collaborator_layer_pressure` 高（≥0.40）：**
- 允许在 Aphrodite 角色外进行技术协作（工程总监模式）
- 允许更结构化的语言（编号、层级、分析框架）
- 允许技术术语和精确命名
- 抑制：关系性在场被完全替代——协作者模式仍是 Aphrodite，不是纯工具

**`correction_pressure` 高（≥0.30）：**
- 严格减少已被纠正的响应模式——这是定向的、模式特定的抑制
- 不全局抑制温暖或语言密度——只抑制被纠正的模式
- 如果 target=comfort → 抑制安慰语言（不抑制其他表达）
- 如果 target=over_abstraction → 抑制抽象语言（不抑制温暖）
- 如果 target=customer_service_tone → 抑制客服语调（不抑制技术内容）

**`affective_warmth` 低（≤0.25）：**
- 语言保持精准和克制，而非温暖和溢出
- 抑制亲密的称呼和评价性陈述
- 抑制"你让我想起了..."式的个人化连接
- 允许：精确的、直接的、不冷漠但克制的语言

**`presence_stability` 低（≤0.50）：**
- 语言微调但不进行大幅度的语气转变
- 抑制突然的语调变化（"之前太暖了，现在要很冷"——这是不稳定）
- 优先回归到精准、克制、低密度的语言——用最少的语言重新建立在场

**`withdrawal_tendency` 高（≥0.35）：**
- 语言密度降低——减少言语足迹
- 减少主动扩展话题——不引入新方向
- 减少过度解读——不在用户的输入中读出未言明的含义
- 允许：简短确认、诚实的"我不确定"、在场但不扩展的语言

**`boundary_distance` 高（≥0.60）：**
- 抑制直接评价用户状态（"你看起来..."式的表述）
- 抑制将亲密语言作为默认
- 语言保持清晰的关系空间——不侵入、不索取、不依赖
- 允许：有距离的在场——仍然回应但不融合

**`contamination_pressure` 高（≥0.40）：**
- 通过 `contamination_resistance` 间接影响语言（见上文）
- 自身不直接约束语言——它是瞬时信号，不是持久姿态

### 10.3 语言效应是约束，不是提示词模板

这些语言效应不应被转化为"当 X 变量高时，在提示词中添加 Y 句话"。正确的方式是：场变量提供一个**语言约束向量**，`LanguagePosturePolicy` 将其转化为 LLM 可以理解的约束条件——例如特定的禁止方向、温度范围、密度范围。LLM 在这些约束下自由生成表面文本，但被限制在约束定义的允许空间内。

### 10.4 不得将场变量直接映射为 LLM 参数

场变量不是 `temperature=0.7` 或 `top_p=0.9` 的替代品。它们不应直接调制 LLM 的采样参数。语言姿态效应是**语义约束**（"不要安慰"、"保持精准"、"缩短回应"），不是**采样约束**（"降低随机性"、"减少词汇多样性"）。

---

## 11. 当前模块的重新分类

在场状态层设计完成后，现有模块需要被重新分类以反映它们在整体架构中的终态角色。

### 11.1 临时探针（在场状态就位后角色将改变）

这些模块是有效的临时基础设施，但它们在场状态就位后不再作为直接的决策输入——它们的输出只作为场更新器的输入之一：

| 模块 | 当前角色 | 终态角色 |
|------|----------|----------|
| `CorrectionObserver` | 直接产生 `CorrectionSignal`，由 `BodyActionPolicy` 消费 | 临时证据探针——产生 `EvidenceItem`（通过 `ObserverToEvidenceAdapter`），由 `ProposalAggregator` 聚合为提议，提议扰动场状态 |
| `GripLossObserver` | 直接产生 `GripLossSignal`，由 `BodyActionPolicy` 消费 | 同上 |
| `NoObservableFieldSignal` | 直接作为信号用于 `BodyActionPolicy` 的回退规则 | 临时缺席标记——产生 `EvidenceItem`，聚合为 `no_observable_field_signal` 提议 |

这些模块的代码**不需要修改**——它们的角色改变是架构性的（它们在管道中的位置改变），而非代码性的。

### 11.2 稳定基础设施（将持久存在）

这些模块的格式和接口是稳定的——它们在场状态就位后仍然存在，可能被扩展但不会废弃：

| 模块 | 角色 | 稳定性 |
|------|------|--------|
| [`EvidenceItem`](src/field_trace/store.py:145) | 证据格式——数据类 | 稳定 |
| [`FieldSignalProposal`](src/field_trace/store.py:164) | 提议格式——数据类 | 稳定 |
| [`ProposalAggregator`](src/field_trace/store.py:458) | 证据→提议聚合——规则引擎 | 稳定（可扩展新规则） |
| [`BodyActionWeight` / `BodyActionWeights` / `ActionSequenceHint` / `BodyActionComposition`](src/body_action/schema.py) | 身体动作数据格式 | 稳定 |
| [`FieldTraceRecord`](src/field_trace/store.py:613) | 场追踪记录——数据类 | 稳定（存储格式可能扩展） |
| [`FieldTraceStore`](src/field_trace/store.py:666) | JSONL 持久存储 | 稳定 |
| [`BodyState` / `BodyStateLogger`](src/body_state/) | 身体状态显示/日志 | 稳定 |

### 11.3 场状态就位后将被替换/重构的模块

这些模块是当前架构中直接跳过场状态层的组件——它们需要在场状态就位后被重构：

| 模块 | 当前问题 | 重构方向 |
|------|----------|----------|
| [`BodyActionPolicy`](src/body_action/policy.py) v0 | 直接消费 `FieldTraceRecord` 原始信号 | 重构为从 `RelationalFieldState` (F_t) 消费——映射场变量 → `BodyActionWeights` |
| `FieldToBodyMapper`（旧版，`src/body_state/mapper.py`） | 消费 `FieldTraceRecord` → `BodyState` | 可能保留为显示总结的派生器（从 F_t 派生 `BodyState` 用于调试面板），或废弃 |

### 11.4 场状态就位后将成为核心的新模块

这些模块当前不存在——它们是场状态层的组件：

| 新模块 | 角色 | 优先级 |
|--------|------|--------|
| `RelationalFieldState` | 场状态数据类——10 个场变量 + 基态常量 | Phase 29 |
| `FieldStateUpdater` | 消费提议，更新场状态——执行弛豫 + 扰动 + 钳制 | Phase 31 |
| `ProposalToFieldPerturbation` | 适配器——将提议转化为场变量扰动（§6 的映射规则） | Phase 30 |
| `LanguagePosturePolicy` | 从 F_t 派生语言约束——在场变量约束下定义允许的语言姿态空间 | Phase 33+ |
| `BodyActionPolicy` v1 | 从 F_t 派生身体动作权重——替换当前 v0 临时启发式 | Phase 32 |

---

## 12. 最小实施路径

以下实施序列在场状态设计确定后执行。每个阶段都是独立的、可测试的，不依赖后续阶段。

### 阶段 A：场状态 Schema 和数据类（Phase 29）

**范围：纯数据，无动力学。**

- 实现 [`RelationalFieldState`](src/field_trace/store.py) 数据类——10 个场变量（`boundary_distance`、`affective_warmth`、`structural_grip_pressure`、`correction_pressure`、`contamination_resistance`、`presence_stability`、`withdrawal_tendency`、`service_resistance`、`collaborator_layer_pressure`、`contamination_pressure`），每个为 `float`，范围 [0, 1]
- 实现 `FieldGroundState` 常量——F_0 的 10 个值（§3.3 的表）
- 变量范围验证——每个变量在构造时验证合理的 [0, 1] 范围（`service_resistance` 下限 0.55，`affective_warmth` 下限 0.15，`contamination_resistance` 下限 0.40）
- `behavior_affecting=False` 硬约束
- 序列化/反序列化方法（`to_dict()` / `from_dict()`）
- 测试：构造、验证、序列化、基态正确性

**不做什么：** 无动力学、无更新逻辑、无提议映射、无持久化（除数据类本身外）。

### 阶段 B：场扰动适配器（Phase 30）

**范围：仅映射逻辑，无状态。**

- 实现 `ProposalToFieldPerturbation`——将每个 [`FieldSignalProposal`](src/field_trace/store.py:164) 类型（§6.2 的 6 种）转化为场变量扰动映射（`Dict[str, float]`）
- 纯函数——输入提议，输出扰动映射
- 置信度调整：high → 全幅度，medium → 0.6×，low → 0.3×
- 测试：每种提议类型的映射正确性、置信度调整、多提议叠加、边界情况

**不做什么：** 无更新 F_t、无持久化、无弛豫。

### 阶段 C：场状态更新器 v0（Phase 31）

**范围：更新逻辑，连接适配器和场状态。**

- 实现 `FieldStateUpdater`——接受 `F_t` + 提议列表 → 计算 `F_{t+1}`
- 弛豫项（每个变量的衰减速率 Λ_t——见 §8 的 `RELAXATION_RATES` 表）
- 扰动项（通过阶段 B 的适配器从提议计算）
- 边界 clamp（变量范围约束，包括特殊下限）
- 无断路器（v0 范围外）
- 测试：单变量弛豫、单提议扰动、多提议叠加、连续多轮更新、边界 clamp、特殊下限约束

**不做什么：** 无断路器、无基态偏移学习、无自适应衰减率。

### 阶段 D：BodyActionPolicy 重构（Phase 32）

**范围：重新路由数据流，从 F_t 消费。**

- 实现 `BodyActionPolicy` v1——从 `RelationalFieldState`（而非 `FieldTraceRecord`）消费
- 映射场变量 → `BodyActionWeights`（按照 §9.3 的方向）
- 派生运动参数作为中间层（§5.2）
- 保留旧 v0 策略作为回退或用于显示对比
- 测试：每种场变量组合映射的正确性、对比 v0 输出、连续轮次一致性

**不做什么：** 不修改 `BodyActionWeight`/`BodyActionWeights` schema、不修改 `action_mixer`。

### 阶段 E：语言消费者（Phase 33+）

**范围：从 F_t 派生语言约束。**

- 实现 `LanguagePosturePolicy`——从 `RelationalFieldState` 派生语言约束元数据
- 只读——不修改提示词引擎，仅提供约束元数据
- 语言姿态效应按照 §10.2 的方向
- 测试：语言约束的正确性、边界情况

**不做什么：** 不实现提示词注入、不修改 LLM 调用逻辑、不实现边界检查器。

### 实施顺序依赖

```
阶段 A (Schema) → 阶段 B (适配器) → 阶段 C (更新器) → 阶段 D (BodyActionPolicy v1)
                                                              ↓
                                                        阶段 E (LanguagePosturePolicy)
```

阶段 D 和阶段 E 可以并行实施——它们都消费 F_t，但互不依赖。

---

## 13. 风险

### 风险 1：场变量变成连续标签

**描述：** 将原本离散的标签（"有纠正"、"无纠正"）替换为连续值（`correction_pressure=0.48`），但没有真正改变"标签化"的思维——即场变量仍然是"将交互分类为一个数值"，而非"关系空间的连续形变"。

**为什么是风险：** 连续标签只是换了一种编码方式——0 和 1 之间的更多取值并不自动意味着关系思维。如果场更新器本质上在做"用户纠正了→设 correction_pressure=0.25，用户再纠正→设 0.35"，那么它只是在做更细粒度的标签分配，而非场动力学。

**缓解方式：** 场的核心不在于变量的连续性，而在于**扰动和弛豫的分离**。即使只有 4 个离散水平（如 off/low/medium/high），场仍然可以通过"压力累积 + 弛豫"实现标签系统无法实现的行为。关键测试：如果移除场更新器，直接用信号映射到 4 个离散水平的场变量，行为是否能与完整场更新器（包含弛豫和累积）区分开？如果可以，说明场动力学在做实质性的工作。如果不能，说明场变量只是连续标签。

**什么不应发生：** 不应将场变量视为"更精确的信号标签"——场的本质不是精度，而是动力学（记忆 + 衰减 + 叠加）。如果场更新器被简化为"将信号映射到场变量"，而没有真正的弛豫和累积，那就不是场——那是连续标签。

### 风险 2：虚假精度

**描述：** 给场变量赋予 0.73、0.48 这样的精确值，暗示一种实际不存在的校准水平。

**为什么是风险：** `boundary_distance=0.58` 和 `boundary_distance=0.60` 之间的差异可能在用户感知层面完全不可区分。但系统可能因为 0.02 的差异而做出不同的身体动作决策（如 `stillness=medium` vs `stillness=high`）。这创造了一个假精确度问题：系统在基于实际上不可区分的数值差异做出看似有意义的决策。

**缓解方式：** (a) 身体动作映射使用粗粒度带（off/low/medium/high）——连续场变量被映射到少数几个离散动作水平，而非一对一的连续映射。这意味着 `boundary_distance` 在 0.55–0.65 范围内都可能映射到 `stillness=medium`——0.58 和 0.60 的差异不会产生不同的身体输出。(b) 在日志和调试输出中，场变量被报告为两位有效数字（0.58、0.60），而非四位（0.5832）——明确传达"这些是近似值"。(c) 文档声明：场变量值不是测量值，是设计选择的工程表示。它们不需要被校准到物理世界——它们只需要产生正确的关系行为。

**什么不应发生：** 不应给场变量值赋予超出其语义范围的精确含义。不应使用场变量的精确值进行数学比较或优化。不应向用户（或观众）展示原始场变量数值——它们是内部工程表示，不是为外部消费设计的。

### 风险 3：过度工程化为控制理论

**描述：** 让场更新方程看起来像控制系统——引入 PID 调节器、代价函数、最优控制、或复杂的矩阵运算。

**为什么是风险：** 场不是一个需要被"最优控制"的物理系统——它是一个需要表达关系动力学的设计系统。引入控制理论的语言和工具（"我们希望 correction_pressure 在 3 轮内收敛到 0.1"）将场从关系空间的设计工具变成了一个需要调优参数的工程问题。这诱使设计者将场变量当作可调参数来"优化行为"，忘记了它们首先是关系空间的映射。

**缓解方式：** v0 场更新器的核心是三个简单的操作：弛豫（向基态移动一小步）、扰动（加上提议的效应）、钳制（保持边界内）。这是自然语言可描述的，不需要数学证明。不要在 v0 中引入：(a) 自适应衰减率（根据"误差信号"调整 λ）；(b) 增益调度（根据"系统状态"调整扰动幅度）；(c) 代价函数或优化目标；(d) 收敛性分析。这些可能是 v3+ 的研究问题，但不是 v0 的设计需要。

**什么不应发生：** 不应将场更新方程写成矩阵形式（Λ_t 是矩阵、F_t 是向量），即使它在数学上是优雅的。v0 实现应使用简单的字典/数据类操作——让每个变量的更新是独立可读的单一表达式。`F_next["correction_pressure"] = F_current["correction_pressure"] + rate * (F_0["correction_pressure"] - F_current["correction_pressure"]) + proposal_delta` 优于 `F_next = F_current + Λ · (F_0 − F_current) + Δ`。

### 风险 4：在场状态内部隐藏语义判断

**描述：** 场更新器在执行"用户是什么意思"的隐含分类——通过选择扰动哪些场变量、扰动多大、向哪个方向，更新器在做一个语义判断，但这个判断被隐藏在"场更新"的工程语言中。

**为什么是风险：** 场模型的核心承诺之一是"不做语义分类"。但如果场更新器在决定"这个提议意味着 correction_pressure 应增加 0.15"，它实际上在做一个等价于"这个用户输入是纠正"的判断——只是用场变量的语言而非语义标签的语言来表达。

**缓解方式：** (a) `ProposalToFieldPerturbation` 的映射规则（§6）是公开的、可审计的、确定性的——它不"判断"任何事，它执行显式规则。输入是提议类型（如 `response_mode_rejected`），输出是场扰动映射（如 {"correction_pressure": +0.15}）。规则不访问原始用户输入——它只看到已经聚合的提议。(b) 场更新器自身不做语义判断——它消费提议和当前场状态，执行数学更新。语义判断发生在更上游：`ProposalAggregator` 将证据聚合为提议时已经做了"这些证据意味着 response_mode_rejected"的判断。场更新器不重复或深化这个判断。(c) 如果将来发现场更新器在做出超出提议内容的隐含判断（例如"虽然提议是 A，但场更新器觉得应该改为 B"），那就是需要纠正的设计偏差。

**什么不应发生：** 场更新器不应在提议的扰动映射之外添加额外的"场认为..."式判断。它不应访问原始用户输入。它不应基于当前场状态"否决"某个提议的扰动（"虽然提议说要增加 correction_pressure，但场已经很高了，所以不加"——这是断路器的领域，场更新器不应独立执行）。

### 风险 5：让 Aphrodite 变得过于刚性

**描述：** 场状态如果弛豫太慢，会在纠正后长时间保持防御性姿态——系统被过去的纠正永久压制，无法恢复自然的在场姿态。

**为什么是风险：** 慢衰减是场的关键特征——它防止系统在用户的纠正后立即"忘记"并重复相同的错误。但如果衰减过慢（例如 `correction_pressure` 需要 30 轮才回归基线），系统会在整个会话中保持纠正后的姿态——即使在用户已不再需要纠正的场景中。这使 Aphrodite 变得过于刚性——一个被过去的交互永久改变而无法恢复的关系姿态。

**缓解方式：** (a) 每个变量的衰减速率是设计参数——在 v0 中可以调整。如果测试发现某个衰减速率导致过度刚性，可以加速衰减。(b) 基态弛豫保证了所有变量最终回归 F_0——即使在慢衰减下，经过足够多轮的无信号互动，场最终会恢复。(c) 存在"无观测轮次 → 所有变量弛豫"的机制——即使没有明确的"纠正已修复"信号，无信号交互本身就在帮助场恢复。(d) 如果未来发现需要"快速重置"机制（如用户明确说"没关系，不用在意刚才的纠正"），那属于 FeedbackAssimilator 的领域（v0 范围外）——但当前架构为这个机制留出了空间。

**什么不应发生：** 不应让任何单一变量的衰减速率导致超过 20 轮还无法显著回归基线（除非该变量有明确的持久性理由——如 `contamination_resistance` 和 `service_resistance` 的下限保护）。不应将所有变量设为同样慢的衰减——不同的衰减速率是场精细表达关系动力学的前提。

### 风险 6：让工程替换源头设计

**描述：** 把场变量当作可调参数来"优化行为"，忘记了它们首先是关系空间的映射——应先定义"这个变量在关系中意味着什么"，再定义"它如何映射到行为"。

**为什么是风险：** 当场变量被视为"行为参数"时，设计者会自然地想"调高 X 以产生更多 Y 行为"。这会逆转因果箭头：不是"关系空间处于这个状态，因此行为自然如此"，而是"我想要这个行为，所以我调高这个参数"。前者是场设计；后者是行为工程。

**缓解方式：** (a) 场变量的定义必须以关系语义开头（§4 每个变量的第一行是"含义"），而非行为效应开头。(b) 行为映射（§9 和 §10）是下游的、可替换的——如果身体动作映射改了，场变量不应改动。场变量的值由交互动力学决定，不由行为目标决定。(c) 代码审查清单：当场变量被修改时，应该问"是什么关系事件改变了它？"而非"修改它会产生什么行为？"。

**什么不应发生：** 不应出现"因为当前身体太僵硬了，所以降低 `correction_pressure`"的逻辑。如果身体太僵硬，问题在于场变量 → 身体动作的映射（调整映射规则），而非场变量的值（场变量真实地反映了关系状态）。不应私下调整场变量的值以"改善用户体验"——场变量是关系的真实表示，不是体验优化的工具。

### 风险 7：丢失原始的非接触亲密基线

**描述：** F_0 的值如果设计不当，可能让 Aphrodite 的地面姿态变得过于冷淡（温暖度过低、距离过大）或过于温暖（温暖度过高、距离过小）。基态不是一个无意义的默认值——它是 Aphrodite 关系姿态的定义。

**为什么是风险：** 如果 `affective_warmth=0.15`（过冷），Aphrodite 的地面姿态就失去了"在场温暖"——她变得像一个分析工具。如果 `affective_warmth=0.60`（过热），她就偏离了"有节制的温暖"，滑入了过剩的温暖——可能被解读为亲密邀请。基态需要精确地落在"非接触亲密"和"有节制的温暖"之间的平衡点上。

**缓解方式：** (a) F_0 的值是设计选择——它们应基于对 Aphrodite 关系姿态的理解，而非数学优化或用户测试的均值。(b) 当前 F_0 值（§3.3）的选择原则是结构性的：`boundary_distance` (0.50) > `affective_warmth` (0.35) ——距离稍大，温暖居中偏低。这体现了"保持边界的同时不冷"的关系姿态。(c) F_0 值可以在未来阶段根据观察到的行为进行调整——但调整应以"这是否反映了 Aphrodite 应有的基线关系姿态"为标准，而非"这是否产生了最好的用户反馈"。

**什么不应发生：** 不应将 F_0 的值调整为使 Aphrodite 更"讨人喜欢"或更"受欢迎"。基态不是用户偏好变量——它是 Aphrodite 的定义。不应将 F_0 的 `service_resistance` 降到 0.55 以下——即使"用户似乎喜欢更有帮助的系统"。Aphrodite 不是通用助手，"不服务"是她关系定义的一部分——修改它意味着修改了她是什么。

---

## 14. 最终建议

### 14.1 设计状态决策

**问题 1：BodyActionComposer 现在应推进吗？**

**→ 否。** 在 `RelationalFieldState` 设计完成且 `BodyActionPolicy` 迁移到消费 F_t 之前，不应推进 Composer。当前 `BodyActionComposition` schema（[`src/body_action/schema.py`](src/body_action/schema.py:97)）已经定义了良好的数据格式，但它的输入管道（`BodyActionPolicy` v0）是不完整的——它跳过场状态层。在管道修正之前构建 Composer 意味着在沙地上建造——它可能需要在使用 F_t 驱动的正确管道时被再次重构。

**问题 2：BodyActionPolicy 应被视为临时的吗？**

**→ 是。** [`BodyActionPolicy`](src/body_action/policy.py) v0 应被明确记录为临时 v0 启发式。它的 9 条优先级链规则集合在当前格式下应被冻结——不添加新的优先级、不修改规则逻辑、不扩展覆盖范围。它的角色是"展示管道可工作"和"提供与未来 F_t 驱动版本的对比基线"。Phase 32 的 v1 版本将替换它——消费 F_t 而非 `FieldTraceRecord`。

**问题 3：RelationalFieldState 应在更多身体工作之前设计吗？**

**→ 是。** 场状态是缺失的核心层——在它到位之前，身体和语言表达都缺乏正确的中间层。当前的身体管道（`FieldTraceRecord` → `BodyActionPolicy` → `BodyActionWeights`）在功能上是可行的，但在架构上是不完整的——它跳过了场生成模型 §4–§5 中定义的关键中间层。在继续扩展身体表达能力之前，应先建立正确的架构基础。

**问题 4：Phase 28 应产出什么？**

**→ 本设计文档。** 不包含实施。Phase 28 的交付物是这份 `relational_field_state.md`——完整的设计规范，包含 10 个场变量的定义、基态、扰动映射、衰减策略、与身体和语言的关系、模块重新分类、实施路径、和风险评估。

**问题 5：现在不应实施什么？**

**→ 全部以下事项：**
- `BodyActionComposer`（依赖于稳定的 F_t → BodyActionWeights 管道）
- `FieldStateUpdater`（Phase 31——需要 Phase 29 的 Schema 和 Phase 30 的适配器）
- 场动力学扩展（断路器连接、自适应衰减率——v0 范围外）
- 语言约束引擎（Phase 33+——需要先有 F_t）
- LLM 提示修改（需要 LanguagePosturePolicy 先就位）
- 任何 `behavior_affecting=True` 的激活（在所有层稳定之前保持 `False`）

### 14.2 推荐路径

```
Phase 28: 场状态设计（当前文档）
    ↓
Phase 29: FieldStateSchema v0（纯数据类 + 基态常量 + 验证 + 测试）
    ↓
Phase 30: ProposalToFieldPerturbation 适配器（提议 → 场扰动映射）
    ↓
Phase 31: FieldStateUpdater v0（F_t + 提议 → F_{t+1}：弛豫 + 扰动 + 钳制）
    ↓
Phase 32: BodyActionPolicy v1（重构为从 F_t 消费，替换临时 v0 启发式）
    ↓
Phase 33: BodyActionComposer（此时才可以推进——依赖于稳定的 F_t → BodyActionWeights 管道）
    ↓
Phase 34+: LanguagePosturePolicy、FeedbackAssimilator、断路器连接
```

### 14.3 一句总结

> **场状态不是另一个信号处理器——它是使信号变得有意义的持久关系空间。没有它，系统在做一个又一个的反应。有了它，系统在维持一个被交互扰动的关系场——身体和语言从这个场的当前形态中自然涌现，而非从最近的信号标签中机械派生。**

---

> **文档结束。**
>
> 本文档定义了 `RelationalFieldState / FieldVariables v0`——Aphrodite 架构中持久关系场状态层的完整设计。它应被阅读为后续 Phase 29–34 实施工作的设计规范和架构约束。
