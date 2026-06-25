#!/usr/bin/env python3
# Optional native SWE-bench Docker runner — NOT used in default routing CI.
"""Requires Docker + SWE-bench harness. Operator-only."""
from __future__ import annotations


def run_native_swe(instance_id: str) -> dict:
    return {
        "skipped": True,
        "reason": "native SWE-bench Docker runner not wired in routing subset; use ext_swebench_lite micro tasks",
        "instance_id": instance_id,
    }
