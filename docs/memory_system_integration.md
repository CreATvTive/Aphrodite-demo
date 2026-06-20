# 记忆系统融合方案

> 文件性质：documentation-only / historical snapshot —— 记录早期桌面原型记忆系统向 `src/memory/` 迁移的融合思路。其中提到的 `src/character/` 等路径及模块归属可能与当前 runtime 不一致；当前实现与权威设计请参见 `docs/design/README.md`、`AGENTS.md` 与当前 runtime anchors。

## 现状分析

### 桌面原型 (`A0.32026205.01(暂停）.py`)
**已有能力：**
- ✅ SQLite + FAISS 记忆存储与检索
- ✅ 遗忘曲线（14 天半衰期）+ 见过次数加固
- ✅ 自动标签提取与权重打分
- ✅ 话题熔断机制
- ✅ 三层检索评分（语义 0.75 + 时间 0.15 + 权重 0.07 + 关键词 0.03）
- ✅ 自学习风格策略（强化学习）
- ✅ 空闲检测与主动搭话
- ✅ 情绪/能量/亲密度状态系统

**局限：**
- ❌ 单层记忆（只有情景记忆，缺少语义记忆提炼）
- ❌ 单角色设计（没有多角色记忆隔离）
- ❌ 缺少人格核心约束
- ❌ 缺少关系系统
- ❌ 缺少世界状态管理

---

### Git 仓库架构 (`architecture.md` + `src/semantic_trigger/`)
**已有能力：**
- ✅ 5 层架构设计（表现层/交互层/世界层/角色层/持久层）
- ✅ 语义触发引擎（检索→重排→决策→仲裁）
- ✅ 人格核心设计（Persona Core）
- ✅ 关系系统设计
- ✅ 世界状态管理

**局限：**
- ❌ 记忆系统只有设计文档，没有实现代码
- ❌ 语义触发是"任务导向"，不是"记忆导向"

---

## 融合方案

### 核心思路

**把桌面原型的记忆系统作为"角色层"的核心模块，嵌入 Git 仓库的 5 层架构中。**

```
┌─────────────────────────────────────────────────────────────┐
│                    表现层 (Presentation)                     │
│   Streamlit 页面 (已有) / 游戏 UI / 事件通知                  │
├─────────────────────────────────────────────────────────────┤
│                    交互层 (Interaction)                      │
│   语义触发引擎 (已有) + 对话管理 (新建)                       │
├─────────────────────────────────────────────────────────────┤
│                    世界层 (World)                            │
│   世界状态 + 事件系统 + 时间推进 (新建)                        │
├─────────────────────────────────────────────────────────────┤
│                    角色层 (Character)                        │
│   ├─ 人格核心 (新建)                                         │
│   ├─ 记忆系统 (桌面原型移植) ← 本次融合重点                   │
│   ├─ 关系网络 (新建)                                         │
│   └─ 情绪状态 (桌面原型已有)                                  │
├─────────────────────────────────────────────────────────────┤
│                    持久层 (Persistence)                      │
│   SQLite (记忆) + FAISS (向量) + PostgreSQL (可选)           │
└─────────────────────────────────────────────────────────────┘
```

---

## 实施步骤

### Phase 1: 记忆系统移植 (1-2 天)

**目标：** 把桌面原型的 `MemoryStore` 类移植到 `src/memory/` 目录

**文件结构：**
```
src/memory/
├── __init__.py
├── store.py          # MemoryStore 类（移植自桌面原型）
├── schemas.py        # 记忆数据结构定义
├──遗忘.py            # 遗忘曲线计算
├── tags.py           # 标签提取与权重打分
└── config.py         # 记忆系统配置
```

**关键改动：**
1. 把 `MemoryStore` 从单文件拆分成模块化结构
2. 增加**多角色支持**（`character_id` 字段）
3. 增加**记忆类型**（`memory_type: "episodic" | "semantic"`）
4. 保留原有核心算法（遗忘曲线、标签权重、话题熔断）

---

### Phase 2: 三层记忆模型实现 (2-3 天)

**目标：** 在 `MemoryStore` 基础上实现工作记忆/情景记忆/语义记忆

