# VEGA Core

**Vector Encoded General Agent** — a self-evolving LLM agent harness that keeps your
knowledge, rules, and workflows outside the model so they persist across sessions,
model swaps, and team deployments.

> VEGA Core is the open, generalized layer extracted from a production personal agent.
> It ships with the harness infrastructure; you supply the domain knowledge via
> `data/agents/_default.md` and let users evolve behavior through natural language.

---

## What it is

- **LLM-agnostic** — ChatGPT (Codex Responses), OpenRouter, LM Studio / local models.
  Swap providers without touching the rest of the stack.
- **Persistent identity** — persona, memory, and rules live in SQLite + Markdown files,
  not inside the model. Sessions start with full context every time.
- **Three-layer self-evolution**
  - Operator sets domain knowledge → `data/agents/_default.md` (immutable constitution)
  - Workflows → `/skill` creates slash commands, saved to `data/commands/*.md`
  - Behavior correction → "from now on do X" triggers `rule_save` → `data/agents/RULES.md`
- **Compaction-based retrospect** — every 20 turns, the agent summarizes history,
  updates memory, and auto-saves behavior rules it detected from user corrections.
- **Tool telemetry** — `/audit` shows per-tool call/failure rates from SQLite.
- **Desktop app** — Tauri v2 native window (tray, global hotkey, drag-and-drop, vision).
- **Multi-access levels** — local (full) / enterprise (`X-VEGA-Key` header) / CE (restricted).

---

## Quick start

### 1. Server (FastAPI)

```bash
cp .env.example .env        # fill in your API keys
pip install -r requirements.txt
python -m uvicorn web.server:app --host 0.0.0.0 --port 8100
```

Open `http://localhost:8100` in your browser.

### 2. Desktop app (optional)

```bash
cd desktop
cargo tauri dev
```

### 3. Code sandbox (optional — required for `bash_exec` / `python_exec`)

```bash
cd sandbox
docker compose up -d
```

### 4. Web search (optional — required for `web_search`)

