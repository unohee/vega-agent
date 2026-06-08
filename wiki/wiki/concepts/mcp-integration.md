---
title: "MCP 서버 등록/호출 패턴"
tags: [mcp, tools, integration]
sources: [entities/pipeline-streaming]
updated: 2026-06-02
status: active
---

# MCP 서버 등록/호출 패턴

`pipeline/mcp_client.py`의 `init_mcp_tools()` / `call_mcp_tool()`.

## 등록 위치

**user data dir의 `mcp.json`** (= `data_paths.mcp_config_path()`).
레포의 `data/mcp.json`은 **읽지 않는다** → [[concepts/data-paths]] 참조.

## 초기화 시점

- `web/server.py` lifespan에서 `init_mcp_tools()` 1회 호출
- 채널 봇은 `ensure_mcp_loaded()` (프로세스당 1회)

## 도구 envelope (kyte 반환 포맷)

```json
{ "data": <list|dict|null>, "source": {"system": "...", "fetched_at": "..."}, "note": "<선택>" }
```

kyte 도구는 모두 read-only envelope → CE 모드에서도 안전하게 허용.

## 새 MCP 서버 추가

1. user data dir의 `mcp.json`에 항목 추가
2. 서버 재시작 (lifespan이 다시 `init_mcp_tools()` 실행)
3. CE 모드에서 허용하려면 `tools._CE_ALLOWED_TOOLS`에 prefix 추가 + `dispatch_tool` 양쪽 수정 → [[concepts/ce-mode-gate]]

## 관련

- [[concepts/data-paths]]
- [[concepts/ce-mode-gate]]
