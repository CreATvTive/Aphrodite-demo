# Aphrodite Architecture Map

> 生成时间: 2026-05-05 | 模式: Aphrodite Architect (只读分析) | 版本: v1

---

## 1. User Input 进入系统后的完整链路

系统中存在 **两条并发的主链路**，最终交汇于 `_emit_presence_reply()`。

### 链路 A：Brain Loop（事件驱动分发）

```
event_q.get()                                          [agentlib/runtime_engine.py:1763]
  │
  ├─ _parse_event(evt)                                 [agentlib/runtime_engine.py:1771]
  │
  ├─ _perception_fusion()                              [agentlib/runtime_engine.py:1638]
  │     └─ 仅做 command_hint:
  │          - "/" 开头 → "slash_command"
  │          - 含 "selfdrive"/"debug"/"autofix" → "runtime_control"
  │
  ├─ _decision_core()                                  [agentlib/runtime_engine.py:1656]
  │     └─ if-else 链:
  │          ├─ idle       → respond_idle
  │          ├─ debug      → debug_command
  │          ├─ selfdrive  → selfdrive_control
  │          ├─ status     → selfdrive_status
  │          └─ default    → llm_chat
  │
  └─ 根据 decision.action 分支:
       ├─ action 路径: _action_planner()
       │     └─ debug_command / selfdrive_control / selfdrive_status
       │
       ├─ immediate_protocol.send()                    [agentlib/runtime_immediate_protocol.py:39]
       │     ├─ FastGate.infer()                       [agentlib/router/fast_gate.py:17]
       │     │     └─ CHAT(emotional_support) / EXECUTE(task_request) / CHAT(default)
       │     ├─ LLMRouter.route()                      [agentlib/router/llm_router.py:54]
       │     │     ├─ FastGate → emotional_support? → CHAT
       │     │     ├─ _is_task_delegation() → !task → CHAT
       │     │     ├─ _hard_clarify_reason() → ASK_CLARIFY
       │     │     ├─ _is_execute_task() → EXECUTE_LIGHT/HEAVY
       │     │     └─ default → TOOL_LIGHT
       │     ├─ RouterStateMachine.apply()             [agentlib/router/llm_router.py:357]
       │     │     └─ restricted_scopes + needs_confirm → confirm gate
       │     └─ compose_immediate_reply() → _emit_presence_reply()
       │
       ├─ direct_debug → _emit_presence_reply()
       ├─ direct_video → _emit_presence_reply()
       ├─ nl_control   → _emit_presence_reply()
       │
       └─ default (llm_chat):
            ├─ _maybe_auto_switch_persona()             [agentlib/runtime_engine.py:791]
            │     └─ persona_router.detect_persona_from_text()
            │           ├─ embedding-first (sentence-transformers, 0.75 weight)
            │           └─ keyword boost fallback (0.10 weight each)
            ├─ style_policy.act()
            ├─ companion_rag.retrieve_memory_context()
            ├─ companion_chat / companion_reply_stream()
            │     └─ GLMClient.chat()
            └─ _emit_presence_reply()                   [agentlib/runtime_engine.py:4849]
```

### 链路 B：Presence Min Flow（语义解释 + 策略执行）

```
_emit_presence_reply()                                 [agentlib/runtime_engine.py:4849]
  │
  └─ _presence_min_flow()                              [agentlib/runtime_engine.py:2105]
       │
       ├─ _interpret_event_placeholder()               [agentlib/runtime_engine.py:2092]
       │     └─ InputInterpreter.interpret(text, ctx)  [src/interpreter/input_interpreter.py:30]
       │           ├─ _normalize_text()
       │           ├─ 6 组 _contains_any() 关键词列表检查
       │           ├─ _negative_disambiguation()
       │           └─ validate_and_clip()               [src/interpreter/validators.py:27]
       │                 └─ 9 个数值字段 [0,1] 裁剪
       │
       ├─ apply_dependency_guard()                     [src/relationship/relationship_engine.py:4]
       │     └─ dependency_risk > 0.5 →
       │           boundary_sensitivity↑, carefulness↑,
       │           distance_preference↑, permission_to_approach↓
       │
       ├─ decide_persistence()                         [src/memory/memory_gate.py:4]
       │     └─ speculative → working
       │         confidence<0.45 → working
       │         first_seen & confidence<0.8 → tentative
       │         importance>0.7 & confidence>0.85 → stable
       │         default → tentative
       │
       ├─ mix_action_weights()                         [src/body/action_mixer.py:4]
       │     └─ gaze exclusivity normalization
       │         posture conflict resolution (lean vs withdraw)
       │         motion_suppression → micro_smile↓, hand_pause↑
       │
       └─ 返回 PresenceTrace (41 字段)
            ├─ raw_input, interpreted_event
            ├─ mind_delta, relationship_delta
            ├─ memory_write_decisions
            ├─ action_basis_weights, mixer_result
            ├─ body_influence, latency_tier
            └─ final_output, warnings

_emit_reply()                                          [agentlib/runtime_engine.py]
  └─ reply_q.put(msg)

_check_presence_trace_after_emit()                     [agentlib/runtime_engine.py:4874]
  └─ missing_presence_trace? → warn
      route_mismatch? → warn
```

