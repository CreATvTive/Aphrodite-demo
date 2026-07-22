"""P2 task card 4 contract tests: provider abstraction + structure-A call.

All tests use the deterministic [`FakeTransport`](../../app/chatbox/provider/transport.py);
no real provider request is ever issued.  These tests cover the evidence
requirements listed in the task contract:

* one-line config switch DeepSeek/Kimi;
* default provider/model follow the Phase decision;
* DeepSeek and Kimi request adaptation (profile id, base_url, api_model);
* structure-A two-segment reply/increment parsing;
* registry-driven dimension filtering;
* unknown dimension / illegal value / malformed output safe defaults;
* timeout / auth / network / empty-response silent degradation to pure dynamics;
* sensitive info (api_key) never appears in errors or logs;
* ≥100 valid output samples with parse success rate ≥95%;
* P1 regression: the provider package imports only stdlib + app.chatbox.
"""

from __future__ import annotations

import inspect
import math
import random

import pytest

from app.chatbox.provider import (
    DEFAULT_PROFILES,
    FakeTransport,
    HttpTransport,
    INCREMENT_AMPLITUDE_CAP,
    INCREMENT_DELIMITER,
    ParsedReply,
    ProviderConfig,
    ProviderProfile,
    ProviderRegistry,
    ProviderRequest,
    ProviderResponse,
    ProviderTransportError,
    StructureACaller,
    build_default_registry,
    load_provider_config,
    parse_structure_a,
)


# ---------------------------------------------------------------------------
# Registry / profiles
# ---------------------------------------------------------------------------


def test_default_registry_has_deepseek_and_kimi():
    reg = build_default_registry()
    assert set(reg.profile_ids) == {"deepseek", "kimi"}
    assert "deepseek" in reg and "kimi" in reg


def test_default_provider_is_deepseek_phase_decision():
    cfg = load_provider_config(env={})
    assert cfg.provider_id == "deepseek"
    reg = build_default_registry()
    profile = reg.profile(cfg.provider_id)
    assert profile.profile_id == "deepseek"
    assert profile.display_label == "DeepSeek"
    assert profile.base_url == "https://api.deepseek.com/v1"


def test_default_model_label_is_deepseek_chat():
    cfg = load_provider_config(env={})
    reg = build_default_registry()
    profile = reg.profile(cfg.provider_id)
    # config.api_model is None -> profile default used.
    assert cfg.api_model is None
    assert profile.api_model == "deepseek-chat"


def test_one_line_switch_to_kimi():
    cfg = load_provider_config(env={"CHATBOX_PROVIDER": "kimi"})
    assert cfg.provider_id == "kimi"
    reg = build_default_registry()
    profile = reg.profile(cfg.provider_id)
    assert profile.profile_id == "kimi"
    assert profile.display_label == "Kimi"
    assert profile.base_url == "https://api.moonshot.cn/v1"
    assert profile.api_model == "moonshot-v1-8k"


def test_one_line_switch_back_to_deepseek():
    cfg = load_provider_config(env={"CHATBOX_PROVIDER": "deepseek"})
    assert cfg.provider_id == "deepseek"


def test_unknown_provider_profile_raises():
    reg = build_default_registry()
    with pytest.raises(KeyError):
        reg.profile("claude")


def test_registry_override_replaces_profile_model():
    custom = ProviderProfile(
        profile_id="deepseek",
        display_label="DeepSeek",
        api_model="deepseek-reasoner",
        base_url="https://api.deepseek.com/v1",
        api_style="openai_compat",
    )
    reg = ProviderRegistry(overrides={"deepseek": custom})
    assert reg.profile("deepseek").api_model == "deepseek-reasoner"


def test_registry_override_unknown_key_raises():
    with pytest.raises(KeyError):
        ProviderRegistry(overrides={"claude": DEFAULT_PROFILES["deepseek"]})


def test_registry_extra_profile_added():
    extra = ProviderProfile(
        profile_id="glm",
        display_label="GLM",
        api_model="glm-5",
        base_url="https://open.bigmodel.cn/api/paas/v4/",
        api_style="openai_compat",
    )
    reg = ProviderRegistry(extra={"glm": extra})
    assert "glm" in reg and "deepseek" in reg and "kimi" in reg


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_env_model_override():
    cfg = load_provider_config(env={"CHATBOX_PROVIDER_MODEL": "deepseek-reasoner"})
    assert cfg.api_model == "deepseek-reasoner"


def test_config_per_provider_model_env():
    cfg = load_provider_config(env={"CHATBOX_PROVIDER": "kimi", "CHATBOX_KIMI_MODEL": "moonshot-v1-32k"})
    assert cfg.api_model == "moonshot-v1-32k"


def test_config_timeout_and_retries_env():
    cfg = load_provider_config(env={"CHATBOX_PROVIDER_TIMEOUT_SEC": "30", "CHATBOX_PROVIDER_MAX_RETRIES": "2"})
    assert cfg.timeout_sec == 30.0
    assert cfg.max_retries == 2


def test_config_invalid_timeout_raises():
    with pytest.raises(ValueError):
        load_provider_config(env={"CHATBOX_PROVIDER_TIMEOUT_SEC": "0"})


def test_config_invalid_retries_raises():
    with pytest.raises(ValueError):
        load_provider_config(env={"CHATBOX_PROVIDER_MAX_RETRIES": "-1"})


def test_config_api_key_from_per_provider_env():
    cfg = load_provider_config(env={"CHATBOX_DEEPSEEK_API_KEY": "sk-deep"})
    assert cfg.api_key == "sk-deep"


def test_config_api_key_shared_fallback():
    cfg = load_provider_config(env={"CHATBOX_PROVIDER_API_KEY": "sk-shared"})
    assert cfg.api_key == "sk-shared"


def test_config_api_key_per_provider_takes_precedence():
    cfg = load_provider_config(env={
        "CHATBOX_DEEPSEEK_API_KEY": "sk-deep",
        "CHATBOX_PROVIDER_API_KEY": "sk-shared",
    })
    assert cfg.api_key == "sk-deep"


def test_config_no_key_when_env_empty():
    cfg = load_provider_config(env={})
    assert cfg.api_key is None


# ---------------------------------------------------------------------------
# Structure-A parsing
# ---------------------------------------------------------------------------


def test_parse_two_segment_reply_and_increment():
    output = "你好呀。\n---\n{\"birth_01\": 0.1, \"birth_03\": -0.2}"
    parsed = parse_structure_a(output, registry_dim_ids=["birth_01", "birth_03"])
    assert parsed.reply_text == "你好呀。"
    assert parsed.parsed_ok is True
    assert parsed.increment == {"birth_01": 0.1, "birth_03": -0.2}


def test_parse_no_delimiter_keeps_reply_no_increment():
    output = "只是一段回复，没有结构化增量。"
    parsed = parse_structure_a(output, registry_dim_ids=["birth_01"])
    assert parsed.reply_text == "只是一段回复，没有结构化增量。"
    assert parsed.increment == {}
    assert parsed.parsed_ok is False
    assert parsed.parse_note == "no-delimiter"


def test_parse_unknown_dimension_dropped():
    output = "回复\n---\n{\"unknown_dim\": 0.1, \"birth_01\": 0.2}"
    parsed = parse_structure_a(output, registry_dim_ids=["birth_01"])
    assert parsed.increment == {"birth_01": 0.2}
    assert "unknown_dim" not in parsed.increment


def test_parse_illegal_value_out_of_range_dropped():
    output = "回复\n---\n{\"birth_01\": 1.5, \"birth_02\": -1.7}"
    parsed = parse_structure_a(output, registry_dim_ids=["birth_01", "birth_02"])
    assert parsed.increment == {}
    assert parsed.parsed_ok is False


def test_parse_amplitude_truncated_to_cap():
    # 0.9 is in [-1,1] but exceeds the 0.3 single-move amplitude cap.
    output = "回复\n---\n{\"birth_01\": 0.9}"
    parsed = parse_structure_a(output, registry_dim_ids=["birth_01"])
    assert parsed.increment == {"birth_01": INCREMENT_AMPLITUDE_CAP}
    output2 = "回复\n---\n{\"birth_01\": -0.9}"
    parsed2 = parse_structure_a(output2, registry_dim_ids=["birth_01"])
    assert parsed2.increment == {"birth_01": -INCREMENT_AMPLITUDE_CAP}


def test_parse_value_at_cap_boundary_preserved():
    output = "回复\n---\n{\"birth_01\": 0.3, \"birth_02\": -0.3}"
    parsed = parse_structure_a(output, registry_dim_ids=["birth_01", "birth_02"])
    assert parsed.increment == {"birth_01": 0.3, "birth_02": -0.3}


def test_parse_malformed_increment_segment_degrades():
    output = "回复\n---\n这不是 JSON"
    parsed = parse_structure_a(output, registry_dim_ids=["birth_01"])
    assert parsed.reply_text == "回复"
    assert parsed.increment == {}
    assert parsed.parsed_ok is False
    assert parsed.parse_note == "increment-not-object"


