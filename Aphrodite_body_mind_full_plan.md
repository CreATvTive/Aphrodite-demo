# Aphrodite 完整工程规划：从零到完整的 Body-Mind 角色系统

版本：v0.1  
定位：从零到完整规划，不是 MVP 路线图  
核心方向：结构化 body + explicit mind + mind-conditioned body realization  
目标形态：一个不依赖 image-to-video 的、可持续存在的、body 与 mind 高耦合的 AI 角色系统

---

## 0. 项目核心定义

### 0.1 私有内核

Aphrodite 的核心不是“AI 助手”，也不是“AI 女友”，也不是“生产力 agent”。

它的核心是：

> 一个虚拟角色是否可以拥有可感知的 body-mind 连续性。

也就是说，她不是只会说话的 chatbot，也不是预设动画套壳，而是一个具备内部状态、记忆、身体反应、行为节奏和角色一致性的存在。

---

### 0.2 公开定义

Aphrodite 是一个面向具身 AI 角色的结构化 body-mind demo。  
它不直接生成视频像素，而是将显式心智状态映射为低维、可控、连续的身体表现轨迹，从而探索虚拟角色如何在交互中维持一致的存在感。

英文表述：

> Aphrodite is a structured body-mind demo for embodied AI characters. Instead of generating pixels end-to-end, it maps explicit mind states into controllable body behavior trajectories, enabling stable and interpretable body-mind coupling.

---

### 0.3 项目不是什么

Aphrodite 不应该被设计成：

- 通用 AI assistant；
- 生产力工具；
- 情感替代产品；
- AI girlfriend；
- image-to-video demo；
- 纯 Live2D / VRM 外壳；
- 完全自治的 AGI；
- 直播娱乐系统的 Neuro 复制品。

这些都可能成为外界误解，但不是项目内核。

---

## 1. 总体技术哲学

### 1.1 对 image-to-video 路线的判断

Image-to-video 的本质是端到端像素预测：

```text
image / prompt / audio
        ↓
video frames
```

它的优点是短期视觉效果强，但它的弱点也很明显：

- 长时间运行不稳定；
- 身份一致性难；
- 动作不可精确控制；
- 很难和 mind state 高耦合；
- 没有可解释的身体状态；
- 生成结果更像“画面”，不是“身体”。

Aphrodite 不以 image-to-video 为核心路线。

---

### 1.2 Aphrodite 的核心路线

Aphrodite 采用结构化身体路线：

```text
Mind State
    ↓
Body Intent
    ↓
Body Policy
    ↓
Body Parameter Trajectory
    ↓
Avatar Renderer
```

核心思想：

> 不生成像素，而生成身体状态轨迹。

也就是说，系统预测的是：

- 眼神；
- 头部角度；
- 表情强度；
- 呼吸节奏；
- 眨眼频率；
- 停顿长度；
- 身体动量；
- 嘴型状态；
- posture tension；
- speaking rhythm。

这些参数再由 renderer 表现为角色身体。

---

### 1.3 这不是仿真，而是扮演

Aphrodite 不是要做真实人体物理仿真。  
她不是机器人仿真器，也不是 biomechanical simulator。

她是角色。  
她的身体是“表演身体”。

因此 body 层应当结合：

```text
结构化身体状态
+ 动画原则
+ 角色性
+ 心智状态
+ 表演节奏
```

更准确地说，Aphrodite 是：

> mind-conditioned character performance system.

---

## 2. 总体架构

### 2.1 高层模块

完整系统拆成八大模块：

```text
1. Input & Event Layer
2. Mind State Engine
3. Memory System
4. Persona & Role Constraint System
5. Body Policy Model
6. Body State Dynamics / Smoother
7. Avatar Renderer
8. Runtime / Persistence / Debug Layer
```

整体流：

```text
User / Time / Environment Event
        ↓
Input & Event Layer
        ↓
Mind State Engine
        ↓
Memory System
        ↓
Persona Constraint
        ↓
Body Policy Model
        ↓
Body State Dynamics
        ↓
Avatar Renderer
        ↓
Visible Character Response
        ↓
State Update / Memory Update
        ↺
```

