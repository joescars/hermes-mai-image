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

Two paths install the same plugin. Prefer the catalog install; fall back to the Git install when you want the moving branch.

**Catalog install (recommended).** Once this plugin is listed in the Hermes plugin catalog:

```bash
hermes plugins install mai-image
```

The catalog entry pins an exact 40-character commit SHA that has been validated and reviewed for the catalog. Installs clone the repository and check out precisely that commit, so a catalog install is reproducible and does not move until you run `hermes plugins update mai-image` (which adopts a newly reviewed pin). Use this path for a stable, vetted plugin.

**Git install (tracks the branch).** To run the current development code instead of a pinned release:

```bash
/usr/local/lib/hermes-agent/venv/bin/pip install --upgrade \\
  "git+https://github.com/joescars/hermes-mai-image.git"
```

The Git install tracks the branch, so `--upgrade` picks up the newest commits. A catalog install is pinned and validated; a Git install is live and unreviewed. Use the catalog install unless you specifically want unreleased changes.

Verify the package is visible either way:

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

**Credential handling.** The plugin reads `MAI_FOUNDRY_API_KEY` and `MAI_FOUNDRY_ENDPOINT` through Hermes secret scope for the **active profile** — the values are resolved in the context of the profile that is running, so a second profile cannot pick them up accidentally. The values live in the active profile's `.env` under `$HERMES_HOME/profiles/<profile>/.env` (the default profile's `.env` sits at `$HERMES_HOME/.env`). The API key is sent to your Foundry resource in the `api-key` header on each request and is never logged, never written by the plugin anywhere else, and never included in image metadata.

Never put API keys in `config.yaml`, chat messages, or Git. `config.yaml` holds only non-secret model selection, not credentials.

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
- Installable from the Hermes plugin catalog (pinned SHA) or directly from GitHub

## Model discovery

The provider discovers the models available on your Foundry resource at runtime by calling the resource's `/models` route:

```text
GET {endpoint}/models
api-key: ***
```

The response is read for a `data` array (or a `models` array) of entries; each entry's `id`/`name`/`model` becomes the model id and `display_name`/`name` becomes the label. Discovery is best-effort — not every Foundry resource exposes `/models`, and MAI generation itself does not require it.

When discovery is unavailable, returns nothing, or fails, the provider falls back to a built-in MAI catalog:

```text
MAI-Image-2.6-Flash
MAI-Image-2.6
MAI-Image-2.5-Pro
MAI-Image-2.5-Flash
MAI-Image-2.5
```

**Overriding the discovery path.** Set `MAI_FOUNDRY_MODELS_PATH` to point discovery at a different route on the same resource. A value without a leading `/` is normalised by adding one:

```dotenv
MAI_FOUNDRY_MODELS_PATH=/models
```

The model actually sent to MAI is resolved in this order: an explicit per-request model, then `MAI_IMAGE_MODEL` from the active profile's `.env`, then the model set in `config.yaml` (`image_gen.model` or `image_gen.azure-mai.model`), then the `MAI-Image-2.5` default. The discovered list feeds the picker; it does not change which deployment name is sent unless you pick one.

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

The plugin accepts a local file path or HTTP(S) URL as `image_url`/`reference_image_urls`. Exactly one source image is supported for edits; additional references are rejected with a clear error. Generated images remain in Hermes' `$HERMES_HOME/cache/images/`. Successful results expose the WebUI-facing `file://` URI in `image` and retain the absolute cached path in `host_image` for Hermes media delivery.

## Disclosure

This section is the plugin's [catalog rule 13](https://github.com/NousResearch/hermes-agent/blob/main/plugin-catalog/README.md) disclosure — what the plugin does that you should know before installing it.

