# 角色生成系统 - 环境配置指南

> 文件性质：documentation-only / 历史实现说明 —— 描述 `src/character/` 模块的环境配置与集成示例。该模块在当前 Aphrodite runtime 中不是具身主链路的必经组件；涉及 persona、记忆与 runtime 集成方式时，请以 [`docs/design/README.md`](../../docs/design/README.md) 与当前 runtime anchors 为准。

## 当前状态

✅ **代码已完成**：
- 数据结构定义 (`schemas.py`)
- 角色生成器 (`generator.py`)
- 人格 - 记忆联动逻辑
- 测试用例（Mock 模式）

⚠️ **需要配置**：
- API Key（LLM 调用）
- Python 依赖安装

---

## 环境要求

### 1. Python 版本
```bash
python3 --version  # 需要 3.10+
```

### 2. 安装依赖

**方式 A：使用项目 requirements.txt**
```bash
cd /path/to/Aphrodite-demo
pip install -r requirements.txt
pip install duckduckgo-search  # 联网搜索（可选）
```

**方式 B：手动安装**
```bash
pip install openai duckduckgo-search sentence-transformers faiss-cpu numpy
```

### 3. 配置 API Key

**方式 A：环境变量（推荐）**
```bash
# 添加到 ~/.bashrc 或 ~/.zshrc
export DASHSCOPE_API_KEY="your_dashscope_key"
export QWEN_MODEL="qwen3-max"

# 或者使用 OpenAI
export OPENAI_API_KEY="your_openai_key"
export OPENAI_MODEL="gpt-4o"
```

**方式 B：创建 .env 文件**
```bash
# 在项目根目录创建 .env 文件
cat > .env << EOF
DASHSCOPE_API_KEY=your_dashscope_key
QWEN_MODEL=qwen3-max
EOF
```

**获取 API Key：**
- 通义千问（DashScope）：https://dashscope.console.aliyun.com/
- OpenAI：https://platform.openai.com/api-keys

---

## 快速测试

### 测试 1：本地生成（不需要 API Key）
```bash
cd /path/to/Aphrodite-demo
python3 -c "from src.character import generate_character_from_query; print(generate_character_from_query('丰川祥子').to_json())"
```

预期输出：丰川祥子的完整角色设定（JSON 格式）

### 测试 2：真实生成（需要 API Key）
```bash
cd /path/to/Aphrodite-demo
python3 -c "
from src.character import generate_character_from_query
profile = generate_character_from_query('我想要一个傲娇的猫娘角色', enable_web_search=True)
print(profile.to_json(indent=2))
"
```

---

## 在主机器上运行

### 步骤 1：同步代码
```bash
# 从 Git 仓库拉取（如果已提交）
git pull origin main

# 或者手动复制文件
# 复制 src/character/ 目录到主机器
```

### 步骤 2：安装依赖
```bash
cd /path/to/Aphrodite-demo
pip install -r requirements.txt
pip install duckduckgo-search
```

### 步骤 3：配置 API Key
```bash
export DASHSCOPE_API_KEY="your_key"
```

### 步骤 4：测试运行

```bash
python3 -c "from src.character import generate_character_from_query; print(generate_character_from_query('赫敏·格兰杰').to_json())"  # 真实生成
```
---

## 集成到现有系统

### 1. 修改 runtime_engine.py

在文件顶部添加导入：
```python
from src.character import generate_character_from_query, CharacterProfile
```

添加角色创建函数：
```python
def create_character_from_query(user_query: str) -> CharacterProfile:
    """根据用户查询创建角色"""
    profile = generate_character_from_query(user_query, enable_web_search=True)
    
    # 保存角色设定
    save_character_profile(profile)
    
    # 初始化记忆系统
    from src.memory import MemoryStore
    memory_store = MemoryStore(
        character_id=profile.id,
        config=profile.memory_config,
    )
    
    return profile, memory_store
```

### 2. 修改 companion_chat.py

更新 system prompt 构建：
```python
from src.character import CharacterProfile

def build_system_prompt_with_persona(
    profile: CharacterProfile,
    retrieved_memories: List[str],
) -> str:
    """构建带人格约束的 system prompt"""
    persona_section = f"""
【角色设定】
名字：{profile.name}
身份：{profile.description}
人格特质：{profile.persona.tags}
核心价值观：{profile.stance.core_values}
"""
    # ... 继续构建其他部分
```

---

## 常见问题

### Q1: `ModuleNotFoundError: No module named 'openai'`
**A:** 安装依赖：`pip install openai`

### Q2: `API key not set`
**A:** 设置环境变量：`export DASHSCOPE_API_KEY="your_key"`

### Q3: 联网搜索失败
**A:** 安装 duckduckgo-search：`pip install duckduckgo-search`，或者设置 `enable_web_search=False`

### Q4: 生成的角色不符合预期
**A:** 调整 prompt 或提供更多上下文信息，例如：
```python
profile = generate_character_from_query(
    "我想要《哈利·波特》里的赫敏，聪明、勤奋、正义感强",
    enable_web_search=True
)
```

---

## 下一步开发计划

| 优先级 | 功能 | 预计时间 |
|--------|------|---------|
| P0 | 移植记忆系统（MemoryStore） | 2-3 小时 |
| P1 | 集成到 runtime_engine | 1-2 小时 |
| P2 | 多角色支持（记忆隔离） | 2-3 小时 |
| P3 | 声音克隆集成（ElevenLabs） | 1-2 小时 |
| P4 | 关系系统（角色间关系网络） | 3-4 小时 |

---

## 文件清单

```
src/character/
├── __init__.py           # 模块导出
├── schemas.py            # 数据结构定义 ✅
├── generator.py          # 角色生成器 ✅
├── README.md             # 使用说明 ✅
├── SETUP.md              # 环境配置 ✅
└── tests/
    └── test_generator.py # 单元测试（待完成）
```

---

## 联系与支持

遇到问题时：
1. 检查 API Key 是否正确设置
2. 确认依赖已安装
3. 查看错误日志
4. 提交 issue 到 Git 仓库
