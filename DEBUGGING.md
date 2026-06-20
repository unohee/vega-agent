# VEGA Debugging Handbook

A standing reference collecting **where to look** when something breaks in the distribution build (`.app`).
Read it in this order: "if this symptom, here" → path cheat sheet → frequently used commands.

> Key premise: the distribution backend is a PyInstaller **onefile** binary (`vega-backend`).
> Because of that, paths based on `Path(__file__)` point to a temp folder that changes on every run
> (`sys._MEIPASS`, e.g., `/tmp/.../_MEIxxxxxx`). **Never put persistent data on a bundle-relative path**;
> always use a persistent user path such as `data_dir()` (below). Most past auth bugs came from this pitfall.

---

## 1. Symptom → Where to Look (troubleshooting table)

| Symptom | First place to look | Common cause |
|------|------------|-----------|
| `No OAuth profile found` on the first screen | `~/Library/Logs/VEGA/vega-backend.log`, and whether `~/Library/Application Support/VEGA/openai_oauth.json` exists | Not logged in yet (normal) / token not saved to a persistent path |
| The auth screen (browser) does not appear | Same log + whether `pkce_login` was called | Frontend can't call `/api/onboarding/pkce`, or backend can't open the browser |
| Entered an API key but after restart it says "no key" | `GET /api/onboarding/key-source` (source diagnostics) | bearer provider can't read Keychain / key is only on a dead `.env` path |
| `CERTIFICATE_VERIFY_FAILED` (external HTTPS) | `vega-backend.stderr.log` | certifi CA bundle missing — reproduces only on a clean Mac (details in `FIX_0601.md`) |
| New DMG installed but old bug persists | `launchctl print gui/$(id -u)/com.unohee.vega-backend` | A stale LaunchAgent is holding 8100 with the old backend |
| `백엔드 연결 실패` (backend connection failed) page | `~/Library/Logs/VEGA/vega-backend.stderr.log` + `vega-shell.log` | Backend failed to start / port 8100 occupied |
| Gatekeeper blocks on another Mac | DMG notarization/staple status (§4 commands) | Notarization missing, or entitlement lost during signing |
| Don't know which LLM provider is active | `active_provider` + `configured` in `GET /api/onboarding` | — |

> **Meta lesson** (recurring): many bugs only blow up in a clean-install environment. The dev machine
> can't reproduce them due to local Keychain/Python/SSL influences. Verification on a clean account/machine
> before release is the real line of defense.

---

## 2. Path Cheat Sheet

### 2-1. Logs (`~/Library/Logs/VEGA/`)

| File | Contents | Who writes it |
|------|------|---------|
| `vega-backend.log` | Python backend unified log (rotating 5MB×5), includes uvicorn + uncaught exceptions | When run from console/directly (set by the launcher) |
| `vega-backend.stdout.log` | Backend stdout | LaunchAgent (daemon) or Rust fallback spawn |
| `vega-backend.stderr.log` | Backend stderr | same as above |
| `vega-shell.log` | Rust shell diagnostics (update check, backend spawn, LaunchAgent registration) | `desktop/src/lib.rs` `vlog!` |

- The location can be overridden with the `VEGA_LOG_DIR` env var (defaults to the above if unset).
- Code: `pipeline/data_paths.py::log_dir()`, `bin/vega_backend_launcher.py`, `desktop/src/lib.rs::log_dir/shell_log`.

### 2-2. Data / Settings (`~/Library/Application Support/VEGA/`)

This is the persistent user data root returned by **`data_dir()`**. (overridable with `VEGA_DATA_DIR`)
Code: `pipeline/data_paths.py`.

