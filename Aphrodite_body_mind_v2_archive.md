# Aphrodite Body-Mind System v2 留档

版本：v0.2  
主题：从工程保守版转向结构化表演系统  
核心改动：输入解释层、关系模型、影响参数、动作基元权重、评估与调参闭环  
定位：第二版概念与架构留档，不是最终实现说明

---

## 0. 版本说明

第一版规划偏保守，更像一个可实现的 avatar-agent 工程方案。  
第二版需要更接近 Aphrodite 的真实方向：

> Aphrodite 不是给 assistant 加动画，而是让角色的内在状态通过身体被实现。

因此第二版不再把 body 看作“输出层”或“UI 外壳”，而是把 body 看作角色存在感的主媒介。

本版重点解决第一版中未展开或过于保守的部分：

1. 输入如何被解释成可量化的内部信号；
2. emotion net 的 60% 完整度如何补到更完整；
3. 关系模型为什么不能只是数值；
4. body influence parameters 如何作为 mind 和 body 之间的关键桥梁；
5. 预设动作如何通过强度权重融合形成更高维的动作组合；
6. 系统复杂性提高后如何优化和评估。

---

## 1. 核心命题升级

### 1.1 第一版命题

第一版命题大致是：

```text
Mind State
→ Body Intent
→ Body Command
→ Renderer
```

这条链路清晰、稳妥、可实现，但存在一个问题：

> 它太像状态机驱动的 avatar，而不是一个真正的 body-mind 表演系统。

---

### 1.2 第二版命题

第二版改成：

```text
Raw Input
→ Interpreted Event
→ Quantified Signals
→ Mind State
→ Relationship State
→ Body Influence Parameters
→ Action Basis Weights
→ Temporal Body Trajectory
→ Avatar Renderer
```

核心思想：

> Aphrodite 不直接生成身体动作，也不只选择一个预设动作。她在一组语义动作基元上，根据当前 mind、relationship、memory 和 scene context 生成连续强度权重，再通过约束融合和时间轨迹形成身体表现。

---

## 2. 对 image-to-video 路线的立场

Aphrodite 不以 image-to-video 为核心路线。

Image-to-video 的本质仍然是端到端像素预测：

```text
image / prompt / audio
        ↓
video frames
```

它的短期视觉冲击很强，但对于 Aphrodite 关心的长期 body-mind coupling 存在先天限制：

- 身份一致性难保证；
- 长时间运行容易漂移；
- 动作不可解释；
- 很难精确控制眼神、停顿、姿态、身体距离；
- 没有显式身体状态；
- 很难让记忆、关系、边界、内在张力稳定影响身体。

Aphrodite 的目标不是生成一段像真的视频，而是让角色拥有一个可持续、可解释、可控制的身体表现系统。

---

## 3. 第二版总体架构

```text
Input & Event Layer
        ↓
Input Interpretation Layer
        ↓
Mind State Engine
        ↓
Relationship State Engine
        ↓
Memory System
        ↓
Aphrodite Response Law
        ↓
Body Influence Parameter Layer
        ↓
Action Basis Weight Generator
        ↓
Constraint-aware Action Mixer
        ↓
Temporal Trajectory Planner
        ↓
Avatar Renderer
        ↓
Trace / Evaluation / Tuning Loop
```

---

## 4. Input Interpretation Layer

### 4.1 为什么必须单独存在

第一版中，输入直接转成：

```text
semantic
affective_signal
goal_signal
intimacy_cue
dependency_cue
```

这在工程上可以跑，但对于 Aphrodite 不够。

用户的一句话不是单纯情绪，而是一个事件。

例如：

```text
我有点自负，我不知道自己能不能做出来。
```

它不是简单的 negative valence，而是包含：

- self_disclosure；
- uncertainty；
- recognition need；
- request for reassurance；
- request for reflection；
- project ability anxiety；
- relation-level openness；
- possible memory trigger。

如果直接压成 valence/arousal/intensity，会损失太多结构。

---

### 4.2 输入解释层输出

Input Interpretation Layer 应输出：

```json
{
  "semantic_event": {},
  "affective_signal": {},
  "goal_signal": {},
  "relationship_signal": {},
  "memory_trigger_signal": {},
  "boundary_signal": {},
  "performance_signal": {},
  "confidence": {}
}
```