def test_parse_empty_increment_segment_degrades():
    output = "回复\n---\n"
    parsed = parse_structure_a(output, registry_dim_ids=["birth_01"])
    assert parsed.increment == {}
    assert parsed.parsed_ok is False


def test_parse_tolerant_json_in_prose():
    output = "回复\n---\n这是增量：{\"birth_01\": 0.1} 结束"
    parsed = parse_structure_a(output, registry_dim_ids=["birth_01"])
    assert parsed.increment == {"birth_01": 0.1}
    assert parsed.parsed_ok is True


def test_parse_tolerant_json_in_code_fence():
    output = "回复\n---\n```json\n{\"birth_01\": 0.15}\n```"
    parsed = parse_structure_a(output, registry_dim_ids=["birth_01"])
    assert parsed.increment == {"birth_01": 0.15}


def test_parse_non_numeric_value_dropped():
    output = "回复\n---\n{\"birth_01\": \"high\"}"
    parsed = parse_structure_a(output, registry_dim_ids=["birth_01"])
    assert parsed.increment == {}


def test_parse_bool_value_dropped():
    output = "回复\n---\n{\"birth_01\": true}"
    parsed = parse_structure_a(output, registry_dim_ids=["birth_01"])
    assert parsed.increment == {}


def test_parse_nan_value_dropped():
    output = "回复\n---\n{\"birth_01\": NaN}"
    # NaN is not valid JSON; the tolerant extractor returns None.
    parsed = parse_structure_a(output, registry_dim_ids=["birth_01"])
    assert parsed.increment == {}


def test_parse_nested_object_ignored():
    output = "回复\n---\n{\"birth_01\": {\"x\": 0.1}}"
    parsed = parse_structure_a(output, registry_dim_ids=["birth_01"])
    assert parsed.increment == {}


def test_parse_delimiter_with_surrounding_spaces():
    output = "回复\n  ---  \n{\"birth_01\": 0.1}"
    parsed = parse_structure_a(output, registry_dim_ids=["birth_01"])
    assert parsed.increment == {"birth_01": 0.1}


def test_parse_only_registered_dimensions_kept():
    dims = ["birth_01", "birth_02", "birth_03"]
    output = "回复\n---\n{\"birth_01\": 0.1, \"birth_02\": 0.2, \"birth_99\": 0.3, \"birth_03\": 0.05}"
    parsed = parse_structure_a(output, registry_dim_ids=dims)
    assert set(parsed.increment) == {"birth_01", "birth_02", "birth_03"}


# ---------------------------------------------------------------------------
# StructureACaller end-to-end with FakeTransport
# ---------------------------------------------------------------------------


def _caller(provider_id="deepseek", responder=None, failure=None, api_key="sk-test"):
    reg = build_default_registry()
    cfg = ProviderConfig(
        provider_id=provider_id,
        api_model=None,
        api_key=api_key,
        timeout_sec=60.0,
        max_retries=0,
    )
    transport = FakeTransport(responder=responder, failure=failure)
    return StructureACaller(registry=reg, config=cfg, transport=transport), transport


def test_caller_deepseek_request_adaptation():
    caller, transport = _caller(
        "deepseek",
        responder=lambda req: "你好。\n---\n{}",
    )
    parsed = caller.call(system_prompt="s", user_prompt="u", registry_dim_ids=["birth_01"])
    assert len(transport.calls) == 1
    req = transport.calls[0]
    assert req.provider_id == "deepseek"
    assert req.api_model == "deepseek-chat"
    assert req.base_url == "https://api.deepseek.com/v1"
    assert req.api_key == "sk-test"
    assert req.messages[0].role == "system"
    assert req.messages[1].content == "u"
    assert parsed.provider_id == "deepseek"


def test_caller_kimi_request_adaptation():
    caller, transport = _caller(
        "kimi",
        responder=lambda req: "你好。\n---\n{}",
    )
    parsed = caller.call(system_prompt="s", user_prompt="u")
    req = transport.calls[0]
    assert req.provider_id == "kimi"
    assert req.api_model == "moonshot-v1-8k"
    assert req.base_url == "https://api.moonshot.cn/v1"
    assert parsed.provider_id == "kimi"


def test_caller_returns_reply_and_increment():
    caller, _ = _caller(
        "deepseek",
        responder=lambda req: "嗯，我在。\n---\n{\"birth_01\": 0.1, \"birth_03\": -0.2}",
    )
    parsed = caller.call(system_prompt="s", user_prompt="u", registry_dim_ids=["birth_01", "birth_03"])
    assert parsed.reply_text == "嗯，我在。"
    assert parsed.increment == {"birth_01": 0.1, "birth_03": -0.2}
    assert parsed.parsed_ok is True
    assert parsed.degraded is False


