Status: HISTORICAL
Authority-Scope: Pre-chatbox Live2D/demo architecture historical snapshot
Superseded-By: docs/chatbox/phase-plan-v0.md
Last-Verified: 2026-07-14

> **HISTORICAL — 非当前指令：**正文中的“完整架构”“快速开始”“开发路线”和“协议”均为历史快照，不是 chatbox v0 指令。当前工程权威见 [`docs/chatbox/phase-plan-v0.md`](../docs/chatbox/phase-plan-v0.md)；身份边界见 [`docs/design/README.md`](../docs/design/README.md)。

# Live2D 对话演示技术架构设计

## 1. 系统概述

### 1.1 架构目标
构建一个 Web 端 2D Live2D 虚拟角色对话系统，实现用户与具备记忆、人格、情感表达的虚拟角色进行自然对话。

### 1.2 技术栈
- **前端**: HTML5 + TypeScript + Live2D Cubism SDK + Vue.js/React
- **后端**: Python (FastAPI/Flask) + WebSocket
- **语音**: GPT-SoVITS (TTS)
- **通信**: WebSocket (实时) + HTTP REST (资源获取)

---

## 2. 前后端交互方案

### 2.1 通信协议设计

采用 **WebSocket + HTTP 混合架构**:

| 通信类型 | 协议 | 用途 | 理由 |
|---------|------|------|------|
| 对话消息 | WebSocket | 用户消息发送、角色回复推送 | 低延迟、双向实时 |
| 表情/动作状态 | WebSocket | 表情切换、动作触发通知 | 实时同步 |
| 语音流 | WebSocket (二进制) | TTS 音频流传输 | 流式传输、低延迟 |
| 资源加载 | HTTP REST | Live2D 模型、配置、历史记录 | 标准缓存、CDN 友好 |
| 记忆查询 | HTTP REST | 历史对话、角色设定 | 按需加载 |

### 2.2 WebSocket 连接管理

```
┌─────────────┐                    ┌─────────────┐
│   Frontend  │                    │   Backend   │
│             │  ── Connect ──→    │             │
│             │  ←── Auth OK ──    │             │
│             │  ── Send Msg ──→   │             │
│             │  ←── Reply ────    │             │
│             │  ←── Expression ─  │             │
│             │  ←── Audio Chunk ─ │             │
└─────────────┘                    └─────────────┘
```

### 2.3 数据格式定义

#### 2.3.1 用户消息 (Client → Server)

```typescript
interface UserMessage {
  type: 'user_message';
  payload: {
    message_id: string;        // UUID
    content: string;           // 用户输入文本
    timestamp: number;         // Unix 时间戳 (ms)
    session_id: string;        // 会话 ID
  };
}
```

#### 2.3.2 角色回复 (Server → Client)

```typescript
interface CharacterReply {
  type: 'character_reply';
  payload: {
    message_id: string;        // UUID
    content: string;           // 回复文本
    emotion: EmotionType;      // 当前情感状态
    expression_id: string;     // 表情 ID
    motion_id?: string;        // 动作 ID (可选)
    timestamp: number;         // Unix 时间戳 (ms)
    in_reply_to: string;       // 回复的消息 ID
  };
}
```

#### 2.3.3 表情/动作控制 (Server → Client)

```typescript
interface ExpressionUpdate {
  type: 'expression_update';
  payload: {
    expression_id: string;     // 表情 ID
    blend_time: number;        // 过渡时间 (ms)
    priority: number;          // 优先级 (高优先级可打断)
  };
}

interface MotionTrigger {
  type: 'motion_trigger';
  payload: {
    motion_id: string;         // 动作 ID
    loop: boolean;             // 是否循环
    duration?: number;         // 持续时间 (ms)
  };
}
```

#### 2.3.4 语音流 (Server → Client)

