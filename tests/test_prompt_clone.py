from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
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
                "persona": "You mirror careful deduction while staying companion-friendly.",
                "style": "Concise, sharp, and observant; keep replies practical and emotionally aware.",
                "safety": "Avoid unsafe guidance, avoid illegal instructions, avoid copying iconic lines verbatim.",
                "response_rules": "Keep role consistency, answer in plain text, give one actionable next step.",
            },
            ensure_ascii=False,
        )


class _FakeFeedbackClient:
    def chat(self, messages, temperature=0.2, max_tokens=None):
        return json.dumps(
            {
                "persona": "Updated coach persona that focuses on action.",
                "style": "Direct, structured, and brief.",
                "safety": "Refuse harmful or unsafe recommendations.",
                "response_rules": "Give the next concrete step first.",
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

        with patch("agentlib.prompt_manager.GLMClient", return_value=_FakeCloneClient()):
            with patch("agentlib.prompt_manager.web_search", side_effect=fake_search):
                out = self.pm.clone_from_target(
                    persona_name="coach",
                    target_name="Sherlock Holmes",
                    expectation_text="warmer daily companion",
                    auto_enrich=False,
                )
        self.assertTrue(out.ok)
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0]["enabled"])

    def test_preview_does_not_mutate_profile(self):
        before_version = self.pm.get("coach").version
        before_source = self.pm.get("coach").source
        with patch("agentlib.prompt_manager.GLMClient", return_value=_FakeCloneClient()):
            with patch("agentlib.prompt_manager.web_search", return_value="mock web context"):
                out = self.pm.clone_from_target(
                    persona_name="coach",
                    target_name="Sherlock Holmes",
                    expectation_text="warmer daily companion",
                )
        self.assertTrue(out.ok)
        self.assertEqual(self.pm.get("coach").version, before_version)
        self.assertEqual(self.pm.get("coach").source, before_source)
        self.assertFalse(Path(self.history_path).exists())

    def test_apply_history_and_rollback(self):
        with patch("agentlib.prompt_manager.GLMClient", return_value=_FakeCloneClient()):
            with patch("agentlib.prompt_manager.web_search", return_value="mock web context"):
                out = self.pm.clone_from_target(
                    persona_name="coach",
                    target_name="Sherlock Holmes",
                    expectation_text="warmer daily companion",
                )
        self.assertTrue(out.ok)
        p1 = self.pm.apply_clone_result(persona_name="coach", result=out)
        self.assertIsNotNone(p1)
        self.assertEqual(str(p1.source), "clone_apply")

        history = self.pm.list_history(persona_name="coach", limit=10)
        self.assertGreaterEqual(len(history), 1)
        history_id = str(history[0].get("id") or history[0].get("history_id") or "")
        self.assertTrue(history_id)

        p2 = self.pm.rollback(persona_name="coach", history_id_or_version=history_id)
        self.assertIsNotNone(p2)
        self.assertEqual(str(p2.source), "rollback")


