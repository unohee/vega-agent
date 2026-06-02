# VEGA 개발 보고서

> 현재 버전: **0.1.6** | 최종 업데이트: 2026-06-02

---

## 목차

1. [프로젝트 개요](#1-프로젝트-개요)
2. [아키텍처 전체 구조](#2-아키텍처-전체-구조)
3. [컴포넌트 상세](#3-컴포넌트-상세)
4. [LLM 프로바이더 시스템](#4-llm-프로바이더-시스템)
5. [도구 시스템](#5-도구-시스템)
6. [데이터 영속성](#6-데이터-영속성)
7. [보안 및 인증](#7-보안-및-인증)
8. [배포 파이프라인](#8-배포-파이프라인)
9. [알려진 한계 및 TODO](#9-알려진-한계-및-todo)

---

## 1. 프로젝트 개요

VEGA는 macOS 데스크탑용 **개인 AI 에이전트 하네스**다. 외부 LLM(ChatGPT·OpenRouter·Anthropic 등)을 백엔드로 두고, 이메일·캘린더·코드 실행·파일 관리 등 실제 도구를 LLM에게 위임해 사용자의 일상 업무를 자동화한다.

```
┌────────────────────────────────────────────────────────────────┐
│  사용자                                                         │
│    ↓  Cmd+Shift+V (전역 단축키) 또는 트레이 클릭               │
│  VEGA.app (Tauri/Rust 데스크탑 셸)                             │
│    ↓  http://127.0.0.1:8100                                     │
│  vega-backend (PyInstaller onefile · FastAPI · Python 3.14)    │
│    ↓  OpenAI-호환 API / Anthropic API / OAuth                  │
│  외부 LLM  ←→  로컬 SLM (LM Studio)                           │
└────────────────────────────────────────────────────────────────┘
```

### 핵심 설계 원칙

| 원칙 | 구현 방식 |
|------|-----------|
| **로컬 우선** | 도메인 질의(오늘 일정·현재 이슈 등)는 로컬 SLM으로 처리 |
| **모든 인증은 Keychain** | API 키·OAuth 토큰을 macOS Keychain에 저장, .env는 폴백 |
| **영속 데이터는 Application Support** | `~/Library/Application Support/VEGA/` — 업데이트 후에도 유지 |
| **번들 외부에 쓰지 않는다** | PyInstaller onefile `_MEIPASS`는 읽기전용 임시경로 — 쓰기 금지 |

---

## 2. 아키텍처 전체 구조

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         VEGA.app (macOS 번들)                                │
│                                                                               │
│  ┌──────────────────────────────┐   ┌──────────────────────────────────────┐ │
│  │  Tauri 데스크탑 셸 (Rust)    │   │   vega-backend (Python / onefile)    │ │
│  │                              │   │                                      │ │
│  │  lib.rs                      │   │  bin/vega_backend_launcher.py        │ │
│  │  ├─ ensure_launchagent()     │   │  └─ uvicorn → web/server.py (FastAPI)│ │
│  │  ├─ spawn_backend_directly() │   │                                      │ │
│  │  ├─ 트레이 아이콘            │   │  web/                                │ │
│  │  ├─ Cmd+Shift+V 단축키       │   │  ├─ server.py          (메인 라우터) │ │
│  │  └─ 설정 창(settings.html)   │   │  └─ routers/                         │ │
│  │                              │   │     ├─ onboarding.py                 │ │
│  │  client_config.rs            │   │     ├─ llm.py                        │ │
│  │  └─ 서버 URL / 언어 설정     │   │     ├─ dashboard.py                  │ │
│  │                              │   │     ├─ fs.py                         │ │
│  └──────────────────────────────┘   │     ├─ memory_inspector.py           │ │
│            │ LaunchAgent             │     ├─ scheduler.py                  │ │
│            │ com.unohee.vega-backend │     └─ widgets.py                    │ │
│            ↓                        │                                      │ │
│  ~/Library/LaunchAgents/            │  pipeline/                           │ │
│  com.unohee.vega-backend.plist      │  ├─ streaming.py     (GPT 루프)      │ │
│  → 로그인 시 백엔드 자동 기동       │  ├─ llm_gateway.py   (멀티 프로바이더)│ │
│                                     │  ├─ tools.py         (도구 레지스트리)│ │
│                                     │  ├─ session_store.py (SQLite)        │ │
│                                     │  ├─ keychain.py      (API 키 관리)   │ │
│                                     │  ├─ mcp_client.py    (MCP 클라이언트)│ │
│                                     │  ├─ sandbox.py       (Docker)        │ │
│                                     │  └─ compaction.py   (메모리 압축)    │ │
│                                     └──────────────────────────────────────┘ │
│                                                   │                           │
│                                     ┌─────────────┼──────────────────┐        │
│                                     │             │                  │        │
│                            ┌────────▼──┐  ┌───────▼──────┐  ┌───────▼──────┐ │
│                            │  SQLite   │  │  Keychain /  │  │  Docker      │ │
│                            │  (vega.db)│  │  .env / env  │  │  (샌드박스)  │ │
│                            └───────────┘  └──────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    ↓                 ↓                  ↓
             OpenRouter         Anthropic            LM Studio
           (cloud tier)       (cloud tier)         (local tier)
```

### 요청 처리 흐름

```
사용자 입력
    │
    ▼
web/server.py: handle_slash()
    │ 슬래시 커맨드?
    ├─ YES → commands.py: expand_command() → LLM 프롬프트로 변환
    └─ NO ──────────────────────────────────────────────────────▶
                                                                 │
                                                        tier_router.route_tier()
                                                        │
                                              ┌─────────┴────────┐
                                          cloud 신호          local 신호
                                          (작성/검색/분석)    (오늘/현재/진행중)
                                              │                  │
                                              ▼                  ▼
                                      llm_gateway             llm_gateway
                                      cloud tier              local tier
                                      (OpenRouter 등)         (LM Studio)
                                              │                  │
                                              └────────┬─────────┘
                                                       ▼
                                             streaming.py: stream_gpt()
                                             - 시스템 프롬프트 조립
                                             - 대화 이력 포함
                                             - 도구 스키마 전달
                                                       │
                                              ┌────────▼──────────┐
                                              │  LLM 응답 스트림  │
                                              │  tool_call 감지   │
                                              └────────┬──────────┘
                                                       │ tool_call?
                                             ┌─────────┴──────────┐
                                             │                    │
                                         YES: dispatch_tool()   NO: 텍스트 출력
                                         └─ tools.py / mcp_client.py
                                                       │
                                                결과 → 다음 LLM 턴
```

---

## 3. 컴포넌트 상세

### 3.1 Tauri 데스크탑 셸 (`desktop/`)

**역할**: Rust로 작성된 macOS 네이티브 래퍼. Python 백엔드를 LaunchAgent로 관리하고 WebView 창으로 chat UI를 표시한다.

```
desktop/
├─ src/
│  ├─ lib.rs              # 메인 로직: LaunchAgent 등록, 트레이, 단축키
│  ├─ main.rs             # Tauri 진입점
│  └─ client_config.rs   # CE(클라이언트) 모드: 원격 서버 URL 저장
├─ tauri.conf.json        # 앱 메타데이터, 번들 설정, updater 설정
├─ Cargo.toml             # 의존성: tauri, tauri-plugin-updater, dirs-next
├─ dist/                  # WebView 파일 (settings.html, client-settings.html)
└─ entitlements.plist     # disable-library-validation (PYI-30816 회피)
```

**LaunchAgent 등록 흐름**:

```
앱 첫 실행
    │
    ▼
ensure_launchagent()
    1. ~/Library/Logs/VEGA/ 생성 (launchd가 자동 생성 안 함)
    2. VEGA.app/Contents/Resources/com.unohee.vega-backend.plist 읽기
    3. __HOME__ → 실제 홈 디렉터리로 치환
    4. ~/Library/LaunchAgents/com.unohee.vega-backend.plist 저장
    5. launchctl bootout gui/{uid}/com.unohee.vega-backend (기존 제거)
    6. launchctl bootstrap gui/{uid} <plist> (새 등록)
    7. launchctl kickstart -k gui/{uid}/com.unohee.vega-backend (즉시 시작)
    │
    ├─ 성공 → wait_and_navigate() → /entry 로드
    └─ 실패 → spawn_backend_directly() (직접 실행 폴백)
```

**빌드 모드**:
- `--features daemon` (기본): 백엔드 sidecar 포함 올인원
- `--features client`: 백엔드 없이 원격 서버 URL에 연결

### 3.2 Python 백엔드 (`web/`, `pipeline/`)

**진입점**: `bin/vega_backend_launcher.py`

```python
# 초기화 순서
1. sys._MEIPASS (번들 임시 루트) 설정
2. certifi CA 경로 → SSL_CERT_FILE 강제 (clean-install SSL 문제 해결)
3. RotatingFileHandler → ~/Library/Logs/VEGA/vega-backend.log
4. uvicorn.run("web.server:app", host="127.0.0.1", port=8100, log_config=None)
```

**FastAPI 앱 구조** (`web/server.py`):

| 엔드포인트 그룹 | 경로 | 설명 |
|---|---|---|
| 페이지 | `GET /`, `/entry`, `/chat`, `/install` | WebView HTML 서빙 |
| 헬스 | `GET /api/health` | 인증·샌드박스·MCP 도구 수 진단 |
| 채팅 | `POST /api/chat/stream` | SSE 스트리밍 대화 (메인 엔드포인트) |
| 세션 | `GET/POST /api/sessions`, `DELETE /api/sessions/{sid}` | 세션 CRUD |
| 파일 업로드 | `POST /api/upload`, `/api/upload-image` | 첨부파일 처리 |
| 터미널 | `WS /api/terminal/{sid}` | WebSocket PTY |
| 관리자 | `POST /api/admin/keys` | Enterprise 키 관리 (로컬 전용) |
| 온보딩 | `/api/onboarding/*` | 설치 마법사 (별도 라우터) |
| LLM 관리 | `/api/llm/*` | 프로바이더·모델 CRUD (별도 라우터) |
| 대시보드 | `/api/dashboard/*` | 위젯 데이터 (별도 라우터) |
| 파일시스템 | `/api/fs/*` | 파일 브라우저 (별도 라우터) |
| 메모리 | `/api/memory/*` | 메모리 인스펙터 (별도 라우터) |
| 스케줄러 | `/api/scheduler/*` | 예약 작업 (별도 라우터) |

**접근 레벨**:
```
loopback (127.0.0.1)  → full 권한 (모든 도구)
LAN               → CE 모드 (외부 SaaS만, 로컬 파일/시스템 차단)
Enterprise 키 제시 → full 권한 (원격에서도)
```

### 3.3 스트리밍 GPT 루프 (`pipeline/streaming.py`)

대화 1턴의 전체 처리를 담당한다.

```
stream_gpt(history, images, working_dir, provider)
    │
    ├─ build_system(working_dir)         # 정적 시스템 프롬프트 (캐싱됨)
    │   ├─ 페르소나 (vega_query.get_persona)
    │   ├─ 작업 디렉터리 목록
    │   └─ 에이전트 MD (_agent_dir()/agents/*.md)
    │
    ├─ build_dynamic_preamble()          # 매 턴 갱신 (30분 캐시)
    │   ├─ 현재 시각 (KST)
    │   ├─ Linear 진행 중 이슈
    │   ├─ 이번 주 캘린더
    │   └─ 중요 메일 (24h)
    │
    ├─ get_schemas_for_mode()            # 활성화된 도구 스키마 필터
    │
    └─ _build_request() → SSE 스트림
        │
        ├─ 텍스트 청크 → yield
        └─ tool_call 감지 → dispatch_tool() → 결과 → 재귀 턴
```

**시스템 프롬프트 레이어**:
```
[정적 - 캐싱]        [동적 - 30분 TTL]     [사용자 정의]
  페르소나             현재 시각              data_dir()/agents/
  작업폴더             Linear 이슈              _default.md  (불변 헌법)
  슬래시 커맨드        캘린더                   RULES.md     (에이전트가 수정)
  에이전트 MD          중요 메일                {provider}.md (프로바이더별 힌트)
```

### 3.4 컨텍스트 압축 (`pipeline/compaction.py`)

```
대화 이력 >= 20 메시지 OR 토큰 초과
    │
    ▼
_compact_history()
    ├─ 최근 6개 메시지 보존 (KEEP_RECENT)
    ├─ 나머지를 압축 LLM으로 요약
    ├─ memory_save / rule_save 도구 호출로 메모리 갱신
    └─ 압축 요약 + 최근 6개로 이력 교체

세션 종료 / 주기적 호출
    └─ heartbeat.py: _lms_title_session()
        ├─ 대화 내용으로 세션 제목 자동 생성
        └─ session_store.rename_session() + _save_session_digest()
```

### 3.5 자가 개선 (`pipeline/self_improve.py`)

```
도구 연속 실패 감지 (CONSECUTIVE_THRESHOLD = 2)
    │
    ▼
_trigger_improvement(tool_name, failures)
    ├─ 도구 소스 코드 추출
    ├─ GPT로 패치 생성
    ├─ sandbox에서 테스트 실행
    ├─ 테스트 통과 → 사용자 승인 요청
    └─ 승인 → 실제 파일에 패치 적용

보호 도구 (패치 불가):
    gmail_send, calendar_create_event, bash_exec, python_exec 등
```

---

## 4. LLM 프로바이더 시스템

### 4.1 프로바이더 설정 (`data/llm_providers.json`)

```json
{
  "active": "openrouter",
  "tiers": {
    "local": "lmstudio",
    "cloud": "openrouter"
  },
  "providers": {
    "chatgpt":    { "kind": "responses",          "auth_type": "chatgpt_oauth" },
    "openrouter": { "kind": "chat_completions",   "auth_type": "bearer",      "api_key_env": "OPENROUTER_API" },
    "anthropic":  { "kind": "anthropic",          "auth_type": "anthropic_key","api_key_env": "ANTHROPIC_API_KEY" },
    "openai":     { "kind": "chat_completions",   "auth_type": "bearer",      "api_key_env": "OPENAI_API_KEY" },
    "lmstudio":   { "kind": "chat_completions",   "auth_type": "none",        "base_url": "http://localhost:1234/v1" }
  }
}
```

### 4.2 2단 티어 라우터 (`pipeline/tier_router.py`)

```
사용자 입력
    │
    ▼
route_tier(text) - 휴리스틱 기반 (LLM 추론 없음, 0 latency)
    │
    ├─ cloud 신호 감지: 작성|써줘|메일|요약|검색|분석|코드|번역|계획|추천
    │   → cloud tier (OpenRouter · Anthropic 등)
    │
    ├─ local 신호 감지: 오늘|현재|급한|우선순위|여유|담당|진행중|카드|이슈
    │   → local tier (LM Studio SLM)
    │
    └─ 신호 없음 → cloud (안전한 기본값)

local 티어 다운 시 → cloud 자동 폴백
```

### 4.3 API 키 우선순위

```
get_key("OPENROUTER_API")
    │
    1. macOS Keychain (서비스명: "VEGA")
    2. ~/Library/Application Support/VEGA/.env
    3. 레포 루트 .env (개발 환경)
    4. 환경변수 (os.environ)
    5. 기본값 ""
```

---

## 5. 도구 시스템

### 5.1 도구 카테고리

```
VEGA 도구 (총 ~70개 + MCP 동적 추가)
│
├─ 웹               web_search, web_fetch
├─ Gmail            gmail_search/read/send/draft/modify_labels
├─ 캘린더           calendar_list/create/update/delete_event
├─ Google Drive     drive_search, drive_read
├─ 파일시스템       file_read, file_edit
├─ iCloud Drive     icloud_list/move/rename/mkdir
├─ 오피스           xlsx_*, docx_*, pptx_* (openpyxl/mammoth)
├─ 코드 실행        bash_exec, python_exec, host_exec, sandbox_*
├─ 메모리           memory_persona_update, memory_event_add, memory_entity_upsert
├─ 세션             session_list, session_delete, session_clean
├─ 이미지           image_generate
├─ 슬라이드/문서    slides_create, docs_create
├─ Linear           linear_list/get/search/create/update_issue, linear_add_comment
├─ Discord          discord_notify
├─ 커스텀 스킬      skill_save, skill_delete (슬래시 커맨드)
├─ 위젯             widget_save, widget_delete
├─ 규칙             rule_save, rule_delete, rule_list
└─ MCP              mcp_list/add/remove_server, mcp_reload + 동적 등록 도구
```

### 5.2 도구 실행 흐름

```
LLM이 tool_call 반환
    │
    ▼
dispatch_tool(name, arguments)
    │
    ├─ 플랜 모드? → 쓰기/실행 도구 차단 → "플랜 모드 활성화" 메시지 반환
    ├─ CE 모드? → CE_ALLOWED_TOOLS 외 차단
    │
    ├─ MCP 도구 (mcp__ 접두사)? → dispatch_tool_async() → mcp_client
    │
    └─ 일반 도구 → TOOL_FUNCTIONS[name](arguments)
        ├─ tools_google.py  (Gmail/Calendar/Drive/Linear)
        ├─ tools_web.py     (web_search/fetch)
        ├─ tools_code.py    (bash_exec/python_exec/sandbox_*)
        ├─ tools_office.py  (xlsx/docx/pptx)
        └─ tools.py 내장    (memory/session/rule/skill/widget)
```

### 5.3 코드 실행 샌드박스

```
bash_exec / python_exec
    │
    ├─ Docker 사용 가능?
    │   ├─ YES: vega-sandbox 컨테이너 exec
    │   │       /vega_data → ~/Library/Application Support/VEGA/ (rw 마운트)
    │   │       /project   → 작업 디렉터리 (세션별 설정)
    │   │       /host_home → 홈 디렉터리 (ro 마운트)
    │   └─ NO:  host_exec 경로 (Docker 없을 때 폴백)
    │
    └─ 경로 자동 변환:
        ~/...경로 → /host_home/...
        ~/Library/Application Support/VEGA/ → /vega_data/
```

### 5.4 MCP 클라이언트 (`pipeline/mcp_client.py`)

```
서버 기동 시 (FastAPI lifespan)
    │
    ▼
ensure_mcp_loaded()
    ├─ data_dir()/mcp.json 읽기
    ├─ 각 서버 stdio/sse 연결
    ├─ 도구 목록 조회
    ├─ 프롬프트 인젝션 패턴 검사 (_sanitize_mcp_description)
    └─ TOOL_SCHEMAS에 동적 추가 (mcp__{server}__{tool} 접두사)

실행 시: dispatch_tool_async() → fastmcp.Client.call_tool()
```

---

## 6. 데이터 영속성

### 6.1 디렉터리 구조

```
~/Library/Application Support/VEGA/    ← data_dir() — 모든 영속 데이터
├─ vega.db                             # 메인 SQLite (대화·메모리·이벤트)
├─ llm_providers.json                  # 활성 LLM 프로바이더 설정
├─ mcp.json                            # MCP 서버 설정
├─ user_profile.json                   # 사용자 프로필 (이름·역할·이메일 등)
├─ tool_groups.json                    # 도구 그룹 활성화 설정
├─ tool_telemetry.db                   # 도구 사용 통계
├─ .env                                # API 키 (Keychain 폴백)
├─ agents/
│  ├─ _default.md                      # 에이전트 헌법 (불변)
│  └─ RULES.md                         # 사용자 정의 규칙 (에이전트가 수정)
├─ commands/                           # 사용자 정의 슬래시 커맨드
├─ uploads/                            # 채팅 첨부파일
└─ charts/                             # 생성된 차트

~/Library/Logs/VEGA/                   ← log_dir()
├─ vega-backend.log                    # Python 백엔드 로그 (5MB × 5 순환)
└─ vega-shell.log                      # Rust 셸 로그

~/Library/LaunchAgents/
└─ com.unohee.vega-backend.plist       # 로그인 시 백엔드 자동 기동
```

### 6.2 SQLite 스키마 (`vega.db`)

```sql
-- 대화 세션
conversations (
    uuid TEXT PRIMARY KEY,
    name TEXT,
    created_at TEXT,
    updated_at TEXT,
    msg_count INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0,
    working_dir TEXT
)

-- 메시지
messages (
    uuid TEXT PRIMARY KEY,
    session_uuid TEXT REFERENCES conversations(uuid),
    role TEXT,           -- user / assistant / tool
    content TEXT,
    usage_meta TEXT,     -- JSON: {model, input_tokens, output_tokens, cost_usd, ttft_sec}
    created_at TEXT
)

-- 페르소나 섹션
persona_sections (id, section_key, content, scope, version, is_active, updated_at)

-- 이벤트
events (id, title, date, time, category, notes, created_at)

-- 엔티티
entities (id, name, category, context, last_seen, notes)

-- 프로젝트 상태
project_state (id, name, status, metrics_json, risks_json, next_actions_json, ...)

-- 메모리
memory_entries (id, content, embedding, created_at, ...)
```

---

## 7. 보안 및 인증

### 7.1 API 키 관리

```
키 저장 우선순위
    1. macOS Keychain  (서비스: "VEGA", 계정: 키 이름)    ← 권장
    2. ~/Library/Application Support/VEGA/.env           ← 차선
    3. 레포 루트 .env                                     ← 개발 전용
    4. 환경변수                                           ← CI/CD
    5. 기본값 ""

키 출처 진단: GET /api/onboarding/key-source
→ { "OPENROUTER_API": { "source": "keychain", "masked": "sk-or-v1-****1234" } }
```

### 7.2 ChatGPT OAuth (PKCE)

```
POST /api/onboarding/pkce
    │
    ├─ PKCE code_verifier / code_challenge 생성
    ├─ 브라우저에서 ChatGPT 로그인 페이지 열기
    ├─ 사용자 로그인 → redirect_uri로 authorization_code 수신
    ├─ code_verifier로 access_token + refresh_token 교환
    └─ ~/Library/Application Support/VEGA/openai_oauth.json 저장

토큰 만료 시: ensure_valid_token() → refresh_token으로 자동 갱신
```

### 7.3 접근 제어

```
요청 출처 판단
    │
    ├─ 127.0.0.1 (loopback) → "full" 권한
    ├─ X-Enterprise-Key 헤더 일치 → "full" 권한
    └─ 기타 (LAN 등) → "ce" 권한 (CE 모드)

CE 모드 제한:
    ✓ 허용: 웹 검색, Gmail, 캘린더, Drive, Linear, Discord, 메모리 읽기
    ✗ 차단: file_read/edit, host_exec, bash_exec, icloud_*, 시스템 도구
```

---

## 8. 배포 파이프라인

### 8.1 빌드 단계

```
bash scripts/build_dmg.sh
│
├─ [0/5] PyInstaller → bin/vega-backend (94MB onefile)
│   ├─ bin/vega-backend.spec
│   ├─ certifi 데이터 번들
│   ├─ fastmcp metadata (copy_metadata)
│   └─ bin/.venv 격리 환경
│
├─ [1/5] cargo tauri build --target aarch64-apple-darwin --bundles app
│   ├─ APPLE_SIGNING_IDENTITY 미설정 (adhoc 빌드)
│   └─ TAURI_SIGNING_PRIVATE_KEY → updater .sig 생성
│
├─ [1.5/5] sign_and_notarize.sh (앱 재서명)
│   ├─ vega-backend codesign + entitlements
│   ├─ vega-desktop codesign + entitlements
│   └─ VEGA.app deep 재서명 (disable-library-validation 필수)
│
├─ [2/5] hdiutil → DMG 스테이징
│
├─ [3/5] hdiutil create → VEGA-{VERSION}.dmg
│
├─ [4/5] DMG 서명 + notarytool 공증 + staple
│   ├─ VEGA_NOTARY_PROFILE=vega-notary 필요
│   └─ Gatekeeper: "Notarized Developer ID accepted"
│
└─ [4.5/5] updater 아티팩트 (.app.tar.gz + .sig)
    └─ CF R2에 업로드 (endpoints PLACEHOLDER 교체 필요)
```

### 8.2 버전 히스토리

| 버전 | 날짜 | 주요 변경 |
|------|------|-----------|
| 0.1.1 | 2026-05-31 | 첫 배포 |
| 0.1.2 | 2026-06-01 | SSL·서명·LaunchAgent 패치 (clean-install 실패 수정) |
| 0.1.3 | 2026-06-01 | SSL 이중방어, certifi 자동화, 서명/공증 자동화 |
| 0.1.4 | 2026-06-02 | onefile 경로 버그(OAuth 토큰), 시스템 로그 추가, 설정 창 API 키 입력 |
| 0.1.5 | 2026-06-02 | onefile write 경로 5종 영속화, 인증 상태 프로바이더 인식, 프로필 버튼 |
| 0.1.6 | 2026-06-02 | onefile 경로 추가 수정(commands/streaming/llm router), MCP 상태바 버튼, Docker 경고 |

### 8.3 알려진 빌드 함정

| 함정 | 증상 | 해결 |
|------|------|------|
| PyInstaller onefile `Path(__file__)` | 쓰기 실패 (읽기전용 `_MEIPASS`) | `data_dir()` 사용, fallback만 `Path(__file__)` |
| fastmcp `PackageNotFoundError` | 기동 즉시 죽음 | `copy_metadata("fastmcp", "mcp", "openai", "anthropic")` |
| Tauri `--bundles dmg` | create-dmg hang (osascript/Finder) | `--bundles app` + hdiutil로 DMG 직접 생성 |
| `errSecInternalComponent` | codesign 실패 | 키체인 unlock + `security set-key-partition-list` |
| bash 3.2 빈 배열 unbound | `set -u`에서 `"${arr[@]}"` 오류 | `"${arr[@]+"${arr[@]}"}"`  패턴 |
| notarytool 업로드 hang | "initiating connection"에서 멈춤 | `xcrun notarytool history`로 제출 ID 확인, 없으면 재시도 |

---

## 9. 알려진 한계 및 TODO

### 현재 미구현

| 기능 | 상태 | 비고 |
|------|------|------|
| CF R2 자동 업데이트 엔드포인트 | PLACEHOLDER | `tauri.conf.json` endpoints 교체 필요 |
| Windows / Linux 빌드 | 미지원 | macOS 전용 (LaunchAgent, Keychain) |
| 멀티 사용자 | 미지원 | 단일 Keychain 서비스 "VEGA" |
| iOS 빌드 | 프로토타입 | `feat/ios-app-prototype` 브랜치, CoreSimulator sudo 장벽 존재 |
| 테스트 커버리지 | 낮음 | 핵심 모듈 대부분 untested (cxt 레지스트리 기준) |

### 기술 부채

- `data/commands/` (번들 내 기본 커맨드)가 번들 임시경로에 있어 사용자가 추가한 커맨드가 `user_commands_dir()`에만 쓰임 — 번들 커맨드 목록이 onefile에서 보이지 않을 수 있음
- `vega.db` 경로가 `data_paths.db_path()`로 통일됐지만 일부 테스트가 하드코딩 경로 사용
- Docker 샌드박스 이미지(`vega-sandbox:latest`) 빌드 스크립트 미포함

---

*이 문서는 `feat/ios-app-prototype` 브랜치 HEAD(커밋 `ae658de`) 기준으로 작성됐다.*
