# Created: 2026-07-01
# Purpose: web.server 와 web.state 의 공유 런타임 상태 동일성 회귀 (INT-2234 audit).
#   server.py 가 import 한 공유 dict 를 재할당하면 web.routers.sessions 와 분열된다.

from __future__ import annotations


def test_task_registry_single_source():
    import web.server as srv
    import web.state as st
    # 재할당하면 server 의 chat/approve/abort 와 sessions 의 active/resume/zombie 가
    # 다른 registry 를 본다 (CRITICAL). 동일 객체여야 한다.
    assert srv._TASK_REGISTRY is st._TASK_REGISTRY


def test_access_single_source():
    import web.server as srv
    import web.state as st
    assert srv._ACCESS is st._ACCESS


def test_sessions_router_shares_same_objects():
    import web.routers.sessions as sess
    import web.state as st
    assert sess._TASK_REGISTRY is st._TASK_REGISTRY
    assert sess._ACCESS is st._ACCESS
