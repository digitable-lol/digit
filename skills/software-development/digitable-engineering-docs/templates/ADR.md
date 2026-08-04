# ADR-[NNNN]: [Decision]

Date: YYYY-MM-DD
Status: proposed | accepted | superseded by ADR-[NNNN]
Owners: [roles allowed to change this decision]
Related SDD: [path or link to the capability this decision serves]

## Decision summary

One sentence, in a fixed shape:

We will `[decision]` so that `[property we want]`, accepting `[the main cost]`.

If the cost slot is empty the decision has not been made, only assumed.

## Context

Four things, in this order:

- the user or system pressure that forces a choice now;
- the constraints in force: budget, headcount, platform, contract, law;
- what doing nothing costs, written as an outcome rather than as "tech debt";
- which part of this change is hard or expensive to undo.

## Quality attributes

Rank only the attributes this decision actually moves. An attribute with no
scenario is a wish, not a requirement, and it cannot be weighed against
anything.

| Attribute | Scenario | Required response | Priority |
| --- | --- | --- | --- |
| Availability | When `[event]` | `[response]` within `[limit]` | High |
| Modifiability | When `[change arrives]` | `[scope]` changes without `[cost]` | Medium |

## Options

At least two, each one a choice a competent engineer could defend. An option
listed only so the preferred one wins is padding, and reviewers can see it.

### Option A - [name]

- Strengths:
- Weaknesses:
- Operational cost:
- Reversibility:
- Evidence:

### Option B - [name]

- Strengths:
- Weaknesses:
- Operational cost:
- Reversibility:
- Evidence:

## Trade-off matrix

Score with `+`, `0` or `-`, and write the scenario behind a score before you
write the score. Weights come from the quality-attribute table above, not from
taste.

| Criterion | Weight | Option A | Option B |
| --- | ---: | ---: | ---: |
| [criterion] | 3 | + | 0 |

## Decision

Name the option that won, the scope it covers, and the non-goals it does not
cover. Non-goals prevent the next reader from stretching this decision over
work it was never weighed against.

## Consequences

### Positive

-

### Costs and risks

-

### Follow-up

- [ ] [Action, owner, condition that makes it due]

## Fitness functions

How will CI, monitoring or code review notice that this decision has stopped
holding? Each line is something a machine or a reviewer can run.

- `[automated check]`
- `[metric and threshold]`

## Revisit trigger

Reopen this ADR when `[measurable condition]`. A calendar date is not a
trigger: it fires when nothing has changed and stays silent when everything
has.
