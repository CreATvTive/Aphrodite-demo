#!/bin/bash
# setup-worktree.sh - 自动创建 OpenClaw 工作区 worktree
# 用法：./setup-worktree.sh <项目名称> [任务描述]

set -e

PROJECT_NAME="${1:-}"
MISSION="${2:-}"
WORKSPACE_ROOT="/home/creative/.openclaw/workspace"

if [ -z "$PROJECT_NAME" ]; then
    echo "❌ 用法：$0 <项目名称> [任务描述]"
    echo "   示例：$0 my-feature '实现用户记忆系统'"
    exit 1
fi

PROJECT_DIR="${WORKSPACE_ROOT}/${PROJECT_NAME}"
TEST_DIR="${WORKSPACE_ROOT}/${PROJECT_NAME}-test"
BRANCH_NAME="feature/${PROJECT_NAME}"

echo "🚀 开始创建 OpenClaw 工作区..."
echo "   项目：${PROJECT_NAME}"
echo "   分支：${BRANCH_NAME}"
echo "   任务：${MISSION:-未指定}"
echo ""

# 1. 检查是否已存在
if [ -d "$TEST_DIR" ]; then
    echo "❌ 目录已存在：${TEST_DIR}"
    exit 1
fi

if [ -d "$PROJECT_DIR" ]; then
    echo "⚠️  项目目录已存在：${PROJECT_DIR}"
    echo "   直接使用现有目录创建 worktree..."
else
    echo "📁 创建项目目录：${PROJECT_DIR}"
    mkdir -p "$PROJECT_DIR"
    cd "$PROJECT_DIR"
    git init
    git commit --allow-empty -m "Initial commit: ${PROJECT_NAME}"
fi

# 2. 创建 worktree + 分支 (一步完成)
echo "🔗 创建 worktree: ${TEST_DIR}"
echo "🌿 创建分支：${BRANCH_NAME}"
cd "$PROJECT_DIR"
git worktree add -b "$BRANCH_NAME" "${TEST_DIR}"

# 3. Legacy workspace-continuity files are intentionally not copied into project worktrees.

# 4. 生成 mission.md
echo "📝 生成 mission.md..."
cat > "${TEST_DIR}/mission.md" << EOF
# Mission - ${PROJECT_NAME}

**创建时间：** $(date '+%Y-%m-%d %H:%M')
**分支：** ${BRANCH_NAME}
**Worktree:** ${PROJECT_NAME}-test

---

## 任务描述

${MISSION:-待补充}

---

## 进度

- [ ] 任务分析
- [ ] 实施
- [ ] 测试
- [ ] 提交

---

## 笔记

$(date '+%Y-%m-%d') - 工作区创建完成

EOF

# 5. 提交初始文件
echo "💾 提交初始文件..."
cd "$TEST_DIR"
git add -A
git commit -m "chore: 初始化工作区 - ${PROJECT_NAME}"

# 6. 启动 OpenClaw
echo ""
echo "🎉 工作区创建完成！"
echo ""
echo "📂 目录结构:"
echo "   ${PROJECT_DIR}        ← 主工作区 (分支：${BRANCH_NAME})"
echo "   ${PROJECT_NAME}-test   ← 工作区 (推荐在此工作)"
echo ""
echo "📄 文件:"
echo "   ${TEST_DIR}/mission.md  ← 任务说明"
echo ""

# 启动 OpenClaw
if command -v openclaw &> /dev/null; then
    echo "🚀 启动 OpenClaw..."
    echo ""
    cd "$TEST_DIR"
    exec openclaw
else
    echo "⚠️  openclaw 命令未找到，请手动启动"
    echo "   cd ${TEST_DIR}"
    echo "   openclaw"
fi
