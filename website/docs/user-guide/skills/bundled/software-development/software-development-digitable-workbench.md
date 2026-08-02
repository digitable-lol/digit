---
title: "Digitable Workbench — Use when embedding Digit into Digitable Workbench"
sidebar_label: "Digitable Workbench"
description: "Use when embedding Digit into Digitable Workbench"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Digitable Workbench

Use when embedding Digit into Digitable Workbench.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/software-development/digitable-workbench` |
| Version | `1.0.0` |
| Author | Digitable |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `digitable`, `workbench`, `acp`, `mcp`, `integration` |
| Related skills | [`digit`](/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-digit), [`hermes-agent`](/docs/user-guide/skills/bundled/autonomous-ai-agents/autonomous-ai-agents-hermes-agent), [`digitable-tools`](/docs/user-guide/skills/bundled/software-development/software-development-digitable-tools) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Digitable Workbench

## Overview

Integrate Digit through stable agent protocols, not Desktop renderer internals. The full contract is in `docs/digitable/workbench.md` in the Digit repository.

## Routing

- Use `digit-acp` over stdio for interactive workspace sessions, streaming, approvals, and cancellation.
- Use the Hermes-tools MCP server for bounded utility-style calls.
- Use the authenticated gateway for shared or always-on remote deployments.

## Implementation workflow

1. Confirm the Workbench repository and its process/runtime boundary.
2. Choose exactly one session authority: ACP child process or remote gateway.
3. Keep workspace root, profile, model provider, and privacy mode visible in UI state.
4. Forward approval requests; never silently widen permissions.
5. Use the shared Digitable locale toggle and brand assets rather than duplicating them.
6. Test cancellation, restart, attachment transfer, Russian/English switching, and isolation between two workspaces.

## Completion criteria

- [ ] Workbench can start and stop Digit without orphan processes.
- [ ] Streaming and cancellation work.
- [ ] Tool approvals are user-visible.
- [ ] Two workspaces cannot leak sessions or memory into each other.
- [ ] Local/remote provider status and locale toggle are visible.
