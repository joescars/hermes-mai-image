import asyncio
import base64
import importlib.util
import json
import sys
import types
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
    image_provider.success_response = lambda extra=None, **kwargs: {"success": True, **kwargs, **(extra or {})}
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

    assert models == [{"id": "deployment-flash", "display": "Flash deployment (unverified: may not be an image model)"}]


def test_discovery_non_json_body_returns_empty(monkeypatch):
    provider = load_provider(monkeypatch)

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            raise json.JSONDecodeError("bad", "<html>", 0)

    monkeypatch.setattr(provider.requests, "get", lambda *args, **kwargs: Response())
    assert provider.discover_models("https://example.services.ai.azure.com", "key") == []


def test_discovery_labels_unrecognised_models_as_unverified(monkeypatch):
    provider = load_provider(monkeypatch)

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "chat-gpt", "name": "Chat"}, {"id": "MAI-Image-2.5"}]}

    monkeypatch.setattr(provider.requests, "get", lambda *args, **kwargs: Response())
    models = provider.discover_models("https://example", "key")
    assert [m["id"] for m in models] == ["chat-gpt", "MAI-Image-2.5"]
    assert "unverified" in models[0]["display"]
    assert models[1]["display"] == "MAI-Image-2.5"


def test_discovery_network_error_returns_empty(monkeypatch):
    provider = load_provider(monkeypatch)

    def boom(*args, **kwargs):
        raise provider.requests.ConnectionError("down")

    monkeypatch.setattr(provider.requests, "get", boom)
    assert provider.discover_models("https://example", "key") == []


def test_default_model_is_in_fallback_catalog(monkeypatch):
    provider = load_provider(monkeypatch)
    assert provider.DEFAULT_MODEL == "MAI-Image-2.5"
    assert provider.DEFAULT_MODEL in provider.FALLBACK_MODELS


def test_generation_retries_rate_limit_and_server_errors(monkeypatch):
    provider = load_provider(monkeypatch)
    responses = []
    class Response:
        def __init__(self, status):
            self.status_code = status
            self.headers = {"Retry-After": "3"} if status == 429 else {}
        def raise_for_status(self):
            if self.status_code >= 400:
                raise provider.requests.HTTPError(response=self)
        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"png").decode()}]}
    responses.extend([Response(503), Response(429), Response(200)])
    sleeps = []
    monkeypatch.setattr(provider.time, "sleep", sleeps.append)
    monkeypatch.setattr(provider.requests, "post", lambda *args, **kwargs: responses.pop(0))
    result = provider.MAIImageProvider(api_key="key", endpoint="https://example", model="m").generate("cat")
    assert result["success"] is True
    assert 10 <= sleeps[0] <= 13  # no Retry-After: long jittered backoff
    assert sleeps[1] == 3  # Retry-After seconds honoured


class _Resp:
    def __init__(self, provider, status, headers=None):
        self._provider = provider
        self.status_code = status
        self.headers = headers or {}
    def raise_for_status(self):
        if self.status_code >= 400:
            raise self._provider.requests.HTTPError(response=self)
    def json(self):
        return {"data": [{"b64_json": base64.b64encode(b"png").decode()}]}


def _run_posts(monkeypatch, provider, responses, deadline=None):
    """Drive generate() with a fake clock; sleep advances it. Returns (result, calls, sleeps)."""
    clock = [1000.0]
    sleeps, calls = [], []
    monkeypatch.setattr(provider, "_now", lambda: clock[0])
    monkeypatch.setattr(provider.time, "sleep", lambda s: (sleeps.append(s), clock.__setitem__(0, clock[0] + s)))
    def post(*a, **k):
        calls.append(k.get("timeout"))
        return responses.pop(0)
    monkeypatch.setattr(provider.requests, "post", post)
    if deadline is not None:
        monkeypatch.setenv("MAI_REQUEST_DEADLINE", str(deadline))
    result = provider.MAIImageProvider(api_key="key", endpoint="https://example", model="m").generate("cat")
    return result, calls, sleeps


def test_retry_after_http_date_is_honoured(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime
    provider = load_provider(monkeypatch)
    when = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=45), usegmt=True)
    result, calls, sleeps = _run_posts(monkeypatch, provider, [
        _Resp(provider, 429, {"Retry-After": when}), _Resp(provider, 200)])
    assert result["success"] is True
    assert len(sleeps) == 1 and 40 <= sleeps[0] <= 46


