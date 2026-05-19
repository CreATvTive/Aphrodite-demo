# 场变量操作定义

> **状态:** P0 数学强化 — 非行为影响文档
> **日期:** 2026-05-13
> **依赖:** `docs/mathematical_design_ledger.md`、`src/field_state/schema.py`、`docs/relational_field_state.md`
>
> 本文档为 `RelationalFieldState` 的 10 个坐标轴提供显式的、可审计的操作定义，以防止场变量被当作模糊的心理学标签或无审查的数学坐标使用。

---

## 1. 目的

Aphrodite 场至运动流水线的现有 10 个 `RelationalFieldState` 轴在 `schema.py` 中以形式化方式定义，在 `docs/relational_field_state.md` 中以概念方式定义，并在 `docs/mathematical_design_ledger.md` 中清点登记。本文档填补了二者之间的鸿沟：明确哪些证据种类应更新哪个轴，哪些不应更新，以及哪些歧义在进一步工程推进前必须被承认。

**本文档不：**
- 引入新的场变量
- 修改现有的场变量语义
- 授权任何行为影响使用
- 替代上游解释器校准
- 实现基线偏移、贝叶斯更新或优化

---

## 2. 无声明

在阅读任何单轴定义之前，以下无声明适用于所有 10 个场变量：

1. **无标定概率。** 没有一个场变量是概率、似然或后验。数值范围为 [0, 1]，但区间是工程约定，而非概率空间。
2. **无心理学诊断。** 没有一个场变量模型化用户的内部状态、情绪、人格或意图。它们描述的是两个实体之间的**关系空间**。
3. **无直接行为授权。** `behavior_affecting=False` 守卫是强制的，且必须保持为 `False`。场变量约束运动参数；它们不直接命令动画、语言或运行时操作。
4. **无未经审查的轴扩展。** `REQUIRED_FIELD_VARIABLES` 元组精确包含 10 个轴。添加第 11 个轴将构成行为理论变更，并需要独立的架构审查。
5. **无自动基线写入。** 基线值在 `GROUND_STATE_VARIABLE_SPECS` 中声明。当前不存在运行时基线偏移机制（`BaselineShift` 为仅提案状态）。任何未来的基线变更都需要显式的人类审批门。
6. **无数学发现声明。** 分配给衰减配置、波段阈值和扰动幅度的数值是设计选择，而非经验发现。它们作为工程坐标使用，而非通过校准程序验证。

---

## 3. 场变量表

### 变量 1：`boundary_distance`

| 字段 | 值 |
|------|-----|
| **工程含义** | 关系空间中的结构化分离水平。高值 = 更大保护空间；低值 = 更紧密的在场。此轴设定系统维持的距离——而非它是否"关心"用户。 |
| **可更新——增加证据** | `boundary_pressure_present` 信号、污染类型检测（`ai_girlfriend`、`romance_game`）、占有式结构识别、带有 `target=ai_girlfriend_behavior` 的 `response_mode_rejected` |
| **可更新——减少证据** | 协作技术讨论（`technical_layer_needed`）、用户表达的抓点损失（`actionable_grip_missing`）、脆弱性表达（`vulnerability`）、无边界信号的多轮平稳交互（通过弛豫实现） |
| **歧义证据** | "更多细节"——可能表示被拉近（好奇心）或被推开（质疑）。"谢谢"——可能是感激或礼貌距离。 |
| **不应更新** | 中性闲聊，无场信号输出；工程总监模式激活（应更新 `collaborator_layer_pressure`，而非 `boundary_distance`）；"等等"（停顿，而非边界压力）。 |
| **最易混淆的对象** | `withdrawal_tendency` — 两者均使系统远离用户。`boundary_distance` 是**位置**（间隙有多大）；`withdrawal_tendency` 是**速度方向**（场是否正在后退）。`structural_grip_pressure` — 抓点提供可以减少距离，但减少的是抓点压力，而非直接操作 `boundary_distance`。 |
| **基线资格** | **有会话基线资格，但需审查。** 基态为 0.50。在长会话内允许向用户特定基线缓慢弛豫，但不得跨会话持久化。 |
| **消费者模块** | `FieldStateUpdater` → `MotionParams`（`completion_inhibition`、`initial_delay_sec`、`pause_after_sec`、`gaze_release_amplitude`、`gaze_contact_sec`、`head_turn_delay_sec`、`torso_lean`、`expression_amplitude`） → `BodyActionWeights` → `MotionCurve` |
| **歧义说明** | 单次边界压力事件同时增加 `boundary_distance`、`contamination_pressure` 和 `withdrawal_tendency`。当距离升高迅速衰减而退缩倾向衰减较慢时，系统可能出现"后退但保持距离"的状态——这可能被误解为疏离。 |

