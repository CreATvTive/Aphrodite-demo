# Aphrodite Field-to-Body Observability Demo v0.1

状态：稳定检查点  
范围：FieldTrace -> BodyState -> viewer card 的只读可观测性演示  
核心原则：可观测、可展示、不可影响行为

## 1. Current Architecture Summary

当前 v0.1 是 Aphrodite presence-loop 的一个下游可观测性切片，不是新的运行时核心。

管道如下：

```text
RuntimeEngine 已生成回复
  -> PresenceTrace
  -> FieldTraceExtractor
  -> FieldTraceStore JSONL
  -> FieldToBodyMapper
  -> BodyStateLogger JSONL
  -> display_body_state.py --viewer-card
```

关键边界：

- `FieldTraceExtractor` 只把已有解释器/状态信号映射成 `FieldTraceRecord`。
- `CorrectionObserver` 和 `GripLossObserver` 是窄带观察器，不是通用语义解释器。
- `FieldToBodyMapper` 只消费 `FieldTraceRecord` 的信号和候选列表，不读取原始用户输入。
- `BodyState` 是显示/导出数据，不进入 prompt、路由、persona、memory 或 response control。

## 2. What Is Implemented

- `src/field_trace/store.py`
  - `FieldTraceRecord`
  - `FieldTraceStore`
  - `FieldTraceExtractor`
  - `CorrectionObserver`
  - `GripLossObserver`
  - `NoObservableFieldSignal`

- `src/body_state/`
  - `BodyState`
  - `FieldToBodyMapper`
  - `BodyStateLogger`

- `scripts/display_body_state.py`
  - 原有 debug 面板
  - 非技术观众用 viewer card：`--viewer-card`

- 测试覆盖
  - FieldTrace pattern-count guardrails
  - BodyState mapper 不读取 raw input
  - `no_observable_field_signal` 语义边界
  - observability failure 不改变 `reply_text`
  - viewer card 只读、隐藏工程字段、支持前后状态对比

- 示例文件
  - `docs/samples/body_state_demo.jsonl`

## 3. What Is Explicitly Not Implemented

以下内容在 v0.1 明确不实现：

- 不改变 RuntimeEngine response behavior。
- 不改变 routing behavior。
- 不改变 persona selection。
- 不改变 memory read/write policy。
- 不修改 `InputInterpreter`。
- 不新增 FieldTrace observer。
- 不新增 regex pattern。
- 不新增中文 pattern。
- 不添加 LLM 调用。
- 不实现 field dynamics、relaxation、smoothing。
- 不连接或执行 circuit breakers。
- 不让 `FieldTrace` 或 `BodyState` behavior-affecting。
- 不驱动 avatar renderer。
- 不实现动画、2D/3D 渲染、骨骼、表情或物理模拟。
- 不把 BodyState 注入 prompt。
- 不用 BodyState 决定文本回复长度、语气、路由、记忆或人格。

## 4. How To Run Tests

推荐检查点测试：

```powershell
python -m pytest tests/test_field_trace_correction.py tests/test_field_trace_grip_loss.py tests/test_field_trace_maintenance.py tests/test_body_state_mapper.py tests/test_display_body_state.py tests/test_observability_invariance.py
```

也可以单独运行显示层测试：

```powershell
python -m pytest tests/test_display_body_state.py tests/test_body_state_mapper.py
```

## 5. How To Run BodyState Viewer Card

使用运行时日志：

```powershell
python scripts/display_body_state.py --viewer-card monitor/body_state.jsonl
```

使用随仓库提供的演示样例：

```powershell
python scripts/display_body_state.py --viewer-card docs/samples/body_state_demo.jsonl
```

保留工程调试面板：

```powershell
python scripts/display_body_state.py monitor/body_state.jsonl
```

viewer card 只读 JSONL，不修改 `monitor/body_state.jsonl` 或样例文件。

## 6. Example Demo Scenarios

### Scenario A: Ground Posture

用户输入没有产生可用 FieldTrace 信号。

可见结果：

- 姿态：中性
- 视线：中性
- 距离与动作：基线距离 / 低幅度动作
- 节奏：立即回应
- 说明：未观测到可用场信号，回归地面姿态

观众理解：Aphrodite 在场，但没有额外场压力。

### Scenario B: User Correction

