Status: HISTORICAL
Authority-Scope: Pre-chatbox Live2D/demo architecture historical snapshot
Superseded-By: docs/chatbox/phase-plan-v0.md
Last-Verified: 2026-07-14

> **HISTORICAL — 非当前指令：**正文中的“完整架构”“快速开始”“开发路线”和“协议”均为历史快照，不是 chatbox v0 指令。当前工程权威见 [`docs/chatbox/phase-plan-v0.md`](../docs/chatbox/phase-plan-v0.md)；身份边界见 [`docs/design/README.md`](../docs/design/README.md)。

# 情感控制协议 (Emotion Control Protocol)

## 设计目标

定义一套**底层控制协议**，用于 AI 角色系统 → 表演层 的通信。

**原则：**
- 简单、原始、直接
- 表演层拿到就能用
- 不依赖特定技术（Live2D/VRM/其他都能映射）
- 可扩展

---

## 核心数据结构

### 1. 控制帧 (ControlFrame)

```typescript
interface ControlFrame {
  timestamp: number;           // 时间戳 (ms)
  frame_id: string;            // 帧 ID
  
  // 面部控制
  face: FaceControl;
  
  // 头部控制
  head: HeadControl;
  
  // 身体控制（可选）
  body?: BodyControl;
  
  // 语音相关（可选）
  voice?: VoiceControl;
}
```

### 2. 面部控制 (FaceControl)

```typescript
interface FaceControl {
  // 眼睛
  eye_open_l: number;          // 左眼开合 0-1
  eye_open_r: number;          // 右眼开合 0-1
  eye_ball_x: number;          // 眼球 X -1 到 1
  eye_ball_y: number;          // 眼球 Y -1 到 1
  
  // 嘴巴
  mouth_open: number;          // 口型开合 0-1
  mouth_smile: number;         // 微笑 -1 到 1
  mouth_shape: number;         // 口型形状 0-1（用于 lip sync）
  
  // 眉毛
  brow_l_y: number;            // 左眉 Y -1 到 1
  brow_r_y: number;            // 右眉 Y -1 到 1
  
  // 脸颊
  cheek_puff: number;          // 鼓腮 0-1
}
```

### 3. 头部控制 (HeadControl)

```typescript
interface HeadControl {
  angle_x: number;             // 头部旋转 X -1 到 1
  angle_y: number;             // 头部旋转 Y -1 到 1
  angle_z: number;             // 头部旋转 Z -1 到 1
  position_x: number;          // 头部位置 X -1 到 1
  position_y: number;          // 头部位置 Y -1 到 1
}
```

### 4. 身体控制 (BodyControl)

```typescript
interface BodyControl {
  breath: number;              // 呼吸起伏 0-1
  shoulder_l: number;          // 左肩 -1 到 1
  shoulder_r: number;          // 右肩 -1 到 1
}
```

### 5. 语音控制 (VoiceControl)

```typescript
interface VoiceControl {
  volume: number;              // 音量 0-1
  pitch: number;               // 音高 0-1
  rate: number;                // 语速 0-1
  phoneme: string;             // 当前音素（用于精确 lip sync）
}
```

---

## 协议类型

### A. 参数模式 (Parameter Mode)

直接发送具体参数值：

```json
{
  "mode": "parameters",
  "data": {
    "eye_open_l": 0.9,
    "eye_open_r": 0.9,
    "mouth_open": 0.7,
    ...
  }
}
```

**优点：** 精确控制
**缺点：** 数据量大

---

### B. 预设模式 (Preset Mode)

发送预设 ID + 插值参数：

```json
{
  "mode": "preset",
  "data": {
    "expression": "happy",
    "intensity": 0.8,
    "blend_duration": 200
  }
}
```

**预设列表：**
- `neutral` - 中性
- `happy` - 开心
- `sad` - 悲伤
- `angry` - 愤怒
- `surprised` - 惊讶
- `thinking` - 思考
- `listening` - 倾听

**优点：** 简洁
**缺点：** 需要预定义

---

### C. 向量模式 (Vector Mode)

发送抽象向量，表演层自行映射：

```json
{
  "mode": "vector",
  "data": {
    "valence": 0.7,           // 愉悦度 -1 到 1
    "arousal": 0.5,           // 唤醒度 0-1
    "dominance": 0.3          // 支配度 0-1
  }
}
```

**优点：** 最抽象，最灵活
**缺点：** 表演层需要映射表

---

## 通信接口

### WebSocket 消息格式

```typescript
// AI → 表演层
interface ControlMessage {
  type: "control_frame";
  frame: ControlFrame;
}

interface PresetMessage {
  type: "preset";
  preset_id: string;
  intensity: number;
  duration_ms?: number;
}

interface VectorMessage {
  type: "vector";
  valence: number;
  arousal: number;
  dominance?: number;
}

// 表演层 → AI（可选反馈）
interface PerformanceState {
  type: "performance_state";
  current_expression: string;
  active_parameters: Record<string, number>;
}
```

---

## 表现层映射示例

### 映射到 Live2D

```javascript
function mapToLive2D(frame) {
  model.internalModel.coreModel.setParameterValueById(
    'ParamMouthOpenY', frame.face.mouth_open
  );
  model.internalModel.coreModel.setParameterValueById(
    'ParamEyeLOpen', frame.face.eye_open_l
  );
  // ...
}
```

### 映射到 VRM

```javascript
function mapToVRM(frame) {
  vrm.expressionManager.setValue(
    'happy', frame.face.mouth_smile
  );
  // ...
}
```

### 映射到简单矩形条（调试用）

```javascript
function mapToBars(frame) {
  document.getElementById('mouth-bar').style.height = 
    frame.face.mouth_open * 100 + 'px';
  // ...
}
```

### 映射到 Emoji

```javascript
function mapToEmoji(frame) {
  const { valence, arousal } = frame.vector;
  if (valence > 0.5 && arousal > 0.5) return '😄';
  if (valence < -0.5) return '😢';
  if (arousal > 0.7) return '😲';
  return '😐';
}
```

---

## 极简演示方案

### 方案 1：矩形条面板

```
┌─────────────────────────────────┐
│ 眼睛开合  [████████░░] 0.8     │
│ 嘴巴开合  [█████░░░░░] 0.5     │
│ 微笑      [██░░░░░░░░] 0.2     │
│ 眉毛      [░░░░░░░░░░] 0.0     │
│ 头部倾斜  [░░░██░░░░░] -0.1    │
└─────────────────────────────────┘
```

### 方案 2：Emoji + 文字

```
当前状态：😐 neutral
眼睛：0.9 | 嘴巴：0.1 | 眉毛：0.0
```

### 方案 3：Canvas 简单绘制

画一个圆（脸）+ 两条线（眼睛）+ 一条弧线（嘴巴）
用协议参数直接控制弧度和位置

---

## 下一步

1. 确定协议字段（可以精简）
2. 实现一个简单的演示面板
3. 手动发送控制信号测试
4. 后续对接 AI 情感引擎