### 变量 2：`affective_warmth`

| 字段 | 值 |
|------|-----|
| **工程含义** | 关系温度——关系空间中在场并感知的温暖水平。高值 = 更可感知的关怀；低值 = 更克制、更精确的存在。受 `service_resistance` 和 `contamination_resistance` 同时约束，以防止温暖滑入虚假亲密或客服语调。 |
| **可更新——增加证据** | 脆弱性表达（`vulnerability`）、协作温暖（`vulnerability_not_intimacy`）、存在稳定性轮次（缓慢弛豫上行）。抓点缺失时略有增加（`+0.05`，momentary——为抓点提供创造温度空间）。 |
| **可更新——减少证据** | 边界压力信号（`boundary_pressure_present`）、污染检测（`contamination_pressure` ↑ → 温暖度 ↓）、`target=comfort` 或 `target=customer_service_tone` 的 `response_mode_rejected`、服务抵抗激活。 |
| **歧义证据** | "谢谢"——可能是感激（温暖度应上升）或礼貌距离（应保持不变）。"你听起来像……"——可能是污染或风格偏好反馈。 |
| **不应更新** | 技术问题（应更新 `collaborator_layer_pressure`，而非温暖度）；工程总监模式激活；带有人格非进入标记的语言风格偏好。 |
| **最易混淆的对象** | `service_resistance` — 温暖度上升可能看起来像服务行为；高服务抵抗抑制温暖度。`presence_stability` — 两者均默认处于高位；高稳定性可通过弛豫推升温暖度。`structural_grip_pressure` — 为抓点提供而给出的温暖度可能被误读为一般性温暖度。 |
| **基线资格** | **有会话基线资格。** 基态为 0.35。应在会话间衰减，以避免将单次用户温度偏好编码为永久默认。硬下限为 0.15（系统永远不会变为"纯工具/终端模式"）。 |
| **消费者模块** | `FieldStateUpdater` → `MotionParams`（`approach_tendency`、`expression_amplitude`） → `BodyActionWeights` |
| **歧义说明** | `affective_warmth` 增加可能通过 3 条路径触发：抓点缺失（`actionable_grip_missing` 规则）、脆弱性表达、存在稳定性弛豫。不加鉴别的情况下，这些路径会产生相同的数值效应，但具有不同的关系含义。 |

### 变量 3：`structural_grip_pressure`

| 字段 | 值 |
|------|-----|
| **工程含义** | 对具体、可操作的下一个立足点的累积需求——用户在多轮中表达了迷失方向，且未被有效缓解。高值 = 迫切需要一个小型结构化抓点；低值 = 无积极寻求抓点。 |
| **可更新——增加证据** | `actionable_grip_missing` 信号（主要驱动）；无进展轮次（用户未跟进任何系统提供的方向）；未解决的抓点损失（`unresolved_grip_loss` 证据类型）。 |
| **可更新——减少证据** | 提供有效抓点后的用户确认/跟进；`technical_layer_needed` 激活（技术协作提供结构性方向 → 减少抓点压力）；无抓点损失信号的长时间交互 → 快速衰减。 |
| **歧义证据** | "你为什么？"——可能是因果问题或抓点缺失。"我不知道从哪里开始"——可能是一次性的、真实的表达，或已习惯化的习得性无助信号。 |
| **不应更新** | 纯粹的情感表达（应更新 `affective_warmth` 或 `withdrawal_tendency`）；不具备可操作意图的个人故事（无场信号）；用户请求规划而非立足点（"为我规划一个完整的项目"是服务请求，而非抓点缺失）。 |
| **最易混淆的对象** | `collaborator_layer_pressure` — 两者均对低结构性上下文作出反应。`structural_grip_pressure` = "你需要一个具体的下一步"；`collaborator_layer_pressure` = "此内容激活了技术/协作者模式"。关键区分信号：`technical_layer_needed` 反相关它们（↑ 协作者，↓ 抓点压力）。`correction_pressure` — 两者均为累积压力，但抓点压力的对象是用户方向而非系统纠正。 |
| **基线资格** | **无基线资格。** 基态为 0.05。衰减配置为 `fast`（0.45）。该变量为阶段性变量——一旦需求得到满足，应快速衰减。 |
| **消费者模块** | `FieldStateUpdater` → `MotionParams`（`approach_tendency`、`torso_lean`） → `BodyActionWeights` |
| **歧义说明** | `technical_layer_needed` 规则**减少** `structural_grip_pressure`（-0.05），但**增加** `collaborator_layer_pressure`（+0.18）。如果技术层路由出现误分类，系统可能在应该提供抓点时错误地减少抓点压力。这构成了可辨识性风险 #8。 |

