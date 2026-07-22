Status: HISTORICAL
Authority-Scope: Pre-chatbox Live2D/demo architecture historical snapshot
Superseded-By: docs/chatbox/phase-plan-v0.md
Last-Verified: 2026-07-14

> **HISTORICAL — 非当前指令：**正文中的“完整架构”“快速开始”“开发路线”和“协议”均为历史快照，不是 chatbox v0 指令。当前工程权威见 [`docs/chatbox/phase-plan-v0.md`](../docs/chatbox/phase-plan-v0.md)；身份边界见 [`docs/design/README.md`](../docs/design/README.md)。

# 矩阵控制协议 (Matrix Control Protocol)

## 设计目标

控制一个 **20x20 像素矩阵**，用图案表现角色情感状态。

**特点：**
- 简单、直观、可视化
- 可以预设情感图案（笑脸/沮丧/惊讶等）
- 支持动画（眨眼、说话、呼吸）
- 任何技术都能渲染（Canvas/DOM/LED/终端）

---

## 核心数据结构

### 1. 矩阵帧 (MatrixFrame)

```typescript
interface MatrixFrame {
  timestamp: number;           // 时间戳 (ms)
  frame_id: string;            // 帧 ID
  
  // 像素数据：20x20 = 400 个像素
  pixels: PixelData[];         // 长度 400
  
  // 或者用二维数组
  grid: number[][];            // 20x20, 值 0-1 表示亮度/颜色索引
}
```

### 2. 像素数据 (PixelData)

```typescript
interface PixelData {
  x: number;                   // 0-19
  y: number;                   // 0-19
  value: number;               // 0-1 亮度 或 颜色索引
  color?: {                    // 可选：RGB 颜色
    r: number;                 // 0-255
    g: number;
    b: number;
  };
}
```

---

## 情感预设图案

### 预设 ID 列表

| ID | 名称 | 描述 |
|----|------|------|
| `neutral` | 中性 | 平静表情 |
| `happy` | 开心 | 笑脸 |
| `sad` | 沮丧 | 嘴角向下 |
| `angry` | 愤怒 | 皱眉 |
| `surprised` | 惊讶 | 睁大眼睛 |
| `thinking` | 思考 | 眼睛看向一侧 |
| `listening` | 倾听 | 专注表情 |
| `excited` | 兴奋 | 大眼睛 + 红晕 |
| `sleeping` | 睡觉 | 闭眼 |
| `talking` | 说话 | 张嘴动画 |

---

## 图案示例（20x20）

### 😊 Happy (开心)

```
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 1 1 1 1 1 0 0 1 1 1 1 1 0 0 0 0
0 0 0 0 1 0 0 0 1 0 0 1 0 0 0 1 0 0 0 0
0 0 0 0 1 0 0 0 1 0 0 1 0 0 0 1 0 0 0 0
0 0 0 0 1 1 1 1 1 0 0 1 1 1 1 1 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 1 1 1 1 1 0 0 0 0 0 0
0 0 0 0 0 0 0 0 1 0 0 0 0 0 1 0 0 0 0 0
0 0 0 0 0 0 0 1 0 0 0 0 0 0 0 1 0 0 0 0
0 0 0 0 0 0 0 1 0 0 0 0 0 0 0 1 0 0 0 0
0 0 0 0 0 0 0 0 1 1 1 1 1 1 1 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
```

### 😢 Sad (沮丧)

```
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 1 1 1 1 1 0 0 1 1 1 1 1 0 0 0 0
0 0 0 0 1 0 0 0 1 0 0 1 0 0 0 1 0 0 0 0
0 0 0 0 1 0 0 0 1 0 0 1 0 0 0 1 0 0 0 0
0 0 0 0 1 1 1 1 1 0 0 1 1 1 1 1 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 1 1 1 1 1 1 1 0 0 0 0 0
0 0 0 0 0 0 0 1 0 0 0 0 0 0 0 1 0 0 0 0
0 0 0 0 0 0 0 1 0 0 0 0 0 0 0 1 0 0 0 0
0 0 0 0 0 0 0 0 1 0 0 0 0 0 1 0 0 0 0 0
0 0 0 0 0 0 0 0 0 1 1 1 1 1 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
```

### 😲 Surprised (惊讶)

```
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 1 1 1 1 1 0 0 1 1 1 1 1 0 0 0 0
0 0 0 0 1 0 0 0 1 0 0 1 0 0 0 1 0 0 0 0
0 0 0 0 1 0 0 0 1 0 0 1 0 0 0 1 0 0 0 0
0 0 0 0 1 1 1 1 1 0 0 1 1 1 1 1 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 1 1 1 1 1 0 0 0 0 0 0 0
0 0 0 0 0 0 0 1 0 0 0 0 0 1 0 0 0 0 0 0
0 0 0 0 0 0 0 1 0 0 0 0 0 1 0 0 0 0 0 0
0 0 0 0 0 0 0 1 0 0 0 0 0 1 0 0 0 0 0 0
0 0 0 0 0 0 0 1 0 0 0 0 0 1 0 0 0 0 0 0
0 0 0 0 0 0 0 0 1 0 0 0 0 1 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 1 1 1 1 0 0 0 0 0 0 0
0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
```

---

## 协议类型

### A. 预设模式 (Preset Mode)

```json
{
  "mode": "preset",
  "preset_id": "happy",
  "intensity": 0.8,        // 0-1 控制亮度/对比度
  "duration_ms": 2000      // 持续时间
}
```

