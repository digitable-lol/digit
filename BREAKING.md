# BREAKING — the Digit rebrand

Digit 0.19.8, merged 2026-08-03.

This release renames the product off the Hermes identity. Most of it is
internal, but **four things cross a boundary someone else owns** and will break
software that is not in this repository. They were broken on purpose, after the
owner chose a clean break over a dual-emit release. That decision is recorded
here rather than left to be discovered.

Attribution is not part of the break. Digit remains a derivative work of
[Hermes Agent](https://github.com/NousResearch/hermes-agent), Copyright (c) 2025
Nous Research, MIT — see `LICENSE`, `NOTICE` and the Origin section of
`README.md`. Model identifiers (`hermes-4-405b`,
`NousResearch/Hermes-3-Llama-3.1-70B`, …) are unchanged: they are addressed over
the wire to inference providers.

---

## 1. The ACP `_meta` namespace key: `hermes` → `digit`

**This is a wire format, and it is the only break with no compatibility path
at all.**

`digit-acp` attaches protocol metadata to ACP messages under a vendor namespace
inside `_meta`. The namespace key was `hermes`; it is now `digit`. **Both keys
are never emitted. There is no deprecation window and no shim.**

Three payloads move:

| Payload | Before | After |
|---|---|---|
| session provenance | `_meta.hermes.sessionProvenance` | `_meta.digit.sessionProvenance` |
| standalone compaction summary | `_meta.hermes.compactionSummary` | `_meta.digit.compactionSummary` |
| merged-tail compaction summary | `_meta.hermes.containsCompactionSummary` | `_meta.digit.containsCompactionSummary` |

The values and their shapes are unchanged. Only the namespace key moved.

### Who is affected

Any ACP client that reads `_meta.hermes`:

- **Zed** — session provenance disappears. Compaction summaries stop being
  identifiable as summaries and render as ordinary turns.
- **Digitable Workbench** — same, plus anything Workbench derives from
  `sessionProvenance` (session lineage, compression depth, rotation flags).

Nothing errors. The key is simply absent, so a client that does
`meta.get("hermes", {})` silently sees an empty object and every downstream
feature quietly turns off. **That is why this file exists.**

### What client authors must do

Read the new key. If you must support both Digit versions from one build,
accept either and prefer the new one:

```ts
const ns = meta?.digit ?? meta?.hermes ?? {};
```

Do not treat the absence of `_meta.digit` as an error — `_meta` is optional in
ACP and Digit omits the namespace entirely when there is nothing to report.

Guarded by `tests/acp/test_session_provenance.py`, which now pins the new key by
name and asserts the old one is gone. Before this release no test covered the
key name at all, which is how the rename shipped unnoticed in the first place.

---

## 2. The `hermes`, `hermes-agent` and `hermes-acp` commands are removed

Earlier READMEs promised these as permanent compatibility aliases. **That
promise is withdrawn.** The replacements already existed and are unchanged:

| Removed | Replacement |
|---|---|
| `hermes` | `digit` |
| `hermes-agent` | `digit-agent` |
| `hermes-acp` | `digit-acp` |

They fail with "command not found", not silently — with one important
exception.

### Who is affected

- **Buzz Desktop** launches the ACP agent by spawning `hermes-acp`. After
  upgrading, that spawn fails and the agent never comes up. Buzz must spawn
  `digit-acp`.
- **Zed** and any other editor with an ACP agent entry pointing at
  `hermes-acp` — same fix, in the agent's `command` field.
- Shell aliases, `systemd` units, cron entries, CI jobs and Dockerfiles calling
  `hermes` or `hermes-agent`.

### The exception worth knowing about

If a stale `hermes-acp` launcher is still on `PATH` from a previous install,
the spawn *succeeds* and runs pre-rebrand code against a post-rebrand data
directory.

**`digit update` now clears these for you.** It removes `hermes`,
`hermes-agent` and `hermes-acp` from `~/.local/bin` and `/usr/local/bin` when
their contents identify them as ours — after installing `digit-acp`, so an ACP
host never has a window with neither name present. A launcher at one of those
paths that is *not* recognisably ours is left alone and reported, because
deleting an unrelated program that happens to share the name would be worse
than the skew.

If you would rather not run the updater, the manual equivalent is:

```bash
rm -f "$(dirname "$(command -v digit)")"/hermes "$(dirname "$(command -v digit)")"/hermes-agent "$(dirname "$(command -v digit)")"/hermes-acp
```

`scripts/install.sh` does **not** do this: installing over an early install
writes the three new launchers and leaves the three old ones in place. Cleanup
lives on the update path only.

---

## 3. Python modules renamed, with no aliases

| Before | After |
|---|---|
| `hermes_constants` | `digit_constants` |
| `hermes_state`, `hermes_state_*` | `digit_state`, `digit_state_*` |
| `hermes_logging` | `digit_logging` |
| `hermes_time` | `digit_time` |
| `hermes_bootstrap` | `digit_bootstrap` |
| `hermes_cli/` (package) | `digit_cli/` |

**There is no module-level alias.** Anything importing these directly —
plugins, hook scripts, out-of-tree tools — must be updated. Failure is a loud
`ModuleNotFoundError`.

Clear stale bytecode when upgrading in place; `__pycache__` is gitignored and
survives a `git clean -fd`, and old `.pyc` files shadow the renamed modules:

```bash
find . -name __pycache__ -type d -not -path './.venv/*' -exec rm -rf {} +
```

npm workspace scopes `@hermes/*` and `@hermes-agent/*` are now `@digit/*`, and
`setup-hermes.sh` is now `setup-digit.sh`.

---

## 4. `$HERMES_HOME` is no longer what Digit reads

Digit reads `DIGIT_HOME`, defaulting to `~/.digit`. This one is **bridged**:
`digit_compat.adopt_legacy_env()` copies any `HERMES_*` variable onto its
`DIGIT_*` name when the new name is unset, warns once, and lets the new name win
when both are set. The bridge is removed one minor release from now.

The bridge only covers **inbound** reads by Digit itself. It does nothing for
software that writes into that directory:

- **Out-of-tree installers and plugins.** Anything that resolves
  `${HERMES_HOME:-$HOME/.hermes}` and writes there — model-provider plugins,
  hook scripts, MCP servers with an install step — now writes to a directory
  Digit does not read. **This fails silently**: files land, the installer
  reports success, and Digit never sees them. The `digit-ml/runtime`
  llamacpp-router installer had exactly this bug at merge time. The correct
  resolution order is:

  ```bash
  HOME_DIR="${1:-${DIGIT_HOME:-${HERMES_HOME:-$HOME/.digit}}}"
  ```

  The `~/.digit` fallback is the load-bearing part. Honouring `HERMES_HOME`
  when it is set keeps a user who has not migrated their exports working, and
  matches Digit's own precedence.

- **Outbound `HERMES_HOME` for subprocesses.** Digit sets only `DIGIT_HOME` in
  the environment it hands to MCP servers and plugins.
  `digit_compat.export_legacy_env()` exists but is not wired to a call site —
  which boundaries get the mirror is an open decision, see the rebrand
  `QUESTIONS.md` item 8.

The data directory itself moved from `~/.hermes` to `~/.digit`. **Nothing is
copied automatically** — that directory can hold provider credentials. On first
run Digit detects an old tree and prints the exact command:

```bash
cp -a ~/.hermes/. ~/.digit/
```

To keep the old location instead: `DIGIT_HOME=~/.hermes`.

---

## 5. `metadata.hermes` in SKILL.md frontmatter → `metadata.digit`

Bundled skills were migrated; all four skills added in this merge ship with the
new key. **Third-party skills keep working**: the loader reads `metadata.digit`
and falls back to `metadata.hermes`, warning once per skill. That fallback is
removed one minor release from now, so skill authors should move the key.

---

## Rolling back

See `docs/digitable/rebrand-rollback.md`. Read it before reverting anything —
**partial rollback of the alias removal does not restore the commands**, and
reverting the wrong commit re-breaks the installer.