```typescript
// 二进制帧格式
interface AudioFrame {
  type: 'audio_chunk';         // WebSocket 二进制消息
  payload: {
    chunk_id: number;          // 分块序号
    total_chunks: number;      // 总分块数
    format: 'pcm' | 'mp3' | 'ogg';
    sample_rate: number;       // 采样率
    data: ArrayBuffer;         // 音频数据
  };
}
```

#### 2.3.5 情感类型定义

```typescript
type EmotionType = 
  | 'neutral'    // 中性
  | 'happy'      // 开心
  | 'sad'        // 悲伤
  | 'angry'      // 生气
  | 'surprised'  // 惊讶
  | 'shy'        // 害羞
  | 'thinking';  // 思考
```

---

## 3. 前端架构设计

### 3.1 整体结构

```
┌─────────────────────────────────────────────────────────┐
│                      Frontend (Web)                      │
├─────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌───────────────────────────────┐ │
│  │  Live2D 渲染层   │  │        对话 UI 层              │ │
│  │  ┌───────────┐  │  │  ┌─────────────────────────┐  │ │
│  │  │ Canvas    │  │  │  │  消息气泡列表            │  │ │
│  │  │ (WebGL)   │  │  │  │  ┌─────────────────────┐│  │ │
│  │  ├───────────┤  │  │  │  │  输入框 + 发送按钮   ││  │ │
│  │  │ 模型控制器 │  │  │  │  └─────────────────────┘│  │ │
│  │  ├───────────┤  │  │  └─────────────────────────┘  │ │
│  │  │ 表情管理器 │  │  │                               │ │
│  │  └───────────┘  │  │                               │ │
│  └─────────────────┘  └───────────────────────────────┘ │
├─────────────────────────────────────────────────────────┤
│                    WebSocket 通信层                       │
│              (消息收发 + 状态同步 + 音频流)                │
└─────────────────────────────────────────────────────────┘
```

### 3.2 Live2D 渲染层

#### 3.2.1 核心组件

```typescript
// Live2D 渲染器
class Live2DRenderer {
  private canvas: HTMLCanvasElement;
  private model: L2DModel;
  private motionManager: MotionManager;
  private expressionManager: ExpressionManager;
  
  async loadModel(modelPath: string): Promise<void>;
  setExpression(expressionId: string, blendTime: number): void;
  triggerMotion(motionId: string, loop: boolean): void;
  render(): void;  // 每帧调用
}

// 表情管理器
class ExpressionManager {
  private expressions: Map<string, L2DExpression>;
  private currentExpression: string;
  
  loadExpression(id: string, path: string): void;
  setExpression(id: string, blendTime: number): void;
}

// 动作管理器
class MotionManager {
  private motions: Map<string, L2DMotion>;
  private currentMotion: string | null;
  
  loadMotion(id: string, path: string): void;
  triggerMotion(id: string, loop: boolean, duration?: number): void;
  update(deltaTime: number): void;
}
```

#### 3.2.2 表情/动作映射表

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
    "tap_head": "motion/tap_head.motion3.json",
    "tap_body": "motion/tap_body.motion3.json",
    "speak": "motion/speak.motion3.json",
    "nod": "motion/nod.motion3.json",
    "shake": "motion/shake.motion3.json"
  }
}
```

### 3.3 对话 UI 层

#### 3.3.1 组件结构 (Vue 示例)

```vue
<template>
  <div class="dialogue-container">
    <!-- Live2D 画布 -->
    <canvas ref="live2dCanvas" class="live2d-canvas"></canvas>
    
    <!-- 消息列表 -->
    <div class="message-list">
      <MessageBubble
        v-for="msg in messages"
        :key="msg.id"
        :message="msg"
        :is-user="msg.sender === 'user'"
      />
    </div>
    
    <!-- 输入区域 -->
    <div class="input-area">
      <textarea
        v-model="inputText"
        placeholder="输入消息..."
        @keydown.enter.exact="sendMessage"
      />
      <button @click="sendMessage" :disabled="isSending">
        <span v-if="isSending">发送中...</span>
        <span v-else>发送</span>
      </button>
    </div>
  </div>