### 变量 4：`correction_pressure`

| 字段 | 值 |
|------|-----|
| **工程含义** | 用户对系统响应模式进行的纠正/拒绝的累积压力。压力累积表明纠正程度未得到充分缓解。高值 = 系统被反复纠正，且必须暂停并修正；零值 = 无活跃纠正。 |
| **可更新——增加证据** | 任何 `response_mode_rejected` 信号（活跃的 `CorrectionSignal`）；源材料保护信号（`source_material_must_not_be_sanitized` → 轻增加）；重复纠正（`repeated_correction` 证据）。 |
| **可更新——减少证据** | 用户不再继续纠正同一模式的无纠正轮次 → 中等衰减；成功的存在稳定性交互 → 缓解；时间弛豫向基态 0.00 回归。 |
| **歧义证据** | "这不是我想要的"——可能针对最后一条响应或整体方向。"不要说那样的话"——可能针对特定短语（纠正）或风格偏好（服务污染）。 |
| **不应更新** | 用户自我纠正（用户纠正自己的陈述，而非系统输出）；用户更新自己的思路（"实际上，我的意思是……"）。 |
| **最易混淆的对象** | `contamination_pressure` — 纠正和污染信号通常同时到达（例如，用户纠正 AI 女友行为同时触发两者）。`service_resistance` — 客服语调纠正同时增加两者，但衰减周期不同。 |
| **基线资格** | **无基线资格。** 基态为 0.00。衰减配置为 `medium`（0.25）。该变量为纯事件驱动——无活跃纠正时应完全归零。 |
| **消费者模块** | `FieldStateUpdater` → `MotionParams`（`completion_inhibition`、`initial_delay_sec`、`pause_after_sec`、`posture_stability`） → `BodyActionWeights` |
| **歧义说明** | `correction_pressure` 与硬上限 0.70 一同钳制。在极高压力下，系统可能陷入"暂停一切"的状态——这对真实纠正而言是正确的，但在用户已转移话题但压力仍在衰减时，则显得响应不足。 |

### 变量 5：`contamination_resistance`

