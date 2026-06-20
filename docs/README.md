# Aphrodite

Aphrodite 是一个面向**具身角色存在感**的 AI 角色系统实验仓库。项目把角色解释、记忆、关系边界、身体表现和场景反馈组织成可以测试、回放并逐步接入表现层的运行闭环，而不是只生成一段看似合适的聊天回复。

当前仓库处于 **Python 运行切片与设计验证并行推进** 阶段：已有可运行的场景演示、编排循环、解释器校准与测试资产；Live2D、完整游戏内嵌表现层和部分 body-mind 统一集成仍属于设计或原型方向。

## 项目边界

Aphrodite 不是通用助手、聊天机器人、NPC、情绪引擎、生产力工具或普通 agent demo。工程实现应保持以下原则：

- 角色表达以身体状态、距离、停顿、记忆重量和克制语言为核心，而非服务式迎合。
- 技术或专业问题由工程/导演层处理，不让角色人格承担通用助手职责。
- 脆弱、依赖或危机语境必须保留边界，不把危险材料直接实现为关系操控或亲密升级。
- 涉及 persona、关系、记忆显著性与身体影响层的改动，先对齐 [设计宪章索引](design/README.md)。

## 当前可验证能力

| 能力切片 | 当前状态 | 主要位置 / 入口 |
| --- | --- | --- |
| 固定场景演示包 | 可直接运行；提供安全、社交、任务三套剧本及最小指标面板 | [`cli/run_demo_pack.py`](../cli/run_demo_pack.py)、[`demos/scenarios/`](../demos/scenarios/) |
| Autonomy 编排闭环 | 代码和测试入口已存在 | [`agentlib/autonomy/`](../agentlib/autonomy/)、[`tests/test_autonomy_demo_v2_e2e.py`](../tests/test_autonomy_demo_v2_e2e.py) |
| Runtime 与 self-drive 链路 | 已有实现与测试资产；集成路径仍在稳定中 | [`agentlib/runtime_engine.py`](../agentlib/runtime_engine.py)、[`agent_kernel/`](../agent_kernel/) |
| 输入解释与边界信号 | 已实现并有 golden cases / 校准报告；输出 persona 路由、风险与表现信号 | [`src/interpreter/`](../src/interpreter/)、[`tests/golden_cases/`](../tests/golden_cases/)、[`reports/interpreter_smoke_phase_2_2.md`](reports/interpreter_smoke_phase_2_2.md)、[`reports/interpreter_calibration_phase_2_3.md`](reports/interpreter_calibration_phase_2_3.md) |
| 记忆、关系与动作模块 | 已拆分为代码模块并有针对性测试；仍在向统一 runtime 收敛 | [`src/memory/`](../src/memory/)、[`src/relationship/`](../src/relationship/)、[`src/body/`](../src/body/)、[`src/core/`](../src/core/) |
| 离线 RAG 与检索评估 | 工具链存在，可用于样本构建、调参与回放 | [`rag_offline/`](../rag_offline/) |
| Live2D / 语音 / 游戏表现层 | 以架构文档为主，尚非可交付前端 | [`architecture/`](../architecture/)、[`live2d-integration-design.md`](live2d-integration-design.md) |

[`src/semantic_trigger/`](../src/semantic_trigger/) 保留为历史/实验与回归参考路径；按照当前 runtime contract，它不是具身主链路的必经模块。

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

统一事件模型与过渡映射见 [`embodied_runtime_contract.md`](embodied_runtime_contract.md)。其中定义的 `perception -> decision -> actuation` Envelope 是后续集成的协议目标，不代表每个表现层能力都已经接通。

## 快速开始

### 1. 准备环境

