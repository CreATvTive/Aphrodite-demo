Status: HISTORICAL
Authority-Scope: Pre-chatbox Live2D/demo architecture historical snapshot
Superseded-By: docs/chatbox/phase-plan-v0.md
Last-Verified: 2026-07-14

> **HISTORICAL — 非当前指令：**正文中的“完整架构”“快速开始”“开发路线”和“协议”均为历史快照，不是 chatbox v0 指令。当前工程权威见 [`docs/chatbox/phase-plan-v0.md`](../docs/chatbox/phase-plan-v0.md)；身份边界见 [`docs/design/README.md`](../docs/design/README.md)。

# Live2D 对话演示 - 技术架构文档

本目录包含 "游戏式 AI 角色世界" 项目中 Live2D 对话演示模块的完整技术架构设计。

---

## 📚 文档索引

| 文档 | 描述 | 适合人群 |
|------|------|----------|
| [`ARCHITECTURE-SUMMARY.md`](./ARCHITECTURE-SUMMARY.md) | ⭐ **快速参考** - 核心决策、接口定义、开发路线 | 所有开发者 |
| [`live2d-dialogue-architecture.md`](./live2d-dialogue-architecture.md) | 📘 **完整架构** - 详细设计、模块说明、实现建议 | 架构师、核心开发 |
| [`data-flow-diagrams.md`](./data-flow-diagrams.md) | 📊 **数据流图** - 时序图、状态机、流程图 | 前端/后端开发 |
| [`api-interfaces.ts`](./api-interfaces.ts) | 💻 **前端接口** - TypeScript 类型定义 | 前端开发 |
| [`backend_interfaces.py`](./backend_interfaces.py) | 🐍 **后端接口** - Python 数据模型和接口 | 后端开发 |

---

## 🎯 架构概述

```
┌─────────────────────────────────────────────────────────────┐
│                      用户 (Browser)                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  Live2D     │  │   对话 UI    │  │   WebSocket 客户端   │  │
│  │  角色渲染    │  │  输入 + 消息  │  │   + 音频播放        │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            │
                            │ WebSocket + HTTP
                            │
┌─────────────────────────────────────────────────────────────┐
│                    后端服务 (Python/FastAPI)                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   Memory    │  │  Character  │  │      Voice          │  │
│  │   Module    │  │   Module    │  │   (GPT-SoVITS)      │  │
│  │  (记忆存储)  │  │  (角色管理)  │  │    (语音合成)       │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
│                           │                                  │
│                  ┌────────┴────────┐                         │
│                  │   LLM Service   │                         │
│                  │   (回复生成)     │                         │
│                  └─────────────────┘                         │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔑 核心特性

- ✅ **实时对话**: WebSocket 双向通信，低延迟
- ✅ **情感表达**: 7 种情感状态，自动语义分析
- ✅ **Live2D 渲染**: 表情 + 动作同步控制
- ✅ **流式语音**: GPT-SoVITS 对接，边合成边播放
- ✅ **记忆系统**: 对话历史 + 长期记忆查询
- ✅ **断线重连**: 自动重连 + 消息队列

---

## 🚀 快速开始

### 前端开发

1. 阅读 [`ARCHITECTURE-SUMMARY.md`](./ARCHITECTURE-SUMMARY.md) 了解核心接口
2. 参考 [`api-interfaces.ts`](./api-interfaces.ts) 定义 TypeScript 类型
3. 查看 [`data-flow-diagrams.md`](./data-flow-diagrams.md) 理解数据流

### 后端开发

1. 阅读 [`ARCHITECTURE-SUMMARY.md`](./ARCHITECTURE-SUMMARY.md) 了解 API 端点
2. 参考 [`backend_interfaces.py`](./backend_interfaces.py) 实现数据模型
3. 查看 [`live2d-dialogue-architecture.md`](./live2d-dialogue-architecture.md) 第 4 节了解详细设计

### 全栈开发

建议按以下顺序阅读:
1. `ARCHITECTURE-SUMMARY.md` (10 分钟) - 快速了解整体
2. `data-flow-diagrams.md` (15 分钟) - 理解数据流
3. `live2d-dialogue-architecture.md` (30 分钟) - 深入细节

---

## 📋 开发检查清单

### 前端

- [ ] WebSocket 客户端实现 (连接、重连、心跳)
- [ ] Live2D 模型加载和渲染
- [ ] 表情/动作管理器
- [ ] 对话 UI 组件 (消息列表、输入框)
- [ ] 音频播放器 (流式)
- [ ] 状态管理 (Pinia/Zustand)

### 后端

- [ ] WebSocket 服务器 (FastAPI)
- [ ] 消息路由和处理
- [ ] Memory 模块对接
- [ ] Character 模块对接
- [ ] LLM 回复生成
- [ ] 情感分析模块
- [ ] GPT-SoVITS 语音合成对接
- [ ] HTTP REST API

### 联调

- [ ] 端到端对话流程
- [ ] 表情/动作同步
- [ ] 音画同步
- [ ] 断线重连测试
- [ ] 性能测试

---

## 📞 接口联系

### WebSocket 端点
```
ws://localhost:8000/ws?token=<JWT_TOKEN>
```

### HTTP API 基础路径
```
http://localhost:8000/api/v1/
```

### 示例消息

**用户发送:**
```json
{
  "type": "user_message",
  "payload": {
    "message_id": "uuid-here",
    "content": "你好呀!",
    "timestamp": 1709999999000,
    "session_id": "session-123"
  }
}
```

**角色回复:**
```json
{
  "type": "character_reply",
  "payload": {
    "message_id": "uuid-here",
    "content": "你好！今天过得怎么样？",
    "emotion": "happy",
    "expression_id": "exp_happy",
    "motion_id": "motion_speak",
    "timestamp": 1710000000000,
    "in_reply_to": "uuid-here"
  }
}
```

---

## 📝 更新日志

| 日期 | 版本 | 更新内容 |
|------|------|----------|
| 2026-03-11 | 1.0 | 初始架构设计完成 |

---

## 🔗 相关文档

- 项目主文档：`/home/creative/.openclaw/workspace/`
- 用户文档：`USER.md`
- 灵魂文档：`SOUL.md`

---

*文档由 OpenClaw Subagent (demo-architect) 生成*
*最后更新：2026-03-11*
