# 仓库整理与 Embedding 部署文件台账

> 用途：用于后续目录整理、文件迁移、Embedding 模型与索引落位确认。
> 盘点日期：2026-05-25。
> 文件性质：documentation-only 快照（snapshot），记录 2026-05-25 工作区状态；后续仓库结构、测试位置、导入关系可能已变化，引用时请核对当前文件系统。
> 本文件只记录盘点当日的文件分布、引用关系和整理事项，不代表功能完成状态。

## 1. 盘点时的工作区状态

| 项目 | 当前情况 | 整理时处理原则 |
| --- | --- | --- |
| Git 分支 | `main`，落后 `origin/main` 2 个提交 | 开始批量迁移前先确认同步策略 |
| 已修改文件 | `README.md` 已修改，尚未提交 | 与后续整理提交分开确认或一起纳入明确提交 |
| Gitlink 删除状态 | `Aphrodite-demo`、`Aphrodite-demo-test` 在 `HEAD` 中是 gitlink，当前工作区显示已删除 | 不与目录整理混在一起处理，先确认是否有意删除 |
| 本地环境文件 | `.env` 存在且已忽略 | 不搬入文档、数据或提交目录 |
| 配置示例风险 | `.env.example` 为跟踪文件，其中 `GLM_API_KEY` 当前为非空值 | 对外共享或提交前改为占位符并轮换对应密钥 |
| 本地生成物 | `outputs/`、`data/`、根目录记忆数据库/索引存在且已忽略 | 按运行产物处理，不当作源码迁移 |

## 2. 顶层目录台账

| 当前路径 | 跟踪文件数 | 内容归类 | 主要关联 | 整理建议 |
| --- | ---: | --- | --- | --- |
| `agentlib/` | 62 | 当前运行时、记忆/RAG、persona 路由、autonomy、调度 | `agent_kernel/`、`src/`、`monitor/` | 代码主路径之一，暂不移动 |
| `agent_kernel/` | 13 | planner/worker/judge/failure guard 内核 | `agentlib/runtime_engine.py`、`agentlib/autonomy/` | 暂不移动 |
| `src/` | 46 | interpreter、core、memory、relationship、body、voice、历史 trigger 等模块 | `agentlib/runtime_engine.py`、测试、CLI | 暂不移动；先确认主路径边界 |
| `rag_offline/` | 16 | Embedding 训练、评估、调参、回放数据与脚本 | `agentlib/companion_rag.py`、模型目录 | 保留为数据/模型流水线区，可后续重命名归类 |
| `cli/` | 8 | 演示、回放、历史 trigger 评估入口 | `demos/`、`src/semantic_trigger/`、`agentlib/` | 暂不移动 |
| `scripts/` | 16 | 报告、辅助生成与演示脚本 | `src/interpreter/`、`agentlib/` | 暂不移动；后续按用途分子目录 |
| `demos/` | 3 | 标准演示场景 JSON | `cli/run_demo_pack.py` | 保留 |
| `tests/` | 89 | 测试、fixtures、golden cases、校准数据 | 各代码目录 | 保留；根目录遗留测试待并入 |
| `docs/` | 20 + 本文件 | 设计文档、契约、报告、demo 手册 | 根目录计划稿、`architecture/` | 作为文档收拢目标目录 |
| `architecture/` | 8 | Live2D/表现层接口与协议设计稿 | `docs/live2d-*` | 可后续并入 `docs/architecture/` |
| `research/` | 3 | 调研资料 | 表现层设计 | 可后续并入 `docs/research/` |
| `emotion-protocol/` | 8 | 独立协议演示子目录 | 表现层设计 | 先独立保留 |
| `config/` | 2 | 关系/延迟策略 YAML | runtime 相关模块 | 与 `configs/` 的归属待统一 |
| `configs/` | 2 | app / trigger 示例 YAML | `src/semantic_trigger/` 路径 | 与 `config/` 的归属待统一 |
| `evals/` | 3 | 路由评估 JSONL | router / eval 脚本 | 保留为受版本管理的数据 |
| `monitor/` | 3 跟踪 + 1 本地 | prompts、RAG docs、调试 runner、运行数据库 | runtime / RAG 配置 | 静态资产与运行产物需拆开 |
| `memory/` | 2 跟踪 + 1 本地 | 会话日记与本地测试数据库 | 工作区上下文 / 记忆测试 | 日记与模型记忆数据库不应混放 |
| `outputs/` | 0 | 本地报告和 task run 输出 | CLI / scripts | 保持忽略，后续迁入统一运行产物目录亦可 |
| `data/` | 0 | 本地数据 | 当前未知/运行输入 | 保持忽略，整理前先分类内容 |