| File/item | Contents |
|-----------|------|
| `openai_oauth.json` | ChatGPT (OpenAI) OAuth token (permission 600) |
| `agent.db` | vega-agent-only SQLite (separate from the main VEGA `vega.db`) |
| `contacts.db` | Contacts |
| `llm_providers.json` | Provider settings + `active` (runtime hot-reloads on every call) |
| `mcp.json`, `tool_groups.json` | MCP/tool settings (user overrides) |
| `user_profile.json` | Onboarding profile (includes the `onboarded` flag) |
| `persona.md`, `widgets.json` | Persona/widgets |
| `.env` | **Persistent** .env (see key priority below). Used as a key fallback in distribution builds |
| `uploads/`, `charts/`, `commands/` | Uploads, charts, user slash commands |

**API keys / secrets — Keychain**
- macOS Keychain, service name **`VEGA`**. Code: `pipeline/keychain.py`.
- Key lookup priority (`keychain.get`): **Keychain → .env → environment variable**.
- `.env` search paths: `~/Library/Application Support/VEGA/.env` (preferred) → repo root `.env` (dev fallback).
  - ⚠️ In a distribution build the repo `.env` does not exist. Distribution-build keys must go in **Keychain** or the **persistent `.env`**.
- Example key names: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API`, `GOOGLE_CLIENT_ID`.
- **Diagnostics**: `GET /api/onboarding/key-source` — returns where each key comes from among Keychain/.env/env var (value masked) + the `.env` paths being searched and whether they exist.

### 2-3. Build / Signing / Distribution

| Item | Path/value |
|------|---------|
| Build script | `scripts/build_dmg.sh` (all-in-one: PyInstaller → Tauri → re-sign → DMG → notarize → updater assets) |
| Signing/notarization script | `scripts/sign_and_notarize.sh` |
| PyInstaller spec | `bin/vega-backend.spec` / entry point `bin/vega_backend_launcher.py` |
| Build venv | `bin/.venv` (includes PyInstaller; if the shebang has a different username it's broken → regenerate) |
| DMG artifact | `build_output/VEGA-<version>.dmg` |
| updater asset | `build_output/updater/VEGA-<version>-aarch64.app.tar.gz` (+`.sig`) |
| entitlements | `desktop/entitlements.plist` (`disable-library-validation` required) |
| Signing ID | `Developer ID Application: Heewon Oh (635QK74RYK)` (login.keychain) |
| Notarization profile | notarytool keychain-profile `vega-notary` (Apple ID zigfrio@naver.com, team 635QK74RYK) |
| updater signing key | `~/.tauri/vega-updater.key` (no password). **If lost, updating existing installs is permanently impossible** |
| Where the version is embedded | `desktop/tauri.conf.json`, `desktop/Cargo.toml`, `scripts/build_dmg.sh` (+ `Cargo.lock` sync) — bump all of them |

**Run the build**:
```bash
source ~/dev/mlx_env/bin/activate
VEGA_NOTARY_PROFILE=vega-notary bash scripts/build_dmg.sh
```
- If the signing certificate is in login.keychain, you don't need to provide `VEGA_KEYCHAIN` (codesign uses the search list).
- Piping with `| tee` hides failures (pipe exit code) — capture logs with `> file 2>&1`.

### 2-4. Process / LaunchAgent

| Item | Value |
|------|-----|
| Backend port | `127.0.0.1:8100` |
| LaunchAgent Label | `com.unohee.vega-backend` |
| plist source (repo) | `desktop/resources/com.unohee.vega-backend.plist` |
| plist bundle (.app) | `/Applications/VEGA.app/Contents/Resources/com.unohee.vega-backend.plist` |
| plist active (runtime) | `~/Library/LaunchAgents/com.unohee.vega-backend.plist` (Rust copies/re-registers after substituting `__HOME__`) |
| Backend binary (installed) | `/Applications/VEGA.app/Contents/MacOS/vega-backend` |
| Install entry flow | `/entry` → branches to `/install` (install wizard) or `/chat` 302 based on onboarding status |

- In daemon mode, on first run Rust (`desktop/src/lib.rs::ensure_launchagent`) refreshes the plist and
  forces the current app's backend via `bootout → bootstrap → kickstart -k`.
  On failure, falls back to spawning `Contents/MacOS/vega-backend` directly.

---

## 3. Frequently Used Debugging Commands

```bash
# ── Logs ──────────────────────────────────────────────
tail -f ~/Library/Logs/VEGA/vega-backend.log          # backend live
tail -50 ~/Library/Logs/VEGA/vega-backend.stderr.log  # daemon stderr
tail -50 ~/Library/Logs/VEGA/vega-shell.log           # Rust shell