</template>
```

#### 3.3.2 消息数据结构

```typescript
interface Message {
  id: string;
  sender: 'user' | 'character';
  content: string;
  timestamp: number;
  emotion?: EmotionType;      // 仅角色消息
  audioUrl?: string;          // 语音 URL (可选)
  status: 'sending' | 'sent' | 'error';
}
```

### 3.4 表情/动作控制逻辑

#### 3.4.1 语义到表情映射

```typescript
// 基于关键词的简单映射 (可扩展为 ML 模型)
const EMOTION_KEYWORDS: Record<EmotionType, string[]> = {
  happy: ['开心', '高兴', '哈哈', '笑', '好棒', '喜欢'],
  sad: ['难过', '伤心', '哭', '遗憾', '抱歉'],
  angry: ['生气', '讨厌', '烦', '怒'],
  surprised: ['惊讶', '哇', '真的吗', '没想到'],
  shy: ['害羞', '不好意思', '尴尬'],
  thinking: ['嗯...', '让我想想', '思考'],
  neutral: []  // 默认
};

function detectEmotion(text: string): EmotionType {
  for (const [emotion, keywords] of Object.entries(EMOTION_KEYWORDS)) {
    if (keywords.some(kw => text.includes(kw))) {
      return emotion as EmotionType;
    }
  }
  return 'neutral';
}
```

#### 3.4.2 表情状态机

```typescript
enum ExpressionState {
  IDLE = 'idle',
  SPEAKING = 'speaking',
  LISTENING = 'listening',
  THINKING = 'thinking',
  TRANSITION = 'transition'
}

class ExpressionStateMachine {
  private state: ExpressionState = ExpressionState.IDLE;
  private emotionStack: EmotionType[] = [];
  
  onUserMessage(): void {
    this.state = ExpressionState.LISTENING;
    this.setExpression('neutral');
  }
  
  onThinking(): void {
    this.state = ExpressionState.THINKING;
    this.setExpression('thinking');
  }
  
  onReply(emotion: EmotionType): void {
    this.state = ExpressionState.SPEAKING;
    this.emotionStack.push(emotion);
    this.setExpression(emotion);
    this.triggerMotion('speak', true);
  }
  
  onSpeakingEnd(): void {
    this.state = ExpressionState.IDLE;
    this.emotionStack.pop();
    const baseEmotion = this.emotionStack[0] || 'neutral';
    this.setExpression(baseEmotion);
    this.stopMotion('speak');
  }
}
```

---

## 4. 后端接口设计

### 4.1 API 概览

```
┌─────────────────────────────────────────────────────────┐
│                    Backend (Python)                      │
├─────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
│  │  对话处理    │  │  表情触发    │  │   语音合成      │  │
│  │  (memory)   │  │ (character) │  │  (GPT-SoVITS)   │  │
│  │             │  │             │  │                 │  │
│  │  - 接收消息  │  │  - 语义分析  │  │  - 文本转语音   │  │
│  │  - 查记忆    │  │  - 情感识别  │  │  - 流式输出     │  │
│  │  - 生成回复  │  │  - 表情映射  │  │  - 音频编码     │  │
│  └─────────────┘  └─────────────┘  └─────────────────┘  │
├─────────────────────────────────────────────────────────┤
│              WebSocket Server + HTTP REST API            │
└─────────────────────────────────────────────────────────┘
```

### 4.2 HTTP REST API

#### 4.2.1 获取角色配置

```
GET /api/v1/character/{character_id}/config

Response:
{
  "character_id": "char_001",
  "name": "角色名称",
  "model_path": "/models/character_001/",
  "default_emotion": "neutral",
  "voice_id": "voice_001",
  "personality": "角色人格描述..."
}
```

#### 4.2.2 获取对话历史

```
GET /api/v1/sessions/{session_id}/messages?limit=50&before={timestamp}