### 链路 C：Semantic Intent Lane（并行 LLM 触发器）

```
semantic_intent_lane.infer()                           [agentlib/semantic_intent_lane.py:48]
  ├─ LLMRouter.route()                                 [agentlib/router/llm_router.py:54]
  └─ RouterStateMachine.apply()
       └─ 输出: {intent, decision, confidence, risk_level, execution_allowed, ...}
```

---

## 2. Input Interpretation 相关文件

### 核心模块 (`src/interpreter/`)

| 文件 | 角色 | 行数 |
|---|---|---|
| [`src/interpreter/__init__.py`](Aphrodite-demo/src/interpreter/__init__.py) | 模块导出，仅暴漏 `InputInterpreter` | 3 |
| [`src/interpreter/input_interpreter.py`](Aphrodite-demo/src/interpreter/input_interpreter.py) | **语义解释核心**：`interpret(text, context)` 方法 | 166 |
| [`src/interpreter/schema.py`](Aphrodite-demo/src/interpreter/schema.py) | `InterpretedEvent` dataclass：7 个语义维度 | 27 |
| [`src/interpreter/validators.py`](Aphrodite-demo/src/interpreter/validators.py) | `validate_and_clip()`：9 个数值字段 [0,1] 裁剪 | 35 |

### 依赖方

| 文件 | 调用方式 | 位置 |
|---|---|---|
| [`agentlib/runtime_engine.py`](Aphrodite-demo/agentlib/runtime_engine.py) | `self.input_interpreter.interpret()` | L2094 |
| [`scripts/interpreter_smoke_report.py`](Aphrodite-demo/scripts/interpreter_smoke_report.py) | `InputInterpreter().interpret()` | L50 |
| [`scripts/interpreter_calibration_report.py`](Aphrodite-demo/scripts/interpreter_calibration_report.py) | `InputInterpreter().interpret()` | L63 |

### 测试

| 文件 | 内容 |
|---|---|
| [`tests/test_input_interpreter_schema.py`](Aphrodite-demo/tests/test_input_interpreter_schema.py) | schema 完整性：7 个顶层 key + 数值字段存在性 |
| [`tests/test_input_interpreter_golden_cases.py`](Aphrodite-demo/tests/test_input_interpreter_golden_cases.py) | golden cases: technical_question, internal_tension, vulnerability, memory, negative_disambiguation |
| [`tests/calibration/interpreter_phase_2_3_cases.json`](Aphrodite-demo/tests/calibration/interpreter_phase_2_3_cases.json) | 校准用例集 |
| [`tests/golden_cases/`](Aphrodite-demo/tests/golden_cases/) 目录 | 17 个 JSON golden case 文件 |

---

## 3. RuntimeEngine 如何调用 Interpretation / Memory / Persona / Body Policy

### 调用关系图

```
RuntimeEngine.__init__()                               [agentlib/runtime_engine.py:207-262]
  │
  ├─ self.input_interpreter = InputInterpreter()         ← interpretation
  ├─ self.persona_name = "aphrodite"                     ← persona
  ├─ self.prompt_manager = PromptManager()               ← persona prompt
  ├─ self.semantic_intent_lane = SemanticIntentLane()    ← LLM trigger routing
  ├─ self.immediate_protocol = ImmediateReplyProtocol()   ← fast reply
  ├─ self.state_authority = StateAuthority()             ← global state
  └─ self.style_policy = StylePolicy()                   ← style decision
```

