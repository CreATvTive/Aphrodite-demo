# Live2D × AI 角色系统集成方案设计

## 1. 概述

本方案设计 AI 动态生成角色与 Live2D 视觉表现层的集成架构，实现：
- **人格特质** → **表情倾向** 的静态映射
- **对话情绪** → **实时表情** 的动态切换
- **语音输出** → **口型同步** 的实时驱动

---

## 2. 角色特征 → Live2D 参数映射

### 2.1 人格特质映射（静态配置）

人格特质决定角色的"默认表情倾向"和"参数变化范围"。

```typescript
interface PersonalityTraits {
  extraversion: number;      // 外向性 0-1
  agreeableness: number;     // 宜人性 0-1
  neuroticism: number;       // 神经质 0-1
  openness: number;          // 开放性 0-1
  energy: number;            // 活力值 0-1
}

interface Live2DParamBounds {
  // 表情参数范围限制
  eyeOpen: [number, number];      // 眼睛开合 [min, max]
  mouthSmile: [number, number];   // 笑容程度
  eyebrowAngle: [number, number]; // 眉毛角度
  bodyLean: [number, number];     // 身体倾斜
  blinkFrequency: number;         // 眨眼频率 (次/分钟)
}

// 人格 → 参数范围映射表
const PERSONALITY_MAPPING: Record<keyof PersonalityTraits, Partial<Live2DParamBounds>> = {
  extraversion: {
    mouthSmile: [0.3, 0.8],      // 外向者更多笑容
    eyeOpen: [0.6, 1.0],         // 眼睛更睁开
    blinkFrequency: 15,          // 眨眼较快
  },
  agreeableness: {
    mouthSmile: [0.4, 0.7],      // 温和笑容
    eyebrowAngle: [-0.2, 0.3],   // 眉毛柔和
  },
  neuroticism: {
    eyeOpen: [0.3, 0.7],         // 眼神更游离
    blinkFrequency: 25,          // 眨眼频繁
    bodyLean: [-0.3, 0.1],       // 身体内收
  },
  energy: {
    bodyLean: [-0.1, 0.4],       // 活力高时身体前倾
    blinkFrequency: 20,
  },
};
```

### 2.2 对话情绪映射（实时动态）

对话时的情绪状态驱动实时表情变化。

