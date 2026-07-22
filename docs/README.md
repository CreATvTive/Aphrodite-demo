# Aphrodite 文档入口

Status: CURRENT DOCUMENTATION ENTRY
Authority-Scope: Documentation navigation and authority boundaries
Supersedes: Python runtime/demo-oriented documentation entry
Superseded-By: —
Last-Verified: 2026-07-14

Aphrodite chatbox v0 是当前工程主线。它不是通用 agent、工具助手、游戏 NPC、情绪标签引擎、companion 产品或生产力工具；中心是 subject-position、existence expression、relational-field dynamics、embodied surface behavior、controlled public expression 与 continuity of presence。

## 权威阅读顺序

1. [`chatbox/phase-plan-v0.md`](chatbox/phase-plan-v0.md) — 当前工程范围、架构、冻结合同、P1–P4 门与验收的最高权威；文件为外部落定原文的字节保真副本。
2. [`chatbox/README.md`](chatbox/README.md) — Phase 的仓库内导航、当前实现状态和合同入口。
3. [`design/README.md`](design/README.md) — 仅负责身份、persona、关系姿态、表达倾向与反漂移边界。
4. [`governance/pr-governance.md`](governance/pr-governance.md) — review 必查项、高风险权威审查与 legacy 隔离规则。
5. [`archive/README.md`](archive/README.md) — 历史材料索引；归档内容不定义当前 runtime 或 persona。

## 当前状态

当前主线的新实现入口由 Phase 冻结为 `app/chatbox/`。该入口尚不存在或尚未实现时，只表示 P1 尚未落地；不得把 `agentlib/`、`agent_kernel/`、`src/semantic_trigger/`、`demos/scenarios/` 或其三类场景指标恢复为当前入口。

chatbox v0 的本地 Python 后端 + Web UI、WebSocket + JSON、provider 抽象、维度注册表、spring-damper + OU、writer 只移动 attractor、感知事件总线、涌现式 `P_talk`、SQLite 与 P1–P4 冻结要求均以 Phase 原文为准。本索引不复述或改写这些合同。

## 权威边界

- [`design/aphrodite_private_origin_design_source.md`](design/aphrodite_private_origin_design_source.md) 不决定目录、provider、协议、数值动力学、schema、测试或 Phase。
- archive、旧 runtime 和 demo 文档只能作为历史/思想参考，不能覆盖 Phase。
- visual reference 不属于 P1–P4 验收，不要求 Live2D，也不能反向定义场动力学。
- 合同拆分文件只在出现稳定、可审查内容后创建；当前登记见 [`chatbox/contracts/README.md`](chatbox/contracts/README.md)。

## 文档分类

| 区域 | 分类 | 当前用途 |
| --- | --- | --- |
| [`chatbox/`](chatbox/) | current architecture authority | Phase 与 chatbox v0 导航 |
| [`design/`](design/) | authoritative identity boundary / reference | 身份边界与非绑定视觉参考 |
| [`governance/`](governance/) | current project rule | review 与权威审查规则 |
| [`archive/`](archive/) | archived history / legacy continuity | 历史参考，统一无实施权威 |

根级混合代码目录 [`../architecture/`](../architecture/) 未在本次文档主线切换中移动或改写；其 Markdown 仍是旧架构参考，不是 chatbox v0 权威入口。
