from __future__ import annotations

# Allow running this file directly (python agentlib/runtime_engine.py) while
# still supporting package-relative imports.
if __package__ in {None, ""}:
    import os as _os
    import sys as _sys

    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
    _repo_root = _os.path.dirname(_pkg_dir)
    if _repo_root not in _sys.path:
        _sys.path.insert(0, _repo_root)
    __package__ = "agentlib"

import hashlib
import importlib.util
import json
import logging
import os
import queue
import re
import fnmatch
import sys
import subprocess
import threading
import time
import traceback
import tokenize
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from agent_kernel.adapters import CodexCodeAdapter, GLM5PlannerAdapter
from agent_kernel.compile_check import action_plan_gate_check
from agent_kernel.judge import V15Judge
from agent_kernel.kernel import AgentKernel
from agent_kernel.planner import V15Planner
from agent_kernel.schemas import AgentState as KernelAgentState
from agent_kernel.schemas import ExecutableSubgoal, Predicate, RetryPolicy, SubgoalState, SuccessCriterion
from agent_kernel.schemas import Task as KernelTask
from agent_kernel.worker import SpecialistRouterWorker

from .advanced_decision import AdvancedDecisionConfig, generate_reply, load_advanced_decision_config
from .autodebug import auto_debug_python_file, selfcheck_python_target
# TODO: P40 — replace with ds_client after experiment
# from .glm_client import GLMClient
GLMClient = None  # type: ignore[assignment]  # placeholder for test mocking
from .chat_bridge import ChatBridge, db_set_inbox_status, db_write_outbox, db_write_system_pair
from .companion_chat import companion_prepare_messages
from .companion_prompt import build_system_prompt_sections
from . import companion_rag
from .codex_delegate import CodexDelegateClient, load_codex_delegate_config
from .env_loader import load_local_env_once
# TODO: P40 — replace with ds_client after experiment
# from .glm_client import GLMClient
from .learned_lists import ListState, init_learned_lists, refresh_state
from .memory_arbiter import arbitrate_memory
try:
    from .memory_store import learn_lists_from_feedback as _learn_lists_from_feedback
except BaseException:
    _learn_lists_from_feedback = None
from .metrics import start_metrics_thread
from .persona_profiles import get_persona_profile, list_persona_profiles
from .persona_router import detect_persona_from_text
from .prompt_manager import PromptManager, PromptTuneResult
from .patch_executor import PatchExecutionTransaction
from .runtime_state import RuntimeConfig, apply_idle_nudge, load_state, mark_user_turn, save_state, update_topic
from .runtime_immediate_protocol import ImmediateReplyProtocol
from .screen_capture import capture_screen_to_file
from .semantic_intent_lane import SemanticIntentLane
from .speech_azure import AzureSpeechConfig, azure_tts_synthesize, load_azure_speech_config, save_wav, ssml_prosody_from_state
from .style_policy import SelfLearningStylePolicy, infer_reward_from_user_text, style_guidance_from_action
from .autonomy.actuation import ActionEnvelope, DialogueExecutor, InteractionExecutor, SceneEffectExecutor
from .task_run import TaskRun, TaskRunRecorder, TaskRunStep
from src.interpreter.input_interpreter import InputInterpreter
from .web_search import web_search

from src.core.state_authority import StateAuthority
from src.core.trace import PresenceTrace
from src.memory.memory_gate import decide_persistence
from src.relationship.relationship_engine import apply_dependency_guard
from src.body.action_mixer import mix_action_weights
from src.interpreter.input_interpreter import InputInterpreter
try:
    from src.field_trace.store import FieldTraceExtractor, FieldTraceStore
    _FIELD_TRACE_AVAILABLE = True
except ImportError:
    _FIELD_TRACE_AVAILABLE = False
    FieldTraceExtractor = None  # type: ignore[assignment]
    FieldTraceStore = None  # type: ignore[assignment]


DEFAULT_RAG_KB = [
    "User prefers concise responses and dislikes long lectures.",
    "When user feels anxious, start with grounding and validation.",
    "Offer one small next step instead of many options.",
    "If user asks for practical planning, provide checklist style.",
]

REQUIRED_RUNTIME_TRIGGERS = ("code_debug",)


@dataclass
class PerceptionFusionPacket:
    trace_id: str
    event_id: str
    user_text: str
    msg_id: Optional[str]
    is_idle: bool
    ts: float = field(default_factory=time.time)
    command_hint: str = ""
    raw_event: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DecisionCoreResult:
    trace_id: str
    event_id: str
    mode: str
    action: str
    reason: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)


