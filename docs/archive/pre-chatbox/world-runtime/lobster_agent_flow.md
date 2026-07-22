# 龙虾代理（Coach→Codex）相关代码巡检

本文件用于快速定位“用户指令 → LLM Coach 规划目标/阶段 → Codex 执行命令”的现有实现。

## 1. 入口与总流程

- 运行时核心在 `agentlib/runtime_engine.py`。
- 自驱模式（selfdrive）会初始化 `AgentKernel`，并注入：
  - `V15Planner`（规划节点）
  - `SpecialistRouterWorker`（专家路由）
  - `GLM5PlannerAdapter(client=self._glm5_plan_for_selfdrive)`（规划器适配）
  - `CodexCodeAdapter(client=self._codex_execute_for_selfdrive)`（代码执行适配）

这基本就是你说的“先由 coach/planner 产出可执行计划，再交给 codex 执行”的主链路。

## 2. 用户意图与 persona（含 coach）

- `agentlib/persona_router.py`：根据关键词/主题/向量相似度，在 `aphrodite / coach / analyst / codex5.2` 间路由。
- `agentlib/persona_profiles.py`：定义了 `coach` 与 `codex5.2` 的 persona 规则。
- `runtime_engine._maybe_auto_switch_persona()`：每回合会根据用户输入自动切 persona。

说明：当前“coach”偏语言层行为约束（回复风格、行动清单），并不是单独的计划执行器；计划执行器在 kernel + planner adapter。

## 3. 规划阶段（可视作 LLM Coach 角色）

- `runtime_engine._glm5_plan_for_selfdrive()`：
  - 读取 goal/context/capability snapshot/剩余预算
  - 以 strict JSON schema 提示 LLM 输出 `generated_subgoals`
  - 输出字段要求含 `subgoal_id/int…/executor_type/tool_name/inputs/success_criteria/retry_policy` 等
  - 失败时会 fallback 到最小可执行子目标

- `agent_kernel/adapters.py::GLM5PlannerAdapter.plan()`：
  - 吞并不同 planner 输出形态（`generated_subgoals/subgoals/tasks`）
  - 编译并规范化为内核任务
  - 若空计划，注入 fallback subgoal

## 4. 计划到执行命令的转换

- `agent_kernel/worker.py::SpecialistRouterWorker`：
  - 对 task kind 做候选专家选择（planner/codex）
  - 对输入进行语义/安全校验（依赖、URL、风控等）
  - 将 `plan_goal` 转给 planner adapter；将 `code_task` 转给 codex adapter

- `runtime_engine._codex_execute_for_selfdrive()`：
  - 根据任务复杂度选择 fast/deep lane
  - 调用 `CodexDelegateClient.try_chat_json()`
  - 校验 delegate 输出 schema（`ok/output/error/wait_user/artifacts`）
  - 包装为内核可消费的统一结果结构

- `agentlib/codex_delegate.py`：
  - 管理 OpenAI/Codex 调用接口
  - 支持 responses/chat/auto surface
  - 标准化错误类别（auth/environment_missing/parse_error/permission_denied/execution_error）

## 5. 编排与守护能力

- `agent_kernel/kernel.py`：运行循环、依赖检查、compile gate、失败路由、局部重规划。
- `agent_kernel/judge.py`：根据 worker 输出决定 accept/replan/ask_user。
- `agent_kernel/compile_check.py`：执行前静态 gate（依赖、工具可用性、约束）。
- `agent_kernel/failure_router.py` + `circuit_breaker.py`：异常分类与循环保护。

## 6. 与“龙虾代理”目标的映射建议

如果你要把“代理部分”产品化成“龙虾”人格，可分三层做，不需要重写内核：

1. **人格层（龙虾 persona）**
   - 在 `persona_profiles.py` 增加 `lobster` 档案。
   - 在 `persona_router.py` 增加与“执行/开发/任务推进”相关的龙虾触发词。

2. **规划层（Coach 语义显式化）**
   - 保留 `_glm5_plan_for_selfdrive`，但将 system prompt 中 planner 角色文案改成“龙虾教练”。
   - 强化 subgoal schema：例如强制每步附“可观测验收条件”。

3. **执行层（Codex 命令约束）**
   - 在 `_codex_execute_for_selfdrive` 的 task_payload 中加入 `persona=lobster`（注意修正拼写）与执行策略标签。
   - 在 `_dry_run_validate_task_payload` 扩展命令白名单和路径白名单，避免“龙虾”执行越权操作。

## 7. 你当前最关键可复用点

你描述的“用户给指令 -> coach 规划阶段目标 -> 转换给 codex 命令并最终完成”，代码库里已经有可直接复用的闭环：

- 目标输入：`_start_selfdrive_session()` / selfdrive kernel state
- 规划：`_glm5_plan_for_selfdrive()` + `GLM5PlannerAdapter`
- 执行：`_codex_execute_for_selfdrive()` + `CodexDelegateClient`
- 守护：`plan_compile_check` + `failure_router` + `circuit_breaker`

下一步真正要做的，不是“从零实现”，而是把这条链路命名/提示词/约束策略替换为“龙虾代理”的产品语义。


## 8. 当前已落地的“完整链路”机制（本次增强）

1. **输入审查 + 权限询问**
   - `START_SELFDRIVE` 前新增请求审查：检查目标是否可执行（非空、非“随便做”等模糊目标）。
   - 当检测到高风险操作或配置了非全权限模式时，会进入确认态并要求用户“确认执行”。

2. **Coach 产出可执行任务约束**
   - `GLM5PlannerAdapter` 新增 actionability 编译规则：
     - 非行动性 intent（如 brainstorm/聊聊/想一想）会被拒绝。
     - code_task 必须包含 `inputs.instruction`，否则拒绝。

3. **Codex 测试 / Debug / 非侵入执行**
   - 保持现有 `PatchExecutionTransaction`：补丁执行后会做 verify（优先 verify_command，缺省 py_compile），失败自动回滚。
   - 该流程天然支持“先改动→再验证→失败回滚”的非侵入闭环。

4. **失败原因分类（可观测）**
   - `failure_router` 新增分类：
     - `goal_not_executable`
     - `permission_denied`
     - `environment_missing`
   - 内核在 `ASK_USER` 路径会按分类给出不同的等待用户提示，便于下一步补齐信息。


## 9. 主链路去重（本次整理）

- 新增统一的 `selfdrive` 启动参数构建函数（goal/budget/confirmed），替换多处重复解析逻辑。
- `confirmed` 回调路径改为“编译一次 DSL、执行一次控制命令”，避免重复 `intent` 判断和重复启动分支。
- 保留原有功能语义（权限确认、目标可执行性检查、budget 透传），但减少了冗余路径调用，降低主链路分支复杂度。

