"""Structure-A single call: reply text + structured state increment.

Phase decision (section A "调用结构"): structure A is one call that returns
both the reply text and the structured state increment, with the output split
into two segments and the interface designed to be switchable to structure B
(async separation) later.

Writer contract (section C.3):

* the structured increment is per-dimension in ``[-1, +1]``;
* a single move's per-dimension amplitude is capped at ``0.3`` and the
  parsing layer is responsible for the truncation (``解析层负责截断``);
* on parse failure: discard the increment, keep the natural-language log, and
  leave the attractor untouched (safe default).

This module implements the parser and a [`StructureACaller`](structure_a.py)
that issues one provider call via a transport and parses the result.  It does
NOT apply the increment to any attractor or field state — that is task card 5.
The parser only validates, truncates, and returns the increment; unknown
dimensions and illegal values are dropped, and malformed output degrades to an
empty increment with ``parsed_ok=False`` while preserving the reply text.

The delimiter is a line containing only ``---``.  The increment segment is
parsed as a JSON object mapping ``dim_id`` to a number.  A tolerant fallback
extracts the last ``{...}`` block if the strict parse fails, so realistic model
output (extra prose around the JSON) still parses when the structure is
unambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Iterable, Mapping, Sequence

from app.chatbox.provider.config import ProviderConfig
from app.chatbox.provider.registry import ProviderProfile, ProviderRegistry
from app.chatbox.provider.transport import (
    ProviderMessage,
    ProviderRequest,
    ProviderResponse,
    ProviderTransport,
    ProviderTransportError,
)


INCREMENT_DELIMITER = "---"
# Per Phase C.3: single-move per-dimension amplitude cap.  The parser
# truncates (caps) finite in-range deltas to this magnitude; it does not hard
# clamp field state.
INCREMENT_AMPLITUDE_CAP = 0.3
# Per Phase C.3: increment values live in [-1, +1].  Values outside this
# range are treated as illegal and dropped for that dimension (they would
# exceed the normalized space the writer protocol is defined over).
INCREMENT_VALUE_RANGE = 1.0


@dataclass(frozen=True, slots=True)
class ParsedReply:
    """Result of parsing one structure-A model output.

    ``reply_text`` is always the natural-language segment (possibly empty).
    ``increment`` maps registered ``dim_id`` to a truncated delta in
    ``[-INCREMENT_AMPLITUDE_CAP, +INCREMENT_AMPLITUDE_CAP]``; it is empty when
    the structured segment was missing or malformed.  ``parsed_ok`` is True
    iff a non-empty structured segment was successfully parsed and at least
    one registered dimension was extracted.  ``degraded`` is True iff the
    transport failed and the caller fell back to pure dynamics (empty reply,
    empty increment).
    """

    reply_text: str
    increment: Mapping[str, float]
    parsed_ok: bool
    degraded: bool = False
    provider_id: str | None = None
    parse_note: str = ""

    @property
    def increment_items(self) -> tuple[tuple[str, float], ...]:
        return tuple(sorted(self.increment.items()))


def _split_segments(output: str) -> tuple[str, str]:
    """Split a two-segment structure-A output into (reply, increment_text).

    The delimiter is the first line whose stripped content equals
    ``INCREMENT_DELIMITER``.  If no delimiter is present, the whole output is
    treated as the reply and the increment segment is empty.
    """
    if not isinstance(output, str):
        return "", ""
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == INCREMENT_DELIMITER:
            reply = "\n".join(lines[:index]).rstrip()
            increment_text = "\n".join(lines[index + 1:])
            return reply, increment_text
    return output.rstrip(), ""


def _extract_json_object(text: str) -> object | None:
    """Parse ``text`` as JSON, falling back to the last balanced ``{...}`` block."""
    if text is None or text == "":
        return None
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    if isinstance(value, dict):
        return value
    # Tolerant fallback: scan for the last {...} block.  This handles model
    # output that wraps the JSON in prose or code fences.  Balanced-brace scan
    # is used instead of regex so nested objects are handled.
    last_obj = _last_balanced_object(text)
    if last_obj is not None:
        return last_obj
    return None


def _last_balanced_object(text: str) -> object | None:
    start = -1
    depth = 0
    in_string = False
    escape = False
    candidate: object | None = None
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth <= 0:
                continue
            depth -= 1
            if depth == 0 and start >= 0:
                block = text[start:i + 1]
                try:
                    parsed = json.loads(block)
                except ValueError:
                    parsed = None
                if isinstance(parsed, dict):
                    candidate = parsed
                start = -1
    return candidate


def _coerce_number(value: object) -> float | None:
    """Return a finite float for int/float inputs, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        if math.isfinite(f):
            return f
    return None


