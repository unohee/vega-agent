#!/usr/bin/env python3
# Created: 2026-06-24
# Purpose: INT-1893 before/after — 단순 태스크 라운드 상한 측정(무과금, API 없음).
# Usage: python scripts/measure_load_rounds.py
"""Load routing before/after table for INT-1893 acceptance."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from pipeline.tier_router import (  # noqa: E402
    legacy_load_from_user_blob,
    resolve_load_routing,
    rounds_for_load,
)

SCENARIOS: list[tuple[str, list[dict]]] = [
    (
        "ikea_multiturn",
        [
            {"role": "user", "content": "매출 데이터 분석해서 보고서 작성해줘"},
            {"role": "assistant", "content": "보고서 초안을 작성했습니다."},
            {"role": "user", "content": "이케아 5만원 이하 사무용 조명 5개 추천해줘"},
        ],
    ),
    (
        "short_analyze",
        [{"role": "user", "content": "이 파일 분석해줘"}],
    ),
    (
        "heavy_code",
        [{"role": "user", "content": "이 파이썬 함수 디버그하고 리팩터해줘"}],
    ),
]


def _max_for_load(load: str) -> int:
    return {"light": 10, "standard": 20, "heavy": 24}.get(load, 20)


def main() -> int:
    rows = []
    for name, msgs in SCENARIOS:
        after = resolve_load_routing(msgs)
        before_load = legacy_load_from_user_blob(msgs)
        rows.append({
            "scenario": name,
            "before": {"load": before_load, "max_rounds": _max_for_load(before_load)},
            "after": {"load": after["load"], "max_rounds": after["max_rounds"]},
        })

    print("[INT-1893] load routing before/after (no API)")
    print(f"{'scenario':<18} {'before load':<10} {'before max':<12} {'after load':<10} {'after max'}")
    for r in rows:
        b, a = r["before"], r["after"]
        print(f"{r['scenario']:<18} {b['load']:<10} {b['max_rounds']:<12} {a['load']:<10} {a['max_rounds']}")

    out = REPO / "build_output" / "int1893_before_after.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"schema_version": 1, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[INT-1893] saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
