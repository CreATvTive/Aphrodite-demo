"""Transport abstraction for provider calls.

A [`ProviderTransport`](transport.py) takes a [`ProviderRequest`](transport.py)
and returns a [`ProviderResponse`](transport.py) or raises a
[`ProviderTransportError`](transport.py).  Two implementations are provided:

* [`FakeTransport`](transport.py): deterministic, in-process transport used by
  tests.  It never performs any network I/O and never reads credentials.
* [`HttpTransport`](transport.py): stdlib ``urllib``-based HTTP transport for
  the OpenAI-compatible chat-completions surface.  It is the only path that
  reads the API key, and it redacts the key from any error it raises.

Both DeepSeek and Kimi expose an OpenAI-compatible ``/chat/completions`` surface,
so a single ``openai_compat`` request adapter is sufficient for v0.  The
adapter puts the system/user messages into the standard ``messages`` array and
requests ``response_format={"type": "text"}``; the two-segment structure-A
output is parsed by [`structure_a.py`](structure_a.py), not by the transport.

Security: the transport never echoes ``api_key`` into errors or logs.  Error
messages carry only a stable code, the provider id, the HTTP status (when
applicable), and a redacted detail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import urllib.error
import urllib.request
from typing import Mapping, Protocol, Sequence


class ProviderTransportError(Exception):
    """Stable, credential-free transport error.

    ``code`` is one of: ``auth``, ``timeout``, ``network``, ``empty``,
    ``http``, ``malformed``.  ``detail`` is redacted and never contains the
    API key.
    """

    def __init__(self, code: str, provider_id: str, detail: str, *, status: int | None = None) -> None:
        self.code = code
        self.provider_id = provider_id
        self.status = status
        self.detail = detail
        super().__init__(f"{code}:{provider_id}:{detail}")


@dataclass(frozen=True, slots=True)
class ProviderMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    provider_id: str
    api_model: str
    base_url: str
    api_key: str | None
    timeout_sec: float
    messages: tuple[ProviderMessage, ...]
    max_tokens: int | None = None
    temperature: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not self.provider_id:
            raise ValueError("provider_id must be non-empty")
        if not isinstance(self.api_model, str) or not self.api_model:
            raise ValueError("api_model must be non-empty")
        if not isinstance(self.base_url, str) or not self.base_url:
            raise ValueError("base_url must be non-empty")
        if not isinstance(self.timeout_sec, (int, float)) or isinstance(self.timeout_sec, bool):
            raise ValueError("timeout_sec must be a number")
        if not self.messages:
            raise ValueError("messages must be non-empty")
        for msg in self.messages:
            if not isinstance(msg, ProviderMessage):
                raise TypeError("messages must be ProviderMessage")
            if msg.role not in ("system", "user", "assistant"):
                raise ValueError(f"unsupported role: {msg.role!r}")


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    provider_id: str
    content: str
    raw: object | None = None


class ProviderTransport(Protocol):
    def call(self, request: ProviderRequest) -> ProviderResponse:
        ...


def _redact(value: str | None) -> str:
    if value is None:
        return "<none>"
    if value == "":
        return "<empty>"
    return "<redacted>"


def _build_openai_compat_body(request: ProviderRequest) -> dict:
    body: dict = {
        "model": request.api_model,
        "messages": [
            {"role": m.role, "content": m.content} for m in request.messages
        ],
        "stream": False,
    }
    if request.max_tokens is not None:
        body["max_tokens"] = request.max_tokens
    if request.temperature is not None:
        body["temperature"] = request.temperature
    return body


def _extract_content(provider_id: str, payload: object) -> str:
    if not isinstance(payload, dict):
        raise ProviderTransportError("malformed", provider_id, "response is not a JSON object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderTransportError("empty", provider_id, "response has no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ProviderTransportError("malformed", provider_id, "choice is not an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ProviderTransportError("malformed", provider_id, "message missing")
    content = message.get("content")
    if not isinstance(content, str) or content == "":
        raise ProviderTransportError("empty", provider_id, "content empty")
    return content


class HttpTransport:
    """Stdlib HTTP transport for the OpenAI-compatible chat-completions surface.

    Only this class reads ``api_key``.  It sends it as a bearer token and
    redacts it from every error.  No real request is issued by the test
    suite; tests use [`FakeTransport`](transport.py) instead.
    """

    __slots__ = ("_opener",)

    def __init__(self, opener: urllib.request.OpenerDirector | None = None) -> None:
        self._opener = opener

    def call(self, request: ProviderRequest) -> ProviderResponse:
        url = request.base_url.rstrip("/") + "/chat/completions"
        body = _build_openai_compat_body(request)
        data = json.dumps(body).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if request.api_key:
            headers["Authorization"] = f"Bearer {request.api_key}"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            if self._opener is not None:
                response = self._opener.open(req, timeout=request.timeout_sec)
            else:
                response = urllib.request.urlopen(req, timeout=request.timeout_sec)
        except urllib.error.HTTPError as exc:
            status = exc.code
            code = "auth" if status in (401, 403) else "http"
            raise ProviderTransportError(
                code, request.provider_id, f"HTTP {status}", status=status
            ) from exc
        except TimeoutError as exc:
            raise ProviderTransportError(
                "timeout", request.provider_id, "request timed out"
            ) from exc
        except urllib.error.URLError as exc:
            if getattr(exc, "reason", None) and "timed out" in str(exc.reason).lower():
                raise ProviderTransportError(
                    "timeout", request.provider_id, "request timed out"
                ) from exc
            raise ProviderTransportError(
                "network", request.provider_id, "network error"
            ) from exc
        except OSError as exc:
            raise ProviderTransportError(
                "network", request.provider_id, "OS error"
            ) from exc
        try:
            with response:
                raw_bytes = response.read()
        except TimeoutError as exc:
            raise ProviderTransportError(
                "timeout", request.provider_id, "read timed out"
            ) from exc
        except OSError as exc:
            raise ProviderTransportError(
                "network", request.provider_id, "read error"
            ) from exc
        try:
            payload = json.loads(raw_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderTransportError(
                "malformed", request.provider_id, "response not valid JSON"
            ) from exc
        content = _extract_content(request.provider_id, payload)
        return ProviderResponse(
            provider_id=request.provider_id, content=content, raw=payload
        )


@dataclass
class FakeTransport:
    """Deterministic in-process transport for tests.

    ``responder`` is a callable mapping a [`ProviderRequest`](transport.py) to
    a raw model-output string (the two-segment structure-A text).  If
    ``responder`` is omitted, a static reply is returned.  ``failure`` can be
    set to a ``ProviderTransportError`` to simulate a transport failure.

    The fake transport never reads ``api_key`` and never performs I/O.
    """

    responder: object | None = None
    failure: ProviderTransportError | None = None
    calls: list[ProviderRequest] = field(default_factory=list)

    def call(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        if self.failure is not None:
            raise self.failure
        if self.responder is None:
            content = "你好。\n---\n{}"
        else:
            content = self.responder(request)  # type: ignore[misc]
        if not isinstance(content, str):
            raise TypeError("FakeTransport responder must return a str")
        return ProviderResponse(
            provider_id=request.provider_id, content=content, raw=None
        )