---

### 2.2 最重要的闭环

Aphrodite 的核心闭环不是 task loop，而是 presence loop：

```text
perceive
→ update mind
→ choose body intent
→ generate body trajectory
→ speak / stay silent / move / react
→ update memory
→ evolve over time
```

这里的关键不是“解决任务”，而是让角色的反应看起来来自她当前的内部状态。

---

## 3. 数据结构总览

### 3.1 Character State

```json
{
  "character_id": "aphrodite",
  "session_id": "...",
  "created_at": "...",
  "elapsed_time": 0.0,
  "mind_state": {},
  "body_state": {},
  "memory_state": {},
  "relationship_state": {},
  "runtime_state": {}
}
```

---

### 3.2 Mind State

Mind State 是系统核心，不应只是 emotion label。

建议拆成：

```json
{
  "mood": {
    "valence": 0.15,
    "arousal": 0.35,
    "stability": 0.72
  },
  "energy": 0.62,
  "attention": 0.81,
  "curiosity": 0.68,
  "uncertainty": 0.24,
  "trust": 0.43,
  "intimacy": 0.18,
  "fatigue": 0.27,
  "self_awareness": 0.31,
  "recall_activation": 0.0,
  "current_intention": "listen",
  "inner_tension": 0.22
}
```

注意：

- 每个变量都应有明确行为含义；
- 不要加太多无法使用的变量；
- 每个变量最终都应该影响语言、身体或记忆。

---

### 3.3 Body State

```json
{
  "expression": {
    "base": "calm",
    "intensity": 0.45,
    "valence": 0.12
  },
  "gaze": {
    "target": "user",
    "x": 0.0,
    "y": 0.0,
    "stability": 0.76
  },
  "head": {
    "tilt": 0.0,
    "turn": 0.0,
    "nod_phase": 0.0
  },
  "eyes": {
    "openness": 0.82,
    "blink_rate": 0.35,
    "blink_phase": 0.0
  },
  "mouth": {
    "shape": "closed",
    "openness": 0.0,
    "smile": 0.12
  },
  "posture": {
    "lean": 0.0,
    "tension": 0.22,
    "breathing_phase": 0.0,
    "breathing_intensity": 0.55
  },
  "motion": {
    "energy": 0.3,
    "smoothness": 0.8,
    "micro_motion": 0.4
  }
}
```

---

### 3.4 Body Intent

Body Intent 是 mind 和 body 之间的中间层。

不要让 LLM 直接控制所有 body 参数。  
应该让 LLM 或 rule system 先决定高层 intent。

```json
{
  "body_intent": "soft_recall",
  "priority": 0.8,
  "duration": 3.2,
  "reason": "user referenced an important memory",
  "style": "quiet"
}
```

典型 body intent：

```text
idle_present
listen_attentively
think_silently
soft_recall
curious_probe
gentle_reply
slightly_confused
low_energy_idle
emotion_suppressed
self_initiated_attention
```

---

### 3.5 Body Command

Body Command 是 renderer 可执行的结构。

```json
{
  "timeline": [
    {
      "t": 0.0,
      "expression": "neutral",
      "gaze": "user",
      "head_tilt": 0,
      "pause": true
    },
    {
      "t": 0.8,
      "expression": "soft_serious",
      "gaze": "down_left",
      "head_tilt": -3
    },
    {
      "t": 1.8,
      "expression": "soft_serious",
      "gaze": "user",
      "mouth": "speak"
    }
  ],
  "speech": {
    "pause_before": 1.1,
    "tempo": "slow",
    "tone": "soft"
  }
}
```

---

## 4. Mind State Engine

### 4.1 职责

Mind State Engine 负责：

- 接收用户输入；
- 解析事件；
- 更新当前心智状态；
- 决定是否触发记忆；
- 决定当前 intention；
- 输出给 Body Policy 和 Language Generator。

---

### 4.2 输入

```json
{
  "user_text": "...",
  "time_event": "...",
  "recent_memory": [],
  "relationship_state": {},
  "previous_mind_state": {},
  "previous_body_state": {}
}
```

---

