# 行为保持型代码结构优化 / 重构风格规范

> Archived pre-chatbox engineering convention; non-authoritative for chatbox v0.

> 分类：`archived-pre-chatbox-convention`（历史工程约束）  
> 权威层级：本文件属于工程规范，低于 [`docs/design/README.md`](../../../design/README.md) 及其指向的设计源文件。任何重构不得与设计宪章冲突，涉及 persona、relationship、memory salience、body influence 或相关路由的改动必须先对齐设计层级。

## 1. 适用范围

本规范约束仓库内的**行为保持型结构优化**工作：在不改变 public API、CLI、配置/schema/golden、runtime trace、关系场语义、clamp/decay/baseline、公私边界、语言条件、身体映射等外部可观测行为的前提下，改善代码可读性、局部复用与维护成本。

## 2. 非目标（不得改变）

以下项目**不在**行为保持型重构范围内，除非得到 Architect、Relational Field Architect、Language Condition Architect、Embodiment Interface Specialist 或用户明确授权：

- public API、CLI 命令行入口与返回格式；
- 配置 schema、YAML/JSON 示例、golden cases；
- runtime trace 输出结构与语义；
- `RelationalFieldState` 的轴数、轴名、轴序、轴语义；
- clamp 边界、decay 行为、baseline 含义；
- public/internal state 边界；
- `LanguageConditionVector`、语言条件映射、表达上限；
- embodiment surface / body mapping；
- Aphrodite 的核心身份边界（非通用助手、聊天机器人、NPC、情绪引擎、生产力工具或普通 agent demo）。

## 3. 触发条件

仅当下列条件同时满足时，才可进行行为保持型重构：

1. 目标模块的当前行为已被测试、golden case、校准报告或 smoke report 覆盖；
2. 重构范围可隔离，不会跨未隔离的子系统（如同时修改 runtime engine 与 autonomy demo 共享数据结构）；
3. 存在可复用的最小验证集（至少运行相关单元测试并检查 diff）；
4. 变更目标在任务单中明确列出，不包含“顺手修复”的功能 bug 或语义调整。

## 4. 允许的操作

- 提取局部 helper 函数并复用已有行为；
- 合并邻近的重复分支或条件判断；
- 重命名局部变量/私有函数以提升可读性；
- 调整 import 顺序、拆分过长函数（不改变调用语义）；
- 在不改变输出结构的前提下，优化日志/错误信息的字符串拼接方式。

## 5. 禁止的操作

- 改变函数返回值、异常类型或默认参数；
- 修改模块级常量、配置键、环境变量读取逻辑；
- 调整数据类的字段、schema 或序列化格式；
- 修复功能 bug（应走 Debug Investigator / Implementation Worker 链路）；
- 引入新的抽象层、新的目录或新的命名空间；
- 删除看似“未使用”的函数、类或文件，除非已通过静态检查和测试确认无引用。

## 6. 验证要求

每次提交前必须：

1. 运行变更文件相关的最小测试集（单元测试优先）；
2. 若存在 golden cases / smoke reports，确认其输出无变化；
3. 执行 `git diff` 并解释每一处 diff 与重构目标的直接关系；
4. 对无法自动化验证的边界行为，提供人工检查说明。

## 7. 与现有工作流的衔接

- 行为保持型重构前，建议由 **Codebase Explorer** 确认目标范围与外部接口边界；
- 范围明确后，由 **Architect** 或相关 specialist 确认是否 truly behavior-preserving；
- 实现由 **Implementation Worker** 或 **Field Auditor** 在批准范围内执行；
- 完成后由 **Test Engineer** 验证、**Code Reviewer** 审查、**Code Skeptic** 挑战完成证据，最后由 **Integration Gatekeeper** 接受。

## 8. 文档自身边界

本文件仅记录工程约束，不定义 Aphrodite 的架构、人格、关系场或身体映射。若本规范与设计宪章或 runtime evidence 冲突，以 [`docs/design/README.md`](../../../design/README.md) 及其指向的权威设计源为准。