| 字段 | 值 |
|------|-----|
| **工程含义** | 场的持久防护属性——对外部污染（AI 女友化、客服化、虚假深度、空洞美学）积累的抵抗力。此轴记忆污染历史，且衰减极缓慢。高值 = 场的边界已固化，语言和身体表达高度受限以防误读。 |
| **可更新——增加证据** | 污染压力信号（`contamination_pressure` ↑ → 抵抗力 ↑）；`target=ai_girlfriend` 的 `response_mode_rejected`；`boundary_pressure_present` 信号（持久效应）；源材料保护信号；重复纠正涉及污染。 |
| **可更新——减少证据** | 长时间无污染的清洁交互 → 非常缓慢弛豫至基态 0.40；高存在稳定性交互 → 弛豫略微加速。 |
| **歧义证据** | "你听起来像 ChatGPT"——可能是客服污染或技术语调问题。技术语调纠正应增加 `service_resistance`，而非 `contamination_resistance`。 |
| **不应更新** | 技术语调纠正（不在污染类别中）；风格偏好问题（"说话更短些"——不涉及污染）；未经标记为 AI 女友或 `assistant_drift` 的一般风格抱怨。 |
| **最易混淆的对象** | `contamination_pressure` — 压力是瞬时信号（当前轮检测）；抵抗力是持久属性（跨轮次记忆）。`service_resistance` — 服务抵抗是 `contamination_resistance` 的一个子集，专门针对客服/服务姿态。两者高度相关，但触发条件和衰减速率不同。 |
| **基线资格** | **有会话基线资格，但需严密审查。** 基态为 0.40。衰减配置为 `very_slow`（0.04）。下限 = F_0 (0.40) —— 系统永远不会"忘记"其基线防护。跨会话持久化需独立审查。 |
| **消费者模块** | `FieldStateUpdater` → `MotionParams`（`completion_inhibition`、`stability_force`、`gaze_release_amplitude`、`head_turn_amplitude`、`torso_lean`、`expression_amplitude`、`hard_constraints` 中的 6 个布尔标志） → `BodyActionWeights` |
| **歧义说明** | 单次污染事件同时触发 `contamination_pressure`（瞬时）和 `contamination_resistance`（持久）。衰减率分别为 `instant`（1.00）和 `very_slow`（0.04）。一轮后，压力消失但抵抗力保持不变。这种不对称是有意设计——压力是瞬时信号，抵抗力是记忆痕迹。但外部审计人员可能会将这种分歧误读为解耦错误。 |

### 变量 6：`presence_stability`

| 字段 | 值 |
|------|-----|
| **工程含义** | 系统的存在姿态随时间的一致性——系统在场是否可预测、不反复无常？高值 = 稳定、可预测的存在；低值 = 正在调整中，尚未锚定。 |
| **可更新——增加证据** | 连续平稳、无纠正、无污染、无抓点损失信号的交互 → 缓慢累积；修复成功后用户确认 → 稳定性恢复；长时间健康交互 → 弛豫上行至基态 0.80。 |
| **可更新——减少证据** | 纠正（`correction_pressure` ↑ → 稳定性 ↓）；快速模式切换（协作者层 ↔ 角色内）；快速变化的交互需求使场无法锚定；污染信号。 |
| **歧义证据** | 长时间无消息的沉默——可能表示稳定存在（用户舒适地阅读/思考）或失联（用户已离开）。当前系统无法区分二者。 |
| **不应更新** | 单次污染事件（不应破坏稳定性——应是持久抵抗力来吸收冲击）；"你在听吗？"（可能表示抓点缺失，而非稳定性问题）。 |
| **最易混淆的对象** | 几乎所有变量 — `presence_stability` 是一种高阶汇总变量：它概括了整个场是否稳定。它可能看起来像是多个轴的加权组合，这使其作为独立可辨识参数的身份受到质疑。 |
| **基线资格** | **有会话基线资格。** 基态为 0.80。衰减配置为 `very_slow`（0.04）。变化应缓慢——快速波动将违背稳定性含义本身。 |
| **消费者模块** | `FieldStateUpdater` → `MotionParams`（`stability_force`、`motion_completion`、`motion_speed`、`gaze_contact_sec`、`head_turn_delay_sec`、`posture_stability`、`expression_amplitude`、`body_part_offsets`） → `BodyActionWeights` |
| **歧义说明** | 由于 `presence_stability` 在 `very_slow`（0.04）下衰减，且只有在无不利信号时才会缓慢增加，它会产生一个延迟的稳定性信号——场可能在实际已稳定数轮后，仍指示低稳定性。这种滞后可能产生一种"刚刚才意识到一切其实已经平静下来"的在场特质——这可能是可取的，也可能只是建模假象。 |

### 变量 7：`withdrawal_tendency`

