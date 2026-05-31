# Changelog

이 프로젝트의 모든 주요 변경사항은 이 파일에 기록된다.
포맷은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)를 따르며,
버저닝은 [Semantic Versioning](https://semver.org/lang/ko/)을 따른다.

## [Unreleased]

### Added
- **2단 의도 라우터** (`pipeline/tier_router.py` + `llm_gateway.get_provider_for_tier`) — 요청을 도메인 지식 질의/갱신(→ 로컬 SLM, 결정론적·비용 0)과 즉각 업무지원(→ 클라우드 deepseek-v4-flash, 생성·추론·검색)으로 분류. 로컬 SLM 다운 시 클라우드 자동 폴백. `llm_providers.json`에 `tiers`{local:lmstudio, cloud:openrouter} 매핑. 채널 봇이 `route_tier`로 tier 결정 → `stream_gpt(tier=)`로 전달.
- **채널 봇 어댑터** (`pipeline/channels/`) — 텔레그램·슬랙에서 VEGA를 구동. 익숙한 메신저 UX로 사내 AI 경험을 일반 채팅 앱과 일치시키는 것이 목적.
  - `core.py` — 공통 코어 `run_agent_turn(channel, conv_id, text, on_delta, ce_mode)`. 채널 대화 ID↔VEGA 세션 매핑(`data/channel_sessions.json`), 히스토리 복원, `stream_gpt` 호출, 토큰 점진 델타 콜백, 세션 영속을 한 함수로 묶음.
  - `telegram_bot.py` — python-telegram-bot 폴링. DM은 항상, 그룹은 @멘션 시 처리. `edit_message_text`로 점진 스트리밍, 4096자 분할, `/start`·`/reset`.
  - `slack_bot.py` — slack_bolt Socket Mode. `app_mention`·DM 트리거, `thread_ts` 기준 세션 격리, `chat_update`로 점진 스트리밍.
- **KYTE 도구 통합** — kyte-portal의 업무 도구(Airtable/Gmail/Superthread/Calendar/Drive 조회 10종)를 stdio MCP 서버(`kyte_cli/mcp_server.py`)로 받아 자동 등록. `kyte__find_work` 등으로 호출. deepseek-v4-flash(OpenRouter)로 E2E 검증 통과.
- **CHANGELOG.md** — 이 파일 신규 도입.
- `requirements.txt`에 `python-telegram-bot>=22`, `slack_bolt>=1.21` 추가.
- `.env.example`에 `TELEGRAM_BOT_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` 항목 추가.

### Changed
- 기본 LLM 프로바이더를 OpenRouter `deepseek/deepseek-v4-flash`로 설정 (`data/llm_providers.json` active=openrouter).
- CE(원격 클라이언트) 모드에서 `kyte__*` 도구를 허용 — 스키마 노출(`get_schemas_for_mode`)과 실행 방어 차단(`dispatch_tool`) **양쪽**에 `kyte__` prefix 통과 추가. kyte 도구는 모두 read-only envelope라 안전하며, 채널 봇의 핵심 목적이 회사 데이터 조회임.
- `.env.example`의 OpenRouter 키 변수명을 `OPENROUTER_API`로 정정 (`data/llm_providers.json`의 `api_key_env`와 일치).

### Fixed
- **새 환경(빈 DB) 부팅 실패** 다수 수정 — 코드가 한 번도 새 `VEGA_DATA_DIR`로 검증된 적 없어 깨져 있었음:
  - `session_store._ensure_schema()`가 만드는 `messages` 테이블 컬럼(`session_uuid/role/content`)과 CRUD(`append_message`/`load_history`)가 실제 사용하는 컬럼(`conv_uuid/sender/text/char_len/updated_at`)이 불일치 → CREATE TABLE을 CRUD에 맞춤. (`no such column: sender` 해소)
  - `vega_query.py`에 `persona_sections`/`events`/`entities`/`event_entities` 테이블 생성 코드 부재 → `_ensure_schema()` 추가, 모듈 로드 시 자동 호출. (`no such table: persona_sections` 해소)
- 네이티브 `linear_*` 도구가 부재 모듈(`pipeline.linear_client`)을 import해 호출마다 실패 + self_improve 폭주 → import 실패 시 `linear_*` 스키마를 TOOL_SCHEMAS에서 제외. (Linear가 필요하면 `LINEAR_API_KEY`로 MCP `linear__*` 자동 등록되어 동작.)

### Removed
- (해당 없음)

### Notes
- **배포 주의**: VEGA는 `mcp.json`을 user data dir(macOS `~/Library/Application Support/VEGA/mcp.json`, 또는 `$VEGA_DATA_DIR/mcp.json`)에서만 읽는다. repo의 `data/mcp.json`은 무시되므로, kyte MCP 등록은 user data dir에 배치해야 한다.
- Discord 브리지는 vega-core에서 no-op 스텁(`pipeline/discord_bridge.py`) — 채널은 텔레그램/슬랙을 사용.
- 회귀 테스트: `tests/test_channel_kyte_e2e.py` (라이브 OpenRouter, `OPENROUTER_API` 미설정 시 skip).
