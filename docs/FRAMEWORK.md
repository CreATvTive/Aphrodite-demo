> **Architecture ownership marker - Historical snapshot / Non-authoritative / documentation-only**
>
> This document is preserved as historical architecture material. It is not authoritative for the current Aphrodite runtime architecture; consult `docs/design/` for explicitly authoritative design guidance and the current runtime anchors for implementation ownership. The "已完成模块" section reflects an earlier project phase; `src/character/` and related features may no longer match the current runtime priority.

# 游戏式 AI 角色世界 - 项目框架

## 项目愿景

**不是聊天机器人，而是一个世界模拟器。**

让用户能够和真正有记忆、有成长、有个体化理解能力的虚拟角色沟通，获得原本没有的沟通机会。

---

## 核心架构

```
┌─────────────────────────────────────────────────────────────┐
│                    表现层 (Presentation)                     │
│   Streamlit / Web UI / Discord / 游戏界面                    │
├─────────────────────────────────────────────────────────────┤
│                    交互层 (Interaction)                      │
│   对话管理 / 行动系统 / 语义触发引擎                          │
├─────────────────────────────────────────────────────────────┤
│                    世界层 (World)                            │
│   世界状态 / 事件系统 / 时间推进                              │
├─────────────────────────────────────────────────────────────┤
│                    角色层 (Character) ⭐                     │
│   ├─ 人格核心 (Persona)                                     │
│   ├─ 记忆系统 (Memory)                                      │
│   ├─ 关系网络 (Relationship)                                │
│   └─ 情绪状态 (State)                                       │
├─────────────────────────────────────────────────────────────┤
│                    声音层 (Voice) ⭐                         │
│   GPT-SoVITS 声音克隆 / TTS / 情感控制                        │
├─────────────────────────────────────────────────────────────┤
│                    持久层 (Persistence)                      │
│   SQLite (记忆) + FAISS (向量) + PostgreSQL (可选)           │
└─────────────────────────────────────────────────────────────┘
```

**⭐ 本次开发重点：角色层 + 声音层**

---

## 已完成模块

> 以下“已完成模块”描述的是历史阶段（阶段 3 之前）的 `src/character/`、`src/voice/` 路径，这些路径已在阶段 3 移除；当前 runtime 以 `docs/design/README.md` 与 `AGENTS.md` 锚点为准。

### 1. 角色生成系统 (`src/character/`)

**功能：** 从用户简短描述生成完整角色设定

```python
from src.character import generate_character_from_query

# 用户输入 → 完整角色
profile = generate_character_from_query("banG Dream! 里的丰川祥子")
```

**输出包含：**
- ✅ 人格特质（大五人格 + 性格标签）
- ✅ 声音配置（支持 GPT-SoVITS 克隆）
- ✅ 语法风格（词汇/句式/口头禅/禁用词）
- ✅ 世界环境（时代/地点/规则/事件）
- ✅ 立场价值观（道德阵营/信念/敌友/边界）
- ✅ 记忆系统配置（根据人格自动调整）

**核心文件：**
```
src/character/
├── schemas.py           # 数据结构定义 ✅
├── generator.py         # 角色生成器 ✅
├── __init__.py          # 模块导出 ✅
├── README.md            # 使用说明 ✅
└── SETUP.md             # 环境配置 ✅
```

**人格 - 记忆联动示例：**
```python
# 高宜人性角色 → 情绪权重高、遗忘慢
config.emotion_weight = 0.05 + (p.agreeableness * 0.15)  # 0.05~0.20
config.half_life_days = 14.0 + (p.agreeableness * 14.0)  # 14~28 天

# 高外向性角色 → 主动搭话频繁
config.idle_threshold_sec = max(10, 30 - int(p.extraversion * 20))
config.max_nudges = 1 + int(p.extraversion * 3)
```

---

### 2. 声音系统 (`src/voice/`)

**功能：** GPT-SoVITS 声音克隆与合成

