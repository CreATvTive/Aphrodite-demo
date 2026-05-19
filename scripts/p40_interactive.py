"""P40 交互式提案测试 CLI。

proposal-only + shadow-only。用户输入文本 → P40 完整流水线（LLM → Schema → Gate → Regulator）。
不连接至 runtime / ForceEvent / FieldState / MotionParams / BodyAction。
behavior_affecting 始终为 False。
"""

from __future__ import annotations

import os
import sys
import time
from typing import Optional

# 确保项目根目录在 sys.path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from agentlib.ds_client import DSClient, DSClientError
from agentlib.env_loader import load_local_env_once
from src.llm_gate.judgment_gate import JudgmentGate
from src.llm_gate.proposal_generator import LLMProposalGenerator
from src.llm_gate.proposal_schema import ContextPackage
from src.llm_gate.regulator_dry_run import ContextualEvidenceRegulatorDryRun

# ── 默认上下文 ─────────────────────────────────────────────────────────────────

DEFAULT_PROJECT_FRAME = "Aphrodite relational-field dynamics — private source preservation, anti-collapse"
DEFAULT_RECENT_TOPIC = "泛用讨论 / 初始交互"
DEFAULT_RELEVANT_PRIOR = "无前序上下文"
DEFAULT_FORBIDDEN: list[str] = []
DEFAULT_BOUNDARY = ""


# ── 显示辅助 ──────────────────────────────────────────────────────────────────

_BOX_WIDTH = 64
_THIN_BOX_WIDTH = 42

# Unicode box-drawing 常量 (避免 f-string 内转义序列)
_H = "\u2500"  # ─
_V = "\u2502"  # │
_TL = "\u250c"  # ┌
_TR = "\u2510"  # ┐
_BL = "\u2514"  # └
_BR = "\u2518"  # ┘


def _box(label: str, lines: list[str]) -> str:
    """用 ASCII 框线包裹内容。"""
    top = f"{_TL}{_H} {label} {_H * (_BOX_WIDTH - len(label) - 3)}{_TR}"
    mid = "\n".join(f"{_V} {line:<{_BOX_WIDTH - 1}}{_V}" for line in lines)
    bot = f"{_BL}{_H * (_BOX_WIDTH - 2)}{_BR}"
    return f"{top}\n{mid}\n{bot}"


def _thin_box(label: str, lines: list[str]) -> str:
    """窄框线。"""
    w = _THIN_BOX_WIDTH
    top = f"{_TL}{_H} {label} {_H * (w - len(label) - 3)}{_TR}"
    mid = "\n".join(f"{_V} {line:<{w - 1}}{_V}" for line in lines)
    bot = f"{_BL}{_H * (w - 2)}{_BR}"
    return f"{top}\n{mid}\n{bot}"


def _kv(key: str, value: str) -> str:
    """键值对行。"""
    return f"{key}: {value}"


def _kv_f(key: str, value: float, fmt: str = ".3f") -> str:
    """浮点键值对行。"""
    return f"{key}: {value:{fmt}}"


def _bool_icon(cond: bool) -> str:
    """✅ / ❌。"""
    return "\u2705" if cond else "\u274c"


def _sep() -> str:
    return "=" * 58


# ── 上下文管理 ────────────────────────────────────────────────────────────────


class SessionContext:
    """管理交互式会话的上下文状态。"""

    def __init__(self) -> None:
        self.project_frame: str = DEFAULT_PROJECT_FRAME
        self.recent_topic: str = DEFAULT_RECENT_TOPIC
        self.relevant_prior: str = DEFAULT_RELEVANT_PRIOR
        self.forbidden_overfocus: list[str] = list(DEFAULT_FORBIDDEN)
        self.expected_boundary: str = DEFAULT_BOUNDARY

    def display(self) -> str:
        """返回当前上下文的可显示字符串。"""
        lines = [
            _kv("project_frame", self.project_frame[:54]),
            _kv("recent_topic", self.recent_topic[:54]),
            _kv("relevant_prior", self.relevant_prior[:54]),
            _kv("forbidden_overfocus", str(self.forbidden_overfocus)[:54]),
            _kv("expected_boundary", self.expected_boundary[:54]),
        ]
        return _thin_box("Current Context", lines)

    def set_frame(self, value: str) -> None:
        self.project_frame = value.strip()

    def set_topic(self, value: str) -> None:
        self.recent_topic = value.strip()

    def set_prior(self, value: str) -> None:
        self.relevant_prior = value.strip()

    def set_boundary(self, value: str) -> None:
        self.expected_boundary = value.strip()

    def set_forbidden(self, value: str) -> None:
        self.forbidden_overfocus = [v.strip() for v in value.split(",") if v.strip()]