Response:
{
  "messages": [
    {
      "id": "msg_001",
      "sender": "user",
      "content": "你好",
      "timestamp": 1709999999000
    },
    {
      "id": "msg_002",
      "sender": "character",
      "content": "你好呀!",
      "emotion": "happy",
      "timestamp": 1710000000000
    }
  ],
  "has_more": true
}
```

#### 4.2.3 获取记忆摘要

```
GET /api/v1/character/{character_id}/memory/summary

Response:
{
  "character_id": "char_001",
  "user_name": "用户昵称",
  "relationship": "朋友",
  "key_memories": [
    "第一次见面在...",
    "用户喜欢...",
    "共同经历..."
  ],
  "last_interaction": 1709999999000
}
```

### 4.3 WebSocket 消息处理

#### 4.3.1 服务端消息路由

```python
class WebSocketHandler:
    async def on_message(self, websocket: WebSocket, message: dict):
        msg_type = message.get('type')
        
        if msg_type == 'user_message':
            await self.handle_user_message(websocket, message['payload'])
        elif msg_type == 'ping':
            await websocket.send_json({'type': 'pong'})
        elif msg_type == 'typing_start':
            await self.broadcast_typing_status(websocket, True)
        elif msg_type == 'typing_end':
            await self.broadcast_typing_status(websocket, False)
```

#### 4.3.2 对话处理流程

```python
async def handle_user_message(self, websocket: WebSocket, payload: dict):
    # 1. 保存用户消息
    await self.memory.save_message(
        session_id=payload['session_id'],
        sender='user',
        content=payload['content'],
        timestamp=payload['timestamp']
    )
    
    # 2. 查询记忆和角色设定
    context = await self.memory.get_context(
        session_id=payload['session_id'],
        limit=10
    )
    character = await self.character.get_character(payload['character_id'])
    
    # 3. 生成回复 (调用 LLM)
    reply_text = await self.llm.generate_reply(
        character=character,
        context=context,
        user_message=payload['content']
    )
    
    # 4. 情感分析
    emotion = await self.emotion_analyzer.analyze(reply_text)
    expression_id = self.character.map_emotion_to_expression(emotion)
    
    # 5. 发送回复
    reply_message = {
        'type': 'character_reply',
        'payload': {
            'message_id': str(uuid.uuid4()),
            'content': reply_text,
            'emotion': emotion,
            'expression_id': expression_id,
            'timestamp': int(time.time() * 1000),
            'in_reply_to': payload['message_id']
        }
    }
    await websocket.send_json(reply_message)
    
    # 6. 触发语音合成 (异步)
    asyncio.create_task(
        self.synthesize_and_send_audio(
            websocket=websocket,
            text=reply_text,
            voice_id=character['voice_id']
        )
    )
    
    # 7. 保存角色回复
    await self.memory.save_message(
        session_id=payload['session_id'],
        sender='character',
        content=reply_text,
        emotion=emotion,
        timestamp=reply_message['payload']['timestamp']
    )
```

### 4.4 表情触发模块

```python
class EmotionAnalyzer:
    """基于语义的情感分析"""
    
    async def analyze(self, text: str) -> EmotionType:
        # 方案 1: 规则匹配 (简单快速)
        emotion = self.keyword_match(text)
        if emotion:
            return emotion
        
        # 方案 2: 调用 LLM 分析 (更准确)
        emotion = await self.llm_analyze(text)
        return emotion
    
    def keyword_match(self, text: str) -> Optional[EmotionType]:
        for emotion, keywords in EMOTION_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                return emotion
        return None
    
    async def llm_analyze(self, text: str) -> EmotionType:
        prompt = f"""分析以下文本的情感，返回 JSON:
        文本：{text}
        情感选项：neutral, happy, sad, angry, surprised, shy, thinking
        """
        result = await self.llm.complete(prompt)
        return parse_emotion_from_json(result)


