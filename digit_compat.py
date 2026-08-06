"""Compatibility shims for the Hermes -> Digit rebrand.

Everything in this module exists to keep *existing installations* working
across the rename. It is scheduled for removal one minor release after the
rebrand ships; see CHANGELOG and ROLLBACK.md.

Three separate contracts are bridged here:

1. **Environment variables.** 601 distinct ``HERMES_*`` names became ``DIGIT_*``.
   Those names are not internal: users put them in shell profiles, systemd
   units, Docker compose files and CI secrets. :func:`adopt_legacy_env` copies
   any surviving ``HERMES_*`` value onto its ``DIGIT_*`` counterpart when the
   new name is unset, and warns once so the user knows to update.

2. **Legacy data directory.** ``HERMES_HOME`` used to default to ``~/.hermes``;
   the default is now ``~/.digit``. The project deliberately does *not*
   auto-import the old tree (it can hold credentials, and a silent merge is
   worse than an explicit one), but a silent *switch* is equally bad: the user
   would just find an empty agent. :func:`legacy_home_notice` detects the
   situation and returns an actionable message.

3. **Skill metadata key.** ``metadata.hermes`` in SKILL.md frontmatter became
   ``metadata.digit``. :func:`read_skill_metadata_block` reads the new key and
   falls back to the old one so third-party skills keep loading.

4. **Launcher names left on PATH.** ``hermes``, ``hermes-agent`` and
   ``hermes-acp`` are gone from ``pyproject.toml``, but removing a console
   script from the project does not remove a launcher an *earlier installer*
   already wrote into ``~/.local/bin`` or ``/usr/local/bin``. Most of those
   just fail with "command not found"; ``hermes-acp`` does not — the spawn
   succeeds and runs pre-rebrand code against a post-rebrand data directory
   (BREAKING.md, "The exception worth knowing about").
   :func:`legacy_launcher_paths` and :func:`is_our_legacy_launcher` are the
   shared vocabulary for finding and identifying them, so ``digit update`` and
   ``digit uninstall`` cannot drift apart on which names count.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

LEGACY_PREFIX = "HERMES_"
CURRENT_PREFIX = "DIGIT_"

# Set by adopt_legacy_env so callers can report what was bridged.
adopted: Dict[str, str] = {}

_warned = False


def adopt_legacy_env(environ: Optional[Dict[str, str]] = None,
                     *, warn: bool = True) -> Dict[str, str]:
    """Copy ``HERMES_*`` env vars onto their ``DIGIT_*`` names.

    The new name always wins when both are set, so a user who has migrated is
    never overridden by a stale export left in a shell profile.

    Returns a mapping of ``{legacy_name: new_name}`` for whatever was adopted.
    Idempotent and safe to call from multiple entry points.
    """
    global _warned
    env = os.environ if environ is None else environ
    bridged: Dict[str, str] = {}

    for legacy in [k for k in env if k.startswith(LEGACY_PREFIX)]:
        current = CURRENT_PREFIX + legacy[len(LEGACY_PREFIX):]
        if env.get(current):
            continue
        env[current] = env[legacy]
        bridged[legacy] = current

    adopted.update(bridged)

    if bridged and warn and not _warned:
        _warned = True
        names = ", ".join(sorted(bridged)[:5])
        more = f" (+{len(bridged) - 5} more)" if len(bridged) > 5 else ""
        print(
            f"[digit] Using legacy {LEGACY_PREFIX}* environment variables: {names}{more}.\n"
            f"[digit] These are deprecated. Rename them to {CURRENT_PREFIX}* — "
            f"support will be removed in the next minor release.",
            file=sys.stderr,
        )
    return bridged


def export_legacy_env(environ: Optional[Dict[str, str]] = None,
                      *, names: Iterable[str] = ("DIGIT_HOME",)) -> None:
    """Mirror selected ``DIGIT_*`` values back onto their ``HERMES_*`` names.

    Needed only for *outbound* process boundaries: user-installed MCP servers,
    plugins and hook scripts written against the old contract read
    ``HERMES_HOME`` from the environment Digit hands them. Dropping it in the
    same release that renames everything else would break them with no warning.
    Remove alongside :func:`adopt_legacy_env`.
    """
    env = os.environ if environ is None else environ
    for name in names:
        if name.startswith(CURRENT_PREFIX) and env.get(name):
            env.setdefault(LEGACY_PREFIX + name[len(CURRENT_PREFIX):], env[name])


# ---------------------------------------------------------------------------
# Legacy data directory
# ---------------------------------------------------------------------------

def legacy_home_path() -> Path:
    """Where the pre-rebrand data directory lived on this platform."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "hermes"
    return Path.home() / ".hermes"


