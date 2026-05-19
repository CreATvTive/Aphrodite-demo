from __future__ import annotations

import unittest

from agent_kernel.failure_router import classify_failure


class FailureRouterTests(unittest.TestCase):
    def test_missing_input_routes_to_ask_user(self):
        d = classify_failure(
            subgoal_id="s1",
            tool_name="code_task",
            error_message="missing required input: user_id",
            prior_fingerprints=[],
        )
        self.assertEqual(d.category.value, "missing_input")
        self.assertEqual(d.action.value, "ask_user")

    def test_transient_routes_to_retry(self):
        d = classify_failure(
            subgoal_id="s1",
            tool_name="code_task",
            error_message="TimeoutError: backend timeout",
            prior_fingerprints=[],
        )
        self.assertEqual(d.category.value, "transient_tool_error")
        self.assertEqual(d.action.value, "retry")

    def test_auth_routes_to_repair_auth(self):
        d = classify_failure(
            subgoal_id="s1",
            tool_name="code_task",
            error_message="401 unauthorized token missing",
            prior_fingerprints=[],
        )
        self.assertEqual(d.category.value, "auth_error")
        self.assertEqual(d.action.value, "repair_auth")

    def test_repeated_same_error_routes_to_circuit_break(self):
        first = classify_failure(
            subgoal_id="s1",
            tool_name="code_task",
            error_message="logic conflict",
            prior_fingerprints=[],
        )
        second = classify_failure(
            subgoal_id="s1",
            tool_name="code_task",
            error_message="logic conflict",
            prior_fingerprints=[first.fingerprint, first.fingerprint],
        )
        self.assertEqual(second.category.value, "repeated_same_error")
        self.assertEqual(second.action.value, "circuit_break")

    def test_permission_denied_is_classified(self):
        d = classify_failure(
            subgoal_id="s1",
            tool_name="code_task",
            error_message="permission_denied:target_path_outside_workspace",
            prior_fingerprints=[],
        )
        self.assertEqual(d.category.value, "permission_denied")
        self.assertEqual(d.action.value, "ask_user")

    def test_environment_missing_is_classified(self):
        d = classify_failure(
            subgoal_id="s1",
            tool_name="code_task",
            error_message="environment_missing:module not found",
            prior_fingerprints=[],
        )
        self.assertEqual(d.category.value, "environment_missing")
        self.assertEqual(d.action.value, "ask_user")

    def test_goal_not_executable_is_classified(self):
        d = classify_failure(
            subgoal_id="s1",
            tool_name="plan_goal",
            error_message="goal_not_executable:missing acceptance criteria",
            prior_fingerprints=[],
        )
        self.assertEqual(d.category.value, "goal_not_executable")
        self.assertEqual(d.action.value, "ask_user")


if __name__ == "__main__":
    unittest.main()