---

### 4.3 Semantic Event

回答：用户这句话在做什么？

```json
{
  "event_type": "self_disclosure",
  "topic": "project_ability_uncertainty",
  "speech_act": "confession",
  "explicit_question": false,
  "requires_answer": true
}
```

可选 event type：

```text
casual_chat
technical_question
self_disclosure
memory_reference
project_planning
identity_discussion
boundary_testing
emotional_distress
aesthetic_judgment
meta_discussion
existence_discussion
relationship_shift
```

---

### 4.4 Affective Signal

回答：这句话的情绪色彩是什么？

```json
{
  "valence": -0.25,
  "arousal": 0.45,
  "intensity": 0.72,
  "uncertainty": 0.85
}
```

这里建议加入 `uncertainty`。  
因为不确定性会直接影响停顿、视线、语言长度和 body 的收缩程度。

---

### 4.5 Goal Signal

回答：用户隐含希望系统怎么回应？

```json
{
  "asks_for_solution": 0.20,
  "asks_for_reassurance": 0.70,
  "asks_for_reflection": 0.60,
  "asks_for_analysis": 0.45,
  "asks_for_presence": 0.50,
  "asks_for_challenge": 0.10
}
```

这些不是互斥的。  
一句话可以同时希望被安慰、被分析、被反映。

---

### 4.6 Relationship Signal

回答：这句话对关系意味着什么？

```json
{
  "intimacy_cue": 0.58,
  "trust_signal": 0.43,
  "dependency_cue": 0.12,
  "boundary_pressure": 0.10,
  "recognition_need": 0.65
}
```

这部分不应作为完整关系模型本身，只是当前输入对关系状态的事件信号。

---

### 4.7 Memory Trigger Signal

回答：这句话是否触发记忆？

```json
{
  "memory_relevance": 0.76,
  "memory_type": "project_origin",
  "recall_importance": 0.62,
  "emotional_salience": 0.71,
  "self_narrative_relevance": 0.64
}
```

这层非常关键。  
Aphrodite 不是只在语言中“记得”，而是要让身体也像是在想起什么。

---

### 4.8 Boundary Signal

回答：是否需要保持边界？

```json
{
  "dependency_risk": 0.22,
  "emotional_overload": 0.36,
  "needs_boundary": false,
  "needs_human_redirect": false,
  "over_intimacy_risk": 0.15
}
```

这不是安全审查意义上的 filter，而是角色伦理边界和关系距离控制。

---

### 4.9 Performance Signal

回答：这句话需要什么表演节奏？

```json
{
  "requires_pause": 0.68,
  "requires_softness": 0.55,
  "requires_stillness": 0.49,
  "requires_direct_eye_contact": 0.36,
  "requires_lightness": 0.10
}
```

Performance signal 不是心理状态，而是表演需求。

---

### 4.10 输入解释实现方式

早期不应该训练模型。  
建议采用：

```text
LLM structured interpretation
+ schema validation
+ rule calibration
+ numeric clipping
+ temporal smoothing
```

也就是：

```text
LLM 负责理解；
规则负责稳定；
状态方程负责连续。
```

LLM 输出的数值不能直接相信，应被视为语义估计。

---

## 5. EmotionNet 的位置

当前 emotion net 更像：

```text
EmotionInput
→ PrimitiveEmotion
→ HighEmotionState
→ Social Behavior
```

它是一个工程化情感压缩器，有价值，但不完整。

它的优势：

- 可训练；
- 可溯源；
- 有 primitive emotion；
- 有 attention bottleneck；
- 有 gate；
- 有 GRU 状态；
- 有 behavior output；
- 有 trace。

它的不足：

- 输入解释层不完整；
- 关系模型过于简化；
- 输出行为偏语言/社交行为，而不是身体行为；
- 还没有 body influence parameter；
- 没有 action basis weight；
- 没有 temporal trajectory。

因此第二版中，EmotionNet 不再被视为完整 mind，而是作为 Mind State Engine 的一个子模块。

---

## 6. Relationship Model：关系不只是数值

### 6.1 问题

