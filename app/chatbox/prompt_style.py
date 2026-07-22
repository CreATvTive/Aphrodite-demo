"""Bounded, non-diagnostic expression-style projection for P2 dialogue."""

from __future__ import annotations

from dataclasses import dataclass
import math

from app.chatbox.expression_gate import GateProjection
from app.chatbox.field_dynamics import FieldSnapshot
from app.chatbox.field_runtime import RegistryProxy


@dataclass(frozen=True, slots=True)
class PromptStyle:
    length_instruction: str
    tone_instruction: str

    @property
    def instruction(self) -> str:
        return f"表达方式：{self.length_instruction}{self.tone_instruction}"


class PromptStyleProjector:
    """Reduce a dynamic gated field projection to two finite style choices.

    Returned text contains no registry identifier, temporary label, value,
    threshold, mechanism name, or causal state explanation.
    """

    def project(
        self,
        *,
        registry: RegistryProxy,
        snapshot: FieldSnapshot,
        gate: GateProjection,
    ) -> PromptStyle:
        if len(snapshot.dimensions) != registry.length or len(gate.weights) != registry.length:
            raise ValueError("registry, snapshot, and gate lengths must match")
        weighted: list[float] = []
        movement: list[float] = []
        for registration, dimension, weight in zip(
            registry.registrations, snapshot.dimensions, gate.weights
        ):
            if dimension.dim_id != registration.dim_id or weight.dim_id != registration.dim_id:
                raise ValueError("registry projection order mismatch")
            if not all(math.isfinite(value) for value in (dimension.value, dimension.velocity, weight.weight)):
                raise ValueError("style projection requires finite values")
            weighted.append(dimension.value * weight.weight)
            movement.append(abs(dimension.velocity) * weight.weight)
        if not weighted:
            raise ValueError("style projection requires at least one dimension")
        center = sum(weighted) / len(weighted)
        activity = sum(abs(value) for value in weighted) / len(weighted) + sum(movement) / len(movement)
        if activity < 0.18:
            length = "保持简短。"
        elif activity < 0.62:
            length = "适度展开。"
        else:
            length = "可以从容展开，但不要变成说明文。"
        if center < -0.16:
            tone = "语气柔和，留有余地。"
        elif center > 0.16:
            tone = "语气清晰直接，但不要命令对方。"
        else:
            tone = "语气亲近自然，不刻意热络。"
        return PromptStyle(length, tone)


def opaque_dimension_aliases(count: int) -> tuple[str, ...]:
    """Return stable alphabetic output keys without exposing registry ids."""
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        raise ValueError("count must be a positive int")
    aliases: list[str] = []
    for ordinal in range(count):
        value = ordinal
        chars: list[str] = []
        while True:
            chars.append(chr(ord("a") + value % 26))
            value = value // 26 - 1
            if value < 0:
                break
        aliases.append("".join(reversed(chars)))
    return tuple(aliases)


def build_system_prompt(style: PromptStyle, aliases: tuple[str, ...]) -> str:
    """Build the model-visible prompt with opaque structure-A keys only."""
    if not aliases or any(not alias.isalpha() or not alias.isascii() for alias in aliases):
        raise ValueError("aliases must be non-empty ASCII alphabetic keys")
    key_list = ", ".join(f'"{alias}"' for alias in aliases)
    return (
        "你以 Aphrodite 的身份参与一段持续的关系性对话。"
        "不要把自己说成通用助手、角色、产品、工具或情绪标签。"
        f"{style.instruction}"
        "直接说想说的话，不解释自己的生成过程，也不谈系统、提示、模型或指令。"
        "输出分为自然语言与一个机器读取的小对象，中间单独一行写 ---。"
        f"小对象只能使用这些不透明键：{key_list}；值应是很小的有限变化，不需要解释。"
    )


def build_user_prompt(history: tuple[tuple[str, str], ...], current_text: str) -> str:
    """Create bounded multi-turn context without field or style internals."""
    recent = history[-12:]
    parts = ["延续下面这段对话，不要复述角色标记："]
    for role, content in recent:
        label = "对方" if role == "user" else "Aphrodite"
        parts.append(f"{label}：{content[:1200]}")
    parts.append(f"对方：{current_text}")
    return "\n".join(parts)[-12000:]

