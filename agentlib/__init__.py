from .metrics import MetricsDB, start_metrics_thread
from .learned_lists import LearnedLists, ListState, init_learned_lists, refresh_state
from .style_policy import (
    ACTIONS,
    StyleDecision,
    SelfLearningStylePolicy,
    featurize_for_style,
    infer_reward_from_user_text,
    style_guidance_from_action,
)
try:
    from .memory_store import (
        MemoryStore,
        PhraseFilter,
        memory_weight,
        should_store_memory,
        learn_lists_from_feedback,
    )
except BaseException as _memory_store_import_error:
    MemoryStore = None  # type: ignore[assignment]
    PhraseFilter = None  # type: ignore[assignment]

    def _raise_memory_store_import_error(*_args, **_kwargs):
        raise RuntimeError(
            "agentlib.memory_store is unavailable: "
            f"{_memory_store_import_error}"
        ) from _memory_store_import_error

    memory_weight = _raise_memory_store_import_error
    should_store_memory = _raise_memory_store_import_error
    learn_lists_from_feedback = _raise_memory_store_import_error
from .web_search import web_search
from .goal_stack import Goal, GoalStack
from .tool_router import ToolDecision, simple_tool_router
from .memory_arbiter import MemorySlice, arbitrate_memory
from .sched_core import TaskQueue, Task, ToolExecutor, ToolResult, MemoryGovernance, MemoryItem
# TODO: P40 — replace glm_client with ds_client; import when callers are migrated
# from .glm_client import GLMClient, GLMConfig, GLMClientError, load_glm_config
from .companion_chat import (
    build_companion_messages,
    companion_reply,
    companion_reply_stream,
    companion_prepare_messages,
)
from .companion_prompt import build_system_prompt_sections, render_system_prompt
from .companion_rag import (
    RagCandidate,
    RagConfig,
    RagResult,
    build_rag_context,
    build_rag_package,
    get_memory_status,
    get_memory_store,
    is_memory_enabled,
    load_rag_config,
    record_turn_memory,
    retrieve_memory_context,
    render_rag_block,
    render_rag_trace,
)
from .runtime_state import RuntimeConfig, load_state, save_state, mark_user_turn, apply_idle_nudge, update_topic
from .chat_bridge import (
    ChatBridge,
    ensure_chat_tables_backend,
    db_set_inbox_status,
    db_write_outbox,
    db_write_system_pair,
    extract_text_from_payload,
)
from .runtime_engine import RuntimeEngine
from .advanced_decision import (
    AdvancedDecisionConfig,
    AdvancedDecisionResult,
    load_advanced_decision_config,
    generate_reply,
)
from .speech_azure import (
    AzureSpeechConfig,
    load_azure_speech_config,
    detect_ssml_unsupported,
    render_ssml,
    render_ssml_with_fallback,
    azure_tts_request,
    azure_tts_synthesize,
    azure_stt_transcribe_wav,
    ssml_prosody_from_state,
    save_wav,
)
from .autodebug import DebugRound, DebugResult, selfcheck_python_target, auto_debug_python_file
from .persona_profiles import PersonaProfile, get_persona_profile, list_persona_profiles
from .persona_router import PersonaRouteDecision, detect_persona_from_text
from .prompt_manager import PromptProfile, PromptManager, PromptTuneResult
from .screen_capture import ScreenCaptureResult, capture_screen_to_file
from .codex_delegate import CodexDelegateConfig, CodexDelegateResult, CodexDelegateClient, load_codex_delegate_config

__all__ = [
    "MetricsDB",
    "start_metrics_thread",
    "LearnedLists",
    "ListState",
    "init_learned_lists",
    "refresh_state",
    "ACTIONS",
    "StyleDecision",
    "SelfLearningStylePolicy",
    "featurize_for_style",
    "infer_reward_from_user_text",
    "style_guidance_from_action",
    "MemoryStore",
    "PhraseFilter",
    "memory_weight",
    "should_store_memory",
    "learn_lists_from_feedback",
    "web_search",
    "Goal",
    "GoalStack",
    "ToolDecision",
    "simple_tool_router",
    "MemorySlice",
    "arbitrate_memory",
    "TaskQueue",
    "Task",
    "ToolExecutor",
    "ToolResult",
    "MemoryGovernance",
    "MemoryItem",
    # TODO: P40 — replace with ds_client (DSClient, DSClientError) after experiment
    # "GLMClient",
    # "GLMConfig",
    # "GLMClientError",
    # "load_glm_config",
    # "DSClient",
    # "DSClientError",
    "build_companion_messages",
    "companion_reply",
    "companion_reply_stream",
    "companion_prepare_messages",
    "build_system_prompt_sections",
    "render_system_prompt",
    "is_memory_enabled",
    "get_memory_store",
    "get_memory_status",
    "retrieve_memory_context",
    "record_turn_memory",
    "RagCandidate",
    "RagResult",
    "build_rag_context",
    "build_rag_package",
    "render_rag_block",
    "render_rag_trace",
    "load_rag_config",
    "RagConfig",
    "RuntimeConfig",
    "load_state",
    "save_state",
    "mark_user_turn",
    "apply_idle_nudge",
    "update_topic",
    "ChatBridge",
    "ensure_chat_tables_backend",
    "db_set_inbox_status",
    "db_write_outbox",
    "db_write_system_pair",
    "extract_text_from_payload",
    "RuntimeEngine",
    "AdvancedDecisionConfig",
    "AdvancedDecisionResult",
    "load_advanced_decision_config",
    "generate_reply",
    "AzureSpeechConfig",
    "load_azure_speech_config",
    "detect_ssml_unsupported",
    "render_ssml",
    "render_ssml_with_fallback",
    "azure_tts_request",
    "azure_tts_synthesize",
    "azure_stt_transcribe_wav",
    "ssml_prosody_from_state",
    "save_wav",
    "DebugRound",
    "DebugResult",
    "selfcheck_python_target",
    "auto_debug_python_file",
    "PersonaProfile",
    "get_persona_profile",
    "list_persona_profiles",
    "PersonaRouteDecision",
    "detect_persona_from_text",
    "PromptProfile",
    "PromptManager",
    "PromptTuneResult",
    "ScreenCaptureResult",
    "capture_screen_to_file",
    "CodexDelegateConfig",
    "CodexDelegateResult",
    "CodexDelegateClient",
    "load_codex_delegate_config",
]
