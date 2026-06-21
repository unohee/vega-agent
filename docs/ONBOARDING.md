# VEGA Onboarding Guide

> Step-by-step guidance for first-time users and new developers

---

## Table of Contents

1. [Installation](#1-installation)
2. [First Launch Flow](#2-first-launch-flow)
3. [LLM Provider Setup](#3-llm-provider-setup)
4. [Google Integration Setup](#4-google-integration-setup)
5. [Connecting MCP Servers](#5-connecting-mcp-servers)
6. [After Setup — Basic Usage](#6-after-setup--basic-usage)
7. [Troubleshooting](#7-troubleshooting)
8. [Developer Onboarding](#8-developer-onboarding)

---

## 1. Installation

### Requirements

| Item | Minimum |
|------|--------|
| macOS | 11.0 (Big Sur) or later |
| Chip | Apple Silicon (aarch64) |
| Disk | About 500MB |
| Network | Internet connection for LLM API calls |

### Installation Steps

```
1. Download VEGA-0.1.6.dmg (or use it directly from build_output/)
2. Mount the DMG → drag VEGA.app into the Applications folder
3. Launch VEGA.app for the first time
   ├─ If a Gatekeeper warning appears: right-click → Open → Open
   └─ Or: System Settings → Privacy & Security → "Open Anyway"
```

> **Note**: The DMG is a build that has completed Apple Notary notarization and Developer ID signing. Under normal conditions no Gatekeeper warning will appear.

---

## 2. First Launch Flow

```
Launch VEGA.app
    │
    ▼
Rust shell: register LaunchAgent
    ├─ Create ~/Library/LaunchAgents/com.unohee.vega-backend.plist
    └─ Auto-start the backend (port 8100)
    │
    ▼
Load http://127.0.0.1:8100/entry
    │
    ├─ Onboarding complete? (onboarded: true in user_profile.json)
    │   ├─ YES → /chat (main chat screen)
    │   └─ NO  → /install (install wizard)
    │
    ▼ (first launch)
Install wizard (/install)
    │
    ├─ Step 1: Select and authenticate an LLM provider
    ├─ Step 2: Enter user profile (name, role, organization)
    ├─ Step 3: Google integration (optional)
    └─ Done → /chat
```

### Onboarding Completion Conditions

Any one of the following:
- Complete the `finish` action in the install wizard
- Or directly via API: `POST /api/onboarding/finish`

Saved on completion:
- `~/Library/Application Support/VEGA/user_profile.json`
- `~/Library/Application Support/VEGA/llm_providers.json`
- API key stored in the macOS Keychain (service name: "VEGA")

---

## 3. LLM Provider Setup

### Supported Providers

```
┌─────────────────────────────────────────────────────────────────────┐
│  ID            │ Name                     │ Auth method│ Best for   │
├─────────────────────────────────────────────────────────────────────┤
│ anthropic      │ Anthropic (Claude)       │ API key    │ High-end reasoning│
│ openai         │ OpenAI API               │ API key    │ GPT series │
│ openrouter     │ OpenRouter               │ API key    │ Multi-model│  ← recommended
│ chatgpt        │ ChatGPT (Codex)          │ OAuth PKCE │ Coding     │
│ local          │ Local / on-prem server   │ URL only   │ Offline    │
└─────────────────────────────────────────────────────────────────────┘
```

> **Recommended**: When getting started for the first time, **OpenRouter** is the most convenient. With a single API key you can access all models — Claude, GPT, Gemini, DeepSeek, and more.

### OpenRouter Setup (recommended)

```
1. Go to https://openrouter.ai → sign up or log in
2. Keys → "Create key" → enter a name → copy the key (sk-or-v1-...)
3. VEGA settings window → "AI provider" → select "OpenRouter"
4. Enter the API key → Save
```

### Anthropic (Claude) Setup

```
1. Go to https://console.anthropic.com → log in
2. API Keys → "Create Key" → copy the key (sk-ant-...)
3. VEGA settings window → "AI provider" → select "Anthropic"
4. Enter the API key → Save
```

### OpenAI Setup

```
1. Go to https://platform.openai.com → log in
2. API Keys → "Create new secret key" → copy the key (sk-...)
3. VEGA settings window → "AI provider" → select "OpenAI API"
4. Enter the API key → Save
```

### ChatGPT (PKCE OAuth) Setup

```
1. VEGA settings window → "AI provider" → select "ChatGPT"
2. Click the "Log in with ChatGPT" button
3. Log in to your ChatGPT account in the browser
4. Authentication complete → VEGA stores the token automatically
```

> **Note**: The ChatGPT OAuth token is refreshed automatically when it expires. If the refresh fails, re-login is required.

### Local LLM (LM Studio / Ollama) Setup

```
1. Install and run LM Studio or Ollama
2. Start an OpenAI-compatible API server (default: http://localhost:1234/v1)
3. VEGA settings window → "AI provider" → select "Local server"
4. Enter the URL (e.g., http://localhost:1234/v1)
5. Enter the model ID to use (e.g., gemma-4-e4b-it-mlx)
```

### Accessing the Settings Window

You can change settings even after onboarding is complete:

```
Method 1: Status bar (bottom) → click the settings icon
Method 2: Tray icon → "Settings"
Method 3: Keyboard shortcut Cmd+, (settings window)
```

### Checking Where the API Key Is Stored

```bash
# Key source diagnostic API
curl http://127.0.0.1:8100/api/onboarding/key-source

# Example response
{
  "OPENROUTER_API": { "source": "keychain", "masked": "sk-or-v1-****1234" },
  "ANTHROPIC_API_KEY": { "source": "none" }
}
```

---

## 4. Google Integration Setup

To use the Gmail, Calendar, and Drive tools, Google OAuth setup is required.

### Prerequisite: Google Cloud Project

```
1. Go to https://console.cloud.google.com
2. Create a new project (or use an existing one)
3. Enable the following APIs in the API Library:
   - Gmail API
   - Google Calendar API
   - Google Drive API
4. Configure the OAuth consent screen:
   - User type: External
   - App name: VEGA (or any name you like)
   - Scopes: add gmail.modify, calendar, drive.readonly
5. Create an OAuth client ID:
   - Application type: Desktop app
   - Download: client_secret_xxxx.json
```

### Registering the Google Client with VEGA

```bash
# Method 1: Handled automatically during the Google step of the install wizard

# Method 2: Call the API directly
curl -X POST http://127.0.0.1:8100/api/onboarding/google/creds \
  -H "Content-Type: application/json" \
  -d '{"client_id": "xxx.apps.googleusercontent.com", "client_secret": "xxx"}'

# Method 3: Copy the file directly
cp ~/Downloads/client_secret_xxx.json \
   ~/Library/Application\ Support/VEGA/google_oauth_client.json
```

### Running Google OAuth

```bash
# Calling the API opens the consent screen in the browser
curl -X POST http://127.0.0.1:8100/api/onboarding/google/auth

# Or: request it directly in chat
# Type "Connect my Google Calendar" and VEGA will guide you automatically
```

---

## 5. Connecting MCP Servers

Connecting MCP (Model Context Protocol) servers lets you integrate additional tools into VEGA.

### Managing MCP from the Status Bar

```
Chat screen → bottom status bar → click the "MCP" button
  or
Chat screen → "+" menu → "Manage MCP servers"
```

### How to Add an MCP Server

**Method 1: Add from the UI**
```
MCP management window → Add server
→ Name: my-server
→ Command: npx -y @my-company/mcp-server
→ Save
```

**Method 2: Edit the JSON directly**
```bash
# Edit ~/Library/Application Support/VEGA/mcp.json
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

**Method 3: Add from chat**
```
# In the chat box
"Add a server with the mcp_add_server tool: npx -y @modelcontextprotocol/server-filesystem"
```

### Supported Transport Types

```
stdio: { "command": "npx", "args": [...] }     ← most common
sse:   { "url": "http://localhost:3000/sse" }   ← HTTP SSE
```

### Automatic MCP Tool Registration

When a server connects, VEGA automatically:
1. Queries the tool list
2. Scans for prompt injection patterns (security)
3. Registers them in TOOL_SCHEMAS in the form `mcp__{server_name}__{tool_name}`

---

## 6. After Setup — Basic Usage

### Shortcuts

| Shortcut | Action |
|--------|------|
| `Cmd+,` | Open the settings window |

### Chat UI Basic Features

```
┌─────────────────────────────────────────────────────────────────┐
│  [Session list]│  [Chat area]                        [Settings]│
│               │                                               │
│  ● Today's todos│  VEGA: Hello! How can I help you?           │
│  ○ Clean inbox │                                               │
│  ○ Code review │  Me: Sort out today's important emails in Gmail│
│               │                                               │
│  [+ New session]│  VEGA: [tool: gmail_search → gmail_read →   │
│               │         gmail_modify_labels]                  │
│               │         Sorted 5 important emails received today│
│               │  ────────────────────────────────────────────│
│               │  [Type a message...]          [/] [📎] [Send] │
├─────────────────────────────────────────────────────────────────┤
│  📁 No folder │  Session ID│  ⊕ MCP  │  👤 Profile│  🤖 deepseek│
└─────────────────────────────────────────────────────────────────┘
```

### Slash Commands

Commands beginning with `/` direct the agent's behavior:

```
/help            — list available commands
/new             — start a new session
/rename <name>   — rename the current session
/search <keyword>— search the conversation within the session
/plan            — turn on plan mode (blocks write/execute tools)
/plan-off        — turn off plan mode
/sessions        — list all sessions
/resume <UUID>   — continue a specific session
/context         — show the current context token count
/who             — check the current user profile
```

### Setting the Working Directory

Specify the base folder VEGA references when working with code:

```
Bottom status bar → click "📁 No folder" → choose a folder
```

Once specified, tools such as `bash_exec`, `file_read`, and `file_edit` operate relative to that folder.

### Status Bar Items

| Item | Meaning |
|------|------|
| `● MCP` | Manage MCP servers |
| `👤 Profile` | Edit name, role, organization |
| `🤖 deepseek` | Currently active model |
| `Token count` | Estimated context size for the next message |
| `⚠ Docker` | Docker not running warning (bash/python tools unavailable) |
| `✓ Authenticated` | LLM provider authenticated successfully |
| `●` (green) | Backend server healthy |

---

## 7. Troubleshooting

### Log Locations

```bash
# Python backend log
tail -f ~/Library/Logs/VEGA/vega-backend.log

# Rust shell log
tail -f ~/Library/Logs/VEGA/vega-shell.log

# LaunchAgent standard output
tail -f ~/Library/Logs/VEGA/vega-backend.stdout.log
```

### Common Problems

#### When the backend does not start

```bash
# 1. Check the process
pgrep -af vega-backend

# 2. Check port usage
lsof -i :8100

# 3. Manual restart
launchctl kickstart -k gui/$(id -u)/com.unohee.vega-backend

# 4. Re-register the LaunchAgent (automatic on app restart)
launchctl bootout gui/$(id -u)/com.unohee.vega-backend
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.unohee.vega-backend.plist
```

#### "No OAuth profile found" error

When the ChatGPT OAuth token has expired or is missing:

```
Settings window → AI provider → "ChatGPT" → "Re-login"
Or: switch to another provider such as OpenRouter
```

#### When the API key is not recognized

```bash
# Check the key source
curl http://127.0.0.1:8100/api/onboarding/key-source

# Check directly in the Keychain
security find-generic-password -s "VEGA" -a "OPENROUTER_API" -w

# Re-enter the key: Settings window → AI provider → the provider → re-enter the key
```

#### When the Docker tools do not work

When the `⚠ Docker` warning appears in the status bar:

```bash
# Make sure Docker Desktop is running
open -a Docker

# Check container status
docker ps -a | grep vega-sandbox

# Start the container
docker start vega-sandbox
# Or build from the image first
cd ~/dev/vega-agent/sandbox && docker compose up -d
```

#### When only a few tools are visible

Diagnose with a health check:
```bash
curl http://127.0.0.1:8100/api/health
# {
#   "total_tools": 70,
#   "sandbox": "docker_off",   ← Docker is off
#   "mcp_tools": 0,            ← MCP not connected
#   "auth": "ok"
# }
```

#### How to reset the configuration

```bash
# Full reset (caution: deletes all conversation history and settings)
rm -rf ~/Library/Application\ Support/VEGA/

# Reset authentication only
security delete-generic-password -s "VEGA" 2>/dev/null
rm -f ~/Library/Application\ Support/VEGA/openai_oauth.json
```

---

## 8. Developer Onboarding

### Setting Up the Development Environment

```bash
# 1. Clone the repository
git clone https://github.com/unohee/vega-agent.git
cd vega-agent

# 2. Python environment (mlx_env recommended)
source ~/dev/mlx_env/bin/activate
pip install -r requirements.txt

# 3. Rust environment
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
cargo install tauri-cli@2

# 4. Initialize the database
python scripts/init_user_db.py

# 5. Start the development server
python -m uvicorn web.server:app --host 127.0.0.1 --port 8100 --reload
```

### Directory Structure

```
vega-agent/
├─ bin/                  # PyInstaller bundle related
│  ├─ vega-backend.spec  # build spec
│  └─ vega_backend_launcher.py  # entry point
├─ data/                 # default/example config files (included in the bundle)
│  ├─ llm_providers.json # LLM provider defaults
│  ├─ mcp.json           # MCP server config
│  └─ agents/            # agent MD files
├─ desktop/              # Tauri Rust shell
│  ├─ src/lib.rs         # main logic
│  └─ tauri.conf.json    # app config
├─ pipeline/             # core Python modules
│  ├─ streaming.py       # GPT streaming loop
│  ├─ llm_gateway.py     # multi-provider router
│  ├─ tools.py           # tool registry
│  ├─ session_store.py   # SQLite session management
│  ├─ keychain.py        # API key management
│  └─ data_paths.py      # single source of persistent paths
├─ scripts/              # build/deploy scripts
│  ├─ build_dmg.sh       # full build script
│  └─ sign_and_notarize.sh  # signing/notarization
├─ tests/                # tests
├─ testing/              # experimental scripts
└─ web/                  # FastAPI app
   ├─ server.py          # main server
   ├─ routers/           # router modules
   └─ static/            # HTML/JS (chat.html, etc.)
```

### Guide to Editing Core Files

#### Adding an LLM Provider

```python
# Add the provider to data/llm_providers.json
{
  "providers": {
    "my_provider": {
      "label": "My Provider",
      "kind": "chat_completions",      # chat_completions | anthropic | responses
      "auth_type": "bearer",           # bearer | anthropic_key | chatgpt_oauth | none
      "api_key_env": "MY_API_KEY",
      "base_url": "https://api.my-provider.com/v1",
      "default_model": "my-model"
    }
  }
}

# Also add it to PROVIDER_CATALOG in web/routers/onboarding.py
PROVIDER_CATALOG.append({
    "id": "my_provider", "label": "My Provider", "auth": "key",
    "key_env": "MY_API_KEY", "key_hint": "sk-...",
    "verify_url": "https://api.my-provider.com/v1/models",
    "verify_header": "bearer",
    "desc": "My provider description",
})
```

#### Adding a New Tool

```python
# pipeline/tools.py — add the schema to TOOL_SCHEMAS
TOOL_SCHEMAS.append({
    "type": "function",
    "function": {
        "name": "my_tool",
        "description": "My tool description",
        "parameters": {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": "input value"}
            },
            "required": ["input"]
        }
    }
})

# Add the implementation to TOOL_FUNCTIONS
TOOL_FUNCTIONS["my_tool"] = lambda args: my_tool_impl(args["input"])

# Called automatically from dispatch_tool()
```

#### Adding a Persistent File Path

```python
# Always obtain paths through data_paths.py
# Never use Path(__file__) directly — it points to a temp path in a PyInstaller onefile

from pipeline.data_paths import data_dir

def my_config_path() -> Path:
    return data_dir() / "my_config.json"
```

### Build and Test

```bash
# Unit tests
source ~/dev/mlx_env/bin/activate
python -m pytest tests/ -v

# Full build (including signing/notarization)
VEGA_NOTARY_PROFILE=vega-notary bash scripts/build_dmg.sh

# Signing only (no notarization, fast local test)
bash scripts/build_dmg.sh
# → build_output/VEGA-{VERSION}.dmg

# Notarization only (when the DMG already exists)
VEGA_SIGN_ID="Developer ID Application: Heewon Oh (635QK74RYK)" \
VEGA_NOTARY_PROFILE=vega-notary \
bash scripts/sign_and_notarize.sh --artifact-only build_output/VEGA-0.1.6.dmg
```

### Key Development Principles

1. **Never use onefile paths**: For writable paths, instead of `Path(__file__)` always use `data_dir()`
2. **Keychain first**: For API keys use `keychain.set_secret()`, and read with `keychain.get()`
3. **Persistent data**: `~/Library/Application Support/VEGA/` — preserved even after updates
4. **Bundled data is read-only**: Files inside the `data/` directory are for reference only; make changes in `data_dir()`
5. **Dynamic MCP tool registration**: Automatic when the server starts — no need to edit `TOOL_SCHEMAS` directly

---

*This document is based on VEGA 0.1.6. For the latest information, refer to `DEBUGGING.md` and the header comments of each module.*
