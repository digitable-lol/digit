# Architecture Review

System or change: `[name]`
Review date: YYYY-MM-DD
Decision owner: `[name or role]`
Reviewers: `[roles]`

## One-page brief

Written by the author before the review, not by the reviewers during it.

- User outcome:
- Scope:
- Non-goals:
- Expected load and growth:
- Data classification:
- Availability target:
- Recovery target:
- Budget and operational constraints:

## System map

One diagram showing users, trust boundaries, runtime components, data stores
and external dependencies. Every arrow names its protocol or delivery
mechanism; an unlabelled arrow hides the failure mode the review exists to
find.

## Review questions

Each box is either ticked with evidence or left open and turned into a finding.
A tick without evidence is worse than an empty box, because it stops the
question from being asked again.

### Product and failure

- [ ] Success is defined as a result the user can observe.
- [ ] Waiting, empty, partial and failed states each have a designed
      appearance.
- [ ] A failing dependency cannot be reported to the user as success.
- [ ] The user has a way back from every failure.

### Boundaries and data

- [ ] Every component and data set has a named owner.
- [ ] Client input cannot replace a value the server owns.
- [ ] Consistency requirements are stated per workflow, not globally.
- [ ] Idempotency keys exist wherever a retry can happen.
- [ ] Each data set has a retention rule and a deletion path.

### Reliability

- [ ] Timeouts add up to an end-to-end budget.
- [ ] The retry policy names its limit, its backoff and which errors are
      retryable.
- [ ] Queue growth and poison messages have a defined response.
- [ ] Restore has been exercised, not only backup creation.
- [ ] Degraded behaviour is something the user can live with.

### Security and privacy

- [ ] Trust boundaries and plausible attackers are named.
- [ ] Authorization is enforced at the resource, not only in the UI.
- [ ] Secrets live outside source and outside build artifacts.
- [ ] Logs carry no credentials and no personal data that is not needed.
- [ ] Public write endpoints have abuse controls.

### Delivery

- [ ] The migration is backward compatible, or correctly sequenced.
- [ ] Rolling back does not corrupt state written by the new version.
- [ ] Every feature flag has an owner and a removal condition.
- [ ] Build provenance ties the deployed bytes to a pushed commit.
- [ ] Verification in production has a named owner.

## Findings

| Priority | Finding | Evidence | Owner | Resolution |
| --- | --- | --- | --- | --- |
| P0-P3 | | | | |

## Decision

- [ ] Approved
- [ ] Approved with follow-up
- [ ] Rework required

Reason:
