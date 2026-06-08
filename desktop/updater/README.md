# VEGA 자동 업데이트 (Tauri Updater + CF R2)

데스크탑 앱(daemon DMG)은 시작 시 백그라운드로 최신 버전을 조회해, 새 버전이 있으면
**조용히 내려받아 설치 후 재시작**한다. (`desktop/src/lib.rs` → `spawn_update_check`)

## 구성 요소

| 위치 | 역할 |
|------|------|
| `tauri.conf.json` → `plugins.updater.endpoints` | 업데이트 매니페스트(JSON) 조회 URL. **현재 PLACEHOLDER** |
| `tauri.conf.json` → `plugins.updater.pubkey` | 서명 검증 공개키 (이미 실제 키 주입됨) |
| `tauri.conf.json` → `bundle.createUpdaterArtifacts` | 빌드 시 `.app.tar.gz` 생성 |
| `~/.tauri/vega-updater.key` | **서명 개인키** (절대 커밋 금지, repo 밖에 보관) |
| `capabilities/desktop.json` → `updater:default` | updater 권한 |
| `updater/latest.json.template` | R2에 올릴 매니페스트 템플릿 |

## endpoint 패턴

```
https://<R2_PUBLIC_DOMAIN>/vega/updates/{{target}}/{{arch}}/{{current_version}}
```
Tauri가 `{{target}}`(`darwin`), `{{arch}}`(`aarch64`), `{{current_version}}`을 치환해 GET 한다.
R2는 이 경로에 매니페스트 JSON(아래)을 반환하면 된다 (정적 파일 호스팅으로 충분).

## 배포 절차 (새 버전 릴리스 시)

1. **빌드** — 서명 키가 있으면 자동으로 updater 아티팩트가 생성된다:
   ```bash
   bash scripts/build_dmg.sh
   # → build_output/updater/VEGA-<ver>-aarch64.app.tar.gz (+ .sig)
   ```
2. **R2 업로드** — `.app.tar.gz`를 공개 다운로드 경로에 올린다.
3. **매니페스트 작성** — `latest.json.template`을 복사해 채운다:
   - `version`: 새 버전 (SemVer)
   - `platforms.darwin-aarch64.url`: 위 2)의 `.app.tar.gz` 공개 URL
   - `platforms.darwin-aarch64.signature`: **`.sig` 파일의 내용**(경로 아님 — 빌드 로그가 출력해 줌)
4. **매니페스트 업로드** — endpoint 경로에 JSON을 올린다.

## ⚠️ 배포 전 반드시 할 일

- [ ] `tauri.conf.json`의 `endpoints` PLACEHOLDER를 **실제 R2 도메인**으로 교체
- [ ] R2 버킷 + 공개 도메인(또는 Cloudflare CDN) 셋업
- [ ] 개인키(`~/.tauri/vega-updater.key`) 안전 백업 — 분실 시 기존 설치본에 업데이트 불가

## 키 재생성 (분실 시 — 기존 사용자는 업데이트 못 받음)

```bash
cargo tauri signer generate -w ~/.tauri/vega-updater.key --password ""
# 새 pubkey를 tauri.conf.json plugins.updater.pubkey 에 교체
```
