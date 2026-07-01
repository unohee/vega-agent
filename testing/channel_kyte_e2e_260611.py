# Created: 2026-06-11
# Updated: 2026-07-01 (INT-2238) — VEGA_DATA_DIR tmp 격리 + 고유 conv_id + 키 미설정 skip +
#   현재-run 정확히 2개 추가 검증. 기존엔 고정 conv_id·격리 없음으로 repo data/ 오염 +
#   누적 히스토리만으로도 len>=2 통과하는 false-pass 가 있었다.
import asyncio
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# data_dir()·db_path() 는 lru_cache — import/첫 호출 전에 격리 env 를 잡아야 한다.
_TMP = tempfile.mkdtemp(prefix="vega_e2e_")
os.environ["VEGA_DATA_DIR"] = _TMP


async def main() -> int:
    # 환경 미비(LLM 키) 시 skip — 기능 회귀와 설정 누락을 구분한다.
    from pipeline import keychain
    if not any(keychain.get(k) for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API")):
        print("SKIP: LLM API 키 미설정 — 채널 e2e 를 건너뜁니다.")
        return 0

    # 1) 텔레그램 봇 모듈 import (구성 단계 검증 — 토큰 없이)
    from pipeline.channels import telegram_bot
    print("IMPORT telegram_bot OK; _EDIT_INTERVAL=", telegram_bot._EDIT_INTERVAL)

    # 2) 채널 코어 run_agent_turn E2E (텔레그램 SDK 없이 코어만)
    from pipeline.channels import core
    from pipeline import session_store

    conv_id = f"e2e_{uuid.uuid4().hex[:8]}"  # 매 실행 고유 — 매핑/히스토리 오염·재유입 방지
    sid = core.session_for("telegram", conv_id)
    len_before = len(session_store.load_history(sid))

    deltas: list[str] = []

    async def on_delta(full: str) -> None:
        deltas.append(full)

    final = await core.run_agent_turn(
        "telegram", conv_id,
        "KYTE 작품 k1의 제목을 도구로 조회해서 알려줘.",
        on_delta=on_delta, ce_mode=True,
    )
    print(f"N_DELTAS={len(deltas)} FINAL={(final or '')[:200]}")

    # 3) 이번 run 에서 정확히 user+assistant 2개가 순서대로 추가됐는지 (누적분 false-pass 차단)
    hist = session_store.load_history(sid)
    added = len(hist) - len_before
    roles_tail = [h["role"] for h in hist[-2:]]
    print(f"SESSION={sid[:12]}... ADDED={added} ROLES_TAIL={roles_tail}")

    ok = added == 2 and roles_tail == ["user", "assistant"] and len(deltas) > 0 and bool(final)
    print(f"\n=== VERDICT pass={ok} ===")
    return 0 if ok else 2


_rc = 1
try:
    _rc = asyncio.run(main())
finally:
    shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(_rc)
