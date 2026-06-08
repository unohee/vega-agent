# Created: 2026-06-01
# Purpose: 런타임 설정(settings.json) 읽기/쓰기. SearXNG URL 등 온보딩·설정창에서
#   바꾸는 값을 user data dir 의 settings.json 에 보관한다. llm_providers.json 과
#   동일한 user-data JSON 패턴.
# Dependencies: pipeline.data_paths (stdlib only)

from __future__ import annotations

import json
from typing import Any

from pipeline.data_paths import settings_path


def load_settings() -> dict[str, Any]:
    """settings.json 전체를 dict 로 반환. 없거나 손상되면 빈 dict."""
    p = settings_path()
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def get_setting(key: str, default: Any = None) -> Any:
    return load_settings().get(key, default)


def set_setting(key: str, value: Any) -> None:
    """단일 키 upsert 후 즉시 디스크에 기록 (원자적 교체)."""
    data = load_settings()
    data[key] = value
    _write(data)


def _write(data: dict[str, Any]) -> None:
    p = settings_path()
    tmp = p.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(p)  # 원자적 교체 — 부분 기록 방지
