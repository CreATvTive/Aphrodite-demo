# 演示故障应急手册（Demo Failure Runbook）

适用范围：外部演示现场出现失败、卡住、指标异常时的快速处置。

## 0. 先稳住现场（30 秒内）

1. 对外口径：
   - "我们先切到标准演示包，保证流程完整。"
2. 暂停临场自由操作，切回固定剧本。

## 1. 快速自检（1 分钟）

```bash
bash scripts/run_demo_bundle.sh
```

- 若命令直接失败：进入 **2. 启动层故障**。
- 若命令成功但指标异常：进入 **3. 指标层故障**。

## 2. 启动层故障处理

### 2.1 Python 环境/依赖问题

1. 检查解释器：
   ```bash
   python --version
   ```
2. 使用同一解释器重跑：
   ```bash
   python cli/run_demo_pack.py --scenario all
   ```

### 2.2 路径/文件缺失

1. 检查剧本文件是否存在：
   ```bash
   test -f demos/scenarios/security_scene.json && echo ok
   test -f demos/scenarios/social_scene.json && echo ok
   test -f demos/scenarios/task_scene.json && echo ok
   ```
2. 缺失时从仓库恢复后重试。

## 3. 指标层故障处理

## 3.1 动作成功率低于预期

1. 查看报告：`outputs/demo/demo_report.json`
2. 定位失败 step 的 `event/action`。
3. 优先切换到单场景重跑：
   ```bash
   python cli/run_demo_pack.py --scenario security_scene --json
   ```

## 3.2 降级次数异常升高

1. 核对场景中的 `degrade_steps` 是否被误改。
2. 若被改动，恢复到标准值（每个场景当前 1 个降级步骤）。

## 3.3 事件吞吐异常低

1. 排除机器资源抖动（关掉高占用任务）。
2. 重跑并对比两次输出，若持续偏低，使用单场景演示兜底。

## 4. 演示兜底策略（必备）

1. 总演示失败时，至少保证一个场景完整跑通：
   ```bash
   python cli/run_demo_pack.py --scenario task_scene
   ```
2. 对外说明：
   - "为了保证稳定性，我们展示标准任务闭环场景。"

## 5. 会后复盘模板（5 分钟）

记录三项：

- 触发故障的时间点。
- 故障类型（启动/指标/环境）。
- 采取动作与恢复耗时。

将复盘补充到团队演示文档，避免同类故障再次发生。
