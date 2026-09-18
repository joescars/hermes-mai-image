---
name: hermes-mai-image
description: "Repository guidance for the standalone Hermes Agent Microsoft Foundry MAI image-generation plugin."
---

# Hermes MAI Image Plugin

## Purpose

This repository contains a standalone Hermes Agent image-generation provider for Microsoft Foundry MAI image models. It is distributed from GitHub and is designed to become a PyPI package later.

## Repository layout

- `src/hermes_mai_image/provider.py` — provider implementation, model discovery, generation, editing, response handling, and cache integration.
- `src/hermes_mai_image/plugin.yaml` — Hermes plugin metadata.
- `src/hermes_mai_image/__init__.py` — public package exports and the module-level `register` hook.
- `tests/test_mai_image_plugin.py` — mocked HTTP contract tests; tests never use live Azure credentials.
- `README.md` — installation, configuration, Web UI, CLI, troubleshooting, and update instructions.
- `pyproject.toml` — package metadata, dependencies, test extra, and the `hermes_agent.plugins` entry point.

## Hermes plugin contract

The package must keep this entry point format:

```toml
[project.entry-points."hermes_agent.plugins"]
azure-mai-image = "hermes_mai_image"
```

Hermes discovers this provider as a module import. Do not change it to `hermes_mai_image:register` without verifying Hermes' provider discovery contract; that target is treated differently by Hermes and previously caused the provider not to appear in `hermes tools`.

The provider identity is:

- Name: `azure-mai`
- Display name: `Microsoft Foundry MAI`
- Plugin key: `azure-mai-image`

The module-level `register(ctx)` function calls `ctx.register_image_gen_provider(MAIImageProvider())`.

## API contract

Use Microsoft Foundry's native MAI API, not the standard OpenAI image API:

- Generation: `POST {endpoint}/mai/v1/images/generations`
- Editing: `POST {endpoint}/mai/v1/images/edits`
- Authentication: `api-key` request header
- Generation JSON fields: `model`, `prompt`, `width`, `height`
- Editing: multipart form fields `model`, `prompt`, and `image`

The configured endpoint is the resource root, such as `https://resource.services.ai.azure.com`. The provider appends `/mai/v1`; never require users to enter that suffix.

Required credentials/settings:

- `MAI_FOUNDRY_API_KEY` — secret; store in the active Hermes profile `.env` only.
- `MAI_FOUNDRY_ENDPOINT` — Foundry resource root.
- `MAI_IMAGE_MODEL` — optional deployment/model override; the exact Foundry deployment name is preferred.
- `MAI_FOUNDRY_MODELS_PATH` — optional discovery path override, default `/models`.

Never place credentials in source, tests, `config.yaml`, README examples, commits, logs, or chat messages.

## Development workflow

Work on a feature branch, not directly on `main`:

```bash
git switch main
git pull --ff-only origin main
git switch -c feature/<short-description>
```

Create a local environment and install test dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
```

Run the focused suite:

```bash
python -m pytest -q
python -m compileall -q src tests
```

Build a wheel before packaging changes:

```bash
python -m pip wheel --no-deps --wheel-dir dist .
```

## Testing rules

- Keep HTTP tests mocked; no test should require or transmit a real Azure key.
- Test request URLs, headers, payload shapes, multipart edit fields, response parsing, missing credentials, and cache materialization.
- Follow red-green-refactor for behavior changes: write a failing test first, confirm the expected failure, implement the smallest fix, then run the full suite.
- Do not weaken tests to accommodate an implementation error.

For live validation, use a separately configured Hermes profile and explicit user-provided credentials. Never add live credentials to test fixtures or CI.

## Release and distribution

The primary installation path is the GitHub URL:

```bash
pip install --upgrade "git+https://github.com/joescars/hermes-mai-image.git"
```

After changing package metadata or provider code:

1. Run tests, compilation, and wheel build.
2. Review `git diff --check` and the staged file list.
3. Commit with a focused message.
4. Push the feature branch and open a PR unless the user explicitly asks to work directly on `main`.
5. After merge, verify the GitHub default branch contains the commit.

If the Hermes Python virtual environment is replaced by a Hermes update, users may need to reinstall the package into the new environment. Profile credentials and configuration are separate and should not be deleted by a plugin release.

## Documentation requirements

Update `README.md` when installation, configuration, endpoints, provider discovery, or update behavior changes. Keep the CLI and Web UI steps accurate. Explicitly state that the endpoint is the resource root and that `/mai/v1` is appended by the provider.

## Safety boundaries

- Do not modify Hermes core for provider-specific behavior.
- Do not add unauthenticated live API calls to tests or build steps.
- Do not print API keys, authorization headers, or full credential-bearing URLs.
- Do not use destructive Git commands (`reset --hard`, unapproved stash, broad clean) to resolve unrelated local changes.
