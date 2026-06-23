# Created: 2026-06-23
# Purpose: image_convert (Pillow 결정적 포맷/크기 변환) 회귀 — INT-1883.
# Dependencies: pipeline.tools_office, Pillow
# Test Status: green (2026-06-23)

from __future__ import annotations

from PIL import Image

from pipeline.tools_office import image_convert


def test_png_rgba_to_jpg_flattens(tmp_path):
    src = tmp_path / "a.png"
    Image.new("RGBA", (40, 30), (255, 0, 0, 128)).save(src)
    dst = tmp_path / "a.jpg"
    r = image_convert(str(src), str(dst))
    assert r.get("ok") and r["format"] == "JPEG", r
    out = Image.open(dst)
    assert out.mode == "RGB"          # 알파 합성됨 (JPEG는 알파 불가)
    assert out.size == (40, 30)       # 크기 보존


def test_resize(tmp_path):
    src = tmp_path / "b.png"
    Image.new("RGB", (40, 30), (0, 0, 255)).save(src)
    dst = tmp_path / "b_small.png"
    r = image_convert(str(src), str(dst), width=20, height=10)
    assert r.get("ok") and r["size"] == [20, 10], r


def test_missing_source(tmp_path):
    r = image_convert(str(tmp_path / "nope.png"), str(tmp_path / "out.jpg"))
    assert "error" in r and "없음" in r["error"]


def test_path_guard_blocks_outside_root():
    r = image_convert("/tmp/whatever.png", "/etc/evil.jpg")
    assert "error" in r and "SAFEGUARD" in r["error"]


def test_registered_in_tools():
    import pipeline.tools as t
    assert "image_convert" in t.TOOL_FUNCTIONS
    assert any(s.get("name") == "image_convert" for s in t.TOOL_SCHEMAS)
