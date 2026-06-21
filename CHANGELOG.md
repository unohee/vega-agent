# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed (코드 실행 경계 — INT-1470)
- **샌드박스 홈 전체 마운트 제거 → 연결 폴더로 축소** (`pipeline/sandbox.py`) — 연결 작업 폴더(`_PROJECT_DIR`)가 설정된 요청은 그 폴더(`/project`, rw)와 VEGA data(`/vega_data`, rw)만 마운트한다. 기존엔 "격리"를 표방하면서 호스트 홈 전체(`$HOME`→`/host_home`)를 함께 노출해 격리 이점을 자가무효화했다. 연결 폴더 모드에선 홈→`/host_home` 경로 재작성도 하지 않아 연결 폴더 밖 호스트 경로 접근이 차단된다. 연결 폴더가 없는 레거시 영속 컨테이너 경로는 기존 동작 유지. 도구 설명(`bash_exec`/`python_exec`)도 경계를 반영하도록 갱신.

## [0.1.14] - 2026-06-11

### Added (Windows build — INT-1438)
- **Windows NSIS build pipeline** (`.github/workflows/build-windows.yml`) — on the windows-latest runner: PyInstaller backend build → `/api/health` smoke test → Tauri NSIS installer build → attach to Release. No Windows code-signing certificate (unsigned, accepting the SmartScreen warning).
- **`desktop/tauri.windows.conf.json`** — Windows-only bundle target (nsis, currentUser install).
- **Rust shell Windows support** (`desktop/src/lib.rs`) — macOS-only code such as LaunchAgent/libc is gated behind `cfg(target_os = "macos")`; Windows/Linux spawn the backend directly (CREATE_NO_WINDOW), with a platform-specific log directory (%LOCALAPPDATA%\VEGA\logs), and the tray "restart backend" action does taskkill+respawn. The overlay title bar remains macOS-only.
- **Python backend Windows guards** — `pipeline/keychain.py` (.env/environment-variable fallback when the `security` CLI is absent), `pipeline/data_paths.py` (Windows %LOCALAPPDATA%\VEGA), `web/server.py` (pty/fcntl/termios import guards — only the built-in terminal is disabled).

