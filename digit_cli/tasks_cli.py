"""``digit tasks`` — the shared Digitable tracker, addressed by uuid only.

Several agents and the owner work the same Taskwarrior database (the
``.digitable-tasks`` tree that sits beside the checkouts). This command is the
listing / creation / closing surface over it, and the skill
``skills/productivity/digitable-tasks`` points the model here instead of at a
raw ``task`` shell-out.

Why a command and not a model tool
----------------------------------
Rung 2 of the Footprint Ladder (see AGENTS.md): the capability is expressible
as shell commands, so the agent runs ``digit tasks …`` guided by a skill and
the model-tool schema — which every API call pays for — does not grow.

Why uuid only, enforced here
----------------------------
Taskwarrior shows a small integer next to every pending task and accepts it as
a reference. That integer is **positional**: it is recomputed from the pending
set on every garbage-collection pass, so closing one task renumbers the ones
after it. An agent that lists tasks, thinks, and then closes "number 2" can
close a task it never read. On 2026-08-04 seven agents in a row did exactly
that on this database.

A prompt cannot fix that, because the failure happens when the model is being
*reasonable*: the number was right when it was printed. So the rule lives in
the tool. :func:`require_uuid` accepts nothing but a full canonical uuid, and
every subcommand that names an existing task goes through it.

Note that "accept a uuid prefix" is not a safe relaxation, and this is the
subtle part: Taskwarrior itself resolves unique uuid prefixes, and a bare
integer like ``66`` is a syntactically valid hex prefix. Prefix matching would
therefore silently re-admit the exact class of mistake the rule exists to
prevent. Full uuids only.

Creation mints the uuid on our side and hands it to ``task import`` rather than
parsing it back out of ``task add`` output. ``task add`` reports the positional
integer, and recovering the uuid from it is a race when several agents write
concurrently — which is the normal state of this database.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# Task references
# --------------------------------------------------------------------------

#: Canonical 8-4-4-4-12 hex form, anchored. Case-insensitive on input; we
#: normalise to lowercase so the same task always prints the same way.
_UUID_RE = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)

#: A bare positional integer — the reference we refuse.
_NUMERIC_RE = re.compile(r"\A\d+\Z")

#: Taskwarrior id sets and ranges: ``2,5``, ``3-7``, ``1,4-6``.
_NUMERIC_SET_RE = re.compile(r"\A\d+(?:[,-]\d+)+\Z")

#: The directory name of the shared tracker, searched for from the cwd up.
TRACKER_DIR_NAME = ".digitable-tasks"

_HINT = (
    "Run `digit tasks list` and copy the uuid column — it is stable for the "
    "life of the task."
)


class TaskRefError(ValueError):
    """A task reference that this command refuses to pass to Taskwarrior.

    ``kind`` is one of ``numeric``, ``numeric_set``, ``partial`` or
    ``malformed``, so callers (and tests) can distinguish "you used the
    renumbering id" from "you mistyped a uuid" without matching on prose.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def require_uuid(ref: str) -> str:
    """Return ``ref`` as a canonical lowercase uuid, or raise :class:`TaskRefError`.

    This is the whole rule, in one place, so that every entry point — argparse
    handler, skill-driven invocation, future callers — cannot get it wrong by
    forgetting a check.
    """
    text = (ref or "").strip()
    if _UUID_RE.match(text):
        return text.lower()

    if _NUMERIC_RE.match(text):
        raise TaskRefError(
            "numeric",
            f"'{text}' is a positional id, not a uuid. Positional ids are "
            f"recomputed whenever the pending set changes, so closing one task "
            f"renumbers the rest and this reference can point at a different "
            f"task than the one you read. " + _HINT,
        )
    if _NUMERIC_SET_RE.match(text):
        raise TaskRefError(
            "numeric_set",
            f"'{text}' is a positional id range. This command takes exactly "
            f"one task, by uuid. " + _HINT,
        )
    # Hex-ish but short: Taskwarrior would resolve this as a uuid prefix, which
    # is precisely how a positional id sneaks back in.
    if re.fullmatch(r"[0-9a-fA-F-]{1,35}", text):
        raise TaskRefError(
            "partial",
            f"'{text}' is at most a uuid prefix. Prefixes are not accepted: a "
            f"positional id is itself a valid hex prefix, so allowing them "
            f"would re-admit the mistake this rule prevents. " + _HINT,
        )
    raise TaskRefError(
        "malformed",
        f"'{text}' is not a uuid. Expected 8-4-4-4-12 hex, e.g. "
        f"276c477e-9f8e-4f1b-9950-d05a3a725c51. " + _HINT,
    )


