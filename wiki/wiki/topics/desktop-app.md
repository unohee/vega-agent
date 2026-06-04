---
title: "Tauri v2 데스크톱 앱 + DMG 배포"
tags: [tauri, desktop, dmg, pyinstaller, launchagent]
updated: 2026-06-02
status: active
---

# Tauri v2 데스크톱 앱 + DMG 배포

`desktop/` 디렉터리. Tauri v2(Rust 셸) + PyInstaller 백엔드 번들.

## 아키텍처

```
Tauri (Rust 셸)
  ├── 트레이 아이콘
  ├── 전역 단축키 (⌘⇧V)
  └── 창 토글
       ↓ (첫 실행 시)
LaunchAgent 등록 (com.unohee.vega-backend)
  → bin/vega-backend (PyInstaller, 94MB)
  → uvicorn web.server:app
```

## DMG 빌드

```bash
bash scripts/build_dmg.sh
```
순서: PyInstaller (`bin/vega-backend.spec`) → `cargo tauri build` → DMG 패키징.
Developer ID 인증서 없으면 무서명 빌드 자동 전환.

## 알려진 함정

- `create-dmg` hang: 일부 환경에서 대화형 프롬프트 waiting → `--no-internet-enable` 플래그 필요
- fastmcp 메타데이터: PyInstaller spec에서 hidden import로 명시해야 번들에 포함
- `bin/vega-backend` 94MB → whisper 라이브러리 포함 불가 (PyTorch ~2GB)

## 관련

- [[topics/stt-integration]] — PyInstaller 번들 제약
- `desktop/Cargo.lock`, `bin/vega-backend.spec`
