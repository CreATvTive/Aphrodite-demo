#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日简报生成器
- 获取时事新闻
- 获取科技动态
- 收集工作记录
- 生成 markdown + 图片
"""

import os
import sys
import json
import subprocess
from datetime import datetime
from pathlib import Path

WORKSPACE_ROOT = Path("/home/creative/.openclaw/workspace")
BRIEFING_DIR = WORKSPACE_ROOT / "briefings"

def get_git_commits(repo_path: Path, since: str = "yesterday") -> list:
    """获取 git 提交记录"""
    try:
        result = subprocess.run(
            ["git", "log", "--since", since, "--oneline", "--max-count", "10"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split("\n")
    except Exception:
        pass
    return []

def scan_repos() -> list:
    """扫描所有 git 仓库"""
    repos = []
    for item in WORKSPACE_ROOT.iterdir():
        if item.is_dir() and (item / ".git").exists():
            repos.append(item)
        # 检查 worktree
        if item.is_dir() and (item / ".git").is_file():
            repos.append(item)
    return repos

def collect_work_summary() -> str:
    """收集工作记录"""
    summary = []
    repos = scan_repos()
    
    for repo in repos:
        commits = get_git_commits(repo)
        if commits:
            summary.append(f"### 📂 {repo.name}\n```")
            summary.extend(commits)
            summary.append("```")
    
    return "\n\n".join(summary) if summary else "无变更记录"

def generate_markbriefing_template(news: str, tech: str, work: str) -> str:
    """生成 markdown 简报"""
    today = datetime.now().strftime("%Y-%m-%d")
    
    return f"""# 📰 每日简报

**日期：** {today}  
**生成时间：** {datetime.now().strftime("%H:%M")}

---

## 📰 时事新闻

{news if news else "_暂无内容_"}

---

## 🔬 科技动态

{tech if tech else "_暂无内容_"}

---

## 📝 工作记录

{work}

---

_由 OpenClaw 自动生成 | 明日同一时间更新_
"""

def main():
    print("📰 生成每日简报...")
    print(f"   工作区：{WORKSPACE_ROOT}")
    print(f"   输出：{BRIEFING_DIR}")
    print()
    
    BRIEFING_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    
    # 收集工作记录
    print("📝 收集工作记录...")
    work_summary = collect_work_summary()
    
    # 生成基础 markdown（新闻部分留空，由 Agent 填充）
    print("📄 生成简报模板...")
    template = generate_markbriefing_template(
        news="_由 Agent 填充_",
        tech="_由 Agent 填充_",
        work=work_summary
    )
    
    output_file = BRIEFING_DIR / f"briefing_{today}.md"
    output_file.write_text(template, encoding="utf-8")
    
    print(f"✅ 简报模板生成：{output_file}")
    print()
    print("📋 下一步：")
    print("   1. 调用 web_search 获取新闻和科技动态")
    print("   2. 更新简报内容")
    print("   3. (可选) 生成图片版本")
    print()
    
    # 输出文件路径
    print(f"OUTPUT_FILE={output_file}")

if __name__ == "__main__":
    main()
