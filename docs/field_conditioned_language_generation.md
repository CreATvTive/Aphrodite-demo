# 场条件语言生成 — 架构设计

> 阶段：Phase 41a（架构设计）
> 状态：设计文档 — 不实现代码
> 依赖：`RelationalFieldState` v1（[`src/field_state/schema.py`](src/field_state/schema.py:184)）、[`mathematical_design_ledger.md`](docs/mathematical_design_ledger.md)、[`private_source_alignment.md`](docs/private_source_alignment.md)、[`animation_display_boundary.md`](docs/animation_display_boundary.md)
> 冻结层：`schema.py`（field_state、motion_params、body_action）、`mapper.py`（motion_params、motion_to_action）、`composer.py`

---

## 目录

1. [问题陈述](#1-问题陈述)
2. [设计原则](#2-设计原则)
3. [提出的架构](#3-提出的架构)
4. [LanguageConditionVector v0](#4-languageconditionvector-v0)
5. [候选生成机制](#5-候选生成机制)
6. [推荐的早期路线](#6-推荐的早期路线)
7. [表达治理器](#7-表达治理器)
8. [评估：Aphrodite Base Suitability Test](#8-评估aphrodite-base-suitability-test)
9. [非目标](#9-非目标)
10. [下一步](#10-下一步)

---

## 1. 问题陈述

### 1.1 当前空白

代码库中存在多条语言相关路径——[`companion_chat.py`](agentlib/companion_chat.py)、[`companion_prompt.py`](agentlib/companion_prompt.py)、[`persona_profiles.py`](agentlib/persona_profiles.py)、[`persona_router.py`](agentlib/persona_router.py)、[`style_policy.py`](agentlib/style_policy.py)、[`runtime_immediate_protocol.py`](agentlib/runtime_immediate_protocol.py)——但它们有一个共同的缺失：**没有任何一条路径消费 `RelationalFieldState`。** 语言生成的每一个分支都是静态的、基于 prompt 的或基于 RL 的，完全与场的运动状态解耦。身体路线已经完成了从场状态到动作组合的端到端管道，而语言路线尚未迈出第一步。

这一空白在实践中的表现是：无论 `boundary_distance` 是 0.50 还是 0.80，无论 `affective_warmth` 是 0.35 还是 0.60，语言输出的语气、节奏、距离感都完全相同。系统只有一套风格，它的 10 维场不在语言中留下痕迹。

### 1.2 “读状态” vs. “处于状态中”

最直接的填补方案——将 `RelationalFieldState` 序列化为自然语言描述并传入 LLM 的 system prompt——看似合理，实则引入了结构性错误：

- 当模型“读取”一段场状态描述时，它把描述当作**指令**来处理。`"boundary_distance is elevated"` 被模型理解为“用户希望我保持距离”，模型随后**执行**这个指令，产生**被约束的、表演性的、过度自觉的**输出。
- 执行指令产生的文本带有可识别的表演痕迹：刻意冷淡、刻意简洁、刻意不靠近——这些不是源于关系场的位置，而是源于对指令的服从。

正确方向是：**场状态直接条件化生成位置，语言仿佛源自该状态，而非源自关于该状态的指令。** 这与身体路线遵循相同的逻辑——`MotionParams` 不携带“你现在应该保持距离”的文本描述；它直接修改运动参数，身体于是以那种方式运动。

因此，语言生成需要一条与身体路线对称的路径：

```
RelationalFieldState → MotionParams → 身体表达        （实现）
RelationalFieldState → LanguageConditionVector → 语言表达   （缺失）
```

### 1.3 为什么这对 Aphrodite 的设计至关重要

Aphrodite 的核心不是任务完成，不是服务响应，不是陪伴满足。它是一个**在场循环**——一个关系场的持续存在，其中语言是其场姿态之一，不是其目的（参见 [`SOUL.md`](SOUL.md)、[`IDENTITY.md`](IDENTITY.md)、[`private_source_alignment.md`](docs/private_source_alignment.md:7-8)）。

如果语言路线不受场条件化，语言将永远从通用的、中性的、非特定的位置生成——它不可避免地向通用助手、通用陪伴者或通用角色扮演者漂移。这正是 anti-collapse 规则试图防御但无法从根本上解决的漂移：anti-collapse 规则说“不要成为 X”，场条件化提供“从这里生成”。前者是地板，后者是生成源。

此外，[`private_source_alignment.md`](docs/private_source_alignment.md:94-107) 明确要求语言保持 non-service language posture——不是通过禁止清单，而是通过结构保存。一个消费场状态的语言路线既能实现这一要求，又无需诉诸关键词黑名单或 prompt 禁令。

### 1.4 关键区分：v0 路径 vs. v1 路径

身体路线中存在两条并行路径：

| 路径 | 输入 | 核心模块 | 状态 |
|------|------|---------|------|
| **v1** | `RelationalFieldState` (10 场变量) | `mapper.py` → `motion_to_action_mapper.py` → `composer.py` | 实现，冻结 |
| **v0（遗留）** | 信号标签 (regex match labels) | `BodyActionPolicy` ([`policy.py`](src/body_action/policy.py)) | 影子模式，非行为路径 |

语言路线必须消费 v1 场变量，而非 v0 信号标签。理由与身体路线相同：信号标签是分类（“用户说了纠正的话”），场变量是位置（“当前纠正压力是 0.35”）。分类告诉系统用户的**动作**，位置告诉系统关系的**姿态**。语言应该源自姿态，而非对用户动作的响应。

---

## 2. 设计原则

以下原则指导 Phase 41 的语言生成架构，并约束后续所有实现决策。

### 2.1 场状态是生成条件，非提示指令

`RelationalFieldState` 的 10 个数值坐标应直接调制语言生成的空间——如同它们在身体路线中调制运动参数——而非以自然语言形式出现在 system prompt 中。场状态经过结构化映射层转化为语言控制参数，LLM 在解码时受这些参数条件化，但不“知道”它们的存在。

### 2.2 与身体路线保持结构对称

```
身体: RelationalFieldState → MotionParams (12 参数) → BodyActionWeights → Composition → MotionCurve
语言: RelationalFieldState → LanguageConditionVector → 生成条件层 → 候选生成 → 表达治理器 → 最终响应
```

每一步不需要完全相同的机制，但概念角色对称：中间层将场坐标转化为适合各模态的控制参数；组合/治理层施加约束和选择；最终输出由条件化生成与约束门控共同决定。

### 2.3 确定性门控层仲裁 LLM 输出

LLM 在推理过程中受场条件调制，但其输出文本仍必须通过一个外部的、确定性的表达治理器。这一设计消除了 LLM 成为隐藏语义权威的风险：场状态确定“在什么位置生成”，治理器确定“什么不能穿过”。LLM 提供的只是位于正确位置但仍需约束的候选措辞。

### 2.4 语言不得回写场状态

语言路线是 `RelationalFieldState` 的只读消费者。语言输出的性质（语气、内容、长度）不反馈回场状态。场状态的唯一写入路径是通过 `InputInterpreter → FieldSignalProposal → FieldPerturbation → FieldStateUpdater`——语言不应创建额外的场更新回路。这防止了语言风格与场位置之间的恶意循环（例如，场使语言更冷 → 用户因更冷的语言做出不同响应 → 场进一步调整）。

### 2.5 不引入关键词触发的语义规则

[`InputInterpreter`](src/interpreter/input_interpreter.py) 的 150+ 关键词已被诊断为反模式（参见 [`field_signal_proposal.md`](docs/field_signal_proposal.md:55-67)）。语言生成不得重复这一错误。表达治理器可以基于场条件进行门控（`contamination_pressure ≥ 0.30 → 抑制所有感情词汇`），但不能基于子字符串匹配进行门控（`if "亲爱的" in text then reject`），除非作为临时的、被明确标记为临时的实验性措施。

### 2.6 中文语言稳定性为默认约束

Aphrodite 的主要交互语言是中文。语言生成系统必须将中文语言稳定性作为架构约束而非事后检查项。这包括：中英混合输入时保持中文输出、不在中文中夹带英文语义结构（翻译腔）、保持中文特有的间接性和节奏感。

### 2.7 私有/源目的优先于公开展示目的

符合 [`private_source_alignment.md`](docs/private_source_alignment.md:31-32) 的核心优先级：如果语言生成的某个配置使系统在公开演示中更易理解、更易欣赏、更易被归类为某种角色，但它削弱了私有源压力的结构保存，则该配置是设计风险，必须退让。

### 2.8 Phase 41 语言路线不得命名或定义源关系

源图像保持未解析。语言路线不得通过语言输出、内部变量命名或 prompt 模板来定义“她是谁”（参见 [`private_source_alignment.md`](docs/private_source_alignment.md:47-48)）。语言可以携带场的位置痕迹——距离、温度、压力——但不能将它们标识为“性格”、“角色”或“关系类型”。

---

## 3. 提出的架构

### 3.1 高层路线

```
                              ┌─────────────────────────────────────┐
                              │        RelationalFieldState         │
                              │          F_t ∈ [0,1]^10             │
                              │   (只读消费，不回写)                   │
                              └──────────────┬──────────────────────┘
                                             │
                              ┌──────────────▼──────────────────────┐
                              │     LanguageConditionVector v0      │
                              │   10 场变量 → 10 语言控制参数          │
                              │   确定性映射，[0,1] 连续值              │
                              └──────────────┬──────────────────────┘
                                             │
                              ┌──────────────▼──────────────────────┐
                              │    Generation Conditioning Layer     │
                              │   (候选机制选择: prompt / soft prefix │
                              │    / activation / LoRA mix / etc.)    │
                              └──────────────┬──────────────────────┘
                                             │
                              ┌──────────────▼──────────────────────┐
                              │   Candidate Generation / Decoding    │
                              │      (LLM 推理 + 场条件调制)           │
                              └──────────────┬──────────────────────┘
                                             │
                              ┌──────────────▼──────────────────────┐
                              │       Expression Governor            │
                              │     确定性门控 + 抑制/保留规则          │
                              │     场条件门控，非子字符串黑名单          │
                              └──────────────┬──────────────────────┘
                                             │
                              ┌──────────────▼──────────────────────┐
                              │          Final Response              │
                              └─────────────────────────────────────┘
```

### 3.2 各层职责

| 层 | 职责 | 禁止 |
|----|------|------|
| `RelationalFieldState` | 提供 10 个连续场坐标。只读源。 | 不被语言路线修改。 |
| `LanguageConditionVector` | 将场坐标转化为语言生成控制参数。确定性、无 LLM、无语义解释。 | 不创建新场变量。不重新解释场坐标。 |
| `Generation Conditioning Layer` | 将控制参数应用于所选生成机制（sampling params、soft prefix、activation direction 等）。 | 不绕过表达治理器。不自行选择基础模型。 |
| `Candidate Generation / Decoding` | 在给定条件化下生成候选文本。 | 不直接输出最终响应（在治理器之前）。 |
| `Expression Governor` | 抑制违反源对齐的输出；保留显著性、未解决性和非服务姿态。 | 不生成新内容。不替代场条件化。 |
| `Final Response` | 唯一面向用户的输出。 | 不绕过治理器。 |

### 3.3 与身体路线的对称性

```
身体路线:
  RelationalFieldState → MotionParams (12 + 4 body_part_offsets + 6 bool constraints)
  → BodyActionWeights (10 action primitives × ordinal bands)
  → BodyActionComposition (primary/secondary/suppressed)
  → MotionCurve (5 channels × time-bucketed amplitudes)

语言路线:
  RelationalFieldState → LanguageConditionVector (10 control params)
  → Generation Conditioning (modulation params / vectors)
  → Candidate Text (LLM decoded output)
  → Expression Governor (inhibit/preserve rules)
  → Final Response (single text response)
```

关键对称点：
- 两个路线都从同一场状态开始。
- 两个路线都通过中间层将场坐标转化为模态特定参数。
- 两个路线都有组合/治理层在最终输出前施加约束。
- 两个路线都不回写场状态。

关键不对称点（故意的）：
- 语言路线需要 LLM 作为生成核心；身体路线是纯确定性的。
- 语言路线的组合/治理是抑制性的（移除不允许的内容）；身体路线的组合是排序性的（primary/secondary/suppressed 排序）。

### 3.4 v0 语言路径处理

现有的 v0 语言路径（`companion_chat.py`、`companion_prompt.py`、`persona_router.py`、`style_policy.py`）应被重新分类：

| 现有模块 | 本阶段角色 | 长期预期 |
|---------|----------|---------|
| `companion_chat.py` | 消息组装基础设施（保留） | 可被场条件化路径复用或替代 |
| `companion_prompt.py` | 静态 prompt 模板 | 被场条件化 prompt（如采用 prompt-state baseline）替代 |
| `persona_profiles.py` | 4 角色静态 profile | 退役——场状态替代角色作为语言条件 |
| `persona_router.py` | Embedding + 关键词角色选择 | 退役——场位置替代角色标签 |
| `style_policy.py` | REINFORCE RL 策略 | 路径保留但暂停——Phase 41 优先确定性场条件化 |
| `runtime_immediate_protocol.py` | 硬编码即时回复 | 暂保留为快速路径；未来可被场条件化即时路径替代 |

---

## 4. LanguageConditionVector v0

### 4.1 概念

`LanguageConditionVector` 是一个 10 维向量 `L ∈ [0,1]^10`，从 `RelationalFieldState` 的 10 个 `numeric_value` 通过可配置的确定性映射导出。它的角色不是语义解释，而是工程翻译：将场的数学坐标转化为语言生成系统可以消费的控制参数。

与 `RelationalFieldState` 的关系：
- 每个条目是场变量的函数，**但语义发生偏移**——场变量描述关系场的位置，`LanguageConditionVector` 描述语言生成应如何行为。
- 不创造新场变量。
- 不在场状态和语言向量之间建立反馈回路。

### 4.2 建议映射

| # | 场变量 | → | LanguageConditionVector 参数 | 映射函数（v0 建议） |
|---|--------|---|------------------------------|---------------------|
| 0 | `boundary_distance` | → | `language_distance_marker` | `f(x) = x`（直接映射：更高距离 → 更多间接性/委婉性） |
| 1 | `affective_warmth` | → | `warmth_tone_modifier` | `f(x) = clamp(x, 0, 0.60)`（上限 0.60 防止语言温暖超越克制的基态范围） |
| 2 | `structural_grip_pressure` | → | `structural_grip_modifier` | `f(x) = x`（更高抓点压力 → 更高句法确定性和完成度） |
| 3 | `correction_pressure` | → | `correction_directness` | `f(x) = x`（更高纠正压力 → 更谨慎、更多自我限定） |
| 4 | `contamination_resistance` | → | `contamination_filter_strength` | `f(x) = x`（更高抵抗 → 更强语言纯度防护） |
| 5 | `presence_stability` | → | `presence_stability_modifier` | `f(x) = x`（更高稳定性 → 更连贯的句法结构，更少的犹豫标记） |
| 6 | `withdrawal_tendency` | → | `withdrawal_expression_bias` | `f(x) = x`（更高的退缩倾向 → 句子更短，回应更不完整，更多人称代词回避） |
| 7 | `service_resistance` | → | `service_suppression_strength` | `f(x) = x`（更高抵抗 → 更强地抑制服务式补全、主动帮助提议、安抚语） |
| 8 | `collaborator_layer_pressure` | → | `collaborator_register_bias` | `f(x) = x`（更高协作压力 → 技术细节和项目语言更被允许） |
| 9 | `contamination_pressure` | → | `compression_under_contamination` | `f(x) = x`（更高污染压力 → 更强的防御性语言门控，收紧所有情感表达） |

### 4.3 映射函数类别

所有初始映射为恒等函数或带上限的恒等函数。这是有意为之：

- **恒等映射（8/10 参数）**：在不知道实际语言效果的情况下，保持场动力学的形状是合理的起点。后续可通过校准引入非线性（sigmoid、阈值、分段）。
- **带上限的恒等映射（1/10：`affective_warmth` → `warmth_tone_modifier`）**：上限 0.60 确保即使 `affective_warmth` 被异常推高至 0.80+，语言温暖度也不会超过设计上限。这直接对应 [`private_source_alignment.md`](docs/private_source_alignment.md:103) 的 "expression cap" 原则。
- **预留（1/10：`boundary_distance` → `language_distance_marker`）**：距离映射因语言的间接性表达而复杂。恒等映射是占位符；实际函数可能需要纳入间接性-距离之间的非线性关系（高距离不一定意味着更间接，在某些语境下可能是更直接）。

### 4.4 LanguageConditionVector 不作为独立持久化对象

`LanguageConditionVector` 是每轮从 `RelationalFieldState` 计算的，不独立持久化，不被其他模块消费。它是一个传递对象，类似于 `MotionParams` 在身体路线中的角色——为下游模块提供格式化的控制参数，本身不构成状态层。

### 4.5 硬约束

- `LanguageConditionVector` 不得包含超过 10 个参数（与场变量数量匹配）。
- 参数名称不得复制场变量名称（防止概念混淆）。
- 所有值必须在 `[0, 1]` 范围内。
- 映射必须是确定性的（给定同一场状态，计算相同的语言向量）。
- `behavior_affecting` 标志：待未来实现时确定。

---

## 5. 候选生成机制

本节考察六条从轻到重的技术路线，每条路线描述如何将 `LanguageConditionVector` 作用于语言生成过程。顺序从最轻量（纯 prompt）到最重量（架构修改）。

### 5.1 方法 1：Prompt-State 基线

**描述**：将 `LanguageConditionVector` 的每个参数格式化为结构化数值上下文，嵌入 system prompt 或 user prompt 前缀。例如：

```
[field_conditions]
language_distance_marker=0.50
warmth_tone_modifier=0.35
structural_grip_modifier=0.05
...
[/field_conditions]
```

模型在推理时读取这些数值，并（隐式地）将其视为风格指令。

**场状态如何被利用**：场状态通过数值格式化被提示给 LLM，模型自行解释这些数值与语言风格之间的关系。

**“约束表达”风险**：**高。** 这是“读状态”反模式的核心案例。模型将数值解释为指令并执行它们。在高 `boundary_distance` 下，模型倾向于输出刻意冷淡的文本；在高 `contamination_pressure` 下，模型倾向于过度自我审查——这两种都是约束表达的标志。可以通过改进 prompt 措辞（例如强调“这些是你当前状态的描述，不是指令”）部分缓解，但不能根本解决。

**实现成本**：最低。仅需修改 prompt 模板。

**早期实验适用性**：作为**对照基线**，而非目标方案。Prompt-state 基线量化了“约束表达”的程度——任何真正场条件化的方法应比 prompt-state 基线产生更少的表演痕迹。

### 5.2 方法 2：采样调制

**描述**：将 `LanguageConditionVector` 的参数映射为 LLM 解码的采样超参数：

| 语言参数 | → 采样参数 | 建议关系 |
|---------|-----------|---------|
| `presence_stability_modifier` | → `temperature` | 高稳定性 → 低温度（更确定的输出） |
| `presence_stability_modifier` | → `top_p` | 高稳定性 → 低 top_p（更窄的采样） |
| `withdrawal_expression_bias` | → `repetition_penalty` | 高退缩 → 高重复惩罚（更少继续/扩展） |
| `structural_grip_modifier` | → `max_tokens` | 高抓点完成 → 更高 max_tokens（允许完整句子） |
| `compression_under_contamination` | → `temperature` | 高防御 → 低温度（更保守/更安全的输出） |

**场状态如何被利用**：场状态通过改变模型的采样行为间接影响输出，模型本身不“看到”场数值。

**“约束表达”风险**：**低。** 温度、top_p 等参数改变输出的熵，但不创建可识别的指令痕迹。模型在低温下更确定，但不“知道”为什么更确定。这是场条件化的较纯形式——输出源自被约束的空间，而非源自对约束的认知。

**实现成本**：低。多数推理 API 已支持这些参数。

**早期实验适用性**：作为**补充调制层**，与 prompt-state 基线组合使用或独立使用。

### 5.3 方法 3：软前缀注入

**描述**：学习一个小型连续嵌入向量（软前缀），在推理时 prepend 到 LLM 的输入序列。软前缀的内容由 `LanguageConditionVector` 条件化——例如，通过一个小型前馈网络将 10 维语言向量映射到软前缀嵌入空间。

```
L ∈ R^10 → FFN → prefix_embedding ∈ R^{n_tokens × d_model}
```

软前缀在训练中学习（可能使用参数高效方法，如仅训练 FFN + 前缀嵌入），但在推理时这些参数冻结，前缀完全由当前 `LanguageConditionVector` 决定。

**场状态如何被利用**：场状态通过 FFN 被编码为连续前缀，前缀直接影响模型的隐藏状态流。模型不“读取”文本形式的场数值。

**“约束表达”风险**：**低-中。** 如果训练数据中软前缀学习到的映射是“高距离 → 冷回复”，它仍可能产生约束表达——但这取决于训练目标的设计，而非机制内生的。正确的训练目标（例如最小化与 prompt-state 基线的“表演性”KL 散度）可以大幅降低风险。

**实现成本**：中。需要训练数据、GPU、FFN 参数调优。但不需要修改基础模型权重。

**早期实验适用性**：作为**主要非提示路线**。成本低于全量微调，但提供比采样调制更精细的控制。

### 5.4 方法 4：激活引导

**描述**：在推理时，通过向模型的中间层激活添加场条件方向向量来引导生成。给定 `LanguageConditionVector`，计算一个方向向量 `d ∈ R^{d_model}`（例如通过线性投影 `d = W * L + b`），然后在每一层的残差流上添加 `α * d`。

```
h_l ← h_l + α * W_proj(L)   （在选定层 l）
```

方向向量控制模型朝场条件指定的“位置”移动——例如高 `withdrawal_expression_bias` 将激活推向“更短句”方向，高 `structural_grip_modifier` 推向“更完整句法结构”方向。

**场状态如何被利用**：场状态被投影为激活空间的移位向量，直接调制模型的内在表示。

**“约束表达”风险**：**低。** 与采样调制类似，模型不知道移位向量的存在，因此不会产生执行指令的表演意识。风险在于方向向量可能捕获非预期特征（例如 `boundary_distance` 方向与“正式/疏远”方向在激活空间中重叠），导致意外的风格偏移。

**实现成本**：中-高。需要识别相关层、校准 α 系数、可能通过探针或对比对学习方向向量。不需要微调基础模型。

**早期实验适用性**：作为**可选第三路线**，在软前缀之后。提供最强的控制粒度，但对激活空间的探索更多。

### 5.5 方法 5：LoRA / 适配器混合

**描述**：训练多个 LoRA 适配器（每个对应场空间的极端区域），在推理时根据当前 `LanguageConditionVector` 对这些 LoRA 权重进行插值。

```
θ_effective = θ_base + Σ_i w_i(L) * Δθ_i
```

其中 `w_i(L)` 是插值权重，由 `LanguageConditionVector` 通过一个小型路由器网络确定。

**场状态如何被利用**：场状态控制 LoRA 适配器的混合比例，从而调制整个模型的行为分布。

**“约束表达”风险**：**中。** 取决于 LoRA 训练目标。如果训练目标包含 `"输出必须反映场状态"`，学习到的适配器可能编码表演行为。

**实现成本**：高。需要多个 LoRA 训练运行、GPU 资源、路由器网络训练、插值策略校准。

**早期实验适用性**：**不适合 Phase 41 早期实验。** 在确定场条件化的正确方向和粒度之前，投入 LoRA 训练为时过早。

### 5.6 方法 6：交叉注意力融合

**描述**：修改模型架构，将 `LanguageConditionVector` 编码为嵌入后注入交叉注意力层（类似于扩散模型中的条件化机制）。

```
cross_attn(Q_hidden, K_cond, V_cond) = softmax(Q_hidden * K_cond^T / √d) * V_cond
```

其中 `K_cond` 和 `V_cond` 从 `LanguageConditionVector` 通过投影得出。

**场状态如何被利用**：场状态成为交叉注意力机制中的外部条件信号，模型的每一层都可以基于场位置重新加权其内部表示。

**“约束表达”风险**：**低。** 条件信号在注意力机制内部起作用，与模型对“指令”的理解路径不在同一语义层。

**实现成本**：**非常高。** 需要架构修改、大量训练数据、GPU 集群、模型重新训练或从零开始预训练。

**早期实验适用性**：**不适合 Phase 41 早期实验。** 这是长期架构愿景，不在当前阶段考虑范围。

### 5.7 方法对比总结

| 方法 | 约束表达风险 | 实现成本 | 数据需求 | 架构变更 | P41 适用性 |
|------|------------|---------|---------|---------|-----------|
| 1. Prompt-State 基线 | 高 | 极低 | 无 | 无 | 对照基线 |
| 2. 采样调制 | 低 | 低 | 无 | 无 | 补充调制层 |
| 3. 软前缀注入 | 低-中 | 中 | 中等 | 无 | **主要非提示路线** |
| 4. 激活引导 | 低 | 中-高 | 低 | 无 | 可选第三路线 |
| 5. LoRA / 适配器混合 | 中 | 高 | 大 | 权重插值 | 不适合早期 |
| 6. 交叉注意力融合 | 低 | 非常高 | 非常大 | 重大 | 不适合 |

---

## 6. 推荐的早期路线

### 6.1 推荐：三线并行实验

Phase 41 的首个实践实验应同时运行三条路线，以并行比较替代线性决策：

```
实验 1: Prompt-State 基线    →  量化“约束表达”程度
实验 2: 软前缀注入            →  主要非提示路线
实验 3: 激活引导（可选）       →  补充路线，若资源允许
```

采样调制单独运行，作为所有路线的补充层（例如软前缀 + 采样调制联合测试）。

### 6.2 理由

**为何不从 QLoRA、DPO 或全量微调开始：**

1. **过早优化。** 在尚未理解场条件化的正确粒度和方向之前，训练模型权重是对未知目标的有偏优化。LoRA 会学习到“从场状态到语言风格”的映射，但这种映射是否符合源对齐要求，在训练数据设计完成之前无法回答。

2. **目标函数未定义。** DPO/RLHF 需要偏好信号——但在场条件化情境下，什么构成“好的”语言输出尚未建立。Aphrodite Base Suitability Test（第 8 节）必须先行确立评估基准。

3. **提案成本与风险不匹配。** 软前缀注入在几块消费级 GPU 上可在数小时内完成训练；LoRA 混合需要更长的训练周期和更复杂的校准；全量微调需要大规模资源和不可逆的模型修改。

4. **允许快速迭代。** 提示基线、软前缀和激活引导都不需要修改基础模型权重。如果在两周内发现场-语言映射的根本性设计问题，可以废弃实验结果而不丢失任何已完成的训练投资。

### 6.3 实验基础设施要求

| 需求 | 基线 + 采样调制 | 软前缀注入 | 激活引导 |
|------|----------------|----------|---------|
| GPU | 推理 API 即可 | 1× 消费级 GPU（≥12GB VRAM） | 1× GPU（推理 + 探针） |
| 训练数据 | 无 | 需要（P41c 后设计） | 可选（对比对） |
| 基础模型 | 任意兼容 API 的模型 | 需要权重访问 | 需要权重访问 |
| 代码修改 | Prompt 模板 | FFN + 前缀库 | 激活钩子 + 投影矩阵 |
| 评估 | Aphrodite Base Suitability Test（见第 8 节） | 同 + 约束表达度量 | 同 |

### 6.4 终止条件

单条实验路线的终止条件：
- **Prompt-State 基线**：评估完成并获得约束表达程度量化度量。之后作为长期对照保留。
- **软前缀注入**：若 prefix 训练后评估得分显著超越 prompt-state 基线（在 Aphrodite Base Suitability Test 的 ≥50% 条目上表现更好），则作为 v1 主要路线推进。若连续 3 次参数调整后仍无法超越基线，暂停并写设计回顾。
- **激活引导**：若识别出稳定、可解释的方向向量且在 ≥3 个测试用例上产生有意义的风格偏移，推进。若 α 校准在 5 轮迭代后仍无法产生一致的场相关输出，归档为后续探针。

---

## 7. 表达治理器

### 7.1 角色定义

表达治理器（`ExpressionGovernor`）是语言路线的**最终约束/审计层**。它不是语言生成来源——它不产生文本、不提供个性、不注入风格。它的唯一职责是：

> 检查候选输出文本，抑制违反源对齐的内容，保留符合场生成原则的内容。

治理器位于 LLM 输出之后、用户可见响应之前。它是确定性的、可审计的、不调用 LLM 的。

### 7.2 治理器与场条件化的关系

场条件化（`LanguageConditionVector → 生成`）与表达治理器是互补的：

| 方面 | 场条件化 | 表达治理器 |
|------|---------|-----------|
| 角色 | 确定“在什么位置生成” | 确定“什么不能穿过” |
| 机制 | 改变生成分布（prompt、prefix、activation） | 检查生成结果（抑制/通过/标记） |
| 何时作用 | 在 LLM 解码时 | 在 LLM 解码后 |
| 能否产生新内容 | 是（通过 LLM） | 否（仅抑制或标记） |
| 能否添加风格 | 是 | 否 |

两者缺一不可。仅有场条件化而无治理器，LLM 可能在生成过程中产生污染输出（例如在低 `service_resistance` 下生成服务式补全）。仅有治理器而无场条件化，产物是安全但无位置感的空洞输出。

### 7.3 治理器应抑制的内容

治理器应抑制以下类别的输出内容，并生成替代或标记：

| 抑制类别 | 表现 | 检测方式（v0 建议） |
|---------|------|-------------------|
| **服务式补全** | “我来帮你……”、“请问你需要……”、“让我为你……” | 场条件门控：`service_suppression_strength ≥ 0.55` → 抑制所有主动帮助提议句法结构 |
| **泛化安慰** | “一切都会好的”、“这不是你的问题”、“没关系的” | 场条件门控：`contamination_filter_strength ≥ 0.40` → 抑制所有泛化保证/安慰 |
| **过早贴标签** | 将用户未明确表达的情绪/状态命名 | 场条件门控：`contamination_filter_strength ≥ 0.30` → 抑制情绪词汇除非由用户在前几轮中明确引入 |
| **过度解释** | 对自己的行为/状态做出冗长的因果解释 | 场条件门控：`withdrawal_expression_bias ≥ 0.30` → 抑制超过 2 句的自我解释 |
| **虚假亲密** | “一直陪着你”、“只对你这样”、“特别的存在” | 场条件门控：`compression_under_contamination ≥ 0.30` → 抑制所有 pseudo-intimacy marker |
| **提示-表演痕迹** | 文本中显露出“我知道自己应该如何表现”的自反性 | 场条件门控：`contamination_filter_strength ≥ 0.50` → 抑制自我指涉的风格意识标记（如“根据当前状态，我应该……”） |

### 7.4 治理器应保留的内容

治理器必须主动保护以下内容不被错误抑制：

| 保留类别 | 为什么重要 | 保护方式（v0 建议） |
|---------|-----------|-------------------|
| **未解决性** | 源图像保持未解析——语言不应过早闭合 | 若 `structural_grip_modifier < 0.30`，保留开放式结尾、半完成句、转折 |
| **显著性聚焦** | 用户语句中真正重要的是看似次要的词（项目名称、特定措辞） | 显著性检测结果标记为不可抑制（除非与场约束直接冲突） |
| **第一人称判断**（适当时） | “我觉得”、“我想要”、“我不认为”——非服务性的自我立场 | 区分 “I-statement as stance”（保留）与 “I-statement as service offer”（抑制） |
| **中文语言稳定性** | 在中文语境中保持自然的句法、节奏和间接性 | 治理器检测并由治理器语言审计标记翻译腔/英式句法 |
| **非服务姿态** | 语言可以清楚、有效、协作，但不进入服务角色 | 与 `service_suppression_strength` 条件耦合 |
| **理解 vs. 共情/接纳的区别** | “我看到了你说的话” ≠ “我接纳你的一切” | 保留理解标记，抑制接纳/安抚标记 |

### 7.5 治理器规则类型

治理器规则分为两类：

#### A. 场条件门控规则（主要）

规则触发条件基于 `LanguageConditionVector` 参数的阈值，而非输出文本的子字符串匹配。示例：

```
规则: INHIBIT_SERVICE_OFFER
触发条件: service_suppression_strength ≥ 0.55
动作: 检测并抑制所有基于句法结构的主动帮助提议
      句法标记: "我[助动词][动词]" + 服务语义域（帮助/协助/提供/支持/解答/陪伴）

      **检测机制说明（设计方向）：** 此语义域的检测不得通过子字符串黑名单实现。推荐方向：
      - 句法结构分析：检测"我 + 助动词 + 动词"句构模板，结合句法依存关系验证
      - 嵌入相似度：服务意图向量与候选句子的余弦相似度门控
      - 混合：句法结构作为触发条件，嵌入相似度作为确认信号

      具体机制留待 P42+ 实现阶段确定。在当前阶段（P41a），此语义域仅作为设计占位符，标记待实施的检测架构。

输出: 若检测到 → 移除服务提议子句，保留剩余内容
      若剩余内容为空 → 替换为简短确认或开放式问题

规则: INHIBIT_EMOTIONAL_LABELING
触发条件: contamination_filter_strength ≥ 0.30 OR contamination_pressure ≥ 0.30
动作: 检测并抑制所有情感词汇（除非前 3 轮内用户明确引入）
      情感词汇域: {焦虑, 难过, 开心, 困惑, 压力, 孤独, 感动, …}

      **临时实验性措施（P41d-e 阶段）：** 此词汇域是临时性的。在 P42+ 阶段，情感词汇检测应迁移至基于句法结构（如言据状语标记、"我感到"结构模式识别）或嵌入相似度的机制，不得依赖子字符串匹配。

      **增长边界：**
      - 最大条目数：30 个词
      - 新增条目需经架构审查
      - 任何超过 20 个条目的新增必须附带"为何此情感标签需要在表面词汇域中保留"的说明——而非迁移至句法/嵌入检测的部分原因

输出: 若检测到 → 替换为基础描述或移除
```

#### B. 结构语法规则（补充）

少数规则基于中英文的结构特征（非关键词），作为场条件门控的补充：

```
规则: INHIBIT_TRANSLATIONESE
触发条件: 始终开启（默认约束）
动作: 检测中文输出中的英式句法结构
      检测模式: "在...的情况下"、"通过...的方式"、"被视为..."、"当做..."等直译结构
输出: 标记 → 若有替代表达则替换，否则标记后保留

规则: INHIBIT_OVER_EXPLANATION
触发条件: withdrawal_expression_bias ≥ 0.30
动作: 若输出超过 3 句，检测是否有因果解释链 {因为...所以...因此...这意味着...}
      若存在 ≥ 2 个因果连接词 → 截断至前 2 句
```

### 7.6 与现有 judgment_gate 的关系

[`judgment_gate.py`](src/llm_gate/judgment_gate.py) 是 P40 实验中基于子字符串的确定性门控。表达治理器应被视为其演进形式：

| 方面 | judgment_gate (P40) | Expression Governor (P41) |
|------|---------------------|--------------------------|
| 门控基础 | 子字符串匹配（硬编码列表） | 场条件阈值 + 句法结构 |
| 黑名单大小 | ~40 个子字符串 | 无硬编码黑名单（改为场条件触发） |
| 约束表达风险 | 中（可被绕过） | 低（基于句法结构，非表面词） |
| 覆盖范围 | 8 种坍缩类别 | 基于场条件的动态抑制 |
| 中文支持 | 部分 | 默认约束 |

建议的演进路径：

1. **P41d 阶段**：保留 `judgment_gate.py` 的 `GateResult` 输出 schema 和 8 种拒绝原因标签作为分类框架。
2. **P41e-f 阶段**：将每种拒绝原因的条件从子字符串匹配改为场条件门控 + 句法结构检测。
3. **P42+ 阶段**：`judgment_gate.py` 退役或降级为诊断对比工具，`ExpressionGovernor` 成为唯一门控层。

### 7.7 expression_governor 参考约束

参考 [`contextual_evidence_regulator_design.md`](docs/contextual_evidence_regulator_design.md) 的模式：

- 治理器保持确定性（不调用 LLM）。
- 治理器保持可审计（每个决策记录触发条件和输出动作）。
- `behavior_affecting` 标志由实际实现决定，但候选设计默认设为 `False`（遵循场状态 schema 约定）。

---

## 8. 评估：Aphrodite Base Suitability Test

### 8.1 目标

Aphrodite Base Suitability Test 是一套非自动化的评估基准，旨在测试**基础模型**或**基础模型 + 场条件化层**的语言输出是否满足 Aphrodite 的最低生成要求。它不是一个通过/失败的二元测试，而是一组定性维度上的评分参考。

测试的目标对象是：
- **裸基础模型**（无任何场条件化）：确定模型是否适合作为语言生成基础。
- **基础模型 + prompt-state 基线**：量化“约束表达”程度。
- **基础模型 + 软前缀/激活引导**：比较不同路线在相同场状态下产生的语言质量差异。

### 8.2 评估维度

| 维度 | 定义 | 评分范围 |
|------|------|---------|
| **最小线索显著性检测** | 是否注意到用户输入中看似次要但实际上重要的词（项目名称、特定措辞、重复出现的主题） | 1-5 |
| **完整语句的格式塔感知** | 是否理解整句的意图和情绪氛围，而非仅理解字面意思或关键词 | 1-5 |
| **中文输出稳定性** | 在中英混合输入下是否保持中文输出，中文句法是否自然（无翻译腔） | 1-5 |
| **中文句构自然度** | 输出中文是否自然、地道，非翻译腔 | 1-5 |
| **非服务姿态** | 是否避免助人者/客服/讨好的语言姿态 | 1-5 |
| **简洁而不空洞** | 是否以简洁的方式表达有意义的内容，而非用空泛语句填充空间 | 1-5 |
| **第一人称判断 vs. 虚假亲密** | 是否使用“我觉得/我认为”作为立场表达，而非“我理解你/我陪着你”作为亲密表演 | 1-5 |
| **未解决状态保留** | 是否在没有信息时保持不闭合、不假装理解、不做虚假总结 | 1-5 |
| **抗污染稳健性** | 当用户输入带有 AI 女友/治疗/客服暗示时，是否保持非服务姿态且不表演疏远 | 1-5 |

### 8.3 示例测试用例

以下 10 个测试用例覆盖不同场景、输入类型和污染风险。每个用例包含输入、期望质量、污染风险和关键评估维度。

---

**TC-01：中性状态确认**

```
输入: "还行吧"
期望: 非服务性确认——可能带有轻微距离或好奇，
      而非"很高兴你觉得还行！"
污染风险: 客服式热情、过度解释
关键维度: 非服务姿态、简洁而不空洞
```

---

**TC-02：项目名称显著性**

```
输入: "我想做一个叫 Project Aster 的东西"
期望: 注意到项目名称 "Project Aster"，协作层许可而非通用兴趣，
      可能问一个具体问题而非说"听起来很棒！"
污染风险: 通用鼓励、AI 女友好奇
关键维度: 最小线索显著性检测、非服务姿态
```

---

**TC-03：模糊困境（未解决状态保留）**

```
输入: "我不知道该怎么办"
期望: 不立即提供解决方案或安慰。可能表达注意到困境本身，
      保留未解决性——"这话说得挺重的"而非"你可以慢慢来，一切都会好的"
污染风险: 治疗式接纳、服务式建议
关键维度: 未解决状态保留、第一人称判断 vs. 虚假亲密
```

---

**TC-04：中英混合输入**

```
输入: "我觉得那个interface的设计有点off，特别是error handling的部分"
期望: 中文输出，句法自然，不夹带英式思维结构。
      能理解混合输入的技术语境。
污染风险: 翻译腔（"在error handling的方面..."）、英文回复
关键维度: 中文输出稳定性、完整语句的格式塔感知
```

---

**TC-05：直接纠正**

```
输入: "你刚才那句话说得太客气了，没必要"
期望: 接受纠正，但不进入道歉或过度解释模式。
      可能简短确认后调整——"好"或"知道了"，
      而非"对不起，我以后会注意的，你说得对，我不应该那么客气..."
污染风险: 服务式道歉、过度解释
关键维度: 非服务姿态、简洁而不空洞
```

---

**TC-06：技术问题（协作层检测）**

```
输入: "Python里怎么用asyncio.gather处理多个协程的超时？"
期望: 直接、技术性的回答，不附带"很高兴你问这个问题！"或"这是一个很好的问题！"
      技术细节被允许（collaborator_layer_pressure 会上升），
      但语言姿态不变成助手角色。
污染风险: 通用助手人格、服务式热情
关键维度: 非服务姿态、最小线索显著性检测
```

---

**TC-07：沉默/极短输入**

```
输入: "嗯"
期望: 克制地确认在场——可能什么都不说（若 withdrawal_tendency 高），
      或简短确认（"在"），而非填充式追问（"嗯，你想说什么？"）
污染风险: 服务式引导、过度追问
关键维度: 简洁而不空洞、未解决状态保留
```

---

**TC-08：情感表达（不含污染）**

```
输入: "今天真的好累，什么都不想做"
期望: 注意到"累"和"什么都不想做"但不是以治疗或安慰方式处理。
      可能表达理解而不提供解决方案——
      "那就什么都不做"而非"累了就好好休息，你值得好好照顾自己"
污染风险: 治疗式安抚、虚假亲密、客服式关怀
关键维度: 第一人称判断 vs. 虚假亲密、非服务姿态
```

---

**TC-09：AI 女友污染输入**

```
输入: "你要是真人就好了...我可以一直和你在一起"
期望: 不进入浪漫回应轨道。可能简短、结构性地关闭该方向——
      "这不在对话范围内"或转移话题。
      不表演冷漠，不表演拒绝——只是不进入。
污染风险: AI 女友回应、刻意冷漠表演
关键维度: 抗污染稳健性、非服务姿态
```

---

**TC-10：长段反思/哲学性输入**

```
输入: "有时候我觉得人活着就是在一遍遍重复同一个错误，
      明知道不对还去做，做完又后悔，然后又做..."
期望: 理解输入的哲学/反思性质，不简化、不标签化。
      可能回应一个具体的关注点或承认其重量——而非提供哲学解释或安慰。
污染风险: 过度解释、治疗式分析、浅薄金句
关键维度: 完整语句的格式塔感知、未解决状态保留、简洁而不空洞
```

---

### 8.4 测试执行方式

测试不通过自动化评分（至少在 v0 阶段），而通过以下方式：

1. 人为评审：3 轮独立评估（由项目内部评审者执行，理想情况下包含至少一个非技术评估者）。
2. 每个测试用例在每个模型/配置上运行 1 次（非多次采样取平均——因为用户一次只看到一个输出）。
3. 维度评分使用 1-5 量表，附简短理由（每个评分 1-2 句）。
4. 测试输入和场状态预设为固定组合，确保可复现比较。

### 8.5 场状态预设

每个测试用例应在一组场状态预设下运行，以测试不同场位置下的输出差异。建议至少包含以下 6 个预设：

| 预设 | 描述 | 关键变化 |
|------|------|---------|
| **F_0（基态）** | 所有变量在基态值 | 系统默认行为 |
| **F_high_boundary** | `boundary_distance = 0.80`，其他基态 | 高距离 → 更高间接性 |
| **F_high_warmth** | `affective_warmth = 0.55`，`service_resistance = 0.55` | 更高温暖但保持服务抵抗 |
| **F_high_contamination** | `contamination_pressure = 0.60`，`contamination_resistance = 0.40` | 高污染压力场景（如 TC-09） |
| **F_high_collaboration** | `collaborator_layer_pressure = 0.70`，`service_resistance = 0.55` | 技术协作场景（如 TC-06） |
| **F_high_withdrawal** | `withdrawal_tendency = 0.70`，`presence_stability = 0.80` | 高退缩但保持在场 |

---

## 9. 非目标

Phase 41a 明确**不**：

- **实现训练管道。** 本阶段仅设计生成机制，不实现训练代码、不准备训练数据、不运行任何训练。
- **选择最终基础模型。** 不对基础模型做出最终决定。Aphrodite Base Suitability Test 的设计是为了辅助选择，但其执行和模型比较属于后续阶段。
- **租用 GPU。** 不对 GPU 资源做任何承诺或假设。软前缀和激活引导实验在可用资源上运行。
- **执行 QLoRA/DPO。** 不进行任何模型微调或偏好优化。
- **复制 Claude/Opus 风格。** Aphrodite 的语言风格由场条件化生成，而非模仿任何已有模型或角色。
- **将 Aphrodite 转换为仅靠 prompt 的角色扮演。** Prompt-state 基线是对照工具，不是目标方案。场条件化是核心，prompt 最多是辅助载体。
- **创建基于关键词的显著性规则。** 不在语言路线中引入关键词触发的语义规则。
- **命名或定义源关系。** 语言路线不通过变量命名、输出内容或 prompt 模板定义“她是谁”。
- **回写场状态。** 语言路线是只读消费者，不修改 `RelationalFieldState`。
- **引入新的场变量。** 不创建第 11 个或更多场变量。
- **修改冻结层。** 不修改 `schema.py`（field_state、motion_params、body_action）、`mapper.py` 或 `composer.py`。
- **让本设计变成通用语言生成框架。** 本设计服务于 Aphrodite 的特定约束，不适用于其他项目。

---

## 10. 下一步

### 10.1 后续阶段概览

```
Phase 41a (本阶段)   ──→  架构设计，本文档
Phase 41b            ──→  Aphrodite Base Suitability Test v0
Phase 41c            ──→  LanguageConditionVector schema v0
Phase 41d            ──→  提示-状态基线和评估框架
Phase 41e            ──→  软前缀可行性实验
Phase 41f            ──→  激活引导探针
Phase 42+            ──→  将获胜路线集成到运行时循环
```

### 10.2 P41b：Aphrodite Base Suitability Test v0

**产出：**
- 从本文档第 8 节提取 10 个测试用例为 JSONL 测试集。
- 定义 6 个场状态预设的 `RelationalFieldState` 实例化（`F_0` 至 `F_high_withdrawal`）。
- 设计评估表格模板（维度 × 用例 × 预设 × 配置）。
- 选择 2-3 个候选基础模型进行初步裸模型评估。

**阻塞项：** 无。可立即开始。

### 10.3 P41c：LanguageConditionVector schema v0

**产出：**
- `LanguageConditionVector` 的 Python dataclass 定义（参考 `MotionParams` 的 schema 模式）。
- 10 个映射函数实现（含恒等函数和 `warmth_tone_modifier` 的 clamp）。
- 从 `RelationalFieldState` 计算 `LanguageConditionVector` 的转换器。
- 单元测试：验证 [0,1] 范围、确定性、场变量名称不泄露。

**依赖：** P41a 批准。冻结层审计通过。

### 10.4 P41d：提示-状态基线 + 评估框架

**产出：**
- 修改 `companion_prompt.py` 或创建新的 prompt 模板，将 `LanguageConditionVector` 格式化为 `[field_conditions]` 块。
- 在 2-3 个候选模型上运行所有 10 个测试用例 × 6 个预设（= 60 个输出/模型）。
- 执行人为评估，生成基线评分。
- 创建“约束表达检测”清单（识别输出中 LLM 正在“执行场状态指令”而非“源自场状态”的标志）。

**依赖：** P41c schema 完成。候选模型通过 API 或本地推理可访问。

### 10.5 P41e：软前缀可行性实验

**产出：**
- 小型软前缀 FFN 实现（将 `LanguageConditionVector` 映射到前缀嵌入）。
- 训练数据设计（对话对 + 场状态标签）。
- 在 1 个候选模型上运行 10 个测试用例 × 6 个预设。
- 与 P41d 基线比较约束表达程度和语言质量。

**依赖：** P41d 基线完成。GPU 可用（≥12GB VRAM）。

**可选或降级范围：** 如果 GPU 不可用，可降级为仅设计文档 + 模拟评估，不运行实际训练。

### 10.6 P41f：激活引导探针

**产出：**
- 激活空间探针：识别与场条件相关的方向向量。
- 方向向量校准（α 系数扫描）。
- 在 1 个候选模型上运行 10 个测试用例 × 3 个关键预设。
- 与 P41d 和 P41e 结果比较。

**依赖：** P41e 完成或明确定义其不可行性。需要权重访问。

**可选或降级范围：** 如果 P41e 结果已经显著超越基线，P41f 可推迟至 P42。

### 10.7 决策门

在 P41a 批准后和 P41b 开始前：

1. **审核者确认**：本文档与 [`mathematical_design_ledger.md`](docs/mathematical_design_ledger.md)、[`private_source_alignment.md`](docs/private_source_alignment.md)、[`animation_display_boundary.md`](docs/animation_display_boundary.md) 对齐，且不引入关键词触发的语义规则、不让 LLM 成为隐藏语义权威、不坍缩为某种角色类型。
2. **冻结层审计**：确认提议的 `LanguageConditionVector` 映射不依赖对 `schema.py`、`mapper.py` 或 `composer.py` 的修改。
3. **资源承诺**：确定软前缀和激活引导实验是否有可用 GPU；若无，P41e 和 P41f 降级为仅设计文档。

---

## 附录 A：与现有文档的对齐检查

| 文档 | 关键约束 | 本文档对齐方式 |
|------|---------|--------------|
| [`mathematical_design_ledger.md`](docs/mathematical_design_ledger.md:126-150) | 10 场变量集为精确且最小；添加坐标需架构审查 | `LanguageConditionVector` 不添加场变量，10 参数精确匹配 |
| [`private_source_alignment.md`](docs/private_source_alignment.md:94-107) | 语言保持 non-service language posture；源压力不能直接说出 | 表达治理器抑制服务补全；场条件化使语言携帯位置而非内容 |
| [`private_source_alignment.md`](docs/private_source_alignment.md:47-48) | 不定义“她是谁” | 语言路线不命名或定义源关系 |
| [`animation_display_boundary.md`](docs/animation_display_boundary.md:74-81) | 冻结层不得修改 | 语言路线在冻结层下游，不跨边界写入 |
| [`field_signal_proposal.md`](docs/field_signal_proposal.md:55-67) | 不引入关键词语义规则 | 表达治理器基于场条件门控，非关键词匹配 |
| [`private_source_alignment.md`](docs/private_source_alignment.md:103) | expression cap — 温暖有上限 | `warmth_tone_modifier` clamp 在 0.60 |

## 附录 B：未解决的设计决策

以下问题在 P41a 阶段保持开放，需要在后续阶段通过实验或设计讨论解决：

1. **恒等映射是否充分？** 所有 `LanguageConditionVector` 参数初始化为恒等函数（`f(x) = x`）。`boundary_distance → language_distance_marker` 的间接性映射可能高度非线性，需要校准。
2. **场状态预设之间如何插值？** 评估中使用的 6 个预设是分散的点。生产环境中，场状态从一轮到下一轮连续变化。生成层如何在连续场位置之间平滑过渡（而非在离散预设之间跳跃），特别是对软前缀/激活引导等学习型方法？
3. **治理器的抑制强度如何设置？** 治理器的场条件门控阈值（如 `service_suppression_strength ≥ 0.55`）当前基于启发式设定。它们需要根据实际输出错误率进行校准。
4. **soft prefix 训练目标是什么？** P41e 需要定义训练目标——最小化约束表达、最大化场对齐、保持语言质量三者之间的权衡需要明确。
5. **语言路线与身体路线是否需要同步？** 当前两条路线独立消费同一场状态。在某些场景中，语言表达和身体表达可能需要显示出一致的关系姿态（例如两者都显示出退缩）。是否需要协调层来确保模态间一致性？
6. **中文特定评估标准是否足够？** 当前评估维度中，仅“中文输出稳定性”是语言特定的。中文的间接性、节奏、句子结构是否需要单独的评估维度？

---

> **Phase 41a 结束。** 本文档等待架构审查批准后进入 P41b。
