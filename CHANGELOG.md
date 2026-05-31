# Changelog

이 프로젝트의 모든 주요 변경사항은 이 파일에 기록된다.
포맷은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)를 따르며,
버저닝은 [Semantic Versioning](https://semver.org/lang/ko/)을 따른다.

## [Unreleased]

### Added
- **데스크톱 앱(Tauri v2) + 배포용 DMG** (`desktop/`) — 메인 VEGA 레포의 Tauri 셸을 vega-core로 이식. daemon 모드(기본)는 첫 실행 시 `com.unohee.vega-backend` LaunchAgent를 등록해 PyInstaller 백엔드를 상시 실행하고, 트레이 아이콘·전역 단축키(⌘⇧V)·창 토글을 제공. `scripts/build_dmg.sh`가 PyInstaller 백엔드(`bin/vega-backend.spec`) → `cargo tauri build` → DMG 패키징을 수행. Developer ID 인증서가 없으면 자동으로 무서명 빌드.
- **코드 샌드박스 자동 확보** (`pipeline/sandbox.ensure_sandbox_ready` + 서버 lifespan 워밍업) — 서버 기동 시 백그라운드로 Docker `vega-sandbox` 컨테이너를 확보(이미 있으면 재사용, 이미지 없을 때만 빌드)해 첫 `bash_exec`/`python_exec` 지연을 없앤다. Docker 미설치/미기동이면 조용히 skip(에이전트는 계속 동작, 코드 실행만 보류). `docker_available()` 추가. compose 경로는 `${VEGA_HOST_HOME}`/`${VEGA_DATA_DIR}` 환경변수로 파라미터화(메인 레포 하드코딩 제거)하고 `_compose_env()`가 주입 — 배포본·다른 사용자 환경에서도 동작. 영속 볼륨(`sandbox_lib`/`packages`/`history`)은 보존되어 에이전트가 만든 모듈·pip 패키지가 재시작에도 유지. DMG 번들에 `sandbox/{Dockerfile,docker-compose.yml}` 포함.
- **설치 마법사 — 연결된 LLM이 진행** (`web/static/install_wizard.html` + `web/routers/onboarding.py`) — 데몬 첫 실행 시 `/entry`가 온보딩 여부를 보고 `/install`로 보낸다. 마법사는 (1) OpenRouter 키 입력+라이브 검증→Keychain 저장, (2) **연결된 LLM이 대화형으로** 이름·역할·소속을 수집(LLM 응답의 ```vega``` directive를 파싱해 user_profile에 즉시 반영), (3) Google Cloud OAuth(Client ID/Secret 저장→브라우저 동의→refresh token 발급) 단계로 구성. 완료 시 `onboarded=true` 마킹 후 `/chat`으로 전환.
- **PyInstaller 백엔드 번들** (`bin/vega_backend_launcher.py`, `bin/vega-backend.spec`) — `web.server:app`을 uvicorn으로 띄우는 단일 바이너리. `web/static`·`data/{agents,commands}` 기본값을 번들에 포함.
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
- **CE 모드 게이트 비활성화 (당분간)** — 개인용이라 모든 진입점(데스크톱 앱·채널 봇)에서 로컬 파일/exec 도구를 포함한 전체 도구를 노출·실행하도록 변경. `get_schemas_for_mode`는 `ce_mode` 무관 전체 스키마 반환, `dispatch_tool`의 CE 차단 제거. `ce_mode` 인자와 `_CE_ALLOWED_TOOLS`/`_CE_MODE_VAR`는 호환·재활성화용으로 보존. **plan_mode 차단은 그대로 유지**. (원격 노출 시 화이트리스트 복원 필요 — 채널 봇 토큰 유출 시 로컬 머신 노출 위험.)
- **DB 파일 분리** — vega-core는 자체 `agent.db`를 쓴다 (`data_paths.db_path()` `vega.db`→`agent.db`). 같은 user data dir를 메인(개인) VEGA의 `vega.db`와 공유하더라도 파일을 분리해 messages 테이블 스키마 충돌(구 `session_uuid/role/content` ↔ 신 `conv_uuid/sender/text`)을 회피. `run_log.py`·`memory_inspector.py`의 하드코딩 폴백 경로도 `agent.db`로 통일. `scripts/init_user_db.py`는 부재 모듈(`pipeline.heartbeat`) 의존 제거 + persona/events/entities 스키마 명시 생성.
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