- **Network destination.** Your prompt, the optional source image (for edits), and your `MAI_FOUNDRY_API_KEY` are sent over **https** to **your own Microsoft Azure AI Foundry resource** — the endpoint you configure in `MAI_FOUNDRY_ENDPOINT`. The plugin talks to no other host. The API key travels only in the `api-key` header on requests to that resource.
- **Billing.** Every generation and edit calls your Foundry deployment and **bills your Azure subscription** for that deployment. There is **no Hermes approval prompt before spending** — the plugin does not gate on cost and does not know MAI pricing. Your Azure quota and rate limits are the only backstop.
- **Local writes.** Returned images are downloaded and written into the Hermes image cache under `$HERMES_HOME/cache/images/`. Nothing is written outside that cache and the plugin's own working directory.
- **No telemetry.** The plugin reports no usage, analytics, or diagnostics anywhere.
- **No shell commands.** It never runs a subprocess or shell command.
- **No background processes.** It makes synchronous HTTP requests and starts no daemons, timers, or watchers.
- **No self-updates.** The plugin does not fetch or replace its own files. Catalog updates arrive only through a reviewed SHA-bump in the catalog plus `hermes plugins update mai-image`.
- **No extra dependencies.** The only runtime dependency is `requests` (plus Hermes itself).

## Compatibility

- **Hermes floor:** `requires_hermes: ">=0.21.6"`. The plugin uses image-gen provider APIs that require a Hermes at or above this version.
- **Where it is declared:** in `plugin.yaml` (`requires_hermes`) inside the package, **and** in the catalog entry (`catalog/mai-image.yaml`, `requires_hermes`), so both the loader and the catalog enforce the same floor.
- **Install behaviour:** an install by catalog name refuses on an older Hermes, and `hermes plugins update` keeps an installed copy at its current pin until Hermes is updated.
- **Python:** 3.10 or newer (`requires-python = ">=3.10"`).

## Security behaviour

Source images are the one place this plugin handles untrusted input. Remote references are fetched with `requests` behind an explicit SSRF policy rather than being handed to the HTTP client unexamined. Local file reads are additionally checked against Hermes' file-safety rules.

- **Scheme allowlist.** Only `https` source references are fetched; anything else is refused.
- **Address checks.** The host is resolved and every returned address must be public; loopback, private, link-local, reserved and multicast ranges are refused, and the cloud metadata address is blocked regardless of settings. When Hermes' own `tools.url_safety` policy is importable it is used in preference to the built-in check.
- **Redirects are not followed.** A redirecting source response raises instead of being followed, so a public URL cannot bounce into an internal address.
- **Byte caps.** A downloaded source image is capped at 50 MiB (`MAX_REMOTE_IMAGE_BYTES`) enforced while streaming, so an oversized body is aborted rather than buffered; the same limit applies to local source files.
- **Content sniffing.** The source is validated by magic bytes (PNG, JPEG, GIF, WebP) and the authoritative MIME type is taken from the file content, not from the file extension or a caller-supplied type. Non-images are rejected.
- **Exactly one reference image.** MAI editing takes a single source image. Zero or one is accepted; two or more are rejected with a `too_many_references` error before any request is sent.
- **Width and height constraints.** Generation uses fixed MAI presets — landscape `1536x1024`, square `1024x1024`, portrait `1024x1536` — chosen from the resolved aspect ratio. There is no free-form width/height input.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest -q
```

The tests mock HTTP responses and never require an Azure key.

## Validation

`hermes plugins validate src/hermes_mai_image` passes. It reports one warning, which is understood and accepted:

```
⚠ private core imports (catalog rule 9: public surfaces only; these can break on
any release): provider.py:45 agent.file_safety.raise_if_read_blocked
```

**Why it is accepted.** `raise_if_read_blocked` is the shared chokepoint Hermes itself uses for local-path input loading; the same import appears in Hermes' own bundled providers (`plugins/image_gen/{xai,openai,openai-codex,krea,openrouter}` and `plugins/video_gen/xai`). It is what stops a local reference image from reading a credential store or a protected path, and there is no public equivalent for it. Dropping the import to silence the warning would remove that guard — a worse outcome than the warning.

The warning fires because `agent.file_safety` is not listed in `hermes_cli/data/plugin_api.json`, which is the allowlist the lint consults. A request to publish a public seam for this (and for image fetching, where no public download API exists at all) is filed upstream at [NousResearch/hermes-agent#155704](https://github.com/NousResearch/hermes-agent/issues/155704); if it lands, the import is replaced and the warning disappears.

The other private import originally flagged — `tools.vision_tools._download_image` — **has been removed**: the plugin now fetches source images itself behind the published `tools.url_safety.is_safe_url` policy. The redirect guard, byte cap and magic-byte sniffing are covered by tests, including a test asserting a redirect's `Location` is never dereferenced.

## Microsoft documentation

- [Deploy and use MAI image models in Microsoft Foundry](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-mai-image)