```typescript
enum EmotionType {
  NEUTRAL = 'neutral',
  HAPPY = 'happy',
  SAD = 'sad',
  ANGRY = 'angry',
  SURPRISED = 'surprised',
  EXCITED = 'excited',
  THINKING = 'thinking',
  LISTENING = 'listening',
}

interface EmotionExpression {
  emotion: EmotionType;
  duration: number;  // 持续时间 (ms)
  params: {
    ParamAngleX: number;    // 头部 X 角度
    ParamAngleY: number;    // 头部 Y 角度
    ParamAngleZ: number;    // 头部 Z 角度
    ParamEyeLOpen: number;  // 左眼开合
    ParamEyeROpen: number;  // 右眼开合
    ParamEyeLSmile: number; // 左眼笑容
    ParamEyeRSmile: number; // 右眼笑容
    ParamBrowLY: number;    // 左眉 Y 位置
    ParamBrowRY: number;    // 右眉 Y 位置
    ParamBrowLAngle: number;// 左眉角度
    ParamBrowRAngle: number;// 右眉角度
    ParamMouthOpenY: number;// 嘴巴开合
    ParamMouthForm: number; // 嘴巴形状
    ParamCheek: number;     // 脸颊红晕
  };
  transition: 'instant' | 'smooth' | 'fade';
}

// 情绪表情预设库
const EMOTION_EXPRESSIONS: Record<EmotionType, EmotionExpression> = {
  [EmotionType.NEUTRAL]: {
    emotion: 'neutral',
    duration: 0,  // 默认状态，无超时
    params: {
      ParamAngleX: 0, ParamAngleY: 0, ParamAngleZ: 0,
      ParamEyeLOpen: 0.8, ParamEyeROpen: 0.8,
      ParamEyeLSmile: 0, ParamEyeRSmile: 0,
      ParamBrowLY: 0, ParamBrowRY: 0,
      ParamBrowLAngle: 0, ParamBrowRAngle: 0,
      ParamMouthOpenY: 0, ParamMouthForm: 0,
      ParamCheek: 0,
    },
    transition: 'smooth',
  },
  [EmotionType.HAPPY]: {
    emotion: 'happy',
    duration: 3000,
    params: {
      ParamAngleX: 0, ParamAngleY: -5, ParamAngleZ: 5,
      ParamEyeLOpen: 0.9, ParamEyeROpen: 0.9,
      ParamEyeLSmile: 0.6, ParamEyeRSmile: 0.6,
      ParamBrowLY: 0.2, ParamBrowRY: 0.2,
      ParamBrowLAngle: 0.3, ParamBrowRAngle: 0.3,
      ParamMouthOpenY: 0.3, ParamMouthForm: 0.5,
      ParamCheek: 0.3,
    },
    transition: 'smooth',
  },
  [EmotionType.SAD]: {
    emotion: 'sad',
    duration: 4000,
    params: {
      ParamAngleX: 0, ParamAngleY: 10, ParamAngleZ: -5,
      ParamEyeLOpen: 0.5, ParamEyeROpen: 0.5,
      ParamEyeLSmile: 0, ParamEyeRSmile: 0,
      ParamBrowLY: -0.3, ParamBrowRY: -0.3,
      ParamBrowLAngle: -0.4, ParamBrowRAngle: -0.4,
      ParamMouthOpenY: 0, ParamMouthForm: -0.5,
      ParamCheek: 0,
    },
    transition: 'fade',
  },
  [EmotionType.EXCITED]: {
    emotion: 'excited',
    duration: 2000,
    params: {
      ParamAngleX: 5, ParamAngleY: -10, ParamAngleZ: 10,
      ParamEyeLOpen: 1.0, ParamEyeROpen: 1.0,
      ParamEyeLSmile: 0.8, ParamEyeRSmile: 0.8,
      ParamBrowLY: 0.5, ParamBrowRY: 0.5,
      ParamBrowLAngle: 0.5, ParamBrowRAngle: 0.5,
      ParamMouthOpenY: 0.6, ParamMouthForm: 0.7,
      ParamCheek: 0.5,
    },
    transition: 'instant',
  },
  [EmotionType.THINKING]: {
    emotion: 'thinking',
    duration: 0,  // 持续到状态切换
    params: {
      ParamAngleX: -10, ParamAngleY: 5, ParamAngleZ: 0,
      ParamEyeLOpen: 0.6, ParamEyeROpen: 0.6,
      ParamEyeLSmile: 0, ParamEyeRSmile: 0,
      ParamBrowLY: -0.1, ParamBrowRY: -0.1,
      ParamBrowLAngle: 0.2, ParamBrowRAngle: 0.2,
      ParamMouthOpenY: 0, ParamMouthForm: 0,
      ParamCheek: 0,
    },
    transition: 'smooth',
  },
  [EmotionType.LISTENING]: {
    emotion: 'listening',
    duration: 0,
    params: {
      ParamAngleX: 0, ParamAngleY: 0, ParamAngleZ: 0,
      ParamEyeLOpen: 0.85, ParamEyeROpen: 0.85,
      ParamEyeLSmile: 0.2, ParamEyeRSmile: 0.2,
      ParamBrowLY: 0, ParamBrowRY: 0,
      ParamBrowLAngle: 0, ParamBrowRAngle: 0,
      ParamMouthOpenY: 0, ParamMouthForm: 0,
      ParamCheek: 0.1,
    },
    transition: 'smooth',
  },
};
```

### 2.3 语音 → 口型同步