### Fixed
- **Port 8100 split-brain** (`bin/vega_backend_launcher.py`, INT-1439) — if another backend (e.g. the personal VEGA dev daemon's `*:8100` wildcard bind) is already serving, do not grab `127.0.0.1:8100` on top of it; instead yield and wait until it is released. Prevents a recurrence of the incident that felt like "session context vanishing" when two backends split incoming traffic. Added `app`/`db` identity fields to `/api/health`.
- **Unblocked pytest collection** — moved `tests/test_channel_kyte_e2e.py` (a manual script that calls the LLM and runs sys.exit on import) to `testing/channel_kyte_e2e_260611.py`.

## [0.1.7] - 2026-06-04

### Added (VEGA backport — chat/dashboard UX)
- **Revisit interleaving restoration** (`web/server.py`, `pipeline/session_store.py`, `chat.html`) — introduced an `events` structure that records text↔tool-execution chronologically within an assistant message. Added an `events TEXT` column to the `messages` table (migration); responses that used tools persist their events so that, on session revisit, they are restored in the same order as live. Pure-text responses fall back to text.
- **Per-tool completion summary + command-centric badges** (`web/server.py`) — `_exec_summary` summarizes host_exec/bash_exec results in a single line of "what was done" (command+rc), and `_tool_summary` reflects the executed command per call_id in the badge. Output is shown separately in the terminal block.
- **Aborted-response persistence** (`web/server.py`) — `_build_aborted_message` preserves traces of tool execution even when a response is aborted, so they do not disappear on revisit.
- **Message editing** (`chat.html`) — edit and resend a user message.
- **Work-process transparency (Claude Code style)** (`data/agents/_default.md`) — added an agent directive to naturally show the work-in-progress as body text while using tools.
- **Dashboard memory ecosystem view** (`dashboard.html`, `web/routers/memory_inspector.py`) — fully redesigned the home into a hero (what VEGA remembers) + tabs (recent memories · people/entities · timeline · persona · rules/skills · today) structure.
- **File viewer drag-quote + open in external editor** (`web/routers/fs.py`, `chat.html`) — drag text in the file viewer to quote it into the chat, or open it directly in an external editor.

### Added
- **STT (speech-to-text) support** (`pipeline/stt_gateway.py`) — a common gateway for OpenAI Whisper API-compatible endpoints. Supported providers: OpenAI (`whisper-1`), Groq, local faster-whisper-server, LM Studio. A `LocalSTTUnavailable` exception silently returns 503 when the sidecar is not running. Added an `stt` section to `data/llm_providers.json` (`provider`, `model`, `language`, `response_format`). Added `/api/stt` and `/api/stt/config` endpoints.
- **Chat UI microphone button** (`chat.html`) — added a 🎙 button to the input box. Records in-browser via MediaRecorder → sends to `/api/stt` → inserts the text at the cursor position. Also added a "Voice input" item to the `+` popover menu. Shows a "local STT not running" toast when local STT is not running.
- **UI language selection (Korean/English)** (`chat.html`, `dashboard.html`) — added a `KO`/`EN` toggle button to the header. Static UI text is swapped using a `VEGA_STRINGS` i18n object + `applyLang()` + `data-i18n` attribute pattern. The selected language is persisted via `localStorage['vega_lang']`.
- **Multilingual support roadmap** (`docs/I18N_ROADMAP.md`) — documented a 4-stage roadmap: Phase 1 (full string translation) → Phase 2 (externalize to JSON) → Phase 3 (add Japanese/Chinese) → Phase 4 (agent response language linkage).
- **User manual for non-developers** (`README.md`) — fully rewritten. A wiki-level manual you can follow without screenshots, from installation to voice input, file attachment, slash commands, and MCP.

### Added (previously unrecorded)
- **Multi-provider install wizard + Anthropic native adapter** — the install wizard expanded from OpenRouter-only to a provider list (Anthropic·OpenAI·OpenRouter API keys / ChatGPT PKCE login / local·on-premise URL) → selection → the corresponding authentication flow. Keys are saved to Keychain after live validation (`/models` 200), then registered into `llm_providers.json` via `upsert_provider` and activated. The inference backend likewise supports multiple providers:
  - **Anthropic native adapter** (`llm_gateway` `kind=anthropic`) — calls `/v1/messages` directly instead of being OpenAI-compatible. `x-api-key`+`anthropic-version` headers, system as a cache_control block, Responses↔Anthropic message/tool schema (`input_schema`) conversion, `max_tokens` required. Added Anthropic SSE parsing to `streaming._stream_sse` (`message_start`/`content_block_delta` text_delta·input_json_delta/`message_delta` usage/`message_stop`). `auth_type`: `anthropic_key` (console key) / `claude_oauth` (on hold — client_id is private, import-guarded).
  - Added an **OpenAI direct API** provider (`api.openai.com`, bearer).
  - Local/on-premise is registered by entering just an OpenAI-compatible URL (registration allowed even if the server does not respond).
- **Desktop app (Tauri v2) + distribution DMG** (`desktop/`) — ported the main VEGA repo's Tauri shell to vega-agent. Daemon mode (default) registers a `com.unohee.vega-backend` LaunchAgent on first run to keep the PyInstaller backend running persistently, and provides a tray icon and window toggle. `scripts/build_dmg.sh` performs PyInstaller backend (`bin/vega-backend.spec`) → `cargo tauri build` → DMG packaging. Automatically falls back to an unsigned build if there is no Developer ID certificate.
- **Automatic code sandbox provisioning** (`pipeline/sandbox.ensure_sandbox_ready` + server lifespan warm-up) — at server startup, provisions the Docker `vega-sandbox` container in the background (reused if it already exists, built only when the image is missing) to eliminate the first `bash_exec`/`python_exec` delay. Silently skips if Docker is not installed/running (the agent keeps working, only code execution is deferred). Added `docker_available()`. The compose paths are parameterized via `${VEGA_HOST_HOME}`/`${VEGA_DATA_DIR}` environment variables (removing the main repo's hardcoding), injected by `_compose_env()` — so it works in the distribution build and on other users' environments too. Persistent volumes (`sandbox_lib`/`packages`/`history`) are preserved so modules and pip packages created by the agent survive restarts. The DMG bundle includes `sandbox/{Dockerfile,docker-compose.yml}`.
- **Install wizard — driven by the connected LLM** (`web/static/install_wizard.html` + `web/routers/onboarding.py`) — on the daemon's first run, `/entry` checks the onboarding status and routes to `/install`. The wizard consists of (1) OpenRouter key input + live validation → save to Keychain, (2) **the connected LLM conversationally** collects name·role·affiliation (parsing the ```vega``` directive from the LLM response and reflecting it into user_profile immediately), (3) Google Cloud OAuth (save Client ID/Secret → browser consent → issue refresh token). On completion it marks `onboarded=true` and switches to `/chat`.
- **PyInstaller backend bundle** (`bin/vega_backend_launcher.py`, `bin/vega-backend.spec`) — a single binary that runs `web.server:app` with uvicorn. Includes the defaults for `web/static`·`data/{agents,commands}` in the bundle.
- **Two-tier intent router** (`pipeline/tier_router.py` + `llm_gateway.get_provider_for_tier`) — classifies requests into domain-knowledge queries/updates (→ local SLM, deterministic, zero cost) and immediate task assistance (→ cloud deepseek-v4-flash, generation·reasoning·search). Automatic cloud fallback when the local SLM is down. A `tiers`{local:lmstudio, cloud:openrouter} mapping in `llm_providers.json`. The channel bot determines the tier via `route_tier` → passes it to `stream_gpt(tier=)`.
- **Channel bot adapters** (`pipeline/channels/`) — run VEGA from Telegram·Slack. The goal is to match the in-house AI experience with familiar messenger UX, the same as ordinary chat apps.
  - `core.py` — the common core `run_agent_turn(channel, conv_id, text, on_delta, ce_mode)`. Bundles channel conversation ID↔VEGA session mapping (`data/channel_sessions.json`), history restoration, `stream_gpt` call, incremental token delta callbacks, and session persistence into one function.
  - `telegram_bot.py` — python-telegram-bot polling. DMs always, groups when @mentioned. Incremental streaming via `edit_message_text`, 4096-character splitting, `/start`·`/reset`.
  - `slack_bot.py` — slack_bolt Socket Mode. Triggered on `app_mention`·DM, session isolation by `thread_ts`, incremental streaming via `chat_update`.
- **KYTE tool integration** — kyte-portal's work tools (Airtable/Gmail/Superthread/Calendar/Drive — 10 read tools) are received via a stdio MCP server (`kyte_cli/mcp_server.py`) and auto-registered. Invoked with `kyte__find_work` and the like. Passed E2E validation with deepseek-v4-flash (OpenRouter).
- **CHANGELOG.md** — newly introduced this file.
- Added `python-telegram-bot>=22`, `slack_bolt>=1.21` to `requirements.txt`.
- Added `TELEGRAM_BOT_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` entries to `.env.example`.

### Changed
- **CE mode gate disabled (for now)** — since it is personal use, changed all entry points (desktop app·channel bots) to expose and execute the full tool set, including local file/exec tools. `get_schemas_for_mode` returns the full schema regardless of `ce_mode`, and the CE block in `dispatch_tool` is removed. The `ce_mode` argument and `_CE_ALLOWED_TOOLS`/`_CE_MODE_VAR` are preserved for compatibility/reactivation. **plan_mode blocking remains as-is**. (Whitelist restoration is needed if exposed remotely — a leaked channel bot token risks exposing the local machine.)
- **DB file separation** — vega-agent uses its own `agent.db` (`data_paths.db_path()` `vega.db`→`agent.db`). Even if it shares the same user data dir as the main (personal) VEGA's `vega.db`, the files are separated to avoid a messages-table schema conflict (old `session_uuid/role/content` ↔ new `conv_uuid/sender/text`). The hardcoded fallback paths in `run_log.py`·`memory_inspector.py` are also unified to `agent.db`. `scripts/init_user_db.py` removes its dependency on the absent module (`pipeline.heartbeat`) + explicitly creates the persona/events/entities schema.
- Set the default LLM provider to OpenRouter `deepseek/deepseek-v4-flash` (`data/llm_providers.json` active=openrouter).
- Allow `kyte__*` tools in CE (remote client) mode — added `kyte__` prefix pass-through to **both** schema exposure (`get_schemas_for_mode`) and execution-defense blocking (`dispatch_tool`). The kyte tools are all read-only envelopes so they are safe, and the channel bot's core purpose is querying company data.
- Corrected the OpenRouter key variable name in `.env.example` to `OPENROUTER_API` (matching `api_key_env` in `data/llm_providers.json`).

### Fixed
- **Multiple fixes for boot failure in a new environment (empty DB)** — the code had never been validated against a fresh `VEGA_DATA_DIR` and was broken:
  - The `messages` table columns created by `session_store._ensure_schema()` (`session_uuid/role/content`) did not match the columns the CRUD (`append_message`/`load_history`) actually uses (`conv_uuid/sender/text/char_len/updated_at`) → aligned CREATE TABLE with the CRUD. (Resolved `no such column: sender`.)
  - `vega_query.py` was missing the code to create the `persona_sections`/`events`/`entities`/`event_entities` tables → added `_ensure_schema()`, called automatically on module load. (Resolved `no such table: persona_sections`.)
- The native `linear_*` tools imported the absent module (`pipeline.linear_client`), failing on every call + causing a self_improve storm → exclude the `linear_*` schema from TOOL_SCHEMAS when the import fails. (If Linear is needed, the MCP `linear__*` is auto-registered and works via `LINEAR_API_KEY`.)

### Removed
- (None)

### Notes
- **Distribution note**: VEGA reads `mcp.json` only from the user data dir (macOS `~/Library/Application Support/VEGA/mcp.json`, or `$VEGA_DATA_DIR/mcp.json`). The repo's `data/mcp.json` is ignored, so kyte MCP registration must be placed in the user data dir.
- The Discord bridge is a no-op stub in vega-agent (`pipeline/discord_bridge.py`) — channels use Telegram/Slack.
- Regression test: `tests/test_channel_kyte_e2e.py` (live OpenRouter, skipped when `OPENROUTER_API` is not set).
