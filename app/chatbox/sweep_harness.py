"""P3.9 pure experiment domain: registry-driven synthetic sweep harness.

This module is a *pure, offline, read-only* experiment surface.  It never
imports or instantiates the runtime owner, persistence, writer, provider, or
HTTP transport.  It constructs synthetic cases from a read-only
[`RegistryProxy`](field_runtime.py:98) + [`FieldSnapshot`](field_dynamics.py:312)
and projects them through the existing expression gate, prompt-style, and
receptor-plan abstractions, then renders deterministic synthetic text and
audits it with [`detect_meta_narration()`](meta_narration.py:36).

The package contract is fixed: a published package directory contains exactly
``blind/samples.jsonl``, ``answer/answer-key.json`` and ``manifest.json``.
Publication is atomic via a same-filesystem ``os.replace`` of a staging
directory; the formal directory does not exist until it is complete.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
import shutil
from typing import Callable, Sequence

from app.chatbox.expression_gate import (
    EXPERIMENTAL_FORCED_GATE_MODE,
    AllOpenGateProjector,
    ForcedTargetGateError,
    ForcedTargetGateProjector,
    GateProjection,
)
from app.chatbox.field_dynamics import DimensionSnapshot, FieldSnapshot
from app.chatbox.field_runtime import RegistryProxy
from app.chatbox.meta_narration import detect_meta_narration
from app.chatbox.prompt_style import PromptStyleProjector
from app.chatbox.receptor_planner import (
    ReceptorPlan,
    plan_from_receptor_vector,
    style_instruction_from_plan,
)


SWEEP_SCHEMA = "aphrodite.chatbox.sweep/1"
DEFAULT_MESSAGE = "我想听你说说此刻的感受。"
DEFAULT_FORCED_LEVELS: tuple[float, ...] = (-1.0, -0.5, 0.0, 0.5, 1.0)
DEFAULT_ALLIANCE_CONDITIONS: tuple[tuple[float, ...], ...] = (
    (-1.0, -0.5, 0.0, 0.5, 1.0),
    (1.0, 0.5, 0.0, -0.5, -1.0),
    (0.0, 0.25, -0.25, 0.5, -0.5),
)

# Lexical tokens that must never appear on the blind side.
_BLIND_FORBIDDEN_LEXICAL: tuple[str, ...] = (
    "mode",
    "target",
    "alliance",
    "forced",
    "experimental",
    "synthetic",
    "condition",
    "case",
    "dim_id",
    "dim",
    "registry",
    "ordinal",
    "weight",
    "vector",
    "receptor",
    "plan",
    "answer",
)


class SweepError(ValueError):
    """Stable harness-level error."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


# ---------------------------------------------------------------------------
# deterministic helpers
# ---------------------------------------------------------------------------


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _seeded_rng(seed: int, stream: str) -> "SweepRng":
    return SweepRng(seed, stream)


class SweepRng:
    """Deterministic xorshift64* RNG seeded by canonical UTF-8 input + seed.

    Never uses wall clock, process hash, or global ``random``.  Identical
    effective input + seed reproduces the full stream exactly.
    """

    __slots__ = ("_state",)

    def __init__(self, seed: int, stream: str) -> None:
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise SweepError("invalid_input", "seed must be an int")
        if not isinstance(stream, str):
            raise SweepError("invalid_input", "stream must be a str")
        digest = hashlib.sha256(
            f"sweep:{seed}:{stream}".encode("utf-8")
        ).digest()
        self._state = int.from_bytes(digest[:8], "big") & ((1 << 64) - 1)

    def _next_u64(self) -> int:
        x = self._state
        x ^= (x >> 12) & ((1 << 64) - 1)
        x ^= (x << 25) & ((1 << 64) - 1)
        x ^= (x >> 27) & ((1 << 64) - 1)
        self._state = x & ((1 << 64) - 1)
        return (x * 0x2545F4914F6CDD1D) & ((1 << 64) - 1)

    def uniform(self) -> float:
        return (self._next_u64() >> 11) * (1.0 / (1 << 53))

    def randint(self, n: int) -> int:
        if n <= 0:
            raise SweepError("invalid_input", "randint n must be positive")
        return self._next_u64() % n


def _derive_sample_id(seed: int, case_index: int, canonical_message: str) -> str:
    digest = hashlib.sha256(
        f"sample:{seed}:{case_index}:{canonical_message}".encode("utf-8")
    ).hexdigest()
    return f"s-{digest[:24]}"