```python
from src.voice import GPTSoVITSAdapter

adapter = GPTSoVITSAdapter()

# 为角色克隆声音
adapter.clone_voice(
    character_name="丰川祥子",
    reference_audio="sakiko_reference.wav",
    reference_text="音频对应的文字",
)

# 合成语音（带情感）
result = adapter.synthesize(
    text="我已经决定了",
    emotion="sad",  # happy/sad/angry/fearful/disgusted/surprised/neutral
    save_path="output.wav",
)
```

**核心文件：**
```
src/voice/
├── gptsovits_adapter.py  # GPT-SoVITS 适配器 ✅
├── __init__.py           # 模块导出 ✅
└── README.md             # 使用说明 ✅
```

**情感 - 人格联动：**
```python
# 高神经质角色 → 情绪波动大
if profile.persona.neuroticism > 0.6:
    emotion = map_emotion_from_state(state)  # 动态调整情感
```

---

## 待完成模块

### P0: 记忆系统移植

**目标：** 把桌面原型的 MemoryStore 移植到 `src/memory/`

**已有能力（桌面原型）：**
- ✅ SQLite + FAISS 存储与检索
- ✅ 遗忘曲线（14 天半衰期）
- ✅ 标签权重打分
- ✅ 话题熔断机制

**需要补充：**
- ➕ 多角色支持（`character_id` 字段）
- ➕ 三层记忆模型（工作/情景/语义）
- ➕ 与 `PersonaMemoryConfig` 对接

**预计时间：** 2-3 小时

---

### P1: 集成到 runtime_engine

**目标：** 让现有对话系统支持角色切换

**改动：**
```python
# runtime_engine.py
from src.character import generate_character_from_query
from src.voice import GPTSoVITSAdapter

class RuntimeEngine:
    def __init__(self):
        self.tts = GPTSoVITSAdapter()
        self.current_profile = None
    
    def create_character(self, user_query: str):
        """创建角色"""
        self.current_profile = generate_character_from_query(user_query)
        
        # 配置声音
        self.tts.config.ref_audio_path = self.current_profile.voice.clone_reference
        
        # 初始化记忆系统
        self.memory = MemoryStore(
            character_id=self.current_profile.id,
            config=self.current_profile.memory_config,
        )
    
    def reply(self, user_text: str):
        """对话回复"""
        # 检索记忆
        memories = self.memory.retrieve(user_text)
        
        # 生成回复
        reply = generate_reply(user_text, memories, self.current_profile)
        
        # 合成语音
        emotion = map_emotion(self.current_profile, self.state)
        self.tts.synthesize(reply, emotion=emotion)
```

**预计时间：** 1-2 小时

---

### P2: 关系系统

**目标：** 实现角色之间的关系网络

**数据结构：**
```python
@dataclass
class Relationship:
    character_id: str
    user_id: str
    metrics: {
        trust: float,      # 0-1
        intimacy: float,   # 0-1
        respect: float,    # 0-1
        tension: float,    # 0-1
    }
    history: List[RelationshipEvent]
    status: str  # acquaintance/friend/close/strained/broken
```

**预计时间：** 2-3 小时

---

### P3: 世界状态系统

**目标：** 世界独立运行（用户离线时也在推进）

**功能：**
- 时间推进（现实时间 or 加速时间）
- 角色自主行为（不在等待用户）
- 事件系统（生日、节日、突发事件）
- 遗憾设计（用户可以错过事件）

**预计时间：** 3-4 小时

---

## 技术栈

### 核心依赖

| 模块 | 技术选型 | 状态 |
|------|---------|------|
| **LLM** | Qwen3 / GPT-4 | ✅ 已配置 |
| **记忆存储** | SQLite + FAISS | ✅ 原型完成 |
| **向量检索** | Sentence Transformers | ✅ 已集成 |
| **声音克隆** | GPT-SoVITS | ✅ 适配器完成 |
| **联网搜索** | DuckDuckGo | ✅ 已集成 |

### 可选依赖

