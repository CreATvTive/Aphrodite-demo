from __future__ import annotations

from typing import Iterable, List, TypeVar


_T = TypeVar("_T")


def _dedup_keep_order_values(items: Iterable[_T]) -> List[_T]:
    seen = set()
    out: List[_T] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _dedup_keep_order_stripped_nonempty(items: Iterable[object]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        k = str(x).strip()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out