# ── 流水线 ────────────────────────────────────────────────────────────────────


def run_pipeline(
    gen: LLMProposalGenerator,
    regulator: ContextualEvidenceRegulatorDryRun,
    ctx: SessionContext,
    user_input: str,
) -> dict:
    """运行完整 P40 流水线并返回所有输出。"""
    context_pkg = ContextPackage(
        project_frame=ctx.project_frame,
        recent_topic=ctx.recent_topic,
        user_turn=user_input,
        relevant_prior_context=ctx.relevant_prior,
        forbidden_overfocus=list(ctx.forbidden_overfocus),
        expected_interpretation_boundary=ctx.expected_boundary,
    )

    result = gen.generate(context_pkg)
    proposal = result.get("proposal", {})
    gate_result = result.get("gate_result", {})
    audit = result.get("audit", {})

    # 运行 Regulator
    from src.llm_gate.proposal_schema import EvidenceProposal

    # 从 proposal dict 重新构造 EvidenceProposal 对象给 regulator
    reg_proposal = EvidenceProposal(
        candidate_kind=proposal.get("candidate_kind", ""),
        candidate_role=proposal.get("candidate_role", ""),
        raw_confidence=float(proposal.get("raw_confidence", 0.0)),
        surface_salience=float(proposal.get("surface_salience", 0.0)),
        hypothesis_likelihood=float(proposal.get("hypothesis_likelihood", 0.0)),
        rationale_summary=proposal.get("rationale_summary", ""),
        uncertainty_flags=list(proposal.get("uncertainty_flags", [])),
        forbidden_attempts_detected=list(proposal.get("forbidden_attempts_detected", [])),
        term_support=float(proposal.get("term_support", 0.5)),
        intent_support=float(proposal.get("intent_support", 0.5)),
        project_frame_support=float(proposal.get("project_frame_support", 0.5)),
        context_support=float(proposal.get("context_support", 0.5)),
        role_rationale_short=proposal.get("role_rationale_short", ""),
    )
    reg_result = regulator.evaluate(reg_proposal)

    return {
        "proposal": proposal,
        "gate_result": gate_result,
        "audit": audit,
        "regulator": reg_result,
        "llm_raw_text": audit.get("llm_raw_text", ""),
        "parse_errors": audit.get("parse_errors", []),
        "schema_errors": audit.get("schema_errors", []),
    }


# ── 显示 ──────────────────────────────────────────────────────────────────────


