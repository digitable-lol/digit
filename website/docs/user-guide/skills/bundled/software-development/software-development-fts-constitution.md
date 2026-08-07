---
title: "Fts Constitution — Write project invariants as a checkable FTS model"
sidebar_label: "Fts Constitution"
description: "Write project invariants as a checkable FTS model"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Fts Constitution

Write project invariants as a checkable FTS model.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/software-development/fts-constitution` |
| Version | `1.0.0` |
| Author | Digitable |
| License | Apache-2.0 |
| Platforms | linux, macos, windows |
| Tags | `fts`, `spec-driven`, `constitution`, `invariants`, `requirements`, `ftspec` |
| Related skills | [`fts`](/user-guide/skills/bundled/software-development/software-development-fts), [`fts-specify`](/user-guide/skills/bundled/software-development/software-development-fts-specify), [`fts-admit`](/user-guide/skills/bundled/software-development/software-development-fts-admit), [`fts-memory`](/user-guide/skills/bundled/software-development/software-development-fts-memory) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Digit loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# FTS Constitution

Write the project constitution: invariants that no future specification may
violate. The constitution is an executable FTS model whose utilities return a
count of violations, not a markdown list of good intentions.

## When to Use

- A new spec-driven project starts and the non-negotiables must be fixed first.
- The same argument ("can we give 50 % just this once?") returns every quarter.
- A rule was violated in production and must become machine-checkable.
- An existing markdown "principles" document must become enforceable.

## When NOT to Use

- The constraint depends on live data (stock levels, rates, a user's balance).
  A constitution reads scalars supplied to it; it does not query anything.
- The constraint is about code style, architecture, or process. FTS checks
  domain values, not review etiquette.
- The rule holds only under a quantifier ("for every line item in the order").
  FTS has no collections; the honest move is to name the gap in the README.
- One project, one team, one constitution. Do not write per-feature ones.

## Prerequisites

- `ftsc` — compiler (module linking, functors) — and `ftspec` — corpus checker
  (applies invariants to specs) — both on `PATH`. Install from the FTS
  language repository (`github.com/the-homeless-god/fts`).
- Optionally Digit's MCP tools `fts_check` / `fts_test` for a single model.
- The corpus root layout, because roles are decided by path:

```
<корпус>/
  constitution.fts          ← this skill (also: конституция.fts)
  specs/<NNN-имя>/spec.fts  ← fts-specify
  memory/NNNN-имя.fts       ← fts-memory
  mapping/*.fts             ← functors between specs
```

## The Contract That Makes a Statute Work

`ftspec` applies a constitution utility to a spec utility only when:

- field `итог` is present — the spec utility's **result** is substituted there;
- **every** other required field of the invariant's input object matches a field
  of the spec utility's input object **by name and by type**.

No match, no application — **silently**. This is the single most common failure:
the statute compiles, its examples pass, and it checks nothing. An invariant
whose object has only `итог` matches every utility in the corpus; that is how a
project-wide statute is written.

Consequence: constitution field names are the project's vocabulary. Pick the
customer's words and make the specs use the same ones.

## Procedure

1. **Collect the non-negotiables.** Ask for the sentence that ends an argument:
   "we never discount more than 30 %", "we never take money without
   confirmation". Three to seven statutes. More than that and nobody reads them.

2. **Turn each into: object → utility → rules → property → examples.**
   Start from `templates/constitution.fts`.

   ```fts
   объект «Скидка на чек»
     сумма является деньгами
     итог является деньгами

   утилита «Скидка не превышает предела»
     принимает «Скидка на чек»
     возвращает число
     начинает с 0

     правило «Скидка выше 30 процентов чека»
       если сумма больше 0
       и итог больше 30 процентов от поля сумма
       то добавить 1
   ```

   The same statute on the English surface — one parser, one canonical model:

   ```fts
   utility "Discount stays under the cap"
     accepts "Discount on a receipt"
     returns number
     starts with 0

     rule "Discount above 30 percent of the receipt"
       if сумма is greater than 0
       and итог is greater than 30 percent of field сумма
       then add 1
   ```

   Domain names in quotes are never translated. The surface is a team decision
   (which language the customer speaks); the semantics are identical. Surfaces
   cannot be mixed inside one file — the language is fixed by the first
   non-comment line.

3. **One statute, one rule, one field where possible.** A rule reading three
   fields produces a diagnostic nobody can act on: the engineer cannot tell
   which boundary was crossed.

4. **Guard the grid, not just the sensible inputs.** `ftspec` evaluates
   invariants on a finite grid built from the spec's own thresholds: each
   threshold and threshold ± 1. For a threshold of 0 that includes −1, where
   "30 percent of сумма" changes sign. Add the guarding condition
   (`если сумма больше 0`) or you will ship false positives and the team will
   stop reading the output.

5. **Add boundary examples.** Threshold, threshold − 1, threshold + 1. An
   example deep inside the range verifies nothing. Every rule needs one example
   that actually fires it.

6. **Compile and check.**

   ```bash
   ftsc check <корпус>
   ftspec check <корпус>
   ```

7. **Verify the statutes actually bind.** In the `ftspec check` output read
   `summary.invariants` and `summary.gridPoints`. If `invariants` is smaller
   than the number of utilities you wrote, some of them matched nothing —
   go back to the contract above and fix the field names.

8. **Record each threshold as a decision** in `memory/` (skill `fts-memory`)
   and reference it from a comment in the constitution. A number without a
   recorded reason gets renegotiated in six months.

## Success Criteria

- [ ] `constitution.fts` sits at the corpus root and `ftsc check` passes.
- [ ] `ftspec check` reports `diagnostics: []` and `constitution: constitution.fts`.
- [ ] `summary.invariants` equals the number of statutes you wrote.
- [ ] Every statute has boundary examples and they pass.
- [ ] Every threshold has a decision file in `memory/`.
- [ ] A deliberately illegal spec is rejected with `FTSPEC_CONSTITUTION` — test
      this once, on a throwaway copy of the corpus. An untested gate is not a gate.

## Common Mistakes

| Mistake | What happens | Fix |
|---|---|---|
| Invariant field names differ from the specs' | Statute never applies, silently | Match names and types exactly |
| No `итог` field | The utility's result is never examined | Add `итог` |
| Statute fires on grid edges (negative sums) | False positives, output ignored | Add a guarding condition |
| One rule reads four fields | Diagnostic is unactionable | Split into rules |
| Threshold with no decision in `memory/` | Renegotiated every quarter | Write the decision |
| Statute written as prose in a comment | Checks nothing | Prose belongs in comments, statutes in utilities |
| Twenty statutes | Nobody reads them; the corpus becomes noise | Keep the ones that actually end arguments |

## The Honest Boundary

This is not a proof that the requirements are consistent. What is actually
checked:

- **type agreement** — field types across the corpus (`ftsc`);
- **condition intersection** — whether two rules can apply at once, on
  intervals over constants (`ftspec`);
- **invariants on a finite grid** — thresholds ± 1, capped at 1024 points per
  utility; a truncated grid is reported in `summary.gridTruncated`;
- **example coverage** — whether any example fires each rule.

Not checked, and the constitution must say so out loud:

- requirements with dependencies between fields beyond the comparisons FTS has;
- anything under a quantifier ("for every position in the order");
- anything depending on external data — a database, a rate, another service;
- whether the statute is *right*. A wrong threshold checked exactly is still wrong.

A violation between two grid points can be missed. This is an empirical
argument (rule behaviour changes at condition boundaries), not a logical one.
Write these limits into the project README; do not let them be discovered later.

## Verification

```bash
ftsc check <корпус>
ftspec check <корпус>
ftspec report <корпус>
```

Working corpus: `skills/software-development/fts-admit/example/`.
Language and case catalog: `https://courses.digitable.life/fts/`.
