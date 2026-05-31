# Created: 2026-05-31
# Purpose: VEGA 슬랙 봇 어댑터. slack_bolt Socket Mode 로 멘션/DM 을 받아
#          channels.core.run_agent_turn 으로 에이전트를 돌리고, 답변을 chat_update 로
#          점진 스트리밍한다. 텔레그램 어댑터와 동일 코어를 공유한다.
# Dependencies: slack_bolt>=1.21 (+ slack_sdk), pipeline.channels.core
# Test Status: under validation
"""VEGA 슬랙 봇 (Socket Mode).

실행: SLACK_BOT_TOKEN(xoxb-...) + SLACK_APP_TOKEN(xapp-...) 설정 후
    python -m pipeline.channels.slack_bot

동작:
- app_mention(채널 멘션) / message.im(DM) → run_agent_turn → 답변
- 스레드 유지: 멘션된 메시지의 thread_ts(없으면 ts)를 스레드로 답글
- 스트리밍: 첫 토큰에 chat_postMessage, 이후 _EDIT_INTERVAL 초마다 chat_update
- 세션 격리: 슬랙 thread_ts 를 대화 키로 사용 (channels.core)
"""
from __future__ import annotations

import os
import time

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from pipeline.channels.core import run_agent_turn

_EDIT_INTERVAL = 1.0  # chat_update 최소 간격(초) — 슬랙 rate limit(분당 ~50) 회피
_CHANNEL = "slack"


def _bot_token() -> str:
    tok = os.getenv("SLACK_BOT_TOKEN", "").strip()
    if not tok:
        raise RuntimeError("SLACK_BOT_TOKEN(xoxb-...) 환경변수가 비어있다.")
    return tok


def _app_token() -> str:
    tok = os.getenv("SLACK_APP_TOKEN", "").strip()
    if not tok:
        raise RuntimeError("SLACK_APP_TOKEN(xapp-...) 환경변수가 비어있다. Socket Mode 에 필요.")
    return tok


def _strip_mention(text: str) -> str:
    """<@U0BOTID> 멘션 토큰 제거."""
    import re
    return re.sub(r"<@[A-Z0-9]+>", "", text or "").strip()


def build_app() -> AsyncApp:
    app = AsyncApp(token=_bot_token())

    async def _handle(event: dict, say, client) -> None:
        text = _strip_mention(event.get("text", ""))
        channel = event.get("channel")
        # 스레드 키: 부모가 있으면 그 thread_ts, 없으면 이 메시지 ts → 같은 스레드 = 같은 세션
        thread_ts = event.get("thread_ts") or event.get("ts")
        if not text or not channel:
            return

        conv_id = f"{channel}:{thread_ts}"

        # 첫 메시지 전송 (스레드)
        posted = await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text="…")
        ts = posted["ts"]

        state = {"last_edit": 0.0, "shown": ""}

        async def _render(full: str) -> None:
            now = time.monotonic()
            body = full[:3900] if len(full) > 3900 else full
            if now - state["last_edit"] < _EDIT_INTERVAL:
                return
            if body and body != state["shown"]:
                try:
                    await client.chat_update(channel=channel, ts=ts, text=body)
                    state["shown"] = body
                    state["last_edit"] = now
                except Exception:
                    pass

        try:
            final = await run_agent_turn(
                _CHANNEL, conv_id, text, on_delta=_render, ce_mode=True,
            )
        except Exception as e:
            await client.chat_update(channel=channel, ts=ts, text=f"⚠️ 처리 중 오류: {e}")
            return

        body = (final or "(빈 응답)")[:3900]
        if body != state["shown"]:
            try:
                await client.chat_update(channel=channel, ts=ts, text=body)
            except Exception:
                pass

    @app.event("app_mention")
    async def _on_mention(event, say, client):
        await _handle(event, say, client)

    @app.event("message")
    async def _on_message(event, say, client):
        # DM(채널 타입 im)만 처리 — 일반 채널 메시지는 멘션으로만 받는다. 봇 자기 메시지 무시.
        if event.get("channel_type") != "im":
            return
        if event.get("bot_id") or event.get("subtype"):
            return
        await _handle(event, say, client)

    return app


def main() -> None:
    import asyncio
    from pipeline import mcp_client  # noqa: F401  (.env 로드 부수효과)
    app = build_app()
    handler = AsyncSocketModeHandler(app, _app_token())
    print("[slack] VEGA 슬랙 봇 Socket Mode 시작…")
    asyncio.run(handler.start_async())


if __name__ == "__main__":
    main()