def new_uuid() -> str:
    """Mint a uuid for a task we are about to create."""
    return str(_uuid.uuid4())


# --------------------------------------------------------------------------
# Locating the tracker
# --------------------------------------------------------------------------


class TrackerNotFound(RuntimeError):
    """No ``.digitable-tasks`` tree could be resolved."""


def find_tracker(explicit: Optional[str] = None,
                 *,
                 start: Optional[Path] = None,
                 environ: Optional[Dict[str, str]] = None,
                 config: Optional[Dict[str, Any]] = None) -> Path:
    """Resolve the Taskwarrior data directory.

    Order: ``--data-dir`` → ``config.yaml`` (``tasks.data_dir``) → Taskwarrior's
    own ``TASKDATA`` → a ``.digitable-tasks/data`` directory found by walking up
    from the working directory.

    ``TASKDATA`` is honoured because it is *Taskwarrior's* published contract,
    not a new ``DIGIT_*`` knob: a user who already exports it for their own
    ``task`` usage should not have to repeat themselves. Behavioural
    configuration lives in ``config.yaml``.
    """
    env = os.environ if environ is None else environ

    for candidate in (
        explicit,
        ((config or {}).get("tasks") or {}).get("data_dir") or None,
        env.get("TASKDATA") or None,
    ):
        if candidate:
            path = Path(os.path.expanduser(str(candidate)))
            if not path.is_dir():
                raise TrackerNotFound(f"{path} is not a directory")
            return path

    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        data = directory / TRACKER_DIR_NAME / "data"
        if data.is_dir():
            return data

    raise TrackerNotFound(
        f"No {TRACKER_DIR_NAME}/data found in {here} or any parent. Point at "
        f"it with `digit tasks --data-dir PATH`, set `tasks.data_dir` in "
        f"config.yaml, or export TASKDATA."
    )


# --------------------------------------------------------------------------
# Taskwarrior transport
# --------------------------------------------------------------------------


class TrackerError(RuntimeError):
    """Taskwarrior could not be run, or refused the operation."""


