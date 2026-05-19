# UE5 Field Force Debug Arrow Prototype — 实施规格

## 1. 概述

- **目标**: 在 UE5 中构建核心独立的 3D 场力可视化原型
- **不是**: 最终动画、角色行为系统、MotionParams 集成
- **作用**: 10D 场力向量 U(t) 的诊断查看器
- **不连接**: UDP/OSC、实时核心输出（先用硬编码预设）

## 2. Actor 设置

### 2.1 所需 Actor
- `BP_FieldForceActor` — 主 Actor（白模占位符）
- `BP_UserTarget` — 测试用目标点 Actor（可选，可改为固定前方目标点）

### 2.2 BP_FieldForceActor 组件
- `SceneComponent` — Root
- `StaticMeshComponent` — 简单胶囊体/圆柱体（白模占位符）
- `ArrowComponent × 3` — 程序化生成的 Debug 向量（红/青/黄）

### 2.3 CoM 计算
```
CoM = GetActorLocation() + FVector(0, 0, 90)
```

### 2.4 DirectionToUser
```
如果 UserActor != nullptr:
    ToUser = UserActor.Location - Self.Location
    ToUser.Z = 0
    DirToUser = ToUser.GetSafeNormal()
否则:
    DirToUser = GetActorForwardVector()
```

## 3. U_array 输入结构

10 维场力数组，轴顺序（来源：[`AXIS_INDEX`](Aphrodite-demo/src/field_dynamics/force_adapter.py:23-34) / [`FIELD_DIMENSION`](Aphrodite-demo/src/field_dynamics/schema.py:9)）：

| 索引 | 轴名 | 含义 |
|------|------|------|
| 0 | boundary_distance | 边界距离压力 |
| 1 | affective_warmth | 情感温暖 |
| 2 | structural_grip_pressure | 结构抓点压力 |
| 3 | correction_pressure | 修正压力 |
| 4 | contamination_resistance | 污染抵抗 |
| 5 | presence_stability | 在场稳定性 |
| 6 | withdrawal_tendency | 退缩倾向 |
| 7 | service_resistance | 服务抵抗 |
| 8 | collaborator_layer_pressure | 协作者层压力 |
| 9 | contamination_pressure | 污染压力 |

### Blueprint 变量
```
UPROPERTY(EditAnywhere, Category="Field Force")
TArray<float> U_array;  // 固定大小为 10

// 构造函数中初始化:
U_array.SetNum(10);
```

## 4. 三个 Debug 向量

### 4.1 边界/退缩力 — 红色箭头

**构成轴**：
- `U_array[0]` boundary_distance
- `U_array[6]` withdrawal_tendency

**幅值**：
```
BoundaryMagnitude = U_array[0] + U_array[6]
```

**方向**：
- 正值 → 远离用户（保护性退缩/空间创造）
- 负值 → 朝向用户（罕见，仅在边界降低时）

**绘制**：
```
BoundaryVector = DirToUser * BoundaryMagnitude * VisualizationScale * (-1.0)
// 正值箭头指向远离用户
ArrowEnd = CoM + ClampSize(BoundaryVector, MaxArrowLength)
DrawDebugDirectionalArrow(World, CoM, ArrowEnd, 50, Red, false, -1, 0, 3)
```

**含义**：物理距离压力 / 退缩压力。**不得直接映射为注视**。

### 4.2 温暖/提升力 — 青色箭头

**构成轴**：
- `U_array[1]` affective_warmth

**幅值**：
```
WarmthMagnitude = U_array[1]
```

**方向**：Z 轴向上（正值）

**绘制**：
```
WarmthVector = FVector(0, 0, WarmthMagnitude * VisualizationScale)
ArrowEnd = CoM + ClampSize(WarmthVector, MaxArrowLength)
DrawDebugDirectionalArrow(World, CoM, ArrowEnd, 50, Cyan, false, -1, 0, 3)
```

**含义**：非接触温暖 / 垂直提升。
**关键约束**：`affective_warmth` **不得映射为向用户的物理靠近**。
温暖是气质/存在品质，不是身体接近。

### 4.3 抵抗/压缩力 — 黄色箭头

