# VEGA 온보딩 가이드

> 처음 설치하는 사용자 및 새 개발자를 위한 단계별 안내

---

## 목차

1. [설치](#1-설치)
2. [첫 실행 흐름](#2-첫-실행-흐름)
3. [LLM 프로바이더 설정](#3-llm-프로바이더-설정)
4. [Google 연동 설정](#4-google-연동-설정)
5. [MCP 서버 연결](#5-mcp-서버-연결)
6. [설정 이후 — 기본 사용법](#6-설정-이후--기본-사용법)
7. [문제 해결](#7-문제-해결)
8. [개발자 온보딩](#8-개발자-온보딩)

---

## 1. 설치

### 요구 사항

| 항목 | 최솟값 |
|------|--------|
| macOS | 11.0 (Big Sur) 이상 |
| 칩 | Apple Silicon (aarch64) |
| 디스크 | 약 500MB |
| 네트워크 | LLM API 호출용 인터넷 연결 |

### 설치 단계

```
1. VEGA-0.1.6.dmg 다운로드 (또는 build_output/ 에서 직접 사용)
2. DMG 마운트 → VEGA.app을 Applications 폴더로 드래그
3. VEGA.app 첫 실행
   ├─ Gatekeeper 경고 뜰 경우: 우클릭 → 열기 → 열기
   └─ 또는: 시스템 설정 → 개인정보 보호 및 보안 → "확인 없이 열기"
```

> **참고**: DMG는 Apple Notary 공증 및 Developer ID 서명이 완료된 빌드다. 정상 환경에서는 Gatekeeper 경고가 뜨지 않는다.

---

## 2. 첫 실행 흐름

```
VEGA.app 실행
    │
    ▼
Rust 셸: LaunchAgent 등록
    ├─ ~/Library/LaunchAgents/com.unohee.vega-backend.plist 생성
    └─ 백엔드 자동 기동 (포트 8100)
    │
    ▼
http://127.0.0.1:8100/entry 로드
    │
    ├─ 온보딩 완료? (user_profile.json 의 onboarded: true)
    │   ├─ YES → /chat (메인 채팅 화면)
    │   └─ NO  → /install (설치 마법사)
    │
    ▼ (첫 실행)
설치 마법사 (/install)
    │
    ├─ 단계 1: LLM 프로바이더 선택 및 인증
    ├─ 단계 2: 사용자 프로필 입력 (이름·역할·소속)
    ├─ 단계 3: Google 연동 (선택)
    └─ 완료 → /chat
```

### 온보딩 완료 조건

다음 중 하나:
- 설치 마법사에서 `finish` 액션 완료
- 또는 직접 API: `POST /api/onboarding/finish`

완료 시 저장:
- `~/Library/Application Support/VEGA/user_profile.json`
- `~/Library/Application Support/VEGA/llm_providers.json`
- macOS Keychain (서비스명: "VEGA")에 API 키 저장

---

## 3. LLM 프로바이더 설정

### 지원 프로바이더

```
┌─────────────────────────────────────────────────────────────────────┐
│  ID            │ 이름                    │ 인증 방식  │ 권장 용도  │
├─────────────────────────────────────────────────────────────────────┤
│ anthropic      │ Anthropic (Claude)       │ API 키     │ 고성능 추론│
│ openai         │ OpenAI API               │ API 키     │ GPT 시리즈 │
│ openrouter     │ OpenRouter               │ API 키     │ 멀티 모델  │  ← 권장
│ chatgpt        │ ChatGPT (Codex)          │ OAuth PKCE │ 코드 특화  │
│ local          │ 로컬 / 온프레미스 서버   │ URL만      │ 오프라인   │
└─────────────────────────────────────────────────────────────────────┘
```

> **추천**: 처음 시작하는 경우 **OpenRouter**가 가장 편리하다. API 키 하나로 Claude·GPT·Gemini·DeepSeek 등 모든 모델에 접근 가능하다.

### OpenRouter 설정 (권장)

```
1. https://openrouter.ai 접속 → 회원가입 또는 로그인
2. Keys → "Create key" → 이름 입력 → 키 복사 (sk-or-v1-...)
3. VEGA 설정 창 → "AI 프로바이더" → "OpenRouter" 선택
4. API 키 입력 → 저장
```

### Anthropic (Claude) 설정

```
1. https://console.anthropic.com 접속 → 로그인
2. API Keys → "Create Key" → 키 복사 (sk-ant-...)
3. VEGA 설정 창 → "AI 프로바이더" → "Anthropic" 선택
4. API 키 입력 → 저장
```

### OpenAI 설정

```
1. https://platform.openai.com 접속 → 로그인
2. API Keys → "Create new secret key" → 키 복사 (sk-...)
3. VEGA 설정 창 → "AI 프로바이더" → "OpenAI API" 선택
4. API 키 입력 → 저장
```

### ChatGPT (PKCE OAuth) 설정

```
1. VEGA 설정 창 → "AI 프로바이더" → "ChatGPT" 선택
2. "ChatGPT로 로그인" 버튼 클릭
3. 브라우저에서 ChatGPT 계정 로그인
4. 인증 완료 → VEGA 자동으로 토큰 저장
```

> **참고**: ChatGPT OAuth 토큰은 만료 시 자동으로 갱신된다. 갱신 실패 시 재로그인 필요.

### 로컬 LLM (LM Studio / Ollama) 설정

```
1. LM Studio 또는 Ollama 설치 및 실행
2. OpenAI 호환 API 서버 기동 (기본: http://localhost:1234/v1)
3. VEGA 설정 창 → "AI 프로바이더" → "로컬 서버" 선택
4. URL 입력 (예: http://localhost:1234/v1)
5. 사용할 모델 ID 입력 (예: gemma-4-e4b-it-mlx)
```

### 설정 창 접근

온보딩 완료 후에도 설정을 변경할 수 있다:

```
방법 1: 상태바(하단) → 설정 아이콘 클릭
방법 2: 트레이 아이콘 → "설정"
방법 3: 키보드 단축키 Cmd+, (설정 창)
```

### API 키 저장 위치 확인

```bash
# 키 출처 진단 API
curl http://127.0.0.1:8100/api/onboarding/key-source

# 응답 예시
{
  "OPENROUTER_API": { "source": "keychain", "masked": "sk-or-v1-****1234" },
  "ANTHROPIC_API_KEY": { "source": "none" }
}
```

---

## 4. Google 연동 설정

Gmail·캘린더·Drive 도구를 사용하려면 Google OAuth 설정이 필요하다.

### 사전 준비: Google Cloud 프로젝트

```
1. https://console.cloud.google.com 접속
2. 새 프로젝트 생성 (또는 기존 프로젝트 사용)
3. API 라이브러리에서 다음 API 활성화:
   - Gmail API
   - Google Calendar API
   - Google Drive API
4. OAuth 동의 화면 구성:
   - 사용자 유형: 외부
   - 앱 이름: VEGA (또는 원하는 이름)
   - 범위: gmail.modify, calendar, drive.readonly 추가
5. OAuth 클라이언트 ID 생성:
   - 애플리케이션 유형: 데스크탑 앱
   - 다운로드: client_secret_xxxx.json
```

### VEGA에 Google 클라이언트 등록

```bash
# 방법 1: 설치 마법사 중 Google 단계에서 자동 처리

# 방법 2: API 직접 호출
curl -X POST http://127.0.0.1:8100/api/onboarding/google/creds \
  -H "Content-Type: application/json" \
  -d '{"client_id": "xxx.apps.googleusercontent.com", "client_secret": "xxx"}'

# 방법 3: 파일 직접 복사
cp ~/Downloads/client_secret_xxx.json \
   ~/Library/Application\ Support/VEGA/google_oauth_client.json
```

### Google OAuth 실행

```bash
# API 호출 시 브라우저에서 동의 화면 열림
curl -X POST http://127.0.0.1:8100/api/onboarding/google/auth

# 또는: 채팅에서 직접 요청
# "Google 캘린더 연동해줘"라고 입력하면 VEGA가 자동으로 안내
```

---

## 5. MCP 서버 연결

MCP(Model Context Protocol) 서버를 연결하면 추가 도구를 VEGA에 통합할 수 있다.

### 상태바에서 MCP 관리

```
채팅 화면 → 하단 상태바 → "MCP" 버튼 클릭
  또는
채팅 화면 → "+" 메뉴 → "MCP 서버 관리"
```

### MCP 서버 추가 방법

**방법 1: UI에서 추가**
```
MCP 관리 창 → 서버 추가
→ 이름: my-server
→ 명령: npx -y @my-company/mcp-server
→ 저장
```

**방법 2: JSON 직접 편집**
```bash
# ~/Library/Application Support/VEGA/mcp.json 편집
{
  "mcpServers": {
    "my-server": {
      "command": "npx",
      "args": ["-y", "@my-company/mcp-server"],
      "env": {
        "MY_API_KEY": "${MY_API_KEY}"
      }
    }
  }
}
```

**방법 3: 채팅에서 추가**
```
# 채팅창에서
"mcp_add_server 도구로 서버 추가해줘: npx -y @modelcontextprotocol/server-filesystem"
```

### 지원 transport 유형

```
stdio: { "command": "npx", "args": [...] }     ← 가장 일반적
sse:   { "url": "http://localhost:3000/sse" }   ← HTTP SSE
```

### MCP 도구 자동 등록

서버 연결 시 VEGA가 자동으로:
1. 도구 목록 조회
2. 프롬프트 인젝션 패턴 검사 (보안)
3. `mcp__{서버명}__{도구명}` 형태로 TOOL_SCHEMAS에 등록

---

## 6. 설정 이후 — 기본 사용법

### 단축키

| 단축키 | 동작 |
|--------|------|
| `Cmd+,` | 설정 창 열기 |

### 채팅 UI 기본 기능

```
┌─────────────────────────────────────────────────────────────────┐
│  [세션 목록]  │  [채팅 영역]                          [설정] │
│               │                                               │
│  ● 오늘 할 일  │  VEGA: 안녕하세요! 무엇을 도와드릴까요?     │
│  ○ 이메일 정리 │                                               │
│  ○ 코드 리뷰   │  나: Gmail에서 오늘 온 중요 메일 정리해줘     │
│               │                                               │
│  [+ 새 세션]  │  VEGA: [tool: gmail_search → gmail_read →    │
│               │         gmail_modify_labels]                  │
│               │         오늘 수신한 중요 메일 5개를 분류했습니다│
│               │  ────────────────────────────────────────────│
│               │  [메시지 입력...]              [/] [📎] [전송]│
├─────────────────────────────────────────────────────────────────┤
│  📁 폴더없음  │  세션ID  │  ⊕ MCP  │  👤 프로필  │  🤖 deepseek│
└─────────────────────────────────────────────────────────────────┘
```

### 슬래시 커맨드

`/`로 시작하는 명령어로 에이전트 행동을 지시한다:

```
/help           — 사용 가능한 커맨드 목록
/new            — 새 세션 시작
/rename <이름>  — 현재 세션 이름 변경
/search <키워드>— 세션 내 대화 검색
/plan           — 플랜 모드 켜기 (쓰기/실행 도구 차단)
/plan-off       — 플랜 모드 끄기
/sessions       — 모든 세션 목록
/resume <UUID>  — 특정 세션 이어서 대화
/context        — 현재 컨텍스트 토큰 수 표시
/who            — 현재 사용자 프로필 확인
```

### 작업 디렉터리 설정

코드 작업 시 VEGA가 참조할 기준 폴더를 지정한다:

```
하단 상태바 → "📁 폴더없음" 클릭 → 폴더 선택
```

지정 후 `bash_exec`, `file_read`, `file_edit` 등 도구가 해당 폴더를 기준으로 동작한다.

### 상태바 표시 항목

| 항목 | 의미 |
|------|------|
| `● MCP` | MCP 서버 관리 |
| `👤 프로필` | 이름·역할·소속 수정 |
| `🤖 deepseek` | 현재 활성 모델 |
| `토큰 수` | 다음 메시지 예상 컨텍스트 크기 |
| `⚠ Docker` | Docker 미기동 경고 (bash/python 도구 불가) |
| `✓ 인증됨` | LLM 프로바이더 정상 인증 |
| `●` (녹색) | 백엔드 서버 정상 |

---

## 7. 문제 해결

### 로그 위치

```bash
# Python 백엔드 로그
tail -f ~/Library/Logs/VEGA/vega-backend.log

# Rust 셸 로그
tail -f ~/Library/Logs/VEGA/vega-shell.log

# LaunchAgent 표준 출력
tail -f ~/Library/Logs/VEGA/vega-backend.stdout.log
```

### 자주 발생하는 문제

#### 백엔드가 시작되지 않는 경우

```bash
# 1. 프로세스 확인
pgrep -af vega-backend

# 2. 포트 점유 확인
lsof -i :8100

# 3. 수동 재시작
launchctl kickstart -k gui/$(id -u)/com.unohee.vega-backend

# 4. LaunchAgent 재등록 (앱 재실행 시 자동)
launchctl bootout gui/$(id -u)/com.unohee.vega-backend
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.unohee.vega-backend.plist
```

#### "No OAuth profile found" 에러

ChatGPT OAuth 토큰이 만료되었거나 없는 경우:

```
설정 창 → AI 프로바이더 → "ChatGPT" → "재로그인"
또는: OpenRouter 등 다른 프로바이더로 전환
```

#### API 키가 인식되지 않는 경우

```bash
# 키 출처 확인
curl http://127.0.0.1:8100/api/onboarding/key-source

# Keychain에서 직접 확인
security find-generic-password -s "VEGA" -a "OPENROUTER_API" -w

# 키 재입력: 설정 창 → AI 프로바이더 → 해당 프로바이더 → 키 재입력
```

#### Docker 도구가 작동하지 않는 경우

상태바에 `⚠ Docker` 경고가 표시되면:

```bash
# Docker Desktop 실행 확인
open -a Docker

# 컨테이너 상태 확인
docker ps -a | grep vega-sandbox

# 컨테이너 시작
docker start vega-sandbox
# 또는 이미지부터 생성
cd ~/dev/vega-agent/sandbox && docker compose up -d
```

#### 도구가 몇 개밖에 안 보이는 경우

헬스 체크로 진단:
```bash
curl http://127.0.0.1:8100/api/health
# {
#   "total_tools": 70,
#   "sandbox": "docker_off",   ← Docker 꺼짐
#   "mcp_tools": 0,            ← MCP 연결 안 됨
#   "auth": "ok"
# }
```

#### 설정 초기화 방법

```bash
# 전체 초기화 (주의: 대화 이력·설정 모두 삭제)
rm -rf ~/Library/Application\ Support/VEGA/

# 인증만 초기화
security delete-generic-password -s "VEGA" 2>/dev/null
rm -f ~/Library/Application\ Support/VEGA/openai_oauth.json
```

---

## 8. 개발자 온보딩

### 개발 환경 설정

```bash
# 1. 저장소 클론
git clone https://github.com/unohee/vega-agent.git
cd vega-agent

# 2. Python 환경 (mlx_env 권장)
source ~/dev/mlx_env/bin/activate
pip install -r requirements.txt

# 3. Rust 환경
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
cargo install tauri-cli@2

# 4. 데이터베이스 초기화
python scripts/init_user_db.py

# 5. 개발 서버 시작
python -m uvicorn web.server:app --host 127.0.0.1 --port 8100 --reload
```

### 디렉터리 구조

```
vega-agent/
├─ bin/                  # PyInstaller 번들 관련
│  ├─ vega-backend.spec  # 빌드 스펙
│  └─ vega_backend_launcher.py  # 진입점
├─ data/                 # 기본/예시 설정 파일 (번들에 포함)
│  ├─ llm_providers.json # LLM 프로바이더 기본 설정
│  ├─ mcp.json           # MCP 서버 설정
│  └─ agents/            # 에이전트 MD 파일
├─ desktop/              # Tauri Rust 셸
│  ├─ src/lib.rs         # 메인 로직
│  └─ tauri.conf.json    # 앱 설정
├─ pipeline/             # 핵심 Python 모듈
│  ├─ streaming.py       # GPT 스트리밍 루프
│  ├─ llm_gateway.py     # 멀티 프로바이더 라우터
│  ├─ tools.py           # 도구 레지스트리
│  ├─ session_store.py   # SQLite 세션 관리
│  ├─ keychain.py        # API 키 관리
│  └─ data_paths.py      # 영속 경로 단일 출처
├─ scripts/              # 빌드/배포 스크립트
│  ├─ build_dmg.sh       # 전체 빌드 스크립트
│  └─ sign_and_notarize.sh  # 서명/공증
├─ tests/                # 테스트
├─ testing/              # 실험용 스크립트
└─ web/                  # FastAPI 앱
   ├─ server.py          # 메인 서버
   ├─ routers/           # 라우터 모듈
   └─ static/            # HTML/JS (chat.html 등)
```

### 핵심 파일 수정 가이드

#### LLM 프로바이더 추가

```python
# data/llm_providers.json에 프로바이더 추가
{
  "providers": {
    "my_provider": {
      "label": "내 프로바이더",
      "kind": "chat_completions",      # chat_completions | anthropic | responses
      "auth_type": "bearer",           # bearer | anthropic_key | chatgpt_oauth | none
      "api_key_env": "MY_API_KEY",
      "base_url": "https://api.my-provider.com/v1",
      "default_model": "my-model"
    }
  }
}

# web/routers/onboarding.py의 PROVIDER_CATALOG에도 추가
PROVIDER_CATALOG.append({
    "id": "my_provider", "label": "내 프로바이더", "auth": "key",
    "key_env": "MY_API_KEY", "key_hint": "sk-...",
    "verify_url": "https://api.my-provider.com/v1/models",
    "verify_header": "bearer",
    "desc": "내 프로바이더 설명",
})
```

#### 새 도구 추가

```python
# pipeline/tools.py — TOOL_SCHEMAS에 스키마 추가
TOOL_SCHEMAS.append({
    "type": "function",
    "function": {
        "name": "my_tool",
        "description": "내 도구 설명",
        "parameters": {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": "입력값"}
            },
            "required": ["input"]
        }
    }
})

# TOOL_FUNCTIONS에 구현 추가
TOOL_FUNCTIONS["my_tool"] = lambda args: my_tool_impl(args["input"])

# dispatch_tool()에서 자동 호출됨
```

#### 영속 파일 경로 추가

```python
# 항상 data_paths.py를 통해 경로를 얻는다
# 직접 Path(__file__) 사용 금지 — PyInstaller onefile에서 임시경로 가리킴

from pipeline.data_paths import data_dir

def my_config_path() -> Path:
    return data_dir() / "my_config.json"
```

### 빌드 및 테스트

```bash
# 단위 테스트
source ~/dev/mlx_env/bin/activate
python -m pytest tests/ -v

# 전체 빌드 (서명/공증 포함)
VEGA_NOTARY_PROFILE=vega-notary bash scripts/build_dmg.sh

# 서명만 (공증 제외, 빠른 로컬 테스트)
bash scripts/build_dmg.sh
# → build_output/VEGA-{VERSION}.dmg

# 공증만 (DMG 이미 있을 때)
VEGA_SIGN_ID="Developer ID Application: Heewon Oh (635QK74RYK)" \
VEGA_NOTARY_PROFILE=vega-notary \
bash scripts/sign_and_notarize.sh --artifact-only build_output/VEGA-0.1.6.dmg
```

### 주요 개발 원칙

1. **onefile 경로 절대 금지**: `Path(__file__)` 쓰기 경로 → 반드시 `data_dir()` 사용
2. **Keychain 우선**: API 키는 `keychain.set_secret()`, 읽기는 `keychain.get()`
3. **영속 데이터**: `~/Library/Application Support/VEGA/` — 업데이트 후에도 유지
4. **번들 데이터는 읽기 전용**: `data/` 디렉터리 내 파일은 참조만, 수정은 `data_dir()`에
5. **MCP 도구 동적 등록**: 서버 기동 시 자동 — `TOOL_SCHEMAS` 직접 수정 불필요

---

*이 문서는 VEGA 0.1.6 기준이다. 최신 정보는 `DEBUGGING.md` 및 각 모듈 헤더 주석을 참조.*
