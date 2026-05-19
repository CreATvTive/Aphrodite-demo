from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import json
import os
import re
import subprocess
import threading
import time
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .codex_delegate import CodexDelegateClient, load_codex_delegate_config
# TODO: P40 — replace with ds_client after experiment
# from .glm_client import GLMClient


@dataclass
class DebugRound:
    round_id: int
    error: str
    applied: bool
    note: str = ""


@dataclass
class DebugResult:
    ok: bool
    file_path: str
    message: str
    rounds: List[DebugRound] = field(default_factory=list)
    changed: bool = False
    applied_rounds: int = 0
    classification: str = "failed"
    skip_reason: str = ""


_COMPILE_CACHE_LOCK = threading.Lock()
_COMPILE_CACHE: Dict[Tuple[str, int, int], Tuple[bool, str]] = {}
_COMPILE_CACHE_MAX = 1024
_CODEX_PATCH_CLIENT_LOCK = threading.Lock()
_CODEX_PATCH_CLIENT: Optional[CodexDelegateClient] = None


def selfcheck_python_target(target: str) -> Tuple[bool, str]:
    path = Path(target).resolve()
    if not path.exists():
        return False, f"target not found: {target}"

    py_files: List[Path] = []
    if path.is_file():
        if path.suffix.lower() != ".py":
            return False, "target is not a python file"
        py_files = [path]
    else:
        py_files = sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)
        if not py_files:
            return True, "no python files found"

    bad: List[str] = []
    for f in py_files:
        ok, err = _compile_python_file(f)
        if not ok:
            bad.append(f"{f}: {err}")
    if bad:
        return False, "\n".join(bad[:20])
    return True, f"selfcheck passed, files={len(py_files)}"


def auto_debug_python_file(
    file_path: str,
    max_rounds: int = 2,
    *,
    error_context: str = "",
    verify_command: str = "",
    cwd: str = "",
) -> DebugResult:
    p = Path(file_path).resolve()
    if not p.exists():
        return DebugResult(ok=False, file_path=str(p), message="file not found", classification="failed")
    if p.suffix.lower() != ".py":
        return DebugResult(ok=False, file_path=str(p), message="only .py file is supported", classification="failed")

    original = _safe_read_text(p)
    if original is None:
        return DebugResult(ok=False, file_path=str(p), message="cannot read file", classification="failed")

    rounds: List[DebugRound] = []
    ext_err = str(error_context or "").strip()
    has_error_context = bool(ext_err)
    has_external_runtime_error = _looks_like_runtime_error(ext_err)
    error_mentions_target = _error_mentions_file(ext_err, file_path=str(p)) if has_error_context else False
    verify_cmd = str(verify_command or "").strip()
    workdir = str(cwd or p.parent)
    backup_written = False

    ok_compile, compile_err = _compile_python_file(p)
    verify_ok, verify_err = (True, "")
    if ok_compile and verify_cmd:
        verify_ok, verify_err = _run_verify_command(verify_cmd, cwd=workdir)

    # Only allow zero-round noop when there is no external diagnostic context.
    if ok_compile and verify_ok and (not has_error_context):
        return DebugResult(
            ok=True,
            file_path=str(p),
            message="autodebug success in 0 round(s)",
            rounds=rounds,
            changed=False,
            applied_rounds=0,
            classification="noop",
        )

    # External diagnostics exist but do not point to this file: explicitly skip.
    if ok_compile and verify_ok and has_error_context and (not error_mentions_target):
        return DebugResult(
            ok=False,
            file_path=str(p),
            message="autodebug skipped: error does not point to target file",
            rounds=rounds,
            changed=False,
            applied_rounds=0,
            classification="skipped",
            skip_reason="error not for target",
        )

    if ok_compile and verify_ok and has_external_runtime_error and not verify_cmd:
        if not error_mentions_target:
            return DebugResult(
                ok=False,
                file_path=str(p),
                message="autodebug skipped: runtime error does not point to target file",
                rounds=rounds,
                changed=False,
                applied_rounds=0,
                classification="skipped",
                skip_reason="error not for target",
            )
    if (not ok_compile) and has_error_context and error_mentions_target:
        effective_err = f"{compile_err}\nexternal diagnostics:\n{ext_err[-4000:]}".strip()
    elif ok_compile and (not verify_ok) and has_error_context and error_mentions_target:
        effective_err = f"{verify_err}\nexternal diagnostics:\n{ext_err[-4000:]}".strip()
    else:
        effective_err = compile_err if not ok_compile else (verify_err if not verify_ok else ext_err)

    for i in range(1, max(1, int(max_rounds)) + 1):
        source = _safe_read_text(p) or ""
        focus = _extract_focus_from_error(source=source, error_text=effective_err, file_path=str(p))
        patched = _request_patch_from_model(
            source=source,
            error_text=effective_err,
            focus_hint=focus.get("hint", ""),
            focus_snippet=focus.get("snippet", ""),
        )
        if not patched:
            rounds.append(DebugRound(round_id=i, error=effective_err, applied=False, note="model output invalid"))
            break
        if patched == source:
            rounds.append(DebugRound(round_id=i, error=effective_err, applied=False, note="model returned unchanged code"))
            effective_err = f"{effective_err}\nmodel returned unchanged code".strip()
            continue

        # verify candidate before writing
        valid, candidate_err = _compile_python_code(patched, filename=str(p))
        if not valid:
            rounds.append(
                DebugRound(
                    round_id=i,
                    error=effective_err,
                    applied=False,
                    note=f"candidate invalid: {candidate_err}",
                )
            )
            effective_err = f"{effective_err}\ncandidate invalid: {candidate_err}"
            continue

        if not backup_written:
            _safe_backup(path=p, original=original)
            backup_written = True
        if not _safe_write_text(p, patched):
            rounds.append(DebugRound(round_id=i, error=effective_err, applied=False, note="write failed"))
            break
        round_note = "patch applied"
        if ok_compile and not verify_cmd and has_external_runtime_error:
            round_note = "patch applied (runtime verification skipped)"
        rounds.append(DebugRound(round_id=i, error=effective_err, applied=True, note=round_note))
        # External runtime log is stale after one attempted patch.
        has_external_runtime_error = False
        ext_err = ""

        ok_compile, compile_err = _compile_python_file(p)
        verify_ok, verify_err = (True, "")
        if ok_compile and verify_cmd:
            verify_ok, verify_err = _run_verify_command(verify_cmd, cwd=workdir)
        if ok_compile and verify_ok:
            applied_rounds = sum(1 for r in rounds if bool(r.applied))
            return DebugResult(
                ok=True,
                file_path=str(p),
                message=f"autodebug success in {i} round(s)",
                rounds=rounds,
                changed=True,
                applied_rounds=applied_rounds,
                classification="patched",
            )
        effective_err = compile_err if not ok_compile else verify_err

    current_text = _safe_read_text(p)
    changed_now = False
    if current_text is not None and current_text != original:
        _safe_write_text(p, original)
        final_text = _safe_read_text(p)
        changed_now = bool(final_text is not None and final_text != original)
    tail_err = str(effective_err or "unknown error").strip()
    return DebugResult(
        ok=False,
        file_path=str(p),
        message=f"autodebug failed: {tail_err}",
        rounds=rounds,
        changed=changed_now,
        applied_rounds=sum(1 for r in rounds if bool(r.applied)),
        classification="failed",
    )