## 3. 根目录散落文件

### 3.1 应保留在根目录的入口文件

| 文件 | 类别 | 处理建议 |
| --- | --- | --- |
| `README.md` | 项目入口 | 保留根目录 |
| `requirements.txt` | Python 依赖 | 保留根目录，后续若更换依赖管理再迁移 |
| `.env.example` | 配置模板 | 保留根目录；先清空非占位密钥 |
| `.gitignore` | 忽略规则 | 保留根目录；模型目录确定后补充规则 |
| `AGENTS.md` | 工作区规则 | 保留根目录 |

### 3.2 工作区连续性文件

| 文件 | 当前用途 | 处理建议 |
| --- | --- | --- |
| 根目录 `SOUL.md`、`USER.md`、`MEMORY.md`、`IDENTITY.md`、`HEARTBEAT.md`、`TOOLS.md` | 2026-05-25 盘点时存在于根目录的工作区连续性文件；当前权威规则要求将 legacy continuity 材料归档至 `docs/archive/legacy-continuity/` | 已按当前项目规则归档；不要将其作为 Aphrodite runtime/persona 当前依据 |
| `memory/*.md` | 2026-05-25 盘点时的每日记录 | 保留为历史连续性记录，不与向量数据库目录合并 |

### 3.3 可收拢到 `docs/` 的文档

| 当前文件 | 建议归类目录 |
| --- | --- |
| `Aphrodite_body_mind_full_plan.md` | `docs/plans/` |
| `Aphrodite_body_mind_v2_archive.md` | `docs/archive/` |
| `Aphrodite_v2_engineering_plan.md` | `docs/plans/` |
| `Aphrodite_v2_engineering_issues.md` | `docs/archive/` 或 `docs/reports/` |
| `Aphrodite_v2_visual_direction_production_guide.md` | `docs/design/` |
| `architecture.md` | `docs/architecture/` |
| `FRAMEWORK.md` | `docs/archive/` 或 `docs/architecture/`，先核对是否仍为有效说明 |
| `DELIVERY_REPORT.md` | `docs/reports/` |

### 3.4 根目录遗留测试和脚本

| 当前文件 | 关联内容 | 建议归类 |
| --- | --- | --- |
| `test_character_sakiko.py` | `src/character/` / 样本角色 | 已删除（legacy removed） |
| `test_memory_sakiko.py` | 记忆模块 | 已删除（legacy removed） |
| `test_memory_sakiko_mock.py` | 记忆 mock | 已删除（legacy removed） |
| `test_memory_simple.py` | 记忆模块 | 已删除（legacy removed） |
| `setup_env.ps1` | 环境准备 | 保留根目录或并入 `scripts/setup/` |
| `submit_pr.sh` | 提交流程辅助 | 并入 `scripts/dev/` 前先检查使用方 |

## 4. 当前文件关系图

### 4.1 运行与解释路径

```text
agentlib/runtime_engine.py
├── agent_kernel/                         # 规划、执行、判断、失败守护
├── src/interpreter/input_interpreter.py  # 输入解释（盘点时的目标路径；请以当前 src/interpreter/ 实际文件为准）
├── src/core/                             # 状态与 trace
├── src/memory/memory_gate.py             # 写入判定（盘点时的目标路径；请以当前 src/memory/ 实际文件为准）
├── src/relationship/relationship_engine.py  # 盘点时的目标路径；当前实现可能尚未完成
└── src/body/action_mixer.py              # 盘点时的目标路径；当前实现可能尚未完成
```

