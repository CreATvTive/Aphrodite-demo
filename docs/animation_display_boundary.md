# Animation / Display Interface Boundary Memo

## 1. Purpose

定义 core pipeline 与 animation/display 层之间的接口边界。
此文档是 P40 LLM 实验和 P41-42 展示侧工作的参考。

## 2. Interface Layers

### 2.1 MotionCurve — 渲染器时间包络

- **角色**: 面向渲染器的时间包络（主）+ 调试检查层（辅）
- **输入**: MotionParams（12参数 + hard_constraints + body_part_offsets）
- **输出**: MotionCurve（5通道时间-幅度曲线: gaze/head/torso/expression/posture）
- **禁止知道**: BodyActionWeights, BodyActionComposition, FieldState
- **文件**: src/motion_curve/schema.py, src/motion_curve/generator.py

### 2.2 FieldVectorState — 行为隐含场

- **角色**: 公开演示用的行为隐含场可视化（"磁力线"式）
- **输入**: RelationalFieldState（10场变量数值）
- **输出**: FieldVectorState（矢量场描述: 引力/斥力/稳定力/阈值）
- **禁止知道**: MotionParams, BodyActionWeights, 私有源性质
- **文件**: src/field_vector/ (P41)

### 2.3 KeyPosePreview — 关键姿势预览

- **角色**: 派生预览，非核心模型。用于非技术观众
- **输入**: BodyActionComposition + MotionCurve
- **输出**: KeyPosePreview（关键姿势描述: 注视方向/头部角度/躯干倾斜/表情幅度）
- **禁止知道**: FieldState, MotionParams公式
- **文件**: src/key_pose/ (P42)

### 2.4 SkeletonProxy — 骨架挂点

- **角色**: 仅挂点（attachment points），不做完整骨架动画
- **输入**: BodyActionComposition + MotionCurve
- **输出**: AttachmentPoints（头部/肩×2/脊柱/髋锚点坐标）
- **禁止知道**: FieldState, BodyActionWeights公式
- **文件**: src/skeleton_proxy/ (P42)

### 2.5 RendererAdapter — 渲染器适配器

- **角色**: 聚合 MotionCurve + SkeletonProxy，输出渲染器指令
- **输入**: MotionCurve + AttachmentPoints + KeyPosePreview
- **输出**: 渲染器指令（未来）
- **禁止知道**: FieldState, MotionParams
- **文件**: src/renderer/ (P43+)

### 2.6 LLM Experiment Layer — LLM 实验层

- **角色**: P40 受控 LLM 集成实验
- **输入**: 安全运动摘要（从 MotionParams 派生，中性标签，剥离源注释）
- **输出**: 候选语言片段 + 行为片段（经 Judgment Gate 门控，标记 [experimental]）
- **禁止知道**: FieldState, EvidenceItem, FieldSignalProposal, 私有源语言
- **LLM 禁止**: 定义角色、消费私有源、驱动身体管道、修改场状态
- **文件**: src/llm_gate/, agentlib/ds_client.py, scripts/run_llm_experiment.py (P40)

## 3. What Belongs to Display Layer (Not Core)

| 项目 | 原因 |
|------|------|
| 场实体可视化（矢量箭头、磁力线） | 展示层，不进入 core 管道 |
| 关键姿势比较 | 派生预览，不重新定义核心 |
| 骨架关节映射 | 展示层实现细节 |
| 化身/白模选择 | 展示层决策 |
| 渲染器时序曲线 | 展示层消费 MotionCurve |
| UI 布局 | 前端/展示层 |
| LLM 措辞选择 | LLM 实验层，非 core 管道 |
| 公共叙事 | 展示层包装 |

## 4. Frozen Layers (Do Not Change)

以下层在 Phase 39-42 期间冻结：

- src/field_state/schema.py — 10 场变量定义
- src/motion_params/schema.py — MotionParams schema + 公式
- src/motion_params/mapper.py — FieldStateToMotionParamsMapper
- src/body_action/motion_to_action_mapper.py — MotionToActionMapper
- src/body_action/composer.py — BodyActionComposer
- src/body_action/schema.py — BodyActionWeights + Composition schema

## 5. Experiment Isolation (P40 LLM)

- LLM 输出标记 [experimental]
- Judgment Gate 拦截违反源对齐的内容
- LLM 输出不写入场状态（不回写）
- 独立日志文件
- 仅在 7 个黄金场景上运行实验
- 终止条件: 连续 3 次 Gate 拒绝 → 暂停实验
