# Created: 2026-06-11
# Purpose: 이미지 첨부 + 편집 요청 → image_generate 라우팅/페이로드 회귀 테스트 (INT-1457)
#          — (1) API 키가 Keychain/.env 체인으로 해석되는지 (os.getenv만 보던 버그),
#            (2) 편집/생성 모드 모델 선택과 OpenRouter 페이로드 조립,
#            (3) path 없는 첨부의 서버측 경로 보장(_persist_attached_image),
#            (4) 경로 힌트 마커가 서버/도구 설명 간에 어긋나지 않는지.
# Dependencies: pipeline/tools.py, web/server.py
# Test Status: green (2026-06-11)

from __future__ import annotations

import base64
import json
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.tools import image_generate

# 1×1 픽셀 PNG (PIL 없이도 유효한 최소 이미지)
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
    "h6FO1AAAAABJRU5ErkJggg=="
)

_FAKE_IMAGE_RESPONSE = {
    "choices": [{
        "message": {
            "images": [{"image_url": {"url": "data:image/png;base64,"
                                             + base64.b64encode(_TINY_PNG).decode()}}],
            "content": "",
        }
    }]
}


class _FakeResp:
    def __init__(self, body: dict):
        self._body = body

    def read(self):
        return json.dumps(self._body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _call_capturing(monkeypatch, tmp_path, **kwargs):
    """image_generate 호출 — urlopen을 가로채 요청 페이로드/헤더를 캡처."""
    captured: dict = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        captured["payload"] = json.loads(req.data.decode())
        return _FakeResp(_FAKE_IMAGE_RESPONSE)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    # 결과 PNG 저장 위치를 tmp로
    import pipeline.data_paths as dp
    monkeypatch.setattr(dp, "charts_dir", lambda: tmp_path / "charts")
    result = image_generate(**kwargs)
    return result, captured


class TestKeyResolution:
    def test_env_var_missing_falls_back_to_keychain(self, monkeypatch, tmp_path):
        """온보딩이 Keychain에 넣은 키도 잡아야 한다 — os.getenv만 보면 배포본에서 전멸 (INT-1457)."""
        monkeypatch.delenv("OPENROUTER_API", raising=False)
        with patch("pipeline.keychain.get", return_value="sk-or-keychain") as kc_get:
            result, captured = _call_capturing(monkeypatch, tmp_path, prompt="a cat")
        kc_get.assert_called_once_with("OPENROUTER_API")
        assert "error" not in result, result
        assert captured["headers"].get("Authorization") == "Bearer sk-or-keychain"

    def test_no_key_anywhere_friendly_error(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API", raising=False)
        with patch("pipeline.keychain.get", return_value=""):
            result = image_generate(prompt="a cat")
        assert "error" in result
        # 사용자 친화 안내 (설정 경로) — 개발자용 'environment variable' 문구 금지
        assert "API 키" in result["error"] and "설정" in result["error"]
        assert "environment variable" not in result["error"]

    def test_env_var_takes_priority(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENROUTER_API", "sk-or-env")
        result, captured = _call_capturing(monkeypatch, tmp_path, prompt="a cat")
        assert captured["headers"].get("Authorization") == "Bearer sk-or-env"


class TestModelRouting:
    def test_generate_mode_default_model(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENROUTER_API", "sk-test")
        result, captured = _call_capturing(monkeypatch, tmp_path, prompt="a cat")
        assert captured["payload"]["model"] == "google/gemini-2.5-flash-image"
        assert captured["payload"]["modalities"] == ["image"]
        # 생성 모드: 텍스트 프롬프트 단독
        assert captured["payload"]["messages"][0]["content"] == "a cat"
        assert result.get("__type") == "image" and Path(result["path"]).exists()

    def test_edit_mode_routes_to_edit_model_with_image(self, monkeypatch, tmp_path):
        """첨부 이미지 편집: 검증된 기본 모델로 라우팅 + 원본이 base64로 페이로드에 실려야 한다.
        (gpt-5-image-mini 기본값은 OpenRouter 402로 INT-1457에서 gemini로 교체 — 실측)"""
        monkeypatch.setenv("OPENROUTER_API", "sk-test")
        src = tmp_path / "attached.png"
        src.write_bytes(_TINY_PNG)
        result, captured = _call_capturing(
            monkeypatch, tmp_path, prompt="remove the text", image_path=str(src))
        assert captured["payload"]["model"] == "google/gemini-2.5-flash-image"
        content = captured["payload"]["messages"][0]["content"]
        assert isinstance(content, list)
        kinds = {b["type"] for b in content}
        assert kinds == {"text", "image_url"}
        img_block = next(b for b in content if b["type"] == "image_url")
        assert img_block["image_url"]["url"].startswith("data:image/")
        assert "error" not in result, result

    def test_edit_mode_missing_file_friendly_error(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API", "sk-test")
        result = image_generate(prompt="x", image_path="/no/such/file.png")
        assert "error" in result and "/no/such/file.png" in result["error"]

    def test_response_level_api_error_surfaced(self, monkeypatch, tmp_path):
        """OpenRouter 응답 레벨 error(402 크레딧 등)를 삼키지 않고 노출해야 한다 (INT-1457 실측)."""
        monkeypatch.setenv("OPENROUTER_API", "sk-test")
        err_body = {"choices": [{"message": {"content": None}}],
                    "error": {"message": "This request requires more credits", "code": 402}}

        def fake_urlopen(req, timeout=0):
            return _FakeResp(err_body)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        result = image_generate(prompt="a cat")
        assert "error" in result
        assert "more credits" in result["error"]
        assert "이미지 모델 호출 실패" in result["error"]


class TestServerPathGuarantee:
    """path 없는 첨부(프론트 업로드 실패)도 서버가 경로를 만들어 편집 가능해야 한다."""

    def test_persist_attached_image(self, monkeypatch, tmp_path):
        from web import server as srv
        monkeypatch.setattr(srv, "_uploads_dir", lambda: tmp_path / "uploads")
        img = {"data": base64.b64encode(_TINY_PNG).decode(), "media_type": "image/png"}
        p = srv._persist_attached_image(img)
        assert Path(p).exists()
        assert Path(p).read_bytes() == _TINY_PNG
        assert p.endswith(".png")

    def test_persist_jpeg_extension(self, monkeypatch, tmp_path):
        from web import server as srv
        monkeypatch.setattr(srv, "_uploads_dir", lambda: tmp_path / "uploads")
        img = {"data": base64.b64encode(b"fakejpeg").decode(), "media_type": "image/jpeg"}
        assert srv._persist_attached_image(img).endswith(".jpg")


class TestHintMarkerConsistency:
    """서버가 붙이는 '[첨부 이미지 경로]' 힌트와 도구 설명·기본 에이전트 가이드가 어긋나면
    모델이 경로를 못 찾는다 — 문자열 결합 드리프트 가드."""

    MARKER = "[첨부 이미지 경로]"

    def test_marker_in_server_and_tool_schema(self):
        root = Path(__file__).parent.parent
        server_src = (root / "web" / "server.py").read_text(encoding="utf-8")
        tools_src = (root / "pipeline" / "tools.py").read_text(encoding="utf-8")
        default_md = (root / "data" / "agents" / "_default.md").read_text(encoding="utf-8")
        assert self.MARKER in server_src
        assert self.MARKER in tools_src
        assert self.MARKER in default_md
