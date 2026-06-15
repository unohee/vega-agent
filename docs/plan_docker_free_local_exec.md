# 계획서 — VEGA.app 단독(Docker 없이) 코드 실행 & 문서 처리

> 목표: 비개발자 머신에서 VEGA.app만 설치해도 Python 코드 실행 + PDF/XLS/DOCX 프리뷰·생성이
> Docker·Xcode CLT·시스템 Python 없이 동작한다. 근거: `reference_frozen_interpreter_local_exec.md` (2026-06-15 실측 검증).

## 배경 — 실측으로 확정된 문제

현재 코드 실행/문서 처리가 비개발자 머신에서 깨지는 3개 원인:

1. **`pipeline/tools_office.py:23`** — `_sandbox_call()`이 `sandbox_python`(Docker) 강제.
   xlsx/docx/pptx 생성·편집 전부 Docker 없으면 죽음.
2. **`pipeline/tools_code.py:21`** — `python_exec`가 `~/dev/mlx_env/bin/python`(개발자 본인 환경) 하드코딩.
   일반 유저 머신엔 그 경로가 없음 → 즉시 실패.
3. **`bin/vega-backend.spec`** — office/PDF/이미지 라이브러리가 `hiddenimports`에 **전혀 없음**.
   PDF 추출(`tools_google._pdf_bytes_to_text`, 호스트 직접 경로)마저 라이브러리 누락으로 실패.

검증으로 확정된 해결 가능성:
- frozen 인터프리터 재진입(`vega-backend run-code <code>`) 작동, 프로세스 격리(timeout kill) 정상.
- numpy/pandas C extension 포함 번들 가능(`hiddenimports` + `collect_submodules`).

## 작업 분해 (검증 기준 명시)

### Phase 1 — 번들 의존성 추가 (가장 먼저, 단독으로도 가치)

`bin/vega-backend.spec`의 패키지 수집 루프에 추가:

```python
for pkg in (..., "openpyxl", "pypdf", "docx", "pptx",
            "msoffcrypto", "PIL", "xlrd",
            "numpy", "pandas"):   # numpy/pandas는 코드 실행 데이터 처리용
    hiddenimports += collect_submodules(pkg)
```

- **검증:** 로컬 PyInstaller 빌드 → `vega-backend run-code "import openpyxl, pypdf, docx, pptx, msoffcrypto, PIL, numpy, pandas; print('OK')"` 가 OK 출력.
- **트레이드오프:** 번들 크기 증가(numpy/pandas ~수십 MB, office ~10MB). 측정해서 기록.
- **주의:** import 이름 ≠ 패키지 이름 — python-docx→`docx`, python-pptx→`pptx`, Pillow→`PIL`.

### Phase 2 — frozen 인터프리터 진입점 추가

`bin/vega_backend_launcher.py` 상단(서버 기동 전)에 서브커맨드 분기:

```python
# run-code / run-python: frozen 인터프리터를 격리 서브프로세스로 재사용
if len(sys.argv) >= 2 and sys.argv[1] in ("run-code", "run-python"):
    import runpy
    if sys.argv[1] == "run-code":
        exec(compile(sys.argv[2], "<vega>", "exec"), {"__name__": "__main__"})
    else:
        script = sys.argv[2]; sys.argv = [script] + sys.argv[3:]
        runpy.run_path(script, run_name="__main__")
    sys.exit(0)
```

- 이 분기는 로깅·certifi·포트대기 **이전**에 둔다(빠른 단발 실행, 서버 초기화 불필요).
- **검증:** 빌드된 번들로 timeout kill(무한루프 exit 124), stdout/stderr 분리, 예외 트레이스백 전달 재현.

### Phase 3 — 실행 레이어 폴백 (Docker → frozen 인프로세스/서브프로세스)

런타임 인터프리터 결정 헬퍼 추가 (`pipeline/tools_code.py`):

```python
def _interp_cmd():
    """코드 실행 인터프리터 결정: frozen이면 self run-code, 아니면 mlx_env/python."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "run-code"]   # 동봉 인터프리터
    if MLX_PYTHON.exists():
        return [str(MLX_PYTHON)]              # 개발 환경
    return [sys.executable]                   # 시스템 fallback
```

- `python_exec` (tools_code.py:243): `[str(MLX_PYTHON), tmppath]` →
  frozen이면 `[sys.executable, "run-code", code_str]`, 아니면 기존.
- `tools_office.py:_sandbox_call`: `docker_state()=="ok"`면 `sandbox_python`(격리 유지),
  아니면 동일 코드를 frozen 인프로세스/서브프로세스로 실행하는 `_host_call`로 폴백.
  - office 작업은 *정해진 라이브러리 호출*이라 무한루프 위험 없음 → 인프로세스 직접 exec도 안전.
    단 일관성 위해 `python_exec` 경로 재사용 권장.
- **검증:** Docker 끈 상태에서 `xlsx_create`/`docx_create`/PDF 추출 실제 호출 → 파일 생성·텍스트 추출 성공.
  Docker 켠 상태에서 기존 샌드박스 경로 회귀 없음 확인.

### Phase 4 — 안전망 (격리가 약해진 만큼 보강)

frozen 호스트 실행은 프로세스 격리뿐, 홈디렉터리 접근 가능. (`reference_frozen_interpreter_local_exec.md` 격리 수준 참조)

- `python_exec` 호스트 경로에도 기존 bash safeguard에 준하는 가드:
  `.env`/secret 경로 쓰기 차단, `/vega_data`·`/host_home` 밖 파괴적 작업 경고.
- **검증:** `python_exec`로 `.env` 읽기/`os.remove` 시도 → 차단 또는 경고 반환.
- UI: 코드 실행 도구 상태 표시에서 Docker 없을 때 "호스트 실행(낮은 격리)" 명시.

## 순서 의존성

Phase 1 → Phase 2 → Phase 3 (1·2가 3의 전제). Phase 4는 3 직후 또는 병행.
Phase 1만으로도 PDF/DOCX 프리뷰 호스트 직접 경로가 복구되므로 **단독 출시 가치 있음**.

## 미해결 / 결정 필요

- **번들 크기 vs 기능**: numpy/pandas까지 다 넣으면 ~수십 MB 증가. 코드 실행에 데이터 분석 패키지를
  기본 동봉할지, 아니면 별도 다운로드(외부 site-packages)로 분리할지 → 사용자 결정.
- **office 폴백 격리**: Docker 폴백 시 office 작업을 인프로세스 exec할지 서브프로세스로 분리할지.
  서브프로세스가 안전하나 약간 느림.
- **abuse**: 호스트 실행은 시스템 격리 없음. 무료 배포본에서 악성 코드 실행 리스크는 Docker 대비 큼.

## 라우팅

- 이 계획 실행 → Linear 이슈 (VEGA / Intrect 팀). 메모리 근거: `reference_frozen_interpreter_local_exec.md`,
  `project_positioning_fear_not_terminal.md`.
