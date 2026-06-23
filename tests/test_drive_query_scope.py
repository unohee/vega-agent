# Created: 2026-06-23
# Purpose: drive_search 폴더 스코프 쿼리 빌더 회귀 — INT-1884 (폴더 밖 과다 스캔 방지).
# Dependencies: pipeline.tools_google._build_drive_query
# Test Status: green (2026-06-23)

from __future__ import annotations

from pipeline.tools_google import _build_drive_query


def test_natural_language_wrapped_as_fulltext():
    q = _build_drive_query("사전답사 보고서")
    assert "fullText contains" in q and "trashed = false" in q


def test_drive_syntax_passthrough():
    q = _build_drive_query("name contains 'KSL'")
    assert q == "name contains 'KSL'"


def test_folder_scope_appended():
    q = _build_drive_query("사전답사", folder_id="1cwYAGqdB0kZ")
    assert "'1cwYAGqdB0kZ' in parents" in q
    assert q.startswith("(") and "fullText contains" in q


def test_folder_scope_with_drive_syntax():
    q = _build_drive_query("mimeType = 'application/pdf'", folder_id="FID")
    assert "'FID' in parents" in q and "mimeType = 'application/pdf'" in q


def test_no_folder_no_parents_clause():
    assert "in parents" not in _build_drive_query("그냥 검색")
