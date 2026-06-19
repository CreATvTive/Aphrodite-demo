> **Architecture ownership marker - Standalone Experimental Branch**
>
> This directory is an independent emotion-protocol experiment. It is not an authoritative description or implementation path for the current Aphrodite runtime architecture. Preserve it as experimental/reference material unless it is deliberately integrated through the current runtime and authoritative design process.

# 🎭 情感状态协议与实验平台

> Emotion State Protocol & Experiment Platform

一个简单但完整的情感状态协议实现，用于直观验证和实验情感状态系统。

---

## 📁 项目结构

```
emotion-protocol/
├── README.md                 # 本文件
├── EMOTION_PROTOCOL.md       # 完整协议文档
└── demo/
    ├── emotion_engine.py     # Python 控制台演示
    └── web_demo.html         # 网页可视化演示
```

---

## 🚀 快速开始

### 方式一：网页演示 (推荐)

直接在浏览器中打开 `demo/web_demo.html`:

```bash
# macOS / Linux
open demo/web_demo.html

# Windows
start demo/web_demo.html

# 或者用任意浏览器打开文件
```

**功能:**
- 📊 实时情感状态可视化
- 🎚️ 效价 - 唤醒度双轴显示
- ⚡ 一键触发各类情感事件
- 📜 事件日志时间线
- 📈 情感状态分布图
- ⚙️ 可调节衰减率和阈值

### 方式二：Python 控制台演示

```bash
cd demo

# 交互模式
python3 emotion_engine.py

# 自动演示模式
python3 emotion_engine.py --auto
```

**交互命令:**
```
1-5. 触发不同类型的用户消息
6.   触发环境变化 (意外事件)
7-8. 触发记忆回想 (积极/消极)
9.   自定义消息
0.   重置所有情感
r.   刷新显示
q.   退出
```

---

## 📖 核心概念

### 1. 情感数据结构

每个情感包含:
- **类型**: 开心/悲伤/愤怒/惊讶等 10 种基础情感
- **强度**: 0.0 - 1.0
- **衰减率**: 每秒衰减速度
- **持续时间**: 情感预期存在时间

### 2. 情感状态机

```
事件触发 → 规则评估 → 情感修改 → 衰减计算 → 状态更新
```

### 3. 效价 - 唤醒度模型

```
效价 (Valence):   -1 (负面) ←→ 0 (中性) ←→ 1 (正面)
唤醒度 (Arousal):  0 (平静) ←→ 1 (激动)
```

情感映射示例:
| 情感 | 效价 | 唤醒度 |
|------|------|--------|
| 开心 | +0.8 | +0.6 |
| 悲伤 | -0.7 | -0.4 |
| 愤怒 | -0.6 | +0.8 |
| 惊讶 | +0.3 | +0.9 |

---

## 🎮 使用示例

### 网页演示操作

1. **观察初始状态**: 主导情感为"中性" 😐
2. **点击"赞美"按钮**: 看到"开心"情感条增长
3. **点击"悲伤消息"**: 同时存在开心和悲伤 (情感叠加)
4. **等待几秒**: 观察情感自然衰减
5. **点击"重置"**: 清空所有情感

### Python 演示操作

```bash
$ python3 emotion_engine.py

══════════════════════════════════════════════════
🎭 情感状态面板
══════════════════════════════════════════════════

主导情感：😐 中性
效价 (Valence):   ░░░░░░░░░░░░░░░░░░░░ 0.00
唤醒度 (Arousal): ███░░░░░░░░░░░░░░░░░ 0.10

情感强度:
  (无活跃情感)
══════════════════════════════════════════════════

请选择操作 > 1

⚡ 事件触发：user_message
   载荷：{'text': '今天好开心！'}

══════════════════════════════════════════════════
🎭 情感状态面板
══════════════════════════════════════════════════

主导情感：😊 开心
效价 (Valence):   ░░░░░░░░░░░░░░░░░███ +0.80
唤醒度 (Arousal): ░░░░░░░░░░░░░░░░████ +0.60

情感强度:
  😊 开心  : ███████████████ 0.40 (存在 0.1s)
══════════════════════════════════════════════════
```

---

## 🔌 API 接口规范

### RESTful API (设计)

```
GET  /api/emotion/state          # 获取当前情感状态
POST /api/emotion/state          # 手动设置情感
DELETE /api/emotion/state        # 清除所有情感

POST /api/events/trigger         # 触发事件
GET  /api/events/history         # 获取事件历史

GET  /api/config                 # 获取配置
PUT  /api/config                 # 更新配置
POST /api/config/reset           # 重置配置

WS   /api/ws                     # WebSocket 实时推送
```

### 表现层扩展接口

```typescript
interface ExpressionAdapter {
  init(config): Promise<void>;
  updateEmotion(state): void;
  playAnimation(emotion, intensity): void;
  destroy(): void;
}

// 可实现:
// - Live2DAdapter: 驱动 Live2D 模型表情
// - VRMAdapter: 驱动 VRM 虚拟人
// - EmojiAdapter: 显示 emoji 动画
// - VoiceAdapter: 改变语音语调
```

---

## ⚙️ 配置说明

### 情感配置

```json
{
  "emotions": {
    "joy": {
      "decayRate": 0.015,
      "cooldown": 2000,
      "defaultDuration": 8000
    },
    "sadness": {
      "decayRate": 0.008,
      "cooldown": 15000,
      "defaultDuration": 15000
    }
  }
}
```

### 规则配置

```json
{
  "rules": [
    {
      "id": "praise_triggers_joy",
      "from": "*",
      "to": "joy",
      "trigger": ["user_message"],
      "conditions": {
        "payloadContains": ["praise", "thank", "love"]
      },
      "effect": {
        "addIntensity": 0.3,
        "duration": 8000
      }
    }
  ]
}
```

---

## 🧪 实验场景

### 场景 1: 情感叠加
1. 触发"赞美" → 开心 😊
2. 触发"悲伤消息" → 开心 + 悲伤共存
3. 观察主导情感变化

### 场景 2: 情感衰减
1. 触发"意外事件" → 惊讶 😲 (高强度)
2. 等待 5 秒 → 观察惊讶快速衰减
3. 触发"表达爱意" → 喜爱 😍 (慢衰减)

### 场景 3: 情感冲突
1. 触发"愤怒" → 愤怒 😠
2. 触发"赞美" → 愤怒 + 开心 (低兼容性)
3. 观察冲突解决

### 场景 4: 效价 - 唤醒度轨迹
1. 在网页演示中观察状态图
2. 触发不同情感
3. 看点在二维空间中的分布

---

## 📚 扩展方向

### 短期扩展
- [ ] 添加更多情感类型 (骄傲、羞愧、嫉妒等)
- [ ] 实现情感规则配置文件加载
- [ ] 添加情感日志导出功能

### 中期扩展
- [ ] 实现 WebSocket 实时推送
- [ ] 开发 Live2D 适配器
- [ ] 添加情感规则可视化编辑器

### 长期扩展
- [ ] 集成到 AI 角色系统
- [ ] 实现长期情感/心境系统
- [ ] 添加情感记忆关联

---

## 🛠️ 技术栈

- **协议文档**: Markdown
- **Python 演示**: 纯 Python 3.6+, 无依赖
- **网页演示**: 原生 HTML/CSS/JavaScript, 无框架

---

## 📄 许可证

MIT License - 自由使用、修改和分发

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request!

---

**最后更新**: 2026-03-11
**版本**: v1.0