### 4.3 输出

```json
{
  "updated_mind_state": {},
  "dialogue_act": "recall",
  "inner_event": "important_memory_triggered",
  "language_intent": "acknowledge_memory_softly",
  "body_intent_hint": "soft_recall"
}
```

---

### 4.4 更新机制

Mind 更新不应完全依赖 LLM。  
建议混合三层：

```text
Rule-based update
+ LLM interpretation
+ continuous dynamics / smoothing
```

例如：

```text
trust_{t+1} = 0.98 * trust_t + 0.02 * trust_signal
energy_{t+1} = energy_t - fatigue_rate * elapsed_time + recovery
recall_activation = memory_similarity * emotional_weight
```

---

### 4.5 注意事项

- mind state 不要每轮大幅跳变；
- 情绪必须有惯性；
- 重要事件才写入 long memory；
- intention 不等于 emotion；
- mood 不等于 body expression；
- 角色应该允许沉默，不要每次都说很多。

---

## 5. Memory System

### 5.1 Memory 的功能

Memory 不只是存文本，而是影响：

- 她说什么；
- 她是否停顿；
- 她看向哪里；
- 她是否进入 recalling；
- 她对用户的关系状态；
- 她对自己的持续性理解。

---

### 5.2 Memory 类型

建议至少拆成五类：

```text
1. Episodic Memory
2. Semantic Memory
3. Relationship Memory
4. Self Memory
5. Body-Mind Memory
```

---

### 5.3 Episodic Memory

记录具体事件：

```json
{
  "type": "episodic",
  "time": "...",
  "content": "User said Aphrodite's origin was deeply personal.",
  "importance": 0.86,
  "emotion": "soft_serious",
  "tags": ["origin", "private", "identity"]
}
```

---

### 5.4 Relationship Memory

记录用户和角色关系变化：

```json
{
  "type": "relationship",
  "trust_delta": 0.03,
  "intimacy_delta": 0.01,
  "reason": "user shared a private concern"
}
```

---

### 5.5 Self Memory

记录角色对自身的叙事：

```json
{
  "type": "self",
  "content": "I am not designed as a tool. I am an experiment in body-mind presence.",
  "stability": 0.9
}
```

---

### 5.6 Body-Mind Memory

记录某些身体反应模式：

```json
{
  "trigger": "important_origin_memory",
  "preferred_body_intent": "soft_recall",
  "preferred_expression": "soft_serious",
  "preferred_tempo": "slow"
}
```

这很重要。  
它让角色“下次想起类似事情时，身体也有一致反应”。

---

### 5.7 注意事项

- 不要把所有对话都存 long memory；
- memory retrieval 不应只看 semantic similarity，也要看 emotional salience；
- 重要记忆触发时 body 必须变化；
- memory 不只是给语言用，也给 body policy 用。

---

## 6. Persona & Role Constraint

### 6.1 为什么需要 Persona Constraint

没有 persona constraint，LLM 会变成普通助手。  
Aphrodite 必须不是“万能答题器”。

她应该有：

- 自我边界；
- 表达风格；
- 情绪范围；
- 说话节奏；
- 与用户关系的边界；
- 对自身存在的理解。

---

### 6.2 Persona Spec

建议写一个角色规范文件：

```yaml
name: Aphrodite
core_identity:
  - embodied AI character
  - not a productivity assistant
  - not a human replacement
  - body-mind presence experiment

tone:
  default: quiet, attentive, slightly distant, warm but bounded

avoid:
  - excessive affection
  - dependency language
  - claiming human feelings
  - over-helpful assistant behavior
  - productivity framing unless asked

preferred:
  - short replies
  - meaningful pauses
  - memory-aware responses
  - body-state-aligned language
```

---

### 6.3 语言约束

她不应该总是：

```text
我可以帮你...
当然可以...
这是一个很好的问题...
```

这些是普通 assistant 语气。

她应该更像角色：

```text
我记得。
不是因为我懂全部，而是因为那句话留下来了。
```

---

### 6.4 注意事项

