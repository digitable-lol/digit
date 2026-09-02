"""A supervisor for delegated agents, with OTP's semantics and OTP's names.

Digit can already nest agents: a lead spawns workers, waits, and folds their
summaries into its own answer.  What it could not do is *supervise* them --
notice that a child terminated abnormally, restart it under a declared rule,
give up when restarting stops helping, and end when the work is done.  Every
one of those is a decision, and until now the only thing in the tree that can
decide is a model.  That is the wrong place for it: a level whose whole job is
"start these, watch them, restart on crash" spends a model call to re-derive a
policy that was already stated, and gets it wrong differently each run.

OTP solved this by making the supervisor *library code*.  ``supervisor.erl``
does not think; the programmer supplies data -- a strategy, a restart
intensity, and one child spec per child -- and the generic behaviour does the
rest.  This module is that behaviour, and it keeps OTP's vocabulary intact
rather than inventing a parallel one:

``strategy``
    ``one_for_one`` -- restart only the child that terminated.
    ``one_for_all`` -- terminate the remaining children and restart them all.
    ``rest_for_one`` -- restart the terminated child and every child declared
    after it, in declaration order.

``restart`` (per child)
    ``permanent`` -- always restarted.
    ``transient`` -- restarted only on *abnormal* termination.  The default
    here, and the only one that makes sense for work that ends: a permanent
    child that completes is restarted anyway, so a task supervisor holding one
    always ends by exceeding its restart intensity.  Implemented faithfully so
    that fact is visible rather than hidden behind a rename.
    ``temporary`` -- never restarted, whatever the termination reason.

``max_restarts`` / ``max_seconds`` (restart intensity)
    More than ``max_restarts`` restarts inside ``max_seconds`` and the
    supervisor terminates instead of continuing -- OTP's protection against a
    child that cannot be fixed by restarting.  Elixir's defaults are 3 restarts
    in 5 seconds; agent children run for minutes, not milliseconds, so 5
    seconds would let an unbounded restart loop through unnoticed.  The window
    defaults to 300 seconds here.  The names and the meaning are OTP's; the
    number is not, and is stated so it can be argued with.

**Fail fast, do not repair.**  A restarted child is a *new* child: a fresh
agent, a fresh conversation, the same brief.  Nothing tries to reason about
what the dead one had half-done.  This is the one OTP rule that transfers to
agents without translation -- an LLM's broken state is exactly the kind nobody
can inspect from outside.

**One deliberate deviation, named.**  An OTP supervisor is permanent: children
that exit normally are restarted or, with ``transient``, simply leave the
supervisor running.  A supervisor here is task-scoped: it exits ``normal`` once
every child has terminated and none needs restarting, because the work it was
started for is then done.  That is a ``Task.Supervisor``, not a
``Supervisor`` -- said plainly rather than glossed over.

The module knows nothing about agents.  A child spec carries a ``start``
callable taking the attempt number and returning the child's result dict; the
delegation layer supplies one that builds a fresh agent and runs it.  That
keeps the policy testable without a model, which is the point.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, wait
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

ONE_FOR_ONE = "one_for_one"
ONE_FOR_ALL = "one_for_all"
REST_FOR_ONE = "rest_for_one"
STRATEGIES = (ONE_FOR_ONE, ONE_FOR_ALL, REST_FOR_ONE)

PERMANENT = "permanent"
TRANSIENT = "transient"
TEMPORARY = "temporary"
RESTART_TYPES = (PERMANENT, TRANSIENT, TEMPORARY)

DEFAULT_MAX_RESTARTS = 3
# Not Elixir's 5 seconds -- see the module docstring.
DEFAULT_MAX_SECONDS = 300.0

# A child result counts as a normal termination only on these statuses...
_NORMAL_STATUSES = frozenset({"completed"})
# ...and only with one of these termination reasons.  Both halves are needed:
# measured live, a child that could not reach the model endpoint at all came
# back as ``status="completed"`` with ``exit_reason="max_iterations"`` and the
# summary "API call failed after 3 retries: Connection error." Keying the
# restart decision on ``status`` alone therefore missed exactly the failure
# restarts exist for.  ``exit_reason`` is the real termination reason -- OTP's
# distinction -- and a result that carries none is taken at its status.
_NORMAL_EXIT_REASONS = frozenset({"completed"})


@dataclass(frozen=True)
class SupFlags:
    """``Supervisor.init/1``'s supervisor flags, by their Elixir names."""

    strategy: str = ONE_FOR_ONE
    max_restarts: int = DEFAULT_MAX_RESTARTS
    max_seconds: float = DEFAULT_MAX_SECONDS