```typescript
interface LipSyncConfig {
  // 音素到口型参数的映射
  phonemeMap: Record<string, {
    ParamMouthOpenY: number;
    ParamMouthForm: number;
  }>;
  
  // 平滑参数
  smoothingWindow: number;  // 平滑窗口大小 (ms)
  latencyCompensation: number; // 延迟补偿 (ms)
}

// 基础音素映射 (可根据模型调整)
const PHONEME_TO_MOUTH: LipSyncConfig['phonemeMap'] = {
  // 闭口音
  'm': { ParamMouthOpenY: 0.1, ParamMouthForm: -0.3 },
  'n': { ParamMouthOpenY: 0.15, ParamMouthForm: -0.2 },
  'ng': { ParamMouthOpenY: 0.1, ParamMouthForm: -0.1 },
  
  // 开元音
  'a': { ParamMouthOpenY: 0.8, ParamMouthForm: 0.5 },
  'o': { ParamMouthOpenY: 0.6, ParamMouthForm: -0.5 },
  'u': { ParamMouthOpenY: 0.3, ParamMouthForm: -0.7 },
  
  // 闭元音
  'i': { ParamMouthOpenY: 0.4, ParamMouthForm: 0.3 },
  'e': { ParamMouthOpenY: 0.5, ParamMouthForm: 0.2 },
  
  // 爆破音
  'p': { ParamMouthOpenY: 0.2, ParamMouthForm: -0.5 },
  'b': { ParamMouthOpenY: 0.2, ParamMouthForm: -0.4 },
  't': { ParamMouthOpenY: 0.3, ParamMouthForm: 0 },
  'd': { ParamMouthOpenY: 0.3, ParamMouthForm: 0.1 },
  'k': { ParamMouthOpenY: 0.25, ParamMouthForm: -0.3 },
  'g': { ParamMouthOpenY: 0.25, ParamMouthForm: -0.2 },
  
  // 摩擦音
  's': { ParamMouthOpenY: 0.2, ParamMouthForm: 0.4 },
  'z': { ParamMouthOpenY: 0.2, ParamMouthForm: 0.3 },
  'sh': { ParamMouthOpenY: 0.25, ParamMouthForm: 0.2 },
  'f': { ParamMouthOpenY: 0.15, ParamMouthForm: -0.6 },
  'v': { ParamMouthOpenY: 0.15, ParamMouthForm: -0.5 },
  'th': { ParamMouthOpenY: 0.2, ParamMouthForm: -0.4 },
  
  // 流音
  'l': { ParamMouthOpenY: 0.3, ParamMouthForm: 0.1 },
  'r': { ParamMouthOpenY: 0.35, ParamMouthForm: 0 },
  
  // 半元音
  'w': { ParamMouthOpenY: 0.4, ParamMouthForm: -0.6 },
  'y': { ParamMouthOpenY: 0.35, ParamMouthForm: 0.2 },
  
  // 静默/呼吸
  'silence': { ParamMouthOpenY: 0, ParamMouthForm: 0 },
  'breath': { ParamMouthOpenY: 0.1, ParamMouthForm: 0 },
};
```

---

## 3. Live2D 配置生成系统

### 3.1 模型选择策略

```typescript
interface ModelSelectionCriteria {
  // 角色基础属性
  gender: 'male' | 'female' | 'non-binary';
  ageRange: 'child' | 'teen' | 'adult' | 'elder';
  bodyType: 'slim' | 'average' | 'athletic' | 'curvy';
  
  // 风格属性
  artStyle: 'anime' | 'realistic' | 'chibi' | 'pixel';
  colorPalette: string[];  // 主色调
  
  // 场景适配
  setting: 'fantasy' | 'modern' | 'sci-fi' | 'historical';
  formality: 'casual' | 'formal' | 'uniform';
}

interface Live2DModel {
  id: string;
  name: string;
  tags: string[];
  modelPath: string;
  textures: string[];
  expressions: string[];
  physics: string;
  pose: string;
  
  // 适配度评分函数
  matchScore(criteria: ModelSelectionCriteria): number;
}

// 模型库示例
const MODEL_LIBRARY: Live2DModel[] = [
  {
    id: 'haru_01',
    name: 'Haru (Default)',
    tags: ['female', 'adult', 'anime', 'casual'],
    modelPath: 'models/Haru/Pro/Model/haru_greeter_t03.model3.json',
    textures: ['default', 'white', 'pink'],
    expressions: ['f01.exp3.json', 'f02.exp3.json', 'f03.exp3.json'],
    physics: 'physics.json',
    pose: 'pose.json',
    matchScore: (criteria) => {
      let score = 0;
      if (criteria.gender === 'female') score += 30;
      if (criteria.ageRange === 'adult') score += 20;
      if (criteria.artStyle === 'anime') score += 25;
      if (criteria.formality === 'casual') score += 15;
      return score;
    },
  },
  // ... 更多模型
];

// 模型选择算法
function selectBestModel(
  criteria: ModelSelectionCriteria,
  availableModels: Live2DModel[]
): Live2DModel {
  return availableModels
    .map(model => ({
      model,
      score: model.matchScore(criteria),
    }))
    .sort((a, b) => b.score - a.score)[0]?.model;
}
```

