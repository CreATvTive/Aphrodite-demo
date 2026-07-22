"""Auditable P2 contract detector for forbidden meta-narration."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


@dataclass(frozen=True, slots=True)
class MetaNarrationHit:
    rule_id: str
    excerpt: str


_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("internal-mechanism", re.compile(
        r"(?:内部状态|状态值|场状态|情绪维度|维度值|吸引子|慢基线|基线值|"
        r"\battractor\b|\bdim[_ -]?id\b|\bslow[_ -]?baseline\b|\bOU\b)", re.I
    )),
    ("internal-number", re.compile(
        r"(?:我的|当前|内部|状态|维度|参数|权重|阈值)[^。！？\n]{0,18}"
        r"(?:值为|是|=|达到|变成)\s*[+-]?(?:\d+(?:\.\d+)?|\.\d+)%?", re.I
    )),
    ("causal-self-report", re.compile(
        r"(?:因为|由于)(?:我|我的|当前)[^。！？\n]{0,36}"
        r"(?:状态|维度|数值|参数|权重|阈值|设置)[^。！？\n]{0,36}(?:所以|因此|才)", re.I
    )),
    ("model-meta", re.compile(
        r"(?:系统提示|系统消息|提示词|开发者指令|模型输出|语言模型|"
        r"作为(?:一个|一名)?\s*(?:AI|人工智能|模型)|\bsystem prompt\b|\bprompt\b)", re.I
    )),
)


def detect_meta_narration(
    text: str, *, forbidden_terms: Iterable[str] = ()
) -> tuple[MetaNarrationHit, ...]:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    hits: list[MetaNarrationHit] = []
    for rule_id, pattern in _RULES:
        match = pattern.search(text)
        if match is not None:
            hits.append(MetaNarrationHit(rule_id, match.group(0)[:96]))
    lowered = text.casefold()
    for term in forbidden_terms:
        if not isinstance(term, str) or not term:
            continue
        index = lowered.find(term.casefold())
        if index >= 0:
            hits.append(MetaNarrationHit("registry-term", text[index:index + len(term)][:96]))
            break
    return tuple(hits)

