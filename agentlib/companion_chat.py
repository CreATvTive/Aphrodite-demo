from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional

from ._utils import _dedup_keep_order_values
from .glm_client import GLMClient
from .companion_prompt import build_system_prompt_sections, render_system_prompt
from .companion_rag import build_rag_context, build_rag_package, render_rag_block
from .companion_rag import record_turn_memory, retrieve_memory_context

"""
对话装配层（Companion Chat）。

关系索引：
- Prompt 分段来源：
  - agentlib/companion_prompt.py
- 检索结果来源：
  - agentlib/companion_rag.py:build_rag_package
- 模型传输层：
  - agentlib/glm_client.py:GLMClient
- Demo 调用入口：
  - Aphrodite demo ver.A.py

职责：
1) 组装最终消息列表（system + history + user）
2) 请求检索上下文（或接收外部传入的 rag_items）
3) 同时提供流式回复 API 和调试友好的 prepare API
"""

DEFAULT_COMPANION_SYSTEM_PROMPT = (
    "你是一个温柔、稳定、不过度说教的对话陪伴助手。"
    "优先理解用户感受，再给出简洁、可执行的回应。"
)


def build_companion_messages(
    user_text: str,
    history: Optional[List[Dict[str, Any]]] = None,
    system_prompt: Optional[str] = None,
    system_sections: Optional[Dict[str, str]] = None,
    rag_items: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    # 消息结构与 OpenAI 兼容，由 GLMClient.stream_chat() 直接消费。
    messages: List[Dict[str, str]] = []
    if system_prompt:
        system_content = system_prompt.strip()
    else:
        sections = system_sections or build_system_prompt_sections(
            persona=DEFAULT_COMPANION_SYSTEM_PROMPT,
        )
        system_content = render_system_prompt(sections)

    rag_block = render_rag_block(rag_items or [])
    if rag_block:
        system_content = (system_content + "\n\n" + rag_block).strip()

    messages.append(
        {
            "role": "system",
            "content": system_content,
        }
    )

    for item in history or []:
        role = str(item.get("role", "")).strip()
        content = str(item.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": (user_text or "").strip()})
    return messages


def companion_reply_stream(
    user_text: str,
    history: Optional[List[Dict[str, Any]]] = None,
    system_prompt: Optional[str] = None,
    system_sections: Optional[Dict[str, str]] = None,
    rag_items: Optional[List[str]] = None,
    rag_knowledge_base: Optional[List[str]] = None,
    rag_top_k: int = 3,
    rag_mode: Optional[str] = None,
    memory_enabled: bool = True,
    memory_top_k: int = 4,
    memory_writeback: bool = True,
    temperature: float = 0.8,
) -> Iterator[str]:
    # 运行时快速路径：先取上下文，再流式输出 token，并在结束后写回长期记忆。
    prepared = companion_prepare_messages(
        user_text=user_text,
        history=history,
        system_prompt=system_prompt,
        system_sections=system_sections,
        rag_items=rag_items,
        rag_knowledge_base=rag_knowledge_base,
        rag_top_k=rag_top_k,
        rag_mode=rag_mode,
        memory_enabled=memory_enabled,
        memory_top_k=memory_top_k,
    )
    selected_rag_items = list(prepared.get("rag_items") or [])
    messages = prepared["messages"]
    client = GLMClient()
    stream = client.stream_chat(messages=messages, temperature=temperature)

    if not memory_writeback:
        return stream

    def _wrapped() -> Iterator[str]:
        chunks: List[str] = []
        for piece in stream:
            chunks.append(piece)
            yield piece
        assistant_text = "".join(chunks).strip()
        if assistant_text:
            # 写回显式 retrieval 条目 + 回合文本抽取条目，形成长期记忆闭环。
            record_turn_memory(
                user_text=user_text,
                assistant_text=assistant_text,
                explicit_items=selected_rag_items,
            )

    return _wrapped()


def companion_prepare_messages(
    user_text: str,
    history: Optional[List[Dict[str, Any]]] = None,
    system_prompt: Optional[str] = None,
    system_sections: Optional[Dict[str, str]] = None,
    rag_items: Optional[List[str]] = None,
    rag_knowledge_base: Optional[List[str]] = None,
    rag_top_k: int = 3,
    rag_mode: Optional[str] = None,
    memory_enabled: bool = True,
    memory_top_k: int = 4,
) -> Dict[str, Any]:
    # 调试/观测路径：同时返回检索诊断信息与最终 messages。
    selected_rag_items = rag_items
    rag_trace = []
    rag_queries = []
    rag_mode_used = rag_mode or "auto"
    rag_retrieval_used = bool(selected_rag_items)
    rag_skip_reason = ""
    memory_hits: List[str] = []
    memory_reason = "disabled"
    if selected_rag_items is None:
        kb = [str(x).strip() for x in (rag_knowledge_base or []) if str(x).strip()]
        if memory_enabled:
            memory_hits = retrieve_memory_context(
                user_text,
                history=history,
                k=max(1, int(memory_top_k)),
            )
            if memory_hits:
                kb = _dedup_keep_order(memory_hits + kb)
                memory_reason = "retrieved"
            else:
                memory_reason = "empty"
        pkg = build_rag_package(
            user_text=user_text,
            knowledge_base=kb,
            top_k=rag_top_k,
            rag_mode=rag_mode,
            history=history,
        )
        selected_rag_items = pkg.items
        rag_trace = pkg.trace
        rag_queries = pkg.queries
        rag_mode_used = pkg.mode_used
        rag_retrieval_used = bool(pkg.retrieval_used)
        rag_skip_reason = str(pkg.skip_reason or "")
    else:
        memory_reason = "provided_rag_items"
    messages = build_companion_messages(
        user_text=user_text,
        history=history,
        system_prompt=system_prompt,
        system_sections=system_sections,
        rag_items=selected_rag_items,
    )
    return {
        "messages": messages,
        "rag_items": selected_rag_items,
        "rag_trace": rag_trace,
        "rag_queries": rag_queries,
        "rag_mode_used": rag_mode_used,
        "rag_retrieval_used": rag_retrieval_used,
        "rag_skip_reason": rag_skip_reason,
        "memory_hits": memory_hits,
        "memory_reason": memory_reason,
    }


def companion_reply(
    user_text: str,
    history: Optional[List[Dict[str, Any]]] = None,
    system_prompt: Optional[str] = None,
    system_sections: Optional[Dict[str, str]] = None,
    rag_items: Optional[List[str]] = None,
    rag_knowledge_base: Optional[List[str]] = None,
    rag_top_k: int = 3,
    rag_mode: Optional[str] = None,
    memory_enabled: bool = True,
    memory_top_k: int = 4,
    memory_writeback: bool = True,
    temperature: float = 0.8,
) -> str:
    # 非流式便捷封装：适合只关心完整文本的调用方。
    return "".join(
        companion_reply_stream(
            user_text=user_text,
            history=history,
            system_prompt=system_prompt,
            system_sections=system_sections,
            rag_items=rag_items,
            rag_knowledge_base=rag_knowledge_base,
            rag_top_k=rag_top_k,
            rag_mode=rag_mode,
            memory_enabled=memory_enabled,
            memory_top_k=memory_top_k,
            memory_writeback=memory_writeback,
            temperature=temperature,
        )
    ).strip()


def _dedup_keep_order(items: List[str]) -> List[str]:
    return _dedup_keep_order_values(items)
