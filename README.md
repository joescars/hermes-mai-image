# Hermes Microsoft Foundry MAI Image Plugin

Standalone Hermes Agent image-generation backend for Microsoft Foundry MAI image models.

## Features

- API-key authentication with the Foundry `api-key` header
- Live model discovery through the resource `/models` endpoint, with a documented MAI fallback catalog
- Text-to-image generation through `/mai/v1/images/generations`
- Image-to-image editing through `/mai/v1/images/edits`
- Landscape, square, and portrait dimensions
- Stable local output files under `$HERMES_HOME/cache/images/`
- Installable from GitHub now and publishable to PyPI later

## Configuration

Put credentials in Hermes' `.env` file, not `config.yaml`:

```dotenv
MAI_FOUNDRY_API_KEY=your-foundry-key
MAI_FOUNDRY_ENDPOINT=https://your-resource.services.ai.azure.com
MAI_IMAGE_MODEL=your-deployment-name
```

`MAI_IMAGE_MODEL` is optional and defaults to `MAI-Image-2.5`. The value is the deployment name assigned in Foundry, not necessarily the catalog model name.

Optional discovery override:

```dotenv
MAI_FOUNDRY_MODELS_PATH=/models
```

## Install from GitHub

After this repository is pushed:

```bash
pip install "git+https://github.com/<owner>/hermes-mai-image.git"
```

Restart Hermes, then select it with:

```bash
hermes tools
```

Choose **Image Generation → Microsoft Foundry MAI**. The plugin can also be selected by setting `image_gen.provider: azure-mai` in Hermes configuration through the supported Hermes configuration command.

## Install from PyPI later

Once published:

```bash
pip install hermes-mai-image
```

GitHub is the better first distribution target because it supports rapid iteration and direct issue/PR collaboration. PyPI is useful afterward for stable versioned releases and simpler installation.

## API behavior

MAI generation uses:

```text
POST {endpoint}/mai/v1/images/generations
api-key: ...
{"model": "<deployment>", "prompt": "...", "width": 1536, "height": 1024}
```

MAI editing uses multipart form data:

```text
POST {endpoint}/mai/v1/images/edits
api-key: ...
model=<deployment>
prompt=<prompt>
image=<file>
```

The plugin accepts a local file path or HTTP(S) URL as `image_url`/`reference_image_urls`. Generated base64 output is cached locally by Hermes.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest -q
```

The tests mock HTTP responses and never require an Azure key.

## Microsoft documentation

- [Deploy and use MAI image models in Microsoft Foundry](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-mai-image)