class CharacterManager:
    """角色状态管理"""
    
    def map_emotion_to_expression(self, emotion: EmotionType) -> str:
        mapping = {
            'neutral': 'exp_neutral',
            'happy': 'exp_happy',
            'sad': 'exp_sad',
            'angry': 'exp_angry',
            'surprised': 'exp_surprised',
            'shy': 'exp_shy',
            'thinking': 'exp_thinking'
        }
        return mapping.get(emotion, 'exp_neutral')
    
    async def get_expression_params(self, expression_id: str) -> dict:
        """获取表情参数 (用于 Live2D)"""
        return await self.db.query(
            "SELECT * FROM expressions WHERE id = ?",
            (expression_id,)
        )
```

### 4.5 语音合成 (GPT-SoVITS 对接)

```python
class VoiceSynthesizer:
    """GPT-SoVITS TTS 封装"""
    
    def __init__(self, sovits_server_url: str):
        self.server_url = sovits_server_url
        self.session = aiohttp.ClientSession()
    
    async def synthesize_streaming(
        self,
        text: str,
        voice_id: str,
        websocket: WebSocket
    ) -> None:
        """流式合成并发送音频"""
        
        async with self.session.post(
            f"{self.server_url}/api/v1/tts/stream",
            json={
                'text': text,
                'speaker': voice_id,
                'format': 'pcm',
                'sample_rate': 24000
            }
        ) as response:
            chunk_id = 0
            total_chunks = 0
            
            async for chunk in response.content.iter_chunked(4096):
                if chunk_id == 0:
                    # 第一块包含元数据
                    header = json.loads(chunk.split(b'\n')[0])
                    total_chunks = header['total_chunks']
                
                audio_frame = {
                    'type': 'audio_chunk',
                    'chunk_id': chunk_id,
                    'total_chunks': total_chunks,
                    'format': 'pcm',
                    'sample_rate': 24000,
                    'data': chunk
                }
                
                await websocket.send_bytes(
                    json.dumps(audio_frame).encode() + b'\n' + chunk
                )
                chunk_id += 1
```

---

## 5. 数据流图

### 5.1 完整对话数据流

```
┌──────────┐                              ┌──────────┐
│   用户    │                              │  后端服务  │
│  (前端)   │                              │  (Python) │
└────┬─────┘                              └────┬─────┘
     │                                         │
     │  1. 输入消息                             │
     │  ─────────────────────────────────────→ │
     │                                         │
     │                                         │ 2. 保存消息到 memory
     │                                         │    ↓
     │                                         │ 3. 查询历史记忆
     │                                         │    ↓
     │                                         │ 4. 调用 LLM 生成回复
     │                                         │    ↓
     │                                         │ 5. 情感分析 → 表情 ID
     │                                         │
     │  6. 推送回复 (文本 + 表情)                 │
     │  ←───────────────────────────────────── │
     │                                         │
     │  7. 切换表情 (Live2D)                    │
     │     ──────────→ [Live2D 渲染]             │
     │                                         │
     │  8. 推送语音流 (分块)                     │
     │  ←───────────────────────────────────── │ 9. GPT-SoVITS 流式合成
     │                                         │    ←──────────────┐
     │  9. 播放音频                             │                   │
     │     ──────────→ [AudioContext]           │                   │
     │                                         │                   │
     │  10. 消息上屏                            │                   │
     │      ──────────→ [消息气泡]               │                   │
     │                                         │                   │
     └─────────────────────────────────────────┘                   │
                                                                   │
                              ┌────────────────────────────────────┘
                              │
                         ┌────┴─────┐
                         │ GPT-SoVITS│
                         │  TTS 服务  │
                         └──────────┘
```

### 5.2 状态同步时序图

```
时间轴 ─────────────────────────────────────────────────────→

前端                          后端                         Live2D
 │                             │                             │
 │── user_message ───────────→│                             │
 │                             │                             │
 │                             │── [思考中] ────────────────→│ setExpression('thinking')
 │                             │                             │
 │←─ expression_update ────────│ (expression: 'thinking')    │
 │                             │                             │
 │←─ character_reply ──────────│ (content + emotion)         │
 │  setExpression('happy') ──→│                             │
 │                             │                             │
 │←─ audio_chunk (1) ──────────│                             │
 │←─ audio_chunk (2) ──────────│                             │
 │←─ audio_chunk (N) ──────────│                             │
 │  playAudio() ────────────→  │                             │
 │                             │                             │
 │←─ expression_update ────────│ (expression: 'neutral')     │
 │  setExpression('neutral') ─→│                             │
 │                             │                             │