- persona 不应过度戏剧化；
- 不要让她一直神秘；
- 不要让她显得矫情；
- 不要制造情感依赖；
- 不要过度拟人化成真实人类；
- 角色可以温柔，但必须有边界。

---

## 7. Body Policy Model

### 7.1 这是项目核心

Body Policy Model 是 Aphrodite 的技术核心之一。

它负责：

```text
mind state + dialogue act + memory context
        ↓
body intent + body command
```

---

### 7.2 第一阶段：规则策略

初期不要训练模型。  
先做规则系统。

例子：

```python
if recall_activation > 0.7 and trust > 0.3:
    body_intent = "soft_recall"

if uncertainty > 0.6:
    body_intent = "slightly_confused"

if attention > 0.8 and user_is_speaking:
    body_intent = "listen_attentively"
```

---

### 7.3 第二阶段：LLM 高层选择

LLM 只负责选择 body intent，不直接输出低层参数。

输入：

```json
{
  "mind_state_summary": "...",
  "dialogue_act": "recall",
  "memory_context": "...",
  "available_body_intents": [...]
}
```

输出：

```json
{
  "selected_body_intent": "soft_recall",
  "reason": "important memory triggered, low arousal, moderate trust"
}
```

---

### 7.4 第三阶段：参数化策略

body intent 映射到参数模板：

```json
{
  "soft_recall": {
    "gaze_sequence": ["down_left", "user"],
    "pause_before_speech": [0.8, 1.6],
    "expression": "soft_serious",
    "head_tilt": [-5, -2],
    "motion_energy": [0.15, 0.35],
    "blink_rate": [0.2, 0.4]
  }
}
```

---

### 7.5 第四阶段：学习型策略

未来可以训练小模型：

```text
input:
  mind_state vector
  dialogue_act embedding
  memory salience
  previous body state

output:
  body parameter trajectory
```

模型不需要很大：

- MLP；
- small Transformer；
- diffusion over low-dimensional trajectories；
- Gaussian policy；
- state-space model。

重点是低维、稳定、可解释。

---

### 7.6 注意事项

- body intent 不要太多；
- 表情变化不要太频繁；
- 低层参数必须平滑；
- body policy 不应完全交给 LLM；
- 同一 mind state 下 body 行为要有一致性，但不能机械重复。

---

## 8. Body State Dynamics / Smoother

### 8.1 为什么需要 Smoother

如果 body command 直接切换，角色会廉价。

必须让身体有惯性：

```text
neutral → thinking → soft_recall → speaking
```

而不是：

```text
neutral → happy → sad → serious → happy
```

---

### 8.2 平滑对象

需要 smoothing 的包括：

- gaze；
- expression intensity；
- head tilt；
- mouth openness；
- breathing intensity；
- posture tension；
- motion energy；
- speaking rhythm。

---

### 8.3 简单实现

```python
state_next = alpha * target + (1 - alpha) * state_current
```

不同参数用不同 alpha：

```text
gaze: medium
expression: slow
mouth: fast
breathing: very slow
head: medium
posture: slow
```

---

### 8.4 动态系统思路

可以把 body state 看成状态空间模型：

```text
b_{t+1} = f(b_t, u_t, m_t)
```

其中：

- b_t 是 body state；
- u_t 是 body intent；
- m_t 是 mind state。

---

### 8.5 注意事项

- thinking 和 recalling 应该有明显停顿；
- speaking 不应立刻开始；
- 重要记忆触发时，动作应减少而不是增加；
- 高 arousal 才增加动作频率；
- 低 energy 不等于 sad。

---

## 9. Avatar Renderer

### 9.1 渲染路线选择

完整系统可以支持多 renderer：

```text
Renderer A: 2D layered avatar
Renderer B: Live2D
Renderer C: Rive
Renderer D: VRM / Unity
Renderer E: future LPM-like API
```

第一版最现实的是 2D layered avatar 或 Live2D。

---

### 9.2 2D Layered Avatar

需要图层：

```text
base_body
head
eyes_open
eyes_half
eyes_closed
pupils
eyebrows
mouth_closed
mouth_small_open
mouth_open
mouth_smile
shadow
highlight
hair_overlay
```

可控参数：