@pytest.mark.parametrize("status", [500, 504, 501, 400])
def test_non_retryable_statuses_are_not_retried(monkeypatch, status):
    provider = load_provider(monkeypatch)
    result, calls, sleeps = _run_posts(monkeypatch, provider, [_Resp(provider, status)])
    assert result["success"] is False
    assert len(calls) == 1 and sleeps == []


def test_502_is_retried(monkeypatch):
    provider = load_provider(monkeypatch)
    result, calls, sleeps = _run_posts(monkeypatch, provider, [_Resp(provider, 502), _Resp(provider, 200)])
    assert result["success"] is True and len(calls) == 2


def test_deadline_stops_retries_and_never_sleeps_past_it(monkeypatch):
    provider = load_provider(monkeypatch)
    result, calls, sleeps = _run_posts(
        monkeypatch, provider, [_Resp(provider, 429, {"Retry-After": "50"}) for _ in range(3)], deadline=60)
    assert result["success"] is False
    assert len(calls) == 2 and sleeps == [50]  # second wait (50s) would overrun the 60s budget


def test_request_timeout_shrinks_to_remaining_budget(monkeypatch):
    provider = load_provider(monkeypatch)
    result, calls, sleeps = _run_posts(
        monkeypatch, provider, [_Resp(provider, 429, {"Retry-After": "50"}), _Resp(provider, 200)], deadline=100)
    assert result["success"] is True
    assert calls[0] == 100 and calls[1] == 50


