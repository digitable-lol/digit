---
name: fts-admit
description: Check a new requirement against the corpus before coding.
version: 1.0.0
author: Digitable
license: Apache-2.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [fts, spec-driven, ftspec, requirements, review, verification]
    category: software-development
    related_skills: [fts, fts-specify, fts-constitution, fts-memory]
---

# FTS Admit

Check a new requirement against the already accepted corpus **before** a line of
code is written: rule conflicts, constitution breaches, example coverage,
functor consistency. Run `ftspec`, then explain the result to a human in the
language of the requirement, not of the tool.

## When to Use

- A spec written with `fts-specify` is ready and asks to enter the corpus.
- Before implementation, generation, or an estimate.
- In CI on every change under `specs/`, `constitution.fts`, `memory/`, `mapping/`.
- Someone claims "this contradicts what we agreed in March" and the claim needs
  a decidable answer.
- After deleting or renaming a spec — to find the decisions it orphaned.

## When NOT to Use

- Nothing changed in the corpus. This is a checker, not a linter to run for comfort.
- To decide whether the requirement is *right*. The tool answers "does it
  contradict what is already written", never "did the customer want this".
- Instead of talking to the customer. `FTSPEC_RULE_CONFLICT` is the start of a
  conversation, not its conclusion.
- On a corpus with no constitution, expecting invariants to be enforced —
  without `constitution.fts` only conflicts, duplicates and coverage are checked.

## Prerequisites

`ftsc` and `ftspec` on `PATH`. Install from the FTS language repository
(`github.com/the-homeless-god/fts`).

```
<корпус>/
  constitution.fts              роль определяется путём, не содержимым
  specs/<NNN-имя>/spec.fts      идентификатор спеки = имя каталога
  memory/NNNN-имя.fts
  mapping/*.fts
```

Russian directory names work too: `конституция.fts`, `спеки/`, `память/`,
`отображения/`. `ftspec` parses nothing itself — it delegates to `ftsc` and the
FTS core, and adds only the *role* of each file, taken from its path.

## Commands

```bash
ftspec check  <корпус>
ftspec admit  <корпус> --spec specs/003-promo
ftspec report <корпус> --spec 003-promo
ftspec report <корпус> --format json
ftspec check  <корпус> --step 5     # grid step
```

Output contract, identical to `ftsc`: JSON on stdout, diagnostics on stderr,
non-zero exit on error. `--spec` accepts `specs/003-promo` or plain `003-promo`.
`report` prints markdown by default — that is what you show a human.

Run `ftsc` first; a corpus that does not link cannot be reasoned about:

```bash
ftsc check <корпус>
ftsc graph <корпус>    # mermaid: categories and functors
```

## Diagnostic Codes

| Code | Severity | Means | First move |
|---|---|---|---|
| `FTSPEC_MEMORY_STALE` | error | a decision in `memory/` points at a file or category that no longer exists | re-read the decision with a human; the corpus moved without it |
| `FTSPEC_RULE_CONFLICT` | error | two rules from different specs can apply at once and demand different things | one of the two requirements is wrong — ask which |
| `FTSPEC_CONSTITUTION` | error | on some grid input the utility breaches an invariant | the input is printed; walk it with the author |
| `FTSPEC_UNCOVERED` | warning | a rule is fired by no example | ask for the number the customer expects |
| `FTSPEC_RULE_DUPLICATE` | warning | identical conditions and identical action in two specs | extract into a shared module |
| `FTSPEC_CORPUS_EMPTY` | error | no specs found | check the layout above |
| `FTSPEC_SPEC_UNKNOWN` | error | `--spec` names a spec that is not in the corpus | the identifier is the directory name |

Checks run in a fixed order: stale memory first (a corpus that cannot be read
makes conflict analysis meaningless), then conflicts, constitution, coverage,
duplicates.

## Procedure

1. **Link first.** `ftsc check <корпус>`. Fix `FTSC_*` before anything else —
   a functor whose object lost its image (`FTSC_FUNCTOR_OBJECT_MISSING`) means
   two requirement areas have already drifted apart.

2. **Admit the one spec.** `ftspec admit <корпус> --spec specs/<id>`. Its
   diagnostics are filtered to those involving this spec, so a corpus with
   pre-existing debt does not bury the new requirement.

3. **Read the machine-readable part.** Every diagnostic carries `details`:
   `overlap` (where conditions intersect), `witness` (a concrete input pair),
   `input` / `outcome` for constitution breaches. Use these, do not paraphrase.

