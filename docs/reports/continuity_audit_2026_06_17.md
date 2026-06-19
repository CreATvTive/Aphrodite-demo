# Aphrodite 连续性审计报告

> 审计日期：2026-06-17
> 审计模式：continuity-steward → chat (intake-router) → docs-specialist
> 审计前提：用户说明"部分断裂是因为多次切换工具导致的"，审计已据此区分**工具切换碎片**与**真实架构缺口**。
> 本文件性质：documentation-only — 记录审计发现，不授权任何架构变更。

---

## 1. 审计范围

以陌生人视角从零审查仓库中约 180+ 文件，覆盖：

- 顶层文档：[`AGENTS.md`](AGENTS.md)、[`README.md`](README.md)
- 设计宪法：[`docs/design/README.md`](docs/design/README.md)、[`docs/design/aphrodite_private_origin_design_source.md`](docs/design/aphrodite_private_origin_design_source.md)
- 项目文档：[`docs/README.md`](docs/README.md)、[`docs/repository_inventory.md`](docs/repository_inventory.md)、[`docs/DELIVERY_REPORT.md`](docs/DELIVERY_REPORT.md)
- 运行时代码：[`agentlib/runtime_engine.py`](agentlib/runtime_engine.py)（259KB）、[`agent_kernel/`](agent_kernel/)、[`agentlib/autonomy/`](agentlib/autonomy/)
- 模块代码：[`src/`](src/) 全目录递归
- 测试：[`tests/`](tests/) 全目录递归
- 实验分支：[`emotion-protocol/`](emotion-protocol/)
- 历史材料：[`docs/archive/legacy-continuity/`](docs/archive/legacy-continuity/)
- Kilo 基础设施：[`.kilocode/`](.kilocode/)

---

## 2. 权威性分类汇总

### 2.1 最高权威 — 设计宪法（authoritative-design-doc）

| 文件 | 分类 |
|------|------|
| [`docs/design/aphrodite_private_origin_design_source.md`](docs/design/aphrodite_private_origin_design_source.md) | authoritative-design-doc |
| [`docs/design/README.md`](docs/design/README.md) | authoritative-design-doc |

设计宪法定义了 Aphrodite 的私人来源、关系结构（封闭式收容、否定式吸引、不接触的亲密 ~1米）、内部危险/外部污染区分、沉默与克制语言策略、专业问题非进入姿态等。第 0.1 节明确其优先级高于视觉/工程/runtime。

### 2.2 当前权威 — 项目规则与运行时锚点

| 材料 | 分类 | 备注 |
|------|------|------|
| [`AGENTS.md`](AGENTS.md) | current-project-rule | 引用的部分 `src/` 目录为空壳（见 §3） |
| [`agentlib/runtime_engine.py`](agentlib/runtime_engine.py) | current-runtime-anchor | 259KB 通用 agent 运行时 |
| [`agent_kernel/`](agent_kernel/) | current-runtime-anchor | planner/worker/judge/failure-guard |
| [`agentlib/autonomy/`](agentlib/autonomy/) | current-runtime-anchor | perception/decision/actuation 编排 |
| [`src/interpreter/`](src/interpreter/) | current-runtime-anchor | `input_interpreter.py` + `schema.py`（含 `unknown_output`）可用 |
| [`tests/`](tests/) | current-runtime-anchor | ~50+ 测试文件含 golden_cases |
| [`docs/README.md`](docs/README.md) | current-project-rule | 部分内容已过时（见 §3 碎片项） |

### 2.3 实验/独立分支

| 材料 | 分类 | 依据 |
|------|------|------|
| [`emotion-protocol/`](emotion-protocol/) | experimental-branch | 自标记"Standalone Experimental Branch" |
| [`src/semantic_trigger/`](src/semantic_trigger/) | experimental-branch / legacy | 被 `docs/README.md` 标记为"历史/实验路径" |

### 2.4 文档参考资料（documentation-only）

