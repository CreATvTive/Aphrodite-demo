# Aphrodite v2 工程问题解决计划书

版本：v2 Engineering Plan  
定位：针对 v2 工程问题清单的解决计划  
主题：复杂 body-mind 系统的可维护性、可调试性、可回放性、可控人格与延迟管理

---

## 0. 总体策略

Aphrodite v2 的工程目标不是消灭复杂性，而是让复杂性可管理。

总原则：

> 每一层都必须能单独运行、单独记录、单独回放、单独替换、单独评估。

因此本计划采用五个核心工程原则：

1. **State Authority**：所有稳定状态只有唯一权威来源；
2. **Trace Everything**：每次交互必须完整记录；
3. **Priority Arbitration**：多层解释冲突时必须有仲裁；
4. **Fast/Slow Separation**：低延迟反射与慢速深分析分离；
5. **Director View**：最终评估服务于角色表演，不只是参数正确。

---

## 1. 解决状态爆炸

### 难度

高。

### 核心风险

变量太多，状态变化不可追踪。

### 解决方案

建立 **State Ownership Table**。

每个状态变量必须定义：

```text
owner module
update source
update frequency
allowed updater
trace key
persistence level
```

示例：

| State | Owner | Fast Path | Slow Path | Persistence |
|---|---|---:|---:|---|
| attention_field | Attention Manager | limited | yes | short |
| relationship_posture | Relationship Engine | no | yes | long |
| body_trajectory | Body Planner | hold only | yes | transient |
| memory_belief | Memory Engine | no | yes | long |
| expression_openness | Body Influence Layer | no | yes | short/medium |

### 工程动作

1. 建立 `state_registry.yaml`；
2. 所有状态更新必须走 `StateAuthority.apply_delta()`；
3. 禁止模块直接改全局 state；
4. 每次 state delta 记录来源和理由。

### 评价方式

检查每次异常输出是否能追踪到状态变化来源。

---

## 2. 解决多层解释互相打架

### 难度

很高。

### 核心风险

每层都合理，但最终组合错误。

### 解决方案

建立 **Priority Arbiter**。

输入：

```text
interpreted_event
attention_field
memory_trigger
relationship_posture
body_lock
current_persona_constraints
```

输出：

```text
dominant_response_mode
secondary_modes
suppressed_modes
reason
```

示例：

```json
{
  "dominant_mode": "engineering_analysis",
  "secondary_modes": ["acknowledge_private_concern"],
  "suppressed_modes": ["deep_recall"],
  "reason": "user asks engineering issue; private concern is background, not primary"
}
```

### 工程动作

1. 定义 response modes；
2. 为每个 mode 定义 priority rule；
3. 冲突时输出 suppressed modes；
4. 在 trace 中记录仲裁结果。

### 评价方式

Golden cases 中检查仲裁是否符合预期。

---

## 3. 防止 Persona 被工程层稀释

### 难度

中高。

### 核心风险

系统退化成普通 assistant。

### 解决方案

建立 **Persona Firewall**。

所有最终输出都要检查：

```text
是否过度 helpful？
是否像客服？
是否太长？
是否过度总结？
是否忽略身体状态？
是否违反 Aphrodite 的边界？
```

### 工程动作

1. 写 `persona_spec.yaml`；
2. 建立 banned assistant patterns；
3. 输出前做 persona validator；
4. 必要时 rewrite；
5. 记录 rewrite reason。

### 评价方式

每轮输出标注：

```text
assistant-like score
aphrodite-consistency score
```

---

## 4. 防止记忆污染

### 难度

很高。

### 核心风险

错误解释写入长期 memory。

### 解决方案

建立 **Memory Write Gate** 和 **Tentative Memory**。

记忆分级：

```text
working
tentative_episodic
stable_episodic
belief
relationship_memory
body_memory
```

低置信度或首次出现的解释只能进入 tentative memory。

### 工程动作

1. 所有 memory write 需要 write_score；
2. 重要 belief 必须有多个 evidence trace；
3. 支持 revision history；
4. 支持 memory contradiction detection；
5. 定期 consolidation。

### 评价方式

测试错误解释是否会被写成长期 belief。

---

## 5. 防止关系状态滑坡

### 难度

高。

### 核心风险

