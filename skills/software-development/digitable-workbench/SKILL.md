---
name: digitable-workbench
description: Use when embedding Digit into Digitable Workbench.
version: 1.0.0
author: Digitable
license: MIT
platforms: [linux, macos, windows]
metadata:
  digit:
    tags: [digitable, workbench, acp, mcp, integration]
    related_skills: [digit, digit, digitable-tools]
---

# Digitable Workbench

## Overview

Integrate Digit through stable agent protocols, not Desktop renderer internals. The full contract is in `docs/digitable/workbench.md` in the Digit repository.

## Routing

- Use `digit-acp` over stdio for interactive workspace sessions, streaming, approvals, and cancellation.
- Use the Digit-tools MCP server for bounded utility-style calls.
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
