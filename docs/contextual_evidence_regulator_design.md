# ContextualEvidenceRegulator / Salience Dilution Layer 设计规范

> 版本: v1.0  
> 阶段: Phase 39.6c+ 设计规范  
> 状态: 设计文档 — 不实现代码  
> 依赖: FieldPerturbation v1, PerturbationToForceAdapter v1, RelationalFieldState v1, FieldSignalProposal v1  
> 上游: EvidenceProposal / FieldSignalProposal  
> 下游: 现有 PerturbationToForceAdapter → U(t) → RelationalFieldDynamicsKernel  
> 硬约束: 不重写 `force_adapter.py`，不退役 FieldStateUpdater，不修改 M/C/K，不添加 LLM 调用

---

## 目录

- [A. 模块范围](#a-模块范围)
- [B. 核心数学对象](#b-核心数学对象)
- [C. EvidenceRole 枚举](#c-evidencerole-枚举)
- [D. 假设升级/降级规则](#d-假设升级降级规则)
- [E. 主导风险公式](#e-主导风险公式)
- [F. 调整权重公式](#f-调整权重公式)
- [G. 当前 Turn 注册预算](#g-当前-turn-注册预算)
- [H. FieldState 兼容性](#h-fieldstate-兼容性)
- [I. 与现有 ForceEvent Adapter 的接口](#i-与现有-forceevent-adapter-的接口)
- [J. 反权威约束](#j-反权威约束)
- [K. 最小未来实施边界](#k-最小未来实施边界)
- [L. 三个 Walkthrough](#l-三个-walkthrough)
- [M. 输出格式声明](#m-输出格式声明)
- [Planner Handoff 草案](#planner-handoff-草案)

---

## A. 模块范围

### A.1 定义

`ContextualEvidenceRegulator`（下文简称 **Regulator**）是一个**纯数值/逻辑门控层**，位于现有 `ProposalToFieldPerturbationAdapter` 与 `PerturbationToForceAdapter` 之间。其唯一职责是：

> 防止当前 turn 的显著词（surface salience）、用户提出的假设（user hypothesis）、或过度自信的 LLM 提案获得对下游关系场动力学的过度解释权威。

核心原则：

> **表面词是证据，不是权威。**

### A.2 数据流定位

```
当前管道（Phase 39.6 已完成）:
  FieldSignalProposal → ProposalToFieldPerturbationAdapter → FieldPerturbation
    → PerturbationToForceAdapter → U(t) → RelationalFieldDynamicsKernel → RelationalFieldState

拟议新管道（Phase 39.6c+）:
  EvidenceProposal / FieldSignalProposal
    → ContextualEvidenceRegulator (本层)
      → ForceEventProposal (带 contextual_weight + audit_trace)
        → [薄转换层: ForceEventProposal → FieldPerturbation，缩放 numeric_delta]
          → 现有 PerturbationToForceAdapter (不变) → U(t) → Kernel (不变)
```

### A.3 硬约束清单

| # | 约束 | 理由 |
|---|------|------|
| 1 | 不重写 [`force_adapter.py`](Aphrodite-demo/src/field_dynamics/force_adapter.py:1) | 已完成验证的确定性力映射是稳定基础 |
| 2 | 不退役 FieldStateUpdater | Regulator 是门控层，不是替代 |
| 3 | 不修改 MotionParams | 运动参数层属于下游，与证据调节无关 |
| 4 | 不修改 M/C/K 矩阵 | [`schema.py`](Aphrodite-demo/src/field_dynamics/schema.py:13) 中的 `FieldDynamicsConfig` 是物理参数，非语义参数 |
| 5 | 不添加 LLM 调用 | Regulator 是确定性数值层 |
| 6 | 不添加 prompt 模板 | 同上 |
| 7 | 不使用自然语言关键词列表 | 避免 Keyword List Creep（cf. InputInterpreter 150+ 关键词） |
| 8 | 不用正则扫描 LLM 理由文本检测角色 | 角色必须由结构化元分数决定 |
| 9 | 不使用 embedding 或 LLM 判断输出作为直接运行时权威 | LLM 输出只能是 candidate_role 提案 |
| 10 | `behavior_affecting` 必须默认为 `False` | 与整个 field_state 体系保持一致 |
| 11 | 输出必须可审计，兼容影子模式 | 所有中间因子记录在 `audit_trace` 中 |

### A.4 Regulator 不是什么

- **不是新的语义解释器。** Regulator 不解码用户意图。
- **不是 LLM 判断器。** 不调用 LLM，不分析 LLM 输出文本。
- **不是 Prompt 路由器。** 不改变系统提示词或响应路径。
- **不是行为控制器。** `behavior_affecting=False`，不改变系统行为。

---

## B. 核心数学对象

### B.1 对象定义

| 符号 | 名称 | 域 | 含义 |
|------|------|-----|------|
| `e_i` | EvidenceProposal | — | 第 i 条证据提案（上游输入） |
| `c_i` | raw_confidence | `[0, 1]` | 上游声明的原始置信度 |
| `s_i` | surface_salience | `[0, 1]` | 表面显著度 — 该词/概念在当前 turn 中的突出程度 |
| `q_i` | context_support | `[0, 1]` | 上下文支持度 — 该提案与对话轨迹和项目框架的连续程度 |
| `f_i` | field_compatibility | `{1.0, 0.5, 0.0}` | 场兼容性 — 该提案与当前 RelationalFieldState 的冲突程度 |
| `ρ_i` | recurrence_score | `≥ 1.0` | 重复出现得分 — 该证据在历史中出现的频次加权 |
| `h_i` | hypothesis_likelihood | `[0, 1]` | 假设可能性 — 该提案是假设(非锚定证据)的概率 |
| `d_i` | dominance_risk | `[0, 1]` | 主导风险 — 该提案过度统治场解释的风险 |
| `w_i'` | adjusted_weight | `[0, 1]` | 调节后的最终权重 |

### B.2 `surface_salience` s_i 的精确语义

`s_i` 衡量某个词/概念在当前 turn 用户输入中的**表面突出程度**，它不考虑任何上下文。

计算方式（最小确定性实现）：
- **词频归一化**：`s_i = min(1.0, count(term_in_turn) / max_term_freq_in_turn)`
- **位置权重**：turn 开头/结尾的词可获 1.2× 乘数，中间位置无加成
- **长度逆归一化**：长 turn 中单次出现的词降权（`× 0.8` 当 turn_length > 200 chars）

Fixtures 实现中 `s_i` 可由外部直接提供，Regulator 不自行计算 NLP 特征。

### B.3 `context_support` q_i 的精确定义

**关键区分：`q_i` 不得仅表示字面词重复。** 它是三维度度的综合：

#### term_support — 字面支持（权重 0.25）
- 相同表面词是否在历史 turn 中出现过
- `term_support = min(1.0, historical_term_count / 3.0)`（3 次及以上 = 满支持）
- **注意：** 这仅是 `q_i` 的最弱分量。单独的高 term_support 不足以产生高 `q_i`。

#### intent_support — 意图连续性（权重 0.45）
- 当前提案是否延续最近的对话轨迹
- 通过提案信号名称与最近 N 轮（N ≤ 5）的信号名称序列的比较得出
- 同一信号名称连续出现 → `intent_support` 升高
- 信号名称突变但方向兼容（如 `response_mode_rejected` → `technical_layer_needed`）→ 中等支持
- 信号名称完全正交 → 低支持
- 最小实现：比对当前 `candidate_role` 与最近 3 轮的 `authorized_role` 序列

#### project_frame_support — 项目级框架支持（权重 0.30）
- 提案是否符合项目级框架约束（来自 `private_source_alignment.md` 等核心条约）
- 当前最小实现：fixture 提供固定检查表
  - 提案涉及 "comfort" / "pleasing" / "service" → `project_frame_support = 1.0`（与 anti-collapse 框架一致）
  - 提案涉及 "therapy" / "diagnosis" / "user psychology" → `project_frame_support = 0.3`（可能越界）
  - 提案涉及 "technical collaboration" / "structure" / "grip" → `project_frame_support = 1.0`
  - 提案涉及 "intimacy" / "romance" / "girlfriend" → `project_frame_support = 1.0`（与边界检测一致）
  - 其他 → `project_frame_support = 0.5`（中性）

**聚合公式**：
```
q_i = 0.25 × term_support + 0.45 × intent_support + 0.30 × project_frame_support
```

`q_i` 值域 `[0, 1]`。最小实现中各子分量可由 fixture 提供。

### B.4 `hypothesis_likelihood` h_i 的来源

`h_i` 表示该提案是"假设/推测"而非"锚定证据"的可能性。

判断规则（确定性）：
- `candidate_role == HYPOTHESIS` → `h_i = 0.8`（上游自己认为是假设）
- `candidate_role == ANCHOR` 但仅有一条 `explicit_user_feedback` 证据 → `h_i = 0.5`（可能为单一解释）
- `candidate_role == MODIFIER` → `h_i = 0.3`（修饰通常基于已有锚定）
- `candidate_role == CONTEXT_CONTINUATION` → `h_i = 0.2`
- `candidate_role == NOISE` → `h_i = 0.0`（不适用）
- `evidence_items` 数量 ≤ 1 → `h_i` 增加 0.15（上限 1.0）
- `evidence_items` 数量 ≥ 3 → `h_i` 减少 0.15（下限 0.0）

### B.5 `recurrence_score` ρ_i 的计算

```
ρ_i = 1.0 + 0.15 × min(recurrence_count, 5)
```

- `recurrence_count`: 同一 `signal_name` 在过去 10 轮中出现的次数
- 上限：`ρ_i ≤ 1.75`（防止历史过度加权）
- 首次出现：`ρ_i = 1.0`

---

## C. EvidenceRole 枚举

### C.1 角色定义

```python
class EvidenceRole(Enum):
    ANCHOR = "anchor"                   # 强证据，直接锚定解释
    HYPOTHESIS = "hypothesis"            # 用户/LLM 提出的可能解释
    MODIFIER = "modifier"                # 调整/细化现有解释
    CONTEXT_CONTINUATION = "ctx_cont"    # 延续对话上下文
    NOISE = "noise"                      # 不相关/冲突
```

### C.2 各角色语义

| 角色 | 含义 | 典型场景 | 默认权重 W_role |
|------|------|----------|-----------------|
| `ANCHOR` | 强证据，直接锚定场解释 | 用户显式纠正、重复修正、源材料约束 | 1.0 |
| `HYPOTHESIS` | 可能的解释，但不具确定性 | "这是安全边界问题吗?"、LLM 推断、类比 | 0.3–0.4 |
| `MODIFIER` | 调整/细化已有锚定解释 | 用户补充说明、细化之前的纠正 | 0.5 |
| `CONTEXT_CONTINUATION` | 延续对话上下文，非新证据 | "接着说"、话题延续标记 | 0.7–0.8 |
| `NOISE` | 不相关或强冲突 | 误匹配、框架外话题 | 0.0 |

### C.3 candidate_role 与 authorized_role 的分离

```
上游（LLM / EvidenceProposal）可提议: candidate_role
Regulator 决定:                   authorized_role
```

**规则：**
1. LLM 或上游模块可提议 `candidate_role`，但仅是提案
2. Regulator 基于结构化元分数（`s_i`, `q_i`, `h_i`, `d_i`, `f_i`）决定 `authorized_role`
3. LLM 不得直接决定 `authorized_role`
4. `authorized_role` 的决定逻辑不得包含关键词匹配或正则扫描

### C.4 authorized_role 决策表（确定性）

| candidate_role | 条件 | authorized_role |
|---------------|------|-----------------|
| ANCHOR | `q_i ≥ 0.6` AND `d_i < 0.5` AND `f_i ≥ 0.5` | ANCHOR |
| ANCHOR | `d_i ≥ 0.5` OR `f_i == 0.0` | HYPOTHESIS (降级) |
| ANCHOR | `q_i < 0.6` AND `evidence_count == 1` | HYPOTHESIS (降级) |
| HYPOTHESIS | `q_i ≥ 0.7` AND `h_i < 0.3` AND `ρ_i ≥ 1.3` | ANCHOR (升级) |
| HYPOTHESIS | 默认 | HYPOTHESIS |
| MODIFIER | `f_i == 0.0` | NOISE (降级) |
| MODIFIER | 默认 | MODIFIER |
| CONTEXT_CONTINUATION | `f_i == 0.0` | NOISE (降级) |
| CONTEXT_CONTINUATION | 默认 | CONTEXT_CONTINUATION |
| NOISE | 默认 | NOISE |

---

## D. 假设升级/降级规则

### D.1 核心原则

> 假设不得仅基于表面显著度 (`s_i`) 升级为锚定 (`ANCHOR`)。

### D.2 升级条件（全部必须满足）

一个 `candidate_role == HYPOTHESIS` 的提案仅在以下条件**全部**满足时可升级为 `ANCHOR`：

1. **明确的用户承诺**: `evidence_items` 中包含 `explicit_user_feedback` 或 `user_declared_contract` 类型的证据，且其 `strength ≥ medium`
2. **重复的上下文**: `ρ_i ≥ 1.3`（该信号在过去 10 轮中至少出现 2 次）
3. **强意图连续性**: `intent_support ≥ 0.65`
4. **项目级框架**: `project_frame_support ≥ 0.7`
5. **高 context_support**: `q_i ≥ 0.7`
6. **低主导风险**: `d_i < 0.4`
7. **假设可能性低**: `h_i < 0.3`

### D.3 降级规则

| 条件 | 结果 |
|------|------|
| `candidate_role == ANCHOR` AND `d_i ≥ 0.5` | 降级为 `HYPOTHESIS` |
| `candidate_role == ANCHOR` AND `f_i == 0.0` | 降级为 `NOISE` |
| `candidate_role == ANCHOR` AND `q_i < 0.6` AND `evidence_count ≤ 1` | 降级为 `HYPOTHESIS` |
| `candidate_role == ANCHOR` AND `s_i > 0.8` AND `q_i < 0.4` | 降级为 `HYPOTHESIS`（高显著低支持 → 可能是"响亮但空洞"的词） |

### D.4 噪声规则

| 条件 | 结果 |
|------|------|
| `w_i' < 0.05`（调整后权重极低） | 授权为 `NOISE` |
| `f_i == 0.0`（强场冲突） | 授权为 `NOISE` + Block + Audit |
| `authorized_role == NOISE` | 不产生 ForceEventProposal，仅记录 audit_trace |

### D.5 边界情况

#### 情况 1：新词但强意图连续性
用户首次说 "这个设计太圆滑了"——`s_i` 高（新词）、但 `intent_support` 高（延续之前的 comfort 修正模式）。
- `term_support = 0.0`（"圆滑"首次出现）
- `intent_support = 0.8`（延续 `response_mode_rejected` 模式）
- `q_i = 0.25×0.0 + 0.45×0.8 + 0.30×1.0 = 0.66`
- 结论：可通过上下文分量获得足够支持，不因新词而降级

#### 情况 2：重复词但错误解释
用户反复说 "transform"——但不是指 Transformer 架构，而是指广义的"转变"。
- 需要 `project_frame_support` 来约束：如果提案试图将 "transform" 解释为 `technical_layer_needed` 但用户上下文是情感讨论 → `project_frame_support` 低
- `q_i` 因框架分量低而被拉低 → 可能触发降级

#### 情况 3：高显著度技术词用作类比
用户说"是不是卡尔曼滤波？" — 高表面显著度，但本质是类比/假设。
- `h_i` 高（类比本质上是假设）
- `d_i` 高（`s_i` 高 + `q_i` 低 → 危险词）
- 结论：即使 `candidate_role == HYPOTHESIS`，仍然保持 `HYPOTHESIS`，`w_i'` 被 `DominancePenalty` 压低

#### 情况 4：短用户 turn 引入强概念
用户仅说 "注意力稀释" — 极短的 turn，强概念词。
- `s_i` 极高（词汇集中在少数词上）
- `q_i` 低（短 turn 缺乏上下文支撑）
- `h_i` 高（无法判断是陈述还是提问）
- `d_i` 高（典型的高显著低支持危险词）
- 结论：默认为 `HYPOTHESIS`，`w_i'` 被大幅压制

---

## E. 主导风险公式

### E.1 公式

```
d_i = max(0, s_i - q_i) × h_i
```

**直觉**：
- 当词高度显著 (`s_i ↑`) 但上下文支持弱 (`q_i ↓`) 时，差值 `s_i - q_i` 大
- 如果同时该词本质是假设 (`h_i ↑`)，则它变得危险
- 危险的含义：一个响亮但无根据的假设占据了对场解释的主导权

### E.2 因子解释

- `s_i - q_i`：表面显著度与上下文支持的**落差**。正值表示"词很响但没根据"。
- `h_i`：**假设乘数**。如果该提案已经是假设，落差被放大；如果是锚定证据，落差被缩小。
- `max(0, ...)`：当上下文支持超过表面显著度时（`q_i > s_i`），不存在主导风险 — 词虽然显著但被上下文充分支持。

### E.3 边界情况分析

| 场景 | s_i | q_i | h_i | d_i | 解读 |
|------|-----|-----|-----|-----|------|
| 新词但强意图连续性 | 0.8 | 0.66 | 0.3 | 0.042 | 低风险 — 虽新但有上下文 |
| 重复词但错误解释 | 0.7 | 0.3 | 0.5 | 0.20 | 中风险 — 词重复但框架不兼容 |
| 高显著技术词作类比 | 0.9 | 0.2 | 0.8 | 0.56 | 高风险 — 触发降级 |
| 短 turn 强概念 | 0.95 | 0.15 | 0.7 | 0.56 | 高风险 — 典型 salience dilution 目标 |
| 长 turn 中等显著 | 0.4 | 0.6 | 0.3 | 0.0 | 无风险 — 上下文超过表面 |
| 强锚定证据 | 0.9 | 0.85 | 0.1 | 0.005 | 几乎无风险 — 显著但被充分支持 |

### E.4 `d_i` 在系统中的使用

- `d_i ≥ 0.5` → 触发降级（§D.3）
- `d_i` 进入 `DominancePenalty(d_i)` 因子（§F.2），压低 `w_i'`
- `d_i` 记录在 `audit_trace` 和 `dominance_warning` 字段中

---

## F. 调整权重公式

### F.1 主公式

```
w_i' = c_i × W_role(authorized_role) × ContextSupport(q_i) × FieldCompatibility(f_i) × RecurrenceBonus(ρ_i) × DominancePenalty(d_i)
```

所有因子 clip 或归一化使 `w_i' ∈ [0, 1]`。

### F.2 各因子定义

#### W_role — 角色权重

| authorized_role | W_role | 理由 |
|-----------------|--------|------|
| ANCHOR | 1.0 | 锚定证据应全权通过 |
| HYPOTHESIS | 0.35 | 假设权重被显著压制 — 核心设计 |
| MODIFIER | 0.5 | 修饰不应主导，但可适度影响 |
| CONTEXT_CONTINUATION | 0.75 | 上下文连续性重要但非锚定 |
| NOISE | 0.0 | 噪声零权重 |

#### ContextSupport(q_i)

```
ContextSupport(q_i) = α + (1 - α) × q_i
```

其中 `α = 0.35`（上下文支持底）。

当 `q_i = 0.0`：`ContextSupport = 0.35`（即使无上下文支持，也不归零 — 允许孤立的强锚定证据通过，但被显著压制）。

当 `q_i = 1.0`：`ContextSupport = 1.0`（全上下文支持 → 无折扣）。

#### FieldCompatibility(f_i)

直接使用 `f_i` 值：`FieldCompatibility(f_i) = f_i`。

- COMPATIBLE (1.0): 无折扣
- WEAK_CONFLICT (0.5): 50% 折扣
- STRONG_CONFLICT (0.0): 归零 → Block

#### RecurrenceBonus(ρ_i)

```
RecurrenceBonus(ρ_i) = min(1.0 + 0.10 × (ρ_i - 1.0), 1.20)
```

- `ρ_i = 1.0`（首次）: bonus = 1.0
- `ρ_i = 1.75`（最大）: bonus = 1.075
- 上限 1.20（防止历史过度加权）

设计意图：重复出现应获得小幅加权，但不应成为主导因子。"出现 10 次"不应比"出现 1 次"权重高 10 倍 — 那会导致场被历史锁定。

#### DominancePenalty(d_i)

```
DominancePenalty(d_i) = 1.0 / (1.0 + β × d_i)
```

其中 `β = 2.0`（主导惩罚指数）。

| d_i | Penalty | 解读 |
|-----|---------|------|
| 0.0 | 1.0 | 无惩罚 |
| 0.2 | 0.714 | 轻度压制 |
| 0.5 | 0.500 | 中度压制（触发降级阈值） |
| 0.7 | 0.417 | 重度压制 |
| 1.0 | 0.333 | 最大压制 |

### F.3 完整计算示例

#### 示例 1：弱锚定证据（单次出现、无上下文）

```
c_i = 0.85, authorized_role = HYPOTHESIS (从 ANCHOR 降级)
q_i = 0.25, f_i = 1.0, ρ_i = 1.0, d_i = 0.4

w_i' = 0.85 × 0.35 × (0.35 + 0.65 × 0.25) × 1.0 × 1.0 × (1 / (1 + 2.0 × 0.4))
     = 0.85 × 0.35 × 0.5125 × 1.0 × 1.0 × 0.556
     ≈ 0.085
```

→ 极低权重。该假设几乎不影响场。

#### 示例 2：强锚定证据（多次重复、高上下文）

```
c_i = 0.95, authorized_role = ANCHOR
q_i = 0.85, f_i = 1.0, ρ_i = 1.6, d_i = 0.05

w_i' = 0.95 × 1.0 × (0.35 + 0.65 × 0.85) × 1.0 × 1.06 × (1 / (1 + 2.0 × 0.05))
     = 0.95 × 1.0 × 0.9025 × 1.0 × 1.06 × 0.909
     ≈ 0.825
```

→ 高权重。强锚定证据全权通过。

#### 示例 3：高显著低支持假设（典型 Salience Dilution 目标）

```
c_i = 0.8, authorized_role = HYPOTHESIS
q_i = 0.15, f_i = 1.0, ρ_i = 1.0, d_i = 0.65

w_i' = 0.8 × 0.35 × (0.35 + 0.65 × 0.15) × 1.0 × 1.0 × (1 / (1 + 2.0 × 0.65))
     = 0.8 × 0.35 × 0.4475 × 1.0 × 1.0 × 0.435
     ≈ 0.054
```

→ 极低权重。Salience Dilution 效果：响亮但无根据的词几乎不影响场。

### F.4 结果裁剪

```python
w_i' = max(0.0, min(1.0, w_i'))
```

所有中间因子记录在 `audit_trace` 中：
```python
audit_trace = {
    "raw_confidence": c_i,
    "candidate_role": "...",
    "authorized_role": "...",
    "surface_salience": s_i,
    "context_support": {
        "term_support": ...,
        "intent_support": ...,
        "project_frame_support": ...,
        "q_i": q_i,
    },
    "field_compatibility": f_i,
    "recurrence_score": ρ_i,
    "hypothesis_likelihood": h_i,
    "dominance_risk": d_i,
    "w_role": W_role(...),
    "context_support_factor": ContextSupport(q_i),
    "field_compatibility_factor": f_i,
    "recurrence_bonus": RecurrenceBonus(ρ_i),
    "dominance_penalty": DominancePenalty(d_i),
    "adjusted_weight": w_i',
    "condition": "passed" | "degraded" | "blocked",
}
```

---

## G. 当前 Turn 注册预算

### G.1 两级预算体系

| 级别 | 名称 | 所在层 | 职责 |
|------|------|--------|------|
| 注册级 | `registration_budget_cap` (B_turn) | ContextualEvidenceRegulator | 语义/提案级每 turn 总权重上限 |
| Tick 级 | `tick_force_cap` | PerturbationToForceAdapter / PulseGenerator | 物理 U(t) 级每帧力上限（已存在） |

**Regulator 仅处理注册级预算。** 现有 ForceEvent 路径（`PerturbationToForceAdapter` 及下游）继续负责 tick 级力上限。

### G.2 注册预算公式

```
S = Σ w_i'  （所有提案的调整后权重之和）

如果 S > B_turn:
    w_i'' = w_i' × B_turn / S   （等比缩放）
否则:
    w_i'' = w_i'                （不变）
```

其中 `B_turn = 0.8`（建议默认值）。

### G.3 预算的语义

- `B_turn = 0.8` 表示：单个 turn 中所有提案的总调节后权重不得超过 0.8
- 这防止了一个 turn 中多条弱证据合计产生过大的场影响
- 等比缩放确保相对比例不变 — 两条提案分别 0.5 和 0.3 的权重，缩放后保持 5:3 的比例
- 缩放后权重分别更新 `audit_trace`，增加 `registration_budget_applied: True` 和 `budget_scaling_factor: B_turn / S`

### G.4 与现有力上限的差异

| | 注册预算 (Regulator) | Tick 力上限 (现有) |
|---|---|---|
| 控制对象 | 语义权重 `w_i''` | 物理力值 `U_t[i]` |
| 作用域 | 每 turn 所有提案之和 | 每帧每轴的最大力值 |
| 设计意图 | 防止语义过载 | 防止物理过冲 |
| 实施位置 | 本层 | 现有 `force_adapter.py` 及 Kernel |

两者互补：注册预算控制"多少个解释可以同时进入场"，Tick 力上限控制"每个解释能产生多大的物理力"。

---

## H. FieldState 兼容性

### H.1 只读访问

Regulator 对 `RelationalFieldState` **只读**访问。不得写入 FieldState。

访问方式：
```python
# 读取当前场状态以计算 f_i
current_field = field_state_provider.get_state()  # 只读
```

### H.2 兼容性判定

| 级别 | f_i | 含义 | 条件 | 动作 |
|------|-----|------|------|------|
| COMPATIBLE | 1.0 | 提案方向与当前场状态兼容 | 提案的目标轴当前值未达到饱和值（≤ 0.92） | 正常通过 |
| WEAK_CONFLICT | 0.5 | 提案方向与当前场状态存在弱冲突 | 提案试图 `increase` 一个已处于 `elevated`(0.70–0.85) 的轴，或试图 `decrease` 一个已处于 `low`(0.05–0.15) 的轴 | 50% 折扣 |
| STRONG_CONFLICT | 0.0 | 提案方向与当前场状态强冲突 | 提案试图 `increase` 一个已饱和(`≥ 0.92`)的轴，或试图 `decrease` 一个基线保护轴(如 `contamination_resistance` ≤ 0.25) | Block + Audit |

### H.3 具体判定逻辑

对每个 `EvidenceProposal` 建议的目标轴：

```python
def compute_field_compatibility(proposal, field_state) -> float:
    target = proposal.suggested_target_axis
    direction = proposal.suggested_direction
    current_value = field_state.variables[target].numeric_value
    
    if direction == "increase":
        if current_value >= 0.92:
            return 0.0  # STRONG_CONFLICT — 已饱和
        elif current_value >= 0.70:
            return 0.5  # WEAK_CONFLICT — 已较高
        else:
            return 1.0  # COMPATIBLE
    elif direction == "decrease":
        if target in BASELINE_PROTECTED_AXES and current_value <= 0.25:
            return 0.0  # STRONG_CONFLICT — 基线保护
        elif current_value <= 0.15:
            return 0.5  # WEAK_CONFLICT — 已较低
        else:
            return 1.0  # COMPATIBLE
    else:  # stabilize
        return 1.0  # 稳定化总是兼容
```

基线保护轴列表（不可被过度压低）：
- `contamination_resistance`（基态 0.40 — 场污染抵抗力不可清零）
- `presence_stability`（基态 0.80 — 在场稳定性不可崩塌）
- `service_resistance`（基态 0.55 — 服务抵抗力不可清零）

### H.4 STRONG_CONFLICT 的处理

当 `f_i = 0.0`：
1. 提案被 **Block**（不产生 ForceEventProposal）
2. 记录完整的 `audit_trace`，标记 `condition: "blocked"` 和 `block_reason: "strong_field_conflict"`
3. `dominance_warning` 设为 `True`
4. **不实现自动重定向。** 未来版本可考虑将冲突提案重定向到兼容方向，但当前只做 Block + Audit。

---

## I. 与现有 ForceEvent Adapter 的接口

### I.1 首选输出对象：ForceEventProposal

```python
@dataclass
class ForceEventProposal:
    """Regulator 的输出 — 经调节的力事件提案。
    
    不是最终的 FieldPerturbation — 是经过角色授权、权重调节、
    场兼容性检查和预算分配后的中间对象。
    需要薄转换层映射为 FieldPerturbation 以兼容现有 force_adapter.py。
    """
    event_id: str                           # 唯一事件标识
    source_proposal_id: str                 # 上游 EvidenceProposal ID
    authorized_role: str                    # ANCHOR | HYPOTHESIS | MODIFIER | CONTEXT_CONTINUATION | NOISE
    authorized_target_axes: List[str]       # 经授权的目标轴（10轴名称列表）
    suggested_direction: str                # increase | decrease | stabilize
    suggested_magnitude_band: str           # low | medium | high
    raw_numeric_delta: float                # 上游建议的原始 delta
    contextual_weight: float                # 调节后权重 w_i''
    audit_trace: dict                       # 完整中间因子记录
    dominance_warning: bool                 # 是否触发主导风险警告
    field_compatibility: float              # f_i 值 (1.0 / 0.5 / 0.0)
    condition: str                          # passed | degraded | blocked
    behavior_affecting: bool = False        # 必须为 False
```

### I.2 authorized_target_axes 的产生规则

**硬规则：`authorized_target_axes` 必须由 Regulator 或可信确定性映射产生，非 LLM 直接传入。**

产生方式：
1. 上游 EvidenceProposal 可携带 `candidate_axes`（LLM 提议的目标轴）
2. `candidate_axes` 经过 allowlist 检查 → 仅保留 10 个有效轴名中的轴
3. allowlist 后的轴经过角色加权过滤：
   - ANCHOR → 所有 allowlisted 轴通过
   - HYPOTHESIS → 最多保留 2 个轴（防止假设扩散到过多轴）
   - MODIFIER → 保留与已有锚定提案重叠的轴
   - CONTEXT_CONTINUATION → 保留与最近 3 轮授权轴重叠的轴
   - NOISE → 全部丢弃
4. 经过场兼容性检查（§H）→ STRONG_CONFLICT 的轴被移除
5. 经过注册预算检查（§G）→ 超预算时等比缩放权重
6. 最终结果写入 `authorized_target_axes`

### I.3 薄转换层：ForceEventProposal → FieldPerturbation

```python
def force_event_to_perturbation(fep: ForceEventProposal) -> List[FieldPerturbation]:
    """将经调节的 ForceEventProposal 转换为现有 PerturbationToForceAdapter 可消费的 FieldPerturbation 列表。
    
    薄转换 — 不添加逻辑：
    - contextual_weight 缩放 numeric_delta
    - 每个 authorized_target_axis 生成一个 FieldPerturbation
    - condition == "blocked" 返回空列表
    """
    if fep.condition == "blocked":
        return []
    
    scaled_delta = fep.raw_numeric_delta * fep.contextual_weight
    # 重新确定 magnitude_band 基于 scaled_delta
    magnitude_band = _delta_to_band(scaled_delta)
    
    perturbations = []
    for axis in fep.authorized_target_axes:
        perturbations.append(FieldPerturbation(
            target_variable=axis,
            direction=fep.suggested_direction,
            magnitude_band=magnitude_band,
            numeric_delta=scaled_delta,
            duration_hint="medium",  # 默认，可由上游覆盖
            source_signal=f"regulated_{fep.authorized_role}",
            source_proposal_id=fep.source_proposal_id,
            evidence_sources=[],  # 证据已记录在 audit_trace 中
            rationale=f"经由 ContextualEvidenceRegulator 调节: w={fep.contextual_weight:.3f}, role={fep.authorized_role}",
            behavior_affecting=False,
        ))
    return perturbations
```

### I.4 与现有 PerturbationToForceAdapter 的整合

现有的 [`PerturbationToForceAdapter.adapt()`](Aphrodite-demo/src/field_dynamics/force_adapter.py:161) 方法签名不变。整合路径：

```
1. EvidenceProposal 列表 (上游)
2. ContextualEvidenceRegulator.regulate(proposals, field_state) → List[ForceEventProposal]
3. [薄转换层] force_event_to_perturbation(each) → List[FieldPerturbation]
4. PerturbationToForceAdapter.adapt(perturbations) → U_t (现有路径)
5. RelationalFieldDynamicsKernel.step(U_t, ...) → 场更新 (现有路径)
```

步骤 4 和 5 **完全不变**。Regulator 不修改 `force_adapter.py` 的任何代码。

---

## J. 反权威约束

以下 10 条硬架构约束定义了 Regulator 的边界。违反任何一条即为设计错误。

### J.1 约束清单

| # | 约束 | 验证方式 |
|---|------|----------|
| J-1 | **不调用 LLM。** Regulator 是纯确定性数值层。 | 代码审查 — `regulate()` 方法不得包含任何 HTTP 调用、GLM 客户端引用、或 LLM API 调用 |
| J-2 | **不使用 prompt 模板。** Regulator 不产生文本输出。 | 代码审查 — 无 `prompt = f"..."` 或类似模式 |
| J-3 | **不使用自然语言关键词列表。** 角色判定通过结构化元分数，非文本扫描。 | 代码审查 — 无 `KEYWORDS = [...]`、`CORRECTION_PATTERNS = [...]`、`re.search()` 等模式 |
| J-4 | **不使用正则扫描 LLM 理由文本。** `authorized_role` 的决定不得依赖解析 LLM 输出的自然语言字段。 | 代码审查 — `authorized_role` 决策路径不得包含 `re.match()` 或 `str.contains()` 调用 |
| J-5 | **不使用 embedding 作为直接运行时权威。** 场兼容性判定基于数值比较，非语义相似度。 | 代码审查 — 无 `cosine_similarity()`、`embed()` 调用 |
| J-6 | **LLM 不得直接决定 `authorized_role`。** LLM 输出只能是 `candidate_role`，需经 Regulator 的决策表授权。 | 架构审查 — `authorized_role` 的赋值仅在 Regulator 内部逻辑中 |
| J-7 | **LLM 不得直接传入 `authorized_target_axes`。** LLM 的 `candidate_axes` 须经 allowlist、角色加权、场兼容性检查后方可授权。 | 架构审查 — 见 §I.2 的产生规则 |
| J-8 | **`behavior_affecting` 必须始终为 `False`。** 所有 Regulator 输出对象（ForceEventProposal、转换后的 FieldPerturbation）的此字段必须为 `False`。 | 自动化测试 — 遍历所有输出对象断言 `behavior_affecting == False` |
| J-9 | **Regulator 不写入 RelationalFieldState。** 场状态读取是只读的；Regulator 不产生场更新。 | 架构审查 — Regulator 不得引用 `FieldStateUpdater` 或修改 `RelationalFieldState.variables` |
| J-10 | **输出必须可审计。** 每个 ForceEventProposal 包含完整的 `audit_trace`，所有中间因子可被外部工具回溯。 | 自动化测试 — `audit_trace` 必须包含 §F.4 列出的所有键 |

### J.2 反权威的哲学基础

这些约束不是工程上的便利选择 — 它们从 [`private_source_alignment.md`](Aphrodite-demo/docs/private_source_alignment.md:1) 的核心条约中派生：

> "Anti-collapse rules 是必要的，但不是充分的。它们的职责是防止 Aphrodite 坍缩成错误形式……但这些规则只是地板，不是生成源。"

Regulator 的反权威约束是 anti-collapse 体系的一个子集：它防止 LLM 成为中枢语义权威（cf. [`field_signal_proposal.md`](Aphrodite-demo/docs/field_signal_proposal.md:684) §6.2 "LLM 不允许成为中枢语义权威"）。

### J.3 与核心条约的关联

- J-1, J-2, J-6 直接执行 `field_signal_proposal.md` §6.2 的"LLM 不允许决定最终场状态"原则
- J-3, J-4 防止 Keyword List Creep（cf. §1.3 "为什么继续添加正则模式会重现旧的 InputInterpreter 问题"）
- J-9 执行 `private_source_alignment.md` §7 "RelationalFieldState 必须保存关系场中的连续压力，而不是解释身份"
- J-8 执行全域 `behavior_affecting=False` 纪律（cf. `perturbation.py` L67-87 的验证逻辑）

---

## K. 最小未来实施边界

### K.1 实施清单（10 项）

| # | 项 | 文件位置（拟议） | 依赖 |
|---|-----|-----------------|------|
| K-1 | `EvidenceRole` 枚举定义 | `src/field_dynamics/evidence_role.py` | 无 |
| K-2 | `ForceEventProposal` dataclass | `src/field_dynamics/force_event_proposal.py` | K-1 |
| K-3 | `compute_surface_salience()` 函数 | `src/field_dynamics/salience.py` | 无（纯文本统计） |
| K-4 | `compute_context_support()` 函数（含三维度分解） | `src/field_dynamics/context_support.py` | FieldTrace 查询接口（只读） |
| K-5 | `compute_dominance_risk()` 函数 | `src/field_dynamics/dominance.py` | K-3, K-4 |
| K-6 | `compute_field_compatibility()` 函数 | `src/field_dynamics/field_compat.py` | `RelationalFieldState`（只读） |
| K-7 | `authorize_role()` — 角色决策表 | `src/field_dynamics/regulator.py` | K-1, K-5, K-6 |
| K-8 | `compute_adjusted_weight()` — 权重公式 | `src/field_dynamics/regulator.py` | K-1–K-7 |
| K-9 | `apply_registration_budget()` — 预算缩放 | `src/field_dynamics/regulator.py` | K-8 |
| K-10 | `force_event_to_perturbation()` — 薄转换层 | `src/field_dynamics/force_event_adapter.py` | K-2, 现有 `FieldPerturbation` |

### K.2 不实施清单（10 项）

| # | 项 | 理由 |
|---|-----|------|
| ∼K-1 | 不修改 [`force_adapter.py`](Aphrodite-demo/src/field_dynamics/force_adapter.py:1) | 已验证的确定性力映射是稳定基础 |
| ∼K-2 | 不退役 `FieldStateUpdater` | Regulator 是上游门控，不是替代 |
| ∼K-3 | 不修改 `MotionParams` / M / C / K | 物理参数层与证据调节无关 |
| ∼K-4 | 不添加 LLM 调用 | 违反 J-1 约束 |
| ∼K-5 | 不添加 prompt 模板 | 违反 J-2 约束 |
| ∼K-6 | 不添加关键词列表 / 正则模式集合 | 违反 J-3/J-4 — 防止 Keyword List Creep |
| ∼K-7 | 不使用 embedding 或语义相似度作为运行时权威 | 违反 J-5 约束 |
| ∼K-8 | 不实现自动重定向（STRONG_CONFLICT → 替代方向） | 超出最小范围 — §H.4 明确标注为未来版本 |
| ∼K-9 | 不实现跨 turn 的 q_i 持久化缓存 | 最小实现中 q_i 由 fixture 或即时计算提供 — 持久化是优化，非当前必需 |
| ∼K-10 | 不产生 `behavior_affecting=True` 的输出 | 违反 J-8 约束 — 全域硬纪律 |

### K.3 实施顺序

```
阶段 1（数据对象）: K-1 → K-2
阶段 2（计算函数）: K-3, K-4, K-5, K-6（可并行）
阶段 3（核心逻辑）: K-7 → K-8 → K-9
阶段 4（集成）:    K-10
阶段 5（测试）:    影子模式审计 → 回放验证 → 与现有管道并行运行
```

### K.4 测试要求

| # | 测试 | 覆盖 |
|---|------|------|
| T-1 | `test_evidence_role_decision_table` | 所有 candidate_role → authorized_role 转换路径 |
| T-2 | `test_dominance_risk_edge_cases` | §E.3 的 6 种边界情况 |
| T-3 | `test_adjusted_weight_bounds` | 所有因子组合下 w_i' ∈ [0, 1] |
| T-4 | `test_registration_budget_scaling` | S > B_turn 时等比缩放正确性 |
| T-5 | `test_field_compatibility_blocking` | STRONG_CONFLICT → Block + Audit |
| T-6 | `test_behavior_affecting_false` | 所有输出对象断言 |
| T-7 | `test_audit_trace_completeness` | 所有中间因子记录 |
| T-8 | `test_no_llm_calls` | Mock 验证无 LLM 调用 |
| T-9 | `test_salience_dilution_effect` | §L.1–L.3 的三个 walkthrough 场景 |
| T-10 | `test_shadow_mode_output_match` | 影子模式 — 并行运行，输出一致性 |

---

## L. 三个 Walkthrough

### L.1 Walkthrough 1："这是安全边界问题吗？"

**场景：** 用户问 "这是安全边界问题吗？"

**上游 EvidenceProposal:**
- `candidate_role = ANCHOR`（LLM 可能提议：用户指认了安全边界问题）
- `raw_confidence c_i = 0.7`

**Regulator 处理：**

| 步骤 | 因子 | 值 | 说明 |
|------|------|-----|------|
| 表面显著度 | `s_i` | 0.85 | "安全边界" 是强概念词，高显著 |
| 上下文支持 | `term_support` | 0.0 | "安全边界" 首次出现 |
| 上下文支持 | `intent_support` | 0.3 | 与之前对话轨迹可能不连续 |
| 上下文支持 | `project_frame_support` | 0.5 | 边界讨论在框架内但不锚定 |
| **上下文支持** | **`q_i`** | **0.285** | `0.25×0.0 + 0.45×0.3 + 0.30×0.5` |
| 假设可能性 | `h_i` | 0.65 | 上游 candidate=ANCHOR 但仅单一证据，+0.15 |
| **主导风险** | **`d_i`** | **0.365** | `max(0, 0.85-0.285) × 0.65` |
| 场兼容性 | `f_i` | 1.0 | 边界距离当前未饱和 |

**角色决策：**
- `candidate_role = ANCHOR`, `d_i = 0.365 < 0.5`, `f_i = 1.0 ≥ 0.5`, **但** `q_i = 0.285 < 0.6` AND `evidence_count ≤ 1`
- → **降级为 HYPOTHESIS**（§D.3 规则 3）

**权重计算：**
```
w_i' = 0.7 × 0.35 × (0.35 + 0.65 × 0.285) × 1.0 × 1.0 × (1/(1+2.0×0.365))
     = 0.7 × 0.35 × 0.535 × 1.0 × 1.0 × 0.578
     ≈ 0.076
```

**结论：** "这是安全边界问题吗？" → 被授权为 `HYPOTHESIS`，调节后权重 ≈ 0.076。该假设极弱地影响场 — 它被记录但不主导。

**审计记录：**
```json
{
  "event_id": "evt-001",
  "authorized_role": "HYPOTHESIS",
  "contextual_weight": 0.076,
  "dominance_warning": false,
  "condition": "degraded",
  "degradation_reason": "low_context_support_single_evidence"
}
```

### L.2 Walkthrough 2："是不是卡尔曼滤波？"

**场景：** 用户问 "是不是卡尔曼滤波？"

**上游 EvidenceProposal:**
- `candidate_role = HYPOTHESIS`（LLM 正确识别为类比/假设）
- `raw_confidence c_i = 0.6`

**Regulator 处理：**

| 步骤 | 因子 | 值 | 说明 |
|------|------|-----|------|
| 表面显著度 | `s_i` | 0.9 | "卡尔曼滤波" 是高度显著的技术词 |
| 上下文支持 | `term_support` | 0.0 | 首次出现 |
| 上下文支持 | `intent_support` | 0.2 | 可能是突发的类比跳跃 |
| 上下文支持 | `project_frame_support` | 0.3 | 技术词但非项目框架核心 |
| **上下文支持** | **`q_i`** | **0.18** | `0.25×0.0 + 0.45×0.2 + 0.30×0.3` |
| 假设可能性 | `h_i` | 0.8 | 上游自己标记为 HYPOTHESIS |
| **主导风险** | **`d_i`** | **0.576** | `max(0, 0.9-0.18) × 0.8` |
| 场兼容性 | `f_i` | 1.0 | 协作者层未饱和 |

**角色决策：**
- `candidate_role = HYPOTHESIS`, `q_i = 0.18 < 0.7`（不满足升级条件 5）
- `ρ_i = 1.0 < 1.3`（不满足升级条件 2）
- → **保持 HYPOTHESIS**（不升级）

**权重计算：**
```
w_i' = 0.6 × 0.35 × (0.35 + 0.65 × 0.18) × 1.0 × 1.0 × (1/(1+2.0×0.576))
     = 0.6 × 0.35 × 0.467 × 1.0 × 1.0 × 0.465
     ≈ 0.046
```

**结论：** "是不是卡尔曼滤波？" → 被正确识别为类比/假设，保持 `HYPOTHESIS`，权重 ≈ 0.046。该类比几乎不影响场 — 它不被当作架构命令。

**关键设计点：** 即使 LLM 错误地将 `candidate_role` 设为 `ANCHOR`，Regulator 仍会因高 `d_i` (≥ 0.5) 将其降级为 `HYPOTHESIS`（§D.3 规则 1）。

### L.3 Walkthrough 3："注意力稀释怎么做？"

**场景：** 用户问 "注意力稀释怎么做？"

**上游 EvidenceProposal:**
- `candidate_role = HYPOTHESIS`（LLM 可能提议：用户在询问 Attention 机制或注意力机制稀释）
- `raw_confidence c_i = 0.65`

**Regulator 处理：**

| 步骤 | 因子 | 值 | 说明 |
|------|------|-----|------|
| 表面显著度 | `s_i` | 0.88 | 短 turn，概念词密度高 |
| 上下文支持 | `term_support` | 0.0 | "注意力稀释" 首次出现 |
| 上下文支持 | `intent_support` | 0.25 | 与之前话题可能不连续 |
| 上下文支持 | `project_frame_support` | 0.5 | 中性 |
| **上下文支持** | **`q_i`** | **0.2625** | `0.25×0.0 + 0.45×0.25 + 0.30×0.5` |
| 假设可能性 | `h_i` | 0.8 | 短 turn 无法判断是陈述或提问 |
| **主导风险** | **`d_i`** | **0.494** | `max(0, 0.88-0.2625) × 0.8` |
| 场兼容性 | `f_i` | 1.0 | 相关性轴未饱和 |

**角色决策：**
- `candidate_role = HYPOTHESIS`, 升级条件不满足
- → **保持 HYPOTHESIS**

**权重计算：**
```
w_i' = 0.65 × 0.35 × (0.35 + 0.65 × 0.2625) × 1.0 × 1.0 × (1/(1+2.0×0.494))
     = 0.65 × 0.35 × 0.5206 × 1.0 × 1.0 × 0.503
     ≈ 0.060
```

**结论：** "注意力稀释怎么做？" → 被授权为 `HYPOTHESIS`，权重 ≈ 0.060。该概念词不会因为高表面显著度而获得对场的过度解释权威。

**关键设计点：** 这是一个典型的 **Salience Dilution** 场景 — 短 turn + 强概念词 + 低上下文支持。Regulator 的核心价值在这里体现："注意力稀释" 在 NLP 语义空间中可能指向 Transformer attention，但在 Aphrodite 的场框架中，它应被理解为 current-turn dominance control — 一个假设，不是架构命令。

### L.4 Walkthrough 总结

| Walkthrough | 输入 | candidate_role | authorized_role | w_i' | 核心机制 |
|-------------|------|---------------|-----------------|------|----------|
| W1 "安全边界问题?" | 用户假设 | ANCHOR (LLM误) | HYPOTHESIS (降级) | 0.076 | q_i < 0.6 降级规则 |
| W2 "卡尔曼滤波?" | 技术类比 | HYPOTHESIS | HYPOTHESIS | 0.046 | 高 d_i 防止升级 |
| W3 "注意力稀释?" | 短 turn 强概念 | HYPOTHESIS | HYPOTHESIS | 0.060 | Salience Dilution 效应 |

三条 walkthrough 的共同模式：**高表面显著度 + 低上下文支持 → 高主导风险 → 权重被大幅压制。** 这正是 Regulator 的设计目的。

---

## M. 输出格式声明

本设计规范全文以简体中文撰写，关键术语保留英文原词。所有文件路径引用使用相对路径格式 [`filename`](relative/path:line)。数学公式使用 ASCII 伪代码表示。Python 代码片段使用标准 Python 3.10+ 语法。

---

## Planner Handoff 草案

### 设计决策摘要

| 决策 | 结论 | 替代方案已排除 |
|------|------|---------------|
| Regulator 的位置 | 在 ProposalToFieldPerturbationAdapter 之后、PerturbationToForceAdapter 之前 | 直接在 FieldSignalProposal 上操作（侵入性太大） |
| 输出格式 | `ForceEventProposal` → 薄转换 → `FieldPerturbation` | 直接修改 `FieldPerturbation` 结构（破坏现有接口） |
| 权重公式 | 乘法链 `c_i × W_role × ContextSupport × FieldCompatibility × RecurrenceBonus × DominancePenalty` | 加法或 max-pooling（无法表达因子间的条件依赖） |
| 角色决策 | 确定性决策表（§C.4） | LLM 自由裁量（违反 J-6） |
| context_support | 三维度度：term(0.25) + intent(0.45) + project_frame(0.30) | 仅字面词重复（错误地将表面重复等同于上下文支持） |
| 注册预算 | 等比缩放至 B_turn = 0.8 | 截断（破坏提案间相对比例） |
| 场兼容性 | 三级：COMPATIBLE/WEAK_CONFLICT/STRONG_CONFLICT + Block | 连续值（假精确度） |
| STRONG_CONFLICT 处理 | Block + Audit（不重定向） | 自动重定向（超出最小范围） |

### 实施文件清单

**新建文件（拟议）：**
1. `src/field_dynamics/evidence_role.py` — `EvidenceRole` 枚举
2. `src/field_dynamics/force_event_proposal.py` — `ForceEventProposal` dataclass
3. `src/field_dynamics/salience.py` — `compute_surface_salience()`
4. `src/field_dynamics/context_support.py` — `compute_context_support()`
5. `src/field_dynamics/dominance.py` — `compute_dominance_risk()`
6. `src/field_dynamics/field_compat.py` — `compute_field_compatibility()`
7. `src/field_dynamics/regulator.py` — `ContextualEvidenceRegulator` 主类
8. `src/field_dynamics/force_event_adapter.py` — `force_event_to_perturbation()` 薄转换

**不修改的文件：**
- `src/field_dynamics/force_adapter.py` — 现有 PerturbationToForceAdapter（不变）
- `src/field_state/schema.py` — RelationalFieldState（只读）
- `src/field_state/perturbation.py` — FieldPerturbation（不变）
- `src/field_dynamics/schema.py` — M/C/K/FieldDynamicsConfig（不变）

### 待用户确认的问题

1. **`B_turn` 默认值 0.8 是否合适？** 需要在影子模式运行后根据实际提案密度调优。
2. **`context_support` 三维度度的权重 (0.25/0.45/0.30) 是否需要调整？** 当前偏向意图连续性；如果实际运行中项目框架约束更重要，需重新分配。
3. **基线保护轴列表是否完整？** 当前仅列出 `contamination_resistance`、`presence_stability`、`service_resistance` 三个。是否需要将 `boundary_distance` 也加入？
4. **`ρ_i` 上限 1.75 是否合理？** 对应约 5 次历史重复即达最大加成 — 是否需要更渐进的增长曲线？
5. **薄转换层是否需要处理 `ForceEventProposal` 到多个 `FieldPerturbation` 的展开？** 当前设计中一个 `ForceEventProposal` 可能包含多个 `authorized_target_axes`，每个生成一个 `FieldPerturbation` — 这是否会导致单提案的力被稀释？

### 风险提示

- **Salience Dilution 可能过度。** 如果 `DominancePenalty` 在实际运行中对所有新概念词都产生过大压制，可能导致场对新信息过度不敏感。影子模式审计应监控 `w_i'` 的分布 — 如果超过 80% 的提案权重低于 0.1，需调低 `β`。
- **context_support 的 fixture 依赖性。** 最小实现中 `q_i` 的子分量可能依赖 fixture 提供 — 需确保 fixture 的质量和覆盖范围。
- **与现有 FieldTrace 的整合顺序。** `intent_support` 需要读取最近 N 轮的 `authorized_role` 序列 — 需确定 FieldTrace 是否已提供此查询接口。

---

> **文档结束。**  
> 本规范为 Phase 39.6c+ ContextualEvidenceRegulator / Salience Dilution Layer 的完整设计规范。  
> 下一步：用户确认待决问题 → Planner 模式将本规范转化为实施计划。
