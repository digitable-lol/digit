# Incident Postmortem: [short title]

Incident window: `[start]` - `[end]`
Severity: `[level]`
Status: draft
Incident lead: `[role]`

This document is blameless. It explains which system conditions and which
missing safeguards let the incident happen, not which person performed the
last visible action.

## Executive summary

Three sentences:

1. what users experienced;
2. for how long and how widely;
3. what restored service.

## User impact

- Affected users and workflows:
- First impact:
- Last impact:
- Wrong or unavailable results:
- Data loss or privacy impact:
- Support load:

Use measured values. Anything estimated is labelled as an estimate.

## Timeline

One timezone throughout, named once at the top.

| Time | Observation or action | Evidence |
| --- | --- | --- |
| HH:MM | | |

## Detection

- How did we find out?
- Which signal should have told us earlier?
- Why did existing monitoring not raise an actionable alert?

## Technical narrative

The causal chain from the starting condition to the user-visible impact:

```text
starting condition
  -> the transition nothing prevented
  -> the safeguard that was absent or disabled
  -> what the user saw
```

Do not stop at the first human action or the first component that crashed;
both are usually the middle of the chain, not its start.

## Contributing conditions

- Product and design:
- Architecture:
- Code:
- Configuration:
- Delivery:
- Observability:
- Documentation and process:

## What worked

-

## What made recovery harder

-

## Corrective actions

Every action must lower probability, blast radius or recovery time. "Be more
careful", "write documentation" and "add an alert" are not actions unless the
response to that alert has been tested.

| Priority | Action | Owner | Verification | Status |
| --- | --- | --- | --- | --- |
| P0-P3 | | | | open |

## Verification

- Regression test:
- Failure injection:
- Signal in production:
- Rollback or restore drill:

## Lessons for other systems

Which guard, design-system rule or delivery check should be adopted elsewhere
before the same shape of incident finds it?
