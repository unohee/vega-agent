# Created: 2026-05-18 / Refactored: 2026-05-20
# Purpose: VEGA tool registry — TOOL_SCHEMAS + dispatch_tool (implementations in sub-modules)
# Dependencies: pipeline.tools_google, tools_web, tools_code, tools_office

from __future__ import annotations

import contextvars
import json
from pathlib import Path
from typing import Any

# /plan mode contextvar — server.py injects the session's plan_mode just before entering _run_gpt_task.
# dispatch_tool reads this value to block write/exec tools.
_PLAN_MODE_VAR: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "vega_plan_mode", default=False
)

# CE mode contextvar — True for remote client sessions.
# dispatch_tool double-blocks tools outside the CE allowlist.
_CE_MODE_VAR: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "vega_ce_mode", default=False
)


def set_plan_mode(active: bool) -> None:
    """Toggle plan mode for the current execution context (called by server.py at round start)."""
    _PLAN_MODE_VAR.set(bool(active))


def set_ce_mode(active: bool) -> None:
    """Toggle CE mode for the current execution context (called by server.py at round start)."""
    _CE_MODE_VAR.set(bool(active))


# CE (Community Edition) mode — allowlist of tools permitted when a remote client connects.
# Local system access (host_exec, file_*, icloud_*, iMessage etc.) is excluded.
# SaaS read/write (linear, discord, web) is allowed — credentials are delegated from the server machine.
_CE_ALLOWED_TOOLS: frozenset[str] = frozenset({
    # Web
    "web_search", "web_fetch",
    # Gmail (read + write)
    "gmail_search", "gmail_read", "gmail_send", "gmail_draft",
    "gmail_modify_labels", "gmail_batch_modify",
    "gmail_list_attachments", "gmail_download_attachment",
    # Calendar
    "calendar_list_events", "calendar_create_event",
    "calendar_update_event", "calendar_delete_event",
    # Drive (read)
    "drive_search", "drive_read",
    # Google Slides / Docs
    "slides_create", "slides_append_slide",
    "docs_create", "docs_append",
    # Linear
    "linear_list_issues", "linear_get_issue", "linear_search_issues",
    "linear_create_issue", "linear_update_issue", "linear_add_comment",
    # Discord
    "discord_notify",
    # Memory (read)
    "memory_search", "memory_get",
    # Session management (read)
    "session_list",
    # ask_user_question always permitted
    "ask_user_question",
})


def get_schemas_for_mode(
    base: list[dict],
    ce_mode: bool = False,
) -> list[dict]:
    """현재 모드에 맞는 도구 스키마를 반환.

    CE 게이트는 당분간 비활성 — 개인용이라 모든 진입점(데스크톱 앱·채널 봇)에서
    로컬 파일/exec 도구를 포함한 전체 도구를 노출한다. ce_mode 인자는 호출부
    호환을 위해 유지하나 더 이상 스키마를 필터링하지 않는다.
    (원격 노출 시 재활성화하려면 아래 _CE_ALLOWED_TOOLS 화이트리스트 필터를 되살릴 것.)"""
    return base


# Tools blocked in plan mode. Read/search/lookup/memory_read are excluded.
# ask_user_question and exit_plan_mode are required even in plan mode — allowed.
_PLAN_BLOCKED_TOOLS: frozenset[str] = frozenset({
    # Exec/code (including sandbox_)
    "host_exec", "bash_exec", "python_exec", "sandbox_exec",
    # File write
    "file_edit",
    # Gmail write/send
    "gmail_send", "gmail_draft", "gmail_modify_labels", "gmail_batch_modify",
    # Calendar write
    "calendar_create_event", "calendar_update_event", "calendar_delete_event",
    # iCloud write
    "icloud_move", "icloud_rename", "icloud_mkdir",
    # Skill/widget
    "skill_save", "skill_delete", "widget_save", "widget_delete",
    # MCP management
    "mcp_add_server", "mcp_remove_server", "mcp_reload",
    # Linear write
    "linear_create_issue", "linear_update_issue", "linear_add_comment",
    # Memory write
    "memory_persona_update", "memory_event_add", "memory_entity_upsert",
    # Contact memo
    "contact_memo_update",
    # Session delete/cleanup
    "session_delete", "session_clean",
    # Discord notification (external send)
    "discord_notify",
    # KIS trading — blocked (read-only counterparts pass if they exist as separate functions)
    "kis_order_cash", "kis_order_cancel", "kis_order_modify",
})

# ── Tool function imports ─────────────────────────────────────────────────────

from pipeline.tools_google import (
    gmail_search, gmail_read, gmail_send, gmail_draft, gmail_modify_labels, gmail_batch_modify,
    gmail_list_attachments, gmail_download_attachment,
    calendar_list_events, calendar_create_event, calendar_update_event, calendar_delete_event,
    drive_search, drive_read, file_read, file_edit,
    icloud_list, icloud_move, icloud_rename, icloud_mkdir,
    slides_create, slides_append_slide,
    docs_create, docs_append,
)
from pipeline.tools_web import web_search, web_fetch
from pipeline.discord_bridge import discord_notify
# NOTE: vega-core 공개판은 office/browser/things/kis/imessage 개인 도구 모듈을 싣지 않는다.
# OFFICE_TOOL_SCHEMAS / OFFICE_TOOL_FUNCTIONS 는 빈 값으로 스텁한다.
# (개인 VEGA 에서 이식하려면 pipeline/tools_office.py 를 추가하고 이 두 줄을 import 로 교체)
OFFICE_TOOL_SCHEMAS: list[dict] = []
OFFICE_TOOL_FUNCTIONS: dict[str, Any] = {}

