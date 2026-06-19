#!/bin/bash
# daily-briefing.sh - 生成每日简报
# 用法：./scripts/daily-briefing.sh [输出目录]

set -e

OUTPUT_DIR="${1:-/home/creative/.openclaw/workspace/briefings}"
WORKSPACE_ROOT="/home/creative/.openclaw/workspace"
TODAY=$(date '+%Y-%m-%d')

echo "📰 生成每日简报..."
echo "   日期：${TODAY}"
echo "   输出：${OUTPUT_DIR}"
echo ""

mkdir -p "$OUTPUT_DIR"

# 1. 获取时事新闻（调用 OpenClaw web_search）
echo "📰 获取时事新闻..."
NEWS_FILE="${OUTPUT_DIR}/news_${TODAY}.md"

cat > "$NEWS_FILE" << 'EOF'
# 每日简报

**日期：** DATE_PLACEHOLDER

---

## 📰 时事新闻

NEWS_PLACEHOLDER

---

## 🔬 科技动态

TECH_PLACEHOLDER

---

## 📝 昨日工作

WORK_PLACEHOLDER

---

_由 OpenClaw 自动生成_
EOF

# 2. 获取 git 提交记录
echo "📝 收集工作记录..."
WORK_SUMMARY=""
for repo in "$WORKSPACE_ROOT"/*/; do
    if [ -d "$repo/.git" ]; then
        REPO_NAME=$(basename "$repo")
        COMMITS=$(cd "$repo" && git log --since="yesterday" --oneline 2>/dev/null | head -10 || true)
        if [ -n "$COMMITS" ]; then
            WORK_SUMMARY="${WORK_SUMMARY}
### ${REPO_NAME}
\`\`\`
${COMMITS}
\`\`\`
"
        fi
    fi
done

# 3. 替换占位符（新闻部分留给 Agent 填充）
sed -i "s/DATE_PLACEHOLDER/${TODAY}/g" "$NEWS_FILE"

echo "✅ 简报框架生成完成：${NEWS_FILE}"
echo ""
echo "📋 下一步：调用 Agent 填充新闻和科技内容"

# 输出文件路径供 Agent 使用
echo "NEWS_FILE=${NEWS_FILE}"
echo "WORK_SUMMARY<<EOF"
echo "$WORK_SUMMARY"
echo "EOF"