| 模块 | 技术选型 | 用途 |
|------|---------|------|
| **多角色编排** | LangGraph / AutoGen | 多角色交互 |
| **关系数据库** | PostgreSQL | 大规模部署 |
| **事件系统** | Redis Streams | 世界事件推送 |
| **前端界面** | Streamlit / React | 用户界面 |

---

## 项目结构

```
Aphrodite-demo/
├── src/
│   ├── character/        # 角色系统 ✅
│   │   ├── schemas.py
│   │   ├── generator.py
│   │   └── ...
│   ├── voice/            # 声音系统 ✅
│   │   ├── gptsovits_adapter.py
│   │   └── ...
│   ├── memory/           # 记忆系统 (待移植)
│   ├── world/            # 世界系统 (待开发)
│   └── relationship/     # 关系系统 (待开发)
├── agentlib/             # 现有对话引擎
├── agent_kernel/         # Agent 内核
├── docs/                 # 文档
└── FRAMEWORK.md          # 本文件
```

---

## 开发路线图

### Phase 1: 核心能力 (本周)
- [x] 角色生成系统
- [x] 声音系统适配器
- [ ] 记忆系统移植
- [ ] 集成到 runtime_engine

### Phase 2: 世界构建 (下周)
- [ ] 关系系统
- [ ] 世界状态系统
- [ ] 时间推进机制
- [ ] 事件系统

### Phase 3: 表现层 (下下周)
- [ ] Streamlit 界面
- [ ] 角色状态可视化
- [ ] 记忆浏览界面
- [ ] 声音播放控制

### Phase 4: 扩展 (后续)
- [ ] 多角色支持
- [ ] 角色间自主交互
- [ ] 用户自定义角色
- [ ] 模组系统

---

## 测试角色

### 丰川祥子（测试用）

**人格特质：**
- 开放性：0.75 → 高语义权重
- 外向性：0.35 → 低频搭话
- 宜人性：0.45 → 低情绪权重
- 神经质：0.70 → 情绪偏向检索

**记忆配置：**
- 半衰期：12 天（中等偏快，符合"不看向过去"）
- 搭话间隔：45 秒（内向）
- 最多搭话：1 次

**声音配置：**
- 声线：少女音，清冷，略带疏离感
- 情感：以 neutral/sad 为主

**边界：**
- ❌ 不接受同情
- ❌ 不谈论家庭
- ❌ 不看向 CRYCHIC

---

## 快速开始

### 1. 环境准备

```bash
# 安装依赖
pip install openai duckduckgo-search sentence-transformers faiss-cpu

# 配置 API Key
export DASHSCOPE_API_KEY="your_key"
```

### 2. 测试角色生成

```bash
cd /path/to/Aphrodite-demo
python3 -c "from src.character import generate_character_from_query; print(generate_character_from_query('丰川祥子').to_json())"
```

### 3. 配置 GPT-SoVITS

```bash
# 克隆 GPT-SoVITS
git clone https://github.com/RVC-Boss/GPT-SoVITS.git
cd GPT-SoVITS
pip install -r requirements.txt

# 启动服务
python api_v2.py --host 127.0.0.1 --port 9880
```

### 4. 测试声音合成

```bash
cd /path/to/Aphrodite-demo
python3 src/voice/gptsovits_adapter.py
```

---

## 贡献指南

### 提交代码前

1. 确保测试通过
2. 更新文档
3. 检查代码格式（black + isort）
4. 添加单元测试（如适用）

### 报告问题

1. 描述问题现象
2. 提供复现步骤
3. 附上错误日志
4. 说明环境信息（Python 版本、OS 等）

---

## 许可证

本项目采用 MIT 许可证。

GPT-SoVITS 采用其原项目许可证（Apache 2.0）。

---

## 联系方式

- **GitHub**: [你的仓库地址]
- **Discord**: [你的 Discord 链接]
- **邮箱**: [你的邮箱]

---

_最后更新：2026-03-09_