```

### 5.3 模块依赖关系

```
┌─────────────────────────────────────────────────────────────┐
│                         Frontend                             │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ Live2D SDK   │  │ Vue/React    │  │ WebSocket Client │   │
│  │ (渲染引擎)    │  │ (UI 框架)     │  │ (通信层)          │   │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘   │
│         │                 │                    │             │
│         └─────────────────┴────────────────────┘             │
│                           │                                  │
└───────────────────────────┼──────────────────────────────────┘
                            │ WebSocket + HTTP
┌───────────────────────────┼──────────────────────────────────┐
│                           ▼                                  │
│                         Backend                              │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ Memory Module│  │ Character    │  │ Voice Module     │   │
│  │ (记忆存储)    │  │ Module       │  │ (GPT-SoVITS)     │   │
│  │              │  │ (角色管理)    │  │                  │   │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘   │
│         │                 │                    │             │
│         └─────────────────┴────────────────────┘             │
│                           │                                  │
│                  ┌────────┴────────┐                         │
│                  │   LLM Service   │                         │
│                  │   (回复生成)     │                         │
│                  └─────────────────┘                         │
└─────────────────────────────────────────────────────────────┘
```

---

## 6. 接口定义草案

### 6.1 WebSocket 消息完整定义

```typescript
// ============ 客户端 → 服务端 ============

// 用户发送消息
interface UserMessage {
  type: 'user_message';
  payload: {
    message_id: string;
    content: string;
    timestamp: number;
    session_id: string;
  };
}

// 开始输入
interface TypingStart {
  type: 'typing_start';
  payload: {
    session_id: string;
  };
}

// 结束输入
interface TypingEnd {
  type: 'typing_end';
  payload: {
    session_id: string;
  };
}

// 心跳
interface Ping {
  type: 'ping';
  payload: {
    timestamp: number;
  };
}

// ============ 服务端 → 客户端 ============

// 角色回复
interface CharacterReply {
  type: 'character_reply';
  payload: {
    message_id: string;
    content: string;
    emotion: EmotionType;
    expression_id: string;
    motion_id?: string;
    timestamp: number;
    in_reply_to: string;
  };
}

// 表情更新
interface ExpressionUpdate {
  type: 'expression_update';
  payload: {
    expression_id: string;
    blend_time: number;
    priority: number;
  };
}

// 动作触发
interface MotionTrigger {
  type: 'motion_trigger';
  payload: {
    motion_id: string;
    loop: boolean;
    duration?: number;
  };
}

// 音频分块 (二进制消息)
interface AudioChunk {
  type: 'audio_chunk';
  chunk_id: number;
  total_chunks: number;
  format: 'pcm' | 'mp3' | 'ogg';
  sample_rate: number;
  data: ArrayBuffer;
}

// 心跳响应
interface Pong {
  type: 'pong';
  payload: {
    timestamp: number;
    server_time: number;
  };
}

// 错误消息
interface ErrorMessage {
  type: 'error';
  payload: {
    code: string;
    message: string;
    details?: any;
  };
}
```

### 6.2 HTTP REST API 完整定义

```yaml
openapi: 3.0.0
info:
  title: Live2D Dialogue API
  version: 1.0.0

