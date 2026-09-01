"""Extend the cluster write boundary over the shell.

``agent/cluster_boundary.py`` bounds the *file tools* by checking a path before
``write_file`` / ``patch`` touch it.  The terminal tool has no path to check: it
hands a string to ``bash``, and bash writes wherever the OS user can.  A
delegated agent that is refused ``write_file('/outside/x')`` could until now run
``sh -c 'echo x > /outside/x'`` and get the same file.

Two mechanisms close that, in this order:

* **Landlock (the lock).**  Linux 5.13+ lets an unprivileged process narrow its
  own filesystem rights irrevocably, and the restriction is inherited across
  ``fork``/``exec``.  We build a ruleset that leaves *read* and *execute*
  unhandled — so the shell still reads the whole filesystem — and handles every
  *write*-shaped right, granting it only under the agent's declared roots.  It
  is applied in the child between ``fork`` and ``exec``, so it covers the shell,
  everything the shell spawns, ``python -c``, background jobs, and stdin pushed
  into an already-running process.  There is nothing to parse and nothing to
  outsmart: the kernel refuses the ``open``.

* **A static pre-check (the message).**  Landlock's refusal reaches the model as
  ``Permission denied``, which reads like a broken command rather than a
  boundary.  :func:`check_command_allowed` recognises the literal, unambiguous
  cases — a redirection or a well-known writing verb aimed at a spelled-out
  path outside the boundary — and refuses *before* execution with the same
  wording the file tools use.  It is deliberately conservative: it only refuses
  on evidence, and everything it cannot read is still stopped by Landlock.

**Fail closed.**  When a boundary is registered but Landlock is unavailable
(non-Linux, kernel < 5.13, LSM not enabled), :func:`unavailable_reason` returns
a message and the caller must refuse the command.  A boundary that silently
degrades to "best effort" is the hole this module exists to close.

**What this costs.**  Named plainly, because a boundary whose price is hidden
gets switched off later:

* Writes outside the roots fail — including ones nobody meant as an escape:
  ``/tmp`` scratch files, ``~/.cache``, ``pip install``, ``git config --global``,
  ``/dev/shm``.  A confined agent gets its declared roots and nothing else.
* Env vars no longer survive between commands.  Digit persists them by
  re-dumping ``/tmp/digit-snap-<session>.sh`` after every command; that write is
  now refused.  The dump is already written to fail silently
  (``2>/dev/null || rm -f … || true`` in ``BaseEnvironment._wrap_command``), so
  commands still run and the cwd still tracks — it travels in a stdout marker,
  not a file — but ``export FOO=1`` in one call is gone by the next.
* Character devices a shell cannot work without are granted explicitly
  (``/dev/null``, ``/dev/zero``, ``/dev/full``, ``/dev/random``,
  ``/dev/urandom``, ``/dev/tty``, ``/dev/ptmx``, ``/dev/pts``) — write only, no
  creating new entries there.  ``/dev/shm`` is *not* granted: it is a writable
  tmpfs outside the boundary, which is exactly what is being closed.
"""

from __future__ import annotations

import contextlib
import contextvars
import ctypes
import logging
import os
import re
import struct
import sys
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Landlock syscall surface
# ---------------------------------------------------------------------------

# Numbers are identical on every architecture that has the syscalls (they were
# added to the generic table); an arch without them fails the ABI probe and we
# fall back to "unavailable" rather than calling a wrong number.
_NR_CREATE_RULESET = 444
_NR_ADD_RULE = 445
_NR_RESTRICT_SELF = 446

_CREATE_RULESET_VERSION = 1 << 0
_RULE_PATH_BENEATH = 1

_PRCTL = 157
_PR_SET_NO_NEW_PRIVS = 38

_FS = {
    name: 1 << bit
    for bit, name in enumerate(
        (
            "EXECUTE", "WRITE_FILE", "READ_FILE", "READ_DIR", "REMOVE_DIR",
            "REMOVE_FILE", "MAKE_CHAR", "MAKE_DIR", "MAKE_REG", "MAKE_SOCK",
            "MAKE_FIFO", "MAKE_BLOCK", "MAKE_SYM", "REFER", "TRUNCATE",
            "IOCTL_DEV",
        )
    )
}

