# Aphrodite

Aphrodite 是一个面向**具身角色存在感**的 AI 角色系统实验仓库。项目试图把角色解释、记忆、关系边界、身体表现和场景反馈组织成可以测试、回放并逐步接入表现层的运行闭环，而不是只生成一段看似合适的聊天回复。

当前仓库处于 **Python 运行切片与设计验证并行推进** 阶段：已有可运行的场景演示、编排循环、解释器校准与测试资产；Live2D、完整游戏内嵌表现层和部分 body-mind 统一集成仍属于设计或原型方向。

## 项目边界

Aphrodite 的目标不是通用助手、效率工具、AI 女友、心理支持产品或披着角色外壳的问答机器人。工程实现应保持以下原则：

- 角色表达以身体状态、距离、停顿、记忆重量和克制语言为核心，而非服务式迎合。
- 技术或专业问题由工程/导演层处理，不让角色人格承担通用助手职责。
- 脆弱、依赖或危机语境必须保留边界，不把危险材料直接实现为关系操控或亲密升级。
- 涉及 persona、关系、记忆显著性与身体影响层的改动，先对齐 [设计宪章索引](docs/design/README.md)。

## 当前可验证能力

| 能力切片 | 当前状态 | 主要位置 / 入口 |
| --- | --- | --- |
| 固定场景演示包 | 可直接运行；提供安全、社交、任务三套剧本及最小指标面板 | `cli/run_demo_pack.py`、`demos/scenarios/` |
| Autonomy 编排闭环 | 代码和测试入口已存在；当前受 runtime/interpreter 导入接口不一致阻塞 | `agentlib/autonomy/`、`tests/test_autonomy_demo_v2_e2e.py` |
| Runtime 与 self-drive 链路 | 已有实现与测试资产；集成路径仍在稳定中 | `agentlib/runtime_engine.py`、`agent_kernel/` |
| 输入解释与边界信号 | 已实现并有 golden cases / 校准报告；输出 persona 路由、风险与表现信号 | `src/interpreter/`、`tests/golden_cases/`、`docs/reports/` |
| 记忆、关系与动作模块 | 已拆分为代码模块并有针对性测试；仍在向统一 runtime 收敛 | `src/memory/`、`src/relationship/`、`src/body/`、`src/core/` |
| 离线 RAG 与检索评估 | 工具链存在，可用于样本构建、调参与回放 | `rag_offline/` |
| Live2D / 语音 / 游戏表现层 | 以架构文档为主，尚非可交付前端；`src/voice/` 等旧适配器路径已在阶段 3 移除 | `architecture/`、`docs/live2d-integration-*.md` |

`src/semantic_trigger/` 保留为历史/实验与回归参考路径；按照当前 runtime contract，它不是具身主链路的必经模块。

## 闭环与实现切片

项目围绕下面的目标闭环组织。仓库目前存在多个可验证切片，而不是一个已经串起所有模块的唯一启动器。

```text
用户输入 / 伪感知事件
          |
          v
Input Interpretation & Boundary Signals
src/interpreter/ + config/
          |
          v
Runtime / Planning / State Authority
agentlib/runtime_engine.py + agent_kernel/ + src/core/
          |
     +----+------------------+
     |                       |
     v                       v
Memory & Relationship     Body / Scene Action
src/memory/              src/body/
src/relationship/        agentlib/autonomy/
     |                       |
     +-----------+-----------+
                 v
        Trace / Replay / Reports
```

统一事件模型与过渡映射见 [docs/embodied_runtime_contract.md](docs/embodied_runtime_contract.md)。其中定义的 `perception -> decision -> actuation` Envelope 是后续集成的协议目标，不代表每个表现层能力都已经接通。

## 快速开始

### 1. 准备环境

