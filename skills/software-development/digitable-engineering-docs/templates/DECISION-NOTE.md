# Decision note (memory pointer)

A memory entry for a choice that would cost more to undo than the work cost to
do. It does not replace an ADR: the full record - context, options, trade-off
matrix, fitness functions, revisit trigger - stays in the ADR, in the
repository the decision governs. The note exists for one job: to let the agent
reach that ADR from a different project, a year later, without remembering the
repository name.

## Routing

| The decision governs | Primary artifact | Memory note |
| --- | --- | --- |
| One repository | ADR in that repository | Pointer note (below) |
| Several repositories with one owner | ADR in the repository that changes most, linked from the others | Pointer note |
| The agent's own method, tooling or memory | Memory note only | Full note (below) |
| A vendor or platform constraint nobody here controls | Not a decision - a permanent note | Permanent note |

## Rules

- **No duplication.** A pointer note never restates the ADR body. Read the note
  without opening the ADR: if you learned the reasoning, it is a copy. A
  pointer longer than roughly fifteen lines, or one containing an options
  comparison, has already failed.
- **The pointer resolves.** Name the repository, the ADR path and the commit
  that introduced it. A branch name is not an address - branches move and get
  deleted. Resolve the path at that commit before saving the note.
- **The revisit trigger is a condition, not a date.** Copy the ADR trigger
  verbatim so a memory search surfaces it. "Reopen when p99 write latency
  exceeds 400 ms for a week" is a trigger; "review in Q1" is not, and the ADR
  template rejects it too.
- **Superseded, never edited.** When the decision changes, mark the old note
  superseded and link the new one. Reasoning that was correct at the time stays
  readable.

## Pointer note

```markdown
title:: [The decision, written as a claim]
zk-id:: [YYYYMMDDhhmm]
zk-type:: decision
status:: active
confidence:: verified
verified-on:: [YYYY-MM-DD]
source:: [repo]@[commit] [path to ADR]

### Thesis
[One sentence in the ADR's own shape: we do X so that Y, accepting Z.]

### Relations
Related with:: [[Note describing the pressure behind this]]

Contradicts:: [[The decision this one replaces]]

Tags:: #decision

### Description
Revisit when: [the ADR's trigger, copied word for word].
Full record: [path to ADR] in [repo], at [commit].
```

## Standalone note, when no repository owns the decision

Used for choices about the agent's own working method. Here the note is the
whole record, so it carries the parts of the ADR that still apply.

```markdown
title:: [The decision, written as a claim]
zk-id:: [YYYYMMDDhhmm]
zk-type:: decision
status:: active
confidence:: verified
verified-on:: [YYYY-MM-DD]
source:: [what settled it: a run, a measurement, an instruction from the owner]

### Thesis
We [decision] so that [property we want], accepting [cost].

### Description
Rejected: [alternative] - [the specific reason, with the number behind it].
Cost accepted: [what got worse; measured, or marked as an estimate].
Reversal cost: [what undoing this would take].
Revisit when: [measurable condition].
Evidence: [command, output or owner instruction, with a date].
```

## The rejection line, weak and usable

Weak - a reader can act on none of it, and nobody can show it to be wrong:

```text
Rejected: server-side sessions - did not scale.
```

Usable:

```text
Rejected: server-side sessions in Redis - at 40k concurrent users the working
set measured 11 GB against a 8 GB instance, and the next size up costs
+310 USD/month (load test 2026-06-02, numbers in the ADR). Signed tokens move
the cost to revocation latency, which is the cost accepted above.
```

The second version can be proved wrong. That is the entire difference, and it
is why the first version is worth nothing in memory.