Run a [SearXNG](https://docs.searxng.org/) instance and point `VEGA_SEARXNG_URL` at it.
For team deployments, one shared instance is enough for all users.

```bash
# Quick local instance
docker run -d -p 18888:8080 searxng/searxng
```

---

## Key features

| Feature | Command / API |
|---------|--------------|
| Multi-model chat + tool use | `POST /api/chat/stream` |
| Persistent memory | `memory_persona_update`, `memory_event_add` tools |
| Behavior rules | Say "from now on ~" → auto `rule_save` to `RULES.md` |
| View saved rules | `/rules` |
| Tool telemetry | `/audit` |
| Research mode | `/research [topic]` — web-first, cite sources |
| Plan mode | `/plan` — no writes, planning only |
| YOLO mode | `/yolo` — auto-approve `host_exec` (hard blocks still apply) |
| Custom slash commands | `/skill` wizard |
| Dashboard widgets | `/widget` wizard |
| MCP server integration | Edit `data/mcp.json` (Claude Desktop format) |

---

## Configuration

| File | Purpose |
|------|---------|
| `.env` | API keys, endpoints — never commit |
| `data/agents/_default.md` | **Operator constitution** — domain knowledge, tool rules, persona. Edit this to customize for your team. |
| `data/agents/RULES.md` | **User rules** — auto-updated via `rule_save`, or edit directly. |
| `data/agents/{provider}.md` | Per-provider overrides (chatgpt / openrouter / lmstudio) |
| `data/commands/*.md` | Custom slash commands (created via `/skill`) |
| `data/mcp.json` | MCP server config (Claude Desktop format) |
| `data/widgets.json` | Agent View dashboard widgets |
| `data/user_profile.json` | User display name, email accounts |

### `_default.md` — the most important file

This is your agent's constitution. Before deploying to a team:

1. Replace the generic response rules with your domain conventions.
2. Add tool usage rules specific to your stack.
3. Add a memory update section matching your entity types.
4. Leave `RULES.md` empty — users will evolve it through conversation.

---

## Architecture

```
web/server.py             FastAPI — REST + SSE streaming, access control, mode flags
pipeline/streaming.py     GPT tool-use SSE loop (plan/research/CE modes + RULES synthesis)
pipeline/llm_gateway.py   Multi-provider router (ChatGPT / OpenRouter / LM Studio)
pipeline/tools*.py        Tool layer (Google / Code / Office / Memory / RULES)
pipeline/compaction.py    20-turn compaction — summary + memory update + rule retrospect
pipeline/tool_telemetry.py  Per-tool call stats (SQLite)
pipeline/self_improve.py  Tool failure → GPT patch → sandbox verify → user approval
pipeline/session_store.py Session + message persistence (SQLite)
desktop/                  Tauri v2 desktop app
data/agents/_default.md   Operator constitution (immutable)
data/agents/RULES.md      User behavior rules (mutable via rule_save)
sandbox/                  Code execution Docker container
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full module reference.

### 문서

| 문서 | 내용 |
|------|------|
| [docs/VEGA_OVERVIEW.md](docs/VEGA_OVERVIEW.md) | 전체 아키텍처·컴포넌트·빌드 파이프라인 개발 보고서 |
| [docs/ONBOARDING.md](docs/ONBOARDING.md) | 설치·LLM 설정·Google 연동·개발 환경 온보딩 가이드 |
| [DEBUGGING.md](DEBUGGING.md) | 경로 치트시트 + 증상별 트러블슈팅 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 모듈 레퍼런스 (에이전트 온보딩용) |

---

## Deployment patterns

### Single user (default)
Run the server locally, connect from `localhost:8100` or via Tailscale.

### Team deployment
1. Host the server on an internal machine.
2. Set `VEGA_SEARXNG_URL` to a shared SearXNG instance.
3. Set enterprise keys via `POST /api/admin/keys` (loopback only).
4. Distribute the desktop app (DMG) to team members — they connect to the server URL.
5. Customize `data/agents/_default.md` with your team's domain knowledge.

### Multi-user isolation
Each user gets their own `VEGA_DATA_DIR`. See `scripts/init_user_db.py`.

---

## Channel bots (Telegram / Slack)

Drive VEGA from familiar messengers so the in-house AI experience matches a normal
chat app. Both bots share one agent core (`pipeline/channels/core.py` →
`run_agent_turn`) and stream the answer progressively (edit-in-place).

```bash
# Telegram — token from @BotFather
export TELEGRAM_BOT_TOKEN=...
python -m pipeline.channels.telegram_bot

# Slack — Socket Mode (xoxb bot token + xapp app-level token)
export SLACK_BOT_TOKEN=xoxb-... SLACK_APP_TOKEN=xapp-...
python -m pipeline.channels.slack_bot
```

- DM is always handled; group/channel messages only when the bot is @-mentioned.
- Each conversation (Telegram `chat_id`, Slack `thread_ts`) maps to its own VEGA
  session — history persists across turns (`data/channel_sessions.json`).
- Remote channels run in **CE mode** (restricted tool allowlist — no local file/exec).

## KYTE tool integration (kyte-portal)

KYTE business tools (Airtable / Gmail / Superthread / Calendar / Drive) are exposed
to VEGA as an **stdio MCP server** — no VEGA code changes required.

> ⚠️ **Deployment note:** VEGA reads `mcp.json` from the **user data dir**
> (`~/Library/Application Support/VEGA/mcp.json` on macOS, `$VEGA_DATA_DIR/mcp.json`
> if set) — **not** from the repo `data/mcp.json`. Place the kyte entry there.

```json
{"mcpServers": {"kyte": {
    "command": "/path/to/python",
    "args": ["-m", "kyte_cli.mcp_server"],
    "env": {"PYTHONPATH": "/path/to/kyte-portal"}}}}
```

The server (`kyte_cli/mcp_server.py` in kyte-portal) wraps `integration_tools`
(10 read-only lookup tools) and returns `{data, source, note}` envelopes. VEGA
auto-registers them at startup via `init_mcp_tools()` and calls them as
`kyte__find_work`, `kyte__komca_lines`, etc. Default model:
`deepseek/deepseek-v4-flash` (OpenRouter, env var `OPENROUTER_API`).
Verified end-to-end: query → `kyte__find_work` → Airtable → Korean answer.

---

## License

MIT — see [LICENSE](LICENSE).
