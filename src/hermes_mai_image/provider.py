"""Microsoft Foundry MAI image-generation backend for Hermes Agent.

The MAI API is not OpenAI-compatible: it uses ``/mai/v1`` endpoints,
``api-key`` authentication, JSON generation requests, and multipart edits.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import requests

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    resolve_aspect_ratio,
    save_b64_image,
    save_url_image,
    success_response,
)

logger = logging.getLogger(__name__)

try:
    # Private core import, deliberately retained (catalog rule 9). `agent.file_safety` is not
    # listed in hermes_cli/data/plugin_api.json, so `hermes plugins validate` warns here; the
    # warning is correct about the coupling and is documented in README "Validation" and in
    # issue #7. Retained because raise_if_read_blocked is the shared chokepoint Hermes' own
    # bundled providers use for local-path input loading -- the same import appears in
    # plugins/image_gen/{xai,openai,openai-codex,krea,openrouter} and plugins/video_gen/xai --
    # and it has no public equivalent. Dropping it would remove the guard that stops a local
    # reference image from reading a credential store. Tracked upstream at
    # NousResearch/hermes-agent#155704 (request to publish a public seam for this and for
    # image fetching).
    # Documented file-safety seam: blocks reading credentials and other protected paths
    # when a local reference image is supplied. Published in the plugin API docs.
    from agent.file_safety import raise_if_read_blocked
except ImportError:  # pragma: no cover - only for standalone import outside Hermes
    def raise_if_read_blocked(_path: str) -> None:
        return

try:
    # Published plugin API (hermes_cli/data/plugin_api.json): SSRF policy used for remote
    # source images. Importing this does not trip the private-core-import admission lint.
    from tools.url_safety import is_safe_url
except ImportError:  # pragma: no cover - only for standalone import outside Hermes
    is_safe_url = None

try:
    # Catalog rule 11: declared secrets are read through Hermes' credential route so the
    # active profile is honoured (a bare os.environ read can pick up another profile's key
    # under multiplexing).
    from agent.secret_scope import get_secret
except ImportError:  # pragma: no cover - only for standalone import outside Hermes
    get_secret = None

MAX_REMOTE_IMAGE_BYTES = 50 * 1024 * 1024
REMOTE_TIMEOUT = (5, 15)
MAX_REQUEST_ATTEMPTS = 3

DEFAULT_ENDPOINT = ""
DEFAULT_MODEL = "MAI-Image-2.5"
MODEL_ENV = "MAI_IMAGE_MODEL"
API_KEY_ENV = "MAI_FOUNDRY_API_KEY"
ENDPOINT_ENV = "MAI_FOUNDRY_ENDPOINT"
DISCOVERY_PATH_ENV = "MAI_FOUNDRY_MODELS_PATH"
REQUEST_TIMEOUT = 300
DISCOVERY_TIMEOUT = 15

FALLBACK_MODELS = (
    "MAI-Image-2.6-Flash",
    "MAI-Image-2.6",
    "MAI-Image-2.5-Pro",
    "MAI-Image-2.5-Flash",
    "MAI-Image-2.5",
)
SIZES = {
    "landscape": (1536, 1024),
    "square": (1024, 1024),
    "portrait": (1024, 1536),
}


def _setting(name: str, default: str = "") -> str:
    value = get_secret(name) if get_secret is not None else None
    return (value or os.environ.get(name) or default).strip()


def _endpoint(endpoint: Optional[str] = None) -> str:
    return (endpoint or _setting(ENDPOINT_ENV, DEFAULT_ENDPOINT)).rstrip("/")


def _headers(api_key: str) -> Dict[str, str]:
    return {"api-key": api_key, "Accept": "application/json"}


def _load_image_gen_config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config
        config = load_config()
        return config if isinstance(config, dict) else {}
    except Exception:
        return {}


def _configured_model() -> Optional[str]:
    config = _load_image_gen_config()
    section = config.get("image_gen") if isinstance(config, dict) else None
    if not isinstance(section, dict):
        return None
    scoped = section.get("azure-mai")
    if isinstance(scoped, dict) and str(scoped.get("model") or "").strip():
        return str(scoped["model"]).strip()
    model = section.get("model")
    return str(model).strip() if isinstance(model, str) and model.strip() else None


def _resolve_model(explicit: Optional[str], override: Optional[str]) -> str:
    return str(explicit or override or _setting(MODEL_ENV) or _configured_model() or DEFAULT_MODEL)


def _http_error_message(response: Any, model: str) -> str:
    status = getattr(response, "status_code", None)
    try:
        body = response.json()
        detail = body.get("error", {}).get("message") if isinstance(body, dict) else None
    except Exception:
        detail = None
    detail = str(detail or getattr(response, "text", "") or "request rejected").strip()
    if status in (400, 404):
        return (
            f"MAI deployment '{model}' was not found or is not available on this Foundry resource "
            f"({status}). Use the exact deployment name from Microsoft Foundry. Details: {detail}"
        )
    return f"MAI request failed ({status or 'unknown status'}): {detail}"


def _error(provider: str, prompt: str, aspect: str, message: str, error_type: str, model: str = ""):
    return error_response(
        error=message,
        error_type=error_type,
        provider=provider,
        model=model,
        prompt=prompt,
        aspect_ratio=aspect,
    )


def discover_models(endpoint: str, api_key: str) -> List[Dict[str, str]]:
    """Best-effort discovery from the resource's standard ``/models`` route.

    MAI's image-generation endpoint itself does not require discovery. Some
    Foundry resources expose ``/models`` and some do not, so callers must use
    the fallback catalog when this returns an empty list.
    """
    path = _setting(DISCOVERY_PATH_ENV, "/models")
    if not path.startswith("/"):
        path = "/" + path
    try:
        response = requests.get(
            f"{endpoint.rstrip('/')}{path}",
            headers=_headers(api_key),
            timeout=DISCOVERY_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        # Discovery is best-effort: a missing or unreachable /models route falls back to the
        # built-in MAI catalog rather than failing the request.
        logger.debug("MAI model discovery failed: %s", exc)
        return []
    try:
        body = response.json()
    except ValueError:  # includes json.JSONDecodeError; a non-JSON body must not raise
        logger.debug("MAI model discovery returned a non-JSON body")
        return []
    entries = body.get("data") if isinstance(body, dict) else None
    if entries is None and isinstance(body, dict):
        entries = body.get("models")
    if not isinstance(entries, list):
        return []
    models = []
    for entry in entries:
        if isinstance(entry, str):
            model_id, display = entry, entry
        elif isinstance(entry, dict):
            model_id = entry.get("id") or entry.get("name") or entry.get("model")
            display = entry.get("display_name") or entry.get("name") or model_id
        else:
            continue
        if not model_id:
            continue
        model_id = str(model_id)
        # /models is not image-specific: it can list chat and embedding deployments. Label
        # ids we cannot recognise as MAI/image so a user does not pick a chat deployment.
        label = str(display)
        if "image" not in model_id.lower() and not model_id.lower().startswith("mai"):
            label += " (unverified: may not be an image model)"
        models.append({"id": model_id, "display": label})
    return models


def _validate_image_bytes(data: bytes, content_type: str) -> str:
    """Reject non-images and return the authoritative MIME type from magic bytes."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError(f"Source is not a supported image (received {content_type or 'unknown type'})")