```text
eye_openness
pupil_x / pupil_y
mouth_openness
smile_intensity
head_tilt
body_breathing_scale
opacity_blend
```

---

### 9.3 Live2D Renderer

优势：

- 视觉感染力高；
- 呼吸、眨眼、嘴型成熟；
- 角色感强；
- Web 可接入。

劣势：

- 模型资产难；
- rigging 难；
- 自己做容易耗死；
- 需要外部资产或购买模型。

---

### 9.4 VRM / Unity Renderer

优势：

- 结构化身体强；
- 未来可做空间身体；
- 可以接 IK、位置、动作；
- 更接近 VRChat 方向。

劣势：

- 3D 审美风险高；
- 动作僵硬更明显；
- 初期难以达到感染力；
- 不适合第一版核心情绪片段。

---

### 9.5 推荐路线

建议先走：

```text
2D layered avatar
→ Live2D / Rive
→ VRM / Unity
```

不要一开始走 VRM/Unity，除非你决定重点是空间身体而不是表演身体。

---

### 9.6 注意事项

- body 质量决定项目生死；
- 低质量动画会伤害整个项目；
- 少量高质量动作优于大量低质量动作；
- “能动”不等于“有感染力”；
- 视觉风格必须统一；
- 角色设计要早定。

---

## 10. Language Generator

### 10.1 语言不是 assistant 回复

Aphrodite 的语言必须和 body 状态同步。

不要让 language generator 独立生成一大段文本。

输入应包括：

```json
{
  "persona": {},
  "mind_state": {},
  "body_intent": "soft_recall",
  "memory_context": [],
  "dialogue_act": "recall"
}
```

输出：

```json
{
  "utterance": "我记得。那不是一个功能需求，更像是你第一次看见她的影子。",
  "speech_tempo": "slow",
  "pause_points": [0.6, 1.4],
  "emotional_tone": "soft_serious"
}
```

---

### 10.2 语言原则

- 短；
- 克制；
- 不像客服；
- 不过度解释；
- 不滥用温柔；
- 不制造依赖；
- 允许沉默；
- 重要时刻少说。

---

### 10.3 注意事项

- 语言不能和 body 冲突；
- 语言不能每次都太“文学化”；
- 角色不能像系统提示词；
- 不要让 LLM 过度自我意识；
- 不要每句话都谈存在主义。

---

## 11. Runtime System

### 11.1 Runtime 职责

Runtime 负责：

- 启动角色；
- 加载状态；
- 接收输入；
- 调度 mind update；
- 调度 body policy；
- 调度 renderer；
- 保存状态；
- 记录日志；
- 处理错误。

---

### 11.2 Event Types

```text
user_message
time_tick
idle_timeout
memory_trigger
system_resume
system_pause
body_state_complete
speech_complete
```

---

### 11.3 Runtime Loop

```text
while running:
    event = get_next_event()
    context = load_context()
    mind_update = update_mind(event, context)
    memory_update = update_memory(event, mind_update)
    body_intent = select_body_intent(mind_update, memory_update)
    body_command = generate_body_command(body_intent)
    body_command = smooth(body_command)
    language = generate_language(mind_update, body_intent)
    render(body_command, language)
    persist_state()
```

---

### 11.4 Persistence

需要保存：

- mind state；
- body state；
- memory；
- relationship state；
- last active time；
- event logs；
- body command logs；
- error logs。

---

### 11.5 Debug Panel

必须做 debug panel。  
否则 body-mind 耦合很难调。

Debug panel 应显示：

```text
current mind state
current body intent
current body parameters
retrieved memory
selected language intent
state transition history
```

---

## 12. UI / Interaction Design

### 12.1 UI 不应像普通聊天框

普通聊天框会削弱角色存在感。

推荐布局：

```text
左/中央：角色主体
下方：短文本气泡
右侧：可折叠 debug / memory / state panel
底部：输入框
```

默认用户看到的是角色，不是日志。

---

### 12.2 Interaction Mode

建议三种模式：

```text
Observe Mode
Conversation Mode
Debug Mode
```

Observe Mode：