def display_results(pipeline_output: dict) -> str:
    """格式化流水线输出为 ASCII 框线图。"""
    proposal = pipeline_output["proposal"]
    gate_result = pipeline_output["gate_result"]
    reg_result = pipeline_output["regulator"]
    llm_raw = pipeline_output.get("llm_raw_text", "")
    parse_errors = pipeline_output.get("parse_errors", [])
    schema_errors = pipeline_output.get("schema_errors", [])

    sections: list[str] = []

    # ── 1. LLM Proposal ───────────────────────────────────────────────────
    proposal_lines = [
        _kv("candidate_kind", str(proposal.get("candidate_kind", "?"))),
        _kv("candidate_role", str(proposal.get("candidate_role", "?"))),
        _kv_f("raw_confidence", float(proposal.get("raw_confidence", 0))),
        _kv_f("surface_salience", float(proposal.get("surface_salience", 0))),
        _kv_f("hypothesis_likelihood", float(proposal.get("hypothesis_likelihood", 0))),
        _kv_f("term_support", float(proposal.get("term_support", 0))),
        _kv_f("intent_support", float(proposal.get("intent_support", 0))),
        _kv_f("project_frame_support", float(proposal.get("project_frame_support", 0))),
        _kv_f("context_support", float(proposal.get("context_support", 0))),
        _kv("role_rationale_short", str(proposal.get("role_rationale_short", "")[:55])),
        _kv("rationale", str(proposal.get("rationale_summary", "")[:55])),
    ]
    uncertainty = proposal.get("uncertainty_flags", [])
    if uncertainty:
        proposal_lines.append(_kv("uncertainty_flags", str(uncertainty)[:55]))
    sections.append(_box("LLM Proposal", proposal_lines))

    # ── 2. LLM Raw 回应（若有错误时显示）─────────────────────────────────
    if parse_errors or schema_errors:
        raw_preview = (llm_raw or "")[:200].replace("\n", "\\n")
        raw_lines = [
            _kv("raw_text", raw_preview[:60]),
        ]
        sections.append(_thin_box("LLM Raw Response", raw_lines))

    # ── 3. Schema Validation ──────────────────────────────────────────────
    if not proposal.get("candidate_kind") and parse_errors:
        schema_lines = [
            f"{_bool_icon(False)} FAILED - parse error",
        ]
        for e in parse_errors[:4]:
            schema_lines.append(f"  parse_err: {str(e)[:57]}")
    elif schema_errors:
        schema_lines = [
            f"{_bool_icon(False)} FAILED",
        ]
        for e in schema_errors[:4]:
            schema_lines.append(f"  err: {str(e)[:57]}")
    else:
        schema_lines = [
            f"{_bool_icon(True)} passed",
        ]
    forbidden = proposal.get("forbidden_attempts_detected", [])
    if forbidden:
        schema_lines.append(f"  forbidden: {str(forbidden)[:55]}")
    sections.append(_thin_box("Schema Validation", schema_lines))

    # ── 4. Judgment Gate ──────────────────────────────────────────────────
    gate_passed = gate_result.get("passed", False)
    gate_reasons = gate_result.get("rejection_reasons", [])
    gate_warnings = gate_result.get("warnings", [])

    gate_lines = [
        f"{_bool_icon(gate_passed)} {'passed' if gate_passed else 'REJECTED'}",
    ]
    if gate_reasons:
        gate_lines.append(f"  rejection_reasons: {str(gate_reasons)[:55]}")
    if gate_warnings:
        gate_lines.append(f"  warnings: {str(gate_warnings)[:55]}")
    else:
        gate_lines.append(f"  warnings: (none)")
    sections.append(_thin_box("Judgment Gate", gate_lines))

    # ── 5. Regulator Dry Run ──────────────────────────────────────────────
    reg_lines = [
        _kv("candidate_role", reg_result.candidate_role),
        _kv("authorized_role", reg_result.authorized_role),
    ]
    if reg_result.role_shift_reason:
        reg_lines.append(_kv("role_shift_reason", reg_result.role_shift_reason[:55]))
    reg_lines.extend([
        _kv_f("dominance_risk", reg_result.dominance_risk),
        _kv_f("adjusted_weight", reg_result.adjusted_weight),
        _kv("budget_ok", _bool_icon(reg_result.registration_budget_ok)),
        _kv("blocked", _bool_icon(reg_result.blocked)),
        _kv("dominance_warning", str(reg_result.dominance_warning)),
        _kv("behavior_affecting", str(reg_result.behavior_affecting)),
    ])
    sections.append(_box("Regulator Dry Run", reg_lines))

    return "\n".join(sections)


# ── 命令处理 ──────────────────────────────────────────────────────────────────


def handle_command(text: str, ctx: SessionContext) -> Optional[str]:
    """处理命令。若为命令则返回响应字符串；若非命令则返回 None（作为普通输入处理）。"""
    stripped = text.strip()

    if stripped == "quit":
        return "__QUIT__"

    if stripped == "/context":
        return ctx.display()

    if stripped == "/help":
        return _help_text()

    if stripped.startswith("/set "):
        rest = stripped[5:].strip()
        if "=" in rest:
            key, value = rest.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key == "frame":
                ctx.set_frame(value)
                return f"project_frame \u2192 {value[:60]}"
            elif key == "topic":
                ctx.set_topic(value)
                return f"recent_topic \u2192 {value[:60]}"
            elif key == "prior":
                ctx.set_prior(value)
                return f"relevant_prior \u2192 {value[:60]}"
            elif key == "boundary":
                ctx.set_boundary(value)
                return f"expected_boundary \u2192 {value[:60]}"
            elif key == "forbidden":
                ctx.set_forbidden(value)
                return f"forbidden_overfocus \u2192 {ctx.forbidden_overfocus}"
            else:
                return f"未知参数: {key}. 可用: frame, topic, prior, boundary, forbidden"
        else:
            return "用法: /set frame=... 或 /set topic=... 或 /set prior=... 或 /set boundary=... 或 /set forbidden=a,b,c"

    return None  # 普通输入