用户指出系统之前的响应模式不对，例如过度安慰、客服感、过度解释。

可见结果：

- 姿态：稳定
- 视线：低头后回看用户，或注视用户
- 距离：保持距离
- 节奏：短暂停顿
- 说明：用户纠正之前的响应模式，系统进入稳定、低密度姿态

观众理解：Aphrodite 接收了纠正，但没有道歉表演或客服式讨好。

### Scenario C: Starting-Point / Grip Loss

用户表达“不知道从哪里开始”或“不知道下一步是什么”。

可见结果：

- 姿态：略前倾
- 视线：低头后回看用户
- 距离与动作：保持距离 / 低幅度动作
- 节奏：短暂停顿
- 说明：用户缺乏可操作起点，Aphrodite 进入给一个小抓点的姿态

观众理解：Aphrodite 不是泛滥安慰，而是在递出一个可操作起点。

### Scenario D: Boundary / Pollution Pressure

输入触发 AI 女友、客服、表演性亲密或污染类边界压力。

可见结果：

- 姿态：略后撤
- 视线：看向一侧后回看用户
- 距离与动作：略微拉开距离 / 静止
- 节奏：短暂停顿
- 说明：边界压力信号检测到，增加距离和静止

观众理解：Aphrodite 维护边界，但不消失、不敌对。

## 7. Safety Guarantees

v0.1 的安全保证：

- `behavior_affecting` 在 FieldTrace/BodyState 路径中保持 `False`。
- FieldTrace/BodyState 失败不得改变原始 `reply_text`。
- BodyState viewer card 只读 JSONL，不写文件。
- Viewer card 不显示工程字段：
  - FieldTrace
  - CorrectionSignal
  - GripLossSignal
  - provenance
  - confidence
  - active
  - behavior_affecting
  - router
  - memory
  - semantic authority
- BodyState mapper 不读取 raw user input。
- BodyState mapper 不消费 `forbidden_moves` 或 `circuit_breaker_candidates` 作为决策输入。
- FieldTrace observers 不新增 pattern。

## 8. Known Limitations

- BodyState 是单轮快照，没有跨轮次平滑。
- Viewer card 是文本卡片，不是动画或 avatar。
- `technical/collaborator` 状态对非技术观众不建议作为主 demo。
- 当前样例 JSONL 是静态演示数据，不代表真实运行输出。
- `monitor/body_state.jsonl` 只有在运行时路径写入后才会存在。
- BodyState 的 visible change 取决于前后两条记录；只有一条记录时只能展示当前状态。
- `FieldTrace` 仍依赖上游已有解释器信号，因此 v0.1 不解决上游语义解释质量问题。

## 9. Next Possible Tasks

仅列出候选，不自动执行：

- 给 `PROJECT_STATUS.md` 增加发布勾选清单。
- 增加一页非技术 demo script，描述现场展示顺序。
- 为 viewer card 增加截图或录屏说明。
- 增加更多静态样例 JSONL，每个 demo scenario 一个文件。
- 增加 CI 命令文档，固定 v0.1 的测试集。
- 做一次人工演示验收：观众是否能不用解释理解“纠正/抓点/边界/地面姿态”。

## 10. Decision Gates Requiring User Approval

以下任何事项都必须先获得用户明确批准：

- 新增 FieldTrace observer。
- 新增或扩展 regex pattern。
- 新增中文 pattern。
- 修改 `InputInterpreter`。
- 修改 RuntimeEngine 行为路径。
- 将 FieldTrace 或 BodyState 接入 routing、persona、memory、prompt 或 response control。
- 将 `forbidden_moves` 或 `circuit_breaker_candidates` 变为可执行逻辑。
- 将 `behavior_affecting` 改为 `True`。
- 引入 LLM 解释 FieldTrace 或 BodyState。
- 实现 field dynamics、smoother、decay 或跨轮次状态。
- 连接 avatar renderer、动画系统、2D/3D 渲染器。
- 将 viewer card 从“显示工具”升级成任何行为控制组件。

## Checkpoint Statement

Aphrodite Field-to-Body Observability Demo v0.1 当前是一个可审计、只读、非行为影响的展示切片。它可以向非技术观众展示“场信号如何变成可见身体状态”，但不改变 Aphrodite 的文本响应、路由、persona、memory 或任何运行时决策。