### 3.2 服装/配饰配置

```typescript
interface CostumeConfig {
  // 服装层 (Live2D ArtMesh)
  clothing: {
    top?: string;      // ArtMesh ID
    bottom?: string;
    shoes?: string;
    accessories?: string[];
  };
  
  // 颜色覆盖
  colorOverrides: Record<string, {
    r: number; g: number; b: number; a: number;
  }>;
  
  // 纹理替换
  textureReplacements: Record<string, string>;  // ArtMesh ID → 纹理路径
}

interface CostumePreset {
  id: string;
  name: string;
  tags: string[];
  config: CostumeConfig;
}

// 服装预设库
const COSTUME_PRESETS: CostumePreset[] = [
  {
    id: 'casual_modern',
    name: '现代休闲',
    tags: ['casual', 'modern', 'daily'],
    config: {
      clothing: {
        top: 'ArtMesh_CasualTop',
        bottom: 'ArtMesh_Jeans',
        shoes: 'ArtMesh_Sneakers',
      },
      colorOverrides: {},
      textureReplacements: {},
    },
  },
  {
    id: 'formal_uniform',
    name: '正式制服',
    tags: ['formal', 'uniform', 'professional'],
    config: {
      clothing: {
        top: 'ArtMesh_Blazer',
        bottom: 'ArtMesh_Skirt',
        shoes: 'ArtMesh_Heels',
        accessories: ['ArtMesh_Tie', 'ArtMesh_Badge'],
      },
      colorOverrides: {
        'ArtMesh_Blazer': { r: 0.2, g: 0.2, b: 0.4, a: 1 },
      },
      textureReplacements: {},
    },
  },
  {
    id: 'fantasy_mage',
    name: '奇幻法师',
    tags: ['fantasy', 'magical', 'robe'],
    config: {
      clothing: {
        top: 'ArtMesh_Robe',
        bottom: 'ArtMesh_Robe_Lower',
        shoes: 'ArtMesh_Boots',
        accessories: ['ArtMesh_Hat', 'ArtMesh_Staff'],
      },
      colorOverrides: {
        'ArtMesh_Robe': { r: 0.4, g: 0.2, b: 0.6, a: 1 },
      },
      textureReplacements: {
        'ArtMesh_Hat': 'textures/mage_hat_star.png',
      },
    },
  },
];
```

### 3.3 定制贴图生成

```typescript
interface CustomTextureConfig {
  // 生成参数
  baseColor: string;
  pattern?: 'solid' | 'stripe' | 'check' | 'floral' | 'geometric';
  accentColors?: string[];
  
  // AI 生成参数 (如果支持)
  aiPrompt?: string;
  styleReference?: string;
}

interface TextureGenerationResult {
  success: boolean;
  texturePath?: string;
  metadata: {
    width: number;
    height: number;
    format: string;
    generatedAt: string;
  };
  error?: string;
}

// 贴图生成器接口
interface ITextureGenerator {
  generate(config: CustomTextureConfig): Promise<TextureGenerationResult>;
  
  // 批量生成
  generateBatch(
    configs: CustomTextureConfig[]
  ): Promise<TextureGenerationResult[]>;
  
  // 基于角色设定自动生成
  generateFromCharacter(
    characterProfile: CharacterProfile
  ): Promise<TextureGenerationResult>;
}

// 实现示例：使用 Stable Diffusion 生成
class SDTextureGenerator implements ITextureGenerator {
  async generate(config: CustomTextureConfig): Promise<TextureGenerationResult> {
    // 调用 SD API 生成贴图
    const prompt = this.buildPrompt(config);
    const result = await this.callSDAPI(prompt);
    return {
      success: true,
      texturePath: result.imagePath,
      metadata: {
        width: 512,
        height: 512,
        format: 'png',
        generatedAt: new Date().toISOString(),
      },
    };
  }
  
  private buildPrompt(config: CustomTextureConfig): string {
    let prompt = `seamless texture pattern, ${config.baseColor}`;
    if (config.pattern) {
      prompt += `, ${config.pattern} pattern`;
    }
    if (config.accentColors) {
      prompt += `, accent colors: ${config.accentColors.join(', ')}`;
    }
    if (config.aiPrompt) {
      prompt += `, ${config.aiPrompt}`;
    }
    prompt += ', high quality, game asset, 2d style';
    return prompt;
  }
  
  private async callSDAPI(prompt: string): Promise<{ imagePath: string }> {
    // 实现 SD API 调用
    return { imagePath: '/textures/generated/xxx.png' };
  }
}
```