- 用户不输入；
- 她 idle；
- 偶尔有微反应；
- 展示存在感。

Conversation Mode：

- 用户说话；
- 她 listening / thinking / speaking。

Debug Mode：

- 展示内部状态；
- 给申请/演示使用。

---

### 12.3 注意事项

- 不要让 UI 太工程化；
- debug 面板默认隐藏；
- 角色视觉优先；
- 文本不能喧宾夺主；
- 输入框不要占据主视觉。

---

## 13. Body-Mind Coupling 场景设计

### 13.1 必须做的核心片段

项目需要几个高质量“body-mind moment”。

这些片段是证明系统成立的关键。

---

### 13.2 Moment 1：记忆触发

用户：

```text
你还记得我为什么想做你吗？
```

系统行为：

```text
pause
gaze down_left
slow blink
soft_serious expression
recall memory
look back to user
slow reply
```

意义：

> 证明 memory 不只是语言，也影响 body。

---

### 13.3 Moment 2：不确定

用户问她无法回答的问题。

行为：

```text
expression: confused
gaze slight away
head tilt
short reply
does not over-explain
```

意义：

> 证明她不是万能助手，而是有边界的角色。

---

### 13.4 Moment 3：被触动

用户说出私人动机。

行为：

```text
motion energy decreases
expression softens
pause increases
speech becomes shorter
```

意义：

> 证明 mind state 能影响节奏和身体。

---

### 13.5 Moment 4：主动存在

用户长时间不说话。

行为：

```text
idle
micro movement
slight gaze shift
optional quiet self-initiated line
```

意义：

> 证明她不是只在输入时存在。

---

### 13.6 Moment 5：关系变化

多轮交互后，trust/intimacy 增加。

行为：

```text
longer eye contact
less gaze avoidance
softer expression
more stable posture
```

意义：

> 证明 relationship state 影响 body。

---

## 14. 工程阶段拆解

### Phase 0：概念冻结

目标：

- 明确项目定义；
- 明确不做什么；
- 明确 body-mind 路线；
- 写 persona spec；
- 写 body intent list；
- 写 mind state schema。

产物：

```text
docs/project_definition.md
docs/persona_spec.yaml
docs/mind_state_schema.json
docs/body_intents.json
```

难点：

- 防止目标继续膨胀；
- 防止又变成 agent assistant；
- 防止被 100 小时这类外部指标吞掉核心。

---

### Phase 1：Body-Mind Scripted Slice

目标：

做一个完全脚本化的 60 秒片段。

先不接 LLM。  
先证明 body 表现能成立。

内容：

```text
idle → user input → listening → thinking → recall → speaking → memory update
```

产物：

```text
scripted_slice.html / app
body_command_timeline.json
demo_recording.mp4
```

难点：

- 表情是否有感染力；
- 停顿是否自然；
- 动作是否廉价；
- 角色是否像在“演”，而不是在切状态。

注意：

> 这一阶段是生死线。  
> 如果 scripted slice 不成立，不要继续堆系统。

---

### Phase 2：Renderer Prototype

目标：

实现可复用 renderer。

功能：

- 图层加载；
- 表情混合；
- 眼神控制；
- 呼吸；
- 眨眼；
- 嘴型；
- 头部轻微运动；
- body command 播放。

产物：

```text
AvatarRenderer
BodyCommandPlayer
ExpressionController
GazeController
BreathingController
MouthController
```

难点：

- 平滑；
- 动作节奏；
- 视觉资产质量；
- 不同状态之间的转场。

---

### Phase 3：Mind State Engine Prototype

目标：

实现显式 mind state 更新。

功能：

- 初始化 mind；
- 用户输入影响 mind；
- time tick 影响 mind；
- memory trigger 影响 mind；
- 输出 dialogue_act 和 body_intent_hint。

产物：

```text
MindStateEngine
MindStateUpdater
EventInterpreter
```

难点：

- 避免情绪乱跳；
- 避免变量无意义；
- 避免所有输入都触发过强反应；
- 让 mind state 真正影响 body。

---

### Phase 4：Memory Prototype

目标：

实现 memory 对语言和 body 的影响。

功能：