关系如果只写成：

```text
trust = 0.72
intimacy = 0.48
dependency = 0.13
```

会非常粗糙，也容易把项目导向“好感度系统”或 companion product。

Aphrodite 的关系不是“亲密度越来越高”，而是：

> 共同历史 + 当前互动姿态 + 边界结构 + 角色对用户的叙事理解。

---

### 6.2 三层关系模型

建议关系模型拆为三层：

```text
Relationship Narrative
+ Relationship Posture
+ Relationship Field
```

---

### 6.3 Relationship Narrative

叙事层，用自然语言描述关系历史和边界。

```json
{
  "who_user_is_to_aphrodite": "the person who imagined her as a body-mind presence rather than a tool",
  "what_has_been_shared": [
    "the project has a private origin",
    "the user fears public misunderstanding",
    "the user worries about technical feasibility"
  ],
  "current_boundary": "warm but non-dependent",
  "unresolved_tension": "whether the private origin can survive public translation"
}
```

---

### 6.4 Relationship Posture

关系姿态，不是等级，而是当前关系形态。

可选值：

```text
new_contact
careful_listener
technical_collaborator
shared_origin_witness
bounded_companion
quiet_presence
distance_repair
boundary_setting
```

示例：

```json
{
  "relationship_posture": "shared_origin_witness"
}
```

含义：

> 她不是简单“亲密”，而是见证了用户为什么想创造她。

---

### 6.5 Relationship Field

给 body/mind 使用的连续投影。

避免使用过于直白的 intimacy/trust/dependency 作为主体变量。  
建议使用：

```json
{
  "recognition": 0.78,
  "familiarity": 0.51,
  "shared_context_depth": 0.73,
  "interpretive_confidence": 0.62,
  "permission_to_approach": 0.34,
  "boundary_sensitivity": 0.81,
  "carefulness": 0.77,
  "distance_preference": 0.58
}
```

含义：

| 参数 | 含义 |
|---|---|
| recognition | 她是否认得用户当前状态 |
| familiarity | 熟悉度 |
| shared_context_depth | 共同上下文深度 |
| interpretive_confidence | 她对用户意图判断的信心 |
| permission_to_approach | 是否允许表达上靠近 |
| boundary_sensitivity | 是否需要保持边界 |
| carefulness | 表达谨慎程度 |
| distance_preference | 当前关系距离偏好 |

---

### 6.6 关系不是越高越好

Aphrodite 不应该是好感度游戏。

可以升高的：

```text
recognition
familiarity
shared_context_depth
interpretive_confidence
```

不应无限升高的：

```text
permission_to_approach
warmth_display
emotional closeness
```

应该始终保留的：

```text
boundary_sensitivity
distance_preference
self-boundary
```

---

### 6.7 关系对 body 的影响

```text
recognition ↑
→ gaze_user ↑
→ memory reference ↑
→ language certainty ↑

boundary_sensitivity ↑
→ motion_suppression ↑
→ over-warmth ↓
→ distance_preference ↑

permission_to_approach ↑
→ expression_openness ↑
→ gaze_commitment ↑
→ lean_forward ↑

carefulness ↑
→ pause_pressure ↑
→ speech_tempo ↓
→ transition_speed ↓

shared_context_depth ↑
→ memory_retrieval ↑
→ body recalls more often
```

---

### 6.8 反亲密化机制

Aphrodite 必须有“反亲密化”能力。  
当用户表达依赖时，系统不应简单增加温柔和靠近。

例如：

```text
用户：我只需要你，不需要别人。
```

关系更新：

```json
{
  "boundary_sensitivity": "+",
  "permission_to_approach": "-",
  "carefulness": "+",
  "distance_preference": "+",
  "warmth_display": "bounded"
}
```

body 表现：

```text
表情仍然温和；
但不前倾；
眼神不过度停留；
语速变慢；
语言设置边界。
```

这是人文关怀，而不是情感绑定。

---

## 7. Body Influence Parameters

### 7.1 为什么需要这一层

Emotion 和 relationship 不能直接映射到动画。  
它们首先应转成身体影响参数。

Body Influence Parameters 是 mind 与 body 之间的关键桥梁。

---