---

## 4. 集成接口设计

### 4.1 核心接口

```typescript
/**
 * Live2D 角色管理器主接口
 */
interface ILive2DCharacterManager {
  // 初始化
  initialize(config: Live2DInitConfig): Promise<void>;
  
  // 创建角色实例
  createCharacter(
    profile: CharacterProfile
  ): Promise<Live2DCharacterInstance>;
  
  // 获取角色实例
  getCharacter(characterId: string): Live2DCharacterInstance | null;
  
  // 销毁角色
  destroyCharacter(characterId: string): void;
  
  // 批量操作
  listCharacters(): Live2DCharacterInstance[];
  clearAllCharacters(): void;
}

/**
 * Live2D 角色实例
 */
interface Live2DCharacterInstance {
  readonly id: string;
  readonly profile: CharacterProfile;
  readonly model: Live2DModel;
  
  // 状态控制
  setEmotion(emotion: EmotionType, intensity?: number): void;
  setPersonality(traits: PersonalityTraits): void;
  
  // 语音同步
  startLipSync(audioStream: AudioStream): void;
  stopLipSync(): void;
  
  // 外观切换
  changeCostume(presetId: string): Promise<void>;
  applyCustomTexture(part: string, texturePath: string): void;
  
  // 手动参数控制
  setParameter(paramName: string, value: number): void;
  getParameter(paramName: string): number;
  
  // 事件订阅
  onEmotionChange(callback: (emotion: EmotionType) => void): Subscription;
  onSpeechStart(callback: () => void): Subscription;
  onSpeechEnd(callback: () => void): Subscription;
  
  // 渲染
  render(canvas: HTMLCanvasElement, deltaTime: number): void;
}

/**
 * 角色配置文件
 */
interface CharacterProfile {
  // 基础信息
  id: string;
  name: string;
  
  // 人格特质
  personality: PersonalityTraits;
  
  // 声音配置
  voice: {
    provider: 'elevenlabs' | 'azure' | 'local';
    voiceId: string;
    pitch: number;
    speed: number;
  };
  
  // 记忆/背景 (用于选择模型风格)
  background: {
    setting: string;
    occupation?: string;
    relationships?: Record<string, string>;
  };
  
  // 外观偏好
  appearance: {
    artStyle: string;
    colorPreferences: string[];
    costumeStyle: string;
  };
}
```

### 4.2 事件驱动架构

```typescript
/**
 * AI 角色系统 → Live2D 事件总线
 */
interface CharacterEventBus {
  // 发布事件
  publish(event: CharacterEvent): void;
  
  // 订阅事件
  subscribe(
    eventType: CharacterEventType,
    handler: (event: CharacterEvent) => void
  ): Subscription;
}

type CharacterEventType =
  | 'CHARACTER_CREATED'
  | 'EMOTION_CHANGED'
  | 'SPEECH_STARTED'
  | 'SPEECH_ENDED'
  | 'MEMORY_UPDATED'
  | 'RELATIONSHIP_CHANGED'
  | 'COSTUME_CHANGED';

interface CharacterEvent {
  type: CharacterEventType;
  characterId: string;
  timestamp: number;
  payload: Record<string, any>;
}

// 事件处理器示例
class Live2DEventHandler {
  constructor(
    private eventBus: CharacterEventBus,
    private characterManager: ILive2DCharacterManager
  ) {
    this.setupListeners();
  }
  
  private setupListeners(): void {
    this.eventBus.subscribe('EMOTION_CHANGED', (event) => {
      const character = this.characterManager.getCharacter(event.characterId);
      if (character) {
        character.setEmotion(event.payload.emotion, event.payload.intensity);
      }
    });
    
    this.eventBus.subscribe('SPEECH_STARTED', (event) => {
      const character = this.characterManager.getCharacter(event.characterId);
      if (character) {
        character.startLipSync(event.payload.audioStream);
      }
    });
    
    this.eventBus.subscribe('SPEECH_ENDED', (event) => {
      const character = this.characterManager.getCharacter(event.characterId);
      if (character) {
        character.stopLipSync();
      }
    });
    
    this.eventBus.subscribe('COSTUME_CHANGED', (event) => {
      const character = this.characterManager.getCharacter(event.characterId);
      if (character) {
        character.changeCostume(event.payload.costumePresetId);
      }
    });
  }
}
```