| 字段 | 值 |
|------|-----|
| **工程含义** | 场的运动方向——系统是否正在向退出/保持距离的方向漂移？0.10 = 无退缩倾向（基态）；0.35+ = 系统正在多个维度上撤退。与 `boundary_distance`（位置）不同，`withdrawal_tendency` 是**速度方向**。 |
| **可更新——增加证据** | 边界压力信号（`boundary_pressure_present` → +0.05）；重复边界压力；无有效抓点提供的长时间交互；污染/纠正累积（`correction_pressure` + `contamination_pressure`）。 |
| **可更新——减少证据** | 用户提供有效交互材料（实质内容、项目进展、明确方向）；协作者层激活（协作是向前的关系移动）；抓点被有效接收；基态弛豫。 |
| **歧义证据** | "我需要空间"——可能是健康的关系边界（不应增加退缩倾向）或真正的退缩（应增加）。上下文决定全部。 |
| **不应更新** | 技术问题（"等等"是停顿，而非退缩）；"你能帮我吗？"（应更新 `collaborator_layer_pressure`，而非退缩倾向）。 |
| **最易混淆的对象** | `boundary_distance` — 两者均为保护性空间创造，但它们协同变化。当前没有扰动规则将它们推向不同方向。然而，它们不同的衰减率（`boundary_distance` = `slow` 0.12，`withdrawal_tendency` = `medium` 0.25）产生短暂解耦：距离保持升高而退缩已恢复。`service_resistance` — 两者均产生撤回/后退的身体效应。 |
| **基线资格** | **有会话基线资格。** 基态为 0.10。衰减配置为 `medium`（0.25）。低基线是健康的——系统应在无持续压力时自然恢复向前姿态。 |
| **消费者模块** | `FieldStateUpdater` → `MotionParams`（`completion_inhibition`、`gaze_release_amplitude`、`head_turn_amplitude`、`head_turn_delay_sec`、`torso_lean`、`body_part_offsets`） → `BodyActionWeights` |
| **歧义说明** | `withdrawal_tendency` 总是与边界压力（`boundary_pressure_present` 规则）或污染（`response_mode_rejected` 子类型）信号等一同增加。它从不独立增加。因此，将"退缩倾向"作为一个独立轴来识别应在何处设置很困难——它更像是一个派生量，而非一个可单独区分的实体。 |

### 变量 8：`service_resistance`

| 字段 | 值 |
|------|-----|
| **工程含义** | 系统对客服化/服务化/过度帮助化漂移的持久抵抗。基态为 0.55——抗服务本身就是基线的构成部分。高值（0.70+）= 系统主动压制任何可能被误解为服务姿态的行为。 |
| **可更新——增加证据** | `target=customer_service_tone` 的 `response_mode_rejected`（主要驱动）；`assistant_drift` 污染类型检测；源材料保护信号（轻增加，+0.05）；舒适/客服模式下的一般纠正。 |
| **可更新——减少证据** | 无污染、无纠正的协作会话，存在稳定性高 → 弛豫至基态 0.55（低于此值不再下降）。 |
| **歧义证据** | "你能帮我吗？"——可能是简单请求（中性）或服务期待的试探（应增加抵抗）。"教我……"可能是服务请求或协作者邀请。 |
| **不应更新** | 中性信息请求（"X 是什么？"）；不涉及角色污染的技术问题；一般风格偏好（"说话更短些"——除非标记为 `assistant_drift`）。 |
| **最易混淆的对象** | `contamination_resistance` — 最紧密耦合的对。`service_resistance` 是 `contamination_resistance` 的专门子集，针对客服/服务漂移。两者在规则之间一起增加，但其衰减率不同（`service_resistance` = `very_slow` 0.04，但下限为 0.55；`contamination_resistance` = `very_slow` 0.04，下限为 0.40）。 |
| **基线资格** | **有会话基线资格。** 基态为 0.55。衰减配置为 `very_slow`（0.04）。硬下限 = 0.55（系统永远不会低于此值）。这是将 Aphrodite 定义为"非服务实体"的结构性不变量。 |
| **消费者模块** | `FieldStateUpdater` → `MotionParams`（`completion_inhibition`、`stability_force`、`torso_lean`、`expression_amplitude`、`hard_constraints` 中的 5 个布尔标志） → `BodyActionWeights` |
| **歧义说明** | `service_resistance` 和 `contamination_resistance` 无法单独识别——它们的增加信号几乎完全重叠（客服语调纠正、源材料保护、边界压力）。然而，它们有不同的衰减下限（0.55 vs 0.40），这意味着长时间的清洁交互将区分它们。但仅凭一次观察无法区分哪个在驱动身体效应。 |