def _retry_delay(response: Any, attempt: int) -> float:
    """Seconds to wait before retry ``attempt`` (1-based): ``Retry-After``, else 2**(attempt-1)."""
    headers = getattr(response, "headers", None) or {}
    raw = headers.get("Retry-After") if hasattr(headers, "get") else None
    try:
        if raw is not None:
            return max(0.0, min(float(raw), 60.0))
    except (TypeError, ValueError):
        pass
    return float(2 ** (attempt - 1))


def _post_with_retry(url: str, **kwargs: Any):
    """``requests.post`` with bounded retry on 429/5xx; returns the final response."""
    response = None
    for attempt in range(1, MAX_REQUEST_ATTEMPTS + 1):
        response = requests.post(url, **kwargs)
        status = getattr(response, "status_code", None)
        retryable = isinstance(status, int) and (status == 429 or 500 <= status < 600)
        if not retryable or attempt == MAX_REQUEST_ATTEMPTS:
            return response
        time.sleep(_retry_delay(response, attempt))
    return response


def _address_is_public(host: str) -> bool:
    """True when ``host`` resolves only to public, non-reserved addresses.

    Self-contained SSRF guard used when Hermes' own policy is unavailable. Mirrors the
    intent of ``tools.url_safety``: reject loopback, private, link-local, reserved and
    multicast ranges, plus the cloud metadata address.
    """
    import ipaddress
    import socket

    if not host:
        return False
    if host.lower() in {"localhost", "metadata.google.internal"}:
        return False
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if not address.is_global or address.is_multicast:
            return False
    return True