def test_generation_retries_stop_at_max(monkeypatch):
    provider = load_provider(monkeypatch)
    class Response:
        status_code = 503
        headers = {}
        def raise_for_status(self):
            raise provider.requests.HTTPError(response=self)
    calls = []
    monkeypatch.setattr(provider.time, "sleep", lambda _: None)
    monkeypatch.setattr(provider.requests, "post", lambda *a, **k: (calls.append(1) or Response()))
    result = provider.MAIImageProvider(api_key="key", endpoint="https://example", model="m").generate("cat")
    assert result["success"] is False
    assert len(calls) == 3


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
    assert result["image"] == "file:///tmp/mai.png"
    assert result["host_image"] == "/tmp/mai.png"
    assert calls["url"].endswith("/mai/v1/images/generations")
    assert calls["kwargs"]["headers"]["api-key"] == "key"
    assert calls["kwargs"]["json"] == {
        "model": "deployment", "prompt": "a blue bird", "width": 1248, "height": 832
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
    assert result["image"] == "file:///tmp/mai.png"
    assert result["host_image"] == "/tmp/mai.png"
    assert calls["url"].endswith("/mai/v1/images/edits")
    assert calls["kwargs"]["data"] == {"model": "deployment", "prompt": "make it brighter"}
    assert calls["kwargs"]["files"] == [("image", ("source.png", b"\x89PNG\r\n\x1a\nimage", "image/png"))]


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


@pytest.mark.parametrize(
    "name,data",
    [
        ("anim.gif", b"GIF89a" + b"\x00" * 10),
        ("old.gif", b"GIF87a" + b"\x00" * 10),
        ("pic.webp", b"RIFF\x00\x00\x00\x00WEBPVP8 "),
    ],
)
def test_edit_rejects_gif_and_webp_without_posting(monkeypatch, tmp_path, name, data):
    provider = load_provider(monkeypatch)
    source = tmp_path / name
    source.write_bytes(data)
    posted = []
    monkeypatch.setattr(provider.requests, "post", lambda *a, **k: posted.append(1))

    result = provider.MAIImageProvider(
        api_key="key", endpoint="https://example.services.ai.azure.com", model="deployment"
    ).generate("edit", "square", image_url=str(source))

    assert result["success"] is False
    assert result["error_type"] == "invalid_input"
    assert "JPEG or PNG" in result["error"]
    assert posted == []


def test_source_validation_accepts_jpeg(monkeypatch):
    provider = load_provider(monkeypatch)
    assert provider._validate_image_bytes(b"\xff\xd8\xff\xe0data", "") == "image/jpeg"


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


def test_remote_download_blocks_unsafe_address(monkeypatch, tmp_path):
    """A URL Hermes' URL-safety policy rejects must not be fetched at all."""
    provider = load_provider(monkeypatch)
    monkeypatch.setattr(provider, "_source_url_is_safe", lambda url: False)
    fetched = []

    def fake_get(*args, **kwargs):
        fetched.append(args)
        raise AssertionError("unsafe URL must not be requested")

    monkeypatch.setattr(provider.requests, "get", fake_get)
    with pytest.raises(ValueError, match="private, internal or blocked"):
        provider._download_remote_image("http://169.254.169.254/latest/meta-data", tmp_path / "x")
    assert fetched == []


def test_remote_download_rejects_redirect(monkeypatch, tmp_path):
    """A redirecting source must be refused, not followed into a second address.

    Following a redirect is how a public URL bounces into an internal one, so the
    Location header must never be dereferenced.
    """
    provider = load_provider(monkeypatch)
    monkeypatch.setattr(provider, "_source_url_is_safe", lambda url: True)

    class RedirectingResponse:
        status_code = 302
        is_redirect = True
        is_permanent_redirect = False
        headers = {"Location": "http://169.254.169.254/latest/meta-data/"}

        def iter_content(self, chunk_size):
            raise AssertionError("a redirecting response must not be streamed")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    calls = []

    def fake_get(url, *args, **kwargs):
        calls.append(url)
        return RedirectingResponse()

    monkeypatch.setattr(provider.requests, "get", fake_get)
    with pytest.raises(ValueError, match="may not redirect"):
        provider._download_remote_image("https://images.example/r.png", tmp_path / "r")
    # Only the original URL was requested; the Location was never fetched.
    assert calls == ["https://images.example/r.png"]


def test_remote_download_disables_redirect_following(monkeypatch, tmp_path):
    """requests must be told not to follow redirects; is_redirect alone never fires otherwise."""
    provider = load_provider(monkeypatch)
    monkeypatch.setattr(provider, "_source_url_is_safe", lambda url: True)
    captured = {}

    class Response:
        status_code = 200
        is_redirect = False
        is_permanent_redirect = False
        history = []

        def iter_content(self, chunk_size):
            yield b"\x89PNG\r\n\x1a\n" + b"0" * 16

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_get(url, *args, **kwargs):
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr(provider.requests, "get", fake_get)
    provider._download_remote_image("https://images.example/a.png", tmp_path / "a")
    assert captured.get("allow_redirects") is False


def test_remote_download_rejects_redirect_history_and_3xx(monkeypatch, tmp_path):
    """A response that was reached via redirects, or is itself a 3xx, is refused."""
    provider = load_provider(monkeypatch)
    monkeypatch.setattr(provider, "_source_url_is_safe", lambda url: True)

    def make(status, history):
        class Response:
            status_code = status
            is_redirect = False
            is_permanent_redirect = False

            def iter_content(self, chunk_size):
                raise AssertionError("must not be streamed")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        Response.history = history
        return Response()

    for status, history in ((200, [object()]), (302, [])):
        monkeypatch.setattr(provider.requests, "get", lambda *a, _r=make(status, history), **k: _r)
        with pytest.raises(ValueError, match="may not redirect"):
            provider._download_remote_image("https://images.example/r.png", tmp_path / "r")


def test_remote_download_rejects_http_scheme(monkeypatch, tmp_path):
    """Plain HTTP is refused even for a public address (HTTPS-only policy)."""
    provider = load_provider(monkeypatch)
    monkeypatch.setattr(provider, "is_safe_url", None)
    fetched = []
    monkeypatch.setattr(
        provider.requests, "get", lambda *a, **k: fetched.append(a) or (_ for _ in ()).throw(AssertionError)
    )
    with pytest.raises(ValueError, match="private, internal or blocked"):
        provider._download_remote_image("http://8.8.8.8/x.png", tmp_path / "h")
    assert fetched == []


def test_remote_download_enforces_byte_cap(monkeypatch, tmp_path):
    """An oversized stream is aborted before the body is buffered in full."""
    provider = load_provider(monkeypatch)
    monkeypatch.setattr(provider, "_source_url_is_safe", lambda url: True)

    class Response:
        status_code = 200
        is_redirect = False
        is_permanent_redirect = False
        history = []

        def iter_content(self, chunk_size):
            for _ in range(6):
                yield b"x" * (10 * 1024 * 1024)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(provider.requests, "get", lambda *a, **k: Response())
    with pytest.raises(ValueError, match="byte limit"):
        provider._download_remote_image("https://images.example/big.png", tmp_path / "big")


def test_remote_download_rejects_non_image_body(monkeypatch, tmp_path):
    """A public URL that returns a non-image body is rejected by magic-byte sniffing."""
    provider = load_provider(monkeypatch)
    monkeypatch.setattr(provider, "_source_url_is_safe", lambda url: True)

    class Response:
        status_code = 200
        is_redirect = False
        is_permanent_redirect = False
        history = []

        def iter_content(self, chunk_size):
            yield b"<html>not an image</html>"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(provider.requests, "get", lambda *a, **k: Response())
    with pytest.raises(ValueError, match="not a supported image"):
        provider._download_remote_image("https://images.example/page.html", tmp_path / "p")


def _load_manifest():
    """Parse the plugin manifest with a real YAML parser (declared test dependency)."""
    import yaml

    return yaml.safe_load((ROOT / "src" / "hermes_mai_image" / "plugin.yaml").read_text())


def test_manifest_version_matches_project_version():
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    manifest = _load_manifest()

    assert manifest["version"] == project["project"]["version"]

def test_manifest_parses_with_required_catalog_fields():
    manifest = _load_manifest()
    assert {"name", "version", "description", "author", "license", "requires_hermes", "capabilities"} <= manifest.keys()
    assert manifest["author"] == "joescars"
    assert manifest.get("kind") != "backend"
    assert manifest["capabilities"] == {
        "provides_tools": [],
        "provides_hooks": [],
        "provides_middleware": [],
    }

def test_setting_uses_hermes_secret_without_consulting_environment(monkeypatch):
    secret_scope = ModuleType("agent.secret_scope")
    secret_scope.get_secret = lambda name: "scoped-key" if name == "MAI_FOUNDRY_API_KEY" else None
    monkeypatch.setitem(sys.modules, "agent.secret_scope", secret_scope)
    provider = load_provider(monkeypatch)

    def environment_must_not_be_consulted(*args, **kwargs):
        raise AssertionError("environment fallback was consulted")

    monkeypatch.setattr(provider.os.environ, "get", environment_must_not_be_consulted)
    assert provider._secret(provider.API_KEY_ENV) == "scoped-key"

def test_missing_scoped_secret_does_not_fall_back_to_environment_in_hermes(monkeypatch):
    secret_scope = ModuleType("agent.secret_scope")
    secret_scope.get_secret = lambda name: None
    monkeypatch.setitem(sys.modules, "agent.secret_scope", secret_scope)
    monkeypatch.setenv("MAI_FOUNDRY_API_KEY", "other-profile-key")
    provider = load_provider(monkeypatch)
    assert provider._secret(provider.API_KEY_ENV) == ""


def test_manifest_declares_endpoint_non_secret_and_optional_settings():
    manifest = _load_manifest()
    schema = manifest["config_schema"]
    assert schema["MAI_FOUNDRY_API_KEY"]["type"] == "secret"
    assert schema["MAI_FOUNDRY_ENDPOINT"]["type"] == "string"
    for name in ("MAI_IMAGE_MODEL", "MAI_FOUNDRY_MODELS_PATH"):
        assert schema[name]["type"] == "string" and schema[name]["required"] is False
        assert name in manifest["optional_env"]


def test_non_secret_settings_prefer_config_over_environment(monkeypatch):
    provider = load_provider(monkeypatch)
    monkeypatch.setenv("MAI_FOUNDRY_ENDPOINT", "https://env.example")
    monkeypatch.setattr(provider, "_load_image_gen_config", lambda: {"image_gen": {"azure-mai": {"endpoint": "https://cfg.example/", "models_path": "/m"}}})
    assert provider._endpoint() == "https://cfg.example"
    assert provider._setting(provider.DISCOVERY_PATH_ENV, "models_path", "/models") == "/m"
    monkeypatch.setattr(provider, "_load_image_gen_config", lambda: {})
    assert provider._endpoint() == "https://env.example"


def test_setting_falls_back_to_environment_without_hermes(monkeypatch):
    monkeypatch.delenv("MAI_FOUNDRY_API_KEY", raising=False)
    provider = load_provider(monkeypatch)
    monkeypatch.setenv("MAI_FOUNDRY_API_KEY", "environment-key")
    assert provider._secret(provider.API_KEY_ENV) == "environment-key"


def test_ssrf_guard_blocks_private_and_metadata_addresses(monkeypatch):
    """The self-contained guard must reject loopback, private and metadata addresses."""
    provider = load_provider(monkeypatch)
    monkeypatch.setattr(provider, "is_safe_url", None)

    for blocked in [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1/x.png",
        "http://localhost/x.png",
        "https://10.0.0.5/x.png",
        "http://192.168.1.10/x.png",
        "http://[::1]/x.png",
        "ftp://example.com/x.png",
        "http://8.8.8.8/x.png",
    ]:
        assert provider._source_url_is_safe(blocked) is False, blocked

    assert provider._source_url_is_safe("https://8.8.8.8/x.png") is True


def test_ssrf_guard_fails_closed_when_hermes_policy_raises(monkeypatch):
    """A broken Hermes policy must deny, never silently allow."""
    provider = load_provider(monkeypatch)

    def boom(url):
        raise RuntimeError("policy unavailable")

    monkeypatch.setattr(provider, "is_safe_url", boom)
    assert provider._source_url_is_safe("https://images.example/a.png") is False


def _capture_size(monkeypatch, model, aspect, env=None, config=None):
    provider = load_provider(monkeypatch)
    monkeypatch.delenv("MAI_IMAGE_SIZE_PROFILE", raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(provider, "_load_image_gen_config", lambda: config or {})
    calls = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"png").decode()}]}

    def post(url, **kwargs):
        calls.update(kwargs)
        return Response()

    monkeypatch.setattr(provider.requests, "post", post)
    provider.MAIImageProvider(api_key="k", endpoint="https://e.services.ai.azure.com", model=model).generate("p", aspect)
    return calls["json"]["width"], calls["json"]["height"]


