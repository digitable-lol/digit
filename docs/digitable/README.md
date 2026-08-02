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

| Surface | Digit entry point | Compatibility entry point |
|---|---|---|
| CLI | `digit` | `hermes` |
| headless agent | `digit-agent` | `hermes-agent` |
| Workbench / IDE ACP | `digit-acp` | `hermes-acp` |
| Desktop | Digit | Hermes internals remain compatible |

## Bundled Digitable skills

- `digit`: identity, language, privacy, and ecosystem routing.
- `digitable-portal`: canonical domains and cross-product navigation.
- `digitable-courses`: course discovery and learning-path guidance.
- `digitable-tools`: deterministic routing to every browser utility.
- `digitable-workbench`: ACP/MCP integration and hand-off rules.

## Upstream sync policy

Keep distribution changes additive and concentrated in identity, skin, assets, skills, docs, and launch aliases. Merge upstream regularly and do not rename internal Python packages unless an upstream incompatibility forces it.
