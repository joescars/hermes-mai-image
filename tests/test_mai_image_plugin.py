import base64
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "hermes_mai_image" / "provider.py"


def load_provider(monkeypatch):
    agent = ModuleType("agent")
    image_provider = ModuleType("agent.image_gen_provider")

    class ImageGenProvider:
        pass

    image_provider.ImageGenProvider = ImageGenProvider
    image_provider.DEFAULT_ASPECT_RATIO = "square"
    image_provider.resolve_aspect_ratio = lambda value: value or "square"
    image_provider.success_response = lambda **kwargs: {"success": True, **kwargs}
    image_provider.error_response = lambda **kwargs: {"success": False, **kwargs}
    image_provider.save_b64_image = lambda value, prefix, extension="png": Path(
        "/tmp", f"{prefix}.{extension}"
    )
    image_provider.save_url_image = lambda value, prefix: Path("/tmp", f"{prefix}.png")
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.image_gen_provider", image_provider)

    spec = importlib.util.spec_from_file_location("hermes_mai_image.provider", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_discovery_parses_models_response(monkeypatch):
    provider = load_provider(monkeypatch)

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "deployment-flash", "name": "Flash deployment"}]}

    monkeypatch.setattr(provider.requests, "get", lambda *args, **kwargs: Response())
    models = provider.discover_models("https://example.services.ai.azure.com", "key")

    assert models == [{"id": "deployment-flash", "display": "Flash deployment"}]


def test_generation_uses_mai_endpoint_and_api_key(monkeypatch):
    provider = load_provider(monkeypatch)
    calls = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"png").decode()}]}

    def post(url, **kwargs):
        calls.update(url=url, kwargs=kwargs)
        return Response()

    monkeypatch.setattr(provider.requests, "post", post)
    result = provider.MAIImageProvider(
        api_key="key", endpoint="https://example.services.ai.azure.com", model="deployment"
    ).generate("a blue bird", "landscape")

    assert result["success"] is True
    assert calls["url"].endswith("/mai/v1/images/generations")
    assert calls["kwargs"]["headers"]["api-key"] == "key"
    assert calls["kwargs"]["json"] == {
        "model": "deployment", "prompt": "a blue bird", "width": 1536, "height": 1024
    }


def test_edit_uses_multipart_image_upload(monkeypatch, tmp_path):
    provider = load_provider(monkeypatch)
    source = tmp_path / "source.png"
    source.write_bytes(b"image")
    calls = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"edited").decode()}]}

    def post(url, **kwargs):
        calls.update(url=url, kwargs=kwargs)
        return Response()

    monkeypatch.setattr(provider.requests, "post", post)
    result = provider.MAIImageProvider(
        api_key="key", endpoint="https://example.services.ai.azure.com", model="deployment"
    ).generate("make it brighter", "square", image_url=str(source))

    assert result["success"] is True
    assert calls["url"].endswith("/mai/v1/images/edits")
    assert calls["kwargs"]["data"] == {"model": "deployment", "prompt": "make it brighter"}
    assert calls["kwargs"]["files"]["image"][0] == "source.png"


def test_edit_blocks_credential_path(monkeypatch, tmp_path):
    provider = load_provider(monkeypatch)
    secret = tmp_path / ".env"
    secret.write_text("MAI_FOUNDRY_API_KEY=secret")

    def block(path):
        raise ValueError(f"blocked: {path}")

    monkeypatch.setattr(provider, "raise_if_read_blocked", block)
    with pytest.raises(ValueError, match="blocked"):
        provider._load_source(str(secret))


def test_remote_edit_uses_safe_bounded_download(monkeypatch):
    provider = load_provider(monkeypatch)
    calls = []

    def safe_download(url, destination):
        calls.append((url, destination))
        destination.write_bytes(b"remote image")
        return destination

    monkeypatch.setattr(provider, "_download_remote_image", safe_download)
    name, data, content_type = provider._load_source("https://images.example/source.png")

    assert calls[0][0] == "https://images.example/source.png"
    assert name == "source.png"
    assert data == b"remote image"
    assert content_type == "image/png"


def test_missing_credentials_returns_auth_error(monkeypatch):
    provider = load_provider(monkeypatch)
    result = provider.MAIImageProvider(api_key="", endpoint="", model="deployment").generate("cat")
    assert result["success"] is False
    assert result["error_type"] == "auth_required"
