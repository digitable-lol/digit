---
name: digitable-engineering-docs
description: "Use when writing an ADR, SDD, architecture review, postmortem or release checklist: produce the Digitable form with its real sections, not a generic one. Triggers on architecture decision record, design or spec document, design review, incident writeup, release gate, and on any decision durable enough that reversing it costs more than redoing the work."
version: 1.0.0
author: Digitable
license: MIT
platforms: [linux, macos, windows]
metadata:
  digit:
    tags: [digitable, adr, sdd, architecture-review, postmortem, release-checklist, engineering-docs]
    related_skills: [digitable-workbench, plan, requesting-code-review]
---

# Digitable Engineering Documents

## Overview

Digitable engineering documents are not free-form prose with a heading on top.
Each type has fixed sections, and each section exists because a specific class
of mistake gets made when it is missing. Write the sections in order; a section
you cannot fill is a finding, not a formatting problem.

Two properties run through every type in this family and are worth stating
once:

- **A claim carries its evidence.** A number without a measurement, a score
  without the scenario behind it, an estimate not marked as an estimate - all
  of these read as facts and none of them can be checked.
- **A rule you cannot catch someone breaking is decoration.** Triggers are
  measurable conditions, acceptance criteria are observable, fitness functions
  are things CI or a monitor actually runs.

## When to Use

- The user asks for an ADR, SDD, design doc, spec, architecture review,
  postmortem or release checklist.
- A decision is being made that would cost more to reverse than the work cost
  to do.
- A design is about to be committed to and needs other people's eyes.
- An incident affected users, or something is about to ship.

Don't use for: a plan for the current task (use `plan`), a code review (use
`requesting-code-review`), or a throwaway note about a reversible choice - a
document with empty sections is worse than no document.

## Routing: which document

| Situation | Document | Why not the other one |
| --- | --- | --- |
| A choice between real options, expensive to reverse, governing one repository | ADR in that repository | An SDD describes what gets built; it has no place to weigh options that lost |
| A capability to build: user-visible behaviour, states, contract, acceptance | SDD | An ADR does not carry user scenarios or acceptance criteria |
| A design about to be committed to, needing other people's eyes | Architecture review | Findings and priorities are the review's output; an ADR records a conclusion, not an inspection |
| Users were affected by a failure | Postmortem | A postmortem is about conditions and safeguards, not about a decision |
| Something is about to ship | Release checklist | The gate is a list of verified facts, not a narrative |
| A decision governs several repositories | ADR in the repository that changes most, linked from the others | Two ADRs on one decision drift apart |
| A durable choice about the agent's own method or tooling | Decision note only (`templates/DECISION-NOTE.md`) | No repository owns it, so there is no place to put the ADR |
| A vendor or platform constraint nobody here controls | A permanent note, not a decision | You did not decide it; recording it as a decision implies it can be revisited |

An ADR and an SDD are often both right. The split is: **the ADR says why this
option and when to reopen it; the SDD says what the thing does and how you know
it is accepted.** The ADR names its `Related SDD`, the SDD lists the
alternatives it inherited. Neither restates the other.

## ADR - what each section must contain

Template: `templates/ADR.md`. Worked example, every section filled:
`references/ADR-0014-queued-pdf-export.md`.

- **Decision summary.** One sentence in a fixed shape: *we will `[decision]` so
  that `[property we want]`, accepting `[the main cost]`.* The shape is not
  decoration - an empty cost slot means the decision has not been made, only
  assumed.
- **Context.** Four items in order: the pressure forcing a choice now; the
  constraints in force; what doing nothing costs, as an outcome rather than as
  "tech debt"; and which part is hard or expensive to undo.
- **Quality attributes.** Only the attributes this decision moves, each with a
  scenario, a required response and a priority. An attribute with no scenario
  is a wish and cannot be weighed against anything.
- **Options.** At least two, each defensible by a competent engineer. Per
  option: strengths, weaknesses, operational cost, reversibility, evidence.
- **Trade-off matrix.** Weighted criteria scored `+` / `0` / `-`. Write the
  scenario behind a score before writing the score; weights come from the
  quality attributes, not from taste.
- **Decision.** The option that won, its scope, and explicit non-goals. The
  non-goals stop the next reader from stretching this decision over work it was
  never weighed against.
- **Consequences.** Positive; costs and risks; follow-up as checkboxes with an
  owner and the condition that makes each due.
- **Fitness functions.** How CI, a monitor or a reviewer notices that the
  decision no longer holds. Each line is something that actually runs.
- **Revisit trigger.** A measurable condition. Not a calendar date: a date
  fires when nothing has changed and stays silent when everything has.

## SDD - what each section must contain

Template: `templates/SDD.md`.

- **Context.** The situation today, in observable evidence. Do not smuggle the
  preferred solution into the problem statement.
- **Outcome.** One measurable sentence about what becomes possible for the
  user.
- **Decision.** The chosen behaviour and architecture, plainly and without
  hedging.
- **User scenarios.** Numbered steps from starting state through action,
  feedback and persisted result, ending with where a failure leaves the user.
- **Product contract.** Entry point, primary action, success, waiting, empty,
  recoverable failure, terminal failure, accessibility, responsive behaviour.
  Every line is a state a user can reach; a blank line here becomes an
  unspecified screen later.
