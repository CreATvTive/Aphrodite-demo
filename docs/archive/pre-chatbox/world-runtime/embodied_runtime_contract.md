# Embodied Runtime Contract（协议草案 v0）

> 目标：定义**最小闭环**的统一事件模型，支持多模态输入、决策、执行并行开发。  
> 范围：当前仅定义数据结构与生命周期，不绑定具体算法实现。

## 1. 设计边界与原则

- 事件驱动优先：runtime 以事件 loop 作为唯一核心，不假设单一 CLI 入口。
- 最小闭环字段：先保证 perception → decision → actuation 可贯通。
- 可扩展但不绑死：预留 `emotion`、`uncertainty`、`source_latency_ms` 等扩展字段。
- 兼容渐进迁移：允许现有 `RuntimeEngine` 通过映射节点接入协议。

## 2. 统一事件模型（Envelope）

所有事件都遵循统一包结构：

- `event_id`: 全局唯一事件 ID。
- `event_type`: 事件类型（输入/输出/内部 + 子类型）。
- `lifecycle`: 生命周期阶段（queued/processing/completed/failed/dropped）。
- `ts`: 事件时间戳（epoch 秒）。
- `trace_id`: 链路追踪 ID（跨事件关联）。
- `source`: 事件来源（user/sensor/system/brain/...）。
- `target`: 目标执行域（brain/shell/scene/output/...）。
- `payload`: 事件主体。
- `meta`: 扩展元信息（可选）。

## 3. 事件类别

### 3.1 输入事件（Input Events）

- `input.visual.pseudo`：伪视觉输入（截图、图像摘要、视觉标注）
- `input.audio.pseudo`：伪听觉输入（语音文本、声音标签、VAD 片段描述）
- `input.touch.pseudo`：伪触觉输入（交互手势、触发器、控制器输入）
- `input.olfactory.pseudo`：伪嗅觉输入（环境状态标签、气味语义映射）

### 3.2 输出事件（Output Events）

- `output.scene.action`：场景动作（角色/镜头/动画/状态机动作）
- `output.dialog.utterance`：对话输出（文本、语音任务）
- `output.interaction.feedback`：交互反馈（确认、拒绝、澄清、引导）

### 3.4 映射清单（最小保障）

以下 7 类事件都必须能映射到同一个 Envelope（`event_id/event_type/lifecycle/ts/trace_id/source/target/payload/meta`）：

- 输入（4）
  - `visual -> input.visual.pseudo`
  - `audio -> input.audio.pseudo`
  - `touch -> input.touch.pseudo`
  - `olfactory -> input.olfactory.pseudo`
- 输出（3）
  - `scene_action -> output.scene.action`
  - `dialog_utterance -> output.dialog.utterance`
  - `interaction_feedback -> output.interaction.feedback`

### 3.3 内部事件（Internal Events）

- `internal.brain.decision`：brain 决策结果（意图、计划、候选动作）
- `internal.state.updated`：状态更新（运行状态、情绪状态、关系值等）
- `internal.memory.write`：记忆写入（短期/长期记忆）

## 4. 生命周期约束

标准状态流转（建议）：

1. `queued`：事件入队
2. `processing`：被某节点消费中
3. `completed`：成功产出结果
4. `failed`：失败（可重试/不可重试由 `meta` 决定）
5. `dropped`：主动丢弃（过期、冲突、降级策略）

## 5. 启动与运行（无主入口）

在“无主入口”前提下，runtime 启动不再依赖单一 CLI：

- **事件驱动 loop** 为核心，允许多个 producer 并发注入事件：
  - DB/IM 桥接注入用户文本
  - 传感器适配层注入伪多模态事件
  - 定时器/idle watcher 注入内部触发事件
- CLI 仅作为可选输入适配器之一，不再是系统启动前提。

## 6. 映射到当前代码（过渡约定）

当前 `agentlib/runtime_engine.py` 可映射节点：

- perception：`event_q` 取事件 + `_parse_event`
- decision：`immediate_protocol.send(...)` 与后续策略/路由判断
- actuation：`_emit_reply(...)` / `reply_q.put(...)` / 外部 bridge 写回

该映射用于迁移期间的协议对齐，不改变现有业务逻辑。

## 7. 废案区域（固定）

`src/semantic_trigger/` 当前**暂不接入主链路**。  
可保留为实验/回归参考，不作为协议主事件总线的必经路径。

## 8. 并行开发建议

协议稳定后可并行推进：

- shell：输入源适配（CLI/IM/工具回调）
- scene：`output.scene.action` 执行层
- sense：伪视觉/伪听觉/伪触觉/伪嗅觉适配
- output：对话与交互反馈渲染层

以上模块通过统一 Envelope 通信，降低互相阻塞风险。