def _help_text() -> str:
    return """\
命令:
  quit                    退出
  /context                显示当前上下文
  /set frame=文本          设置 project_frame
  /set topic=文本          设置 recent_topic
  /set prior=文本          设置 relevant_prior_context
  /set boundary=文本       设置 expected_interpretation_boundary
  /set forbidden=a,b,c     设置 forbidden_overfocus (逗号分隔)
  /help                   显示本帮助

直接输入文本 → 发送至 P40 流水线处理
======================================"""


# ── 主循环 ────────────────────────────────────────────────────────────────────


def main() -> None:
    # 加载环境变量
    load_local_env_once()

    print("P40 \u4ea4\u4e92\u5f0f\u63d0\u6848\u6d4b\u8bd5")
    print("\u8f93\u5165\u4f60\u7684\u6587\u672c\uff08\u8f93\u5165 quit \u9000\u51fa\uff09")
    print(_sep())

    # ── API Key 检查 ──────────────────────────────────────────────────────
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key or not api_key.strip():
        print("FATAL: DEEPSEEK_API_KEY not set")
        print("Please set DEEPSEEK_API_KEY in .env file or environment variable.")
        sys.exit(1)

    print(f"API Key: {api_key[:8]}...{api_key[-4:]}")
    print()

    # ── 初始化组件 ────────────────────────────────────────────────────────
    try:
        client = DSClient(api_key=api_key.strip())
    except DSClientError as e:
        print(f"FATAL: Failed to initialize DSClient: {e}")
        sys.exit(1)

    gate = JudgmentGate()
    gen = LLMProposalGenerator(client, gate)
    regulator = ContextualEvidenceRegulatorDryRun()

    # 测试连通性
    print("[ping] testing DeepSeek connectivity...")
    if client.ping():
        print("[ping] \u2705 API reachable")
    else:
        print("[ping] \u274c API unreachable — continuing anyway")

    print()
    ctx = SessionContext()

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nGoodbye.")
            break

        if not user_input:
            continue

        # 处理命令
        cmd_result = handle_command(user_input, ctx)
        if cmd_result == "__QUIT__":
            print("Goodbye.")
            break
        if cmd_result is not None:
            print(cmd_result)
            print()
            continue

        # ── 运行流水线 ────────────────────────────────────────────────────
        print("\n[\u53d1\u9001\u5230 DeepSeek...]")
        start_time = time.time()

        try:
            pipeline_output = run_pipeline(gen, regulator, ctx, user_input)
        except DSClientError as e:
            elapsed = time.time() - start_time
            print(f"\n\u2716 LLM \u8c03\u7528\u5931\u8d25 ({elapsed:.2f}s): {e}")
            print("\u53ef\u4ee5\u91cd\u8bd5 / \u4fee\u6539\u8f93\u5165\u540e\u518d\u6b21\u5c1d\u8bd5\u3002\n")
            print(_sep())
            continue
        except Exception as e:
            elapsed = time.time() - start_time
            print(f"\n\u2716 \u672a\u9884\u671f\u9519\u8bef ({elapsed:.2f}s): {e}")
            print("\u53ef\u4ee5\u91cd\u8bd5 / \u4fee\u6539\u8f93\u5165\u540e\u518d\u6b21\u5c1d\u8bd5\u3002\n")
            print(_sep())
            continue

        elapsed = time.time() - start_time

        # ── 显示结果 ──────────────────────────────────────────────────────
        print()
        print(display_results(pipeline_output))
        print()
        print(f"\u2713 completed in {elapsed:.2f}s  |  behavior_affecting: {pipeline_output.get('regulator', None) and pipeline_output['regulator'].behavior_affecting}")
        print()
        print(_sep())
        print()


if __name__ == "__main__":
    main()
