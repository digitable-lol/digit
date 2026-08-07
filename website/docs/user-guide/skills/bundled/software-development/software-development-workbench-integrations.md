---
title: "Workbench Integrations — Use when connecting a Digitable Workbench integration"
sidebar_label: "Workbench Integrations"
description: "Use when connecting a Digitable Workbench integration"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Workbench Integrations

Use when connecting a Digitable Workbench integration.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/software-development/workbench-integrations` |
| Version | `1.0.0` |
| Author | Digit |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `digitable`, `workbench`, `integrations`, `themes`, `palette`, `mcp`, `catalog` |
| Related skills | [`digitable-workbench`](/user-guide/skills/bundled/software-development/software-development-digitable-workbench), [`digitable-portal`](/user-guide/skills/bundled/productivity/productivity-digitable-portal), [`digit-tools-core`](/user-guide/skills/bundled/software-development/software-development-digit-tools-core) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Digit loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Workbench Integrations

## Overview

Digitable Workbench ships palettes and configs for a catalog of programs —
terminals, CLI tools, editors, desktop apps, and two MCP servers. Each target
has a card that says where its files go, in what order to connect them, how to
check the result, and what is different about that particular target.

`digit workbench` reads that catalog from the `courses` checkout. **Look the
answer up; do not recall it.** Destination paths differ per program and per
platform, and a plausible-looking wrong path (`~/.config/vim/` instead of
`~/.vim/colors/`) fails silently — the program simply keeps its old colours and
the user is left debugging a copy that never happened.

## When to Use

- "Put the Digitable palette into &lt;program>" — the card has `dest` and `steps`.
- "Which programs does Workbench cover?" — `list` / `categories`.
- "Where does the config for &lt;program> live?" — `show <id>`.
- Wiring the Digit-tools or FTS-gate MCP server — both are cards in the `mcp`
  category, with the config-merge command in their steps.

## Commands

```bash
digit workbench categories                    # the five categories and their counts
digit workbench list                          # every target
digit workbench list --category editors       # one category
digit workbench list --caveats                # only targets with a caveat
digit workbench search tmux                   # matches ids, names, steps, caveats
digit workbench show neovim                   # one card in full
digit workbench show neovim --json            # machine-readable
```

Every subcommand takes `--json`. `show` exits **2** when the id is not in the
catalog (as opposed to **1**, which means the catalog itself could not be read)
and prints near matches, so a typo is distinguishable from a missing checkout
without parsing the message.

## Read the caveat before you repeat the steps

Eleven of the cards carry a `caveat`, and it is never cosmetic. Some examples of
what it holds:

- Discord has no theme support at all; recolouring needs a client mod, and using
  one breaks Discord's terms of service.
- Firefox and Thunderbird keep unsigned extensions only on some channels — a
  permanent install needs your own signature.
- Digit Tools is GPL-3.0-only and deliberately not in the paid archive; the user
  builds the binary themselves.

`digit workbench show` prints the caveat **above** the steps for that reason.
Summarising a card without it is not a shorter answer, it is a wrong one: the
user acts on steps that do not apply to them.

The listing marks those cards with `!` in its own column. When you narrow a list
for the user, keep the marker.

## What this skill does not do

- **It does not have the files.** The catalog says what a target needs and where
  it goes; the palette files themselves come from the Workbench archive (or, for
  the five open guide-only cards — Digit, Digitable Chat, Blender MCP, Digit
  Tools, FTS Gate — from the guide, because those have no palette files at all).
  `available` on a card tells you which of the two it is.
- **It does not install anything.** The steps are for the user to run, or for you
  to run in the user's shell after they agree — copying files into `~/.config`
  is a change to their machine, not a lookup.
- **It is not `digitable-workbench`.** That skill is about embedding Digit *into*
  the Workbench shell over ACP/MCP. This one is about the integration catalog
  Workbench publishes. Different question, different surface.

## Where the catalog lives

`data/workbench-integrations.toml` in the `courses` checkout. Resolution order:
`--catalog`, then `workbench.catalog` in `config.yaml`, then the `courses`
checkout `digit kb` already resolves, then the first
`data/workbench-integrations.toml` found walking up from the working directory.

The catalog is **not** vendored into Digit. A copy inside the Digit tree would
keep answering after the source moved on, and it would do it silently — so when
no checkout is reachable the command says so and answers nothing. If you see
that error, ask for the checkout path; do not fall back to memory.

Do not quote a card count from this file or from any page. The catalog counts
itself, and any number written down beside it goes stale — run
`digit workbench categories` and read the total off the output.
