Status: HISTORICAL
Authority-Scope: Pre-chatbox Live2D/demo architecture historical snapshot
Superseded-By: docs/chatbox/phase-plan-v0.md
Last-Verified: 2026-07-14

> **HISTORICAL — 非当前指令：**正文中的“完整架构”“快速开始”“开发路线”和“协议”均为历史快照，不是 chatbox v0 指令。当前工程权威见 [`docs/chatbox/phase-plan-v0.md`](../docs/chatbox/phase-plan-v0.md)；身份边界见 [`docs/design/README.md`](../docs/design/README.md)。

# Live2D 对话演示架构 - 快速参考

> 本文档是完整架构设计的快速参考版本，适合开发时快速查阅。

---

## 📋 核心决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 通信协议 | WebSocket + HTTP | WebSocket 实时对话，HTTP 资源加载 |
| 前端框架 | Vue 3 / React + TypeScript | 组件化、类型安全 |
| Live2D SDK | Cubism SDK R5 | 官方支持、功能完整 |
| 后端框架 | FastAPI | 异步支持、WebSocket 原生 |
| 语音合成 | GPT-SoVITS | 现有模块对接 |
| 音频格式 | PCM (流式) | 低延迟、易处理 |

---

## 🔌 WebSocket 消息类型

### 客户端 → 服务端

```typescript
type ClientMessage =
  | { type: 'user_message'; payload: { message_id, content, timestamp, session_id } }
  | { type: 'typing_start'; payload: { session_id } }
  | { type: 'typing_end'; payload: { session_id } }
  | { type: 'ping'; payload: { timestamp } }
```

### 服务端 → 客户端

```typescript
type ServerMessage =
  | { type: 'character_reply'; payload: { message_id, content, emotion, expression_id, motion_id?, timestamp, in_reply_to } }
  | { type: 'expression_update'; payload: { expression_id, blend_time, priority } }
  | { type: 'motion_trigger'; payload: { motion_id, loop, duration? } }
  | { type: 'audio_chunk'; /* 二进制消息 */ }
  | { type: 'pong'; payload: { timestamp, server_time } }
  | { type: 'error'; payload: { code, message } }
```

---

## 🎭 情感类型

```typescript
type EmotionType = 
  | 'neutral'    // 中性 (默认)
  | 'happy'      // 开心
  | 'sad'        // 悲伤
  | 'angry'      // 生气
  | 'surprised'  // 惊讶
  | 'shy'        // 害羞
  | 'thinking';  // 思考
```

### 关键词映射 (简化版)

```python
EMOTION_KEYWORDS = {
    'happy': ['开心', '高兴', '哈哈', '笑', '好棒', '喜欢'],
    'sad': ['难过', '伤心', '哭', '遗憾', '抱歉'],
    'angry': ['生气', '讨厌', '烦', '怒'],
    'surprised': ['惊讶', '哇', '真的吗', '没想到'],
    'shy': ['害羞', '不好意思', '尴尬'],
    'thinking': ['嗯...', '让我想想', '思考'],
}
```

---

## 🏗️ 前端组件结构

```
src/
├── components/
│   ├── Live2D/
│   │   ├── Live2DCanvas.vue      # 渲染画布
│   │   ├── ExpressionManager.ts  # 表情管理
│   │   └── MotionManager.ts      # 动作管理
│   ├── Chat/
│   │   ├── MessageList.vue       # 消息列表
│   │   ├── MessageBubble.vue     # 消息气泡
│   │   └── ChatInput.vue         # 输入框
│   └── common/
│       └── StatusBar.vue         # 状态栏
├── services/
│   ├── WebSocketClient.ts        # WS 客户端
│   ├── AudioPlayer.ts            # 音频播放
│   └── API.ts                    # HTTP API
├── stores/
│   ├── chatStore.ts              # 对话状态
│   ├── characterStore.ts         # 角色状态
│   └── uiStore.ts                # UI 状态
└── types/
    └── index.ts                  # TypeScript 类型
```

---

## 🐍 后端模块结构

```
backend/
├── app/
│   ├── main.py                   # FastAPI 入口
│   ├── websocket/
│   │   ├── handler.py            # WS 消息处理
│   │   ├── manager.py            # 会话管理
│   │   └── messages.py           # 消息定义
│   ├── services/
│   │   ├── memory.py             # 记忆服务
│   │   ├── character.py          # 角色服务
│   │   ├── voice.py              # 语音服务 (GPT-SoVITS)
│   │   ├── emotion.py            # 情感分析
│   │   └── llm.py                # LLM 对接
│   ├── models/
│   │   ├── message.py            # 消息模型
│   │   ├── character.py          # 角色模型
│   │   └── session.py            # 会话模型
│   └── api/
│       ├── routes/
│       │   ├── character.py      # 角色 API
│       │   ├── messages.py       # 消息 API
│       │   └── voice.py          # 语音 API
│       └── deps.py               # 依赖注入
└── config.py                     # 配置
```

---