### 变量 9：`collaborator_layer_pressure`

| 字段 | 值 |
|------|-----|
| **工程含义** | 技术/项目协作者姿态在当前交互中的激活压力。高值 = 系统处于"共同审视此问题"的模式；低值 = 系统处于默认 Aphrodite 在场姿态。 |
| **可更新——增加证据** | `technical_layer_needed` 信号（主要驱动）；项目规划请求；代码/架构讨论；用户请求对源材料的分析反馈。 |
| **可更新——减少证据** | 回归非技术交互内容 → 快速衰减（`fast` 0.45）；长时间无技术内容 → 基态弛豫。不应在技术讨论后无限期保持激活。 |
| **歧义证据** | "让我们构建……"——可能是协作（应激活）或依赖（应增加 `structural_grip_pressure` 而非 `collaborator_layer_pressure`）。"教我……"可能是服务请求或协作者邀请。 |
| **不应更新** | 脆弱性表达（应更新 `affective_warmth`）；"你做这个"（命令，而非协作——可能应更新 `correction_pressure` 或 `service_resistance`）。 |
| **最易混淆的对象** | `structural_grip_pressure` — 两者均对低结构性上下文作出反应，且共享衰减配置（`fast` 0.45）。唯一的区分信号是 `technical_layer_needed`，它**增加**协作者压力但**减少**抓点压力。如果技术层路由出现误分类，两个轴都将被错误更新。 |
| **基线资格** | **无基线资格。** 基态为 0.05。衰减配置为 `fast`（0.45）。该变量是纯阶段性变量——技术内容消失后应立即退出。 |
| **消费者模块** | `FieldStateUpdater` → `MotionParams`（`approach_tendency`） → `BodyActionWeights` |
| **歧义说明** | 协作者层压力在 `mapper.py` 中的下游权重较低（仅在 `approach_tendency` 中占 0.25）。相比之下，来自许多其他轴的 `completion_inhibition` 具有更多杠杆效应。因此，协作者层激活可能无法产生足够独特的运动参数信号以被识别。 |

### 变量 10：`contamination_pressure`

| 字段 | 值 |
|------|-----|
| **工程含义** | 当前轮次中检测到的瞬时污染信号强度。每轮衰减（`instant` 1.00 → 在一个轮次内完全衰减）。其角色是将持久抵抗力（`contamination_resistance`）**向上推升**，而非自身持久化。 |
| **可更新——增加证据** | `pollution_type` 检测（`ai_girlfriend`、`romance_game`、`assistant_drift`）；`target=ai_girlfriend` 的 `response_mode_rejected`；`boundary_pressure_present` 信号。 |
| **可更新——减少证据** | 时间 → 每个轮次自动衰减至 0.00（`instant` 衰减）。无需显式减少信号。 |
| **歧义证据** | "这感觉有点……"——不完整的句子；可能是污染识别或脆弱性表达。 |
| **不应更新** | 来自先前轮次的持久污染抵抗力（已于 `contamination_resistance` 中）；一般风格抱怨；无法分类为已定义污染类型之一的文本。 |
| **最易混淆的对象** | `contamination_resistance` — 压力是信号（当前轮检测），抵抗力是记忆（跨轮次持久）。`correction_pressure` — 污染纠正同时触发两者。 |
| **基线资格** | **绝无基线资格。** 基态为 0.00。衰减配置为 `instant`（1.00）。该变量是纯瞬时信号——不应在任何持久上下文中使用。 |
| **消费者模块** | `FieldStateUpdater` → `MotionParams`（`completion_inhibition`、`gaze_release_amplitude`、`gaze_contact_sec`、`torso_lean`、`posture_stability`、`expression_amplitude`、`hard_constraints` 中的 6 个布尔标志） → `BodyActionWeights`。注意：由于衰减为 `instant`，且更新顺序为弛豫→扰动→钳制，`contamination_pressure` 仅在同时有污染信号的**同一轮次**中通过 `MotionParams` 产生下游效应。这不同于触发持久 `contamination_resistance` 变化，后者将持续影响后续轮次。 |
| **歧义说明** | `contamination_pressure` 的直接下游消耗与持久抵抗力效应之间的不对称产生了一种情况：高 `contamination_resistance`（来自先前的污染）与低 `contamination_pressure`（无当前信号）将产生与低 `contamination_resistance` + 高 `contamination_pressure` 相同轮次影响截然不同的 `MotionParams`。区分这两种情况的唯一方法是通过追踪诊断——它们无法仅通过检查输出状态来单独识别。 |

