#!/usr/bin/env python3
# Created: 2026-06-21
# Purpose: CLI wrapper for derived entity co-occurrence edge generation.

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.graph_cooccurrence import main


if __name__ == "__main__":
    raise SystemExit(main())