建议使用 Python 3.10+ 与虚拟环境。在 Windows PowerShell 中：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -X utf8 -m pip install -r requirements.txt
```

仓库包含 UTF-8 编码的中文场景和测试数据；在 Windows 默认代码页不是 UTF-8 时，建议像下文一样通过 `python -X utf8` 运行项目命令。固定场景演示只依赖本地仓库内容，不需要 API key。涉及模型调用、外部语音或完整记忆检索时，再根据 [`.env.example`](../.env.example) 创建本地 `.env`。

### 2. 运行标准场景演示

这是最稳定、最适合观察输出形态的入口：

```powershell
python -X utf8 cli/run_demo_pack.py --scenario all --save-report outputs/demo/demo_report.json
```

演示包会依次运行：

- `security_scene`：异常识别、隔离动作与告警路径。
- `social_scene`：来访接待、意图确认与路径引导。
- `task_scene`：需求接收、执行分发与结果回执。

输出包含事件吞吐、动作成功率和降级次数。剧本说明见 [`demo/playbooks.md`](demo/playbooks.md)，现场故障处理见 [`demo/failure_runbook.md`](demo/failure_runbook.md)。

### 3. 编排循环演示入口

```powershell
python -X utf8 -m agentlib.autonomy.demo_v2
```

这个入口设计用于展示任务生成、工具执行、评估、反思、失败路由和重试/回退，而不是完整视觉角色应用。

### 4. 查看解释器校准

解释器用于把输入转换为技术路由、人格非进入、关系/边界风险与身体表现信号。已有结果可直接阅读：

- [Phase 2.2 smoke report](reports/interpreter_smoke_phase_2_2.md)
- [Phase 2.3 calibration report](reports/interpreter_calibration_phase_2_3.md)

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

## 目录结构

```text
Aphrodite-demo/
├── agentlib/              # 运行时、autonomy 闭环、router 与调度能力
├── agent_kernel/          # planner / worker / judge / failure guard 内核
├── src/
│   ├── interpreter/       # 输入解释与边界/表现信号
│   ├── core/              # 状态权威、事件与 trace
│   ├── memory/            # 记忆 gate 与存储模型
│   ├── relationship/      # 关系引擎（非 emotion-label 系统）
│   ├── body/              # 动作混合
│   └── semantic_trigger/  # 历史/实验路径
├── cli/                   # 演示与评估入口
├── demos/scenarios/       # 固化演示剧本
├── tests/                 # 单元、集成、golden cases 与 fixtures
├── rag_offline/           # 离线检索训练、评估和回放
├── scripts/               # 报告生成与演示辅助脚本
├── config/                # 当前关系/延迟策略配置
├── configs/               # trigger 与 app 示例配置
├── docs/                  # 设计、契约、报告和演示手册
│   ├── design/            # 权威设计入口
│   ├── reports/           # 可验证能力报告
│   ├── demo/              # 演示运行手册
│   └── archive/legacy-continuity/  # 归档连续性材料，不作当前依据
└── architecture/          # Live2D/协议/接口设计稿（参考性质）
```

本地运行产生的 `.env`、`outputs/`、`data/`、`memory.sqlite`、`memory.faiss`、`memory_ids.npy` 和监控数据库均不应作为源码提交；忽略规则见 [`.gitignore`](../.gitignore)。

## 文档导航

### 先读这些

| 文档 | 用途 |
| --- | --- |
| [design/README.md](design/README.md) | 设计权威层级、角色边界与修改前置约束 |
| [embodied_runtime_contract.md](embodied_runtime_contract.md) | 具身事件模型、生命周期与现有 runtime 映射 |
| [lobster_agent_flow.md](lobster_agent_flow.md) | `runtime_engine` 与 `agent_kernel` 的规划/执行链路说明 |
| [demo/playbooks.md](demo/playbooks.md) | 三套标准演示场景与运行方式 |

### 产品与工程设计

| 文档 | 用途 |
| --- | --- |
| [Aphrodite_body_mind_full_plan.md](Aphrodite_body_mind_full_plan.md) | body-mind 系统整体规划与阶段拆解 |
| [Aphrodite_v2_engineering_plan.md](Aphrodite_v2_engineering_plan.md) | 工程风险、优先级与推荐实现顺序 |
| [Aphrodite_v2_engineering_issues.md](Aphrodite_v2_engineering_issues.md) | 问题归档 |
| [Aphrodite_v2_visual_direction_production_guide.md](Aphrodite_v2_visual_direction_production_guide.md) | 视觉方向与制作约束；由 `design/README.md` 设计层级直接引用 |

### 运行与调试参考

| 文档 | 用途 |
| --- | --- |
| [error_ledger_paths.md](error_ledger_paths.md) | 错误分类与路径速查 |
| [voice_sample_sources.md](voice_sample_sources.md) | 语音样本来源与授权注意事项 |
| [debugging.md](debugging.md) | 通用调试说明 |
| [debugging_guide.md](debugging_guide.md) | 历史 trigger 路径调试命令与回归数据入口 |
| [trigger_schema.md](trigger_schema.md) | 触发器 schema；主要服务于 `src/semantic_trigger/` 历史路径 |

## 开发约定

1. 修改 persona、relationship、memory salience、body influence 或相关路由前，先阅读 [design/README.md](design/README.md) 指向的设计源文件。
2. 新能力优先接入当前 runtime / autonomy 测试链路，并补充可回放的测试或报告；不要仅以表现层文档宣称功能完成。
3. 对 `src/semantic_trigger/` 的工作视为历史路径维护或迁移验证，除非后续 runtime contract 明确将其重新纳入主线。
4. 运行产生的报告、数据库与密钥文件保持在本地忽略范围内，避免与代码和设计源混在提交中。
