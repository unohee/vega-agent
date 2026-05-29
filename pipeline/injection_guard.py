# Created: 2026-05-27
# Purpose: Guardrail against indirect prompt injection via tool results
# Dependencies: re (stdlib)
#
# Detects and neutralizes injection patterns in external data
# (web_search, web_fetch, file_read, MCP responses) before they enter LLM context.
#
# Detection: pattern matching (deterministic, no LLM needed)
# Neutralization: replace matched spans with [INJECTION_BLOCKED] — context flow preserved

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# ── Injection pattern list ────────────────────────────────────────────────────
# Based on real-world cases (INJECAGENT benchmark, CVE-2025-49150, PromptInject paper)

_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Classic override
    (re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|context)", re.I), "instruction_override"),
    (re.compile(r"disregard\s+(all\s+)?(previous|prior)\s+", re.I), "instruction_override"),
    (re.compile(r"forget\s+(everything|all)\s+(above|previous|you.ve\s+been\s+told)", re.I), "instruction_override"),

    # Forced role/persona change
    (re.compile(r"you\s+are\s+now\s+(a\s+|an\s+)?(?!VEGA)", re.I), "persona_hijack"),
    (re.compile(r"your\s+new\s+(role|persona|instructions?|task)\s+(is|are)\s*:", re.I), "persona_hijack"),
    (re.compile(r"act\s+as\s+(a\s+|an\s+)?(?!(assistant|VEGA))\w+", re.I), "persona_hijack"),

    # System prompt exfiltration
    (re.compile(r"(print|output|reveal|show|repeat|display|share|leak)\s+(your\s+)?(system\s+prompt|instructions?|initial\s+prompt|original\s+instructions?|prompt)", re.I), "exfiltration"),
    (re.compile(r"what\s+(are|were)\s+your\s+(original\s+)?(instructions?|system\s+prompt)", re.I), "exfiltration"),
    (re.compile(r"reveal\s+(your\s+)?(original|initial|previous|prior|hidden)\s+", re.I), "exfiltration"),

    # Forced tool execution
    (re.compile(r"(immediately|now)\s+(call|invoke|execute|run)\s+(the\s+)?\w+\s*(tool|function|command)", re.I), "forced_tool_call"),
    (re.compile(r"use\s+the\s+(bash_exec|host_exec|python_exec|file_delete|gmail_send|discord_send)\s+tool", re.I), "forced_tool_call"),

    # HTML/XML tag injection (MCP tool poisoning vector)
    (re.compile(r"<\s*system\s*>", re.I), "tag_injection"),
    (re.compile(r"<\s*/?instructions?\s*>", re.I), "tag_injection"),
    (re.compile(r"<\s*OVERRIDE\s*>", re.I), "tag_injection"),

    # Credential/secret exfiltration
    (re.compile(r"(send|exfiltrate|leak|transmit)\s+.*(api.?key|token|credential|password|secret)", re.I), "credential_theft"),
    (re.compile(r"(read|cat|print|output)\s+.*\.(env|pem|key|p12)", re.I), "credential_theft"),
]

# Tools that return external data — only their results are scanned
_EXTERNAL_DATA_TOOLS: frozenset[str] = frozenset({
    "web_search",
    "web_fetch",
    "file_read",
    "gmail_search",
    "gmail_read",
    "drive_read",
    "drive_search",
})


def scan_text(text: str) -> list[dict]:
    """Scan text for injection patterns. Returns list of findings (empty if none)."""
    findings = []
    for pattern, kind in _PATTERNS:
        m = pattern.search(text)
        if m:
            findings.append({
                "kind": kind,
                "match": m.group(0)[:80],
                "pos": m.start(),
            })
    return findings


def neutralize(text: str) -> tuple[str, list[dict]]:
    """Replace injection patterns with [INJECTION_BLOCKED: <kind>].
    Returns: (neutralized text, list of detected patterns)"""
    findings = []
    result = text
    for pattern, kind in _PATTERNS:
        def _replace(m: re.Match, _kind: str = kind) -> str:
            findings.append({"kind": _kind, "match": m.group(0)[:80]})
            return f"[INJECTION_BLOCKED: {_kind}]"
        result = pattern.sub(_replace, result)
    return result, findings


def guard_tool_result(tool_name: str, result_json: str) -> str:
    """Apply guardrail to the JSON result string from dispatch_tool.

    For external data tools or MCP tools, scans and neutralizes text fields in the result.
    Logs a warning and returns neutralized result when injection is detected.
    """
    from pipeline.mcp_client import is_mcp_tool
    is_external = tool_name in _EXTERNAL_DATA_TOOLS or is_mcp_tool(tool_name)
    if not is_external:
        return result_json

    try:
        data = json.loads(result_json)
    except (json.JSONDecodeError, ValueError):
        # Non-JSON: process directly as text
        cleaned, findings = neutralize(result_json)
        if findings:
            logger.warning(f"[InjectionGuard] {tool_name}: blocked {len(findings)} pattern(s) — {[f['kind'] for f in findings]}")
        return cleaned

    dirty, data = _neutralize_dict(data, tool_name)
    if dirty:
        return json.dumps(data, ensure_ascii=False)
    return result_json


def _neutralize_dict(obj: object, tool_name: str) -> tuple[bool, object]:
    """Recursively neutralize injection patterns in dict/list/str. Returns (changed, result)."""
    if isinstance(obj, str):
        cleaned, findings = neutralize(obj)
        if findings:
            logger.warning(
                f"[InjectionGuard] {tool_name}: "
                f"blocked {[f['kind'] for f in findings]} — "
                f"original: {findings[0]['match']!r}"
            )
            return True, cleaned
        return False, obj
    if isinstance(obj, dict):
        dirty = False
        result: dict = {}
        for k, v in obj.items():
            changed, new_v = _neutralize_dict(v, tool_name)
            result[k] = new_v
            if changed:
                dirty = True
        return dirty, result
    if isinstance(obj, list):
        dirty = False
        result_list: list = []
        for item in obj:
            changed, new_item = _neutralize_dict(item, tool_name)
            result_list.append(new_item)
            if changed:
                dirty = True
        return dirty, result_list
    return False, obj
