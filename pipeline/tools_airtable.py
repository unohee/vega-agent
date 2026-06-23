# Created: 2026-06-18
# Purpose: Airtable 네이티브 도구 — base/table/record 조회·생성·수정 (INT-1498).
#   pyairtable 기반. INT-1570(Excel 빈 행) 근본 해결: Airtable 의 linked/lookup
#   필드는 연결 레코드의 **record ID 배열**만 반환하므로, 실제 값을 보려면
#   airtable_get_records 로 그 ID 들을 한 번에 조회해야 한다.
#
#   주의(실측 2026-06-18): pyairtable 3.3.0 의 base.schema() 는 일부 필드 타입
#   (aiText 등)에서 pydantic ValidationError 로 죽는다 → list_tables 는 메타 REST
#   API(/v0/meta/bases/{id}/tables)를 직접 호출해 우회한다.
# Dependencies: pyairtable, requests, pipeline.auth.airtable
# Test Status: tests/test_tools_workspace.py
from __future__ import annotations

from typing import Any

_META_BASE = "https://api.airtable.com/v0/meta"
_RECONNECT_MSG = "Airtable 미연결 — 설정 → 워크스페이스에서 Airtable PAT 를 연결하세요."


def _require_pat() -> str:
    from pipeline.auth import airtable as _auth
    pat = _auth.token()
    if not pat:
        raise RuntimeError(_RECONNECT_MSG)
    return pat


def _api():
    from pyairtable import Api
    return Api(_require_pat())


# ── 조회 ────────────────────────────────────────────────────────────────────────

def airtable_list_bases() -> list[dict]:
    """접근 가능한 Airtable base 목록. base_id 를 모를 때 먼저 호출."""
    return [{"id": b.id, "name": b.name} for b in _api().bases()]


def airtable_list_tables(base_id: str) -> list[dict]:
    """base 의 테이블·필드 스키마. linked 필드는 어느 테이블로 연결되는지(linked_table_id) 표시.

    pyairtable schema() 가 일부 필드 타입에서 죽으므로 메타 REST API 직접 호출.
    """
    import requests
    r = requests.get(
        f"{_META_BASE}/bases/{base_id}/tables",
        headers={"Authorization": f"Bearer {_require_pat()}"},
        timeout=20,
    )
    if r.status_code in (401, 403):
        raise RuntimeError(
            "Airtable 권한 부족(스키마 읽기 거부) — PAT에 "
            "`schema.bases:read` · `data.records:read` 스코프와 해당 base 접근 권한이 "
            "있어야 합니다. 설정 → 워크스페이스에서 Airtable PAT를 그 스코프로 재연결하세요. "
            f"(HTTP {r.status_code})"
        )
    if r.status_code >= 400:
        raise RuntimeError(f"Airtable meta API HTTP {r.status_code}: {r.text[:200]}")
    out = []
    for t in r.json().get("tables", []):
        fields = []
        for f in t.get("fields", []):
            fd = {"name": f.get("name"), "type": f.get("type")}
            opts = f.get("options") or {}
            linked = opts.get("linkedTableId")
            if linked:
                fd["linked_table_id"] = linked
            fields.append(fd)
        out.append({
            "id": t.get("id"),
            "name": t.get("name"),
            "primary_field_id": t.get("primaryFieldId"),
            "fields": fields,
        })
    return out


def airtable_list_records(
    base_id: str,
    table_id: str,
    fields: list[str] | None = None,
    formula: str | None = None,
    max_records: int | None = None,
    view: str | None = None,
) -> dict:
    """테이블 레코드 조회 (자동 페이지네이션).

    ⚠️ linked/lookup 필드 값은 연결 레코드의 **record ID 배열**(['rec...'])일 뿐
    실제 값이 아니다. 실제 값은 airtable_get_records 로 그 ID 들을 조회할 것.
    큰 테이블은 formula/max_records 로 범위를 좁힐 것.
    """
    table = _api().table(base_id, table_id)
    kw: dict = {}
    if fields:
        kw["fields"] = fields
    if formula:
        kw["formula"] = formula
    if max_records:
        kw["max_records"] = max_records
    if view:
        kw["view"] = view
    recs = table.all(**kw)
    return {
        "base_id": base_id, "table_id": table_id, "count": len(recs),
        "records": [{"id": r["id"], "fields": r["fields"]} for r in recs],
    }


def airtable_get_records(base_id: str, table_id: str, record_ids: list[str]) -> dict:
    """여러 레코드를 ID 로 한 번에 조회 — linked 필드(record ID 배열)의 실제 값을 가져올 때 핵심.

    예: airtable_list_records 가 detail 라인을 ['recA','recB',...] 로만 줄 때,
    이 도구로 그 ID 들의 실제 필드(금액·영역 등)를 한 번에 받는다 (INT-1570).
    """
    if not record_ids:
        return {"base_id": base_id, "table_id": table_id, "count": 0, "records": []}
    table = _api().table(base_id, table_id)
    out: list[dict] = []
    chunk = 50  # formula 길이 안전 한계
    for i in range(0, len(record_ids), chunk):
        ids = record_ids[i:i + chunk]
        formula = "OR(" + ",".join(f"RECORD_ID()='{rid}'" for rid in ids) + ")"
        for r in table.all(formula=formula):
            out.append({"id": r["id"], "fields": r["fields"]})
    return {"base_id": base_id, "table_id": table_id, "count": len(out), "records": out}