def _source_url_is_safe(url: str) -> bool:
    """SSRF policy for remote source images: https only, public address required."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        return False
    if is_safe_url is not None:
        try:
            return bool(is_safe_url(url))
        except Exception:  # noqa: BLE001 - treat any policy failure as unsafe
            return False
    return _address_is_public(parts.hostname or "")


def _download_remote_image(url: str, destination: Path) -> Path:
    """Download a remote source image safely.

    Validation goes through Hermes' published URL-safety helper
    (``tools.url_safety.is_safe_url``, listed in ``plugin_api.json``), which blocks
    private/internal addresses, cloud metadata endpoints and the link-local range. The fetch
    itself uses ``requests`` with streaming and a byte cap so an oversized body is aborted
    before it is buffered.

    Redirects are refused outright rather than followed and re-validated: following a
    redirect is exactly how a public URL can bounce into an internal address, and a source
    image never legitimately needs one. The response's Location is never dereferenced.

    Remote sources are deliberately NOT restricted to the configured Foundry host: a user
    legitimately edits from any public image URL, so the policy is "public address only",
    not "one allowlisted host".
    """
    if not _source_url_is_safe(url):
        raise ValueError("Remote image URL resolves to a private, internal or blocked address")

    total = 0
    with requests.get(url, stream=True, timeout=REMOTE_TIMEOUT, allow_redirects=False) as response:
        if (
            response.is_redirect
            or response.is_permanent_redirect
            or response.history
            or 300 <= response.status_code < 400
        ):
            raise ValueError("Remote image may not redirect")
        if response.status_code >= 400:
            raise ValueError(f"Image download failed with HTTP {response.status_code}")
        with destination.open("wb") as output:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_REMOTE_IMAGE_BYTES:
                    raise ValueError(f"Image exceeds {MAX_REMOTE_IMAGE_BYTES} byte limit")
                output.write(chunk)
    _validate_image_bytes(destination.read_bytes()[:16], "")
    return destination


def _load_source(source: str):
    """Return a named file tuple accepted by requests' multipart encoder."""
    source = source.strip()
    if source.startswith(("http://", "https://")):
        filename = Path(urlsplit(source).path).name or "reference.png"
        suffix = Path(filename).suffix or ".img"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            temp_path = Path(handle.name)
        try:
            _download_remote_image(source, temp_path)
            data = temp_path.read_bytes()
        finally:
            temp_path.unlink(missing_ok=True)
        content_type = mimetypes.guess_type(filename)[0] or "image/png"
        content_type = _validate_image_bytes(data, content_type)
        return filename, data, content_type

    path = Path(source).expanduser()
    raise_if_read_blocked(str(path))  # credential-read guard before local bytes (see import site)
    if not path.is_file():
        raise FileNotFoundError(source)
    data = path.read_bytes()
    if len(data) > MAX_REMOTE_IMAGE_BYTES:
        raise ValueError(f"Image exceeds {MAX_REMOTE_IMAGE_BYTES} byte limit")
    content_type = mimetypes.guess_type(path.name)[0] or "image/png"
    content_type = _validate_image_bytes(data, content_type)
    return path.name, data, content_type


def _image_result(body: Any, *, provider: str, model: str, prompt: str, aspect: str):
    entries = body.get("data") if isinstance(body, dict) else None
    first = entries[0] if isinstance(entries, list) and entries else None
    if not isinstance(first, dict):
        return _error(provider, prompt, aspect, "MAI returned no image data", "empty_response", model)
    try:
        if first.get("b64_json"):
            saved_path = save_b64_image(first["b64_json"], prefix="mai", extension="png")
        elif first.get("url"):
            saved_path = save_url_image(first["url"], prefix="mai")
        else:
            return _error(provider, prompt, aspect, "MAI response contained neither b64_json nor URL", "empty_response", model)
        host_image = str(saved_path)
        image = saved_path.resolve().as_uri()
    except Exception as exc:
        return _error(provider, prompt, aspect, f"Could not save MAI image: {exc}", "io_error", model)
    return success_response(
        image=image,
        model=model,
        prompt=prompt,
        aspect_ratio=aspect,
        provider=provider,
        modality="image",
        extra={"host_image": host_image},
    )


