# Release Checklist

Release: `[product/version]`
Commit: `[full SHA]`
Owner: `[name or role]`

## Scope

- [ ] The release outcome and its non-goals are written down.
- [ ] The owning SDD or ADR matches what is actually shipping.
- [ ] Unrelated changes in the worktree are excluded.
- [ ] Generated artifacts state the commit they were built from.

## Quality gates

- [ ] Unit and integration suites are green.
- [ ] Critical end-to-end flows pass.
- [ ] Layout checked at the target widths.
- [ ] Keyboard navigation and visible focus checked.
- [ ] Empty, loading, error and retry states checked.
- [ ] Link and production-origin checks pass.
- [ ] No secrets, local paths or debug flags in the artifact.

Record what was run and what came back:

```text
[command] -> [result]
```

## Data and compatibility

- [ ] The migration is backward compatible, or correctly sequenced.
- [ ] Old clients and workers tolerate the new state.
- [ ] Retry and idempotency behaviour is covered.
- [ ] A backup exists and the restore path was exercised.

## Delivery

- [ ] The source state being released is pushed, not only local.
- [ ] The artifact came out of that exact state.
- [ ] Deployment mechanism and destination are named.
- [ ] A rollback artifact or configuration is available.
- [ ] Every feature flag has an owner and a removal condition.

## Production verification

- [ ] Health endpoint answers.
- [ ] The primary user journey completes.
- [ ] At least one failure and recovery path behaves as designed.
- [ ] Logs and metrics show no new sensitive data and no error spike.
- [ ] Assets and canonical URLs point at production origins.

Evidence:

```text
[URL, screenshot, query or run id]
```

## Rollback

Trigger:

```text
[condition someone can observe without guessing]
```

Procedure:

1.
2.
3.

## Release note

What changed, who benefits, what it still does not do, and how to report a
problem. Internal implementation language does not belong in user-facing copy.
