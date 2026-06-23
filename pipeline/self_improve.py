# Created: 2026-05-20
# Purpose: VEGA semi-autonomous self-improvement — detect tool failures → generate patch → sandbox verify → user approval
# Dependencies: pipeline.sandbox, pipeline.streaming (GPT calls), pipeline.tools
# Test Status: under review

from __future__ import annotations

import asyncio
import inspect
import json
import textwrap
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

# ── Failure log ───────────────────────────────────────────────────────────────
# key: tool_name → [{"ts", "error", "args_summary"}]
_FAILURE_LOG: dict[str, list[dict]] = {}
_CONSECUTIVE_THRESHOLD = 2   # trigger improvement after N consecutive failures
_MAX_FAILURES_KEPT = 10      # max records kept per tool

# ── Guardrail constants ────────────────────────────────────────────────────────
# Core pipeline files must never be patch targets
_PROTECTED_TOOLS = frozenset({
    "gmail_send", "calendar_create_event", "calendar_delete_event",
    "calendar_update_event", "imessage_send",             # external tools with side effects
    "bash_exec", "python_exec", "host_exec",              # code execution tools (recursion risk)
    "sandbox_save_module", "sandbox_list_skills",         # improvement infrastructure itself
    "memory_persona_update", "memory_event_add",          # memory integrity
})

# Patterns disallowed in patch code
_BLOCKED_PATTERNS = [
    "os.system", "subprocess", "__import__", "open(",
    "exec(", "eval(", "importlib", "sys.path",  # cxt-ignore: security
    "socket", "urllib", "requests", "httpx",     # network (also blocked inside sandbox)
]


def record_failure(tool_name: str, error: str, args: dict) -> None:
    """Called on dispatch_tool failure. Tracks consecutive failure count."""
    log = _FAILURE_LOG.setdefault(tool_name, [])
    log.append({
        "ts": datetime.now(KST).isoformat(),
        "error": error[:300],
        "args_summary": json.dumps({k: str(v)[:80] for k, v in args.items()}, ensure_ascii=False)[:200],
    })
    # Trim old records
    if len(log) > _MAX_FAILURES_KEPT:
        _FAILURE_LOG[tool_name] = log[-_MAX_FAILURES_KEPT:]


def should_improve(tool_name: str) -> bool:
    """Return True if the tool has failed at least _CONSECUTIVE_THRESHOLD times and is not protected."""
    if tool_name in _PROTECTED_TOOLS:
        return False
    log = _FAILURE_LOG.get(tool_name, [])
    if len(log) < _CONSECUTIVE_THRESHOLD:
        return False
    # Check that the most recent N entries are all failures
    recent = log[-_CONSECUTIVE_THRESHOLD:]
    return all("error" in e for e in recent)


def clear_failures(tool_name: str) -> None:
    """Reset the failure counter on tool success."""
    _FAILURE_LOG.pop(tool_name, None)


# ── Source retrieval ──────────────────────────────────────────────────────────

def _get_tool_source(tool_name: str) -> str | None:
    """
    Extract function source from TOOL_FUNCTIONS.
    For sandbox_call wrappers, includes the inner fn_body string.
    """
    from pipeline.tools import TOOL_FUNCTIONS
    fn = TOOL_FUNCTIONS.get(tool_name)
    if fn is None:
        return None
    try:
        return inspect.getsource(fn)
    except Exception:
        return f"# source unavailable: {fn}"


# ── Guardrail: code inspection ────────────────────────────────────────────────

def _check_patch_safety(code: str) -> list[str]:
    """Return list of blocked patterns found in patch code. Empty list means safe."""
    violations = []
    for pattern in _BLOCKED_PATTERNS:
        if pattern in code:
            violations.append(pattern)
    return violations


# ── Patch request to GPT ──────────────────────────────────────────────────────

