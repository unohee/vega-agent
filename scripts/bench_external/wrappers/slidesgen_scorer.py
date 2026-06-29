#!/usr/bin/env python3
# Optional SlidesGen-Bench content scorer wrapper.
"""If BENCH_SLIDESGEN_ROOT points to cloned repo, delegate scoring; else fallback."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def score_pptx(pptx_path: Path) -> dict:
    root = os.getenv("BENCH_SLIDESGEN_ROOT", "").strip()
    if not root or not Path(root).is_dir():
        return {"skipped": True, "reason": "BENCH_SLIDESGEN_ROOT unset"}
    script = Path(root) / "evaluate" / "content_only.py"
    if not script.is_file():
        return {"skipped": True, "reason": "content_only.py not found"}
    try:
        proc = subprocess.run(
            [sys.executable, str(script), str(pptx_path)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return {"skipped": False, "stdout": proc.stdout[:500], "returncode": proc.returncode}
    except Exception as e:
        return {"skipped": True, "reason": str(e)[:200]}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: slidesgen_scorer.py path/to/deck.pptx")
        raise SystemExit(2)
    print(score_pptx(Path(sys.argv[1])))
