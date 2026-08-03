# Changelog

## Unreleased — Digit identity (2026-08-03)

### Removed (BREAKING)

- The `hermes`, `hermes-agent` and `hermes-acp` commands are gone. Earlier
  READMEs promised them as permanent compatibility aliases; **that promise is
  withdrawn deliberately.** Use `digit`, `digit-agent` and `digit-acp` — the
  replacements already existed and are unchanged. Any script, systemd unit,
  cron entry, CI job, editor ACP config or shell alias invoking the old names
  must be updated; they will fail with "command not found", not silently.

### Changed

- Top-level modules renamed: `hermes_constants` → `digit_constants`,
  `hermes_state*` → `digit_state*`, `hermes_logging` → `digit_logging`,
  `hermes_time` → `digit_time`, `hermes_bootstrap` → `digit_bootstrap`, and the
  `hermes_cli/` package → `digit_cli/`. Anything importing these directly must
  be updated; there is no module-level alias.
- SKILL.md frontmatter key `metadata.hermes` → `metadata.digit`. Bundled skills
  were migrated. **Third-party skills keep working**: the loader reads the new
  key and falls back to the old one, warning once per skill.
- Environment variables `HERMES_*` → `DIGIT_*` (601 names). **Existing setups
  keep working**: any `HERMES_*` variable still set is copied onto its `DIGIT_*`
  name at startup when the new name is unset, with a one-time deprecation
  warning. The new name always wins when both are set.
- `setup-hermes.sh` → `setup-digit.sh`.
- npm workspace scopes `@hermes/*` and `@hermes-agent/*` → `@digit/*`.

### Deprecated

- `HERMES_*` environment variables, the `metadata.hermes` skill key, and the
  outbound `HERMES_HOME` mirror handed to MCP servers and plugins. All three
  are bridged by `digit_compat.py` and will be **removed in the next minor
  release**.

### Migration

The data directory default moved from `~/.hermes` to `~/.digit`. Nothing is
copied automatically — `~/.hermes` can hold provider credentials, and
duplicating those is your decision. On first run Digit detects the old tree and
prints the exact command:

    cp -a ~/.hermes/. ~/.digit/

To keep the old location instead, set `DIGIT_HOME=~/.hermes`.

### Explicitly NOT changed

These are external facts, not branding, and renaming them would break working
software or breach a licence:

- **Model identifiers** — `hermes-3-405b`, `hermes-4-405b`,
  `NousResearch/Hermes-3-Llama-3.1-70B`, `nousresearch/hermes-4-70b` and the
  regex that classifies them. These go over the wire to inference APIs.
- **Upstream attribution** — the statement that Digit is derived from
  [Hermes Agent](https://github.com/NousResearch/hermes-agent) and the
  `Copyright (c) 2025 Nous Research` notice in `LICENSE`. Required by the MIT
  licence; see the Origin section in the README.
- **The upstream docs host** `hermes-agent.nousresearch.com`, which still
  serves the model catalog Digit fetches at runtime.
- **`hermes-0day`** — the name of a real security incident whose indicators of
  compromise ship in `digit_cli/mcp_security.py`.
- **The "hey hermes" wake word**, which is baked into the bundled ONNX/TFLite
  weights. The detector fires on those acoustics regardless of what the
  identifier is called.
- **`contributors/` and `.mailmap`** — third-party identity records.

### Added

- `optional-mcps/fts-gate/` and `optional-mcps/digit-tools/` — the two
  first-party MCP servers, installable with `digit mcp install <name>`.
- `install.type: local` in the MCP catalog schema, for servers built alongside
  Digit rather than fetched from a registry.
- `docs/digitable/mcp-servers.md`, including the licence analysis for running a
  GPL-3.0 server behind an MCP process boundary.

### Fixed

- The `fts` skill instructed the model to call `fts_check`, `fts_compile`,
  `fts_test`, `fts_execute`, `fts_generate`, `fts_prove`, `fts_certify` and
  `fts_verify`. **None of those tools exist anywhere in this project.** The
  skill now names the two the gate actually advertises, `fts_gate_check` and
  `fts_morphisms_list`, and its guard test was updated to match.
- `mcp_serve.py` fell back to `~/.hermes` for the session and state paths while
  the rest of the runtime had already moved to `~/.digit`.