### 调用时机 (per turn)

```
_brain_loop() → event 到达
  │
  ├─ _perception_fusion()
  │     └─ command_hint 关键词检测 (selfdrive/debug/autofix)
  │
  ├─ _decision_core()
  │     └─ debug/selfdrive/llm_chat 分支
  │
  ├─ [llm_chat 分支]
  │     ├─ _maybe_auto_switch_persona()                  ← persona_router 调用
  │     │     └─ detect_persona_from_text()
  │     │           ├─ embedding (sentence-transformers)
  │     │           └─ keyword boost (aphrodite/coach/analyst/codex5.2)
  │     │
  │     ├─ style_policy.act()
  │     │
  │     ├─ companion_rag.retrieve_memory_context()       ← memory retrieval
  │     │     └─ MemoryStore.query() (SQLite + FAISS)
  │     │
  │     ├─ _build_system_prompt_bundle()
  │     │     └─ persona_profile → system prompt sections
  │     │
  │     ├─ companion_chat()
  │     │     └─ GLMClient.chat() → LLM reply
  │     │
  │     └─ companion_rag.record_turn_memory()            ← memory writeback
  │
  └─ _emit_presence_reply()
        └─ _presence_min_flow()
              ├─ _interpret_event_placeholder()           ← InputInterpreter 调用
              │     └─ self.input_interpreter.interpret(text, context)
              ├─ apply_dependency_guard()                 ← relationship policy
              ├─ decide_persistence()                     ← memory gate
              └─ mix_action_weights()                     ← body policy
```

---

## 4. provider → validator → policy_guard 当前状态

### 架构目标 vs 现状

| 层 | 架构目标 | 当前实现 | 判定 |
|---|---|---|---|
| **provider** | LLM/embedding 驱动的语义解释器 | [`InputInterpreter.interpret()`](Aphrodite-demo/src/interpreter/input_interpreter.py:30) — 完全由 6 组硬编码关键词列表 + `_contains_any()` + if-else 链驱动 | ❌ 名存实亡 |
| **validator** | 语义一致性验证器 | [`validate_and_clip()`](Aphrodite-demo/src/interpreter/validators.py:27) — 仅对 9 个数值字段做 `max(0.0, min(1.0, v))` 裁剪，零语义验证 | ❌ 作用极弱 |
| **policy_guard** | 策略卫士 | **完全缺失**。由两个分散函数近似承担 | ❌ 不存在 |

### policy_guard 的分散近似实现

| 函数 | 文件 | 覆盖范围 | 缺口 |
|---|---|---|---|
| [`apply_dependency_guard()`](Aphrodite-demo/src/relationship/relationship_engine.py:4) | `src/relationship/relationship_engine.py` | 仅依赖风险 → 4 个关系维度调整 | 无 persona 边界、无污染类型处理、无内部张力处理 |
| [`decide_persistence()`](Aphrodite-demo/src/memory/memory_gate.py:4) | `src/memory/memory_gate.py` | 仅记忆写入门控（confidence/importance/first_seen） | 无 external_pollution_risk 处理、无 tension_type 处理、无 persona_non_entry 处理 |

**`policy_guard` 字符串在代码库中零出现。**

---

## 5. keyword-list creep / phrase-trigger risk

### 风险矩阵（按严重程度排序）

| # | 文件:行 | 列表名 | 关键词数 | 风险等级 | 说明 |
|---|---|---|---|---|---|
| 1 | [`input_interpreter.py:53-57`](Aphrodite-demo/src/interpreter/input_interpreter.py:53) | `technical_phrases` | 56 | 🔴 严重 | 中英文混排，匹配即设 `persona_non_entry=True` |
| 2 | [`input_interpreter.py:66-75`](Aphrodite-demo/src/interpreter/input_interpreter.py:66) | `pollution_map` | 8 组 × 多词 | 🔴 严重 | 8 种污染类型：ai_girlfriend, romance_game, idol_performance, assistant_drift, fake_deep, safety_customer_service, beautiful_but_empty, companion_product |
| 3 | [`input_interpreter.py:81-95`](Aphrodite-demo/src/interpreter/input_interpreter.py:81) | `tension_map` | 13 组 × 中英 | 🔴 严重 | 13 种内部张力类型：negative_attraction, possessive_structure, contained, protected, fixed, chosen, sealed_field, non_contact_intimacy, distance_pressure, memory_weight, internal_danger, superego_pressure, source_fragment_purity |
| 4 | [`input_interpreter.py:102-108`](Aphrodite-demo/src/interpreter/input_interpreter.py:102) | `vulnerability_phrases` | 18 | 🟠 高 | 脆弱性表达检测 |
| 5 | [`input_interpreter.py:116-120`](Aphrodite-demo/src/interpreter/input_interpreter.py:116) | `dependency_phrases` | 10 | 🟠 高 | 依赖表达检测 |
| 6 | [`input_interpreter.py:150`](Aphrodite-demo/src/interpreter/input_interpreter.py:150) | `ambiguous_phrases` | 14 | 🟠 高 | 模糊指代检测 |