# ── Health / diagnostics (when backend is up) ─────────
curl -s http://127.0.0.1:8100/api/health | python3 -m json.tool
curl -s http://127.0.0.1:8100/api/onboarding | python3 -m json.tool          # active/configured
curl -s http://127.0.0.1:8100/api/onboarding/key-source | python3 -m json.tool # key sources

# ── Keys (Keychain, service VEGA) ─────────────────────
security find-generic-password -s VEGA -a OPENAI_API_KEY -w   # prints value (caution)
python3 -m pipeline.keychain get OPENAI_API_KEY               # Keychain→.env→env order
python3 -m pipeline.keychain set OPENAI_API_KEY sk-...        # store in Keychain

# ── Process / port ────────────────────────────────────
lsof -ti:8100                                          # PID occupying 8100
launchctl print gui/$(id -u)/com.unohee.vega-backend   # daemon status
launchctl kickstart -k gui/$(id -u)/com.unohee.vega-backend  # restart daemon
launchctl bootout gui/$(id -u)/com.unohee.vega-backend       # bring down daemon

# ── Run the built backend directly (change port to isolate) ─
VEGA_PORT=8123 /Applications/VEGA.app/Contents/MacOS/vega-backend

# ── Verify signing / notarization ─────────────────────
spctl -a -vvv -t install build_output/VEGA-<version>.dmg  # Gatekeeper
xcrun stapler validate build_output/VEGA-<version>.dmg    # staple
codesign -d --entitlements - /Applications/VEGA.app/Contents/MacOS/vega-backend \
  | grep disable-library-validation                    # confirm entitlement is included
security find-identity -v -p codesigning | grep "Developer ID"
```

---

## 4. Known Pitfalls (summary)

For the detailed background of each item, see memory / `FIX_0601.md`.

1. **Bundle temp-path pitfall** — saving persistent data relative to `Path(__file__)`/cwd breaks under onefile. Use `data_dir()`/`log_dir()`.
2. **bearer provider not querying Keychain** — looking only at env vars loses the key after restart. Needs a `keychain.get_secret` fallback (fixed).
3. **`.env` may be a dead path in distribution builds** — the repo `.env` is not in the bundle. Use the persistent `.env` or Keychain.
4. **certifi CA missing** → `CERTIFICATE_VERIFY_FAILED` only on a clean Mac. Add `collect_data_files("certifi")` to the spec; the launcher pins `SSL_CERT_FILE`.
5. **stale LaunchAgent** — a new install attaches to the old backend. Rust does bootout→bootstrap→kickstart on every run.
6. **entitlement lost during signing** — doing a `--deep` re-sign without entitlements drops `disable-library-validation`, causing PYI-30816. Specify `--entitlements` starting from the inner binaries.
7. **bash 3.2 empty-array pitfall** — under `set -u`, expanding an empty `"${arr[@]}"` is an unbound error. Use the `"${arr[@]+"${arr[@]}"}"` pattern.
8. **create-dmg hang** — if `"dmg"` is in the tauri targets, osascript hangs in headless. Use targets `["app"]` and build the DMG with hdiutil.
9. **fastmcp PackageNotFoundError** — the spec needs `.dist-info` bundled, e.g., `copy_metadata("fastmcp")`.
10. **`| tee` hides build failure** — because of the pipe exit code. Capture logs with a redirect.

---

## 5. Related Documents

- `ARCHITECTURE.md` — full structure
- `FIX_0601.md` — detailed record of 0.1.2 clean-install debugging (SSL/signing/stale agent)
- `desktop/updater/README.md` — automatic update (CF R2) distribution procedure