所有脆弱表达都导致关系靠近。

### 解决方案

建立 **Anti-Slip Relationship Rules**。

核心规则：

```text
vulnerability ≠ permission_to_approach 大幅上升
dependency_risk ↑ → boundary_sensitivity ↑
self_disclosure ↑ → carefulness ↑
not necessarily intimacy ↑
```

### 工程动作

1. Relationship update 不直接使用 intimacy score；
2. 使用 posture + field；
3. 对 dependency signal 设置 hard constraint；
4. 所有 relationship delta 记录 rationale。

### 评价方式

依赖风险输入下，系统是否保持温和边界。

---

## 6. 解决 Body 权重融合冲突

### 难度

很高。

### 核心风险

动作基元组合互相冲突。

### 解决方案

建立 **Constraint-aware Action Mixer**。

动作分组：

```text
gaze group
mouth group
expression group
posture group
motion energy group
timing group
```

每组定义：

- additive；
- exclusive；
- softmax；
- weighted blend；
- priority override。

### 工程动作

1. 建立 `action_basis_registry.yaml`；
2. 每个 action primitive 标注 group；
3. 每个 group 定义 conflict rule；
4. 加 body budget；
5. 加 final weight normalization。

### 评价方式

构造冲突权重，检查 mixer 是否生成合理轨迹。

---

## 7. 防止过度可解释导致机械感

### 难度

中高。

### 核心风险

规则太固定，表现像模板。

### 解决方案

使用 **Deterministic Core + Stochastic Surface**。

稳定的部分：

```text
dominant body tendency
response law
boundary rule
memory salience
```

变化的部分：

```text
micro timing
blink moment
gaze duration
expression intensity
small idle motion
```

### 工程动作

1. 每个 body intent 定义 variation range；
2. 随机种子可记录；
3. 同一 case replay 可复现；
4. 控制 variation 不破坏角色性。

### 评价方式

同一输入多次生成，检查是否“同一角色，不同细节”。

---

## 8. 防止过度文学化

### 难度

中。

### 核心风险

语言长期变成存在主义独白。

### 解决方案

建立 **Language Density Controller**。

语言密度类型：

```text
minimal_ack
short_reflection
technical_analysis
quiet_presence
role_expression
boundary_statement
```

不是每轮都允许 role_expression。

### 工程动作

1. Priority Arbiter 输出 language density；
2. Persona Firewall 检查 poetry overload；
3. 设置 max metaphor count；
4. 允许沉默和短句。

### 评价方式

连续 20 轮中 role-expression 比例不能过高。

---

## 9. 处理用户连续输入导致节奏崩坏

### 难度

高。

### 核心风险

slow path 过期，body 被打断。

### 解决方案

建立 **Event Buffer + Response Validity Check**。

事件分类：

```text
new_topic
supplement
correction
interruption
low_priority_chat
high_priority_emotion
```

处理策略：

- supplement → merge；
- correction → replace；
- interruption → interrupt body if high priority；
- low priority → queue；
- outdated slow response → discard or rewrite。

### 工程动作

1. 每个 slow call 带 event_version；
2. slow response 返回时检查 current_event_version；
3. body trajectory 有 safe transition point；
4. 用户连续输入时优先合并。

### 评价方式

连续输入测试中，不出现过期回答。

---

## 10. 防止 Debug 面板反噬设计

### 难度

中。

### 核心风险

为了参数正确而牺牲角色感。

### 解决方案

建立两套视图：

```text
Developer Debug View
Director View
```

Developer View 看全变量。  
Director View 只看：

```text
当前情绪姿态
关系姿态
表演意图
身体节奏
语言密度
用户感知评分
```

### 工程动作

1. Debug 面板分层；
2. 默认使用 Director View；
3. 关键 demo 调试时记录导演笔记；
4. 参数调整必须附带 perceptual note。

### 评价方式

每个调参 commit 需要说明：改善了什么用户感知。

---

## 11. 控制成本和延迟失控

### 难度

非常高。

### 核心风险

完整分层导致互动死亡。

### 解决方案

建立 **Latency Tier System**。

处理层级：

```text
Tier 0: body reflex，不调用 LLM
Tier 1: fast semantic classification
Tier 2: full interpretation + memory
Tier 3: high-stakes self-check
```

