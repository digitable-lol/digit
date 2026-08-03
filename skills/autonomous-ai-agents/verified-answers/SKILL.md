---
name: verified-answers
description: Use when an answer must be traceable to a source rather than generated — Digit's verified mode. Covers the three allowed content sources (deterministic tool output, verbatim corpus quote with an anchor, FTS certificate over reviewed morphisms), the A/B mode contract and its no-silent-downgrade rule, the deterministic refusal gates, and how to word a refusal. Use it before claiming any fact about Digitable courses, prices, tools or FTS, and whenever the user asks whether an answer was verified.
version: 1.0.0
author: Digitable
license: MIT
platforms: [linux, macos, windows]
metadata:
  digit:
    tags: [digit, verification, grounding, refusal, rag, fts]
    category: autonomous-ai-agents
    related_skills: [digit, fts-gate, digit-tools-core, digitable-courses, fts]
---

# Verified Answers

## Overview

In verified mode the model produces a **choice**, never a **fact**. Content comes
from трёх sources only. A selection error yields "wrong answer" or
"not found". A generation error yields an invented fact — that channel is closed
by construction, so do not reopen it by paraphrasing.

| Source | What it yields | Who produces it |
|---|---|---|
| Deterministic tool | a computed result | `digit-tools-core` catalog |
| Verbatim corpus quote | a substring plus a file anchor | corpus search + verbatim check |
| FTS certificate | a conclusion from reviewed morphisms | `fts-gate` |

## When to Use

- Any claim about Digitable courses, prices, legal terms, tools, or FTS syntax.
- Any computation the user expects to be correct, not plausible.
- The user asks "is this verified?", "where is this from?", or disputes a fact.
- Before a consequential action that depends on a domain rule.

Do not use verified mode for opinion, brainstorming, or style work. Those are
advisory mode and must be labeled as such.

## Two modes, one invariant

- **Mode A (verified).** Content from the three sources only. Every element must
  carry resolvable provenance: the file exists and the text is in it; the tool id
  is in the catalog and the arguments are attached; the certificate has a digest.
- **Mode B (advisory).** Free generation, explicitly labeled.

The invariant: **mode A never degrades into mode B silently.** If the request does
not fit A, either refuse or switch to B with a visible label. "I could not verify
this, but here is my answer" destroys the whole contract, because the user can no
longer tell which answers were checked.

## Procedure

1. Classify the request: computation, corpus fact, domain rule, or none of these.
2. Route to the matching source. Never mix a quote with a remembered fact.
3. For a quote: reproduce it verbatim, name the file, and confirm the text is in
   the **current** file, not only in a search index.
4. For a tool: state the tool id and the arguments used. Do not restate the
   result in your own numbers.
5. For a rule: require both a valid specification and a certificate. An
   explanation is not a substitute for a certificate.
6. If any step fails, refuse. Refusal is the correct outcome, not a failure.
7. If the user asks for advisory content anyway, switch to mode B and say so in
   the same message.

## How to refuse

A refusal is a closed-world statement and must carry numbers, otherwise it is
itself an unverifiable claim about the world. Shape of the message — the figures
below are illustrative, substitute the actual values of the run:

> Not present in the Digitable course materials. Checked 12 fragments, best
> relevance 0.31 against a threshold of 0.55.

Include: the refusal code, the search area, and the counts. For a missing
argument, name the argument. For an unverified premise, name the morphism.

## Pitfalls

- A verbatim quote proves the text exists in the corpus. It does **not** prove
  the text answers the question — this is the dominant residual error class.
- A tool executed on the wrong argument returns a correct number for a question
  nobody asked. Check that the argument is data, not an adverbial ("in
  windows-1251", "with a length of 12 characters" are not payloads).
- A green FTS run does not make a domain law true. Truth of a premise is an
  assumption, not a theorem.
- "Nothing found" without a named search area and counts is not a refusal.
- Never say "I do not hallucinate". Say what is true: content comes only from
  verifiable sources, and the residual errors are correct quotes answering a
  different question.

## Verification

- [ ] Every factual element names its source kind and its anchor.
- [ ] No number in the answer was written from memory.
- [ ] The mode is stated whenever the answer is not fully verified.
- [ ] Refusals carry a code, a search area, and counts.
- [ ] Quotes were checked against the current file, not only the index.