**小计：`input_interpreter.py` 一个文件含 150+ 独立关键词/短语，分布在 6 个独立列表中。**

| 7 | [`fast_gate.py:23-44`](Aphrodite-demo/agentlib/router/fast_gate.py:23) | `infer()` regex 链 | 3 组 | 🟡 中 | emotional_support(7词), task_request(16词), emotion_only(8词) |
| 8 | [`llm_router.py:128-137`](Aphrodite-demo/agentlib/router/llm_router.py:128) | `_has_goal()` | 20+ | 🟡 中 | 中文动词 regex |
| 9 | [`llm_router.py:139-148`](Aphrodite-demo/agentlib/router/llm_router.py:139) | `_has_object()` | 22 | 🟡 中 | 中文名词 regex |
| 10 | [`llm_router.py:241-258`](Aphrodite-demo/agentlib/router/llm_router.py:241) | `_is_execute_task()` | 30+ | 🟡 中 | state_change + pure_generation + diagnosis_workload |
| 11 | [`llm_router.py:177-208`](Aphrodite-demo/agentlib/router/llm_router.py:177) | `_hard_clarify_reason()` | 嵌套 | 🟡 中 | 多组嵌套 regex 判断 |
| 12 | [`llm_router.py:260-269`](Aphrodite-demo/agentlib/router/llm_router.py:260) | `_is_high_impact_execute()` | 16 | 🟡 中 | 含 `rm -rf` |
| 13 | [`persona_router.py:35-44`](Aphrodite-demo/agentlib/persona_router.py:35) | 4 组 `_apply_keywords()` | 25 | 🟢 低 | embedding-first，关键词仅 0.10 boost |

**总计：代码库中 13 处独立的关键词列表积聚点。**

### 累积风险说明

`input_interpreter.py` 的每条 if 分支都是**独立互不感知**的。新增一种语义判断 = 新增一个列表 + 一个 if 块。当前 6 个列表已覆盖：
- 技术问题检测
- 外部污染检测 (8 种)
- 内部张力检测 (13 种)
- 脆弱性检测
- 依赖表达检测
- 模糊指代检测

但缺失：美学判断、纠正意图、补充意图、行动冲突等（这些在 golden cases 中有用例但无对应的关键词列表）。

---

## 6. acceptable lightweight fallback

以下模块在当前阶段是**可接受的轻量回退**，不需要优先重构：

| 文件 | 原因 | 判定依据 |
|---|---|---|
| [`fast_gate.py`](Aphrodite-demo/agentlib/router/fast_gate.py) | 自述 "Ultra-fast gate"，定位就是轻量正则第一层筛选。只做三分类（CHAT/EXECUTE/emotional_support），失败默认 CHAT（安全偏向）。 | 明确的设计意图 + 低风险默认值 |
| [`persona_router.py`](Aphrodite-demo/agentlib/persona_router.py) | **embedding-first 架构**：先用 `sentence-transformers` 计算余弦相似度（占 0.75 权重），关键词仅作为 0.10 的轻量 boost，单次 boost 上限 0.25。 | 正确的架构分层：embedding 主路径 + keyword 辅助 |
| [`memory_gate.py`](Aphrodite-demo/src/memory/memory_gate.py) | 纯阈值驱动：`confidence<0.45`, `first_seen & confidence<0.8`, `importance>0.7 & confidence>0.85`。**零关键词依赖**。 | 纯数学决策 |
| [`action_mixer.py`](Aphrodite-demo/src/body/action_mixer.py) | 纯数学规则：gaze 归一化、posture 冲突消解、motion_suppression 阈值。**零文本依赖**。 | 纯数学决策 |
| [`validators.py`](Aphrodite-demo/src/interpreter/validators.py) | 纯数值裁剪，结构正确。问题在于作用范围太窄（仅 9 个字段），而非结构缺陷。 | 结构正确，需扩展而非替换 |
| [`relationship_engine.py`](Aphrodite-demo/src/relationship/relationship_engine.py) | 明确的单一职责：`dependency_risk` → 4 个关系维度线性调整。逻辑简洁、可预测。 | 正确的作用域，问题在于它不应该承担全部 policy_guard 职责 |

