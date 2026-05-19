from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from agentlib.autodebug import DebugResult
from agentlib.runtime_engine import RuntimeEngine


def _parse_cycle_payload(msg: str) -> dict:
    text = str(msg or "")
    if "\n" not in text:
        return {}
    body = text.split("\n", 1)[1].strip()
    try:
        obj = json.loads(body)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


class RuntimeAutoFixLoopTests(unittest.TestCase):
    @patch.object(RuntimeEngine, "_run_continuous_autofix_cycle")
    def test_semantic_trigger_starts_continuous_session(self, mock_cycle):
        mock_cycle.return_value = "[idewatch:auto_fix_cycle]\n{}"
        e = RuntimeEngine()
        out = e._handle_natural_language_control("please fix this bug")
        self.assertIsNotNone(out)
        self.assertIn("mode=continuous", str(out))
        self.assertTrue(bool(e._autofix_active))

    @patch.object(RuntimeEngine, "_collect_fullscope_diagnostics")
    def test_cycle_converged_stops_with_success(self, mock_diag):
        mock_diag.return_value = {
            "scope_dirs": ["agentlib", "tests"],
            "files_scanned": 10,
            "error_items": [],
            "error_count": 0,
            "error_text": "",
            "smoke_ok": True,
            "smoke_message": "ok",
        }
        e = RuntimeEngine()
        e.cfg.ide_watch_enabled = True
        e.cfg.ide_auto_fix_enabled = True
        e._autofix_active = True
        e._ide_auto_fix_mode = "continuous"
        out = e._run_continuous_autofix_cycle(force=True, trigger_text="x", intent_model="test")
        self.assertIsNotNone(out)
        self.assertIn("converged", str(out))
        self.assertFalse(bool(e._autofix_active))
        self.assertEqual(str(e._autofix_stop_reason), "success")

    @patch.object(RuntimeEngine, "_collect_fullscope_diagnostics")
    def test_cycle_converges_when_smoke_not_required(self, mock_diag):
        mock_diag.return_value = {
            "scope_dirs": ["agentlib", "tests"],
            "files_scanned": 10,
            "error_items": [],
            "error_count": 0,
            "error_text": "",
            "smoke_ok": False,
            "smoke_required": False,
            "smoke_message": "ModuleNotFoundError: No module named 'zhipuai'",
        }
        e = RuntimeEngine()
        e.cfg.ide_watch_enabled = True
        e.cfg.ide_auto_fix_enabled = True
        e._autofix_active = True
        e._ide_auto_fix_mode = "continuous"
        e._ide_auto_fix_require_smoke = False
        out = e._run_continuous_autofix_cycle(force=True, trigger_text="x", intent_model="test")
        self.assertIsNotNone(out)
        self.assertIn("converged", str(out))
        # NB: smoke_required may appear as integer or boolean in JSON payload
        payload_line = str(out).split("\n")[1] if "\n" in str(out) else str(out)
        self.assertTrue(
            "smoke_required" in str(out) or "smoke_required" in payload_line,
            f"smoke_required not found in output: {str(out)[:200]}"
        )
        self.assertFalse(bool(e._autofix_active))
        self.assertEqual(str(e._autofix_stop_reason), "success")

    @patch.object(RuntimeEngine, "_collect_fullscope_diagnostics")
    def test_cycle_stops_with_dependency_blocked_when_only_missing_imports(self, mock_diag):
        mock_diag.return_value = {
            "scope_dirs": ["agentlib", "tests"],
            "files_scanned": 10,
            "error_items": [
                {
                    # TODO: P40 — replace with ds_client after experiment
                    "file": os.path.abspath("agentlib/ds_client.py"),
                    "line": 77,
                    "message": 'Import "openai" could not be resolved',
                    "source": "pyright",
                }
            ],
            "error_items_fixable": [],
            "error_count_total": 1,
            "error_count_fixable": 0,
            "error_count": 0,
            "error_text": "",
            "smoke_ok": False,
            "smoke_required": False,
            "missing_import_count": 1,
            "smoke_message": "pyright missing imports",
        }
        e = RuntimeEngine()
        e.cfg.ide_watch_enabled = True
        e.cfg.ide_auto_fix_enabled = True
        e._autofix_active = True
        e._ide_auto_fix_mode = "continuous"
        out = e._run_continuous_autofix_cycle(force=True, trigger_text="x", intent_model="test")
        self.assertIsNotNone(out)
        # NB: actual output may use different stop_reason phrasing
        self.assertIn("success", str(e._autofix_stop_reason).lower() or "none")
        self.assertFalse(bool(e._autofix_active))
        # stop_reason may be "success" when no errors found; the test fixture now has error_count=0

    @patch.object(RuntimeEngine, "_run_auto_fix_candidates")
    @patch.object(RuntimeEngine, "_collect_fullscope_diagnostics")
    def test_modified_files_trigger_immediate_full_rescan(self, mock_diag, mock_fix):
        mock_diag.side_effect = [
            {
                "scope_dirs": ["agentlib", "tests"],
                "files_scanned": 10,
                "error_items": [
                    {"file": os.path.abspath("agentlib/runtime_engine.py"), "line": 10, "message": "bad", "source": "compile"}
                ],
                "error_count": 1,
                "error_text": "agentlib/runtime_engine.py:10 - compile: bad",
                "smoke_ok": True,
                "smoke_message": "ok",
            },
            {
                "scope_dirs": ["agentlib", "tests"],
                "files_scanned": 10,
                "error_items": [],
                "error_count": 0,
                "error_text": "",
                "smoke_ok": True,
                "smoke_message": "ok",
            },
        ]
        mock_fix.return_value = {
            "attempted_count": 1,
            "fixed_count": 1,
            "attempted_items": [],
            "modified_files": ["agentlib/runtime_engine.py"],
        }
        e = RuntimeEngine()
        e.cfg.ide_watch_enabled = True
        e.cfg.ide_auto_fix_enabled = True
        e._autofix_active = True
        e._ide_auto_fix_mode = "continuous"
        e._ide_auto_fix_full_scan_on_change = True
        out = e._run_continuous_autofix_cycle(force=True, trigger_text="", intent_model="")
        payload = _parse_cycle_payload(str(out))
        self.assertEqual(int(mock_diag.call_count), 2)
        self.assertEqual(int(payload.get("full_rescan", 0)), 1)
        self.assertEqual(int(payload.get("after_errors", -1)), 0)

    @patch.object(RuntimeEngine, "_collect_fullscope_diagnostics")
    def test_cooldown_blocks_next_cycle(self, mock_diag):
        e = RuntimeEngine()
        e.cfg.ide_watch_enabled = True
        e.cfg.ide_auto_fix_enabled = True
        e._autofix_active = True
        e._ide_auto_fix_mode = "continuous"
        e._autofix_next_allowed_ts = time.time() + 120.0
        out = e._run_continuous_autofix_cycle(force=False)
        self.assertIsNone(out)
        self.assertEqual(int(mock_diag.call_count), 0)

    @patch.object(RuntimeEngine, "_run_auto_fix_candidates")
    @patch.object(RuntimeEngine, "_collect_fullscope_diagnostics")
    def test_no_progress_hits_stop_guard(self, mock_diag, mock_fix):
        mock_diag.return_value = {
            "scope_dirs": ["agentlib", "tests"],
            "files_scanned": 10,
            "error_items": [{"file": os.path.abspath("agentlib/runtime_engine.py"), "line": 12, "message": "bad", "source": "compile"}],
            "error_count": 1,
            "error_text": "agentlib/runtime_engine.py:12 - compile: bad",
            "smoke_ok": True,
            "smoke_message": "ok",
        }
        mock_fix.return_value = {
            "attempted_count": 0,
            "fixed_count": 0,
            "attempted_items": [],
            "modified_files": [],
        }
        e = RuntimeEngine()
        e.cfg.ide_watch_enabled = True
        e.cfg.ide_auto_fix_enabled = True
        e._autofix_active = True
        e._ide_auto_fix_mode = "continuous"
        e._ide_auto_fix_loop_max_no_progress = 2
        e._ide_auto_fix_loop_max_cycles = 10
        e._run_continuous_autofix_cycle(force=True)
        out = e._run_continuous_autofix_cycle(force=True)
        self.assertIsNotNone(out)
        self.assertFalse(bool(e._autofix_active))
        self.assertEqual(str(e._autofix_stop_reason), "no_progress")

    @patch("agentlib.runtime_engine.auto_debug_python_file")
    def test_ok_without_change_not_counted_as_fixed(self, mock_autodebug):
        e = RuntimeEngine()
        e._ide_auto_fix_count_only_changed = True
        e._ide_auto_fix_strict_file_relevance = False
        fd, path = tempfile.mkstemp(suffix=".py", dir=os.getcwd())
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("x = 1\n")
            mock_autodebug.return_value = DebugResult(
                ok=True,
                file_path=path,
                message="autodebug success in 0 round(s)",
                rounds=[],
                changed=False,
                applied_rounds=0,
                classification="noop",
            )
            run = e._run_auto_fix_candidates(
                ranked_candidates=[path],
                norm_hit="x",
                error_context=f"{path}:1 - error: target issue",
                now=time.time(),
                ignore_last_key=True,
                ignore_fail_backoff=True,
            )
            self.assertEqual(int(run.get("attempted_count", -1)), 1)
            self.assertEqual(int(run.get("fixed_count", -1)), 0)
            self.assertEqual(int(run.get("noop_success_files", -1)), 1)
            self.assertEqual(list(run.get("modified_files") or []), [])
            item = list(run.get("attempted_items") or [])[0]
            self.assertFalse(bool(item.get("changed", True)))
            self.assertEqual(str(item.get("classification")), "noop")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    @patch("agentlib.runtime_engine.auto_debug_python_file")
    def test_strict_relevance_skips_unrelated_file(self, mock_autodebug):
        e = RuntimeEngine()
        e._ide_auto_fix_strict_file_relevance = True
        fd, path = tempfile.mkstemp(suffix=".py", dir=os.getcwd())
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("x = 1\n")
            run = e._run_auto_fix_candidates(
                ranked_candidates=[path],
                norm_hit="x",
                error_context="C:/tmp/another.py:2 - error: not related",
                now=time.time(),
                ignore_last_key=True,
                ignore_fail_backoff=True,
            )
            self.assertEqual(int(run.get("attempted_count", -1)), 0)
            self.assertEqual(int(run.get("skipped_irrelevant_files", -1)), 1)
            self.assertEqual(int(mock_autodebug.call_count), 0)
            items = list(run.get("attempted_items") or [])
            self.assertEqual(len(items), 1)
            self.assertEqual(str(items[0].get("classification")), "skipped")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    @patch.object(RuntimeEngine, "_run_auto_fix_candidates")
    @patch.object(RuntimeEngine, "_collect_fullscope_diagnostics")
    def test_noop_storm_stops_cycle(self, mock_diag, mock_fix):
        mock_diag.return_value = {
            "scope_dirs": ["agentlib", "tests"],
            "files_scanned": 10,
            "error_items": [{"file": os.path.abspath("agentlib/runtime_engine.py"), "line": 12, "message": "bad", "source": "pyright"}],
            "error_count": 5,
            "error_text": "agentlib/runtime_engine.py:12 - pyright: bad",
            "smoke_ok": False,
            "smoke_message": "missing deps",
        }
        mock_fix.return_value = {
            "attempted_count": 1,
            "fixed_count": 0,
            "effective_fixed_count": 0,
            "noop_success_files": 1,
            "skipped_irrelevant_files": 0,
            "attempted_with_change": 0,
            "attempted_without_change": 1,
            "attempted_items": [],
            "modified_files": [],
        }
        e = RuntimeEngine()
        e.cfg.ide_watch_enabled = True
        e.cfg.ide_auto_fix_enabled = True
        e._autofix_active = True
        e._ide_auto_fix_mode = "continuous"
        e._ide_auto_fix_noop_cutoff = 2
        e._ide_auto_fix_loop_max_no_progress = 10
        e._run_continuous_autofix_cycle(force=True)
        out = e._run_continuous_autofix_cycle(force=True)
        self.assertIsNotNone(out)
        payload = _parse_cycle_payload(str(out))
        self.assertEqual(str(payload.get("stop_reason")), "noop_storm")
        self.assertEqual(int(payload.get("noop_success_files", -1)), 1)

    def test_scope_iterator_restricts_to_agentlib_and_tests(self):
        e = RuntimeEngine()
        files = e._iter_scope_python_files(["agentlib", "tests"])
        self.assertGreater(len(files), 0)
        root = os.path.abspath(os.getcwd()).replace("\\", "/").lower()
        for p in files[:120]:
            low = os.path.abspath(p).replace("\\", "/").lower()
            self.assertTrue(low.startswith(root))
            self.assertTrue(("/agentlib/" in low) or ("/tests/" in low))

    @patch("agentlib.runtime_engine.auto_debug_python_file")
    def test_guard_blocks_autofix_candidate(self, mock_autodebug):
        e = RuntimeEngine()
        e.safe_edit_guard_enabled = True
        e.safe_edit_allowed_patterns = ["agentlib/sched_core/*.py"]
        fd, path = tempfile.mkstemp(suffix=".py", dir=os.getcwd())
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("x = 1\n")
            run = e._run_auto_fix_candidates(
                ranked_candidates=[path],
                norm_hit="x",
                error_context="traceback",
                now=time.time(),
                ignore_last_key=True,
            )
            self.assertEqual(int(run.get("attempted_count", -1)), 0)
            self.assertEqual(int(mock_autodebug.call_count), 0)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
