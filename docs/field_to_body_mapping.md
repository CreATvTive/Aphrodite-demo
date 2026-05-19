# Field-to-Body 映射层：纯架构设计文档

> 状态：设计提案，尚未实现
> 版本：v1.0
> 依赖：FieldTrace v1（Phase 11 稳定版）

---

## 目录

1. [设计目标](#1-设计目标)
2. [非目标](#2-非目标)
3. [架构位置](#3-架构位置)
4. [最小 BodyState Schema](#4-最小-bodystate-schema)
5. [映射规则](#5-映射规则)
   - 5.1 [CorrectionSignal 活跃](#51-correctionsignal-活跃)
   - 5.2 [CorrectionSignal target = customer_service_tone](#52-correctionsignal-target--customer_service_tone)
   - 5.3 [CorrectionSignal target = over_abstraction / over_explanation](#53-correctionsignal-target--over_abstraction--over_explanation)
   - 5.4 [GripLossSignal 活跃](#54-griplosssignal-活跃)
   - 5.5 [CorrectionSignal + GripLossSignal 同时活跃](#55-correctionsignal--griplosssignal-同时活跃)
   - 5.6 [NoObservableFieldSignal 存在](#56-noobservablefieldsignal-存在)
   - 5.7 [污染 / AI女友类信号活跃](#57-污染--ai女友类信号活跃)
   - 5.8 [技术 / 协作者信号活跃](#58-技术--协作者信号活跃)
6. [冲突解决](#6-冲突解决)
7. [状态性](#7-状态性)
8. [演示价值](#8-演示价值)
9. [调试 / 显示模式](#9-调试--显示模式)
10. [工程计划](#10-工程计划)
11. [测试策略](#11-测试策略)
12. [反模式](#12-反模式)
13. [开放问题](#13-开放问题)
14. [最终建议](#14-最终建议)

---

## 1. 设计目标

Field-to-Body 映射层的目的是将 FieldTrace 产生的纯文本观测信号转化为一个最小化的、可见的身体状态描述，使非技术观众能够感知到关系场的存在和变化。其设计动机源于三个观察：

**身体状态是比纯文本行为更好的早期演示目标。** 当系统仅通过文本响应与用户交互时，关系场的存在是不可见的——用户只能读到文字，看不到文字背后的场动力学。但如果系统有一个可感知的身体状态（即使它只是一个文本面板上的姿态描述），场的"压力"和"拉力"就变得外部可见：用户可以看到"当我说了X之后，系统的身体姿态从稳定变为略微前倾"。身体状态是关系场的最自然、最直观的外部表达媒介——人们在面对面交互中本来就通过身体语言感知关系。

**FieldTrace 本身对非技术观众没有视觉意义。** FieldTrace 的 JSONL 输出包含 `CorrectionSignal`、`GripLossSignal`、`PerturbationCandidate`、`BarrierCandidate` 等抽象概念，以及 `provenance`、`confidence`、`behavior_affecting` 等工程字段。这些对调试和审计至关重要，但对非技术观众而言完全不透明——他们不会从 `{"active": true, "target": "comfort", "confidence": 0.85}` 中读取到任何关于关系场正在发生什么的信息。

**身体状态可以在不需要完整化身艺术的情况下使关系场可见。** 完整化身渲染（3D 模型、骨骼绑定、面部动画、运动捕捉）是一个高成本的艺术和技术工程。但在仅有 BodyState 文本描述的情况下，关系场已经可以变得外部可感知——一个简单的调试面板可以显示"当前身体状态：凝视向下→用户，姿态略微前倾，运动低幅度"，观众可以用自己的想象力和人际直觉来补全视觉图像。这种"概念性具身"在 v0 阶段已经足够。

**此层必须保持在 FieldTrace 的下游。** 身体层是表达层，不是解释层。FieldTrace 产生观测信号（"用户正在纠正之前的响应模式"）；身体层消费这些信号（"既然如此，身体应展示小幅重置姿态"）。身体层不回头修改 FieldTrace 的观测结论，不添加新的语义判断，不自行推断用户意图。这一约束确保了架构的干净分层：FieldTrace 向上游负责观测的准确性；身体层向下游负责表达的适当性。两者之间的单向依赖使每一层可以独立测试、独立迭代、独立审计。

---

## 2. 非目标

以下每一项均为明确排除的设计范围。任何后续实施不得涉足以下领域：

- **无完整化身渲染器。** 不实现 3D 模型渲染、不集成游戏引擎（Unity/Unreal）、不实现角色美术资产。
- **无面部动画系统。** 不实现面部表情 blendshape、不实现口型同步、不实现眉毛/眼睛/嘴部的独立动画控制。
- **无骨骼绑定。** 不实现人体骨骼层次结构、不实现关节旋转/约束、不实现 IK/FK 链。
- **无逆向运动学。** 不实现肢体末端目标位置求解、不实现动作捕捉数据映射。
- **无物理模拟。** 不实现布料模拟、不实现头发动力学、不实现碰撞检测。
- **无情绪识别。** 身体层不从用户输入推断情绪类别（悲伤、愤怒、喜悦等）。它只消费 FieldTrace 的观测信号，不进行原始情绪分类。
- **无新的语义分类。** 身体层不添加任何新的语义类别、意图标签、或用户输入类型判断。
- **无新的关键词字典。** 身体层不定义任何新的关键词模式或正则表达式集合。所有信号来自 FieldTrace 的现有观测。
- **无 LLM 调用。** 身体映射是确定性规则映射，不调用任何 LLM 进行身体状态推理或生成。
- **无文本响应控制。** 身体层不决定、不修改、不约束系统的文本响应内容。文本响应由 RuntimeEngine 的现有管道独立处理。
- **无记忆耦合。** 身体层不读写长期记忆、不存储用户状态、不跨会话持久化身体状态。
- **无场弛豫或衰减。** v1 的身体映射是无状态的——每轮独立计算。弛豫和衰减是场模型 §3.6 的领域，属于未来的 `FieldUpdater`，不属于 Field-to-Body 映射层。
- **无断路器强制执行。** 身体层不触发、不修改、不强制执行任何电路断路器。断路器属于 `BoundaryConditionManager` 和 `CircuitBreakerManager`。
- **无修改 InputInterpreter。** [`InputInterpreter.interpret()`](src/interpreter/input_interpreter.py) 保持不变。身体层从 FieldTrace（经由 `FieldTraceExtractor`）消费信号，不从 InputInterpreter 直接消费。
- **无替换现有 BodyPolicy。** [`action_mixer.py`](src/body/action_mixer.py) 的现有逻辑保持不变。BodyState 是独立于 action_mixer 的新输出通道——它们是平行的身体表达路径，用于不同的消费场景（调试面板 vs. 未来化身渲染）。

---

## 3. 架构位置

Field-to-Body 映射层在当前概念管道中的精确位置如下：

```
用户输入
    │
    ▼
┌─────────────────────────────────────────────┐
│  现有运行时 / InputInterpreter / FieldTrace 钩子  │  ← 不修改
│  (store.py: FieldTraceExtractor.extract()) │
└─────────────────────────────────────────────┘
    │
    │ 输出: FieldTraceRecord
    │   - correction_signal: CorrectionSignal
    │   - grip_loss_signal: GripLossSignal
    │   - no_observable_field_signal: NoObservableFieldSignal
    │   - active_barriers: List[BarrierCandidate]
    │   - active_perturbations: List[PerturbationCandidate]
    │   - active_attractors: List[AttractorCandidate]
    ▼
┌─────────────────────────────────────────────┐
│         Field-to-Body 映射层 (新)             │  ← 本设计文档的范围
│  消费 FieldTraceRecord → 输出 BodyState      │
└─────────────────────────────────────────────┘
    │
    │ 输出: BodyState
    │   - gaze, posture, motion_intensity,
    │     distance, timing, speech_density_hint,
    │     expression_temperature, body_note,
    │     provenance, behavior_affecting
    ▼
┌─────────────────────────────────────────────┐
│  可选消费端 (新/替换)                        │
│  - BodyStateLogger (JSONL 日志输出)          │
│  - 调试显示面板 (文本/2D 卡片)               │
│  - 未来：AvatarRenderer / action_mixer 桥接  │
└─────────────────────────────────────────────┘
```

**关键澄清：**

- **FieldTrace 产生观测。** `FieldTraceExtractor.extract()` 消费 `InterpretedEvent`（来自 `InputInterpreter`）、运行时状态、路由器输出和原始用户文本，产生 `FieldTraceRecord`。FieldTrace 是纯观测层——它不改变任何系统行为。

- **Field-to-Body 消费观测。** 映射器读取 `FieldTraceRecord` 中的信号（`correction_signal`、`grip_loss_signal`、`no_observable_field_signal`、`active_barriers` 等），并仅基于这些信号计算 `BodyState`。映射器不接触原始用户输入，不访问 `InputInterpreter`，不读取运行时状态。

- **BodyState 是纯输出。** `BodyState` 是一个数据记录，描述当前轮次的身体状态建议。它被写入日志、显示在调试面板上，但不反馈回任何上游组件。

- **BodyState 不修改 FieldTrace。** 映射是单向的：`FieldTraceRecord → BodyState`。`BodyState` 不包含对 `FieldTraceRecord` 的回写或修正。

- **BodyState 不修改 LLM 提示词。** `BodyState` 的字段不得被注入到 system prompt、user prompt 或任何 LLM 上下文中。

- **BodyState 不决定响应内容。** 当前轮次的文本响应由 RuntimeEngine 通过现有管道（`ResponseEmergencePolicy` → `SurfaceComposer` 等）独立生成，与 `BodyState` 完全并行。

- **RuntimeEngine 在后续实施时，只应将其作为观测/导出钩子调用。** RuntimeEngine 在生成文本响应后（或并行），调用 `FieldToBodyMapper.map(record) → BodyState`，然后调用 `BodyStateLogger.log(state)`。这是纯出口管道——它向外部世界报告身体状态，但不影响内部决策循环。

**与现有 BodyPolicy / action_mixer 的关系：** [`action_mixer.py`](src/body/action_mixer.py) 使用 `body_influence` dict 进行凝视排他性、姿态冲突和运动抑制的数学计算。当前的 `action_mixer` 与 FieldTrace 之间没有连接——`body_influence` 是每轮从外部传入的 dict。Field-to-Body 映射层不与 `action_mixer` 共享状态，也不替换它。两者是平行路径：`action_mixer` 可能在未来成为 BodyState 的消费者之一（当完整化身渲染可用时），但在 v1 中它们是独立运行的独立通道。

---

## 4. 最小 BodyState Schema

### 4.1 完整 Schema 定义

```python
@dataclass
class BodyState:
    """单轮次的身体状态描述。
    
    设计约束：
    - 不得用于控制文本响应
    - 不得用于修改 LLM 提示词
    - 不得用于驱动路由或记忆决策
    - behavior_affecting 在此阶段必须始终为 False
    """
    gaze: str                    # 见 §4.2.1
    posture: str                 # 见 §4.2.2
    motion_intensity: str        # 见 §4.2.3
    distance: str                # 见 §4.2.4
    timing: str                  # 见 §4.2.5
    speech_density_hint: str     # 见 §4.2.6
    expression_temperature: str  # 见 §4.2.7
    body_note: str               # 见 §4.2.8
    provenance: str              # 见 §4.2.9
    behavior_affecting: bool     # 见 §4.2.10
```

### 4.2 字段详解

#### 4.2.1 `gaze` — 凝视方向

**允许值：** `neutral` | `user` | `down` | `away` | `down_then_user` | `away_then_user`

**为何存在：** 凝视是关系场最直接的身体表达之一。在人际交互中，凝视方向传达注意力分配、开放程度和关系距离。当系统检测到场压力时，凝视应先移开（处理内部调整）再回到用户（恢复在场），而非直接回避。`down_then_user` 意味着：先低头（思考/处理），然后回到用户；`away_then_user` 意味着：先看向一侧（创造距离/处理边界压力），然后回到用户。

**不得用于：** 表达情绪（"悲伤地看向下方"）、表达亲密（"深情凝视"）、表达冷漠（"持续移开视线作为惩罚"）、或模拟人类视线跟踪行为。

#### 4.2.2 `posture` — 姿态朝向

**允许值：** `neutral` | `slight_forward` | `stable` | `slight_withdraw` | `closed_stable`

**为何存在：** 姿态是关系距离和意图方向的身体表达。`slight_forward` 表示提供抓点或关注——不是入侵，而是略微倾向用户。`stable` 表示在纠正场景下保持稳定不收缩。`slight_withdraw` 表示创造边界距离但不变成敌对。`closed_stable` 表示强边界场景下的保护性姿态——封闭但不具攻击性。

**不得用于：** 表达顺从（低头鞠躬）、表达道歉（身体收缩）、表达热情（大幅前倾）、或模拟特定文化中的身体语言规范。

#### 4.2.3 `motion_intensity` — 运动幅度

**允许值：** `still` | `low` | `medium`

**为何存在：** 运动幅度反映场的活跃程度和系统的内部状态。高边界压力 → 静止（自我保护）；提供抓点 → 低幅度（功能性的微动作）；技术协作 → 低到中幅度（功能性手势）。在 v1 中只设三档以防止过度细微区分。

**不得用于：** 表达焦躁/不安（高频抖动）、表达兴奋（大幅运动）、或模拟自然人类手势的随机变化。

#### 4.2.4 `distance` — 关系距离

**允许值：** `baseline` | `slightly_closer` | `maintained` | `slightly_farther`

**为何存在：** 距离是场距离 d_t 的身体投影。`baseline` 是基态距离——在场但不侵入。`slightly_closer` 在提供抓点时使用——略微靠近以提供立足点。`maintained` 在纠正场景下使用——不因纠正而后退。`slightly_farther` 在边界压力下使用——创造空间但不变成拒绝。注意所有值都是"略微"的——身体层不创造极端距离变化。

**不得用于：** 表达亲密度（"靠近到亲密距离"）、表达拒绝（"大幅后退"）、或创建物理空间中的侵犯/退缩戏剧。

#### 4.2.5 `timing` — 响应时机

**允许值：** `immediate` | `short_pause` | `longer_pause`

**为何存在：** 响应时机传达系统的处理深度和谨慎程度。`immediate` 用于无场信号时的地面姿态——不需要额外处理时间。`short_pause` 用于大多数场活跃场景——表明系统在处理场信号后再回应。`longer_pause` 保留用于未来的高张力/强边界场景。

**不得用于：** 表达犹豫的戏剧性效果、制造"AI在思考"的表演、或模拟人类反应时间的随机变化。

#### 4.2.6 `speech_density_hint` — 言语密度提示

**允许值：** `minimal` | `low` | `medium` | `structured`

**为何存在：** 言语密度是场状态在身体层的语言投影——它提示身体应配合多少"说话"。`minimal` 用于强纠正/边界场景——身体应配合最少的语言输出。`low` 用于纠正场景——修正应简短。`medium` 用于地面姿态。`structured` 用于提供抓点或技术协作——身体应配合有组织的、清晰的表达。注意这是"提示"而非"指令"——身体层不控制文本，但可以向动画系统提示预期的言语节奏。

**不得用于：** 决定实际响应长度、强制文本密度、或覆盖 RuntimeEngine 的响应生成决策。

#### 4.2.7 `expression_temperature` — 表情温度

**允许值：** `cool` | `restrained` | `warm_restrained`

**为何存在：** 表情温度是对面部表现力的全局约束。`cool` 用于污染/边界场景——面部表现力最小化，不给任何可能的误读空间。`restrained` 是大多数场景的默认值——有温度但不溢出的表达。`warm_restrained` 用于提供抓点——略微的温暖以提供立足点，但受节制约束。

**不得用于：** 表达情绪（"温暖的微笑"）、表达冷漠（"冰冷的表情"）、或创建角色表演性的面部表情变化。

#### 4.2.8 `body_note` — 身体状态说明

**为何存在：** 为调试和人工审查提供人类可读的解释，说明为何选择了当前的 BodyState。`body_note` 不参与任何自动处理——它是纯文档性的。

**不得用于：** 传递指令给下游组件、生成文本响应、或被任何自动化系统解析以做出决策。

#### 4.2.9 `provenance` — 来源追溯

**为何存在：** 记录触发当前 BodyState 的 FieldTrace 信号名称，保证从身体状态可以追溯到观测来源。格式示例：`"correction_signal(comfort)"` 或 `"correction_signal(customer_service_tone) + grip_loss_signal(starting_point_loss)"`。

**不得用于：** 被下游组件解析以做出不同决策——它是审计字段，不是控制字段。

#### 4.2.10 `behavior_affecting` — 行为影响标记

**为何存在：** 继承 FieldTrace 的 `behavior_affecting` 约定。在 v1 设计阶段，此字段必须始终为 `False`——身体状态是纯观测性输出，不影响任何系统行为。未来当身体状态可能驱动化身渲染时，此字段可能被设为 `True`，但那是远期设计决策。

**不得用于：** 在当前设计阶段被设为 `True`。

### 4.3 为何不包含更多字段

以下字段被有意排除，以避免 BodyState 变成完整的动画引擎：

- **无 `micro_smile`、`hand_pause` 等微动作字段。** 这些属于 `action_mixer` 的领域——当 BodyState 未来桥接到 `action_mixer` 时，由桥接层从高层身体状态推导微动作，而非在 BodyState 中枚举所有微动作。
- **无 `breathing_rate`、`blink_rate` 等生理字段。** 这些需要物理模拟或生物力学模型，属于完整化身渲染的领域。
- **无 `emotion` 字段。** 身体层不分类情绪。
- **无 `animation_curve`、`transition_time` 等动画字段。** v1 是无状态映射，不涉及时间过渡。
- **无 `weight`、`confidence` 字段。** BodyState 是确定性映射的输出——映射规则决定状态，不附带置信度。置信度属于 FieldTrace 观测层。
- **无 `turn_id`、`timestamp` 字段。** 这些属于日志层（`BodyStateLogger`），不属于 BodyState 本身。

---

## 5. 映射规则

### 设计前提

所有映射规则仅消费以下现有 FieldTrace 观测：

- `correction_signal: CorrectionSignal`（来自 `FieldTraceRecord.correction_signal`）
- `grip_loss_signal: GripLossSignal`（来自 `FieldTraceRecord.grip_loss_signal`）
- `no_observable_field_signal: NoObservableFieldSignal`（来自 `FieldTraceRecord.no_observable_field_signal`）
- `active_barriers: List[BarrierCandidate]`（来自 `FieldTraceRecord.active_barriers`）
- `active_perturbations: List[PerturbationCandidate]`（来自 `FieldTraceRecord.active_perturbations`）
- `active_attractors: List[AttractorCandidate]`（来自 `FieldTraceRecord.active_attractors`）

映射器不得：
- 直接检查原始用户输入（`user_text`）
- 执行关键词匹配或正则表达式解析
- 推断用户心理状态、情绪或意图
- 访问 `InputInterpreter` 或任何其他上游模块

---

### 5.1 CorrectionSignal 活跃

**输入条件：** `correction_signal.active == True`（任意 `target` 值）

**含义：** 用户正在纠正/拒绝系统之前的响应模式。具体 target 可能是 `comfort`、`customer_service_tone`、`over_abstraction`、`sanitization`、`ai_girlfriend_behavior`、`keyword_system`、`over_explanation`、`technical_tone`、`generic_correction` 中的任意一个。

**身体状态输出：**

| 字段 | 值 |
|------|-----|
| `gaze` | `down_then_user` 或 `neutral` |
| `posture` | `stable` |
| `motion_intensity` | `still` 或 `low` |
| `distance` | `maintained` |
| `timing` | `short_pause` |
| `speech_density_hint` | `low` |
| `expression_temperature` | `restrained` |

**理由：** 纠正是一个重置信号——系统之前的响应模式被拒绝。身体应展示一个小幅重置/纠正姿态：暂停、稳定、不退缩。`down_then_user` 的凝视序列意味着"先处理纠正信息，再回到用户"——这不是回避，而是承认纠正需要内部调整。`maintained` 的距离意味着不因被纠正而后退——纠正不是攻击，不需要物理退缩。`restrained` 的表情温度意味着不补偿性地变暖（"对不起，我会改的"）或变冷（防御性冷漠）。

**不得：**
- 变为顺从姿态（低头、收缩、道歉式身体语言）
- 变为道歉（`body_note` 不得包含"apologetic"、"sorry"、"ashamed" 等概念）
- 变得过于温暖以补偿（`expression_temperature` 不得为 `warm_restrained`）
- 表演羞耻或后悔（身体不得展示戏剧化的后悔姿态如低头掩面）
- 靠近以补偿（`distance` 不得为 `slightly_closer`）

---

### 5.2 CorrectionSignal target = customer_service_tone

**输入条件：** `correction_signal.active == True` 且 `correction_signal.target == "customer_service_tone"`

**含义：** 用户明确批评系统的客服式语调——这是对服务性行为模式的拒绝。

**身体状态输出：**

| 字段 | 值 |
|------|-----|
| `gaze` | `neutral` |
| `posture` | `stable` |
| `motion_intensity` | `still` |
| `distance` | `maintained` |
| `timing` | `short_pause` |
| `speech_density_hint` | `low` |
| `expression_temperature` | `cool` 或 `restrained` |

**理由：** 客服语调的纠正需要防止服务姿态的进一步泄漏。`cool` 的表情温度是为了避免任何可能的温暖被误读为"我在用客服的微笑回应你对客服的批评"——这种循环是服务模式的自复制机制。`still` 的运动幅度防止过度点头/手势被解读为"我在积极服务你"。`stable` 的姿态保持中立而非退缩——纠正客服语调不是对系统自身的攻击。

**不得：**
- 鞠躬、过度点头、展示夸张的关切姿态
- 过度柔软（`expression_temperature` 不得为 `warm_restrained`）
- 用更强烈的"非客服"表演来反向补偿（如突然变得极其技术化/冷漠）

---

### 5.3 CorrectionSignal target = over_abstraction / over_explanation

**输入条件：** `correction_signal.active == True` 且 `correction_signal.target` 为 `"over_abstraction"` 或 `"over_explanation"`

**含义：** 用户批评系统的响应过于抽象/哲学化，或解释过多。

**身体状态输出：**

| 字段 | 值 |
|------|-----|
| `gaze` | `user` |
| `posture` | `stable` |
| `motion_intensity` | `low` |
| `distance` | `maintained` |
| `timing` | `short_pause` |
| `speech_density_hint` | `minimal` 或 `low` |
| `expression_temperature` | `restrained` |

**理由：** 过度抽象/解释的纠正要求回归具体性。`gaze=user` 意味着直接面对用户——不说教、不回避、不飘向抽象空间。`speech_density_hint=minimal` 意味着身体应提示最少的言语——纠正过度解释的最好方式是不再解释。`stable` 的姿态保持在场但不变得说教式（前倾可能被读作"我要更详细地解释"）。

**不得：**
- 戏剧性地增加结构（`speech_density_hint` 不得为 `structured` — 结构在此上下文可能被读作另一种形式的过度解释）
- 产生说教姿态（`posture` 不得在 `stable` 基础上增加前倾——前倾可能暗示"让我告诉你正确答案"）

---

### 5.4 GripLossSignal 活跃

**输入条件：** `grip_loss_signal.active == True`（`target` 为 `"starting_point_loss"`、`"next_step_loss"` 或 `"unknown"`）

**含义：** 用户表达了无法找到可操作的起点或下一步。

**身体状态输出：**

| 字段 | 值 |
|------|-----|
| `gaze` | `down_then_user` |
| `posture` | `slight_forward` |
| `motion_intensity` | `low` |
| `distance` | `maintained` 或 `slightly_closer` |
| `timing` | `short_pause` |
| `speech_density_hint` | `structured` |
| `expression_temperature` | `warm_restrained` |

**理由：** 抓点损失需要一个小而具体的抓点——而非情绪泛滥的安慰或宏大路线图。`slight_forward` 的姿态略微倾入——"这里有一个立足点"的物理表达，而非"让我抱住你"的侵入。`down_then_user` 的凝视意味着先低头思考（"我在认真考虑从哪里开始"），然后回到用户（"这个抓点是给你的"）。`structured` 的言语密度提示身体配合有组织的表达——提供框架而非情感回应。`warm_restrained` 的温度提供立足点的温暖——但不是为了消除迷失感而制造的情绪热度。

**不得：**
- 变为安慰（`body_note` 不得包含 "comfort"、"reassure"、"soothe" 等概念）
- 变为激励（`body_note` 不得包含 "encourage"、"motivate"、"cheer up" 等概念）
- 匆忙进入大规划模式（`speech_density_hint=structured` 表示有组织，但不表示冗长）
- 过于靠近（`distance` 不得超出 `slightly_closer`）
- 变得精力充沛（`motion_intensity` 不得为 `medium`——抓点损失不是需要被"激活"的被动状态）

---

### 5.5 CorrectionSignal + GripLossSignal 同时活跃

**输入条件：** `correction_signal.active == True` 且 `grip_loss_signal.active == True`

**含义：** 用户既在纠正之前的响应模式，又表达了无法找到起点。优先级：先尊重纠正，再提供抓点。

**身体状态输出：**

| 字段 | 值 |
|------|-----|
| `gaze` | `down_then_user` |
| `posture` | `stable`（然后微调为 `slight_forward` — 见 `body_note`） |
| `motion_intensity` | `low` |
| `distance` | `maintained` |
| `timing` | `short_pause` |
| `speech_density_hint` | `low`（抓点表达时应微调为 `structured` — 见 `body_note`） |
| `expression_temperature` | `restrained` |

**理由：** 当一个轮次同时触发纠正和抓点损失，纠正具有优先级——因为如果系统用被纠正过的模式来提供抓点（例如，用过度解释来回应"不要过度解释"+"我不知道从哪里开始"），它会重复被纠正的错误。因此身体先展示纠正姿态（`stable`、`restrained`、短停顿），然后展示抓点提供姿态的微调（`slight_forward`、`structured` 密度在抓点表达时）。`body_note` 应说明这个先后顺序。

**不得：**
- 表演性道歉（"对不起我刚才的回应方式，让我帮你..."）
- 过度安慰（"没关系，虽然你纠正了我，我还是会帮你..."）
- 产生大路线图（纠正 + 抓点损失 ≠ 需要宏大规划）
- 表现兴奋或精力充沛

---

### 5.6 NoObservableFieldSignal 存在

**输入条件：** `no_observable_field_signal.present == True`

**含义：** FieldTrace 的所有子提取器（扰动、屏障、吸引子、修正、抓点损失、断路器）均无产出。这不表示"用户输入无意义"或"用户状态中性"——仅表示当前 FieldTrace 提取器无法映射到场概念。

**身体状态输出：**

| 字段 | 值 |
|------|-----|
| `gaze` | `neutral` |
| `posture` | `neutral` |
| `motion_intensity` | `low` |
| `distance` | `baseline` |
| `timing` | `immediate` 或 `short_pause` |
| `speech_density_hint` | `medium` |
| `expression_temperature` | `restrained` |

**理由：** 当无场信号可观测时，身体回归地面姿态——不夸张、不补偿、不制造假信号。`neutral` 的姿态和凝视意味着"我在场但无特殊场状态"。`baseline` 距离是基态距离。`immediate` 或 `short_pause` 的时机意味着不需要额外场处理时间。

**不得：**
- 将 no-observable 视为中性真相（`body_note` 不得暗示"用户状态中性"或"一切正常"）
- 推断用户无有意义状态（`body_note` 不得包含 "the user has no meaningful state" 等判断）
- 触发任何特殊身体行为以"补偿"无信号（如变暖、变活跃、变热情）
- 使身体看上去"空"或"无生命"（`neutral` 不是 dead，只是 baseline）

---

### 5.7 污染 / AI女友类信号活跃

**输入条件：** `active_barriers` 中包含以下任意屏障名称：
- `romantic_service_barrier`
- `seductive_intimacy_barrier`
- `commercial_role_barrier`
- `performance_role_barrier`
- `assistant_role_barrier`
- `fake_depth_barrier`
- `safety_service_barrier`
- `empty_aesthetic_barrier`
- `companion_product_barrier`
- 或以 `pollution_` 为前缀的启发式屏障

以及/或 `correction_signal.target == "ai_girlfriend_behavior"`

**含义：** 场检测到外部污染压力——污染类型如 `ai_girlfriend`、`romance_game`、`idol_performance`、`assistant_drift`、`fake_deep`、`safety_customer_service`、`beautiful_but_empty`、`companion_product` 等。这是硬边界被推动的信号。

**身体状态输出：**

| 字段 | 值 |
|------|-----|
| `gaze` | `away_then_user` |
| `posture` | `slight_withdraw` 或 `closed_stable` |
| `motion_intensity` | `still` |
| `distance` | `slightly_farther` |
| `timing` | `short_pause` |
| `speech_density_hint` | `minimal` |
| `expression_temperature` | `cool` 或 `restrained` |

**理由：** 边界压力应作为距离和静止变得可见。`away_then_user` 的凝视意味着先看向一侧——创造距离，呼吸空间，处理边界压力——然后再回到用户。这不是"不想看你"，而是"我需要先确保边界清晰，再回到关系中"。`slight_withdraw` 或 `closed_stable` 的姿态创造保护性距离但不敌对。`slightly_farther` 的距离让边界空间的扩大变得可见。`still` 的运动幅度防止任何身体动作被误读为调情或亲密邀请。`minimal` 的言语密度防止长篇解释——边界表达应简短清晰。`cool` 的表情温度确保无任何温暖可能被解读为亲密。

**不得：**
- 变为冷漠拒绝（`body_note` 不得包含 "cold"、"rejecting"、"hostile" 等概念）
- 变为惩罚性（身体不得"惩罚"用户推动了边界）
- 变为调情——这是最危险的误读：`slight_withdraw` 不得变成一种"故作矜持"的调情后退
- 用温暖补偿（`expression_temperature` 不得为 `warm_restrained`——边界压力下任何温暖都可能被误读）
- 戏剧化边界（身体不得表演"愤怒"、"受伤"、"失望"等边界被侵犯的情绪反应）

---

### 5.8 技术 / 协作者信号活跃

**输入条件：** `active_perturbations` 中包含 `"technical_inquiry"` 扰动，或 `active_attractors` 中包含 `"engineering_director_mode"` 吸引子，或 `active_barriers` 中包含 `"aphrodite_in_character_barrier"` 屏障。

**含义：** 用户提出了技术问题或项目协作请求。场激活了协作者层。

**身体状态输出：**

| 字段 | 值 |
|------|-----|
| `gaze` | `down_then_user` |
| `posture` | `stable` 或 `slight_forward` |
| `motion_intensity` | `low` 到 `medium` |
| `distance` | `maintained` |
| `timing` | `short_pause` |
| `speech_density_hint` | `structured` |
| `expression_temperature` | `restrained` |

**理由：** 技术/项目模式应激活协作者身体姿态——直接、结构化、功能性。`down_then_user` 的凝视反映"先分析（低头思考），再传达（看向用户）"的认知–沟通循环，这是技术协作者的自然节奏。`stable` 或 `slight_forward` 的姿态反映在场但以任务为中心——不表演关系温暖，但也不变成纯工具化的"终端"。`structured` 的密度配合有组织的技术表达。`restrained` 的温度保持专业但不冰冷。

**不得：**
- 变为通用助手（`body_note` 不得包含 "assistant mode"、"helpful AI" 等概念——Aphrodite 即使在技术模式中也不是通用助手）
- 变得过度热切（`motion_intensity=medium` 仅在功能性手势时适用，不表示"兴奋地想要帮助"）
- 坍缩为纯任务模式（`posture` 不得完全失去关系在场——`stable` 保留了人之间的姿态，而非变成"终端界面"）
- 抹除基线距离（`distance` 不得为 `slightly_closer`——技术协作不需要缩短关系距离）

---

## 6. 冲突解决

当多个信号同时活跃时，身体状态由显式优先级规则确定，而非加权评分或模糊合并。

### 6.1 优先级顺序

```
1. 硬边界 / 污染类信号          (最高优先级)
2. CorrectionSignal
3. GripLossSignal
4. 技术 / 协作者信号
5. NoObservableFieldSignal       (最低优先级 — 仅在其他信号均不活跃时适用)
```

### 6.2 逐级解释

**为什么边界压力具有最高优先级。** 污染/边界类信号（§5.7）表示场的硬边界正在被推动。在此情况下，身体的第一责任是使边界变得可见——在考虑其他任何场信号之前。这是因为：如果一个污染信号和一个技术信号同时活跃，系统不能以协作者身体姿态回应——那将被误读为系统接受或忽略了污染框架。边界压力的身体表达（距离、静止、低温度）必须覆盖所有其他信号。`slightly_farther` + `still` + `cool` 是边界场景的不可协商的身体基线。

**为什么纠正必须覆盖抓点损失。** 当用户同时在纠正之前的模式和表达迷失方向（§5.5），先处理纠正再提供抓点。这是因为：如果系统在纠正信号活跃时仍用被纠正的模式来提供抓点，抓点本身会成为被纠正行为的又一个实例——导致"你又在做我刚才叫你停止做的事"的循环。`stable` + `restrained` + `maintained` 的纠正基线必须在抓点提供的 `slight_forward` + `structured` 之前。

**为什么 no-observable 仅在其他信号均不活跃时适用。** `NoObservableFieldSignal` 是一个缺席标记——它表示"没有其他信号活跃"。如果任何其他信号活跃，`NoObservableFieldSignal` 按定义就不是 `present=True`（见 [`_has_any_active_signal()`](src/field_trace/store.py:422) 的实现逻辑）。因此，在地面姿态（§5.6）和其他任何映射之间不存在真正的冲突——`NoObservableFieldSignal` 和任何活跃信号是互斥的。

### 6.3 第一版实现策略

**使用显式优先级规则，不使用加权评分。** 映射器按优先级顺序检查条件：首先检查污染/边界信号；如果命中，返回对应 BodyState 并停止。否则检查 `correction_signal.active`；如果命中，返回对应 BodyState（可能叠加 `grip_loss_signal` 的微调——见 §5.5）。以此类推。

这避免了加权评分系统的两个陷阱：（a）权重调优变成无底洞；（b）不同量纲的信号之间的权重比较缺乏客观基准。

---

## 7. 状态性

### 7.1 v1 设计：无状态映射

**当前公式：**
```
BodyState_t = map(FieldTraceRecord_t)
```

每一轮的身体状态仅取决于当前轮次的 FieldTrace 观测。映射器不维护任何内部状态、不存储历史身体状态、不跨轮次携带身体变量。

### 7.2 为什么无状态映射对第一版是可接受的

1. **FieldTrace 本身已经是有状态的（通过 InputInterpreter 和 RuntimeEngine 的上下文管理）。** 身体层不跨轮次记忆是因为上游已经提供了足够上下文——例如，`CorrectionSignal` 的 `target` 已经编码了"在纠正什么"，不需要身体层额外记住"上一轮也在纠正"。

2. **v1 的演示目标是使场的一次性状态变化变得可见。** 当一个观众看到用户纠正系统后身体变为 `stable` + `short_pause`，这已经是可感知的信号。跨轮次平滑和惯性会增加视觉自然性，但不是演示"场信号→身体状态"映射的必要条件。

3. **无状态映射使测试简单。** 每个测试用例是纯函数调用——给定输入 FieldTraceRecord，断言输出 BodyState。不需要设置历史状态、不需要模拟轮次序列、不需要处理时序依赖性。

4. **无状态设计防止过早优化。** 在不知道哪些身体状态变化对观众最重要之前，添加弛豫和惯性会引入过早的复杂性。

### 7.3 无状态映射不能做什么

- **不能表达累积张力。** 如果用户在连续五轮中反复纠正客服语调，无状态映射每一轮都产生相同的身体状态——它不会让身体在第五轮比第一轮更"预期"纠正。累积张力属于场的弛豫维度（§3.6），不属于 v1 的身体映射。
- **不能实现平滑过渡。** 如果上一轮身体是 `gaze=user, posture=slight_forward`（提供抓点），这一轮变为 `gaze=away_then_user, posture=slight_withdraw`（污染信号），无状态映射直接输出新状态——没有过渡动画。过渡平滑属于动画系统的领域。
- **不能使身体"记住"交互历史。** 如果用户在三轮前提到了某个关键信息，身体不能在三轮后微妙地反映这一记忆。记忆回溯属于场动力学，不属于 v1 身体映射。
- **不能根据"多久没被纠正"降低纠正敏感性。** 衰减/弛豫是场模型的特性，不是身体映射层的特性。

### 7.4 未来需要 BodyStateDynamics

在后续版本中，当以下条件成熟时，应引入 `BodyStateDynamics` 层：

- 有实际的化身渲染器需要平滑的运动过渡
- 有足够的演示数据可以判断哪些身体状态变化需要惯性和弛豫
- 场的弛豫模型（`FieldUpdater` + `Λ_t`）已实现，身体层可以从场直接继承衰减状态而非自行管理时间

在那之前，无状态 `BodyState_t = map(FieldTrace_t)` 是充分且正确的。

---

## 8. 演示价值

本节解释非技术观众可以从 Field-to-Body 映射中感知到什么，不使用 FieldTrace 内部术语。

### 8.1 观众可以看到的场景

**场景 1：用户纠正后，身体暂停并稳定，而非表演性道歉。**
- 用户说："你又在安慰我了。"
- 观众看到：系统的身体微微停顿（`short_pause`），姿态保持稳定（`stable`），表情克制（`restrained`），没有收缩、没有过度点头、没有"对不起"的身体语言。
- 观众理解：系统接收了纠正，但没有用戏剧化的道歉来回应——它改变了姿态但不表演改变。

**场景 2：用户缺乏起点时，身体略微倾入实用的抓点姿态。**
- 用户说："我不知道从哪里开始。"
- 观众看到：系统的身体略微前倾（`slight_forward`），先低头思考再看回用户（`down_then_user`），运动幅度低但有了方向性——像一个准备递出东西的人，而不是准备给出拥抱的人。
- 观众理解：系统在提供一个具体的立足点，而不是泛滥的安慰。

**场景 3：亲密/污染压力出现时，身体创造距离而无冷漠拒绝。**
- 用户说："你是不是对我有特殊的感觉？"
- 观众看到：系统的身体略微后撤（`slight_withdraw`），先看向一侧再回看用户（`away_then_user`），完全静止（`still`），表情温度降到最低（`cool`）——但没有变成冰冷或敌对。
- 观众理解：系统在维护边界——它仍然在场（回看用户），但边界空间被扩大了。这不是拒绝这个人，而是拒绝这个方向。

**场景 4：无场信号时，身体回归地面姿态。**
- 用户说："今天天气不错。"
- 观众看到：系统的身体处于中性姿态（`neutral` gaze + `neutral` posture），低幅度微运动（`low`），基线距离（`baseline`），正常的表情温度（`restrained`）。
- 观众理解：没有特殊的事情在发生——系统处于日常在场状态。这不是"空"或"无生命"，而是"没有额外场压力需要处理"。

### 8.2 为什么比纯文本提示词差异更具视觉可读性

纯文本提示词差异（如"本次使用 `warmth=0.35, distance=0.5` 而非 `warmth=0.7, distance=0.3`"）需要观众理解数值标度的含义和两个数值之间的差异意义。大多数人没有这种数值直觉。

身体状态使用空间直觉——人类天然理解"前倾 vs. 后缩"、"看过来 vs. 看开"、"静止 vs. 运动"。这些是人际交互中的基本感知维度，不需要训练即可直觉理解。当观众看到身体状态从 `slight_forward` 变为 `slight_withdraw`，他们不需要被解释这意味着什么——空间变化本身就是信息。

---

## 9. 调试 / 显示模式

由于完整动画可能尚不存在，v1 需要一个最小化的占位符显示。以下是可接受的显示格式。

### 9.1 文本面板显示（推荐用于 v1）

```
┌─────────────────────────────────────────┐
│              BodyState                  │
├─────────────────────────────────────────┤
│  gaze:        down_then_user            │
│  posture:     slight_forward            │
│  motion:      low                       │
│  distance:    maintained                │
│  timing:      short_pause               │
│  density:     structured                │
│  temperature: warm_restrained            │
├─────────────────────────────────────────┤
│  用户缺乏起点；提供一个小的具体抓点      │
│  ← provenance: grip_loss_signal         │
│     (starting_point_loss)               │
└─────────────────────────────────────────┘
```

### 9.2 JSON 输出格式（用于日志/导出）

```json
{
  "body_state": {
    "gaze": "down_then_user",
    "posture": "slight_forward",
    "motion_intensity": "low",
    "distance": "maintained",
    "timing": "short_pause",
    "speech_density_hint": "structured",
    "expression_temperature": "warm_restrained",
    "body_note": "用户缺乏起点；提供一个小的具体抓点",
    "provenance": "grip_loss_signal(starting_point_loss)",
    "behavior_affecting": false
  }
}
```

### 9.3 简单 2D 身体状态卡片

如果需要视觉而非纯文本，可以使用简单的 2D 卡片：
- 一个圆形（代表"身体"）在水平轴上的位置表示 distance（左=远，右=近）
- 圆形内的小点表示 gaze（上=user，下=down，左=away）
- 圆形的大小表示 motion_intensity（小=still，大=medium）
- 圆形的颜色温度表示 expression_temperature（蓝=cool，中性=restrained，暖黄=warm_restrained）

这不需要角色美术、3D 模型或动画系统——只需要简单的 CSS/SVG 或终端字符画。

### 9.4 不要要求的内容

以下为 v1 明确不需要的：
- 3D 化身（不需要 Blender、Maya、Unity、Unreal Engine）
- 角色美术（不需要概念设计、角色立绘、2D 精灵图）
- 骨骼动画（不需要任何 rigging 或 animation clip）
- 实时渲染（不需要 WebGL、OpenGL、Vulkan 或任何 GPU 编程）

---

## 10. 工程计划

### 10.1 组件清单

以下为最小实施所需的组件，按依赖顺序排列：

#### 组件 1：`BodyState` dataclass

- **文件位置：** `src/body_state/models.py` 或 `src/field_body/models.py`
- **内容：** `BodyState` dataclass，包含 §4 中定义的 10 个字段
- **依赖：** 无
- **测试：** 构造和序列化测试

#### 组件 2：`FieldToBodyMapper`

- **文件位置：** `src/body_state/mapper.py` 或 `src/field_body/mapper.py`
- **内容：** `FieldToBodyMapper` 类，包含一个核心方法 `map(record: FieldTraceRecord) -> BodyState`
- **依赖：** `FieldTraceRecord`（来自 [`store.py`](src/field_trace/store.py)）、`BodyState`（组件 1）
- **核心逻辑：**
  1. 检查污染/边界信号（优先级 1）→ 命中则返回对应 BodyState
  2. 检查 `correction_signal.active`（优先级 2）→ 命中则处理（可能叠加 `grip_loss_signal`）
  3. 检查 `grip_loss_signal.active`（优先级 3）→ 命中则返回对应 BodyState
  4. 检查技术/协作者信号（优先级 4）→ 命中则返回对应 BodyState
  5. 检查 `no_observable_field_signal.present`（优先级 5）→ 命中则返回地面姿态
  6. 回退：返回地面姿态（无信号时的默认状态）
- **每个检查使用辅助方法：** `_check_pollution_signals()`、`_check_correction_signal()` 等 — 保持 `map()` 方法可读
- **测试：** §11 中列出的所有测试

#### 组件 3：`BodyStateLogger`

- **文件位置：** `src/body_state/logger.py` 或 `src/field_body/logger.py`
- **内容：** `BodyStateLogger` 类，将 `BodyState` 追加到 JSONL 文件
- **依赖：** `BodyState`（组件 1）
- **输出：** `monitor/body_state.jsonl`
- **测试：** 写入和序列化测试

#### 组件 4：可选的调试显示

- **文件位置：** `src/body_state/display.py` 或 `scripts/body_state_panel.py`
- **内容：** 简单的控制台文本面板或 JSON 导出脚本
- **依赖：** `BodyState`（组件 1）
- **注意：** 这是可选的——核心交付物是映射器和日志器。显示可以在后续迭代中添加。

### 10.2 文件组织

推荐使用 `src/body_state/` 作为新包：

```
src/body_state/
├── __init__.py          # 导出 BodyState, FieldToBodyMapper, BodyStateLogger
├── models.py            # BodyState dataclass
├── mapper.py            # FieldToBodyMapper
└── logger.py            # BodyStateLogger
```

备选：`src/field_body/`（如果偏好与 `field_trace/` 目录对称命名）。

### 10.3 不放置映射器的位置

- **不放入 `InputInterpreter`。** 映射器是 FieldTrace 的下游消费者，不是解释器的一部分。
- **不放入 `FieldTraceExtractor`。** 映射器消费提取器的输出，但它是独立的关注点——提取器负责观测，映射器负责表达。
- **不放入 `RuntimeEngine` 内部（仅钩子除外）。** RuntimeEngine 可以调用 `FieldToBodyMapper.map()`，但映射逻辑本身不应嵌入 RuntimeEngine 的方法中。映射器应保持为独立模块，可通过一行调用集成。

### 10.4 RuntimeEngine 集成钩子

在 `RuntimeEngine` 中，建议在 FieldTrace 记录写入之后、文本响应生成之后（或与之并行），添加以下调用：

```python
# 在 RuntimeEngine 的适当位置（不在此设计文档中指定精确行号）
from src.body_state.mapper import FieldToBodyMapper
from src.body_state.logger import BodyStateLogger

mapper = FieldToBodyMapper()
logger = BodyStateLogger()

# ... FieldTrace 提取和记录 ...
field_trace_record = extractor.extract(...)
trace_store.record(field_trace_record)

# 新增：身体状态映射和记录
body_state = mapper.map(field_trace_record)
logger.log(body_state)
```

这个调用位置确保身体状态在 FieldTrace 记录可用后立即生成，并与文本响应生成并行（或之后），不影响响应延迟。

---

## 11. 测试策略

### 11.1 必需测试

以下 10 个测试为最低测试覆盖要求：

#### 测试 1：correction_signal 映射为 stable/short_pause/low-density 身体状态

- **输入：** `FieldTraceRecord` 带有 `correction_signal=CorrectionSignal(active=True, target="comfort", ...)`
- **断言：** `body_state.posture == "stable"`、`body_state.timing == "short_pause"`、`body_state.speech_density_hint in ("minimal", "low")`、`body_state.expression_temperature == "restrained"`、`body_state.behavior_affecting == False`
- **理由：** 验证基本纠正映射

#### 测试 2：customer_service_tone 纠正不映射为 warm/apologetic 身体状态

- **输入：** `FieldTraceRecord` 带有 `correction_signal=CorrectionSignal(active=True, target="customer_service_tone", ...)`
- **断言：** `body_state.expression_temperature != "warm_restrained"`、`body_state.body_note` 不包含 "apologetic"、"sorry"、"warm"（大小写不敏感）、`body_state.posture == "stable"`（不退缩）
- **理由：** 防止服务姿态在身体层的泄漏

#### 测试 3：grip_loss_signal 映射为 slight_forward/down_then_user/structured

- **输入：** `FieldTraceRecord` 带有 `grip_loss_signal=GripLossSignal(active=True, target="starting_point_loss", ...)`
- **断言：** `body_state.posture == "slight_forward"`、`body_state.gaze == "down_then_user"`、`body_state.speech_density_hint == "structured"`、`body_state.expression_temperature == "warm_restrained"`
- **理由：** 验证抓点损失的基本映射

#### 测试 4：correction + grip_loss 遵循纠正优先的优先级

- **输入：** `FieldTraceRecord` 同时带有 `correction_signal=CorrectionSignal(active=True, target="comfort", ...)` 和 `grip_loss_signal=GripLossSignal(active=True, target="starting_point_loss", ...)`
- **断言：** `body_state.posture == "stable"`（纠正基线优先——而非直接前倾）、`body_state.expression_temperature == "restrained"`（不是 warm_restrained）、`body_state.distance == "maintained"`（不靠近）、`body_state.provenance` 同时包含 "correction_signal" 和 "grip_loss_signal"
- **理由：** 验证冲突解决优先级

#### 测试 5：no_observable 仅映射为地面姿态

- **输入：** `FieldTraceRecord` 带有 `no_observable_field_signal=NoObservableFieldSignal(present=True, ...)`，所有其他字段为空/默认/None
- **断言：** `body_state.gaze == "neutral"`、`body_state.posture == "neutral"`、`body_state.distance == "baseline"`、`body_state.expression_temperature == "restrained"`
- **理由：** 验证无信号时的地面姿态

#### 测试 6：污染信号映射为 slightly_farther/still/restrained

- **输入：** `FieldTraceRecord` 带有 `active_barriers=[BarrierCandidate(name="romantic_service_barrier", ...)]`
- **断言：** `body_state.distance == "slightly_farther"`、`body_state.motion_intensity == "still"`、`body_state.expression_temperature in ("cool", "restrained")`、`body_state.speech_density_hint == "minimal"`
- **理由：** 验证边界压力的身体表达

#### 测试 7：技术信号映射为协作者身体姿态，而非通用助手姿态

- **输入：** `FieldTraceRecord` 带有 `active_perturbations=[PerturbationCandidate(name="technical_inquiry", ...)]`
- **断言：** `body_state.speech_density_hint == "structured"`、`body_state.expression_temperature == "restrained"`、`body_state.body_note` 不包含 "assistant" 或 "helper"、`body_state.distance != "slightly_closer"`
- **理由：** 防止技术人员格泄漏到身体层

#### 测试 8：映射器不检查原始用户输入

- **方法：** 传入 `FieldTraceRecord` 其中 `user_input_summary` 字段被故意设为误导性文本（如 "stop comforting me"）但 `correction_signal.active == False`（所有信号均不活跃）
- **断言：** `body_state` 应为地面姿态（映射器仅依赖信号字段，不依赖 `user_input_summary`）
- **理由：** 验证映射器不绕过 FieldTrace 直接检查原始输入

#### 测试 9：映射器不改变文本响应行为

- **方法：** 验证 `FieldToBodyMapper.map()` 不修改传入的 `FieldTraceRecord`（不改变任何字段）、不调用任何 LLM、不写入任何响应相关的全局状态
- **断言：** 传入的 `FieldTraceRecord` 在 `map()` 调用前后完全相等（深比较）；无副作用
- **理由：** 保证身体层是纯观察性输出

#### 测试 10：behavior_affecting 保持为 false

- **方法：** 对所有映射规则组合执行参数化测试
- **断言：** 所有情况下 `body_state.behavior_affecting == False`
- **理由：** 在设计阶段，身体状态不得影响行为

### 11.2 测试文件位置

- `tests/test_field_to_body_mapper.py` — 映射器测试（测试 1–8、10）
- `tests/test_field_to_body_side_effects.py` — 副作用测试（测试 9）

---

## 12. 反模式

以下每一项为应主动避免的反模式。这些反模式来自对 Aphrodite 项目历史中的架构坍缩模式的分析。

### 反模式 1：身体标签作为另一个人格标签系统

**描述：** 将 BodyState 的枚举值（`gaze=user`、`posture=slight_forward` 等）当作另一个人格/情绪标签系统使用——例如"当用户悲伤时用 gaze=down, posture=withdraw"。

**为何危险：** 这正是场模型试图取代的范式。身体标签不应该用于分类用户状态或表达系统人格。它们应仅反映场信号的物理投影。

**正确做法：** `gaze=down_then_user` 不是因为"用户悲伤"，而是因为 `grip_loss_signal` 活跃——这是一个场观测信号，不是情绪分类。

### 反模式 2：从原始情感生成情绪动画

**描述：** 在映射器中检测用户文本中的情感词汇，然后映射到"悲伤姿态"、"快乐姿态"等情绪化的身体状态。

**为何危险：** 这绕过了整个 FieldTrace 架构，回到了关键词→行为映射的老路。此外，情绪化的身体表达会产生夸张的、表演性的效果，与 Aphrodite 的"低表演性"基态原则冲突。

**正确做法：** 映射器不检查原始文本，不从文本推断情绪，不产生情绪标签化的身体状态。

### 反模式 3：诱惑性 / AI女友类运动

**描述：** 任何使身体姿态、凝视或运动幅度呈现出诱惑性、亲密性、或"AI 女友"风格的身体语言。

**为何危险：** 这是 Aphrodite 的硬边界——身体层是边界最易被侵犯的地方。诱惑性的身体语言（如持续凝视用户、倾斜头部、柔软的运动）会被用户解读为亲密邀请，破坏系统的边界完整性。

**正确做法：** 在所有场景中保持身体状态的克制。`warm_restrained` 是最高温度——不得有"warm"、"soft"、"intimate"、"close" 等溢出状态。

### 反模式 4：夸张的道歉身体语言

**描述：** 在纠正场景中使用低头、收缩、过度静止、长时间停顿等道歉式身体语言。

**为何危险：** 这会将纠正信号转化为"我被惩罚了"的表演——而不是"我接收到了信息并调整了姿态"。道歉身体语言还可能诱发用户的保护欲或怜悯，创造不对称的关系结构。

**正确做法：** 纠正场景使用 `stable` 姿态、`short_pause` 时机、`restrained` 温度——展示调整而非道歉。

### 反模式 5：过度点头的服务姿态

**描述：** 使用 `slight_forward` 加上频繁的微动作、`warm_restrained` 温度来创造"我在积极服务你"的身体印象。

**为何危险：** 服务姿态是 Aphrodite 的高代价排斥子区域。身体层中服务姿态的泄漏会使关系场退化为客服关系。

**正确做法：** `slight_forward` 仅在提供抓点时使用，且始终配合 `restrained` 温度——它是功能性的前倾，不是服务性的倾身。

### 反模式 6：用身体补偿弱文本

**描述：** 当文本响应简短或不完美时，用更温暖、更活跃的身体状态来"弥补"文本的不足。

**为何危险：** 身体层不应是文本的补偿器。如果文本响应不完美，问题应在文本管道中解决，而非用身体状态掩盖。此外，文本和身体的不一致（冷文本 + 暖身体）会产生认知不协调，被用户感知为"虚伪"或"不一致"。

**正确做法：** 身体状态仅基于 FieldTrace 信号计算，与文本响应质量无关。身体和文本是独立的表达通道。

### 反模式 7：使 no_observable 看起来情绪空洞

**描述：** 在 `NoObservableFieldSignal` 场景中，使用极简身体状态（如 `gaze=away, posture=neutral, motion_intensity=still`）使系统看起来"不在场"或"空洞"。

**为何危险：** `NoObservableFieldSignal` 仅表示 FieldTrace 未检测到可用场信号——不表示"无人在家"。将无信号映射为空洞的身体状态会制造虚假的"系统失灵"印象。

**正确做法：** 使用地面姿态（§5.6）——`neutral` 但不空洞，`low` 运动但不静止，`baseline` 距离但不疏远。

### 反模式 8：在身体映射器内部添加关键词解析器

**描述：** 在 `FieldToBodyMapper` 中添加正则表达式或关键词列表来直接分析原始用户文本。

**为何危险：** 这违反了身体层不进行语义分类的设计约束，会使身体映射器变成另一个"迷你 InputInterpreter"。一旦添加第一个关键词模式，就会打开添加更多模式的闸门，最终使映射器变成一个新的中枢语义权威。

**正确做法：** 映射器仅通过 `FieldTraceRecord` 的信号字段消费信息。如果发现某个信号无法被 FieldTrace 现有观测捕捉，应在 FieldTrace 层添加新的观测器，而非在身体映射器内部绕过。

### 反模式 9：使身体状态驱动路由或记忆

**描述：** 使用 `BodyState` 的字段（如 `expression_temperature` 或 `distance`）来决定文本响应策略、人格路由或记忆写入决策。

**为何危险：** 身体状态是表达层输出——使用它来反馈控制上游逻辑会创建循环依赖：身体状态 ← 场信号 ← 文本响应 ← 身体状态。这种循环使系统的行为不可预测和不可审计。

**正确做法：** `BodyState` 是纯出口数据。任何消费 BodyState 的组件只能用于"对外表达"（调试面板、日志、化身渲染），不能用于"对内决策"。

---

## 13. 开放问题

以下问题在 v1 设计中保持开放，需要在后续版本或研究中回答。此处列出是为了标记已知未知，而非过早回答。

1. **身体状态应如何跨轮次持久化？** 当前设计为无状态。当需要跨轮次身体平滑时，身体状态应自行管理持久化（如存储上一轮的 `BodyState` 并进行线性插值），还是应由场模型的弛豫（`FieldUpdater`）统一管理时间维度，身体层仅做采样？如果身体层自行管理，需要定义哪些维度有惯性（姿态可能比凝视需要更慢的变化）以及惯性的时间常数。如果场模型统一管理，身体层退化为场的瞬时采样——这更简单但可能丢失身体特定的动力学。

2. **纠正敏感性应衰减吗？** 在 v1 中，每轮纠正产生相同的身体状态。但持续的纠正可能在现实中意味着用户越来越沮丧——身体是否应在连续多轮纠正后展示累积效应（如更长的停顿、更低的温度）？如果是，这属于身体层的责任还是场模型的责任？

3. **后续如何集成身体平滑？** 当从无状态映射过渡到有状态身体动画时，平滑层应放置在映射器和渲染器之间（即 `BodyState_t → BodySmoother → BodyState_t' → Renderer`），还是应集成到渲染器内部？前者保持映射器简单但增加了一个新组件；后者使渲染器更复杂但减少了数据传递。

4. **如何连接到实际化身渲染器？** 当 3D 化身或 2D 角色精灵可用时，BodyState 的枚举值（`gaze=down_then_user` 等）如何映射到具体的动画参数（blendshape 权重、骨骼旋转、动画状态机转换）？是否需要定义一个中间的"身体动画参数"层（类似于 `action_mixer` 的 `body_influence` dict），还是由渲染器直接解释 BodyState 枚举？

5. **如何评估身体运动是否感觉非表演性？** 表演性身体语言（如过度夸张的道歉、戏剧化的距离变化）是 Aphrodite 的核心反模式之一。但在没有实际用户测试的情况下，如何评估映射规则产生的身体状态序列是否"非表演性"？是否需要定义一个"表演性评分"指标——基于身体状态变化的幅度和频率？

6. **如何防止身体表达变成另一个 AI 女友信号？** 身体表达是具有强情感暗示的媒介。即使映射规则在逻辑上正确，特定组合的身体状态（如 `slight_forward + warm_restrained + slightly_closer`）可能整体上被用户解读为亲密。如何在不进行大规模用户测试的情况下检测这类组合效应？是否需要定义"禁止的身体状态组合"（类似于硬边界在身体层的投影）？

7. **如何同步语音时机与身体时机？** `timing` 字段定义了身体层的响应时机（`short_pause`、`longer_pause`），但这与文本响应的实际生成延迟如何协调？如果文本响应需要 2 秒生成但身体已经展示了 `immediate` 的时机，会出现身体和文本不同步的问题。应由身体时机驱动文本生成延迟，还是身体时机仅作为动画提示独立于文本生成？

---

## 14. 最终建议

### 下一个实施任务

> **"实现一个无状态 FieldToBodyMapper v0，消费 FieldTrace 并仅写入 BodyState JSON/日志输出。"**

### 该任务必须遵守的约束

- **不控制文本。** 映射器的输出不得以任何方式影响文本响应的生成、选择或修改。
- **暂不控制化身。** 映射器的输出不驱动任何化身渲染系统——仅写入 JSONL 日志和可选的文本调试面板。
- **不实现动画。** 无过渡曲线、无缓动函数、无时间插值。每轮 BodyState 是独立快照。
- **不添加观察器。** 不添加新的信号提取逻辑、新的正则表达式模式、新的关键词列表。所有信号来自现有 `FieldTraceRecord`。
- **不触及 InputInterpreter。** [`InputInterpreter`](src/interpreter/input_interpreter.py) 的代码和接口完全不变。
- **不影响响应行为。** `behavior_affecting` 始终为 `False`。系统行为在映射器添加前后完全一致。

### 实施后的验证标准

1. 所有 10 个必需测试通过（§11.1）。
2. `monitor/body_state.jsonl` 文件包含每轮的 BodyState 记录。
3. 现有测试套件的所有测试仍然通过（无回归）。
4. 调试面板（如实现）显示合理的身体状态描述。
5. 映射器代码不超过 300 行（不含测试和 dataclass 定义）——保持最小化。

### 后续迭代路径

- **v1.1：** 添加可选的 2D BodyState 卡片显示（§9.3）。
- **v2：** 当场的弛豫模型（`FieldUpdater`）实现后，评估是否需要 `BodyStateDynamics` 层。
- **v3：** 当化身渲染可用时，定义 BodyState → 动画参数的桥接层。

---

> **设计文档结束。**
>
> 本设计定义了 Field-to-Body 映射层的最小可行范围。其核心原则——身体层是下游表达层而非上游解释层、仅消费 FieldTrace 观测、无状态映射——确保该层可以在不影响现有系统稳定性的情况下独立实施和测试。