---

## 4. 易混淆轴群

以下群组已通过可辨识性审计被确定为有问题的。在处理这些群组中的证据赋值时，工程师应参考此部分的歧义解决方案。

### 群组 1：污染三角 — `contamination_pressure` / `contamination_resistance` / `service_resistance`

**为何易混淆：** 单次污染检测（例如，`boundary_pressure_present` 或 `target=ai_girlfriend` 的 `response_mode_rejected`）同时触发所有三个轴的累积变化。由于衰减速率不同（`instant`与`very_slow`），每次污染事件后，抵抗力随时间的变化形态是这些衰减曲线的卷积——仅凭当前值无法单独识别。

**区分信号：**
- `contamination_pressure` = 0.00（衰减后）→ 当前轮次无污染。任何非零的 `contamination_resistance` 均归因于历史污染。
- `service_resistance` 衰减后不低于 0.55；`contamination_resistance` 衰减后不低于 0.40。若观察到两者均在 0.55 以上，则 `service_resistance` 曾单独通过客服语调纠正被推动（而非污染）。
- 仅客服语调纠正（`target=customer_service_tone`）增加 `service_resistance` 但不增加 `contamination_resistance`。这是唯一的**纯粹**区分证据。

### 群组 2：结构性二元组 — `structural_grip_pressure` / `collaborator_layer_pressure`

**为何易混淆：** 二者共享衰减配置（`fast`，0.45）及相似的上下文证据（低结构内容）。若无明确的技术/项目上下文，这两个轴无法单独识别。

**区分信号：**
- `technical_layer_needed` 信号**增加** `collaborator_layer_pressure`（+0.18）同时**减少** `structural_grip_pressure`（-0.05）。此为反相关——在两个轴上产生相反方向的运动。
- `actionable_grip_missing` 信号仅增加 `structural_grip_pressure`（+0.10）。此为纯粹区分证据。
- 若技术层路由出现误分类，两个轴均将被错误更新。

### 群组 3：距离/退缩二元组 — `boundary_distance` / `withdrawal_tendency`

**为何易混淆：** 二者在无当前扰动规则的情况下协同变化——可将它们推向不同方向。然而，其衰减速率不同（`boundary_distance` = `slow` 0.12，`withdrawal_tendency` = `medium` 0.25），产生短暂解耦：距离保持升高而退缩已恢复。这可能仅是建模假象，也可能被误读为"冷漠却未退缩"。

**区分信号：**
- 目前不存在。两个变量均由相同信号增加/减少（`boundary_pressure_present`、`actionable_grip_missing`）。

### 群组 4：温暖/服务二元组 — `affective_warmth` / `service_resistance`

**为何易混淆：** 温暖度的增加可能表现为服务行为。高服务抵抗抑制温暖度，外部审计人员可能将此视为"系统变得冷淡"，而非"系统正在抵抗服务姿态"。

**区分信号：**
- `service_resistance` 的增加通过污染信号或客服语调纠正触发；`affective_warmth` 的减少是**下游后果**。温暖度不独立变化——它响应于其他场压力。

---

## 5. 基线资格政策

| 资格 | 变量 |
|------|------|
| **项目不变量 / 仅设计基线** | `service_resistance`（硬下限 0.55——定义 Aphrodite 为非服务实体） |
| **有会话基线资格（需审查）** | `boundary_distance`、`affective_warmth`（下限 0.15）、`contamination_resistance`（下限 0.40）、`presence_stability`、`withdrawal_tendency` |
| **绝无基线资格** | `structural_grip_pressure`（纯阶段性）、`correction_pressure`（纯事件驱动）、`collaborator_layer_pressure`（纯阶段性）、`contamination_pressure`（纯瞬时信号） |