# ── Tool schemas (GPT tool-use format) ───────────────────────────────────────

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "name": "web_search",
        "description": "인터넷에서 정보를 검색한다. 최신 뉴스, 기술 문서, 일반 지식 조회에 사용.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색 키워드 또는 문장"},
                "max_results": {"type": "integer", "default": 5, "description": "최대 결과 수"},
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "web_fetch",
        "description": "특정 URL의 본문 텍스트를 가져온다. 검색 결과 링크를 더 자세히 읽을 때 사용.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "읽을 URL"},
            },
            "required": ["url"],
        },
    },
    {
        "type": "function",
        "name": "gmail_search",
        "description": "Gmail에서 이메일을 검색한다. Gmail 검색 문법 지원 (from:, subject:, is:unread, after:2026/01/01 등).",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail 검색 쿼리"},
                "max_results": {"type": "integer", "default": 10},
                "account": {"type": "string", "enum": ["personal", "intrect"], "default": "personal",
                            "description": "사용할 Gmail 계정 키 (personal 또는 intrect)"},
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "gmail_read",
        "description": "특정 Gmail 메시지의 본문을 읽는다. gmail_search로 얻은 id를 사용.",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "메시지 ID (gmail_search 결과의 id)"},
                "account": {"type": "string", "enum": ["personal", "intrect"], "default": "personal"},
            },
            "required": ["message_id"],
        },
    },
    {
        "type": "function",
        "name": "gmail_send",
        "description": "이메일을 전송한다. 반드시 사용자 확인 후 실행.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "수신자 이메일"},
                "subject": {"type": "string", "description": "제목"},
                "body": {"type": "string", "description": "본문"},
                "account": {"type": "string", "enum": ["personal", "intrect"], "default": "personal"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "type": "function",
        "name": "gmail_draft",
        "description": "이메일 임시저장(draft)을 생성한다. 발송하지 않고 Gmail 임시보관함에 저장 — 사용자가 검토 후 직접 보낸다.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "수신자 이메일"},
                "subject": {"type": "string", "description": "제목"},
                "body": {"type": "string", "description": "본문"},
                "account": {"type": "string", "enum": ["personal", "intrect"], "default": "personal"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "type": "function",
        "name": "gmail_modify_labels",
        "description": "메시지 1개의 라벨/상태를 변경한다. 읽음 처리=remove ['UNREAD'], 별표=add ['STARRED'], 보관(archive)=remove ['INBOX'], 휴지통=add ['TRASH']. 여러 메시지를 한꺼번에 처리할 땐 gmail_batch_modify를 쓸 것.",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "메시지 ID (gmail_search 결과의 id)"},
                "add": {"type": "array", "items": {"type": "string"}, "description": "추가할 라벨 ID 목록 (예: ['STARRED'])"},
                "remove": {"type": "array", "items": {"type": "string"}, "description": "제거할 라벨 ID 목록 (예: ['UNREAD'])"},
                "account": {"type": "string", "enum": ["personal", "intrect"], "default": "personal"},
            },
            "required": ["message_id"],
        },
    },
    {
        "type": "function",
        "name": "gmail_batch_modify",
        "description": (
            "여러 메시지를 한 번의 API 호출로 라벨 일괄 변경. "
            "메일 정리·보관·읽음처리·삭제 등 대량 작업 시 반드시 이 도구를 사용한다. "
            "gmail_modify_labels를 반복 호출하는 것보다 훨씬 빠르고 라운드를 소모하지 않는다. "
            "예: 뉴스레터 전체 보관 = remove=['INBOX'], 전체 읽음 = remove=['UNREAD']"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "처리할 메시지 ID 목록 (gmail_search 결과의 id 필드들)",
                },
                "add": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "추가할 라벨 ID 목록. 예: ['TRASH'] (삭제), ['STARRED'] (별표)",
                    "default": [],
                },
                "remove": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "제거할 라벨 ID 목록. 예: ['INBOX'] (보관), ['UNREAD'] (읽음 처리)",
                    "default": [],
                },
                "account": {"type": "string", "enum": ["personal", "intrect"], "default": "personal"},
            },
            "required": ["message_ids"],
        },
    },
    {
        "type": "function",
        "name": "gmail_list_attachments",
        "description": "Gmail 메시지의 첨부파일 목록을 반환한다. gmail_search로 얻은 id를 사용. 다운로드 전 먼저 호출해 attachment_id를 확인한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "메시지 ID (gmail_search 결과의 id)"},
                "account": {"type": "string", "enum": ["personal", "intrect"], "default": "personal"},
            },
            "required": ["message_id"],
        },
    },
    {
        "type": "function",
        "name": "gmail_download_attachment",
        "description": "Gmail 첨부파일을 로컬 파일로 저장한다. gmail_list_attachments로 attachment_id를 먼저 확인하고 사용한다. KB국민카드·삼성카드 등 보안 첨부파일(암호화 xlsx)도 저장 후 file_read로 비밀번호 지정 해제 가능.",
        "parameters": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "메시지 ID"},
                "attachment_id": {"type": "string", "description": "attachment_id (gmail_list_attachments 결과의 attachment_id)"},
                "save_path": {"type": "string", "description": "저장할 절대경로 (예: /Users/unohee/Downloads/kb_card_2026_05.xlsx)"},
                "account": {"type": "string", "enum": ["personal", "intrect"], "default": "personal"},
            },
            "required": ["message_id", "attachment_id", "save_path"],
        },
    },
    {
        "type": "function",
        "name": "calendar_list_events",
        "description": "Google 캘린더 일정을 조회한다. primary 외 구독 캘린더(수업, Family 등) 포함 전체 조회.",
        "parameters": {
            "type": "object",
            "properties": {
                "days_from_today": {"type": "integer", "default": 7, "description": "오늘부터 N일 조회"},
                "max_results": {"type": "integer", "default": 20},
                "account": {"type": "string", "enum": ["personal", "intrect"], "default": "personal"},
                "calendar_name": {"type": "string", "default": "", "description": "특정 캘린더 이름 필터. 빈 문자열이면 전체 조회."},
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "calendar_create_event",
        "description": "Google 캘린더에 새 일정을 추가한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "일정 제목"},
                "start_iso": {"type": "string", "description": "시작 시각 ISO 8601 (예: 2026-05-20T14:00:00+09:00)"},
                "end_iso": {"type": "string", "description": "종료 시각 ISO 8601"},
                "description": {"type": "string", "default": ""},
                "location": {"type": "string", "default": ""},
                "account": {"type": "string", "enum": ["personal", "intrect"], "default": "personal"},
            },
            "required": ["summary", "start_iso", "end_iso"],
        },
    },
    {
        "type": "function",
        "name": "calendar_update_event",
        "description": "Google 캘린더 일정을 수정한다. event_id는 calendar_list_events의 id 필드.",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "수정할 이벤트 ID"},
                "summary": {"type": "string", "description": "새 제목"},
                "start_iso": {"type": "string", "description": "새 시작 시각 ISO 8601"},
                "end_iso": {"type": "string", "description": "새 종료 시각 ISO 8601"},
                "description": {"type": "string", "description": "새 설명"},
                "location": {"type": "string", "description": "새 장소"},
                "account": {"type": "string", "enum": ["personal", "intrect"], "default": "personal"},
            },
            "required": ["event_id"],
        },
    },
    {
        "type": "function",
        "name": "calendar_delete_event",
        "description": "Google 캘린더 일정을 삭제한다. 반드시 사용자 확인 후 실행.",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "삭제할 이벤트 ID"},
                "account": {"type": "string", "enum": ["personal", "intrect"], "default": "personal"},
            },
            "required": ["event_id"],
        },
    },
    {
        "type": "function",
        "name": "drive_search",
        "description": "Google Drive에서 파일을 검색한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Drive 검색 쿼리 (예: name contains 'ArtifactNet')"},
                "max_results": {"type": "integer", "default": 10},
                "account": {"type": "string", "enum": ["personal", "intrect"], "default": "personal"},
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "drive_read",
        "description": "Google Drive 파일 본문을 읽는다. Docs/Sheets/Slides/PDF/xlsx 지원.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "파일 ID (drive_search 결과의 id)"},
                "account": {"type": "string", "enum": ["personal", "intrect"], "default": "personal"},
            },
            "required": ["file_id"],
        },
    },
    {
        "type": "function",
        "name": "file_read",
        "description": (
            "로컬 파일 읽기.\n"
            "- xlsx/xls/csv/tsv: 마크다운 테이블로 변환. sheet로 시트 지정.\n"
            "- db/sqlite/sqlite3: 스키마 + 샘플 데이터 반환. sheet로 특정 테이블 지정.\n"
            "- txt/md/json/py/yaml 등 텍스트 파일: 원문 반환.\n"
            "경로는 절대경로 또는 ~/... 형태. "
            "암호화된 엑셀은 password 인자로 복호화. "
            "결과가 '__needs_password__'로 시작하면 사용자에게 비밀번호를 물어 재호출."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "파일 경로 (예: ~/Downloads/data.xlsx, ~/data/vega.db)"},
                "sheet": {"type": "string", "description": "xlsx: 시트 이름 / sqlite: 테이블 이름 (생략 시 전체 요약)", "default": ""},
                "max_rows": {"type": "integer", "description": "최대 행 수 (기본 500)", "default": 500},
                "password": {"type": "string", "description": "암호화된 엑셀의 비밀번호 (필요 시)", "default": ""},
            },
            "required": ["path"],
        },
    },
    {
        "type": "function",
        "name": "file_edit",
        "description": (
            "로컬 xlsx/csv/tsv 파일 편집. 변경 전 .bak 자동 백업.\n"
            "operation:\n"
            "  set_cell   — 특정 셀 변경 (row, col 또는 col_name 필요)\n"
            "  append_row — 마지막 행에 추가 (values=[...] 필요)\n"
            "  update_row — 조건 행 수정 (where={열:값}, values={열:새값})\n"
            "  delete_row — 조건 행 삭제 (where={열:값})\n"
            "  add_sheet  — xlsx 전용 시트 추가 (sheet=이름)"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path":     {"type": "string", "description": "파일 경로"},
                "operation":{"type": "string", "enum": ["set_cell","append_row","update_row","delete_row","add_sheet"]},
                "sheet":    {"type": "string", "description": "시트 이름 (xlsx, 생략 시 활성 시트)", "default": ""},
                "row":      {"type": "integer", "description": "행 번호 (1-based, 1행=헤더)"},
                "col":      {"type": "integer", "description": "열 번호 (1-based)"},
                "col_name": {"type": "string",  "description": "열 이름 (col 대신 사용 가능)"},
                "value":    {"description": "set_cell 시 저장할 값"},
                "values":   {
                    "description": "append_row: [v1,v2,...] 배열 / update_row: {열이름:새값} 객체",
                    "anyOf": [
                        {"type": "array", "items": {}},
                        {"type": "object"},
                    ],
                },
                "where":    {"type": "object",  "description": "update_row/delete_row 조건 {열이름: 일치값}"},
            },
            "required": ["path", "operation"],
        },
    },
    {
        "type": "function",
        "name": "rule_save",
        "description": (
            "data/agents/RULES.md에 행동 규칙을 추가하거나 수정한다. "
            "사용자가 '앞으로 이렇게 해줘', '이 방식으로 항상 처리해', '기억해줘 — 규칙으로' 같은 "
            "지속적 행동 변경을 요구할 때 호출한다. "
            "rule_id는 고유 식별자(소문자-하이픈). "
            "rule_text는 명령형 단문으로 작성(예: '답변은 반말로 한다'). "
            "overwrite=True면 동일 rule_id 규칙을 교체한다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "rule_id":   {"type": "string", "description": "규칙 식별자 (소문자-하이픈, 예: reply-tone)"},
                "section":   {"type": "string", "description": "규칙 카테고리 섹션 (예: 응답 스타일, 도구 사용, 도메인 규칙). 새 섹션이면 자동 생성."},
                "rule_text": {"type": "string", "description": "규칙 본문 — 명령형 단문 또는 여러 줄. 에이전트가 따라야 할 행동 지침."},
                "overwrite": {"type": "boolean", "description": "동일 rule_id가 있을 때 덮어쓰기", "default": False},
            },
            "required": ["rule_id", "section", "rule_text"],
        },
    },
    {
        "type": "function",
        "name": "rule_delete",
        "description": "RULES.md에서 rule_id로 규칙을 삭제한다. 사용자가 '그 규칙 취소해', '삭제해줘'라고 할 때 호출.",
        "parameters": {
            "type": "object",
            "properties": {
                "rule_id": {"type": "string", "description": "삭제할 규칙 식별자"},
            },
            "required": ["rule_id"],
        },
    },
    {
        "type": "function",
        "name": "rule_list",
        "description": "현재 RULES.md에 저장된 모든 규칙 목록을 반환한다. 사용자가 '저장된 규칙 보여줘', '어떤 규칙이 있어?' 할 때 호출.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "skill_save",
        "description": (
            "새 슬래시 커맨드(skill)를 data/commands/{name}.md 로 저장한다. "
            "/skill 마법사에서 사용자와 합의한 내용을 이 도구로 저장. "
            "name은 소문자/숫자/하이픈만(예: deploy, daily-report). "
            "body는 VEGA가 호출 시 따를 마크다운 지시문 — 단계와 사용할 도구를 명확히."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name":          {"type": "string", "description": "커맨드 이름 (/ 없이, 소문자-하이픈)"},
                "description":   {"type": "string", "description": "한 줄 설명 (자동완성·목록에 표시)"},
                "body":          {"type": "string", "description": "본문 마크다운 — 실행 단계 지시문"},
                "argument_hint": {"type": "string", "description": "인자 힌트 (예: '[--no-push]'). 없으면 빈 문자열", "default": ""},
                "overwrite":     {"type": "boolean", "description": "기존 동명 커맨드 덮어쓰기", "default": False},
            },
            "required": ["name", "description", "body"],
        },
    },
    {
        "type": "function",
        "name": "skill_delete",
        "description": "커스텀 슬래시 커맨드 삭제 (data/commands/{name}.md → 휴지통). 내장 커맨드는 삭제 불가.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "삭제할 커맨드 이름 (/ 없이)"},
            },
            "required": ["name"],
        },
    },
    {
        "type": "function",
        "name": "widget_save",
        "description": (
            "Agent View(홈 화면)에 커스텀 위젯을 추가/수정한다. /widget 마법사가 사용. "
            "type: stat(큰 숫자+라벨) | list(항목 목록) | text(텍스트) | action(폼+버튼으로 슬래시 실행). "
            "source는 화이트리스트만: clock, session_count, recent_command, git_status, "
            "mail_count, today_brief, project_count, skill_count. "
            "source 없는 text 위젯은 text 필드에 고정 내용. span은 그리드 칸수(1~3). "
            "action 위젯은 (1) 기존 슬래시 커맨드 이름(skill) + (2) 사용자 입력 폼(inputs) 필수. "
            "사용자가 폼 채워 Run 버튼 누르면 슬래시 본문에 인자 치환 후 LLM 격리 실행. "
            "슬래시는 사전에 skill_save로 등록되어 있어야 함. 슬래시 본문에서 입력값 참조는 ${name} 또는 $name 형식."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "widget_id":  {"type": "string", "description": "위젯 id (소문자-하이픈, 고유)"},
                "title":      {"type": "string", "description": "위젯 제목"},
                "type":       {"type": "string", "enum": ["stat", "list", "text", "action"]},
                "source":     {"type": "string", "description": "데이터소스 키 (화이트리스트). 정적 text/action이면 빈 문자열", "default": ""},
                "icon":       {"type": "string", "description": "이모지 아이콘", "default": "🧩"},
                "span":       {"type": "integer", "description": "그리드 칸수 1~3", "default": 1},
                "text":       {"type": "string", "description": "source 없는 text 위젯의 고정 내용", "default": ""},
                "skill":      {"type": "string", "description": "action 위젯이 호출할 슬래시 커맨드 이름 (e.g. 'youtube-meta')", "default": ""},
                "inputs": {
                    "type": "array",
                    "description": "action 위젯 입력 폼 필드. 1~8개. body에서 ${name}으로 치환.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name":        {"type": "string", "description": "변수명 [a-z][a-z0-9_]*"},
                            "label":       {"type": "string", "description": "UI 라벨"},
                            "type":        {"type": "string", "enum": ["text", "url", "number", "textarea"], "default": "text"},
                            "placeholder": {"type": "string", "default": ""},
                            "required":    {"type": "boolean", "default": True},
                        },
                        "required": ["name"],
                    },
                    "default": [],
                },
                "overwrite":  {"type": "boolean", "description": "기존 동일 id 수정", "default": False},
            },
            "required": ["widget_id", "title", "type"],
        },
    },
    {
        "type": "function",
        "name": "widget_delete",
        "description": "Agent View 커스텀 위젯 삭제 (id로).",
        "parameters": {
            "type": "object",
            "properties": {
                "widget_id": {"type": "string", "description": "삭제할 위젯 id"},
            },
            "required": ["widget_id"],
        },
    },
    {
        "type": "function",
        "name": "icloud_list",
        "description": "iCloud Drive 디렉터리 목록 조회. 파일명·크기·타입 반환. path 생략 시 루트, '~/iCloud/Documents' 형식으로 하위 폴더 지정 가능.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "iCloud Drive 내 경로. 빈 문자열이면 루트."},
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "icloud_move",
        "description": "iCloud Drive 파일/폴더 이동. dst가 폴더면 그 안으로 이동, 파일명이면 그 이름으로 저장.",
        "parameters": {
            "type": "object",
            "properties": {
                "src": {"type": "string", "description": "이동할 원본 경로 (iCloud Drive 상대 또는 절대)"},
                "dst": {"type": "string", "description": "이동할 대상 경로 (폴더 또는 새 파일 경로)"},
            },
            "required": ["src", "dst"],
        },
    },
    {
        "type": "function",
        "name": "icloud_rename",
        "description": "iCloud Drive 파일/폴더 이름 변경. 같은 폴더 내에서 이름만 바꾼다.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "이름 바꿀 파일/폴더 경로"},
                "new_name": {"type": "string", "description": "새 이름 (경로 구분자 포함 불가)"},
            },
            "required": ["path", "new_name"],
        },
    },
    {
        "type": "function",
        "name": "icloud_mkdir",
        "description": "iCloud Drive에 새 폴더 생성. 중간 경로도 자동 생성.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "생성할 폴더 경로"},
            },
            "required": ["path"],
        },
    },
    {
        "type": "function",
        "name": "discord_notify",
        "description": "Discord 웹훅으로 알림을 발송한다. 도구 에러·heartbeat 결과·작업 완료 보고에 사용.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "알림 본문"},
                "title": {"type": "string", "default": "VEGA", "description": "알림 제목"},
                "level": {"type": "string", "enum": ["info", "warn", "error", "ok"], "default": "info", "description": "info=파랑, warn=노랑, error=빨강, ok=초록"},
            },
            "required": ["message"],
        },
    },
    {
        "type": "function",
        "name": "contact_memo_update",
        "description": "연락처에 관계 메모를 기록한다. 이 사람이 누구인지, 어떤 맥락에서 아는지 등 에이전트가 기억해야 할 내용.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "연락처 이름 (일부 포함 가능)"},
                "memo": {"type": "string", "description": "기록할 관계 메모"},
            },
            "required": ["name", "memo"],
        },
    },
    # ── Google Slides ────────────────────────────────────────────────────────
    {
        "type": "function",
        "name": "slides_create",
        "description": (
            "Google Slides 프레젠테이션을 새로 만들고 슬라이드를 추가합니다. "
            "완료 후 편집 URL을 반환하므로 사용자에게 바로 공유 가능. "
            "슬라이드 layout: TITLE_AND_BODY(기본), TITLE_ONLY, BLANK, SECTION_HEADER."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "프레젠테이션 제목"},
                "slides": {
                    "type": "array",
                    "description": (
                        "슬라이드 리스트. 각 항목: "
                        "{title: 슬라이드 제목, body: 본문(줄바꿈 \\n), "
                        "layout: 레이아웃(생략 시 TITLE_AND_BODY), notes: 발표자 노트}"
                    ),
                    "items": {"type": "object"},
                },
                "account": {"type": "string", "default": "personal"},
            },
            "required": ["title", "slides"],
        },
    },
    {
        "type": "function",
        "name": "slides_append_slide",
        "description": "기존 Google Slides 프레젠테이션에 슬라이드를 추가합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "presentation_id": {"type": "string", "description": "프레젠테이션 ID (URL의 /d/ID/ 부분)"},
                "slides": {
                    "type": "array",
                    "description": "추가할 슬라이드 리스트 (slides_create와 동일 형식)",
                    "items": {"type": "object"},
                },
                "account": {"type": "string", "default": "personal"},
            },
            "required": ["presentation_id", "slides"],
        },
    },
    # ── Google Docs ───────────────────────────────────────────────────────────
    {
        "type": "function",
        "name": "docs_create",
        "description": (
            "Google Docs 문서를 새로 만들고 내용을 삽입합니다. "
            "완료 후 편집 URL 반환. "
            "블록 타입: heading(level 1~6), paragraph, bullet, table(rows 2차원 배열), pagebreak."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "문서 제목"},
                "content": {
                    "type": "array",
                    "description": (
                        "내용 블록 리스트. 예: "
                        "[{type:'heading',text:'제목',level:1}, "
                        "{type:'paragraph',text:'본문'}, "
                        "{type:'bullet',text:'항목'}, "
                        "{type:'table',rows:[['열1','열2'],['값1','값2']]}]"
                    ),
                    "items": {"type": "object"},
                },
                "account": {"type": "string", "default": "personal"},
            },
            "required": ["title", "content"],
        },
    },
    {
        "type": "function",
        "name": "docs_append",
        "description": "기존 Google Docs 문서 끝에 내용을 추가합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "문서 ID (URL의 /d/ID/ 부분)"},
                "content": {
                    "type": "array",
                    "description": "추가할 내용 블록 (docs_create와 동일 형식)",
                    "items": {"type": "object"},
                },
                "account": {"type": "string", "default": "personal"},
            },
            "required": ["document_id", "content"],
        },
    },
    # ── Linear ───────────────────────────────────────────────────────────────
    {
        "type": "function",
        "name": "linear_list_issues",
        "description": (
            "Linear 이슈 목록 조회. 팀·상태로 필터링. "
            "team_key 예: INT(Intrect), STO(STONKS), KT(KYTE), AUD(audio), RES(Research)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "team_key": {"type": "string", "description": "팀 키 또는 이름 (선택). 없으면 전체 팀."},
                "states": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "상태 필터 (예: ['In Progress', 'Todo', 'Backlog', 'Done', 'Canceled']). 없으면 전체.",
                },
                "limit": {"type": "integer", "description": "최대 결과 수 (기본 30)", "default": 30},
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "linear_get_issue",
        "description": "Linear 이슈 상세 조회 (설명·코멘트 포함). issue_id는 UUID 또는 identifier(예: INT-42).",
        "parameters": {
            "type": "object",
            "properties": {
                "issue_id": {"type": "string", "description": "이슈 UUID 또는 identifier (예: INT-42)"},
            },
            "required": ["issue_id"],
        },
    },
    {
        "type": "function",
        "name": "linear_search_issues",
        "description": "Linear 이슈 전문 검색.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색어"},
                "limit": {"type": "integer", "description": "최대 결과 수 (기본 10)", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "linear_create_issue",
        "description": (
            "Linear 이슈 생성. "
            "priority: 0=없음, 1=긴급, 2=높음, 3=보통, 4=낮음."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "이슈 제목"},
                "team_key": {"type": "string", "description": "팀 키 (예: INT, STO, KT)"},
                "description": {"type": "string", "description": "이슈 설명 (마크다운)", "default": ""},
                "priority": {"type": "integer", "description": "우선순위 (기본 3=보통)", "default": 3},
                "state_name": {"type": "string", "description": "초기 상태 (기본 Backlog)", "default": "Backlog"},
                "label_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "라벨 목록 (선택)",
                    "default": [],
                },
            },
            "required": ["title", "team_key"],
        },
    },
    {
        "type": "function",
        "name": "linear_update_issue",
        "description": "Linear 이슈 제목·설명·상태·우선순위·라벨 업데이트. 변경할 필드만 전달.",
        "parameters": {
            "type": "object",
            "properties": {
                "issue_id": {"type": "string", "description": "이슈 UUID 또는 identifier (예: INT-42)"},
                "title": {"type": "string", "description": "새 제목"},
                "description": {"type": "string", "description": "새 설명"},
                "state_name": {"type": "string", "description": "새 상태 (예: In Progress, Done, Canceled)"},
                "priority": {"type": "integer", "description": "새 우선순위 (0–4)"},
                "label_names": {"type": "array", "items": {"type": "string"}, "description": "새 라벨 목록"},
            },
            "required": ["issue_id"],
        },
    },
    {
        "type": "function",
        "name": "linear_add_comment",
        "description": "Linear 이슈에 코멘트를 추가한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "issue_id": {"type": "string", "description": "이슈 UUID 또는 identifier (예: INT-42)"},
                "body": {"type": "string", "description": "코멘트 내용 (마크다운)"},
            },
            "required": ["issue_id", "body"],
        },
    },
]

