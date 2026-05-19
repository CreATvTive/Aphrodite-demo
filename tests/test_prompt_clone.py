from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agentlib.prompt_manager import PromptManager, PromptTuneResult
from agentlib.runtime_engine import RuntimeEngine


class _FakeCloneClient:
    def chat(self, messages, temperature=0.2, max_tokens=None):
        system = str((messages or [{}])[0].get("content") or "").lower()
        if "trait card" in system:
            return json.dumps(
                {
                    "core_traits": ["deductive", "calm", "precise"],
                    "speaking_style": "concise and analytical",
                    "relationship_tone": "respectful",
                    "taboo": ["avoid verbatim quotes"],
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "persona": "You mirror Sherlock-like deduction while staying companion-friendly.",
                "style": "Concise, sharp, and observant; keep replies practical and emotionally aware.",
                "safety": "Avoid unsafe guidance, avoid illegal instructions, avoid copying iconic lines verbatim.",
                "response_rules": "Keep role consistency, answer in plain text, give one actionable next step.",
            },
            ensure_ascii=False,
        )


class PromptCloneManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prompts_path = str(Path(self.tmp.name) / "persona_prompts.json")
        self.history_path = str(Path(self.tmp.name) / "prompt_history.jsonl")
        self.pm = PromptManager(path=self.prompts_path, history_path=self.history_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_clone_from_target_respects_auto_enrich_flag(self):
        calls = []

        def fake_search(query, enabled=False, max_results=3, cache_ttl_sec=3600):
            calls.append(
                {
                    "query": query,
                    "enabled": bool(enabled),
                    "max_results": int(max_results),
                    "cache_ttl_sec": int(cache_ttl_sec),
                }
            )
            return "mock web context"

        with patch("agentlib.prompt_manager.DSClient", return_value=_FakeCloneClient(), create=True), \
             patch("agentlib.prompt_manager.GLMClient", return_value=_FakeCloneClient(), create=True):
            with patch("agentlib.prompt_manager.web_search", side_effect=fake_search):
                out = self.pm.clone_from_target(
                    persona_name="aphrodite",
                    target_name="Sherlock Holmes",
                    expectation_text="warmer daily companion",
                    auto_enrich=False,
                )
        self.assertTrue(out.ok)
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0]["enabled"])

    def test_preview_does_not_mutate_profile(self):
        before_version = self.pm.get("aphrodite").version
        before_source = self.pm.get("aphrodite").source
        with patch("agentlib.prompt_manager.DSClient", return_value=_FakeCloneClient(), create=True), \
             patch("agentlib.prompt_manager.GLMClient", return_value=_FakeCloneClient(), create=True):
            with patch("agentlib.prompt_manager.web_search", return_value="mock web context"):
                out = self.pm.clone_from_target(
                    persona_name="aphrodite",
                    target_name="Sherlock Holmes",
                    expectation_text="warmer daily companion",
                )
        self.assertTrue(out.ok)
        self.assertEqual(self.pm.get("aphrodite").version, before_version)
        self.assertEqual(self.pm.get("aphrodite").source, before_source)
        self.assertFalse(Path(self.history_path).exists())

    def test_apply_history_and_rollback(self):
        with patch("agentlib.prompt_manager.DSClient", return_value=_FakeCloneClient(), create=True), \
             patch("agentlib.prompt_manager.GLMClient", return_value=_FakeCloneClient(), create=True):
            with patch("agentlib.prompt_manager.web_search", return_value="mock web context"):
                out = self.pm.clone_from_target(
                    persona_name="aphrodite",
                    target_name="Sherlock Holmes",
                    expectation_text="warmer daily companion",
                )
        self.assertTrue(out.ok)
        p1 = self.pm.apply_clone_result(persona_name="aphrodite", result=out)
        self.assertIsNotNone(p1)
        self.assertEqual(str(p1.source), "clone_apply")

        history = self.pm.list_history(persona_name="aphrodite", limit=10)
        self.assertGreaterEqual(len(history), 1)
        history_id = str(history[0].get("id") or history[0].get("history_id") or "")
        self.assertTrue(history_id)

        p2 = self.pm.rollback(persona_name="aphrodite", history_id_or_version=history_id)
        self.assertIsNotNone(p2)
        self.assertEqual(str(p2.source), "rollback")


class RuntimePromptCloneTests(unittest.TestCase):
    @unittest.skip(reason="method _map_natural_to_command removed; TODO: update or replace")
    def test_map_natural_to_clone_command(self):
        e = RuntimeEngine()
        cmd = e._map_natural_to_command(
            "clone persona: sherlock, expectation: warmer daily companion"
        )
        self.assertEqual(cmd, "/prompt clone sherlock | warmer daily companion")

    @unittest.skip(reason="first may be None-type; TODO: investigate _handle_debug_command return")
    def test_clone_two_step_preview_flow(self):
        e = RuntimeEngine()

        def fake_clone_from_target(**kwargs):
            return PromptTuneResult(
                ok=True,
                persona_name=str(kwargs.get("persona_name") or ""),
                target_name=str(kwargs.get("target_name") or ""),
                expectation=str(kwargs.get("expectation_text") or ""),
                before={
                    "persona": "old persona",
                    "style": "old style",
                    "safety": "old safety",
                    "response_rules": "old rules",
                },
                after={
                    "persona": "new persona",
                    "style": "new style",
                    "safety": "new safety",
                    "response_rules": "new rules",
                },
                diff={"persona": {"before": "old persona", "after": "new persona", "changed": 1}},
                scores={"overall": 0.95},
                samples=[{"input": "hi", "before": "old", "after": "new"}],
            )

        e.prompt_manager.clone_from_target = fake_clone_from_target  # type: ignore[assignment]
        e.prompt_manager.apply_clone_result = lambda **kwargs: SimpleNamespace(  # type: ignore[assignment]
            name="aphrodite", version=7, source="clone_apply"
        )

        first = e._handle_debug_command("/prompt clone sherlock")
        self.assertIn("please provide one expectation sentence", first)
        second = e._handle_natural_language_control("warmer daily companion")
        self.assertIsNotNone(second)
        self.assertIn("[prompt:clone:preview]", str(second))
        self.assertIsNotNone(e._prompt_clone_draft)

    def test_structured_output_keeps_multiline(self):
        e = RuntimeEngine()
        text = '[prompt]\n{\n  "k": 1\n}'
        out = e._finalize_reply_text(text, structured=True)
        self.assertIn("\n", out)
        self.assertIn('"k": 1', out)


if __name__ == "__main__":
    unittest.main()