# Everything write-shaped. EXECUTE / READ_FILE / READ_DIR / IOCTL_DEV are left
# unhandled on purpose: an unhandled right is always allowed, so a confined
# agent keeps full read and execute access to the machine.
_WRITE_BASE = (
    _FS["WRITE_FILE"] | _FS["REMOVE_DIR"] | _FS["REMOVE_FILE"] | _FS["MAKE_CHAR"]
    | _FS["MAKE_DIR"] | _FS["MAKE_REG"] | _FS["MAKE_SOCK"] | _FS["MAKE_FIFO"]
    | _FS["MAKE_BLOCK"] | _FS["MAKE_SYM"]
)
# Rights that only exist from a given ABI onward; asking for one the running
# kernel does not know is EINVAL, so they are added by version.
_WRITE_BY_ABI = ((2, _FS["REFER"]), (3, _FS["TRUNCATE"]))

# Writable device nodes a shell needs to function at all. `cmd >/dev/null` is in
# Digit's own command wrapper, so without these the terminal tool cannot run one
# command. Granted WRITE_FILE|TRUNCATE only — not MAKE_*, so nothing new can be
# created under /dev.
_DEVICE_GRANTS = (
    "/dev/null", "/dev/zero", "/dev/full", "/dev/random", "/dev/urandom",
    "/dev/tty", "/dev/ptmx", "/dev/pts",
)


class ConfinementUnavailable(RuntimeError):
    """Landlock cannot confine this process."""


def _libc() -> ctypes.CDLL:
    lib = ctypes.CDLL(None, use_errno=True)
    lib.syscall.restype = ctypes.c_long
    return lib


class _RulesetAttr(ctypes.Structure):
    # Only handled_access_fs. Landlock takes an extensible struct: a shorter
    # one is accepted and the missing fields (net, scoped) read as zero.
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


def _path_beneath_attr(access: int, fd: int) -> ctypes.Array:
    """``struct landlock_path_beneath_attr`` — packed ``__u64`` + ``__s32``.

    Built with :mod:`struct` rather than a ``ctypes.Structure``: the kernel
    declares it packed, and expressing that through ``_pack_`` now requires an
    explicit ``_layout_`` that does not exist on every supported Python.
    """
    return ctypes.create_string_buffer(struct.pack("<Qi", access, fd), 12)


_abi_cache: Optional[int] = None


def landlock_abi() -> int:
    """Landlock ABI version supported by the running kernel, 0 when absent."""
    global _abi_cache
    if _abi_cache is not None:
        return _abi_cache
    if sys.platform != "linux":
        _abi_cache = 0
        return 0
    try:
        abi = _libc().syscall(_NR_CREATE_RULESET, None, 0, _CREATE_RULESET_VERSION)
    except Exception as exc:  # pragma: no cover - exotic libc
        logger.debug("landlock probe failed: %s", exc)
        abi = -1
    _abi_cache = int(abi) if abi and abi > 0 else 0
    return _abi_cache


def available() -> bool:
    return landlock_abi() > 0


def _handled_mask(abi: int) -> int:
    mask = _WRITE_BASE
    for since, right in _WRITE_BY_ABI:
        if abi >= since:
            mask |= right
    return mask