# ── Memory write schemas ─────────────────────────────────────────────────────

MEMORY_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "name": "memory_persona_update",
        "description": (
            "VEGA 자신의 페르소나 섹션을 업데이트한다. "
            "대화 중 사용자에 대해 새로운 사실을 파악했거나, 기존 내용이 틀렸음을 확인했을 때 호출. "
            "버전 히스토리가 유지되므로 덮어쓰기가 아닌 새 버전 삽입."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "section_key": {
                    "type": "string",
                    "description": "섹션 키 (예: work_context, personal_context, top_of_mind, brief_history, long_term, other_instructions)",
                },
                "content": {"type": "string", "description": "새 섹션 내용 (전체 교체)"},
                "notes": {"type": "string", "description": "변경 이유 또는 출처 메모 (선택)", "default": ""},
            },
            "required": ["section_key", "content"],
        },
    },
    {
        "type": "function",
        "name": "memory_event_add",
        "description": (
            "사용자의 라이프 타임라인에 새 이벤트를 추가한다. "
            "오늘 있었던 중요한 일, 결정, 대화에서 드러난 사건 등을 기록할 때 사용."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event_date": {"type": "string", "description": "날짜 (YYYY-MM-DD)"},
                "title": {"type": "string", "description": "한 줄 제목"},
                "body": {"type": "string", "description": "상세 내용"},
                "tags": {"type": "string", "description": "쉼표 구분 태그 (예: trading,mental_health,business)", "default": ""},
            },
            "required": ["event_date", "title", "body"],
        },
    },
    {
        "type": "function",
        "name": "memory_entity_upsert",
        "description": (
            "인물·조직·프로젝트 등 엔티티 정보를 추가하거나 갱신한다. "
            "대화에서 새 인물이 등장하거나 기존 인물의 정보가 업데이트됐을 때 사용."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "엔티티 이름"},
                "kind": {
                    "type": "string",
                    "enum": ["person", "org", "project", "topic"],
                    "description": "엔티티 종류",
                },
                "notes": {"type": "string", "description": "설명 또는 관계 메모", "default": ""},
                "aliases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "동의어·별명 목록 (선택)",
                    "default": [],
                },
            },
            "required": ["name", "kind"],
        },
    },
]