| 关系 | 当前注意事项 |
| --- | --- |
| `agentlib/runtime_engine.py` -> `src/interpreter/schema.py` | 据 2026-05-25 盘点，此处存在 `unknown_output` 引用问题；[`src/interpreter/schema.py`](../../src/interpreter/schema.py) 当前已提供该函数，导入是否仍阻塞请按当前代码重新验证（本文件为快照，不保证最新状态）。 |
| `agentlib/autonomy/` -> `agent_kernel/` | autonomy 编排使用内核的 failure/compile/replan 组件 |
| `cli/run_demo_pack.py` -> `demos/scenarios/*.json` | 当前可独立运行的标准场景入口，不依赖上述导入 |
| `scripts/interpreter_*_report.py` -> `src/interpreter/` | 用于生成解释器报告 |

### 4.2 历史 trigger 路径

```text
configs/*.example.yaml
        |
        v
cli/run_trigger_demo.py / cli/eval_trigger_engine.py / cli/eval.py
        |
        v
src/semantic_trigger/
```

`src/semantic_trigger/` 仍被 CLI 和单元测试引用，但在当前 README/runtime contract 中不是具身运行主链路。整理时不应直接删除，适合标记为 `legacy` / `experimental` 后再逐步解耦。

## 5. Embedding 与检索文件链

### 5.1 依赖、配置与运行引用

| 文件 | 相关条目 / 行为 | 与整理的关系 |
| --- | --- | --- |
| `requirements.txt` | `numpy`、`faiss-cpu`、`sentence-transformers` | Embedding 运行依赖入口 |
| `.env.example` | `RAG_MODE`、`RAG_EMBED_MODEL`、`RAG_INDEX_PATH`、`RAG_DOCS_PATH` 及 RAG 参数 | 配置模板；密钥字段需先清理 |
| `agentlib/memory_store.py` | 默认模型 `BAAI/bge-small-zh-v1.5`；默认写入 `memory.sqlite`、`memory.faiss`、`memory_ids.npy` | 当前 companion RAG 实际会尝试创建的记忆存储实现 |
| `agentlib/companion_rag.py` | `MemoryStore()` 初始化；`embedding` / `hybrid` 检索；失败时可退回 keyword/ephemeral 路径 | 在线/运行时 RAG 连接点 |
| `agentlib/persona_router.py` | 读取 `RAG_EMBED_MODEL` 并加载 `SentenceTransformer` | 同一模型变量还用于 persona 路由 |
| `src/memory/store.py` | 默认模型相同；默认写入同名 `memory.sqlite`、`memory.faiss`、`memory_ids.npy` | 第二套记忆存储实现，默认产物名与 `agentlib` 冲突 |
| `src/semantic_trigger/embedder.py` | 独立 embedder 接口与 hash 实现 | 历史 trigger 路径，不等同于当前 RAG 模型部署入口 |

### 5.2 训练与评估路径

```text
rag_offline/evalset_template.jsonl
        |
        v
rag_offline/prepare_triplets.py
        |
        v
rag_offline/triplets.jsonl
        |
        v
rag_offline/train_embedding.py
        |
        v
指定输出模型目录（文档示例：monitor/rag_embed_model）
```

| 文件 | 输入 / 输出位置 | 整理处理 |
| --- | --- | --- |
| `rag_offline/evalset_template.jsonl` | 版本内评估模板 | 作为受版本管理的数据保留 |
| `rag_offline/triplets.jsonl` | 训练 triplets，当前已跟踪 | 确认其是否应长期版本管理 |
| `rag_offline/prepare_triplets.py` | 输入评估集，输出 triplets | 保留在模型数据流水线组 |
| `rag_offline/train_embedding.py` | 输入 triplets，输出模型目录 | 模型输出目录需先统一 |
| `rag_offline/conversation_train.py` | 可输出会话、corpus、triplets 和模型目录 | 生成输出应进入忽略目录 |
| `rag_offline/eval_retrieval.py` | 输入数据集与模型参数，输出报告 | 当前模型覆盖参数到 runtime 的连接需要核对 |
| `rag_offline/tune_rag_params.py` | 输出 `rag_offline/tune_report.json` | 输出已被 `.gitignore` 覆盖 |
| `rag_offline/export_env.py` | 输出 RAG 配置片段 | 生成配置与正式 `.env` 分开保存 |
| `rag_offline/best_rag.keyword.env` | 当前跟踪文件 | 判断是示例配置还是历史产物后再归类 |

### 5.3 当前已存在的本地模型/索引相关产物

