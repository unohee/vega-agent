# Created: 2026-06-01
# Purpose: 세션 제목·요약 자동생성. 연결된 활성 Provider(OpenRouter/Anthropic/OpenAI 등)로 처리.
# Dependencies: pipeline.streaming.stream_gpt, pipeline.llm_gateway

from __future__ import annotations

import asyncio
import json
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable


_TITLE_SYSTEM = """You are a session-title generator. Given a conversation, return ONLY a JSON object with these fields:
- title: 5 words or fewer, no punctuation (the session name shown in the UI)
- summary: 1-2 sentences describing what the conversation accomplished
- narrative: 2-3 sentences of richer context for future retrieval

Rules:
- Match the language the user spoke in (Korean conversation → Korean title/summary/narrative)
- title must be concise — it appears in a sidebar list
- Return ONLY valid JSON, no markdown fences, no extra text"""


@dataclass(frozen=True)
class GoogleFreshnessSource:
    """One Google incremental source owned by heartbeat orchestration."""

    name: str
    ingest: Callable[[str, Any], Any]
    load_cursor: Callable[[], Any] | None = None
    advance_cursor: Callable[[Any], None] | None = None


_GOOGLE_FRESHNESS_LOCK = threading.Lock()
_GOOGLE_FRESHNESS_SOURCES: tuple[GoogleFreshnessSource, ...] = ()


def _google_access_token() -> str | None:
    from pipeline.auth.google import ensure_valid_token

    return ensure_valid_token()


def run_google_freshness_sync(
    sources: list[GoogleFreshnessSource] | tuple[GoogleFreshnessSource, ...] | None = None,
    token_getter: Callable[[], str | None] | None = None,
) -> dict:
    """Run heartbeat-owned Google incremental freshness sync.

    No credentials/network are required when auth is absent: missing auth is a clean skip.
    Each source owns its cursor callbacks; heartbeat advances a cursor only after that
    source's ingest returns successfully.
    """
    if not _GOOGLE_FRESHNESS_LOCK.acquire(blocking=False):
        return {"ok": True, "skipped": "lock_held", "sources": []}

    try:
        get_token = token_getter or _google_access_token
        access_token = get_token()
        if not access_token:
            return {"ok": True, "skipped": "auth_missing", "sources": []}

        results = []
        ok = True
        for source in sources if sources is not None else _GOOGLE_FRESHNESS_SOURCES:
            cursor = source.load_cursor() if source.load_cursor else None
            try:
                ingest_result = source.ingest(access_token, cursor)
                if source.advance_cursor:
                    source.advance_cursor(ingest_result)
                results.append({"source": source.name, "ok": True})
            except Exception as e:
                ok = False
                results.append({"source": source.name, "ok": False, "error": str(e)})
        return {"ok": ok, "sources": results}
    finally:
        _GOOGLE_FRESHNESS_LOCK.release()


def _lms_title_session(messages: list[dict]) -> dict | None:
    """연결된 활성 Provider로 세션 제목·요약을 생성한다.

    server.py의 _auto_title_session이 run_in_executor로 동기 호출하므로
    내부에서 새 이벤트 루프를 만들어 stream_gpt를 실행한다.
    """
    if not messages:
        return None

    # 최근 10턴만 넘겨 토큰 낭비 방지
    recent = messages[-10:]
    prompt_lines = []
    for m in recent:
        role = "User" if m.get("role") in ("user", "human") else "Assistant"
        content = m.get("content", "")
        if isinstance(content, list):
            # 멀티모달 content 블록에서 텍스트만 추출
            content = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            )
        if content:
            prompt_lines.append(f"[{role}]: {content[:400]}")

    if not prompt_lines:
        return None

    conversation_text = "\n".join(prompt_lines)
    user_msg = f"Generate title/summary/narrative for this conversation:\n\n{conversation_text}"

    collected: dict[str, str] = {"text": ""}

    async def _run() -> None:
        from pipeline.streaming import stream_gpt

        async def on_token(tok: str) -> None:
            collected["text"] += tok

        await stream_gpt(
            messages=[{"role": "user", "content": user_msg}],
            system=_TITLE_SYSTEM,
            on_token=on_token,
            tier="cloud",
            ce_mode=False,
        )

    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_run())
    except Exception as e:
        print(f"[heartbeat] title generation failed: {e}")
        return None
    finally:
        loop.close()

    raw = collected["text"].strip()
    # 마크다운 펜스 제거 (모델이 규칙을 어길 경우 방어)
    raw = re.sub(r"^```[a-z]*\s*|\s*```$", "", raw, flags=re.DOTALL).strip()

    try:
        result = json.loads(raw)
        if isinstance(result, dict) and result.get("title"):
            return {
                "title": str(result.get("title", "")).strip(),
                "summary": str(result.get("summary", "")).strip(),
                "narrative": str(result.get("narrative", "")).strip(),
            }
    except Exception:
        pass

    # JSON 파싱 실패 시 raw 텍스트 첫 줄을 제목으로 사용
    fallback_title = raw.split("\n")[0][:50].strip()
    if fallback_title:
        return {"title": fallback_title, "summary": "", "narrative": ""}
    return None


