from __future__ import annotations

import os
from typing import Any, Dict, Iterator, List

from .env_loader import load_local_env_once

DEFAULT_DS_BASE_URL = "https://api.deepseek.com"
DEFAULT_DS_MODEL = "deepseek-chat"


class DSClientError(RuntimeError):
    """DeepSeek client error with structured context."""

    def __init__(
        self,
        message: str,
        *,
        model: str,
        status_code: int | None = None,
        original_error: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.model = model
        self.status_code = status_code
        self.original_error = original_error


def _load_api_key() -> str:
    load_local_env_once()
    return (os.getenv("DEEPSEEK_API_KEY") or "").strip()


def _is_auth_error(status_code: int | None) -> bool:
    return status_code in (401, 403)


def _is_rate_limit(status_code: int | None) -> bool:
    return status_code == 429


def _extract_status_code(error: Exception) -> int | None:
    code = getattr(error, "status_code", None)
    if isinstance(code, int):
        return code
    response = getattr(error, "response", None)
    response_code = getattr(response, "status_code", None)
    if isinstance(response_code, int):
        return response_code
    return None


class DSClient:
    """DeepSeek API client using OpenAI-compatible SDK.

    Usage::

        client = DSClient()
        reply = client.chat_completion([{"role": "user", "content": "Hello"}])
        for chunk in client.stream_completion([{"role": "user", "content": "Hello"}]):
            print(chunk, end="")
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_DS_MODEL,
        base_url: str = DEFAULT_DS_BASE_URL,
    ) -> None:
        self._api_key = api_key.strip() if api_key is not None else _load_api_key()
        self.model = model
        self.base_url = base_url

    def _get_client(self):
        """Lazy-import and initialize the OpenAI client."""
        try:
            from openai import OpenAI
        except ImportError as e:
            raise DSClientError(
                "openai package is not available. Install with: pip install openai",
                model=self.model,
                original_error=e,
            ) from e

        if not self._api_key:
            raise DSClientError(
                "Missing API key. Set DEEPSEEK_API_KEY environment variable or pass api_key explicitly.",
                model=self.model,
                status_code=401,
            )

        return OpenAI(api_key=self._api_key, base_url=self.base_url)

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 256,
        top_p: float = 0.9,
    ) -> str:
        """Send chat completion request, return response text.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            temperature: Sampling temperature (0.0–1.0).
            max_tokens: Maximum tokens in the response.
            top_p: Nucleus sampling parameter.

        Returns:
            The model's response text.

        Raises:
            DSClientError: On API errors (auth, rate limit, timeout, etc.).
        """
        client = self._get_client()
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
                stream=False,
            )
        except Exception as e:
            status = _extract_status_code(e)
            raise DSClientError(
                f"DeepSeek chat completion failed. model={self.model}, status={status}",
                model=self.model,
                status_code=status,
                original_error=e,
            ) from e

        content = response.choices[0].message.content if response.choices else ""
        return str(content or "").strip()

    def stream_completion(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 256,
        top_p: float = 0.9,
    ) -> Iterator[str]:
        """Stream chat completion, yield text chunks.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            temperature: Sampling temperature (0.0–1.0).
            max_tokens: Maximum tokens in the response.
            top_p: Nucleus sampling parameter.

        Yields:
            Text chunks from the streaming response.

        Raises:
            DSClientError: On API errors.
        """
        client = self._get_client()
        try:
            stream = client.chat.completions.create(
                model=self.model,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
                stream=True,
            )
        except Exception as e:
            status = _extract_status_code(e)
            raise DSClientError(
                f"DeepSeek stream completion failed. model={self.model}, status={status}",
                model=self.model,
                status_code=status,
                original_error=e,
            ) from e

        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def ping(self) -> bool:
        """Test API connectivity with a minimal request.

        Returns:
            True if the API is reachable and the key is valid.
        """
        try:
            self.chat_completion(
                [{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            return True
        except DSClientError:
            return False