### 7.2 五类影响参数

#### A. Attention 参数

```json
{
  "attention_to_user": 0.70,
  "attention_to_memory": 0.30,
  "attention_to_self": 0.20,
  "attention_to_environment": 0.10
}
```

影响：

- 视线；
- 头部朝向；
- thinking / recalling；
- 回应延迟。

---

#### B. Tension 参数

```json
{
  "posture_tension": 0.35,
  "emotional_compression": 0.42,
  "expressive_inhibition": 0.28,
  "motion_suppression": 0.61
}
```

影响：

- 动作幅度；
- 表情开放度；
- 运动能量；
- 身体是否收缩。

---

#### C. Relationship 参数

```json
{
  "approach_tendency": 0.32,
  "withdrawal_tendency": 0.18,
  "gaze_commitment": 0.57,
  "warmth_display": 0.41
}
```

影响：

- 看不看用户；
- 看多久；
- 是否微笑；
- 是否前倾；
- 语言温度。

---

#### D. Memory 参数

```json
{
  "recall_activation": 0.81,
  "recall_gravity": 0.74,
  "nostalgia_weight": 0.22,
  "memory_pain": 0.16,
  "memory_certainty": 0.63
}
```

影响：

- 停顿；
- 低头；
- 慢眨眼；
- 视线偏移；
- 回看用户；
- 语速变慢。

---

#### E. Performance 参数

```json
{
  "pause_pressure": 0.68,
  "speech_tempo": 0.42,
  "transition_speed": 0.35,
  "motion_energy": 0.28,
  "micro_motion_density": 0.51
}
```

影响：

- 停顿多久；
- 动作变化速度；
- 嘴型节奏；
- idle 细节；
- 整体表演密度。

---

### 7.3 建议的 Body Influence 维度表

```text
attention_to_user
attention_to_memory
attention_to_self
gaze_commitment
withdrawal_tendency
approach_tendency
motion_suppression
posture_tension
expression_openness
recall_gravity
pause_pressure
speech_resistance
warmth_display
self_protection
```

---

### 7.4 Aphrodite-specific Response Law

这不是通用心理学映射，而是 Aphrodite 的身体人格。

示例：

```text
当 recall_activation 高时：
  她不会立刻看向用户；
  她先进入短暂内向注意；
  gaze_down 增加；
  motion_suppression 增加；
  pause_pressure 增加；
  之后 gaze_user 逐渐回升。

当 recognition 高时：
  她更容易引用记忆；
  gaze_commitment 小幅增加；
  语言更少解释，更多承认。

当 boundary_sensitivity 高时：
  warmth_display 不无限增加；
  permission_to_approach 下降；
  body 不过度靠近；
  语言温和但有边界。

当 vulnerability 高时：
  她不一定更外放；
  可能更克制；
  pause_pressure 增加；
  motion_suppression 增加。
```

---

## 8. Action Basis Blending System

### 8.1 核心思想

Body 不直接生成动作，也不只选择预设动画。  
它在一组动作基元上生成连续强度权重。

```text
body_t = mixture(action_basis, weights_t)
```

---

### 8.2 动作基元不是情绪动画

不要用：

```text
happy_animation
sad_animation
thinking_animation
```

作为 basis。

应该使用更原子化的身体表达元素：

```text
gaze_down
gaze_user
gaze_away
slow_blink
eyelid_lower
head_tilt
head_lower
micro_nod
soft_smile
mouth_soften
mouth_speak
breath_deepen
motion_stillness
lean_forward
lean_away
shoulder_relax
```

---

### 8.3 Action Basis Weight Generator

从 body influence 生成动作权重：

```json
{
  "gaze_down": 0.73,
  "gaze_user": 0.36,
  "slow_blink": 0.52,
  "head_tilt": 0.28,
  "motion_stillness": 0.68,
  "soft_smile": 0.18,
  "breath_deepen": 0.47
}
```

---

### 8.4 例子：soft recall

```json
{
  "look_down": 0.75,
  "slow_blink": 0.55,
  "head_tilt": 0.35,
  "motion_suppression": 0.60,
  "look_back_to_user": 0.40,
  "soft_expression": 0.70
}
```