4. **Explain in the language of the requirement.** Not "FTSPEC_RULE_CONFLICT in
   rule index 1", but:

   > Требование «промокод даёт фиксированные 100 ₽» и принятое в марте правило
   > «постоянному клиенту 5 % от чека» применимы одновременно — например, чек
   > 10 000 ₽ и постоянный клиент. Одно задаёт итог целиком, другое добавляет
   > к нему. Внутри одной спеки спор решает порядок правил; между двумя
   > спеками порядка не существует, поэтому результат зависел бы от того, чей
   > код выполнится первым. Нужно решение: промокод заменяет скидку
   > постоянного клиента или складывается с ней?

   Real output for that case, from `ftspec report`:

   ```
   ### Конфликты правил (`FTSPEC_RULE_CONFLICT`)

   - **ошибка** — правила «Постоянный клиент» из specs/001-discount/spec.fts и
     «Фиксированное промо» из specs/003-promo/spec.fts (объект «Покупка»)
     применимы одновременно при «постоянный клиент» = да: одно правило задаёт
     результат целиком, другое добавляет к нему
     - пример входа: `{"постоянный клиент":true}`
   ```

5. **Never "fix" a conflict by editing the older spec** without the author.
   A conflict is a disagreement between two accepted requirements; only a person
   may decide which one yields.

6. **Record the outcome.** Whatever was decided becomes a decision in `memory/`
   (skill `fts-memory`) — including "we deliberately allow both, promo wins".
   Otherwise the same conflict returns as a bug report.

7. **Re-run until clean**, then generate:

   ```bash
   ftspec admit <корпус> --spec specs/<id>
   ftsc build <корпус> --target typescript --out dist
   ```

## Reading the Summary

```jsonc
"summary": {
  "specs": 2,
  "constitution": "constitution.fts",
  "invariants": 3,       // statutes ACTUALLY applied — if lower than written, names do not match
  "gridPoints": 20,      // total inputs evaluated
  "gridTruncated": [],   // utilities whose grid hit the 1024-point cap — coverage is partial there
  "rules": 3,
  "rulesCovered": 3,
  "skippedPairs": 0      // rule pairs not analysable (condition compares a field to a non-constant)
}
```

`invariants`, `gridTruncated` and `skippedPairs` are where the method's honesty
lives. Report them; a green result with `skippedPairs: 12` means twelve rule
pairs were never compared.

## Success Criteria

- [ ] `ftsc check` links the corpus with no diagnostics.
- [ ] `ftspec admit --spec …` returns `ok: true`, `diagnostics: []`.
- [ ] `summary.invariants` matches the number of statutes in the constitution.
- [ ] `gridTruncated` is empty, or the truncation is named in the report.
- [ ] `skippedPairs` is 0, or each skipped pair was reviewed by a human.
- [ ] All model examples pass (`summary.examples`, every entry `n/n`).
- [ ] Every conflict resolved by a person, with a decision written to `memory/`.

## Common Mistakes

| Mistake | Consequence |
|---|---|
| Reporting the code instead of the case | The human cannot act; the tool gets ignored |
| "It compiles, therefore the requirement is right" | Compilation proves form, not intent |
| Silencing `FTSPEC_UNCOVERED` by deleting the rule | The requirement quietly disappears |
| Resolving a conflict by editing the older spec alone | An agreed requirement is lost without anyone noticing |
| Green result with a non-empty `skippedPairs` reported as full coverage | False confidence |
| Running `admit` without `ftsc check` | Diagnostics about a corpus that does not even link |
| No `mapping/` functor between related specs | Rules in different categories are never compared — conflicts stay invisible |

## The Honest Boundary

`ftspec` does **not** prove that the requirements are consistent. Precisely what
it does:

- **type agreement** across the corpus, via `ftsc` linking;
- **intersection of conditions** — over intervals of constants only. A condition
  comparing a field to another field is not analysable; the pair is counted in
  `skippedPairs` and reported, not hidden;
- **constitution invariants on a finite grid** — each threshold from the spec's
  own conditions and threshold ± `--step`, product capped at 1024 points per
  utility. The argument that violations live at boundaries is empirical, not
  logical: a breach strictly between two grid points can be missed;
- **example coverage** — by executing a probe utility through the FTS core, not
  by re-implementing the comparisons.

Structurally out of reach: quantifiers (FTS has no collections), dependencies
between fields beyond FTS's comparisons, and anything resting on external data.
Two `add` rules are never reported as a conflict — addition commutes — even
when a human would call the combined discount absurd. That judgment is the
human's, and this skill must say so instead of implying the corpus is proven.

## Verification

```bash
ftsc check  <корпус>
ftspec check  <корпус>
ftspec report <корпус> --spec specs/<id>
```

Clean corpus to compare against: `skills/software-development/fts-admit/example/`.
Checker internals and module/functor format: see the FTS language repository
(`github.com/the-homeless-god/fts`).