def _compile_python_file(path: Path) -> Tuple[bool, str]:
    try:
        st = path.stat()
        key = (str(path.resolve()), int(st.st_mtime_ns), int(st.st_size))
    except Exception:
        key = None

    if key is not None:
        with _COMPILE_CACHE_LOCK:
            cached = _COMPILE_CACHE.get(key)
        if cached is not None:
            return cached

    try:
        with tokenize.open(str(path)) as f:
            source = f.read()
        compile(source, str(path), "exec")
        result = (True, "")
    except Exception as e:
        result = (False, f"{type(e).__name__}: {e}")

    if key is not None:
        with _COMPILE_CACHE_LOCK:
            _COMPILE_CACHE[key] = result
            if len(_COMPILE_CACHE) > _COMPILE_CACHE_MAX:
                _COMPILE_CACHE.pop(next(iter(_COMPILE_CACHE)))
    return result


def _compile_python_code(code: str, filename: str) -> Tuple[bool, str]:
    try:
        compile(code, filename, "exec")
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _request_patch_from_model(source: str, error_text: str, focus_hint: str = "", focus_snippet: str = "") -> Optional[str]:
    system = (
        "You are a Python auto-debugger. Fix code to resolve the given compile/runtime error. "
        "Return strict JSON with keys: patched_code (string), reason (string)."
    )
    user = {
        "error": error_text[-4000:],
        "source_code": source[-20000:],
        "constraints": [
            "keep behavior unchanged except bug fix",
            "do not add placeholders",
            "return full file content in patched_code",
        ],
        "focus_hint": str(focus_hint or "")[:400],
        "focus_snippet": str(focus_snippet or "")[:3000],
    }
    if _env_bool("AUTODEBUG_CODEX_ONLY", True):
        return _request_patch_from_codex(system=system, user=user)
    if _env_bool("AUTODEBUG_PARALLEL_MODELS", True):
        patched = _request_patch_parallel(system=system, user=user)
        if patched:
            return patched
    patched_codex = _request_patch_from_codex(system=system, user=user)
    if patched_codex:
        return patched_codex
    return _request_patch_from_glm(system=system, user=user)


def _request_patch_parallel(system: str, user: Dict[str, Any]) -> Optional[str]:
    timeout_sec = _env_float("AUTODEBUG_PARALLEL_TIMEOUT_SEC", 30.0)
    deadline = time.time() + max(1.0, float(timeout_sec))
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="autodebug-model")
    pending: set[Future[Optional[str]]] = set()
    try:
        pending.add(executor.submit(_request_patch_from_codex, system=system, user=user))
        pending.add(executor.submit(_request_patch_from_glm, system=system, user=user))
        while pending and time.time() < deadline:
            remain = max(0.05, deadline - time.time())
            done, pending = wait(pending, timeout=remain, return_when=FIRST_COMPLETED)
            for fut in done:
                try:
                    patched = fut.result()
                except Exception:
                    patched = None
                if isinstance(patched, str) and patched.strip():
                    for p in pending:
                        p.cancel()
                    return patched
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return None


