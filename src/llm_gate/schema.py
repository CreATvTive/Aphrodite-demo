"""Judgment Gate 的数据结构 — 纯数据类型，无逻辑。"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class GateResult:
    """Judgment Gate 的判断结果。

    通过的片段标记 [experimental]；不通过的片段记录拒绝原因。
    Gate 输出不写入场状态。
    """

    passed: bool = False
    """是否通过 Gate 检查。"""

    rejection_reasons: List[str] = field(default_factory=list)
    """拒绝原因列表。每个元素对应一条命中的规则（如 'service_language'）。"""

    filtered_text: str = ""
    """通过的文本（可能被修剪）。未通过时为空字符串。"""

    warnings: List[str] = field(default_factory=list)
    """通过但有风险的警告（不导致拒绝）。"""

    experimental_marker: bool = True
    """始终为 True — 所有通过 Gate 的片段标记 [experimental]."""