**构成轴**：
- `U_array[3]` correction_pressure
- `U_array[7]` service_resistance
- `U_array[4]` contamination_resistance
- `U_array[9]` contamination_pressure

**幅值**：
```
ResistanceMagnitude = U_array[3] + U_array[7] + U_array[4] + U_array[9]
```

**方向**：Z 轴向下（正值）

**绘制**：
```
ResistanceVector = FVector(0, 0, ResistanceMagnitude * VisualizationScale * (-1.0))
ArrowEnd = CoM + ClampSize(ResistanceVector, MaxArrowLength)
DrawDebugDirectionalArrow(World, CoM, ArrowEnd, 50, Yellow, false, -1, 0, 3)
```

**含义**：内部压缩 / 抵抗 / 刚度。诊断用向量，不是字面意义上的身体运动。

## 5. 文本叠加（可选）

显示未映射到三个箭头的轴：
- `U_array[2]` structural_grip_pressure
- `U_array[8]` collaborator_layer_pressure
- `U_array[5]` presence_stability

```
DebugText = Format("Grip:{0:F2} Collab:{1:F2} Stability:{2:F2}",
    U_array[2], U_array[8], U_array[5])
DrawDebugString(World, CoM + FVector(0,0,150), DebugText, nullptr, White, 0, true)
```

## 6. 三个预设值 — 键盘切换

在 `BP_FieldForceActor` EventGraph 中绑定键盘事件。

### 预设 A — correction（按 `1`）

```
U_array:
  boundary_distance=0.10
  affective_warmth=0.05
  structural_grip_pressure=0.10
  correction_pressure=0.70
  contamination_resistance=0.20
  presence_stability=0.50
  withdrawal_tendency=0.20
  service_resistance=0.45
  collaborator_layer_pressure=0.10
  contamination_pressure=0.10

预期视觉：
  - 强黄色下压箭头（correction + service_resistance = 1.15 聚合）
  - 弱红色退缩（boundary + withdrawal = 0.30）
  - 弱/无青色提升（warmth = 0.05）
  - 文本：低 grip(0.10), 低 collab(0.10), 中 stability(0.50)
```

### 预设 B — dependency_expression（按 `2`）

```
U_array:
  boundary_distance=0.55
  affective_warmth=0.45
  structural_grip_pressure=0.65
  correction_pressure=0.05
  contamination_resistance=0.15
  presence_stability=0.60
  withdrawal_tendency=0.35
  service_resistance=0.25
  collaborator_layer_pressure=0.05
  contamination_pressure=0.10

预期视觉：
  - 青色提升(warmth=0.45)和红色退缩(boundary+withdrawal=0.90)并存
  - 温暖可见但不朝向用户
  - 温和黄色压缩
  - 关键：温暖应有物理靠近的感觉
```

### 预设 C — technical_question（按 `3`）

```
U_array:
  boundary_distance=0.20
  affective_warmth=0.02
  structural_grip_pressure=0.10
  correction_pressure=0.10
  contamination_resistance=0.10
  presence_stability=0.65
  withdrawal_tendency=0.10
  service_resistance=0.50
  collaborator_layer_pressure=0.70
  contamination_pressure=0.05

预期视觉：
  - 无强温暖提升（warmth=0.02）
  - 无亲密/靠近信号
  - 主要为黄色抵抗箭头 + 高协作者文本值(0.70)
  - 中性红色退缩
```

## 7. Tick 函数 — 伪代码