async def _request_patch(tool_name: str, failures: list[dict], current_source: str) -> str | None:
    """
    Request a patch from GPT. Instructs it to return a single pure Python function.
    Returns: code string or None on failure.
    """
    import urllib.request
    from pipeline.auth.chatgpt import CODEX_BASE_URL, DEFAULT_CODEX_MODEL, _load_profile, ensure_valid_token

    failures_text = "\n".join(
        f"- [{f['ts']}] args={f['args_summary']} → {f['error']}" for f in failures
    )

    prompt = f"""Tool `{tool_name}` has repeatedly failed as follows:

{failures_text}

Current implementation:
```python
{current_source}
```

**Instructions:**
1. Analyze the cause of failure and write a corrected Python function.
2. Keep the function signature (name, parameters) unchanged.
3. Must return a dict.
4. External network, subprocess, os.system, eval, exec are forbidden.
5. Only packages installed in the sandbox may be used (openpyxl, python-docx, python-pptx, pandas, numpy, etc.).
6. Respond with only a code block (```python ... ```). No explanation needed."""

    profile = _load_profile()
    if not profile:
        return None

    try:
        access_token = await asyncio.get_event_loop().run_in_executor(None, ensure_valid_token)
    except Exception:
        return None

    payload = json.dumps({
        "model": DEFAULT_CODEX_MODEL,
        "instructions": "You are a Python expert. Respond with only a python code block.",
        "input": [{"role": "user", "content": prompt}],
        "store": False,
        "stream": False,
    }).encode()

    req = urllib.request.Request(
        CODEX_BASE_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
            "chatgpt-account-id": profile.get("account_id", ""),
            "OpenAI-Beta": "responses=experimental",
        },
        method="POST",
    )

    try:
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=30).read())
        resp = json.loads(raw)
        # Responses API: output[0].content[0].text
        text = ""
        for item in resp.get("output", []):
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    text += block.get("text", "")
        # Extract code block
        if "```python" in text:
            code = text.split("```python", 1)[1].split("```", 1)[0].strip()
        elif "```" in text:
            code = text.split("```", 1)[1].split("```", 1)[0].strip()
        else:
            code = text.strip()
        return code if code else None
    except Exception as e:
        print(f"[self_improve] GPT patch request failed: {e}", flush=True)
        return None


# ── Patch verification in sandbox ─────────────────────────────────────────────

def _improve_exec(code: str, timeout: int = 30) -> dict:
    """self_improve 코드 실행 — 호스트 우선, Docker 는 VEGA_USE_DOCKER opt-in 시만 (INT-1870).

    호스트 경로는 python_exec 가 워크스페이스 skills/ 를 PYTHONPATH 에 올려 저장된 패치
    모듈을 import 한다. 두 경로 모두 {stdout,stderr,returncode} 계약 동일."""
    try:
        from pipeline.sandbox import docker_enabled
        use_docker = docker_enabled()
    except Exception:
        use_docker = False
    if use_docker:
        from pipeline.sandbox import sandbox_python
        return sandbox_python(code, timeout=timeout)
    from pipeline.tools_code import python_exec
    return python_exec(code, timeout=timeout)


def _test_patch(tool_name: str, patch_code: str, test_args: dict) -> dict:
    """
    Verify patch code by running it (host-first; Docker only on VEGA_USE_DOCKER opt-in).
    test_args: original args from the failing call. Passes if re-run returns a dict without error.
    Returns: {"ok": bool, "output": str, "error": str}
    """
    args_repr = repr(test_args)
    test_code = f"""
import json

{patch_code}

# Run test
try:
    args = {args_repr}
    result = {tool_name}(**args)
    assert isinstance(result, dict), f"return value is not a dict: {{type(result)}}"
    assert "error" not in result or result.get("ok"), f"error returned: {{result}}"
    print(json.dumps({{"ok": True, "result": result}}, ensure_ascii=False, default=str))
except Exception as e:
    print(json.dumps({{"ok": False, "error": str(e)}}, ensure_ascii=False))
"""
    result = _improve_exec(test_code, timeout=30)
    stdout = result.get("stdout", "").strip()
    if result.get("returncode") != 0:
        return {"ok": False, "error": result.get("stderr", "")[:300]}
    # Parse the last JSON line from stdout
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                pass
    return {"ok": False, "error": f"결과 파싱 실패: {stdout[:200]}"}


# ── Improvement entry point ───────────────────────────────────────────────────

