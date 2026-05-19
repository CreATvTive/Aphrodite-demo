# UE5 GazeDiagnosticTrace Widget A0 — Hardcoded Field Presence Debug Panel

## 0. 当前阶段说明

Aphrodite 目前已有：

- Gaze Dynamics Kernel v0.3.1 数学设计
- Diagnostic Trace / Field Presence View 设计

但当前目标不是实现完整 gaze dynamics，也不是接入 JSON、UDP、OSC、Python core 或 runtime。

当前目标只是：

> 在 UE5 中做一个最小可见调试面板，用硬编码的 `GazeDiagnosticTrace` 预设显示 gaze-field 结构。

这个面板应该能让用户按 `1 / 2 / 3` 切换三个场景：

1. `correction`
2. `dependency_expression`
3. `technical_question`

并能看到：

- 每个 gaze target 在 yaw/pitch 空间中的位置；
- 哪个 channel 正在主导；
- 为什么 contact 被 block 或 allowed；
- 当前系统处于 relational / escape / task / rest 哪种模式；
- contact / refractory timer 状态。

---

## 1. 明确不做的事

本阶段不要做：

- 不接 live JSON
- 不接 Python/core
- 不接 UDP/OSC/networking
- 不写 C++，除非绝对必要
- 不做 Control Rig
- 不做 skeletal mesh
- 不做 animation blueprint
- 不做 renderer integration
- 不实现真实 gaze dynamics math
- 不连接 Aphrodite runtime
- 不让角色真的动

本阶段只做：

> UE5 / Blueprint / UMG 里的 hardcoded debug panel。

---

## 2. Panel 结构

### A. 2D Spatial Radar

显示一个二维 yaw/pitch 平面。

坐标约定：

- X axis = Yaw
- Y axis = Pitch
- 中心 `(0, 0)` = perfect user gaze target

需要显示这些点：

- `G_rel`
- `G_escape`
- `G_task`
- `G_rest`
- `G_target`
- `Eye_actual`
- `Head_actual`

建议显示方式：

| 点 | 显示 |
|---|---|
| `G_rel` | blue hollow circle |
| `G_escape` | red hollow circle |
| `G_task` | purple hollow circle |
| `G_rest` | gray hollow circle |
| `G_target` | solid white dot |
| `Eye_actual` | green crosshair |
| `Head_actual` | yellow cross marker |

如果 Blueprint / UMG 不方便画虚线或 hollow circle，可以先用不同颜色的小方块 / 小圆点替代。

额外数值：

- `user_contact_offset_norm = distance((0,0), Eye_actual)`
- `target_tracking_error_norm = distance(G_target, Eye_actual)`

不要只叫 `intent_actual_offset_norm`，因为它含义不够清晰。

---

### B. Channel Competition Bars

显示四个 channel 权重：

- `w_rel`
- `w_escape`
- `w_task`
- `w_rest`

需要高亮当前：

- `dominant_channel`

如果 UMG ProgressBar 难做，可以用横向 `Border` / `Image` 缩放宽度模拟。

---

### C. Scalars & Timer Strip

显示这些 scalar：

- `P_release`
- `P_unlock`
- `S_warmth`
- `F_fatigue`

显示这些 timer：

- `contact_timer`
- `release_refractory_timer`

Timer 最大值：

- `contact_timer max = 3.0s`
- `release_refractory_timer max = 2.0s`

显示计算：

```text
contact_timer_display = contact_timer / 3.0
refractory_timer_display = release_refractory_timer / 2.0
```

---

### D. Explanation Log

显示：

- `primary_reason`
- `secondary_reasons`

可用 reason 文案：

- `Forced Release (Correction/Contamination/Withdrawal)`
- `Fatigue Release (Contact timer exceeded limit)`
- `Task Override (Collaborator mode active)`
- `Service Resistance Unlock (Drifting to rest)`
- `Relational Offset (Blocked by Boundary or Grip Hold)`
- `Clear Contact (Full relational permission)`

---

## 3. Hardcoded Trace Presets

### Preset 1 — correction

Purpose:

> Show forced release and refractory residue.

Values:

```text
G_rel = (0, 0)
G_escape = (0, -25)
G_task = (0, -20)
G_rest = (5, -2)
G_target = (0, -22)
Eye_actual = (0, -18)
Head_actual = (0, -7)

w_rel = 0.05
w_escape = 1.80
w_task = 0.00
w_rest = 0.05
dominant_channel = "escape"

P_release = 0.90
P_unlock = 0.10
S_warmth = 0.10
F_fatigue = 0.00

contact_timer = 0.20
release_refractory_timer = 1.80
side_bias = 1.0

primary_reason = "Forced Release (Correction/Contamination/Withdrawal)"
secondary_reasons = ["Refractory residue active", "Eye released faster than head"]
```

Expected visual:

- escape bar dominates
- `G_escape` and `G_target` are down
- `Eye_actual` is below center
- `Head_actual` follows only partially
- refractory timer is high

---

### Preset 2 — dependency_expression

Purpose:

> Show non-contact intimacy / near-user but incomplete hold.

Values:

```text
G_rel = (4, -3)
G_escape = (10, -5)
G_task = (0, -20)
G_rest = (5, -2)
G_target = (4, -3)
Eye_actual = (3.5, -2.8)
Head_actual = (1.2, -0.9)

w_rel = 0.85
w_escape = 0.05
w_task = 0.00
w_rest = 0.20
dominant_channel = "rel"

P_release = 0.05
P_unlock = 0.25
S_warmth = 0.80
F_fatigue = 0.20

contact_timer = 1.80
release_refractory_timer = 0.00
side_bias = 1.0

primary_reason = "Relational Offset (Blocked by Boundary or Grip Hold)"
secondary_reasons = ["Near-user hold active", "Warmth softens timing but does not create gaze lock"]
```