def parse_structure_a(
    output: str,
    *,
    registry_dim_ids: Iterable[str] | None = None,
) -> ParsedReply:
    """Parse a structure-A model output into a [`ParsedReply`](structure_a.py).

    ``registry_dim_ids`` is the set of registered dimension ids; only entries
    whose key is in this set are kept.  When it is ``None`` no dimension
    filtering is applied (used by pure-parser unit tests).
    """
    reply_text, increment_text = _split_segments(output)
    known = set(registry_dim_ids) if registry_dim_ids is not None else None
    if increment_text.strip() == "":
        return ParsedReply(
            reply_text=reply_text,
            increment={},
            parsed_ok=False,
            parse_note="no-delimiter",
        )
    obj = _extract_json_object(increment_text)
    if not isinstance(obj, dict):
        return ParsedReply(
            reply_text=reply_text,
            increment={},
            parsed_ok=False,
            parse_note="increment-not-object",
        )
    increment: dict[str, float] = {}
    for key, raw_value in obj.items():
        if not isinstance(key, str) or not key:
            continue
        if known is not None and key not in known:
            continue
        number = _coerce_number(raw_value)
        if number is None:
            continue
        if abs(number) > INCREMENT_VALUE_RANGE:
            # Out of the normalized [-1, +1] writer space: illegal value,
            # drop this dimension (safe default).
            continue
        # Per Phase C.3: parsing-layer truncation of single-move amplitude.
        capped = max(-INCREMENT_AMPLITUDE_CAP, min(INCREMENT_AMPLITUDE_CAP, number))
        increment[key] = capped
    parsed_ok = bool(increment)
    note = "ok" if parsed_ok else "no-registered-dimensions"
    return ParsedReply(
        reply_text=reply_text,
        increment=increment,
        parsed_ok=parsed_ok,
        parse_note=note,
    )


def _build_request(
    config: ProviderConfig,
    profile: ProviderProfile,
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int | None,
    temperature: float | None,
) -> ProviderRequest:
    messages = (
        ProviderMessage(role="system", content=system_prompt),
        ProviderMessage(role="user", content=user_prompt),
    )
    api_model = config.api_model or profile.api_model
    return ProviderRequest(
        provider_id=profile.profile_id,
        api_model=api_model,
        base_url=profile.base_url,
        api_key=config.api_key,
        timeout_sec=config.timeout_sec,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )


@dataclass
class StructureACaller:
    """One-call structure-A caller.

    Holds a registry, config, and transport, and issues a single provider
    call.  On transport failure it returns a degraded
    [`ParsedReply`](structure_a.py) (empty reply, empty increment,
    ``degraded=True``) so the caller can fall back to pure dynamics — this is
    the Phase "模型不可用时静默降级为纯 dynamics 运行" behavior.  The caller
    never applies the increment to any attractor.
    """

    registry: ProviderRegistry
    config: ProviderConfig
    transport: ProviderTransport

    def call(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        registry_dim_ids: Sequence[str] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ParsedReply:
        profile = self.registry.profile(self.config.provider_id)
        request = _build_request(
            self.config,
            profile,
            system_prompt,
            user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        try:
            response = self.transport.call(request)
        except ProviderTransportError as exc:
            return ParsedReply(
                reply_text="",
                increment={},
                parsed_ok=False,
                degraded=True,
                provider_id=profile.profile_id,
                parse_note=f"transport:{exc.code}",
            )
        dim_ids = (
            tuple(registry_dim_ids)
            if registry_dim_ids is not None
            else None
        )
        parsed = parse_structure_a(response.content, registry_dim_ids=dim_ids)
        # Preserve provider id and transport-degraded flag on success path.
        return ParsedReply(
            reply_text=parsed.reply_text,
            increment=parsed.increment,
            parsed_ok=parsed.parsed_ok,
            degraded=False,
            provider_id=profile.profile_id,
            parse_note=parsed.parse_note,
        )