"""Locate the ``digitdisk`` CLI, gate on its version, capture a machine snapshot.

digit does **not** reimplement digitdisk.  It shells out to the installed
binary and renders the JSON that comes back, so the two tools can not drift
apart into two different opinions about the same machine.

Everything discovery- and version-shaped lives in this module rather than in
the RPC handler, for two reasons:

* the handler stays a thin wrapper (see ``digitdisk.status`` in
  ``methods_tools.py``), and
* every rule below is unit-testable without a real digitdisk on PATH — the
  three interesting cases (fresh binary / too old / absent) are exactly the
  ones you cannot reproduce on a developer's machine on demand.

**The invocation form is load-bearing.**  digitdisk parses a bare argument in
the subcommand slot as a *path*, so ``digitdisk --json status`` fails with
``подкоманда status не принимает путей`` — the flag must follow the
subcommand.  ``status --json`` is the only form that works; do not "tidy" it.

**Why the version gate exists.**  The snapshot payload is a data contract, not
a stable public API: ``status --json`` carries no ``contract_version`` field of
its own (``analyze --json`` does, ``status --json`` does not).  So the only
thing digit can check before trusting the keys is the tool's own version, read
from the first line of ``digitdisk --version``.  Below the floor we say so, by
number, and render nothing — parsing an older tool's JSON blind is how you get
a panel that is confidently wrong.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Callable, Iterable

# The oldest digitdisk whose `status --json` keys this panel is written
# against — the version in the digitdisk tree when the panel was built.
# Raise this (and the renderer) together when the payload shape changes.
MIN_VERSION: tuple[int, int, int] = (0, 6, 0)
MIN_VERSION_TEXT = ".".join(str(p) for p in MIN_VERSION)

# Flags AFTER the subcommand — see the module docstring.  `--lang en` keeps
# digitdisk's own diagnostics in the TUI's language; it does not change the
# JSON keys (those are fixed) and is harmless on every subcommand.
SNAPSHOT_ARGS: tuple[str, ...] = ("status", "--json", "--lang", "en")
VERSION_ARGS: tuple[str, ...] = ("--version",)

# A full status sweep walks /proc for every process and samples CPU for
# 200 ms; ~1.6 s is typical on a 256-core host.  The ceiling is generous
# because the alternative — a panel that times out on a busy machine — is
# worse than one that takes a moment.
SNAPSHOT_TIMEOUT_S = 30
VERSION_TIMEOUT_S = 5

INSTALL_HINT = (
    "install digitdisk "
    f"{MIN_VERSION_TEXT} or newer: `brew install digitable-lol/tap/digitdisk`, "
    "or download a release archive and put the binary on PATH. "
    "Set DIGITDISK_BIN to point at a specific build."
)

# Looked at in order, after $DIGITDISK_BIN and after PATH.  These are the
# places a Homebrew install (Apple Silicon, then Intel/Linuxbrew) and a
# manual `install -m 0755` land, per the digitdisk README.
FALLBACK_DIRS: tuple[str, ...] = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/home/linuxbrew/.linuxbrew/bin",
    "~/.local/bin",
    "~/.digit/bin",
)

BINARY_NAME = "digitdisk"


def parse_version(text: str) -> tuple[int, int, int] | None:
    """Pull the version triple out of ``digitdisk --version`` output.

    The first line is ``digitdisk <version>``; the rest is build/toolchain
    detail we do not parse.  Returns ``None`` for a source build, which
    stamps the literal ``dev`` rather than lying about a release number —
    the caller decides what to do about that (see ``_classify_version``).
    """
    first = (text or "").strip().splitlines()
    if not first:
        return None
    parts = first[0].split()
    if len(parts) < 2:
        return None
    raw = parts[1].strip()
    # Tolerate a leading `v` and a `-rc1`/`+build` suffix.
    raw = raw.lstrip("vV").split("-")[0].split("+")[0]
    bits = raw.split(".")
    if len(bits) < 2:
        return None
    try:
        nums = [int(b) for b in bits[:3]]
    except ValueError:
        return None
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def find_binary(
    *,
    env: dict[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    is_exec: Callable[[str], bool] | None = None,
    fallback_dirs: Iterable[str] = FALLBACK_DIRS,
) -> str | None:
    """Resolve the digitdisk executable, or ``None`` when it is not installed.

    Order: explicit ``$DIGITDISK_BIN`` override (mirrors how ``externalCli.ts``
    lets ``$DIGIT_BIN`` pin a build), then PATH, then the handful of
    directories a package manager or a manual install uses.  The override wins
    outright and is *not* second-guessed: if someone points it at a missing
    file we report the tool as absent rather than silently using a different
    one than they asked for.
    """
    env = os.environ if env is None else env
    if is_exec is None:

        def is_exec(path: str) -> bool:
            return os.path.isfile(path) and os.access(path, os.X_OK)

    override = (env.get("DIGITDISK_BIN") or "").strip()
    if override:
        return override if is_exec(override) else None

    found = which(BINARY_NAME)
    if found:
        return found

    for directory in fallback_dirs:
        candidate = os.path.join(os.path.expanduser(directory), BINARY_NAME)
        if is_exec(candidate):
            return candidate
    return None


def _run(binary: str, args: Iterable[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        from digit_cli._subprocess_compat import windows_hide_flags

        hide = windows_hide_flags()
    except Exception:  # pragma: no cover - non-Windows or trimmed install
        hide = 0
    return subprocess.run(
        [binary, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        # Force UTF-8 with lossy decode: digitdisk prints Cyrillic, and a
        # locale-mismatched host must not be able to crash the gateway thread.
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        creationflags=hide,
    )


def _classify_version(version: tuple[int, int, int] | None) -> tuple[bool, bool]:
    """Return ``(usable, known)`` for a parsed version.

    A source build (``digitdisk dev``) parses to ``None``.  We let it through
    rather than blocking it — refusing would break anyone building digitdisk
    from a checkout — but mark it unknown so the panel can say the version
    was not verified instead of implying it was.
    """
    if version is None:
        return True, False
    return version >= MIN_VERSION, True


def probe(
    *,
    env: dict[str, str] | None = None,
    runner: Callable[[str, Iterable[str], int], subprocess.CompletedProcess] = _run,
    finder: Callable[..., str | None] = find_binary,
) -> dict[str, Any]:
    """Find digitdisk, check its version, and return a machine snapshot.

    Always resolves to a payload with a ``state`` the renderer can switch on —
    never raises — because a missing or broken external tool is a thing to
    *show* the user, not an error that blanks the panel:

    ``ok``       — ``snapshot`` holds the parsed ``status --json`` payload.
    ``missing``  — digitdisk is not installed; ``hint`` says how to get it.
    ``outdated`` — installed but below :data:`MIN_VERSION`; both numbers given.
    ``failed``   — found and new enough, but the run or the parse did not work.
    """
    required = MIN_VERSION_TEXT
    binary = finder(env=env) if finder is find_binary else finder()
    if not binary:
        return {
            "state": "missing",
            "required": required,
            "hint": INSTALL_HINT,
        }

    try:
        vproc = runner(binary, VERSION_ARGS, VERSION_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {
            "state": "failed",
            "binary": binary,
            "required": required,
            "error": f"`digitdisk --version` timed out after {VERSION_TIMEOUT_S}s",
        }
    except OSError as exc:
        # The binary vanished or is not executable between find and run.
        return {
            "state": "missing",
            "required": required,
            "hint": f"{binary} could not be run ({exc.strerror or exc}). {INSTALL_HINT}",
        }

    version_text = (vproc.stdout or vproc.stderr or "").strip().splitlines()
    version_line = version_text[0].strip() if version_text else ""
    version = parse_version(vproc.stdout or vproc.stderr or "")
    usable, known = _classify_version(version)
    version_str = ".".join(str(p) for p in version) if version else None

    if not usable:
        return {
            "state": "outdated",
            "binary": binary,
            "version": version_str,
            "version_line": version_line,
            "required": required,
            "hint": (
                f"digitdisk {version_str} is installed, but this panel reads the "
                f"snapshot format of {required} and newer. {INSTALL_HINT}"
            ),
        }

    try:
        proc = runner(binary, SNAPSHOT_ARGS, SNAPSHOT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {
            "state": "failed",
            "binary": binary,
            "version": version_str,
            "required": required,
            "error": f"`digitdisk status --json` timed out after {SNAPSHOT_TIMEOUT_S}s",
        }
    except OSError as exc:
        return {
            "state": "failed",
            "binary": binary,
            "version": version_str,
            "required": required,
            "error": str(exc),
        }

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return {
            "state": "failed",
            "binary": binary,
            "version": version_str,
            "required": required,
            "error": (detail[0] if detail else f"exit code {proc.returncode}")[:400],
        }

    try:
        snapshot = json.loads(proc.stdout or "")
    except (ValueError, TypeError) as exc:
        return {
            "state": "failed",
            "binary": binary,
            "version": version_str,
            "required": required,
            "error": f"could not parse `digitdisk status --json` output: {exc}",
        }

    if not isinstance(snapshot, dict):
        return {
            "state": "failed",
            "binary": binary,
            "version": version_str,
            "required": required,
            "error": "`digitdisk status --json` did not return a JSON object",
        }

    return {
        "state": "ok",
        "binary": binary,
        "version": version_str,
        "version_known": known,
        "version_line": version_line,
        "required": required,
        "snapshot": snapshot,
    }
