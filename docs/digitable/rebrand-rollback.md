# Rolling back the Digit rebrand

The rebrand landed on `main` as a chain of self-contained commits. This
supersedes the `ROLLBACK.md` shipped with the rebrand package, which described
a commit layout that no longer matches and gave two instructions that do not
work. Both corrections are called out below.

```
docs(website): перегенерировать страницы навыков и каталоги
feat(skills): добавить digit-tools-core, fts-gate, verified-answers и ouroboros-tracing
test(acp):    сделать тест _meta сторожем сломанного ключа провода
fix(mcp):     убрать машинные пути из манифестов fts-gate и digit-tools
fix(skills):  убрать дубль навыка blender-mcp
feat(mcp):    добавить fts-gate и digit-tools в утверждённый каталог
rebrand!:     удалить команды hermes, hermes-agent и hermes-acp
fix:          починить установщик, сборку образа и локфайлы после переименования
rebrand:      читать legacy-окружение, домашний каталог и ключ метаданных
rebrand:      переименовать навык апстрима в digit-runtime
rebrand:      переименовать идентификаторы Hermes в Digit
rebrand:      мосты совместимости Hermes → Digit
```

## Full rollback

Revert newest first. Reverting the rename commit on its own conflicts with
everything above it, all of which touches renamed paths.

```bash
git revert --no-commit <newest>..<oldest>^
git commit -m "Откат ребрендинга Digit"
find . -name __pycache__ -type d -not -path './.venv/*' -exec rm -rf {} +
uv pip install -e .          # console scripts are only regenerated on install
```

`__pycache__` matters. It is gitignored, so `git clean -fd` leaves it behind,
and stale bytecode from the renamed modules shadows the restored ones. The
import errors that produces look exactly like a failed rollback.

## Partial rollbacks

### Putting the `hermes` commands back — reverting is NOT the way

**Correction 1.** The package's `ROLLBACK.md` said `git revert <alias-removal>`
"restores `hermes`, `hermes-agent` and `hermes-acp` in `pyproject.toml`". **It
does not.** The three `[project.scripts]` entries were removed by the *rename*
commit, not by the breaking one. Reverting `rebrand!: удалить команды …`
touches only `README.md`, `CHANGELOG.md`, `docs/digitable/README.md` and
`NOTICE` — it restores a paragraph promising three commands that still do not
exist, and it deletes the upstream attribution file. That is strictly worse
than doing nothing.

To actually restore the aliases, add them back by hand and reinstall:

```toml
[project.scripts]
digit = "digit_cli.main:main"
digit-agent = "run_agent:main"
digit-acp = "acp_adapter.entry:main"
hermes = "digit_cli.main:main"
hermes-agent = "run_agent:main"
hermes-acp = "acp_adapter.entry:main"
```

```bash
uv pip install -e .
```

Do not revert `NOTICE` in the process — the MIT licence of Hermes Agent
requires the attribution to travel with the work, independently of any
rebrand decision.

### Never revert the installer repair on its own

**Correction 2.** In the rebrand package these repairs lived *inside* the
breaking commit, which made every partial rollback of the alias removal
unsafe: it silently took `scripts/install.sh` back to a state where the agent
block overwrites the CLI launcher, so `digit` would run the headless agent
instead of the CLI, and where `cp X X` aborts `setup_path` under `set -e`.
They are now a separate commit — `fix: починить установщик, сборку образа и
локфайлы после переименования` — precisely so that it can be kept.

That commit is a repair of damage the rename caused. **Keep it for as long as
the rename is in the tree.** It only becomes revertible together with the
rename itself, in a full rollback. It also carries the Dockerfile FTS5 trigram
probe and the lockfile workspace-member names; without either, the image build
fails outright.

### Dropping the MCP servers

```bash
digit mcp uninstall fts-gate
digit mcp uninstall digit-tools
git revert <feat(mcp) commit>
git revert <fix(mcp) commit>       # the machine-path correction, if reverting the catalog
rm -rf optional-mcps/fts-gate optional-mcps/digit-tools
```

Uninstall **before** reverting. `digit mcp install` writes into
`mcp_servers.<name>` in the user's `config.yaml`, which is user data and is not
touched by any revert; reverting first leaves an installed entry pointing at a
manifest that no longer exists.

Reverting the catalog commit also takes the `fts` skill back to the version
naming eight tools that do not exist on any server. That skill was broken
before the rebrand and reverting restores the breakage — prefer reverting only
the catalog part of the commit.

### Dropping the compatibility shims

Not before the next minor release. The shims are what keeps existing
installations working: `HERMES_*` variables in shell profiles, systemd units
and CI; third-party skills whose frontmatter still uses `metadata.hermes`.

When the time comes, delete `digit_compat.py` and the call sites in
`digit_constants.py`, `digit_cli/main.py`, `tools/skills_tool.py`,
`tools/skills_hub.py` and `tools/blueprints.py`.

Note that the ACP `_meta` namespace has **no shim to drop** — that break is
unbridged by design. See `BREAKING.md`.

### The `blender-mcp` de-duplication

`fix(skills): убрать дубль навыка blender-mcp` is not part of the rebrand. It
removes a name collision that predates it and can be kept or reverted
independently — except that reverting it re-creates two skills named
`blender-mcp`, one of which silently shadows the other.

## What rollback does NOT undo

**User data.** No commit writes to `~/.digit` or `~/.hermes`. If a user
followed the first-run notice and ran `cp -a ~/.hermes/. ~/.digit/`, that copy
stays — it is a copy, not a move, so `~/.hermes` is intact and a rolled-back
install finds it where it left it.

If a user set `DIGIT_HOME`, they need `HERMES_HOME` again after a rollback; the
pre-rebrand code has never heard of `DIGIT_HOME`.

**ACP clients already updated to read `_meta.digit`** will stop seeing session
provenance and compaction markers again after a rollback, with no error. There
is no dual emit in either direction.

## Verifying a rollback

```bash
python3 -c "import hermes_constants; print('ok')"
.venv/bin/python -m pytest tests/skills tests/website tests/acp tests/acp_adapter -q
```

That four-directory set is the part of the suite the rename actually moves, and
it runs in well under a minute. The full suite takes hours and carries roughly
175 failures that predate this work.

**Correction 3.** The package's `ROLLBACK.md` told you to compare against
`testruns/before.xml` with `lib/compare_tests.py`. **Do not.** Those two files
are the discarded `pytest-xdist` run: `before.xml` records 22 126 tests where
the serial run recorded 24 018, because a worker died and pytest silently
dropped about 1 900 tests while still printing a normal-looking summary. The
package's own `QUESTIONS.md` item 12 documents this and states the headline
numbers were taken from serial runs. Neither `before.xml` (21 762 passed) nor
`after.xml` (23 577 passed) reproduces the 23 684 the package reports.

The trustworthy baseline is the per-directory serial run in
`testruns/chunks-before/` — 32 JUnit files, 24 018 tests, 23 684 passed —
against `testruns/chunks-after/`, 24 018 tests, 23 685 passed. Compare those if
you need a full-suite diff.
