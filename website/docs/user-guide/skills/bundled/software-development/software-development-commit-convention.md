---
title: "Commit Convention — Write commit messages in the house style: what and why"
sidebar_label: "Commit Convention"
description: "Write commit messages in the house style: what and why"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Commit Convention

Write commit messages in the house style: what and why.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/software-development/commit-convention` |
| Version | `1.0.0` |
| Author | Digit |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `git`, `commits`, `convention`, `writing`, `review` |
| Related skills | [`github-pr-workflow`](/user-guide/skills/bundled/github/github-github-pr-workflow), [`requesting-code-review`](/user-guide/skills/bundled/software-development/software-development-requesting-code-review) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Digit loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# House Commit Convention

## Overview

A commit message in these repositories answers one question: **what became true
that was not true before, and why it had to.** The subject states the change as
an outcome. The body gives the reason — the failure that prompted it, the
measurement that showed it, the decision behind it. Neither retells the diff;
the diff is already in the commit.

The rules below are not style preference. They were measured over **554
non-merge commits** across `digit`, `courses` and `flang-v6` — every commit by
the two people who write in this style. Where a rule cites a number, that number
is the corpus.

## When to Use

- Writing any commit message in `digit`, `courses`, `flang-v6`, or another repo
  of the owner's
- Rewording a message before `--amend`, or writing one for a PR squash
- Reviewing someone else's message, or your own from an earlier turn

**Don't use for:** upstream contributions to Hermes Agent. `CONTRIBUTING.md`
mandates plain Conventional Commits there, and `agent/oneshot.py` enforces a
72-character subject. Those rules govern the upstream project; this skill
governs the owner's own history.

## The Rule

**Subject — what became true.** Not the area you touched, not the noun you
added. The reader must learn the outcome without opening the diff.

| Instead of | Write |
|---|---|
| `docs: update documentation` | `Документация форка получает собственный адрес: docs.digitable.life` |
| `feat: add _readable function` | `kb: чужой каталог среди мест поиска больше не роняет сборку индекса` |
| `fix: resolve issue` | `Кнопка «Слушать» не появлялась в проде ни на одной странице` |
| `test: add new test cases` | `test_prompt_builder больше не отравляет соседний тест-файл` |

The right-hand column is real; the left is what a diff-reading generator
produces. Note what the good subjects share: each names a *state of the world*,
and several name the bug rather than the fix — `Кнопка «Слушать» не появлялась`
tells you more than "add audio flag" ever could.

**Body — why, and how you know.** Cite the thing that made the change
necessary: the red run, the outage, the user who would have been misled. 86% of
house commits have a body; the median is ~1200 characters. Small mechanical
changes go without one.

## Measured Shape

| Property | Corpus | Rule |
|---|---|---|
| Language | 85% Russian | Russian, unless the repo's history is English |
| Conventional-Commits prefix (`fix:`, `feat(scope):`) | 21.5% | Optional. Use when it genuinely classifies; never pad with it |
| Subject length | median 63, p90 78, max 95 | Aim under 80. Past 95 you are writing a body |
| Subject ends with a period | 0 of 554 | Never |
| Has a body | 86% | Yes, unless the change is trivially self-evident |
| Body line width | p90 = 81 | Wrap near 80 |

Prefix types actually used, in order: `feat` (71), `fix` (29), `docs` (11),
`test` (3), `perf` (2), `ci` (2), `refactor` (1).

## Grounding: the part that matters

Half of a good commit body is information **the diff does not contain.** Across
266 house commits with a real body, a mean **54%** of the body's content words
appear nowhere in the change they describe. A quarter of the numbers and 43% of
the identifiers in those messages are absent from their own diff.

That is correct, not a defect. `CI был красным пять коммитов` and `920 тестов, 7
упало` and `продажи стояли сутки при зелёном здоровье` are the reason the commit
exists, and none of them is derivable from the patch.

It is also the trap. **You may only write a fact you actually observed this
session** — a run you read, a log you opened, a file you searched. A plausible
number inferred from the diff is a fabricated measurement, and it is worse than
no number at all, because the register of these messages makes it look
authoritative forever.

If you did not measure it, either measure it now or say the weaker true thing.

## How to Run

Write the message to a file, then check it against its own diff:

```bash
python3 skills/software-development/commit-convention/scripts/check_commit_message.py \
    --message-file /tmp/msg.txt --staged
```

Exit code 1 means an error you must fix. The **GROUNDING** section is not an
error — it lists every number and identifier your message asserts that the diff
does not contain, so you can confirm each against something you really saw.

The checker passes all 554 house commits and rejects 84% of what a
diff-heuristic generator produces. It cannot see meaning: a well-formed but
empty subject still passes. It narrows the failure modes; it does not replace
reading your own message.

## Procedure

1. **Stage only your own paths.** Other agents work in these repos in parallel.
   `git add -A` will sweep up their half-finished work.
   ```bash
   git commit --only path/one path/two -F /tmp/msg.txt
   ```
   Done when `git diff --cached --name-only` lists your files and nothing else.

2. **Read the staged diff.** `git diff --cached`. You are about to describe it;
   describing it unread is how "update existing code" happens.

3. **Write the subject as an outcome.** State what is now true. Check it against
   the table above — if it names a file or a symbol rather than a consequence,
   rewrite it. Done when someone who has not seen the diff learns something
   real.

4. **Write the body: reason first.** What went wrong, what you measured, what
   you decided and what you rejected. Every specific fact must trace to
   something you observed this session. Done when a reader six months out knows
   why this was necessary.

5. **Run the checker.** Fix every ERROR. For each GROUNDING line, name to
   yourself where you saw it; delete what you cannot place. Done when exit code
   is 0 and every remaining fact is one you can point at.

6. **Set authorship if the owner requires it.**
   ```bash
   GIT_AUTHOR_NAME=Digitable GIT_AUTHOR_EMAIL=noreply@digitable.life \
   GIT_COMMITTER_NAME=Digitable GIT_COMMITTER_EMAIL=noreply@digitable.life \
   git commit --only <paths> -F /tmp/msg.txt
   ```

## Common Pitfalls

1. **Naming the artifact instead of the change.** `feat: add validate_commit_message
   function` — the diff already says a function appeared. Say what it now
   prevents.
2. **Inventing a measurement.** Writing "сотни коммитов" because the diff felt
   large, or a test count you did not run. If it has digits, you read it
   somewhere or you do not write it.
3. **One commit, several claims.** A subject joined by `|` or `and also` is two
   commits. Split them.
4. **Padding with a prefix.** `chore: update project files` is not more
   informative than `update project files`. The prefix is optional; the content
   is not.
5. **Restating the diff in the body.** "Added X to Y, changed Z" is a worse copy
   of `git show`. The body is for what `git show` cannot tell you.
6. **`git add -A` in a shared repo.** Commits other agents' work under your
   message and your authorship. Always `--only`.
7. **Assuming the upstream 72-char limit.** `agent/oneshot.py::validate_commit_message`
   rejects 22% of the owner's own subjects and every unprefixed one. It encodes
   the upstream convention, not this one.

## Verification Checklist

- [ ] `git diff --cached --name-only` lists only paths I changed
- [ ] Subject names an outcome, not a file or symbol
- [ ] Subject has no trailing period and is under 80 characters
- [ ] Body explains why, and does not narrate the diff
- [ ] Every number and identifier in the message is one I observed this session
- [ ] `check_commit_message.py --staged` exits 0
- [ ] Authorship env vars set if the owner requires them
