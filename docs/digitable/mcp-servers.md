# Digitable MCP servers

Digit ships two first-party MCP servers in the approved catalog
(`optional-mcps/`). Both are stdio servers launched as separate processes.

| Entry | Binary | Tools | Licence |
|---|---|---|---|
| `fts-gate` | `node <install>/dist/src/mcp.js` | `fts_gate_check`, `fts_morphisms_list` | Apache-2.0 |
| `digit-tools` | `<install>/dist/digit-tools-mcp` (self-contained) | `tools_categories`, `tools_list`, `tools_execute` | GPL-3.0 |

Install them the same way as any other catalog entry:

```bash
digit mcp install fts-gate
digit mcp install digit-tools
digit mcp list
```

Both are declared with `install.type: local` — they are built alongside Digit
rather than fetched from a registry. Install resolves and existence-checks the
path; it clones nothing and runs nothing.

By default each resolves under Digit's own data directory, which is the same on
every host:

| Entry | Default location | Source |
|---|---|---|
| `fts-gate` | `~/.digit/mcp-servers/fts-gate` | [digitable-lol/fts-gate](https://github.com/digitable-lol/fts-gate) |
| `digit-tools` | `~/.digit/mcp-servers/tools-core` | [digitable-lol/tools-core](https://github.com/digitable-lol/tools-core) |

Build the server and place it there, or point the override at your own
checkout:

```bash
export DIGIT_FTS_GATE_HOME=/path/to/fts-gate
export DIGIT_TOOLS_CORE_HOME=/path/to/tools-core
```

If neither the override nor the default exists, `digit mcp install` fails and
names both the resolved path and the variable to set. A manifest never encodes
one developer's machine layout.

## Licence interaction — the short answer

**Installing or using either server does not change Digit's licence, and does
not impose GPL obligations on Digit.**

Digit is MIT. `fts-gate` is Apache-2.0. `digit-tools` is GPL-3.0, inherited
from [it-tools](https://github.com/CorentinTh/it-tools), which it is a headless
port of.

The reason the GPL does not propagate is the process boundary. Digit does not
import, link against, or embed `digit-tools`. It spawns it as a separate
operating-system process and exchanges JSON-RPC messages over stdin/stdout at
arm's length. The FSF's own position is that separate programs communicating
over pipes or sockets — exchanging data rather than sharing an address space
and internal data structures — are separate works, not one combined work.
Nothing in Digit's source is derived from it-tools, and nothing in it-tools
ends up inside a Digit binary.

Practically:

- Digit's own source stays MIT and can be distributed under MIT.
- The MCP protocol messages are data, not a linking interface.
- No GPL source is copied into this repository. The catalog entry records a
  path to a build; it does not vendor code.

## What GPL-3.0 *does* still require

The obligations attach to the `digit-tools` binary itself, not to Digit:

- **If you redistribute the `digit-tools-mcp` binary** — bundling it in an
  installer, a Docker image, or a customer deliverable — you are distributing a
  GPL-3.0 work and must accompany it with the corresponding source (or a
  written offer), keep the licence notice, and pass on the same rights.
- **If you modify tools-core** and ship the result, the modifications are also
  GPL-3.0.
- **If you only run it locally**, having installed it yourself, there is no
  distribution and therefore no obligation beyond keeping the notice.

`fts-gate` under Apache-2.0 is permissive and compatible in all of these cases;
it requires attribution and a NOTICE if one is provided.

The one arrangement to avoid is **statically linking or importing tools-core
source into Digit** — that would create a combined work and would put Digit
under GPL-3.0. The MCP boundary is what keeps this clean, so keep it: do not
`import` from `tools-core` in Python or TypeScript, and do not vendor its
`src/` into this tree.

## Verifying a server actually works

The catalog probes tools at install time, but you can check the handshake
directly. Both servers speak MCP over stdio:

```bash
digit mcp probe fts-gate
digit mcp probe digit-tools
```

Confirmed working as of the rebrand (real `tools/list` + `tools/call`
handshakes):

- `fts_gate_check` on `examples/order-shipment.fts` returns
  `status: certified`, `evidence: verified`, with a proof certificate and
  matching expected/actual digests.
- `fts_morphisms_list` returns 29 morphisms (23 verified, 3 derived, 3
  proposed).
- `tools_categories` returns 14 categories covering 95 utilities.
- `tools_execute {tool_id: "hash_text", args: {...}}` returns a SHA-256 that
  matches `sha256sum` byte for byte.

## Tool naming

Tool names in `SKILL.md` files must match what the servers advertise. They are,
verbatim:

```
fts_gate_check       source (required), context, require_evidence
fts_morphisms_list   trust, domain
tools_categories     (no arguments)
tools_list           category (required)
tools_execute        tool_id (required), args, timeout_ms
```

`digit-tools` is a **router**, not 95 flat tools. Call `tools_categories`,
then `tools_list` for the schemas of one category, then `tools_execute`.
Argument names are validated against each utility's own `input_schema`;
guessing them returns a structured `invalid_args` error rather than a wrong
answer.
