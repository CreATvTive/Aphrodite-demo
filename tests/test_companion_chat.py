from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _load_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


if "agentlib" not in sys.modules:
    pkg = types.ModuleType("agentlib")
    pkg.__path__ = [str(ROOT / "agentlib")]
    sys.modules["agentlib"] = pkg

# TODO: P40 鈥?replace with ds_client after experiment
# _load_module("agentlib.glm_client", ROOT / "agentlib" / "glm_client.py")
_load_module("agentlib.companion_prompt", ROOT / "agentlib" / "companion_prompt.py")
_load_module("agentlib.companion_rag", ROOT / "agentlib" / "companion_rag.py")
companion_mod = _load_module("agentlib.companion_chat", ROOT / "agentlib" / "companion_chat.py")

build_companion_messages = companion_mod.build_companion_messages
companion_reply = companion_mod.companion_reply
companion_reply_stream = companion_mod.companion_reply_stream
companion_prepare_messages = companion_mod.companion_prepare_messages


class CompanionMessageTests(unittest.TestCase):
    def test_build_companion_messages(self):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "system", "content": "ignored"},
            {"role": "tool", "content": "ignored"},
        ]
        messages = build_companion_messages(
            user_text="how are you?",
            history=history,
            system_prompt="SYS",
        )
        self.assertEqual(messages[0], {"role": "system", "content": "SYS"})
        self.assertEqual(messages[1], {"role": "user", "content": "hi"})
        self.assertEqual(messages[2], {"role": "assistant", "content": "hello"})
        self.assertEqual(messages[-1], {"role": "user", "content": "how are you?"})
        self.assertEqual(len(messages), 4)

    def test_build_companion_messages_with_sections_and_rag(self):
        messages = build_companion_messages(
            user_text="test",
            system_sections={
                "persona": "p",
                "style": "s",
                "safety": "safe",
                "response_rules": "rule",
            },
            rag_items=["r1", "r2"],
        )
        sys_text = messages[0]["content"]
        self.assertIn("[persona]", sys_text)
        self.assertIn("[retrieval_context]", sys_text)
        self.assertIn("- r1", sys_text)


class CompanionReplyTests(unittest.TestCase):
    def test_companion_reply_stream(self):
        class FakeClient:
            def stream_completion(self, messages, temperature=0.8, max_tokens=None):
                return iter(["A", "B"])

        with patch("agentlib.companion_chat.DSClient", return_value=FakeClient(), create=True), \
             patch("agentlib.companion_chat.GLMClient", return_value=FakeClient(), create=True):
            chunks = list(companion_reply_stream("hello"))
        self.assertEqual(chunks, ["A", "B"])

    def test_companion_reply_aggregates_stream(self):
        class FakeClient:
            def stream_completion(self, messages, temperature=0.8, max_tokens=None):
                return iter(["A", "B"])

        with patch("agentlib.companion_chat.DSClient", return_value=FakeClient(), create=True), \
             patch("agentlib.companion_chat.GLMClient", return_value=FakeClient(), create=True):
            text = companion_reply("hello")
        self.assertEqual(text, "AB")

    def test_companion_reply_stream_uses_passed_rag_items(self):
        captured = {"messages": None}

        class FakeClient:
            def stream_completion(self, messages, temperature=0.8, max_tokens=None):
                captured["messages"] = messages
                return iter(["OK"])

        with patch("agentlib.companion_chat.DSClient", return_value=FakeClient(), create=True), \
             patch("agentlib.companion_chat.GLMClient", return_value=FakeClient(), create=True):
            list(
                companion_reply_stream(
                    "hello",
                    rag_items=["x1", "x2"],
                    rag_knowledge_base=["kb1", "kb2"],
                )
            )

        self.assertIsNotNone(captured["messages"])
        sys_text = captured["messages"][0]["content"]
        self.assertIn("- x1", sys_text)

    def test_companion_reply_stream_passes_rag_mode(self):
        class FakeClient:
            def stream_completion(self, messages, temperature=0.8, max_tokens=None):
                return iter(["OK"])

        with patch("agentlib.companion_chat.DSClient", return_value=FakeClient(), create=True), \
             patch("agentlib.companion_chat.GLMClient", return_value=FakeClient(), create=True):
            with patch(
                "agentlib.companion_chat.companion_prepare_messages",
                return_value={"messages": [{"role": "user", "content": "hello"}], "rag_items": []},
            ) as mocked:
                list(
                    companion_reply_stream(
                        "hello",
                        rag_items=None,
                        rag_knowledge_base=["kb1"],
                        rag_mode="hybrid",
                    )
                )
        _, kwargs = mocked.call_args
        self.assertEqual(kwargs.get("rag_mode"), "hybrid")

    def test_companion_prepare_messages_returns_rag_meta(self):
        with patch(
            "agentlib.companion_chat.build_rag_package",
            return_value=types.SimpleNamespace(
                items=["ctx1"],
                trace=["t1"],
                queries=["q1"],
                mode_used="hybrid",
                retrieval_used=True,
                skip_reason="",
            ),
        ):
            out = companion_prepare_messages(
                user_text="hello",
                rag_knowledge_base=["k1"],
                rag_mode="hybrid",
            )
        self.assertIn("messages", out)
        self.assertEqual(out["rag_items"], ["ctx1"])
        self.assertEqual(out["rag_mode_used"], "hybrid")
        self.assertTrue(out["rag_retrieval_used"])

    def test_companion_prepare_messages_merges_memory_hits(self):
        with patch("agentlib.companion_chat.retrieve_memory_context", return_value=["mem-a", "mem-b"]):
            with patch(
                "agentlib.companion_chat.build_rag_package",
                return_value=types.SimpleNamespace(
                    items=["ctx1"],
                    trace=["t1"],
                    queries=["q1"],
                    mode_used="hybrid",
                    retrieval_used=True,
                    skip_reason="",
                ),
            ) as mocked_pkg:
                out = companion_prepare_messages(
                    user_text="hello",
                    rag_knowledge_base=["kb1"],
                    rag_mode="hybrid",
                    memory_enabled=True,
                    memory_top_k=2,
                )
        _, kwargs = mocked_pkg.call_args
        self.assertEqual(kwargs["knowledge_base"], ["mem-a", "mem-b", "kb1"])
        self.assertEqual(out["memory_hits"], ["mem-a", "mem-b"])
        self.assertEqual(out["memory_reason"], "retrieved")

    def test_companion_reply_stream_writeback(self):
        class FakeClient:
            def stream_completion(self, messages, temperature=0.8, max_tokens=None):
                return iter(["A", "B"])

        with patch("agentlib.companion_chat.DSClient", return_value=FakeClient(), create=True), \
             patch("agentlib.companion_chat.GLMClient", return_value=FakeClient(), create=True):
            with patch("agentlib.companion_chat.record_turn_memory") as mocked_record:
                chunks = list(companion_reply_stream("hello", rag_items=["x1"], memory_writeback=True))
        self.assertEqual(chunks, ["A", "B"])
        mocked_record.assert_called_once()


if __name__ == "__main__":
    unittest.main()
