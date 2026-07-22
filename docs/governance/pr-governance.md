# Chatbox v0 PR 治理规则

Status: CURRENT PROJECT RULE
Authority-Scope: Review discipline for chatbox v0 changes
Supersedes: Pre-chatbox path-level governance guidance
Superseded-By: —
Last-Verified: 2026-07-14

## 1. 权威与模块范围

当前工程主线是 [`../chatbox/phase-plan-v0.md`](../chatbox/phase-plan-v0.md) 冻结的 `app/chatbox/`。允许依赖仅限 `src/core/`、经评估后采用的 `src/relationship/` 部分、标准持久化设施和 `config/`。

`agentlib/`、`agent_kernel/`、`src/semantic_trigger/`、`demos/scenarios/` 及其 security/social/task 三类场景指标属于 legacy/quarantine。它们可以保留在仓库中，但不得作为 chatbox v0 的实现、验收或架构权威。

## 2. 每次 review 必查

1. 是否修改 `tests/`；任何测试删除、断言弱化、fixture 改写或合同规避都必须逐项解释。
2. `app/chatbox/` 是否出现白名单之外的 import，尤其是隔离的 legacy 模块。
3. writer 是否存在任何直接写 state 的路径；writer 只能移动 attractor。
4. 是否修改、绕过或弱化 Phase C 节合同、P1–P4 任务验收或 Owner 亲自运行的门。
5. 是否硬编码维度数量、加入 hard clamp，或用定时主动消息替换涌现式 `P_talk`。
6. 文档是否把 archive、身份源或 visual reference 提升为数值动力学、schema、provider 或验收权威。

## 3. 合同测试

Phase 建议 `tests/contract/` 作为合同测试目录，但当前文档不声称该目录或 CI enforcement 已经存在。最终目录名和冻结流程是 **TODO — Owner decision**。确定之前，review 必须按 Phase 的合同文本人工核对测试位置和覆盖范围。

合同测试一旦由 Owner 明确冻结，其修改需要 Owner 显式批准；普通实现任务不得通过改测试制造通过。

## 4. 高风险权威审查

- Phase 范围、P1–P4 门、合同或验收变化：Owner / Orchestrator / Architect；
- identity、persona、relationship posture、expression tendency、anti-drift：Continuity Steward 与相关设计审查；
- relational field、clamp、decay、baseline 或 public/internal boundary：相关 architecture specialist / Field Auditor；
- Kilo mode、provider mapping、indexing、cache、env 或 git hygiene：Config Steward。

## 5. 证据要求

PR 或变更报告必须列出改动文件、目的、测试/静态验证命令、结果、未验证项和已接受风险。CI、branch protection、CODEOWNERS 或 path-enforcement 只有在仓库配置被单独核验后才能宣称存在；本文件本身不构成自动 enforcement。