# ── Session management tools ─────────────────────────────────────────────────

SESSION_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "name": "session_list",
        "description": (
            "VEGA 채팅 세션 목록을 조회한다. "
            "세션 이름·메시지 수·마지막 활동 시각이 포함된다. "
            "세션을 정리·삭제하기 전에 먼저 호출해 목록을 확인한다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "최대 반환 수 (기본 30)", "default": 30},
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "session_delete",
        "description": (
            "특정 세션을 삭제한다. 대화 메시지도 함께 영구 삭제된다. "
            "삭제 전 반드시 session_list로 uuid를 확인할 것. "
            "현재 진행 중인 세션은 삭제하지 않는다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "session_uuid": {"type": "string", "description": "삭제할 세션 UUID"},
            },
            "required": ["session_uuid"],
        },
    },
    {
        "type": "function",
        "name": "session_clean",
        "description": (
            "불필요한 세션을 일괄 정리한다. "
            "메시지가 거의 없는 빈 세션, 오래되고 짧은 세션, trivial 1회성 조회 세션을 자동으로 삭제한다. "
            "dry_run=true로 먼저 삭제 예정 목록을 확인할 수 있다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keep_min_messages": {
                    "type": "integer",
                    "description": "이 수 미만 메시지 세션은 삭제 (기본 2)",
                    "default": 2,
                },
                "max_age_days": {
                    "type": "integer",
                    "description": "이 일수 초과 & 짧은 세션 삭제. null이면 나이 기준 없음 (기본 90)",
                    "default": 90,
                },
                "prune_trivial": {
                    "type": "boolean",
                    "description": "true이면 주가조회·날씨 등 trivial 1회성 세션도 삭제 (기본 false)",
                    "default": False,
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "true이면 실제 삭제 없이 대상만 반환 (기본 false)",
                    "default": False,
                },
            },
            "required": [],
        },
    },
]

# ── Schema merge ─────────────────────────────────────────────────────────────

from pipeline.tools_code import CODE_TOOL_SCHEMAS, CODE_TOOL_FUNCTIONS
from pipeline.vega_query import persona_upsert, event_add, entity_upsert

TOOL_SCHEMAS.extend(MEMORY_TOOL_SCHEMAS)
TOOL_SCHEMAS.extend(SESSION_TOOL_SCHEMAS)
TOOL_SCHEMAS.extend(CODE_TOOL_SCHEMAS)
TOOL_SCHEMAS.extend(OFFICE_TOOL_SCHEMAS)

# vega-core: 네이티브 linear_* 도구는 pipeline.linear_client(개인 VEGA 전용, 여기 없음)에
# 의존한다. 모듈이 없으면 호출 시 무조건 실패하고 self_improve 가 폭주하므로,
# 모듈을 import 할 수 없으면 linear_* 스키마를 LLM 에 노출하지 않는다.
# (Linear 가 필요하면 LINEAR_API_KEY 를 설정 → MCP linear__* 서버로 자동 등록되어 동작.)
try:
    import importlib as _importlib
    _importlib.import_module("pipeline.linear_client")
    _LINEAR_NATIVE_OK = True
except Exception:
    _LINEAR_NATIVE_OK = False

if not _LINEAR_NATIVE_OK:
    TOOL_SCHEMAS[:] = [
        s for s in TOOL_SCHEMAS
        if not str(s.get("name", "")).startswith("linear_")
    ]

# UX/conversation flow control tools — results are intercepted by the server SSE handler and converted to UI widgets/mode toggles
TOOL_SCHEMAS.extend([
    {
        "type": "function",
        "name": "ask_user_question",
        "description": (
            "사용자에게 선택지·확인·분기·허가·선호를 묻는다. plain text로 묻지 말고 이 도구를 사용해라. "
            "판정 기준: '사용자가 답하지 않으면 다음 행동을 결정할 수 없다' → 이 도구를 호출. "
            "예: 접근 방식 A/B 선택, 대상 파일 지정, 진행 허가, 범위 결정, 도구/라이브러리 선택, 모호 해소. "
            "questions는 1–4개, 각 question의 options는 2–4개. 첫 옵션을 추천안으로 두고 label에 '(Recommended)' 표기. "
            "옵션이 상호배타적이지 않으면 multiSelect=true. 'Other' 옵션은 추가하지 마라 — 런타임이 자동 제공한다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string", "description": "완성된 의문문 (물음표로 끝남)"},
                            "header": {"type": "string", "description": "12자 이하 짧은 라벨/칩"},
                            "multiSelect": {"type": "boolean", "default": False},
                            "options": {
                                "type": "array",
                                "minItems": 2,
                                "maxItems": 4,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string", "description": "선택지 표시 텍스트 (1-5단어)"},
                                        "description": {"type": "string", "description": "선택지 의미/결과 설명"},
                                    },
                                    "required": ["label", "description"],
                                },
                            },
                        },
                        "required": ["question", "header", "options"],
                    },
                },
            },
            "required": ["questions"],
        },
    },
    {
        "type": "function",
        "name": "exit_plan_mode",
        "description": (
            "Plan 모드 종료 시 사용자에게 계획 승인을 요청한다. /plan 모드에서만 사용. "
            "계획을 마크다운으로 잘 정리해서 plan 인자에 담아 호출하면 사용자가 승인/거절을 선택할 수 있다. "
            "승인되면 plan 모드가 해제되고 다음 응답부터 실행 도구가 다시 활성화된다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "plan": {
                    "type": "string",
                    "description": "사용자에게 보여줄 계획서 (마크다운). 단계별·검증 기준 포함 권장.",
                },
            },
            "required": ["plan"],
        },
    },
])

# MCP management tools (called when user says "add X MCP" in natural language)
TOOL_SCHEMAS.extend([
    {
        "type": "function",
        "name": "mcp_list_servers",
        "description": "현재 등록된 MCP 서버 목록과 각 서버의 도구 개수를 조회한다.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "mcp_add_server",
        "description": (
            "새 MCP 서버를 등록한다. 사용자가 'X MCP 추가해줘'라고 말하면 호출. "
            "command_line에 `npx -y @modelcontextprotocol/server-filesystem ~/dev` 같은 한 줄 명령을 전달하거나, "
            "원격 서버면 url을 전달. 이름은 자동 추출되지만 name으로 명시 가능. "
            "저장 후 도구를 활성화하려면 mcp_reload를 별도로 호출해야 한다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command_line": {"type": "string", "description": "stdio MCP의 실행 명령 한 줄 (npx/uvx/python 등)"},
                "url": {"type": "string", "description": "원격 SSE/HTTP MCP의 URL"},
                "name": {"type": "string", "description": "서버 이름 (생략 시 자동 추출)"},
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "mcp_remove_server",
        "description": "등록된 MCP 서버를 mcp.json에서 제거한다. 핫리로드(mcp_reload)로 적용.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "제거할 서버 이름"}},
            "required": ["name"],
        },
    },
    {
        "type": "function",
        "name": "mcp_reload",
        "description": (
            "mcp.json 변경을 런타임에 반영해 새 MCP 도구를 활성화한다. "
            "mcp_add_server / mcp_remove_server 호출 후 반드시 이 도구로 마무리."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
])

# ── Image generation tool ────────────────────────────────────────────────────

TOOL_SCHEMAS.extend([
    {
        "type": "function",
        "name": "image_generate",
        "description": (
            "이미지를 생성하거나 편집한다. OpenRouter를 통해 Gemini/GPT 이미지 모델 호출.\n"
            "- image_path 없음: 텍스트 프롬프트로 새 이미지 생성\n"
            "- image_path 있음: 기존 이미지를 프롬프트 지시에 따라 편집 "
            "(예: 텍스트 제거, 배경 교체, 스타일 변경 등)\n"
            "사용자가 '이미지 만들어줘', '그림 그려줘', '이 이미지에서 X 지워줘', "
            "'배경 바꿔줘' 등을 요청할 때 사용."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "생성/편집 지시 프롬프트 (영어 권장, 상세할수록 좋음)",
                },
                "image_path": {
                    "type": "string",
                    "description": (
                        "편집할 원본 이미지 파일 경로 (절대경로 또는 ~/로 시작). "
                        "사용자가 이미지를 첨부했다면 data/uploads/ 아래 경로를 사용. "
                        "생략하면 새 이미지 생성 모드."
                    ),
                },
                "model": {
                    "type": "string",
                    "description": "사용할 모델. 편집 모드 기본값: openai/gpt-5-image-mini, 생성 모드 기본값: google/gemini-2.5-flash-image",
                    "enum": [
                        "google/gemini-2.5-flash-image",
                        "google/gemini-3.1-flash-image-preview",
                        "openai/gpt-5-image-mini",
                        "openai/gpt-5-image",
                        "bytedance-seed/seedream-4.5",
                    ],
                },
            },
            "required": ["prompt"],
        },
    },
])