@pytest.mark.parametrize("model,aspect,expected", [
    ("MAI-Image-2.5", "landscape", (1248, 832)),
    ("MAI-Image-2.5-Pro", "portrait", (832, 1248)),
    ("my-deployment", "square", (1024, 1024)),
    ("MAI-Image-2.6", "landscape", (1824, 1216)),
    ("mai-image-2-6-flash", "portrait", (1216, 1824)),
    ("MAI-Image-2.6", "square", (1536, 1536)),
])
def test_sizes_are_model_aware(monkeypatch, model, aspect, expected):
    assert _capture_size(monkeypatch, model, aspect) == expected


def test_size_profile_env_override(monkeypatch):
    assert _capture_size(monkeypatch, "prod-deploy", "landscape", env={"MAI_IMAGE_SIZE_PROFILE": "2.6"}) == (1824, 1216)
    assert _capture_size(monkeypatch, "MAI-Image-2.6", "landscape", env={"MAI_IMAGE_SIZE_PROFILE": "2.5"}) == (1248, 832)


def test_size_profile_config_override(monkeypatch):
    cfg = {"image_gen": {"azure-mai": {"size_profile": "2.6"}}}
    assert _capture_size(monkeypatch, "prod-deploy", "portrait", config=cfg) == (1216, 1824)


