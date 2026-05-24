from __future__ import annotations

import unittest
from unittest.mock import patch

from agentlib.persona_router import (
    AUTO_ROUTE_CANDIDATES,
    PersonaRouteDecision,
    detect_persona_from_text,
)


def _route(text: str, state=None):
    return detect_persona_from_text(text, state or {})


class AphroditeNotInAutoRouteCandidatesTests(unittest.TestCase):
    """Aphrodite is reserved for explicit configuration — auto-routing must never select it."""

    def test_auto_route_candidates_excludes_aphrodite(self):
        self.assertNotIn("aphrodite", AUTO_ROUTE_CANDIDATES)
        self.assertEqual(AUTO_ROUTE_CANDIDATES, frozenset({"coach", "analyst", "codex5.2"}))

    def test_decision_scores_never_contain_aphrodite(self):
        decision = _route("I feel sad and overwhelmed")
        self.assertNotIn("aphrodite", decision.scores)
        self.assertNotEqual(decision.persona, "aphrodite")

    def test_emotion_topic_prior_does_not_introduce_aphrodite(self):
        decision = _route("hi", state={"topic": "emotion"})
        self.assertNotIn("aphrodite", decision.scores)
        self.assertNotEqual(decision.persona, "aphrodite")


class ComfortKeywordsDoNotRouteToAphroditeTests(unittest.TestCase):
    """Sad / lonely / anxious / stressed / overwhelmed / comfort must not yield aphrodite."""

    COMFORT_INPUTS = [
        "I'm feeling sad today",
        "I'm so lonely",
        "I'm anxious and worried",
        "I'm under so much stress",
        "I feel overwhelmed by everything",
        "I need some comfort right now",
        "anxious sad stress overwhelmed lonely comfort",
    ]

    def test_no_comfort_input_routes_to_aphrodite(self):
        for text in self.COMFORT_INPUTS:
            with self.subTest(text=text):
                decision = _route(text)
                self.assertNotEqual(
                    decision.persona,
                    "aphrodite",
                    f"comfort input routed to aphrodite: {text!r}",
                )
                self.assertNotIn("aphrodite", decision.scores)

    def test_comfort_input_does_not_create_aphrodite_score(self):
        decision = _route("anxious sad stress overwhelmed lonely comfort")
        for key in decision.scores:
            self.assertIn(key, AUTO_ROUTE_CANDIDATES)


class NonAphroditeRoutingStillWorksTests(unittest.TestCase):
    """Coach / analyst / codex routing remains intact."""

    def test_planning_keywords_route_to_coach(self):
        decision = _route("help me plan my week with a todo list and deadline")
        self.assertEqual(decision.persona, "coach")

    def test_analysis_keywords_route_to_analyst(self):
        decision = _route("compare the tradeoff and evidence and risk assumption analyze it")
        self.assertEqual(decision.persona, "analyst")

    def test_engineering_keywords_route_to_codex(self):
        decision = _route("please patch the bug and add a test, refactor and debug it")
        self.assertEqual(decision.persona, "codex5.2")

    def test_planning_topic_prior_boosts_coach(self):
        decision = _route("hi", state={"topic": "planning"})
        self.assertEqual(decision.persona, "coach")

    def test_tech_topic_prior_boosts_codex_or_analyst(self):
        decision = _route("hi", state={"topic": "tech"})
        self.assertIn(decision.persona, {"codex5.2", "analyst"})

    def test_neutral_input_yields_tied_low_confidence(self):
        decision = _route("hello there")
        self.assertLess(decision.confidence, 0.60)


class AphroditeStaysWhenComfortInputArrivesTests(unittest.TestCase):
    """A user already on aphrodite is not switched out when they type emotional text.

    The router can return another persona, but low confidence + the runtime guard
    keeps them on aphrodite. Here we verify the router alone does not produce a
    high-confidence non-aphrodite winner for pure comfort signals.
    """

    def test_pure_comfort_signal_yields_low_confidence(self):
        decision = _route("I'm sad and overwhelmed and lonely")
        self.assertLess(decision.confidence, 0.60)


class EmbeddingPathExcludesAphroditeTests(unittest.TestCase):
    """If embeddings are available, they must not surface aphrodite into the decision."""

    def test_embedding_fed_aphrodite_score_is_dropped(self):
        with patch(
            "agentlib.persona_router._embedding_scores",
            return_value={
                "aphrodite": 0.95,
                "coach": 0.10,
                "analyst": 0.10,
                "codex5.2": 0.10,
            },
        ):
            decision = _route("I'm feeling sad")
        self.assertNotEqual(decision.persona, "aphrodite")
        self.assertNotIn("aphrodite", decision.scores)


if __name__ == "__main__":
    unittest.main()
