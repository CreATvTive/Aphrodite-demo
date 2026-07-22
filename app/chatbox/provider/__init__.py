"""P2 task-card 4: provider abstraction and structure-A call for chatbox v0.

This package implements the registry-based provider boundary frozen by
[`docs/chatbox/phase-plan-v0.md`](../../docs/chatbox/phase-plan-v0.md) section A
("对话模型" / "调用结构"):

* a provider registry with DeepSeek and Kimi profiles, switchable by one
  configuration line;
* a transport abstraction so tests use a deterministic fake transport and no
  real provider request is ever issued from this package;
* a structure-A single call that returns both the reply text and the structured
  state increment, parsed from a two-segment model output with safe degradation;
* a clear boundary for the future structure-B (async separation) switch.

It does NOT apply the parsed increment to any attractor or field state.  Writer
application is task card 5; the parser here only validates and returns the
increment, dropping unknown dimensions and illegal values as the safe default.
"""

from __future__ import annotations

from app.chatbox.provider.config import (
    ProviderConfig,
    load_provider_config,
)
from app.chatbox.provider.registry import (
    DEFAULT_PROFILES,
    ProviderProfile,
    ProviderRegistry,
    build_default_registry,
)
from app.chatbox.provider.structure_a import (
    INCREMENT_AMPLITUDE_CAP,
    INCREMENT_DELIMITER,
    INCREMENT_VALUE_RANGE,
    ParsedReply,
    StructureACaller,
    parse_structure_a,
)
from app.chatbox.provider.transport import (
    FakeTransport,
    HttpTransport,
    ProviderMessage,
    ProviderRequest,
    ProviderResponse,
    ProviderTransport,
    ProviderTransportError,
)

__all__ = [
    "DEFAULT_PROFILES",
    "FakeTransport",
    "HttpTransport",
    "INCREMENT_AMPLITUDE_CAP",
    "INCREMENT_DELIMITER",
    "INCREMENT_VALUE_RANGE",
    "ParsedReply",
    "ProviderConfig",
    "ProviderMessage",
    "ProviderProfile",
    "ProviderRegistry",
    "ProviderRequest",
    "ProviderResponse",
    "ProviderTransport",
    "ProviderTransportError",
    "StructureACaller",
    "build_default_registry",
    "load_provider_config",
    "parse_structure_a",
]