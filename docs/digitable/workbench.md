# Workbench integration contract

Digit exposes three integration levels so Workbench does not need to depend on Desktop UI internals.

## Recommended: ACP child process

Workbench launches `digit-acp` over stdio and treats the process as an Agent Client Protocol server. This is the preferred interactive integration: sessions, streaming, tool calls, approvals, and cancellation remain owned by Digit.

The Workbench adapter should:

1. Resolve `digit-acp` from the selected Digit installation.
2. Start one process per isolated workspace or profile.
3. Send the workspace root as the session working directory.
4. Surface every approval request to the user; never auto-approve filesystem, shell, browser, or network mutations.
5. Forward attachments as native ACP content when supported and fall back to absolute local paths only for local sessions.
6. Stop the child process when the workspace closes, unless the user explicitly pins it as a persistent agent.

## MCP tool surface

For lightweight delegation, Workbench may connect to Digit's Hermes-tools MCP server and call a bounded tool instead of opening a full agent session. Use this for deterministic utilities, portal lookup, and narrowly scoped transformations. Use ACP when the task requires conversation state, planning, or multiple tools.

## Remote/gateway mode

For a shared or always-on Digit, Workbench should use the authenticated gateway rather than tunnelling the local stdio protocol. Profile and session identifiers must be explicit to prevent one project from inheriting another project's memory.

## Required Workbench UI

- model/provider selector with `local`, `cloud`, and `auto` labels;
- visible local/remote privacy indicator;
- current Digit profile and workspace root;
- approval queue and cancel button;
- Russian/English locale toggle using the shared Digitable component;
- link to `courses.digitable.life` and a utility launcher for `tools.digitable.life`.

The actual Workbench repository still needs to provide the adapter and header component. This document is the boundary contract for that implementation.
