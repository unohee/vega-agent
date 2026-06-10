# Created: 2026-06-10
# Purpose: 저사양 머신 게이트 단위 테스트 — low_memory_host 판정 (INT-1430)
# Dependencies: pipeline/sandbox.py
# Test Status: green (2026-06-10)

from __future__ import annotations

from types import SimpleNamespace

import psutil

from pipeline.sandbox import _LOW_MEM_BYTES, low_memory_host


def test_low_memory_machine_detected(monkeypatch):
    monkeypatch.setattr(psutil, "virtual_memory",
                        lambda: SimpleNamespace(total=8 * 1024 ** 3))
    assert low_memory_host() is True


def test_high_memory_machine_not_low(monkeypatch):
    monkeypatch.setattr(psutil, "virtual_memory",
                        lambda: SimpleNamespace(total=32 * 1024 ** 3))
    assert low_memory_host() is False


def test_boundary_exactly_16gb_not_low(monkeypatch):
    monkeypatch.setattr(psutil, "virtual_memory",
                        lambda: SimpleNamespace(total=_LOW_MEM_BYTES))
    assert low_memory_host() is False


def test_psutil_failure_defaults_to_false(monkeypatch):
    def _boom():
        raise RuntimeError("psutil down")
    monkeypatch.setattr(psutil, "virtual_memory", _boom)
    # 판정 실패 시 기존 동작(선기동) 유지 — 저사양 게이트가 오탐으로 켜지지 않게
    assert low_memory_host() is False
