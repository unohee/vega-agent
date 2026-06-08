# VEGA 디버깅 핸드북

배포본(`.app`)에서 뭔가 깨졌을 때 **어디를 봐야 하는지** 모아둔 상설 레퍼런스.
"이 증상이면 여기" → 경로 치트시트 → 자주 쓰는 명령 순서로 본다.

> 핵심 전제: 배포본 백엔드는 PyInstaller **onefile** 바이너리(`vega-backend`)다.
> 그래서 `Path(__file__)` 기준 경로는 매 실행마다 바뀌는 임시폴더(`sys._MEIPASS`,
> 예: `/tmp/.../_MEIxxxxxx`)를 가리킨다. **영속 데이터는 절대 번들 상대경로에 두면 안 되고**,
> 항상 `data_dir()`(아래) 같은 사용자 영속 경로를 써야 한다. 과거 인증 버그 대부분이 이 함정에서 나왔다.

---

## 1. 증상 → 어디를 보나 (트러블슈팅 표)

| 증상 | 1차로 볼 곳 | 흔한 원인 |
|------|------------|-----------|
| 첫 화면에 `No OAuth profile found` | `~/Library/Logs/VEGA/vega-backend.log`, 그리고 `~/Library/Application Support/VEGA/openai_oauth.json` 존재 여부 | 아직 로그인 안 함(정상) / 토큰이 영속 경로에 안 저장됨 |
| 인증 장면(브라우저)이 안 뜸 | 같은 로그 + `pkce_login` 호출 여부 | 프론트가 `/api/onboarding/pkce`를 못 부르거나 백엔드가 브라우저 못 엶 |
| API 키를 넣었는데 재시작하면 "키 없음" | `GET /api/onboarding/key-source` (출처 진단) | bearer 프로바이더가 Keychain 못 읽음 / 키가 `.env` 죽은 경로에만 있음 |
| `CERTIFICATE_VERIFY_FAILED` (외부 HTTPS) | `vega-backend.stderr.log` | certifi CA 번들 누락 — 깨끗한 맥에서만 재현 (자세히는 `FIX_0601.md`) |
| 새 DMG 설치해도 옛 버그 그대로 | `launchctl print gui/$(id -u)/com.unohee.vega-backend` | stale LaunchAgent가 옛 백엔드로 8100을 잡고 있음 |
| `백엔드 연결 실패` 페이지 | `~/Library/Logs/VEGA/vega-backend.stderr.log` + `vega-shell.log` | 백엔드 기동 실패 / 포트 8100 점유 |
| 다른 맥에서 Gatekeeper 차단 | DMG 공증/staple 상태 (§4 명령) | 공증 누락 또는 서명 시 entitlement 유실 |
| 어떤 LLM 프로바이더가 활성인지 모름 | `GET /api/onboarding` 의 `active_provider` + `configured` | — |

> **메타 교훈**(반복됨): clean-install 환경에서만 터지는 버그가 많다. 개발 머신은 로컬
> Keychain/Python/SSL 영향으로 재현이 안 된다. 배포 전 깨끗한 계정/머신 검증이 진짜 방어선이다.

---

## 2. 경로 치트시트

### 2-1. 로그 (`~/Library/Logs/VEGA/`)

| 파일 | 내용 | 누가 씀 |
|------|------|---------|
| `vega-backend.log` | Python 백엔드 통합 로그(회전 5MB×5), uvicorn + 미잡힌 예외 포함 | 콘솔/직접 실행 시 (런처가 설정) |
| `vega-backend.stdout.log` | 백엔드 stdout | LaunchAgent(daemon) 또는 Rust fallback spawn |
| `vega-backend.stderr.log` | 백엔드 stderr | 〃 |
| `vega-shell.log` | Rust 셸 진단(업데이트 체크·백엔드 spawn·LaunchAgent 등록) | `desktop/src/lib.rs` `vlog!` |

- 위치는 `VEGA_LOG_DIR` 환경변수로 덮어쓸 수 있음 (없으면 위 기본값).
- 코드: `pipeline/data_paths.py::log_dir()`, `bin/vega_backend_launcher.py`, `desktop/src/lib.rs::log_dir/shell_log`.

### 2-2. 데이터·설정 (`~/Library/Application Support/VEGA/`)

이게 **`data_dir()`** 가 반환하는 영속 사용자 데이터 루트. (`VEGA_DATA_DIR` 로 override 가능)
코드: `pipeline/data_paths.py`.

