# Created: 2026-06-23
# Purpose: office 도구 등록 + pdf_create(markdown→PDF, 한글) 회귀 테스트 (INT-1843).
#          항목1: tools_office 13종이 빈 스텁으로 회귀하지 않는지. 항목2: PDF 생성 동작.
# Dependencies: pipeline.tools, pipeline.tools_office, reportlab, pypdf
# Test Status: green (2026-06-23)

from __future__ import annotations

import pypdf
import pytest

_OFFICE_EXPECTED = {
    "xlsx_create", "xlsx_read", "xlsx_merge", "xlsx_style", "xlsx_set_formula",
    "docx_read", "docx_create", "docx_append",
    "pptx_read", "pptx_create", "pptx_append_slide",
    "latex_compile", "latex_template", "pdf_create",
}


def test_office_and_pdf_tools_registered():
    """공개 빌드에서 office/pdf 도구가 LLM 에 노출되는지 — 빈 스텁 회귀 가드(INT-1843)."""
    import pipeline.tools as t
    names = {s.get("name") for s in t.TOOL_SCHEMAS}
    missing_schema = _OFFICE_EXPECTED - names
    assert not missing_schema, f"스키마 미등록: {missing_schema}"
    missing_fn = {n for n in _OFFICE_EXPECTED if n not in t.TOOL_FUNCTIONS}
    assert not missing_fn, f"함수 미등록: {missing_fn}"


def test_pdf_create_korean_markdown(tmp_path):
    """markdown(한글 + 헤딩·굵게·목록·표·코드펜스)을 PDF 로 렌더 — 구조 + 한글 추출 검증."""
    from pipeline.tools_office import pdf_create
    out = tmp_path / "report.pdf"
    md = (
        "# 한글 보고서\n\n"
        "이것은 **굵게**와 *기울임*, `code` 를 포함한 문단.\n\n"
        "## 목록\n- 첫째\n- 둘째\n\n"
        "## 표\n| 이름 | 값 |\n| --- | --- |\n| 가나다 | 123 |\n\n"
        "```python\ndef f():\n    return 1\n```\n\n---\n끝."
    )
    r = pdf_create(path=str(out), content=md, title="월간 리포트")
    assert r.get("ok") is True, f"pdf_create 실패: {r}"
    assert out.exists() and out.stat().st_size > 1000
    assert out.read_bytes()[:5] == b"%PDF-", "PDF 매직 헤더 아님"
    reader = pypdf.PdfReader(str(out))
    assert len(reader.pages) >= 1
    text = reader.pages[0].extract_text() or ""
    # 한글이 tofu 가 아니라 실제 글자로 들어갔는지 (CID 폰트 + ToUnicode)
    assert any("가" <= c <= "힣" for c in text), f"한글 추출 실패 — 폰트/CID 문제 의심: {text[:80]!r}"
    assert "월간 리포트" in text and "한글 보고서" in text


def test_pdf_create_path_guard_blocks_outside_root():
    """path_guard 밖 경로(/etc)는 [SAFEGUARD] 로 거부되고 파일이 안 생겨야 함."""
    from pipeline.tools_office import pdf_create
    r = pdf_create(path="/etc/vega_guard_test.pdf", content="x")
    assert "error" in r and "SAFEGUARD" in r["error"], f"가드 미작동: {r}"
    import os
    assert not os.path.exists("/etc/vega_guard_test.pdf")


def test_image_convert_path_guard_blocks_src_outside_root(tmp_path):
    from pipeline.tools_office import image_convert
    src = tmp_path / "ok.png"
    src.write_bytes(b"fake")
    r = image_convert(src=str(src), dst="/etc/vega_guard_out.jpg")
    assert "error" in r and "SAFEGUARD" in r["error"], f"src 가드 미작동: {r}"
