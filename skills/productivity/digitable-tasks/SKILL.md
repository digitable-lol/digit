---
name: digitable-tasks
description: "Use for the shared Digitable backlog; close tasks by uuid."
version: 1.0.0
author: Digit
license: MIT
platforms: [linux, macos]
metadata:
  digit:
    tags: [tasks, tracker, taskwarrior, backlog, uuid, digitable]
    related_skills: [digitable-portal, digitable-engineering-docs]
---

# Digitable Tasks

## Overview

The owner and several agents work one shared backlog — a Taskwarrior database
in a `.digitable-tasks` tree beside the checkouts. `digit tasks` is the way in.
Do not shell out to `task` directly: the wrapper exists to stop one specific
mistake, and calling around it puts the mistake back.

## When to Use

- Reading your assignment ("what is open in area X?")
- Recording that something became true, with what proves it
- Creating follow-up work you discovered but were not asked to do
- Closing work you finished

## The rule: uuid, never the number

Taskwarrior prints a small integer beside every pending task and accepts it as
a reference. **That integer is positional.** It is recomputed from the pending
set, so closing one task renumbers the ones after it. A reference that was
correct when you read it can point at a different task by the time you act on
it. On 2026-08-04 seven agents in a row closed the wrong task this way.

`digit tasks` therefore refuses positional ids and exits **2** when it sees
one. It refuses uuid *prefixes* too, and that part is not pedantry: Taskwarrior
resolves unique prefixes, and a bare number is itself a valid hex prefix, so
allowing prefixes would quietly re-admit the same mistake.

If you get exit 2, you do not need a workaround. You need the uuid, and
`digit tasks list` prints it.

## Commands

```bash
digit tasks list                              # every open task, uuid first
digit tasks list --project DIGIT              # one area
digit tasks list --status completed           # what is already closed
digit tasks list --json                       # machine-readable

digit tasks show <uuid>                       # one task with its annotations

digit tasks add --project DIGIT 'what should become true'
digit tasks note <uuid> 'what proves it'
digit tasks done <uuid> --note 'what proves it'
```

`add` prints the uuid of the task it created. `done --note` records the
annotation *before* changing the status, so a task is never left closed with no
evidence attached.

## Read the annotations before you work

`digit tasks show <uuid>` is not optional. Earlier agents leave what they
measured, what they decided, and what they deliberately did not do. Repeating
work that an annotation already reports as done is the most common way to waste
a session; contradicting a recorded decision is the most expensive.

## Closing: what an annotation has to contain

A verdict is a fact, not an impression. Before `done`, have one of:

- a command that was run and its output ("`scripts/run_tests.sh tests/x` — 42
  passed, 2.8s")
- a commit sha
- a `grep` that shows the thing exists, or does not

Write the numbers you measured, not the numbers a README claims. A course once
shipped invented figures from exactly that shortcut and had to be cleaned out.

State what you did *not* do in the same annotation. A task closed with an
unstated hole buys confidence it has not earned, and the next agent pays for it.

## What this skill does not do

- **It does not decide.** A task marked as the owner's decision stays open. Say
  what the decision needs, and leave it.
- **It does not reassign or schedule.** `digit tasks` covers listing, creation,
  annotation and closing. For anything else, ask.
- **It is not the kanban board.** `digit kanban` is Digit's own SQLite board for
  dispatching work to profiles. This is the human-owned backlog. They are
  different databases and different lifecycles; do not mirror one into the
  other.

## Where the tracker lives

Resolution order: `--data-dir`, then `tasks.data_dir` in `config.yaml`, then
`TASKDATA` (Taskwarrior's own variable, honoured so a user who already exports
it does not have to repeat themselves), then the first `.digitable-tasks/data`
directory found walking up from the working directory.

If none of those resolve, the command says so and names what it looked for —
it does not silently create an empty tracker of its own.