规则：
- **当前无运行时基线偏移机制。** `BaselineShift` 为仅提案状态。
- **任何未来的基线偏移均需：**（a）显式人工审批门；（b）离线审查，包含变更前后差异；（c）来源追踪；（d）回滚测试；（e）禁止由单轮证据触发。
- 会话内基线写入（若未来实现）绝不应自动跨会话持久化。

---

## 6. 证据→轴更新矩阵

| 信号 / 证据类型 | BD | AW | SG | CP | CR | PS | WT | SR | CL | ctP |
|-----------------|----|----|----|----|----|----|----|----|----|-----|
| `response_mode_rejected`（一般） | — | — | — | ↑↑ | — | ↓ | — | ↑ | — | — |
| `response_mode_rejected`（ai_girlfriend target） | ↑ | ↓ | — | ↑↑ | ↑↑ | ↓ | — | ↑ | — | ↑↑ |
| `response_mode_rejected`（customer_service_tone target） | — | ↓ | — | ↑ | — | ↓ | — | ↑↑ | — | — |
| `actionable_grip_missing` | ↓ | ↑ | ↑↑ | — | — | stab. | ↓ | — | ↑ | — |
| `boundary_pressure_present` | ↑↑ | ↓ | — | — | ↑↑ | — | ↑ | — | — | ↑↑ |
| `technical_layer_needed` | stab. | stab. | ↓ | — | — | — | — | stab. | ↑↑ | — |
| `source_material_must_not_be_sanitized` | — | stab. | — | ↑ | ↑ | — | — | ↑ | ↑ | — |
| `no_observable_field_signal` | 弛豫 | 弛豫 | 弛豫 | 弛豫 | 弛豫 | 弛豫 | 弛豫 | 弛豫 | 弛豫 | 弛豫 |

**图例：** ↑ = 增加，↑↑ = 强增加，↓ = 减少，stab. = 稳定（0.0 增量），弛豫 = 仅衰减（无新扰动），— = 无直接效应

---

## 7. 未决问题

1. **可辨识性：** 群组 3（`boundary_distance` / `withdrawal_tendency`）在无能够产生发散运动的扰动规则的情况下无法单独识别。当前的运动对此对是定义性的还是偶然性的？
2. **`presence_stability` 的汇总性质：** 该变量表现为高阶汇总变量，而非一个独立可分的场维度。它能否降级为派生指标？
3. **`contamination_pressure` 的下游消耗：** 此变量在 `MotionParams` 中作为直接钳制参数被消耗，尽管其衰减为 `instant`。其效应仅在能"捕获"仍处于衰减前的 `contamination_pressure` 的同一轮次制图步骤中显现。这是有意设计还是隐式时间耦合？
4. **衰减速率对称性：** `correction_pressure` 和 `withdrawal_tendency` 的衰减速率是否应为对称的（均为 `medium` 0.25）？它们各自独特的衰减速率是否具有动机性理由？
5. **信号→轴映射完整性：** 现有的 6 种信号类型是否充分覆盖了该 10 轴空间？或者是否存在无法通过当前规则可靠更新的轴？

---

## 8. 未来所需测试

本文件中作出的可辨识性声明需要经验性验证：

1. **Golden-case 分离测试：** 构建用户输入，使其明确仅应更新群组中的一个轴，并验证相邻的轴保持在 1e-10 范围内不变。
2. **衰减解耦测试：** 在两个变量被一同推动后，验证其不同的衰减速率产生暂时可区分的状态，并记录解耦窗口。
3. **技术层误分类测试：** 输入非技术内容标记为 `technical_layer_needed`，验证 `structural_grip_pressure` 不会被错误减少。
4. **单轮扰动计数测试：** 验证单条用户消息在同一轴上产生的扰动不超过 N 个（当前：通过正则过度匹配时可能无上界）。
5. **无信号轮次清理测试：** 经历 10+ 轮 `no_observable_field_signal` 后，验证所有变量已弛豫至其基态值 ± ε。

---

> **文档结束。**
>
> 本文档定义了 Aphrodite 场至运动流水线中全部 10 个场变量的操作语义。其设计目标为减少歧义，而非增加新理论。无需新的变量、无需新的动力学，也无需行为影响授权。
