# VEGA Core — Architecture

> 이 문서는 다른 에이전트가 이 레포에 빠르게 온보딩하기 위한 구조 지도다.
> 사람 독자용 개요는 README를 참조.

## 시스템 개요

VEGA Core는 **로컬-퍼스트, 모델-비종속 LLM 에이전트 하네스**다. 핵심 추상화는
"LLM은 액션 레이어, 지식·규칙·기억은 모델 밖 파일/DB에 영속"이다.

제품 관점에서 VEGA는 **쉬운 데스크톱 AI 앱과 개발자 전용 터미널 에이전트 환경 사이의
빈칸**을 메우는 앱이다. Claude Desktop/ChatGPT Desktop 같은 앱은 접근성은 좋지만
권한·커스텀·로컬 실행·워크플로우 지속성이 제한되고, Claude Code/Codex/OpenClaw/MCP/CLI
조합은 강력하지만 터미널·bash·설정 파일·daemon 운영에 익숙하지 않으면 접근하기 어렵다.

따라서 VEGA의 설계 목표는 "터미널 레벨의 AI 작업 능력을 데스크톱 앱 UX로 제공"하는 것이다.
비개발자 LLM 파워유저 또는 파워유저가 되고 싶은 사람을 1차 사용자로 보고, 개발자에게는
내부 구조와 확장 지점을 열어둔다.

핵심 제품 원칙:
- **Local-first is the trust boundary**: 기본 기능은 계정 없이 로컬에서 동작해야 한다.
- **Bring your models**: 모델은 교체 가능해야 하고, 사용자의 작업 상태는 VEGA에 남아야 한다.
- **Desktop-simple, terminal-capable**: 도구 실행·파일 접근·MCP·승인 흐름은 강력하되 UI에서 통제 가능해야 한다.
- **No setup tax for power users**: OpenClaw/MCP/CLI 조합을 직접 설치·운영하지 않아도 비슷한 능력을 제공한다.
- **Cloud is additive**: 싱크·백업·원격 지원·관리 정책은 유료/클라우드 기능이 될 수 있지만 로컬 코어를 대체하면 안 된다.

핵심 구성:
- **에이전트 루프** (`pipeline/streaming.py`): SSE tool-use 멀티라운드 루프
- **멀티 프로바이더** (`pipeline/llm_gateway.py`): ChatGPT/OpenRouter/LM Studio
- **영속 메모리** (`pipeline/session_store.py`, `vega_query.py`, `memory_store.py`)
- **3겹 자기진화** (`pipeline/compaction.py`): 페르소나·규칙·스킬
- **진입 채널** (`web/server.py` FastAPI/SSE, `pipeline/channels/` 텔레그램·슬랙 봇)
- **외부 도구 통합** (`pipeline/mcp_client.py`): MCP 서버(예: kyte-portal 업무 도구)

## 디렉토리 트리 (주요)

```
pipeline/
  streaming.py      — GPT tool-use SSE 루프 (plan/research/CE 모드)
  llm_gateway.py    — 멀티 프로바이더 라우터 (OpenRouter=기본, deepseek-v4-flash)
  tools.py          — 도구 레지스트리 + dispatch_tool + CE/plan 모드 게이트
  tools_*.py        — 도구 구현 (google/code/web). office는 vega-core에서 빈 스텁
  discord_bridge.py — Discord no-op 스텁 (vega-core는 텔레그램/슬랙 사용)
  compaction.py     — 20턴 컴팩션 + 메모리/규칙 갱신
  session_store.py  — 세션/메시지 영속 (SQLite, conversations/messages)
  vega_query.py     — 페르소나/이벤트/엔티티 쿼리 + 스키마 자동생성
  memory_store.py   — 벡터 메모리 (LanceDB)
  mcp_client.py     — MCP 서버 통합 (stdio/sse), init_mcp_tools
  data_paths.py     — user data dir 해석 (모든 DB/config 경로의 단일 출처)
  self_improve.py   — 도구 실패 → 패치 → 검증
  channels/         — 메신저 채널 어댑터 (신규)
    core.py         — run_agent_turn 공통 코어 + 세션 매핑
    telegram_bot.py — python-telegram-bot 폴링 봇
    slack_bot.py    — slack_bolt Socket Mode 봇
web/
  server.py         — FastAPI (REST + SSE), lifespan에서 init_mcp_tools
data/                — repo 번들 기본값 (commands/, agents/, llm_providers.json)
  agents/_default.md — 배포자 헌법 (불변)
  agents/RULES.md    — 사용자 규칙 (가변)
sandbox/            — 코드 실행 Docker
desktop/            — Tauri v2 데스크톱 앱
tests/              — pytest (test_channel_kyte_e2e.py = 채널↔kyte E2E)
```

