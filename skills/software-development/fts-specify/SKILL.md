---
name: fts-specify
description: Turn a business requirement into an FTS specification.
version: 1.0.0
author: Digitable
license: Apache-2.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [fts, spec-driven, requirements, specification, ddd, ftspec]
    category: software-development
    related_skills: [fts, fts-constitution, fts-admit, fts-memory]
---

# FTS Specify

Turn one incoming requirement — a paragraph from a customer, a ticket, a
meeting note — into a specification written as an executable FTS model, before
any code is written.

## When to Use

- A new requirement arrives and must enter the corpus.
- An existing markdown spec has to become checkable.
- Two people read the same requirement differently and the argument needs a
  decidable form.
- A rule has to drive generated code, tests, tables, or forms from one source.

## When NOT to Use

- The requirement is about UI layout, retries, transactions, or orchestration.
  FTS describes data, pure decisions over that data, and permitted transitions.
- The decision needs a lookup, a rate, or a balance from outside. Split it: the
  external fact is an input field, the decision is the spec, the fetch is the
  application's job.
- The requirement is an experiment nobody has agreed to yet. Specify what was
  agreed; use `fts-memory` for what was rejected.
- The domain has no numbers or flags at all — a pure workflow with no decisions.
  Model the transitions with morphisms only, and say so.

## Prerequisites

- `constitution.fts` exists at the corpus root (skill `fts-constitution`).
  Without it `ftspec` still checks conflicts and coverage, but nothing enforces
  project-wide limits.
- `ftsc` and `ftspec` available on `PATH`. Install from the FTS language
  repository (`github.com/the-homeless-god/fts`).
- One spec lives at `specs/<NNN-краткое-имя>/spec.fts`. The directory name is
  the spec identifier used by `ftspec admit --spec`.

## Reading a Requirement Into Constructs

| In the requirement | Construct | Note |
|---|---|---|
| a noun with fields ("purchase", "invoice") | `объект` / `object` | shape of data |
| "if …, then …" about a number or money | `правило` inside `утилита` | |
| "never more / never less than" | `свойство` / `property` | postcondition on every run |
| "not allowed until …" | `морфизм` / `morphism` | permitted state transition |
| a concrete case from the conversation | `пример` / `example` | executable, must converge |
| "this specific order may ship" | `теорема` / `theorem` | derivation from one fact |
| the same noun in another area | `функтор` in `mapping/` | checked mapping between areas |

The category-theory apparatus, without mystique:

- **category** = one bounded context of requirements;
- **object** = a shape of data;
- **morphism** = a permitted transition between states — it computes nothing;
- **functor** = a mapping of one requirement area onto another with a shape
  check: total on objects and on fields, type-compatible, and consistent on
  morphisms (`F(f): F(a) → F(b)`).

A functor is an engineering check, not decoration. It also serves as the
*dictionary* `ftspec` uses to compare rules written in different words.

## Procedure

1. **Fix one bounded context.** One incoming requirement → one file → one
   category. If the requirement spans two areas, write two specs and a functor.

2. **Copy the customer's words.** Field and rule names are the ubiquitous
   language and become identifiers in generated code. Quote multi-word names
   with `«»` or `"`; a single identifier needs no quotes.

3. **Choose the surface, then stay on it.** `templates/spec.fts` (Russian) and
   `templates/spec.en.fts` (English) are the same document. English reserved
   phrases are rewritten line by line into the Russian ones before parsing, so
   both compile to the identical canonical model. Quoted domain names are never
   translated — `object "Покупка"` inside an English file is legal and often
   correct. You cannot mix surfaces in one file; the language is decided by the
   first non-comment line.

4. **Write the object.** Only the fields this decision needs. Every field of
   the input object must be supplied by every example, so unrelated fields make
   examples unreadable. Types: `числом/number`, `деньгами/money`,
   `признаком/boolean`, `датой/date`, `строкой/string`; optional is
   `иногда является` / `may be`.

   Keep state fields (`является состоянием` / `is state`) in a **separate**
   object: a state cannot be given a meaningful value in a calculation example.

