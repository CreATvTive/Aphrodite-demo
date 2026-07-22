"""Provider registry and frozen DeepSeek/Kimi profiles for chatbox v0.

The registry is the single source of provider profiles.  A profile is a
read-only description of how to adapt a request for one provider; it never
holds credentials and never performs I/O.  Credentials live only in
[`ProviderConfig`](config.py) and are never echoed into profiles, errors, or
logs (see the transport layer).

Profile fields follow the project safeguard that distinguishes provider
profile, API model identifier, display label, alias, reasoning parameter,
tool capability, and cache behavior.  Only the provider-switch layer is in
scope for task card 4; reasoning/tool/cache knobs are declared but left at
their Phase default and are not wired into a call path in this card.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    """A read-only provider profile.

    Fields:

    * ``profile_id``: stable registry key (e.g. ``"deepseek"``).  This is the
      value switched by one configuration line.
    * ``display_label``: human-facing label only; never used as a request
      identifier.
    * ``api_model``: the API model identifier sent to the provider.  Defaults
      are the Phase-frozen values; a config override replaces this string.
    * ``base_url``: provider chat-completions endpoint base.
    * ``api_style``: request/response adapter style.  v0 supports
      ``"openai_compat"`` (DeepSeek and Kimi both expose an OpenAI-compatible
      chat-completions surface) and ``"fake"`` (deterministic test transport).
    * ``supports_reasoning``: whether the provider exposes a reasoning
      parameter.  Declared for the future structure-B boundary; not wired into
      a call path in this card.
    * ``supports_tools``: whether the provider exposes tool calling.  Declared
      only; v0 structure-A does not use tools.
    * ``cache_behavior``: provider cache behavior label.  Declared only.
    """

    profile_id: str
    display_label: str
    api_model: str
    base_url: str
    api_style: str
    supports_reasoning: bool = False
    supports_tools: bool = False
    cache_behavior: str = "none"


_DEFAULT_DEEPSEEK = ProviderProfile(
    profile_id="deepseek",
    display_label="DeepSeek",
    # Phase default model: DeepSeek V4 Pro (cheap, good Chinese).  The exact
    # public API model identifier is a provider-side detail; this default is
    # overridable via config without changing the profile.
    api_model="deepseek-chat",
    base_url="https://api.deepseek.com/v1",
    api_style="openai_compat",
    supports_reasoning=False,
    supports_tools=False,
    cache_behavior="none",
)

_DEFAULT_KIMI = ProviderProfile(
    profile_id="kimi",
    display_label="Kimi",
    api_model="moonshot-v1-8k",
    base_url="https://api.moonshot.cn/v1",
    api_style="openai_compat",
    supports_reasoning=False,
    supports_tools=False,
    cache_behavior="none",
)

DEFAULT_PROFILES: Mapping[str, ProviderProfile] = {
    _DEFAULT_DEEPSEEK.profile_id: _DEFAULT_DEEPSEEK,
    _DEFAULT_KIMI.profile_id: _DEFAULT_KIMI,
}


class ProviderRegistry:
    """Registry of provider profiles, keyed by ``profile_id``.

    The registry is the only authority for which profiles exist.  It is
    constructible with the frozen defaults, with extra profiles, or with
    overrides for the default profiles (e.g. a different ``api_model``).  It
    never stores credentials.
    """

    __slots__ = ("_profiles",)

    def __init__(
        self,
        *,
        defaults: Mapping[str, ProviderProfile] | None = None,
        extra: Mapping[str, ProviderProfile] | None = None,
        overrides: Mapping[str, ProviderProfile] | None = None,
    ) -> None:
        base = dict(DEFAULT_PROFILES if defaults is None else defaults)
        if extra is not None:
            for key, profile in extra.items():
                base[key] = profile
        if overrides is not None:
            for key, profile in overrides.items():
                if key not in base:
                    raise KeyError(
                        f"cannot override unknown provider profile: {key!r}"
                    )
                base[key] = profile
        for profile in base.values():
            if not isinstance(profile, ProviderProfile):
                raise TypeError("registry values must be ProviderProfile")
        self._profiles: dict[str, ProviderProfile] = dict(base)

    def profile(self, profile_id: str) -> ProviderProfile:
        if not isinstance(profile_id, str) or not profile_id:
            raise KeyError("profile_id must be a non-empty string")
        try:
            return self._profiles[profile_id]
        except KeyError as exc:
            raise KeyError(
                f"unknown provider profile: {profile_id!r}; "
                f"known: {sorted(self._profiles)}"
            ) from exc

    @property
    def profile_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))

    def __contains__(self, profile_id: object) -> bool:
        return isinstance(profile_id, str) and profile_id in self._profiles


def build_default_registry() -> ProviderRegistry:
    """Return a registry with the frozen DeepSeek/Kimi defaults."""
    return ProviderRegistry()