## 모듈 책임

| 모듈 | 책임 | 진입점 |
|------|------|--------|
| streaming.py | tool-use 루프, SSE 스트리밍, CE/plan 스키마 필터 | `stream_gpt()`, `_build_request()`, `build_system()` |
| llm_gateway.py | 프로바이더 라우팅, 요청 빌드, 도구 그룹 필터 | `build_request()`, `get_active_provider()` |
| tools.py | 도구 스키마 + 디스패치 + CE/plan 게이트 | `dispatch_tool()`, `get_schemas_for_mode()` |
| compaction.py | 회고, 메모리 영속 | `compact_history()` |
| session_store.py | 대화 영속 (conversations/messages) | `append_message()`, `load_history()`, `create_session()` |
| vega_query.py | 페르소나/이벤트/엔티티 + 스키마 생성 | `get_persona()`, `_ensure_schema()` |
| mcp_client.py | 외부 MCP 도구 등록/호출 | `init_mcp_tools()`, `call_mcp_tool()`, `is_mcp_tool()` |
| channels/core.py | 채널 1턴 실행 + 세션 매핑 | `run_agent_turn()`, `session_for()` |
| channels/telegram_bot.py | 텔레그램 봇 | `main()`, `build_application()` |
| channels/slack_bot.py | 슬랙 봇 (Socket Mode) | `main()`, `build_app()` |
| data_paths.py | user data dir 경로 해석 | `data_dir()`, `mcp_config_path()`, `db_path()` |

## 데이터 흐름

### 핵심 에이전트 루프
```
사용자 메시지
  → build_system() (페르소나+규칙+커맨드) [+ 채널은 kyte 도구 힌트 덧붙임]
  → stream_gpt() 루프 (for _ in range(max_rounds)):
      _build_request() → get_schemas_for_mode(TOOL_SCHEMAS, ce_mode) → llm_gateway.build_request()
      → LLM SSE → token_q / tool_q (이중 Queue) → on_token / 도구 누적
      → dispatch_tool() (CE/plan 게이트 통과 후 실행) → function_call_output 재주입
  → 최종 응답
  → 20턴마다 compact_history() (요약+메모리+규칙)
```

### 채널 봇 흐름 (텔레그램/슬랙)
```
메신저 메시지 (DM 또는 @멘션)
  → channels/{telegram,slack}_bot 핸들러
  → channels/core.run_agent_turn(channel, conv_id, text, on_delta, ce_mode=True)
      → ensure_mcp_loaded() (프로세스당 1회, kyte 등 MCP 도구를 TOOL_SCHEMAS에 합침)
      → session_for(channel, conv_id) → vega 세션 ID (data/channel_sessions.json)
      → load_history() + 현재 메시지 → stream_gpt()
      → on_token마다 on_delta(누적텍스트) → 채널이 edit_message/chat_update로 점진 갱신
      → append_message(sid, "human", ...) / append_message(sid, "assistant", ...)
  → 최종 답변
```

### KYTE 도구 게이트웨이 (cross-repo)
```
kyte-portal: integration_tools 10종 (Airtable/Gmail/Superthread/Calendar/Drive 조회)
  → kyte_cli/mcp_server.py (stdio MCP 서버, INTEGRATION_TOOL_SPECS → MCP Tool)
  → [user data dir]/mcp.json 의 "kyte" 항목으로 등록
  → vega-core mcp_client.init_mcp_tools() 가 startup/첫턴에 로드
  → TOOL_SCHEMAS에 kyte__find_work, kyte__komca_lines ... 추가
  → dispatch는 call_mcp_tool() 경유, envelope {data, source, note} 반환
```

## 주요 타입·스키마

### SQLite (session_store.py — `db_path()` = `<data_dir>/vega.db`)
```
conversations(uuid PK, source, name, created_at, updated_at, msg_count, working_dir, archived)
messages(uuid PK, source, conv_uuid, sender, text, char_len, created_at, updated_at, usage_meta)
  -- sender: "human" | "assistant"; load_history는 sender=="human"만 user로 매핑
```
### SQLite (vega_query.py — 동일 vega.db, `_ensure_schema()`가 보장)
```
persona_sections(id PK, section_key, content, scope, version, is_active, notes, updated_at)
events(id PK, event_date, title, body, tags, created_at)
entities(id PK, name, kind, canonical_id, aliases_json, notes, first_seen, last_seen)
event_entities(event_id, entity_id, match_text)
```
### 채널 세션 매핑 (channels/core.py — `<repo>/data/channel_sessions.json`)
```
{ "telegram:<chat_id>": "<vega-session-uuid>", "slack:<channel>:<thread_ts>": "..." }
```
### 도구 envelope (kyte 도구 반환 — read-only)
```
{ "data": <list|dict|null>, "source": {"system": "...", "fetched_at": "..."}, "note": "<선택>" }
```

