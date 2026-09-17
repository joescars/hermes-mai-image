"""Microsoft Foundry MAI image-generation backend for Hermes Agent.

The MAI API is not OpenAI-compatible: it uses ``/mai/v1`` endpoints,
``api-key`` authentication, JSON generation requests, and multipart edits.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def _load_source(source: str):
    """Return a named file tuple accepted by requests' multipart encoder."""
    if source.startswith(("http://", "https://")):
        response = requests.get(source, timeout=60)
        response.raise_for_status()
        name = source.split("?", 1)[0].rsplit("/", 1)[-1] or "reference.png"
        return name, response.content, response.headers.get("Content-Type", "image/png")
    path = Path(source).expanduser()
    if not path.is_file():
        raise FileNotFoundError(source)
    content_type = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return path.name, path.read_bytes(), content_type


def _image_result(body: Any, *, provider: str, model: str, prompt: str, aspect: str):
    entries = body.get("data") if isinstance(body, dict) else None
    first = entries[0] if isinstance(entries, list) and entries else None
    if not isinstance(first, dict):
        return _error(provider, prompt, aspect, "MAI returned no image data", "empty_response", model)
    try:
        if first.get("b64_json"):
            image = str(save_b64_image(first["b64_json"], prefix="mai", extension="png"))
        elif first.get("url"):
            image = str(save_url_image(first["url"], prefix="mai"))
        else:
            return _error(provider, prompt, aspect, "MAI response contained neither b64_json nor URL", "empty_response", model)
    except Exception as exc:
        return _error(provider, prompt, aspect, f"Could not save MAI image: {exc}", "io_error", model)
    return success_response(
        image=image,
        model=model,
        prompt=prompt,
        aspect_ratio=aspect,
        provider=provider,
        modality="image",
    )


class MAIImageProvider(ImageGenProvider):
    provider_id = "azure-mai"
    label = "Microsoft Foundry MAI"

    def __init__(self, api_key: Optional[str] = None, endpoint: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key if api_key is not None else _setting(API_KEY_ENV)
        self.endpoint = _endpoint(endpoint)
        self.model = model or _setting(MODEL_ENV, DEFAULT_MODEL)
        self._models: Optional[List[Dict[str, str]]] = None

    @property
    def name(self) -> str:
        return self.provider_id

    @property
    def display_name(self) -> str:
        return self.label

    def is_available(self) -> bool:
        return bool(self.api_key and self.endpoint)

    def list_models(self) -> List[Dict[str, str]]:
        if self._models is None:
            try:
                self._models = discover_models(self.endpoint, self.api_key)
            except Exception as exc:
                logger.debug("MAI model discovery unavailable: %s", exc)
                self._models = []
        return self._models or [{"id": model, "display": model} for model in FALLBACK_MODELS]

    def default_model(self) -> Optional[str]:
        return self.model or DEFAULT_MODEL

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
        model = str(kwargs.get("model") or self.model or DEFAULT_MODEL)
        if not prompt:
            return _error(self.name, prompt, aspect, "Prompt is required", "invalid_input", model)
        if not self.api_key or not self.endpoint:
            return _error(self.name, prompt, aspect, f"Set {API_KEY_ENV} and {ENDPOINT_ENV}", "auth_required", model)

        sources = ([image_url] if image_url else []) + list(reference_image_urls or [])
        try:
            if sources:
                name, data, content_type = _load_source(sources[0])
                response = requests.post(
                    f"{self.endpoint}/mai/v1/images/edits",
                    headers={"api-key": self.api_key},
                    data={"model": model, "prompt": prompt},
                    files={"image": (name, data, content_type)},
                    timeout=REQUEST_TIMEOUT,
                )
                modality = "image"
            else:
                width, height = SIZES.get(aspect, SIZES["square"])
                response = requests.post(
                    f"{self.endpoint}/mai/v1/images/generations",
                    headers={**_headers(self.api_key), "Content-Type": "application/json"},
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
            return _error(self.name, prompt, aspect, f"MAI request failed: {exc}", "api_error", model)
        except Exception as exc:
            logger.debug("MAI image generation failed", exc_info=True)
            return _error(self.name, prompt, aspect, f"MAI image generation failed: {exc}", "io_error", model)


def register(ctx: Any) -> None:
    ctx.register_image_gen_provider(MAIImageProvider())