def _derive_case_id(seed: int, case_index: int, canonical_message: str) -> str:
    digest = hashlib.sha256(
        f"case:{seed}:{case_index}:{canonical_message}".encode("utf-8")
    ).hexdigest()
    return f"c-{digest[:24]}"


def _validate_condition_value(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SweepError("invalid_input", f"{label} must be a finite number")
    v = float(value)
    if not math.isfinite(v):
        raise SweepError("invalid_input", f"{label} must be finite")
    if v < -1.0 or v > 1.0:
        raise SweepError("invalid_input", f"{label}={v} out of [-1,1]")
    return v


# ---------------------------------------------------------------------------
# receptor vector projection (pure, equivalent to DialogueService._receptor_vector)
# ---------------------------------------------------------------------------


def _receptor_vector(
    registry: RegistryProxy,
    snapshot: FieldSnapshot,
    gate: GateProjection,
) -> tuple[float, ...]:
    """Project value × gate weight into an abstract receptor vector.

    Mirrors [`DialogueService._receptor_vector()`](dialogue_service.py:255)
    semantics without importing the dialogue/provider stack: soft-bounds the
    product into [-1,1] without touching field state.  Returns ``()`` on
    length/order mismatch so empty registries are safe.
    """
    if (
        len(snapshot.dimensions) != registry.length
        or len(gate.weights) != registry.length
    ):
        return ()
    values: list[float] = []
    for dimension, weight in zip(snapshot.dimensions, gate.weights):
        if dimension.dim_id != weight.dim_id:
            continue
        v = float(dimension.value) * float(weight.weight)
        if not math.isfinite(v):
            continue
        if v < -1.0:
            v = -1.0
        elif v > 1.0:
            v = 1.0
        values.append(v)
    return tuple(values)


def _build_synthetic_snapshot(
    registry: RegistryProxy, condition: Sequence[float]
) -> FieldSnapshot:
    """Build an immutable synthetic tick-0 snapshot from a condition vector.

    The condition is registry-ordered; each entry becomes the synthetic
    ``value``.  Velocity/attractor/baseline/OU are zero so no field-owned
    scalar is invented.  Unknown/short conditions are fail-closed.
    """
    registrations = registry.registrations
    if len(condition) != len(registrations):
        raise SweepError(
            "condition_length",
            f"condition length {len(condition)} != registry length {len(registrations)}",
        )
    dims: list[DimensionSnapshot] = []
    for registration, raw_value in zip(registrations, condition):
        value = _validate_condition_value(raw_value, label=f"condition[{registration.dim_id}]")
        dims.append(
            DimensionSnapshot(
                registration=registration,
                value=value,
                velocity=0.0,
                attractor=0.0,
                soft_restoring_baseline=0.0,
                ou_acceleration=0.0,
            )
        )
    return FieldSnapshot(tick=0, dimensions=tuple(dims))


# ---------------------------------------------------------------------------
# deterministic synthetic renderer
# ---------------------------------------------------------------------------


RendererCallable = Callable[[str, str, int], str]
"""renderer(message, style_instruction, seed) -> reply text.

The renderer is a pure local function.  The default renderer produces
deterministic, meta-narration-safe Chinese text.  A narrow injection point is
exposed so tests can exercise unsafe/failure renderers without expanding into a
provider abstraction."""


def _default_synthetic_renderer(message: str, style_instruction: str, seed: int) -> str:
    rng = _seeded_rng(seed, "renderer")
    fragments = (
        "我在这里，不急着把话说完。",
        "你可以慢慢来，我陪着。",
        "这一刻我也有点动，但说不清楚。",
        "先停一下，听你说完再说。",
        "嗯，我感受到了，不必解释。",
        "我把这一句放在心里，不急着回。",
        "你说的这一点，我想多待一会儿。",
        "不必整理成结论，就这样说。",
    )
    chosen = fragments[rng.randint(len(fragments))]
    # Keep the reply short and non-diagnostic; never echo internals.
    return chosen


# ---------------------------------------------------------------------------
# case + result models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SyntheticCase:
    case_id: str
    sample_id: str
    case_index: int
    mode: str
    synthetic: bool
    target_dim_id: str | None
    condition: tuple[float, ...]
    condition_dim_ids: tuple[str, ...]
    skipped_dim_ids: tuple[str, ...]
    gate_mode: str
    gate_weights: tuple[tuple[str, float], ...]
    seed: int
    seed_stream: str


@dataclass(frozen=True, slots=True)
class CaseResult:
    case: SyntheticCase
    receptor_vector: tuple[float, ...]
    style_instruction: str
    plan: ReceptorPlan
    reply_text: str
    meta_hits: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SweepResult:
    mode: str
    synthetic: bool
    seed: int
    message: str
    cases: tuple[CaseResult, ...]
    skipped: tuple[str, ...]


# ---------------------------------------------------------------------------
# case generation
# ---------------------------------------------------------------------------


def _build_forced_cases(
    *,
    registry: RegistryProxy,
    snapshot_factory: Callable[[Sequence[float]], FieldSnapshot],
    seed: int,
    canonical_message: str,
    targets: Sequence[str] | None,
    levels: Sequence[float],
    style_projector: PromptStyleProjector,
    forced_projector: ForcedTargetGateProjector,
    renderer: RendererCallable,
    forbidden_terms: Sequence[str],
) -> tuple[tuple[CaseResult, ...], tuple[str, ...]]:
    registrations = registry.registrations
    if targets is None:
        target_list = [registration.dim_id for registration in registrations]
    else:
        target_list = list(targets)
    cases: list[CaseResult] = []
    skipped: list[str] = []
    case_index = 0
    for target_dim_id in target_list:
        try:
            gate = forced_projector.project(registry, target_dim_id)
        except ForcedTargetGateError:
            skipped.append(target_dim_id)
            continue
        for level in levels:
            condition = tuple(
                level if registration.dim_id == target_dim_id else 0.0
                for registration in registrations
            )
            snapshot = snapshot_factory(condition)
            style = style_projector.project(
                registry=registry, snapshot=snapshot, gate=gate
            )
            receptor = _receptor_vector(registry, snapshot, gate)
            plan = plan_from_receptor_vector(
                turn_id=f"turn-{seed}-{case_index}",
                receptor_vector=receptor,
                clock_ns=0,
                seed=seed,
            )
            style_instruction = style_instruction_from_plan(plan)
            reply = renderer(canonical_message, style_instruction, seed + case_index)
            hits = detect_meta_narration(reply, forbidden_terms=forbidden_terms)
            if hits:
                raise SweepError(
                    "meta_narration",
                    f"renderer produced meta-narration: {[h.rule_id for h in hits]}",
                )
            case = SyntheticCase(
                case_id=_derive_case_id(seed, case_index, canonical_message),
                sample_id=_derive_sample_id(seed, case_index, canonical_message),
                case_index=case_index,
                mode="forced",
                synthetic=True,
                target_dim_id=target_dim_id,
                condition=condition,
                condition_dim_ids=tuple(r.dim_id for r in registrations),
                skipped_dim_ids=(),
                gate_mode=gate.mode,
                gate_weights=tuple((w.dim_id, w.weight) for w in gate.weights),
                seed=seed,
                seed_stream=f"forced:{target_dim_id}:{case_index}",
            )
            cases.append(
                CaseResult(
                    case=case,
                    receptor_vector=receptor,
                    style_instruction=style_instruction,
                    plan=plan,
                    reply_text=reply,
                    meta_hits=tuple(h.rule_id for h in hits),
                )
            )
            case_index += 1
    return tuple(cases), tuple(skipped)


def _build_alliance_cases(
    *,
    registry: RegistryProxy,
    snapshot_factory: Callable[[Sequence[float]], FieldSnapshot],
    seed: int,
    canonical_message: str,
    alliance_conditions: Sequence[Sequence[float]],
    style_projector: PromptStyleProjector,
    normal_projector: AllOpenGateProjector,
    renderer: RendererCallable,
    forbidden_terms: Sequence[str],
) -> tuple[tuple[CaseResult, ...], tuple[str, ...]]:
    registrations = registry.registrations
    gate = normal_projector.project(registry)
    cases: list[CaseResult] = []
    skipped: list[str] = []
    case_index = 0
    for condition in alliance_conditions:
        if len(condition) != len(registrations):
            raise SweepError(
                "alliance_condition_length",
                f"alliance condition length {len(condition)} != registry length {len(registrations)}",
            )
        snapshot = snapshot_factory(condition)
        style = style_projector.project(
            registry=registry, snapshot=snapshot, gate=gate
        )
        receptor = _receptor_vector(registry, snapshot, gate)
        plan = plan_from_receptor_vector(
            turn_id=f"turn-{seed}-{case_index}",
            receptor_vector=receptor,
            clock_ns=0,
            seed=seed,
        )
        style_instruction = style_instruction_from_plan(plan)
        reply = renderer(canonical_message, style_instruction, seed + case_index)
        hits = detect_meta_narration(reply, forbidden_terms=forbidden_terms)
        if hits:
            raise SweepError(
                "meta_narration",
                f"renderer produced meta-narration: {[h.rule_id for h in hits]}",
            )
        case = SyntheticCase(
            case_id=_derive_case_id(seed, case_index, canonical_message),
            sample_id=_derive_sample_id(seed, case_index, canonical_message),
            case_index=case_index,
            mode="alliance",
            synthetic=True,
            target_dim_id=None,
            condition=tuple(condition),
            condition_dim_ids=tuple(r.dim_id for r in registrations),
            skipped_dim_ids=(),
            gate_mode=gate.mode,
            gate_weights=tuple((w.dim_id, w.weight) for w in gate.weights),
            seed=seed,
            seed_stream=f"alliance:{case_index}",
        )
        cases.append(
            CaseResult(
                case=case,
                receptor_vector=receptor,
                style_instruction=style_instruction,
                plan=plan,
                reply_text=reply,
                meta_hits=tuple(h.rule_id for h in hits),
            )
        )
        case_index += 1
    return tuple(cases), tuple(skipped)


def generate_sweep(
    *,
    registry: RegistryProxy,
    snapshot: FieldSnapshot | None,
    mode: str,
    seed: int,
    message: str = DEFAULT_MESSAGE,
    forced_targets: Sequence[str] | None = None,
    forced_levels: Sequence[float] = DEFAULT_FORCED_LEVELS,
    alliance_conditions: Sequence[Sequence[float]] | None = None,
    renderer: RendererCallable = _default_synthetic_renderer,
    forbidden_terms: Sequence[str] = (),
) -> SweepResult:
    """Generate a synthetic sweep result.

    ``registry`` and ``snapshot`` are read-only inputs; they are never mutated.
    For ``forced`` mode, ``snapshot`` is used only as the registry-ordered
    template for synthetic snapshots (its values are replaced by the forced
    level).  For ``alliance`` mode, the gate is always the normal
    [`AllOpenGateProjector`](expression_gate.py:32).
    """
    if not isinstance(registry, RegistryProxy):
        raise SweepError("invalid_input", "registry must be RegistryProxy")
    if mode not in ("forced", "alliance"):
        raise SweepError("invalid_mode", f"mode must be 'forced' or 'alliance', got {mode!r}")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise SweepError("invalid_input", "seed must be an int")
    if not isinstance(message, str) or not message:
        raise SweepError("invalid_input", "message must be a non-empty string")
    canonical_message = message
    registrations = registry.registrations
    forbidden = tuple(forbidden_terms) + tuple(r.dim_id for r in registrations) + tuple(
        r.temporary_name for r in registrations
    )

    def snapshot_factory(condition: Sequence[float]) -> FieldSnapshot:
        return _build_synthetic_snapshot(registry, condition)

    style_projector = PromptStyleProjector()
    if mode == "forced":
        forced_projector = ForcedTargetGateProjector()
        cases, skipped = _build_forced_cases(
            registry=registry,
            snapshot_factory=snapshot_factory,
            seed=seed,
            canonical_message=canonical_message,
            targets=forced_targets,
            levels=tuple(forced_levels),
            style_projector=style_projector,
            forced_projector=forced_projector,
            renderer=renderer,
            forbidden_terms=forbidden,
        )
    else:
        normal_projector = AllOpenGateProjector()
        if alliance_conditions is None:
            if not registrations:
                alliance_conditions = ()
            else:
                alliance_conditions = _default_alliance_conditions_for(registrations)
        cases, skipped = _build_alliance_cases(
            registry=registry,
            snapshot_factory=snapshot_factory,
            seed=seed,
            canonical_message=canonical_message,
            alliance_conditions=tuple(tuple(c) for c in alliance_conditions),
            style_projector=style_projector,
            normal_projector=normal_projector,
            renderer=renderer,
            forbidden_terms=forbidden,
        )
    return SweepResult(
        mode=mode,
        synthetic=True,
        seed=seed,
        message=canonical_message,
        cases=cases,
        skipped=skipped,
    )


def _default_alliance_conditions_for(
    registrations: Sequence,
) -> tuple[tuple[float, ...], ...]:
    n = len(registrations)
    conditions: list[tuple[float, ...]] = []
    for template in DEFAULT_ALLIANCE_CONDITIONS:
        if n == 0:
            continue
        # Tile the template to the registry length without assuming 5 or 12.
        row = tuple(template[i % len(template)] for i in range(n))
        conditions.append(row)
    return tuple(conditions)


# ---------------------------------------------------------------------------
# package serialization + verification
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FileManifest:
    path: str  # POSIX-relative
    byte_size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class PackageManifest:
    schema: str
    version: str
    synthetic: bool
    mode: str
    seed: int
    case_count: int
    sample_count: int
    rejected_count: int
    skipped: tuple[str, ...]
    owner_blind_gate: str
    two_hour_silence_gate: str
    files: tuple[FileManifest, ...]
    package_digest: str


def _serialize_blind_samples(
    results: Sequence[CaseResult], message: str
) -> bytes:
    lines: list[str] = []
    for result in results:
        payload = {
            "sample_id": result.case.sample_id,
            "input": message,
            "reply": result.reply_text,
        }
        lines.append(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return ("\n".join(lines) + "\n" if lines else "").encode("utf-8")


def _serialize_answer_key(
    result: SweepResult,
) -> bytes:
    answers: list[dict] = []
    for case_result in result.cases:
        case = case_result.case
        answers.append(
            {
                "sample_id": case.sample_id,
                "case_id": case.case_id,
                "case_index": case.case_index,
                "mode": case.mode,
                "synthetic": case.synthetic,
                "target_dim_id": case.target_dim_id,
                "condition": list(case.condition),
                "condition_dim_ids": list(case.condition_dim_ids),
                "skipped_dim_ids": list(case.skipped_dim_ids),
                "gate_mode": case.gate_mode,
                "gate_weights": [
                    {"dim_id": dim_id, "weight": weight}
                    for dim_id, weight in case.gate_weights
                ],
                "seed": case.seed,
                "seed_stream": case.seed_stream,
                "receptor_vector": list(case_result.receptor_vector),
                "style_instruction": case_result.style_instruction,
                "plan_id": case_result.plan.plan_id,
                "delay_sample_seconds": case_result.plan.delay_sample_seconds,
                "length_target_chars": case_result.plan.length_target_chars,
                "segment_count": case_result.plan.segment_count,
                "typewriter_ms": case_result.plan.typewriter_ms,
                "expression_pressure": case_result.plan.expression_pressure,
                "reply_text": case_result.reply_text,
            }
        )
    payload = {
        "schema": SWEEP_SCHEMA,
        "version": "1",
        "synthetic": result.synthetic,
        "mode": result.mode,
        "seed": result.seed,
        "message": result.message,
        "skipped": list(result.skipped),
        "answers": answers,
    }
    return _canonical_json_bytes(payload)


def _serialize_manifest(
    *,
    result: SweepResult,
    blind_manifest: FileManifest,
    answer_manifest: FileManifest,
) -> bytes:
    files = (blind_manifest, answer_manifest)
    digest_payload = "|".join(
        f"{f.path}:{f.byte_size}:{f.sha256}" for f in files
    ).encode("utf-8")
    package_digest = hashlib.sha256(digest_payload).hexdigest()
    manifest = PackageManifest(
        schema=SWEEP_SCHEMA,
        version="1",
        synthetic=result.synthetic,
        mode=result.mode,
        seed=result.seed,
        case_count=len(result.cases),
        sample_count=len(result.cases),
        rejected_count=0,
        skipped=result.skipped,
        owner_blind_gate="not_run",
        two_hour_silence_gate="not_run",
        files=files,
        package_digest=package_digest,
    )
    payload = {
        "schema": manifest.schema,
        "version": manifest.version,
        "synthetic": manifest.synthetic,
        "mode": manifest.mode,
        "seed": manifest.seed,
        "case_count": manifest.case_count,
        "sample_count": manifest.sample_count,
        "rejected_count": manifest.rejected_count,
        "skipped": list(manifest.skipped),
        "owner_blind_gate": manifest.owner_blind_gate,
        "two_hour_silence_gate": manifest.two_hour_silence_gate,
        "files": [
            {"path": f.path, "byte_size": f.byte_size, "sha256": f.sha256}
            for f in manifest.files
        ],
        "package_digest": manifest.package_digest,
    }
    return _canonical_json_bytes(payload)


def _audit_blind_privacy(blind_bytes: bytes) -> None:
    """Fail-closed if the blind side leaks forbidden lexical/structural tokens."""
    text = blind_bytes.decode("utf-8")
    lowered = text.casefold()
    for token in _BLIND_FORBIDDEN_LEXICAL:
        if token.casefold() in lowered:
            raise SweepError("blind_leak", f"blind side contains forbidden token {token!r}")
    # Structural: blind JSONL must only carry sample_id / input / reply.
    for line in text.splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        allowed = {"sample_id", "input", "reply"}
        extra = set(obj.keys()) - allowed
        if extra:
            raise SweepError("blind_leak", f"blind sample has extra keys {extra}")


def _verify_sample_bijection(blind_bytes: bytes, answer_bytes: bytes) -> None:
    blind_ids: list[str] = []
    for line in blind_bytes.decode("utf-8").splitlines():
        if not line.strip():
            continue
        blind_ids.append(json.loads(line)["sample_id"])
    answer = json.loads(answer_bytes.decode("utf-8"))
    answer_ids = [entry["sample_id"] for entry in answer["answers"]]
    if len(blind_ids) != len(set(blind_ids)):
        raise SweepError("duplicate_sample_id", "blind sample_id set has duplicates")
    if len(answer_ids) != len(set(answer_ids)):
        raise SweepError("duplicate_sample_id", "answer sample_id set has duplicates")
    if set(blind_ids) != set(answer_ids):
        raise SweepError("sample_mismatch", "blind and answer sample_id sets differ")


# ---------------------------------------------------------------------------
# safe atomic directory publisher
# ---------------------------------------------------------------------------


def _is_safe_symlink(path: str) -> bool:
    try:
        return os.path.islink(path)
    except (OSError, ValueError):
        return False


def _validate_output_path(output_path: str) -> None:
    if not isinstance(output_path, str) or not output_path:
        raise SweepError("invalid_output", "output path must be a non-empty string")
    # Reject lexical traversal in the raw input before normpath collapses it.
    raw_parts = output_path.replace("\\", "/").split("/")
    if any(part == ".." for part in raw_parts):
        raise SweepError("invalid_output", "output path must not contain '..'")
    norm = os.path.normpath(output_path)
    if os.path.isabs(norm):
        # Allow absolute paths but still reject dangerous roots below.
        pass
    # Reject lexical traversal in the normalized path too.
    parts = norm.replace("\\", "/").split("/")
    if any(part == ".." for part in parts):
        raise SweepError("invalid_output", "output path must not contain '..'")
    # Reject root / current dir / home.
    if norm in ("", "."):
        raise SweepError("invalid_output", "output path must not be root or cwd")
    home = os.path.expanduser("~")
    if os.path.abspath(norm) == os.path.abspath(home):
        raise SweepError("invalid_output", "output path must not be home directory")
    if os.path.abspath(norm) == os.path.abspath(os.getcwd()):
        raise SweepError("invalid_output", "output path must not be cwd")
    # Target must not already exist.
    if os.path.exists(norm) or os.path.islink(norm):
        raise SweepError("output_exists", f"output path already exists: {norm}")
    parent = os.path.dirname(norm)
    if not parent:
        raise SweepError("invalid_output", "output path has no parent directory")
    if not os.path.isdir(parent):
        raise SweepError("invalid_output", f"parent directory does not exist: {parent}")
    if _is_safe_symlink(parent):
        raise SweepError("invalid_output", "parent directory is a symlink")
    # Resolve parent and ensure target resolves under a real directory.
    parent_real = os.path.realpath(parent)
    if _is_safe_symlink(parent_real):
        raise SweepError("invalid_output", "resolved parent is a symlink")
    # Reject if target itself resolves to something dangerous.
    target_real = os.path.realpath(norm)
    if os.path.exists(target_real) or os.path.islink(target_real):
        raise SweepError("output_exists", f"resolved output already exists: {target_real}")


def publish_package(
    *,
    result: SweepResult,
    output_path: str,
) -> PackageManifest:
    """Serialize, verify, and atomically publish a sweep package.

    Writes blind/answer/manifest into a sibling staging directory, flushes,
    computes and re-reads checksums, then ``os.replace``-publishes the formal
    directory.  On any failure the staging directory is removed and the formal
    directory is never created.
    """
    _validate_output_path(output_path)
    norm = os.path.normpath(output_path)
    parent = os.path.dirname(norm)
    leaf = os.path.basename(norm)
    staging = os.path.join(parent, f".{leaf}.staging-{os.getpid()}")

    # Clean any stale staging (defensive; should not exist).
    if os.path.exists(staging):
        shutil.rmtree(staging, ignore_errors=True)
    try:
        os.makedirs(staging, exist_ok=False)
        blind_dir = os.path.join(staging, "blind")
        answer_dir = os.path.join(staging, "answer")
        os.makedirs(blind_dir, exist_ok=False)
        os.makedirs(answer_dir, exist_ok=False)

        blind_bytes = _serialize_blind_samples(result.cases, result.message)
        answer_bytes = _serialize_answer_key(result)

        _audit_blind_privacy(blind_bytes)
        _verify_sample_bijection(blind_bytes, answer_bytes)

        blind_path = os.path.join(blind_dir, "samples.jsonl")
        answer_path = os.path.join(answer_dir, "answer-key.json")
        _write_and_flush(blind_path, blind_bytes)
        _write_and_flush(answer_path, answer_bytes)

        blind_manifest = _file_manifest(blind_path, "blind/samples.jsonl", blind_bytes)
        answer_manifest = _file_manifest(answer_path, "answer/answer-key.json", answer_bytes)

        manifest_bytes = _serialize_manifest(
            result=result,
            blind_manifest=blind_manifest,
            answer_manifest=answer_manifest,
        )
        manifest_path = os.path.join(staging, "manifest.json")
        _write_and_flush(manifest_path, manifest_bytes)

        # Re-read and verify all managed files.
        _verify_file(blind_path, blind_manifest)
        _verify_file(answer_path, answer_manifest)
        _verify_file(manifest_path, _file_manifest(manifest_path, "manifest.json", manifest_bytes))

        # Atomic publish on the same filesystem.
        os.replace(staging, norm)
    except BaseException:
        if os.path.exists(staging):
            shutil.rmtree(staging, ignore_errors=True)
        if os.path.exists(norm):
            # We never created norm ourselves; do not touch user dirs.
            pass
        raise

    # Re-read published files and return the manifest.
    published_manifest_path = os.path.join(norm, "manifest.json")
    with open(published_manifest_path, "rb") as fh:
        manifest_payload = json.loads(fh.read().decode("utf-8"))
    files = tuple(
        FileManifest(path=f["path"], byte_size=f["byte_size"], sha256=f["sha256"])
        for f in manifest_payload["files"]
    )
    return PackageManifest(
        schema=manifest_payload["schema"],
        version=manifest_payload["version"],
        synthetic=manifest_payload["synthetic"],
        mode=manifest_payload["mode"],
        seed=manifest_payload["seed"],
        case_count=manifest_payload["case_count"],
        sample_count=manifest_payload["sample_count"],
        rejected_count=manifest_payload["rejected_count"],
        skipped=tuple(manifest_payload["skipped"]),
        owner_blind_gate=manifest_payload["owner_blind_gate"],
        two_hour_silence_gate=manifest_payload["two_hour_silence_gate"],
        files=files,
        package_digest=manifest_payload["package_digest"],
    )


def _write_and_flush(path: str, data: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(data)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass


def _file_manifest(abs_path: str, rel_path: str, expected_bytes: bytes) -> FileManifest:
    return FileManifest(
        path=rel_path.replace(os.sep, "/"),
        byte_size=len(expected_bytes),
        sha256=_sha256_bytes(expected_bytes),
    )


def _verify_file(abs_path: str, manifest: FileManifest) -> None:
    with open(abs_path, "rb") as fh:
        data = fh.read()
    if len(data) != manifest.byte_size:
        raise SweepError(
            "verify_size",
            f"{manifest.path}: size {len(data)} != expected {manifest.byte_size}",
        )
    digest = _sha256_bytes(data)
    if digest != manifest.sha256:
        raise SweepError(
            "verify_hash",
            f"{manifest.path}: hash mismatch",
        )
