# Plan — VEGA.app Standalone (Docker-free) Code Execution & Document Processing

> Goal: on a non-developer's machine, installing just VEGA.app is enough for Python code execution + PDF/XLS/DOCX preview·generation
> to work without Docker·Xcode CLT·system Python. Basis: `reference_frozen_interpreter_local_exec.md` (verified empirically on 2026-06-15).

## Background — Problems Confirmed Empirically

The 3 causes that currently break code execution/document processing on non-developer machines:

1. **`pipeline/tools_office.py:23`** — `_sandbox_call()` forces `sandbox_python` (Docker).
   All xlsx/docx/pptx generation·editing dies without Docker.
2. **`pipeline/tools_code.py:21`** — `python_exec` hardcodes `~/dev/mlx_env/bin/python` (the developer's own environment).
   A typical user's machine does not have that path → immediate failure.
3. **`bin/vega-backend.spec`** — office/PDF/image libraries are **completely absent** from `hiddenimports`.
   Even PDF extraction (`tools_google._pdf_bytes_to_text`, the host-direct path) fails due to missing libraries.

Feasibility of resolution confirmed by verification:
- frozen interpreter re-entry (`vega-backend run-code <code>`) works, and process isolation (timeout kill) is normal.
- A bundle including numpy/pandas C extensions is possible (`hiddenimports` + `collect_submodules`).

## Task Breakdown (verification criteria specified)

### Phase 1 — Add bundle dependencies (first, valuable on its own)

Add to the package-collection loop in `bin/vega-backend.spec`:

```python
for pkg in (..., "openpyxl", "pypdf", "docx", "pptx",
            "msoffcrypto", "PIL", "xlrd",
            "numpy", "pandas"):   # numpy/pandas are for data processing in code execution
    hiddenimports += collect_submodules(pkg)
```

- **Verification:** local PyInstaller build → `vega-backend run-code "import openpyxl, pypdf, docx, pptx, msoffcrypto, PIL, numpy, pandas; print('OK')"` prints OK.
- **Trade-off:** increased bundle size (numpy/pandas ~tens of MB, office ~10MB). Measure and record.
- **Caution:** import name ≠ package name — python-docx→`docx`, python-pptx→`pptx`, Pillow→`PIL`.

### Phase 2 — Add the frozen interpreter entry point

Add a subcommand branch at the top of `bin/vega_backend_launcher.py` (before server startup):

```python
# run-code / run-python: reuse the frozen interpreter as an isolated subprocess
if len(sys.argv) >= 2 and sys.argv[1] in ("run-code", "run-python"):
    import runpy
    if sys.argv[1] == "run-code":
        exec(compile(sys.argv[2], "<vega>", "exec"), {"__name__": "__main__"})
    else:
        script = sys.argv[2]; sys.argv = [script] + sys.argv[3:]
        runpy.run_path(script, run_name="__main__")
    sys.exit(0)
```

- Place this branch **before** logging·certifi·port-wait (fast one-shot execution, no server initialization needed).
- **Verification:** with the built bundle, reproduce timeout kill (infinite loop exit 124), stdout/stderr separation, and exception-traceback propagation.

### Phase 3 — Execution layer fallback (Docker → frozen in-process/subprocess)

Add a runtime interpreter-decision helper (`pipeline/tools_code.py`):

```python
def _interp_cmd():
    """Decide the code-execution interpreter: self run-code if frozen, else mlx_env/python."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "run-code"]   # bundled interpreter
    if MLX_PYTHON.exists():
        return [str(MLX_PYTHON)]              # development environment
    return [sys.executable]                   # system fallback
```

- `python_exec` (tools_code.py:243): `[str(MLX_PYTHON), tmppath]` →
  `[sys.executable, "run-code", code_str]` if frozen, otherwise as before.
- `tools_office.py:_sandbox_call`: `sandbox_python` (keep isolation) if `docker_state()=="ok"`,
  otherwise fall back to `_host_call`, which runs the same code as a frozen in-process/subprocess.
  - office work is *fixed library calls* so there is no infinite-loop risk → direct in-process exec is also safe.
    But for consistency, reusing the `python_exec` path is recommended.
- **Verification:** with Docker off, actually call `xlsx_create`/`docx_create`/PDF extraction → file generation·text extraction succeeds.
  With Docker on, confirm there is no regression in the existing sandbox path.

### Phase 4 — Safety net (reinforce now that isolation is weaker)

frozen host execution has only process isolation; home-directory access is possible. (See the isolation level in `reference_frozen_interpreter_local_exec.md`)

- Add to the `python_exec` host path a guard comparable to the existing bash safeguard:
  block writes to `.env`/secret paths, warn on destructive operations outside `/vega_data`·`/host_home`.
- **Verification:** attempt to read `.env`/`os.remove` via `python_exec` → returns blocked or warned.
- UI: in the code-execution tool status display, explicitly state "host execution (low isolation)" when Docker is absent.

## Order Dependencies

Phase 1 → Phase 2 → Phase 3 (1·2 are prerequisites for 3). Phase 4 immediately after 3, or in parallel.
Phase 1 alone restores the host-direct path for PDF/DOCX preview, so **it is worth shipping on its own**.

## Open / Decisions Needed

- **Bundle size vs features**: including numpy/pandas adds ~tens of MB. Whether to bundle data-analysis packages
  by default for code execution, or split them into a separate download (external site-packages) → user decision.
- **office fallback isolation**: on Docker fallback, whether to run office work in-process exec or split it into a subprocess.
  Subprocess is safer but slightly slower.
- **abuse**: host execution has no system isolation. The risk of running malicious code in a free distribution build is greater than with Docker.

## Routing

- Execute this plan → Linear issue (VEGA / Intrect team). Memory basis: `reference_frozen_interpreter_local_exec.md`,
  `project_positioning_fear_not_terminal.md`.