def _save_session_digest(sid: str, title: str, summary: str, narrative: str) -> None:
    """세션 digest 저장. 현재는 title이 이미 rename_session으로 저장되므로 no-op."""
    pass


def get_recent_briefs(limit: int = 4) -> list[dict]:
    """일일 브리핑 목록. agent.db 분기에서는 미구현 — 빈 리스트 반환."""
    return []


def get_recent_narratives(limit: int = 7) -> list[dict]:
    """최근 narrative 목록. agent.db 분기에서는 미구현 — 빈 리스트 반환."""
    return []


_SUGGEST_SYSTEM = (
    "사용자의 최근 작업 세션 목록을 보고, 지금 이어서 하면 좋을 구체적이고 실행 가능한 "
    "할 일 3~5개를 제안한다. 추상적 조언이 아니라 바로 착수할 수 있는 행동이어야 한다.\n"
    "반드시 JSON 배열만 반환한다: [{\"title\": \"짧은 할 일(한 줄)\", \"reason\": \"왜 지금 하면 좋은지 한 문장\"}]\n"
    "규칙:\n"
    "- 마크다운 펜스·설명 없이 JSON 만 출력\n"
    "- 사용자가 쓴 언어(대부분 한국어)로 작성\n"
    "- title 은 8단어 이하"
)


def suggest_todos(_unused=None) -> list[dict]:
    """최근 작업 세션을 컨텍스트로 active provider 가 다음 할 일을 제안한다.

    위젯/대시보드 'VEGA 제안' 배너의 소스. Things 미연동이라 제안 표시 전용
    (수락→외부 트래커 연동은 없음). 컨텍스트(의미있는 최근 세션)가 없으면 빈
    리스트를 반환한다 → dashboard 가 "활동이 쌓이면 제안" 안내를 띄운다.

    tier="cloud" 로 호출 → llm_providers.json 의 tiers.cloud(없으면 active)로
    라우팅. 로컬 SLM 유무와 무관하게 동작한다(INT goal 2026-06-19 검증).
    반환: [{"title": str, "reason": str}] (최대 6개).
    """
    import asyncio
    import re

    try:
        from pipeline.session_store import list_sessions
        sessions = list_sessions(limit=12)
    except Exception as e:
        print(f"[heartbeat] suggest_todos: session load failed: {e}")
        return []

    # 기본 제목/거의 빈 세션 제외 — 의미있는 활동만 컨텍스트로
    meaningful = [
        s for s in sessions
        if s.get("name") and s["name"] != "VEGA 세션" and (s.get("msg_count") or 0) >= 2
    ]
    if not meaningful:
        return []

    ctx = "\n".join(
        f"- {s['name']} ({s.get('msg_count', 0)}개 메시지)" for s in meaningful[:10]
    )
    user_msg = f"최근 작업 세션:\n{ctx}\n\n이어서 하면 좋을 할 일을 제안해줘."

    collected = {"text": ""}

    async def _run() -> None:
        from pipeline.streaming import stream_gpt

        async def on_token(tok: str) -> None:
            collected["text"] += tok

        await stream_gpt(
            messages=[{"role": "user", "content": user_msg}],
            system=_SUGGEST_SYSTEM,
            on_token=on_token,
            tier="cloud",
            ce_mode=False,
        )

    loop = None
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_run())
    except Exception as e:
        print(f"[heartbeat] suggest_todos: LLM call failed: {e}")
        return []
    finally:
        if loop is not None:
            loop.close()

    raw = re.sub(r"^```[a-z]*\s*|\s*```$", "", collected["text"].strip(), flags=re.DOTALL).strip()
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for s in data:
        if isinstance(s, dict) and s.get("title"):
            out.append({
                "title": str(s["title"]).strip(),
                "reason": str(s.get("reason", "")).strip(),
            })
    return out[:6]

# --- Google freshness heartbeat sync (incremental orchestration) ---
import contextlib
import fcntl
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Any


_GOOGLE_SYNC_THREAD_LOCK = threading.Lock()
_GOOGLE_SYNC_DELAY_SEC = float(os.environ.get("VEGA_GOOGLE_HEARTBEAT_DELAY_SEC", "2.0"))
_GOOGLE_SYNC_MIN_INTERVAL_SEC = float(os.environ.get("VEGA_GOOGLE_HEARTBEAT_MIN_INTERVAL_SEC", "300"))
_GOOGLE_SYNC_LAST_RUN = 0.0


@dataclass(frozen=True)
class _GoogleSource:
    name: str
    read_cursor: Callable[[str], dict[str, Any] | None]
    write_cursor: Callable[[str, dict[str, Any]], None]
    ingest: Callable[[dict[str, Any] | None], dict[str, Any] | None]


