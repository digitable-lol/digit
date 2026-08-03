---
name: fts-memory
description: Keep accepted decisions as checkable FTS models.
version: 1.0.0
author: Digitable
license: Apache-2.0
platforms: [linux, macos, windows]
metadata:
  digit:
    tags: [fts, spec-driven, decisions, adr, memory, ftspec]
    category: software-development
    related_skills: [fts, fts-constitution, fts-specify, fts-admit]
---

# FTS Memory

Keep the project's accepted decisions: why a rule is what it is, which
alternatives were rejected, what has gone stale. A decision is itself an FTS
model — facts plus a theorem — not a note in a document, which is why an
orphaned decision can be found by machine.

## When to Use

- A threshold entered the constitution or a spec and needs a recorded reason.
- A conflict found by `fts-admit` was resolved by a human — record what was
  decided *and* what was rejected.
- An old decision is being overturned; the previous one must be marked, not deleted.
- A spec is deleted or renamed and its decisions must be re-read.
- Someone asks "why 30 and not 50" and nobody remembers.

## When NOT to Use

- For the rule itself. The rule lives in a spec or in the constitution; memory
  records the *reasoning*. Duplicating the rule creates two sources of truth.
- For a decision nobody made. Do not write down an option someone floated.
- For work notes, meeting logs, or a task list.
- As a replacement for the constitution. Memory explains; it does not enforce.

## Prerequisites

- Corpus with `constitution.fts` at the root and `specs/`.
- Decisions live in `memory/NNNN-краткое-имя.fts` (or `память/`). The role is
  decided by the path.
- Numbers are monotonic and never reused. An overturned decision keeps its file
  and its number; the new one references it.

## What Is Prose and What Is Model

Prose lives in `//` comments, the machine-checkable part in the model. The split
is not cosmetic:

- a human reads the comment;
- `ftspec` executes the model and finds the orphan.

Required comment sections (template `templates/decision.fts`):

```
ЧТО РЕШИЛИ        one sentence, checkable, not "we strive to"
СТАТУС            принято | отменено решением NNNN
КОГДА             date
ПОЧЕМУ ИМЕННО ТАК the reason, and where the number came from
АЛЬТЕРНАТИВЫ      numbered: option — reason for rejection
ОПИРАЕТСЯ НА      full rule names: «Категория».«Правило»
ЧТО НЕ ГОВОРИТ    explicit boundaries of the decision
```

## The Machine-Checkable Part

Two independent mechanisms, and they answer different questions.

**1. `использует` — the primary one.** `ftspec` verifies it *before* assembling
the corpus: if the file is gone, or the category in it is now named differently,
the decision is reported as `FTSPEC_MEMORY_STALE`.

```fts
модуль «Решение 0003»
  использует «Скидки» из «../specs/001-discount/spec.fts»
```

One reference per spec the decision was taken for. This is the mechanism that
catches "the corpus moved on without the decision".

**2. A utility scoring staleness signals** — for facts a path cannot express:
whether alternatives were recorded, whether another decision overturned this one,
whether the cited rules still exist.

```fts
утилита «Оценить признаки устаревания»
  принимает «Ссылки решения»
  возвращает число
  начинает с 0

  правило «Решение ссылается на исчезнувшее правило»
    если «правил найдено в корпусе» меньше поле «правил упомянуто»
    то добавить 1
```

The same on the English surface — one parser, one canonical model:

```fts
utility "Score staleness signals"
  accepts "Decision references"
  returns number
  starts with 0

  rule "Decision cites a rule that vanished"
    if "правил найдено в корпусе" is less than field "правил упомянуто"
    then add 1
```

Quoted domain names are never translated; the choice of surface is a team
decision, not a semantic one, and cannot be mixed inside one file.

**3. A morphism plus a theorem** — states that the rule is fixed *by this
decision*. Shape-checked: the field type must equal the morphism's domain, and
the last codomain the `следовательно` clause. One theorem per document.

```fts
морфизм «Принятое решение обязательно для новых спек»
  если «Решение принято»
  то «Правило зафиксировано»

теорема «Правило зафиксировано решением 0003»
  дано «Решение» имеет принято равное да
  в данных решения найти где номер равен «0003»
  по морфизму «Принятое решение обязательно для новых спек»
  следовательно «Правило зафиксировано»
```

## Procedure

1. **Take the number.** `ls memory/` — next free, never reused.
2. **Copy `templates/decision.fts`** to `memory/NNNN-краткое-имя.fts`.
3. **Fill the comment sections.** The alternatives section is not optional: a
   decision that does not record what was rejected cannot be revisited
   deliberately, and the template's utility counts an empty list as a staleness
   signal.
4. **Add `использует`** for every spec the decision governs.
5. **List the rules relied on by full name** in the comment, and put their count
   into `«правил упомянуто»`.
6. **Check.**

   ```bash
   ftsc check <корпус>
   ftspec check <корпус>
   ```

7. **When overturning:** do not delete. In the old file set
   `СТАТУС: отменено решением NNNN` and `«отменено другим решением» равен да`
   in its examples' semantics; the new decision names the old one in its
   `ПОЧЕМУ ИМЕННО ТАК`. History that gets deleted stops being an argument.
8. **On `FTSPEC_MEMORY_STALE`:** re-read the decision with a person. Do not
   repair the import. A broken reference is a signal, not a build failure —
   the corpus changed and the reason may no longer hold.

## Success Criteria

- [ ] Every threshold in the constitution has a decision file.
- [ ] Every decision has recorded alternatives with reasons.
- [ ] Every decision has `использует` pointing at the specs it governs.
- [ ] `ftspec check` reports no `FTSPEC_MEMORY_STALE`.
- [ ] Model examples pass (`summary.examples` for `memory/*` shows `n/n`).
- [ ] Overturned decisions are marked, not deleted.
- [ ] Each decision states what it deliberately does not cover.

## Common Mistakes

| Mistake | Consequence |
|---|---|
| The rule is duplicated in the decision | Two sources of truth; they drift |
| No alternatives recorded | In a year the same options get re-litigated |
| No `использует` | An orphaned decision is undetectable by machine |
| Overturned decision deleted | The reasoning is gone; the argument reopens |
| Numbers reused after deletion | Cross-references start pointing at the wrong file |
| `FTSPEC_MEMORY_STALE` fixed by editing the path | The signal is silenced without re-reading the decision |
| Decision written as a spec (with rules) | `ftspec` treats it by path as memory, so its rules are checked by nothing |

## The Honest Boundary

What is genuinely machine-checked here:

- the reference from a decision to a file and to a category name;
- the shape of the theorem's derivation;
- the staleness-signal utility's own examples.

What is not:

- whether the *reason* is still true. FTS compares numbers; it cannot know the
  margin has changed. The `использует` reference catches a moved corpus, never
  a moved market;
- whether the recorded alternatives are the real ones. The utility counts them,
  it does not read them — FTS has strings as a field type, not as data to
  compute over;
- whether the decision is being followed. That is `fts-admit`'s job, and only
  to the extent the constitution encodes it.

A decision model tells you *that* something must be re-read, never *what* the
new answer is. Say this when reporting `FTSPEC_MEMORY_STALE`, or the code will
be read as "the tool knows the decision is wrong".

## Verification

```bash
ftsc check  <корпус>
ftspec check  <корпус>
ftspec report <корпус>
```

Working corpus: `skills/software-development/fts-admit/example/` — decision
`memory/0003-discount-cap.fts` with a live `использует` reference to spec 001.