class Tracker:
    """Thin, uuid-native wrapper over the ``task`` binary.

    Every method that names an existing task takes a full uuid, checks the task
    exists before mutating, and re-reads stored state afterwards instead of
    trusting the exit code. Two measured reasons (taskwarrior 2.6.2):

    * ``rc.verbose=nothing`` is required to keep ``export`` output parseable,
      and it also silences failure text: ``task <unknown-uuid> done`` exits 1
      with **empty** stdout and stderr. The code says something went wrong but
      cannot say what, so an up-front existence check is what turns that into a
      message naming the uuid and the database.
    * ``task <unknown-uuid> export`` exits 0 with an empty array — correctly, it
      is a successful query with no rows. So a non-zero code is not available as
      a uniform "did not exist" signal across reads and writes.
    """

    def __init__(self, data_dir: Path, *, binary: str = "task",
                 timeout: float = 30.0) -> None:
        self.data_dir = Path(data_dir)
        self.binary = binary
        self.timeout = timeout

    # -- plumbing ---------------------------------------------------------

    @property
    def taskrc(self) -> str:
        """The rc file to use.

        The shared tracker keeps its own ``taskrc`` beside the data directory;
        using it preserves any report or UDA definitions the team relies on.
        With no rc file at all Taskwarrior stops to ask whether it should
        create one, so the fallback is ``/dev/null`` — an empty, valid rc —
        rather than nothing.
        """
        sibling = self.data_dir.parent / "taskrc"
        return str(sibling) if sibling.is_file() else os.devnull

    def _run(self, argv: Sequence[str], *,
             stdin: Optional[str] = None) -> Tuple[int, str, str]:
        cmd = [
            self.binary,
            f"rc:{self.taskrc}",
            f"rc.data.location={self.data_dir}",
            "rc.confirmation=off",
            "rc.verbose=nothing",
            "rc.json.array=on",
            *argv,
        ]
        env = dict(os.environ)
        # Explicit rc/data on the command line wins, but a stale TASKDATA in
        # the environment still prints an override banner onto stdout and
        # corrupts JSON parsing. Drop both.
        env.pop("TASKDATA", None)
        env.pop("TASKRC", None)
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                input=stdin, timeout=self.timeout, env=env, check=False,
            )
        except FileNotFoundError as exc:
            raise TrackerError(
                f"`{self.binary}` not found on PATH. The shared tracker is a "
                f"Taskwarrior database; install taskwarrior to use it."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise TrackerError(
                f"`{self.binary}` did not finish within {self.timeout:g}s — "
                f"the database may be locked by another writer."
            ) from exc
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    # -- reads ------------------------------------------------------------

    def export(self, filters: Iterable[str] = ()) -> List[Dict[str, Any]]:
        """Return matching tasks as dicts. Empty result is not an error."""
        code, out, err = self._run([*filters, "export"])
        text = out.strip()
        if not text:
            if code != 0:
                raise TrackerError(err.strip() or f"task export failed ({code})")
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TrackerError(
                f"could not parse `task export` output: {exc}"
            ) from exc
        return data if isinstance(data, list) else [data]

    def get(self, task_uuid: str) -> Optional[Dict[str, Any]]:
        """Return one task by uuid, or ``None`` when it does not exist."""
        wanted = require_uuid(task_uuid)
        for task in self.export([wanted]):
            # Filtering by uuid can also match on other fields in odd
            # configurations; compare explicitly rather than trusting the
            # filter to have been exact.
            if str(task.get("uuid", "")).lower() == wanted:
                return task
        return None

    def require(self, task_uuid: str) -> Dict[str, Any]:
        task = self.get(task_uuid)
        if task is None:
            raise TrackerError(
                f"no task with uuid {require_uuid(task_uuid)} in "
                f"{self.data_dir}. " + _HINT
            )
        return task

    # -- writes -----------------------------------------------------------

    def add(self, description: str, *, project: Optional[str] = None,
            tags: Sequence[str] = (), priority: Optional[str] = None,
            task_uuid: Optional[str] = None) -> str:
        """Create a task and return its uuid.

        The uuid is minted here and imported, so the caller knows it without a
        follow-up lookup — see the module docstring on why reading it back out
        of ``task add`` is a race.
        """
        text = (description or "").strip()
        if not text:
            raise TrackerError("a task needs a description")
        assigned = require_uuid(task_uuid) if task_uuid else new_uuid()

        record: Dict[str, Any] = {
            "uuid": assigned,
            "description": text,
            "status": "pending",
            "entry": _now_stamp(),
        }
        if project:
            record["project"] = project
        if tags:
            record["tags"] = list(tags)
        if priority:
            record["priority"] = priority

        code, out, err = self._run(["import", "-"], stdin=json.dumps(record))
        if self.get(assigned) is None:
            raise TrackerError(
                (err.strip() or out.strip() or f"task import failed ({code})")
            )
        return assigned

    def annotate(self, task_uuid: str, note: str) -> Dict[str, Any]:
        """Attach an annotation; returns the task as it stands afterwards."""
        wanted = require_uuid(task_uuid)
        text = (note or "").strip()
        if not text:
            raise TrackerError("an annotation needs text")
        before = self.require(wanted)
        before_count = len(before.get("annotations") or ())

        _code, out, err = self._run([wanted, "annotate", "--", text])

        after = self.require(wanted)
        if len(after.get("annotations") or ()) <= before_count:
            raise TrackerError(
                f"annotation was not recorded on {wanted}"
                + _because(out, err)
            )
        return after

    def done(self, task_uuid: str, *, note: Optional[str] = None) -> Dict[str, Any]:
        """Close a task, optionally annotating it first.

        The annotation goes on before the status change so that a task is never
        left closed-without-evidence if the second step fails.
        """
        wanted = require_uuid(task_uuid)
        task = self.require(wanted)
        if task.get("status") == "completed":
            raise TrackerError(f"{wanted} is already completed")

        if note:
            self.annotate(wanted, note)

        _code, out, err = self._run([wanted, "done"])

        after = self.get(wanted)
        if after is None or after.get("status") != "completed":
            raise TrackerError(
                f"{wanted} did not reach status completed"
                + _because(out, err)
                + " Reported from the stored state rather than the exit code, "
                  "because a failed mutation can exit non-zero with no message "
                  "under the machine-readable verbosity this command needs."
            )
        return after


def _because(out: str, err: str) -> str:
    """Fold whatever Taskwarrior said into our own message.

    Under ``rc.verbose=nothing`` most failures are mute, but some — a
    permission problem on the data files, for one — do carry text worth
    repeating verbatim rather than replacing with a guess at the cause.
    """
    said = (err or "").strip() or (out or "").strip()
    return f": {said}" if said else "."


def _now_stamp() -> str:
    """Taskwarrior's UTC timestamp format."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _short(text: str, width: int) -> str:
    text = " ".join((text or "").split())
    if width <= 1 or len(text) <= width:
        return text
    return text[: width - 1] + "…"


def render_list(tasks: Sequence[Dict[str, Any]], *, width: int = 100) -> str:
    """Render tasks with the uuid first.

    The uuid leads and the positional id is not printed at all. Printing both
    is what trains an agent to reach for the short one.
    """
    if not tasks:
        return "  no matching tasks"
    desc_width = max(20, width - 40)
    lines = [
        f"  {'uuid':36}  {'project':10}  description",
        f"  {'-' * 36}  {'-' * 10}  {'-' * min(desc_width, 40)}",
    ]
    for task in tasks:
        lines.append(
            f"  {str(task.get('uuid', '?')):36}  "
            f"{_short(str(task.get('project') or '-'), 10):10}  "
            f"{_short(str(task.get('description') or ''), desc_width)}"
        )
    lines.append("")
    lines.append(f"  {len(tasks)} task(s). Close one with: digit tasks done <uuid>")
    return "\n".join(lines)


def render_show(task: Dict[str, Any]) -> str:
    lines = [
        f"  uuid        {task.get('uuid')}",
        f"  status      {task.get('status')}",
        f"  project     {task.get('project') or '-'}",
        f"  entry       {task.get('entry') or '-'}",
    ]
    if task.get("tags"):
        lines.append(f"  tags        {' '.join(task['tags'])}")
    lines.append(f"  description {task.get('description') or ''}")
    annotations = task.get("annotations") or []
    if annotations:
        lines.append("")
        lines.append(f"  annotations ({len(annotations)}):")
        for note in annotations:
            stamp = note.get("entry", "")
            lines.append(f"    {stamp}  {note.get('description', '')}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------


def _tracker_from_args(args) -> Tracker:
    from digit_cli.config import load_config

    try:
        config = load_config() or {}
    except Exception:
        config = {}
    data_dir = find_tracker(getattr(args, "data_dir", None), config=config)
    return Tracker(data_dir)


def _cmd_list(args) -> int:
    tracker = _tracker_from_args(args)
    filters: List[str] = []
    if args.project:
        filters.append(f"project:{args.project}")
    status = args.status
    if status != "all":
        filters.append(f"status:{status}")
    tasks = tracker.export(filters)
    tasks.sort(key=lambda t: (str(t.get("project") or ""), str(t.get("entry") or "")))
    if args.json:
        print(json.dumps(tasks, ensure_ascii=False, indent=2))
    else:
        print(render_list(tasks))
    return 0


def _cmd_add(args) -> int:
    tracker = _tracker_from_args(args)
    created = tracker.add(
        " ".join(args.description),
        project=args.project,
        tags=args.tag or (),
        priority=args.priority,
    )
    if args.json:
        print(json.dumps({"uuid": created}, ensure_ascii=False))
    else:
        print(f"  created {created}")
    return 0


def _cmd_show(args) -> int:
    tracker = _tracker_from_args(args)
    task = tracker.require(args.uuid)
    if args.json:
        print(json.dumps(task, ensure_ascii=False, indent=2))
    else:
        print(render_show(task))
    return 0


def _cmd_note(args) -> int:
    tracker = _tracker_from_args(args)
    task = tracker.annotate(args.uuid, " ".join(args.text))
    print(f"  annotated {task.get('uuid')} "
          f"({len(task.get('annotations') or ())} annotation(s))")
    return 0


def _cmd_done(args) -> int:
    tracker = _tracker_from_args(args)
    task = tracker.done(args.uuid, note=" ".join(args.note) if args.note else None)
    print(f"  completed {task.get('uuid')}")
    return 0


_HANDLERS = {
    "list": _cmd_list,
    "add": _cmd_add,
    "show": _cmd_show,
    "note": _cmd_note,
    "done": _cmd_done,
}


def tasks_command(args) -> int:
    """Dispatch a parsed ``digit tasks`` invocation."""
    sub = getattr(args, "tasks_command", None) or "list"
    handler = _HANDLERS.get(sub)
    if handler is None:
        print(f"Unknown tasks subcommand: {sub}", file=sys.stderr)
        print("Run `digit tasks -h` for usage.", file=sys.stderr)
        return 1
    try:
        return handler(args)
    except TaskRefError as exc:
        # Exit 2 distinguishes "you used a forbidden reference" from a generic
        # failure, so a caller can react without parsing the message.
        print(f"  refused: {exc}", file=sys.stderr)
        return 2
    except (TrackerError, TrackerNotFound) as exc:
        print(f"  error: {exc}", file=sys.stderr)
        return 1


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def add_parser(subparsers) -> argparse.ArgumentParser:
    """Register the ``tasks`` subcommand tree. Returns the top parser."""
    parser = subparsers.add_parser(
        "tasks",
        help="Shared Digitable task tracker (list, add, close) — uuid only",
        description=(
            "Listing, creation and closing over the shared .digitable-tasks "
            "Taskwarrior database that several agents and the owner work at "
            "once.\n\n"
            "Tasks are named by uuid and only by uuid. Taskwarrior's short "
            "positional ids are recomputed whenever the pending set changes, "
            "so a reference read before a close can point at a different task "
            "after it; this command refuses them (exit 2) instead of relying "
            "on the caller to remember.\n\n"
            "  digit tasks list --project DIGIT\n"
            "  digit tasks add --project DIGIT 'what should become true'\n"
            "  digit tasks show <uuid>\n"
            "  digit tasks note <uuid> 'what proves it'\n"
            "  digit tasks done <uuid> --note 'what proves it'"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--data-dir", default=None,
        help="Taskwarrior data directory (default: tasks.data_dir from "
             "config.yaml, else $TASKDATA, else a .digitable-tasks/data "
             "directory found from the working directory upwards)",
    )
    sub = parser.add_subparsers(dest="tasks_command")

    p_list = sub.add_parser("list", help="List tasks, uuid first")
    p_list.add_argument("--project", default=None, help="restrict to one project")
    p_list.add_argument("--status", default="pending",
                        choices=("pending", "completed", "all"),
                        help="which tasks to show (default: pending)")
    p_list.add_argument("--json", action="store_true", help="machine-readable output")

    p_add = sub.add_parser("add", help="Create a task; prints its uuid")
    p_add.add_argument("description", nargs="+", help="what should become true")
    p_add.add_argument("--project", default=None)
    p_add.add_argument("--tag", action="append", default=[],
                       help="tag to attach (repeatable)")
    p_add.add_argument("--priority", default=None, choices=("H", "M", "L"))
    p_add.add_argument("--json", action="store_true")

    p_show = sub.add_parser("show", help="Show one task and its annotations")
    p_show.add_argument("uuid", help="full task uuid")
    p_show.add_argument("--json", action="store_true")

    p_note = sub.add_parser("note", help="Annotate a task")
    p_note.add_argument("uuid", help="full task uuid")
    p_note.add_argument("text", nargs="+", help="annotation text")

    p_done = sub.add_parser("done", help="Close a task")
    p_done.add_argument("uuid", help="full task uuid")
    p_done.add_argument("--note", nargs="+", default=None,
                        help="annotation recorded before closing")

    parser.set_defaults(func=tasks_command)
    return parser
