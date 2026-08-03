---
name: fts
description: Create and verify executable FTS specifications.
version: 2.0.0
author: Digitable
license: Apache-2.0
platforms: [linux, macos, windows]
metadata:
  digit:
    tags: [fts, executable-specifications, ddd, testing, mcp]
    category: software-development
    related_skills: [digit, digitable-courses, digitable-workbench, test-driven-development]
---

# FTS Skill

Create and verify Formal Type Surface specifications. FTS owns deterministic
domain decisions; it does not perform network, database, payment, or other
external effects.

## When to Use

- The user wants to turn a business rule into a checkable specification.
- A DDD command needs a machine-checked guard or evidence certificate.
- An agent-authored policy must be validated before a consequential action.
- The task mentions `.fts`, FTS, executable specifications, or the FTS gate.

Do not use FTS for UI layout, repository access, HTTP retries, transactions, or
arbitrary orchestration. Keep those in the host application.

## Prerequisites

The `fts-gate` MCP server must be installed:

```bash
digit mcp install fts-gate
digit mcp list          # confirm it is enabled
```

It needs no credentials. If it is not installed, say so and stop — do not
invent results.

## Available Tools — exactly two

The gate exposes **two** tools. There is no `fts_check`, `fts_compile`,
`fts_test`, `fts_execute`, `fts_generate`, `fts_prove`, `fts_certify` or
`fts_verify`; earlier versions of this skill listed them and they never
existed. If you find yourself reaching for one of those names, the operation
you want is `fts_gate_check`.

### `fts_gate_check`

Compiles the source, type-checks it, discharges every declared morphism
against the verified library, and returns a proof certificate.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `source` | string | yes | The FTS specification **as text**, not a path |
| `context` | JSON | no | Evidence context used to check witnesses |
| `require_evidence` | boolean | no | Refuse symbolic/theorem-free specs instead of reporting their evidence level (default `false`) |

Returns `status` (`certified` / refused), `result.evidence`
(`verified` / weaker), `result.conclusion`, `result.verification.valid`,
`certificate` with `expected_digest` and `actual_digest`, and
`morphisms_used`.

The server never opens a path you hand it. Its contract is read-only
structured input — read the file yourself with the native tools and pass the
text.

### `fts_morphisms_list`

Lists domain morphisms usable as proof premises.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `trust` | `verified` \| `derived` \| `proposed` | no | Filter by trust level |
| `domain` | string | no | Filter by exact morphism domain type |

Only `verified` morphisms are sound premises for a consequential decision.
`derived` are compositions of verified ones; `proposed` are not yet reviewed.

## CLI Equivalent

The same gate ships a CLI, useful via the native `terminal` tool when you
already have files on disk:

```bash
fts-gate check model.fts --context evidence.json --pretty
fts-gate morphisms --trust verified --pretty
```

Exit codes: `0` certified, `1` refused (see `.code` in the JSON), `2` usage or
I/O error. Every command writes one JSON object to stdout.

## Procedure

1. Identify one bounded context and use its exact ubiquitous language.
2. Call `fts_morphisms_list` to see which premises are actually available.
   Build the specification from those, rather than inventing a law.
3. Separate scalar input facts, the deterministic decision, and the host
   application's external effect.
4. Author indentation-based FTS. Use Russian or English consistently. Quote
   multi-word names; Russian guillemets and regular quotes are both valid.
5. Call `fts_gate_check` with the source text and, when you have evidence, the
   `context`. Stop on any diagnostic — do not repair unknown input by guessing
   a new field.
6. Before a consequential tool call, require `status: "certified"`,
   `result.verification.valid: true` and `result.evidence: "verified"`. Pass
   `require_evidence: true` to make the gate enforce that for you. An LLM
   explanation is not a substitute for the certificate.
7. Report the source, the structured result, and the diagnostic codes. State
   any assumption that originates outside FTS.

## Authoring Pattern

Start from `templates/discount.fts`. Prefer the natural surface:

```fts
категория «Продажи»

  объект Покупка
    сумма является деньгами

  утилита «Рассчитать скидку»
    принимает Покупка
    возвращает деньги
    начинает с 0

    правило «Большая покупка»
      если сумма не меньше 10000
      то добавить 10 процентов от поля сумма

    пример «Покупка на двадцать тысяч»
      дано сумма равна 20000
      ожидается результат равен 2000
```

## Pitfalls

- A successful parse does not prove an external business law is true.
- A symbolic derivation is not a verified evidence certificate.
- Do not silently cap a broken result. Declare a property and let it fail.
- A verified snapshot can become stale before a database write; keep version
  checks and transaction boundaries in the application.
- Never claim FTS generated an HTTP call, SQL transaction, or payment.
- Never claim a tool ran if the server was not installed.

## Verification

- [ ] Category and names match one bounded context.
- [ ] Every premise used appears in `fts_morphisms_list` with `trust: verified`.
- [ ] `fts_gate_check` returns `status: certified`.
- [ ] `result.verification.valid` is `true` and the digests match.
- [ ] Consequential commands ran with `require_evidence: true`.
- [ ] External effects remain outside FTS.
- [ ] Result includes diagnostic codes and external assumptions.

Course and case catalog: `https://courses.digitable.life/fts/`.