| 파일/항목 | 내용 |
|-----------|------|
| `openai_oauth.json` | ChatGPT(OpenAI) OAuth 토큰 (퍼미션 600) |
| `agent.db` | vega-core 전용 SQLite (메인 VEGA `vega.db`와 분리) |
| `contacts.db` | 연락처 |
| `llm_providers.json` | 프로바이더 설정 + `active` (런타임이 매 호출 hot-reload) |
| `mcp.json`, `tool_groups.json` | MCP/툴 설정 (사용자 오버라이드) |
| `user_profile.json` | 온보딩 프로필 (`onboarded` 플래그 포함) |
| `persona.md`, `widgets.json` | 페르소나/위젯 |
| `.env` | **영속** .env (아래 키 우선순위 참고). 배포본에서 키 폴백용 |
| `uploads/`, `charts/`, `commands/` | 업로드·차트·사용자 슬래시 커맨드 |

**API 키 / 시크릿 — Keychain**
- macOS Keychain, 서비스명 **`VEGA`**. 코드: `pipeline/keychain.py`.
- 키 조회 우선순위(`keychain.get`): **Keychain → .env → 환경변수**.
- `.env` 탐색 경로: `~/Library/Application Support/VEGA/.env`(우선) → 레포 루트 `.env`(개발용 폴백).
  - ⚠️ 배포본에서 레포 `.env`는 존재하지 않는다. 배포본 키는 **Keychain** 또는 **영속 `.env`** 에 둬야 함.
- 키 이름 예: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API`, `GOOGLE_CLIENT_ID`.
- **진단**: `GET /api/onboarding/key-source` — 각 키가 Keychain/.env/환경변수 중 어디서 오는지(값은 마스킹) + 탐색 중인 `.env` 경로와 존재 여부를 돌려준다.

### 2-3. 빌드·서명·배포

| 항목 | 경로/값 |
|------|---------|
| 빌드 스크립트 | `scripts/build_dmg.sh` (한 방: PyInstaller → Tauri → 재서명 → DMG → 공증 → updater 자산) |
| 서명/공증 스크립트 | `scripts/sign_and_notarize.sh` |
| PyInstaller spec | `bin/vega-backend.spec` / 진입점 `bin/vega_backend_launcher.py` |
| 빌드용 venv | `bin/.venv` (PyInstaller 포함; shebang이 다른 사용자명이면 깨진 것 → 재생성) |
| DMG 산출물 | `build_output/VEGA-<버전>.dmg` |
| updater 자산 | `build_output/updater/VEGA-<버전>-aarch64.app.tar.gz` (+`.sig`) |
| entitlements | `desktop/entitlements.plist` (`disable-library-validation` 필수) |
| 서명 ID | `Developer ID Application: Heewon Oh (635QK74RYK)` (login.keychain) |
| 공증 프로필 | notarytool keychain-profile `vega-notary` (Apple ID zigfrio@naver.com, team 635QK74RYK) |
| updater 서명 키 | `~/.tauri/vega-updater.key` (비번 없음). **분실 시 기존 설치본 업데이트 영구 불가** |
| 버전 박힌 곳 | `desktop/tauri.conf.json`, `desktop/Cargo.toml`, `scripts/build_dmg.sh` (+ `Cargo.lock` 동기화) — 올릴 땐 전부 |

**빌드 실행**:
```bash
source ~/dev/mlx_env/bin/activate
VEGA_NOTARY_PROFILE=vega-notary bash scripts/build_dmg.sh
```
- 서명 인증서가 login.keychain에 있으면 `VEGA_KEYCHAIN`은 안 줘도 됨(codesign이 search list 사용).
- `| tee` 로 파이프하면 실패가 가려진다(파이프 exit code) — 로그는 `> file 2>&1` 로 받을 것.

### 2-4. 프로세스·LaunchAgent

| 항목 | 값 |
|------|-----|
| 백엔드 포트 | `127.0.0.1:8100` |
| LaunchAgent Label | `com.unohee.vega-backend` |
| plist 소스(레포) | `desktop/resources/com.unohee.vega-backend.plist` |
| plist 번들(.app) | `/Applications/VEGA.app/Contents/Resources/com.unohee.vega-backend.plist` |
| plist 활성(런타임) | `~/Library/LaunchAgents/com.unohee.vega-backend.plist` (Rust가 `__HOME__` 치환 후 복사·재등록) |
| 백엔드 바이너리(설치본) | `/Applications/VEGA.app/Contents/MacOS/vega-backend` |
| 설치 진입 흐름 | `/entry` → 온보딩 여부로 `/install`(설치 마법사) 또는 `/chat` 302 분기 |

- daemon 모드는 첫 실행 시 Rust(`desktop/src/lib.rs::ensure_launchagent`)가 plist를 갱신하고
  `bootout → bootstrap → kickstart -k` 로 현재 앱의 백엔드를 강제 반영한다.
  실패 시 `Contents/MacOS/vega-backend` 직접 spawn fallback.

---

## 3. 자주 쓰는 디버깅 명령

```bash
# ── 로그 ──────────────────────────────────────────────
tail -f ~/Library/Logs/VEGA/vega-backend.log          # 백엔드 실시간
tail -50 ~/Library/Logs/VEGA/vega-backend.stderr.log  # 데몬 stderr
tail -50 ~/Library/Logs/VEGA/vega-shell.log           # Rust 셸

