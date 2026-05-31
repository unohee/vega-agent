import asyncio, sys
sys.path.insert(0, "/Users/unohee/dev/vega-core")

async def main():
    # 1) 텔레그램 봇 모듈 import (구성 단계 검증 — 토큰 없이)
    from pipeline.channels import telegram_bot
    print("IMPORT telegram_bot OK; _EDIT_INTERVAL=", telegram_bot._EDIT_INTERVAL)

    # 2) 채널 코어 run_agent_turn E2E (텔레그램 SDK 없이 코어만)
    from pipeline.channels import core

    deltas = []
    async def on_delta(full):
        deltas.append(full)

    final = await core.run_agent_turn(
        "telegram", "test_chat_999",
        "KYTE 작품 k1의 제목을 도구로 조회해서 알려줘.",
        on_delta=on_delta, ce_mode=True,
    )
    print(f"N_DELTAS={len(deltas)} (점진 스트리밍 콜백 횟수)")
    print(f"FINAL={final[:200]}")

    # 3) 세션 매핑 영속 확인 + 히스토리 누적 확인
    sid = core.session_for("telegram", "test_chat_999")
    from pipeline import session_store
    hist = session_store.load_history(sid)
    print(f"SESSION={sid[:12]}... HISTORY_LEN={len(hist)}")
    roles = [h['role'] for h in hist]
    print(f"ROLES={roles}")

    ok = ("덤벼라" in final) and len(deltas) > 0 and len(hist) >= 2
    print(f"\n=== VERDICT pass={ok} ===")
    sys.exit(0 if ok else 2)

asyncio.run(main())
