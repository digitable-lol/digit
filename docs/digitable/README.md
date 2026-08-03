# Digit distribution

Digit is the Digitable distribution of Hermes Agent. It keeps upstream compatibility while adding a Digitable identity, design tokens, portal-aware skills, local-model presets, and stable integration surfaces for Workbench.

## Product contract

- Product name: **Digit**; vendor: **Digitable**.
- Canonical public surfaces: `digitable.life`, `courses.digitable.life`, and `tools.digitable.life`.
- First-class conversation languages: Russian and English. Digit follows the user's language unless asked to translate; Desktop chrome currently retains the upstream locale set.
- User data and memory stay local unless the selected model or tool provider requires a remote request.
- Live portal facts must be verified from canonical Digitable pages; bundled knowledge is a routing map, not a frozen copy of the sites.
- Upstream attribution stays visible: Digit is based on Nous Research's MIT-licensed Hermes Agent.

## Surfaces

| Surface | Entry point |
|---|---|
| CLI | `digit` |
| headless agent | `digit-agent` |
| Workbench / IDE ACP | `digit-acp` |
| Desktop | Digit |

<!-- rebrand:keep -->
The `hermes`, `hermes-agent` and `hermes-acp` aliases were removed in the <!-- rebrand:keep -->
rebrand; see CHANGELOG.md. `HERMES_*` environment variables are still honoured <!-- rebrand:keep -->
for one more minor release.

## MCP servers

Two first-party servers ship in the approved catalog:

- `fts-gate` — `fts_gate_check`, `fts_morphisms_list` (Apache-2.0)
- `digit-tools` — `tools_categories`, `tools_list`, `tools_execute` (GPL-3.0,
  behind a stdio process boundary; see [mcp-servers.md](mcp-servers.md) for the
  licence analysis)

```bash
digit mcp install fts-gate
digit mcp install digit-tools
```

## Bundled Digitable skills

- `digit`: identity, language, privacy, and ecosystem routing.
- `digitable-portal`: canonical domains and cross-product navigation.
- `digitable-courses`: course discovery and learning-path guidance.
- `digitable-tools`: deterministic routing to every browser utility.
- `digitable-workbench`: ACP/MCP integration and hand-off rules.
- `fts`: executable domain specifications, generated utilities, tests, and verified agent guards.

## Upstream sync policy

Keep distribution changes additive and concentrated in identity, skin, assets, skills and docs. <!-- rebrand:keep --> **The internal Python packages have now been renamed** (`hermes_cli` -> `digit_cli`, `hermes_*` -> `digit_*`), so upstream merges will conflict on every renamed path; expect to resolve them by applying the same rename to incoming code. That cost was accepted deliberately as part of the rebrand — see CHANGELOG.md.