def image_generate(
    prompt: str,
    image_path: str = "",
    model: str = "",
) -> dict:
    """Call OpenRouter image generation/editing model → save PNG to data/charts/.
    Edit mode if image_path provided (default gpt-5-image-mini), else generate mode (default gemini-2.5-flash-image).
    Returns: {"__type": "image", "path": str} | {"error": str}
    """
    import base64
    import os
    import urllib.request
    import uuid
    from pathlib import Path

    api_key = os.getenv("OPENROUTER_API", "")
    if not api_key:
        return {"error": "OPENROUTER_API environment variable not set"}

    edit_mode = bool(image_path)
    if not model:
        model = "openai/gpt-5-image-mini" if edit_mode else "google/gemini-2.5-flash-image"

    # Build message content
    pad_top = pad_left = 0  # for crop restoration
    orig_w = orig_h = 0
    if edit_mode:
        src = Path(image_path).expanduser()
        if not src.exists():
            return {"error": f"file not found: {image_path}"}
        try:
            import io as _io
            from PIL import Image as _PIL
            src_img = _PIL.open(src)
            orig_w, orig_h = src_img.size
            # Place centered on square canvas — so model processes without aspect ratio distortion
            sq = max(orig_w, orig_h)
            canvas = _PIL.new("RGB", (sq, sq), (0, 0, 0))
            pad_left = (sq - orig_w) // 2
            pad_top  = (sq - orig_h) // 2
            canvas.paste(src_img.convert("RGB"), (pad_left, pad_top))
            buf = _io.BytesIO()
            canvas.save(buf, format="PNG")
            raw = buf.getvalue()
            mt = "image/png"
        except Exception:
            # No PIL — use raw bytes as-is
            raw = src.read_bytes()
            suffix = src.suffix.lower()
            mt = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                  ".webp": "image/webp", ".gif": "image/gif"}.get(suffix, "image/png")
        b64_in = base64.b64encode(raw).decode()
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mt};base64,{b64_in}"}},
        ]
    else:
        content = prompt

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "modalities": ["image"],
        "stream": False,
    }).encode()

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/unohee/VEGA",
            "X-Title": "VEGA",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read().decode())
    except Exception as e:
        return {"error": f"OpenRouter call failed: {e}"}

    # Extract image from response — message.images array or image_url inside content
    msg = (resp.get("choices") or [{}])[0].get("message", {})
    images = msg.get("images") or []
    b64_data: str | None = None
    media_type = "image/png"

    for img in images:
        url_obj = img.get("image_url", {})
        url_str = url_obj.get("url", "") if isinstance(url_obj, dict) else str(url_obj)
        if url_str.startswith("data:"):
            header, b64_data = url_str.split(",", 1)
            media_type = header.split(";")[0].replace("data:", "") or "image/png"
            break

    # Inline image fallback inside content
    if not b64_data:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "image_url":
                    url_str = block.get("image_url", {}).get("url", "")
                    if url_str.startswith("data:"):
                        header, b64_data = url_str.split(",", 1)
                        media_type = header.split(";")[0].replace("data:", "") or "image/png"
                        break

    if not b64_data:
        text = msg.get("content") or ""
        if isinstance(text, list):
            text = " ".join(b.get("text", "") for b in text if isinstance(b, dict))
        return {"error": f"No image data. Model response: {str(text)[:300]}"}

    ext = "png" if "png" in media_type else "jpg" if "jp" in media_type else "webp"
    from pipeline.data_paths import charts_dir
    out_path = charts_dir() / f"img_{uuid.uuid4().hex[:8]}.{ext}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_bytes = base64.b64decode(b64_data)

    # Edit mode: restore original aspect ratio with precise crop based on padding position
    if edit_mode and orig_w and orig_h:
        try:
            import io as _io2
            from PIL import Image as _PILImage2
            out_img = _PILImage2.open(_io2.BytesIO(raw_bytes))
            out_sq = out_img.size[0]  # square output
            # Calculate scale relative to input square size
            in_sq = max(orig_w, orig_h)
            scale = out_sq / in_sq
            # Map padding position to output scale
            crop_left = round(pad_left * scale)
            crop_top  = round(pad_top  * scale)
            crop_right  = crop_left + round(orig_w * scale)
            crop_bottom = crop_top  + round(orig_h * scale)
            out_img = out_img.crop((crop_left, crop_top, crop_right, crop_bottom))
            # Resize to original resolution
            out_img = out_img.resize((orig_w, orig_h), _PILImage2.LANCZOS)
            buf = _io2.BytesIO()
            fmt = "PNG" if ext == "png" else "JPEG"
            out_img.save(buf, format=fmt, quality=95)
            raw_bytes = buf.getvalue()
        except Exception:
            pass  # No PIL or failure — save original bytes as-is

    out_path.write_bytes(raw_bytes)
    return {"__type": "image", "path": str(out_path)}


# MCP tools are dynamically added to TOOL_SCHEMAS in web/server.py lifespan at server startup

# ── Rule (RULES.md) management tools ─────────────────────────────────────────

_RULES_PATH = Path(__file__).parent.parent / "data" / "agents" / "RULES.md"


def _rules_load() -> dict[str, dict]:
    """Parse RULES.md → return {rule_id: {section, rule_text}}."""
    if not _RULES_PATH.exists():
        return {}
    import re
    rules: dict[str, dict] = {}
    current_section = "General"
    for line in _RULES_PATH.read_text(encoding="utf-8").splitlines():
        # Section heading
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            current_section = m.group(1).strip()
            continue
        # Rule line: `- [rule_id] rule_text`
        m = re.match(r"^-\s+\[([^\]]+)\]\s+(.+)$", line)
        if m:
            rules[m.group(1).strip()] = {
                "section": current_section,
                "rule_text": m.group(2).strip(),
            }
    return rules


def _rules_save_all(rules: dict[str, dict]) -> None:
    """Serialize rule dictionary to RULES.md."""
    # Group by section
    from collections import defaultdict
    sections: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for rid, info in rules.items():
        sections[info["section"]].append((rid, info["rule_text"]))

    lines = ["# RULES — 사용자 정의 행동 규칙\n",
             "> rule_save 도구로 에이전트가 관리합니다. 직접 편집도 가능합니다.\n"]
    for section, items in sections.items():
        lines.append(f"\n## {section}\n")
        for rid, text in items:
            # Multi-line rule_text: first line inline, rest indented
            rule_lines = text.splitlines()
            lines.append(f"- [{rid}] {rule_lines[0]}")
            for extra in rule_lines[1:]:
                lines.append(f"  {extra}")
    _RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _RULES_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _rule_save(rule_id: str, section: str, rule_text: str, overwrite: bool = False) -> dict:
    import re
    if not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$", rule_id):
        return {"ok": False, "error": "rule_id는 소문자·숫자·하이픈만 허용 (시작/끝은 영숫자)"}
    rules = _rules_load()
    if rule_id in rules and not overwrite:
        return {"ok": False, "error": f"rule_id '{rule_id}'가 이미 존재합니다. overwrite=True로 교체 가능."}
    rules[rule_id] = {"section": section, "rule_text": rule_text}
    _rules_save_all(rules)
    return {"ok": True, "rule_id": rule_id, "section": section, "total": len(rules)}