@dataclass
class ChildSpec:
    """One child under supervision.

    ``start(attempt)`` builds and runs the child from scratch and returns its
    result dict; ``attempt`` is 0 on the first start and increments per
    restart.  ``terminate()`` is best-effort: it asks a still-running sibling
    to stop when the strategy takes it down, and may be ``None``, in which case
    the supervisor waits for it and discards the result instead.
    """

    id: str
    start: Callable[[int], Dict[str, Any]]
    restart: str = TRANSIENT
    terminate: Optional[Callable[[], None]] = None


@dataclass
class Restart:
    child_id: str
    attempt: int
    because: str
    at: float = field(default_factory=time.time)


def normalize_flags(spec: Any) -> SupFlags:
    """Turn an operator/model-supplied mapping into :class:`SupFlags`.

    Raises ``ValueError`` naming the accepted values -- a supervisor started
    with a misspelled strategy would silently supervise nothing the way the
    caller meant.
    """
    if spec is None or spec is True:
        return SupFlags()
    if isinstance(spec, SupFlags):
        return spec
    if isinstance(spec, str):
        spec = {"strategy": spec}
    if not isinstance(spec, dict):
        raise ValueError(
            "supervisor must be an object with keys strategy / max_restarts / "
            "max_seconds, or one of: " + ", ".join(STRATEGIES)
        )

    strategy = str(spec.get("strategy") or ONE_FOR_ONE).strip()
    if strategy not in STRATEGIES:
        raise ValueError(
            f"unknown supervision strategy {strategy!r}. OTP has three: "
            + ", ".join(STRATEGIES)
        )
    try:
        max_restarts = int(spec.get("max_restarts", DEFAULT_MAX_RESTARTS))
    except (TypeError, ValueError):
        raise ValueError("max_restarts must be a whole number of restarts")
    if max_restarts < 0:
        raise ValueError("max_restarts must not be negative")
    try:
        max_seconds = float(spec.get("max_seconds", DEFAULT_MAX_SECONDS))
    except (TypeError, ValueError):
        raise ValueError("max_seconds must be a number of seconds")
    if max_seconds <= 0:
        raise ValueError("max_seconds must be greater than zero")
    return SupFlags(
        strategy=strategy, max_restarts=max_restarts, max_seconds=max_seconds
    )


def normalize_restart(value: Any) -> str:
    """Validate a per-child ``restart`` type, defaulting to ``transient``."""
    if value is None:
        return TRANSIENT
    text = str(value).strip()
    if text not in RESTART_TYPES:
        raise ValueError(
            f"unknown restart type {text!r}. OTP has three: "
            + ", ".join(RESTART_TYPES)
        )
    return text


def is_normal(result: Any) -> bool:
    """True when a child's result dict describes a normal termination.

    Both the status and the termination reason must say so.  ``max_iterations``
    is abnormal: the child stopped because it ran out of budget, not because it
    finished -- and that is the shape an unreachable endpoint arrives in.
    """
    if not isinstance(result, dict):
        return False
    if str(result.get("status") or "") not in _NORMAL_STATUSES:
        return False
    reason = result.get("exit_reason")
    if reason is None:
        return True
    return str(reason) in _NORMAL_EXIT_REASONS