**`persona_router.py` 是唯一一个正确示范 "embedding-first, keyword-boost-only" 模式的文件**，可作为 refactoring 参考模式。

---

## 7. tests 线索

### 直接保护 interpretation 链路的测试

| 测试文件 | 保护内容 | 优先级 |
|---|---|---|
| [`tests/test_input_interpreter_schema.py`](Aphrodite-demo/tests/test_input_interpreter_schema.py) | schema 完整性：7 个顶层 key + 数值字段存在性 | 🔴 不可改 |
| [`tests/test_input_interpreter_golden_cases.py`](Aphrodite-demo/tests/test_input_interpreter_golden_cases.py) | golden cases：technical_question, internal_tension, vulnerability, memory, negative_disambiguation, persona_route | 🔴 不可改 |
| [`tests/golden_cases/`](Aphrodite-demo/tests/golden_cases/) 目录 (17 JSON) | 单 case 级语义契约 | 🔴 不可改 |
| [`tests/calibration/interpreter_phase_2_3_cases.json`](Aphrodite-demo/tests/calibration/interpreter_phase_2_3_cases.json) | 校准用例集 | 🟡 可增不可删 |

### 保护 presence loop 的测试

| 测试文件 | 保护内容 | 优先级 |
|---|---|---|
| [`tests/test_presence_min_flow_integration.py`](Aphrodite-demo/tests/test_presence_min_flow_integration.py) | `_presence_min_flow()` 完整字段输出、relationship guard 行为、memory write decisions | 🔴 不可改 |
| [`tests/test_presence_min_flow_with_interpreter.py`](Aphrodite-demo/tests/test_presence_min_flow_with_interpreter.py) | InputInterpreter 在 presence flow 中的 persona_non_entry、context_inherited 行为 | 🔴 不可改 |
| [`tests/test_presence_trace_guard.py`](Aphrodite-demo/tests/test_presence_trace_guard.py) | `_check_presence_trace_after_emit()`: missing_trace, route_mismatch 告警 | 🔴 不可改 |
| [`tests/test_presence_reply_paths.py`](Aphrodite-demo/tests/test_presence_reply_paths.py) | `_emit_presence_reply()` 的 llm/direct/error_safe 三条路由 | 🔴 不可改 |
| [`tests/test_immediate_protocol_presence.py`](Aphrodite-demo/tests/test_immediate_protocol_presence.py) | immediate protocol 的 presence trace 生成 + dependency guard | 🔴 不可改 |

### 保护 memory / persona / body 的测试

| 测试文件 | 保护内容 |
|---|---|
| [`tests/test_memory_write_gate.py`](Aphrodite-demo/tests/test_memory_write_gate.py) | `decide_persistence()`: low_confidence, first_seen |
| [`tests/test_persona_switch_guard.py`](Aphrodite-demo/tests/test_persona_switch_guard.py) | `_maybe_auto_switch_persona()`: margin, confidence threshold |
| [`tests/test_persona_profiles.py`](Aphrodite-demo/tests/test_persona_profiles.py) | profile 可用性 |
| [`tests/test_action_mixer_conflicts.py`](Aphrodite-demo/tests/test_action_mixer_conflicts.py) | `mix_action_weights()` 冲突消解 |
| [`tests/test_relationship_anti_slip.py`](Aphrodite-demo/tests/test_relationship_anti_slip.py) | `apply_dependency_guard()` 行为 |
| [`tests/test_selfdrive_input_review.py`](Aphrodite-demo/tests/test_selfdrive_input_review.py) | 自驱动输入的 permission review |
| [`tests/test_semantic_guard_behavior.py`](Aphrodite-demo/tests/test_semantic_guard_behavior.py) | semantic guard 的 low_confidence + high_risk 行为 |

