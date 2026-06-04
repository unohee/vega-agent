# Changelog

이 프로젝트의 모든 주요 변경사항은 이 파일에 기록된다.
포맷은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)를 따르며,
버저닝은 [Semantic Versioning](https://semver.org/lang/ko/)을 따른다.

## [Unreleased]

## [0.1.7] - 2026-06-04

### Added (VEGA 백포트 — 채팅/대시보드 UX)
- **재방문 인터리빙 복원** (`web/server.py`, `pipeline/session_store.py`, `chat.html`) — 어시스턴트 메시지에 텍스트↔도구 실행을 시간순으로 기록하는 `events` 구조 도입. `messages` 테이블에 `events TEXT` 컬럼 추가(마이그레이션), 도구가 쓰인 응답은 events를 영속화해 세션 재방문 시 라이브와 동일한 순서로 복원. 순수 텍스트 응답은 텍스트 폴백.
- **도구별 완료 요약 + 명령어 중심 배지** (`web/server.py`) — `_exec_summary`로 host_exec/bash_exec 결과를 '무엇을 했는지'(명령어+rc) 한 줄로 요약, `_tool_summary`가 call_id별 실행 명령어를 배지에 반영. 출력은 터미널 블럭에서 분리 표시.
- **중단 응답 영속화** (`web/server.py`) — `_build_aborted_message`로 응답이 중단돼도 도구 실행 흔적을 보존, 재방문 시 사라지지 않음.
- **메시지 편집** (`chat.html`) — 사용자 메시지를 편집해 재전송.
- **작업 과정 투명성 (Claude Code 스타일)** (`data/agents/_default.md`) — 도구를 쓰며 진행 과정을 본문 텍스트로 자연스럽게 보여주는 에이전트 지침 추가.
- **대시보드 메모리 생태계 뷰** (`dashboard.html`, `web/routers/memory_inspector.py`) — 홈을 히어로(VEGA가 기억하는 것) + 탭(최근 기억·인물/엔티티·타임라인·페르소나·규칙/스킬·오늘) 구조로 전면 재설계.
- **파일 뷰어 드래그 인용 + 외부 에디터로 열기** (`web/routers/fs.py`, `chat.html`) — 파일 뷰어에서 텍스트를 드래그해 채팅에 인용, 외부 에디터로 바로 열기.

### Added
- **STT(음성→텍스트) 지원** (`pipeline/stt_gateway.py`) — OpenAI Whisper API 호환 엔드포인트 공통 게이트웨이. 지원 프로바이더: OpenAI (`whisper-1`), Groq, 로컬 faster-whisper-server, LM Studio. `LocalSTTUnavailable` 예외로 사이드카 미실행 시 503 조용히 반환. `data/llm_providers.json`에 `stt` 섹션 추가 (`provider`, `model`, `language`, `response_format`). `/api/stt`, `/api/stt/config` 엔드포인트 추가.
- **채팅 UI 마이크 버튼** (`chat.html`) — 입력창에 🎙 버튼 추가. MediaRecorder로 브라우저 내 녹음 → `/api/stt` 전송 → 텍스트를 커서 위치에 삽입. `+` 팝오버 메뉴에도 "음성 입력" 항목 추가. 로컬 STT 미실행 시 "로컬 STT 미실행" 토스트 표시.
- **UI 언어 선택 (한국어/English)** (`chat.html`, `dashboard.html`) — 헤더에 `KO`/`EN` 토글 버튼 추가. `VEGA_STRINGS` i18n 객체 + `applyLang()` + `data-i18n` 속성 패턴으로 정적 UI 텍스트 교체. `localStorage['vega_lang']`으로 선택 언어 지속화.
- **다국어 지원 로드맵** (`docs/I18N_ROADMAP.md`) — Phase 1(문자열 완전 번역) → Phase 2(외부 JSON 외부화) → Phase 3(일본어·중국어 추가) → Phase 4(에이전트 응답 언어 연동) 4단계 로드맵 문서화.
- **비전공자용 사용자 설명서** (`README.md`) — 전면 재작성. 설치부터 음성 입력·파일 첨부·슬래시 명령어·MCP까지 스크린샷 없이도 따라할 수 있는 위키 수준 설명서.

### Added (이전 미기록)
- **멀티 프로바이더 설치 마법사 + Anthropic 네이티브 어댑터** — 설치 마법사가 OpenRouter 전용에서 프로바이더 목록(Anthropic·OpenAI·OpenRouter API 키 / ChatGPT PKCE 로그인 / 로컬·온프레미스 URL) → 선택 → 해당 인증 흐름으로 확장. 키는 라이브 검증(`/models` 200) 후 Keychain 저장 + `llm_providers.json`에 `upsert_provider`로 등록 후 활성화. 추론 백엔드도 동일하게 멀티 프로바이더 지원:
  - **Anthropic 네이티브 어댑터** (`llm_gateway` `kind=anthropic`) — OpenAI 호환이 아닌 `/v1/messages`를 직접 호출. `x-api-key`+`anthropic-version` 헤더, system을 cache_control 블록으로, Responses↔Anthropic 메시지/tool 스키마(`input_schema`) 변환, `max_tokens` 필수. `streaming._stream_sse`에 Anthropic SSE 파싱(`message_start`/`content_block_delta` text_delta·input_json_delta/`message_delta` usage/`message_stop`) 추가. `auth_type`: `anthropic_key`(콘솔 키) / `claude_oauth`(보류 — client_id 비공개, import 가드).
  - **OpenAI 직접 API** 프로바이더(`api.openai.com`, bearer) 추가.
  - 로컬·온프레미스는 OpenAI 호환 URL만 입력해 등록(서버 미응답이어도 등록 허용).
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