## 📡 HTTP API 端点

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/api/v1/character/{id}/config` | 获取角色配置 |
| GET | `/api/v1/sessions/{id}/messages` | 获取对话历史 |
| GET | `/api/v1/character/{id}/memory/summary` | 获取记忆摘要 |
| POST | `/api/v1/voice/synthesize` | 语音合成 (非流式) |
| WS | `/ws` | WebSocket 连接 |

---

## 🔄 对话处理流程

```python
async def handle_user_message(websocket, payload):
    # 1. 保存用户消息
    await memory.save_message(...)
    
    # 2. 查询上下文
    context = await memory.get_context(...)
    character = await character_service.get_character(...)
    
    # 3. 生成回复
    reply_text = await llm.generate_reply(character, context, payload['content'])
    
    # 4. 情感分析
    emotion = await emotion_analyzer.analyze(reply_text)
    expression_id = character.map_emotion_to_expression(emotion)
    
    # 5. 发送回复
    await websocket.send_json({
        'type': 'character_reply',
        'payload': {
            'content': reply_text,
            'emotion': emotion,
            'expression_id': expression_id,
        }
    })
    
    # 6. 异步语音合成
    asyncio.create_task(synthesize_and_send_audio(...))
    
    # 7. 保存角色回复
    await memory.save_message(...)
```

---

## 🎨 Live2D 表情/动作配置

```json
{
  "expressions": {
    "neutral": "exp/neutral.exp3.json",
    "happy": "exp/happy.exp3.json",
    "sad": "exp/sad.exp3.json",
    "angry": "exp/angry.exp3.json",
    "surprised": "exp/surprised.exp3.json",
    "shy": "exp/shy.exp3.json",
    "thinking": "exp/thinking.exp3.json"
  },
  "motions": {
    "idle": "motion/idle.motion3.json",
    "speak": "motion/speak.motion3.json",
    "tap_head": "motion/tap_head.motion3.json",
    "tap_body": "motion/tap_body.motion3.json",
    "nod": "motion/nod.motion3.json",
    "shake": "motion/shake.motion3.json"
  }
}
```

---

## 🔊 音频流格式

```typescript
// WebSocket 二进制消息格式
// [4 字节：JSON 头长度][N 字节：JSON 头][剩余：音频数据]

interface AudioChunkHeader {
  type: 'audio_chunk';
  chunk_id: number;        // 从 0 开始
  total_chunks: number;    // 总分块数
  format: 'pcm';           // 音频格式
  sample_rate: 24000;      // 采样率
  channels: 1;             // 声道数
  is_last: boolean;        // 是否最后一块
}
```

### 前端播放示例

```typescript
class AudioPlayer {
  private audioContext: AudioContext;
  private queue: Float32Array[] = [];
  private isPlaying = false;
  
  async play(stream: ReadableStream<Uint8Array>) {
    for await (const chunk of stream) {
      const { header, audio } = parseAudioChunk(chunk);
      this.queue.push(new Float32Array(audio));
      
      if (!this.isPlaying) {
        this.processQueue();
      }
    }
  }
  
  private async processQueue() {
    this.isPlaying = true;
    while (this.queue.length > 0) {
      const data = this.queue.shift()!;
      await this.playBuffer(data);
    }
    this.isPlaying = false;
  }
}
```

---

## 🔐 安全考虑

1. **认证**: WebSocket 连接需要 Token (JWT)
2. **限流**: 消息频率限制 (如 10 条/分钟)
3. **内容过滤**: 输入/输出内容审核
4. **CORS**: 限制允许的源
5. **速率限制**: TTS 请求限流 (避免并发过高)

---

## ⚡ 性能优化

| 优化点 | 方案 |
|--------|------|
| Live2D 渲染 | `requestAnimationFrame` + 离屏暂停 |
| 音频播放 | `AudioWorklet` + 预缓冲 2-3 块 |
| WebSocket | 心跳 30s + 断线重连 (指数退避) |
| 记忆查询 | Redis 缓存 |
| TTS 请求 | 限流 + 队列 |

---

## 📦 依赖推荐

### 前端

```json
{
  "dependencies": {
    "vue": "^3.4.0",
    "pinia": "^2.1.0",
    "pixi.js": "^7.0.0",
    "live2d-widget": "^0.9.0"
  }
}
```

### 后端

```txt
fastapi>=0.100.0
uvicorn>=0.23.0
websockets>=11.0
aiohttp>=3.8.0
redis>=4.5.0
```

---

## 🚀 开发路线

### Phase 1: 基础通信 (1-2 周)
- [ ] WebSocket 连接建立
- [ ] 基础消息收发
- [ ] 简单对话 UI

### Phase 2: Live2D 集成 (1-2 周)
- [ ] 模型加载
- [ ] 表情切换
- [ ] 基础动作

### Phase 3: 后端对接 (2-3 周)
- [ ] Memory 模块接口
- [ ] Character 模块接口
- [ ] LLM 回复生成

### Phase 4: 语音集成 (1-2 周)
- [ ] GPT-SoVITS 对接
- [ ] 流式音频播放
- [ ] 音画同步

### Phase 5: 优化完善 (1-2 周)
- [ ] 断线重连
- [ ] 性能优化
- [ ] 错误处理
- [ ] 用户体验

---

## 📁 相关文件

| 文件 | 描述 |
|------|------|
| `live2d-dialogue-architecture.md` | 完整架构设计文档 |
| `api-interfaces.ts` | TypeScript 接口定义 |
| `backend_interfaces.py` | Python 后端接口定义 |
| `data-flow-diagrams.md` | 数据流图 |
| `ARCHITECTURE-SUMMARY.md` | 本文档 (快速参考) |

---

*最后更新：2026-03-11*
