# VEGA Development Report

> Current version: **0.1.6** | Last updated: 2026-06-02

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Overall Architecture](#2-overall-architecture)
3. [Component Details](#3-component-details)
4. [LLM Provider System](#4-llm-provider-system)
5. [Tool System](#5-tool-system)
6. [Data Persistence](#6-data-persistence)
7. [Security and Authentication](#7-security-and-authentication)
8. [Deployment Pipeline](#8-deployment-pipeline)
9. [Known Limitations and TODO](#9-known-limitations-and-todo)

---

## 1. Project Overview

VEGA is a **personal AI agent harness** for the macOS desktop. With an external LLM (ChatGPT, OpenRouter, Anthropic, etc.) as its backend, it delegates real tools — email, calendar, code execution, file management, and more — to the LLM in order to automate the user's everyday tasks.

```
┌────────────────────────────────────────────────────────────────┐
│  User                                                           │
│    ↓  tray click                                               │
│  VEGA.app (Tauri/Rust desktop shell)                          │
│    ↓  http://127.0.0.1:8100                                     │
│  vega-backend (PyInstaller onefile · FastAPI · Python 3.14)    │
│    ↓  OpenAI-compatible API / Anthropic API / OAuth           │
│  External LLM  ←→  Local SLM (LM Studio)                      │
└────────────────────────────────────────────────────────────────┘
```

### Core Design Principles

| Principle | Implementation |
|------|-----------|
| **Local-first** | Domain queries (today's schedule, current issues, etc.) are handled by the local SLM |
| **All authentication in Keychain** | API keys and OAuth tokens are stored in the macOS Keychain; .env is a fallback |
| **Persistent data in Application Support** | `~/Library/Application Support/VEGA/` — retained even after updates |
| **Never write outside the bundle** | The PyInstaller onefile `_MEIPASS` is a read-only temp path — writing is prohibited |

---

## 2. Overall Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         VEGA.app (macOS bundle)                              │
│                                                                               │
│  ┌──────────────────────────────┐   ┌──────────────────────────────────────┐ │
│  │  Tauri desktop shell (Rust)  │   │   vega-backend (Python / onefile)    │ │
│  │                              │   │                                      │ │
│  │  lib.rs                      │   │  bin/vega_backend_launcher.py        │ │
│  │  ├─ ensure_launchagent()     │   │  └─ uvicorn → web/server.py (FastAPI)│ │
│  │  ├─ spawn_backend_directly() │   │                                      │ │
│  │  ├─ tray icon                │   │  web/                                │ │
│  │  ├─ tray window toggle       │   │  ├─ server.py          (main router) │ │
│  │  └─ settings window          │   │  └─ routers/                         │ │
│  │     (settings.html)          │   │     ├─ onboarding.py                 │ │
│  │  client_config.rs            │   │     ├─ llm.py                        │ │
│  │  └─ server URL / language    │   │     ├─ dashboard.py                  │ │
│  │                              │   │     ├─ fs.py                         │ │
│  └──────────────────────────────┘   │     ├─ memory_inspector.py           │ │
│            │ LaunchAgent             │     ├─ scheduler.py                  │ │
│            │ com.unohee.vega-backend │     └─ widgets.py                    │ │
│            ↓                        │                                      │ │
│  ~/Library/LaunchAgents/            │  pipeline/                           │ │
│  com.unohee.vega-backend.plist      │  ├─ streaming.py     (GPT loop)      │ │
│  → auto-start backend at login      │  ├─ llm_gateway.py   (multi-provider)│ │
│                                     │  ├─ tools.py         (tool registry) │ │
│                                     │  ├─ session_store.py (SQLite)        │ │
│                                     │  ├─ keychain.py      (API key mgmt)  │ │
│                                     │  ├─ mcp_client.py    (MCP client)    │ │
│                                     │  ├─ sandbox.py       (Docker)        │ │
│                                     │  └─ compaction.py   (memory compact) │ │
│                                     └──────────────────────────────────────┘ │
│                                                   │                           │
│                                     ┌─────────────┼──────────────────┐        │
│                                     │             │                  │        │
│                            ┌────────▼──┐  ┌───────▼──────┐  ┌───────▼──────┐ │
│                            │  SQLite   │  │  Keychain /  │  │  Docker      │ │
│                            │  (vega.db)│  │  .env / env  │  │  (sandbox)   │ │
│                            └───────────┘  └──────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    ↓                 ↓                  ↓
             OpenRouter         Anthropic            LM Studio
           (cloud tier)       (cloud tier)         (local tier)
```

### Request Handling Flow

```
User input
    │
    ▼
web/server.py: handle_slash()
    │ slash command?
    ├─ YES → commands.py: expand_command() → convert to LLM prompt
    └─ NO ──────────────────────────────────────────────────────▶
                                                                 │
                                                        tier_router.route_tier()
                                                        │
                                              ┌─────────┴────────┐
                                          cloud signal        local signal
                                          (write/search/      (today/current/
                                           analyze)            in-progress)
                                              │                  │
                                              ▼                  ▼
                                      llm_gateway             llm_gateway
                                      cloud tier              local tier
                                      (OpenRouter etc.)       (LM Studio)
                                              │                  │
                                              └────────┬─────────┘
                                                       ▼
                                             streaming.py: stream_gpt()
                                             - assemble system prompt
                                             - include conversation history
                                             - pass tool schemas
                                                       │
                                              ┌────────▼──────────┐
                                              │  LLM response     │
                                              │  stream           │
                                              │  detect tool_call │
                                              └────────┬──────────┘
                                                       │ tool_call?
                                             ┌─────────┴──────────┐
                                             │                    │
                                         YES: dispatch_tool()   NO: text output
                                         └─ tools.py / mcp_client.py
                                                       │
                                                result → next LLM turn
```

---

## 3. Component Details

### 3.1 Tauri Desktop Shell (`desktop/`)

**Role**: A native macOS wrapper written in Rust. It manages the Python backend as a LaunchAgent and displays the chat UI in a WebView window.

```
desktop/
├─ src/
│  ├─ lib.rs              # main logic: LaunchAgent registration, tray, shortcuts
│  ├─ main.rs             # Tauri entry point
│  └─ client_config.rs   # CE (client) mode: stores remote server URL
├─ tauri.conf.json        # app metadata, bundle config, updater config
├─ Cargo.toml             # dependencies: tauri, tauri-plugin-updater, dirs-next
├─ dist/                  # WebView files (settings.html, client-settings.html)
└─ entitlements.plist     # disable-library-validation (works around PYI-30816)
```

**LaunchAgent registration flow**:

```
First app launch
    │
    ▼
ensure_launchagent()
    1. Create ~/Library/Logs/VEGA/ (launchd does not create it automatically)
    2. Read VEGA.app/Contents/Resources/com.unohee.vega-backend.plist
    3. Replace __HOME__ → actual home directory
    4. Save ~/Library/LaunchAgents/com.unohee.vega-backend.plist
    5. launchctl bootout gui/{uid}/com.unohee.vega-backend (remove existing)
    6. launchctl bootstrap gui/{uid} <plist> (register new)
    7. launchctl kickstart -k gui/{uid}/com.unohee.vega-backend (start immediately)
    │
    ├─ success → wait_and_navigate() → load /entry
    └─ failure → spawn_backend_directly() (direct-execution fallback)
```

**Build modes**:
- `--features daemon` (default): all-in-one with the backend sidecar included
- `--features client`: connects to a remote server URL without a backend

### 3.2 Python Backend (`web/`, `pipeline/`)

**Entry point**: `bin/vega_backend_launcher.py`

```python
# initialization order
1. Set sys._MEIPASS (bundle temp root)
2. certifi CA path → force SSL_CERT_FILE (fixes clean-install SSL issue)
3. RotatingFileHandler → ~/Library/Logs/VEGA/vega-backend.log
4. uvicorn.run("web.server:app", host="127.0.0.1", port=8100, log_config=None)
```

**FastAPI app structure** (`web/server.py`):

| Endpoint group | Path | Description |
|---|---|---|
| Pages | `GET /`, `/entry`, `/chat`, `/install` | Serves WebView HTML |
| Health | `GET /api/health` | Diagnoses auth, sandbox, and MCP tool count |
| Chat | `POST /api/chat/stream` | SSE streaming conversation (main endpoint) |
| Sessions | `GET/POST /api/sessions`, `DELETE /api/sessions/{sid}` | Session CRUD |
| File upload | `POST /api/upload`, `/api/upload-image` | Attachment handling |
| Terminal | `WS /api/terminal/{sid}` | WebSocket PTY |
| Admin | `POST /api/admin/keys` | Enterprise key management (local only) |
| Onboarding | `/api/onboarding/*` | Install wizard (separate router) |
| LLM management | `/api/llm/*` | Provider and model CRUD (separate router) |
| Dashboard | `/api/dashboard/*` | Widget data (separate router) |
| Filesystem | `/api/fs/*` | File browser (separate router) |
| Memory | `/api/memory/*` | Memory inspector (separate router) |
| Scheduler | `/api/scheduler/*` | Scheduled tasks (separate router) |

**Access levels**:
```
loopback (127.0.0.1)  → full access (all tools)
LAN               → CE mode (external SaaS only, local file/system blocked)
Enterprise key presented → full access (even remotely)
```

### 3.3 Streaming GPT Loop (`pipeline/streaming.py`)

Handles the entire processing of a single conversation turn.

```
stream_gpt(history, images, working_dir, provider)
    │
    ├─ build_system(working_dir)         # static system prompt (cached)
    │   ├─ persona (vega_query.get_persona)
    │   ├─ working directory list
    │   └─ agent MD (_agent_dir()/agents/*.md)
    │
    ├─ build_dynamic_preamble()          # refreshed every turn (30-min cache)
    │   ├─ current time (KST)
    │   ├─ Linear in-progress issues
    │   ├─ this week's calendar
    │   └─ important mail (24h)
    │
    ├─ get_schemas_for_mode()            # filter for enabled tool schemas
    │
    └─ _build_request() → SSE stream
        │
        ├─ text chunk → yield
        └─ detect tool_call → dispatch_tool() → result → recursive turn
```

**System prompt layers**:
```
[static - cached]    [dynamic - 30-min TTL] [user-defined]
  persona              current time           data_dir()/agents/
  working folder       Linear issues            _default.md  (immutable constitution)
  slash commands       calendar                 RULES.md     (edited by the agent)
  agent MD             important mail           {provider}.md (per-provider hints)
```

### 3.4 Context Compaction (`pipeline/compaction.py`)

```
conversation history >= 20 messages OR token limit exceeded
    │
    ▼
_compact_history()
    ├─ keep the 6 most recent messages (KEEP_RECENT)
    ├─ summarize the rest with the compaction LLM
    ├─ update memory via memory_save / rule_save tool calls
    └─ replace history with the compacted summary + the 6 recent messages

session end / periodic call
    └─ heartbeat.py: _lms_title_session()
        ├─ auto-generate a session title from the conversation content
        └─ session_store.rename_session() + _save_session_digest()
```

### 3.5 Self-Improvement (`pipeline/self_improve.py`)

```
detect consecutive tool failures (CONSECUTIVE_THRESHOLD = 2)
    │
    ▼
_trigger_improvement(tool_name, failures)
    ├─ extract the tool's source code
    ├─ generate a patch with GPT
    ├─ run tests in the sandbox
    ├─ tests pass → request user approval
    └─ approved → apply the patch to the actual file

protected tools (cannot be patched):
    gmail_send, calendar_create_event, bash_exec, python_exec, etc.
```

---

## 4. LLM Provider System

### 4.1 Provider Configuration (`data/llm_providers.json`)

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

### 4.2 Two-Tier Router (`pipeline/tier_router.py`)

```
User input
    │
    ▼
route_tier(text) - heuristic-based (no LLM inference, 0 latency)
    │
    ├─ detect cloud signal: 작성|써줘|메일|요약|검색|분석|코드|번역|계획|추천
    │   (write|write-for-me|mail|summarize|search|analyze|code|translate|plan|recommend)
    │   → cloud tier (OpenRouter · Anthropic, etc.)
    │
    ├─ detect local signal: 오늘|현재|급한|우선순위|여유|담당|진행중|카드|이슈
    │   (today|current|urgent|priority|free|assignee|in-progress|card|issue)
    │   → local tier (LM Studio SLM)
    │
    └─ no signal → cloud (safe default)

when the local tier is down → automatic fallback to cloud
```

### 4.3 API Key Priority

```
get_key("OPENROUTER_API")
    │
    1. macOS Keychain (service name: "VEGA")
    2. ~/Library/Application Support/VEGA/.env
    3. repo root .env (development environment)
    4. environment variable (os.environ)
    5. default ""
```

---

## 5. Tool System

### 5.1 Tool Categories

```
VEGA tools (~70 total + dynamically added MCP)
│
├─ Web              web_search, web_fetch
├─ Gmail            gmail_search/read/send/draft/modify_labels
├─ Calendar         calendar_list/create/update/delete_event
├─ Google Drive     drive_search, drive_read
├─ Filesystem       file_read, file_edit
├─ iCloud Drive     icloud_list/move/rename/mkdir
├─ Office           xlsx_*, docx_*, pptx_* (openpyxl/mammoth)
├─ Code execution   bash_exec, python_exec, host_exec, sandbox_*
├─ Memory           memory_persona_update, memory_event_add, memory_entity_upsert
├─ Sessions         session_list, session_delete, session_clean
├─ Images           image_generate
├─ Slides/Docs      slides_create, docs_create
├─ Linear           linear_list/get/search/create/update_issue, linear_add_comment
├─ Discord          discord_notify
├─ Custom skills    skill_save, skill_delete (slash commands)
├─ Widgets          widget_save, widget_delete
├─ Rules            rule_save, rule_delete, rule_list
└─ MCP              mcp_list/add/remove_server, mcp_reload + dynamically registered tools
```

### 5.2 Tool Execution Flow

```
LLM returns a tool_call
    │
    ▼
dispatch_tool(name, arguments)
    │
    ├─ plan mode? → block write/exec tools → return "플랜 모드 활성화" ("plan mode active") message
    ├─ CE mode? → block anything outside CE_ALLOWED_TOOLS
    │
    ├─ MCP tool (mcp__ prefix)? → dispatch_tool_async() → mcp_client
    │
    └─ regular tool → TOOL_FUNCTIONS[name](arguments)
        ├─ tools_google.py  (Gmail/Calendar/Drive/Linear)
        ├─ tools_web.py     (web_search/fetch)
        ├─ tools_code.py    (bash_exec/python_exec/sandbox_*)
        ├─ tools_office.py  (xlsx/docx/pptx)
        └─ tools.py builtin (memory/session/rule/skill/widget)
```

### 5.3 Code Execution Sandbox

```
bash_exec / python_exec
    │
    ├─ Docker available?
    │   ├─ YES: exec in the vega-sandbox container
    │   │       /vega_data → ~/Library/Application Support/VEGA/ (rw mount)
    │   │       /project   → working directory (configured per session)
    │   │       /host_home → home directory (ro mount)
    │   └─ NO:  host_exec path (fallback when Docker is absent)
    │
    └─ automatic path translation:
        ~/... path → /host_home/...
        ~/Library/Application Support/VEGA/ → /vega_data/
```

### 5.4 MCP Client (`pipeline/mcp_client.py`)

```
on server startup (FastAPI lifespan)
    │
    ▼
ensure_mcp_loaded()
    ├─ read data_dir()/mcp.json
    ├─ connect to each server via stdio/sse
    ├─ query the tool list
    ├─ check for prompt-injection patterns (_sanitize_mcp_description)
    └─ dynamically add to TOOL_SCHEMAS (mcp__{server}__{tool} prefix)

at execution time: dispatch_tool_async() → fastmcp.Client.call_tool()
```

---

## 6. Data Persistence

### 6.1 Directory Structure

```
~/Library/Application Support/VEGA/    ← data_dir() — all persistent data
├─ vega.db                             # main SQLite (conversations·memory·events)
├─ llm_providers.json                  # active LLM provider config
├─ mcp.json                            # MCP server config
├─ user_profile.json                   # user profile (name·role·email, etc.)
├─ tool_groups.json                    # tool-group enablement config
├─ tool_telemetry.db                   # tool usage statistics
├─ .env                                # API keys (Keychain fallback)
├─ agents/
│  ├─ _default.md                      # agent constitution (immutable)
│  └─ RULES.md                         # user-defined rules (edited by the agent)
├─ commands/                           # user-defined slash commands
├─ uploads/                            # chat attachments
└─ charts/                             # generated charts

~/Library/Logs/VEGA/                   ← log_dir()
├─ vega-backend.log                    # Python backend log (5MB × 5 rotation)
└─ vega-shell.log                      # Rust shell log

~/Library/LaunchAgents/
└─ com.unohee.vega-backend.plist       # auto-start backend at login
```

### 6.2 SQLite Schema (`vega.db`)

```sql
-- conversation sessions
conversations (
    uuid TEXT PRIMARY KEY,
    name TEXT,
    created_at TEXT,
    updated_at TEXT,
    msg_count INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0,
    working_dir TEXT
)

-- messages
messages (
    uuid TEXT PRIMARY KEY,
    session_uuid TEXT REFERENCES conversations(uuid),
    role TEXT,           -- user / assistant / tool
    content TEXT,
    usage_meta TEXT,     -- JSON: {model, input_tokens, output_tokens, cost_usd, ttft_sec}
    created_at TEXT
)

-- persona sections
persona_sections (id, section_key, content, scope, version, is_active, updated_at)

-- events
events (id, title, date, time, category, notes, created_at)

-- entities
entities (id, name, category, context, last_seen, notes)

-- project state
project_state (id, name, status, metrics_json, risks_json, next_actions_json, ...)

-- memory
memory_entries (id, content, embedding, created_at, ...)
```

---

## 7. Security and Authentication

### 7.1 API Key Management

```
key storage priority
    1. macOS Keychain  (service: "VEGA", account: key name)  ← recommended
    2. ~/Library/Application Support/VEGA/.env           ← next best
    3. repo root .env                                     ← development only
    4. environment variable                              ← CI/CD
    5. default ""

key source diagnostics: GET /api/onboarding/key-source
→ { "OPENROUTER_API": { "source": "keychain", "masked": "sk-or-v1-****1234" } }
```

### 7.2 ChatGPT OAuth (PKCE)

```
POST /api/onboarding/pkce
    │
    ├─ generate PKCE code_verifier / code_challenge
    ├─ open the ChatGPT login page in the browser
    ├─ user logs in → receive authorization_code at redirect_uri
    ├─ exchange access_token + refresh_token using code_verifier
    └─ save ~/Library/Application Support/VEGA/openai_oauth.json

on token expiry: ensure_valid_token() → auto-refresh with refresh_token
```

### 7.3 Access Control

```
determine request origin
    │
    ├─ 127.0.0.1 (loopback) → "full" access
    ├─ X-Enterprise-Key header match → "full" access
    └─ other (LAN, etc.) → "ce" access (CE mode)

CE mode restrictions:
    ✓ allowed: web search, Gmail, Calendar, Drive, Linear, Discord, memory read
    ✗ blocked: file_read/edit, host_exec, bash_exec, icloud_*, system tools
```

---

## 8. Deployment Pipeline

### 8.1 Build Steps

```
bash scripts/build_dmg.sh
│
├─ [0/5] PyInstaller → bin/vega-backend (94MB onefile)
│   ├─ bin/vega-backend.spec
│   ├─ certifi data bundle
│   ├─ fastmcp metadata (copy_metadata)
│   └─ bin/.venv isolated environment
│
├─ [1/5] cargo tauri build --target aarch64-apple-darwin --bundles app
│   ├─ APPLE_SIGNING_IDENTITY unset (adhoc build)
│   └─ TAURI_SIGNING_PRIVATE_KEY → generate updater .sig
│
├─ [1.5/5] sign_and_notarize.sh (re-sign the app)
│   ├─ vega-backend codesign + entitlements
│   ├─ vega-desktop codesign + entitlements
│   └─ VEGA.app deep re-sign (disable-library-validation required)
│
├─ [2/5] hdiutil → DMG staging
│
├─ [3/5] hdiutil create → VEGA-{VERSION}.dmg
│
├─ [4/5] DMG signing + notarytool notarization + staple
│   ├─ requires VEGA_NOTARY_PROFILE=vega-notary
│   └─ Gatekeeper: "Notarized Developer ID accepted"
│
└─ [4.5/5] updater artifacts (.app.tar.gz + .sig)
    └─ upload to CF R2 (endpoints PLACEHOLDER must be replaced)
```

### 8.2 Version History

| Version | Date | Key changes |
|------|------|-----------|
| 0.1.1 | 2026-05-31 | First release |
| 0.1.2 | 2026-06-01 | SSL, signing, and LaunchAgent patches (fixed clean-install failure) |
| 0.1.3 | 2026-06-01 | SSL defense-in-depth, certifi automation, signing/notarization automation |
| 0.1.4 | 2026-06-02 | onefile path bug (OAuth token), added system logs, API key input in settings window |
| 0.1.5 | 2026-06-02 | Persisted 5 onefile write paths, provider-aware auth status, profile button |
| 0.1.6 | 2026-06-02 | Additional onefile path fixes (commands/streaming/llm router), MCP status bar button, Docker warning |

### 8.3 Known Build Pitfalls

| Pitfall | Symptom | Fix |
|------|------|------|
| PyInstaller onefile `Path(__file__)` | Write failure (read-only `_MEIPASS`) | Use `data_dir()`, with `Path(__file__)` only as a fallback |
| fastmcp `PackageNotFoundError` | Dies immediately on startup | `copy_metadata("fastmcp", "mcp", "openai", "anthropic")` |
| Tauri `--bundles dmg` | create-dmg hang (osascript/Finder) | `--bundles app` + create the DMG directly with hdiutil |
| `errSecInternalComponent` | codesign failure | Keychain unlock + `security set-key-partition-list` |
| bash 3.2 empty array unbound | `"${arr[@]}"` error under `set -u` | `"${arr[@]+"${arr[@]}"}"` pattern |
| notarytool upload hang | Stuck at "initiating connection" | Check the submission ID with `xcrun notarytool history`; retry if absent |

---

## 9. Known Limitations and TODO

### Currently Unimplemented

| Feature | Status | Notes |
|------|------|------|
| CF R2 auto-update endpoint | PLACEHOLDER | `tauri.conf.json` endpoints need replacing |
| Windows / Linux build | Unsupported | macOS only (LaunchAgent, Keychain) |
| Multi-user | Unsupported | Single Keychain service "VEGA" |
| iOS build | Prototype | `feat/ios-app-prototype` branch; CoreSimulator sudo barrier exists |
| Test coverage | Low | Most core modules are untested (per the cxt registry) |

### Technical Debt

- Because `data/commands/` (the default bundled commands) lives in the bundle's temp path, commands added by the user are written only to `user_commands_dir()` — so the bundled command list may not be visible in the onefile build
- The `vega.db` path has been unified to `data_paths.db_path()`, but some tests still use hardcoded paths
- The build script for the Docker sandbox image (`vega-sandbox:latest`) is not included

---

*This document was written against the HEAD of the `feat/ios-app-prototype` branch (commit `ae658de`).*