### B. 帧模式 (Frame Mode)

```json
{
  "mode": "frame",
  "grid": [
    [0,0,0,...],           // 20 个值
    [0,0,0,...],
    ...                    // 共 20 行
  ]
}
```

### C. 动画模式 (Animation Mode)

```json
{
  "mode": "animation",
  "animation_id": "blink",
  "frames": [...],         // 帧数组
  "fps": 10,
  "loop": false
}
```

### D. 插值模式 (Blend Mode)

```json
{
  "mode": "blend",
  "from": "neutral",
  "to": "happy",
  "progress": 0.5,         // 0-1 插值进度
  "duration_ms": 300
}
```

---

## 内置动画

### 眨眼 (Blink)

```
帧 1 (睁眼): 正常眼睛图案
帧 2 (闭眼): 眼睛变成一条横线
帧 3 (睁眼): 恢复
```

**时序：** 100ms → 50ms → 100ms

### 说话 (Talking)

```
帧 1: 嘴巴闭合
帧 2: 嘴巴微张
帧 3: 嘴巴张开
帧 4: 嘴巴微张
循环...
```

**时序：** 根据语音节奏或固定 100ms/帧

### 呼吸 (Breathing)

```
整个矩阵亮度：0.8 → 1.0 → 0.8 循环
```

**时序：** 2 秒周期

---

## 通信接口

### WebSocket 消息

```typescript
// AI → 矩阵渲染层
interface MatrixControlMessage {
  type: "matrix_control";
  data: PresetMessage | FrameMessage | AnimationMessage;
}

// 渲染层 → AI（可选）
interface MatrixState {
  type: "matrix_state";
  current_preset: string;
  brightness: number;
}
```

---

## 渲染示例

### HTML Canvas

```javascript
const canvas = document.getElementById('matrix');
const ctx = canvas.getContext('2d');
const CELL_SIZE = 10;  // 每个像素 10x10px

function renderGrid(grid) {
  ctx.clearRect(0, 0, 200, 200);
  for (let y = 0; y < 20; y++) {
    for (let x = 0; x < 20; x++) {
      const value = grid[y][x];
      ctx.fillStyle = value > 0.5 ? '#0f0' : '#000';
      ctx.fillRect(x * CELL_SIZE, y * CELL_SIZE, CELL_SIZE - 1, CELL_SIZE - 1);
    }
  }
}
```

### 终端 ASCII

```javascript
function renderAscii(grid) {
  let output = '';
  for (let y = 0; y < 20; y++) {
    for (let x = 0; x < 20; x++) {
      output += grid[y][x] > 0.5 ? '██' : '  ';
    }
    output += '\n';
  }
  console.log(output);
}
```

### DOM 网格

```javascript
function renderDOM(grid, container) {
  container.innerHTML = '';
  container.style.display = 'grid';
  container.style.gridTemplateColumns = 'repeat(20, 1fr)';
  
  for (let y = 0; y < 20; y++) {
    for (let x = 0; x < 20; x++) {
      const cell = document.createElement('div');
      cell.style.width = '10px';
      cell.style.height = '10px';
      cell.style.backgroundColor = grid[y][x] > 0.5 ? 'green' : 'black';
      container.appendChild(cell);
    }
  }
}
```

---

## 极简演示页面

```html
<!DOCTYPE html>
<html>
<head>
  <title>20x20 Matrix Emotion Display</title>
</head>
<body>
  <h1>情感矩阵</h1>
  
  <canvas id="matrix" width="200" height="200"></canvas>
  
  <div id="controls">
    <button onclick="setPreset('happy')">😊 开心</button>
    <button onclick="setPreset('sad')">😢 沮丧</button>
    <button onclick="setPreset('angry')">😠 愤怒</button>
    <button onclick="setPreset('surprised')">😲 惊讶</button>
    <button onclick="setPreset('neutral')">😐 中性</button>
    <button onclick="animate('blink')">👁 眨眼</button>
    <button onclick="animate('talk')">🗣 说话</button>
  </div>
  
  <script>
    // 预设图案
    const PRESETS = {
      happy: [...],    // 20x20 数组
      sad: [...],
      angry: [...],
      surprised: [...],
      neutral: [...]
    };
    
    const canvas = document.getElementById('matrix');
    const ctx = canvas.getContext('2d');
    const CELL_SIZE = 10;
    
    function renderGrid(grid) {
      ctx.clearRect(0, 0, 200, 200);
      for (let y = 0; y < 20; y++) {
        for (let x = 0; x < 20; x++) {
          const value = grid[y][x];
          ctx.fillStyle = value > 0.5 ? '#0f0' : '#000';
          ctx.fillRect(x * CELL_SIZE, y * CELL_SIZE, CELL_SIZE - 1, CELL_SIZE - 1);
        }
      }
    }
    
    function setPreset(id) {
      renderGrid(PRESETS[id]);
    }
    
    function animate(type) {
      // 动画逻辑
    }
    
    // 初始显示中性
    setPreset('neutral');
  </script>
</body>
</html>
```

---

## 下一步

1. 定义所有情感预设图案（20x20 数组）
2. 实现一个简单 HTML 演示页面
3. 添加 WebSocket 接收控制消息
4. 测试图案显示和动画

---

## 附录：完整预设图案数据

（JSON 格式，可直接使用）

```json
{
  "neutral": [
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    ...
  ],
  "happy": [...],
  "sad": [...],
  ...
}
```