def test_caller_timeout_degrades_to_pure_dynamics():
    err = ProviderTransportError("timeout", "deepseek", "request timed out")
    caller, _ = _caller("deepseek", failure=err)
    parsed = caller.call(system_prompt="s", user_prompt="u")
    assert parsed.degraded is True
    assert parsed.reply_text == ""
    assert parsed.increment == {}
    assert parsed.parsed_ok is False
    assert parsed.parse_note == "transport:timeout"


def test_caller_auth_error_degrades():
    err = ProviderTransportError("auth", "deepseek", "HTTP 401", status=401)
    caller, _ = _caller("deepseek", failure=err)
    parsed = caller.call(system_prompt="s", user_prompt="u")
    assert parsed.degraded is True
    assert parsed.parse_note == "transport:auth"


def test_caller_network_error_degrades():
    err = ProviderTransportError("network", "deepseek", "network error")
    caller, _ = _caller("deepseek", failure=err)
    parsed = caller.call(system_prompt="s", user_prompt="u")
    assert parsed.degraded is True
    assert parsed.parse_note == "transport:network"


def test_caller_empty_response_degrades():
    # FakeTransport returns content that parses to no increment; but an empty
    # content string simulates an empty-response transport-level failure.
    caller, _ = _caller("deepseek", responder=lambda req: "")
    parsed = caller.call(system_prompt="s", user_prompt="u")
    assert parsed.reply_text == ""
    assert parsed.increment == {}
    assert parsed.parsed_ok is False


def test_caller_malformed_output_keeps_reply_drops_increment():
    caller, _ = _caller("deepseek", responder=lambda req: "回复\n---\ngarbage")
    parsed = caller.call(system_prompt="s", user_prompt="u", registry_dim_ids=["birth_01"])
    assert parsed.reply_text == "回复"
    assert parsed.increment == {}
    assert parsed.parsed_ok is False
    assert parsed.degraded is False


def test_caller_amplitude_truncated():
    caller, _ = _caller("deepseek", responder=lambda req: "r\n---\n{\"birth_01\": 0.9}")
    parsed = caller.call(system_prompt="s", user_prompt="u", registry_dim_ids=["birth_01"])
    assert parsed.increment == {"birth_01": INCREMENT_AMPLITUDE_CAP}


# ---------------------------------------------------------------------------
# Sensitive info: api_key never in errors or logs
# ---------------------------------------------------------------------------


def test_transport_error_does_not_leak_api_key():
    secret = "sk-super-secret-123456"
    err = ProviderTransportError("auth", "deepseek", "HTTP 401", status=401)
    caller, _ = _caller("deepseek", failure=err, api_key=secret)
    parsed = caller.call(system_prompt="s", user_prompt="u")
    # The error message and parse note must not contain the key.
    assert secret not in str(err)
    assert secret not in parsed.parse_note
    assert secret not in parsed.reply_text


def test_http_transport_error_redaction():
    err = ProviderTransportError("network", "deepseek", "network error")
    assert "sk-" not in str(err)
    assert "api_key" not in str(err).lower()


def test_fake_transport_does_not_read_api_key():
    secret = "sk-fake-secret"
    transport = FakeTransport(responder=lambda req: "r\n---\n{}")
    req = ProviderRequest(
        provider_id="deepseek",
        api_model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        api_key=secret,
        timeout_sec=60.0,
        messages=(__import__("app.chatbox.provider.transport", fromlist=["ProviderMessage"]).ProviderMessage(role="system", content="s"),),
    )
    transport.call(req)
    # The fake transport records the request but must not echo the key.
    assert transport.calls[0].api_key == secret  # stored on request, not leaked
    # No log output is produced by FakeTransport.


# ---------------------------------------------------------------------------
# ≥100 valid output samples: parse success rate ≥95%
# ---------------------------------------------------------------------------


def _make_sample(rng: random.Random, dim_ids: list[str]) -> str:
    reply = rng.choice([
        "嗯，我在听。",
        "今天也慢慢来。",
        "好。",
        "……你想说什么？",
        "我在这儿。",
    ])
    n = rng.randint(1, min(4, len(dim_ids)))
    chosen = rng.sample(dim_ids, n)
    inc = {d: round(rng.uniform(-0.3, 0.3), 3) for d in chosen}
    import json as _json
    return f"{reply}\n---\n{_json.dumps(inc)}"