| 当前路径 | Git 状态 | 来源 / 用途判断 | 整理建议 |
| --- | --- | --- | --- |
| `memory.sqlite` | 已忽略，本地存在 | `agentlib/memory_store.py` 或 `src/memory/store.py` 默认数据库名 | 在确认主存储实现前备份，不迁入源码 |
| `memory.faiss` | 已忽略，本地存在 | 默认向量索引名 | 与数据库、ID 映射作为一组保留 |
| `memory_ids.npy` | 已忽略，本地存在 | 默认索引到数据库 ID 映射 | 与上两项作为一组保留 |
| `memory/test_sakiko.sqlite` | 已忽略，本地存在 | 旧测试数据库 | 移到统一本地测试产物目录或清理前备份 |
| `monitor/metrics.db` | 已忽略，本地存在 | runtime 监控数据库 | 不与向量数据库混放 |
| `monitor/companion_rag_docs.json` | 已跟踪 | companion RAG 文档源 | 作为静态输入保留或迁入数据集目录 |
| `monitor/companion_rag.faiss` | 配置指向，当前未发现文件 | `.env.example` 中的索引目标 | 确认是否仍采用该索引命名 |
| `monitor/rag_embed_model` | 文档/脚本示例目标，当前未发现目录 | 训练模型输出位置 | 部署前改为统一模型目录 |
| `monitor/rag_embed_model_conversation` | 脚本默认目标，当前未发现目录 | 会话训练模型输出位置 | 部署前改为统一模型目录 |

## 6. Embedding 整理前必须确认的冲突点

| 编号 | 问题 | 涉及文件 | 整理决定 |
| ---: | --- | --- | --- |
| 1 | 存在两套 `MemoryStore`，默认数据库/索引/ID 文件名称完全相同，但数据库结构不同 | `agentlib/memory_store.py`、`src/memory/store.py` | 部署模型前选定主实现；未选定前不要用同一产物目录运行二者 |
| 2 | 运行时 companion RAG 实例化的是 `agentlib.memory_store.MemoryStore()` | `agentlib/companion_rag.py` | 若继续沿用当前 runtime，模型和索引落位优先围绕 `agentlib/` 路径处理 |
| 3 | `RAG_EMBED_MODEL` 被 persona router 读取，但 `MemoryStore()` 默认模型直接写在构造器默认值中 | `.env.example`、`agentlib/persona_router.py`、`agentlib/memory_store.py` | 决定配置是否统一驱动两个消费者 |
| 4 | `.env.example` 声明 `RAG_INDEX_PATH` / `RAG_DOCS_PATH`，当前盘点未发现 `agentlib/companion_rag.py` 直接读取这两个路径 | `.env.example`、`agentlib/companion_rag.py` | 判断配置项是待接入还是历史残留 |
| 5 | 离线评估脚本尝试覆盖模型字段，但当前 `RagConfig` 中未见对应 `model_name` 字段 | `rag_offline/eval_retrieval.py`、`agentlib/companion_rag.py` | 训练模型接入评估前核对脚本有效性 |
| 6 | 训练脚本建议把模型输出到 `monitor/`，而 `monitor/` 当前同时放静态资源与运行数据库 | `rag_offline/*.py`、`monitor/` | 将模型、索引、监控数据库拆分到不同目录 |
| 7 | `.gitignore` 忽略了部分索引/数据库，但没有明确覆盖训练得到的模型目录 | `.gitignore`、`monitor/rag_embed_model*` | 模型落位确定后补充忽略或大文件管理规则 |

## 7. 建议的整理落位草案

以下目录仅作为整理目标，当前尚未实施。源码包移动会影响 import，建议在第二阶段处理；第一阶段优先整理文档和本地产物位置。

