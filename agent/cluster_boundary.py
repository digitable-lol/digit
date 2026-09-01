"""Per-agent write boundaries and an append-only ledger for delegated agents.

Digit already isolates a delegated child's *conversation* (fresh history), its
*tools* (``DELEGATE_BLOCKED_TOOLS``), and its *task id* (own terminal session
and file-state cache).  What it does not isolate is the **filesystem**: the
only allow-list in ``agent/file_safety.py`` is ``DIGIT_WRITE_SAFE_ROOT``, which
is read from the process environment and therefore applies identically to every
agent in the process.  A parent and its children cannot be given different
boundaries, so a worker can write anywhere its overagent can.

This module adds the missing piece: a write root **keyed by task id**, so each
level of a delegation tree gets its own boundary, and a ledger that records who
spawned whom with which task and how it ended.

Two rules make the boundary meaningful:

* **Narrowing only.**  A child's root must lie inside its parent's root.  A
  brief that asks for a wider root is refused at registration time, so a lead
  cannot hand a worker more filesystem than the lead itself holds.
* **Refusal, not warning.**  ``check_write_allowed`` returns an error string
  that the caller turns into a tool error.  Nothing here degrades to a log line.

Scope, stated honestly: this governs the *file tools* (``write_file``,
``patch``), which is where ``_check_sensitive_path`` already sits.  It does not
sandbox the terminal tool — a child that can run shell commands runs as the same
OS user and can write anywhere that user can.  Confining that requires the
container/SSH terminal backends, not a path check.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

# Env fallback for the root (depth-0) agent's boundary. Kept separate from
# DIGIT_WRITE_SAFE_ROOT so enabling cluster boundaries does not silently change
# the meaning of the existing process-global knob.
ENV_ROOT = "DIGIT_CLUSTER_WRITE_ROOT"
ENV_LEDGER = "DIGIT_CLUSTER_LEDGER"

_lock = threading.RLock()
# task_id -> tuple of resolved absolute roots. Empty tuple means "declared, but
# nothing writable"; absence from the dict means "no boundary declared".
_roots: Dict[str, Tuple[str, ...]] = {}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _resolve(path: str) -> str:
    return os.path.realpath(os.path.expanduser(str(path)))


def _within(path: str, root: str) -> bool:
    """True when *path* is *root* itself or lives underneath it."""
    return path == root or path.startswith(root + os.sep)


def _normalize(roots: Iterable[str]) -> Tuple[str, ...]:
    out: list[str] = []
    for r in roots:
        if not r:
            continue
        resolved = _resolve(r)
        if resolved not in out:
            out.append(resolved)
    return tuple(out)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def env_root() -> Tuple[str, ...]:
    """Boundary for agents that have none registered (the root agent)."""
    raw = os.getenv(ENV_ROOT, "")
    if not raw:
        return ()
    return _normalize(raw.split(os.pathsep))


def get_write_roots(task_id: Optional[str]) -> Tuple[str, ...]:
    """Roots governing *task_id*, falling back to the env root.

    Returning ``()`` means "unbounded" — the pre-existing behaviour, so a Digit
    run that never registers a boundary behaves exactly as before.
    """
    if task_id:
        with _lock:
            if task_id in _roots:
                return _roots[task_id]
    return env_root()


def set_write_root(
    task_id: str,
    roots: Iterable[str],
    *,
    parent_task_id: Optional[str] = None,
) -> Optional[str]:
    """Register *roots* as the boundary for *task_id*.

    Enforces the narrowing rule against the parent's boundary. Returns ``None``
    on success, or an error string naming the offending root — the caller is
    expected to refuse the spawn rather than continue with a wider boundary.
    """
    requested = _normalize(roots)
    parent_roots = get_write_roots(parent_task_id) if parent_task_id else ()

    if parent_roots and requested:
        for want in requested:
            if not any(_within(want, p) for p in parent_roots):
                return (
                    f"write_root {want!r} is outside the parent's boundary "
                    f"({', '.join(parent_roots)}). A delegated agent may only be "
                    f"given a subset of its parent's write boundary."
                )

    # No explicit request: inherit the parent's boundary verbatim.
    if not requested:
        requested = parent_roots

    with _lock:
        _roots[task_id] = requested
    return None


def clear_write_root(task_id: str) -> None:
    with _lock:
        _roots.pop(task_id, None)


def check_write_allowed(path: str, task_id: Optional[str]) -> Optional[str]:
    """Return an error message when *task_id* may not write *path*."""
    roots = get_write_roots(task_id)
    if not roots:
        return None
    resolved = _resolve(path)
    if any(_within(resolved, r) for r in roots):
        return None
    return (
        f"Refusing to write outside this agent's write boundary.\n"
        f"  path : {resolved}\n"
        f"  allowed: {', '.join(roots)}\n"
        f"This agent was delegated a bounded slice of the filesystem; report the "
        f"need to your parent agent instead of widening the boundary yourself."
    )


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def ledger_path() -> Optional[str]:
    """Where cluster events are appended, or ``None`` when disabled."""
    explicit = os.getenv(ENV_LEDGER, "").strip()
    if explicit:
        return explicit
    try:
        from digit_constants import get_digit_dir

        return str(get_digit_dir("cluster", "cluster") / "ledger.jsonl")
    except Exception:
        return None


def record(event: str, **fields: Any) -> None:
    """Append one event. Never raises into the agent loop."""
    path = ledger_path()
    if not path:
        return
    row = {"ts": round(time.time(), 3), "event": event}
    row.update(fields)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:  # pragma: no cover - best effort by design
        logger.debug("cluster ledger write failed: %s", exc)


def read_ledger(path: Optional[str] = None) -> list[Dict[str, Any]]:
    """Load the ledger as a list of rows (for reporting and tests)."""
    target = path or ledger_path()
    if not target or not os.path.exists(target):
        return []
    rows: list[Dict[str, Any]] = []
    with open(target, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows
