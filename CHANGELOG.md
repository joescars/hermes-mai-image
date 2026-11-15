# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Source images stream into memory (no temp file), local files are size-checked via `stat` before reading, `MAX_REMOTE_IMAGE_BYTES` is renamed `MAX_SOURCE_IMAGE_BYTES`, and the contradictory `agent.file_safety` comment is fixed.

### Added

- Credential resolution through the Hermes secret scope, so provider settings are read from the
  active profile's secret store instead of ambient environment lookup.
- An in-plugin, SSRF-safe image downloader that validates reference image URLs before fetching
  them, replacing a private Hermes import.
- A draft catalog entry under `plugin-catalog/` for submission to the Hermes plugin catalog.

### Changed

- CI hardening: ruff lint, Python 3.13/3.14 matrix, SHA-pinned actions, Dependabot, wheel `plugin.yaml` check, a tag-triggered PyPI Trusted Publishing release workflow, and SPDX license metadata (#29).
- Image sizes are now model-aware: MAI-Image-2.5 models use presets within the 1,048,576-pixel limit
  (landscape `1248x832`, portrait `832x1248`); 2.6 deployments (detected from the name, or via
  `MAI_IMAGE_SIZE_PROFILE` / `image_gen.azure-mai.size_profile` = `2.5`/`2.6`) use larger presets. (#17)
- Safer retry policy: 429 is always retried honouring `Retry-After` (seconds or HTTP-date) with a
  long jittered backoff otherwise; only 502/503 are retried among 5xx (never 500/504, to avoid
  double billing); retries share an overall `MAI_REQUEST_DEADLINE` budget (default 600s).
- Honor Hermes secret scope for `MAI_FOUNDRY_API_KEY` (no env fallback inside Hermes); endpoint is a non-secret setting resolvable from `image_gen.azure-mai.*` config or env, and model/models-path are declared optional (#25).
- HTTP error messages are now accurate per status: 400 reports a rejected request (with the service
  `error.code`/`message` and size/format limits) instead of a missing deployment, 404 covers deployment/endpoint,
  401/403 give authentication guidance, 429 mentions quota tiers, and detail is truncated to ~500 characters (#18).
- GIF and WebP source images are now rejected before upload with `invalid_input` (MAI accepts only
  JPEG or PNG); README and catalog known issues list the supported formats.
- Security: remote source-image downloads no longer follow redirects (`allow_redirects=False`; 3xx and redirect history are rejected), closing an SSRF bypass (#19).
- Provider robustness: model discovery no longer raises when the resource `/models` endpoint is
  unavailable, falling back to the documented MAI catalog.
- Provider robustness: HTTP requests to the Foundry MAI endpoints now use retry with backoff, and
  the image download path is safe to call from an active event loop.
- Provider robustness: remote image downloads enforce a byte cap to bound memory use.
- Manifest metadata completed, and the package version single-sourced so the plugin manifest and
  the Python package report the same version.
- README updated for catalog submission (installation, configuration, and discovery instructions).

## [0.1.2]

### Changed

- README installation, configuration, Web UI, CLI, troubleshooting, and update instructions
  refreshed for the current Hermes plugin discovery flow.