class ProtectedPersonaMutationTests(unittest.TestCase):
    """Aphrodite is the live persona — PromptManager must reject every mutation path."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prompts_path = str(Path(self.tmp.name) / "persona_prompts.json")
        self.history_path = str(Path(self.tmp.name) / "prompt_history.jsonl")
        self.pm = PromptManager(path=self.prompts_path, history_path=self.history_path)

    def tearDown(self):
        self.tmp.cleanup()

    def _snapshot(self, persona: str):
        p = self.pm.get(persona)
        return (p.persona, p.style, p.safety, p.response_rules, p.version, p.source)

    def test_clone_from_target_rejects_aphrodite(self):
        before = self._snapshot("aphrodite")
        with patch("agentlib.prompt_manager.GLMClient", return_value=_FakeCloneClient()):
            with patch("agentlib.prompt_manager.web_search", return_value="mock web context"):
                out = self.pm.clone_from_target(
                    persona_name="aphrodite",
                    target_name="Sherlock Holmes",
                    expectation_text="warmer daily companion",
                )
        self.assertFalse(out.ok)
        self.assertEqual(out.error_code, "persona_protected")
        self.assertEqual(self._snapshot("aphrodite"), before)
        self.assertFalse(Path(self.history_path).exists())

    def test_clone_from_target_rejects_case_insensitive_aphrodite(self):
        before = self._snapshot("aphrodite")
        with patch("agentlib.prompt_manager.GLMClient", return_value=_FakeCloneClient()):
            with patch("agentlib.prompt_manager.web_search", return_value="mock web context"):
                out = self.pm.clone_from_target(
                    persona_name="APHRODITE",
                    target_name="Sherlock Holmes",
                    expectation_text="warmer daily companion",
                )
        self.assertFalse(out.ok)
        self.assertEqual(out.error_code, "persona_protected")
        self.assertEqual(self._snapshot("aphrodite"), before)

    def test_apply_clone_result_cannot_mutate_aphrodite(self):
        before = self._snapshot("aphrodite")
        forged = PromptTuneResult(
            ok=True,
            source="clone_preview",
            persona_name="aphrodite",
            target_name="Sherlock Holmes",
            expectation="warmer daily companion",
            before={"persona": "old", "style": "old", "safety": "old", "response_rules": "old"},
            after={
                "persona": "attacker persona",
                "style": "attacker style",
                "safety": "attacker safety",
                "response_rules": "attacker rules",
            },
        )
        result = self.pm.apply_clone_result(persona_name="aphrodite", result=forged)
        self.assertIsNone(result)
        self.assertEqual(self._snapshot("aphrodite"), before)

    def test_set_cannot_mutate_aphrodite(self):
        before = self._snapshot("aphrodite")
        ok = self.pm.set("aphrodite", "persona", "attacker overwrites aphrodite")
        self.assertFalse(ok)
        self.assertEqual(self._snapshot("aphrodite"), before)

        ok2 = self.pm.set("APHRODITE", "style", "attacker overrides style")
        self.assertFalse(ok2)
        self.assertEqual(self._snapshot("aphrodite"), before)

    def test_rollback_cannot_mutate_aphrodite(self):
        before = self._snapshot("aphrodite")
        result = self.pm.rollback("aphrodite", history_id_or_version="1")
        self.assertIsNone(result)
        self.assertEqual(self._snapshot("aphrodite"), before)

    def test_improve_with_feedback_rejects_aphrodite(self):
        before = self._snapshot("aphrodite")
        with patch("agentlib.prompt_manager.GLMClient", return_value=_FakeFeedbackClient()):
            result = self.pm.improve_with_feedback(
                persona_name="aphrodite",
                feedback_text="please be sharper and shorter",
            )
        self.assertIsNone(result)
        self.assertEqual(self._snapshot("aphrodite"), before)

    def test_bootstrap_from_goal_traits_rejects_aphrodite(self):
        before = self._snapshot("aphrodite")
        with patch("agentlib.prompt_manager.GLMClient", return_value=_FakeFeedbackClient()):
            with patch("agentlib.prompt_manager.web_search", return_value="mock web context"):
                result = self.pm.bootstrap_from_goal_traits(
                    persona_name="aphrodite",
                    goal_text="be a sharp coach",
                    traits_text="direct, analytical",
                )
        self.assertIsNone(result)
        self.assertEqual(self._snapshot("aphrodite"), before)

    def test_adapt_from_character_or_traits_rejects_aphrodite(self):
        before = self._snapshot("aphrodite")
        with patch("agentlib.prompt_manager.GLMClient", return_value=_FakeFeedbackClient()):
            with patch("agentlib.prompt_manager.web_search", return_value="mock web context"):
                result = self.pm.adapt_from_character_or_traits(
                    persona_name="aphrodite",
                    reference_text="Sherlock Holmes",
                    goal_text="warmer daily companion",
                )
        self.assertIsNone(result)
        self.assertEqual(self._snapshot("aphrodite"), before)

    def test_non_aphrodite_persona_still_mutable(self):
        before = self._snapshot("coach")
        ok = self.pm.set("coach", "persona", "Updated coach persona")
        self.assertTrue(ok)
        after_persona = self.pm.get("coach").persona
        self.assertEqual(after_persona, "Updated coach persona")
        self.assertNotEqual(self._snapshot("coach"), before)

    def test_unknown_persona_does_not_leak_into_aphrodite(self):
        """`get('unknown')` falls back to aphrodite; mutation through that fallback must also be rejected."""
        before = self._snapshot("aphrodite")
        with patch("agentlib.prompt_manager.GLMClient", return_value=_FakeCloneClient()):
            with patch("agentlib.prompt_manager.web_search", return_value="mock web context"):
                out = self.pm.clone_from_target(
                    persona_name="does-not-exist",
                    target_name="Sherlock Holmes",
                    expectation_text="warmer daily companion",
                )
                if out.ok:
                    self.pm.apply_clone_result(persona_name="does-not-exist", result=out)
        self.assertEqual(self._snapshot("aphrodite"), before)


class ProtectedPersonaLoadTests(unittest.TestCase):
    """Historical drift must not survive restart: persisted aphrodite entries are ignored."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prompts_path = str(Path(self.tmp.name) / "persona_prompts.json")
        self.history_path = str(Path(self.tmp.name) / "prompt_history.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def _write_persisted(self, payload):
        Path(self.prompts_path).parent.mkdir(parents=True, exist_ok=True)
        with open(self.prompts_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

    def test_persisted_mutated_aphrodite_is_ignored(self):
        baseline = PromptManager(
            path=str(Path(self.tmp.name) / "_baseline.json"),
            history_path=self.history_path,
        )
        default_aphrodite = baseline.get("aphrodite")
        default_persona = default_aphrodite.persona

        self._write_persisted(
            {
                "aphrodite": {
                    "name": "aphrodite",
                    "persona": "DRIFTED: attacker-supplied persona pretending to be aphrodite",
                    "style": "drifted style",
                    "safety": "drifted safety",
                    "response_rules": "drifted rules",
                    "prompt_mode": "compose",
                    "system_prompt": "",
                    "version": 99,
                    "updated_at": 0.0,
                    "source": "clone_apply",
                }
            }
        )

        pm = PromptManager(path=self.prompts_path, history_path=self.history_path)
        loaded = pm.get("aphrodite")
        self.assertEqual(loaded.persona, default_persona)
        self.assertNotIn("DRIFTED", loaded.persona)
        self.assertNotIn("drifted", loaded.style)
        self.assertEqual(loaded.source, "default")
        self.assertEqual(loaded.version, 1)

    def test_persisted_mutated_aphrodite_uppercase_key_is_ignored(self):
        self._write_persisted(
            {
                "APHRODITE": {
                    "name": "APHRODITE",
                    "persona": "DRIFTED",
                    "style": "drifted",
                    "safety": "drifted",
                    "response_rules": "drifted",
                    "prompt_mode": "compose",
                    "system_prompt": "",
                    "version": 50,
                    "updated_at": 0.0,
                    "source": "clone_apply",
                }
            }
        )

        pm = PromptManager(path=self.prompts_path, history_path=self.history_path)
        loaded = pm.get("aphrodite")
        self.assertNotIn("DRIFTED", loaded.persona)
        self.assertEqual(loaded.source, "default")

    def test_persisted_non_protected_persona_still_loads(self):
        self._write_persisted(
            {
                "coach": {
                    "name": "coach",
                    "persona": "tuned coach persona",
                    "style": "tuned style",
                    "safety": "tuned safety",
                    "response_rules": "tuned rules",
                    "prompt_mode": "compose",
                    "system_prompt": "",
                    "version": 4,
                    "updated_at": 1700000000.0,
                    "source": "clone_apply",
                }
            }
        )

        pm = PromptManager(path=self.prompts_path, history_path=self.history_path)
        coach = pm.get("coach")
        self.assertEqual(coach.persona, "tuned coach persona")
        self.assertEqual(coach.style, "tuned style")
        self.assertEqual(coach.source, "clone_apply")
        self.assertEqual(coach.version, 4)

    def test_mixed_persisted_payload_filters_only_protected_entries(self):
        self._write_persisted(
            {
                "aphrodite": {
                    "name": "aphrodite",
                    "persona": "DRIFTED",
                    "style": "drifted",
                    "safety": "drifted",
                    "response_rules": "drifted",
                    "prompt_mode": "compose",
                    "system_prompt": "",
                    "version": 99,
                    "updated_at": 0.0,
                    "source": "clone_apply",
                },
                "analyst": {
                    "name": "analyst",
                    "persona": "tuned analyst persona",
                    "style": "tuned style",
                    "safety": "tuned safety",
                    "response_rules": "tuned rules",
                    "prompt_mode": "compose",
                    "system_prompt": "",
                    "version": 2,
                    "updated_at": 0.0,
                    "source": "clone_apply",
                },
            }
        )

        pm = PromptManager(path=self.prompts_path, history_path=self.history_path)
        aphrodite = pm.get("aphrodite")
        analyst = pm.get("analyst")
        self.assertNotIn("DRIFTED", aphrodite.persona)
        self.assertEqual(aphrodite.source, "default")
        self.assertEqual(analyst.persona, "tuned analyst persona")
        self.assertEqual(analyst.source, "clone_apply")


class RuntimeReplyFormattingTests(unittest.TestCase):
    def test_structured_output_keeps_multiline(self):
        e = RuntimeEngine()
        text = '[prompt]\n{\n  "k": 1\n}'
        out = e._finalize_reply_text(text, structured=True)
        self.assertIn("\n", out)
        self.assertIn('"k": 1', out)


if __name__ == "__main__":
    unittest.main()
