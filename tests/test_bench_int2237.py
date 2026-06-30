# Created: 2026-07-01
# Purpose: bench 하니스 견고성 회귀 (INT-2237 audit) — judge 예외·officeeval 검증·odyssey spec.

from __future__ import annotations

import pytest


def test_judge_catches_backend_exception(monkeypatch):
    # judge backend 예외가 배치 전체를 abort 하지 않고 per-row error 로 기록돼야 한다.
    import scripts.bench_lib as bl

    def _boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(bl, "_or_chat", _boom)
    r = bl.judge({"prompt": "p", "rubric": ["c1"]}, "out", "key", judge_backend="openrouter")
    assert r["pass"] is False
    assert "judge_exception" in (r.get("error") or "")


def test_officeeval_unverifiable_spec_fails(monkeypatch, tmp_path):
    # verifier 가 검사하지 않는 키(mom 등)만 있으면 fail-closed (아무 xlsx 통과 방지).
    import scripts.bench_lib as bl
    f = tmp_path / "x.xlsx"
    f.write_bytes(b"x")
    monkeypatch.setattr("pipeline.tools_office.xlsx_read", lambda p: {"rows": []})
    r = bl.verify_officeeval_spec({"officeeval_spec": {"mom": 5}}, "", artifacts=[str(f)])
    assert r["exec_pass"] is False
    assert "unverifiable" in r["exec_error"]


def test_officeeval_recognized_key_passes(monkeypatch, tmp_path):
    import scripts.bench_lib as bl
    f = tmp_path / "x.xlsx"
    f.write_bytes(b"x")
    monkeypatch.setattr("pipeline.tools_office.xlsx_read", lambda p: {"flat": "total sum=42 here"})
    r = bl.verify_officeeval_spec({"officeeval_spec": {"sum": 42}}, "", artifacts=[str(f)])
    assert r["exec_pass"] is True


def test_pptx_deck_skips_keyword_when_none(monkeypatch):
    # require_keyword=None 이면 'vega' 없는 generic 덱도 통과 (false negative 방지).
    import scripts.bench_lib as bl
    monkeypatch.setattr(
        "pipeline.tools_office.pptx_read",
        lambda p: {"slide_count": 6, "slides": [{"texts": ["Quarterly business review"]}]},
    )
    ok, err, checks, art = bl._verify_pptx_deck(["d.pptx"], min_slides=5, require_keyword=None)
    assert ok is True
    # 기본(vega 요구)에서는 같은 덱이 실패
    ok2, _, _, _ = bl._verify_pptx_deck(["d.pptx"], min_slides=5)
    assert ok2 is False


def test_odysseybench_missing_spec_raises(tmp_path):
    # scripts/bench_external 는 .py 파일과 동명 디렉터리가 공존해 서브모듈 import 가 안 되므로
    # importlib 로 파일을 직접 로드해 검증한다 (INT-2237: spec 없으면 raise — silent empty 금지).
    import importlib.util
    import sys
    import pathlib
    p = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "bench_external" / "ingest.py"
    spec = importlib.util.spec_from_file_location("_be_ingest_t", p)
    ing = importlib.util.module_from_spec(spec)
    sys.modules["_be_ingest_t"] = ing
    spec.loader.exec_module(ing)
    ing.OUT_ROOT = tmp_path  # spec.json 없는 경로
    with pytest.raises(FileNotFoundError):
        ing.ingest_odysseybench(10)