- **System boundary.** A diagram, plus - for every arrow - owner, input,
  output, timeout, retry behaviour and privacy classification.
- **State model.** The transitions, which of them are idempotent, and which
  store wins when two disagree.
- **Data and privacy.** Collected data, purpose, storage, retention, public
  fields, sensitive fields, deletion and export.
- **Security.** Trust boundaries, authn/authz, values the server owns and the
  client may never set, rate limits, secret storage, audit trail.
- **Alternatives.** A table: alternative, benefit, cost, why not selected.
- **Rollout and rollback.** Flag, migration, compatibility, observability,
  rollback trigger, rollback procedure.
- **Acceptance criteria.** Numbered and observable, checkable by someone who
  did not write the document: user behaviour, failure and recovery, an
  automated test, an accessibility or responsive check, a verification in
  production.
- **Open questions.** Each with an owner and the date the answer is needed.

## Architecture review - what it demands

Template: `templates/ARCHITECTURE-REVIEW.md`.

The author writes the one-page brief *before* the review: outcome, scope,
non-goals, expected load and growth, data classification, availability and
recovery targets, budget constraints. Then a system map in which every arrow
names its protocol or delivery mechanism - an unlabelled arrow hides the
failure mode the review exists to find.

The body is five checklists - product and failure, boundaries and data,
reliability, security and privacy, delivery. Each box is either ticked with
evidence or left open and turned into a finding. **A tick without evidence is
worse than an empty box**, because it stops the question from ever being asked
again.

Output is a findings table (priority P0-P3, finding, evidence, owner,
resolution) and one of three decisions: approved, approved with follow-up,
rework required - with a reason.

## Postmortem and release checklist

`templates/POSTMORTEM.md` is blameless by construction: it explains which
conditions and which missing safeguards allowed the incident, not who performed
the last visible action. It insists on measured impact with estimates labelled
as estimates, one timezone in the timeline, and a causal chain that does not
stop at the first human action or the first crashed component. Corrective
actions must lower probability, blast radius or recovery time - "be more
careful", "write documentation" and "add an alert" are not actions unless the
response to that alert has been tested.

`templates/RELEASE-CHECKLIST.md` is a gate, not a narrative: scope, quality
gates, data and compatibility, delivery, production verification, rollback, and
a release note written in user language. Two lines carry most of its weight -
the artifact was built from the pushed source state, and a redirect or a queued
job is not a confirmed result.

`templates/DECISION-NOTE.md` connects the family to cross-session memory: a
short pointer note that names repository, ADR path and commit, copies the
revisit trigger verbatim so a memory search surfaces it, and never restates the
ADR body.

## Common Pitfalls

Each of these is why a section exists. Check the draft against them before
handing it over.

1. **A decision with one option.** Nothing was decided; a preference was
   recorded. Two options, both defensible, or the trade-off matrix is theatre.
2. **A losing option written to lose.** Padding, and reviewers see it. If an
   option cannot be stated in a form its advocate would accept, it is not an
   option and does not belong in the list.
3. **An SDD with no acceptance criteria.** Nobody can tell whether it shipped.
   Criteria must be observable by someone who did not write the document.
4. **"Review in Q1" as a revisit trigger.** It fires when nothing changed and
   stays silent when everything did. Use a measurable condition.
5. **Scores before scenarios.** A `+` written before its justification is a
   vote, and the weights then get tuned until the preferred option wins.
6. **A tick with no evidence in a review.** Converts an open question into a
   settled one at zero cost.
7. **An unlabelled arrow in a diagram.** Hides the protocol, and with it the
   timeout, the retry and the trust boundary.
8. **A quality attribute with no scenario.** "Must be fast" cannot be traded
   against anything and never loses an argument.
9. **A postmortem that stops at a person.** Naming the last actor ends the
   inquiry exactly where the useful part starts.
10. **A corrective action that says "be more careful".** No verification, so
    nothing changed.
11. **Missing non-goals in a decision.** The decision then gets stretched over
    work it was never weighed against.
12. **A memory note that restates its ADR.** Two copies drift; the pointer must
    be findable, not complete.

## Provenance

The templates in `templates/` are original skeletons written for Digit. They
reproduce the *form* of the Digitable Workbench engineering template set -
section names, field labels, document order - because that form is the point of
this skill. They are not copies of the Workbench files: the Workbench archive
is a paid product under a proprietary licence that forbids publishing its
templates in an open repository, and Digit is public. If the user owns the
archive, its own template files remain the authority; this skill is what lets
the agent produce the same form without it.

## Verification Checklist

- [ ] The document type matches the routing table, and any companion document
      is named rather than duplicated.
- [ ] Every section of the chosen template is present and filled, or its
      absence is recorded as an open question with an owner.
- [ ] Every ADR has at least two defensible options and a trade-off matrix
      whose scores follow written scenarios.
- [ ] Every ADR has fitness functions that run and a revisit trigger that is a
      measurable condition.
- [ ] Every SDD has numbered, observable acceptance criteria and a filled
      product contract.
- [ ] Every review finding has evidence, an owner and a priority.
- [ ] No number appears without its measurement, and estimates are labelled.