| 材料 | 分类 |
|------|------|
| [`docs/DELIVERY_REPORT.md`](docs/DELIVERY_REPORT.md) | documentation-only — "丰川祥子"角色测试，与 Aphrodite 核心目标无关 |
| [`docs/repository_inventory.md`](docs/repository_inventory.md) | documentation-only — 2026-05-25 盘点快照，部分过期 |
| [`architecture/`](architecture/) | documentation-only — Live2D/协议设计稿 |
| `docs/Aphrodite_body_mind_*.md`、`docs/Aphrodite_v2_engineering_*.md` | documentation-only — 设计参考 |
| [`docs/lobster_agent_flow.md`](docs/lobster_agent_flow.md) | documentation-only — runtime 链路巡检 |

### 2.5 Legacy / Archived

| 材料 | 分类 |
|------|------|
| [`docs/archive/legacy-continuity/`](docs/archive/legacy-continuity/) | archived-history — 含 USER/MEMORY/SOUL/IDENTITY 等，不可作为当前依据 |
| [`docs/Aphrodite_body_mind_v2_archive.md`](docs/Aphrodite_body_mind_v2_archive.md) | legacy-continuity |

### 2.6 Kilo 配置基础设施

| 材料 | 分类 |
|------|------|
| [`.kilocode/`](.kilocode/) | active-mode-config — 不属于 Aphrodite 源码 |

---

## 3. 工具切换碎片（debris）

> 以下问题由多次工具切换造成，需要一次清理 pass，但**不是架构决策缺口**。

| 编号 | 现象 | 性质 |
|------|------|------|
| D1 | `src/` 下 8+ 空目录（`field_dynamics/`、`field_state/`、`body_action/`、`body_state/`、`llm_gate/`、`motion_curve/`、`motion_params/`、`viewers/`） | 脚手架残留 |
| D2 | 两套 MemoryStore（[`agentlib/memory_store.py`](agentlib/memory_store.py) vs [`src/memory/store.py`](src/memory/store.py)）使用相同默认文件名 | 并行产物冲突 |
| D3 | [`agentlib/runtime_engine.py`](agentlib/runtime_engine.py) 第 71 行和第 80 行重复导入 `InputInterpreter` | 编辑残留 |
| D4 | [`docs/README.md`](docs/README.md) 第 95 行声称 `unknown_output` 导入会失败，但 [`src/interpreter/schema.py`](src/interpreter/schema.py) 第 7 行明确定义了该函数 | 文档滞后 |
| D5 | [`docs/DELIVERY_REPORT.md`](docs/DELIVERY_REPORT.md) 中的"丰川祥子"测试 | 独立测试产物 |
| D6 | [`docs/repository_inventory.md`](docs/repository_inventory.md) 部分信息过期 | 盘点快照 |

---

## 4. 真实架构缺口

> 以下问题**不是工具切换碎片**，而是尚未做出的设计决策或尚未开始的实现。

| 编号 | 现象 | 性质 |
|------|------|------|
| A1 | `RelationalFieldState`、`LanguageConditionVector`、10 个 relational axes、`audit_trace`、clamp/decay/perturbation 等概念**仅存在于 Kilo mode rules 中**，代码中无对应实现 | 设计宪法已定义方向，实现层从未开始 |
| A2 | [`agentlib/runtime_engine.py`](agentlib/runtime_engine.py)（259KB）是工具密集的通用 agent 运行时 | 实际选择了一条与设计宪法有张力的实现路径；可能是早期为验证 agent 闭环而建 |
| A3 | [`src/relationship/relationship_engine.py`](src/relationship/relationship_engine.py) 仅 672 字节（函数签名级别） | 关系场实现未真正开始 |
| A4 | [`src/body/action_mixer.py`](src/body/action_mixer.py) 仅 1KB | 身体表现层未真正开始 |
| A5 | 设计宪法 → 视觉方向 → body-mind → 工程计划 → runtime 之间的层次断层 | 每层有文档，但层间无实际推导链 |

---

## 5. 重新理解的项目状态