```text
Aphrodite-demo/
├── agentlib/                         # 当前 runtime 与在线 RAG 代码，先保留
├── agent_kernel/                     # 当前内核代码，先保留
├── src/                              # 当前模块代码，先保留
├── rag_offline/                      # 模型数据脚本，先保留
│
├── configs/
│   ├── runtime/                      # relationship / latency 等运行配置
│   └── examples/                     # app / trigger / env 示例配置
│
├── datasets/
│   └── rag/                          # 确认可版本管理的 eval / triplets 数据
│
├── var/                              # 全部本地生成物；默认不提交
│   ├── models/embedding/             # 基础或微调 Embedding 模型目录
│   ├── indexes/                      # FAISS 索引与 id map
│   ├── db/                           # 运行时 SQLite 数据库
│   ├── reports/                      # 评估和运行报告
│   └── monitor/                      # metrics、screenshots、音频等
│
├── docs/
│   ├── design/
│   ├── architecture/
│   ├── plans/
│   ├── reports/
│   ├── research/
│   └── archive/
└── tests/
    └── legacy/                       # 当前根目录 test_*.py 的候选去处
```

### 7.1 优先可整理项

| 动作 | 当前来源 | 目标分类 |
| --- | --- | --- |
| 收拢根目录设计稿 | `Aphrodite_*.md`、`architecture.md`、`FRAMEWORK.md`、`DELIVERY_REPORT.md` | `docs/` 对应分类 |
| 区分版本数据与生成数据 | `rag_offline/*.jsonl`、`evals/`、`outputs/`、`data/` | `datasets/` 与 `var/` |
| 划分静态监控资源与本地产物 | `monitor/` | 静态输入保留；数据库/索引/模型转入 `var/` |
| 统一配置目录 | `config/`、`configs/` | `configs/runtime/` 与 `configs/examples/` |
| 归类根目录遗留测试 | `test_*.py` | `tests/legacy/` 或按模块拆分 |

### 7.2 暂缓移动项

| 路径 | 暂缓原因 |
| --- | --- |
| `agentlib/` | runtime 与 RAG 实际连接点在此，移动会影响大量导入 |
| `agent_kernel/` | 被 runtime 和 autonomy 直接导入 |
| `src/` | 包含现行模块及历史路径，需先完成归属判断 |
| `cli/`、`scripts/` | 命令和文档已经引用现路径 |
| `emotion-protocol/` | 相对独立，先确定是否仍纳入仓库主目标 |

## 8. Embedding 模型落位清单

| 待决定事项 | 当前候选 / 现状 | 决定后需要同步的文件 |
| --- | --- | --- |
| 在线主存储实现 | `agentlib/memory_store.py` 与 `src/memory/store.py` 并存；runtime 当前走前者 | runtime 导入、测试、产物路径说明 |
| Embedding 模型目录 | 当前脚本示例位于 `monitor/rag_embed_model*`；建议独立到 `var/models/embedding/` | `rag_offline/train_embedding.py` 调用参数、`.env`、忽略规则 |
| 在线索引目录 | 当前根目录存在 `memory.faiss` / `memory_ids.npy`；配置另指向 `monitor/companion_rag.faiss` | `MemoryStore` 初始化位置、`.env`、迁移脚本或备份策略 |
| 在线数据库目录 | 当前根目录 `memory.sqlite` 存在 | `MemoryStore` 初始化位置、备份与忽略规则 |
| 训练数据归属 | `rag_offline/triplets.jsonl` 已跟踪；训练期间还会生成 conversation 数据 | 决定哪些进入 `datasets/rag/`，哪些进入 `var/` |
| 评估输出归属 | `rag_offline/tune_report*.json` 已忽略；其他报告目标分散 | 统一到 `var/reports/` 或明确保留现状 |
| 模型配置入口 | `.env.example` 中已有 `RAG_EMBED_MODEL` | 清理密钥后，将最终模型路径与索引路径写入本地 `.env` |

## 9. 推荐整理顺序

1. 先处理安全与版本状态：清理 `.env.example` 非占位密钥；确认两个已删除 gitlink 的意图。
2. 选定 Embedding 的在线消费者：优先确认是否以 `agentlib/memory_store.py` 作为当前 runtime 使用的存储实现。
3. 为模型、索引、数据库、报告确定唯一的本地生成物根目录，并补充 `.gitignore`。
4. 备份当前 `memory.sqlite`、`memory.faiss`、`memory_ids.npy` 后，再迁移或重建索引。
5. 将训练/评估数据区分为“需要版本管理的输入数据”和“本地生成结果”。
6. 收拢根目录文档、配置和遗留测试；源码包目录最后处理。
7. 修复或确认 runtime/interpreter 导入阻塞与离线评估模型接入后，再以部署后的 Embedding 模型跑回归。