**数据结构：**
```python
@dataclass
class Memory:
    id: int
    character_id: str
    memory_type: Literal["working", "episodic", "semantic"]
    text: str
    tags: List[Tuple[str, float]]
    
    # 遗忘相关
    created_at: int          # 时间戳
    last_seen: int           # 上次检索时间
    seen_count: int          # 被检索次数
    strength: float          # 基础强度 (0-1)
    
    # 情景记忆特有
    timestamp: Optional[int]  # 事件发生时间
    emotion: Optional[str]    # 情绪标记
    importance: float         # 重要性评分 (0-1)
    
    # 语义记忆特有
    source_memory_ids: List[int]  # 从哪些情景记忆提炼而来
    confidence: float             # 提炼置信度
```

**关键机制：**
- **工作记忆：** 用 `recent_messages` 实现，session 内有效，容量限制 6 轮
- **情景记忆：** 每次对话后，用 LLM 提取关键事件 → 存入 SQLite
- **语义记忆：** 定期（如每天）回顾情景记忆 → LLM 提炼 → 存入 SQLite

---

### Phase 3: 人格核心集成 (1-2 天)

**目标：** 在每次生成回复前注入人格约束

**实现：**
```python
# src/character/persona.py

@dataclass
class Persona:
    identity: Identity
    traits: Dict[str, float]      # 人格特质权重
    values: List[str]             # 价值排序
    speech_pattern: SpeechPattern
    boundaries: List[str]

def build_system_prompt(persona: Persona, state: CharacterState, retrieved_memories: List[str]) -> str:
    """
    构造 system prompt，注入：
    - 人格核心约束
    - 当前状态（情绪/能量/亲密度）
    - 检索到的记忆
    """
```

**与记忆系统集成：**
- 记忆检索时考虑人格特质（高开放性角色更容易联想 distant memories）
- 记忆写入时用人格过滤（不符合人格的记忆不存）

---

### Phase 4: 关系系统 (2-3 天)

**目标：** 实现多角色关系网络

**数据结构：**
```python
# src/character/relationship.py

@dataclass
class Relationship:
    character_id: str
    user_id: str
    metrics: RelationshipMetrics
    history: List[RelationshipEvent]
    status: RelationshipStatus
    unlocks: List[str]

@dataclass
class RelationshipMetrics:
    trust: float      # 0-1
    intimacy: float   # 0-1
    respect: float    # 0-1
    tension: float    # 0-1
```

**与记忆系统集成：**
- 关系事件存入情景记忆（"2026-03-09 用户背叛了角色"）
- 关系状态影响记忆检索（低信任时更容易想起负面记忆）

---

### Phase 5: 世界状态与时间推进 (2-3 天)

**目标：** 实现世界独立运行（用户不在线时也在推进）

**实现：**
```python
# src/world/state.py

@dataclass
class WorldState:
    time: WorldTime
    locations: Dict[str, Location]
    active_events: List[WorldEvent]
    character_states: Dict[str, CharacterState]

# src/world/time.py
class TimeSystem:
    def tick(self, delta_seconds: int) -> None:
        """推进世界时间，处理离线时间流逝"""
        # - 更新角色状态（位置/活动/心情）
        # - 触发定时事件
        # - 处理记忆遗忘（定期调用 MemoryStore.forget()）
```

---

## 代码迁移清单

### 从桌面原型移植的核心代码

| 模块 | 源文件 | 目标文件 | 改动 |
|------|--------|----------|------|
| MemoryStore | `A0.32026205.01(暂停）.py` | `src/memory/store.py` | 增加 character_id, memory_type |
| 遗忘曲线 | 同上行 | `src/memory/forgetting.py` | 拆分成独立模块 |
| 标签提取 | 同上行 | `src/memory/tags.py` | 保留核心算法 |
| 话题熔断 | 同上行 | `src/memory/breaker.py` | 独立成模块 |
| 自学习策略 | 同上行 | `src/character/style_policy.py` | 保留 RL 算法 |
| 状态系统 | 同上行 | `src/character/state.py` | 增加更多状态字段 |

---

## 下一步行动

**建议从 Phase 1 开始，先移植记忆系统核心，再逐步扩展。**

我可以帮你：
1. 创建 `src/memory/` 目录结构
2. 移植 `MemoryStore` 类并模块化
3. 增加多角色支持和记忆类型
4. 写单元测试验证功能

**你想现在就开始吗？还是先讨论一下设计细节？**