def _google_sync_lock_path() -> Path:
    try:
        from pipeline.data_paths import data_dir

        base = Path(data_dir())
    except Exception:
        base = Path(os.environ.get("VEGA_DATA_DIR", ".vega"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "google_heartbeat_sync.lock"


@contextlib.contextmanager
def _google_sync_nonblocking_lock():
    if not _GOOGLE_SYNC_THREAD_LOCK.acquire(blocking=False):
        print("[heartbeat][google] skip: incremental sync already running in this process")
        yield False
        return

    lock_file = None
    try:
        lock_file = open(_google_sync_lock_path(), "a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("[heartbeat][google] skip: incremental sync already running in another process")
            yield False
            return
        yield True
    finally:
        if lock_file is not None:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                lock_file.close()
        _GOOGLE_SYNC_THREAD_LOCK.release()


def _google_auth_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return (
        "invalid_grant" in text
        or "reauth" in text
        or "re-auth" in text
        or "refresh_token expired" in text
        or "refresh token expired" in text
        or "google oauth access token unavailable" in text
        or "설정" in text and "google" in text
    )


def _check_google_auth_for_heartbeat() -> bool:
    try:
        from pipeline.auth import google as google_auth

        if not google_auth.stored_refresh_token():
            print("[heartbeat][google] skip: no stored Google refresh token; connect Google first")
            return False
        token = google_auth.get_access_token()
        if not token:
            print("[heartbeat][google] skip: Google auth unavailable or OAuth client not configured")
            return False
        return True
    except Exception as exc:
        if _google_auth_error(exc):
            print(f"[heartbeat][google] skip: Google auth requires reauth ({exc})")
            return False
        print(f"[heartbeat][google] skip: Google auth check failed ({exc})")
        return False


def _google_sources() -> list[_GoogleSource]:
    from pipeline.google_freshness import (
        ingest_calendar_incremental,
        ingest_drive_incremental,
        ingest_gmail_incremental,
        read_google_freshness_cursor,
        write_google_freshness_cursor,
    )

    return [
        _GoogleSource("gmail", read_google_freshness_cursor, write_google_freshness_cursor, ingest_gmail_incremental),
        _GoogleSource("calendar", read_google_freshness_cursor, write_google_freshness_cursor, ingest_calendar_incremental),
        _GoogleSource("drive", read_google_freshness_cursor, write_google_freshness_cursor, ingest_drive_incremental),
    ]


def _valid_next_google_cursor(next_cursor: object) -> bool:
    return isinstance(next_cursor, dict) and bool(next_cursor.get("syncedAt"))


def heartbeat_google_incremental_sync(*, force: bool = False) -> None:
    """Heartbeat-owned Google incremental sync orchestration.

    Auth is checked before work. Each source reads its cursor immediately before ingest,
    and the cursor is advanced only after a successful ingest returns a valid next cursor.
    """
    global _GOOGLE_SYNC_LAST_RUN

    now = time.monotonic()
    if not force and now - _GOOGLE_SYNC_LAST_RUN < _GOOGLE_SYNC_MIN_INTERVAL_SEC:
        return

    with _google_sync_nonblocking_lock() as acquired:
        if not acquired:
            return
        if not force and time.monotonic() - _GOOGLE_SYNC_LAST_RUN < _GOOGLE_SYNC_MIN_INTERVAL_SEC:
            return
        if not _check_google_auth_for_heartbeat():
            _GOOGLE_SYNC_LAST_RUN = time.monotonic()
            return

        try:
            sources = _google_sources()
        except Exception as exc:
            print(f"[heartbeat][google] skip: Google freshness helpers unavailable ({exc})")
            return

        print("[heartbeat][google] incremental sync start")
        for index, source in enumerate(sources):
            if index:
                time.sleep(_GOOGLE_SYNC_DELAY_SEC)
            try:
                cursor = source.read_cursor(source.name)
                print(f"[heartbeat][google] {source.name}: incremental sync start")
                next_cursor = source.ingest(cursor)
                if not _valid_next_google_cursor(next_cursor):
                    print(f"[heartbeat][google] {source.name}: ingest succeeded but returned no valid next cursor; cursor not advanced")
                    continue
                source.write_cursor(source.name, next_cursor)
                print(f"[heartbeat][google] {source.name}: cursor advanced")
            except Exception as exc:
                if _google_auth_error(exc):
                    print(f"[heartbeat][google] {source.name}: auth invalid/reauth required; stopping Google sync ({exc})")
                    _GOOGLE_SYNC_LAST_RUN = time.monotonic()
                    return
                print(f"[heartbeat][google] {source.name}: incremental sync failed ({exc})")
        _GOOGLE_SYNC_LAST_RUN = time.monotonic()


def run_heartbeat_periodic_work() -> None:
    """Periodic heartbeat hook for non-session background work."""
    heartbeat_google_incremental_sync()

