#!/usr/bin/env python3
# Created: 2026-06-24
# Purpose: INT-1893 L4 — recent light-load turn telemetry report.
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from pipeline.overthinking_telemetry import recent_light_stats  # noqa: E402


def main() -> int:
    days = float(sys.argv[1]) if len(sys.argv) > 1 else 7.0
    stats = recent_light_stats(days=days)
    print(f"[overthinking-report] light turns last {days}d: n={stats.get('n', 0)}")
    if stats.get("n"):
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
