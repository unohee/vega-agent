---
title: "Tauri v2 Desktop App + DMG Distribution"
tags: [tauri, desktop, dmg, pyinstaller, launchagent]
updated: 2026-06-02
status: active
---

# Tauri v2 Desktop App + DMG Distribution

`desktop/` directory. Tauri v2 (Rust shell) + PyInstaller backend bundle.

## Architecture

```
Tauri (Rust shell)
  ├── tray icon
  └── window toggle
       ↓ (on first launch)
LaunchAgent registration (com.unohee.vega-backend)
  → bin/vega-backend (PyInstaller, 94MB)
  → uvicorn web.server:app
```

## DMG Build

```bash
bash scripts/build_dmg.sh
```
Order: PyInstaller (`bin/vega-backend.spec`) → `cargo tauri build` → DMG packaging.
Without a Developer ID certificate, it automatically falls back to an unsigned build.

## Known Pitfalls

- `create-dmg` hang: in some environments an interactive prompt is left waiting → the `--no-internet-enable` flag is required
- fastmcp metadata: must be declared as a hidden import in the PyInstaller spec to be included in the bundle
- `bin/vega-backend` 94MB → the whisper library cannot be included (PyTorch ~2GB)

## Related

- [[topics/stt-integration]] — PyInstaller bundle constraints
- `desktop/Cargo.lock`, `bin/vega-backend.spec`