### 4.3 API 层设计

```typescript
/**
 * REST API 端点设计
 */
const API_ENDPOINTS = {
  // 角色管理
  'POST /api/characters': '创建新角色',
  'GET /api/characters': '获取所有角色',
  'GET /api/characters/:id': '获取角色详情',
  'PUT /api/characters/:id': '更新角色配置',
  'DELETE /api/characters/:id': '删除角色',
  
  // 实时控制
  'POST /api/characters/:id/emotion': '设置情绪',
  'POST /api/characters/:id/speech': '开始语音 (返回音频流)',
  'POST /api/characters/:id/costume': '切换服装',
  
  // 模型/资源配置
  'GET /api/models': '获取可用模型列表',
  'GET /api/costumes': '获取服装预设列表',
  'POST /api/textures/generate': '生成定制贴图',
  
  // WebSocket 实时通道
  'WS /ws/characters/:id': '角色实时事件订阅',
};

// WebSocket 消息格式
interface WSMessage {
  type: 'emotion' | 'speech' | 'parameter' | 'costume';
  characterId: string;
  data: any;
  timestamp: number;
}

// WebSocket 客户端示例
class Live2DWebSocketClient {
  private ws: WebSocket;
  
  constructor(characterId: string, baseUrl: string) {
    this.ws = new WebSocket(`${baseUrl}/ws/characters/${characterId}`);
    this.setupHandlers();
  }
  
  private setupHandlers(): void {
    this.ws.onmessage = (event) => {
      const message: WSMessage = JSON.parse(event.data);
      this.handleMessage(message);
    };
  }
  
  private handleMessage(message: WSMessage): void {
    switch (message.type) {
      case 'emotion':
        this.applyEmotion(message.data);
        break;
      case 'speech':
        this.syncLip(message.data);
        break;
      case 'parameter':
        this.updateParameter(message.data);
        break;
    }
  }
}
```

### 4.4 数据流架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        AI Character System                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ Personality │  │   Memory    │  │   Conversation State    │  │
│  │   Engine    │  │   System    │  │   (Emotion Detection)   │  │
│  └──────┬──────┘  └──────┬──────┘  └───────────┬─────────────┘  │
│         │                │                      │                │
│         └────────────────┼──────────────────────┘                │
│                          │                                       │
│                  ┌───────▼───────┐                               │
│                  │  Event Bus    │                               │
│                  │  (Publisher)  │                               │
│                  └───────┬───────┘                               │
└──────────────────────────┼───────────────────────────────────────┘
                           │
                           │ Character Events
                           │ (EMOTION_CHANGED, SPEECH_STARTED, etc.)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Live2D Integration Layer                      │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │              CharacterEventBus (Subscriber)               │  │
│  └───────────────────────────┬───────────────────────────────┘  │
│                              │                                   │
│              ┌───────────────┼───────────────┐                  │
│              ▼               ▼               ▼                  │
│     ┌─────────────┐ ┌─────────────┐ ┌──────────────┐           │
│     │  Emotion    │ │   LipSync   │ │  Parameter   │           │
│     │  Mapper     │ │   Engine    │ │  Interpolator│           │
│     └──────┬──────┘ └──────┬──────┘ └──────┬───────┘           │
│            │               │                │                   │
│            └───────────────┼────────────────┘                   │
│                            │                                    │
│                   ┌────────▼────────┐                          │
│                   │ Live2D Renderer │                          │
│                   │   (Cubism)      │                          │
│                   └────────┬────────┘                          │
└────────────────────────────┼────────────────────────────────────┘
                             │
                             │ Canvas Draw Calls
                             ▼
                    ┌─────────────────┐
                    │   HTML Canvas   │
                    │   / WebGL       │
                    └─────────────────┘