```text
设计宪法（完整、清晰、权威）
     ↓ ← 不是断层，而是"从未开始往下走"
通用 agent 基础设施（agentlib/ + agent_kernel/）
  ← 独立长出来的平行路径，不是从设计宪法推导的
     ↓
src/ 碎片（解释器可用，其余是空壳和残留）
     ↓
测试（golden cases 对齐设计宪法，但只测解释器）
```

核心判断：**设计宪法一直都在，但从未有人从它出发，自上而下地实现 Aphrodite 的核心语义层。现有的 `agentlib/` 是一条平行路径。**

---

## 6. 测试资产审计

### 已存在且对齐设计宪法的测试

[`tests/golden_cases/`](tests/golden_cases/) 包含 20 个 golden cases 直接测试设计宪法约束：

- `external_pollution_ai_girlfriend.json` — 外部污染检测
- `internal_tension_negative_attraction.json` — 内部张力保留
- `internal_tension_possessive_structure.json` — 占有式结构
- `technical_question_non_entry.json` — 技术问题非进入
- `vulnerability_not_intimacy.json` — 脆弱不等于亲密
- `private_origin_purity_reference.json` — 来源纯粹性

### 限制

这些 golden cases 测试的是 `InputInterpreter`（文本分类器），不驱动关系场、身体表现或语言条件化。

---

## 7. Legacy Continuity 材料状态

[`docs/archive/legacy-continuity/`](docs/archive/legacy-continuity/) 被正确隔离。污染风险：**低**。

`.kilocode/worktrees/` 中存在 `TOOLS.md`、`USER.md` 等副本，属于 Kilo 内部状态，不应被误读为当前项目材料。

---

## 8. 可传递给后续工作的安全上下文

### 可引用（当前权威）

- [`docs/design/aphrodite_private_origin_design_source.md`](docs/design/aphrodite_private_origin_design_source.md) — 设计宪法
- [`docs/design/README.md`](docs/design/README.md) — 设计层次索引
- [`AGENTS.md`](AGENTS.md) — 工作区规则（注意 `src/` 目录状态）
- [`src/interpreter/`](src/interpreter/) — 唯一已知可用且与设计对齐的模块
- [`tests/golden_cases/`](tests/golden_cases/) — 设计对齐的测试用例

### 仅供参考（非权威）

- [`agentlib/runtime_engine.py`](agentlib/runtime_engine.py) — 当前可运行但不代表 Aphrodite 最终形态
- [`docs/DELIVERY_REPORT.md`](docs/DELIVERY_REPORT.md) — 历史交付报告
- [`emotion-protocol/`](emotion-protocol/) — 独立实验

### 不可引用

- [`docs/archive/legacy-continuity/`](docs/archive/legacy-continuity/) 中的任何文件
- [`.kilocode/worktrees/`](.kilocode/worktrees/) 中的任何文件

---

## 9. 推荐下一步

1. **phase-planner** — 基于此审计报告决定项目下一阶段方向
2. **architect** — 如果方向涉及架构决策，将设计宪法映射到具体模块
3. **docs-specialist** — 修复 `docs/README.md` 中的过时信息，更新 `AGENTS.md` 中的 runtime anchor 列表

---

## 10. 延续任务 Prompt

以下 prompt 可在新会话中直接使用，延续本次审计后的工作：

```text
请阅读 docs/reports/continuity_audit_2026_06_17.md 了解项目连续性审计结果。

审计发现两类问题：
1. 工具切换碎片（空目录、重复实现、过时文档）— 需要清理 pass
2. 真实架构缺口（设计宪法从未被自上而下实现，agentlib/ 是平行路径）— 需要方向决策

权威来源：
- 设计宪法：docs/design/aphrodite_private_origin_design_source.md
- 设计索引：docs/design/README.md
- 项目规则：AGENTS.md
- 可用模块：src/interpreter/
- 对齐测试：tests/golden_cases/

不可引用：docs/archive/legacy-continuity/ 和 .kilocode/worktrees/

我需要你帮我决定下一步方向。
```