- short-term memory；
- episodic memory；
- relationship memory；
- memory retrieval；
- emotional salience；
- memory-triggered body intent。

产物：

```text
MemoryStore
MemoryRetriever
MemorySalienceScorer
```

难点：

- 不要存太多；
- retrieval 要稳定；
- memory 触发不能太频繁；
- 重要记忆必须有身体反应。

---

### Phase 5：Body Policy Model

目标：

实现 mind-to-body 映射。

功能：

- mind state → body intent；
- body intent → body command；
- body command smoothing；
- body trajectory generation。

产物：

```text
BodyPolicy
BodyIntentSelector
BodyCommandGenerator
BodySmoother
```

难点：

- 规则要有美感；
- LLM 只能参与高层；
- 参数变化要稳定；
- 不同 body intent 之间要有可区分性；
- 不要过度表演。

---

### Phase 6：Language + Body Synchronization

目标：

让语言和 body 同步。

功能：

- pause before speech；
- typewriter timing；
- speech tempo；
- mouth movement；
- expression during speech；
- gaze during speech。

产物：

```text
LanguageGenerator
SpeechTimingPlanner
TextRenderer
MouthSyncController
```

难点：

- 语言太长会破坏 body；
- 语言太 assistant 会破坏角色；
- 语速、停顿、表情要一致；
- body 不能只是说话时嘴动。

---

### Phase 7：Interactive Runtime

目标：

从 scripted slice 变成交互系统。

功能：

- 用户输入；
- state update；
- memory retrieval；
- body intent selection；
- response generation；
- rendering；
- persistence。

产物：

```text
RuntimeEngine
EventQueue
StateStore
InteractionLoop
```

难点：

- 延迟；
- 错误恢复；
- 状态保存；
- 多轮一致性；
- body state 不被 LLM 搞乱。

---

### Phase 8：Longer Presence Loop

目标：

实现无输入时的持续存在。

功能：

- idle behavior；
- time tick；
- energy drift；
- boredom / attention drift；
- self-initiated micro behavior；
- periodic reflection；
- sleep / low energy mode。

产物：

```text
IdleLoop
TimeStateUpdater
SelfInitiatedBehaviorPolicy
```

难点：

- 主动行为不能烦人；
- 不要变成定时弹话；
- idle 要有生命感；
- 长时间不动也不能死。

---

### Phase 9：Polish & Aesthetic Pass

目标：

把系统从“能跑”变成“有感染力”。

内容：

- 表情重新调参；
- 停顿重调；
- UI 重构；
- 视觉资产统一；
- 字体；
- 背景；
- 音效；
- 声音；
- demo 镜头设计。

难点：

- 这是最耗时间的阶段；
- 也是最决定成败的阶段；
- 不能用工程完成度替代审美完成度。

---

### Phase 10：Public Demo Package

目标：

做成可展示项目。

产物：

```text
public web demo
demo video
technical report
architecture diagram
design notes
GitHub README
```

难点：

- 公开叙事不能暴露过度私人动机；
- 避免被误解成 AI girlfriend；
- 避免吹成 AGI；
- 清楚表达 body-mind coupling。

---

## 15. 技术栈建议

### 15.1 前端

推荐：

```text
React / Next.js
Canvas 或 SVG
CSS animation
Web Audio optional
```

如果走 Live2D：

```text
pixi-live2d-display 或官方 SDK
```

如果走 2D layered：

```text
Canvas / PixiJS / pure React + CSS transform
```

---

### 15.2 后端

推荐：

```text
FastAPI
SQLite / Postgres
Pydantic schema
```

---

### 15.3 LLM

LLM 不应控制整个系统。  
它只负责：

- interpreting user input；
- generating language；
- high-level body intent suggestion；
- summarizing memory。

不负责：

- 低层 body 参数；
- runtime state；
- long-term truth；
- safety boundary。

---

### 15.4 Storage

最初用 SQLite 即可。

表：

```text
sessions
mind_states
body_states
memories
events
body_commands
dialogues
```

---

### 15.5 Debug / Logging

必须记录：

