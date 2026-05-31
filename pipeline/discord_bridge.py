# Created: 2026-05-31
# Purpose: vega-core 용 Discord 비활성 스텁. 개인 VEGA 는 discord.py + discord_bot 패키지에
#          의존하는 풀 브리지를 쓰지만, vega-core(사내 배포본)는 Discord 를 사용하지 않는다.
#          tools.py 가 discord_notify 를 import 하므로 import 체인만 끊지 않도록 no-op 을 제공.
# Dependencies: stdlib only (의도적으로 discord 라이브러리에 의존하지 않음)
"""Discord 비활성 스텁.

사내 배포(vega-core)는 Telegram/Slack 채널을 쓰고 Discord 는 쓰지 않는다.
discord_notify 는 호출되어도 실제 전송 없이 비활성 상태를 명시적으로 반환한다.
Discord 를 다시 붙이려면 개인 VEGA 의 pipeline/discord_bridge.py 를 이식하면 된다.
"""
from __future__ import annotations

import json


def discord_notify(message: str, title: str = "VEGA", level: str = "info") -> dict:
    """Discord 알림 — vega-core 에서는 비활성. no-op 으로 명시적 비활성 상태 반환."""
    return {
        "ok": False,
        "disabled": True,
        "note": "Discord 는 vega-core(사내 배포본)에서 비활성. Telegram/Slack 채널을 사용하세요.",
    }


# 개인 VEGA 의 heartbeat/discord 봇 진입점들이 없을 때 import 에러를 막기 위한 더미.
def run_bot() -> None:  # pragma: no cover - 비활성
    raise RuntimeError("Discord 봇은 vega-core 에서 비활성화되어 있습니다.")
