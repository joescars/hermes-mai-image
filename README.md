# Hermes Microsoft Foundry MAI Image Plugin

Standalone Hermes Agent image-generation backend for Microsoft Foundry MAI image models.

## Quick Start

### 1. Get your Microsoft Foundry values

From the Foundry resource's **Keys and Endpoint** page, collect:

- An API key.
- The resource endpoint, for example:

  ```text
  https://your-resource.services.ai.azure.com
  ```

- The deployment name assigned to your MAI image model.

Use the resource root as the endpoint. Do **not** append `/mai/v1`; the plugin adds that path. The deployment name is the value sent as the API `model` field and may differ from the catalog model name.

### 2. Install the plugin into Hermes

For a standard Hermes installation:

```bash
/usr/local/lib/hermes-agent/venv/bin/pip install --upgrade \\
  "git+https://github.com/joescars/hermes-mai-image.git"
```

Verify the package is visible:

```bash
hermes plugins list
```

### 3. Enable the plugin and image generation

```bash
hermes plugins enable azure-mai-image
hermes tools enable image_gen
```

Verify:

```bash
hermes plugins list
hermes tools list
```

You should see `azure-mai-image` enabled and:

```text
✓ enabled  image_gen  🎨 Image Generation
```

### 4. Restart Hermes processes

Plugins load when a Hermes process starts. No operating-system reboot is needed, but restart long-running processes after installation or updates:

```bash
hermes gateway restart
hermes dashboard --stop
hermes dashboard
```

### 5. Configure from the CLI

Run the interactive tool setup:

```bash
hermes tools
```

Choose **Image Generation → Microsoft Foundry MAI**, then enter the API key, endpoint, and deployment/model when prompted.

### 6. Configure from the Hermes Web UI

1. Run `hermes dashboard` and authenticate.
2. Open **Tools → Image Generation**.
3. Select **Microsoft Foundry MAI**.
4. Enter the API key and resource endpoint.
5. Save the provider.
6. Select the MAI deployment/model if the model picker is shown.

The Web UI uses the provider's setup schema and saves credentials in the active profile's `.env`. It supports these fields:

```text
MAI_FOUNDRY_API_KEY
MAI_FOUNDRY_ENDPOINT
```

If the provider does not appear, restart the dashboard after installing the package. A browser refresh alone does not reload Python plugins.

### Updating Hermes later

Hermes updates do not normally change this GitHub-installed plugin or its configuration. The API key, endpoint, deployment selection, and plugin-enabled state are stored separately in the active Hermes profile.

After a normal Hermes update, verify the plugin:

```bash
hermes plugins list
```

If `azure-mai-image` is still listed as enabled, restart Hermes processes:

```bash
hermes gateway restart
hermes dashboard --stop
hermes dashboard
```

If the Hermes update recreated or replaced its Python virtual environment, reinstall the plugin into the new Hermes environment:

```bash
/usr/local/lib/hermes-agent/venv/bin/pip install --upgrade \\
  "git+https://github.com/joescars/hermes-mai-image.git"
```

Then enable it again if necessary and restart Hermes:

```bash
hermes plugins enable azure-mai-image
hermes gateway restart
```

You do not need to re-enter the API key unless the active Hermes profile changed or its `.env` was intentionally reset.

### 7. Set the deployment explicitly when needed

For most Foundry deployments, set the exact deployment name in the active Hermes profile's `.env`:

```dotenv
MAI_FOUNDRY_API_KEY=your-foundry-key
MAI_FOUNDRY_ENDPOINT=https://your-resource.services.ai.azure.com
MAI_IMAGE_MODEL=your-deployment-name
```

Never put API keys in `config.yaml`, chat messages, or Git. `MAI_IMAGE_MODEL` defaults to `MAI-Image-2.5` if omitted, but the exact deployment name is recommended.

### 8. Test generation

```bash
hermes chat -q "Generate an image of a red fox sitting in a snowy forest at sunrise."
```

Generated images are saved under `$HERMES_HOME/cache/images/`.

### 9. Test editing

Provide a local PNG or JPEG path in the prompt:

```bash
hermes chat -q "Edit /absolute/path/to/source.png: turn the scene into a nighttime scene with moonlight."
```

The edit request uses MAI's `/mai/v1/images/edits` multipart endpoint.

## Features

- API-key authentication with the Foundry `api-key` header
- Live model discovery through the resource `/models` endpoint, with a documented MAI fallback catalog
- Text-to-image generation through `/mai/v1/images/generations`
- Image-to-image editing through `/mai/v1/images/edits`
- Landscape, square, and portrait dimensions
- Stable local output files under `$HERMES_HOME/cache/images/`
- Installable from GitHub now and publishable to PyPI later

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

The plugin accepts a local file path or HTTP(S) URL as `image_url`/`reference_image_urls`. Exactly one source image is supported for edits; additional references are rejected with a clear error. Generated base64 output is cached locally by Hermes.

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