# ── 헬스/진단 (백엔드 떠 있을 때) ─────────────────────
curl -s http://127.0.0.1:8100/api/health | python3 -m json.tool
curl -s http://127.0.0.1:8100/api/onboarding | python3 -m json.tool          # active/configured
curl -s http://127.0.0.1:8100/api/onboarding/key-source | python3 -m json.tool # 키 출처

# ── 키(Keychain, 서비스 VEGA) ─────────────────────────
security find-generic-password -s VEGA -a OPENAI_API_KEY -w   # 값 출력(주의)
python3 -m pipeline.keychain get OPENAI_API_KEY               # Keychain→.env→env 순
python3 -m pipeline.keychain set OPENAI_API_KEY sk-...        # Keychain 저장

# ── 프로세스/포트 ─────────────────────────────────────
lsof -ti:8100                                          # 8100 점유 PID
launchctl print gui/$(id -u)/com.unohee.vega-backend   # 데몬 상태
launchctl kickstart -k gui/$(id -u)/com.unohee.vega-backend  # 데몬 재시작
launchctl bootout gui/$(id -u)/com.unohee.vega-backend       # 데몬 내리기

# ── 빌드된 백엔드 직접 띄워 보기 (포트 바꿔 격리) ─────
VEGA_PORT=8123 /Applications/VEGA.app/Contents/MacOS/vega-backend

# ── 서명/공증 확인 ────────────────────────────────────
spctl -a -vvv -t install build_output/VEGA-<버전>.dmg  # Gatekeeper
xcrun stapler validate build_output/VEGA-<버전>.dmg    # staple
codesign -d --entitlements - /Applications/VEGA.app/Contents/MacOS/vega-backend \
  | grep disable-library-validation                    # entitlement 포함 확인
security find-identity -v -p codesigning | grep "Developer ID"
```

---

## 4. 알려진 함정 (요약)

각 항목의 자세한 경위는 메모리/`FIX_0601.md` 참고.

1. **번들 임시경로 함정** — `Path(__file__)`/cwd 기준 영속 데이터 저장은 onefile에서 깨진다. `data_dir()`/`log_dir()` 써라.
2. **bearer 프로바이더 Keychain 미조회** — 환경변수만 보면 재시작 후 키 분실. `keychain.get_secret` 폴백 필요(수정됨).
3. **`.env`는 배포본에서 죽은 경로일 수 있음** — 레포 `.env`는 번들에 없다. 영속 `.env` 또는 Keychain 사용.
4. **certifi CA 누락** → 깨끗한 맥에서만 `CERTIFICATE_VERIFY_FAILED`. spec에 `collect_data_files("certifi")`, 런처가 `SSL_CERT_FILE` 고정.
5. **stale LaunchAgent** — 새 설치본이 옛 백엔드에 붙음. Rust가 매 실행 bootout→bootstrap→kickstart.
6. **서명 시 entitlement 유실** — `--deep` 재서명을 entitlements 없이 하면 `disable-library-validation` 사라져 PYI-30816. 내부 바이너리부터 `--entitlements` 명시.
7. **bash 3.2 빈 배열 함정** — `set -u`에서 빈 `"${arr[@]}"` 확장이 unbound error. `"${arr[@]+"${arr[@]}"}"` 패턴 사용.
8. **create-dmg hang** — tauri targets에 `"dmg"`가 있으면 헤드리스에서 osascript hang. targets는 `["app"]`, DMG는 hdiutil로.
9. **fastmcp PackageNotFoundError** — spec에 `copy_metadata("fastmcp")` 등 `.dist-info` 동봉 필요.
10. **`| tee` 가 빌드 실패를 가림** — 파이프 exit code 때문. 로그는 리다이렉트로 받을 것.

---

## 5. 관련 문서

- `ARCHITECTURE.md` — 전체 구조
- `FIX_0601.md` — 0.1.2 clean-install 디버깅 상세 기록 (SSL/서명/stale agent)
- `desktop/updater/README.md` — 자동 업데이트(CF R2) 배포 절차