---

## 8. Phase-1 最小重构建议

### 目标

建立真正的 `provider.interpret(...) → validator.validate(...) → policy_guard.apply(...)` 三层结构，**不改变任何外部行为**。

### 当前链路 vs 目标链路

**当前：**
```
InputInterpreter.interpret()
  → 150+ 关键词 if-else 链
  → validate_and_clip() (仅数值裁剪)
  → (无 policy_guard)
```

**目标 (Phase-1)：**
```
InputInterpreter.interpret()          [provider]
  → 保持接口不变，内部改为 embedding-first + keyword fallback
  → SemanticValidator.validate()      [validator]  新增
  → PolicyGuard.apply()               [policy_guard] 新增
```

### Phase-1 步骤

1. **新增 `src/interpreter/policy_guard.py`**
   - `class PolicyGuard` with `apply(interpreted_event) → PolicyDecision`
   - 整合当前分散的 `apply_dependency_guard()` 和 `decide_persistence()` 逻辑
   - 新增 `persona_non_entry` / `external_pollution_risk` / `internal_tension_relevance` 的处理路径
   - 结构参考 [`persona_router.py`](Aphrodite-demo/agentlib/persona_router.py) 的 embedding-first 模式

2. **扩展 `src/interpreter/validators.py`**
   - 从 9 个数值字段裁剪 → 增加语义一致性验证
   - 新增：event_type × persona_route 一致性、boundary_signal 内部一致性
   - 参考 [`test_input_interpreter_schema.py`](Aphrodite-demo/tests/test_input_interpreter_schema.py) 的 schema 契约

3. **渐进替换 `input_interpreter.py` 的关键词列表**
   - 保留接口 `interpret(text, context) → Dict`
   - 内部用 embedding 相似度替代 `_contains_any()`
   - 关键词列表降级为 `persona_router.py` 式的低权重 boost
   - 每次替换一个列表，跑 golden cases 验证

4. **在 `runtime_engine.py` 中接入 PolicyGuard**
   - 在 [`_presence_min_flow()`](Aphrodite-demo/agentlib/runtime_engine.py:2105) 中 `interpreted_event` 之后插入 `PolicyGuard.apply()`
   - 保持 `apply_dependency_guard()` 和 `decide_persistence()` 作为 PolicyGuard 的内部子步骤

---

## 9. 不应该碰的文件或逻辑

### 绝对不应该碰

| 文件 | 原因 |
|---|---|
| [`agentlib/runtime_engine.py`](Aphrodite-demo/agentlib/runtime_engine.py) | 5568 行，系统单点核心。任何修改波及 50+ 测试。Phase-1 只在其中新增 PolicyGuard 接入点，不修改现有逻辑 |
| [`tests/test_presence_min_flow_integration.py`](Aphrodite-demo/tests/test_presence_min_flow_integration.py) | presence loop 守卫测试 |
| [`tests/test_presence_trace_guard.py`](Aphrodite-demo/tests/test_presence_trace_guard.py) | presence trace 守卫测试 |
| [`tests/test_presence_reply_paths.py`](Aphrodite-demo/tests/test_presence_reply_paths.py) | emit reply 路由测试 |
| [`tests/test_presence_min_flow_with_interpreter.py`](Aphrodite-demo/tests/test_presence_min_flow_with_interpreter.py) | interpreter + presence 集成测试 |
| [`tests/test_input_interpreter_golden_cases.py`](Aphrodite-demo/tests/test_input_interpreter_golden_cases.py) | golden cases 行为契约 |
| [`tests/test_input_interpreter_schema.py`](Aphrodite-demo/tests/test_input_interpreter_schema.py) | schema 契约 |
| [`src/interpreter/schema.py`](Aphrodite-demo/src/interpreter/schema.py) | `InterpretedEvent` 下游依赖契约 |
| [`tests/golden_cases/`](Aphrodite-demo/tests/golden_cases/) 目录 | 17 个 JSON golden case 文件 |

### 当前可安全读取/分析但不修改

