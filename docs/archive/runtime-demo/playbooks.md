# 标准演示剧本（固化版）

目标：在不扩展新功能的前提下，将演示流程固定为可重复执行的三套剧本，降低现场波动。

## 1) 安全场景（security_scene）

- **展示主题**：异常识别 → 隔离动作 → 值班通知。
- **脚本文件**：`demos/scenarios/security_scene.json`
- **关键看点**：
  - 门禁异常检测触发。
  - 降级步骤下仍执行 `lock_door`。
  - 最终告警路径打通。

## 2) 社交场景（social_scene）

- **展示主题**：来访接待 → 意图确认 → 路径引导。
- **脚本文件**：`demos/scenarios/social_scene.json`
- **关键看点**：
  - 欢迎与礼貌交互。
  - 意图识别后进入引导链路。
  - 导航噪声时保留可解释指引。

## 3) 任务场景（task_scene）

- **展示主题**：需求接收 → 执行分发 → 结果回执。
- **脚本文件**：`demos/scenarios/task_scene.json`
- **关键看点**：
  - 上游输入缺失时的降级兜底。
  - 执行闭环可追踪。
  - 最终产出回执稳定。

---

## 一键运行（含 mock 感知输入注入）

```bash
bash scripts/run_demo_bundle.sh
```

或直接运行 Python 入口：

```bash
python cli/run_demo_pack.py --scenario all --save-report outputs/demo/demo_report.json
```

默认开启 mock 注入；如需验证无注入行为：

```bash
python cli/run_demo_pack.py --scenario security_scene --no-mock
```

---

## 演示态指标面板（最小版）

命令输出固定三项：

- **事件吞吐（event throughput）**：`eps`
- **动作成功率（action success rate）**
- **降级次数（degradation count）**

建议每次外部演示前先跑一次 `--scenario all`，确认三项指标均在预期范围。