def _request_patch_from_codex(system: str, user: Dict[str, Any]) -> Optional[str]:
    codex_obj = _request_json_from_codex(system=system, user=user)
    if not isinstance(codex_obj, dict):
        return None
    patched = codex_obj.get("patched_code")
    if not isinstance(patched, str) or not patched.strip():
        return None
    return patched


def _request_patch_from_glm(system: str, user: Dict[str, Any]) -> Optional[str]:
    client = GLMClient()
    try:
        raw = client.chat(
            messages=[
                {"role": "system", "content": str(system or "")},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            temperature=0.1,
        )
    except Exception:
        return None
    obj = _extract_json(raw)
    if not obj:
        return None
    patched = obj.get("patched_code")
    if not isinstance(patched, str) or not patched.strip():
        return None
    return patched


def _request_json_from_codex(system: str, user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    client = _get_codex_patch_client()
    return client.try_chat_json(
        system=str(system or "").strip(),
        user_payload=dict(user or {}),
        temperature=0.1,
        max_tokens=2200,
    )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)


def _get_codex_patch_client() -> CodexDelegateClient:
    global _CODEX_PATCH_CLIENT
    with _CODEX_PATCH_CLIENT_LOCK:
        if _CODEX_PATCH_CLIENT is None:
            _CODEX_PATCH_CLIENT = CodexDelegateClient(load_codex_delegate_config())
        return _CODEX_PATCH_CLIENT


def _run_verify_command(command: str, cwd: str) -> Tuple[bool, str]:
    cmd = str(command or "").strip()
    if not cmd:
        return True, ""
    try:
        cp = subprocess.run(
            cmd,
            shell=True,
            cwd=str(cwd or os.getcwd()),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    if cp.returncode == 0:
        return True, ""
    out = ((cp.stdout or "") + "\n" + (cp.stderr or "")).strip()
    tail = "\n".join(out.splitlines()[-20:]) if out else ""
    return False, f"verify_command rc={cp.returncode}\n{tail}".strip()


def _looks_like_runtime_error(error_text: str) -> bool:
    t = str(error_text or "").lower()
    if not t:
        return False
    keys = [
        "traceback",
        "exception",
        "error",
        "failed",
        "assertionerror",
        "nameerror",
        "typeerror",
        "valueerror",
        "indexerror",
        "keyerror",
        "attributeerror",
        "runtimeerror",
    ]
    return any(k in t for k in keys)


def _error_mentions_file(error_text: str, file_path: str) -> bool:
    err = str(error_text or "").lower().replace("\\", "/")
    if not err:
        return False
    path = str(file_path or "").lower().replace("\\", "/")
    base = os.path.basename(path)
    stem = os.path.splitext(base)[0]
    if path and path in err:
        return True
    if base and base in err:
        return True
    if stem and re.search(rf"\b{re.escape(stem)}\.py\b", err):
        return True
    return False


def _extract_focus_from_error(source: str, error_text: str, file_path: str) -> Dict[str, str]:
    src = str(source or "")
    err = str(error_text or "")
    hint = ""
    snippet = ""
    line_no = _extract_line_no(err, file_path=file_path)
    if line_no and src:
        lines = src.splitlines()
        lo = max(1, int(line_no) - 25)
        hi = min(len(lines), int(line_no) + 25)
        chunk = []
        for i in range(lo, hi + 1):
            chunk.append(f"{i:4d}: {lines[i - 1]}")
        snippet = "\n".join(chunk)
        hint = f"focus_line={line_no}; range={lo}-{hi}"
    return {"hint": hint, "snippet": snippet}


def _extract_line_no(error_text: str, file_path: str) -> int:
    t = str(error_text or "")
    if not t:
        return 0
    fp = re.escape(str(file_path or ""))
    if fp:
        m = re.search(rf"{fp}.*?line\s+(\d+)", t, flags=re.IGNORECASE)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return 0
    m2 = re.search(r"line\s+(\d+)", t, flags=re.IGNORECASE)
    if not m2:
        return 0
    try:
        return int(m2.group(1))
    except Exception:
        return 0


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    t = str(text or "").strip()
    if not t:
        return None
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", t)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        if isinstance(obj, dict):
            return obj
    except Exception:
        return None
    return None


def _safe_read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _safe_write_text(path: Path, text: str) -> bool:
    try:
        path.write_text(text, encoding="utf-8")
        return True
    except Exception:
        return False


def _safe_backup(path: Path, original: str) -> None:
    backup = path.with_suffix(path.suffix + ".bak")
    try:
        backup.write_text(str(original or ""), encoding="utf-8")
    except Exception:
        pass
