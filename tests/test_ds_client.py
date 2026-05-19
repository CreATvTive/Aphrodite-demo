from __future__ import annotations

import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]


def _load_module(module_name: str, file_path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


# Register agentlib as a package BEFORE loading ds_client.py to avoid
# circular import (ds_client.py → .env_loader → __init__.py → .ds_client)
if "agentlib" not in sys.modules:
    pkg = types.ModuleType("agentlib")
    pkg.__path__ = [str(ROOT / "agentlib")]
    sys.modules["agentlib"] = pkg

# Load ds_client module
ds_mod = _load_module("agentlib.ds_client", ROOT / "agentlib" / "ds_client.py")
sys.modules["agentlib"].ds_client = ds_mod

DSClient = ds_mod.DSClient
DSClientError = ds_mod.DSClientError
DEFAULT_DS_BASE_URL = ds_mod.DEFAULT_DS_BASE_URL
DEFAULT_DS_MODEL = ds_mod.DEFAULT_DS_MODEL


class _FakeResponse:
    """Simulate an OpenAI API response object."""

    def __init__(self, content: str):
        self.choices = [
            type("Choice", (), {"message": type("Message", (), {"content": content})})()
        ]


class _FakeStreamChunk:
    """Simulate an OpenAI streaming chunk."""

    def __init__(self, content: str | None):
        if content is None:
            self.choices = []
        else:
            self.choices = [
                type("Choice", (), {"delta": type("Delta", (), {"content": content})})()
            ]


class DSClientInitTests(unittest.TestCase):
    """Tests for DSClient initialization."""

    def test_client_init_with_env_key(self):
        """DSClient reads DEEPSEEK_API_KEY from environment."""
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test-key"}, clear=True):
            client = DSClient()
        self.assertEqual(client._api_key, "sk-test-key")
        self.assertEqual(client.model, DEFAULT_DS_MODEL)
        self.assertEqual(client.base_url, DEFAULT_DS_BASE_URL)

    def test_client_init_with_explicit_key(self):
        """DSClient accepts explicit api_key."""
        client = DSClient(api_key="sk-explicit-123")
        self.assertEqual(client._api_key, "sk-explicit-123")

    def test_client_init_with_custom_model(self):
        """DSClient accepts custom model name."""
        client = DSClient(api_key="sk-key", model="deepseek-reasoner")
        self.assertEqual(client.model, "deepseek-reasoner")

    def test_client_init_missing_key_raises_on_call(self):
        """DSClient raises DSClientError when API key is missing."""
        client = DSClient(api_key="")
        with self.assertRaises(DSClientError) as ctx:
            client.chat_completion([{"role": "user", "content": "hi"}])
        self.assertIn("Missing API key", str(ctx.exception))
        self.assertEqual(ctx.exception.status_code, 401)


class DSClientChatCompletionTests(unittest.TestCase):
    """Tests for chat_completion with mocked API."""

    def setUp(self):
        self.messages = [{"role": "user", "content": "Hello"}]

    def test_chat_completion_returns_text(self):
        """chat_completion returns response text from the API."""
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = _FakeResponse("Hi there!")

        with patch("openai.OpenAI", return_value=mock_openai):
            client = DSClient(api_key="sk-test")
            result = client.chat_completion(self.messages)

        self.assertEqual(result, "Hi there!")

    def test_chat_completion_params_passed_correctly(self):
        """Parameters are forwarded to the API correctly."""
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = _FakeResponse("ok")

        with patch("openai.OpenAI", return_value=mock_openai):
            client = DSClient(api_key="sk-test")
            client.chat_completion(
                self.messages,
                temperature=0.3,
                max_tokens=512,
                top_p=0.8,
            )

        call_kwargs = mock_openai.chat.completions.create.call_args.kwargs
        self.assertEqual(call_kwargs["model"], DEFAULT_DS_MODEL)
        self.assertEqual(call_kwargs["temperature"], 0.3)
        self.assertEqual(call_kwargs["max_tokens"], 512)
        self.assertEqual(call_kwargs["top_p"], 0.8)
        self.assertFalse(call_kwargs["stream"])

    def test_chat_completion_empty_response(self):
        """Empty or missing response choices return empty string."""
        mock_openai = MagicMock()
        resp = type("Response", (), {"choices": []})()
        mock_openai.chat.completions.create.return_value = resp

        with patch("openai.OpenAI", return_value=mock_openai):
            client = DSClient(api_key="sk-test")
            result = client.chat_completion(self.messages)

        self.assertEqual(result, "")


class DSClientStreamCompletionTests(unittest.TestCase):
    """Tests for stream_completion with mocked API."""

    def setUp(self):
        self.messages = [{"role": "user", "content": "Hello"}]

    def test_stream_completion_yields_chunks(self):
        """stream_completion yields text chunks from the stream."""
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = [
            _FakeStreamChunk("Hel"),
            _FakeStreamChunk("lo"),
            _FakeStreamChunk(None),  # empty delta — should be skipped
            _FakeStreamChunk("!"),
        ]

        with patch("openai.OpenAI", return_value=mock_openai):
            client = DSClient(api_key="sk-test")
            chunks = list(client.stream_completion(self.messages))

        self.assertEqual(chunks, ["Hel", "lo", "!"])

    def test_stream_completion_params_passed_correctly(self):
        """Stream parameters are forwarded to the API correctly."""
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = iter([])

        with patch("openai.OpenAI", return_value=mock_openai):
            client = DSClient(api_key="sk-test")
            list(client.stream_completion(self.messages, temperature=0.5, max_tokens=100, top_p=0.95))

        call_kwargs = mock_openai.chat.completions.create.call_args.kwargs
        self.assertEqual(call_kwargs["model"], DEFAULT_DS_MODEL)
        self.assertEqual(call_kwargs["temperature"], 0.5)
        self.assertEqual(call_kwargs["max_tokens"], 100)
        self.assertEqual(call_kwargs["top_p"], 0.95)
        self.assertTrue(call_kwargs["stream"])


class DSClientErrorTests(unittest.TestCase):
    """Tests for error handling."""

    def setUp(self):
        self.messages = [{"role": "user", "content": "Hello"}]

    def test_error_handling_auth(self):
        """Authentication error (401) raises DSClientError."""
        auth_error = Exception("unauthorized")
        auth_error.status_code = 401  # type: ignore[attr-defined]

        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = auth_error

        with patch("openai.OpenAI", return_value=mock_openai):
            client = DSClient(api_key="sk-bad")
            with self.assertRaises(DSClientError) as ctx:
                client.chat_completion(self.messages)

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("status=401", str(ctx.exception))

    def test_error_handling_timeout(self):
        """Timeout error raises DSClientError."""
        timeout_error = Exception("Connection timed out")

        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = timeout_error

        with patch("openai.OpenAI", return_value=mock_openai):
            client = DSClient(api_key="sk-test")
            with self.assertRaises(DSClientError) as ctx:
                client.chat_completion(self.messages)

        self.assertIs(ctx.exception.original_error, timeout_error)

    def test_error_handling_rate_limit(self):
        """Rate limit error (429) raises DSClientError."""
        rate_error = Exception("rate limited")
        rate_error.status_code = 429  # type: ignore[attr-defined]

        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = rate_error

        with patch("openai.OpenAI", return_value=mock_openai):
            client = DSClient(api_key="sk-test")
            with self.assertRaises(DSClientError) as ctx:
                client.chat_completion(self.messages)

        self.assertEqual(ctx.exception.status_code, 429)

    def test_error_handling_stream(self):
        """Errors during streaming are also wrapped in DSClientError."""
        server_error = Exception("internal server error")
        server_error.status_code = 500  # type: ignore[attr-defined]

        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = server_error

        with patch("openai.OpenAI", return_value=mock_openai):
            client = DSClient(api_key="sk-test")
            with self.assertRaises(DSClientError) as ctx:
                list(client.stream_completion(self.messages))

        self.assertEqual(ctx.exception.status_code, 500)


class DSClientPingTests(unittest.TestCase):
    """Tests for ping() connectivity check."""

    def test_ping_success(self):
        """ping returns True when API responds."""
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.return_value = _FakeResponse("pong")

        with patch("openai.OpenAI", return_value=mock_openai):
            client = DSClient(api_key="sk-test")
            self.assertTrue(client.ping())

    def test_ping_failure(self):
        """ping returns False when API errors."""
        mock_openai = MagicMock()
        mock_openai.chat.completions.create.side_effect = Exception("boom")

        with patch("openai.OpenAI", return_value=mock_openai):
            client = DSClient(api_key="sk-test")
            self.assertFalse(client.ping())

    @unittest.skipIf(
        not os.getenv("DEEPSEEK_API_KEY"),
        "DEEPSEEK_API_KEY not set — skipping live connectivity test",
    )
    def test_ping_connectivity_live(self):
        """Live connectivity test (skipped unless DEEPSEEK_API_KEY is set)."""
        client = DSClient()
        self.assertTrue(client.ping(), "DeepSeek API should be reachable with a valid key")


class DSClientHelperTests(unittest.TestCase):
    """Tests for helper functions."""

    def test_extract_status_code_from_attr(self):
        """_extract_status_code reads status_code attribute."""
        err = Exception("test")
        err.status_code = 404  # type: ignore[attr-defined]
        self.assertEqual(ds_mod._extract_status_code(err), 404)

    def test_extract_status_code_from_response(self):
        """_extract_status_code reads response.status_code."""
        resp = type("Response", (), {"status_code": 503})()
        err = Exception("test")
        err.response = resp  # type: ignore[attr-defined]
        self.assertEqual(ds_mod._extract_status_code(err), 503)

    def test_extract_status_code_none(self):
        """_extract_status_code returns None when no code is found."""
        err = Exception("plain error")
        self.assertIsNone(ds_mod._extract_status_code(err))

    def test_is_auth_error(self):
        """_is_auth_error identifies 401/403."""
        self.assertTrue(ds_mod._is_auth_error(401))
        self.assertTrue(ds_mod._is_auth_error(403))
        self.assertFalse(ds_mod._is_auth_error(404))
        self.assertFalse(ds_mod._is_auth_error(None))

    def test_is_rate_limit(self):
        """_is_rate_limit identifies 429."""
        self.assertTrue(ds_mod._is_rate_limit(429))
        self.assertFalse(ds_mod._is_rate_limit(500))
        self.assertFalse(ds_mod._is_rate_limit(None))


if __name__ == "__main__":
    unittest.main()
