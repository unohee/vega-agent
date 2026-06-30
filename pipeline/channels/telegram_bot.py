# Created: 2026-05-31
# Purpose: VEGA 텔레그램 봇 어댑터. python-telegram-bot 폴링으로 메시지를 받아
#          channels.core.run_agent_turn 으로 에이전트를 돌리고, 답변을 edit_message_text 로
#          점진 스트리밍한다. 익숙한 메신저 UX 로 사내 AI를 쓰게 하는 것이 목적.
# Dependencies: python-telegram-bot>=22, pipeline.channels.core
# Test Status: under validation
"""VEGA 텔레그램 봇.

실행: TELEGRAM_BOT_TOKEN 환경변수 설정 후
    python -m pipeline.channels.telegram_bot

동작:
- DM/그룹 멘션 텍스트 → run_agent_turn → 답변
- 스트리밍: 첫 토큰에 메시지 전송, 이후 _EDIT_INTERVAL 초마다 edit_message_text 로 갱신
  (텔레그램 rate limit 회피). 4096자 초과 시 이어붙임 메시지로 분할.
- chat_id 별로 vega 세션 격리 (channels.core.session_for).
- /reset 으로 대화 초기화, /start 안내.
"""
from __future__ import annotations

import asyncio
import os
import time

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from pipeline.channels.core import run_agent_turn, reset_session, split_for_channel

_EDIT_INTERVAL = 0.7  # edit_message_text 최소 간격(초) — 텔레그램 rate limit 회피
_TG_MAX = 4000        # 텔레그램 메시지 안전 길이(4096 한계 미만)
_CHANNEL = "telegram"


def _token() -> str:
    tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not tok:
        raise RuntimeError("TELEGRAM_BOT_TOKEN 환경변수가 비어있다. .env 에 봇 토큰을 설정해라.")
    return tok


async def _cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "VEGA 사내 에이전트입니다. 회사 데이터(작품·정산·OKR·메일·일정 등)를 물어보세요.\n"
        "/reset 으로 대화를 초기화할 수 있어요."
    )


async def _cmd_reset(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    reset_session(_CHANNEL, str(update.effective_chat.id))
    await update.message.reply_text("대화를 초기화했습니다.")


def _should_handle(update: Update, bot_username: str) -> bool:
    """DM 은 항상, 그룹은 멘션됐을 때만 처리."""
    chat = update.effective_chat
    msg = update.message
    if not msg or not msg.text:
        return False
    if chat.type == "private":
        return True
    # 그룹: @봇 멘션 또는 봇 메시지에 대한 답글
    text = msg.text or ""
    if bot_username and f"@{bot_username}" in text:
        return True
    # 이 봇 자신에게 온 답글만 처리 (INT-2235) — 그룹의 다른 봇 답글을 가로채지 않는다.
    replied = msg.reply_to_message and msg.reply_to_message.from_user
    if replied and replied.is_bot and bot_username and replied.username == bot_username:
        return True
    return False


def _strip_mention(text: str, bot_username: str) -> str:
    if bot_username:
        return text.replace(f"@{bot_username}", "").strip()
    return text.strip()


async def _on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    bot_username = ctx.bot.username or ""
    if not _should_handle(update, bot_username):
        return

    chat_id = update.effective_chat.id
    user_text = _strip_mention(update.message.text, bot_username)
    if not user_text:
        return

    await ctx.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    # 점진 편집 상태
    state = {
        "msg": None,         # 보낸 메시지 객체
        "last_edit": 0.0,    # 마지막 편집 시각
        "shown": "",         # 화면에 반영된 텍스트
        "base_len": 0,       # 현재 메시지가 담당하는 시작 오프셋
    }

    async def _render(full: str) -> None:
        now = time.monotonic()
        # 현재 메시지가 담당하는 구간
        chunk = full[state["base_len"]:]
        # 너무 길면 끊어서 새 메시지로
        if len(chunk) > _TG_MAX:
            # 현재 메시지를 _TG_MAX 까지 확정하고 다음 메시지 시작
            head = chunk[:_TG_MAX]
            if state["msg"] is not None and state["shown"] != head:
                try:
                    await state["msg"].edit_text(head)
                except Exception:
                    pass
            state["base_len"] += _TG_MAX
            state["msg"] = None
            state["shown"] = ""
            chunk = full[state["base_len"]:]

        if not chunk:
            return
        if state["msg"] is None:
            state["msg"] = await ctx.bot.send_message(chat_id=chat_id, text=chunk)
            state["shown"] = chunk
            state["last_edit"] = now
            return
        # rate limit: 간격 미만이면 스킵 (마지막에 한 번 더 확정 렌더함)
        if now - state["last_edit"] < _EDIT_INTERVAL:
            return
        if chunk != state["shown"]:
            try:
                await state["msg"].edit_text(chunk)
                state["shown"] = chunk
                state["last_edit"] = now
            except Exception:
                pass  # "message is not modified" 등 무시

    try:
        final = await run_agent_turn(
            _CHANNEL, str(chat_id), user_text, on_delta=_render, ce_mode=True,
        )
    except Exception as e:
        await ctx.bot.send_message(chat_id=chat_id, text=f"⚠️ 처리 중 오류: {e}")
        return

    # 마지막 확정 렌더 (throttle 로 누락된 마지막 토큰 반영)
    chunk = final[state["base_len"]:] if final else ""
    if chunk:
        # 잔여분이 _TG_MAX 를 넘어도 잘라 버리지 않고 분할 전송 (INT-2235).
        parts = split_for_channel(chunk, _TG_MAX)
        if state["msg"] is None:
            await ctx.bot.send_message(chat_id=chat_id, text=parts[0])
        elif parts[0] != state["shown"]:
            try:
                await state["msg"].edit_text(parts[0])
            except Exception:
                pass
        for extra in parts[1:]:
            await ctx.bot.send_message(chat_id=chat_id, text=extra)
    elif state["msg"] is None and not final:
        await ctx.bot.send_message(chat_id=chat_id, text="(빈 응답)")


def build_application() -> Application:
    app = Application.builder().token(_token()).build()
    app.add_handler(CommandHandler("start", _cmd_start))
    app.add_handler(CommandHandler("reset", _cmd_reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))
    return app


def main() -> None:
    # .env 로드 (mcp_client 가 이미 하지만, 단독 실행 대비 명시)
    from pipeline import mcp_client  # noqa: F401  (_load_env 부수효과)
    app = build_application()
    print("[telegram] VEGA 텔레그램 봇 폴링 시작…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