def _rule_delete(rule_id: str) -> dict:
    rules = _rules_load()
    if rule_id not in rules:
        return {"ok": False, "error": f"rule_id '{rule_id}'를 찾을 수 없습니다."}
    del rules[rule_id]
    _rules_save_all(rules)
    return {"ok": True, "deleted": rule_id, "remaining": len(rules)}


def _rule_list() -> dict:
    rules = _rules_load()
    if not rules:
        return {"ok": True, "count": 0, "rules": [], "message": "No saved rules"}
    items = [
        {"rule_id": rid, "section": info["section"], "rule_text": info["rule_text"]}
        for rid, info in rules.items()
    ]
    return {"ok": True, "count": len(items), "rules": items}


# ── Skill (slash command) management tool wrappers ───────────────────────────

def _skill_save(name: str, description: str, body: str,
                argument_hint: str = "", overwrite: bool = False) -> dict:
    from pipeline.commands import save_command
    return save_command(name, description, body, argument_hint, overwrite)


def _skill_delete(name: str) -> dict:
    from pipeline.commands import delete_command
    return delete_command(name)


def _widget_save(widget_id: str, title: str, type: str, source: str = "",
                 icon: str = "🧩", span: int = 1, text: str = "",
                 inputs: list | None = None, skill: str = "",
                 overwrite: bool = False) -> dict:
    from pipeline.widgets import save_widget
    return save_widget(
        widget_id, title, type, source, icon, span, text,
        inputs=inputs, skill=skill, overwrite=overwrite,
    )


def _widget_delete(widget_id: str) -> dict:
    from pipeline.widgets import delete_widget
    return delete_widget(widget_id)


def _ask_user_question(questions: list) -> dict:
    """User question tool. The dispatch layer only builds and returns the payload;
    server.py on_tool_done detects the __needs_user_answer__ marker, converts it to an SSE question event,
    receives the user's answer, and substitutes it as the actual function_call_output."""
    if not isinstance(questions, list) or not questions:
        return {"error": "questions must be a non-empty array"}
    # Minimal schema validation (reject immediately if LLM passed bad input — prevent infinite loop)
    norm = []
    for q in questions[:4]:
        if not isinstance(q, dict):
            return {"error": "각 질문은 객체여야 합니다"}
        question = (q.get("question") or "").strip()
        header = (q.get("header") or "").strip()[:12]
        options = q.get("options") or []
        if not question or not isinstance(options, list) or len(options) < 2:
            return {"error": "each question requires a question string and 2-4 options"}
        norm_opts = []
        for opt in options[:4]:
            if isinstance(opt, dict):
                lbl = (opt.get("label") or "").strip()
                desc = (opt.get("description") or "").strip()
                if lbl:
                    norm_opts.append({"label": lbl, "description": desc})
        if len(norm_opts) < 2:
            return {"error": f"'{question}': options requires at least 2 valid labels"}
        norm.append({
            "question": question,
            "header": header or "Select",
            "multiSelect": bool(q.get("multiSelect", False)),
            "options": norm_opts,
        })
    return {"__needs_user_answer__": True, "questions": norm}


def _exit_plan_mode(plan: str) -> dict:
    """Request plan mode exit approval. Actual handling in server.py on_tool_done."""
    plan = (plan or "").strip()
    if not plan:
        return {"error": "plan document is empty"}
    return {"__needs_plan_approval__": True, "plan": plan}


# ── MCP server management tools ──────────────────────────────────────────────

def _mcp_json_path():
    from pipeline.data_paths import mcp_config_path
    return mcp_config_path()


def _mcp_read():
    import json as _json
    p = _mcp_json_path()
    if not p.exists():
        return {"mcpServers": {}}
    return _json.loads(p.read_text(encoding="utf-8"))


def _mcp_write(data):
    import json as _json
    if "mcpServers" not in data or not isinstance(data["mcpServers"], dict):
        data["mcpServers"] = {}
    _mcp_json_path().write_text(_json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def mcp_list_servers() -> dict:
    """Return the list of registered MCP servers, including env-based auto servers (linear etc.)."""
    try:
        from pipeline.mcp_client import _load_registry, _tool_cache  # type: ignore
        reg = _load_registry()
        data = _mcp_read()
        explicit = data.get("mcpServers") or {}
        out = []
        for name, cfg in reg.items():
            out.append({
                "name": name,
                "transport": cfg.get("transport"),
                "command": cfg.get("command"),
                "args": cfg.get("args"),
                "url": cfg.get("url"),
                "auto_env": name not in explicit,
                "tool_count": len(_tool_cache.get(name, [])),
            })
        return {"servers": out}
    except Exception as e:
        return {"error": str(e)}


def mcp_add_server(command_line: str = "", name: str = "", url: str = "") -> dict:
    """Add an MCP server.
    - command_line: single-line command e.g. `npx -y @modelcontextprotocol/server-filesystem ~/dev`
    - or url (remote SSE)
    - name is auto-extracted if omitted (`server-X` suffix etc.)
    Hot-reload must be called separately after saving (mcp_reload).
    """
    import shlex
    try:
        if url:
            if not (url.startswith("http://") or url.startswith("https://")):
                return {"error": "url must start with http(s)://"}
            from urllib.parse import urlparse
            srv_name = (name or urlparse(url).hostname or "remote").split(".")[0]
            entry = {"url": url}
        elif command_line:
            tokens = shlex.split(command_line)
            if not tokens:
                return {"error": "empty command"}
            cmd = tokens[0]
            allowed = {"npx", "uvx", "python", "python3", "node", "deno", "bun"}
            if cmd not in allowed and not cmd.startswith("/"):
                return {"error": f"disallowed command: {cmd}"}
            args = tokens[1:]
            srv_name = name
            if not srv_name:
                for tok in args:
                    if tok.startswith("-"): continue
                    if tok.startswith("@") and "/" in tok:
                        pkg = tok.split("/", 1)[1]
                        for pref in ("server-", "mcp-server-"):
                            if pkg.startswith(pref):
                                srv_name = pkg[len(pref):]; break
                        if not srv_name: srv_name = pkg
                        break
                    if tok.startswith("mcp-server-"):
                        srv_name = tok[len("mcp-server-"):]; break
                    if tok.startswith("server-"):
                        srv_name = tok[len("server-"):]; break
                    srv_name = tok.split("/")[-1].split(".")[0]
                    break
                if not srv_name: srv_name = "mcp-server"
            entry = {"command": cmd, "args": args}
        else:
            return {"error": "command_line or url is required"}

        # Normalize name
        srv_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in srv_name).strip("-_") or "mcp-server"

        data = _mcp_read()
        data.setdefault("mcpServers", {})
        if srv_name in data["mcpServers"]:
            return {"error": f"'{srv_name}' already exists. Remove with mcp_remove_server first or use a different name"}
        data["mcpServers"][srv_name] = entry
        _mcp_write(data)
        return {"ok": True, "name": srv_name, "entry": entry, "next": "apply with mcp_reload"}
    except Exception as e:
        return {"error": str(e)}


def mcp_remove_server(name: str) -> dict:
    """Remove an MCP server from mcp.json. Env-based auto servers require removing the key from .env."""
    try:
        data = _mcp_read()
        servers = data.get("mcpServers") or {}
        if name not in servers:
            return {"error": f"'{name}' not found (or env-based auto registration — remove the key from .env)"}
        del servers[name]
        _mcp_write(data)
        return {"ok": True, "name": name, "next": "apply with mcp_reload"}
    except Exception as e:
        return {"error": str(e)}


def mcp_reload() -> dict:
    """Apply mcp.json changes at runtime — re-register tools without server restart."""
    try:
        import asyncio as _aio
        from pipeline.mcp_client import init_mcp_tools

        # Remove existing MCP schemas (names containing __)
        TOOL_SCHEMAS[:] = [s for s in TOOL_SCHEMAS if "__" not in s.get("name", "")]

        loop = _aio.new_event_loop()
        try:
            schemas = loop.run_until_complete(init_mcp_tools())
        finally:
            loop.close()
        added = 0
        for s_name, sch in schemas.items():
            TOOL_SCHEMAS.extend(sch)
            added += len(sch)
        return {"ok": True, "servers": list(schemas.keys()), "tool_count": added}
    except Exception as e:
        return {"error": str(e)}


# ── Tool function map ─────────────────────────────────────────────────────────