class MAIImageProvider(ImageGenProvider):
    provider_id = "azure-mai"
    label = "Microsoft Foundry MAI"

    def __init__(self, api_key: Optional[str] = None, endpoint: Optional[str] = None, model: Optional[str] = None):
        self.api_key_override = api_key
        self.endpoint_override = endpoint
        self.model_override = model
        self._models: Optional[List[Dict[str, str]]] = None

    def _credentials(self) -> tuple[str, str]:
        return (
            self.api_key_override if self.api_key_override is not None else _setting(API_KEY_ENV),
            _endpoint(self.endpoint_override),
        )

    @property
    def name(self) -> str:
        return self.provider_id

    @property
    def display_name(self) -> str:
        return self.label

    def is_available(self) -> bool:
        api_key, endpoint = self._credentials()
        return bool(api_key and endpoint)

    def list_models(self) -> List[Dict[str, str]]:
        api_key, endpoint = self._credentials()
        if self._models is None:
            try:
                self._models = discover_models(endpoint, api_key)
            except Exception as exc:
                logger.debug("MAI model discovery unavailable: %s", exc)
                self._models = []
        return self._models or [{"id": model, "display": model} for model in FALLBACK_MODELS]

    def default_model(self) -> Optional[str]:
        return _resolve_model(None, self.model_override)

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.label,
            "badge": "paid",
            "tag": "Microsoft MAI image models through Azure AI Foundry; generation and editing",
            "env_vars": [
                {"key": API_KEY_ENV, "prompt": "Microsoft Foundry API key", "url": "https://ai.azure.com"},
                {"key": ENDPOINT_ENV, "prompt": "Microsoft Foundry resource endpoint", "url": "https://ai.azure.com"},
            ],
        }

    def capabilities(self) -> Dict[str, Any]:
        return {"modalities": ["text", "image"], "max_reference_images": 1}

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        *,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)
        api_key, endpoint = self._credentials()
        model = _resolve_model(kwargs.get("model"), self.model_override)
        if not prompt:
            return _error(self.name, prompt, aspect, "Prompt is required", "invalid_input", model)
        if not api_key or not endpoint:
            return _error(self.name, prompt, aspect, f"Set {API_KEY_ENV} and {ENDPOINT_ENV}", "auth_required", model)

        sources = ([image_url] if image_url else []) + list(reference_image_urls or [])
        try:
            if len(sources) > 1:
                return _error(
                    self.name, prompt, aspect,
                    "Microsoft Foundry MAI image editing supports at most one "
                    "source image. Reduce reference images to exactly one.",
                    "too_many_references", model)
            if sources:
                name, data, content_type = _load_source(sources[0])
                response = _post_with_retry(
                    f"{endpoint}/mai/v1/images/edits",
                    headers={"api-key": api_key},
                    data={"model": model, "prompt": prompt},
                    files={"image": (name, data, content_type)},
                    timeout=REQUEST_TIMEOUT,
                )
                modality = "image"
            else:
                width, height = SIZES.get(aspect, SIZES["square"])
                response = _post_with_retry(
                    f"{endpoint}/mai/v1/images/generations",
                    headers={**_headers(api_key), "Content-Type": "application/json"},
                    json={"model": model, "prompt": prompt, "width": width, "height": height},
                    timeout=REQUEST_TIMEOUT,
                )
                modality = "text"
            response.raise_for_status()
            result = _image_result(response.json(), provider=self.name, model=model, prompt=prompt, aspect=aspect)
            if result.get("success"):
                result["modality"] = modality
            return result
        except requests.RequestException as exc:
            logger.debug("MAI request failed", exc_info=True)
            response = getattr(exc, "response", None)
            message = _http_error_message(response, model) if response is not None else f"MAI request failed: {exc}"
            return _error(self.name, prompt, aspect, message, "api_error", model)
        except Exception as exc:
            logger.debug("MAI image generation failed", exc_info=True)
            return _error(self.name, prompt, aspect, f"MAI image generation failed: {exc}", "io_error", model)


def register(ctx: Any) -> None:
    ctx.register_image_gen_provider(MAIImageProvider())