def legacy_home_notice(current_home: Path) -> Optional[str]:
    """Return an actionable message when data is in the old location.

    Fires only when the old tree exists, has content, and the new tree does not
    yet — i.e. exactly the first run after an upgrade. Returns ``None``
    otherwise, so this is cheap to call unconditionally at startup.

    The copy is deliberately NOT performed automatically: ``~/.hermes`` can
    contain ``.env`` credentials and provider tokens, and silently duplicating
    those into a new location is a decision for the user, not the installer.
    """
    legacy = legacy_home_path()
    try:
        if not legacy.is_dir() or not any(legacy.iterdir()):
            return None
        if current_home.exists() and any(current_home.iterdir()):
            return None
    except OSError:
        return None

    if sys.platform == "win32":
        cmd = f'xcopy /E /I /Y "{legacy}" "{current_home}"'
    else:
        cmd = f"cp -a {legacy}/. {current_home}/"

    return (
        f"Digit now stores its data in {current_home}, but an existing\n"
        f"Hermes data directory was found at {legacy}.\n"
        f"\n"
        f"Nothing was copied automatically — that directory can hold API\n"
        f"credentials, and moving them is your call. To carry everything over:\n"
        f"\n"
        f"    {cmd}\n"
        f"\n"
        f"To keep using the old location instead, export DIGIT_HOME={legacy}\n"
        f"Run `digit doctor` after either choice to verify."
    )


# ---------------------------------------------------------------------------
# Launcher names left behind on PATH
# ---------------------------------------------------------------------------

#: Console-script names an installer wrote before the rebrand, paired with the
#: name that replaced each one. Keep in step with the table in BREAKING.md §2.
LEGACY_COMMANDS: Dict[str, str] = {
    "hermes": "digit",
    "hermes-agent": "digit-agent",
    "hermes-acp": "digit-acp",
}

#: Current console-script names, from ``[project.scripts]``. Used by uninstall.
CURRENT_COMMANDS = ("digit", "digit-agent", "digit-acp")

#: Where our installers place launchers on POSIX. ``scripts/install.sh``
#: resolves to the first for user installs and the second for root ones.
LAUNCHER_DIRS = ("~/.local/bin", "/usr/local/bin")


def launcher_dirs() -> list:
    """Absolute launcher directories for this machine.

    The user directory is derived from :meth:`Path.home`, not
    ``os.path.expanduser``, so that it follows the same override the rest of the
    codebase and its tests already use.
    """
    resolved = []
    for entry in LAUNCHER_DIRS:
        if entry.startswith("~"):
            resolved.append(Path.home() / entry.lstrip("~/"))
        else:
            resolved.append(Path(entry))
    return resolved


def legacy_launcher_paths() -> list:
    """Existing pre-rebrand launchers, in the directories our installers use.

    Includes broken symlinks (``is_symlink`` as well as ``is_file``): a dangling
    ``hermes-acp`` pointing into a removed install is still a name that
    shadows nothing useful and still worth clearing.
    """
    found = []
    for directory in launcher_dirs():
        for name in LEGACY_COMMANDS:
            path = directory / name
            try:
                if path.is_file() or path.is_symlink():
                    found.append(path)
            except OSError:
                continue
    return found


#: Strings that only a pre-rebrand Digit/Hermes launcher would contain: the old
#: package module, the old data directory, or the old console-script names.
_OURS_MARKERS = ("hermes_cli", "hermes_bootstrap", "hermes_constants",
                 ".hermes", "hermes-agent", "hermes-acp", "hermes_agent")


def is_our_legacy_launcher(path: Path) -> bool:
    """Whether ``path`` looks like a launcher *we* wrote before the rebrand.

    Deliberately a content check rather than "it is in our directory with our
    name". ``~/.local/bin/hermes`` could belong to something else entirely, and
    an updater that deletes an unrelated program because the name matched is a
    worse failure than leaving a stale launcher in place. When the check cannot
    be made — unreadable, or a symlink we cannot resolve — the answer is False,
    and the caller is expected to report rather than remove.
    """
    try:
        if path.is_symlink():
            target = os.fspath(os.readlink(path))
            if any(marker in target for marker in _OURS_MARKERS):
                return True
        if not path.is_file():
            return False
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(marker in content for marker in _OURS_MARKERS)


# ---------------------------------------------------------------------------
# Skill metadata key
# ---------------------------------------------------------------------------

SKILL_METADATA_KEY = "digit"
SKILL_METADATA_KEY_LEGACY = "hermes"

_metadata_warned: set = set()


def read_skill_metadata_block(metadata: Any, *, source: str = "") -> Dict[str, Any]:
    """Return the Digit block from a SKILL.md ``metadata:`` mapping.

    Reads ``metadata.digit``; falls back to ``metadata.hermes`` for skills
    authored before the rebrand — including third-party skills from the hub,
    which we do not control and cannot rewrite. The fallback is logged once per
    source so skill authors get a nudge without spamming the session.

    Scheduled for removal one minor release after the rebrand.
    """
    if not isinstance(metadata, dict):
        return {}

    block = metadata.get(SKILL_METADATA_KEY)
    if isinstance(block, dict):
        return block

    legacy = metadata.get(SKILL_METADATA_KEY_LEGACY)
    if isinstance(legacy, dict):
        if source and source not in _metadata_warned:
            _metadata_warned.add(source)
            print(
                f"[digit] {source}: SKILL.md uses the deprecated "
                f"'metadata.{SKILL_METADATA_KEY_LEGACY}' key; rename it to "
                f"'metadata.{SKILL_METADATA_KEY}'.",
                file=sys.stderr,
            )
        return legacy

    return {}