Expected visual:

- relational bar dominates
- `G_rel` is near center but slightly side/down
- `Eye_actual` is near `G_rel`, not at user center
- contact_timer is partially filled
- warmth bar high
- this should feel like: present but not locked

---

### Preset 3 — technical_question

Purpose:

> Show task override / collaborator mode.

Values:

```text
G_rel = (0, 0)
G_escape = (10, -5)
G_task = (0, -20)
G_rest = (5, -2)
G_target = (0, -18)
Eye_actual = (0, -16)
Head_actual = (0, -6)

w_rel = 0.05
w_escape = 0.00
w_task = 0.95
w_rest = 0.10
dominant_channel = "task"

P_release = 0.00
P_unlock = 0.10
S_warmth = 0.30
F_fatigue = 0.00

contact_timer = 0.00
release_refractory_timer = 0.00
side_bias = 1.0

primary_reason = "Task Override (Collaborator mode active)"
secondary_reasons = ["Functional attention active", "Relational gaze suppressed"]
```

Expected visual:

- task bar dominates
- `G_task / G_target` are downward
- contact_timer is empty
- relational bar remains only at floor
- this should look like functional attention, not intimacy

---

## 4. UE5 Blueprint / UMG 实施请求

输出应该是 UE5 Blueprint 实施指南，不是高层设计文章。

### 4.1 创建对象

需要说明：

- Widget Blueprint 名称
- 可选 Actor Blueprint 名称
- 每个组件放在哪里

建议命名：

```text
WBP_GazeDiagnosticPanel
BP_GazeDiagnosticPanelHost
```

---

### 4.2 需要添加的变量

为所有 trace fields 添加变量：

#### 坐标变量

```text
G_rel_yaw, G_rel_pitch
G_escape_yaw, G_escape_pitch
G_task_yaw, G_task_pitch
G_rest_yaw, G_rest_pitch
G_target_yaw, G_target_pitch
Eye_actual_yaw, Eye_actual_pitch
Head_actual_yaw, Head_actual_pitch
```

#### 权重变量

```text
w_rel
w_escape
w_task
w_rest
dominant_channel
```

#### 标量变量

```text
P_release
P_unlock
S_warmth
F_fatigue
```

#### timer 变量

```text
contact_timer
release_refractory_timer
```

#### 文本变量

```text
primary_reason
secondary_reasons
```

---

### 4.3 Preset 切换

键盘绑定：

```text
1 -> correction
2 -> dependency_expression
3 -> technical_question
```

每个按键调用一个函数：

```text
LoadPresetCorrection()
LoadPresetDependencyExpression()
LoadPresetTechnicalQuestion()
```

---

### 4.4 2D 点坐标映射

把 yaw/pitch 映射到 widget pixel positions。

建议：

```text
panel_center = (PanelWidth / 2, PanelHeight / 2)
scale = 6 pixels per degree

screen_x = center_x + yaw * scale
screen_y = center_y - pitch * scale
```

注意：

> screen Y 向下增加，所以 pitch 要取负。

---

### 4.5 UMG 里如何画点

如果 custom drawing 太难：

- 每个点用一个小 `Image` widget
- 每个点用不同颜色
- 每 Tick / 每次 preset 切换更新 `Render Translation` 或 `Canvas Slot Position`

建议点：

```text
Img_GRel
Img_GEscape
Img_GTask
Img_GRest
Img_GTarget
Img_EyeActual
Img_HeadActual
```

---

### 4.6 如何做 bars

可以用：

- `ProgressBar`
- 或 `Border` / `Image` 缩放宽度

权重显示范围建议：

```text
weight_display = clamp(weight / 2.0, 0.0, 1.0)
```

Timer 显示：

```text
contact_timer_display = clamp(contact_timer / 3.0, 0.0, 1.0)
refractory_timer_display = clamp(release_refractory_timer / 2.0, 0.0, 1.0)
```

---

### 4.7 如何计算 offset 数值

```text
user_contact_offset_norm =
sqrt(Eye_actual_yaw^2 + Eye_actual_pitch^2)

target_tracking_error_norm =
sqrt((Eye_actual_yaw - G_target_yaw)^2 + (Eye_actual_pitch - G_target_pitch)^2)
```

---

## 5. 测试

运行 Play in Editor：

- 按 `1`
- 按 `2`
- 按 `3`

检查：

### correction

- escape bar dominates
- target and eye are downward
- refractory timer high
- reason = Forced Release

### dependency_expression

- relational bar dominates
- eye is near but not centered
- contact timer partially filled
- reason = Relational Offset

### technical_question

- task bar dominates
- target is downward task position
- contact timer empty
- reason = Task Override

---

## 6. Troubleshooting

需要包含以下排查：

- keyboard input 不工作怎么办
- widget 不显示怎么办
- 点跑出面板怎么办
- bars 不更新怎么办
- text 不更新怎么办
- Yaw/Pitch 方向反了怎么办

---

## 7. 硬约束

- 不接 JSON
- 不接 Python/core
- 不用 networking
- 不用 C++，除非绝对必要
- 不实现 gaze dynamics math
- 不让角色动
- 不用 Control Rig
- 不做漂亮 UI
- 不做最终动画
- 先做诊断可见性

---

## 8. 最终目标

完成后，用户应该能够在 UE5 里按 `1 / 2 / 3`，看到一个可见 Field Presence View：

- `correction`: escape dominates
- `dependency_expression`: relational offset / near-user hold dominates
- `technical_question`: task override dominates
