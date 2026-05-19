from __future__ import annotations

import queue
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from agentlib.autodebug import DebugResult
from agentlib.runtime_engine import RuntimeEngine


class RuntimeDebugNaturalLanguageTests(unittest.TestCase):
    def test_turn_on_debug_short_phrase(self):
        e = RuntimeEngine()
        out = e._handle_natural_language_control("turn on debug")
        self.assertIsNotNone(out)
        self.assertIn("[idewatch] auto_fix=1; mode=continuous;", str(out))

    def test_debug_mode_on_phrase(self):
        e = RuntimeEngine()
        out = e._handle_natural_language_control("debug mode on")
        self.assertIsNotNone(out)
        self.assertIn("[idewatch] auto_fix=1; mode=continuous;", str(out))

    def test_chinese_enable_debug_phrase(self):
        e = RuntimeEngine()
        out = e._handle_natural_language_control("\u5f00\u542fdebug")
        self.assertIsNotNone(out)
        self.assertIn("[idewatch] auto_fix=1; mode=continuous;", str(out))

    def test_autofix_toggle_by_natural_language(self):
        e = RuntimeEngine()
        on_out = e._handle_natural_language_control("autofix on")
        off_out = e._handle_natural_language_control("autofix off")
        self.assertIsNotNone(on_out)
        self.assertIn("[idewatch] auto_fix=1; mode=continuous;", str(on_out))
        self.assertEqual(off_out, "[idewatch] auto_fix=0")

    def test_debug_status_query(self):
        e = RuntimeEngine()
        e._handle_natural_language_control("turn on debug")
        out = e._handle_natural_language_control("debug status")
        self.assertIsNotNone(out)
        self.assertIn("[idewatch] enabled=1;", str(out))

    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_plain_debug_request_does_not_trigger_debug_mode(self, mock_ds_cls, mock_glm_cls):
        mock_glm_cls.return_value.chat.side_effect = RuntimeError("llm unavailable")
        e = RuntimeEngine()
        out = e._handle_natural_language_control("we are discussing debug theory today")
        self.assertIsNone(out)

    def test_debug_activity_is_echoed_after_nl_enable(self):
        e = RuntimeEngine()
        e.debug_frontend_chat_enabled = True
        e._handle_natural_language_control("turn on debug")
        while True:
            try:
                _ = e.reply_q.get_nowait()
            except queue.Empty:
                break
        e._log_activity(tag="debug", text="[idewatch] probe event", echo=False)
        out = e.reply_q.get_nowait()
        self.assertIn("[idewatch] probe event", str(out))

    def test_debug_activity_not_echoed_after_nl_disable(self):
        e = RuntimeEngine()
        e.debug_frontend_chat_enabled = True
        e._handle_natural_language_control("turn on debug")
        e._handle_natural_language_control("turn off debug")
        while True:
            try:
                _ = e.reply_q.get_nowait()
            except queue.Empty:
                break
        e._log_activity(tag="debug", text="[idewatch] should stay hidden", echo=False)
        with self.assertRaises(queue.Empty):
            e.reply_q.get_nowait()

    def test_activity_ack_is_natural_language(self):
        e = RuntimeEngine()
        e.persona_name = "aphrodite"
        e.reply_language = "zh"
        out = e._activity_ack_text("debug")
        self.assertIn("Activity", str(out))
        self.assertFalse(str(out).startswith("["))
        self.assertNotEqual(str(out), "[debug] 已写入调试模块（Activity）。")

    @patch("agentlib.runtime_engine.companion_rag.retrieve_memory_context")
    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_route_control_reply_prefers_llm_ack(self, mock_ds_cls, mock_glm_cls, mock_retrieve_memory):
        mock_retrieve_memory.return_value = ["用户偏好：先给结论，再给进展"]
        mock_glm_cls.return_value.chat.return_value = "我按你的习惯先给结论，后台继续修复，有结果马上告诉你。"
        e = RuntimeEngine()
        e.activity_ack_llm_enabled = True
        ok = e._route_control_reply_to_activity(
            msg_id=None,
            reply_text="[idewatch] auto_fix=1; mode=continuous",
            user_text="帮我debug",
        )
        self.assertTrue(bool(ok))
        out = e.reply_q.get_nowait()
        self.assertIn("后台继续修复", str(out))
        self.assertNotIn("[idewatch]", str(out))

    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_route_control_reply_falls_back_when_llm_unavailable(self, mock_ds_cls, mock_glm_cls):
        mock_glm_cls.return_value.chat.side_effect = RuntimeError("llm unavailable")
        e = RuntimeEngine()
        e.activity_ack_llm_enabled = True
        ok = e._route_control_reply_to_activity(
            msg_id=None,
            reply_text="[idewatch] auto_fix=1; mode=continuous",
            user_text="帮我debug",
        )
        self.assertTrue(bool(ok))
        out = e.reply_q.get_nowait()
        self.assertIn("Activity", str(out))

    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_semantic_enable_for_free_form_intent(self, mock_ds_cls, mock_glm_cls):
        mock_glm_cls.return_value.chat.return_value = '{"action":"enable","confidence":0.94}'
        e = RuntimeEngine()
        e.debug_local_model_enabled = False
        out = e._handle_natural_language_control("please watch IDE errors for me")
        self.assertIsNotNone(out)
        self.assertIn("[idewatch] auto_fix=1; mode=continuous;", str(out))

    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_semantic_status_for_free_form_intent(self, mock_ds_cls, mock_glm_cls):
        mock_glm_cls.return_value.chat.return_value = '{"action":"status","confidence":0.96}'
        e = RuntimeEngine()
        out = e._handle_natural_language_control("what is debug monitor status now")
        self.assertIsNotNone(out)
        self.assertIn("[idewatch] enabled=", str(out))
        self.assertIn("debug_echo=", str(out))

    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_overlay_semantic_can_start_selfdrive_for_free_form_cn_request(self, mock_ds_cls, mock_glm_cls):
        mock_glm_cls.return_value.chat.return_value = (
            '{"channel":"selfdrive","action":"start","confidence":0.98,'
            '"duration_minutes":480,"direction":"搜索AI合成音色并学习调教与翻唱"}'
        )
        e = RuntimeEngine()
        out = e._handle_natural_language_control(
            "我现在需要你自推进8个小时，你需要搜索有关于ai合成音色的内容，学习如何调教音色"
        )
        self.assertIsNotNone(out)
        self.assertIn("[selfdrive] started", str(out))
        self.assertTrue(bool(e.mon.get("selfdrive_enabled", 0)))

    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_selfdrive_local_semantic_can_start_on_delegated_crawler_request(self, mock_ds_cls, mock_glm_cls):
        mock_glm_cls.return_value.chat.side_effect = RuntimeError("llm unavailable")
        e = RuntimeEngine()
        out = e._handle_natural_language_control("我需要你给自己做一个网络爬虫，用于你接下来的数据搜索")
        self.assertIsNotNone(out)
        self.assertIn("[selfdrive] started", str(out))
        self.assertTrue(bool(e.mon.get("selfdrive_enabled", 0)))
        self.assertIn("网络爬虫", str(e.mon.get("selfdrive_goal", "")))

    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_selfdrive_local_semantic_does_not_trigger_on_plain_crawler_topic(self, mock_ds_cls, mock_glm_cls):
        mock_glm_cls.return_value.chat.side_effect = RuntimeError("llm unavailable")
        e = RuntimeEngine()
        out = e._handle_natural_language_control("我想了解一下网络爬虫的基本原理")
        self.assertIsNone(out)
        self.assertFalse(bool(e.mon.get("selfdrive_enabled", 0)))

    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_selfdrive_local_semantic_can_start_on_generic_delegated_task(self, mock_ds_cls, mock_glm_cls):
        mock_glm_cls.return_value.chat.side_effect = RuntimeError("llm unavailable")
        e = RuntimeEngine()
        out = e._handle_natural_language_control("请你接下来给自己做一个数据采集工具，并持续优化稳定性")
        self.assertIsNotNone(out)
        self.assertIn("[selfdrive] started", str(out))
        self.assertTrue(bool(e.mon.get("selfdrive_enabled", 0)))

    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_selfdrive_local_semantic_does_not_trigger_on_what_is_question(self, mock_ds_cls, mock_glm_cls):
        mock_glm_cls.return_value.chat.side_effect = RuntimeError("llm unavailable")
        e = RuntimeEngine()
        out = e._handle_natural_language_control("什么是自驱系统，它的原理是什么")
        self.assertIsNone(out)
        self.assertFalse(bool(e.mon.get("selfdrive_enabled", 0)))

    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_overlay_shadow_mode_records_without_execution(self, mock_ds_cls, mock_glm_cls):
        mock_glm_cls.return_value.chat.return_value = (
            '{"channel":"selfdrive","action":"start","confidence":0.96,'
            '"duration_minutes":60,"direction":"improve tests"}'
        )
        e = RuntimeEngine()
        e.debug_local_model_enabled = False
        e.debug_semantic_enabled = False
        e.selfdrive_semantic_enabled = False
        e.nl_control_overlay_shadow_mode = True
        out = e._handle_natural_language_control("please run selfdrive for 60 minutes to improve tests")
        self.assertIsNone(out)
        self.assertEqual(int(e.mon.get("nl_overlay_shadow_hits", 0)), 1)
        self.assertFalse(bool(e.mon.get("selfdrive_enabled", 0)))

    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_overlay_ambiguous_prediction_is_rejected(self, mock_ds_cls, mock_glm_cls):
        mock_glm_cls.return_value.chat.return_value = (
            '{"channel":"selfdrive","action":"start","confidence":0.88,'
            '"alt_action":"plan","alt_confidence":0.83,'
            '"duration_minutes":45,"direction":"improve stability"}'
        )
        e = RuntimeEngine()
        e.debug_local_model_enabled = False
        e.debug_semantic_enabled = False
        e.selfdrive_semantic_enabled = False
        e.nl_control_overlay_min_margin = 0.10
        out = e._handle_natural_language_control("please run selfdrive for 45 minutes to improve stability")
        self.assertIsNone(out)
        self.assertEqual(int(e.mon.get("nl_overlay_ambiguous", 0)), 1)
        self.assertFalse(bool(e.mon.get("selfdrive_enabled", 0)))

    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_overlay_abstain_does_not_execute(self, mock_ds_cls, mock_glm_cls):
        mock_glm_cls.return_value.chat.return_value = (
            '{"channel":"none","action":"none","confidence":0.40,'
            '"abstain":true,"reason":"uncertain"}'
        )
        e = RuntimeEngine()
        e.debug_local_model_enabled = False
        e.debug_semantic_enabled = False
        e.selfdrive_semantic_enabled = False
        out = e._handle_natural_language_control("please selfdrive maybe do something")
        self.assertIsNone(out)
        self.assertEqual(int(e.mon.get("nl_overlay_abstain", 0)), 1)
        self.assertFalse(bool(e.mon.get("selfdrive_enabled", 0)))

    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_semantic_local_fallback_when_llm_unavailable(self, mock_ds_cls, mock_glm_cls):
        mock_glm_cls.return_value.chat.side_effect = RuntimeError("llm unavailable")
        e = RuntimeEngine()
        out = e._handle_natural_language_control("please fix this bug")
        self.assertIsNotNone(out)
        self.assertIn("[idewatch] auto_fix=1; mode=continuous;", str(out))

    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_free_form_bug_fix_sentence_triggers_debug(self, mock_ds_cls, mock_glm_cls):
        mock_glm_cls.return_value.chat.side_effect = RuntimeError("llm unavailable")
        e = RuntimeEngine()
        out = e._handle_natural_language_control("please fix this bug")
        self.assertIsNotNone(out)
        self.assertIn("[idewatch] auto_fix=1; mode=continuous;", str(out))
        self.assertTrue(bool(e.cfg.ide_watch_enabled))
        self.assertTrue(bool(e.cfg.ide_auto_fix_enabled))

    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_bug_only_sentence_still_triggers_autofix_fallback(self, mock_ds_cls, mock_glm_cls):
        mock_glm_cls.return_value.chat.side_effect = RuntimeError("llm unavailable")
        e = RuntimeEngine()
        out = e._handle_natural_language_control("this has a bug")
        self.assertIsNotNone(out)
        self.assertIn("[idewatch] auto_fix=1; mode=continuous;", str(out))

    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_chinese_direct_debug_request_hits_debug_flow(self, mock_ds_cls, mock_glm_cls):
        mock_glm_cls.return_value.chat.side_effect = RuntimeError("llm unavailable")
        e = RuntimeEngine()
        out = e._handle_natural_language_control("帮我debug一下")
        self.assertIsNotNone(out)
        self.assertTrue(str(out).startswith("[idewatch]"))

    @patch("agentlib.runtime_engine.selfcheck_python_target")
    def test_chinese_selfcheck_phrase_triggers_selfcheck(self, mock_selfcheck):
        mock_selfcheck.return_value = (True, "ok")
        e = RuntimeEngine()
        out = e._handle_natural_language_control("你自检一下")
        self.assertIsNotNone(out)
        self.assertIn("[selfcheck:OK]", str(out))

    @patch("agentlib.runtime_engine.selfcheck_python_target")
    def test_selfcheck_phrase_bypasses_pending_clone(self, mock_selfcheck):
        mock_selfcheck.return_value = (True, "ok")
        e = RuntimeEngine()
        e._prompt_clone_pending_target = "sherlock"
        out = e._handle_natural_language_control("你自检一下")
        self.assertIsNotNone(out)
        self.assertIn("[selfcheck:OK]", str(out))
        self.assertEqual(e._prompt_clone_pending_target, "sherlock")

    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_state_machine_followup_short_command(self, mock_ds_cls, mock_glm_cls):
        mock_glm_cls.return_value.chat.side_effect = RuntimeError("llm unavailable")
        e = RuntimeEngine()
        e._handle_natural_language_control("watch IDE errors")
        out = e._handle_natural_language_control("turn it off")
        self.assertEqual(out, "[idewatch] enabled=0; auto_fix=0")

    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_state_machine_short_command_without_context_noop(self, mock_ds_cls, mock_glm_cls):
        mock_glm_cls.return_value.chat.side_effect = RuntimeError("llm unavailable")
        e = RuntimeEngine()
        out = e._handle_natural_language_control("turn it off")
        self.assertIsNone(out)

    def test_guard_show_and_toggle(self):
        e = RuntimeEngine()
        out1 = e._handle_debug_command("/idewatch guard show")
        self.assertIn("[idewatch:guard] enabled=", str(out1))
        out2 = e._handle_debug_command("/idewatch guard on")
        self.assertIn("enabled=1", str(out2))
        out3 = e._handle_debug_command("/idewatch guard set agentlib/sched_core/*.py")
        self.assertIn("agentlib/sched_core/*.py", str(out3))

    def test_autodebug_blocked_when_guard_disallows_path(self):
        e = RuntimeEngine()
        e._handle_debug_command("/idewatch guard on")
        e._handle_debug_command("/idewatch guard set agentlib/sched_core/*.py")
        out = e._handle_debug_command("/autodebug agentlib/runtime_engine.py")
        self.assertIn("blocked by safe-edit guard", str(out))

    def test_selfdrive_autopilot_blocked_by_guard(self):
        e = RuntimeEngine()
        e._handle_debug_command("/idewatch guard on")
        out = e._execute_selfdrive_action(action="autopilot_once", goal="x", task="")
        self.assertIn("blocked", str(out).lower())

    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_autopilot_task_falls_back_to_glm_patch_when_codex_unavailable(self, mock_ds_cls, mock_glm_cls):
        with tempfile.TemporaryDirectory() as td:
            old_cwd = os.getcwd()
            try:
                os.chdir(td)
                target = os.path.join(td, "demo.py")
                with open(target, "w", encoding="utf-8") as f:
                    f.write("value = 1\n")
                mock_glm_cls.return_value.chat.return_value = (
                    '{"patched_code":"value = 2\\n","reason":"ok"}'
                )
                e = RuntimeEngine()
                e.codex_delegate.try_chat_json = lambda **kwargs: None
                out = e._execute_selfdrive_action(
                    action="autopilot_task",
                    goal="update demo value",
                    task="set value to 2",
                )
                self.assertIn("autopilot fallback=glm_patch;", str(out))
                with open(target, "r", encoding="utf-8") as f:
                    self.assertIn("value = 2", f.read())
                self.assertEqual(str(e._selfdrive_tests[-1].get("cmd")), "glm_patch")
            finally:
                os.chdir(old_cwd)

    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_autopilot_task_reports_failure_when_codex_and_glm_unavailable(self, mock_ds_cls, mock_glm_cls):
        with tempfile.TemporaryDirectory() as td:
            old_cwd = os.getcwd()
            try:
                os.chdir(td)
                target = os.path.join(td, "demo.py")
                with open(target, "w", encoding="utf-8") as f:
                    f.write("value = 1\n")
                mock_glm_cls.return_value.chat.side_effect = RuntimeError("glm down")
                e = RuntimeEngine()
                e.codex_delegate.try_chat_json = lambda **kwargs: None
                out = e._execute_selfdrive_action(
                    action="autopilot_task",
                    goal="update demo value",
                    task="set value to 2",
                )
                self.assertIn(
                    "autopilot fallback failed: codex patch unavailable; glm fallback unavailable:",
                    str(out),
                )
                with open(target, "r", encoding="utf-8") as f:
                    self.assertIn("value = 1", f.read())
                self.assertEqual(str(e._selfdrive_tests[-1].get("cmd")), "codex_patch")
            finally:
                os.chdir(old_cwd)

    def test_generic_progress_query_reports_active_selfdrive(self):
        e = RuntimeEngine()
        now = time.time()
        with e._selfdrive_lock:
            e._selfdrive_active = True
            e._selfdrive_mode = "rule"
            e._selfdrive_goal = "improve stability"
            e._selfdrive_step_index = 1
            e._selfdrive_deadline_ts = now + 120
            e._selfdrive_steps = [
                {"name": "baseline check", "action": "selfcheck", "task": ""},
                {"name": "quick tests", "action": "pytest_quick", "task": ""},
                {"name": "summary", "action": "summary", "task": ""},
            ]
        out = e._handle_natural_language_control("现在任务进展如何")
        self.assertIsNotNone(out)
        self.assertIn("[task] active=1; kind=selfdrive;", str(out))
        self.assertIn("progress=", str(out))

    def test_generic_progress_query_reports_idle_when_no_task(self):
        e = RuntimeEngine()
        out = e._handle_natural_language_control("现在进展怎么样")
        self.assertEqual(out, "[task] no active tracked task")

    def test_need_todo_phrase_maps_to_selfdrive_plan_when_idle(self):
        e = RuntimeEngine()
        out = e._handle_natural_language_control("\u4f60\u9700\u8981\u505a\u4ec0\u4e48")
        self.assertIsNotNone(out)
        self.assertIn("[selfdrive:plan]", str(out))
        self.assertIn("steps=", str(out))

    def test_need_todo_phrase_maps_to_selfdrive_status_when_active(self):
        e = RuntimeEngine()
        now = time.time()
        with e._selfdrive_lock:
            e._selfdrive_active = True
            e._selfdrive_mode = "rule"
            e._selfdrive_goal = "improve stability"
            e._selfdrive_step_index = 0
            e._selfdrive_deadline_ts = now + 180
            e._selfdrive_next_ts = now + 20
            e._selfdrive_steps = [
                {"name": "baseline check", "action": "selfcheck", "task": ""},
                {"name": "quick tests", "action": "pytest_quick", "task": ""},
            ]
        out = e._handle_natural_language_control("\u4f60\u9700\u8981\u505a\u4ec0\u4e48")
        self.assertIsNotNone(out)
        self.assertIn("[selfdrive] active=1", str(out))
        self.assertIn("goal=improve stability", str(out))

    def test_selfdrive_start_command_without_duration_is_unbounded(self):
        e = RuntimeEngine()
        out = e._handle_debug_command("/selfdrive start improve stability and add one feature")
        self.assertIsNotNone(out)
        self.assertIn("[selfdrive] started", str(out))
        self.assertIn("budget=infinite", str(out))
        self.assertEqual(int(e.mon.get("selfdrive_unbounded", 0)), 1)
        self.assertEqual(float(e.mon.get("selfdrive_deadline_ts", -1.0)), 0.0)

    @patch("agentlib.runtime_engine.GLMClient", create=True)
    @patch("agentlib.runtime_engine.DSClient", create=True)
    def test_selfdrive_nl_start_without_duration_is_unbounded(self, mock_ds_cls, mock_glm_cls):
        mock_glm_cls.return_value.chat.side_effect = RuntimeError("llm unavailable")
        e = RuntimeEngine()
        out = e._handle_natural_language_control("请你开始自推进，优化稳定性并新增一个小功能")
        self.assertIsNotNone(out)
        self.assertIn("[selfdrive] started", str(out))
        self.assertIn("unbounded=1", str(out))
        self.assertEqual(int(e.mon.get("selfdrive_unbounded", 0)), 1)
        self.assertEqual(float(e.mon.get("selfdrive_deadline_ts", -1.0)), 0.0)

    def test_unbounded_selfdrive_renews_cycle_after_steps_done(self):
        e = RuntimeEngine()
        e._start_selfdrive(direction="improve stability", duration_minutes=None)
        with e._selfdrive_lock:
            e._selfdrive_step_index = len(e._selfdrive_steps)
        e._run_selfdrive_step()
        with e._selfdrive_lock:
            self.assertTrue(bool(e._selfdrive_active))
            self.assertTrue(bool(e._selfdrive_unbounded))
            self.assertEqual(int(e._selfdrive_step_index), 0)
            self.assertGreater(len(e._selfdrive_steps), 0)

    @patch("agentlib.runtime_engine.companion_rag.record_turn_memory")
    def test_selfdrive_step_records_skill_memory(self, mock_record_turn_memory):
        e = RuntimeEngine()
        e._execute_selfdrive_action = lambda action, goal, task="": "autopilot fallback=codex_patch; patched=1"
        now = time.time()
        with e._selfdrive_lock:
            e._selfdrive_active = True
            e._selfdrive_goal = "修复 runtime_engine 静态问题"
            e._selfdrive_deadline_ts = now + 60.0
            e._selfdrive_unbounded = False
            e._selfdrive_steps = [
                {
                    "name": "autopilot patch",
                    "action": "autopilot_task",
                    "task": "修复 runtime_engine 静态 bug 并补测试",
                }
            ]
            e._selfdrive_step_index = 0
        e._run_selfdrive_step()
        self.assertTrue(mock_record_turn_memory.called)
        merged_items = []
        for call in mock_record_turn_memory.call_args_list:
            kwargs = dict(call.kwargs or {})
            merged_items.extend([str(x) for x in (kwargs.get("explicit_items") or [])])
        joined = "\n".join(merged_items)
        self.assertIn("用户学习了", joined)
        self.assertIn("自推进步骤", joined)

    @patch("agentlib.runtime_engine.auto_debug_python_file")
    def test_ide_auto_fix_failure_backoff_skips_repeat_failures(self, mock_autodebug):
        e = RuntimeEngine()
        e.cfg.ide_auto_fix_enabled = True
        e.cfg.ide_auto_fix_cooldown_sec = 0.0
        e._ide_auto_fix_failure_base_sec = 30.0
        e._ide_auto_fix_failure_max_sec = 30.0

        fd, path = tempfile.mkstemp(suffix=".py", dir=os.getcwd())
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("x = 1\n")
            mock_autodebug.return_value = DebugResult(ok=False, file_path=path, message="no fix", rounds=[])

            raw_1 = f'File "{path}", line 10, in <module>\nValueError: bad input\n'
            out_1 = e._try_ide_auto_fix(raw_delta=raw_1, hit_summary="- traceback\n- ValueError: bad input")
            self.assertIsNone(out_1)
            self.assertEqual(mock_autodebug.call_count, 1)

            raw_2 = f'File "{path}", line 11, in <module>\nValueError: still bad\n'
            out_2 = e._try_ide_auto_fix(raw_delta=raw_2, hit_summary="- traceback\n- runtime path changed")
            self.assertIsNone(out_2)
            self.assertEqual(mock_autodebug.call_count, 1)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