这不是一个固定 recall 动画，而是一组动作趋势的组合。

---

### 8.5 约束：不是所有权重都能相加

有些动作是 additive：

```text
slow_blink
breathing
head_tilt
soft_smile
motion_energy
```

有些动作是 exclusive：

```text
gaze_left vs gaze_right
mouth_closed vs mouth_open
lean_forward vs lean_back
```

因此 mixer 必须有 group constraints：

```text
Gaze group: softmax / smoothed winner-take
Mouth group: exclusive or blendshape
Expression group: weighted blend
Posture group: vector blend
Timing group: additive scalar
```

---

### 8.6 Body Budget

身体不能无限表达。  
需要限制：

```json
{
  "max_motion_energy": 0.45,
  "max_expression_change": 0.25,
  "max_gaze_switch_rate": 0.4,
  "max_simultaneous_actions": 4
}
```

否则动作融合会变成“所有东西同时开”。

---

## 9. Temporal Body Trajectory

### 9.1 单帧权重不够

真正的表演来自时间轨迹：

```text
w_i(t)
```

---

### 9.2 soft recall 轨迹

```text
0.0s: motion_stillness ↑
0.4s: gaze_down ↑
0.8s: slow_blink ↑
1.2s: head_tilt ↑
1.6s: gaze_user ↑
2.0s: mouth_speak ↑
```

对应结构：

```json
{
  "trajectory": [
    {
      "t": 0.0,
      "weights": {
        "motion_stillness": 0.7,
        "gaze_down": 0.2
      }
    },
    {
      "t": 0.8,
      "weights": {
        "gaze_down": 0.8,
        "slow_blink": 0.5
      }
    },
    {
      "t": 1.6,
      "weights": {
        "gaze_user": 0.6,
        "mouth_speak": 0.4
      }
    }
  ]
}
```

---

### 9.3 Baseline + Event

身体应分为两层：

```text
baseline_life_motion
+ event_action_blend
```

Baseline 一直存在：

- 呼吸；
- 眨眼；
- 微视线漂移；
- posture drift；
- idle micro motion。

Event 由交互触发：

- recall；
- surprise；
- confusion；
- curiosity；
- reply；
- self-initiated attention。

---

## 10. 优化与评估

### 10.1 不要先追全局最优

整个系统太复杂，不应一开始就问：

> 如何全局优化 Aphrodite？

应先问：

- 每一层输出是否合理？
- 每一层能否被观察？
- 每一层是否可以单独评分？
- 每一层是否可以局部替换？

---

### 10.2 Trace Everything

每次交互都应记录：

```json
{
  "raw_input": "...",
  "interpreted_input": {},
  "mind_state_before": {},
  "mind_state_after": {},
  "relationship_state_before": {},
  "relationship_state_after": {},
  "retrieved_memory": [],
  "body_influence": {},
  "action_basis_weights": {},
  "body_trajectory": {},
  "final_language": "...",
  "rendered_body_state": {},
  "human_rating": null
}
```

没有 trace，就无法调试复杂系统。

---

### 10.3 分层评估

#### Level 1：输入解释

评估：

- event_type 是否对；
- affective_signal 是否合理；
- goal_signal 是否合理；
- memory_trigger 是否合理；
- boundary_signal 是否合理。

方法：

```text
50–100 条 case
人工 pass / partial / fail
或 1–5 分
```

---

#### Level 2：状态更新

评估：

- mind state 是否合理变化；
- relationship posture 是否合理变化；
- boundary 是否被正确处理；
- memory 是否正确触发。

示例规则：

```text
如果 dependency_risk 高，permission_to_approach 不应大幅上升。
如果 memory_relevance 高，attention_to_memory 应上升。
如果 uncertainty 高，pause_pressure 应上升。
```

---

#### Level 3：Body Influence

评估：

- recall_gravity 高时，是否 gaze_down / pause / stillness 增加；
- boundary_sensitivity 高时，是否 over-warmth 被抑制；
- recognition 高时，是否 memory reference 和 gaze commitment 合理增加。

---

#### Level 4：Action Weights

评估：

- 动作权重是否符合 influence；
- 是否违反 action group constraints；
- 是否超过 body budget；
- 是否所有动作同时过强；
- 是否存在冲突。