def test_all_sizes_within_limits(monkeypatch):
    provider = load_provider(monkeypatch)
    for table, limit in ((provider.SIZES_2_5, 1048576), (provider.SIZES_2_6, 2359296)):
        for w, h in table.values():
            assert w >= 768 and h >= 768 and w * h <= limit


def _fake_response(status, body=None, text=""):
    class Response:
        status_code = status
        headers = {"api-key": "SECRET-KEY"}

        def __init__(self):
            self.text = text

        def json(self):
            if body is None:
                raise ValueError("not json")
            return body

    return Response()


def test_http_400_is_request_rejected_not_deployment(monkeypatch):
    provider = load_provider(monkeypatch)
    msg = provider._http_error_message(
        _fake_response(400, {"error": {"code": "invalid_size", "message": "bad width"}}), "dep"
    )
    assert "equest rejected by MAI" in msg
    assert "invalid_size" in msg and "bad width" in msg
    assert "768" in msg and "PNG" in msg
    assert "not found" not in msg


def test_http_404_mentions_deployment_and_endpoint(monkeypatch):
    provider = load_provider(monkeypatch)
    msg = provider._http_error_message(_fake_response(404, {"error": {"message": "x"}}), "my-dep")
    assert "my-dep" in msg and "exact deployment name" in msg and "endpoint" in msg


@pytest.mark.parametrize("status", [401, 403])
def test_http_auth_errors_do_not_leak_key(monkeypatch, status):
    provider = load_provider(monkeypatch)
    msg = provider._http_error_message(_fake_response(status, {"error": {"message": "denied"}}), "dep")
    assert "authentication" in msg.lower() and "MAI_FOUNDRY_API_KEY" in msg
    assert "SECRET-KEY" not in msg


