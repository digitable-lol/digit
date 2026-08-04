# ADR-0014: Move PDF export to a queued worker with client-supplied idempotency keys

Date: 2026-08-04
Status: accepted
Owners: Platform team (delivery), Reports team (product surface)
Related SDD: `docs/sdd/report-export.md`

## Decision summary

We will render PDF exports in a background worker keyed by a client-supplied
idempotency key, so that a large export cannot time out and cannot be
duplicated by an impatient user, accepting that export stops being an
immediate download and becomes a job the user waits for.

## Context

- **Pressure.** Export runs inline in the HTTP handler. Over the last 30 days
  p99 export latency was 6.2 s, and 3.1 % of exports (1 840 of 59 300) crossed
  the 30 s gateway timeout. Every one of those returned a 504 to a user who had
  already waited half a minute.
- **Constraints.** The gateway timeout is set by the shared ingress and cannot
  be raised for one route. There is one Postgres primary; no queue exists yet,
  but Redis is already deployed for sessions. No new headcount.
- **Cost of doing nothing.** Users retry on 504. Support saw 214 tickets in
  30 days about "the same report three times in my inbox", because each retry
  produced a fresh document with a fresh id. The failure is not only slow, it
  produces wrong data.
- **Hard to reverse.** The idempotency key becomes part of the public API
  contract. Clients that send it will keep sending it, and export ids handed
  out to users end up pasted in tickets and links; the id scheme is effectively
  permanent.

## Quality attributes

| Attribute | Scenario | Required response | Priority |
| --- | --- | --- | --- |
| Availability | When a 900-page export is requested | The request is accepted and the document is produced, with no gateway timeout | High |
| Correctness | When the user submits the same export twice within 24 h | Exactly one document exists, and both requests return the same export id | High |
| Modifiability | When a new export format is added | Only the renderer changes; queueing, retry and idempotency stay untouched | Medium |
| Operability | When the worker is down for 15 min | Jobs accumulate and drain; nothing is lost and the backlog is visible on a dashboard | Medium |

## Options

### Option A - Keep rendering inline, stream the response

- Strengths: no new component; export stays a single request; no id scheme to
  design.
- Weaknesses: streaming keeps the connection open, so a 900-page export still
  dies at the ingress timeout. Solves the perception of slowness, not the
  timeout. Duplicates on retry remain, because nothing deduplicates.
- Operational cost: none beyond today.
- Reversibility: trivial - it is today's design.
- Evidence: ingress config caps `proxy_read_timeout` at 30 s globally; the
  platform team refused a per-route override in ticket PLT-812 because the
  value is enforced by the shared ingress chart.

### Option B - Queue the work, render in a worker, dedupe on an idempotency key

- Strengths: request returns in ~40 ms; render time stops being bounded by any
  HTTP timeout; the key makes retries free and makes duplicates impossible;
  backlog is measurable, so we get an early warning instead of a 504 spike.
- Weaknesses: export becomes asynchronous, so the UI needs a waiting state and
  a way to reach a finished document later. Adds a worker to deploy, monitor
  and roll back.
- Operational cost: one worker deployment, one queue-age alert, one dead-letter
  path. Estimated 3 engineer-days to build and 1 day of ops setup.
- Reversibility: medium. The worker can be deleted, but the idempotency key
  and export ids stay in the public API.
- Evidence: Redis is already in production for sessions with a 30-day incident
  free record. A spike rendered the largest known report (912 pages) in 71 s in
  a worker, well inside a job budget, and impossible inside a 30 s request.

### Option C - Buy a hosted rendering service

- Strengths: no rendering code to own; vendor absorbs scaling.
- Weaknesses: report bodies contain customer financial data, so the export
  payload would cross a trust boundary to a third party. Legal review has a
  6-week lead time and the current DPA does not cover a new sub-processor.
- Operational cost: quoted 640 USD/month at current volume, plus the legal
  review.
- Reversibility: low. Report layout would be expressed in the vendor's template
  language and would have to be rewritten to leave.
- Evidence: vendor pricing page and the current DPA sub-processor annex, which
  lists no rendering vendor.

## Trade-off matrix

| Criterion | Weight | Option A | Option B | Option C |
| --- | ---: | ---: | ---: | ---: |
| Large exports complete | 5 | - | + | + |
| No duplicate documents on retry | 5 | - | + | 0 |
| Keeps report data inside our trust boundary | 4 | + | + | - |
| Cost to build and operate | 2 | + | 0 | 0 |
| Time to ship | 2 | + | 0 | - |

Scores follow the scenarios above. Option A scores `-` on the first row because
the 30 s ingress ceiling is fixed and a 912-page render measured 71 s. Option C
scores `0` on duplicates because the vendor deduplicates per request id but our
own retry path would still create a second job.

## Decision

Option B. Exports are submitted as jobs; `POST /exports` accepts an
`Idempotency-Key` header, returns `202` with an export id, and repeats within
24 hours return the same id and the same document. Rendering runs in a worker
with a 10-minute job budget.

Non-goals: this decision does not cover scheduled or recurring exports, does
not change the report layout engine, and does not move any other synchronous
endpoint to the queue. Those need their own decisions and were not weighed
here.

## Consequences

### Positive

- Exports of any observed size complete; the 3.1 % timeout class disappears.
- Retries become safe, which removes the duplicate-document ticket class
  (214 tickets in the measured 30 days).
- Queue age gives a leading indicator of export trouble, replacing a lagging
  504 rate.

### Costs and risks

- Export is no longer an immediate download. The UI must carry a waiting state
  and a place to find finished exports, and users who liked the instant
  download will experience this as a regression.
- A worker outage is now invisible to the submitting user until the wait gets
  long. Mitigated by the queue-age alert, not by anything the user sees.
- Idempotency keys are stored for 24 h with the resulting export id; that is
  new state with its own retention rule.

### Follow-up

- [ ] Waiting and "your export is ready" states in the reports UI - Reports
      team - before the flag reaches 100 %.
- [ ] Dead-letter handling and a documented replay command - Platform team -
      before the flag reaches 50 %.
- [ ] Remove the inline render path and its tests - Platform team - once the
      flag has been at 100 % for two weeks with no rollback.

## Fitness functions

- `tests/export/test_handler_is_thin.py` fails if the request handler imports
  or calls the renderer - this is what stops the inline path from growing back.
- `tests/export/test_idempotency.py` asserts that two `POST /exports` with the
  same key return the same export id and that exactly one row exists.
- Alert: queue age p95 above 120 s for 10 minutes at steady input rate.
- Alert: dead-letter depth above 0 for 15 minutes.
- Dashboard panel: exports created per idempotency key. A value above 1.0
  means deduplication has broken, and it is checked in the weekly review.

## Revisit trigger

Reopen this ADR when either holds:

- p95 queue age stays above 120 s for three consecutive days at or below
  today's input rate, which means one worker pool is no longer the right shape;
- exports over 500 pages exceed 5 % of monthly volume, which would push the
  10-minute job budget from comfortable to tight.

Not "review next quarter": that fires when nothing has changed and stays quiet
when everything has.