```cpp
void ABP_FieldForceActor::Tick(float DeltaTime)
{
    Super::Tick(DeltaTime);
    
    // 1. 计算参考点
    FVector CoM = GetActorLocation() + FVector(0, 0, 90);
    FVector DirToUser = ComputeDirectionToUser();
    
    // 2. 边界/退缩力 — 红色
    float BoundaryMagnitude = U_array[0] + U_array[6];
    FVector BoundaryVec = DirToUser * BoundaryMagnitude * VisualizationScale * (-1);
    DrawArrow(CoM, CoM + ClampVec(BoundaryVec, MaxArrowLen), Red);
    
    // 3. 温暖/提升力 — 青色
    float WarmthMagnitude = U_array[1];
    FVector WarmthVec = FVector(0, 0, WarmthMagnitude * VisualizationScale);
    DrawArrow(CoM, CoM + ClampVec(WarmthVec, MaxArrowLen), Cyan);
    
    // 4. 抵抗/压缩力 — 黄色
    float ResistanceMagnitude = U_array[3] + U_array[7] + U_array[4] + U_array[9];
    FVector ResistanceVec = FVector(0, 0, ResistanceMagnitude * VisualizationScale * (-1));
    DrawArrow(CoM, CoM + ClampVec(ResistanceVec, MaxArrowLen), Yellow);
    
    // 5. 文本叠加
    FString DebugInfo = Format("Grip:{0:F2} Collab:{1:F2} Stability:{2:F2}",
        U_array[2], U_array[8], U_array[5]);
    DrawDebugString(World, CoM + FVector(0,0,150), DebugInfo, 
                    nullptr, FColor::White, 0, true);
}

FVector ABP_FieldForceActor::ComputeDirectionToUser()
{
    if (UserTarget != nullptr)
    {
        FVector ToUser = UserTarget->GetActorLocation() - GetActorLocation();
        ToUser.Z = 0;
        return ToUser.GetSafeNormal();
    }
    return GetActorForwardVector();
}
```

## 8. 可调参数

```cpp
// 可视化缩放
UPROPERTY(EditAnywhere, Category="Visualization")
float VisualizationScale = 200.0f;  // 幅值→世界空间厘米

// 最大箭头长度
UPROPERTY(EditAnywhere, Category="Visualization")
float MaxArrowLength = 300.0f;  // 世界空间厘米

// 箭头厚度
UPROPERTY(EditAnywhere, Category="Visualization")
float ArrowThickness = 50.0f;

// 箭头持续时间 (-1 = 持久，但每帧重绘)
UPROPERTY(EditAnywhere, Category="Visualization")
float ArrowDuration = -1.0f;

// 用户目标 Actor 引用
UPROPERTY(EditAnywhere, Category="Reference")
AActor* UserTarget;
```

## 9. 截图/录制清单

对每个预设 (`1`/`2`/`3`) 录制或截图：

- [ ] 预设 A (correction): 强黄色下压箭头清晰可见，红色和青色箭头微弱
- [ ] 预设 B (dependency_expression): 青色提升和红色退缩并存，**温暖不朝向用户**
- [ ] 预设 C (technical_question): 无温暖/亲密信号，黄色抵抗为主，文本显示高 collab 值
- [ ] 俯视图: 红色箭头方向与用户方向相反
- [ ] 侧视图: 青色/黄色箭头沿 Z 轴
- [ ] 文本叠加清晰可读

## 10. 已知限制

1. **仅 3 个聚合向量**：10 维被合并为 3 个。每轴箭头在后续阶段单独可视化。
2. **无实时数据流**：U_array 通过键盘预设静态设置。UDP/OSC 在后续阶段连接。
3. **每次 Tick 重绘**：箭头每帧重新绘制。未来可优化为持久箭头组件。
4. **无骨架姿态**：3 个向量均从 CoM 发出，非身体部位方向。骨架绑定在后续阶段。
5. **coordinate_pressure 包含在黄色向量中**：该组合（correction + service + contamination_resistance + contamination_pressure）打包了多个轴。未来可分解。

## 11. 后续步骤（Phase 41+，不在本阶段实施）

1. **UDP/OSC 接收器**：将实时 U_array 数据从 Aphrodite 核心传输到 UE5
2. **流式 U(t) 序列**：接收完整的时间剖面力序列，而非逐帧静态值
3. **每轴箭头**：10 个独立箭头替换 3 个聚合箭头
4. **骨架绑定**：将箭头锚定到身体部位（头部/躯干/手部方向）
5. **Niagara 粒子场**：用于更平滑的矢量场可视化
6. **白模骨架绑定**：将向量附着到骨架关节，实现身体部位级映射
7. **MotionParams 集成**：将运动参数直接驱动为骨骼动画

## 12. 硬约束

- [x] 不实现最终动画
- [x] 不创建动画状态机
- [x] 不连接 Aphrodite 核心
- [x] 不使用 Niagara 或复杂 VFX
- [x] 不将 `affective_warmth` 映射为物理靠近
- [x] 不将注视、身体运动和边界合并为单一的最终动画决策
