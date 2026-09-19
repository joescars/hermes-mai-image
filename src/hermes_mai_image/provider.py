"""Microsoft Foundry MAI image-generation backend for Hermes Agent.

The MAI API is not OpenAI-compatible: it uses ``/mai/v1`` endpoints,
``api-key`` authentication, JSON generation requests, and multipart edits.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import os
import tempfile
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
    from agent.file_safety import raise_if_read_blocked
except ImportError:  # pragma: no cover - only for standalone import outside Hermes
    def raise_if_read_blocked(_path: str) -> None:
        return

MAX_REMOTE_IMAGE_BYTES = 50 * 1024 * 1024

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
    return (os.environ.get(name) or default).strip()


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
    response = requests.get(
        f"{endpoint.rstrip('/')}{path}",
        headers=_headers(api_key),
        timeout=DISCOVERY_TIMEOUT,
    )
    response.raise_for_status()
    body = response.json()
    entries = body.get("data") if isinstance(body, dict) else None
    if entries is None and isinstance(body, dict):
        entries = body.get("models")
    if not isinstance(entries, list):
        return []
    models = []
    for entry in entries:
        if isinstance(entry, str):
            models.append({"id": entry, "display": entry})
        elif isinstance(entry, dict):
            model_id = entry.get("id") or entry.get("name") or entry.get("model")
            if model_id:
                models.append({"id": str(model_id), "display": str(entry.get("display_name") or entry.get("name") or model_id)})
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
def _download_remote_image(url: str, destination: Path) -> Path:
    """Use Hermes' SSRF-safe, redirect-checked, bounded image downloader."""
    from tools.vision_tools import _download_image

    return asyncio.run(_download_image(url, destination))


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
    raise_if_read_blocked(str(path))
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
                response = requests.post(
                    f"{endpoint}/mai/v1/images/edits",
                    headers={"api-key": api_key},
                    data={"model": model, "prompt": prompt},
                    files={"image": (name, data, content_type)},
                    timeout=REQUEST_TIMEOUT,
                )
                modality = "image"
            else:
                width, height = SIZES.get(aspect, SIZES["square"])
                response = requests.post(
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
