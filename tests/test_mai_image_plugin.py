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
    source.write_bytes(b"\x89PNG\r\n\x1a\nimage")
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
        destination.write_bytes(b"\x89PNG\r\n\x1a\nremote image")
        return destination

    monkeypatch.setattr(provider, "_download_remote_image", safe_download)
    name, data, content_type = provider._load_source("https://images.example/source.png")

    assert calls[0][0] == "https://images.example/source.png"
    assert name == "source.png"
    assert data == b"\x89PNG\r\n\x1a\nremote image"
    assert content_type == "image/png"


def test_source_validation_rejects_non_image(monkeypatch, tmp_path):
    provider = load_provider(monkeypatch)
    source = tmp_path / "not-an-image.png"
    source.write_bytes(b"not image")

    with pytest.raises(ValueError, match="not a supported image"):
        provider._load_source(str(source))


def test_generation_rejects_multiple_reference_images(monkeypatch, tmp_path):
    provider = load_provider(monkeypatch)
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"image")
    second.write_bytes(b"image")

    called = []
    monkeypatch.setattr(provider.requests, "post", lambda *args, **kwargs: called.append(1))
    result = provider.MAIImageProvider(
        api_key="key", endpoint="https://example.services.ai.azure.com", model="deployment"
    ).generate("edit", "square", image_url=str(first), reference_image_urls=[str(second)])

    assert result["success"] is False
    assert result["error_type"] == "too_many_references"
    assert called == []


def test_runtime_configuration_is_resolved_after_provider_creation(monkeypatch):
    provider = load_provider(monkeypatch)
    monkeypatch.delenv("MAI_FOUNDRY_API_KEY", raising=False)
    monkeypatch.delenv("MAI_FOUNDRY_ENDPOINT", raising=False)
    instance = provider.MAIImageProvider()
    assert instance.is_available() is False

    monkeypatch.setenv("MAI_FOUNDRY_API_KEY", "new-key")
    monkeypatch.setenv("MAI_FOUNDRY_ENDPOINT", "https://new.services.ai.azure.com")
    monkeypatch.setenv("MAI_IMAGE_MODEL", "new-deployment")

    assert instance.is_available() is True
    assert instance.default_model() == "new-deployment"


def test_config_model_is_used_when_environment_model_is_unset(monkeypatch):
    provider = load_provider(monkeypatch)
    monkeypatch.delenv("MAI_IMAGE_MODEL", raising=False)
    monkeypatch.setattr(provider, "_load_image_gen_config", lambda: {"image_gen": {"model": "configured-deployment"}})

    assert provider.MAIImageProvider(model=None).default_model() == "configured-deployment"


def test_deployment_error_explains_exact_name(monkeypatch):
    provider = load_provider(monkeypatch)

    class Response:
        status_code = 404
        text = "deployment missing"

        def json(self):
            return {"error": {"message": "deployment missing"}}

    error = provider.requests.HTTPError("not found", response=Response())
    monkeypatch.setattr(provider.requests, "post", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    result = provider.MAIImageProvider(
        api_key="key", endpoint="https://example.services.ai.azure.com", model="my-deployment"
    ).generate("cat")

    assert result["success"] is False
    assert "exact deployment name" in result["error"]


def test_missing_credentials_returns_auth_error(monkeypatch):
    provider = load_provider(monkeypatch)
    result = provider.MAIImageProvider(api_key="", endpoint="", model="deployment").generate("cat")
    assert result["success"] is False
    assert result["error_type"] == "auth_required"