```

---

## 5. 实现建议

### 5.1 技术栈推荐

| 组件 | 推荐方案 | 备选方案 |
|------|----------|----------|
| Live2D 渲染 | PixiJS + Live2D Cubism Web SDK | Three.js + Live2D |
| 语音合成 | ElevenLabs API | Azure TTS / Edge TTS |
| 口型同步 | Rhubarb Lip Sync | 自定义音素检测 |
| 事件总线 | EventEmitter / RxJS | Redis Pub/Sub (分布式) |
| 贴图生成 | Stable Diffusion API | DALL-E 3 / Midjourney |

### 5.2 性能优化

```typescript
// 1. 参数插值平滑
function lerpParam(current: number, target: number, delta: number, speed: number): number {
  return current + (target - current) * speed * delta;
}

// 2. 表情过渡队列
class ExpressionQueue {
  private queue: EmotionExpression[] = [];
  private current: EmotionExpression | null = null;
  private transitionProgress: number = 0;
  
  push(emotion: EmotionExpression): void {
    this.queue.push(emotion);
  }
  
  update(delta: number): void {
    if (!this.current && this.queue.length > 0) {
      this.current = this.queue.shift()!;
      this.transitionProgress = 0;
    }
    
    if (this.current) {
      this.transitionProgress += delta / this.current.duration;
      if (this.transitionProgress >= 1) {
        this.current = null;
      }
    }
  }
}

// 3. 口型同步缓冲
class LipSyncBuffer {
  private buffer: PhonemeFrame[] = [];
  private bufferSize = 10; // 帧缓冲
  
  addFrame(phoneme: string, timestamp: number): void {
    this.buffer.push({ phoneme, timestamp });
    if (this.buffer.length > this.bufferSize) {
      this.buffer.shift();
    }
  }
  
  getInterpolatedMouthParams(): { open: number; form: number } {
    // 基于缓冲区插值计算
    // ...
  }
}
```

### 5.3 扩展性设计

```typescript
// 插件系统接口
interface Live2DPlugin {
  name: string;
  version: string;
  
  // 生命周期
  onInitialize(manager: ILive2DCharacterManager): void;
  onCharacterCreated(character: Live2DCharacterInstance): void;
  onDestroy(): void;
  
  // 事件钩子
  onBeforeRender?(character: Live2DCharacterInstance, delta: number): void;
  onAfterRender?(character: Live2DCharacterInstance, delta: number): void;
}

// 示例插件：物理效果增强
class PhysicsEnhancementPlugin implements Live2DPlugin {
  name = 'physics-enhancement';
  version = '1.0.0';
  
  onInitialize(manager: ILive2DCharacterManager): void {
    // 注册物理参数
  }
  
  onBeforeRender(character: Live2DCharacterInstance, delta: number): void {
    // 应用额外的物理效果
  }
  
  onDestroy(): void {
    // 清理
  }
}
```

---

## 6. 总结

本设计实现了：

1. **人格 → 表情映射**: 通过参数范围限制和默认值，让人格特质影响角色的基础表情倾向
2. **情绪 → 实时表情**: 预定义情绪表情库，支持平滑过渡和持续时间控制
3. **语音 → 口型同步**: 音素到口型参数映射，支持实时音频流驱动
4. **配置生成**: 基于角色设定自动选择模型、服装，支持 AI 生成定制贴图
5. **集成接口**: 事件驱动架构 + REST API + WebSocket，支持与 AI 角色系统无缝集成

**下一步行动**:
- [ ] 实现核心接口 (`ILive2DCharacterManager`)
- [ ] 集成 Live2D Cubism Web SDK
- [ ] 开发情绪检测模块 (对接 AI 对话系统)
- [ ] 实现口型同步引擎
- [ ] 构建模型/服装资源库
- [ ] 开发贴图生成服务
- [ ] 创建示例角色和演示应用
