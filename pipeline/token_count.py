# Created: 2026-05-26
# Purpose: Accurate token counting for strings and message lists.
# Per-model tokenizer differences are small, so cl100k_base (GPT-4/Claude compatible approximation) is used uniformly.

from __future__ import annotations

import json as _json
from typing import Any

_ENC = None
_ENC_TRIED = False


def _get_encoder():
    """tiktoken cl100k_base instance. Loaded once and cached."""
    global _ENC, _ENC_TRIED
    if _ENC is not None or _ENC_TRIED:
        return _ENC
    _ENC_TRIED = True
    try:
        import tiktoken
        _ENC = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _ENC = None
    return _ENC


def count_tokens(text: str) -> int:
    """Token count for a string.
    Uses cl100k_base via tiktoken if available; otherwise falls back to a Korean/English heuristic (0.78 tokens/char for Korean)."""
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    # Fallback: Korean/English mixed heuristic
    # High Korean char ratio (U+AC00-D7A3 + Hangul jamo) → 0.78 tokens/char; English → 0.25
    hangul = sum(1 for c in text if "가" <= c <= "힣" or "ㄱ" <= c <= "ㆎ")
    kor_ratio = hangul / max(1, len(text))
    rate = 0.78 * kor_ratio + 0.27 * (1 - kor_ratio)
    return int(len(text) * rate)


def count_message_tokens(messages: list[dict]) -> int:
    """Sum of tokens across a ChatCompletions/Responses messages list."""
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += count_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += count_tokens(part.get("text") or "")
        # role/structure overhead is approximately 4 tokens
        total += 4
    return total


def count_json_tokens(obj: Any) -> int:
    """Token count for a JSON-serializable object (e.g. tool schemas)."""
    try:
        return count_tokens(_json.dumps(obj, ensure_ascii=False))
    except Exception:
        return 0
