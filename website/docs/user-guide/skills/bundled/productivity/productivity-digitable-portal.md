---
title: "Digitable Portal — Use when navigating the Digitable portal ecosystem"
sidebar_label: "Digitable Portal"
description: "Use when navigating the Digitable portal ecosystem"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Digitable Portal

Use when navigating the Digitable portal ecosystem.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/productivity/digitable-portal` |
| Version | `1.0.0` |
| Author | Digitable |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `digitable`, `portal`, `navigation`, `products` |
| Related skills | [`digit`](/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-digit), [`digitable-courses`](/docs/user-guide/skills/bundled/productivity/productivity-digitable-courses), [`digitable-tools`](/docs/user-guide/skills/bundled/software-development/software-development-digitable-tools), [`digitable-workbench`](/docs/user-guide/skills/bundled/software-development/software-development-digitable-workbench), [`fts`](/docs/user-guide/skills/bundled/software-development/software-development-fts) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Digit loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Digitable Portal

## Overview

Use this skill as a routing map for Digitable. Canonical web pages remain the source of truth; do not treat this file as a cached copy of changing content.

## Canonical surfaces

| Need | Canonical surface |
|---|---|
| Digitable overview and product navigation | `https://digitable.life/` |
| Open learning portal and course map | `https://courses.digitable.life/` |
| FTS language, course, and case catalog | `https://courses.digitable.life/fts/` |
| Browser-based developer utilities | `https://tools.digitable.life/` |
| Chat product | `https://chat.digitable.life/` |

## Workflow

1. Identify whether the request is navigation, learning, an FTS specification, a deterministic transformation, chat, or agent work.
2. Load the matching related skill before giving detailed instructions.
3. Verify availability and current wording from the canonical URL when network access exists.
4. Link directly to the deepest useful page, not just the homepage.
5. If a surface is unavailable, say so and offer an offline/manual path without inventing portal state.

## Common pitfalls

- Do not confuse Nous Portal, which is an upstream model provider, with the Digitable portal.
- Do not claim enrollment, payment, or account state without an authenticated source.
- Do not send secrets or user files to a browser utility unless the user understands that boundary; prefer local deterministic tools for sensitive data.

## Verification checklist

- [ ] Correct canonical domain selected.
- [ ] Live state verified when the answer depends on it.
- [ ] Link points to the most specific useful page.
