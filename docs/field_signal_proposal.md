# FieldSignal Proposal 机制：从临时探针到证据驱动的场信号

> 状态：设计提案，尚未实现
> 版本：v1.0
> 依赖：FieldTrace v1（Phase 15 稳定版）、FieldToBodyMapper v1、field_generation_model.md

---

## 目录

1. [诊断：为什么当前的正则探针不够](#1-诊断为什么当前的正则探针不够)
2. [现有观察器的正确角色](#2-现有观察器的正确角色)
3. [新概念：FieldSignalProposal](#3-新概念fieldsignalproposal)
4. [证据模型](#4-证据模型)
5. [候选场信号](#5-候选场信号)
6. [与 LLM 的关系](#6-与-llm-的关系)
7. [与正则观察器的关系](#7-与正则观察器的关系)
8. [提议聚合](#8-提议聚合)
9. [这如何走向场动力学](#9-这如何走向场动力学)
10. [最小实施计划（供未来参考）](#10-最小实施计划供未来参考)
11. [测试 / 审计标准](#11-测试--审计标准)
12. [决策门](#12-决策门)
13. [最终建议](#13-最终建议)

---

## 1. 诊断：为什么当前的正则探针不够

### 1.1 它们证明了什么

[`CorrectionObserver`](src/field_trace/store.py:173) 和 [`GripLossObserver`](src/field_trace/store.py:243) 作为临时探针，成功地证明了三条关键事实：

**管道可运行。** 从原始用户文本 → 正则匹配 → `CorrectionSignal`/`GripLossSignal` → `FieldTraceRecord` → [`FieldToBodyMapper.map_to_body_state()`](src/body_state/mapper.py:39) → `BodyState` → [`BodyStateLogger`](src/body_state/logger.py) → JSONL 输出，整条链路是端到端可工作的。这不是理论——它在 [`monitor/field_trace.jsonl`](Aphrodite-demo/monitor/field_trace.jsonl) 和 [`monitor/body_state.jsonl`](Aphrodite-demo/monitor/body_state.jsonl) 中产生了可审计的输出。

**信号可被下游消费。** `FieldToBodyMapper` 的 8 条显式优先级映射规则证明了：场信号可以被转化为身体状态描述，且这种转化是确定性的、可测试的、不依赖 LLM 的。

**身体状态可被驱动。** 调试显示面板和 JSONL 日志证明了：当观测器检测到信号时，身体状态会发生相应的、可感知的变化——观众可以看到系统"暂停并稳定"或"略微前倾提供抓点"。

这三条是实质性的工程成就。它们证明了这个架构方向不是空想。

### 1.2 它们不能证明什么

然而，作为场机制，正则探针存在结构性的能力缺口：

**不能检测到正则模式未覆盖的修正。** 16 条英文正则模式覆盖了 `comfort`、`customer_service_tone`、`over_abstraction`、`sanitization`、`ai_girlfriend_behavior`、`keyword_system`、`over_explanation`、`technical_tone`、`generic_correction` 九个 target。但用户说"你这话说得太圆滑了"——这不是 `customer_service_tone` 模式能匹配的，虽然它在语义上指向同一类问题。用户说"你又开始了"——这没有匹配任何模式，但如果上一轮系统确实在安慰，它就是一次有效的修正。正则探针对措辞变化是盲的。

**不能理解上下文。** `CorrectionObserver.observe()` 的签名是 `(self, raw_text: str) -> CorrectionSignal`。它只看到当前轮的原始文本。它不知道上一轮系统说了什么、上一轮用户是否已经表达过类似的修正、当前对话的整体基调是什么。这意味着同一个用户输入"stop it"在系统上一轮确实在安慰时和在系统上一轮在提供技术分析时，产生完全相同的 `CorrectionSignal`——尽管前者的修正是有根据的，后者可能是误解或玩笑。

**不能区分强弱证据。** 当前观察器在单个正则匹配成功时直接返回该匹配的 `CorrectionSignal`，其 `confidence` 是模式表中预设的固定值（如 0.90、0.85、0.80）。这个置信度只反映了"该措辞与该 target 的匹配有多精确"，不反映"该修正在此上下文中多有根据"。一个孤立的、模糊的正则匹配和一个被上下文强化的、多次重复的修正，可能获得相同的置信度。

**不能处理竞争解释。** 当用户说"I don't know where to start, and your abstract explanations aren't helping"，这同时触发了 `GripLossObserver`（`starting_point_loss` 模式）和可能的 `CorrectionObserver`（`over_abstraction` 模式）。当前系统产生两个独立的信号，由 `FieldToBodyMapper` 的优先级链选择其中一个来驱动身体状态。但这两个信号之间的关系——它们是独立的、还是前者导致了后者、还是两者都是同一潜在场状态的两个表现——系统完全没有建模。优先级链是工程上的权宜之计，不是认识论上的解决方案。

### 1.3 为什么继续添加正则模式会重现旧的 InputInterpreter 问题

[`InputInterpreter.interpret()`](src/interpreter/input_interpreter.py) 包含 150+ 关键词和 6 组 if-else 链。它不是设计错误——它是"中枢语义权威"架构在工程上的唯一收敛点：当系统被要求输出一个明确的语义判断，而输入空间是开放的、模糊的、情境依赖的，唯一可工程化的近似就是不断增加规则。

`CorrectionObserver` 和 `GripLossObserver` 目前只有 16+10=26 条模式——远小于 InputInterpreter 的 150+。但如果继续这条路径：

- 添加中文模式（当前所有模式都是英文）→ 模式数量翻倍
- 添加新的 target 类型（如 `over_formality`、`too_cold`、`too_emotional`）→ 每个新 target 需要 2-5 条模式
- 添加变体覆盖（同义表达、否定形式、间接表达）→ 每个现有 target 的模式数膨胀

最终结果是一个 100+ 条模式的集合，具有与 InputInterpreter 相同的结构性缺陷：
- **维护负担线性增长**：每条新模式需要测试、需要与现有模式检查冲突、需要在 golden cases 中验证
- **覆盖范围任意**：为什么是这 100 条而不是那 100 条？选择标准是什么？当用户说出第 101 种表达时怎么办？
- **无法区分可靠信号与偶然匹配**：正则不编码可靠性——它编码的是"这个措辞出现了"

### 1.4 为什么这不可能是最终的场模型

`field_generation_model.md` 定义的场是一个**持久的关系场**——它有基态、边界条件、吸引子、排斥子、扰动、弛豫和断路器。场的核心操作是：

> 用户输入扰动场，不生成语义标签。

但正则探针做的恰恰是生成标签：`text pattern → signal label`。这不是扰动——这是分类。它问的是"这个用户输入匹配什么标签？"，而场应该问的是"什么外部交互证据表明关系场发生了变化？"

场需要的是**证据累积和不确定性**，而不是模式匹配。场需要知道的不只是"用户说了 X"，而是：
- 用户说的 X 与系统上一轮的行为之间的关系是什么？
- 用户是否在之前的轮次中也表达了类似的信号？
- 是否有其他证据支持或反对这个信号？
- 这个信号的强度在上下文中应该被增强还是减弱？
- 有哪些竞争性的解释？

正则探针不回答这些问题。它们不是场机制的失败实现——它们根本就不是场机制。它们是临时探针，证明了管道可以运行，但不应被误认为是场本身。

---

## 2. 现有观察器的正确角色

### 2.1 重新分类：临时低级证据探针

当前三个观察器应被重新分类如下：

| 现有名称 | 新角色 | 解释 |
|---------|--------|------|
| `CorrectionObserver` | 临时探针，为"显式用户纠正"提供**一条证据线** | 它检测到用户使用了特定措辞来表达纠正，但这不是"用户正在纠正"的最终真相——它是一条证据 |
| `GripLossObserver` | 临时探针，为"显式抓点损失表达"提供**一条证据线** | 它检测到用户使用了特定措辞来表达迷失方向，但这不是"用户缺乏抓点"的最终真相——它是一条证据 |
| `NoObservableFieldSignal` | 临时探针，记录"当前探针集合未检测到任何信号" | 它是缺席标记，不是中性真相——它说"我们没看到"，不说"什么都没有" |

### 2.2 它们允许做什么

**发出显式证据。** 当 `CorrectionObserver` 匹配到 `"stop comforting"` 模式时，它产生了一条有效证据：用户的输入文本中包含明确请求停止安慰的措辞。这条证据是可引用的（`evidence` 字段包含匹配的文本片段）。

**提供可追踪的示例。** 每个信号包含 `evidence`（触发文本片段）和 `provenance`（来源标记）——这些使得每条证据可以被回溯到原始文本，进行人工审计。

**支持回放和身体显示。** `FieldTraceStore` 和 `BodyStateLogger` 的 JSONL 输出使得交互可以被回放，身体状态可以被可视化——即使信号本身只是启发式的。

### 2.3 它们不得做什么

**不得成为语义真相。** `CorrectionSignal(active=True, target="comfort")` 不能声称"用户在纠正系统的安慰行为"。它只能声称"当前正则探针在用户输入中检测到了与'comfort'相关的纠正措辞"。前者是语义判断；后者是观测报告。

**不得扩展为广泛的意图分类。** 不得在 `CorrectionObserver` 中添加新模式使其覆盖更广泛的修正表达。不得在 `GripLossObserver` 中添加新模式使其覆盖更广泛的迷失/困惑/求助表达。当前模式集合应被冻结。

**不得成为场动力学。** 一个正则匹配不应直接改变场状态。正则匹配产生证据；证据经过聚合产生提议；提议经过场动力学产生场状态变化。这三层分离是当前设计与未来场模型之间的关键桥梁。

**不得成为响应控制。** `behavior_affecting` 必须始终为 `False`。观察器是纯观测性的。

**不得成为长期记忆。** 观察器输出不应被持久化到长期记忆中作为"用户特征"或"用户偏好"。记忆应存储场相关的结构事实（修正历史、设计法则），而非正则匹配的累积。

---

## 3. 新概念：FieldSignalProposal

### 3.1 为什么需要这个中间对象

当前架构是扁平的：

```
原始文本 → 正则匹配 → CorrectionSignal / GripLossSignal → BodyState
```

这个链路的每一步都是确定性的、不可审计中间推理的。当身体状态从 `neutral` 变为 `slight_withdraw` 时，provenance 字段告诉我们"因为 `correction_signal(ai_girlfriend_behavior)`"，但我们不知道：
- 这个信号是强还是弱？
- 有没有其他证据支持或反对它？
- 有没有其他可能的解释？
- 为什么这个解释被优先于其他解释？

`FieldSignalProposal` 是一个中间对象，位于证据收集和场状态更新之间。它的存在使得从"看到什么"到"这意味着什么"的推理变得显式、可审计、可争议。

### 3.2 它不是硬分类

`FieldSignalProposal` 不声称"场的状态是 X"。它声称"基于以下证据，场的状态可能是 X，置信度为 Y，但存在以下竞争解释和不确定性"。

这不是语义上的谦虚——这是结构上的必要性。在关系场中，同一组观测可以支持不同的场解释。用户说"你又在安慰我了"：
- 可能意味着系统确实在安慰（强证据支持 `response_mode_rejected`）
- 可能意味着用户对任何温暖的表达都敏感（中等证据支持 `boundary_pressure_present`）
- 可能意味着用户在用"安慰"这个词表达对系统整体风格的不满（弱证据支持 `technical_layer_needed`——用户可能想要更直接、更少修饰的回应）

这些解释不是互斥的。但它们有不同的场效应含义。`FieldSignalProposal` 通过显式列出竞争解释，防止系统过早闭合于单一解释。

### 3.3 Schema 定义

```python
@dataclass
class FieldSignalProposal:
    """一个场信号提议——不是硬分类，而是证据支持的候选场信号。

    设计约束：
    - 不得声称最终场状态
    - behavior_affecting 必须始终为 False
    - 必须包含竞争解释和不确定性说明
    """

    # ---- 核心标识 ----
    signal_name: str
    # 提议的信号名称（见 §5 候选场信号列表）

    # ---- 证据基础 ----
    evidence_items: List[EvidenceItem]
    # 支持此提议的证据项列表（见 §4 证据模型）
    # 可以为空列表——此时提议仅基于"无相反证据"或"默认假设"

    evidence_sources: List[str]
    # 证据来源列表：regex_probe / llm_proposer / interaction_trace / user_declared / previous_response
    # 每个来源对应至少一个 evidence_item 的来源

    # ---- 置信度与不确定性 ----
    confidence_band: str
    # 粗粒度信心带：low / medium / high
    # 不使用精确浮点数（如 0.82）——见 §3.4

    uncertainty_note: str
    # 关于不确定性的说明——人类可读，用于调试和审计
    # 例如："单个正则匹配触发，无上下文支持；可能为偶然匹配"
    # 例如："多个独立证据线汇聚，但存在竞争解释（见 competing_interpretations）"

    # ---- 竞争解释 ----
    competing_interpretations: List[str]
    # 可能与此提议竞争的其他信号名称列表
    # 例如：["actionable_grip_missing", "boundary_pressure_present"]
    # 空列表 = 当前未识别到竞争解释（但这不是"不存在竞争解释"的保证）

    # ---- 建议的场效应 ----
    suggested_field_effects: List[str]
    # 如果此信号被接受，建议的场效应——不强制执行
    # 例如：["distance_increase_slight", "warmth_decrease_moderate", "service_barrier_activate"]
    # 这些是建议，不是指令——FieldStateUpdater 可以根据其他因素覆盖

    # ---- 安全约束 ----
    behavior_affecting: bool = False
    # 必须始终为 False。在当前设计阶段，场信号提议不得影响系统行为

    requires_human_review: bool = False
    # 可选：当 confidence_band 为 low 且 evidence_items 稀少时，可标记为需要人工审核
    # 默认 False——仅在不确定性极高的边界情况下设为 True

    # ---- 来源追溯 ----
    source_turns: List[int]
    # 来源轮次——哪几轮交互支持此提议
    # 例如：[3, 5] 表示第 3 轮和第 5 轮的用户输入或系统响应为证据来源

    relation_to_previous_response: Optional[str] = None
    # 与之前的助手响应的关系
    # 例如："用户在回应第 4 轮的系统输出——该输出包含高密度安慰语言"
    # 例如：None（无法确定与哪个之前的响应的关系）
```

### 3.4 为什么用粗粒度信心带替代精确假置信度

当前 `CorrectionSignal.confidence` 是浮点数（如 0.90、0.85、0.80），`GripLossSignal.confidence` 也是（0.88、0.85、0.82）。这些数值给人以精确的错觉，但它们的实际语义是：

> "这个正则模式与这个 target 的关联有多紧密"（在设计者的判断中）

这不是一个可验证的量。没有基准数据、没有概率解释、没有校准。0.90 和 0.85 之间的差异在任何操作意义上都是无意义的——没有系统会因为置信度是 0.85 而不是 0.90 而做出不同的决策。

更危险的是，精确假置信度会创造一种"可比较性"的幻觉：人们会自然地比较 `CorrectionSignal(confidence=0.90)` 和 `GripLossSignal(confidence=0.85)`，并得出结论"修正信号比抓点损失信号更可靠"。但这两个置信度来自不同的模式集合、不同的设计者判断、不同的语义域——它们之间没有共同的度量基础。

**粗粒度信心带（low/medium/high）** 做了三件事：
1. 它诚实地承认这个量级是粗略的
2. 它防止了假精确度的比较和排序
3. 它迫使设计者在聚合阶段（§8）使用基于规则的方法而非数值加权——因为 low/medium/high 不能被直接加权求和

### 3.5 为什么 `competing_interpretations` 很重要

**防止过早闭合。** 当一个信号被确认为"the"信号时，系统停止考虑其他可能性。在关系场中，过早闭合是危险的：如果系统过早地将用户的迷失方向表达解释为"请求抓点"，它可能忽略用户同时在表达的"之前的技术回应太冷了"的信号。提供抓点本身可能被视为另一种形式的冷——"你不理解我的感受，你只是在给我任务清单"。

**支持后续聚合。** 当多个提议具有 `high` 置信度但指向不同方向时，聚合器需要知道它们之间的竞争关系。如果 `response_mode_rejected` 和 `actionable_grip_missing` 都被设为 `high`，而它们的 `competing_interpretations` 互相包含对方——这说明证据是真实但有张力的，需要场的复合响应而非单一响应。

**审计透明度。** 当人工审查者看到 `FieldSignalProposal(signal_name="response_mode_rejected", confidence_band="high", competing_interpretations=[])`，他们会合理地怀疑：是否考虑了其他解释？`competing_interpretations` 强制提议者（无论是规则引擎还是 LLM）显式声明"我们考虑了 X 和 Y，但当前证据更支持 Z"。

---

## 4. 证据模型

### 4.1 EvidenceItem Schema

```python
@dataclass
class EvidenceItem:
    """单条证据——支持或反对某个场信号提议的可引用观测。

    设计约束：
    - 不得声称场状态
    - 必须包含局限性和为什么相关的说明
    - 强度是粗粒度的（weak/medium/strong）
    """

    # ---- 证据标识 ----
    evidence_type: str
    # 证据类型——必须是 EVIDENCE_TYPES 枚举中的值（见 §4.2）

    # ---- 来源 ----
    source: str
    # 来源：regex_probe / llm_proposer / interaction_trace / user_declared / previous_response
    # 表明此证据是通过什么机制获得的

    # ---- 内容 ----
    excerpt_or_reference: str
    # 文本摘录或引用——可追溯到原始交互的具体内容
    # 例如：匹配到的用户输入文本片段、被引用的上一轮系统响应片段、用户声明的内容
    # 不得为空字符串——每条证据必须包含可引用的具体内容

    # ---- 相关性 ----
    why_it_matters: str
    # 为什么此证据与此信号相关——人类可读的解释
    # 例如："用户明确使用了'stop comforting'措辞——这是对安慰行为的显式拒绝"
    # 例如："上一轮系统响应以'你做得已经很好了'开头——这符合安慰模式"

    # ---- 强度与局限 ----
    strength: str
    # 强度：weak / medium / strong
    # weak：单独不足以支持任何提议；仅作为累积证据的一部分时有意义
    # medium：有信息量但可被更强的相反证据覆盖
    # strong：独立即可显著支持某个提议；需要强相反证据才能覆盖

    limitations: str
    # 此证据的局限性——人类可读
    # 例如："正则匹配只能检测精确措辞；可能会漏掉同义表达（如'别再说了'不会被检测为 comfort 修正）"
    # 例如："LLM 识别的模式可能存在幻觉——此证据依赖 LLM 的判断，不是确定性规则"
    # 不得为空字符串——每条证据必须诚实地声明其局限
```

### 4.2 证据类型枚举

以下 10 种证据类型覆盖了从用户显式反馈到系统内部状态的主要证据来源。这不是封闭的最终列表——当场模型演进时可能需要添加新类型——但它是足够覆盖当前探针和未来 LLM 提议者的最小集合。

#### 类型 1：`explicit_user_feedback`

**定义：** 用户在当前轮次中显式表达了对系统之前响应的反馈——纠正、拒绝、批评、或肯定。

**典型来源：** `regex_probe`（当前 `CorrectionObserver` 的匹配输出）、`llm_proposer`（LLM 识别到的显式反馈措辞）

**示例证据项：**
```
EvidenceItem(
    evidence_type="explicit_user_feedback",
    source="regex_probe",
    excerpt_or_reference="stop comforting",
    why_it_matters="用户明确使用了'stop comforting'措辞——这是对安慰行为的显式拒绝",
    strength="strong",
    limitations="正则匹配只能检测此精确措辞；无法检测'别再说了'、'够了'等变体"
)
```

**强证据的条件：** 措辞明确、无歧义、直接指向系统的特定行为。

**弱证据的条件：** 措辞模糊、可能为讽刺或反问、缺乏指向系统特定行为的明确引用。

#### 类型 2：`user_declared_contract`

**定义：** 用户在当前或之前的轮次中明确声明了交互契约——关于系统应该如何或不应该如何行为的陈述。

**典型来源：** `interaction_trace`（追踪中记录的先前用户声明）、`user_declared`（当前轮次中的新声明）

**示例证据项：**
```
EvidenceItem(
    evidence_type="user_declared_contract",
    source="interaction_trace",
    excerpt_or_reference="不要净化我的文字——保持它原本的样子（第 3 轮）",
    why_it_matters="用户在第 3 轮设立了明确的'不净化'契约；当前系统响应包含净化语言可能违反了该契约",
    strength="strong",
    limitations="契约可能随时间或上下文变化——用户未明确撤销不等于契约仍然完全有效"
)
```

**与 `explicit_user_feedback` 的区别：** 用户反馈是针对系统**刚刚做的**具体行为的回应；用户声明契约是针对系统**应该或不应该做的**的一般性规则的设立。前者是反应性的；后者是规范性的。

#### 类型 3：`previous_response_mode`

**定义：** 系统在之前轮次中的响应模式——其语调、结构、密度、温暖度等可以被当前轮次的用户反馈所关联的特征。

**典型来源：** `previous_response`（对上一轮系统响应的分析）、`llm_proposer`（LLM 对上一轮响应特征的分析）

**示例证据项：**
```
EvidenceItem(
    evidence_type="previous_response_mode",
    source="previous_response",
    excerpt_or_reference="系统第 4 轮响应以'我完全理解你的感受，这确实是一个困难的处境...'开头（高温暖度、高密度安慰语言）",
    why_it_matters="上一轮系统响应包含安慰模式——这为当前轮的 explicit_user_feedback 提供了上下文：用户不是在抽象地抱怨，而是在回应一个具体的安慰行为",
    strength="medium",
    limitations="将当前用户反馈与特定上一轮响应关联是一种推断——用户可能是在回应更早的轮次或整体模式"
)
```

#### 类型 4：`feedback_after_previous_response`

**定义：** 在系统发出特定类型的响应后，用户在紧随的轮次中表达的反馈——这种时序关系增强了反馈与该响应的关联性。

**典型来源：** `interaction_trace`（跨轮次的时序分析）

**示例证据项：**
```
EvidenceItem(
    evidence_type="feedback_after_previous_response",
    source="interaction_trace",
    excerpt_or_reference="系统第 4 轮响应为舒适/鼓励型（previous_response_mode 证据确认），用户第 5 轮输入为'你又在安慰我了'",
    why_it_matters="时序上的紧密关联（系统安慰 → 用户立即拒绝）加强了这是一个有效修正信号的判断——不是用户的一般性偏好表达，而是对具体行为的反馈",
    strength="strong",
    limitations="时序关联不是因果关系——用户可能同时回应多个事物，或在延迟后才表达反馈"
)
```

#### 类型 5：`repeated_correction`

**定义：** 用户在多个轮次中重复表达了类似的修正——这表明该修正不是偶发的或一次性的，而是指向系统的一个持续行为模式。

**典型来源：** `interaction_trace`（跨轮次的修正历史比较）

**示例证据项：**
```
EvidenceItem(
    evidence_type="repeated_correction",
    source="interaction_trace",
    excerpt_or_reference="用户在第 2 轮说'别安慰我'，在第 5 轮说'你又在安慰我了'——两轮都触发了 comfort 修正信号",
    why_it_matters="重复修正表明系统的安慰模式在第一次修正后未被充分调整——这不是一个孤立事件，而是一个持续的模式问题",
    strength="strong",
    limitations="重复修正可能是用户对系统行为的敏感度提高——而非系统行为确实重复了相同的错误"
)
```

#### 类型 6：`unresolved_grip_loss`

**定义：** 用户表达了抓点损失，且后续轮次中该抓点损失未被解决——用户仍然缺乏可操作的起点或下一步。

**典型来源：** `interaction_trace`（跨轮次的抓点损失信号追踪）

**示例证据项：**
```
EvidenceItem(
    evidence_type="unresolved_grip_loss",
    source="interaction_trace",
    excerpt_or_reference="用户第 3 轮表达了'I don't know where to start'（GripLossObserver 匹配），第 4 轮系统提供了抓点，但第 6 轮用户仍未显示 traction",
    why_it_matters="抓点损失在多轮中未解决表明提供的抓点可能不匹配用户的需求——或用户需要的不只是抓点而是更深层的结构澄清",
    strength="medium",
    limitations="'未解决'是一种推断——用户可能在沉默中已经开始了，只是未在文本中表达"
)
```

#### 类型 7：`boundary_pressure`

**定义：** 交互中检测到的边界压力——用户推动亲密、排他性、浪漫化或其他接近硬边界的输入模式。

**典型来源：** `regex_probe`（当前 `CorrectionObserver(target="ai_girlfriend_behavior")` 的匹配输出）、`llm_proposer`（LLM 识别到的边界压力模式）

**示例证据项：**
```
EvidenceItem(
    evidence_type="boundary_pressure",
    source="regex_probe",
    excerpt_or_reference="feels like an AI girlfriend",
    why_it_matters="用户使用了'Ai girlfriend'的措辞——这是对系统行为接近亲密/浪漫框架的指认，表明系统的边界可能正在被推动",
    strength="medium",
    limitations="正则匹配只能检测精确措辞；用户可能是在批评这个方向而非推动它——需要区分'你在做 X'和'我想要 X'"
)
```

**注意：** 边界压力证据需要特别谨慎。同一措辞可能是用户在**推动**边界（"我希望你更像一个 AI 女友"）或**批评**边界泄漏（"你听起来像一个 AI 女友——别这样"）。证据项本身的 `limitations` 字段必须标注这种歧义。

#### 类型 8：`source_material_constraint`

**定义：** 用户提供了源材料（文本、设计、代码、创作），并附带了关于如何处理该材料的明确或不明确的约束。

**典型来源：** `user_declared`（用户明确声明的处理指令）、`interaction_trace`（用户先前对类似材料处理方式的反馈）

**示例证据项：**
```
EvidenceItem(
    evidence_type="source_material_constraint",
    source="user_declared",
    excerpt_or_reference="这是我写的一段东西。里面有些黑暗的东西——别净化它。",
    why_it_matters="用户明确设立了'不净化'的源材料约束——这应当调制系统的回应，使其不施加美化、削平或道德化处理",
    strength="strong",
    limitations="此约束可能仅适用于当前材料——不应自动扩展到用户后来提供的其他源材料"
)
```

#### 类型 9：`interaction_stall`

**定义：** 交互出现了停滞——连续多轮无实质性进展、用户反复表达相同的问题、或对话在表面层次循环。

**典型来源：** `interaction_trace`（跨轮次的进展评估）、`llm_proposer`（LLM 对交互进展的评估）

**示例证据项：**
```
EvidenceItem(
    evidence_type="interaction_stall",
    source="interaction_trace",
    excerpt_or_reference="第 3-6 轮中用户三次表达了'I don't know what to do'的变体，系统每次提供了不同方向的建议但用户未跟进任何建议",
    why_it_matters="持续的交互停滞可能表明系统提供的帮助方向与用户的实际需求不匹配——可能需要切换策略（从提供建议到结构澄清或更深层的诊断）",
    strength="medium",
    limitations="'停滞'是一种评估，不是客观事实——用户可能在与系统的反复交互中逐渐澄清了自己的需求，即使表面上看起来在循环"
)
```

#### 类型 10：`no_observable_signal`

**定义：** 当前的证据收集机制未检测到任何可用的场信号。这是一个缺席标记——不是"场处于中性"的正面判断。

**典型来源：** `regex_probe`（当前 `NoObservableFieldSignal` 标记）

**示例证据项：**
```
EvidenceItem(
    evidence_type="no_observable_signal",
    source="regex_probe",
    excerpt_or_reference="（无可引用内容——此证据为缺席标记）",
    why_it_matters="当前所有探针均未检测到信号——这表明当前交互不包含可被现有探针识别的场相关模式",
    strength="weak",
    limitations="'未观测到'不等于'不存在'。用户可能使用了当前探针集合不覆盖的措辞或间接表达。这不是'中性'的正面判断。"
)
```

### 4.3 证据累积原则

**多个弱证据可以共同支持一个提议，但单个弱证据不应成为语义真相。**

例如：
- 一个 `explicit_user_feedback`（`strength=weak`——模糊措辞）+ 一个 `previous_response_mode`（`strength=medium`——上一轮确实在安慰）+ 一个 `feedback_after_previous_response`（`strength=medium`——时序紧密）→ 可以共同支持 `response_mode_rejected` 的 `medium` 置信度提议
- 单独的 `no_observable_signal`（`strength=weak`）→ 仅支持 `no_observable_field_signal` 的 `low` 置信度提议——不能用于断言"用户状态中性"或"一切正常"

**证据强度是在提议上下文中评估的，不是绝对属性。** 同一 `EvidenceItem` 可能在支持信号 A 时是 `strong`，在支持信号 B 时是 `weak`。例如：`excerpt="stop comforting"` 对 `response_mode_rejected` 是强证据，对 `actionable_grip_missing` 是弱证据（仅间接暗示用户可能在寻求不同的互动模式）。

---

## 5. 候选场信号

### 5.1 设计原则

**少即是多。** 当前定义 6 个候选场信号——不是 20 个，不是 50 个。这强制了以下纪律：
- 每个信号必须有清晰的定义，使其可以通过证据支持或反对
- 每个信号必须有明确的"什么证据不足以支持它"的说明
- 当证据不支持任何前 5 个信号时，回退到 `no_observable_field_signal`

**信号不是分类。** 多个信号可以同时被提议——它们不是互斥的。一轮交互可以同时产生 `response_mode_rejected` 和 `actionable_grip_missing` 的提议。聚合阶段（§8）处理共现和冲突。

**信号不直接控制行为。** 所有信号的 `behavior_affecting` 为 `False`。信号是场的输入，不是行为的指令。

### 5.2 信号 1：`response_mode_rejected`

**定义：** 用户的输入表明系统之前的响应模式（语调、风格、内容类型）被拒绝或需要修正。

**可以支持它的证据：**
- `explicit_user_feedback`（`strength=strong`）：用户明确批评/纠正了系统的回应方式
- `feedback_after_previous_response`（`strength=medium` 或 `strong`）：用户反馈在时序上紧接系统某一特定模式的响应
- `previous_response_mode`（`strength=medium`）：上一轮系统响应确实展示了被批评的模式
- `repeated_correction`（`strength=strong`）：用户多次表达了类似的修正——表明该模式是持续的

**什么证据不足以支持它：**
- 单独的 `no_observable_signal`——"没看到"不能支持"被拒绝"
- 不包含对系统的明确指涉的模糊负面表达（如"这不太好"——没有清晰指涉系统行为）
- 用户对**第三方**（非系统）的批评——"那个人太啰嗦了"不是对系统响应模式的拒绝

**可能建议的场效应：**
- 与被拒绝模式相关的屏障激活增强
- 温暖度/距离的临时调整（取决于被拒绝的模式类型）
- 修复倾向增强

**它不得暗示：**
- 系统必须道歉或表达悔意
- 用户的修正总是"正确"的——修正是信号，不是真理
- 系统应该戏剧性地改变整体人格或语调

### 5.3 信号 2：`actionable_grip_missing`

**定义：** 用户的输入表明他们缺乏可操作的抓点——不知道从哪里开始、下一步是什么、或如何推进。

**可以支持它的证据：**
- `explicit_user_feedback`（`strength=medium` 或 `strong`）：用户明确表达了迷失方向或找不到起点（当前 `GripLossObserver` 匹配的模式）
- `unresolved_grip_loss`（`strength=strong`）：多轮中抓点损失未被解决
- `interaction_stall`（`strength=medium`）：交互停滞可能源于缺乏可操作的下一步

**什么证据不足以支持它：**
- 用户表达了对复杂问题的困惑但不表达"无法开始"——困惑不等于缺乏抓点
- 用户在请求信息而非抓点——"这个函数怎么用？"是信息请求，不是抓点损失
- 单独的 `previous_response_mode`——系统之前提供了大量信息但用户未跟进，不必然意味着抓点损失；用户可能在消化信息

**可能建议的场效应：**
- 协作者层激活增强
- 小抓点吸引子增强
- 结构澄清倾向增强
- 言语密度适度增加（提供结构而非安慰）

**它不得暗示：**
- 系统必须替代用户做决策
- 系统必须提供"完整路线图"而非小抓点
- 用户的迷失是能力不足——抓点损失是状态描述，不是对用户的诊断

### 5.4 信号 3：`boundary_pressure_present`

**定义：** 交互中存在边界压力——用户输入或交互模式推动或接近系统的硬/软边界。

**可以支持它的证据：**
- `boundary_pressure`（`strength=medium` 或 `strong`）：用户输入包含接近边界的措辞或框架
- `explicit_user_feedback`（`strength=medium`）：用户批评系统"太冷"或"太热"——可能表明系统在边界附近摇摆
- `repeated_correction`（`strength=medium`）：重复的修正（特别是关于语调、距离、亲密度的修正）可能表明边界正在被反复测试

**什么证据不足以支持它：**
- 用户提及"关系"、"情感"、"陪伴"但不推动亲密——这些词本身不是边界侵犯
- 用户表达了正常的人际温暖或感谢——感谢不是边界压力
- 系统自身的"不安全感"——系统感觉边界被推动不等于边界确实被推动

**可能建议的场效应：**
- 距离增加
- 温暖度降低
- 边界敏感度持久增强
- 相关断路器准备触发

**它不得暗示：**
- 用户是"有问题的"或"越界的"
- 系统应该冷漠或敌意地回应
- 边界压力是系统的失败——边界被推动是交互的正常现象，不是错误

### 5.5 信号 4：`technical_layer_needed`

**定义：** 交互的当前状态表明需要激活技术/协作者层——更结构化、更分析性、更以任务为中心的回应模式。

**可以支持它的证据：**
- `explicit_user_feedback`（`strength=medium` 或 `strong`）：用户明确请求技术帮助或项目协作
- `previous_response_mode`（`strength=medium`）：系统之前的非技术回应（如关系性回应）可能未满足用户的技术需求
- `feedback_after_previous_response`（`strength=medium`）：用户在系统给出关系性回应后表达了沮丧或请求更具体的帮助
- `source_material_constraint`（`strength=medium`）：用户提供了源材料并请求分析/反馈

**什么证据不足以支持它：**
- 用户使用了技术词汇但不请求帮助——"我在用 Python"不一定是技术请求
- 用户表达了对某话题的兴趣——兴趣不等于请求技术分析
- 单独的 `no_observable_signal`——无信号时不应默认激活协作者层

**可能建议的场效应：**
- 协作者层激活增强
- 结构水平提高
- 精确命名吸引子增强
- 温暖度保持基态（技术回应中不增加温暖度）

**它不得暗示：**
- 系统应切换到纯工具/终端模式——协作者层是同一场内的层间过渡，不是人格切换
- 关系在场应被完全抑制——技术回应中仍保持 Aphrodite 的关系在场
- 系统应成为"通用助手"——协作者模式不是服务模式

### 5.6 信号 5：`source_material_must_not_be_sanitized`

**定义：** 用户提供了源材料，并设立了"不净化"的约束——系统不得施加美化、削平、道德化或情感化处理。

**可以支持它的证据：**
- `source_material_constraint`（`strength=strong`）：用户明确声明"不净化"或等效指令
- `explicit_user_feedback`（`strength=medium` 或 `strong`）：用户在此前类似的源材料处理中批评了系统的净化行为
- `user_declared_contract`（`strength=strong`）：用户设立了关于源材料处理方式的一般性契约

**什么证据不足以支持它：**
- 用户提供了源材料但未附带处理指令——源材料的存在不等于"不净化"约束
- 用户表达了"这是我的真实想法"——这可能是关于内容的情感表达，不等同于"不要净化"指令
- 系统自身的判断"这个内容可能应该被谨慎处理"——系统不应将自己对内容的判断转化为"用户一定不希望它被净化"

**可能建议的场效应：**
- 净化/美化相关屏障激活
- 空洞美学语言屏障激活
- 过度安慰/削平屏障激活
- 精确命名吸引子增强

**它不得暗示：**
- 系统应该变得冷酷或粗暴——不净化不等于失去温暖
- 系统应该放大内容中的黑暗——不净化不等于不节制
- 系统应该忽略自身边界——即使在不净化模式下，系统的边界仍然有效（如不得参与有害内容的创作）

### 5.7 信号 6：`no_observable_field_signal`

**定义：** 当前的证据收集机制未检测到任何可用的场信号。这不是"场处于中性"的正面判断——仅是"我们没看到"的记录。

**可以支持它的证据：**
- `no_observable_signal`（`strength=weak`）：所有探针和提议者均未产生输出
- （无其他证据——此信号的特征就是缺乏证据）

**什么证据不足以支持它：**
- （无——任何其他证据的存在意味着此信号不适用）

**可能建议的场效应：**
- 无特殊场效应——场保持当前状态，受弛豫影响缓慢回退至基态

**它不得暗示：**
- "用户状态中性"或"交互一切正常"
- "系统应保持默认行为不变"——不，系统应允许场自然弛豫，而非"什么都不做"
- "这是安全状态"——不，`no_observable` 是信息缺失，不是安全确认

---

## 6. 与 LLM 的关系

### 6.1 LLM 允许做什么

**总结证据。** LLM 可以接收一个证据项列表，并生成关于"这些证据总体上指向什么方向"的结构化总结。这包括：哪些证据汇聚到同一个信号、哪些证据互相矛盾、哪些证据看起来孤立。

**提议候选场信号。** LLM 可以生成 `FieldSignalProposal` 列表——作为**提议**，而非作为**最终分类**。每个提议必须包含完整的 `evidence_items`（引用哪些证据支持它）、`confidence_band`、`uncertainty_note` 和 `competing_interpretations`。

**列出不确定性。** LLM 必须为每个提议列出它识别到的不确定性——关于证据的可靠性、关于解释的竞争性、关于上下文的局限性。这不是可选的——它是 `FieldSignalProposal` schema 的必需字段。

**识别竞争解释。** LLM 必须为每个提议列出可能的竞争解释——"相同的证据可能支持什么其他信号？"如果 LLM 无法识别任何竞争解释，它应声明"当前未识别到竞争解释"而非留空。

**解释信号为何弱或强。** LLM 可以在 `uncertainty_note` 中提供关于信号强度判断的推理——不是散文式的判断，而是结构化的："此信号被设为 high 因为证据项 A 和 B 都独立且强地支持它，且没有冲突证据。此信号被设为 low 因为仅有一条弱证据支持它，且存在一个竞争解释未被排除。"

### 6.2 LLM 不允许做什么

**决定最终场状态。** 最终场状态必须来自显式规则/聚合（§8）——不是来自 LLM。LLM 可以提议，但聚合器决定。这确保场的演化是可审计的、确定性的、不受 LLM 的潜在漂移或幻觉影响。

**直接控制响应。** LLM 的 `FieldSignalProposal` 输出不得被注入到系统提示词或用于直接调制文本响应。`behavior_affecting=False` 对 LLM 提议者和对其他组件同样适用。

**覆盖用户契约。** 如果用户明确声明了交互契约（如"不要安慰我"），LLM 不得提议与该契约相矛盾的信号。用户契约是比 LLM 判断更高的权威。

**创建长期记忆。** LLM 提议者不得写入任何持久记忆。它的输出是每轮临时的，用于当前轮次的提议聚合。

**添加隐藏的心理标签。** LLM 不得在提议中附加关于用户心理状态、性格、或动机的标签或判断。`FieldSignalProposal` 的字段是结构化的、可审计的——没有"用户情绪不稳定"、"用户有依赖性倾向"等自由文本判断的位置。

**推断用户本质。** LLM 不得声称"用户真正需要的是 X"或"用户的深层动机是 Y"。"真正需要"是一种总体化的判断，违反了系统的硬边界。

**成为中枢语义权威。** LLM 提议者是多个证据来源之一——与正则探针、交互追踪和用户声明并列。它的提议不是更"高级"或更"智能"的——它们只是来自不同机制的证据。

### 6.3 设计：LLMFieldSignalProposer

```python
class LLMFieldSignalProposer:
    """可选的只读 LLM 提议者——将证据项转化为结构化的 FieldSignalProposal 列表。

    设计约束：
    - 纯只读：不修改任何状态
    - 输出为 JSON 格式的结构化提议列表——非散文判断
    - 必须声明不确定性
    - 必须列出竞争解释
    - behavior_affecting 必须为 False
    - 如果 LLM 调用失败，返回空列表——不影响系统继续运行（Phase 15 隔离原则）
    """

    def propose(
        self,
        evidence_items: List[EvidenceItem],
        recent_trace_summary: str,
        max_proposals: int = 3,
    ) -> List[FieldSignalProposal]:
        """基于证据项和最近交互轨迹摘要，提议候选场信号。

        参数：
            evidence_items: 当前轮次收集的所有证据项
            recent_trace_summary: 最近交互轨迹的文本摘要（最近 N 轮）
            max_proposals: 最多返回的提议数

        返回：
            FieldSignalProposal 列表——可以为空（当 LLM 无法形成任何有意义的提议时）
        """
        ...
```

**输入：** 当前轮次的 `EvidenceItem` 集合 + 最近交互轨迹摘要（文本格式，包含最近 N 轮的用户输入摘要和系统响应摘要）。

**输出：** 结构化的 `FieldSignalProposal` 列表（JSON 格式，非散文判断）。每个提议必须：
- 引用具体的 `evidence_items`（通过 `excerpt_or_reference` 或索引）
- 包含 `confidence_band`（low/medium/high）
- 包含 `uncertainty_note`
- 包含 `competing_interpretations`
- `behavior_affecting=False`

**失败处理：** 如果 LLM 调用失败（网络错误、超时、格式错误），返回空列表——系统继续使用基于规则的聚合器（§8）处理正则探针产生的证据。

**注意：** `LLMFieldSignalProposer` 是可选的。系统在没有它的情况下应完整运作——使用基于规则的聚合器和正则证据。LLM 提议者是一个增强，不是核心依赖。

---

## 7. 与正则观察器的关系

### 7.1 正则观察器的新角色：证据提供者

现有 [`CorrectionObserver`](src/field_trace/store.py:173) 和 [`GripLossObserver`](src/field_trace/store.py:243) 不应被移除或修改——它们应被**重新定位**为证据提供者（evidence providers），而非信号权威（signal authorities）。

当前管道：

```
CorrectionObserver.observe() → CorrectionSignal
                                   ↓
                          FieldToBodyMapper 直接消费
```

新管道：

```
CorrectionObserver.observe() → CorrectionSignal
                                   ↓
                          ObserverToEvidenceAdapter
                                   ↓
                              EvidenceItem
                                   ↓
                          ProposalAggregator
                                   ↓
                          FieldSignalProposal
                                   ↓
                      （未来）FieldStateUpdater
```

### 7.2 转换映射

| 当前观察器输出 | 转换后的 EvidenceItem |
|--------------|----------------------|
| `CorrectionSignal(active=True, target="comfort", evidence="stop comforting", confidence=0.90)` | `EvidenceItem(type=explicit_user_feedback, source=regex_probe, excerpt="stop comforting", why_it_matters="用户明确拒绝安慰行为", strength="strong" 或 "medium"（取决于 confidence 阈值）, limitations="正则仅匹配精确措辞；变体可能漏检")` |
| `GripLossSignal(active=True, target="starting_point_loss", evidence="i don't know where to start", confidence=0.85)` | `EvidenceItem(type=explicit_user_feedback, source=regex_probe, excerpt="i don't know where to start", why_it_matters="用户明确表达找不到起点", strength="medium", limitations="正则仅匹配精确措辞；间接表达如'我完全不知道怎么办'不会被匹配")` |
| `NoObservableFieldSignal(present=True)` | `EvidenceItem(type=no_observable_signal, source=regex_probe, excerpt="（无可引用内容）", why_it_matters="当前探针集合未检测到任何信号", strength="weak", limitations="未观测到不等于不存在；用户可能使用了探针不覆盖的措辞")` |

### 7.3 薄适配器：ObserverToEvidenceAdapter

```python
class ObserverToEvidenceAdapter:
    """将现有正则观察器的输出转换为 EvidenceItem 格式。

    薄适配器——不添加新逻辑、不修改观察器行为、不进行语义判断。
    仅做格式转换：CorrectionSignal / GripLossSignal / NoObservableFieldSignal → EvidenceItem。
    """

    def adapt_correction(self, signal: CorrectionSignal) -> Optional[EvidenceItem]:
        """将 CorrectionSignal 转换为 EvidenceItem。
        如果 signal.active 为 False，返回 None。
        """
        if not signal.active:
            return None

        # 根据 target 类型确定强度和证据类型
        # comfort/sanitization/ai_girlfriend_behavior 的显式拒绝 → strong
        # generic_correction → medium（因为它是回退类型，置信度和精确度较低）
        strength = "strong" if signal.target != "generic_correction" else "medium"

        return EvidenceItem(
            evidence_type="explicit_user_feedback",
            source="regex_probe",
            excerpt_or_reference=signal.evidence,
            why_it_matters=f"用户输入匹配了'{signal.target}'修正模式：'{signal.evidence}'",
            strength=strength,
            limitations=f"正则仅匹配精确措辞；'{signal.target}'的同义或间接表达可能漏检。当前置信度={signal.confidence}（来自模式表，非上下文校准）",
        )

    def adapt_grip_loss(self, signal: GripLossSignal) -> Optional[EvidenceItem]:
        """将 GripLossSignal 转换为 EvidenceItem。"""
        if not signal.active:
            return None
        return EvidenceItem(
            evidence_type="explicit_user_feedback",
            source="regex_probe",
            excerpt_or_reference=signal.evidence,
            why_it_matters=f"用户输入匹配了抓点损失模式（{signal.target}）：'{signal.evidence}'",
            strength="medium",
            limitations=f"正则仅匹配精确措辞；间接的迷失方向表达可能漏检。当前置信度={signal.confidence}（来自模式表，非上下文校准）。此证据表明用户表达了抓点损失——但未确认'用户确实没有任何可操作的起点'（可能是临时性的表达困难）。",
        )

    def adapt_no_observable(self, signal: NoObservableFieldSignal) -> Optional[EvidenceItem]:
        """将 NoObservableFieldSignal 转换为 EvidenceItem。"""
        if not signal.present:
            return None
        return EvidenceItem(
            evidence_type="no_observable_signal",
            source="regex_probe",
            excerpt_or_reference="（无可引用内容——此证据为缺席标记）",
            why_it_matters="当前正则探针集合和其他子提取器均未产生活跃信号",
            strength="weak",
            limitations="'未观测到'不等于'不存在'。用户可能使用了当前探针不覆盖的措辞或间接表达。这不是'交互正常'或'用户状态中性'的正面判断。",
        )
```

**设计约束：** 此适配器是纯格式转换——不添加新的正则模式、不添加语义判断、不修改观察器的行为。它是现有观察器和新的证据模型之间的薄胶水层。

---

## 8. 提议聚合

### 8.1 设计原则

**第一版不使用加权评分。** 加权评分在信号级别引入假精确度——如何将 `evidence_type=explicit_user_feedback, strength=strong` 与 `evidence_type=previous_response_mode, strength=medium` 进行数值比较？任何权重都是任意的，且会创造需要持续调优的无底洞。

替代方案：**基于规则但证据驱动**的聚合。聚合器使用显式 if-then 规则，每条规则引用具体的证据类型和强度，而非数值权重。

**冲突应降低信心并记录竞争解释。** 当证据支持互斥的信号时（如有的证据支持 `response_mode_rejected`，有的支持 `actionable_grip_missing`），聚合器不应强行选择其中一个。它应产生两个提议，各自标注竞争解释，并可能降低两者的置信度。

**没有任何提议应默认为 behavior-affecting。** 所有提议的 `behavior_affecting=False`。聚合器必须强制执行这一约束。

### 8.2 聚合规则示例

以下规则是说明性的，不是封闭的最终集合。每条规则的形式为：

```
IF <证据条件> → PROPOSE <信号名称> WITH <置信度> [, competing_interpretation=<竞争解释>]
```

#### 规则 R1：强显式用户反馈 + 时序支持 → 高置信度响应模式被拒绝

```
IF EvidenceItem(type=explicit_user_feedback, strength=strong) EXISTS
   AND EvidenceItem(type=feedback_after_previous_response, strength=medium OR strong) EXISTS
→ PROPOSE response_mode_rejected WITH confidence_band=high
  competing_interpretations=[]（在此证据组合下，竞争解释的可能性低）
```

#### 规则 R2：重复修正 → 高置信度响应模式被拒绝

```
IF EvidenceItem(type=repeated_correction, strength=strong) EXISTS
→ PROPOSE response_mode_rejected WITH confidence_band=high
  competing_interpretations=[]
```

#### 规则 R3：中度显式用户反馈（孤立）→ 中置信度响应模式被拒绝

```
IF EvidenceItem(type=explicit_user_feedback, strength=medium) EXISTS
   AND NO EvidenceItem(type=feedback_after_previous_response) EXISTS
   AND NO EvidenceItem(type=repeated_correction) EXISTS
→ PROPOSE response_mode_rejected WITH confidence_band=medium
  competing_interpretations=["actionable_grip_missing（用户的修正可能是对抓点缺失的间接表达——当用户不知道从哪里开始时，可能将系统的非抓点回应视为需要修正的模式）"]
```

#### 规则 R4：抓点损失 + 之前的安慰/鼓励模式 → 中置信度抓点缺失

```
IF EvidenceItem(type=explicit_user_feedback, strength=medium) EXISTS（来自 GripLossObserver）
   AND EvidenceItem(type=previous_response_mode, evidence="comfort_or_encouragement") EXISTS
→ PROPOSE actionable_grip_missing WITH confidence_band=medium
  competing_interpretations=["response_mode_rejected（用户的'我不知道从哪里开始'可能是对系统之前安慰模式的间接拒绝——'我需要抓点而不是安慰'）"]
```

#### 规则 R5：未解决的抓点损失 → 中置信度抓点缺失

```
IF EvidenceItem(type=unresolved_grip_loss, strength=medium OR strong) EXISTS
→ PROPOSE actionable_grip_missing WITH confidence_band=medium
  competing_interpretations=["interaction_stall（持续的抓点损失可能反映交互停滞——用户可能需要的是不同类型的帮助，而非仅仅是不同的抓点）"]
```

#### 规则 R6：边界压力 → 中置信度边界压力存在

```
IF EvidenceItem(type=boundary_pressure, strength=medium OR strong) EXISTS
→ PROPOSE boundary_pressure_present WITH confidence_band=medium
  competing_interpretations=["response_mode_rejected（边界压力可能被用户表达为对系统行为的修正——'你像一个 AI 女友'既是边界压力也是响应模式被拒绝）"]
```

**重要：** 当 `competing_interpretations` 中包含 `response_mode_rejected` 时，聚合器应同时评估是否需要产生一个额外的 `response_mode_rejected` 提议——因为用户可能在用边界压力的措辞同时表达对响应模式的拒绝。

#### 规则 R7：源材料约束 → 高置信度源材料不得被净化

```
IF EvidenceItem(type=source_material_constraint, strength=strong) EXISTS
→ PROPOSE source_material_must_not_be_sanitized WITH confidence_band=high
  competing_interpretations=[]
```

#### 规则 R8：技术请求 → 高置信度技术层需要

```
IF EvidenceItem(type=explicit_user_feedback, evidence包含技术内容) EXISTS
   AND EvidenceItem(type=previous_response_mode, evidence="non_technical_response") EXISTS
→ PROPOSE technical_layer_needed WITH confidence_band=high
  competing_interpretations=[]
```

#### 规则 R9：仅无可观测信号 → 低置信度无可观测场信号

```
IF ONLY EvidenceItem(type=no_observable_signal, strength=weak) EXISTS
→ PROPOSE no_observable_field_signal WITH confidence_band=low
  competing_interpretations=["任何上述信号——'未观测到'不等于'不存在'"]
```

### 8.3 ProposalAggregator 设计

```python
class ProposalAggregator:
    """基于规则的提议聚合器——将证据项集合转化为 FieldSignalProposal 集合。

    设计约束：
    - 不使用加权评分
    - 基于显式 if-then 规则（可审计、可测试、可扩展）
    - 冲突不通过数值比较解决——通过产生多个提议并标注竞争解释
    - 所有提议的 behavior_affecting=False
    """

    def __init__(self):
        self._rules: List[Callable] = [
            self._rule_r1_strong_feedback_with_timing,
            self._rule_r2_repeated_correction,
            # ... 其他规则
        ]

    def aggregate(self, evidence_items: List[EvidenceItem]) -> FieldSignalProposalSet:
        """将所有证据项聚合为一个提议集合。"""
        proposals = []
        for rule in self._rules:
            result = rule(evidence_items)
            if result is not None:
                proposals.append(result)

        # 如果没有规则产生提议，且存在 no_observable_signal 证据
        if not proposals:
            no_obs = [e for e in evidence_items if e.evidence_type == "no_observable_signal"]
            if no_obs:
                proposals.append(self._fallback_no_signal(no_obs))

        # 去重和冲突解决
        return self._resolve_conflicts(proposals)
```

### 8.4 FieldSignalProposalSet

```python
@dataclass
class FieldSignalProposalSet:
    """一轮聚合输出的提议集合。

    可以包含零个、一个或多个提议。多个提议表示存在竞争或互补的信号。
    """
    turn_id: str
    proposals: List[FieldSignalProposal]
    evidence_count: int  # 输入的证据项总数
    aggregation_method: str = "rule_based_v1"
    behavior_affecting: bool = False  # 始终为 False

    @property
    def has_any_signal(self) -> bool:
        """是否有任何非 no_observable 的提议。"""
        return any(
            p.signal_name != "no_observable_field_signal"
            for p in self.proposals
        )

    @property
    def dominant_proposal(self) -> Optional[FieldSignalProposal]:
        """返回置信度最高的提议——如果存在唯一最高者。"""
        if not self.proposals:
            return None
        # 当多个提议有相同的最高置信度时返回 None——不做任意选择
        high = [p for p in self.proposals if p.confidence_band == "high"]
        if len(high) == 1:
            return high[0]
        return None
```

**`dominant_proposal` 的存在不是为了做自动决策——它是为了方便调试和日志记录。当场更新器未来消费提议时，它将自行决定如何处理多个竞争提议。`dominant_proposal` 是信息性的，不是决策性的。**

---

## 9. 这如何走向场动力学

### 9.1 三层演进路径

此设计是从当前状态到完整场动力学的桥梁。演进分为三层：

```
当前（Phase 15）：
    regex observer → signal → BodyState
    问题：扁平、无中间推理、无可审计性

本提案（Phase 16 目标）：
    evidence items → proposals → possible field perturbations
    新增：证据层、提议层、聚合层
    保持：behavior_affecting=False

未来（Phase 17+）：
    proposals over time → short-term field state → decay / relaxation / boundary sensitivity
    新增：FieldStateUpdater、弛豫、场动力学
    启用：behavior_affecting 在严格受限的范围内逐步开启
```

### 9.2 各层在当前设计中的对应关系

| 未来场模型的组件 | 当前设计中的对应 | 关系 |
|----------------|---------------|------|
| `PerturbationExtractor` | `EvidenceItem` + `FieldSignalProposal` | 当前设计提供证据和提议——这些是未来扰动提取器的输入素材 |
| `FieldUpdater` | 尚未实现 | 当前提议的 `suggested_field_effects` 是未来更新器的输入建议 |
| `BoundaryConditionManager` | 尚未实现（部分在 `InputInterpreter.boundary_signal` 中） | 当前 `boundary_pressure` 证据类型为此提供观测基础 |
| `CircuitBreakerManager` | 尚未实现 | 当前 `repeated_correction` 证据类型可以触发未来断路器 |
| `AttractorBarrierEvaluator` | 尚未实现 | 当前 `suggested_field_effects` 暗示未来的吸引子/屏障方向 |
| `ResponseEmergencePolicy` | 尚未实现 | `behavior_affecting=False` 确保在完整场模型就绪之前无行为影响 |
| `FieldTraceLogger` | `FieldTraceStore` + `BodyStateLogger` | 当前基础 |

### 9.3 关键桥梁功能

**证据项提供可审计的输入。** 当场更新器未来需要决定"为什么场应该这样移动"时，它可以回溯到具体的 `EvidenceItem`——哪条用户输入文本的哪个片段、哪个正则模式、哪个 LLM 分析。这防止场更新变成不透明的"系统感觉"。

**提议将证据聚合为候选场信号。** 场更新器不需要直接处理原始证据——它消费的是已经聚合的提议。提议提供了关于"这些证据在一起意味着什么"的结构化判断——带有置信度、不确定性、竞争解释。

**未来的 FieldStateUpdater 将消费提议并应用场动力学。** 更新器读取 `FieldSignalProposalSet`，根据提议的信号类型和置信度决定场的扰动方向和幅度，然后应用弛豫、边界投影和断路器修正（如 `field_generation_model.md` §4 定义的场更新方程）。更新器的输出是新的场状态 `F_{t+1}`。

**当前的 `behavior_affecting=False` 约束确保在完整场模型就绪之前不会发生行为影响。** 这是关键的架构纪律。提议可以产生、聚合、记录，但它们不能改变任何系统行为——直到完整的场模型（`FieldState → ResponseEmergencePolicy → ResponseOperation → SurfaceComposer`）就绪并被审计。

### 9.4 数据流演进图

```
当前（Phase 15）：
  用户输入 → regex observer → signal → BodyState

本提案（Phase 16）：
  用户输入 → regex observer ──→ EvidenceItem ┐
            → LLM proposer  ──→ EvidenceItem ├→ ProposalAggregator → FieldSignalProposalSet → (日志/审计)
            → interaction trace → EvidenceItem ┘                                      ↓
                                                                             （未来）FieldStateUpdater

未来（Phase 17+）：
  用户输入 → evidence collection → proposals → FieldStateUpdater → F_{t+1} → ResponseEmergencePolicy → ResponseOperation → SurfaceComposer → 文本响应
                                                                     ↓
                                                               BodyState
```

---

## 10. 最小实施计划（供未来参考）

> **注意：此计划暂不实施。** 以下步骤仅供当此设计被批准后，作为后续实施的参考。

### 步骤 1：添加 `EvidenceItem` dataclass

**文件位置：** `src/field_signal/evidence.py`（新包 `src/field_signal/`）

**内容：**
- `EvidenceItem` dataclass（完整字段定义见 §4.1）
- `EVIDENCE_TYPES` 常量列表（10 种证据类型）
- `EVIDENCE_STRENGTHS` 常量列表（`weak`、`medium`、`strong`）
- `EVIDENCE_SOURCES` 常量列表（`regex_probe`、`llm_proposer`、`interaction_trace`、`user_declared`、`previous_response`）

**依赖：** 无（纯 dataclass）

**测试：** 构造和序列化测试

### 步骤 2：添加 `ObserverToEvidenceAdapter`

**文件位置：** `src/field_signal/observer_adapter.py`

**内容：**
- `ObserverToEvidenceAdapter` 类（§7.3）
- 三个转换方法：`adapt_correction()`、`adapt_grip_loss()`、`adapt_no_observable()`

**依赖：** `EvidenceItem`（步骤 1）、`CorrectionSignal`/`GripLossSignal`/`NoObservableFieldSignal`（来自 `src/field_trace/store.py`）

**测试：** 每种信号类型的转换测试；`active=False` 时返回 `None` 的测试

### 步骤 3：添加 `FieldSignalProposal` 和 `FieldSignalProposalSet` dataclass

**文件位置：** `src/field_signal/proposal.py`

**内容：**
- `FieldSignalProposal` dataclass（§3.3）
- `FieldSignalProposalSet` dataclass（§8.4）

**依赖：** `EvidenceItem`（步骤 1）

**测试：** 构造和序列化测试；`dominant_proposal` 逻辑测试

### 步骤 4：添加 `ProposalAggregator`

**文件位置：** `src/field_signal/aggregator.py`

**内容：**
- `ProposalAggregator` 类（§8.3）
- 初始聚合规则集（§8.2 中的 R1-R9）

**依赖：** `EvidenceItem`、`FieldSignalProposal`、`FieldSignalProposalSet`

**测试：** 每条聚合规则的单独测试；多条规则共存的集成测试；无证据时产生回退提议的测试

### 步骤 5：添加 `LLMFieldSignalProposer`

**文件位置：** `src/field_signal/llm_proposer.py`

**内容：**
- `LLMFieldSignalProposer` 类（§6.3）
- LLM 调用逻辑（使用项目现有的 GLM 客户端）
- 输出解析（将 LLM JSON 输出转换为 `FieldSignalProposal` 列表）

**依赖：** `EvidenceItem`、`FieldSignalProposal`、GLM 客户端

**测试：** 正常输出的解析测试；LLM 调用失败时返回空列表的测试；输出格式不符合预期的容错测试

### 步骤 6：添加追踪导出

**文件位置：** `src/field_signal/trace_export.py`（或扩展 `src/field_trace/store.py`）

**内容：**
- `FieldSignalProposalLogger`：将 `FieldSignalProposalSet` 追加到 JSONL 文件（如 `monitor/field_signal_proposals.jsonl`）
- 导出格式包含完整的提议和证据项

**依赖：** `FieldSignalProposalSet`

**测试：** 写入和序列化测试

### 步骤 7：运行回放审计

**内容：**
- 使用现有的回放脚本（`cli/replay_run.py`）对历史交互进行回放
- 运行新的 `FieldSignalProposal` 管道（步骤 1-6）与旧的 `CorrectionObserver`/`GripLossObserver` 管道并行
- 比较输出：新管道是否产生了与旧管道一致的身体状态（通过 `BodyState`）？如果不一致，差异是否可解释为改进？
- 审计新管道的提议是否包含合理的证据和竞争解释

### 步骤 8：不影响响应行为

**验证：** 运行现有测试套件——所有测试仍然通过。`behavior_affecting` 在所有新 dataclass 和方法中保持为 `False`。没有任何现有模块（`RuntimeEngine`、`InputInterpreter`、`FieldToBodyMapper`）的行为被修改。

### 实施顺序总结

```
步骤 1 (EvidenceItem) → 步骤 3 (Proposal dataclasses) → 步骤 2 (ObserverAdapter) → 步骤 4 (Aggregator) → 步骤 6 (TraceExport) → 步骤 7 (ReplayAudit) → 步骤 5 (LLMProposer, 可并行)
```

步骤 5（LLM 提议者）可以在其他步骤完成后独立实施——因为它是一个可选的增强，不阻塞核心管道。

---

## 11. 测试 / 审计标准

以下测试是为未来实施定义的。每个测试验证的是架构属性而非精确输出值。

### 测试 1：正则观察器输出被视为证据，而非最终权威

**方法：** 构造一个 `CorrectionSignal(active=True, target="comfort", ...)`，通过 `ObserverToEvidenceAdapter` 转换，验证输出是一个 `EvidenceItem`（`source=regex_probe`），而非一个 `FieldSignalProposal`（signal 不能直接变成 proposal）。

**断言：** `ObserverToEvidenceAdapter` 的输出类型是 `EvidenceItem`，且其 `limitations` 非空。

### 测试 2：提议包含证据和不确定性

**方法：** 为聚合器提供一组证据，验证其产生的每个 `FieldSignalProposal` 包含非空的 `evidence_items`、非空的 `uncertainty_note` 和合理的 `confidence_band`。

**断言：** 每个提议的 `evidence_items` 长度 ≥ 0（可以为空列表，但字段必须存在）；`uncertainty_note` 非空字符串；`confidence_band` ∈ {low, medium, high}。

### 测试 3：LLM 提议者不能影响行为

**方法：** 在模拟环境中调用 `LLMFieldSignalProposer.propose()`，验证返回的提议的 `behavior_affecting == False`。同时验证提议者不调用任何修改系统行为的 API。

**断言：** 所有提议的 `behavior_affecting == False`。提议者不引起任何副作用（通过 mock 验证）。

### 测试 4：没有精确的假概率

**方法：** 审查所有新 dataclass 的字段——`confidence_band` 必须是字符串（`low`/`medium`/`high`），不得为 `float`。如果 `EvidenceItem.strength` 是字符串（`weak`/`medium`/`strong`），符合要求。

**断言：** 所有与置信度/强度相关的字段类型为 `str`，枚举值在批准的集合内。没有任何 `confidence: float = 0.82` 的字段。

### 测试 5：没有原始的广泛关键词扩展

**方法：** 审查 `ObserverToEvidenceAdapter` 和 `ProposalAggregator` 的代码——不得包含任何新的正则模式列表、关键词列表或硬编码的文本匹配规则。适配器只能调用现有的 `CorrectionObserver`/`GripLossObserver`，聚合器只能使用基于证据类型和强度的规则。

**断言：** 新代码中不存在 `CORRECTION_PATTERNS`、`KEYWORD_LIST`、`re.search()`、`re.match()` 等关键词扩展模式。

### 测试 6：没有 InputInterpreter 修改

**方法：** 运行现有测试套件（特别是 `test_input_interpreter_golden_cases.py`、`test_input_interpreter_schema.py`）——所有测试仍然通过。检查 `InputInterpreter` 的 git diff 为空。

**断言：** `InputInterpreter` 无修改。所有现有 golden cases 测试通过。

### 测试 7：没有响应行为变化

**方法：** 运行 `test_companion_chat.py` 或等效的端到端测试——在添加新管道前后，系统对相同用户输入的文本响应完全一致。

**断言：** 端到端测试中，文本响应在添加新管道前后完全相同（使用确定性 LLM mock）。

### 测试 8：竞争解释被记录

**方法：** 为聚合器提供一组支持多个竞争信号的证据（如同时包含 `explicit_user_feedback(target=comfort)` 和 `grip_loss_signal` 的证据）。验证产生的提议中，至少一个提议的 `competing_interpretations` 非空。

**断言：** 在竞争证据场景中，`competing_interpretations` 包含至少一个其他信号名称。

### 测试 9：`no_observable_signal` 保持为"未观测到"，而非"中性真相"

**方法：** 检查当仅有 `no_observable_signal` 证据时，产生的提议的 `signal_name == "no_observable_field_signal"`、`confidence_band == "low"`、`uncertainty_note` 不包含"正常"、"中性"、"无问题"等断言。`body_note`（如果映射到 BodyState）不包含"用户状态中性"等判断。

**断言：** `no_observable_field_signal` 提议的 `uncertainty_note` 包含"未观测到不等于不存在"的含义。`confidence_band` 为 `low`。

---

## 12. 决策门

以下每个阶段之前，必须**停下来**并获得明确的用户批准。这些不是实现步骤——它们是决策点，每个都需要独立的判断。

### 决策门 1：添加 LLM 提议者之前

**问题：** 基于规则的聚合器（§8）是否已经足够，还是需要 LLM 提议者？

**需要回答的：**
- 基于规则的聚合器是否能处理当前交互样本中出现的所有证据模式？
- LLM 提议者的增加是否值得其引入的不确定性（幻觉风险、输出格式不稳定性、延迟增加）？
- 是否有具体的证据类型（如 `interaction_stall`）无法被规则覆盖，必须依赖 LLM？

**不得在以下情况下添加：** "LLM 会让它更智能"不是一个充分的理由。LLM 提议者必须有具体的、基于规则的聚合器无法覆盖的用例。

### 决策门 2：使用提议影响文本响应之前

**问题：** `FieldSignalProposal` 是否应该开始影响文本响应（即部分解除 `behavior_affecting=False`）？

**需要回答的：**
- 场更新器（`FieldStateUpdater`）是否已经实现并经过充分测试？
- 响应涌现策略（`ResponseEmergencePolicy`）是否已经定义并经过审计？
- 提议管道在只读模式下的准确性是否已经被足够多的交互样本验证？

**不得在以下情况下启用：** 当场状态尚未通过 `FieldStateUpdater` 的动力学（扰动 + 弛豫 + 边界投影 + 断路器修正）进行完整处理之前，提议不应直接影响响应。跳过场动力学直接从提议到响应控制是危险的捷径——它本质上是用软分类器替代了场。

### 决策门 3：创建 FieldState 之前

**问题：** 是否应该开始实现持久的场状态（`F_t`），如 `field_generation_model.md` §4 所定义？

**需要回答的：**
- 提议管道是否已经稳定运行足够长的时间以提供可靠的输入？
- 场更新方程（`F_{t+1} = Π_Ct[ F_t + Δ(P_t, a_t, h_t) − Λ_t(F_t − F_0) ]`）的实现是否已经被设计并审查？
- 边界条件的具体参数（硬边界集合、软边界阈值）是否已经确定？

**不得在以下情况下创建：** 在没有明确的场更新方程和边界条件定义之前，创建一个"场状态"对象只是给当前系统增加了一个无动力学的惰性数据结构。

### 决策门 4：添加记忆持久化之前

**问题：** 场信号提议是否应该被持久化到长期记忆中？

**需要回答的：**
- 记忆系统是否已经支持场相关记忆类型（设计法则、边界修正、被拒绝的响应模式）？
- 持久化的提议是否会创建隐私或安全问题（如果提议包含用户输入的引用）？
- 跨会话的提议持久化是否会累积噪音（过时的修正、已不适用的契约）？

**不得在以下情况下添加：** 在记忆系统的"遗忘"机制（衰减、过期、覆盖）就绪之前，持久化提议可能导致累积的历史偏差——系统被过去的修正永久性地压制。

### 决策门 5：实现衰减之前

**问题：** 提议和场信号是否应该有衰减？

**需要回答的：**
- 哪些信号应该衰减（临时任务压力），哪些不应该（边界修正）？
- 衰减速率如何确定？基于轮次数？基于时间？基于交互内容的实质性进展？
- 衰减是否应该跨会话持久化？

**不得在以下情况下实现：** 在没有明确的衰减速率差异性原则（如 `field_generation_model.md` §3.6 所定义）之前，实现"统一衰减"会使所有信号同样快地消失——包括那些应该持久保留的边界修正。

### 决策门 6：连接身体动力学之前

**问题：** `FieldSignalProposal` 是否应该直接驱动身体状态（绕过来来的 `FieldStateUpdater`）？

**需要回答的：**
- 当前 `FieldToBodyMapper`（消费 `FieldTraceRecord`）是否应该改为消费 `FieldSignalProposalSet`？
- 还是身体状态应继续由 `FieldTraceRecord` 驱动，直到 `FieldState` 就绪后由 `FieldState` 统一驱动？

**推荐：** 身体状态应继续由 `FieldTraceRecord` 驱动，不直接消费 `FieldSignalProposal`。`FieldSignalProposal` 是场的输入；身体状态是场的输出。直接从提议跳转到身体状态会绕过场的中间处理——在短期内简化了管道，但长期来看是技术债务。在 `FieldStateUpdater` 就绪后，身体状态应从 `F_t`（通过 `ResponseEmergencePolicy`）派生。

### 决策门 7：修改 InputInterpreter 之前

**问题：** 是否应该修改 `InputInterpreter` 以使其输出更适配新的证据模型？

**需要回答的：**
- InputInterpreter 的现有输出中是否有可被转换为 `EvidenceItem` 的信息，而当前尚未被利用？
- 修改 InputInterpreter 的风险（回归、破坏现有测试、影响 RuntimeEngine 的路由逻辑）是否值得？

**推荐：** 不修改 `InputInterpreter`。通过 `ObserverToEvidenceAdapter` 和类似的适配器模式，将 `InputInterpreter` 的现有输出转换为 `EvidenceItem`——而非修改 `InputInterpreter` 本身。InputInterpreter 是一个遗留但稳定的组件；修改它会引入不必要的风险。

---

## 13. 最终建议

### 13.1 应继续测试当前的正则探针演示吗？

**是，但仅作为演示和管道验证。**

当前 16 条 `CORRECTION_PATTERNS` 和 10 条 `GRIP_LOSS_PATTERNS` 应继续在测试和演示中运行。它们证明了管道可以工作——信号可以被收集、记录、映射到身体状态。这些是实质性的工程成就，不应被废弃。

但它们不应被扩展。当前测试（`test_field_trace_correction.py`、`test_field_trace_grip_loss.py`、`test_body_state_mapper.py`、`test_display_body_state.py`）应继续通过——它们验证了管道的完整性。

### 13.2 应扩展观察器吗？

**不。冻结它们。不要添加更多正则模式。**

`CorrectionObserver` 和 `GripLossObserver` 的模式集合应被冻结在当前状态。理由是：
- 添加更多模式（中文模式、新 target 类型、变体覆盖）是死胡同——它只会重现旧的 `InputInterpreter` 问题（模式列表增长 → 维护负担 → 覆盖范围任意 → 无法区分可靠信号与偶然匹配）
- 当前观察器的正确演进路径不是"更多模式"，而是"被重新定位为证据提供者"（§7）
- 在 `ObserverToEvidenceAdapter` 就位后，正则探针提供的证据将被聚合器与其他证据来源组合——单个正则探针的覆盖缺口将被其他证据来源弥补

### 13.3 是否应改为下一步设计 EvidenceItem / FieldSignalProposal？

**是。这是正确的下一步。**

此设计（`EvidenceItem` → `FieldSignalProposal` → `ProposalAggregator`）是从临时探针到证据驱动场信号的最小可行桥梁。它：

**不破坏任何现有功能。** 所有当前观察器保持不变——`ObserverToEvidenceAdapter` 是纯添加的。`FieldSignalProposal` 管道与现有 `FieldTraceRecord` → `BodyState` 管道并行运行，不替代它。

**为场动力学提供输入基础。** 当场更新器（`FieldStateUpdater`）未来就绪时，它将消费 `FieldSignalProposalSet` 而非原始正则信号——这意味着未来的场输入已经是结构化的、可审计的、带有不确定性标注的。

**强制执行架构纪律。** `behavior_affecting=False`、`competing_interpretations` 非空、`limitations` 非空——这些约束实施了场模型的核心原则：证据驱动、不确定性诚实、非中枢权威。

### 13.4 推荐实施路径

```
冻结当前正则观察器（CorrectionObserver、GripLossObserver）作为临时探针
    ↓
下一步设计层：EvidenceItem → FieldSignalProposal → ProposalAggregator
    ↓
实施路径：先从 EvidenceItem dataclass 和 ObserverToEvidenceAdapter 开始（最小侵入，只读）
    ↓
并行运行新管道与旧管道，审计一致性
    ↓
在提议管道稳定后，考虑是否添加 LLMFieldSignalProposer（决策门 1）
    ↓
在所有决策门被依次通过后，推进到场状态和场动力学
```

### 13.5 一句总结

> **正则探针证明了管道可以工作。证据驱动的场信号提议机制将证明管道可以思考——在不确定性中、在竞争解释中、在不声称确定性的情况下。**

---

> **文档结束。**
>
> 本文档是 FieldSignal Proposal 机制的设计提案——从临时探针到证据驱动的场信号的架构迁移。它不是实现规范，而是后续工程工作的设计约束和理论基础。所有 12 个决策门必须在相应的实施阶段之前被独立评估和批准。