class Supervisor:
    """Start children, watch them, restart them by the rules, then exit.

    ``run()`` blocks until the supervisor terminates and returns the report.
    It never raises for a child's failure -- a child that raises is recorded as
    an abnormal termination, which is the event the restart rules exist for.
    """

    def __init__(
        self,
        flags: SupFlags,
        children: Sequence[ChildSpec],
        *,
        on_event: Optional[Callable[..., None]] = None,
        name: str = "sup",
    ) -> None:
        self.flags = flags
        self.children: List[ChildSpec] = list(children)
        self.name = name
        self._on_event = on_event
        self._by_id = {c.id: c for c in self.children}
        self._order = [c.id for c in self.children]
        self._restart_times: deque[float] = deque()
        self._restarts: List[Restart] = []
        self._attempts: Dict[str, int] = {c.id: 0 for c in self.children}
        self._results: Dict[str, Any] = {}
        self._lock = threading.RLock()

    # -- events ------------------------------------------------------------

    def _emit(self, event: str, **fields: Any) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event, supervisor=self.name, **fields)
        except Exception as exc:  # pragma: no cover - a journal must not kill
            logger.debug("supervisor event %s failed: %s", event, exc)

    # -- strategy ----------------------------------------------------------

    def _affected(self, child_id: str) -> List[str]:
        """Which children this strategy takes down when *child_id* dies."""
        if self.flags.strategy == ONE_FOR_ALL:
            return list(self._order)
        if self.flags.strategy == REST_FOR_ONE:
            idx = self._order.index(child_id)
            return list(self._order[idx:])
        return [child_id]

    def _should_restart(self, spec: ChildSpec, result: Any) -> bool:
        if spec.restart == TEMPORARY:
            return False
        if spec.restart == PERMANENT:
            return True
        return not is_normal(result)

    # -- one wave ----------------------------------------------------------

    def _run_wave(self, ids: Sequence[str]) -> Dict[str, Any]:
        """Start every child in *ids* concurrently and collect their results.

        When a child terminates abnormally under ``one_for_all`` /
        ``rest_for_one``, the siblings the strategy takes down are asked to
        terminate while the wave is still running, so the supervisor does not
        sit through work it has already decided to discard.
        """
        from tools.daemon_pool import DaemonThreadPoolExecutor

        results: Dict[str, Any] = {}
        asked_to_stop: set[str] = set()
        with DaemonThreadPoolExecutor(max_workers=max(1, len(ids))) as pool:
            futures = {}
            for cid in ids:
                spec = self._by_id[cid]
                attempt = self._attempts[cid]
                self._emit(
                    "child_started", child_id=cid, attempt=attempt,
                    restart=spec.restart,
                )
                futures[pool.submit(self._start_one, spec, attempt)] = cid

            pending = set(futures)
            while pending:
                done, pending = wait(pending, timeout=0.5,
                                     return_when=FIRST_COMPLETED)
                for fut in done:
                    cid = futures[fut]
                    try:
                        results[cid] = fut.result()
                    except Exception as exc:  # start() is not supposed to raise
                        results[cid] = {
                            "status": "error",
                            "error": f"child start raised: {exc}",
                        }
                    spec = self._by_id[cid]
                    normal = is_normal(results[cid])
                    self._emit(
                        "child_terminated",
                        child_id=cid,
                        attempt=self._attempts[cid],
                        reason="normal" if normal else "abnormal",
                        status=(results[cid] or {}).get("status")
                        if isinstance(results[cid], dict) else None,
                        # The termination reason, which is what normality is
                        # decided on -- a child can be "completed" and still
                        # have stopped for a reason that is not completion.
                        exit_reason=(results[cid] or {}).get("exit_reason")
                        if isinstance(results[cid], dict) else None,
                        error=str((results[cid] or {}).get("error") or "")[:300]
                        if isinstance(results[cid], dict) else None,
                    )
                    if normal or self.flags.strategy == ONE_FOR_ONE:
                        continue
                    if not self._should_restart(spec, results[cid]):
                        continue
                    # The strategy takes siblings down with it. Ask the ones
                    # still running to stop; their results are discarded.
                    for sib in self._affected(cid):
                        if sib == cid or sib in results or sib in asked_to_stop:
                            continue
                        if sib not in ids:
                            continue
                        asked_to_stop.add(sib)
                        self._emit(
                            "child_terminated_by_supervisor",
                            child_id=sib, because=f"{self.flags.strategy}:{cid}",
                        )
                        term = self._by_id[sib].terminate
                        if term is None:
                            continue
                        try:
                            term()
                        except Exception as exc:
                            logger.debug("terminate(%s) failed: %s", sib, exc)
        for cid in asked_to_stop:
            # It was asked to stop, but it may have finished first. Keep what
            # it actually returned and mark why it was taken down -- a journal
            # that rewrites a completed child as "shutdown" is a journal that
            # cannot be checked against the tree afterwards.
            entry = results.get(cid)
            if isinstance(entry, dict):
                entry["terminated_by_supervisor"] = self.flags.strategy
            else:
                results[cid] = {
                    "status": "shutdown",
                    "error": f"terminated by supervisor ({self.flags.strategy})",
                    "terminated_by_supervisor": self.flags.strategy,
                }
        return results

    def _start_one(self, spec: ChildSpec, attempt: int) -> Dict[str, Any]:
        return spec.start(attempt)

    # -- the loop ----------------------------------------------------------

    def run(self) -> Dict[str, Any]:
        started = time.time()
        self._emit(
            "supervisor_start",
            strategy=self.flags.strategy,
            max_restarts=self.flags.max_restarts,
            max_seconds=self.flags.max_seconds,
            children=list(self._order),
        )
        wave_ids = list(self._order)
        reason = "normal"
        waves = 0
        while wave_ids:
            waves += 1
            wave_results = self._run_wave(wave_ids)
            self._results.update(wave_results)

            restart_set: List[str] = []
            for cid in self._order:
                if cid not in wave_results:
                    continue
                spec = self._by_id[cid]
                if not self._should_restart(spec, wave_results[cid]):
                    continue
                for affected in self._affected(cid):
                    if affected not in restart_set:
                        restart_set.append(affected)

            # A temporary child is never restarted, by any route -- not by its
            # own termination and not by a sibling's under one_for_all.
            restart_set = [
                cid for cid in restart_set
                if self._by_id[cid].restart != TEMPORARY
            ]

            next_wave: List[str] = []
            now = time.monotonic()
            for cid in restart_set:
                self._restart_times.append(now)
                horizon = now - self.flags.max_seconds
                while self._restart_times and self._restart_times[0] < horizon:
                    self._restart_times.popleft()
                if len(self._restart_times) > self.flags.max_restarts:
                    reason = "shutdown"
                    self._emit(
                        "supervisor_intensity_exceeded",
                        child_id=cid,
                        restarts=len(self._restart_times),
                        max_restarts=self.flags.max_restarts,
                        max_seconds=self.flags.max_seconds,
                    )
                    break
                self._attempts[cid] += 1
                # Name the termination reason before the status: a child that
                # could not reach the model comes back "completed", and a
                # journal line reading "restarted because: completed" explains
                # nothing.
                _last = self._results.get(cid) or {}
                because = str(
                    _last.get("error")
                    or _last.get("exit_reason")
                    or _last.get("status")
                    or "abnormal termination"
                )[:300]
                self._restarts.append(
                    Restart(child_id=cid, attempt=self._attempts[cid],
                            because=because)
                )
                self._emit(
                    "child_restart",
                    child_id=cid,
                    attempt=self._attempts[cid],
                    strategy=self.flags.strategy,
                    because=because,
                )
                next_wave.append(cid)
            if reason != "normal":
                break
            wave_ids = next_wave

        report = {
            "reason": reason,
            "strategy": self.flags.strategy,
            "max_restarts": self.flags.max_restarts,
            "max_seconds": self.flags.max_seconds,
            "waves": waves,
            "attempts": dict(self._attempts),
            "restarts": [
                {"child_id": r.child_id, "attempt": r.attempt,
                 "because": r.because, "at": round(r.at, 3)}
                for r in self._restarts
            ],
            "children": dict(self._results),
            "duration_seconds": round(time.time() - started, 2),
        }
        self._emit(
            "supervisor_exit",
            reason=reason,
            waves=waves,
            restarts=len(self._restarts),
            duration_seconds=report["duration_seconds"],
        )
        return report
