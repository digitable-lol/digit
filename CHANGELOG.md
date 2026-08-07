# Changelog

## Unreleased — Digit identity (2026-08-03)

**Read [BREAKING.md](BREAKING.md) first** if you maintain anything that talks
to Digit from outside this repository — an ACP client, a plugin, an installer.
It covers the four breaks that cross a boundary someone else owns, the clients
they affect, and what their authors have to change. Rollback:
[docs/digitable/rebrand-rollback.md](docs/digitable/rebrand-rollback.md).

### Removed (BREAKING)

- **The ACP `_meta` vendor namespace key is `digit`, was `hermes`.** This is a
  wire format read by external ACP clients — Zed and Digitable Workbench —
  carrying `sessionProvenance`, `compactionSummary` and
  `containsCompactionSummary`. **Both keys are never emitted**: the owner chose
  a clean break over a dual-emit release, so there is no deprecation window and
  no shim. Clients that read `_meta.hermes` see the key simply absent and every
  feature built on it turns off silently. See BREAKING.md §1.
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
- **Documentation moved to its own host: `docs.digitable.life`.** The site was
  always built from `website/` in this repository, but `deploy-site.yml` was
  gated on `github.repository == 'NousResearch/hermes-agent'`, so the fork
  never published it and every doc link in the CLI help, command hints and
  dashboard pointed at the upstream site. It is now published to the GitHub
  Pages of `digitable-lol/digit`, `baseUrl` is the site root (no `/docs/`
  prefix), and `hermes-agent.nousresearch.com/docs` was replaced everywhere it
  is printed to a user. Old `/docs/<path>` URLs keep working via generated
  redirects. The upstream host survives in exactly two places, as a bootstrap
  fallback for the skills catalog when our own site has not been deployed yet:
  `deploy-site.yml` and `website/scripts/prebuild.mjs`.
- **Wake word: the phrase is now "hey digit".** It stayed "hey hermes" until
  now for a reason that has not changed — the phrase labels trained weights,
  not a string we own — so the fix was to retrain, not to rename. A `hey_digit`
  openWakeWord model was trained (synthetic TTS positives, ACAV100M and
  adversarial negatives, with a deliberately weighted block of "hey hermes" so
  the new model actively *rejects* the old phrase) and ships in
  `tools/wakewords/` in both ONNX and TFLite. Measured behaviour, including the
  working point and its cost in both directions, is in
  `website/docs/user-guide/features/wake-word.md`.
  **`hey_hermes.onnx`/`.tflite` no longer ship.** Configs naming the old model
  (`wake_word.openwakeword.model: hey_hermes`, or the bare `hermes` /
  `hey hermes`) still load — they resolve to the bundled model and log a warning
  that the phrase is now "hey digit", rather than failing to load or silently
  changing what you have to say. To keep the old phrase, point
  `wake_word.openwakeword.model` at your own copy of a `hey_hermes` model, or
  use the `sherpa` provider, which detects any typed phrase with no model.

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
- **`hermes-0day`** — the name of a real security incident whose indicators of
  compromise ship in `digit_cli/mcp_security.py`.
- ~~**The "hey hermes" wake word**, which is baked into the bundled ONNX/TFLite
  weights. The detector fires on those acoustics regardless of what the
  identifier is called.~~ **No longer kept** — the weights were retrained, see
  "Wake word: the phrase is now hey digit" below. This entry stays because the
  reasoning was right: the phrase could only change together with the model.
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

