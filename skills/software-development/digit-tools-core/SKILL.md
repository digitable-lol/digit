---
name: digit-tools-core
description: Use when a task needs a deterministic local computation — hashes, HMAC, encodings, format converters, parsers, network address math, date arithmetic, generators. This is the local headless catalog behind Digit, exposed over MCP as three routing tools rather than one tool per utility. Covers the two-step routing protocol, how to tell payload from a mode argument, seeded reproducibility for non-deterministic tools, and the documented limits. Prefer it over computing a result yourself, and over sending data to a browser utility.
version: 1.0.0
author: Digitable
license: GPL-3.0
platforms: [linux, macos, windows]
metadata:
  digit:
    tags: [digitable, tools, mcp, deterministic, converters, crypto]
    category: software-development
    related_skills: [digitable-tools, verified-answers, digit, digitable-workbench]
---

# Digit Tools Core

## Overview

95 headless utilities in 14 categories, ported
from 75 of the 86 upstream `it-tools`
utilities. Pure functions, a machine-readable catalog, and an MCP stdio server.
No browser, no DOM.

The point is not convenience. **You do not compute the result; the tool does.**
A result you produced yourself has no provenance and cannot be verified.

Related but different: `digitable-tools` routes a human to a browser page on
`tools.digitable.life`. This skill runs the computation locally.

## When to Use

- Any hash, HMAC, encoding, cipher, token, UUID/ULID, OTP, BIP39 operation.
- Format conversion (JSON/YAML/TOML/XML/CSV), formatting, minifying, diffing.
- IPv4/IPv6 math, MAC lookup, user-agent and URL parsing, JWT inspection.
- Text statistics, case conversion, slugify, Unicode/binary conversion.
- Date arithmetic, percentages, temperature, expression evaluation.

Do not use it for anything requiring a live browser, an uploaded file, or camera
and keyboard events — those utilities were deliberately not ported
(11 of them).

## How to Run

Routing is two steps on purpose: registering 95 MCP tools would push
every input schema into context on every turn.

| Step | Tool | Payload |
|---|---|---|
| 1 | category index | 14 one-line entries, ≈536 |
| 2 | category schemas | full schemas and examples for **one** category |
| 3 | execute | run by tool id with arguments |

Configure the MCP server by pointing at the compiled binary — no runtime is
required on the host:

```json
{ "mcpServers": { "digit-tools": { "command": "<path to binary>" } } }
```

Execution never throws. Every outcome is either a result or
`{ok: false, error, code}` with `code` one of `unknown_tool`, `invalid_args`,
`execution_error`, `timeout`. Arguments are validated against the tool's input
schema, with type coercion and defaults applied.

## Procedure

1. Pick the category from the index. Do not guess a tool id.
2. Load that category's schemas, then choose the narrowest matching tool.
3. Separate **payload** from **mode**. A schema field with an enum
   (`algorithm`, `encoding`, `format`) names a method, never data.
4. If the payload is absent from the request, refuse and name the missing
   argument. Do not promote an adverbial phrase into the payload.
5. Execute, then report the tool id and the arguments alongside the result.
6. For a non-deterministic tool, pass an explicit `seed`, `now`, `timestamp` or
   `startedAtMs` whenever the caller needs reproducibility.

## Pitfalls

- **"Hash SHA-256" is not a request to hash the string "SHA-256".** The same
  trap appears as "in windows-1251", "by the ICAO standard", "with a length of 12
  characters", "upper limit 3999". Naming a mode is not supplying data.
- Some tools have no required arguments at all (token and lorem generators).
  Refusing there contradicts the schema — answer with the defaults instead.
- UUID v3/v5 are hashes of (namespace, name); without a namespace the request is
  unanswerable, not merely underspecified.
- A few tools deviate from the browser originals on purpose (line endings,
  rounding, flattened schemas, thrown instead of swallowed errors). Do not
  "correct" the output back to the browser behaviour.
- Salted and key-generating tools are not seedable by design. Do not fake
  reproducibility there.
- The execution timeout is not preemptive: a runaway regular expression can still
  block the process.
- Do not generate production secrets in a shared or recorded session.

## Verification

- [ ] The tool id came from the catalog, not from memory.
- [ ] Payload and mode arguments were separated before the call.
- [ ] The result is reported with its tool id and arguments.
- [ ] Missing-argument cases ended in a refusal naming the argument.
- [ ] Reproducibility was made explicit where the caller needs it.
