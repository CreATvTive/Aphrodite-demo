"""Provider configuration for chatbox v0.

A [`ProviderConfig`](config.py) selects one provider profile by id and carries
the credential reference needed by a real transport.  Credentials are read
from environment variables only; they are never printed, logged, or stored
in a profile.  The default provider/model follows the Phase decision
(DeepSeek V4 Pro).

The config is intentionally tiny: one line switches the provider.  Model
overrides are optional and only change the API model identifier sent to the
provider; they do not change the profile id, display label, or any other
profile field.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping


DEFAULT_PROVIDER_ID = "deepseek"
# Phase default model label.  The actual API model identifier is a provider
# detail; this constant is the documented default and is overridable.
DEFAULT_MODEL_LABEL = "deepseek-chat"


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """Selected provider profile and credential reference.

    ``api_key`` is carried only to be handed to a transport; it must never be
    echoed into errors or logs (the transport layer redacts it).
    """

    provider_id: str
    api_model: str | None
    api_key: str | None
    timeout_sec: float
    max_retries: int

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not self.provider_id:
            raise ValueError("provider_id must be a non-empty string")
        if self.api_model is not None and (
            not isinstance(self.api_model, str) or not self.api_model
        ):
            raise ValueError("api_model must be a non-empty string or None")
        if not isinstance(self.timeout_sec, (int, float)) or isinstance(
            self.timeout_sec, bool
        ) or self.timeout_sec <= 0.0:
            raise ValueError("timeout_sec must be a positive number")
        if (
            not isinstance(self.max_retries, int)
            or isinstance(self.max_retries, bool)
            or self.max_retries < 0
        ):
            raise ValueError("max_retries must be a non-negative int")


def _env(mapping: Mapping[str, str] | None, key: str) -> str | None:
    if mapping is None:
        return os.environ.get(key)
    value = mapping.get(key)
    if value is None or value == "":
        return None
    return value


def load_provider_config(
    *,
    env: Mapping[str, str] | None = None,
    provider_id: str | None = None,
    api_model: str | None = None,
    timeout_sec: float | None = None,
    max_retries: int | None = None,
) -> ProviderConfig:
    """Build a [`ProviderConfig`](config.py) from env + explicit overrides.

    Resolution order for each field: explicit argument > env var > Phase
    default.  Credentials come only from env (``<PROVIDER>_API_KEY`` or the
    shared ``CHATBOX_PROVIDER_API_KEY``); explicit-argument credential
    passing is intentionally not supported to avoid secrets in code.
    """

    resolved_provider = provider_id or _env(env, "CHATBOX_PROVIDER") or DEFAULT_PROVIDER_ID
    resolved_model = api_model or _env(env, "CHATBOX_PROVIDER_MODEL")
    if resolved_model is None:
        # Per-provider default model env fallback.
        resolved_model = _env(env, f"CHATBOX_{resolved_provider.upper()}_MODEL")

    timeout_raw = timeout_sec if timeout_sec is not None else _env(
        env, "CHATBOX_PROVIDER_TIMEOUT_SEC"
    )
    if timeout_raw is None:
        resolved_timeout = 60.0
    else:
        resolved_timeout = float(timeout_raw)
        if resolved_timeout <= 0.0:
            raise ValueError("CHATBOX_PROVIDER_TIMEOUT_SEC must be positive")

    retries_raw = max_retries if max_retries is not None else _env(
        env, "CHATBOX_PROVIDER_MAX_RETRIES"
    )
    if retries_raw is None:
        resolved_retries = 0
    else:
        resolved_retries = int(retries_raw)
        if resolved_retries < 0:
            raise ValueError("CHATBOX_PROVIDER_MAX_RETRIES must be non-negative")

    api_key = (
        _env(env, f"CHATBOX_{resolved_provider.upper()}_API_KEY")
        or _env(env, "CHATBOX_PROVIDER_API_KEY")
    )

    return ProviderConfig(
        provider_id=resolved_provider,
        api_model=resolved_model,
        api_key=api_key,
        timeout_sec=resolved_timeout,
        max_retries=resolved_retries,
    )