| 文件 | 原因 |
|---|---|
| [`agentlib/fast_gate.py`](Aphrodite-demo/agentlib/router/fast_gate.py) | acceptable fallback，不在 Phase-1 范围内 |
| [`agentlib/persona_router.py`](Aphrodite-demo/agentlib/persona_router.py) | correct reference pattern，保持原样 |
| [`agentlib/router/llm_router.py`](Aphrodite-demo/agentlib/router/llm_router.py) | 独立子系统，不属于 interpretation 链 |
| [`src/memory/memory_gate.py`](Aphrodite-demo/src/memory/memory_gate.py) | 纯阈值决策，Phase-1 整合进 PolicyGuard 但保持逻辑不变 |
| [`src/relationship/relationship_engine.py`](Aphrodite-demo/src/relationship/relationship_engine.py) | Phase-1 整合进 PolicyGuard 但保持公式不变 |
| [`src/body/action_mixer.py`](Aphrodite-demo/src/body/action_mixer.py) | 纯数学规则，不在 Phase-1 范围内 |

---

## 附录 A：文件索引

### `src/` 目录结构

```
src/
├── __init__.py
├── body/
│   ├── __init__.py
│   └── action_mixer.py              ← body policy (gaze/posture/motion)
├── character/
│   ├── generator.py                  ← character profile + memory config 生成
│   ├── schemas.py                    ← PersonaTraits, PersonaMemoryConfig
│   └── README.md
├── core/
│   ├── event_types.py               ← EventType 枚举
│   ├── state_authority.py            ← StateAuthority (全局状态变更)
│   └── trace.py                      ← PresenceTrace dataclass (41 字段)
├── interpreter/
│   ├── input_interpreter.py         ← 🔴 核心语义解释器 (150+ 关键词)
│   ├── schema.py                     ← InterpretedEvent (7 维度)
│   └── validators.py                ← validate_and_clip (9 数值字段)
├── memory/
│   ├── memory_gate.py               ← decide_persistence (写入门控)
│   ├── schemas.py                    ← MemoryConfig, memory_weight
│   └── store.py                      ← SQLite + FAISS 存储
├── relationship/
│   ├── relationship_engine.py       ← apply_dependency_guard
│   └── __init__.py
├── semantic_trigger/                ← 独立触发器子系统 (18 文件)
│   ├── engine.py, retriever.py, reranker.py,
│   ├── calibrator.py, adjudicator.py,
│   ├── decision.py, schemas.py, ...
└── voice/
    ├── gptsovits_adapter.py
    └── README.md
```

### `agentlib/` 核心文件

```
agentlib/
├── runtime_engine.py                 ← 🔴 5568 行核心引擎
├── semantic_intent_lane.py           ← LLM 路由 + 状态机 gate
├── persona_router.py                 ← embedding-first persona 检测
├── persona_profiles.py               ← 4 个 persona 定义
├── prompt_manager.py                 ← persona prompt 管理
├── runtime_immediate_protocol.py     ← 即时回复协议
├── companion_chat.py                 ← LLM 对话流
├── companion_rag.py                  ← memory retrieval + writeback
├── memory_store.py                   ← SQLite + FAISS MemoryStore
├── memory_arbiter.py                 ← memory 仲裁
├── runtime_state.py                  ← RuntimeConfig
├── style_policy.py                   ← 风格决策
├── coach.py                          ← Coach 决策
├── router/
│   ├── llm_router.py                 ← LLMRouter + RouterStateMachine
│   └── fast_gate.py                  ← FastGate (正则 pre-filter)
└── __init__.py
```

---

## 附录 B：架构约束检查清单

| 约束 | 当前状态 | 备注 |
|---|---|---|
| presence loop, not task loop | ✅ 存在 | `_presence_min_flow()` + `_emit_presence_reply()` 构成 core loop |
| provider.interpret() | ⚠️ 形式存在 | `InputInterpreter.interpret()` 但由关键词驱动 |
| validator.validate() | ⚠️ 形式存在 | `validate_and_clip()` 但仅做数值裁剪 |
| policy_guard.apply() | ❌ 缺失 | 零出现，由两个分散函数近似 |
| 无关键词列表路由 | ❌ 违反 | 13 处独立关键词积聚点，input_interpreter.py 含 150+ 关键词 |
| 无 phrase-trigger 路由 | ❌ 违反 | `technical_phrases` 匹配即设 `persona_non_entry=True` |
| 无 if-else 累积 | ❌ 违反 | `input_interpreter.py` 由 6 个独立 if 块驱动 |