async def attempt_improvement(tool_name: str) -> dict | None:
    """
    Full tool improvement flow:
    1. Fetch source
    2. Request patch from GPT
    3. Guardrail check
    4. Sandbox test
    5. On pass, return improvement_pending payload (server exposes to UI)
    Returns None on failure.
    """
    failures = _FAILURE_LOG.get(tool_name, [])
    if not failures:
        return None

    source = _get_tool_source(tool_name)
    if source is None:
        return None

    print(f"[self_improve] attempting patch for {tool_name}…", flush=True)

    # Request patch from GPT
    patch_code = await _request_patch(tool_name, failures[-_CONSECUTIVE_THRESHOLD:], source)
    if not patch_code:
        return None

    # Guardrail: check for blocked patterns
    violations = _check_patch_safety(patch_code)
    if violations:
        print(f"[self_improve] {tool_name} patch rejected — blocked patterns: {violations}", flush=True)
        return None

    # Sandbox verification
    test_args = {}
    try:
        test_args = json.loads(failures[-1]["args_summary"]) if failures else {}
        if not isinstance(test_args, dict):
            test_args = {}
    except Exception:
        pass

    test_result = _test_patch(tool_name, patch_code, test_args)
    if not test_result.get("ok"):
        print(f"[self_improve] {tool_name} patch failed sandbox test: {test_result.get('error')}", flush=True)
        return None

    print(f"[self_improve] {tool_name} patch passed sandbox verification → awaiting user approval", flush=True)  # cxt-ignore: fake_execution

    # Generate unified diff (line-level)
    old_lines = source.strip().splitlines()
    new_lines = patch_code.strip().splitlines()
    import difflib
    diff = "\n".join(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"{tool_name} (current)",
        tofile=f"{tool_name} (patch)",
        lineterm="",
    ))

    return {
        "__improvement_pending__": True,
        "tool_name": tool_name,
        "patch_code": patch_code,
        "diff": diff,
        "failures": len(failures),
        "test_output": str(test_result.get("result", ""))[:200],
    }


# ── Patch application ─────────────────────────────────────────────────────────

def apply_patch(tool_name: str, patch_code: str) -> dict:
    """
    Apply an approved patch:
    1. Save patch_<tool>.py (host workspace skills/, or Docker /workspace/lib on opt-in)
    2. Replace function in TOOL_FUNCTIONS at runtime
    3. Clear failure log
    """
    from pipeline.tools import TOOL_FUNCTIONS

    # Re-check protection list (re-verify after approval)
    if tool_name in _PROTECTED_TOOLS:
        return {"ok": False, "error": "보호된 도구 — 패치 불가"}

    violations = _check_patch_safety(patch_code)
    if violations:
        return {"ok": False, "error": f"금지 패턴: {violations}"}

    # Save the patch module — host-first(워크스페이스 skills/), Docker opt-in 시 /workspace/lib (INT-1870)
    try:
        from pipeline.sandbox import docker_enabled
        use_docker = docker_enabled()
    except Exception:
        use_docker = False
    if use_docker:
        from pipeline.sandbox import sandbox_save_module as _save
        skills_path = "/workspace/lib"
    else:
        from pipeline.tools_code import _sandboxed_save_module as _save
        from pipeline.data_paths import workspace_dir
        skills_path = str(workspace_dir() / "skills")

    module_name = f"patch_{tool_name}"
    save_result = _save(module_name, patch_code)
    if not save_result.get("ok"):
        return {"ok": False, "error": f"모듈 저장 실패: {save_result}"}

    # Runtime replacement: re-run the saved module via the host-first executor
    def _patched_fn(**kwargs):
        code = f"""
import json, sys
sys.path.insert(0, {skills_path!r})
from {module_name} import {tool_name}
result = {tool_name}(**{repr(kwargs)})
print(json.dumps(result, ensure_ascii=False, default=str))
"""
        r = _improve_exec(code, timeout=30)
        stdout = r.get("stdout", "").strip()
        for line in reversed(stdout.splitlines()):
            if line.strip().startswith("{"):
                try:
                    return json.loads(line)
                except Exception:
                    pass
        if r.get("returncode") != 0:
            return {"error": r.get("stderr", "")[:300]}
        return {"error": f"결과 파싱 실패: {stdout[:200]}"}  # user-facing error string kept as-is

    _patched_fn.__name__ = tool_name
    TOOL_FUNCTIONS[tool_name] = _patched_fn

    clear_failures(tool_name)

    # Record improvement history
    _log_improvement(tool_name, patch_code)

    return {"ok": True, "tool": tool_name, "module": module_name}


def _log_improvement(tool_name: str, patch_code: str) -> None:
    """Append improvement record to VEGA data."""
    try:
        # 영속 데이터 루트에 기록 — 번들 상대경로(_MEIPASS)는 onefile 에서 읽기전용/임시.
        try:
            from pipeline.data_paths import data_dir
            log_path = data_dir() / "improvements.jsonl"
        except Exception:
            log_path = Path(__file__).parent.parent / "data" / "improvements.jsonl"
        record = {
            "ts": datetime.now(KST).isoformat(),
            "tool": tool_name,
            "patch_lines": len(patch_code.splitlines()),
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