建议使用 Python 3.10+ 与虚拟环境。在 Windows PowerShell 中：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -X utf8 -m pip install -r requirements.txt
```

仓库包含 UTF-8 编码的中文场景和测试数据；在 Windows 默认代码页不是 UTF-8 时，建议像下文一样通过 `python -X utf8` 运行项目命令。固定场景演示只依赖本地仓库内容，不需要 API key。涉及模型调用、外部语音或完整记忆检索时，再根据 [.env.example](.env.example) 创建本地 `.env`。

### 2. 运行标准场景演示

这是最稳定、最适合观察输出形态的入口：

```powershell
python -X utf8 cli/run_demo_pack.py --scenario all --save-report outputs/demo/demo_report.json
```

演示包会依次运行：

- `security_scene`：异常识别、隔离动作与告警路径。
- `social_scene`：来访接待、意图确认与路径引导。
- `task_scene`：需求接收、执行分发与结果回执。

输出包含事件吞吐、动作成功率和降级次数。剧本说明与现场故障处理见 [docs/demo/playbooks.md](docs/demo/playbooks.md) 和 [docs/demo/failure_runbook.md](docs/demo/failure_runbook.md)。

### 3. 编排循环演示入口

```powershell
python -X utf8 -m agentlib.autonomy.demo_v2
```

这个入口设计用于展示任务生成、工具执行、评估、反思、失败路由和重试/回退，而不是完整视觉角色应用。此前关于 `src.interpreter.schema.unknown_output` 缺失导致导入阶段失败的历史阻塞已不再成立；该入口仍应随集成路径稳定后重新纳入回归。

### 4. 查看解释器校准

解释器用于把输入转换为技术路由、人格非进入、关系/边界风险与身体表现信号。已有结果可直接阅读：

- [Phase 2.2 smoke report](docs/reports/interpreter_smoke_phase_2_2.md)
- [Phase 2.3 calibration report](docs/reports/interpreter_calibration_phase_2_3.md)

需要重新生成报告时运行：

```powershell
python -X utf8 scripts/interpreter_smoke_report.py
python -X utf8 scripts/interpreter_calibration_report.py
```

## 测试

运行完整测试集：

```powershell
python -X utf8 -m pytest -q
```

验证当前可运行的场景与解释器切片：

```powershell
python -X utf8 -m pytest tests/test_demo_pack.py tests/test_input_interpreter_golden_cases.py -q
```

Autonomy 集成入口的待修复回归命令：

```powershell
python -X utf8 -m pytest tests/test_autonomy_demo_v2_e2e.py -q
```

语义触发历史路径仍有单独调试命令与回归数据，参见 [docs/debugging_guide.md](docs/debugging_guide.md)。

## 仓库结构速查（按状态）

下表按 **当前项目规则** 给各顶层目录打上状态标签，方便快速判断其用途、权威性与是否可动。状态含义：

- **活跃运行锚点**：当前 runtime 直接导入或执行的代码路径，未批准前不要移动/重命名。
- **活跃设计入口**：当前权威设计文档或规则入口。
- **活跃支持区**：当前在用的工具链、配置或测试资产。
- **实验/独立区**：与主 runtime 相对独立或边界待定的原型、演示协议。
- **历史/归档**：legacy continuity 材料或已被明确替代的旧方案，不当作当前 runtime/persona 依据。
- **敏感**：包含 persona prompt、运行状态或私有连续性数据，未经任务授权不要读取或修改。
- **文档-only**：仅作说明、快照或整理台账，不产生运行时约束。
- **待决**：存在重复、归属未定或需 architect / 用户批准才能整理的区域。

| 路径 | 状态 | 用途与权威关系 |
| --- | --- | --- |
| [`agentlib/`](agentlib/) | 活跃运行锚点 | 当前 runtime、autonomy 闭环、router、调度与在线 RAG 代码。`agentlib/runtime_engine.py` 是主 runtime 入口之一。 |
| [`agent_kernel/`](agent_kernel/) | 活跃运行锚点 | planner / worker / judge / failure guard 内核，被 `agentlib/runtime_engine.py` 与 `agentlib/autonomy/` 直接导入。 |
| [`src/interpreter/`](src/interpreter/) | 活跃运行锚点 | 输入解释与边界/表现信号；其内部类型（如 `LanguageConditionVector`）是运行时约定，不是文档可重命名的对象。 |
| [`src/core/`](src/core/) | 活跃运行锚点 | 状态权威、事件与 trace。 |
| [`src/memory/`](src/memory/) | 活跃运行锚点 | 记忆 gate 与存储模型。 |
| [`src/relationship/`](src/relationship/) | 活跃运行锚点 | 关系引擎；其内部类型（如 `RelationalFieldState`）是运行时约定，不能简化为 emotion labels 或文档重构对象。 |
| [`src/body/`](src/body/) | 活跃运行锚点 | 动作混合与身体表现输出。 |
| [`cli/`](cli/) | 活跃支持区 | 演示、回放、评估入口；[`cli/run_demo_pack.py`](cli/run_demo_pack.py) 是当前最稳定的运行入口。 |
| [`demos/scenarios/`](demos/scenarios/) | 活跃支持区 | 三套标准演示剧本，由 `cli/run_demo_pack.py` 消费。 |
| [`tests/`](tests/) | 活跃支持区 | 单元、集成、golden cases 与 fixtures。 |
| [`rag_offline/`](rag_offline/) | 活跃支持区 | 离线 Embedding 训练、评估、调参与回放工具链。 |
| [`scripts/`](scripts/) | 活跃支持区 | 报告生成与演示辅助脚本。 |
| [`docs/design/`](docs/design/) | **活跃设计入口** | 权威设计层级与 persona 边界入口；修改关系、人格、记忆显著性、身体影响层前必须先读。 |
| [`docs/reports/`](docs/reports/) | 活跃设计入口 | 解释器校准与连续性审计报告，作为当前可验证能力的证据。 |
| [`docs/demo/`](docs/demo/) | 活跃支持区 | 标准演示场景的运行手册与故障处理。 |
| [`docs/archive/legacy-continuity/`](docs/archive/legacy-continuity/) | 历史/归档 | 旧版 workspace continuity 材料（`USER.md`、`MEMORY.md`、`SOUL.md`、`IDENTITY.md`、`HEARTBEAT.md`、`TOOLS.md` 及日期记忆笔记），**不作为当前 Aphrodite runtime/persona 依据**。 |
| [`architecture/`](architecture/) | 文档-only / 实验 | Live2D / 协议 / 接口设计稿，供表现层参考；不是当前可交付前端。 |
| [`config/`](config/) | 待决 | 当前关系/延迟策略配置。与 `configs/` 并存，归属与命名统一需 architect / 用户批准。 |
| [`configs/`](configs/) | 待决 | app / trigger 示例配置。与 `config/` 的重复/合并问题需 architect / 用户批准。 |
| [`monitor/`](monitor/) | 敏感 | persona prompt、RAG 文档、调试 runner 与本地运行数据库；未经任务授权不要读取或修改。 |
| [`memory/`](memory/) | 敏感 / 待决 | 会话日记与本地测试数据库；日记与模型记忆数据库不应混放，整理需批准。 |
| [`evals/`](evals/) | 活跃支持区 | 路由评估 JSONL，作为受版本管理的数据资产。 |
| [`outputs/`](outputs/) | 运行产物 | 本地报告和 task run 输出；已由 [`.gitignore`](.gitignore) 管控，不应提交。 |
| [`data/`](data/) | 运行产物 | 本地运行输入/输出；已由 [`.gitignore`](.gitignore) 管控，不应提交。 |

## 目录结构

```text
Aphrodite-demo/
├── agentlib/              # 运行时、autonomy 闭环、router 与调度能力（当前运行锚点）
├── agent_kernel/          # planner / worker / judge / failure guard 内核（当前运行锚点）
├── src/
│   ├── interpreter/       # 输入解释与边界/表现信号（当前运行锚点）
│   ├── core/              # 状态权威、事件与 trace（当前运行锚点）
│   ├── memory/            # 记忆 gate 与存储模型（当前运行锚点）
│   ├── relationship/      # 关系引擎（当前运行锚点；非 emotion-label 系统）
│   ├── body/              # 动作混合（当前运行锚点）
│   └── semantic_trigger/  # 历史/实验路径
├── cli/                   # 演示与评估入口
├── demos/scenarios/       # 固化演示剧本
├── tests/                 # 单元、集成、golden cases 与 fixtures
├── rag_offline/           # 离线检索训练、评估和回放
├── scripts/               # 报告生成与演示辅助脚本
├── config/                # 当前关系/延迟策略配置（与 configs/ 重复问题待决）
├── configs/               # trigger 与 app 示例配置（与 config/ 重复问题待决）
├── docs/                  # 设计、契约、报告和演示手册
│   ├── design/            # 权威设计入口
│   ├── reports/           # 可验证能力报告
│   ├── demo/              # 演示运行手册
│   └── archive/legacy-continuity/  # 归档连续性材料，不作当前依据
└── architecture/          # Live2D/协议/接口设计稿（文档-only / 实验参考）
```

本地运行产生的 `.env`、`outputs/`、`data/`、`memory.sqlite`、`memory.faiss`、`memory_ids.npy` 和监控数据库均不应作为源码提交；忽略规则见 [`.gitignore`](.gitignore)。

> **导航约定**：文档表中的“状态”列依据当前项目规则与 continuity-steward 分类结果，用于快速识别权威关系；状态标签不修改被指向文件本身的性质，也不授权任何重命名、合并或移动。

## 文档导航

### 先读这些

| 文档 | 用途 |
| --- | --- |
| [docs/design/README.md](docs/design/README.md) | 设计权威层级、角色边界与修改前置约束 |
| [docs/embodied_runtime_contract.md](docs/embodied_runtime_contract.md) | 具身事件模型、生命周期与现有 runtime 映射 |
| [docs/lobster_agent_flow.md](docs/lobster_agent_flow.md) | `runtime_engine` 与 `agent_kernel` 的规划/执行链路巡检 |
| [docs/demo/playbooks.md](docs/demo/playbooks.md) | 三套标准演示场景与运行方式 |

### 产品与工程设计

| 文档 | 状态 | 用途与注意 |
| --- | --- | --- |
| [Aphrodite_body_mind_full_plan.md](Aphrodite_body_mind_full_plan.md) | 活跃设计 | body-mind 系统整体规划与阶段拆解；位于 `docs/` 根目录，尚未迁入子目录。 |
| [Aphrodite_v2_engineering_plan.md](Aphrodite_v2_engineering_plan.md) | 活跃设计 | 工程风险、优先级与推荐实现顺序；位于 `docs/` 根目录，尚未迁入子目录。 |
| [Aphrodite_v2_engineering_issues.md](Aphrodite_v2_engineering_issues.md) | 活跃设计 / 历史归档 | 问题归档；位于 `docs/` 根目录。 |
| [Aphrodite_v2_visual_direction_production_guide.md](Aphrodite_v2_visual_direction_production_guide.md) | 活跃设计 | 视觉方向与制作约束；由 `docs/design/README.md` 设计层级直接引用。 |
| [docs/architecture.md](docs/architecture.md) | 文档-only / 待迁 | 架构说明快照，需核对是否仍有效后再归类。 |
| [docs/FRAMEWORK.md](docs/FRAMEWORK.md) | 文档-only / 待核 | 框架说明；是否仍为有效说明需进一步核对。 |
| [docs/DELIVERY_REPORT.md](docs/DELIVERY_REPORT.md) | 文档-only / 历史 | 交付报告快照；可归入 `docs/reports/` 或 `docs/archive/`。 |

### 表现层与历史方案

| 文档 | 状态 | 用途与注意 |
| --- | --- | --- |
| [docs/live2d-integration-design.md](docs/live2d-integration-design.md) | 文档-only / 实验 | Live2D 集成方案设计；当前以设计稿为主，尚未成为可交付前端。 |
| [architecture/README.md](architecture/README.md) | 文档-only / 实验 | Live2D 对话演示架构索引；参考但不编辑。 |
| [docs/demo_architecture_v2.md](docs/demo_architecture_v2.md) | 文档-only / 实验 | 本地游戏内嵌方案规划。 |
| [docs/memory_system_integration.md](docs/memory_system_integration.md) | 文档-only | 记忆迁移与融合方案记录。 |
| [docs/Aphrodite_body_mind_v2_archive.md](docs/Aphrodite_body_mind_v2_archive.md) | 历史/归档 | body-mind v2 旧稿；按设计层级已被 `Aphrodite_body_mind_full_plan.md` 等替代或承接。 |
| [docs/archive/legacy-continuity/README.md](docs/archive/legacy-continuity/README.md) | 历史/归档 | 旧连续性材料索引，仅用于历史审查。 |

### 运行与调试参考

| 文档 | 状态 | 用途 |
| --- | --- | --- |
| [docs/debugging.md](docs/debugging.md) | 活跃支持 | 通用调试说明。 |
| [docs/debugging_guide.md](docs/debugging_guide.md) | 活跃支持 / 历史 | 历史 trigger 路径调试命令与回归数据入口。 |
| [docs/trigger_schema.md](docs/trigger_schema.md) | 活跃支持 / 历史 | 触发器 schema；主要服务于 `src/semantic_trigger/` 历史路径。 |
| [docs/error_ledger_paths.md](docs/error_ledger_paths.md) | 活跃支持 | 错误分类与路径速查。 |
| [docs/voice_sample_sources.md](docs/voice_sample_sources.md) | 活跃支持 | 语音样本来源与授权注意事项。 |

## 整理与移动待决项（需 architect / 用户批准）

当前 **只更新文档导航，尚未移动任何文件**。以下事项若进入下一阶段整理，需先获得明确批准：

1. **根目录设计稿归位**：`Aphrodite_*.md`、`docs/architecture.md`、`docs/FRAMEWORK.md`、`docs/DELIVERY_REPORT.md` 是否迁入 `docs/plans/`、`docs/archive/` 或 `docs/reports/`。
2. **配置目录合并**：`config/` 与 `configs/` 的重复/归属问题，是否统一为 `configs/runtime/` 与 `configs/examples/`。
3. **监控资源拆分**：`monitor/` 中的静态 RAG 输入、persona prompt 与运行数据库/索引是否需要拆分到不同目录。
4. **memory/ 目录拆分**：会话日记与模型记忆数据库不应混放，需确定落位。
5. **运行产物统一根目录**：`outputs/`、`data/`、根目录 `memory.sqlite` / `memory.faiss` / `memory_ids.npy` 是否迁入统一的 `var/` 结构。
6. **源码包目录移动**：`agentlib/`、`agent_kernel/`、`src/`、`cli/`、`scripts/` 等当前运行锚点暂不动，后续若移动需同步所有 import 与测试路径。

## 开发约定

1. 修改 persona、relationship、memory salience、body influence 或相关路由前，先阅读 [docs/design/README.md](docs/design/README.md) 指向的设计源文件。
2. 新能力优先接入当前 runtime / autonomy 测试链路，并补充可回放的测试或报告；不要仅以表现层文档宣称功能完成。
3. 对 `src/semantic_trigger/` 的工作视为历史路径维护或迁移验证，除非后续 runtime contract 明确将其重新纳入主线。
4. 运行产生的报告、数据库与密钥文件保持在本地忽略范围内，避免与代码和设计源混在提交中。