TOOL_FUNCTIONS: dict[str, Any] = {
    "web_search": web_search,
    "web_fetch": web_fetch,
    "gmail_search": gmail_search,
    "gmail_read": gmail_read,
    "gmail_send": gmail_send,
    "gmail_draft": gmail_draft,
    "gmail_modify_labels": gmail_modify_labels,
    "gmail_batch_modify": gmail_batch_modify,
    "gmail_list_attachments": gmail_list_attachments,
    "gmail_download_attachment": gmail_download_attachment,
    "calendar_list_events": calendar_list_events,
    "calendar_create_event": calendar_create_event,
    "calendar_update_event": calendar_update_event,
    "calendar_delete_event": calendar_delete_event,
    "drive_search": drive_search,
    "drive_read": drive_read,
    "file_read": file_read,
    "file_edit": file_edit,
    "slides_create": slides_create,
    "slides_append_slide": slides_append_slide,
    "docs_create": docs_create,
    "docs_append": docs_append,
    "rule_save": _rule_save,
    "rule_delete": _rule_delete,
    "rule_list": _rule_list,
    "skill_save": _skill_save,
    "skill_delete": _skill_delete,
    "widget_save": _widget_save,
    "widget_delete": _widget_delete,
    "icloud_list": icloud_list,
    "icloud_move": icloud_move,
    "icloud_rename": icloud_rename,
    "icloud_mkdir": icloud_mkdir,
    "discord_notify": discord_notify,
    "contact_memo_update": lambda name, memo: (
        __import__("json").dumps({"ok": True})
        if __import__("pipeline.contact_store", fromlist=["update_memo"]).update_memo(name, memo)
        else __import__("json").dumps({"error": f"contact not found: {name}"})
    ),
    "memory_persona_update": lambda section_key, content, notes="": persona_upsert(section_key, content, notes),
    "memory_event_add": lambda event_date, title, body, tags="": event_add(event_date, title, body, tags),
    "memory_entity_upsert": lambda name, kind, notes="", aliases=None: entity_upsert(name, kind, notes, aliases),
    # Linear tools
    "linear_list_issues": lambda team_key=None, states=None, limit=30: (
        __import__("pipeline.linear_client", fromlist=["list_issues"]).list_issues(
            team_key=team_key, states=states, limit=limit
        )
    ),
    "linear_get_issue": lambda issue_id: (
        __import__("pipeline.linear_client", fromlist=["get_issue"]).get_issue(issue_id)
    ),
    "linear_search_issues": lambda query, limit=10: (
        __import__("pipeline.linear_client", fromlist=["search_issues"]).search_issues(query, limit)
    ),
    "linear_create_issue": lambda title, team_key, description="", priority=3, state_name="Backlog", label_names=None: (
        __import__("pipeline.linear_client", fromlist=["create_issue"]).create_issue(
            title=title, team_key=team_key, description=description or None,
            priority=priority, state_name=state_name, label_names=label_names or []
        )
    ),
    "linear_update_issue": lambda issue_id, title=None, description=None, state_name=None, priority=None, label_names=None: (
        __import__("pipeline.linear_client", fromlist=["update_issue"]).update_issue(
            issue_id=issue_id, title=title, description=description,
            state_name=state_name, priority=priority, label_names=label_names
        )
    ),
    "linear_add_comment": lambda issue_id, body: (
        __import__("pipeline.linear_client", fromlist=["add_comment"]).add_comment(issue_id, body)
    ),
    # Session management
    "session_list": lambda limit=30: json.dumps(
        __import__("pipeline.session_store", fromlist=["list_sessions"]).list_sessions(limit=limit),
        ensure_ascii=False,
    ),
    "session_delete": lambda session_uuid: (
        __import__("pipeline.session_store", fromlist=["delete_session"]).delete_session(session_uuid)
        or json.dumps({"ok": True, "deleted": session_uuid}, ensure_ascii=False)
    ),
    "session_clean": lambda keep_min_messages=2, max_age_days=90, prune_trivial=False, dry_run=False: json.dumps(
        __import__("pipeline.session_store", fromlist=["clean_sessions"]).clean_sessions(
            keep_min_messages=keep_min_messages,
            max_age_days=max_age_days,
            dry_run=dry_run,
            is_trivial_fn=(
                __import__("pipeline.heartbeat", fromlist=["make_trivial_checker"]).make_trivial_checker(
                    use_llm=True, max_msg_count=4
                ) if prune_trivial else None
            ),
        ),
        ensure_ascii=False,
    ),
    **CODE_TOOL_FUNCTIONS,
    **OFFICE_TOOL_FUNCTIONS,
    "mcp_list_servers": mcp_list_servers,
    "mcp_add_server": mcp_add_server,
    "mcp_remove_server": mcp_remove_server,
    "mcp_reload": mcp_reload,
    "ask_user_question": _ask_user_question,
    "exit_plan_mode": _exit_plan_mode,
    "image_generate": image_generate,
}


def patch_account_enum() -> None:
    """Update Gmail/Calendar/Drive account enum from user_profile email_accounts keys at server startup.
    Keeps existing enum if onboarding has not been completed (no accounts registered)."""
    from pipeline.user_profile import email_accounts
    accs = email_accounts()
    if not accs:
        return
    keys = [a["key"] for a in accs]
    default_key = keys[0]
    for schema in TOOL_SCHEMAS:
        props = schema.get("parameters", {}).get("properties", {})
        acc_prop = props.get("account")
        if acc_prop and "enum" in acc_prop:
            acc_prop["enum"] = keys
            acc_prop["default"] = default_key


def dispatch_tool(name: str, arguments: dict) -> str:
    """Invoke a tool → return JSON string. MCP tools use a separate async path (dispatch_tool_async)."""
    # CE 게이트는 당분간 비활성 — 모든 진입점에서 전체 도구 실행 허용.
    # (원격 노출 시 재활성화하려면 _CE_MODE_VAR + _CE_ALLOWED_TOOLS 차단을 되살릴 것.)
    # Block write/exec/external-send tools in /plan mode. ask_user_question and exit_plan_mode are allowed.
    if _PLAN_MODE_VAR.get() and name in _PLAN_BLOCKED_TOOLS:
        return json.dumps({
            "error": (
                f"plan 모드에서 '{name}' 도구는 차단됨. "
                f"계획만 세우고 exit_plan_mode 도구로 사용자 승인을 받은 후 다시 호출해라."
            ),
            "plan_mode_blocked": True,
        }, ensure_ascii=False)
    from pipeline.mcp_client import is_mcp_tool
    if is_mcp_tool(name):
        import asyncio
        from pipeline.mcp_client import call_mcp_tool
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(call_mcp_tool(name, arguments))
        finally:
            loop.close()
    fn = TOOL_FUNCTIONS.get(name)
    if not fn:
        return json.dumps({"error": f"unknown tool: {name}"}, ensure_ascii=False)
    import time as _t
    _t0 = _t.monotonic()
    try:
        result = fn(**arguments)
        result_str = json.dumps(result, ensure_ascii=False, default=str)
        # Apply injection guard to external data tool results
        from pipeline.injection_guard import guard_tool_result
        result_str = guard_tool_result(name, result_str)
        dur_ms = int((_t.monotonic() - _t0) * 1000)
        # Success/failure branch — self_improve (consecutive failures → patch) + tool_telemetry (persistent aggregation)
        parsed = result if isinstance(result, dict) else {}
        is_success = not parsed.get("error")
        if is_success:
            try:
                from pipeline.self_improve import clear_failures
                clear_failures(name)
            except Exception:
                pass
        else:
            try:
                from pipeline.self_improve import record_failure
                record_failure(name, parsed["error"], arguments)
            except Exception:
                pass
        try:
            from pipeline.tool_telemetry import record_call
            record_call(name, is_success, dur_ms,
                        error=None if is_success else parsed.get("error"),
                        args=arguments)
        except Exception:
            pass
        return result_str
    except Exception as e:
        err_msg = str(e)
        dur_ms = int((_t.monotonic() - _t0) * 1000)
        try:
            from pipeline.self_improve import record_failure
            record_failure(name, err_msg, arguments)
        except Exception:
            pass
        try:
            from pipeline.tool_telemetry import record_call
            record_call(name, False, dur_ms, error=err_msg, args=arguments)
        except Exception:
            pass
        return json.dumps({"error": err_msg}, ensure_ascii=False)