## 확장 지점

- **새 도구**: `tools_*.py`에 함수 + `tools.py` TOOL_SCHEMAS/TOOL_FUNCTIONS 등록
- **STT 프로바이더 추가**: `pipeline/stt_gateway.py`의 `_WELL_KNOWN_ENDPOINTS`에 엔드포인트 등록, `data/llm_providers.json`의 `stt` 섹션에서 `provider`·`model`·`language` 설정
- **새 UI 언어**: `web/static/chat.html`과 `dashboard.html`의 `VEGA_STRINGS` 객체에 언어 코드+번역 쌍 추가, 언어 토글 버튼 드롭다운 전환(Phase 3 예정)
- **새 프로바이더**: `data/llm_providers.json`에 추가 (또는 user data dir 사본)
- **새 MCP 서버**: **user data dir의 `mcp.json`** 에 등록 (repo `data/mcp.json` 아님 — 아래 지뢰 참조)
- **새 채널**: `channels/`에 어댑터 작성 → `channels.core.run_agent_turn`을 호출하고 자기 SDK로 점진 렌더만 구현
- **CE 모드 허용 도구**: `tools._CE_ALLOWED_TOOLS`에 추가하거나 prefix 예외(`get_schemas_for_mode` + `dispatch_tool` 양쪽)

## 제품/사업 경계

VEGA는 모델 회사의 앱을 정면 대체하기보다, 여러 모델과 업무 도구를 이어주는 **개인 에이전트
작업공간**이 되는 쪽이 맞다. Claude, ChatGPT, Codex, OpenRouter, 로컬 모델은 모두 교체 가능한
액션/추론 엔진이고, VEGA의 자산은 세션, 메모리, 권한, 도구 연결, 실행 기록, 사용자 워크플로우다.

권장 과금 경계:
- **Free / Local**: 로컬 데스크톱 앱, BYOK/provider 연결, 로컬 세션·메모리, 기본 도구, 자동 업데이트.
- **Pro**: 계정 로그인, 여러 Mac 간 싱크, 암호화 백업, 원격 접속, 모바일/웹 클라이언트, 관리형 커넥터.
- **Team / Enterprise**: 조직 워크스페이스, 정책/권한, 감사 로그, SSO, 중앙 커넥터 관리, 원격 지원.

구현상 cloud 기능은 local-first 코어 위의 부가 계층이어야 한다. 라이선스 체크는 오프라인 grace period를
둬야 하며, 로컬 무료 기능은 계정 검증 실패로 망가지면 안 된다.

## 금기 / 지뢰

- **mcp.json 경로**: `data_paths.mcp_config_path()`는 **user data dir**(`~/Library/Application Support/VEGA/` 또는 `$VEGA_DATA_DIR`)을 가리킨다. repo의 `data/mcp.json`은 **읽지 않는다**. MCP 등록은 반드시 user data dir에.
- **CE 차단은 두 군데**: 스키마 노출(`get_schemas_for_mode`)과 실행 방어(`dispatch_tool`의 `_CE_MODE_VAR` 체크). 원격 채널에 도구를 허용하려면 **둘 다** 고쳐야 한다. 하나만 풀면 모델이 "CE 모드라 차단됨"이라며 실패한다.
- **session_store / vega_query 스키마**: `messages`는 `sender/text/conv_uuid`(role/content 아님). CRUD와 `_ensure_schema`가 반드시 일치해야 함 — 과거 불일치로 새 DB가 깨졌던 이력 있음.
- `data/agents/_default.md`는 배포자 헌법 — 함부로 수정 금지
- 프롬프트 캐싱 위해 `build_system()`은 정적 유지 (동적 컨텍스트는 `build_dynamic_preamble`)
- ChatGPT Codex는 `max_output_tokens` 거부 (responses kind)
- 부재 모듈에 의존하는 도구(linear_client 등)는 import 가드로 스키마에서 제외 — 안 그러면 self_improve가 폭주

## 테스트 전략

- 단위: `tests/test_*.py` — `pytest tests/`
- 통합 E2E: `tests/test_channel_kyte_e2e.py` — 채널 코어 → kyte MCP → OpenRouter(deepseek-v4-flash) → Airtable 왕복. `OPENROUTER_API` 미설정 시 skip. 격리 실행은 `VEGA_DATA_DIR=/tmp/...`로 빈 DB 생성 후 `mcp.json`·`llm_providers.json` 복사.