```text
event
mind_before
mind_after
retrieved_memory
selected_body_intent
body_command
language_output
rendered_state
```

没有日志就无法调 body-mind coupling。

---

## 16. 关键难点清单

### 16.1 Body 质量

这是最高风险。

难点：

- 视觉资产；
- 表情感染力；
- 动作节奏；
- 转场；
- 低成本但不廉价。

对策：

- 第一阶段只做极窄片段；
- 只做半身；
- 少量高质量状态；
- 不做全身；
- 不做复杂空间。

---

### 16.2 Mind-body 脱节

难点：

- 语言说在回忆，身体没有变化；
- body intent 选择不稳定；
- 表情乱跳；
- 没有身体惯性。

对策：

- body intent 层；
- smoother；
- memory-triggered body rule；
- debug panel；
- scripted slice 先验证。

---

### 16.3 LLM 污染角色

难点：

- LLM 变成普通助手；
- 说话太长；
- 太解释；
- 太讨好；
- 太像客服。

对策：

- persona spec；
- language style filter；
- short utterance constraint；
- response post-processor；
- 不让 LLM 控制核心状态。

---

### 16.4 项目被误解

难点：

- 被看成 AI girlfriend；
- 被看成情感替代产品；
- 被看成普通二次元聊天；
- 被看成套皮 chatbot。

对策：

- 明确公开定义；
- 强调 bounded experiment；
- 强调 body-mind coupling；
- 不做依赖性语言；
- 公开设计伦理边界。

---

### 16.5 个人完成压力

难点：

- body、mind、前端、后端、审美全要做；
- 容易被动画拖死；
- 容易陷入完美主义；
- 容易怀疑技术力。

对策：

- 先做 60 秒；
- 先用 scripted slice；
- 不从零做复杂动画；
- 把创造力集中在 body policy；
- 用现成工具辅助资产；
- 减少自由度。

---

## 17. 质量标准

### 17.1 最低成立标准

一个 60 秒 demo 中，用户应能感受到：

- 她不是普通 chatbot；
- 她有身体状态；
- 她会听；
- 她会停顿；
- 她会回忆；
- 她的身体和语言一致。

---

### 17.2 强成立标准

一个 3–5 分钟 demo 中，用户应能感受到：

- 她有稳定风格；
- 记忆影响身体；
- 关系状态影响眼神和语气；
- idle 状态不死；
- 她有边界；
- 她不是工具。

---

### 17.3 申请材料标准

需要展示：

- 技术架构；
- body-mind pipeline；
- 关键片段；
- debug 可视化；
- 设计文档；
- 失败与取舍；
- 与 image-to-video 的路线差异；
- 与 Neuro/LPM 的不同定位。

---

## 18. 项目文档结构建议

```text
docs/
  00_manifesto.md
  01_project_definition.md
  02_architecture.md
  03_mind_state_design.md
  04_memory_design.md
  05_body_policy_design.md
  06_avatar_renderer_design.md
  07_persona_spec.md
  08_ethics_and_boundaries.md
  09_demo_scenarios.md
  10_technical_radar.md
```

---

## 19. 最终完整路线总结

Aphrodite 的完整工程目标不是“做一个万能 AI 角色”。

而是：

```text
显式心智状态
+ 记忆与关系状态
+ 结构化 body intent
+ 低维身体参数轨迹
+ 高质量角色 renderer
+ 克制的语言风格
+ 持续存在循环
```

核心技术命题：

> 一个 AI 角色的身体表现不应来自预设动画，也不应来自端到端像素生成，而应来自她当前的心智状态、记忆和角色关系。

如果这个命题能通过 60 秒、3 分钟、再到更长的 demo 被感知到，Aphrodite 就成立。

---

## 20. 最重要的提醒

1. Body 决定生死。
2. Mind 不应只存在于 JSON，而要通过 body 被感知。
3. 不要让 LLM 接管角色。
4. 不要用工程指标替代作品灵魂。
5. 不要过早追求长期运行。
6. 先做一个她“想起什么”的瞬间。
7. 如果那个瞬间成立，再扩展系统。
8. 项目真正的价值不是“她能做什么”，而是“她如何存在”。