# ── 쓰기 ────────────────────────────────────────────────────────────────────────

def airtable_create_record(base_id: str, table_id: str, fields: dict) -> dict:
    """레코드 1건 생성. fields: {필드명: 값}. linked 필드는 ['rec...'] 형식."""
    r = _api().table(base_id, table_id).create(fields)
    return {"id": r["id"], "fields": r["fields"]}


def airtable_update_record(base_id: str, table_id: str, record_id: str, fields: dict) -> dict:
    """레코드 1건 수정 (지정 필드만 갱신)."""
    r = _api().table(base_id, table_id).update(record_id, fields)
    return {"id": r["id"], "fields": r["fields"]}


# ── 스키마 ──────────────────────────────────────────────────────────────────────

AIRTABLE_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "name": "airtable_list_bases",
        "description": "접근 가능한 Airtable base(앱) 목록을 조회한다. base_id 를 모를 때 먼저 호출.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "airtable_list_tables",
        "description": "Airtable base 의 테이블·필드 구조를 조회한다. linked 필드의 연결 대상"
                       "(linked_table_id)을 확인해 어느 테이블을 조회할지 판단할 때 사용.",
        "parameters": {
            "type": "object",
            "properties": {"base_id": {"type": "string", "description": "Airtable base ID (app...)"}},
            "required": ["base_id"],
        },
    },
    {
        "type": "function",
        "name": "airtable_list_records",
        "description": "Airtable 테이블의 레코드를 조회한다. ⚠️ linked/lookup 필드는 연결 레코드의 "
                       "record ID 배열(['rec...'])만 반환하며 실제 값이 아니다 — 실제 값은 "
                       "airtable_get_records 로 그 ID 들을 조회할 것. 큰 테이블은 formula/max_records 로 좁힐 것.",
        "parameters": {
            "type": "object",
            "properties": {
                "base_id": {"type": "string", "description": "base ID (app...)"},
                "table_id": {"type": "string", "description": "table ID (tbl...) 또는 테이블 이름"},
                "fields": {"type": "array", "items": {"type": "string"},
                           "description": "가져올 필드명 목록 (생략 시 전체)"},
                "formula": {"type": "string", "description": "Airtable filterByFormula 식 (선택)"},
                "max_records": {"type": "integer", "description": "최대 레코드 수 (선택)"},
                "view": {"type": "string", "description": "뷰 이름 (선택)"},
            },
            "required": ["base_id", "table_id"],
        },
    },
    {
        "type": "function",
        "name": "airtable_get_records",
        "description": "여러 레코드를 record ID 목록으로 한 번에 조회한다. linked 필드가 ID 배열만 "
                       "줄 때 그 실제 값(금액·날짜·텍스트 등)을 가져오는 핵심 도구. "
                       "예: detail 라인 ID 69개 → 실제 정산 데이터.",
        "parameters": {
            "type": "object",
            "properties": {
                "base_id": {"type": "string", "description": "base ID (app...)"},
                "table_id": {"type": "string", "description": "table ID (tbl...) — ID 들이 속한 테이블"},
                "record_ids": {"type": "array", "items": {"type": "string"},
                               "description": "조회할 record ID 목록 (['rec...'])"},
            },
            "required": ["base_id", "table_id", "record_ids"],
        },
    },
    {
        "type": "function",
        "name": "airtable_create_record",
        "description": "Airtable 레코드 1건 생성. 반드시 사용자 확인 후 실행.",
        "parameters": {
            "type": "object",
            "properties": {
                "base_id": {"type": "string"},
                "table_id": {"type": "string"},
                "fields": {"type": "object", "description": "{필드명: 값}. linked 필드는 ['rec...'] 형식.",
                           "additionalProperties": True},
            },
            "required": ["base_id", "table_id", "fields"],
        },
    },
    {
        "type": "function",
        "name": "airtable_update_record",
        "description": "Airtable 레코드 1건 수정 (지정 필드만 갱신). 반드시 사용자 확인 후 실행.",
        "parameters": {
            "type": "object",
            "properties": {
                "base_id": {"type": "string"},
                "table_id": {"type": "string"},
                "record_id": {"type": "string", "description": "수정할 record ID (rec...)"},
                "fields": {"type": "object", "description": "{필드명: 값}", "additionalProperties": True},
            },
            "required": ["base_id", "table_id", "record_id", "fields"],
        },
    },
]

AIRTABLE_TOOL_FUNCTIONS: dict[str, Any] = {
    "airtable_list_bases": airtable_list_bases,
    "airtable_list_tables": airtable_list_tables,
    "airtable_list_records": airtable_list_records,
    "airtable_get_records": airtable_get_records,
    "airtable_create_record": airtable_create_record,
    "airtable_update_record": airtable_update_record,
}