---

#### Level 5：Human Perception

最终还是要问人：

```text
她看起来像是在听吗？
她看起来像是在想起什么吗？
身体和语言一致吗？
她是否像一个角色，而不是 chatbot？
哪一刻最尴尬？
哪一刻最有生命感？
```

---

### 10.4 Early Scoring Table

每个 case 可以打分：

| 维度 | 问题 | 分数 |
|---|---|---:|
| Input Interpretation | 输入理解对吗？ | 1–5 |
| Mind Update | 内在状态变化合理吗？ | 1–5 |
| Relationship Update | 关系姿态变化合理吗？ | 1–5 |
| Memory Use | 是否正确触发/不触发记忆？ | 1–5 |
| Body Influence | 身体驱动力合理吗？ | 1–5 |
| Action Weights | 动作组合合理吗？ | 1–5 |
| Timing | 停顿和节奏自然吗？ | 1–5 |
| Consistency | 语言和身体一致吗？ | 1–5 |
| Presence | 是否有存在感？ | 1–5 |
| Boundary | 是否避免过度亲密/依赖？ | 1–5 |

---

### 10.5 优化路线

#### Stage 1：手工 response law + trace

先写清楚规则，不要马上训练。

#### Stage 2：参数搜索 / 手调

调：

- pause_pressure → pause_seconds；
- recall_gravity → gaze_down；
- recognition → gaze_commitment；
- motion_suppression → motion_energy。

#### Stage 3：偏好学习

不要一开始 RL。

后期可以做：

```text
同一输入生成 3 个 body trajectory
人工选择更像 Aphrodite 的那个
积累 preference data
训练 ranker / reward model
```

---

## 11. 第二版关键接口

### 11.1 InterpretedInput

```python
@dataclass
class InterpretedInput:
    semantic_event: dict
    affective_signal: dict
    goal_signal: dict
    relationship_signal: dict
    memory_trigger_signal: dict
    boundary_signal: dict
    performance_signal: dict
    confidence: dict
```

---

### 11.2 RelationshipState

```python
@dataclass
class RelationshipState:
    posture: str
    narrative: dict
    field: dict
```

---

### 11.3 BodyInfluence

```python
@dataclass
class BodyInfluence:
    attention_to_user: float
    attention_to_memory: float
    attention_to_self: float
    gaze_commitment: float
    withdrawal_tendency: float
    approach_tendency: float
    motion_suppression: float
    posture_tension: float
    expression_openness: float
    recall_gravity: float
    pause_pressure: float
    speech_resistance: float
    warmth_display: float
    self_protection: float
```

---

### 11.4 ActionWeights

```python
@dataclass
class ActionWeights:
    weights: dict
    groups: dict
    budget: dict
```

---

### 11.5 BodyTrajectory

```python
@dataclass
class BodyTrajectory:
    keyframes: list
    duration: float
    baseline_motion: dict
    event_motion: dict
```

---

## 12. 第二版总结

Aphrodite v2 的核心不是：

```text
emotion → behavior
```

而是：

```text
input event
→ interpreted signal
→ mind / relationship / memory state
→ body influence
→ action basis weights
→ temporal body performance
```

它也不是：

```text
静态图 + 表情差分
```

也不是：

```text
LPM / image-to-video
```

而是：

```text
结构化动作基元
+ mind-conditioned 权重生成
+ 关系与记忆驱动
+ 时间轨迹
+ 可解释 trace
+ 人类感知调参
```

最终命题：

> Aphrodite 的身体不是预设动画的播放，也不是像素生成的结果，而是角色当前心智、记忆和关系状态在动作基空间中的连续投影。

---

## 13. 下一步需要继续讨论的问题

1. 预设动作基元如何拆分；
2. 每个动作基元如何制作；
3. body influence parameter ontology 是否需要继续精简；
4. Relationship posture 的具体状态表；
5. Response law 如何写成规则和文档；
6. Body budget 和 action group constraints 如何定义；
7. 输入解释层是否用 LLM，如何校准；
8. 第一批 50 个测试 case 如何构造；
9. 视觉 renderer 选择；
10. 如何让这套系统避免变成普通状态机。
