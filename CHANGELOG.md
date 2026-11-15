# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Credential resolution through the Hermes secret scope, so provider settings are read from the
  active profile's secret store instead of ambient environment lookup.
- An in-plugin, SSRF-safe image downloader that validates reference image URLs before fetching
  them, replacing a private Hermes import.
- A draft catalog entry under `plugin-catalog/` for submission to the Hermes plugin catalog.

### Changed

- HTTP error messages are now accurate per status: 400 reports a rejected request (with the service
  `error.code`/`message` and size/format limits) instead of a missing deployment, 404 covers deployment/endpoint,
  401/403 give authentication guidance, 429 mentions quota tiers, and detail is truncated to ~500 characters (#18).
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