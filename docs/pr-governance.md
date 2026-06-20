# PR 治理规则（路径级）

本规则用于支持分包并行开发，目标是：

- 防止分包互相踩代码
- 将审批复杂度控制在路径级，而非一次性引入复杂 RBAC
- 将高风险改动限制在可审计、可回滚的最小范围

## 1. 模块白名单

白名单定义在 `.github/module-whitelist.yml`：

- 每个模块仅允许修改自己路径
- 文档与治理类文件走 `global_allowlist`
- PR 必须声明模块（标题 `[module:<name>]` 或正文 `模块: <name>`）

当前模块：

- `agentlib` → `agentlib/**`
- `agent-kernel` → `agent_kernel/**`
- `semantic-trigger` → `src/semantic_trigger/**`
- `character` → `src/character/**`
- `memory` → `src/memory/**`
- `voice` → `src/voice/**`
- `cli` → `cli/**`
- `tests` → `tests/**`

> 注：`character` 与 `voice` 模块声明（`src/character/`、`src/voice/`）对应路径已在阶段 3 移除，当前白名单保留条目仅用于配置兼容性，后续需由 governance 配置任务清理 `.github/module-whitelist.yml` 与 `.github/CODEOWNERS`。本步骤不修改配置文件。

## 2. 权限级别（Owner / Maintainer / Contributor）

先从路径级治理，不做复杂 RBAC：

- **Owner**
  - 全仓库最终责任人
  - 可审批所有路径，尤其是高风险路径
- **Maintainer（按模块）**
  - 负责本模块日常开发与评审
  - 不得单独放行高风险路径
- **Contributor**
  - 可提交 PR
  - 需要 Maintainer/Owner 审批后合并

`CODEOWNERS` 负责把“谁能审哪些路径”固化为仓库规则。

## 3. 审批规则（建议与分支保护联动）

> 建议在 GitHub Branch Protection 中设置 “Require review from Code Owners”。

- **普通改动（仅命中单模块路径）**
  - 至少 `1` 位对应模块 Maintainer 审批
- **跨模块改动（命中多个模块路径）**
  - 至少 `2` 位审批：`1` 位相关 Maintainer + `1` 位 Owner
- **高风险改动（命中 high_risk_paths）**
  - 必须包含 `1` 位 Owner 审批

## 4. PR 模板强制项

PR 模板位于 `.github/PULL_REQUEST_TEMPLATE.md`，必填：

1. 改动范围（模块、目录、是否跨模块）
2. 风险评估（风险等级、风险点、影响面）
3. 回滚方案（触发条件、步骤、数据兼容）
4. 验证证据（测试命令、结果、日志/截图）
5. 合规问答（4 个“是/否”问题，见模板）

## 5. CI 阻断规则

工作流 `.github/workflows/pr-governance.yml` 会在 PR 时运行：

- 脚本 `.github/scripts/check_path_scope.py` 比较 `base...head` 改动
- 若出现越界路径，CI 默认失败，阻止合并
- 仅当 PR 明确填写跨包批准行 `跨包越界批准: APPROVED by @...（原因）` 时允许越界继续（并保留警告）
- 若 PR 未声明模块、合规问答未填写或答案与实际变更不一致，CI 失败

---

## 落地建议

1. 先替换 `.github/CODEOWNERS` 中的占位团队（`@your-org/...`）
2. 开启分支保护并要求 Code Owners 审批
3. 观察 1-2 周后再细化模块和高风险路径