5. **Write the utility.** `начинает с` seeds the accumulator; then **every**
   rule whose condition holds runs, in declaration order. FTS has no `else` —
   order is priority. `то добавить` accumulates, `то результат равен`
   overwrites.

6. **Write the property.** Carry the constitution's limit that touches this
   utility into a `свойство`. Never clamp a broken result with a rule —
   declare the property and let the violation fail loudly.

7. **Write examples: below the boundary, on it, above it, and one where two
   rules fire at once.** An example deep inside a range verifies nothing. Every
   rule needs an example that actually fires it, or `ftspec` reports
   `FTSPEC_UNCOVERED`.

8. **Add a morphism and a theorem only if the requirement contains a
   permission.** A theorem checks the *shape* of the derivation: the field type
   must equal the first morphism's domain, each codomain the next domain, and
   the last codomain the `следовательно` clause. The truth of the fact itself
   comes from the data and is not proved. One theorem per document.

9. **Compile, then admit.**

   ```bash
   ftsc check <корпус>
   ftspec admit <корпус> --spec specs/003-promo
   ```

   Diagnostics mean the requirement, not the file, needs work. Hand them to the
   skill `fts-admit` — it explains them to a human and drives the fix.

10. **Generate only after admission.**

    ```bash
    ftsc build <корпус> --target typescript --out dist
    ftsc targets      # available backends
    ```

    Generated files are artifacts. Edit the `.fts`, regenerate.

## Success Criteria

- [ ] One category, names taken from the customer.
- [ ] `ftsc check` passes for the whole corpus, not just the new file.
- [ ] `ftspec admit --spec …` returns `ok: true` with `diagnostics: []`.
- [ ] Every rule fires in at least one example; boundaries are covered.
- [ ] Constitution limits appear as properties on the relevant utilities.
- [ ] External effects (HTTP, SQL, mail) are absent from the model.
- [ ] Whatever the model does *not* say is written in the file header.

## Common Mistakes

| Mistake | Consequence |
|---|---|
| Two contexts in one category | The spec cannot be admitted or reused separately |
| Extra fields on the utility's input object | Every example grows; nobody maintains them |
| A state field on the utility's input | `FTS_UTILITY_EXAMPLE_FIELD` — examples cannot fill it |
| Exceptions written as an overwriting rule | `FTSPEC_RULE_CONFLICT` against another spec: between specs there is no rule order |
| Examples only in the middle of ranges | Boundary bugs survive to production |
| Result clamped by a rule instead of a property | The breach becomes invisible |
| Renaming the customer's words to "cleaner" ones | The functor and the conversation both break |
| Effects (send mail, write row) in the model | FTS does not do that; the claim would be false |

## The Honest Boundary

`ftspec` checks type agreement, intersection of rule conditions over intervals
of constants, constitution invariants on a finite grid (thresholds ± 1, capped
at 1024 points), and example coverage. It does **not** prove that the
requirements are consistent.

Out of reach, by construction:

- dependencies between fields beyond FTS's comparisons;
- quantifiers — there are no collections in FTS, so "every line of the order"
  cannot be stated;
- external data — rates, balances, another service's answer;
- whether the requirement is what the customer meant. A compiled model can be
  precisely wrong.

A conflict between two grid points can be missed. Every spec must name, in its
header, what it deliberately does not say — that is the part reviewers read.

## Verification

```bash
ftsc check <корпус>
ftsc graph <корпус>     # mermaid: categories and functors
ftspec admit <корпус> --spec specs/<id>
```

Working corpus: `skills/software-development/fts-admit/example/`.
Module and functor format: see the FTS language repository
(`github.com/the-homeless-god/fts`).
Language and case catalog: `https://courses.digitable.life/fts/`.