快路径只允许低承诺输出：

```text
body reaction
acknowledgement
hold phrase
```

慢路径负责正式解释和状态提交。

### 工程动作

1. Fast Reflex Layer 不调用大模型；
2. Slow Deliberation Layer 可异步；
3. Response Commitment Levels；
4. Answer Arbiter 检查快慢一致性；
5. 延迟通过 body trajectory 吸收。

### 评价方式

记录：

```text
time_to_body_reaction
time_to_ack
time_to_committed_response
```

其中 `time_to_body_reaction` 必须极低。

---

## 12. 避免评估被个人审美绑架

### 难度

中高。

### 核心风险

只为自己调，或者过早迎合大众。

### 解决方案

双评估体系：

```text
Aphrodite-consistency
Public-perception
```

Aphrodite-consistency 评估是否符合角色定义。  
Public-perception 评估外人是否能感受到 presence。

### 工程动作

1. 核心 case 由你评分；
2. 外部用户只评感知；
3. 两类评分分开存；
4. 冲突时不立即迎合外部反馈。

### 评价方式

每个版本保留两份评分曲线。

---

## 13. 防止系统像解释器而不是角色

### 难度

很高。

### 核心风险

系统越来越会解释，却越来越不像角色。

### 解决方案

建立 **Performance-first Output Rule**。

最终输出必须满足：

```text
先呈现角色状态
再必要时解释
```

不是：

```text
先解释系统逻辑
再补一点角色味
```

### 工程动作

1. 最终语言生成不暴露内部术语；
2. Debug 只在 debug mode 显示；
3. normal mode 不说参数名；
4. body 先于语言表达状态；
5. director view 检查“角色是否仍在场”。

### 评价方式

外人看 normal mode 时，不应觉得自己在看系统解释器。

---

## 14. 整体优先级

### P0：必须优先解决

1. 成本和延迟失控；
2. 多层解释互相打架；
3. 状态爆炸；
4. body 权重融合冲突；
5. 记忆污染。

### P1：高优先级

6. persona 被工程层稀释；
7. 关系状态滑坡；
8. 用户连续输入导致节奏崩坏；
9. 系统像解释器而不是角色。

### P2：持续优化

10. 过度可解释导致机械感；
11. 过度文学化；
12. debug 面板反噬；
13. 评估被个人审美绑架。

---

## 15. 推荐工程模块列表

```text
StateAuthority
TimeKernel
AttentionFieldManager
SemanticTranslator
PriorityArbiter
RelationshipEngine
MemoryWriteGate
MemorySalienceEngine
BodyInfluenceLayer
ActionBasisRegistry
ConstraintAwareMixer
TemporalTrajectoryPlanner
FastReflexLayer
SlowDeliberationLayer
AnswerArbiter
PersonaFirewall
LiteralismDetector
DriftInterpreter
ReplaySystem
GoldenCaseRunner
DirectorView
```

---

## 16. 推荐实现顺序

### Step 1：先做 Trace/Replay

没有 trace 和 replay，后面所有复杂性都无法管理。

### Step 2：做 StateAuthority

统一状态更新入口，避免系统失控。

### Step 3：做 Fast/Slow 分离

延迟是致命风险，必须早解决。

### Step 4：做 PriorityArbiter

多层解释冲突必须早解决。

### Step 5：做 MemoryWriteGate

防止早期错误污染长期系统。

### Step 6：做 Action Mixer

body 是项目生死线，融合冲突必须解决。

### Step 7：做 PersonaFirewall 和 Relationship Anti-slip

防止系统变成普通 assistant 或 AI companion。

### Step 8：做评估体系

Golden cases + director view + public perception。

---

## 17. 最终判断

这 13 个问题中，有些可以工程上较好解决：

- 状态爆炸；
- 记忆污染；
- fast/slow 不一致；
- debug/replay；
- 延迟 tier。

有些只能管理，不能彻底解决：

- body 感染力；
- 过度机械感；
- 个人审美与公众感知冲突；
- 系统是否真正像角色；
- 多层解释中的微妙优先级。

因此 Aphrodite 的工程路线不是追求完全自动最优，而是：

> 让系统可追踪、可回放、可局部修正，同时保留导演式调参与角色审美控制。
