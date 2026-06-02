# Created: 2026-06-01
# Purpose: 세션 제목·요약 자동생성. 연결된 활성 Provider(OpenRouter/Anthropic/OpenAI 등)로 처리.
# Dependencies: pipeline.streaming.stream_gpt, pipeline.llm_gateway

from __future__ import annotations

import asyncio
import json
import re


_TITLE_SYSTEM = """You are a session-title generator. Given a conversation, return ONLY a JSON object with these fields:
- title: 5 words or fewer, no punctuation (the session name shown in the UI)
- summary: 1-2 sentences describing what the conversation accomplished
- narrative: 2-3 sentences of richer context for future retrieval

Rules:
- Match the language the user spoke in (Korean conversation → Korean title/summary/narrative)
- title must be concise — it appears in a sidebar list
- Return ONLY valid JSON, no markdown fences, no extra text"""


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