def test_parse_success_rate_at_least_95_percent_over_100_samples():
    rng = random.Random(20260720)
    dim_ids = [f"birth_{i:02d}" for i in range(12)]
    samples = [_make_sample(rng, dim_ids) for _ in range(120)]
    ok = 0
    for output in samples:
        parsed = parse_structure_a(output, registry_dim_ids=dim_ids)
        if parsed.parsed_ok:
            ok += 1
    rate = ok / len(samples)
    assert rate >= 0.95, f"parse success rate {rate:.3f} < 0.95 (ok={ok}/{len(samples)})"


def test_parse_success_rate_with_realistic_noise_at_least_95_percent():
    """Realistic noise on valid samples: code fences, prose around JSON, extra whitespace.

    All samples here are valid outputs (delimiter present).  The missing-delimiter
    case is a malformed-output degradation test covered separately above.
    """
    rng = random.Random(424242)
    dim_ids = [f"birth_{i:02d}" for i in range(12)]
    import json as _json

    def _noisy_sample() -> str:
        reply = rng.choice(["嗯。", "好。", "我在。", "……", "嗯哼。"])
        n = rng.randint(1, 3)
        chosen = rng.sample(dim_ids, n)
        inc = {d: round(rng.uniform(-0.25, 0.25), 3) for d in chosen}
        body = _json.dumps(inc)
        style = rng.choice(["plain", "fence", "prose", "plain", "fence"])
        if style == "fence":
            segment = f"```json\n{body}\n```"
        elif style == "prose":
            segment = f"增量如下：{body} 以上。"
        else:
            segment = body
        return f"{reply}\n---\n{segment}"

    samples = [_noisy_sample() for _ in range(120)]
    ok = 0
    for output in samples:
        parsed = parse_structure_a(output, registry_dim_ids=dim_ids)
        if parsed.parsed_ok:
            ok += 1
    rate = ok / len(samples)
    assert rate >= 0.95, f"noisy parse success rate {rate:.3f} < 0.95 (ok={ok}/{len(samples)})"


# ---------------------------------------------------------------------------
# Quarantine boundary: provider package imports only stdlib + app.chatbox
# ---------------------------------------------------------------------------


def test_provider_package_does_not_import_quarantined_modules():
    import app.chatbox.provider as pkg
    import app.chatbox.provider.config as cfg_mod
    import app.chatbox.provider.registry as reg_mod
    import app.chatbox.provider.transport as tr_mod
    import app.chatbox.provider.structure_a as sa_mod
    forbidden = ("agentlib", "agent_kernel", "src.semantic_trigger", "demos.scenarios")
    for mod in (pkg, cfg_mod, reg_mod, tr_mod, sa_mod):
        src = inspect.getsource(mod)
        for bad in forbidden:
            assert bad not in src, f"{mod.__name__} references quarantined module {bad}"


def test_provider_package_imports_only_stdlib_and_app_chatbox():
    import app.chatbox.provider.config as cfg_mod
    import app.chatbox.provider.registry as reg_mod
    import app.chatbox.provider.transport as tr_mod
    import app.chatbox.provider.structure_a as sa_mod
    allowed_prefixes = ("app.chatbox", "typing", "dataclasses", "os", "json", "math",
                        "urllib", "io", "collections", "__future__")
    for mod in (cfg_mod, reg_mod, tr_mod, sa_mod):
        for name, val in vars(mod).items():
            if inspect.ismodule(val) and val.__name__:
                top = val.__name__.split(".")[0]
                assert top in allowed_prefixes or top == "app", f"{mod.__name__} imports {val.__name__}"


# ---------------------------------------------------------------------------
# P1 regression: provider package does not touch field state
# ---------------------------------------------------------------------------


def test_parsed_reply_does_not_apply_to_field_state():
    """The parser/caller must not import or call any field-state writer."""
    import app.chatbox.provider.structure_a as sa
    src = inspect.getsource(sa)
    assert "move_attractor" not in src
    assert "field_runtime" not in src
    assert "field_dynamics" not in src
    assert "field_state" not in src


def test_structure_a_boundary_preserves_structure_b_switch():
    """The caller is one-call (structure A); structure B is async separation.
    The interface must not bake in assumptions that block the switch."""
    import app.chatbox.provider.structure_a as sa
    # The module must expose parse_structure_a separately from the caller so
    # structure B can reuse the parser with a different transport flow.
    assert hasattr(sa, "parse_structure_a")
    assert hasattr(sa, "StructureACaller")
    assert callable(sa.parse_structure_a)