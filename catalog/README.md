# DRAFT catalog entries — local only, not submitted

This directory holds draft entries for the Hermes plugin catalog
(`plugin-catalog/` in `NousResearch/hermes-agent`).

- `mai-image.yaml` is a **draft** catalog entry for this plugin.
- Drafts are reviewed locally first, by the repository owner, before anything
  leaves this repo.
- Submission to `NousResearch/hermes-agent` — opening a pull request against
  `plugin-catalog/` — happens **only** with the repository owner's explicit
  approval. Nothing here is pushed, submitted, or opened as a PR automatically.

Before submission, re-check the entry against the catalog's admission rules and
entry schema:
<https://github.com/NousResearch/hermes-agent/blob/main/plugin-catalog/README.md>.

The `sha` field in a draft points at the reviewed commit and **must** be
re-pinned to the release commit as the final step before submission (see the
comment in `mai-image.yaml`). It is not re-pinned automatically.