def build_preexec(roots: Sequence[str]) -> Callable[[], None]:
    """Build the post-fork hook confining a child to *roots* for writing.

    The ruleset is created here, in the parent, so the hook itself is two bare
    syscalls on already-allocated ctypes objects.  That matters: ``preexec_fn``
    runs after ``fork`` in a process that inherited every lock the threaded
    parent held, so it must not allocate, import, or take a lock.
    """
    abi = landlock_abi()
    if abi <= 0:
        raise ConfinementUnavailable(
            "Landlock is not available on this kernel (needs Linux 5.13+ with "
            "the landlock LSM enabled)."
        )

    libc = _libc()
    handled = _handled_mask(abi)
    attr = _RulesetAttr(handled)
    ruleset_fd = libc.syscall(
        _NR_CREATE_RULESET, ctypes.byref(attr), ctypes.sizeof(attr), 0
    )
    if ruleset_fd < 0:
        raise ConfinementUnavailable(
            f"landlock_create_ruleset failed (errno {ctypes.get_errno()})"
        )

    def _grant(path: str, access: int) -> None:
        try:
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        except OSError:
            return  # a device or root that does not exist grants nothing
        try:
            rule = _path_beneath_attr(access & handled, fd)
            if libc.syscall(
                _NR_ADD_RULE, ruleset_fd, _RULE_PATH_BENEATH, rule, 0
            ) < 0:
                logger.debug("landlock_add_rule failed for %s", path)
        finally:
            os.close(fd)

    granted = 0
    for root in roots:
        if os.path.exists(root):
            _grant(root, handled)
            granted += 1
    if not granted:
        os.close(ruleset_fd)
        raise ConfinementUnavailable(
            "none of the declared write roots exist on disk: "
            + ", ".join(roots or ("<empty>",))
        )
    for dev in _DEVICE_GRANTS:
        _grant(dev, _FS["WRITE_FILE"] | _FS["TRUNCATE"])

    syscall = libc.syscall
    fd = int(ruleset_fd)

    def _apply() -> None:
        # No allocation, no imports, no locks — see the docstring above.
        syscall(_PRCTL, _PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
        syscall(_NR_RESTRICT_SELF, fd, 0)

    _apply._digit_ruleset_fd = fd  # type: ignore[attr-defined]
    return _apply


# ---------------------------------------------------------------------------
# Active confinement, carried to the spawn sites
# ---------------------------------------------------------------------------

_active: contextvars.ContextVar[Optional[Callable[[], None]]] = contextvars.ContextVar(
    "digit_shell_confinement", default=None
)


def preexec_for_current() -> Optional[Callable[[], None]]:
    """The hook for the confinement in force, or ``None`` when unbounded."""
    return _active.get()


def roots_for(task_id: Optional[str]) -> Tuple[str, ...]:
    from agent.cluster_boundary import get_write_roots

    return get_write_roots(task_id)


def unavailable_reason(task_id: Optional[str]) -> Optional[str]:
    """Why a bounded agent must not be given a shell here, or ``None``.

    Fail closed: a registered boundary that cannot be enforced is a refusal,
    not a warning.
    """
    roots = roots_for(task_id)
    if not roots:
        return None
    if available():
        return None
    return (
        "Refusing to run a shell command: this agent has a write boundary "
        f"({', '.join(roots)}) but the boundary cannot be enforced on the "
        "terminal here.\n"
        "  reason : Landlock is unavailable (needs Linux 5.13+ with the "
        "landlock LSM enabled)\n"
        "The file tools remain bounded. Report the need to your parent agent "
        "instead of working around the boundary with a shell."
    )


_DENIAL_MARKERS = ("Permission denied", "Operation not permitted",
                   "PermissionError", "EACCES", "EPERM")


def explain_denial(output: str, task_id: Optional[str]) -> str:
    """Append the boundary's explanation to a kernel refusal.

    Landlock refuses with a bare ``Permission denied``, which reads like a
    broken command.  When the command ran confined and its output carries that
    shape, say which boundary it hit — worded as the likely cause, since an
    unrelated permission error looks identical from here.
    """
    roots = roots_for(task_id)
    if not roots or not output:
        return output
    if not any(marker in output for marker in _DENIAL_MARKERS):
        return output
    return (
        f"{output}\n\n"
        "[write boundary] This agent may write only under: "
        f"{', '.join(roots)}\n"
        "A permission error above is most likely a write outside that "
        "boundary, refused by the kernel rather than by a path check. Reads "
        "and execution are unrestricted. Report the need to your parent agent "
        "instead of working around the boundary."
    )


@contextlib.contextmanager
def confined(task_id: Optional[str]):
    """Confine every process spawned inside this block to *task_id*'s roots.

    A task with no registered boundary is a no-op, so an ordinary Digit run is
    unchanged.
    """
    roots = roots_for(task_id)
    if not roots:
        yield None
        return
    try:
        hook = build_preexec(roots)
    except ConfinementUnavailable:
        raise
    token = _active.set(hook)
    try:
        yield hook
    finally:
        _active.reset(token)
        try:
            os.close(getattr(hook, "_digit_ruleset_fd"))
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Static pre-check: refuse the legible cases with the boundary's own words
# ---------------------------------------------------------------------------

# Verbs whose named operands are written. Value is which operands to take:
# "all" — every non-flag operand; "last" — only the final one (cp/mv/ln style).
_WRITING_VERBS = {
    "tee": "all", "touch": "all", "mkdir": "all", "rmdir": "all", "rm": "all",
    "truncate": "all", "unlink": "all", "shred": "all", "mkfifo": "all",
    "cp": "last", "mv": "last", "ln": "last", "install": "last", "rsync": "last",
}

# A token we can resolve to one literal path: no expansion, no glob, no
# substitution. Anything else is left to Landlock rather than guessed at.
_LITERAL = re.compile(r"^[^$`*?\[\]{}!~\\]+$")

_REDIRECT = re.compile(r"(?:^|[\s;&|(])\d?>>?(?!&)\s*(\S+)")

_SPLIT = re.compile(r"[;&|]{1,2}|\n")


def _dequote(token: str) -> Optional[str]:
    # A redirection operand is grabbed as one whitespace-delimited run, so a
    # command like `sh -c 'echo x > /out/f'` hands us `/out/f'` -- the closing
    # quote belongs to the enclosing word, not the path. Trim stray quoting and
    # separators before judging, or the refusal names a path that does not exist.
    token = token.strip().rstrip("'\";)")
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"":
        token = token[1:-1]
        # Inside double quotes an expansion still expands; only single quotes
        # (and quote-free tokens) are literal enough to judge.
        return token if "$" not in token and "`" not in token else None
    return token


def _candidate_targets(command: str) -> List[str]:
    targets: List[str] = []
    for raw in _REDIRECT.findall(command):
        tok = _dequote(raw)
        if tok:
            targets.append(tok)
    for segment in _SPLIT.split(command):
        words = segment.strip().split()
        if not words:
            continue
        verb = os.path.basename(words[0])
        mode = _WRITING_VERBS.get(verb)
        if not mode:
            continue
        operands = [w for w in words[1:] if not w.startswith("-")]
        if not operands:
            continue
        chosen = operands if mode == "all" else operands[-1:]
        for tok in chosen:
            got = _dequote(tok)
            if got:
                targets.append(got)
    return targets


def check_command_allowed(
    command: str, task_id: Optional[str], cwd: str = ""
) -> Optional[str]:
    """Refusal text when *command* plainly writes outside the boundary.

    Conservative by construction: a target is judged only when it is a literal
    path.  Returning ``None`` is not a promise that the command stays inside —
    that promise is Landlock's.
    """
    from agent.cluster_boundary import check_write_allowed

    roots = roots_for(task_id)
    if not roots or not command:
        return None
    base = cwd or os.getcwd()
    for target in _candidate_targets(command):
        if not _LITERAL.match(target) or target in ("/dev/null", "/dev/stdout",
                                                    "/dev/stderr", "/dev/tty"):
            continue
        resolved = target if os.path.isabs(target) else os.path.join(base, target)
        # The parent directory is what an create/open-for-write touches; judging
        # the leaf alone would miss `> /outside/newfile` under a missing dir.
        err = check_write_allowed(resolved, task_id)
        if err:
            return (
                f"{err}\n"
                f"  command: {command.strip()[:200]}\n"
                f"This refusal is the same write boundary the file tools "
                f"enforce; the shell is not a way around it."
            )
    return None