def test_http_429_mentions_quota_tiers(monkeypatch):
    provider = load_provider(monkeypatch)
    msg = provider._http_error_message(_fake_response(429, {"error": {"message": "slow"}}), "dep")
    assert "rate limit" in msg.lower() and "tier" in msg.lower()


def test_html_body_is_truncated(monkeypatch):
    provider = load_provider(monkeypatch)
    html = "<html>" + "x" * 5000 + "</html>"
    msg = provider._http_error_message(_fake_response(400, None, html), "dep")
    assert "equest rejected by MAI" in msg
    assert len(msg) < 1200
    assert "<html>" not in msg


def test_request_deadline_resolution(monkeypatch):
    provider = load_provider(monkeypatch)
    monkeypatch.delenv("MAI_REQUEST_DEADLINE", raising=False)
    monkeypatch.setattr(provider, "_plugin_config", lambda: {})
    assert provider._request_deadline_seconds() == float(provider.DEFAULT_REQUEST_DEADLINE)
    monkeypatch.setattr(provider, "_plugin_config", lambda: {"request_deadline": "42"})
    assert provider._request_deadline_seconds() == 42.0


def _multi_source_provider(provider):
    return provider.MAIImageProvider(
        api_key="key", endpoint="https://example.services.ai.azure.com", model="deployment"
    )


def _write_sources(tmp_path, count):
    paths = []
    for index in range(count):
        path = tmp_path / f"ref{index}.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([index]))
        paths.append(str(path))
    return paths


def test_capabilities_allow_five_reference_images(monkeypatch):
    provider = load_provider(monkeypatch)
    assert _multi_source_provider(provider).capabilities()["max_reference_images"] == 5


def test_edit_sends_repeated_image_fields_in_order(monkeypatch, tmp_path):
    provider = load_provider(monkeypatch)
    paths = _write_sources(tmp_path, 5)
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
    result = _multi_source_provider(provider).generate(
        "combine", "square", image_url=paths[0], reference_image_urls=paths[1:]
    )

    assert result["success"] is True
    files = calls["kwargs"]["files"]
    assert isinstance(files, list)
    assert [field for field, _ in files] == ["image"] * 5
    assert [item[0] for _, item in files] == [f"ref{i}.png" for i in range(5)]
    assert all(item[2] == "image/png" for _, item in files)
    assert calls["kwargs"]["data"] == {"model": "deployment", "prompt": "combine"}


def test_six_sources_rejected_before_any_network_call(monkeypatch, tmp_path):
    provider = load_provider(monkeypatch)
    paths = _write_sources(tmp_path, 6)
    called = []
    monkeypatch.setattr(provider.requests, "post", lambda *a, **k: called.append(1))
    monkeypatch.setattr(provider, "_load_source", lambda *a: called.append("load"))
    result = _multi_source_provider(provider).generate(
        "edit", "square", image_url=paths[0], reference_image_urls=paths[1:]
    )

    assert result["success"] is False
    assert result["error_type"] == "too_many_references"
    assert "5" in result["error"]
    assert called == []


def test_total_upload_cap_rejects_before_posting(monkeypatch, tmp_path):
    provider = load_provider(monkeypatch)
    paths = _write_sources(tmp_path, 3)
    monkeypatch.setattr(provider, "MAX_TOTAL_UPLOAD_BYTES", 20)  # each source is 9 bytes
    called = []
    monkeypatch.setattr(provider.requests, "post", lambda *a, **k: called.append(1))
    result = _multi_source_provider(provider).generate(
        "edit", "square", image_url=paths[0], reference_image_urls=paths[1:]
    )

    assert result["success"] is False
    assert "total" in result["error"].lower()
    assert called == []


def test_each_source_still_validated_in_multi_edit(monkeypatch, tmp_path):
    provider = load_provider(monkeypatch)
    good = _write_sources(tmp_path, 1)[0]
    bad = tmp_path / "bad.gif"
    bad.write_bytes(b"GIF89a....")
    called = []
    monkeypatch.setattr(provider.requests, "post", lambda *a, **k: called.append(1))
    result = _multi_source_provider(provider).generate(
        "edit", "square", image_url=good, reference_image_urls=[str(bad)]
    )

    assert result["success"] is False
    assert result["error_type"] == "invalid_input"
    assert called == []