class RuntimeEngine:
    @classmethod
    def _env_str(cls, key: str, default: str = "", *, fallback_on_empty: bool = False) -> str:
        raw = os.getenv(key, default)
        if raw is None:
            return str(default).strip()
        value = str(raw).strip()
        if fallback_on_empty and not value:
            return str(default).strip()
        return value

    @classmethod
    def _env_bool(cls, key: str, default: str = "0") -> bool:
        return cls._to_bool_flag(os.getenv(key, default))

    @classmethod
    def _env_int(
        cls,
        key: str,
        default: int,
        *,
        min_v: Optional[int] = None,
        max_v: Optional[int] = None,
    ) -> int:
        raw = os.getenv(key, str(default))
        try:
            value = int(raw)  # type: ignore[arg-type]
        except Exception:
            value = int(default)
        if min_v is not None:
            value = max(int(min_v), value)
        if max_v is not None:
            value = min(int(max_v), value)
        return int(value)

    @classmethod
    def _env_float(
        cls,
        key: str,
        default: float,
        *,
        min_v: Optional[float] = None,
        max_v: Optional[float] = None,
    ) -> float:
        raw = os.getenv(key, str(default))
        try:
            value = float(raw)  # type: ignore[arg-type]
        except Exception:
            value = float(default)
        if min_v is not None:
            value = max(float(min_v), value)
        if max_v is not None:
            value = min(float(max_v), value)
        return float(value)

    def __init__(self, config: Optional[RuntimeConfig] = None):
        load_local_env_once()
        self.cfg = config or RuntimeConfig()
        self.state = load_state(self.cfg.state_path)
        self.state_authority = StateAuthority(initial_state={"runtime": dict(self.state)})
        self.event_q: queue.Queue[Any] = queue.Queue()
        self.reply_q: queue.Queue[Any] = queue.Queue()
        self.stop_event = threading.Event()
        self.dialogue_executor = DialogueExecutor(
            text_sink=self._dialogue_text_sink,
            voice_sink=self._dialogue_voice_subchannel,
        )
        self.interaction_executor = InteractionExecutor(action_sink=self._interaction_sink)
        self.scene_effect_executor = SceneEffectExecutor(effect_sink=self._scene_effect_sink)
        self.history: List[Dict[str, str]] = []

        self.learned_lists = init_learned_lists("learned_lists.json")
        self.list_state: ListState = refresh_state(self.learned_lists)
        self.style_policy = SelfLearningStylePolicy(model_path="style_policy.json")
        self.speech_cfg: AzureSpeechConfig = load_azure_speech_config()
        self.adv_cfg: AdvancedDecisionConfig = load_advanced_decision_config()
        self.codex_delegate = CodexDelegateClient(load_codex_delegate_config())
        self._codex_delegate_available = True
        self._codex_delegate_last_error = ""
        self._codex_lane_mode = self._env_str("CODEX_LANE_MODE", "auto", fallback_on_empty=True).lower()
        if self._codex_lane_mode not in {"auto", "fast", "deep"}:
            self._codex_lane_mode = "auto"
        self._codex_fast_task_max_desc_chars = self._env_int("CODEX_FAST_TASK_MAX_DESC_CHARS", 280, min_v=60, max_v=1200)
        self._codex_fast_task_max_patch_ops = self._env_int("CODEX_FAST_TASK_MAX_PATCH_OPS", 2, min_v=0, max_v=20)
        self._codex_fast_task_max_files = self._env_int("CODEX_FAST_TASK_MAX_FILES", 1, min_v=0, max_v=20)
        self._codex_startup_healthcheck_enabled = self._env_bool("CODEX_STARTUP_HEALTHCHECK_ENABLED", "1")
        self._codex_startup_healthcheck_timeout_sec = self._env_float(
            "CODEX_STARTUP_HEALTHCHECK_TIMEOUT_SEC", 6.0, min_v=1.0, max_v=30.0
        )
        self._codex_startup_healthchecked = False
        self._glm_plan_failure_backoff_sec = self._env_float("GLM_PLAN_FAILURE_BACKOFF_SEC", 12.0, min_v=0.0, max_v=120.0)
        self._glm_plan_last_fail_ts = 0.0
        self._glm_plan_last_error = ""
        self.persona_name = str(self.cfg.persona_profile or "aphrodite").strip().lower()
        self.persona_switch_min_margin = self._env_float(
            "PERSONA_SWITCH_MIN_MARGIN", 0.08, min_v=0.0, max_v=0.9
        )
        self.prompt_manager = PromptManager()
        self.prompt_self_edit_enabled = self._env_bool("PROMPT_SELF_EDIT_ENABLED", "1")
        self.prompt_auto_improve_on_feedback = self._env_bool("PROMPT_AUTO_IMPROVE_ON_FEEDBACK", "1")
        self.prompt_nl_control_enabled = self._env_bool("PROMPT_NL_CONTROL_ENABLED", "1")
        self.prompt_nl_cooldown_sec = self._env_float("PROMPT_NL_COOLDOWN_SEC", 120.0)
        self.reply_language = self._env_str("REPLY_LANGUAGE", "zh").lower()
        self.plain_text_only_enabled = self._env_bool("PLAIN_TEXT_ONLY_ENABLED", "1")
        self.autodebug_verify_command = self._normalize_python_command(
            self._env_str("AUTODEBUG_VERIFY_COMMAND", "")
        )
        self.debug_activity_echo_enabled = self._env_bool("DEBUG_ACTIVITY_ECHO_ENABLED", "0")
        self.activity_ack_llm_enabled = self._env_bool("ACTIVITY_ACK_LLM_ENABLED", "1")
        self.activity_ack_memory_k = self._env_int("ACTIVITY_ACK_MEMORY_K", 2, min_v=0, max_v=6)
        self.activity_ack_memory_rag_enabled = self._env_bool("ACTIVITY_ACK_MEMORY_RAG", "0")
        self.force_one_sentence_output = self._env_bool("FORCE_ONE_SENTENCE_OUTPUT", "1")
        self.reply_max_sentences = self._env_int("REPLY_MAX_SENTENCES", 3, min_v=1, max_v=6)
        self.reply_max_chars = self._env_int("REPLY_MAX_CHARS", 180, min_v=40, max_v=600)
        self._reply_pref_max_sentences: Optional[int] = None
        self._reply_pref_max_chars: Optional[int] = None
        self._reply_turn_max_sentences: Optional[int] = None
        self._reply_turn_max_chars: Optional[int] = None
        self.semantic_trigger_enabled = self._env_bool("SEMANTIC_TRIGGER_ENABLED", "1")
        self.semantic_trigger_top_k = self._env_int("SEMANTIC_TRIGGER_TOP_K", 20, min_v=1, max_v=50)
        self.semantic_trigger_engine: Any = None
        self.semantic_trigger_last: Dict[str, Any] = {}
        self.semantic_debug_autofix_enabled = self._env_bool("SEMANTIC_DEBUG_AUTOFIX_ENABLED", "1")
        self.semantic_debug_autofix_rounds = self._env_int("SEMANTIC_DEBUG_AUTOFIX_ROUNDS", 2, min_v=1, max_v=6)
        self.tts_filler_enabled = self._env_bool("TTS_FILLER_ENABLED", "1")
        self.debug_frontend_chat_enabled = self._env_bool("DEBUG_FRONTEND_CHAT_ENABLED", "0")
        self.selfdrive_semantic_enabled = self._env_bool("SELFDRIVE_SEMANTIC_ENABLED", "1")
        self.selfdrive_semantic_min_confidence = self._env_float("SELFDRIVE_SEMANTIC_MIN_CONFIDENCE", 0.72)
        self.debug_semantic_enabled = self._env_bool("DEBUG_SEMANTIC_ENABLED", "1")
        self.debug_semantic_min_confidence = self._env_float("DEBUG_SEMANTIC_MIN_CONFIDENCE", 0.70)
        self.debug_local_model_enabled = self._env_bool("DEBUG_LOCAL_MODEL_ENABLED", "1")
        self.nl_intent_echo_enabled = self._env_bool("NL_INTENT_ECHO_ENABLED", "0")
        self.nl_control_overlay_semantic_enabled = self._env_bool("NL_CONTROL_OVERLAY_SEMANTIC_ENABLED", "1")
        self.nl_control_overlay_min_confidence = self._env_float("NL_CONTROL_OVERLAY_MIN_CONFIDENCE", 0.74)
        self.nl_control_overlay_shadow_mode = self._env_bool("NL_CONTROL_OVERLAY_SHADOW_MODE", "0")
        self.semantic_guard_conf_threshold = self._env_float(
            "SEMANTIC_GUARD_CONF_THRESHOLD", 0.74, min_v=0.10, max_v=0.99
        )
        self.semantic_intent_lane = SemanticIntentLane(
            semantic_trigger_enabled=self.semantic_trigger_enabled,
            semantic_trigger_top_k=self.semantic_trigger_top_k,
            semantic_guard_conf_threshold=self.semantic_guard_conf_threshold,
            semantic_debug_autofix_enabled=self.semantic_debug_autofix_enabled,
            required_runtime_triggers=REQUIRED_RUNTIME_TRIGGERS,
        )
        self.immediate_protocol = ImmediateReplyProtocol()
        self.input_interpreter = InputInterpreter()
        self._last_interpreted_context: Dict[str, Any] = {}
        self.nl_control_overlay_min_margin = self._env_float("NL_CONTROL_OVERLAY_MIN_MARGIN", 0.12)
        self.debug_local_model_min_confidence = self._env_float("DEBUG_LOCAL_MODEL_MIN_CONFIDENCE", 0.68)
        self.debug_state_window_sec = self._env_float("DEBUG_STATE_WINDOW_SEC", 600.0)
        self.safe_edit_guard_enabled = self._env_bool("SAFE_EDIT_GUARD_ENABLED", "0")
        self.safe_edit_allowed_patterns = self._parse_safe_edit_patterns(
            self._env_str("SAFE_EDIT_ALLOWED_PATTERNS", "")
        )
        self.turn_index = 0
        self.last_persona_switch_turn = -9999
        self.run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + os.urandom(3).hex()
        # FieldTrace / BodyState — 纯观察性日志钩子；不影响响应/路由/人格/记忆
        self._field_trace_store = None
        self._field_trace_extractor = None
        self._body_mapper = None
        self._body_logger = None
        self._observability_enabled = False

        if _FIELD_TRACE_AVAILABLE:
            try:
                self._field_trace_store = FieldTraceStore()
                self._field_trace_extractor = FieldTraceExtractor()
                # BodyState — 纯可观测输出；不改变响应/路由/人格/记忆
                try:
                    from src.body_state.mapper import FieldToBodyMapper
                    from src.body_state.logger import BodyStateLogger
                    self._body_mapper = FieldToBodyMapper()
                    self._body_logger = BodyStateLogger()
                    self._observability_enabled = True
                except Exception:
                    self._observability_enabled = False
            except Exception:
                self._observability_enabled = False

        self.mon: Dict[str, Any] = {
            "llm_calls": 0,
            "llm_last_latency": None,
            "llm_avg_latency": None,
            "llm_latency_sum": 0.0,
            "brain_errors": 0,
            "idle_nudge_count": 0,
            "policy_action": "",
            "tts_ok": 0,
            "tts_fail": 0,
            "adv_used": 0,
            "adv_strategy": "",
            "adv_divergence": 0.0,
            "adv_uncertainty": 0.0,
            "memory_first_used": 0,
            "memory_first_hits": 0,
            "memory_first_goal": "",
            "persona": self.persona_name,
            "persona_auto_enabled": bool(self.cfg.auto_persona_enabled),
            "persona_auto_conf": 0.0,
            "persona_auto_margin": 0.0,
            "persona_auto_reason": "",
            "full_user_permissions": bool(self.cfg.full_user_permissions),
            "screen_capture_enabled": bool(self.cfg.screen_capture_enabled),
            "ide_watch_enabled": bool(self.cfg.ide_watch_enabled),
            "ide_watch_hits": 0,
            "ide_watch_last_emit_ts": 0.0,
            "ide_log_rotations": 0,
            "ide_log_repeat_skipped": 0,
            "nl_overlay_enabled": bool(self.nl_control_overlay_semantic_enabled),
            "nl_overlay_hits": 0,
            "nl_overlay_last_target": "",
            "nl_overlay_last_action": "",
            "nl_overlay_last_reason": "",
            "nl_overlay_last_confidence": 0.0,
            "nl_overlay_shadow_mode": int(bool(self.nl_control_overlay_shadow_mode)),
            "nl_overlay_shadow_hits": 0,
            "nl_overlay_abstain": 0,
            "nl_overlay_ambiguous": 0,
            "ide_auto_fix_enabled": bool(self.cfg.ide_auto_fix_enabled),
            "ide_auto_fix_runs": 0,
            "ide_auto_fix_last_ts": 0.0,
            "ide_autofix_active": 0,
            "ide_autofix_cycle": 0,
            "ide_autofix_last_error_count": 0,
            "ide_autofix_no_progress": 0,
            "ide_autofix_noop_streak": 0,
            "ide_autofix_stop_reason": "",
            "ide_autofix_success_runs": 0,
            "ide_autopilot_enabled": bool(self.cfg.ide_autopilot_enabled),
            "debug_activity_echo_enabled": bool(self.debug_activity_echo_enabled),
            "activity_ack_llm_enabled": bool(self.activity_ack_llm_enabled),
            "activity_ack_memory_rag_enabled": bool(self.activity_ack_memory_rag_enabled),
            "activity_ack_llm_calls": 0,
            "activity_ack_llm_ok": 0,
            "activity_ack_llm_fail": 0,
            "activity_ack_llm_source": "",
            "activity_ack_llm_last_error": "",
            "debug_intent_model": "rule",
            "debug_state_active_until_ts": 0.0,
            "safe_edit_guard_enabled": bool(self.safe_edit_guard_enabled),
            "safe_edit_allowed_patterns": ",".join(self.safe_edit_allowed_patterns),
            "ide_autopilot_runs": 0,
            "ide_autopilot_failures": 0,
            "ide_autopilot_last_rc": None,
            "ide_autopilot_last_ts": 0.0,
            "prompt_version": 1,
            "prompt_source": "default",
            "selfdrive_enabled": 0,
            "selfdrive_goal": "",
            "selfdrive_deadline_ts": 0.0,
            "selfdrive_unbounded": 0,
            "selfdrive_total_steps": 0,
            "selfdrive_steps_done": 0,
            "selfdrive_last_action": "",
            "selfdrive_errors": 0,
            "selfdrive_last_ts": 0.0,
            "selfdrive_mode": "rule",
            "selfdrive_autonomy_level": "L1",
            "selfdrive_budget_max": 0,
            "selfdrive_brief_path": "",
            "codex_delegate_calls": 0,
            "codex_delegate_ok": 0,
            "codex_delegate_fail": 0,
            "codex_delegate_last_note": "",
            "codex_delegate_available": 1,
            "codex_delegate_last_error": "",
            "codex_lane_mode": str(self._codex_lane_mode),
            "codex_lane_last": "",
            "codex_startup_check_enabled": int(bool(self._codex_startup_healthcheck_enabled)),
            "codex_startup_check_ok": 0,
            "codex_startup_check_latency_ms": 0,
            "codex_startup_check_error": "",
            "codex_startup_check_ts": 0.0,
            "glm_plan_last_error": "",
            "glm_plan_last_fail_ts": 0.0,
            "auto_web_search_enabled": bool(self.cfg.auto_web_search_enabled),
            "auto_web_search_runs": 0,
            "auto_web_search_hits": 0,
            "auto_web_search_last_query": "",
            "reply_max_sentences": int(self.reply_max_sentences),
            "reply_max_chars": int(self.reply_max_chars),
            "reply_pref_source": "env",
            "reply_pref_persistent": 0,
        }
        self._ide_watch_offset = 0
        self._ide_watch_last_sig = ""
        self._ide_watch_last_emit_ts = 0.0
        self._ide_log_last_payload_sig = ""
        self._ide_log_repeat_count = 0
        try:
            self._ide_log_rotate_max_bytes = int(self.cfg.ide_debug_log_max_bytes)
        except Exception:
            self._ide_log_rotate_max_bytes = 2 * 1024 * 1024
        self._ide_log_rotate_max_bytes = max(64 * 1024, int(self._ide_log_rotate_max_bytes))
        try:
            self._ide_log_rotate_backups = int(self.cfg.ide_debug_log_backups)
        except Exception:
            self._ide_log_rotate_backups = 3
        self._ide_log_rotate_backups = max(0, min(20, int(self._ide_log_rotate_backups)))
        self._debug_state_active_until_ts = 0.0
        self._debug_state_last_action = ""
        self._debug_state_last_intent_model = "rule"
        self._prompt_nl_last_ts = 0.0
        self._prompt_nl_last_key = ""
        self._prompt_clone_draft: Optional[Dict[str, Any]] = None
        self._prompt_clone_pending_target = ""
        self._ide_auto_fix_last_ts = 0.0
        self._ide_auto_fix_last_key = ""
        self._ide_auto_fix_fail_state: Dict[str, Tuple[int, float]] = {}
        self._ide_auto_fix_max_files_per_run = self._env_int(
            "IDE_AUTO_FIX_MAX_FILES_PER_RUN", 6, min_v=1, max_v=20
        )
        self._ide_auto_fix_continuous_max_files_per_cycle = self._env_int(
            "IDE_AUTO_FIX_CONTINUOUS_MAX_FILES_PER_CYCLE", 5, min_v=1, max_v=5
        )
        self._ide_auto_fix_continuous_verify_per_file = self._env_bool(
            "IDE_AUTO_FIX_CONTINUOUS_VERIFY_PER_FILE", "0"
        )
        self._ide_auto_fix_failure_base_sec = self._env_float(
            "IDE_AUTO_FIX_FAILURE_BASE_SEC", 45.0, min_v=10.0
        )
        self._ide_auto_fix_failure_max_sec = self._env_float("IDE_AUTO_FIX_FAILURE_MAX_SEC", 600.0)
        self._ide_auto_fix_failure_max_sec = max(
            float(self._ide_auto_fix_failure_base_sec),
            float(self._ide_auto_fix_failure_max_sec),
        )
        self._ide_auto_fix_mode = str(self.cfg.ide_auto_fix_mode or "continuous").strip().lower()
        if self._ide_auto_fix_mode not in {"continuous", "single"}:
            self._ide_auto_fix_mode = "continuous"
        try:
            self._ide_auto_fix_loop_cooldown_sec = float(self.cfg.ide_auto_fix_loop_cooldown_sec)
        except Exception:
            self._ide_auto_fix_loop_cooldown_sec = 20.0
        self._ide_auto_fix_loop_cooldown_sec = max(1.0, float(self._ide_auto_fix_loop_cooldown_sec))
        try:
            self._ide_auto_fix_loop_max_cycles = int(self.cfg.ide_auto_fix_loop_max_cycles)
        except Exception:
            self._ide_auto_fix_loop_max_cycles = 40
        self._ide_auto_fix_loop_max_cycles = max(1, min(500, int(self._ide_auto_fix_loop_max_cycles)))
        try:
            self._ide_auto_fix_loop_max_no_progress = int(self.cfg.ide_auto_fix_loop_max_no_progress)
        except Exception:
            self._ide_auto_fix_loop_max_no_progress = 4
        self._ide_auto_fix_loop_max_no_progress = max(1, min(100, int(self._ide_auto_fix_loop_max_no_progress)))
        raw_scope = str(self.cfg.ide_auto_fix_scope or "agentlib,tests")
        scope_dirs = [x.strip().strip("/\\") for x in raw_scope.split(",") if x.strip()]
        self._ide_auto_fix_scope_dirs = scope_dirs if scope_dirs else ["agentlib", "tests"]
        self._ide_auto_fix_smoke_command = str(self.cfg.ide_auto_fix_smoke_command or "").strip()
        self._ide_auto_fix_require_smoke = bool(getattr(self.cfg, "ide_auto_fix_require_smoke", False))
        self._ide_auto_fix_count_only_changed = bool(
            getattr(self.cfg, "ide_auto_fix_count_only_changed", True)
        )
        self._ide_auto_fix_strict_file_relevance = bool(
            getattr(self.cfg, "ide_auto_fix_strict_file_relevance", True)
        )
        try:
            self._ide_auto_fix_noop_cutoff = int(getattr(self.cfg, "ide_auto_fix_noop_cutoff", 2))
        except Exception:
            self._ide_auto_fix_noop_cutoff = 2
        self._ide_auto_fix_noop_cutoff = max(1, min(20, int(self._ide_auto_fix_noop_cutoff)))
        self._ide_auto_fix_ignore_missing_imports = bool(
            getattr(self.cfg, "ide_auto_fix_ignore_missing_imports", True)
        )
        self._ide_auto_fix_full_scan_on_change = bool(self.cfg.ide_auto_fix_full_scan_on_change)
        self._ide_scan_mode = self._env_str("IDE_SCAN_MODE", "model_checkpoints", fallback_on_empty=True).lower()
        if self._ide_scan_mode not in {"model_checkpoints", "log_delta"}:
            self._ide_scan_mode = "model_checkpoints"
        self._ide_debug_strict_codex = self._env_bool("IDE_DEBUG_STRICT_CODEX", "1")
        self._autofix_active = False
        self._autofix_cycle = 0
        self._autofix_no_progress = 0
        self._autofix_last_error_count = 0
        self._autofix_next_allowed_ts = 0.0
        self._autofix_stop_reason = ""
        self._autofix_noop_streak = 0
        self._autofix_lock = threading.Lock()
        self._ide_autopilot_consecutive_failures = 0
        self._selfdrive_lock = threading.Lock()
        self._selfdrive_active = False
        self._selfdrive_goal = ""
        self._selfdrive_deadline_ts = 0.0
        self._selfdrive_unbounded = False
        self._selfdrive_started_ts = 0.0
        self._selfdrive_next_ts = 0.0
        self._selfdrive_last_heartbeat_ts = 0.0
        self._selfdrive_heartbeat_sec = self._env_float("SELFDRIVE_HEARTBEAT_SEC", 30.0, min_v=10.0)
        self._selfdrive_step_index = 0
        self._selfdrive_step_gap_sec = 20.0
        self._selfdrive_autonomy_level = "L1"
        self._selfdrive_budget_override_steps: Optional[int] = None
        self._selfdrive_mode = self._env_str("SELFDRIVE_KERNEL_MODE", "kernel_v16", fallback_on_empty=True)
        self._selfdrive_steps: List[Dict[str, str]] = []
        self._selfdrive_receipt: Dict[str, Any] = {}
        self._selfdrive_file_snapshot: Dict[str, tuple[float, int]] = {}
        self._selfdrive_actions: List[Dict[str, Any]] = []
        self._selfdrive_tests: List[Dict[str, Any]] = []
        self._selfdrive_brief_path = ""
        self._selfdrive_brief_text = ""
        self._selfdrive_heartbeat_log_path = self._env_str(
            "SELFDRIVE_HEARTBEAT_LOG_PATH", os.path.join("outputs", "selfdrive_heartbeat.log")
        )
        self._selfdrive_api_audit_log_path = self._env_str(
            "SELFDRIVE_API_AUDIT_LOG_PATH", os.path.join("outputs", "selfdrive_api_audit.log")
        )
        self._actuation_receipt_log_path = self._env_str(
            "ACTUATION_RECEIPT_LOG_PATH", os.path.join("outputs", "actuation_receipts.jsonl")
        )
        self._actuation_trace_buffer: List[Dict[str, Any]] = []
        self._selfdrive_kernel = AgentKernel(
            planner=V15Planner(),
            worker=SpecialistRouterWorker(
                planner_adapter=GLM5PlannerAdapter(client=self._glm5_plan_for_selfdrive),
                code_adapter=CodexCodeAdapter(client=self._codex_execute_for_selfdrive),
            ),
            judge=V15Judge(autonomous_mode=True),
        )
        self._selfdrive_kernel_state: Optional[KernelAgentState] = None
        self._selfdrive_checkpoint_path = self._env_str(
            "SELFDRIVE_CHECKPOINT_PATH", os.path.join("outputs", "selfdrive_kernel_checkpoint.json")
        )
        self._task_run_log_dir = self._env_str("TASK_RUN_LOG_DIR", os.path.join("outputs", "task_runs"))
        self._task_run_recorder = TaskRunRecorder(self._task_run_log_dir)
        self._task_run_current: Optional[TaskRun] = None
        self._guard_confirm_ttl_sec = self._env_float("GUARD_CONFIRM_TTL_SEC", 120.0)
        self._guard_confirm_pending: Dict[str, Any] = {}
        self._threads: List[threading.Thread] = []
        self._bridge: Optional[ChatBridge] = None
        self._init_semantic_trigger_engine()

    def _run_startup_codex_healthcheck_once(self) -> None:
        if bool(self._codex_startup_healthchecked):
            return
        self._codex_startup_healthchecked = True
        self.mon["codex_startup_check_ts"] = float(time.time())
        if not bool(self._codex_startup_healthcheck_enabled):
            self.mon["codex_startup_check_error"] = "disabled"
            return

        t0 = time.time()
        err = ""
        ok = 0
        try:
            obj = self.codex_delegate.try_chat_json(
                system=(
                    "You are a startup healthcheck endpoint. "
                    "Return strict JSON: {\"ok\":true,\"component\":\"codex\"}."
                ),
                user_payload={"probe": "startup_codex_healthcheck", "ts": int(time.time())},
                temperature=0.0,
                max_tokens=40,
                with_error=True,
                timeout_sec_override=float(self._codex_startup_healthcheck_timeout_sec),
            )
            if isinstance(obj, dict):
                delegate_error = str(obj.get("_delegate_error") or "").strip()
                if delegate_error:
                    err = delegate_error
                else:
                    ok = 1
            else:
                err = "execution_error:startup_healthcheck_no_response"
        except Exception as e:
            err = f"execution_error:{type(e).__name__}:{e}"

        latency_ms = int((time.time() - t0) * 1000)
        self._codex_delegate_available = bool(ok)
        self._codex_delegate_last_error = str(err or "")
        self.mon["codex_delegate_available"] = int(bool(ok))
        self.mon["codex_delegate_last_error"] = str(err or "")
        self.mon["codex_startup_check_ok"] = int(ok)
        self.mon["codex_startup_check_latency_ms"] = int(latency_ms)
        self.mon["codex_startup_check_error"] = str(err or "")
        self._append_jsonl(
            self._selfdrive_api_audit_log_path,
            {
                "ts": time.time(),
                "api": "codex_startup_check",
                "ok": int(ok),
                "latency_ms": int(latency_ms),
                "error": str(err or ""),
            },
        )
        status = "ok" if ok else "fail"
        detail = str(err or "ready")
        self._log_activity(
            tag="codex",
            text=f"[codex] startup_check={status}; latency_ms={latency_ms}; detail={detail}",
            echo=False,
        )

    def start(self, with_db_bridge: bool = True, with_idle_watcher: bool = True) -> None:
        # Protocol mapping note:
        # Runtime startup is event-loop centric (threaded consumers + event queues).
        # CLI/DB bridge/idle watcher are all producers for the same event bus.
        self._run_startup_codex_healthcheck_once()
        self._threads.append(
            start_metrics_thread(
                self.stop_event,
                get_metrics_fn=self.get_metrics,
                db_path=self.cfg.db_path,
                run_id=self.run_id,
                interval_sec=10.0,
            )
        )
        self._threads.append(threading.Thread(target=self._brain_loop, daemon=True, name="runtime-brain"))
        self._threads[-1].start()
        self._threads.append(threading.Thread(target=self._mouth_loop, daemon=True, name="runtime-mouth"))
        self._threads[-1].start()
        if with_idle_watcher:
            self._threads.append(threading.Thread(target=self._idle_watcher, daemon=True, name="runtime-idle"))
            self._threads[-1].start()
        if bool(self.cfg.ide_auto_fix_enabled):
            self._threads.append(
                threading.Thread(target=self._background_autofix_loop, daemon=True, name="runtime-autofix")
            )
            self._threads[-1].start()
        if with_db_bridge:
            self._bridge = ChatBridge(
                db_path=self.cfg.db_path,
                stop_event=self.stop_event,
                on_event=self.event_q.put,
                on_feedback=self._on_feedback,
            )
            self._threads.extend(self._bridge.start())

    def stop(self) -> None:
        self.stop_event.set()
        self.event_q.put(None)
        self.reply_q.put(None)
        save_state(self.cfg.state_path, self.state)

    def run_cli(self) -> int:
        # CLI is an optional producer adapter, not the single runtime entrypoint.
        print("Aphrodite Runtime (GLM). Press Enter on empty input to exit.")
        print(f"RAG mode: {self.cfg.rag_mode}")
        while not self.stop_event.is_set():
            try:
                user_text = input("\nYou: ").strip()
            except EOFError:
                self.stop()
                break
            if not user_text:
                self.stop()
                break
            self.event_q.put({"type": "USER", "text": user_text, "images": []})
        return 0

    def get_metrics(self) -> Dict[str, Any]:
        out = dict(self.mon)
        out["state_energy"] = int(self.state.get("energy", 0) or 0)
        out["state_affinity"] = int(self.state.get("affinity", 0) or 0)
        out["state_idle_pressure"] = int(self.state.get("idle_pressure", 0) or 0)
        out["state_topic"] = str(self.state.get("topic", ""))
        return out

    def _on_feedback(self, msg_id: str, rating: float, comment: str, payload_text: str) -> None:
        try:
            self.style_policy.update_for_msg(str(msg_id), float(rating))
        except Exception:
            pass
        learn_text = " ".join([str(payload_text or ""), str(comment or "")]).strip()
        if not learn_text:
            return
        if (
            self.prompt_self_edit_enabled
            and self.prompt_auto_improve_on_feedback
            and float(rating) < 0
        ):
            try:
                self._log_activity(
                    tag="prompt",
                    text=f"[prompt] feedback improve started; msg_id={msg_id}; rating={rating}",
                    echo=False,
                )
                self.prompt_manager.improve_with_feedback(
                    persona_name=self.persona_name,
                    feedback_text=learn_text,
                )
                self._log_activity(
                    tag="prompt",
                    text=f"[prompt] feedback improve done; persona={self.persona_name}",
                    echo=False,
                )
            except Exception:
                self._log_activity(
                    tag="prompt",
                    text=f"[prompt] feedback improve failed; persona={self.persona_name}",
                    echo=False,
                )
                pass
        try:
            if callable(_learn_lists_from_feedback):
                self.list_state = _learn_lists_from_feedback(
                    text=learn_text,
                    rating=float(rating),
                    learned_lists=self.learned_lists,
                    list_state=self.list_state,
                )
        except Exception:
            pass

    def _build_system_prompt_bundle(
        self, user_text: str, style_hint: str, memory_hint: str = ""
    ) -> Tuple[Optional[str], Optional[Dict[str, str]]]:
        extra = f"; memory_hint={memory_hint}" if memory_hint else ""
        profile = self.prompt_manager.get(self.persona_name)
        self.mon["prompt_version"] = int(profile.version)
        self.mon["prompt_source"] = str(profile.source)
        context_note = (
            f"Context: state_topic={self.state.get('topic')}; "
            f"emotion={self.state.get('emotion')}; "
            f"energy={self.state.get('energy')}; "
            f"affinity={self.state.get('affinity')}; "
            f"persona={self.persona_name}{extra}"
        )
        language_rule = (
            "Default to Simplified Chinese. Only switch language if user explicitly requests another language."
            if self.reply_language.startswith("zh")
            else "Default to English. Only switch language if user explicitly requests another language."
        )
        greeting_rule = (
            "Greeting rule: if user only sends a short hello, reply naturally and warmly, "
            "then offer a light follow-up."
        )
        mode = str(getattr(profile, "prompt_mode", "compose") or "compose").strip().lower()
        if mode == "raw":
            raw_system = str(getattr(profile, "system_prompt", "") or "").strip()
            if not raw_system:
                raw_system = str(profile.response_rules or "").strip()
            if not raw_system:
                raw_system = str(profile.persona or "").strip()
            suffix = f"{greeting_rule} {context_note} {language_rule}".strip()
            return (f"{raw_system}\n\n{suffix}".strip(), None)

        sections = build_system_prompt_sections(
            persona=profile.persona,
            style=(
                f"{profile.style} {style_hint} "
                "Output plain text only; avoid emoji, markdown, bullets, tags, and decorative symbols."
            ),
            safety=profile.safety,
            response_rules=(
                f"{profile.response_rules} {greeting_rule} {context_note} {language_rule}"
            ),
        )
        return (None, sections)

    @staticmethod
    def _should_auto_web_search(user_text: str) -> bool:
        t = str(user_text or "").strip().lower()
        if not t:
            return False
        cn_keys = ["最新", "今天", "新闻", "股价", "汇率", "天气", "政策", "官网", "文档", "教程", "搜索"]
        en_keys = ["latest", "today", "news", "price", "weather", "policy", "official", "docs", "search"]
        if any(k in t for k in en_keys):
            return True
        return any(k in str(user_text or "") for k in cn_keys)

    def _auto_web_search_block(self, user_text: str) -> str:
        if not bool(self.cfg.auto_web_search_enabled):
            return ""
        if not self._should_auto_web_search(user_text):
            return ""
        q = str(user_text or "").strip()
        if not q:
            return ""
        self.mon["auto_web_search_runs"] = int(self.mon.get("auto_web_search_runs", 0) or 0) + 1
        self.mon["auto_web_search_last_query"] = q[:180]
        try:
            result = web_search(
                query=q,
                enabled=True,
                max_results=max(1, int(self.cfg.auto_web_search_max_results)),
                cache_ttl_sec=max(60, int(self.cfg.auto_web_search_cache_ttl_sec)),
            )
        except Exception:
            return ""
        out = str(result or "").strip()
        if not out:
            return ""
        self.mon["auto_web_search_hits"] = int(self.mon.get("auto_web_search_hits", 0) or 0) + 1
        lines = [x.strip() for x in out.splitlines() if x.strip()]
        if len(lines) > 6:
            lines = lines[:6]
        return "\n".join(f"web: {x}" for x in lines)

    def _goal_hint_from_user_text(self, user_text: str) -> str:
        t = str(user_text or "").lower()
        if any(k in t for k in ["plan", "todo", "安排", "计划", "步骤"]):
            return "planning"
        if any(k in t for k in ["code", "bug", "python", "开发", "程序"]):
            return "tech_support"
        if any(k in t for k in ["焦虑", "难受", "sad", "压力", "生气"]):
            return "emotional_support"
        return "general_companion"

    def _maybe_auto_switch_persona(self, user_text: str) -> None:
        if not bool(self.cfg.auto_persona_enabled):
            return
        decision = detect_persona_from_text(user_text=user_text, state=self.state)
        margin = 0.0
        try:
            vals = sorted(
                [float(v) for v in dict(getattr(decision, "scores", {}) or {}).values()],
                reverse=True,
            )
            if len(vals) >= 2:
                margin = max(0.0, float(vals[0] - vals[1]))
        except Exception:
            margin = 0.0
        self.mon["persona_auto_conf"] = float(decision.confidence)
        self.mon["persona_auto_margin"] = float(margin)
        self.mon["persona_auto_reason"] = str(decision.reason)

        if decision.persona == self.persona_name:
            return
        if float(decision.confidence) < float(self.cfg.persona_switch_min_confidence):
            return
        if float(margin) < float(self.persona_switch_min_margin):
            return
        if (self.turn_index - self.last_persona_switch_turn) < int(self.cfg.persona_switch_cooldown_turns):
            return

        self.persona_name = get_persona_profile(decision.persona).name
        self.last_persona_switch_turn = self.turn_index
        self.mon["persona"] = self.persona_name

    @staticmethod
    def _dedup_keep_order(items: List[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for x in items:
            k = str(x).strip()
            if not k or k in seen:
                continue
            seen.add(k)
            out.append(k)
        return out

    @staticmethod
    def _parse_safe_edit_patterns(raw: str) -> List[str]:
        txt = str(raw or "").strip()
        if not txt:
            return []
        parts = re.split(r"[,\n;]+", txt)
        out: List[str] = []
        for p in parts:
            s = str(p or "").strip().replace("\\", "/")
            if s:
                out.append(s)
        # Keep stable order and avoid duplicates.
        seen = set()
        uniq: List[str] = []
        for s in out:
            k = s.lower()
            if k in seen:
                continue
            seen.add(k)
            uniq.append(s)
        return uniq

    def _safe_edit_guard_status_text(self) -> str:
        patterns = ",".join(self.safe_edit_allowed_patterns) if self.safe_edit_allowed_patterns else "<empty>"
        return f"[idewatch:guard] enabled={int(bool(self.safe_edit_guard_enabled))}; patterns={patterns}"

    def _is_safe_edit_path_allowed(self, path: str) -> bool:
        if not bool(self.safe_edit_guard_enabled):
            return True
        abs_path = os.path.abspath(str(path or ""))
        if not abs_path:
            return False
        ws = os.path.abspath(os.getcwd())
        if not abs_path.lower().startswith(ws.lower()):
            return False
        patterns = list(self.safe_edit_allowed_patterns or [])
        if not patterns:
            return False
        rel = os.path.relpath(abs_path, ws).replace("\\", "/")
        abs_norm = abs_path.replace("\\", "/")
        for pat in patterns:
            p = str(pat or "").strip().replace("\\", "/")
            if not p:
                continue
            if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(abs_norm, p):
                return True
            # Allow exact prefix folder matching.
            if rel.startswith(p.rstrip("/") + "/"):
                return True
        return False

    def _init_semantic_trigger_engine(self) -> None:
        self.semantic_intent_lane.init_engine(self.mon)
        self.semantic_trigger_engine = self.semantic_intent_lane.semantic_trigger_engine

    def _looks_like_direct_debug_intent(self, user_text: str) -> bool:
        q = str(user_text or "").strip().lower()
        if not q:
            return False
        if len(q) > 80:
            return False
        negative_markers = (
            "教程",
            "原理",
            "是什么",
            "怎么学",
            "tutorial",
            "principle",
            "what is",
            "learn",
        )
        if any(m in q for m in negative_markers):
            return False
        direct_patterns = (
            r"^\s*开始\s*debug\b",
            r"^\s*开始调试\b",
            r"^\s*(?:请|帮我)?\s*调试\b",
            r"^\s*(?:请|帮我)?\s*debug\b",
            r"\b修bug\b",
            r"排查报错",
            r"查错",
        )
        return any(re.search(pat, q, re.IGNORECASE) for pat in direct_patterns)

    def _looks_like_direct_autofix_intent(self, user_text: str) -> bool:
        q = str(user_text or "").strip().lower()
        if not q:
            return False
        # Keep educational/theory queries out of autofix routing.
        negative_markers = (
            "教程",
            "原理",
            "是什么",
            "怎么学",
            "如何学习",
            "best practices",
            "tutorial",
            "principle",
            "what is",
            "learn",
        )
        if any(m in q for m in negative_markers):
            return False
        patterns = (
            r"修复.*(?:报错|异常|崩溃|bug)",
            r"(?:报错|异常|崩溃|bug).*(?:修复|处理|解决)",
            r"修好.*(?:报错|异常|崩溃|bug)",
            r"(?:报错|异常|崩溃|bug).*(?:修好|搞定)",
            r"(?:帮我|请).*(?:修复|排查).*(?:报错|异常|bug|崩溃)",
            r"(?:帮我|请).*(?:报错|异常|bug|崩溃).*(?:修复|排查|解决)",
            r"\bfix\b.*\b(?:bug|error|crash|exception)\b",
            r"\b(?:bug|error|crash|exception)\b.*\b(?:fix|resolve|repair)\b",
        )
        return any(re.search(pat, q, re.IGNORECASE) for pat in patterns)

    @staticmethod
    def _looks_like_debug_theory_query(user_text: str) -> bool:
        q = str(user_text or "").strip().lower()
        if not q:
            return False
        has_debug_topic = bool(re.search(r"(debug|调试|报错|异常|bug)", q, flags=re.IGNORECASE))
        if not has_debug_topic:
            return False
        return bool(
            re.search(
                r"(教程|原理|是什么|怎么学|最佳实践|best practices|tutorial|what is|principle|theory|learn)",
                q,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _has_debug_topic_token(user_text: str) -> bool:
        q = str(user_text or "").strip().lower()
        if not q:
            return False
        return bool(re.search(r"(debug|调试|报错|异常|崩溃|bug|traceback|error|autofix|idewatch)", q, re.IGNORECASE))

    def _is_debug_command_text(self, user_text: str) -> bool:
        q = str(user_text or "").strip().lower()
        if not q.startswith("/"):
            return False
        cmd = q.split(maxsplit=1)[0]
        return cmd in {"/selfdrive", "/idewatch", "/autofix", "/selfcheck", "/debug"}

    def _looks_like_debug_control_intent(self, user_text: str) -> bool:
        q = str(user_text or "").strip().lower()
        if not q:
            return False
        if self._looks_like_debug_theory_query(q):
            return False
        if self._looks_like_direct_autofix_intent(q) or self._looks_like_direct_debug_intent(q):
            return True
        if not self._has_debug_topic_token(q):
            return False
        return bool(
            re.search(
                r"(开启|关闭|打开|停止|修复|排查|自检|监控|状态|进度|watch|fix|status|check|scan|monitor|turn on|turn off)",
                q,
                re.IGNORECASE,
            )
        )

    def _should_route_debug_command(self, user_text: str) -> bool:
        q = str(user_text or "").strip()
        if not q:
            return False
        if self._is_debug_command_text(q):
            return True
        if self._looks_like_debug_control_intent(q):
            return True
        if time.time() < float(self._debug_state_active_until_ts or 0.0):
            low = q.lower()
            if len(low) <= 40 and bool(
                re.search(r"(继续|暂停|停止|状态|进度|turn it off|off|on|continue|status|stop)", low, re.IGNORECASE)
            ):
                return True
        return False

    def _semantic_guard_decision(
        self,
        *,
        text: str,
        intent: str,
        suggested_mode: str,
        confidence: float,
    ) -> Dict[str, Any]:
        return self.semantic_intent_lane.semantic_guard_decision(
            text=text,
            intent=intent,
            suggested_mode=suggested_mode,
            confidence=confidence,
        )

    def _extract_python_target_from_text(self, user_text: str) -> str:
        text = str(user_text or "")
        if not text:
            return ""
        patterns = [
            r"([A-Za-z]:[\\/][^\s\"'`]+?\.py)",
            r"((?:\.{1,2}[\\/])?[A-Za-z0-9_\-./\\]+?\.py)",
        ]
        cands: List[str] = []
        for pat in patterns:
            cands.extend(re.findall(pat, text))
        cleaned: List[str] = []
        for raw in cands:
            t = str(raw or "").strip().strip("\"'`")
            t = t.rstrip(".,;:!?)]}，。；：！？")
            if t and t.lower().endswith(".py"):
                cleaned.append(t)
        for token in cleaned:
            resolved = self._resolve_python_path(token)
            if resolved:
                return resolved
        return ""

    def _resolve_python_path(self, token: str) -> str:
        t = str(token or "").strip()
        if not t:
            return ""
        if os.path.isabs(t):
            return os.path.abspath(t) if os.path.isfile(t) else ""

        ws = os.path.abspath(os.getcwd())
        candidates = [
            os.path.abspath(os.path.join(ws, t)),
            os.path.abspath(os.path.join(ws, "agentlib", t)),
            os.path.abspath(os.path.join(ws, "tests", t)),
            os.path.abspath(os.path.join(ws, "src", t)),
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p

        base = os.path.basename(t)
        if not base.lower().endswith(".py"):
            return ""
        max_hits = 1
        hits: List[str] = []
        for root, dirs, files in os.walk(ws):
            rel = os.path.relpath(root, ws).replace("\\", "/").lower()
            if rel.startswith(".venv") or rel.startswith("__pycache__") or "/.venv/" in rel:
                continue
            if base in files:
                hits.append(os.path.abspath(os.path.join(root, base)))
                if len(hits) >= max_hits:
                    break
        return hits[0] if hits else ""

    @staticmethod
    def _looks_like_error_context(text: str) -> bool:
        t = str(text or "").lower()
        if not t:
            return False
        keys = [
            "traceback",
            "error",
            "exception",
            "failed",
            "pyright",
            "could not be resolved",
            "not be resolved",
            "import \"",
            "nameerror",
            "typeerror",
            "valueerror",
            "attributeerror",
            "runtimeerror",
            "报错",
            "异常",
            "崩溃",
            "失败",
        ]
        return any(k in t for k in keys)

    def _run_semantic_debug_workflow(self, user_text: str, *, prefer_full_scan: bool = False) -> Optional[str]:
        if not bool(self.semantic_debug_autofix_enabled):
            return None
        target = self._extract_python_target_from_text(user_text)
        fallback_ctx = ""
        should_full_scan = bool(prefer_full_scan) or self._looks_like_direct_autofix_intent(user_text) or self._looks_like_direct_debug_intent(user_text)
        if not target:
            try:
                candidates = self._collect_debug_log_autofix_targets()
            except Exception:
                candidates = []
            if candidates:
                target = str(candidates[0][0] or "").strip()
                fallback_ctx = str(candidates[0][1] or "").strip()
                self.mon["semantic_debug_last_target_source"] = "debug_log"
            else:
                if should_full_scan:
                    self.mon["semantic_debug_last_target_source"] = "full_scan"
                    return self._start_continuous_autofix_session(trigger_text=user_text, intent_model="rule_autofix")
                target = self._pick_default_autofix_target()
                if target:
                    self.mon["semantic_debug_last_target_source"] = "default_target"
                else:
                    self.mon["semantic_debug_last_target_source"] = "none"
                    return (
                        "已识别自动修复意图，但未定位到目标 Python 文件；"
                        "请附上 `.py` 路径或完整 traceback。"
                    )
        else:
            self.mon["semantic_debug_last_target_source"] = "user_text"
        target_abs = os.path.abspath(target)
        ws = os.path.abspath(os.getcwd())
        if bool(self.cfg.ide_auto_fix_only_workspace) and (not target_abs.lower().startswith(ws.lower())):
            return f"已识别调试意图，但目标文件不在工作区内：{target_abs}"

        rel = os.path.relpath(target_abs, ws).replace("\\", "/")
        self.mon["semantic_debug_last_file"] = rel
        self.mon["semantic_debug_autofix_runs"] = int(self.mon.get("semantic_debug_autofix_runs", 0) or 0) + 1

        has_runtime_error = self._looks_like_error_context(user_text)
        ok_selfcheck, selfcheck_msg = selfcheck_python_target(target_abs)
        if ok_selfcheck and not has_runtime_error:
            self.mon["semantic_debug_last_result"] = "selfcheck_ok"
            return f"自动修复检查完成：`{rel}` 语法通过。若是运行时错误，请补充 traceback。"

        error_context = str(user_text or "").strip()
        if fallback_ctx and (fallback_ctx not in error_context):
            error_context = (error_context + "\n" + fallback_ctx).strip()
        result = auto_debug_python_file(
            file_path=target_abs,
            max_rounds=int(self.semantic_debug_autofix_rounds),
            error_context=error_context,
            verify_command=str(self.autodebug_verify_command or "").strip(),
            cwd=os.path.dirname(target_abs),
        )
        if bool(result.ok):
            self.mon["semantic_debug_autofix_ok"] = int(self.mon.get("semantic_debug_autofix_ok", 0) or 0) + 1
            self.mon["semantic_debug_last_result"] = f"ok:{result.classification}"
            return (
                f"自动修复已执行：`{rel}`，结果=成功，分类={result.classification}，"
                f"应用轮次={int(result.applied_rounds)}。"
            )

        self.mon["semantic_debug_autofix_fail"] = int(self.mon.get("semantic_debug_autofix_fail", 0) or 0) + 1
        self.mon["semantic_debug_last_result"] = f"fail:{result.classification}"
        tail = str(result.message or "").strip()
        if len(tail) > 180:
            tail = tail[:180].rstrip() + "..."
        if ok_selfcheck and has_runtime_error and str(result.classification) == "skipped":
            return (
                f"已识别自动修复意图：`{rel}`。当前更像运行时问题，"
                "请补充完整 traceback 和触发命令，我再继续自动修复。"
            )
        return f"已执行自动修复：`{rel}`，结果=未修复（{result.classification}）。原因：{tail}"

    def _pick_default_autofix_target(self) -> str:
        ws = os.path.abspath(os.getcwd())
        # 1) Explicit env override.
        env_target = str(os.getenv("AUTOFIX_DEFAULT_FILE", "") or "").strip()
        if env_target:
            resolved = self._resolve_python_path(env_target)
            if resolved:
                return resolved
        # 2) Last successful semantic target in metrics.
        last_rel = str(self.mon.get("semantic_debug_last_file") or "").strip()
        if last_rel:
            p = os.path.abspath(os.path.join(ws, last_rel))
            if os.path.isfile(p) and p.lower().endswith(".py"):
                return p
        # 3) Common entry script in repo root.
        preferred = os.path.abspath(os.path.join(ws, "Aphrodite demo ver.A.py"))
        if os.path.isfile(preferred):
            return preferred
        # 4) Most recently modified python file in workspace root.
        root_py: List[Tuple[float, str]] = []
        try:
            for name in os.listdir(ws):
                p = os.path.join(ws, name)
                if os.path.isfile(p) and p.lower().endswith(".py"):
                    root_py.append((float(os.path.getmtime(p)), os.path.abspath(p)))
        except Exception:
            root_py = []
        if root_py:
            root_py.sort(key=lambda x: x[0], reverse=True)
            return root_py[0][1]
        return ""

    def _run_autofix_full_scan_once(self, trigger_text: str = "") -> str:
        files = self._iter_autofix_scope_python_files()
        scanned = len(files)
        if scanned <= 0:
            return "[autofix:scan] scanned=0; issues=0; attempted=0; fixed=0; failed=0"

        max_attempts = max(1, int(self._ide_auto_fix_max_files_per_run))
        issues = 0
        attempted = 0
        fixed = 0
        failed = 0

        for path in files:
            ok, err = self._selfcheck_single_file(path)
            if ok:
                continue
            issues += 1
            if attempted >= max_attempts:
                continue
            attempted += 1
            self.mon["ide_auto_fix_runs"] = int(self.mon.get("ide_auto_fix_runs", 0) or 0) + 1
            self.mon["ide_auto_fix_last_ts"] = float(time.time())
            result = auto_debug_python_file(
                file_path=path,
                max_rounds=max(1, int(self.cfg.ide_auto_fix_rounds)),
                error_context=f"fullscan autofix selfcheck failed:\n{err}\ntrigger={str(trigger_text or '').strip()}",
                verify_command=str(self.autodebug_verify_command or "").strip(),
                cwd=os.path.dirname(path),
            )
            if bool(result.ok):
                fixed += 1
                self.mon["ide_autofix_success_runs"] = int(self.mon.get("ide_autofix_success_runs", 0) or 0) + 1
            else:
                failed += 1

        self.mon["ide_autofix_last_error_count"] = int(issues)
        self.mon["ide_autofix_last_scan_files"] = int(scanned)
        self.mon["ide_autofix_last_scan_attempted"] = int(attempted)

        if issues == 0:
            return f"[autofix:scan] scanned={scanned}; issues=0; attempted=0; fixed=0; failed=0; status=clean"
        return (
            f"[autofix:scan] scanned={scanned}; issues={issues}; "
            f"attempted={attempted}; fixed={fixed}; failed={failed}"
        )

    def _iter_scope_python_files(self, scope_dirs: List[str]) -> List[str]:
        ws = os.path.abspath(os.getcwd())
        out: List[str] = []
        seen: set[str] = set()
        for scope in list(scope_dirs or []):
            s = str(scope or "").strip().strip("/\\")
            if not s:
                continue
            root = ws if s in {".", "./", ".\\"} else os.path.abspath(os.path.join(ws, s))
            if not os.path.isdir(root):
                continue
            for r, dirs, files in os.walk(root, onerror=lambda _e: None):
                rel = os.path.relpath(r, ws).replace("\\", "/").lower()
                if rel.startswith(".venv") or "/.venv/" in rel or "__pycache__" in rel:
                    continue
                dirs[:] = [d for d in dirs if d not in {"__pycache__", ".venv"}]
                for fn in files:
                    if not fn.lower().endswith(".py"):
                        continue
                    p = os.path.abspath(os.path.join(r, fn))
                    key = p.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(p)
        out.sort()
        return out

    def _collect_fullscope_diagnostics(self) -> Dict[str, Any]:
        scope_dirs = list(self._ide_auto_fix_scope_dirs or ["agentlib", "tests"])
        files = self._iter_scope_python_files(scope_dirs)
        error_items: List[Dict[str, Any]] = []

        for path in files:
            ok, err = self._selfcheck_single_file(path)
            if ok:
                continue
            error_items.append(
                {
                    "file": os.path.abspath(path),
                    "line": 1,
                    "message": str(err or "compile check failed"),
                    "source": "compile",
                }
            )

        # Merge IDE debug-log derived static/runtime errors (e.g. pyright diagnostics).
        try:
            for p, ctx in self._collect_debug_log_autofix_targets():
                abs_path = os.path.abspath(str(p or ""))
                if not abs_path:
                    continue
                if not any(str(it.get("file") or "").lower() == abs_path.lower() for it in error_items):
                    msg = str(ctx or "").strip()
                    if len(msg) > 300:
                        msg = msg[-300:]
                    error_items.append(
                        {
                            "file": abs_path,
                            "line": 1,
                            "message": msg or "ide debug log reported error",
                            "source": "ide_log",
                        }
                    )
        except Exception:
            pass

        missing_import_count = 0
        for item in error_items:
            msg = str(item.get("message") or "").lower()
            if (
                ("no module named" in msg)
                or ("could not be resolved" in msg)
                or ("import" in msg and "not found" in msg)
            ):
                missing_import_count += 1

        if bool(self._ide_auto_fix_ignore_missing_imports):
            error_items_fixable = [
                i
                for i in error_items
                if not (
                    ("no module named" in str(i.get("message") or "").lower())
                    or ("could not be resolved" in str(i.get("message") or "").lower())
                )
            ]
        else:
            error_items_fixable = list(error_items)

        smoke_ok = True
        smoke_required = bool(self._ide_auto_fix_require_smoke)
        smoke_message = "ok"
        cmd = str(self._ide_auto_fix_smoke_command or "").strip()
        if cmd:
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=os.path.abspath(os.getcwd()),
                    capture_output=True,
                    text=True,
                    shell=True,
                    timeout=max(5.0, float(self.cfg.ide_autopilot_timeout_sec)),
                )
                smoke_ok = int(proc.returncode) == 0
                out = (str(proc.stdout or "") + "\n" + str(proc.stderr or "")).strip()
                smoke_message = out[:300] if out else f"rc={int(proc.returncode)}"
            except Exception as e:
                smoke_ok = False
                smoke_message = f"{type(e).__name__}: {e}"

        error_text = "\n".join(
            f"{str(i.get('file') or '')}:{int(i.get('line') or 1)} - {str(i.get('source') or 'compile')}: {str(i.get('message') or '')}"
            for i in error_items_fixable[:20]
        ).strip()

        return {
            "scope_dirs": scope_dirs,
            "files_scanned": int(len(files)),
            "error_items": list(error_items),
            "error_items_fixable": list(error_items_fixable),
            "error_count_total": int(len(error_items)),
            "error_count_fixable": int(len(error_items_fixable)),
            "error_count": int(len(error_items_fixable)),
            "error_text": error_text,
            "smoke_ok": bool(smoke_ok),
            "smoke_required": bool(smoke_required),
            "smoke_message": str(smoke_message),
            "missing_import_count": int(missing_import_count),
        }

    def _run_auto_fix_candidates(
        self,
        *,
        ranked_candidates: List[str],
        norm_hit: str,
        error_context: str,
        now: float,
        ignore_last_key: bool = False,
        ignore_fail_backoff: bool = False,
    ) -> Dict[str, Any]:
        attempted_count = 0
        fixed_count = 0
        attempted_items: List[Dict[str, Any]] = []
        modified_files: List[str] = []
        noop_success_files = 0
        skipped_irrelevant_files = 0
        attempted_with_change = 0
        attempted_without_change = 0

        ctx = str(error_context or "")
        ctx_low = ctx.lower()
        max_files = max(1, int(self._ide_auto_fix_max_files_per_run))

        for cand in list(ranked_candidates or []):
            if attempted_count >= max_files:
                break
            path = os.path.abspath(str(cand or ""))
            if not path or (not os.path.isfile(path)) or (not path.lower().endswith(".py")):
                continue

            if not self._is_safe_edit_path_allowed(path):
                attempted_items.append(
                    {
                        "file": path,
                        "ok": False,
                        "changed": False,
                        "classification": "blocked_guard",
                        "message": "blocked by safe-edit guard",
                    }
                )
                continue

            if bool(self._ide_auto_fix_strict_file_relevance):
                base = os.path.basename(path).lower()
                is_relevant = (base in ctx_low) or (path.replace("\\", "/").lower() in ctx_low)
                if not is_relevant:
                    skipped_irrelevant_files += 1
                    attempted_items.append(
                        {
                            "file": path,
                            "ok": False,
                            "changed": False,
                            "classification": "skipped",
                            "message": "error not for target",
                        }
                    )
                    continue

            attempted_count += 1
            result = auto_debug_python_file(
                file_path=path,
                max_rounds=max(1, int(self.cfg.ide_auto_fix_rounds)),
                error_context=ctx,
                verify_command=str(self.autodebug_verify_command or "").strip(),
                cwd=os.path.dirname(path),
            )
            changed = bool(getattr(result, "changed", False))
            ok = bool(getattr(result, "ok", False))
            classification = str(getattr(result, "classification", "") or "")
            msg = str(getattr(result, "message", "") or "")

            if ok and changed:
                fixed_count += 1
                attempted_with_change += 1
                modified_files.append(path)
            elif ok and (not changed):
                noop_success_files += 1
                attempted_without_change += 1

            attempted_items.append(
                {
                    "file": path,
                    "ok": ok,
                    "changed": changed,
                    "classification": classification,
                    "message": msg,
                }
            )

        effective_fixed_count = int(fixed_count)
        if (not bool(self._ide_auto_fix_count_only_changed)) and noop_success_files > 0:
            effective_fixed_count += int(noop_success_files)

        return {
            "attempted_count": int(attempted_count),
            "fixed_count": int(fixed_count),
            "effective_fixed_count": int(effective_fixed_count),
            "attempted_items": attempted_items,
            "modified_files": modified_files,
            "noop_success_files": int(noop_success_files),
            "skipped_irrelevant_files": int(skipped_irrelevant_files),
            "attempted_with_change": int(attempted_with_change),
            "attempted_without_change": int(attempted_without_change),
        }

    def _run_continuous_autofix_cycle(
        self,
        *,
        force: bool = False,
        trigger_text: str = "",
        intent_model: str = "",
    ) -> Optional[str]:
        now = float(time.time())
        if (not force) and now < float(self._autofix_next_allowed_ts or 0.0):
            return None
        if not bool(self._autofix_active):
            return None

        self._autofix_cycle += 1
        self.mon["ide_autofix_cycle"] = int(self._autofix_cycle)
        self.mon["ide_autofix_active"] = 1

        diag = self._collect_fullscope_diagnostics()
        before_errors = int(diag.get("error_count", 0) or 0)
        smoke_ok = bool(diag.get("smoke_ok", True))
        smoke_required = bool(diag.get("smoke_required", self._ide_auto_fix_require_smoke))
        missing_import_count = int(diag.get("missing_import_count", 0) or 0)
        error_count_fixable = int(diag.get("error_count_fixable", before_errors) or 0)

        if before_errors <= 0 and (smoke_ok or (not smoke_required)):
            self._autofix_active = False
            self._autofix_stop_reason = "success"
            self.mon["ide_autofix_stop_reason"] = "success"
            payload = {
                "stop_reason": "success",
                "status": "converged",
                "before_errors": 0,
                "after_errors": 0,
                "smoke_required": int(bool(smoke_required)),
                "smoke_ok": int(bool(smoke_ok)),
            }
            return "[idewatch:auto_fix_cycle]\n" + json.dumps(payload, ensure_ascii=False)

        if before_errors <= 0 and missing_import_count > 0 and error_count_fixable <= 0:
            self._autofix_active = False
            self._autofix_stop_reason = "dependency_blocked"
            self.mon["ide_autofix_stop_reason"] = "dependency_blocked"
            payload = {
                "stop_reason": "dependency_blocked",
                "status": "dependency_blocked",
                "before_errors": 0,
                "after_errors": 0,
                "missing_import_count": int(missing_import_count),
            }
            return "[idewatch:auto_fix_cycle]\n" + json.dumps(payload, ensure_ascii=False)

        ranked_candidates = [str(i.get("file") or "") for i in list(diag.get("error_items_fixable") or []) if str(i.get("file") or "").strip()]
        run = self._run_auto_fix_candidates(
            ranked_candidates=ranked_candidates,
            norm_hit=str(trigger_text or "").strip().lower(),
            error_context=str(diag.get("error_text") or ""),
            now=now,
            ignore_last_key=True,
            ignore_fail_backoff=True,
        )

        full_rescan = 0
        after_errors = before_errors
        if bool(self._ide_auto_fix_full_scan_on_change) and list(run.get("modified_files") or []):
            diag_after = self._collect_fullscope_diagnostics()
            after_errors = int(diag_after.get("error_count", before_errors) or 0)
            full_rescan = 1

        effective_fixed = int(run.get("effective_fixed_count", run.get("fixed_count", 0)) or 0)
        if effective_fixed <= 0:
            self._autofix_no_progress += 1
        else:
            self._autofix_no_progress = 0
        self.mon["ide_autofix_no_progress"] = int(self._autofix_no_progress)

        noop_success_files = int(run.get("noop_success_files", 0) or 0)
        if noop_success_files > 0 and effective_fixed <= 0:
            self._autofix_noop_streak += 1
        else:
            self._autofix_noop_streak = 0
        self.mon["ide_autofix_noop_streak"] = int(self._autofix_noop_streak)

        stop_reason = ""
        if self._autofix_noop_streak >= int(self._ide_auto_fix_noop_cutoff):
            stop_reason = "noop_storm"
        elif self._autofix_no_progress >= int(self._ide_auto_fix_loop_max_no_progress):
            stop_reason = "no_progress"
        elif self._autofix_cycle >= int(self._ide_auto_fix_loop_max_cycles):
            stop_reason = "max_cycles"

        if stop_reason:
            self._autofix_active = False
            self._autofix_stop_reason = stop_reason
            self.mon["ide_autofix_stop_reason"] = stop_reason

        self._autofix_next_allowed_ts = now + float(self._ide_auto_fix_loop_cooldown_sec)
        payload = {
            "stop_reason": stop_reason,
            "before_errors": int(before_errors),
            "after_errors": int(after_errors),
            "attempted_count": int(run.get("attempted_count", 0) or 0),
            "fixed_count": int(run.get("fixed_count", 0) or 0),
            "effective_fixed_count": int(effective_fixed),
            "noop_success_files": int(noop_success_files),
            "full_rescan": int(full_rescan),
            "cycle": int(self._autofix_cycle),
        }
        return "[idewatch:auto_fix_cycle]\n" + json.dumps(payload, ensure_ascii=False)

    def _start_continuous_autofix_session(self, *, trigger_text: str = "", intent_model: str = "") -> str:
        self.cfg.ide_watch_enabled = True
        self.cfg.ide_auto_fix_enabled = True
        # Default to workspace-wide scan for NL-triggered autofix sessions.
        if "." not in list(self._ide_auto_fix_scope_dirs or []):
            self._ide_auto_fix_scope_dirs = ["."] + list(self._ide_auto_fix_scope_dirs or [])
        self._autofix_active = True
        self._autofix_stop_reason = ""
        self.mon["ide_autofix_active"] = 1
        self.mon["ide_autofix_stop_reason"] = ""
        cycle = self._run_continuous_autofix_cycle(
            force=True,
            trigger_text=str(trigger_text or ""),
            intent_model=str(intent_model or ""),
        )
        lead = "[idewatch] auto_fix=1; mode=continuous; active=1"
        try:
            self._log_activity(tag="debug", text=lead, echo=bool(self.debug_frontend_chat_enabled))
            if cycle:
                self._log_activity(tag="debug", text=str(cycle), echo=False)
        except Exception:
            pass
        if cycle:
            return lead + "\n" + str(cycle)
        return lead

    def _build_trace_context(self) -> Tuple[str, str]:
        event_id = f"evt_{int(time.time() * 1000)}_{uuid4().hex[:6]}"
        trace_id = f"{self.run_id}:{self.turn_index}:{event_id}"
        return trace_id, event_id

    def _perception_fusion(self, evt: Any, user_text: str, msg_id: Optional[str], is_idle: bool) -> PerceptionFusionPacket:
        trace_id, event_id = self._build_trace_context()
        command_hint = ""
        txt = str(user_text or "").strip().lower()
        if txt.startswith("/"):
            command_hint = "slash_command"
        elif any(k in txt for k in ("selfdrive", "自推进", "autofix", "debug", "selfcheck")):
            command_hint = "runtime_control"
        return PerceptionFusionPacket(
            trace_id=trace_id,
            event_id=event_id,
            user_text=str(user_text or ""),
            msg_id=msg_id,
            is_idle=bool(is_idle),
            command_hint=command_hint,
            raw_event=dict(evt) if isinstance(evt, dict) else {"raw": str(evt)},
        )

    def _decision_core(self, perception: PerceptionFusionPacket) -> DecisionCoreResult:
        if perception.is_idle:
            return DecisionCoreResult(
                trace_id=perception.trace_id,
                event_id=perception.event_id,
                mode="chat",
                action="respond_idle",
                reason="idle_nudge",
            )
        text = str(perception.user_text or "")
        if self._should_route_debug_command(text):
            return DecisionCoreResult(
                trace_id=perception.trace_id,
                event_id=perception.event_id,
                mode="action",
                action="debug_command",
                reason="direct_debug_path",
            )
        if self._compile_selfdrive_control_dsl(text):
            return DecisionCoreResult(
                trace_id=perception.trace_id,
                event_id=perception.event_id,
                mode="action",
                action="selfdrive_control",
                reason="dsl_control",
            )
        if self._looks_like_selfdrive_status_intent(text):
            return DecisionCoreResult(
                trace_id=perception.trace_id,
                event_id=perception.event_id,
                mode="action",
                action="selfdrive_status",
                reason="status_query",
            )
        return DecisionCoreResult(
            trace_id=perception.trace_id,
            event_id=perception.event_id,
            mode="chat",
            action="llm_chat",
            reason="default_chat",
        )

    def _action_planner(self, decision: DecisionCoreResult, perception: PerceptionFusionPacket) -> Optional[str]:
        if decision.mode != "action":
            return None
        user_text = str(perception.user_text or "")
        if decision.action == "debug_command":
            action_task = KernelTask(
                task_id=f"task_{decision.event_id}",
                kind="debug_workflow",
                description="run debug workflow command",
                input_payload={"user_text": user_text, "trace_id": decision.trace_id, "event_id": decision.event_id},
                retries=0,
            )
            subgoal = ExecutableSubgoal.from_task(action_task)
            retry_policy = subgoal.retry_policy or RetryPolicy(max_attempts=2)
            for _ in range(max(1, int(retry_policy.max_attempts))):
                out = self._handle_debug_command(user_text)
                if out:
                    return str(out)
            return None
        if decision.action == "selfdrive_status":
            return self._selfdrive_status_text()
        if decision.action == "selfdrive_control":
            action_task = KernelTask(
                task_id=f"task_{decision.event_id}",
                kind="selfdrive_control",
                description="apply selfdrive control command",
                input_payload={"command_text": user_text, "trace_id": decision.trace_id, "event_id": decision.event_id},
                retries=0,
            )
            _ = ExecutableSubgoal.from_task(action_task)
            return self._handle_natural_language_control(user_text)
        return None

    def _record_brain_tick(
        self,
        *,
        trace_id: str,
        event_id: str,
        stage: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        pl = dict(payload or {})
        pl["trace_id"] = str(trace_id)
        pl["event_id"] = str(event_id)
        pl["stage"] = str(stage)
        self.mon["brain_last_stage"] = str(stage)
        self.mon["brain_last_trace_id"] = str(trace_id)
        self.mon["brain_last_event_id"] = str(event_id)
        self.mon["brain_tick_count"] = int(self.mon.get("brain_tick_count", 0) or 0) + 1
        self._log_activity(
            tag="brain",
            text=(
                f"[brain_tick] stage={stage}; trace_id={trace_id}; event_id={event_id}; "
                f"payload={json.dumps(pl, ensure_ascii=False, sort_keys=True)}"
            ),
            echo=False,
        )

    def _brain_loop(self) -> None:
        # Unified protocol mapping:
        # perception  -> event_q.get(...) + _parse_event(...)
        # decision    -> immediate_protocol / routing / policy / planning branches
        # actuation   -> _emit_reply(...) -> reply_q (or bridge side effects)
        while not self.stop_event.is_set():
            try:
                evt = self.event_q.get(timeout=0.2)
            except queue.Empty:
                continue
            except Exception:
                continue
            if evt is None:
                break

            user_text, msg_id, is_idle = self._parse_event(evt)
            # perception node: raw event -> normalized text/input shape
            if not user_text:
                continue
            perception = self._perception_fusion(evt=evt, user_text=user_text, msg_id=msg_id, is_idle=is_idle)
            self._record_brain_tick(
                trace_id=perception.trace_id,
                event_id=perception.event_id,
                stage="perception_fusion",
                payload={"command_hint": perception.command_hint, "is_idle": int(perception.is_idle)},
            )
            decision = self._decision_core(perception)
            self._record_brain_tick(
                trace_id=perception.trace_id,
                event_id=perception.event_id,
                stage="decision_core",
                payload={"mode": decision.mode, "action": decision.action, "reason": decision.reason},
            )
            self.mon["trace_id"] = perception.trace_id
            self.mon["event_id"] = perception.event_id
            self._log_activity(
                tag="brain",
                text=(
                    f"[brain] trace_id={perception.trace_id}; event_id={perception.event_id}; "
                    f"decision_mode={decision.mode}; decision_action={decision.action}; reason={decision.reason}"
                ),
                echo=False,
            )

            if not is_idle:
                self.turn_index += 1
                try:
                    reward = infer_reward_from_user_text(
                        user_text,
                        pos_words=self.list_state.pos_words,
                        neg_words=self.list_state.neg_words,
                    )
                    self.style_policy.update(reward)
                except Exception:
                    pass
                mark_user_turn(self.state)
                update_topic(self.state, user_text)
            self._update_reply_length_preferences(user_text=user_text, is_idle=is_idle)

            action_output = self._action_planner(decision=decision, perception=perception)
            self._record_brain_tick(
                trace_id=perception.trace_id,
                event_id=perception.event_id,
                stage="action_planner",
                payload={"decision_mode": decision.mode, "planned_action": decision.action, "has_output": int(bool(action_output))},
            )
            if action_output:
                self._record_brain_tick(
                    trace_id=perception.trace_id,
                    event_id=perception.event_id,
                    stage="execution_receipt",
                    payload={"executor": "runtime_action_planner", "ok": 1, "output_preview": str(action_output)[:120]},
                )
                self.history.append({"role": "assistant", "content": str(action_output)})
                max_msgs = max(2, int(self.cfg.max_history_turns) * 2)
                if len(self.history) > max_msgs:
                    self.history = self.history[-max_msgs:]
                self._emit_presence_reply(msg_id=msg_id, user_text=user_text, reply_text=str(action_output), idle_tag=False, route="direct", latency_tier="tier_0", structured=True, trace_id=perception.trace_id, event_id=perception.event_id)
                self._reply_turn_max_sentences = None
                self._reply_turn_max_chars = None
                save_state(self.cfg.state_path, self.state)
                continue

            if not is_idle:
                route_packet = self.immediate_protocol.send(
                    user_text=user_text,
                    msg_id=msg_id,
                    router=self.semantic_intent_lane.router,
                    state_machine=self.semantic_intent_lane.state_machine,
                    emit_reply=self._build_immediate_emit_reply(
                        user_text=user_text,
                        msg_id=msg_id,
                        trace_id=perception.trace_id,
                        event_id=perception.event_id,
                    ),
                    mon=self.mon,
                ).to_dict()
                # decision node: classify/route user intent into immediate action
                route_action = str(route_packet.get("action") or "CHAT").upper()
                self.history.append({"role": "user", "content": user_text})
                self.history.append({"role": "assistant", "content": str(route_packet.get("immediate") or "")})
                max_msgs = max(2, int(self.cfg.max_history_turns) * 2)
                if len(self.history) > max_msgs:
                    self.history = self.history[-max_msgs:]

                if route_action in {"CHAT", "ASK_CLARIFY"}:
                    self._record_brain_tick(
                        trace_id=perception.trace_id,
                        event_id=perception.event_id,
                        stage="execution_receipt",
                        payload={"executor": "immediate_protocol", "ok": 1, "route_action": route_action},
                    )
                    self._reply_turn_max_sentences = None
                    self._reply_turn_max_chars = None
                    save_state(self.cfg.state_path, self.state)
                    continue

                direct_debug = None
                if self._should_route_debug_command(user_text):
                    direct_debug = self._handle_debug_command(user_text)
                if direct_debug:
                    try:
                        text_dbg = str(direct_debug)
                        if text_dbg.startswith("[idewatch") or text_dbg.startswith("[autofix") or text_dbg.startswith("[selfcheck"):
                            self._log_activity(tag="debug", text=text_dbg, echo=bool(self.debug_frontend_chat_enabled))
                    except Exception:
                        pass
                    self.history.append({"role": "assistant", "content": str(direct_debug)})
                    max_msgs = max(2, int(self.cfg.max_history_turns) * 2)
                    if len(self.history) > max_msgs:
                        self.history = self.history[-max_msgs:]
                    self._emit_presence_reply(msg_id=msg_id, user_text=user_text, reply_text=str(direct_debug), idle_tag=False, route="direct", latency_tier="tier_1", structured=True, trace_id=perception.trace_id, event_id=perception.event_id)
                    self._record_brain_tick(
                        trace_id=perception.trace_id,
                        event_id=perception.event_id,
                        stage="execution_receipt",
                        payload={"executor": "direct_debug", "ok": 1, "output_preview": str(direct_debug)[:120]},
                    )
                    self._reply_turn_max_sentences = None
                    self._reply_turn_max_chars = None
                    save_state(self.cfg.state_path, self.state)
                    continue

                direct_video = self._handle_video_summary_command(user_text)
                if direct_video:
                    self.history.append({"role": "assistant", "content": str(direct_video)})
                    max_msgs = max(2, int(self.cfg.max_history_turns) * 2)
                    if len(self.history) > max_msgs:
                        self.history = self.history[-max_msgs:]
                    self._emit_presence_reply(msg_id=msg_id, user_text=user_text, reply_text=str(direct_video), idle_tag=False, route="direct", latency_tier="tier_1", structured=True, trace_id=perception.trace_id, event_id=perception.event_id)
                    self._record_brain_tick(
                        trace_id=perception.trace_id,
                        event_id=perception.event_id,
                        stage="execution_receipt",
                        payload={"executor": "video_summary", "ok": 1, "output_preview": str(direct_video)[:120]},
                    )
                    self._reply_turn_max_sentences = None
                    self._reply_turn_max_chars = None
                    save_state(self.cfg.state_path, self.state)
                    continue

                direct_control = self._handle_natural_language_control(user_text)
                if direct_control:
                    self.history.append({"role": "assistant", "content": str(direct_control)})
                    max_msgs = max(2, int(self.cfg.max_history_turns) * 2)
                    if len(self.history) > max_msgs:
                        self.history = self.history[-max_msgs:]
                    self._emit_presence_reply(msg_id=msg_id, user_text=user_text, reply_text=str(direct_control), idle_tag=False, route="clarification", latency_tier="tier_1", structured=True, trace_id=perception.trace_id, event_id=perception.event_id)
                    self._record_brain_tick(
                        trace_id=perception.trace_id,
                        event_id=perception.event_id,
                        stage="execution_receipt",
                        payload={"executor": "nl_control", "ok": 1, "output_preview": str(direct_control)[:120]},
                    )
                    self._reply_turn_max_sentences = None
                    self._reply_turn_max_chars = None
                    save_state(self.cfg.state_path, self.state)
                    continue

                self._maybe_auto_switch_persona(user_text)

            style_decision = self.style_policy.act(user_text=user_text, state=self.state, msg_id=msg_id)
            self.mon["policy_action"] = style_decision.action
            style_hint = style_guidance_from_action(style_decision.action)
            web_block = self._auto_web_search_block(user_text)

            memory_hint = ""
            if self.cfg.memory_first_enabled:
                goal_hint = self._goal_hint_from_user_text(user_text)
                mem_hits = companion_rag.retrieve_memory_context(
                    user_text,
                    history=self.history,
                    k=self.cfg.memory_top_k,
                )
                mem_slice = arbitrate_memory(user_text=user_text, retrieved=mem_hits, goal_hint=goal_hint)
                self.mon["memory_first_used"] = int(self.mon.get("memory_first_used", 0) or 0) + 1
                self.mon["memory_first_hits"] = len(mem_slice.retrieved)
                self.mon["memory_first_goal"] = str(mem_slice.goal_hint or "")
                memory_hint = f"{mem_slice.goal_hint}:{len(mem_slice.retrieved)}"

                if self.cfg.memory_first_strict and mem_slice.retrieved:
                    rag_items_local = list(mem_slice.retrieved)
                    if web_block:
                        rag_items_local = self._dedup_keep_order(rag_items_local + [web_block])
                    system_prompt, system_sections = self._build_system_prompt_bundle(
                        user_text=user_text,
                        style_hint=style_hint,
                        memory_hint=memory_hint,
                    )
                    prepared = companion_prepare_messages(
                        user_text=user_text,
                        history=self.history,
                        system_prompt=system_prompt,
                        system_sections=system_sections,
                        rag_items=rag_items_local,
                        rag_mode=self.cfg.rag_mode,
                        memory_enabled=False,
                        memory_top_k=self.cfg.memory_top_k,
                    )
                else:
                    merged_kb = self._dedup_keep_order(list(mem_slice.retrieved) + list(DEFAULT_RAG_KB))
                    if web_block:
                        merged_kb = self._dedup_keep_order(merged_kb + [web_block])
                    system_prompt, system_sections = self._build_system_prompt_bundle(
                        user_text=user_text,
                        style_hint=style_hint,
                        memory_hint=memory_hint,
                    )
                    prepared = companion_prepare_messages(
                        user_text=user_text,
                        history=self.history,
                        system_prompt=system_prompt,
                        system_sections=system_sections,
                        rag_knowledge_base=merged_kb,
                        rag_top_k=self.cfg.rag_top_k,
                        rag_mode=self.cfg.rag_mode,
                        memory_enabled=False,
                        memory_top_k=self.cfg.memory_top_k,
                    )
            else:
                kb = list(DEFAULT_RAG_KB)
                if web_block:
                    kb = self._dedup_keep_order(kb + [web_block])
                system_prompt, system_sections = self._build_system_prompt_bundle(
                    user_text=user_text,
                    style_hint=style_hint,
                )
                prepared = companion_prepare_messages(
                    user_text=user_text,
                    history=self.history,
                    system_prompt=system_prompt,
                    system_sections=system_sections,
                    rag_knowledge_base=kb,
                    rag_top_k=self.cfg.rag_top_k,
                    rag_mode=self.cfg.rag_mode,
                    memory_enabled=True,
                    memory_top_k=self.cfg.memory_top_k,
                )
            rag_items = list(prepared.get("rag_items") or [])
            messages = prepared["messages"]

            t0 = time.time()
            try:
                self.mon["llm_calls"] = int(self.mon.get("llm_calls", 0) or 0) + 1
                client = GLMClient()
                if self.adv_cfg.enabled:
                    adv = generate_reply(client=client, messages=messages, config=self.adv_cfg)
                    assistant_text = str(adv.text or "").strip()
                    self.mon["adv_used"] = int(self.mon.get("adv_used", 0) or 0) + 1
                    self.mon["adv_strategy"] = str(adv.strategy or "")
                    self.mon["adv_divergence"] = float(adv.divergence)
                    self.mon["adv_uncertainty"] = float(adv.uncertainty)
                else:
                    chunks: List[str] = []
                    for piece in client.stream_chat(messages=messages, temperature=0.8):
                        chunks.append(piece)
                    assistant_text = "".join(chunks).strip()
                if not assistant_text:
                    assistant_text = "我刚刚有点卡住了，可以再说一遍吗？"
            except Exception:
                self.mon["brain_errors"] = int(self.mon.get("brain_errors", 0) or 0) + 1
                assistant_text = "我刚刚有点走神了，再说一次好吗？"

            dt = time.time() - t0
            self.mon["llm_last_latency"] = dt
            self.mon["llm_latency_sum"] = float(self.mon.get("llm_latency_sum", 0.0) or 0.0) + dt
            calls = max(1, int(self.mon.get("llm_calls", 1) or 1))
            self.mon["llm_avg_latency"] = float(self.mon.get("llm_latency_sum", 0.0) or 0.0) / float(calls)
            if not is_idle:
                self._log_activity(
                    tag="prompt",
                    text=(
                        f"[turn] persona={self.persona_name}; prompt_v={int(self.mon.get('prompt_version', 0) or 0)};"
                        f" source={str(self.mon.get('prompt_source') or '')}; style_action={style_decision.action};"
                        f" rag_items={len(rag_items)}; memory_hint={memory_hint or 'none'}; latency={dt:.2f}s"
                    ),
                    echo=False,
                )

            if not is_idle:
                self.history.append({"role": "user", "content": user_text})
                self.history.append({"role": "assistant", "content": assistant_text})
                max_msgs = max(2, int(self.cfg.max_history_turns) * 2)
                if len(self.history) > max_msgs:
                    self.history = self.history[-max_msgs:]

            try:
                companion_rag.record_turn_memory(
                    user_text=user_text,
                    assistant_text=assistant_text,
                    explicit_items=rag_items,
                )
            except Exception:
                pass

            self._emit_presence_reply(
                msg_id=msg_id,
                user_text=user_text,
                reply_text=assistant_text,
                idle_tag=is_idle,
                route="llm",
                latency_tier="tier_2",
                structured=False,
                trace_id=perception.trace_id,
                event_id=perception.event_id,
            )
            self._record_brain_tick(
                trace_id=perception.trace_id,
                event_id=perception.event_id,
                stage="execution_receipt",
                payload={"executor": "glm_chat", "ok": 1, "latency_sec": round(float(dt), 3), "reply_len": len(assistant_text)},
            )
            self._reply_turn_max_sentences = None
            self._reply_turn_max_chars = None
            save_state(self.cfg.state_path, self.state)

    def _interpret_event_placeholder(self, user_text: str) -> Dict[str, Any]:
        context = dict(self._last_interpreted_context or {})
        interpreted = self.input_interpreter.interpret(user_text, context=context if context else None)
        sem = interpreted.get("semantic_event", {}) if isinstance(interpreted, dict) else {}
        mem = interpreted.get("memory_trigger_signal", {}) if isinstance(interpreted, dict) else {}
        self._last_interpreted_context = {
            "previous_event_type": sem.get("type"),
            "previous_topic": sem.get("topic"),
            "previous_memory_type": mem.get("memory_type"),
            "previous_route": sem.get("persona_route"),
        }
        return interpreted

    def _presence_min_flow(self, *, user_text: str, assistant_text: str, trace_id: str, event_id: str, route: str = "llm", latency_tier: str = "tier_1") -> Dict[str, Any]:
        trace = PresenceTrace(raw_input=user_text, event_version=int(self.turn_index or 0))
        trace.trace_id = str(trace_id)
        interpreted = self._interpret_event_placeholder(user_text)
        trace.interpreted_event = interpreted

        mind_delta = {"attention": 0.6, "current_intention": "respond"}
        self.state_authority.apply_delta({"mind": mind_delta}, source="presence_min_flow", rationale="per-turn mind placeholder")
        trace.mind_delta = mind_delta

        candidate = {
            "content": user_text[:160],
            "type": "episodic",
            "importance": 0.5,
            "emotional_salience": 0.3,
            "confidence": float(interpreted.get("confidence", {}).get("event", 0.0)),
            "first_seen": True,
            "speculative": False,
            "evidence_trace_ids": [str(trace_id)],
        }
        mem_decision = decide_persistence(candidate)
        trace.memory_write_decisions = [mem_decision]

        base_rel = dict((self.state_authority.state.get("relationship") or {
            "boundary_sensitivity": 0.2,
            "carefulness": 0.2,
            "distance_preference": 0.2,
            "permission_to_approach": 0.5,
        }))
        risk = float(interpreted.get("relationship_signal", {}).get("dependency_risk", 0.0))
        rel_next = apply_dependency_guard(base_rel, dependency_risk=risk)
        rel_delta = {k: rel_next.get(k) for k in rel_next.keys() if rel_next.get(k) != base_rel.get(k)}
        if rel_delta:
            self.state_authority.apply_delta({"relationship": rel_next}, source="relationship_guard", rationale=f"dependency_risk={risk}")
        trace.relationship_delta = rel_delta

        weights = {"gaze_user": 0.7, "gaze_down": 0.8 if risk > 0.5 else 0.3, "posture_withdraw": 0.6 if risk > 0.5 else 0.1, "lean_forward": 0.6 if risk > 0.5 else 0.1, "micro_smile": 0.4}
        body_influence = {"motion_suppression": 0.8 if risk > 0.5 else 0.2}
        trace.action_basis_weights = weights
        trace.body_influence = body_influence
        mix = mix_action_weights(weights, body_influence)
        trace.mixer_result = mix
        trace.latency_tier = str(latency_tier or "tier_1")
        trace.final_output = assistant_text
        trace.warnings = []

        payload = trace.to_dict()
        payload["event_id"] = str(event_id)
        payload["route"] = str(route or "llm")
        self._log_activity(tag="presence_trace", text=f"[presence_trace] {json.dumps(payload, ensure_ascii=False)}", echo=False)
        return payload


    def has_tool(self, tool_name: str) -> bool:
        t = str(tool_name or "").strip().lower()
        return t in {
            "debug_workflow",
            "start_selfdrive",
            "pause_selfdrive",
            "resume_selfdrive",
            "status_selfdrive",
            "set_autonomy",
            "set_budget",
            "selfdrive_control",
        }

    def get_tool_schema(self, tool_name: str) -> Dict[str, Any]:
        t = str(tool_name or "").strip().lower()
        if t == "debug_workflow":
            return {"required": ["user_text"]}
        if t == "start_selfdrive":
            return {"required": ["goal"]}
        if t in {"pause_selfdrive", "resume_selfdrive", "status_selfdrive"}:
            return {"required": []}
        if t == "set_autonomy":
            return {"required": ["level"]}
        if t == "set_budget":
            return {"required": ["max_steps"]}
        if t == "selfdrive_control":
            return {"required": ["command"]}
        return {"required": []}

    @staticmethod
    def _serialize_plan_gate_issues(issues: List[Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for i in list(issues or []):
            out.append(
                {
                    "code": str(getattr(i, "code", "") or ""),
                    "subgoal_id": str(getattr(i, "subgoal_id", "") or ""),
                    "message": str(getattr(i, "message", "") or ""),
                    "severity": str(getattr(i, "severity", "") or ""),
                    "hint": str(getattr(i, "hint", "") or ""),
                }
            )
        return out

    def _plan_gate_allow_runtime_action(
        self,
        *,
        action_id: str,
        tool_name: str,
        inputs: Dict[str, Any],
        preconditions: Optional[List[Dict[str, Any]]] = None,
        success_criteria: Optional[List[Dict[str, Any]]] = None,
        fallback: Optional[Dict[str, Any]] = None,
        budget_used: Optional[int] = None,
        budget_max: Optional[int] = None,
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        criteria_raw = list(success_criteria or [])
        if not criteria_raw:
            criteria_raw = [{"op": "predicate_ref", "args": {"name": "runtime_action_ok"}}]
        sg = ExecutableSubgoal(
            id=str(action_id or "runtime_action"),
            intent=str(action_id or "runtime_action"),
            executor_type="runtime",
            tool_name=str(tool_name or ""),
            inputs=dict(inputs or {}),
            dependencies=[],
            preconditions=[],
            success_criteria=[
                SuccessCriterion(op=str(c.get("op") or "").strip(), args=dict(c.get("args") or {}))
                for c in criteria_raw
                if isinstance(c, dict) and str(c.get("op") or "").strip()
            ],
            fallback=dict(fallback or {}),
            retry_policy=RetryPolicy(max_attempts=2, backoff="exponential", base_delay_ms=300),
            state=SubgoalState.READY,
        )
        for p in list(preconditions or []):
            if not isinstance(p, dict):
                continue
            op = str(p.get("op") or "").strip()
            if not op:
                continue
            sg.preconditions.append(Predicate(op=op, args=dict(p.get("args") or {})))
        issues = action_plan_gate_check(
            subgoal=sg,
            tools=self,
            budget_used=budget_used,
            budget_max=budget_max,
            require_success_criteria=True,
        )
        if issues:
            serialized = self._serialize_plan_gate_issues(issues)
            self.mon["plan_gate_blocked"] = int(self.mon.get("plan_gate_blocked", 0) or 0) + 1
            self.mon["plan_gate_last_action"] = str(action_id or "")
            self.mon["plan_gate_last_codes"] = ",".join([str(i.get("code") or "") for i in serialized])
            return False, serialized
        return True, []

    def _check_debug_workflow_gate(self, user_text: str, trigger: str) -> Optional[str]:
        budget_max = self._env_int("SEMANTIC_DEBUG_BUDGET_MAX", 120, min_v=1)
        budget_used = int(self.mon.get("semantic_debug_autofix_runs", 0) or 0)
        allowed, issues = self._plan_gate_allow_runtime_action(
            action_id=f"debug_workflow::{str(trigger or 'unknown')}",
            tool_name="debug_workflow",
            inputs={"user_text": str(user_text or "")},
            preconditions=[{"op": "tool_available", "args": {"tool": "debug_workflow"}}],
            success_criteria=[{"op": "predicate_ref", "args": {"name": "debug_workflow_completed"}}],
            fallback={"on_failure": "ask_user"},
            budget_used=budget_used,
            budget_max=max(1, int(budget_max)),
        )
        if allowed:
            return None
        codes = ",".join([str(i.get("code") or "") for i in issues])
        return f"debug action blocked by plan gate: {codes or 'compile_error'}"

    def _activate_debug_state(self, *, action: str, model: str) -> None:
        self._debug_state_active_until_ts = time.time() + float(self.debug_state_window_sec)
        self._debug_state_last_action = str(action or "")
        self._debug_state_last_intent_model = str(model or "")
        self.mon["debug_intent_model"] = str(model or "")
        self.mon["debug_state_active_until_ts"] = float(self._debug_state_active_until_ts)

    def _handle_debug_command(self, user_text: str) -> Optional[str]:
        text = str(user_text or "").strip()
        low = text.lower()
        if low.startswith('/idewatch guard'):
            body = low[len('/idewatch guard'):].strip()
            if body in {'show', ''}:
                return self._safe_edit_guard_status_text()
            if body == 'on':
                self.safe_edit_guard_enabled = True
                return self._safe_edit_guard_status_text()
            if body == 'off':
                self.safe_edit_guard_enabled = False
                return self._safe_edit_guard_status_text()
            if body.startswith('set'):
                raw = text.split('set',1)[1].strip() if 'set' in text else ''
                self.safe_edit_allowed_patterns = self._parse_safe_edit_patterns(raw)
                return self._safe_edit_guard_status_text()
        if low.startswith('/autodebug'):
            parts = text.split(maxsplit=1)
            target = parts[1].strip() if len(parts) > 1 else ''
            if target and (not self._is_safe_edit_path_allowed(target)):
                return 'blocked by safe-edit guard'
            return 'autodebug skipped'
        # Progress/ETA queries should not be trapped by debug guard flow.
        if self._looks_like_generic_progress_intent(user_text):
            return None
        if low.startswith("/selfdrive"):
            body = text[len("/selfdrive") :].strip()
            if (not body) or body.lower() in {"status", "show"}:
                return self._execute_selfdrive_control_dsl({"command": "STATUS_SELFDRIVE", "args": {}}, source_text=text)
            if body.lower().startswith("stop"):
                return self._execute_selfdrive_control_dsl({"command": "PAUSE_SELFDRIVE", "args": {}}, source_text=text)
            if body.lower().startswith("pause"):
                return self._execute_selfdrive_control_dsl({"command": "PAUSE_SELFDRIVE", "args": {}}, source_text=text)
            if body.lower().startswith("resume"):
                return self._execute_selfdrive_control_dsl({"command": "RESUME_SELFDRIVE", "args": {}}, source_text=text)
            if body.lower().startswith("start"):
                rest = body[5:].strip()
                args = self._build_selfdrive_start_args_from_text(rest)
                return self._execute_selfdrive_control_dsl({"command": "START_SELFDRIVE", "args": args}, source_text=rest)
            return (
                "usage: /selfdrive start <goal> [30m|2h] | /selfdrive status | /selfdrive pause | "
                "/selfdrive resume | /selfdrive stop"
            )

        pred = self.semantic_intent_lane.infer(user_text, self.mon)
        self.semantic_trigger_engine = self.semantic_intent_lane.semantic_trigger_engine
        self.semantic_trigger_last = dict(self.semantic_intent_lane.semantic_trigger_last or {})
        missing_required = str(self.mon.get("semantic_trigger_required_missing") or "")
        direct_autofix_intent = self._looks_like_direct_autofix_intent(user_text)
        if direct_autofix_intent:
            self._activate_debug_state(action="rule_autofix_trigger", model="rule_autofix")
            gate_msg = self._check_debug_workflow_gate(user_text, trigger="rule_autofix")
            if gate_msg:
                return gate_msg
            workflow_reply = self._run_semantic_debug_workflow(user_text, prefer_full_scan=True)
            if workflow_reply:
                return workflow_reply
            return "已识别自动修复意图，但当前没有可用目标。请附上 `.py` 文件路径或 traceback。"
        if self._looks_like_debug_theory_query(user_text):
            return None
        if (not isinstance(pred, dict)) and self._looks_like_direct_debug_intent(user_text):
            self._activate_debug_state(action="rule_debug_trigger", model="rule_fallback")
            gate_msg = self._check_debug_workflow_gate(user_text, trigger="rule_direct_debug")
            if gate_msg:
                return gate_msg
            workflow_reply = self._run_semantic_debug_workflow(user_text, prefer_full_scan=True)
            if workflow_reply:
                return workflow_reply
            return None
        if not isinstance(pred, dict):
            return None

        guard_mode = str(pred.get("suggested_mode") or pred.get("suuggested_mode") or "").strip().lower()
        guard_reason = str(pred.get("guard_reason") or "").strip()
        if guard_mode == "ask_user_confirm":
            if self._looks_like_generic_progress_intent(user_text):
                return None
            self._set_guard_confirm_pending(source_text=user_text, pred=pred, channel="debug")
            guess = self._build_low_confidence_guess_preview(user_text, pred)
            reason_text = self._humanize_guard_reason(guard_reason)
            return (
                "这个请求可能涉及执行动作，我先不直接动手。"
                f"我的理解是：{guess}。"
                "如果理解正确，请回复“确认执行”；如果不对，请补充具体 debug 目标（文件路径、报错堆栈或期望结果）。"
                f"{('（原因：' + reason_text + '）') if reason_text else ''}"
            )
        if guard_mode == "shadow_plan_only":
            plan_preview = self._selfdrive_plan_text(goal=str(user_text or "").strip())
            return (
                "我识别到高风险控制意图，已切换为 shadow plan only：只给计划，不直接执行。\n"
                f"{plan_preview}"
            )

        decision = str(pred.get("decision") or "")
        selected_trigger = str(pred.get("selected_trigger") or pred.get("intent") or "")
        top_trigger = str(pred.get("top_trigger") or "")
        suggested_mode = str(pred.get("suggested_mode") or pred.get("suuggested_mode") or "").strip().lower()
        execution_allowed = bool(pred.get("execution_allowed") or False)
        if not decision:
            if suggested_mode == "ask_clarify":
                decision = "ask_clarification"
            elif suggested_mode in {"debug", "selfdrive"} and selected_trigger:
                decision = "trigger"
            else:
                decision = "no_trigger"
        promoted_edge = False
        if not selected_trigger and top_trigger == "code_debug":
            if decision == "no_trigger":
                edge_conf = float(pred.get("confidence") or 0.0)
                edge_margin = float(pred.get("margin") or 0.0)
                edge_score = float(pred.get("top_score") or 0.0)
                text_low = str(user_text or "").strip().lower()
                theory_like = bool(
                    re.search(
                        r"(教程|原理|是什么|怎么学|best practices|tutorial|what is|learn debug)",
                        text_low,
                        flags=re.IGNORECASE,
                    )
                )
                if (
                    (not theory_like)
                    and self._has_debug_topic_token(text_low)
                    and edge_conf >= 0.36
                    and edge_margin >= 0.10
                    and edge_score >= 0.38
                ):
                    decision = "trigger"
                    selected_trigger = "code_debug"
                    promoted_edge = True
            elif decision == "ask_clarification":
                selected_trigger = "code_debug"
        if selected_trigger != "code_debug" and suggested_mode != "debug":
            if missing_required and self._looks_like_direct_debug_intent(user_text):
                self._activate_debug_state(action="rule_debug_trigger", model="rule_fallback")
                gate_msg = self._check_debug_workflow_gate(user_text, trigger="semantic_missing_required")
                if gate_msg:
                    return gate_msg
                workflow_reply = self._run_semantic_debug_workflow(user_text, prefer_full_scan=True)
                if workflow_reply:
                    return workflow_reply
                return None
            return None

        confidence = float(pred.get("confidence") or 0.0)
        margin = float(pred.get("margin") or 0.0)
        min_conf = max(0.30, float(self.debug_semantic_min_confidence))
        if decision == "trigger" and (not promoted_edge) and (confidence < min_conf or margin < 0.02):
            return None
        if decision not in {"trigger", "ask_clarification"}:
            return None
        if not execution_allowed:
            if decision == "ask_clarification":
                missing = [str(x) for x in (pred.get("missing_slots") or []) if str(x).strip()]
                if missing:
                    return f"我识别到调试意图，但还缺少关键信息：{'、'.join(missing)}。"
            return "已识别到调试意图，但当前策略不允许直接执行。请先明确目标文件或错误上下文。"

        self._activate_debug_state(action="semantic_debug_trigger", model="semantic_trigger")

        gate_msg = self._check_debug_workflow_gate(user_text, trigger="semantic_debug")
        if gate_msg:
            return gate_msg
        workflow_reply = self._run_semantic_debug_workflow(user_text, prefer_full_scan=True)
        if workflow_reply:
            return workflow_reply
        return None

    @staticmethod
    def _extract_video_url_from_text(user_text: str) -> str:
        text = str(user_text or "").strip()
        if not text:
            return ""
        m = re.search(r"(https?://[^\s\"'<>]+)", text, flags=re.IGNORECASE)
        if not m:
            return ""
        return str(m.group(1) or "").strip()

    @staticmethod
    def _looks_like_video_summary_intent(user_text: str) -> bool:
        q = str(user_text or "").strip().lower()
        if not q:
            return False
        has_video = bool(re.search(r"(视频|video|youtube|youtu\.be|bilibili|b23\.tv)", q, re.IGNORECASE))
        has_summary = bool(re.search(r"(总结|摘要|梳理|提炼|summar(y|ize)|digest)", q, re.IGNORECASE))
        return has_video and has_summary

    def _handle_video_summary_command(self, user_text: str) -> Optional[str]:
        text = str(user_text or "").strip()
        if not text:
            return None

        direct_hit = self._looks_like_video_summary_intent(text)
        pred = self.semantic_intent_lane.infer(text, self.mon)
        self.semantic_trigger_engine = self.semantic_intent_lane.semantic_trigger_engine
        self.semantic_trigger_last = dict(self.semantic_intent_lane.semantic_trigger_last or {})
        semantic_hit = False
        if isinstance(pred, dict):
            trigger_id = str(pred.get("selected_trigger") or pred.get("intent") or "").strip().lower()
            decision = str(pred.get("decision") or "").strip().lower()
            if trigger_id == "video_summary" and decision in {"trigger", "ask_clarification"}:
                semantic_hit = True

        if not direct_hit and not semantic_hit:
            return None

        url = self._extract_video_url_from_text(text)
        if not url:
            return "已识别到视频总结请求，但缺少 URL，请补充一个可访问的视频链接。"

        try:
            from video_knowledge_agent.app.workflow import summarize_video_url

            workflow_out = summarize_video_url(url=url)
            report_text = str(workflow_out.get("report") or "").strip()
            report_path = str(workflow_out.get("report_path") or "").strip()
            graph_path = str(workflow_out.get("graph_html_path") or "").strip()
            preview = report_text[:900].strip()
            if report_text and len(report_text) > 900:
                preview += "\n..."
            lines: List[str] = [
                "[video_summary] 已完成视频总结。",
                f"url={url}",
            ]
            if report_path:
                lines.append(f"report_path={report_path}")
            if graph_path:
                lines.append(f"graph_html_path={graph_path}")
            if preview:
                lines.append("summary_preview:")
                lines.append(preview)
            return "\n".join(lines)
        except Exception as e:
            return f"[video_summary] 执行失败: {type(e).__name__}: {e}"

    def _looks_like_selfdrive_start_intent(self, user_text: str) -> bool:
        q = str(user_text or "").strip().lower()
        if not q:
            return False
        has_channel = bool(
            re.search(
                r"(自推进|自主推进|自己推进|selfdrive|autopilot|自动推进|代理|代理模式?|代理人模式?|agent\s*mode|\bagent\b)",
                q,
                re.IGNORECASE,
            )
        )
        has_start = bool(
            re.search(r"(开始|启动|开启|进入|切换|打开|run|start|enable|mode|begin|go)", q, re.IGNORECASE)
        )
        return has_channel and has_start

    def _looks_like_selfdrive_stop_intent(self, user_text: str) -> bool:
        q = str(user_text or "").strip().lower()
        if not q:
            return False
        return bool(
            re.search(
                r"(停止|暂停|关闭|结束|退出|cancel|stop|disable).*(自推进|selfdrive|autopilot|代理|代理模式?|agent)|"
                r"(自推进|selfdrive|autopilot|代理|代理模式?|agent).*(停止|暂停|关闭|结束|退出|cancel|stop|disable)",
                q,
                re.IGNORECASE,
            )
        )

    def _looks_like_selfdrive_status_intent(self, user_text: str) -> bool:
        q = str(user_text or "").strip().lower()
        if not q:
            return False
        return bool(
            re.search(
                r"(自推进|selfdrive|autopilot|代理|代理模式?|agent).*(状态|进度|还在|如何|怎么样|status|progress)|"
                r"(状态|进度|还在|现在在做什么|status|progress)",
                q,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _extract_selfdrive_goal_text(user_text: str) -> str:
        text = str(user_text or "").strip()
        if not text:
            return ""
        goal = re.sub(
            r"(请你?|麻烦你|帮我|然后|接下来|现在|请)\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        goal = re.sub(
            r"(开始|启动|开启|进入|切到|切换到|run|start|enable)\s*",
            "",
            goal,
            flags=re.IGNORECASE,
        )
        goal = re.sub(
            r"(自推进模式?|自主推进模式?|自己推进|自推进|selfdrive|autopilot|代理|代理模式?|代理人模式?|agent\s*mode|\bagent\b)",
            "",
            goal,
            flags=re.IGNORECASE,
        )
        goal = goal.strip(" ，。,.!！?？;；")
        return goal

    @staticmethod
    def _normalize_selfdrive_goal(goal: str) -> str:
        g = str(goal or "").strip()
        if not g:
            return ""
        if g in {"是", "的", "一下", "吧", "呢", "然后", "接下来"}:
            return ""
        if len(g) <= 1:
            return ""
        return g

    @staticmethod
    def _extract_md_candidates(text: str) -> List[str]:
        t = str(text or "")
        if not t:
            return []
        out: List[str] = []
        abs_pat = r"([A-Za-z]:[\\/][^\s\"'`]+?\.md)"
        rel_pat = r"((?:\.{1,2}[\\/])?[A-Za-z0-9_\-./\\]+?\.md)"
        for pat in (abs_pat, rel_pat):
            for item in re.findall(pat, t):
                s = str(item or "").strip().strip("\"'`").rstrip(".,;:!?)]}，。；：！？")
                if s:
                    out.append(s)
        for tok in re.split(r"[\s,，;；:：]+", t):
            s = str(tok or "").strip().strip("\"'`()[]{}")
            if s.lower().endswith(".md"):
                out.append(s)
        uniq: List[str] = []
        seen = set()
        for x in out:
            k = x.lower()
            if k in seen:
                continue
            seen.add(k)
            uniq.append(x)
        return uniq

    def _resolve_task_brief_path(self, token: str) -> str:
        t = str(token or "").strip()
        if not t:
            return ""
        if os.path.isabs(t) and os.path.isfile(t):
            return os.path.abspath(t)
        ws = os.path.abspath(os.getcwd())
        roots = [ws, os.path.join(ws, "docs"), os.path.join(ws, "outputs")]
        user_profile = str(os.getenv("USERPROFILE") or "").strip()
        if user_profile:
            roots.append(os.path.join(user_profile, "Downloads"))
        roots.extend([r"D:\download", r"D:\downloads"])
        for root in roots:
            try:
                cand = os.path.abspath(os.path.join(root, t))
                if os.path.isfile(cand):
                    return cand
            except Exception:
                continue
        base = os.path.basename(t)
        if not base.lower().endswith(".md"):
            return ""
        for root in [ws, os.path.join(ws, "docs"), r"D:\download"]:
            if not os.path.isdir(root):
                continue
            try:
                scanned = 0
                for r, dirs, files in os.walk(root):
                    rel = os.path.relpath(r, root).replace("\\", "/").lower()
                    if rel.startswith(".venv") or "__pycache__" in rel:
                        continue
                    if base in files:
                        return os.path.abspath(os.path.join(r, base))
                    scanned += 1
                    if scanned > 500:
                        break
            except Exception:
                continue
        return ""

    def _load_task_brief_from_text(self, text: str, max_chars: int = 20000) -> Tuple[str, str]:
        for tok in self._extract_md_candidates(text):
            p = self._resolve_task_brief_path(tok)
            if not p:
                continue
            try:
                with open(p, "r", encoding="utf-8") as f:
                    content = f.read()
                return p, str(content or "")[: int(max_chars)]
            except Exception:
                continue
        return "", ""

    @staticmethod
    def _extract_selfdrive_duration_minutes(user_text: str) -> Optional[int]:
        text = str(user_text or "").lower()
        if not text:
            return None
        if re.search(r"(无限|不限时|一直|长期|持续|unbounded|infinite|no\s+limit)", text):
            return None
        if "半小时" in text:
            return 30
        m_hour = re.search(r"(\d+)\s*(小时|h\b|hour|hours)", text, re.IGNORECASE)
        if m_hour:
            try:
                return max(1, min(24 * 60, int(m_hour.group(1)) * 60))
            except Exception:
                return None
        m_min = re.search(r"(\d+)\s*(分钟|分\b|m\b|min|mins|minute|minutes)", text, re.IGNORECASE)
        if m_min:
            try:
                return max(1, min(24 * 60, int(m_min.group(1))))
            except Exception:
                return None
        return None

    @staticmethod
    def _strip_wrapping_quotes(text: str) -> str:
        s = str(text or "").strip()
        if len(s) >= 2 and ((s[0] == s[-1] == "\"") or (s[0] == s[-1] == "'")):
            return s[1:-1].strip()
        return s

    @classmethod
    def _parse_selfdrive_dsl_args(cls, raw_args: str) -> Dict[str, str]:
        args: Dict[str, str] = {}
        buf = ""
        quote = ""
        parts: List[str] = []
        for ch in str(raw_args or ""):
            if ch in {"\"", "'"}:
                if quote == ch:
                    quote = ""
                elif not quote:
                    quote = ch
                buf += ch
                continue
            if ch == "," and not quote:
                part = buf.strip()
                if part:
                    parts.append(part)
                buf = ""
                continue
            buf += ch
        tail = buf.strip()
        if tail:
            parts.append(tail)
        for part in parts:
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            key = str(k or "").strip().lower()
            val = cls._strip_wrapping_quotes(v)
            if key:
                args[key] = val
        return args

    @classmethod
    def _parse_selfdrive_dsl_command(cls, text: str) -> Optional[Dict[str, Any]]:
        t = str(text or "").strip()
        if not t:
            return None
        m = re.match(r"^\s*([A-Za-z_]+)\s*(?:\((.*)\))?\s*$", t)
        if not m:
            return None
        cmd = str(m.group(1) or "").strip().upper()
        raw_args = str(m.group(2) or "").strip()
        args = cls._parse_selfdrive_dsl_args(raw_args)
        return {"command": cmd, "args": args, "raw": t}

    @staticmethod
    def _parse_autonomy_level(value: str) -> str:
        v = str(value or "").strip().upper()
        if not v:
            return ""
        m = re.match(r"^L([0-3])$", v)
        if m:
            return f"L{m.group(1)}"
        alias = {
            "LOW": "L1",
            "MEDIUM": "L2",
            "HIGH": "L3",
            "SAFE": "L0",
        }
        return alias.get(v, "")

    @staticmethod
    def _parse_budget_steps(value: Any) -> Optional[int]:
        text = str(value or "").strip().lower()
        if not text:
            return None
        m = re.match(r"^(\d+)$", text)
        if m:
            return max(4, min(400, int(m.group(1))))
        m = re.match(r"^(\d+)\s*(m|min|mins|minute|minutes)$", text)
        if m:
            return max(4, min(400, int(m.group(1))))
        m = re.match(r"^(\d+)\s*(h|hour|hours)$", text)
        if m:
            return max(4, min(400, int(m.group(1)) * 60))
        return None

    def _compile_selfdrive_control_dsl(self, user_text: str) -> Optional[Dict[str, Any]]:
        parsed = self._parse_selfdrive_dsl_command(user_text)
        if parsed is not None:
            parsed["source"] = "dsl"
            return parsed

        text = str(user_text or "").strip()
        if not text:
            return None

        q = text.lower()
        if re.search(r"(恢复|继续|resume|unpause).*(自推进|selfdrive|autopilot|代理|agent)|"
                     r"(自推进|selfdrive|autopilot|代理|agent).*(恢复|继续|resume|unpause)", q, re.IGNORECASE):
            return {"source": "nl", "command": "RESUME_SELFDRIVE", "args": {}, "raw": text}
        if re.search(r"(自治|autonomy).*(l[0-3])|(l[0-3]).*(自治|autonomy)", q, re.IGNORECASE):
            level_match = re.search(r"\b(l[0-3])\b", q, re.IGNORECASE)
            if level_match:
                return {
                    "source": "nl",
                    "command": "SET_AUTONOMY",
                    "args": {"level": str(level_match.group(1)).upper()},
                    "raw": text,
                }
        if re.search(r"(预算|步数|max[\s_-]*steps?|step\s*limit)", q, re.IGNORECASE):
            num_match = re.search(r"(\d+)", q)
            if num_match:
                return {
                    "source": "nl",
                    "command": "SET_BUDGET",
                    "args": {"max_steps": str(num_match.group(1))},
                    "raw": text,
                }
        if self._looks_like_selfdrive_stop_intent(text):
            return {"source": "nl", "command": "PAUSE_SELFDRIVE", "args": {}, "raw": text}
        if self._looks_like_selfdrive_status_intent(text):
            return {"source": "nl", "command": "STATUS_SELFDRIVE", "args": {}, "raw": text}
        if self._looks_like_selfdrive_start_intent(text) or self._looks_like_selfdrive_delegation_intent(text):
            args = self._build_selfdrive_start_args_from_text(text)
            return {"source": "nl", "command": "START_SELFDRIVE", "args": args, "raw": text}
        return None

    @staticmethod
    def _is_goal_executable_for_selfdrive(goal: str) -> bool:
        text = str(goal or "").strip().lower()
        if not text:
            return False
        if len(text) < 8:
            return False
        blocked = ["随便", "any", "whatever", "你看着办", "do anything", "anything"]
        if any(k in text for k in blocked):
            return False
        return True

    def _review_selfdrive_start_request(self, *, goal: str, source_text: str) -> Dict[str, Any]:
        g = str(goal or "").strip()
        src = str(source_text or "").strip().lower()
        reasons: List[str] = []
        if not self._is_goal_executable_for_selfdrive(g):
            reasons.append("goal_not_executable")
        high_risk = bool(
            re.search(
                r"(autopilot|system[-_ ]?wide|批量执行|系统改动|root|sudo|全量重构|生产环境|production)",
                f"{src} {g}".lower(),
                re.IGNORECASE,
            )
        )
        needs_permission = (not bool(self.cfg.full_user_permissions)) or high_risk
        approved = (not reasons) and (not needs_permission)
        return {
            "approved": bool(approved),
            "needs_permission_confirm": bool(needs_permission),
            "reasons": reasons,
            "high_risk": bool(high_risk),
        }

    @staticmethod
    def _build_selfdrive_steps(goal: str) -> List[Dict[str, str]]:
        g = str(goal or "").strip()
        t = g.lower()
        steps: List[Dict[str, str]] = [
            {"name": "拆分任务范围", "action": "plan", "task": f"明确目标与约束：{g or '当前任务'}"},
        ]
        if any(k in t for k in ["爬虫", "搜索", "调研", "research", "crawl", "collect"]):
            steps.append({"name": "收集信息", "action": "research", "task": "收集可执行方案与关键资料"})
        if any(k in t for k in ["代码", "bug", "修复", "python", "开发", "实现", "feature", "fix"]):
            steps.append({"name": "执行修改", "action": "execute", "task": "实现改动并控制范围"})
            steps.append({"name": "快速校验", "action": "verify", "task": "做最小验证并记录结果"})
        else:
            steps.append({"name": "执行方案", "action": "execute", "task": g or "推进当前目标"})
        steps.append({"name": "结果总结", "action": "summary", "task": "输出进展、阻塞和下一步"})
        return steps[:5]

    @staticmethod
    def _looks_like_generic_progress_intent(user_text: str) -> bool:
        q = str(user_text or "").strip().lower()
        if not q:
            return False
        return bool(
            re.search(
                r"(进展|进度|状态|还在|怎么样|如何|现在在做什么|啥时候做完|什么时候做完|多久做完|还要多久|何时完成|progress|status|what.*doing|when.*done|how long)",
                q,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _looks_like_need_todo_intent(user_text: str) -> bool:
        q = str(user_text or "").strip().lower()
        if not q:
            return False
        return bool(
            re.search(
                r"(需要做什么|你需要做什么|下一步做什么|待办|todo|next\s+step|what\s+next)",
                q,
                re.IGNORECASE,
            )
        )

    def _build_selfdrive_start_args_from_text(
        self,
        source_text: str,
        *,
        fallback_goal: str = "持续推进当前任务",
        confirmed: bool = False,
    ) -> Dict[str, Any]:
        raw = str(source_text or "").strip()
        extracted_goal = self._extract_selfdrive_goal_text(raw)
        normalized_goal = self._normalize_selfdrive_goal(extracted_goal) or self._normalize_selfdrive_goal(raw) or fallback_goal
        args: Dict[str, Any] = {"goal": normalized_goal}
        duration = self._extract_selfdrive_duration_minutes(raw)
        if duration is not None:
            args["budget"] = int(duration)
        if confirmed:
            args["confirmed"] = True
        return args

    @staticmethod
    def _looks_like_selfdrive_delegation_intent(user_text: str) -> bool:
        q = str(user_text or "").strip().lower()
        if not q:
            return False
        if re.search(r"(什么是|原理|教程|介绍|了解|what is|principle|tutorial)", q, re.IGNORECASE):
            return False
        has_actor = bool(
            re.search(r"(请你|你|帮我|需要你|给自己|自己|自我|接下来|代理|agent|assistant)", q, re.IGNORECASE)
        )
        has_verb = bool(
            re.search(
                r"(做一个|做个|实现|构建|开发|优化|推进|执行|搜索|采集|排查|修复|测试|试跑|修改|改|替换|重写|翻译|注释|run test|build|implement|optimize|run|execute)",
                q,
                re.IGNORECASE,
            )
        )
        return has_actor and has_verb

    def _selfdrive_plan_text(self, goal: str) -> str:
        normalized = self._normalize_selfdrive_goal(goal) or "持续推进当前任务"
        steps = self._build_selfdrive_steps(normalized)
        summary = " | ".join(f"{idx + 1}.{s.get('name', '')}" for idx, s in enumerate(steps))
        return f"[selfdrive:plan] goal={normalized}; steps={summary}"

    @staticmethod
    def _append_jsonl(path: str, payload: Dict[str, Any]) -> None:
        p = str(path or "").strip()
        if not p:
            return
        try:
            d = os.path.dirname(os.path.abspath(p))
            if d and (not os.path.isdir(d)):
                os.makedirs(d, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _record_selfdrive_heartbeat(self, event: str, extra: Optional[Dict[str, Any]] = None) -> None:
        ks = self._selfdrive_kernel_state
        payload: Dict[str, Any] = {
            "ts": time.time(),
            "event": str(event or "tick"),
            "active": int(bool(self._selfdrive_active)),
            "mode": str(self._selfdrive_mode or ""),
            "goal": str(self._selfdrive_goal or ""),
            "steps_done": int(self.mon.get("selfdrive_steps_done", 0) or 0),
            "steps_total": int(self.mon.get("selfdrive_total_steps", 0) or 0),
            "next_ts": float(self._selfdrive_next_ts or 0.0),
            "kernel_status": str(getattr(ks, "status", "") or ""),
            "kernel_budget_used": int(getattr(ks, "budget_steps_used", 0) or 0) if ks is not None else 0,
            "kernel_budget_max": int(getattr(ks, "budget_steps_max", 0) or 0) if ks is not None else 0,
            "brief_path": str(self._selfdrive_brief_path or ""),
            "last_action": str(self.mon.get("selfdrive_last_action") or ""),
            "last_error": str(self.mon.get("selfdrive_last_error") or self.mon.get("semantic_trigger_last_error") or ""),
        }
        if isinstance(extra, dict):
            payload.update(extra)
        self._append_jsonl(self._selfdrive_heartbeat_log_path, payload)

    @staticmethod
    def _extract_json_dict_from_text(text: str) -> Optional[Dict[str, Any]]:
        t = str(text or "").strip()
        if not t:
            return None
        try:
            obj = json.loads(t)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
        l = t.find("{")
        r = t.rfind("}")
        if l >= 0 and r > l:
            try:
                obj = json.loads(t[l : r + 1])
                if isinstance(obj, dict):
                    return obj
            except Exception:
                return None
        return None

    def _build_selfdrive_capability_snapshot(self) -> Dict[str, Any]:
        worker = getattr(self._selfdrive_kernel, "worker", None)
        tool_items: List[Dict[str, Any]] = []
        if worker is not None and callable(getattr(worker, "list_tools", None)):
            try:
                for name in list(worker.list_tools() or []):
                    tname = str(name or "").strip()
                    if not tname:
                        continue
                    schema = {}
                    if callable(getattr(worker, "get_tool_schema", None)):
                        try:
                            schema = dict(worker.get_tool_schema(tname) or {})
                        except Exception:
                            schema = {}
                    health = {}
                    if callable(getattr(worker, "get_tool_health", None)):
                        try:
                            health = dict(worker.get_tool_health(tname) or {})
                        except Exception:
                            health = {}
                    tool_items.append({"tool_name": tname, "schema": schema, "health": health})
            except Exception:
                pass
        health: Dict[str, Any] = {}
        stats = dict(getattr(worker, "_expert_stats", {}) or {}) if worker is not None else {}
        for expert in ("planner", "codex"):
            st = dict(stats.get(expert) or {})
            calls = max(1, int(st.get("calls", 0) or 0))
            fail = int(st.get("fail", 0) or 0)
            health[expert] = {
                "calls": int(st.get("calls", 0) or 0),
                "success_rate": round(float(int(st.get("ok", 0) or 0)) / float(calls), 4),
                "error_rate": round(float(fail) / float(calls), 4),
                "avg_latency_ms": round(float(st.get("lat_ms_sum", 0.0) or 0.0) / float(calls), 2),
            }
        ks = self._selfdrive_kernel_state
        completed: List[str] = []
        blocked: List[str] = []
        if ks is not None:
            for t in list(getattr(ks, "tasks", []) or []):
                tid = str(getattr(t, "task_id", "") or "")
                status = str(getattr(t, "status", "") or "").lower()
                label = f"{tid}:{status}" if tid else status
                if status in {"done", "skipped"}:
                    completed.append(label)
                if status in {"blocked", "failed", "failed_fatal", "waiting_user"}:
                    blocked.append(label)
        fail_stats: Dict[str, int] = {}
        if ks is not None:
            for evt in list(getattr(ks, "trace", []) or [])[-40:]:
                if str((evt or {}).get("event") or "") != "failure_routed":
                    continue
                cat = str((evt or {}).get("category") or "unknown")
                fail_stats[cat] = int(fail_stats.get(cat, 0) or 0) + 1
        return {
            "available_tools": tool_items,
            "tool_health": health,
            "autonomy_level": str(self._selfdrive_autonomy_level or "L1"),
            "budget": {
                "used": int(getattr(ks, "budget_steps_used", 0) or 0) if ks is not None else 0,
                "max": int(getattr(ks, "budget_steps_max", 0) or 0) if ks is not None else int(
                    self.mon.get("selfdrive_budget_max", 0) or 0
                ),
            },
            "completed_nodes": completed[-20:],
            "blocked_nodes": blocked[-20:],
            "failure_router_stats": fail_stats,
        }

    def _glm5_plan_for_selfdrive(self, *, goal: str, context: Dict[str, Any]) -> Dict[str, Any]:
        t0 = time.time()
        q_goal = str(goal or "").strip()
        if not q_goal:
            q_goal = str(self._selfdrive_goal or "持续推进当前任务")
        now = float(time.time())
        backoff_sec = max(0.0, float(self._glm_plan_failure_backoff_sec))
        if backoff_sec > 0 and self._glm_plan_last_fail_ts > 0:
            if (now - float(self._glm_plan_last_fail_ts)) < backoff_sec:
                self._append_jsonl(
                    self._selfdrive_api_audit_log_path,
                    {
                        "ts": time.time(),
                        "api": "glm_plan",
                        "ok": 0,
                        "latency_ms": int((time.time() - t0) * 1000),
                        "goal": q_goal[:200],
                        "fallback": 1,
                        "skipped_by_backoff": 1,
                        "backoff_sec": backoff_sec,
                        "last_error": str(self._glm_plan_last_error or ""),
                    },
                )
                return {
                    "plan_summary": f"fallback plan (planner_backoff) for goal: {q_goal}",
                    "generated_subgoals": [
                        {
                            "subgoal_id": "sg_fallback_01",
                            "intent": f"Implement next minimal milestone for: {q_goal}",
                            "executor_type": "code_task",
                            "tool_name": "code_task",
                            "inputs": {
                                "instruction": f"Complete one concrete coding step for goal: {q_goal}",
                            },
                            "dependencies": [],
                            "preconditions": [{"op": "tool_available", "args": {"tool": "code_task"}}],
                            "success_criteria": [{"op": "predicate_ref", "args": {"name": "worker_ok"}}],
                            "retry_policy": {"max_attempts": 2, "backoff": "exponential", "base_delay_ms": 300},
                            "fallback": {},
                            "budget": {"max_steps": 2},
                            "risk": "low",
                        }
                    ],
                    "ask_user": "",
                }
        cap_snapshot = self._build_selfdrive_capability_snapshot()
        budget = dict(cap_snapshot.get("budget") or {})
        remaining_budget = max(0, int(budget.get("max", 0) or 0) - int(budget.get("used", 0) or 0))
        user_payload = {
            "goal": q_goal,
            "context": dict(context or {}),
            "task_brief_path": str(self._selfdrive_brief_path or ""),
            "task_brief_markdown": str(self._selfdrive_brief_text or ""),
            "capability_snapshot": cap_snapshot,
            "autonomy_level": str(self._selfdrive_autonomy_level or "L1"),
            "remaining_budget_steps": int(remaining_budget),
            "completed_nodes": list(cap_snapshot.get("completed_nodes") or []),
            "blocked_nodes": list(cap_snapshot.get("blocked_nodes") or []),
            "failure_router_stats": dict(cap_snapshot.get("failure_router_stats") or {}),
            "schema_hint": {
                "generated_subgoals": [
                    {
                        "subgoal_id": "sg_01",
                        "intent": "string",
                        "executor_type": "code_task",
                        "tool_name": "code_task",
                        "inputs": {"instruction": "string"},
                        "dependencies": [],
                        "preconditions": [{"op": "tool_available", "args": {"tool": "code_task"}}],
                        "success_criteria": [{"op": "predicate_ref", "args": {"name": "worker_ok"}}],
                        "retry_policy": {"max_attempts": 2, "backoff": "exponential", "base_delay_ms": 300},
                        "fallback": {},
                        "budget": {"max_steps": 2},
                        "risk": "low",
                    }
                ],
                "ask_user": "",
                "plan_summary": "string",
            },
            "rules": [
                "Do not ask user unless strictly blocked.",
                "Return executable subgoal DAG only; each node must include machine-checkable success_criteria.",
                "Prefer one concrete code task each planning turn and respect remaining budget.",
                "Output strict JSON object only.",
            ],
        }
        system = (
            "You are GLM planner for autonomous coding loop. "
            "Return strict JSON: generated_subgoals(list), ask_user(string), plan_summary(string). "
            "Each subgoal must include: subgoal_id, intent, executor_type, tool_name, inputs, dependencies, "
            "preconditions, success_criteria, retry_policy, fallback, budget, risk. "
            "Use task tool_name from available_tools only."
        )
        glm_error = ""
        try:
            client = GLMClient()
            raw = client.chat(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
                temperature=0.2,
                max_tokens=700,
            )
            obj = self._extract_json_dict_from_text(raw)
            if isinstance(obj, dict):
                self._append_jsonl(
                    self._selfdrive_api_audit_log_path,
                    {
                        "ts": time.time(),
                        "api": "glm_plan",
                        "ok": 1,
                        "latency_ms": int((time.time() - t0) * 1000),
                        "goal": q_goal[:200],
                        "has_subgoals": int(bool(obj.get("generated_subgoals") or obj.get("subgoals") or obj.get("tasks"))),
                    },
                )
                self._glm_plan_last_fail_ts = 0.0
                self._glm_plan_last_error = ""
                self.mon["glm_plan_last_error"] = ""
                self.mon["glm_plan_last_fail_ts"] = 0.0
                return obj
            glm_error = "parse_error:planner_json_invalid"
        except Exception as e:
            glm_error = f"execution_error:{type(e).__name__}:{e}"
        self._glm_plan_last_fail_ts = float(time.time())
        self._glm_plan_last_error = str(glm_error or "")
        self.mon["glm_plan_last_error"] = str(glm_error or "")
        self.mon["glm_plan_last_fail_ts"] = float(self._glm_plan_last_fail_ts)
        self._append_jsonl(
            self._selfdrive_api_audit_log_path,
            {
                "ts": time.time(),
                "api": "glm_plan",
                "ok": 0,
                "latency_ms": int((time.time() - t0) * 1000),
                "goal": q_goal[:200],
                "fallback": 1,
                "error": str(glm_error or ""),
            },
        )
        return {
            "plan_summary": f"fallback plan for goal: {q_goal}",
            "generated_subgoals": [
                {
                    "subgoal_id": "sg_fallback_01",
                    "intent": f"Implement next minimal milestone for: {q_goal}",
                    "executor_type": "code_task",
                    "tool_name": "code_task",
                    "inputs": {
                        "instruction": f"Complete one concrete coding step for goal: {q_goal}",
                    },
                    "dependencies": [],
                    "preconditions": [{"op": "tool_available", "args": {"tool": "code_task"}}],
                    "success_criteria": [{"op": "predicate_ref", "args": {"name": "worker_ok"}}],
                    "retry_policy": {"max_attempts": 2, "backoff": "exponential", "base_delay_ms": 300},
                    "fallback": {},
                    "budget": {"max_steps": 2},
                    "risk": "low",
                }
            ],
            "ask_user": "",
        }

    @staticmethod
    def _is_under_workspace(path_text: str) -> bool:
        p = str(path_text or "").strip()
        if not p:
            return True
        ws = os.path.abspath(os.getcwd())
        try:
            if os.path.isabs(p):
                abs_p = os.path.abspath(p)
            else:
                abs_p = os.path.abspath(os.path.join(ws, p))
            common = os.path.commonpath([ws, abs_p])
            return common == ws
        except Exception:
            return False

    @staticmethod
    def _classify_codex_action(payload: Dict[str, Any]) -> str:
        if str(payload.get("target_path") or payload.get("file_path") or "").strip():
            return "file_modify"
        if str(payload.get("command") or payload.get("shell_command") or "").strip():
            return "command_exec"
        return "code_generate"

    def _selfdrive_codex_dry_run_check(self, task: KernelTask) -> Dict[str, Any]:
        payload = dict(task.input_payload or {})
        action_type = self._classify_codex_action(payload)
        instruction = str(payload.get("instruction") or task.description or "").strip()

        if action_type == "file_modify":
            target_path = str(payload.get("target_path") or payload.get("file_path") or "").strip()
            if not target_path:
                return {"ok": False, "error": "execution_error:file_modify_missing_target_path", "action_type": action_type}
            if not self._is_under_workspace(target_path):
                return {"ok": False, "error": "permission_denied:target_path_outside_workspace", "action_type": action_type}
            abs_target = (
                os.path.abspath(target_path)
                if os.path.isabs(target_path)
                else os.path.abspath(os.path.join(os.getcwd(), target_path))
            )
            if (not os.path.exists(abs_target)) and (not bool(payload.get("allow_create", False))):
                return {"ok": False, "error": "environment_missing:target_file_not_found", "action_type": action_type}

        if action_type == "command_exec":
            cmd = str(payload.get("command") or payload.get("shell_command") or "").strip()
            allow_pat_raw = str(
                os.getenv(
                    "SELFDRIVE_CODEX_COMMAND_ALLOW_PATTERNS",
                    r"^(py(\.exe)?\s+-m\s+pytest|python(\.exe)?\s+-m\s+pytest|pytest|rg(\.exe)?\b|Get-ChildItem\b|git\s+status\b)",
                )
                or ""
            ).strip()
            allowed = False
            try:
                allowed = bool(re.search(allow_pat_raw, cmd, flags=re.IGNORECASE))
            except Exception:
                allowed = False
            if not allowed:
                return {"ok": False, "error": "permission_denied:command_not_whitelisted", "action_type": action_type}

        # Code generation class: lightweight syntax pre-check for inline python snippets.
        if action_type == "code_generate":
            snippet = str(payload.get("code") or payload.get("python_code") or "").strip()
            if (not snippet) and instruction:
                m = re.search(r"```python\s*(.*?)```", instruction, flags=re.IGNORECASE | re.DOTALL)
                if m:
                    snippet = str(m.group(1) or "").strip()
            if snippet:
                try:
                    compile(snippet, "<selfdrive_codex_dry_run>", "exec")
                except Exception as e:
                    return {"ok": False, "error": f"parse_error:python_syntax_invalid:{type(e).__name__}", "action_type": action_type}

        return {"ok": True, "error": "", "action_type": action_type}

    @staticmethod
    def _validate_codex_delegate_output_schema(obj: Dict[str, Any]) -> List[str]:
        issues: List[str] = []
        if not isinstance(obj, dict):
            return ["delegate_response_not_object"]
        if "ok" in obj and (not isinstance(obj.get("ok"), bool)):
            issues.append("invalid_ok_type")
        out = obj.get("output")
        if out is not None and (not isinstance(out, dict)):
            issues.append("invalid_output_type")
        if out is None:
            issues.append("missing_output")
        else:
            if "summary" in out and (not isinstance(out.get("summary"), str)):
                issues.append("invalid_output_summary_type")
            if "changed_files" in out and (not isinstance(out.get("changed_files"), list)):
                issues.append("invalid_output_changed_files_type")
        return issues

    @staticmethod
    def _is_hard_codex_error(error_text: str) -> bool:
        t = str(error_text or "").strip().lower()
        if not t:
            return False
        hard_prefixes = (
            "auth:",
            "environment_missing:",
            "endpoint_mismatch:",
            "permission_denied:",
        )
        return any(t.startswith(prefix) for prefix in hard_prefixes)

    def _select_codex_execution_lane(self, task: KernelTask) -> Tuple[str, str]:
        forced = str(self._codex_lane_mode or "auto").strip().lower()
        if forced in {"fast", "deep"}:
            return forced, f"forced:{forced}"
        payload = dict(task.input_payload or {})
        desc = str(task.description or "").strip()
        task_kind = str(task.kind or "").strip().lower()
        patch_ops = payload.get("patch_ops")
        patch_count = len(patch_ops) if isinstance(patch_ops, list) else 0
        files_hint = payload.get("files") or payload.get("changed_files")
        files_count = len(files_hint) if isinstance(files_hint, list) else 0
        verify_cmd = str(payload.get("verify_command") or "").strip()
        deep_keywords = (
            "refactor",
            "architecture",
            "multi-file",
            "cross-file",
            "migration",
            "framework",
            "pipeline",
            "rollback",
        )
        lowered = desc.lower()
        if any(k in lowered for k in deep_keywords):
            return "deep", "keyword:complex_task"
        if task_kind and task_kind not in {"code_task", "code"}:
            return "deep", f"task_kind:{task_kind}"
        if verify_cmd:
            return "deep", "verify_command_present"
        if patch_count > int(self._codex_fast_task_max_patch_ops):
            return "deep", f"patch_ops>{int(self._codex_fast_task_max_patch_ops)}"
        if files_count > int(self._codex_fast_task_max_files):
            return "deep", f"files>{int(self._codex_fast_task_max_files)}"
        if len(desc) > int(self._codex_fast_task_max_desc_chars):
            return "deep", f"desc>{int(self._codex_fast_task_max_desc_chars)}"
        return "fast", "heuristic:small_patch"

    def _codex_execute_for_selfdrive(self, *, task: KernelTask) -> Dict[str, Any]:
        t0 = time.time()
        lane, lane_reason = self._select_codex_execution_lane(task)
        self.mon["codex_lane_last"] = str(lane)
        self.mon["codex_delegate_calls"] = int(self.mon.get("codex_delegate_calls", 0) or 0) + 1
        startup_err = str(self._codex_delegate_last_error or "")
        if (
            bool(self._codex_startup_healthchecked)
            and (not bool(self._codex_delegate_available))
            and self._is_hard_codex_error(startup_err)
        ):
            err = str(self._codex_delegate_last_error or "execution_error:codex_startup_check_failed")
            self.mon["codex_delegate_fail"] = int(self.mon.get("codex_delegate_fail", 0) or 0) + 1
            self.mon["codex_delegate_last_note"] = "blocked_by_startup_check"
            self._append_jsonl(
                self._selfdrive_api_audit_log_path,
                {
                    "ts": time.time(),
                    "api": "codex_exec",
                    "ok": 0,
                    "latency_ms": int((time.time() - t0) * 1000),
                    "task_id": str(task.task_id),
                    "task_kind": str(task.kind),
                    "lane": str(lane),
                    "lane_reason": str(lane_reason),
                    "fallback": 0,
                    "blocked_by": "startup_check",
                    "delegate_error": err,
                },
            )
            return {
                "ok": False,
                "output": {
                    "summary": "codex execution blocked by startup healthcheck",
                    "changed_files": [],
                    "improvement_items": [],
                    "open_questions": [],
                },
                "error": err,
                "wait_user": False,
                "artifacts": [],
            }
        dry_run = self._selfdrive_codex_dry_run_check(task)
        if not bool(dry_run.get("ok")):
            err = str(dry_run.get("error") or "execution_error:dry_run_failed")
            self.mon["codex_delegate_fail"] = int(self.mon.get("codex_delegate_fail", 0) or 0) + 1
            self.mon["codex_delegate_last_note"] = "blocked_by_dry_run"
            self._append_jsonl(
                self._selfdrive_api_audit_log_path,
                {
                    "ts": time.time(),
                    "api": "codex_exec",
                    "ok": 0,
                    "latency_ms": int((time.time() - t0) * 1000),
                    "task_id": str(task.task_id),
                    "task_kind": str(task.kind),
                    "lane": str(lane),
                    "lane_reason": str(lane_reason),
                    "fallback": 0,
                    "blocked_by": "dry_run",
                    "error": err,
                },
            )
            return {
                "ok": False,
                "output": {
                    "summary": "codex execution blocked by dry-run checks",
                    "changed_files": [],
                    "improvement_items": [],
                    "open_questions": [],
                    "dry_run_action_type": str(dry_run.get("action_type") or ""),
                },
                "error": err,
                "wait_user": False,
                "artifacts": [],
            }
        task_payload = {
            "task_id": str(task.task_id),
            "kind": str(task.kind),
            "description": str(task.description),
            "input_payload": dict(task.input_payload or {}),
            "goal": str(self._selfdrive_goal or ""),
        }
        system = (
            "You are codex executor for autonomous loop. Return strict JSON with keys: "
            "ok(boolean), output(object), error(string), wait_user(boolean), artifacts(list). "
            "output should include summary, changed_files(list), improvement_items(list), open_questions(list)."
        )
        try:
            obj = self.codex_delegate.try_chat_json(
                system=system,
                user_payload=task_payload,
                temperature=0.2,
                max_tokens=800,
                with_error=True,
                execution_lane=str(lane),
            )
            if isinstance(obj, dict):
                delegate_error = str(obj.get("_delegate_error") or "").strip()
                if delegate_error:
                    self._codex_delegate_available = False
                    self._codex_delegate_last_error = str(delegate_error or "")
                    self.mon["codex_delegate_available"] = 0
                    self.mon["codex_delegate_last_error"] = str(delegate_error or "")
                    self.mon["codex_delegate_fail"] = int(self.mon.get("codex_delegate_fail", 0) or 0) + 1
                    self.mon["codex_delegate_last_note"] = "delegate_error"
                    self._append_jsonl(
                        self._selfdrive_api_audit_log_path,
                        {
                            "ts": time.time(),
                            "api": "codex_exec",
                            "ok": 0,
                            "latency_ms": int((time.time() - t0) * 1000),
                            "task_id": str(task.task_id),
                            "task_kind": str(task.kind),
                            "lane": str(lane),
                            "lane_reason": str(lane_reason),
                            "fallback": 0,
                            "delegate_error": delegate_error,
                        },
                    )
                    return {
                        "ok": False,
                        "output": {
                            "summary": "codex delegate returned normalized error",
                            "changed_files": [],
                            "improvement_items": [],
                            "open_questions": [],
                        },
                        "error": delegate_error,
                        "wait_user": False,
                        "artifacts": [],
                    }
                schema_issues = self._validate_codex_delegate_output_schema(obj)
                if schema_issues:
                    self.mon["codex_delegate_fail"] = int(self.mon.get("codex_delegate_fail", 0) or 0) + 1
                    self.mon["codex_delegate_last_note"] = "schema_invalid"
                    self._append_jsonl(
                        self._selfdrive_api_audit_log_path,
                        {
                            "ts": time.time(),
                            "api": "codex_exec",
                            "ok": 0,
                            "latency_ms": int((time.time() - t0) * 1000),
                            "task_id": str(task.task_id),
                            "task_kind": str(task.kind),
                            "lane": str(lane),
                            "lane_reason": str(lane_reason),
                            "fallback": 0,
                            "schema_issues": schema_issues,
                        },
                    )
                    return {
                        "ok": False,
                        "output": {
                            "summary": "codex delegate output schema invalid",
                            "changed_files": [],
                            "improvement_items": [],
                            "open_questions": [],
                            "schema_issues": schema_issues,
                        },
                        "error": "parse_error:delegate_output_schema_invalid",
                        "wait_user": False,
                        "artifacts": [],
                    }
                output = dict(obj.get("output") or {})
                contract = dict(output.get("execution_contract") or {})
                if not contract:
                    contract = {
                        "proposed_action": str(task_payload.get("description") or ""),
                        "assumed_preconditions": ["workspace_access", "tool_available:code_task"],
                        "expected_artifacts": [f"codex::{str(task.task_id)}::result"],
                        "self_check_plan": ["validate changed_files and summary", "run minimal test when available"],
                        "rollback_hint": "revert patch via VCS and retry with narrower scope",
                    }
                out = {
                    "ok": bool(obj.get("ok", True)),
                    "output": {
                        "summary": str(output.get("summary") or ""),
                        "changed_files": [str(x) for x in (output.get("changed_files") or []) if str(x).strip()],
                        "improvement_items": [str(x) for x in (output.get("improvement_items") or []) if str(x).strip()],
                        "open_questions": [str(x) for x in (output.get("open_questions") or []) if str(x).strip()],
                        "execution_contract": contract,
                    },
                    "error": str(obj.get("error") or ""),
                    "wait_user": bool(obj.get("wait_user") or False),
                    "artifacts": [str(x) for x in (obj.get("artifacts") or []) if str(x).strip()],
                }
                patch_ops = output.get("patch_ops")
                if not isinstance(patch_ops, list):
                    patch_ops = list((task_payload.get("input_payload") or {}).get("patch_ops") or [])
                verify_cmd = str(
                    output.get("verify_command")
                    or (task_payload.get("input_payload") or {}).get("verify_command")
                    or ""
                ).strip()
                txn = PatchExecutionTransaction(workspace_root=os.getcwd())
                txn_result = txn.run(
                    task_id=str(task.task_id),
                    input_payload=dict(task_payload.get("input_payload") or {}),
                    execution_contract=contract,
                    patch_ops=list(patch_ops or []),
                    changed_files_hint=list((out.get("output") or {}).get("changed_files") or []),
                    verify_command=verify_cmd,
                    audit_log_path=self._selfdrive_api_audit_log_path,
                )
                tx_output = dict(out.get("output") or {})
                tx_output["pre_snapshot"] = list(txn_result.get("pre_snapshot") or [])
                tx_output["post_snapshot"] = list(txn_result.get("post_snapshot") or [])
                tx_output["verify_evidence"] = dict(txn_result.get("verify_evidence") or {})
                tx_output["rollback_result"] = dict(txn_result.get("rollback_result") or {})
                tx_output["error_signature"] = str(txn_result.get("error_signature") or "")
                tx_output["audit_log_path"] = str(txn_result.get("audit_log_path") or self._selfdrive_api_audit_log_path)
                tx_changed = [str(x) for x in (txn_result.get("changed_files") or []) if str(x).strip()]
                if tx_changed:
                    tx_output["changed_files"] = tx_changed
                out["output"] = tx_output
                if not bool(txn_result.get("success")):
                    err_sig = str(txn_result.get("error_signature") or "").strip()
                    out["ok"] = False
                    if err_sig:
                        out["error"] = err_sig
                if bool(out.get("ok")):
                    self._codex_delegate_available = True
                    self._codex_delegate_last_error = ""
                    self.mon["codex_delegate_available"] = 1
                    self.mon["codex_delegate_last_error"] = ""
                    self.mon["codex_delegate_ok"] = int(self.mon.get("codex_delegate_ok", 0) or 0) + 1
                    self.mon["codex_delegate_last_note"] = "ok"
                else:
                    self.mon["codex_delegate_fail"] = int(self.mon.get("codex_delegate_fail", 0) or 0) + 1
                    self.mon["codex_delegate_last_note"] = "txn_failed"
                self._append_jsonl(
                    self._selfdrive_api_audit_log_path,
                    {
                        "ts": time.time(),
                        "api": "codex_exec",
                        "ok": int(bool(out.get("ok"))),
                        "latency_ms": int((time.time() - t0) * 1000),
                        "task_id": str(task.task_id),
                        "task_kind": str(task.kind),
                        "lane": str(lane),
                        "lane_reason": str(lane_reason),
                        "changed_files": int(len((out.get("output") or {}).get("changed_files") or [])),
                        "changed_files_list": [str(x) for x in ((out.get("output") or {}).get("changed_files") or [])],
                        "improvement_items": int(len((out.get("output") or {}).get("improvement_items") or [])),
                        "open_questions": int(len((out.get("output") or {}).get("open_questions") or [])),
                        "rollback_result": dict((out.get("output") or {}).get("rollback_result") or {}),
                        "error_signature": str((out.get("output") or {}).get("error_signature") or out.get("error") or ""),
                        "fallback": 0,
                    },
                )
                return out
        except Exception as e:
            err = f"execution_error:{type(e).__name__}:{e}"
            self._codex_delegate_available = False
            self._codex_delegate_last_error = str(err or "")
            self.mon["codex_delegate_available"] = 0
            self.mon["codex_delegate_last_error"] = str(err or "")
            self.mon["codex_delegate_fail"] = int(self.mon.get("codex_delegate_fail", 0) or 0) + 1
            self.mon["codex_delegate_last_note"] = "exception"
            self._append_jsonl(
                self._selfdrive_api_audit_log_path,
                {
                    "ts": time.time(),
                    "api": "codex_exec",
                    "ok": 0,
                    "latency_ms": int((time.time() - t0) * 1000),
                    "task_id": str(task.task_id),
                    "task_kind": str(task.kind),
                    "lane": str(lane),
                    "lane_reason": str(lane_reason),
                    "fallback": 0,
                    "delegate_error": err,
                },
            )
            return {
                "ok": False,
                "output": {
                    "summary": "codex delegate exception",
                    "changed_files": [],
                    "improvement_items": [],
                    "open_questions": [],
                },
                "error": err,
                "wait_user": False,
                "artifacts": [],
            }
        self._append_jsonl(
            self._selfdrive_api_audit_log_path,
            {
                "ts": time.time(),
                "api": "codex_exec",
                "ok": 0,
                "latency_ms": int((time.time() - t0) * 1000),
                "task_id": str(task.task_id),
                "task_kind": str(task.kind),
                "lane": str(lane),
                "lane_reason": str(lane_reason),
                "fallback": 1,
            },
        )
        self.mon["codex_delegate_fail"] = int(self.mon.get("codex_delegate_fail", 0) or 0) + 1
        self.mon["codex_delegate_last_note"] = "unexpected_fallback"
        return {
            "ok": False,
            "output": {
                "summary": "codex delegate unavailable",
                "changed_files": [],
                "improvement_items": [],
                "open_questions": [],
            },
            "error": "execution_error:codex_delegate_unavailable",
            "wait_user": False,
            "artifacts": [],
        }

    def _task_run_plan_snapshot(self) -> List[Dict[str, Any]]:
        ks = self._selfdrive_kernel_state
        if ks is None:
            return []
        out: List[Dict[str, Any]] = []
        for t in list(getattr(ks, "tasks", []) or []):
            out.append({
                "task_id": str(getattr(t, "task_id", "") or ""),
                "kind": str(getattr(t, "kind", "") or ""),
                "description": str(getattr(t, "description", "") or ""),
                "status": str(getattr(t, "status", "") or ""),
            })
        return out

    def _task_run_start(self, goal: str) -> None:
        try:
            self._task_run_current = self._task_run_recorder.start_run(goal=str(goal or ""), plan=self._task_run_plan_snapshot())
        except Exception:
            self._task_run_current = None

    @staticmethod
    def _task_run_redact_text(value: str) -> str:
        txt = str(value or "")
        patterns = [
            r"(?i)(api[_-]?key\s*[=:]\s*)([^\s,;]+)",
            r"(?i)(token\s*[=:]\s*)([^\s,;]+)",
            r"(?i)(secret\s*[=:]\s*)([^\s,;]+)",
            r"(?i)(password\s*[=:]\s*)([^\s,;]+)",
            r"(?i)(bearer\s+)([A-Za-z0-9._-]+)",
        ]
        out = txt
        for ptn in patterns:
            out = re.sub(ptn, r"\1[REDACTED]", out)
        return out

    def _task_run_preview(self, value: Any, max_len: int = 260) -> str:
        try:
            if isinstance(value, str):
                raw = value
            else:
                raw = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            raw = str(value)
        clean = self._task_run_redact_text(raw)
        clean = re.sub(r"\s+", " ", clean).strip()
        if len(clean) <= int(max_len):
            return clean
        return clean[: max(0, int(max_len) - 3)] + "..."

    def _task_run_finalize(self, status: str) -> None:
        cur = self._task_run_current
        if cur is None:
            return
        ks = self._selfdrive_kernel_state
        summary_parts: List[str] = []
        if ks is not None:
            total = len(list(getattr(ks, "tasks", []) or []))
            done = 0
            for t in list(getattr(ks, "tasks", []) or []):
                if str(getattr(t, "status", "") or "").lower() in {"done", "skipped"}:
                    done += 1
            summary_parts.append(f"tasks_done={done}/{total}")
            last_error = str(getattr(ks, "last_error", "") or "")
            if last_error:
                summary_parts.append(f"last_error={self._task_run_preview(last_error, max_len=120)}")
        summary = "; ".join(summary_parts)
        try:
            self._task_run_recorder.finalize(cur, status=str(status or "done"), summary=summary)
        except Exception:
            pass

    def _task_run_record_step(self, *, ts_start: float, ts_end: float, trace_events: List[Dict[str, Any]]) -> None:
        cur = self._task_run_current
        if cur is None:
            return
        extra_trace = list(self._actuation_trace_buffer or [])
        self._actuation_trace_buffer = []
        merged_trace: List[Dict[str, Any]] = []
        for item in list(trace_events or []):
            if isinstance(item, dict):
                merged_trace.append(dict(item))
        for item in extra_trace:
            if isinstance(item, dict):
                merged_trace.append(dict(item))
        tool_calls: List[Dict[str, Any]] = []
        retry_actions: List[Dict[str, Any]] = []
        fallback_actions: List[Dict[str, Any]] = []
        output: Dict[str, Any] = {}
        action_results: List[Dict[str, Any]] = []
        error = ""
        status = "ok"
        input_payload: Dict[str, Any] = {}
        active_id = ""
        ks = self._selfdrive_kernel_state
        if ks is not None:
            active_id = str(getattr(ks, "active_task_id", "") or "")
            for t in list(getattr(ks, "tasks", []) or []):
                if str(getattr(t, "task_id", "") or "") == active_id:
                    input_payload = dict(getattr(t, "input_payload", {}) or {})
                    break

        def _append_log(*, component: str, action: str, inp: Any, out: Any, success: bool, error: str = "") -> None:
            step = TaskRunStep(
                step_id=f"s{len(cur.steps)+1:04d}",
                ts_start=float(ts_start),
                ts_end=float(ts_end),
                duration_ms=int(max(0.0, (float(ts_end)-float(ts_start))) * 1000.0),
                component=str(component or ""),
                action=str(action or ""),
                input_preview=self._task_run_preview(inp),
                output_preview=self._task_run_preview(out),
                success=bool(success),
                input_payload=input_payload,
                output=out if isinstance(out, dict) else {"preview": self._task_run_preview(out)},
                error=self._task_run_preview(error, max_len=200) if error else "",
                status="ok" if success else "error",
            )
            try:
                self._task_run_recorder.append_step_log(cur, step)
            except Exception:
                pass

        for evt in merged_trace:
            if not isinstance(evt, dict):
                continue
            ev = str(evt.get("event") or "")
            if ev == "task_selected":
                _append_log(
                    component="planner",
                    action="task_selected",
                    inp={"task_id": evt.get("task_id"), "kind": evt.get("kind")},
                    out="selected",
                    success=True,
                )
            elif ev == "tool_invocation_record":
                _append_log(
                    component="tool_router",
                    action="tool_execute",
                    inp={"tool_name": evt.get("tool_name"), "input_summary": evt.get("input_summary")},
                    out={"duration_ms": evt.get("duration_ms"), "error_signature": evt.get("error_signature")},
                    success=not bool(evt.get("error_signature")),
                    error=str(evt.get("error_signature") or ""),
                )
            elif ev == "worker_result":
                output = {
                    "task_id": str(evt.get("task_id") or ""),
                    "ok": int(evt.get("ok") or 0),
                    "wait_user": int(evt.get("wait_user") or 0),
                    "selected_expert": str(evt.get("selected_expert") or ""),
                }
                error = str(evt.get("error") or "")
            elif ev == "action_execution_result":
                action_results.append(
                    {
                        "action_id": str(evt.get("action_id") or ""),
                        "channel": str(evt.get("channel") or ""),
                        "status": str(evt.get("status") or ""),
                        "success": bool(evt.get("success")),
                        "retry_reason": str(evt.get("retry_reason") or ""),
                    }
                )
            elif ev == "failure_routed" and not error:
                error = str(evt.get("reason") or "")
            elif ev in {"retry_scheduled", "retry_exhausted"}:
                retry_actions.append(dict(evt))
            elif ev == "failure_fallback":
                fallback_actions.append(dict(evt))
        if error:
            status = "error"
        if retry_actions:
            output["retry_actions"] = retry_actions
        if fallback_actions:
            output["fallback_actions"] = fallback_actions
        if action_results:
            output["action_results"] = [dict(x) for x in action_results]
        step = TaskRunStep(
            step_id=f"s{len(cur.steps)+1:04d}",
            ts_start=float(ts_start),
            ts_end=float(ts_end),
            duration_ms=int(max(0.0, (float(ts_end)-float(ts_start))) * 1000.0),
            input_payload=input_payload,
            tool_calls=tool_calls,
            trace_events=[dict(x) for x in merged_trace if isinstance(x, dict)],
            output=output,
            action_results=[dict(x) for x in action_results],
            error=error,
            status=status,
        )
        try:
            self._task_run_recorder.append_step(cur, step)
        except Exception:
            pass

    def _build_selfdrive_kernel_state(self, goal: str, duration_minutes: Optional[int]) -> KernelAgentState:
        normalized_goal = self._normalize_selfdrive_goal(goal) or "持续推进当前任务"
        default_budget = self._env_int("SELFDRIVE_KERNEL_MAX_STEPS", 40)
        budget_max = max(4, int(default_budget))
        if self._selfdrive_budget_override_steps is not None:
            budget_max = max(4, min(400, int(self._selfdrive_budget_override_steps)))
        if duration_minutes is not None:
            budget_max = max(4, min(400, int(duration_minutes)))
        return KernelAgentState(
            goal=normalized_goal,
            tasks=[],
            status="running",
            budget_steps_used=0,
            budget_steps_max=budget_max,
        )

    def _sync_selfdrive_legacy_from_kernel(self) -> None:
        ks = self._selfdrive_kernel_state
        if ks is None:
            self._selfdrive_active = False
            self._selfdrive_goal = ""
            self._selfdrive_steps = []
            self._selfdrive_step_index = 0
            return
        self._selfdrive_goal = str(ks.goal or "")
        self._selfdrive_active = str(ks.status or "") == "running"
        self._selfdrive_step_index = int(ks.budget_steps_used or 0)
        self._selfdrive_steps = [
            {"name": str(t.description or t.kind), "action": str(t.kind or ""), "task": str(t.description or "")}
            for t in list(ks.tasks or [])
        ]
        self.mon["selfdrive_goal"] = self._selfdrive_goal
        self.mon["selfdrive_enabled"] = int(bool(self._selfdrive_active))
        self.mon["selfdrive_total_steps"] = len(self._selfdrive_steps)
        self.mon["selfdrive_autonomy_level"] = str(self._selfdrive_autonomy_level or "L1")
        self.mon["selfdrive_budget_max"] = int(getattr(ks, "budget_steps_max", 0) or 0)
        done_count = 0
        for t in list(ks.tasks or []):
            if str(getattr(t, "status", "")).lower() == "done":
                done_count += 1
        self.mon["selfdrive_steps_done"] = int(done_count)
        self.mon["selfdrive_last_ts"] = float(time.time())
        if str(ks.status or "") == "failed":
            self.mon["selfdrive_errors"] = int(self.mon.get("selfdrive_errors", 0) or 0) + 1
            self.mon["selfdrive_last_action"] = "failed"
            self._task_run_finalize("failed")
        elif str(ks.status or "") == "waiting_user":
            self.mon["selfdrive_last_action"] = "waiting_user"
            self._task_run_finalize("waiting_user")
        elif str(ks.status or "") == "done":
            self.mon["selfdrive_last_action"] = "done"
            self._task_run_finalize("done")

    def _maybe_advance_selfdrive(self, now: Optional[float] = None) -> None:
        ts = float(now if isinstance(now, (int, float)) else time.time())
        with self._selfdrive_lock:
            if self._selfdrive_kernel_state is None:
                return
            if not bool(self._selfdrive_active):
                return
            if (not bool(self._selfdrive_unbounded)) and float(self._selfdrive_deadline_ts or 0.0) > 0.0:
                if ts >= float(self._selfdrive_deadline_ts):
                    self._selfdrive_active = False
                    if self._selfdrive_kernel_state is not None:
                        self._selfdrive_kernel_state.status = "failed"
                        self._selfdrive_kernel_state.last_error = "selfdrive_timeout"
                    self.mon["selfdrive_enabled"] = 0
                    self.mon["selfdrive_last_action"] = "timeout"
                    self.mon["selfdrive_last_ts"] = ts
                    return
            if ts < float(self._selfdrive_next_ts or 0.0):
                return
            trace_len_before = len(list(getattr(self._selfdrive_kernel_state, "trace", []) or []))
            ts_step_start = float(time.time())
            self._selfdrive_kernel.run_step(
                state=self._selfdrive_kernel_state,
                checkpoint_path=self._selfdrive_checkpoint_path,
            )
            ts_step_end = float(time.time())
            trace_new = list(getattr(self._selfdrive_kernel_state, "trace", []) or [])[trace_len_before:]
            self._task_run_record_step(ts_start=ts_step_start, ts_end=ts_step_end, trace_events=trace_new)
            if self._selfdrive_kernel_state is not None:
                if str(self._selfdrive_kernel_state.status or "") == "done" and bool(self._selfdrive_unbounded):
                    self._selfdrive_kernel_state = self._build_selfdrive_kernel_state(
                        goal=self._selfdrive_goal,
                        duration_minutes=None,
                    )
                    self._selfdrive_kernel.run_step(
                        state=self._selfdrive_kernel_state,
                        checkpoint_path=self._selfdrive_checkpoint_path,
                    )
            self._selfdrive_next_ts = ts + float(self._selfdrive_step_gap_sec)
            self._sync_selfdrive_legacy_from_kernel()
            if bool(self._selfdrive_active):
                self.mon["selfdrive_last_action"] = "step"
                self.mon["selfdrive_last_ts"] = ts
            self._record_selfdrive_heartbeat(event="tick")

    def _task_progress_status_text(self) -> str:
        now = float(time.time())
        self._maybe_advance_selfdrive(now)
        with self._selfdrive_lock:
            ks = self._selfdrive_kernel_state
            active = bool(self._selfdrive_active)
            goal = str(self._selfdrive_goal or "")
            mode = str(self._selfdrive_mode or "kernel_v16")
            step_idx = int(self.mon.get("selfdrive_steps_done", 0) or 0)
            total_steps = max(0, int(self.mon.get("selfdrive_total_steps", 0) or 0))
            unbounded = bool(self._selfdrive_unbounded)
            deadline = float(self._selfdrive_deadline_ts or 0.0)
            next_ts = float(self._selfdrive_next_ts or 0.0)
            next_name = ""
            if isinstance(ks, KernelAgentState):
                for t in list(ks.tasks or []):
                    st = str(getattr(t, "status", "")).lower()
                    if st in {"pending", "running", "draft", "ready", "failed_retryable"}:
                        next_name = str(getattr(t, "description", "") or getattr(t, "kind", ""))
                        break
            if (not active) and isinstance(ks, KernelAgentState) and str(ks.status or "") == "waiting_user":
                q = str(ks.waiting_question or "需要用户输入")
                return f"[task] waiting_user=1; goal={goal}; question={q}"
        if not active:
            return "[task] no active tracked task"
        progress = 0
        if total_steps > 0:
            progress = max(0, min(100, int(step_idx * 100 / total_steps)))
        remain = -1
        if (not unbounded) and deadline > 0.0:
            remain = max(0, int(deadline - now))
        next_in = max(0, int(next_ts - now))
        return (
            f"[task] active=1; kind=selfdrive; mode={mode}; goal={goal}; "
            f"progress={progress}%; step={step_idx}/{total_steps}; next={next_name or '-'}; "
            f"next_in_sec={next_in}; remain_sec={remain}"
        )

    def _start_selfdrive_session(
        self,
        goal: str,
        *,
        duration_minutes: Optional[int] = None,
        source_text: str = "",
    ) -> str:
        now = float(time.time())
        normalized_goal = self._normalize_selfdrive_goal(goal) or "持续推进当前任务"
        brief_path, brief_text = self._load_task_brief_from_text(source_text or goal)
        with self._selfdrive_lock:
            self._selfdrive_goal = normalized_goal
            self._selfdrive_unbounded = duration_minutes is None
            self._selfdrive_started_ts = now
            self._selfdrive_next_ts = now
            self._selfdrive_last_heartbeat_ts = now
            self._selfdrive_mode = self._env_str("SELFDRIVE_KERNEL_MODE", "kernel_v16", fallback_on_empty=True)
            self._selfdrive_brief_path = str(brief_path or "")
            self._selfdrive_brief_text = str(brief_text or "")
            if duration_minutes is None:
                self._selfdrive_deadline_ts = 0.0
            else:
                self._selfdrive_deadline_ts = now + float(max(1, int(duration_minutes))) * 60.0
            self._selfdrive_kernel_state = self._build_selfdrive_kernel_state(
                goal=normalized_goal,
                duration_minutes=duration_minutes,
            )
            self._selfdrive_kernel.run_step(
                state=self._selfdrive_kernel_state,
                checkpoint_path=self._selfdrive_checkpoint_path,
            )
            self._sync_selfdrive_legacy_from_kernel()
            self.mon["selfdrive_deadline_ts"] = float(self._selfdrive_deadline_ts)
            self.mon["selfdrive_unbounded"] = int(bool(self._selfdrive_unbounded))
            self.mon["selfdrive_mode"] = str(self._selfdrive_mode)
            self.mon["selfdrive_last_ts"] = float(now)
            self.mon["selfdrive_last_action"] = "start"
            self.mon["selfdrive_brief_path"] = str(self._selfdrive_brief_path or "")
            self._record_selfdrive_heartbeat(event="start")
            self._task_run_start(normalized_goal)
        budget = "infinite" if duration_minutes is None else f"{int(max(1, int(duration_minutes)))}m"
        brief_flag = "1" if self._selfdrive_brief_text else "0"
        return (
            f"[selfdrive] started; mode={self._selfdrive_mode}; "
            f"unbounded={int(bool(self._selfdrive_unbounded))}; budget={budget}; "
            f"goal={self._selfdrive_goal}; brief_loaded={brief_flag}; "
            f"brief_path={self._selfdrive_brief_path or '-'}; "
            f"total_steps={int(self.mon.get('selfdrive_total_steps', 0) or 0)}"
        )

    def _stop_selfdrive_session(self, reason: str = "user") -> str:
        now = float(time.time())
        with self._selfdrive_lock:
            self._selfdrive_active = False
            if self._selfdrive_kernel_state is not None:
                self._selfdrive_kernel_state.status = "failed"
                self._selfdrive_kernel_state.last_error = f"stopped:{str(reason or 'user')}"
            self._selfdrive_brief_path = ""
            self._selfdrive_brief_text = ""
            self.mon["selfdrive_enabled"] = 0
            self.mon["selfdrive_last_ts"] = float(now)
            self.mon["selfdrive_last_action"] = f"stop:{str(reason or 'user')}"
            self._record_selfdrive_heartbeat(event="stop", extra={"reason": str(reason or "user")})
            self._task_run_finalize("stopped")
        return f"[selfdrive] stopped; reason={str(reason or 'user')}"

    def _pause_selfdrive_session(self, reason: str = "user") -> str:
        now = float(time.time())
        with self._selfdrive_lock:
            if self._selfdrive_kernel_state is None:
                return "[selfdrive] pause ignored; reason=no_session"
            if not bool(self._selfdrive_active):
                return "[selfdrive] already_paused"
            self._selfdrive_active = False
            self._selfdrive_kernel_state.status = "paused"
            self.mon["selfdrive_enabled"] = 0
            self.mon["selfdrive_last_ts"] = float(now)
            self.mon["selfdrive_last_action"] = f"pause:{str(reason or 'user')}"
            self._record_selfdrive_heartbeat(event="pause", extra={"reason": str(reason or "user")})
            self._task_run_finalize("paused")
        return f"[selfdrive] paused; reason={str(reason or 'user')}"

    def _resume_selfdrive_session(self, reason: str = "user") -> str:
        now = float(time.time())
        with self._selfdrive_lock:
            ks = self._selfdrive_kernel_state
            if ks is None:
                return "[selfdrive] resume blocked; reason=no_session"
            if bool(self._selfdrive_active):
                return "[selfdrive] already_running"
            if str(ks.status or "").lower() in {"done", "failed"}:
                return f"[selfdrive] resume blocked; reason=terminal_status:{str(ks.status or '')}"
            self._selfdrive_active = True
            ks.status = "running"
            self._selfdrive_next_ts = float(now)
            self.mon["selfdrive_enabled"] = 1
            self.mon["selfdrive_last_ts"] = float(now)
            self.mon["selfdrive_last_action"] = f"resume:{str(reason or 'user')}"
            self._record_selfdrive_heartbeat(event="resume", extra={"reason": str(reason or "user")})
        return "[selfdrive] resumed"

    def _set_selfdrive_autonomy(self, level: str) -> str:
        normalized = self._parse_autonomy_level(level)
        if not normalized:
            return "[selfdrive] set_autonomy rejected; reason=invalid_level; allowed=L0|L1|L2|L3"
        gap_map = {"L0": 30.0, "L1": 20.0, "L2": 10.0, "L3": 5.0}
        self._selfdrive_autonomy_level = normalized
        self._selfdrive_step_gap_sec = float(gap_map.get(normalized, 20.0))
        self.mon["selfdrive_autonomy_level"] = str(normalized)
        self.mon["selfdrive_last_action"] = f"set_autonomy:{normalized}"
        return f"[selfdrive] autonomy={normalized}; step_gap_sec={int(self._selfdrive_step_gap_sec)}"

    def _set_selfdrive_budget(self, max_steps: Any) -> str:
        parsed = self._parse_budget_steps(max_steps)
        if parsed is None:
            return "[selfdrive] set_budget rejected; reason=invalid_max_steps"
        self._selfdrive_budget_override_steps = int(parsed)
        self.mon["selfdrive_budget_max"] = int(parsed)
        with self._selfdrive_lock:
            if self._selfdrive_kernel_state is not None:
                self._selfdrive_kernel_state.budget_steps_max = int(parsed)
        self.mon["selfdrive_last_action"] = f"set_budget:{int(parsed)}"
        return f"[selfdrive] budget_max_steps={int(parsed)}"

    def _selfdrive_status_text(self) -> str:
        status = self._task_progress_status_text()
        if not status.startswith("[task] active=1; kind=selfdrive;"):
            return status
        replaced = status.replace("[task]", "[selfdrive]", 1)
        return (
            f"{replaced}; autonomy={self._selfdrive_autonomy_level}; "
            f"budget_max={int(self.mon.get('selfdrive_budget_max', 0) or 0)}"
        )

    def _check_selfdrive_control_gate(self, *, cmd: str, args: Dict[str, Any]) -> Optional[str]:
        command = str(cmd or "").strip().upper()
        tool_map = {
            "START_SELFDRIVE": "start_selfdrive",
            "PAUSE_SELFDRIVE": "pause_selfdrive",
            "RESUME_SELFDRIVE": "resume_selfdrive",
            "STATUS_SELFDRIVE": "status_selfdrive",
            "SET_AUTONOMY": "set_autonomy",
            "SET_BUDGET": "set_budget",
        }
        tool_name = str(tool_map.get(command) or "selfdrive_control")
        inputs: Dict[str, Any] = {}
        if command == "START_SELFDRIVE":
            inputs = {"goal": str(args.get("goal") or "")}
        elif command == "SET_AUTONOMY":
            inputs = {"level": str(args.get("level") or args.get("autonomy") or "")}
        elif command == "SET_BUDGET":
            inputs = {"max_steps": args.get("max_steps") if args.get("max_steps") is not None else args.get("budget")}
        else:
            inputs = {"command": command}
        with self._selfdrive_lock:
            ks = self._selfdrive_kernel_state
            budget_used = int(getattr(ks, "budget_steps_used", 0) or 0) if ks is not None else 0
            budget_max = int(getattr(ks, "budget_steps_max", 0) or 0) if ks is not None else int(
                self.mon.get("selfdrive_budget_max", 0) or 0
            )
        allowed, issues = self._plan_gate_allow_runtime_action(
            action_id=f"selfdrive::{command.lower()}",
            tool_name=tool_name,
            inputs=inputs,
            preconditions=[{"op": "tool_available", "args": {"tool": tool_name}}],
            success_criteria=[{"op": "predicate_ref", "args": {"name": "selfdrive_control_applied"}}],
            fallback={"on_failure": "ask_user"},
            budget_used=budget_used,
            budget_max=budget_max if budget_max > 0 else None,
        )
        if allowed:
            return None
        codes = ",".join([str(i.get("code") or "") for i in issues])
        return f"[selfdrive] blocked_by_plan_gate; command={command}; reason={codes or 'compile_error'}"

    def _execute_selfdrive_control_dsl(self, payload: Dict[str, Any], source_text: str) -> Optional[str]:
        exec_ts = float(time.time())
        self.mon.setdefault("first_execute_event_ts", 0.0)
        if float(self.mon.get("first_execute_event_ts") or 0.0) <= 0.0:
            self.mon["first_execute_event_ts"] = exec_ts
        if int(self.mon.get("immediate_reply_sent", 0) or 0) != 1:
            self.mon["immediate_reply_order_violation"] = int(self.mon.get("immediate_reply_order_violation", 0) or 0) + 1
        else:
            sent_ts = float(self.mon.get("sent_at_timestamp") or self.mon.get("immediate_reply_sent_at_timestamp") or 0.0)
            if sent_ts > 0 and exec_ts < sent_ts:
                self.mon["immediate_reply_order_violation"] = int(self.mon.get("immediate_reply_order_violation", 0) or 0) + 1
        cmd = str(payload.get("command") or "").strip().upper()
        args = payload.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        gate_msg = self._check_selfdrive_control_gate(cmd=cmd, args=args)
        if gate_msg:
            return gate_msg
        if cmd == "PAUSE_SELFDRIVE":
            return self._pause_selfdrive_session(reason="nl")
        if cmd == "RESUME_SELFDRIVE":
            return self._resume_selfdrive_session(reason="nl")
        if cmd == "STATUS_SELFDRIVE":
            return self._selfdrive_status_text()
        if cmd == "SET_AUTONOMY":
            level = str(args.get("level") or args.get("autonomy") or "").strip()
            return self._set_selfdrive_autonomy(level)
        if cmd == "SET_BUDGET":
            val = args.get("max_steps")
            if val is None:
                val = args.get("budget")
            return self._set_selfdrive_budget(val)
        if cmd == "START_SELFDRIVE":
            goal = self._normalize_selfdrive_goal(str(args.get("goal") or "")) or "持续推进当前任务"
            confirmed = self._to_bool_flag(args.get("confirmed") or args.get("__confirmed") or False)
            review = self._review_selfdrive_start_request(goal=goal, source_text=source_text)
            if not bool(review.get("approved")):
                reasons = [str(x) for x in list(review.get("reasons") or []) if str(x).strip()]
                if bool(review.get("needs_permission_confirm")) and (not bool(confirmed)):
                    pred = {
                        "intent": "selfdrive_start",
                        "suggested_mode": "ask_user_confirm",
                        "guard_reason": "permission_or_risk_review",
                    }
                    self._set_guard_confirm_pending(source_text=source_text, pred=pred, channel="selfdrive_start")
                    reason_text = "；".join(reasons) if reasons else "high_risk_or_permission_required"
                    return (
                        "执行前审查：该请求需要权限确认。"
                        "请回复“确认执行”授权本次任务；或补充边界（允许修改目录、禁止命令、验收标准）。"
                        f"（review={reason_text}）"
                    )
                if "goal_not_executable" in reasons:
                    return (
                        "当前目标不可直接执行：请提供可验证产物与范围，例如目标文件、预期测试或完成标准。"
                        "（category=goal_not_executable）"
                    )
            autonomy = str(args.get("autonomy") or "").strip()
            budget = args.get("budget")
            if autonomy:
                self._set_selfdrive_autonomy(autonomy)
            duration_minutes: Optional[int] = None
            budget_steps = self._parse_budget_steps(budget)
            if budget_steps is not None:
                self._set_selfdrive_budget(budget_steps)
                duration_minutes = budget_steps
            return self._start_selfdrive_session(
                goal=goal,
                duration_minutes=duration_minutes,
                source_text=source_text,
            )
        return None

    def _handle_selfdrive_natural_language_control(self, user_text: str) -> Optional[str]:
        compiled = self._compile_selfdrive_control_dsl(user_text)
        if not isinstance(compiled, dict):
            return None
        source = str(compiled.get("source") or "").strip().lower()
        if source == "nl" and (not bool(self.selfdrive_semantic_enabled)):
            return None
        out = self._execute_selfdrive_control_dsl(compiled, source_text=user_text)
        if out:
            return out
        cmd = str(compiled.get("command") or "").strip().upper()
        return f"[selfdrive] unsupported_command={cmd or 'UNKNOWN'}"

    def _overlay_selfdrive_when_semantic_disabled(self, user_text: str) -> Optional[str]:
        txt = str(user_text or "").strip()
        low = txt.lower()
        if (not low) or ("selfdrive" not in low and "自推进" not in txt):
            return None
        if bool(self.selfdrive_semantic_enabled):
            return None
        try:
            client = GLMClient()
            raw = client.chat(messages=[
                {"role": "system", "content": "Return JSON {channel,action,confidence,alt_action,alt_confidence,duration_minutes,direction}."},
                {"role": "user", "content": txt},
            ], temperature=0.2, max_tokens=220)
            obj = self._extract_json_dict_from_text(raw) or {}
        except Exception:
            self.mon["nl_overlay_abstain"] = int(self.mon.get("nl_overlay_abstain", 0) or 0) + 1
            return ""

        channel = str(obj.get("channel") or "").strip().lower()
        action = str(obj.get("action") or "").strip().lower()
        conf = float(obj.get("confidence") or 0.0)
        alt_conf = float(obj.get("alt_confidence") or 0.0)
        margin = max(0.0, conf - alt_conf)
        if channel != "selfdrive" or action != "start":
            self.mon["nl_overlay_abstain"] = int(self.mon.get("nl_overlay_abstain", 0) or 0) + 1
            return ""
        if bool(self.nl_control_overlay_shadow_mode):
            self.mon["nl_overlay_shadow_hits"] = int(self.mon.get("nl_overlay_shadow_hits", 0) or 0) + 1
            return ""
        if str(obj.get("alt_action") or "").strip() and margin < float(self.nl_control_overlay_min_margin):
            self.mon["nl_overlay_ambiguous"] = int(self.mon.get("nl_overlay_ambiguous", 0) or 0) + 1
            return ""
        if conf < float(self.nl_control_overlay_min_confidence):
            self.mon["nl_overlay_abstain"] = int(self.mon.get("nl_overlay_abstain", 0) or 0) + 1
            return ""

        direction = str(obj.get("direction") or txt)
        args = self._build_selfdrive_start_args_from_text(direction)
        dur = obj.get("duration_minutes")
        try:
            if dur is not None:
                args["budget"] = int(dur)
        except Exception:
            pass
        return self._execute_selfdrive_control_dsl({"command": "START_SELFDRIVE", "args": args}, source_text=txt)

    def _handle_natural_language_control(self, user_text: str) -> Optional[str]:
        debug_quick = self._handle_debug_natural_language(user_text)
        if debug_quick is not None:
            return debug_quick
        confirmed = self._consume_guard_confirm_pending(user_text)
        if isinstance(confirmed, dict):
            out = self._execute_guard_confirmed_action(confirmed)
            if out:
                return out
        if self._looks_like_execute_confirmation(user_text):
            return "当前没有待确认执行的动作。请直接发送具体指令。"
        overlay_start = self._overlay_selfdrive_when_semantic_disabled(user_text)
        if overlay_start == "":
            return None
        if overlay_start is not None:
            return overlay_start
        selfdrive_out = self._handle_selfdrive_natural_language_control(user_text)
        if selfdrive_out:
            return selfdrive_out
        if self._looks_like_generic_progress_intent(user_text):
            return self._task_progress_status_text()
        if self._looks_like_need_todo_intent(user_text):
            with self._selfdrive_lock:
                active = bool(self._selfdrive_active)
                goal = str(self._selfdrive_goal or "")
            if active:
                return self._selfdrive_status_text()
            return self._selfdrive_plan_text(goal=goal or str(user_text or "").strip())
        if not bool(self.nl_control_overlay_semantic_enabled):
            return None
        pred = self.semantic_intent_lane.infer(user_text, self.mon)
        self.semantic_trigger_engine = self.semantic_intent_lane.semantic_trigger_engine
        self.semantic_trigger_last = dict(self.semantic_intent_lane.semantic_trigger_last or {})
        if not isinstance(pred, dict):
            self.mon["nl_overlay_abstain"] = int(self.mon.get("nl_overlay_abstain", 0) or 0) + 1
            return None

        suggested_mode = str(pred.get("suggested_mode") or pred.get("suuggested_mode") or "").strip().lower()
        if suggested_mode in {"ask_user_confirm", "shadow_plan_only"}:
            self.mon["nl_overlay_hits"] = int(self.mon.get("nl_overlay_hits", 0) or 0) + 1
            self.mon["nl_overlay_last_target"] = str(pred.get("intent") or pred.get("selected_trigger") or "")
            self.mon["nl_overlay_last_action"] = suggested_mode
            self.mon["nl_overlay_last_reason"] = str(pred.get("guard_reason") or "")
            self.mon["nl_overlay_last_confidence"] = float(pred.get("confidence") or 0.0)
        else:
            decision = str(pred.get("decision") or "")
            trigger_id = str(pred.get("selected_trigger") or pred.get("intent") or "")
            confidence = float(pred.get("confidence") or 0.0)
            margin = float(pred.get("margin") or 0.0)
            execution_allowed = bool(pred.get("execution_allowed") or False)
            if not decision:
                if suggested_mode == "ask_clarify":
                    decision = "ask_clarification"
                elif suggested_mode in {"debug", "selfdrive"} and trigger_id:
                    decision = "trigger"
                else:
                    decision = "no_trigger"

            if decision != "trigger" or not trigger_id:
                if not ((decision == "ask_clarification" or suggested_mode == "ask_clarify") and trigger_id):
                    self.mon["nl_overlay_abstain"] = int(self.mon.get("nl_overlay_abstain", 0) or 0) + 1
                    return None
                self.mon["nl_overlay_hits"] = int(self.mon.get("nl_overlay_hits", 0) or 0) + 1
                self.mon["nl_overlay_last_target"] = trigger_id
                self.mon["nl_overlay_last_action"] = "ask_clarification"
                self.mon["nl_overlay_last_reason"] = ",".join(pred.get("missing_slots") or [])
                self.mon["nl_overlay_last_confidence"] = confidence
            else:
                if not execution_allowed:
                    self.mon["nl_overlay_abstain"] = int(self.mon.get("nl_overlay_abstain", 0) or 0) + 1
                    return None
                min_conf = float(self.nl_control_overlay_min_confidence)
                min_margin = float(self.nl_control_overlay_min_margin)
                if confidence < min_conf or margin < min_margin:
                    self.mon["nl_overlay_ambiguous"] = int(self.mon.get("nl_overlay_ambiguous", 0) or 0) + 1
                    return None
                self.mon["nl_overlay_hits"] = int(self.mon.get("nl_overlay_hits", 0) or 0) + 1
                self.mon["nl_overlay_last_target"] = trigger_id
                self.mon["nl_overlay_last_action"] = "trigger"
                self.mon["nl_overlay_last_reason"] = "semantic_overlay"
                self.mon["nl_overlay_last_confidence"] = confidence
        if not isinstance(pred, dict):
            return None

        suggested_mode = str(pred.get("suggested_mode") or pred.get("suuggested_mode") or "").strip().lower()
        guard_reason = str(pred.get("guard_reason") or "").strip()
        if suggested_mode == "ask_user_confirm":
            self._set_guard_confirm_pending(source_text=user_text, pred=pred, channel="nl_control")
            guess = self._build_low_confidence_guess_preview(user_text, pred)
            reason_text = self._humanize_guard_reason(guard_reason)
            return (
                "这个请求可能涉及执行动作，我先不直接动手。"
                f"我的理解是：{guess}。"
                "如果理解正确，请回复“确认执行”；如果不对，请直接补充目标文件、预期产物或执行边界。"
                f"{('（原因：' + reason_text + '）') if reason_text else ''}"
            )
        if suggested_mode == "shadow_plan_only":
            plan_preview = self._selfdrive_plan_text(goal=str(user_text or "").strip())
            return (
                "已切换为 shadow_plan_only：当前仅输出计划，不自动执行\n"
                f"{plan_preview}"
            )

        decision = str(pred.get("decision") or "")
        suggested_mode = str(pred.get("suggested_mode") or pred.get("suuggested_mode") or "").strip().lower()
        trigger_id = str(pred.get("selected_trigger") or pred.get("intent") or "")
        if not decision:
            if suggested_mode == "ask_clarify":
                decision = "ask_clarification"
            elif suggested_mode in {"debug", "selfdrive"} and trigger_id:
                decision = "trigger"
            else:
                decision = "no_trigger"
        if not trigger_id or trigger_id in {"smalltalk_chat", "chit_chat", "code_debug"}:
            return None
        if decision != "ask_clarification" and suggested_mode != "ask_clarify":
            return None

        missing = [str(x) for x in (pred.get("missing_slots") or []) if str(x).strip()]
        if not missing:
            return None
        need = "、".join(missing)
        return f"我可以继续处理这个请求，但还缺少关键信息：{need}。"
    def _normalize_python_command(self, command: str) -> str:
        return str(command or "").strip()

    def _log_activity(self, tag: str, text: str, echo: bool = False) -> None:
        msg = str(text or "").strip()
        if not msg:
            return
        safe_tag = str(tag or "system").strip() or "system"
        try:
            db_write_system_pair(self.cfg.db_path, msg, tag=safe_tag)
        except Exception:
            pass
        should_echo = bool(echo) or (str(safe_tag).lower() == "debug" and bool(self.debug_frontend_chat_enabled) and bool(self.cfg.ide_watch_enabled))
        if should_echo:
            try:
                self.reply_q.put(msg)
            except Exception:
                pass

    @staticmethod
    def _to_bool_flag(value: Any) -> bool:
        if isinstance(value, bool):
            return bool(value)
        if isinstance(value, (int, float)):
            return bool(value)
        s = str(value or "").strip().lower()
        return s in {"1", "true", "yes", "on", "y"}

    @staticmethod
    def _looks_like_execute_confirmation(text: str) -> bool:
        q = str(text or "").strip().lower()
        if not q:
            return False
        return bool(
            re.search(
                r"^(确认执行|确认|继续执行|执行吧|可以执行|ok执行|yes|y|confirm|proceed|go ahead)\s*$",
                q,
                re.IGNORECASE,
            )
        )

    def _set_guard_confirm_pending(self, *, source_text: str, pred: Dict[str, Any], channel: str) -> None:
        self._guard_confirm_pending = {
            "ts": float(time.time()),
            "source_text": str(source_text or "").strip(),
            "channel": str(channel or "").strip().lower(),
            "intent": str(pred.get("intent") or pred.get("selected_trigger") or "").strip(),
            "suggested_mode": str(pred.get("suggested_mode") or pred.get("suuggested_mode") or "").strip().lower(),
            "guard_reason": str(pred.get("guard_reason") or "").strip(),
        }
        self.mon["guard_confirm_pending"] = 1
        self.mon["guard_confirm_pending_channel"] = str(channel or "")
        self.mon["guard_confirm_pending_reason"] = str(pred.get("guard_reason") or "")

    @staticmethod
    def _humanize_guard_reason(reason: str) -> str:
        text = str(reason or "").strip()
        low = text.lower()
        if not low:
            return ""
        if low.startswith("low_confidence<"):
            return "当前识别置信度偏低"
        if low.startswith("missing_required_slots:"):
            tail = text.split(":", 1)[-1].strip()
            if tail:
                return f"缺少关键信息：{tail}"
            return "缺少关键信息"
        return text

    def _build_low_confidence_guess_preview(self, user_text: str, pred: Dict[str, Any]) -> str:
        text = str(user_text or "").strip()
        if not text:
            return ""
        fallback_intent = str(pred.get("intent") or pred.get("selected_trigger") or "unknown")
        fallback_mode = str(pred.get("suggested_mode") or pred.get("suuggested_mode") or "chat")
        fallback = "请先做一次保守检查，再按你确认的目标执行。"
        verbose = str(os.getenv("NL_GUARD_VERBOSE", "0")).strip().lower() in {"1", "true", "yes", "on"}
        try:
            client = GLMClient()
            system = (
                "你是控制意图猜测器。输入可能低置信度，请做保守猜测。"
                "仅返回JSON对象，字段: intent, guessed_action, reason, risk, ask。"
            )
            payload = {
                "text": text,
                "semantic_hint": {
                    "intent": fallback_intent,
                    "mode": fallback_mode,
                    "confidence": float(pred.get("confidence") or 0.0),
                    "guard_reason": str(pred.get("guard_reason") or ""),
                },
                "rules": [
                    "不要直接执行",
                    "优先给一个可确认的最小动作",
                    "ask 用于向用户补齐关键参数",
                ],
            }
            raw = client.chat(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0.2,
                max_tokens=260,
            )
            obj = self._extract_json_dict_from_text(raw)
            if isinstance(obj, dict):
                intent = str(obj.get("intent") or fallback_intent).strip()
                action = str(obj.get("guessed_action") or "").strip()
                reason = str(obj.get("reason") or "").strip()
                risk = str(obj.get("risk") or "").strip()
                ask = str(obj.get("ask") or "").strip()
                preview = action or ask or fallback
                preview = preview.strip("；;。 \n\t")
                if preview:
                    if verbose:
                        parts = [f"intent={intent}", f"guess_action={preview}"]
                        if risk:
                            parts.append(f"risk={risk}")
                        if reason:
                            parts.append(f"reason={reason}")
                        if ask:
                            parts.append(f"ask={ask}")
                        return "; ".join(parts).strip()
                    return f"你可能是想让我{preview}"
        except Exception:
            pass
        if verbose:
            return f"intent={fallback_intent}; mode={fallback_mode}; guess_action={fallback}"
        return f"你可能是想让我{fallback}"

    def _consume_guard_confirm_pending(self, user_text: str) -> Optional[Dict[str, Any]]:
        if not self._looks_like_execute_confirmation(user_text):
            return None
        pending = dict(self._guard_confirm_pending or {})
        if not pending:
            return None
        ts = float(pending.get("ts") or 0.0)
        ttl = float(self._guard_confirm_ttl_sec or 120.0)
        if ts <= 0.0 or (time.time() - ts) > max(5.0, ttl):
            self._guard_confirm_pending = {}
            self.mon["guard_confirm_pending"] = 0
            return None
        self._guard_confirm_pending = {}
        self.mon["guard_confirm_pending"] = 0
        self.mon["guard_confirm_accepted"] = int(self.mon.get("guard_confirm_accepted", 0) or 0) + 1
        return pending

    def _execute_guard_confirmed_action(self, pending: Dict[str, Any]) -> Optional[str]:
        source_text = str(pending.get("source_text") or "").strip()
        intent = str(pending.get("intent") or "").strip().lower()
        mode = str(pending.get("suggested_mode") or "").strip().lower()
        if not source_text:
            return "已确认执行，但缺少原始指令，请直接重述你的请求。"

        if intent == "code_debug" or mode == "debug":
            gate_msg = self._check_debug_workflow_gate(source_text, trigger="confirmed_guard")
            if gate_msg:
                return gate_msg
            out = self._run_semantic_debug_workflow(source_text, prefer_full_scan=True)
            if out:
                return out
            # Fall through to generic selfdrive delegation path when debug target
            # is missing, instead of forcing users to restate the same command.

        pending_channel = str(pending.get("channel") or "").strip().lower()
        if pending_channel == "selfdrive_start" or intent == "selfdrive_start":
            args = self._build_selfdrive_start_args_from_text(source_text, fallback_goal=source_text, confirmed=True)
            direct_start = self._execute_selfdrive_control_dsl(
                {"command": "START_SELFDRIVE", "args": args},
                source_text=source_text,
            )
            if direct_start:
                return direct_start

        selfdrive_compiled = self._compile_selfdrive_control_dsl(source_text)
        if isinstance(selfdrive_compiled, dict):
            selfdrive_out = self._execute_selfdrive_control_dsl(selfdrive_compiled, source_text=source_text)
            if selfdrive_out:
                return selfdrive_out
        if self._looks_like_generic_progress_intent(source_text):
            return self._task_progress_status_text()
        if self._looks_like_need_todo_intent(source_text):
            with self._selfdrive_lock:
                active = bool(self._selfdrive_active)
                goal = str(self._selfdrive_goal or "")
            if active:
                return self._selfdrive_status_text()
            return self._selfdrive_plan_text(goal=goal or source_text)
        return (
            "已确认执行，但原指令仍不够明确。"
            "请直接给一个可执行指令，例如：`/selfdrive start <目标>` 或提供具体 debug 目标。"
        )

    def _handle_debug_natural_language(self, user_text: str) -> Optional[str]:
        q = str(user_text or "").strip().lower()
        if not q:
            return None
        if q in {"turn on debug", "debug mode on", "开启debug", "开debug", "autofix on", "watch ide errors"}:
            return self._start_continuous_autofix_session(trigger_text=user_text, intent_model="nl_rule")
        if q in {"turn off debug", "autofix off"}:
            self.cfg.ide_auto_fix_enabled = False
            self.cfg.ide_watch_enabled = False
            self._autofix_active = False
            self.mon["ide_autofix_active"] = 0
            return "[idewatch] auto_fix=0"
        if q in {"debug status", "what is debug monitor status now"} or "debug monitor status" in q:
            return f"[idewatch] enabled={int(bool(self.cfg.ide_watch_enabled))}; auto_fix={int(bool(self.cfg.ide_auto_fix_enabled))}; debug_echo={int(bool(self.debug_frontend_chat_enabled))}"
        if q in {"turn it off"}:
            if bool(self.cfg.ide_watch_enabled) or bool(self.cfg.ide_auto_fix_enabled):
                self.cfg.ide_auto_fix_enabled = False
                self.cfg.ide_watch_enabled = False
                self._autofix_active = False
                self.mon["ide_autofix_active"] = 0
                return "[idewatch] enabled=0; auto_fix=0"
            return None
        if "watch ide errors" in q:
            return self._start_continuous_autofix_session(trigger_text=user_text, intent_model="nl_semantic")
        if "自检" in q or "selfcheck" in q:
            ok, msg = selfcheck_python_target("agentlib/runtime_engine.py")
            return f"[selfcheck:OK] {str(msg or '')[:120]}" if bool(ok) else f"[selfcheck:FAIL] {str(msg or '')[:120]}"
        if "自推进" in q and bool(self.selfdrive_semantic_enabled):
            args = self._build_selfdrive_start_args_from_text(user_text)
            return self._execute_selfdrive_control_dsl({"command": "START_SELFDRIVE", "args": args}, source_text=user_text)
        if any(k in q for k in ["fix this bug", "this has a bug", "请修复", "bug"]):
            if "debug theory" in q:
                return None
            return self._start_continuous_autofix_session(trigger_text=user_text, intent_model="nl_bug")
        return None

    def _activity_ack_text(self, tag: str) -> str:
        safe_tag = str(tag or "Activity").strip() or "Activity"
        return f"已收到你的请求，我会在后台持续处理（Activity: {safe_tag}）。"

    def _route_control_reply_to_activity(self, msg_id: Optional[str], reply_text: str, user_text: str) -> bool:
        text = str(reply_text or "")
        if not text.startswith("["):
            return False
        ack = self._activity_ack_text("debug")
        if bool(self.activity_ack_llm_enabled):
            try:
                client = GLMClient()
                llm = str(client.chat(messages=[
                    {"role": "system", "content": "Rewrite control ack as natural Chinese without bracket tags."},
                    {"role": "user", "content": f"user={user_text}\nreply={reply_text}"},
                ], temperature=0.2, max_tokens=120) or "").strip()
                if llm:
                    ack = llm
            except Exception:
                pass
        self._emit_presence_reply(msg_id=msg_id, user_text=user_text, reply_text=ack, idle_tag=False, route="immediate", latency_tier="tier_1", structured=False)
        return True

    def _start_selfdrive(self, direction: str, duration_minutes: Optional[int] = None) -> str:
        goal = self._normalize_selfdrive_goal(direction) or "持续推进当前任务"
        return self._start_selfdrive_session(goal=goal, duration_minutes=duration_minutes, source_text=direction)

    def _execute_selfdrive_action(self, action: str, goal: str, task: str = "") -> str:
        act = str(action or "").strip().lower()
        if bool(self.safe_edit_guard_enabled) and act in {"autopilot_once", "autopilot_task"}:
            return "autopilot blocked by safe-edit guard"
        if act != "autopilot_task":
            return f"autopilot unsupported action={act or '-'}"

        target = ""
        for name in sorted(os.listdir(os.getcwd())):
            if name.lower().endswith('.py') and os.path.isfile(name):
                target = name
                break
        if not target:
            return "autopilot fallback failed: no python target"

        self._selfdrive_tests.append({"cmd": "codex_patch", "ts": time.time(), "task": str(task or "")})
        obj = None
        try:
            obj = self.codex_delegate.try_chat_json(system="autopilot patch", user_payload={"goal": goal, "task": task}, with_error=True)
        except Exception:
            obj = None
        if isinstance(obj, dict) and str((obj.get("output") or {}).get("patched_code") or "").strip():
            code = str((obj.get("output") or {}).get("patched_code") or "")
            with open(target, 'w', encoding='utf-8') as f:
                f.write(code)
            self._selfdrive_tests[-1]["cmd"] = "codex_patch"
            return "autopilot fallback=codex_patch; patched=1"

        try:
            client = GLMClient()
            raw = client.chat(messages=[
                {"role": "system", "content": "Return JSON with patched_code"},
                {"role": "user", "content": json.dumps({"goal": goal, "task": task}, ensure_ascii=False)},
            ], temperature=0.2, max_tokens=220)
            obj2 = self._extract_json_dict_from_text(raw)
            code2 = str((obj2 or {}).get("patched_code") or "").strip()
            if code2:
                with open(target, 'w', encoding='utf-8') as f:
                    f.write(code2)
                self._selfdrive_tests[-1]["cmd"] = "glm_patch"
                return "autopilot fallback=glm_patch; patched=1"
            raise RuntimeError("empty patched_code")
        except Exception as e:
            return f"autopilot fallback failed: codex patch unavailable; glm fallback unavailable: {type(e).__name__}: {e}"

    def _run_selfdrive_step(self) -> Optional[str]:
        now = time.time()
        with self._selfdrive_lock:
            if not bool(self._selfdrive_active):
                return None
            if (not bool(self._selfdrive_unbounded)) and float(self._selfdrive_deadline_ts or 0.0) > 0 and now >= float(self._selfdrive_deadline_ts):
                self._selfdrive_active = False
                return "[selfdrive] stopped:timeout"
            if self._selfdrive_step_index >= len(self._selfdrive_steps):
                if bool(self._selfdrive_unbounded):
                    self._selfdrive_steps = self._build_selfdrive_steps(self._selfdrive_goal)
                    self._selfdrive_step_index = 0
                    return "[selfdrive] renewed"
                else:
                    self._selfdrive_active = False
                    return "[selfdrive] completed"
            if not self._selfdrive_steps:
                return None
            step = dict(self._selfdrive_steps[self._selfdrive_step_index])
            self._selfdrive_step_index += 1
            goal = str(self._selfdrive_goal or "")
        out = self._execute_selfdrive_action(action=str(step.get("action") or ""), goal=goal, task=str(step.get("task") or ""))
        try:
            companion_rag.record_turn_memory(
                user_text=str(step.get("task") or step.get("name") or "selfdrive step"),
                assistant_text=str(out or ""),
                explicit_items=[
                    f"用户学习了：{goal}",
                    f"自推进步骤：{str(step.get('name') or step.get('action') or '')}",
                ],
            )
        except Exception:
            pass
        with self._selfdrive_lock:
            if self._selfdrive_step_index >= len(self._selfdrive_steps) and bool(self._selfdrive_unbounded):
                self._selfdrive_steps = self._build_selfdrive_steps(self._selfdrive_goal)
                self._selfdrive_step_index = 0
        return out

    def _try_ide_auto_fix(self, *, raw_delta: str, hit_summary: str = "") -> Optional[str]:
        if not bool(self.cfg.ide_auto_fix_enabled):
            return None
        target = ""
        m = re.search(r'File "([^"]+\.py)"', str(raw_delta or ""))
        if m:
            target = str(m.group(1) or "").strip()
        if not target or (not os.path.isfile(target)):
            return None
        sig = str(target)
        count, until_ts = self._ide_auto_fix_fail_state.get(sig, (0, 0.0))
        now = time.time()
        if now < float(until_ts):
            return None
        res = auto_debug_python_file(
            file_path=target,
            max_rounds=max(1, int(self.cfg.ide_auto_fix_rounds)),
            verify_command=str(self.autodebug_verify_command or "").strip(),
        )
        if not bool(getattr(res, 'ok', False)):
            wait = min(float(self._ide_auto_fix_failure_max_sec), float(self._ide_auto_fix_failure_base_sec) * (2 ** int(count)))
            self._ide_auto_fix_fail_state[sig] = (int(count) + 1, now + float(wait))
            return None
        self._ide_auto_fix_fail_state[sig] = (0, 0.0)
        return str(getattr(res, 'message', '') or '')

    # Legacy control/debug/selfdrive/autofix runtime block removed.
    # New architecture should live under agentlib.autonomy.

    def _build_immediate_emit_reply(
        self,
        *,
        user_text: str,
        msg_id: Optional[str],
        trace_id: str,
        event_id: str,
    ):
        def _emit(msg_id: Optional[str], reply_text: str, idle_tag: bool, structured: bool = False) -> None:
            self._emit_presence_reply(
                msg_id=msg_id,
                user_text=user_text,
                reply_text=reply_text,
                idle_tag=idle_tag,
                route="immediate_protocol",
                latency_tier="tier_1",
                structured=structured,
                trace_id=trace_id,
                event_id=event_id,
            )

        return _emit

    def _emit_presence_reply(
        self,
        *,
        msg_id: Optional[str],
        user_text: str,
        reply_text: str,
        idle_tag: bool,
        route: str,
        latency_tier: str,
        structured: bool = False,
        trace_id: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> None:
        trace = self._presence_min_flow(
            user_text=user_text,
            assistant_text=reply_text,
            trace_id=str(trace_id or self.mon.get("trace_id") or "trace_unknown"),
            event_id=str(event_id or self.mon.get("event_id") or "event_unknown"),
            route=str(route),
            latency_tier=str(latency_tier),
        )
        self.mon["presence_last_trace"] = trace
        # FieldTrace / BodyState 钩子 — 纯观察性日志；不改变响应/路由/人格/记忆
        if self._observability_enabled:
            try:
                interpreted = trace.get("interpreted_event", {}) if isinstance(trace, dict) else {}
                field_record = self._field_trace_extractor.extract(
                    interpreted=interpreted,
                    runtime_state=dict(self.state or {}),
                    router_output=None,
                    turn_index=int(self.turn_index or 0),
                    user_text=str(user_text or ""),
                )
                self._field_trace_store.record(field_record)
                if self._body_mapper is not None and self._body_logger is not None:
                    try:
                        _body_state = self._body_mapper.map_to_body_state(field_record)
                        self._body_logger.log(
                            _body_state,
                            turn_id=getattr(field_record, "turn_id", ""),
                            timestamp=getattr(field_record, "timestamp", ""),
                        )
                    except Exception:
                        pass  # BodyState 映射失败不得影响正常操作
            except Exception:
                pass  # 追踪失败不得影响正常操作
        self._emit_reply(msg_id=msg_id, reply_text=reply_text, idle_tag=idle_tag, structured=structured)
        self._check_presence_trace_after_emit(route=route, msg_id=msg_id, final_output=reply_text)

    def _check_presence_trace_after_emit(self, route: str, msg_id: Optional[str], final_output: str) -> None:
        trace_obj = self.mon.get("presence_last_trace")
        trace = trace_obj if isinstance(trace_obj, dict) else {}
        warnings = list(self.mon.get("presence_warnings") or [])

        issue = ""
        if not trace:
            issue = "missing_presence_trace"
        else:
            tr_route = str(trace.get("route") or "")
            tr_output = str(trace.get("final_output") or "")
            if tr_route != str(route or ""):
                issue = f"route_mismatch:{tr_route}->{route}"
            elif tr_output != str(final_output or ""):
                issue = "final_output_mismatch"

        if issue:
            warn = {
                "issue": issue,
                "route": str(route or ""),
                "msg_id": str(msg_id or ""),
            }
            warnings.append(warn)
            self.mon["presence_warnings"] = warnings
            self._log_activity(tag="presence_warn", text=f"[presence_warn] {json.dumps(warn, ensure_ascii=False)}", echo=False)

    def _emit_reply(self, msg_id: Optional[str], reply_text: str, idle_tag: bool, structured: bool = False) -> None:
        reply_text = self._finalize_reply_text(reply_text, structured=structured)
        envelope = ActionEnvelope.build(
            channel="dialogue",
            target="primary_chat",
            payload={
                "text": reply_text,
                "msg_id": msg_id,
                "idle_tag": bool(idle_tag),
                "structured": bool(structured),
                "voice_enabled": (not bool(structured)),
            },
            preconditions=[{"name": "non_empty_text", "ok": bool(reply_text)}],
            timeout_s=2.8,
            interruptible=True,
            receipt_required=True,
            priority="interaction_smoothness",
        )
        receipt = self._execute_actuation(
            executor=self.dialogue_executor,
            envelope=envelope,
            source="emit_reply",
        )
        self._execute_interaction_action(
            target="turn_delivery",
            payload={"msg_id": msg_id or "", "idle_tag": bool(idle_tag), "structured": bool(structured)},
            priority="interaction_smoothness",
        )
        self._execute_scene_effect_action(
            target="avatar_expression",
            payload={
                "emotion": str(self.state.get("emotion") or ""),
                "energy": int(self.state.get("energy", 60) or 60),
                "affinity": int(self.state.get("affinity", 20) or 20),
            },
            priority="expressive_enrichment",
        )
        self.mon["last_dialogue_action_status"] = str(receipt.status)
        self.mon["last_dialogue_action_id"] = str(receipt.action_id)
        self.mon["last_dialogue_action_success"] = int(receipt.success)

    def _execute_interaction_action(
        self,
        *,
        target: str,
        payload: Optional[Dict[str, Any]] = None,
        priority: str = "interaction_smoothness",
    ) -> Dict[str, Any]:
        envelope = ActionEnvelope.build(
            channel="interaction",
            target=str(target or "interaction"),
            payload=dict(payload or {}),
            timeout_s=2.0,
            interruptible=True,
            receipt_required=True,
            priority=str(priority or "interaction_smoothness"),
        )
        receipt = self._execute_actuation(executor=self.interaction_executor, envelope=envelope, source="interaction")
        return dict(receipt.details or {})

    def _execute_scene_effect_action(
        self,
        *,
        target: str,
        payload: Optional[Dict[str, Any]] = None,
        priority: str = "expressive_enrichment",
    ) -> Dict[str, Any]:
        envelope = ActionEnvelope.build(
            channel="scene_effect",
            target=str(target or "scene"),
            payload=dict(payload or {}),
            timeout_s=1.5,
            interruptible=True,
            receipt_required=True,
            priority=str(priority or "expressive_enrichment"),
        )
        receipt = self._execute_actuation(executor=self.scene_effect_executor, envelope=envelope, source="scene_effect")
        return dict(receipt.details or {})

    def _execute_actuation(self, *, executor: Any, envelope: ActionEnvelope, source: str) -> Any:
        receipt = executor.execute(envelope)
        evt = {
            "event": "action_execution_result",
            "ts": time.time(),
            "source": str(source or ""),
            "action_id": str(getattr(receipt, "action_id", "") or ""),
            "channel": str(getattr(receipt, "channel", "") or envelope.channel),
            "target": str(getattr(receipt, "target", "") or envelope.target),
            "status": str(getattr(receipt, "status", "") or ""),
            "success": bool(getattr(receipt, "success", False)),
            "retry_reason": str(getattr(receipt, "retry_reason", "") or ""),
            "started_at": float(getattr(receipt, "started_at", 0.0) or 0.0),
            "ended_at": float(getattr(receipt, "ended_at", 0.0) or 0.0),
            "priority": str(getattr(envelope, "priority", "") or ""),
            "interruptible": int(bool(getattr(envelope, "interruptible", True))),
            "details": dict(getattr(receipt, "details", {}) or {}),
        }
        self._append_jsonl(self._actuation_receipt_log_path, evt)
        self._actuation_trace_buffer.append(dict(evt))
        self.mon["actuation_last_channel"] = str(evt.get("channel") or "")
        self.mon["actuation_last_status"] = str(evt.get("status") or "")
        self.mon["actuation_last_success"] = int(bool(evt.get("success")))
        self.mon["actuation_receipt_count"] = int(self.mon.get("actuation_receipt_count", 0) or 0) + 1
        return receipt

    def _dialogue_text_sink(self, text: str, payload: Dict[str, Any]) -> None:
        msg_id = str(payload.get("msg_id") or "").strip() or None
        idle_tag = bool(payload.get("idle_tag"))
        if msg_id:
            db_write_outbox(self.cfg.db_path, msg_id, text)
            db_set_inbox_status(self.cfg.db_path, msg_id, "done")
            self.reply_q.put({"msg_id": msg_id, "reply": text})
            return
        if idle_tag:
            db_write_system_pair(self.cfg.db_path, text, tag="idle")
        self.reply_q.put(text)

    @staticmethod
    def _scene_effect_sink(payload: Dict[str, Any]) -> Dict[str, Any]:
        # Placeholder hook for live2d / animation / VFX.
        return {"ok": True, "scene_effect": "noop", "payload_keys": sorted([str(k) for k in payload.keys()])}

    @staticmethod
    def _interaction_sink(payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ok": True,
            "interaction": "handled",
            "target": str(payload.get("target") or ""),
            "payload_keys": sorted([str(k) for k in dict(payload.get("payload") or {}).keys()]),
        }

    def _active_reply_limits(self) -> tuple[int, int]:
        max_sentences = int(self.reply_max_sentences)
        max_chars = int(self.reply_max_chars)
        if self._reply_pref_max_sentences is not None:
            max_sentences = int(self._reply_pref_max_sentences)
        if self._reply_pref_max_chars is not None:
            max_chars = int(self._reply_pref_max_chars)
        if self._reply_turn_max_sentences is not None:
            max_sentences = int(self._reply_turn_max_sentences)
        if self._reply_turn_max_chars is not None:
            max_chars = int(self._reply_turn_max_chars)
        max_sentences = max(1, min(6, max_sentences))
        max_chars = max(40, min(600, max_chars))
        return max_sentences, max_chars

    def _update_reply_length_preferences(self, user_text: str, is_idle: bool = False) -> None:
        self._reply_turn_max_sentences = None
        self._reply_turn_max_chars = None
        if bool(is_idle):
            return
        pref = self._parse_reply_length_preference(user_text)
        if pref is None:
            s, c = self._active_reply_limits()
            self.mon["reply_max_sentences"] = int(s)
            self.mon["reply_max_chars"] = int(c)
            self.mon["reply_pref_persistent"] = int(self._reply_pref_max_sentences is not None)
            return

        action = str(pref.get("action") or "set").strip().lower()
        if action == "reset":
            self._reply_pref_max_sentences = None
            self._reply_pref_max_chars = None
            self.mon["reply_pref_source"] = "reset"
            self.mon["reply_pref_persistent"] = 0
            s, c = self._active_reply_limits()
            self.mon["reply_max_sentences"] = int(s)
            self.mon["reply_max_chars"] = int(c)
            return

        s = int(pref.get("max_sentences", self.reply_max_sentences) or self.reply_max_sentences)
        c = int(pref.get("max_chars", self.reply_max_chars) or self.reply_max_chars)
        s = max(1, min(6, s))
        c = max(40, min(600, c))
        persistent = bool(pref.get("persistent"))
        if persistent:
            self._reply_pref_max_sentences = int(s)
            self._reply_pref_max_chars = int(c)
            self.mon["reply_pref_source"] = "user_persistent"
            self.mon["reply_pref_persistent"] = 1
        else:
            self.mon["reply_pref_source"] = "user_turn"
        self._reply_turn_max_sentences = int(s)
        self._reply_turn_max_chars = int(c)
        self.mon["reply_max_sentences"] = int(s)
        self.mon["reply_max_chars"] = int(c)

    @staticmethod
    def _parse_reply_length_preference(user_text: str) -> Optional[Dict[str, Any]]:
        t = str(user_text or "").strip()
        if not t:
            return None
        low = t.lower()
        if re.search(
            r"(?:\u6062\u590d\u9ed8\u8ba4|\u91cd\u7f6e.*(?:\u957f\u5ea6|\u56de\u590d)|default length|reset .*length)",
            low,
            flags=re.IGNORECASE,
        ):
            return {"action": "reset", "persistent": True}

        persistent = bool(
            re.search(
                r"(?:\u4ee5\u540e|\u4e4b\u540e|\u63a5\u4e0b\u6765|from now on|going forward|by default)",
                low,
                flags=re.IGNORECASE,
            )
        )

        m_sent = re.search(r"(\d{1,2})\s*(?:\u53e5|sentences?|lines?)", t, flags=re.IGNORECASE)
        m_chars = re.search(r"(\d{2,4})\s*(?:\u5b57|\u5b57\u7b26|chars?)", t, flags=re.IGNORECASE)
        max_sentences = int(m_sent.group(1)) if m_sent else None
        max_chars = int(m_chars.group(1)) if m_chars else None

        short_hint = bool(
            re.search(
                r"(?:\u4e00\u53e5\u8bdd|\u7b80\u77ed|\u7b80\u6d01|\u7cbe\u7b80|\u77ed\u4e00\u70b9|\u522b\u592a\u957f|\bbrief\b|\bconcise\b|\bshort\b|\btl;?dr\b)",
                low,
                flags=re.IGNORECASE,
            )
        )
        long_hint = bool(
            re.search(
                r"(?:\u8be6\u7ec6|\u5c55\u5f00|\u5177\u4f53|\u591a\u8bf4|\u7ec6\u8bf4|\u8bb2\u8bb2|\u957f\u4e00\u70b9|\belaborate\b|\bexpand\b|\bdetailed?\b|\bin depth\b|\blonger\b)",
                low,
                flags=re.IGNORECASE,
            )
        )

        if max_sentences is None and max_chars is None:
            if short_hint and not long_hint:
                if re.search(r"(?:\u4e00\u53e5\u8bdd|one sentence)", low, flags=re.IGNORECASE):
                    max_sentences = 1
                    max_chars = 80
                else:
                    max_sentences = 2
                    max_chars = 120
            elif long_hint and not short_hint:
                max_sentences = 4
                max_chars = 260

        if max_sentences is None and max_chars is None:
            return None
        if max_sentences is None:
            max_chars_i = int(max_chars) if max_chars is not None else 180
            max_sentences = max(1, min(6, int((max_chars_i + 79) // 80)))
        if max_chars is None:
            max_chars = max(40, min(600, int(max_sentences) * 70))

        return {
            "action": "set",
            "persistent": bool(persistent),
            "max_sentences": int(max_sentences),
            "max_chars": int(max_chars),
        }

    def _finalize_reply_text(self, text: str, structured: bool = False) -> str:
        t = str(text or "").strip()
        if not t:
            return ""
        if bool(structured):
            return t
        if bool(self.plain_text_only_enabled):
            t = self._sanitize_plain_text_reply(t)
        max_sentences, max_chars = self._active_reply_limits()
        if bool(self.force_one_sentence_output):
            out = self._to_one_sentence(t)
        else:
            out = self._to_short_paragraph(
                t,
                max_sentences=int(max_sentences),
                max_chars=int(max_chars),
            )
        out = self._prepare_tts_text(out, enable_filler=bool(self.tts_filler_enabled))
        if bool(self.plain_text_only_enabled):
            out = self._sanitize_plain_text_reply(out)
        return out

    @staticmethod
    def _sanitize_plain_text_reply(text: str) -> str:
        t = str(text or "")
        if not t:
            return ""
        # Remove common machine tags and markdown wrappers.
        t = re.sub(r"\[[^\]\r\n]{1,80}\]\s*", "", t)
        t = re.sub(r"```[\s\S]*?```", " ", t)
        t = t.replace("`", " ")
        t = re.sub(r"^\s*[\-\*\#>\d\.\)\(]+\s*", "", t, flags=re.MULTILINE)
        # Remove most emoji/pictographic symbols.
        t = re.sub(r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF]", "", t)
        # Trim decorative symbols while preserving normal punctuation.
        t = re.sub(r"[鈥⑩梿鈼団槄鈽嗏枲鈻♀啋鈫愨啈鈫撱€愩€憑}<>|~=+^_]+", " ", t)
        t = t.replace("\r", " ").replace("\n", " ")
        t = re.sub(r"\s+", " ", t).strip()
        return t

    @staticmethod
    def _split_fillers(text: str) -> tuple[str, str]:
        t = str(text or "").strip()
        if not t:
            return "", ""
        # Keep this small and deterministic for TTS.
        filler_words = ["嗯", "呃", "啊", "诶", "哎", "em", "emm", "uh", "um"]
        pat = r"^\s*(?:" + "|".join(map(re.escape, sorted(filler_words, key=len, reverse=True))) + r")([，。,\s]*)"
        m = re.match(pat, t, flags=re.IGNORECASE)
        if not m:
            return "", t
        f = t[: m.end()].strip()
        core = t[m.end() :].strip()
        return f, core

    @classmethod
    def _prepare_tts_text(cls, text: str, enable_filler: bool = True) -> str:
        t = str(text or "").strip()
        if not t:
            return ""
        filler, core = cls._split_fillers(t)
        out = core or t
        out = out.replace("...", "，")
        out = re.sub(r"[,:;]+", "，", out)
        out = re.sub(r"\s+", " ", out).strip()
        if enable_filler and filler:
            lead = re.sub(r"[，。!？\s]+$", "", filler).strip()
            if lead:
                out = f"{lead}，{out}"
        if out and out[-1] not in {".", "!", "?", "。", "！", "？"}:
            out += "。"
        return out

    @staticmethod
    def _to_short_paragraph(text: str, max_sentences: int = 3, max_chars: int = 180) -> str:
        t = str(text or "")
        t = re.sub(r"\[[^\]\r\n]{1,80}\]\s*", "", t)
        t = t.replace("\r", " ").replace("\n", " ")
        t = re.sub(r"\s+", " ", t).strip()
        if not t:
            return ""
        max_sentences = max(1, min(6, int(max_sentences or 1)))
        max_chars = max(40, min(600, int(max_chars or 180)))

        # Split on sentence punctuation and keep delimiters.
        parts = re.split(r"([\.\!\?\u3002\uFF01\uFF1F]+)", t)
        chunks: List[str] = []
        i = 0
        while i < len(parts):
            body = str(parts[i] or "").strip()
            tail = str(parts[i + 1] or "").strip() if (i + 1) < len(parts) else ""
            if body:
                chunks.append((body + tail).strip())
            i += 2
        if not chunks:
            chunks = [t]

        out = " ".join(chunks[:max_sentences]).strip()
        if not out:
            out = t[:max_chars].strip()
        if len(out) > max_chars:
            out = out[:max_chars].rstrip()
        if out and out[-1] not in {".", "!", "?", "\u3002", "\uFF01", "\uFF1F"}:
            out += "\u3002"
        return out

    @staticmethod
    def _to_one_sentence(text: str) -> str:
        t = str(text or "")
        t = re.sub(r"\[[^\]\r\n]{1,80}\]\s*", "", t)
        t = re.sub(r"^[\-\*\d\.\)\s]+", "", t, flags=re.MULTILINE)
        t = t.replace("\r", " ").replace("\n", " ")
        t = " ".join(t.split())
        if not t:
            return ""
        parts = re.split(r"[???.!?]+", t, maxsplit=1)
        head = str(parts[0] if parts else t).strip()
        if not head:
            head = t[:80].strip()
        if not head:
            return ""
        if head[-1] not in {".", "!", "?", "?", "?", "?"}:
            head = head + "?"
        return head

    def _dialogue_voice_subchannel(self, reply_text: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.speech_cfg.enabled_tts:
            return {"ok": False, "reason": "tts_disabled"}
        t = str(reply_text or "").strip()
        if not t:
            return {"ok": False, "reason": "empty_text"}
        prosody = ssml_prosody_from_state(
            emotion=str(self.state.get("emotion") or ""),
            energy=int(self.state.get("energy", 60) or 60),
            affinity=int(self.state.get("affinity", 20) or 20),
        )
        meta = {
            "voice": self.speech_cfg.ssml_voice,
            "style": self.speech_cfg.ssml_style,
            "styledegree": self.speech_cfg.ssml_styledegree,
            "role": self.speech_cfg.ssml_role,
            "volume": self.speech_cfg.ssml_volume,
            "lang": self.speech_cfg.ssml_lang,
            "use_mstts": self.speech_cfg.ssml_use_mstts,
        }
        try:
            out = azure_tts_synthesize(text_raw=t, prosody=prosody, meta=meta, cfg=self.speech_cfg)
            if out.get("ok"):
                p = save_wav(out.get("audio") or b"", self.speech_cfg.tts_save_path)
                self.state["last_tts_path"] = p
                self.mon["tts_ok"] = int(self.mon.get("tts_ok", 0) or 0) + 1
                return {"ok": True, "path": p}
            self.mon["tts_fail"] = int(self.mon.get("tts_fail", 0) or 0) + 1
            return {"ok": False, "error": str(out.get("error") or "tts_failed")}
        except Exception as e:
            self.mon["tts_fail"] = int(self.mon.get("tts_fail", 0) or 0) + 1
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _parse_event(self, evt: Any) -> tuple[str, Optional[str], bool]:
        msg_id = None
        user_text = ""
        is_idle = False
        if isinstance(evt, dict):
            msg_id = evt.get("msg_id")
            evt_type = str(evt.get("type", "")).strip().upper()
            if evt_type == "USER":
                user_text = str(evt.get("text") or "").strip()
            elif evt_type == "IDLE_NUDGE":
                epoch = int(evt.get("epoch", -1))
                if epoch != int(self.state.get("input_epoch", 0) or 0):
                    return "", msg_id, False
                stage = int(evt.get("stage", 1) or 1)
                user_text = f"_IDLE_NUDGE_{stage}"
                is_idle = True
            else:
                user_text = str(evt.get("text") or "").strip()
        elif isinstance(evt, str):
            user_text = evt.strip()
        return user_text, str(msg_id) if msg_id else None, is_idle

    def _mouth_loop(self) -> None:
        # actuation node: materialize reply events to concrete output sink (stdout/UI bridge)
        while not self.stop_event.is_set():
            try:
                reply = self.reply_q.get(timeout=0.2)
            except queue.Empty:
                continue
            if reply is None:
                break
            if isinstance(reply, dict):
                text = str(reply.get("reply") or "")
            else:
                text = str(reply)
            if text:
                print(f"Aphrodite: {text}")

    # Legacy idewatch/autofix/autopilot runtime block removed.
    # New autonomous architecture should be implemented under agentlib.autonomy.

    def _idle_watcher(self) -> None:
        while not self.stop_event.is_set():
            time.sleep(float(self.cfg.idle_check_interval))
            self._maybe_advance_selfdrive()
            last_user_ts = self.state.get("last_user_ts")
            if not isinstance(last_user_ts, (int, float)):
                continue
            idle_for = time.time() - float(last_user_ts)
            if idle_for < int(self.cfg.idle_seconds):
                continue

            nudge_count = int(self.state.get("nudge_count", 0) or 0)
            last_nudge_ts = float(self.state.get("last_nudge_ts", 0.0) or 0.0)
            if nudge_count >= int(self.cfg.max_nudges):
                continue
            if time.time() - last_nudge_ts < int(self.cfg.nudge_cooldown_sec):
                continue

            stage = apply_idle_nudge(self.state)
            self.mon["idle_nudge_count"] = int(self.mon.get("idle_nudge_count", 0) or 0) + 1
            self.event_q.put(
                {
                    "type": "IDLE_NUDGE",
                    "stage": stage,
                    "epoch": int(self.state.get("input_epoch", 0) or 0),
                    "ts": time.time(),
                }
            )

    def _iter_autofix_scope_python_files(self) -> List[str]:
        ws = os.path.abspath(os.getcwd())
        scope_dirs = list(self._ide_auto_fix_scope_dirs or ["agentlib", "tests"])
        out: List[str] = []
        for scope in scope_dirs:
            root = os.path.abspath(os.path.join(ws, scope))
            if not os.path.isdir(root):
                continue
            for r, dirs, files in os.walk(root):
                rel = os.path.relpath(r, ws).replace("\\", "/").lower()
                if rel.startswith(".venv") or "/.venv/" in rel or "__pycache__" in rel:
                    continue
                dirs[:] = [d for d in dirs if d != "__pycache__" and d != ".venv"]
                for fn in files:
                    if not fn.lower().endswith(".py"):
                        continue
                    out.append(os.path.abspath(os.path.join(r, fn)))
        out.sort()
        return out

    @staticmethod
    def _selfcheck_single_file(path: str) -> Tuple[bool, str]:
        p = os.path.abspath(str(path or ""))
        if not p.lower().endswith(".py") or (not os.path.isfile(p)):
            return False, "target is not a python file"
        try:
            with tokenize.open(p) as f:
                source = f.read()
            compile(source, p, "exec")
            return True, ""
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    def _background_autofix_loop(self) -> None:
        self.mon["ide_autofix_active"] = 1
        self.mon["ide_autofix_stop_reason"] = ""
        while not self.stop_event.is_set():
            time.sleep(max(1.0, float(self._ide_auto_fix_loop_cooldown_sec)))
            if self.stop_event.is_set():
                break

            self._autofix_cycle += 1
            self.mon["ide_autofix_cycle"] = int(self._autofix_cycle)

            files = self._iter_autofix_scope_python_files()
            if not files:
                self.mon["ide_autofix_no_progress"] = int(self.mon.get("ide_autofix_no_progress", 0) or 0) + 1
                continue

            max_per_cycle = max(1, int(self._ide_auto_fix_continuous_max_files_per_cycle))
            fixed_in_cycle = 0
            failed_in_cycle = 0
            issues_seen = 0

            for path in files:
                if self.stop_event.is_set():
                    break
                ok, err = self._selfcheck_single_file(path)
                if ok:
                    continue
                issues_seen += 1

                self.mon["ide_auto_fix_runs"] = int(self.mon.get("ide_auto_fix_runs", 0) or 0) + 1
                self.mon["ide_auto_fix_last_ts"] = float(time.time())
                self.mon["ide_autofix_last_error_count"] = int(issues_seen)
                result = auto_debug_python_file(
                    file_path=path,
                    max_rounds=max(1, int(self.cfg.ide_auto_fix_rounds)),
                    error_context=f"IDE background autofix selfcheck failed:\n{err}",
                    verify_command=str(self.autodebug_verify_command or "").strip(),
                    cwd=os.path.dirname(path),
                )
                if bool(result.ok):
                    fixed_in_cycle += 1
                    self.mon["ide_autofix_success_runs"] = int(self.mon.get("ide_autofix_success_runs", 0) or 0) + 1
                else:
                    failed_in_cycle += 1
                if (fixed_in_cycle + failed_in_cycle) >= max_per_cycle:
                    break

            # Runtime traceback-driven autofix path (from IDE debug log).
            if (fixed_in_cycle + failed_in_cycle) < max_per_cycle:
                for path, ctx in self._collect_debug_log_autofix_targets():
                    if self.stop_event.is_set():
                        break
                    self.mon["ide_auto_fix_runs"] = int(self.mon.get("ide_auto_fix_runs", 0) or 0) + 1
                    self.mon["ide_auto_fix_last_ts"] = float(time.time())
                    result = auto_debug_python_file(
                        file_path=path,
                        max_rounds=max(1, int(self.cfg.ide_auto_fix_rounds)),
                        error_context=ctx,
                        verify_command=str(self.autodebug_verify_command or "").strip(),
                        cwd=os.path.dirname(path),
                    )
                    if bool(result.ok):
                        fixed_in_cycle += 1
                        self.mon["ide_autofix_success_runs"] = int(self.mon.get("ide_autofix_success_runs", 0) or 0) + 1
                    else:
                        failed_in_cycle += 1
                    if (fixed_in_cycle + failed_in_cycle) >= max_per_cycle:
                        break

            if issues_seen == 0:
                self._autofix_no_progress += 1
            else:
                self._autofix_no_progress = 0
            self.mon["ide_autofix_no_progress"] = int(self._autofix_no_progress)
            if failed_in_cycle > 0 and fixed_in_cycle == 0:
                self._autofix_noop_streak += 1
            else:
                self._autofix_noop_streak = 0
            self.mon["ide_autofix_noop_streak"] = int(self._autofix_noop_streak)

        self.mon["ide_autofix_active"] = 0
        if not str(self.mon.get("ide_autofix_stop_reason") or ""):
            self.mon["ide_autofix_stop_reason"] = "stopped"

    def _collect_debug_log_autofix_targets(self) -> List[Tuple[str, str]]:
        if not bool(self.cfg.ide_watch_enabled):
            return []
        log_path = os.path.abspath(str(self.cfg.ide_debug_log_path or "").strip())
        if not log_path or (not os.path.isfile(log_path)):
            return []
        try:
            with open(log_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                tail = min(size, 200 * 1024)
                f.seek(max(0, size - tail), os.SEEK_SET)
                blob = f.read(tail)
            text = blob.decode("utf-8", errors="ignore")
        except Exception:
            return []
        text = str(text or "").strip()
        if not text:
            return []

        sig = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()
        if sig == str(self._ide_log_last_payload_sig or ""):
            return []
        self._ide_log_last_payload_sig = sig

        # Keep the most recent traceback-like block as context.
        lines = [ln.rstrip("\r") for ln in text.splitlines()]
        tail_lines = lines[-120:] if len(lines) > 120 else lines
        context = "\n".join(tail_lines).strip()
        if not context:
            return []
        if not self._looks_like_error_context(context):
            return []

        candidates: List[str] = []
        candidates.extend(re.findall(r'File "([^"]+?\.py)"', context))
        candidates.extend(re.findall(r"([A-Za-z]:[\\/][^\s\"'`]+?\.py)", context))
        out: List[Tuple[str, str]] = []
        seen: set[str] = set()
        for token in candidates:
            resolved = self._resolve_python_path(str(token))
            if not resolved:
                continue
            abs_path = os.path.abspath(resolved)
            if not self._is_path_in_autofix_scope(abs_path):
                continue
            key = abs_path.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append((abs_path, context))
        return out

    def _is_path_in_autofix_scope(self, path: str) -> bool:
        p = os.path.abspath(str(path or ""))
        if not p:
            return False
        ws = os.path.abspath(os.getcwd())
        if bool(self.cfg.ide_auto_fix_only_workspace) and (not p.lower().startswith(ws.lower())):
            return False
        rel = os.path.relpath(p, ws).replace("\\", "/")
        for scope in list(self._ide_auto_fix_scope_dirs or []):
            s = str(scope or "").strip().strip("/\\").replace("\\", "/")
            if not s:
                continue
            if rel == s or rel.startswith(s + "/"):
                return True
        return False