paths:
  /api/v1/character/{character_id}/config:
    get:
      summary: 获取角色配置
      parameters:
        - name: character_id
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: 角色配置
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/CharacterConfig'

  /api/v1/sessions/{session_id}/messages:
    get:
      summary: 获取对话历史
      parameters:
        - name: session_id
          in: path
          required: true
          schema:
            type: string
        - name: limit
          in: query
          schema:
            type: integer
            default: 50
        - name: before
          in: query
          schema:
            type: integer
          description: Unix 时间戳，获取此时间之前的消息
      responses:
        '200':
          description: 消息列表
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/MessageList'

  /api/v1/character/{character_id}/memory/summary:
    get:
      summary: 获取记忆摘要
      parameters:
        - name: character_id
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: 记忆摘要
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/MemorySummary'

  /api/v1/voice/synthesize:
    post:
      summary: 语音合成 (非流式)
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                text:
                  type: string
                voice_id:
                  type: string
                format:
                  type: string
                  enum: [pcm, mp3, ogg]
      responses:
        '200':
          description: 音频文件
          content:
            audio/wav:
              schema:
                type: string
                format: binary

components:
  schemas:
    CharacterConfig:
      type: object
      properties:
        character_id:
          type: string
        name:
          type: string
        model_path:
          type: string
        default_emotion:
          type: string
        voice_id:
          type: string
        personality:
          type: string

    MessageList:
      type: object
      properties:
        messages:
          type: array
          items:
            $ref: '#/components/schemas/Message'
        has_more:
          type: boolean

    Message:
      type: object
      properties:
        id:
          type: string
        sender:
          type: string
          enum: [user, character]
        content:
          type: string
        timestamp:
          type: integer
        emotion:
          type: string
          nullable: true

    MemorySummary:
      type: object
      properties:
        character_id:
          type: string
        user_name:
          type: string
        relationship:
          type: string
        key_memories:
          type: array
          items:
            type: string
        last_interaction:
          type: integer
```

---

## 7. 实现建议

### 7.1 技术选型建议

| 模块 | 推荐方案 | 备选方案 |
|------|---------|---------|
| Live2D SDK | Cubism SDK for Web R5 | PixiJS + Live2D 插件 |
| UI 框架 | Vue 3 + TypeScript | React + TypeScript |
| 状态管理 | Pinia (Vue) / Zustand (React) | Redux |
| WebSocket | 原生 WebSocket API | Socket.io |
| 音频播放 | Web Audio API | Howler.js |
| 后端框架 | FastAPI | Flask + Flask-SocketIO |
| LLM | 现有 LLM 服务 | OpenAI API / 本地部署 |
| TTS | GPT-SoVITS | Azure TTS / ElevenLabs |

### 7.2 性能优化建议

1. **Live2D 渲染**
   - 使用 `requestAnimationFrame` 保持 60fps
   - 模型加载后缓存到内存
   - 离屏时暂停渲染

2. **音频流**
   - 使用 `AudioContext` 的 `ScriptProcessorNode` 或 `AudioWorklet`
   - 预缓冲 2-3 个音频块
   - 支持中断播放 (用户打断时)

3. **WebSocket**
   - 实现心跳机制 (30s 间隔)
   - 断线自动重连 (指数退避)
   - 消息队列 (网络恢复后重发)

4. **后端**
   - 使用异步 IO (asyncio)
   - 记忆查询加缓存 (Redis)
   - TTS 请求限流 (避免并发过高)

### 7.3 安全考虑

1. **认证**: WebSocket 连接需要 Token 认证
2. **限流**: 消息频率限制 (防止滥用)
3. **内容过滤**: 输入/输出内容审核
4. **CORS**: 限制允许的源

---

## 8. 下一步工作

1. **原型开发**
   - [ ] 搭建基础 WebSocket 通信
   - [ ] 集成 Live2D 模型渲染
   - [ ] 实现基础对话 UI

2. **后端对接**
   - [ ] 实现 memory 模块接口
   - [ ] 对接 character 模块
   - [ ] 集成 GPT-SoVITS

3. **功能完善**
   - [ ] 表情/动作系统
   - [ ] 语音播放优化
   - [ ] 断线重连机制

4. **测试优化**
   - [ ] 性能测试
   - [ ] 兼容性测试
   - [ ] 用户体验优化

---

*文档版本：1.0*
*创建时间：2026-03-11*
*作者：OpenClaw Subagent (demo-architect)*
