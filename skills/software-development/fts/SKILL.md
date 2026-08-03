---
name: fts
description: Create and verify executable FTS specifications.
version: 1.0.0
author: Digitable
license: Apache-2.0
platforms: [linux, macos, windows]
metadata:
  digit:
    tags: [fts, executable-specifications, ddd, testing, mcp]
    category: software-development
    related_skills: [digit, digitable-courses, digitable-workbench, test-driven-development, fts-constitution, fts-specify, fts-admit, fts-memory]
---

# FTS Skill

Create, test, execute, and verify Formal Type Surface specifications. FTS owns
deterministic domain decisions; it does not perform network, database, payment,
or other external effects.

## When to Use

- The user wants to turn a business rule into an executable utility.
- One rule must drive examples, generated TypeScript, forms, or tables.
- A DDD command needs a checkable guard or evidence certificate.
- An agent-authored policy must be validated before a consequential action.
- The task mentions `.fts`, FTS, executable specifications, or FTS MCP tools.

Do not use FTS for UI layout, repository access, HTTP retries, transactions, or
arbitrary orchestration. Keep those in the host application.

## Prerequisites

Use one of these boundaries:

1. The `fts` CLI is available on `PATH`.
2. The FTS MCP server is configured and exposes `fts_check`.
3. A project-local checkout exposes `dist/src/cli.js` and `dist/src/mcp.js`.

No credential is required by FTS itself. A private repository checkout may
require the user's existing GitHub access.

## How to Run

With the CLI, use the native `terminal` tool:

```bash
fts check model.fts --pretty
fts test model.fts --pretty
fts run model.fts --utility "Рассчитать скидку" --input input.json --pretty
fts generate model.fts --out generated
```

With MCP, pass source and context explicitly. Never ask the MCP server to read a
model-invented path; its contract is read-only structured input.

## Quick Reference

| Goal | CLI | MCP |
|---|---|---|
| Canonical JSON | `fts compile` | `fts_compile` |
| Validate model | `fts check` | `fts_check` |
| Run examples | `fts test` | `fts_test` |
| Execute utility | `fts run` | `fts_execute` |
| Generate TS/tests | `fts generate` | `fts_generate` |
| Explain derivation | `fts prove` | `fts_prove` |
| Bind evidence | `fts certify` | `fts_certify` |
| Verify evidence | `fts verify` | `fts_verify` |

## Procedure

1. Identify one bounded context and use its exact ubiquitous language.
2. Separate scalar input facts, deterministic decision, expected examples, and
   the host application's external effect.
3. Author indentation-based FTS. Use Russian or English consistently. Quote
   multi-word names; Russian guillemets and regular quotes are valid.
4. Run `fts_check`. Stop on any diagnostic; do not repair unknown input by
   guessing a new field.
5. For utilities, add below/on/above-boundary examples and run `fts_test`.
6. Use `fts_execute` for a concrete scalar input or `fts_generate` for checked
   TypeScript and `node:test` files.
7. For a theorem, call `fts_certify` with the explicit snapshot, then
   `fts_verify` against the same snapshot.
8. Before a consequential tool call, require both `valid: true` and
   `status: verified`. An LLM explanation is not a substitute.
9. Return source, structured result, and diagnostic codes. State assumptions
   that originate outside FTS.

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
- A browser compile is suitable for UI, not a strict certificate decision.
- Generated files are artifacts; edit `.fts` and regenerate them.
- Do not silently cap a broken result. Declare a property and let it fail.
- A verified snapshot can become stale before a database write; keep version
  checks and transaction boundaries in the application.
- Never claim FTS generated an HTTP call, SQL transaction, or payment.

## Verification

- [ ] Category and names match one bounded context.
- [ ] `fts_check` returns valid.
- [ ] Every utility has boundary examples and `fts_test` passes.
- [ ] Properties express required postconditions.
- [ ] External effects remain outside FTS.
- [ ] Consequential commands require `fts_verify` status `verified`.
- [ ] Result includes diagnostic codes and external assumptions.

Course and case catalog: `https://courses.digitable.life/fts/`.
