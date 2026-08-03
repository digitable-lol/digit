---
name: fts-gate
description: Use when an FTS specification must be certified before it drives a consequential decision. The gate answers a different question than `fts check`/`fts test` — not "is this specification self-consistent" but "is every premise it rests on admissible". Covers the refusal codes, the morphism library with its trust levels, the structural fallacy detector, how to read a refusal, and the hard limit that a green formalism does not make a domain law true. Use it before certifying, before adding a morphism, and whenever a specification is authored or edited by a model.
version: 1.0.0
author: Digitable
license: Apache-2.0
platforms: [linux, macos, windows]
metadata:
  digit:
    tags: [fts, verification, certification, morphisms, mcp, fallacies]
    category: software-development
    related_skills: [fts, verified-answers, digit, digitable-workbench]
---

# FTS Gate

## Overview

`fts check` and `fts test` prove a specification is consistent with itself. The
gate proves that every premise it uses comes from a human-reviewed list. These
are different guarantees, and the second one is the one that stops a confidently
wrong answer.

Key property: **refusal is a consequence of a typing failure, not a decision by a
model.** For a morphism absent from the library the partial function `M(m)` is
undefined, the `APPLY` typing rule does not apply, and no derivation exists.
There is nothing to argue with.

## When to Use

- A specification is about to drive a payment, a discount, a legal decision.
- A model authored or edited a `.fts` file.
- A new domain law is being added to the morphism library.
- A certificate must be re-checked independently of its producer.

Do not use the gate to decide whether a specification answers the user's
question. It does not know what was asked (§ "Pitfalls").

## Prerequisites

Node.js 20 or newer. No dependencies outside the vendored FTS
copy. Either the CLI (`fts-gate check`) or the MCP server with
two read-only tools: specification check and morphism listing.

## How to Run

```bash
node dist/src/cli.js check model.fts --context snapshot.json --pretty
node dist/src/cli.js morphisms --trust proposed --pretty
```

Exit codes: `0` certified, `1` refused (code in `.code`), `2` bad invocation.
stdout always contains exactly one JSON object.

Over MCP a refusal comes back as a **normal result**, not an error. `isError`
means a broken call or a broken library, never a refusal.

## Reading a refusal

Eight codes cover the pipeline in order: surface syntax, static
semantics, premise admissibility, executed examples and declared properties,
certificate construction, independent re-verification, library integrity, and an
unexpected-condition code that guarantees no exception escapes.

A separate structural fallacy detector adds eight codes —
circular premises, a morphism applied from the codomain side, an uninhabited
domain, one identifier with two meanings, an unhandled branch, a threshold with
no declared behaviour exactly on the boundary, an object carrying itself as a
state, a property never exercised by any example.

Every detector refusal carries a `verdict` field stating that a named fallacy
returns the thesis to "not shown" — it never claims the content is false.
Reproduce that distinction when you report the refusal to the user.

## The morphism library

`morphisms/manifest.json` is the single source of truth about admissible
premises: 29 entries, of which 18 are
human-reviewed and carry a `source` link, 3 are typed
compositions of reviewed ones, and 3 are model-suggested and
**may not serve as premises**.

Adding a morphism:

1. Declare it in a `.fts` module together with its law identifier.
2. Add a manifest entry with the identical signature and a `source` link to a
   document a reviewer can open.
3. Run the test suite — a test forbids the module and the manifest from drifting
   apart.

The whole signature is compared: domain, codomain and law id. You cannot borrow
trust from a reviewed name by substituting a different domain under it.

## Pitfalls

- **A green formalism does not make a law true.** The reference counterexample
  taxes an export shipment at the standard rate instead of zero; parse, test and
  certify all pass, and the detector finds none of the
  67 catalog positions in it. Only the manifest entry
  stops it.
- **Removing structural defects makes a false conclusion smoother, not truer.**
  Do not treat a clean detector run as evidence of correctness.
- **"No fallacy found" is not "no fallacy".** The detector optimises precision,
  not recall; 12 catalog positions are mechanically
  checked and 55 are not.
- **The gate does not know the question.** A flawless certificate about shipment
  stays flawless when the user asked about refunds. Routing is the application's
  job.
- **A symbolic certificate is not a proof of fact.** Require the verified
  evidence level for consequential decisions.
- Digests bind artifacts to each other; they do not establish authorship.

## Verification

- [ ] `status` is `certified`, not merely `valid`.
- [ ] Evidence level is `verified` for any consequential decision.
- [ ] Every new morphism has a `source` a reviewer actually opened.
- [ ] Refusals are reported with their code, and never as "this is false".
- [ ] External assumptions are quoted from the certificate, not summarised.
