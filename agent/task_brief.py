"""RCTF task briefs for delegated agents.

``delegate_task`` accepts a free-text ``goal`` and ``context``. That is enough
for a one-off subagent and not enough for a cluster: a lead that retells a task
in its own words drops the provenance, the neighbours, and the falsifier, and
the worker returns something nobody can evaluate.

This module carries the brief contract from
``dotfiles/skills/cluster-agent-setup/references/task-brief.md`` into code --
Role, Context, Task, Format, plus the five required contents -- so a brief that
omits a required part is refused at dispatch instead of producing an
unverifiable answer several minutes later.

The rendered text is what the child actually reads; ``validate`` is what stops
a retelling from reaching it.

**Composition, not obedience.**  Asking a lead to write the brief does not
work -- but not for the reason it first appeared to.  Three separate causes
were measured on qwen2.5:7b-instruct and 14b-instruct, and only the third is
about the model:

1. ``run_agent._dispatch_delegate_task`` -- the only path a *model* can reach
   -- forwarded ``goal``, ``context``, ``tasks``, ``max_iterations`` and
   ``role``, and nothing else.  ``brief`` and ``skill`` were in the tool schema
   but not in that call, so a lead that did send a brief had it dropped
   silently.  Every earlier measurement of "the lead does not brief its worker"
   could not distinguish that from the lead's own omission.  With the argument
   forwarded, the 7b lead sends a complete 702-character RCTF brief object.
2. ``validate`` refused briefs whose ``role`` was 12 characters, naming the
   missing length -- and the lead answered by rewriting the brief *in its
   reply* and never making the corrected call.  A refusal costs a whole worker
   on a model that will not retry.
3. Sometimes the lead writes the instructions in prose and never calls the tool
   at all.  Nothing about brief handling touches that one.

So the brief is composed rather than demanded.  Three of the five required
contents of ``task-brief.md`` are facts the *harness* holds and the model can
only restate badly -- the write boundary, the neighbours (the sibling tasks in
the same fan-out), the role and what is reserved to the dispatcher -- and the
fourth, the cell, is the ``goal`` the model already has to send.

Only the definition of done is irreducibly the dispatcher's, and it is offered
as one flat string (``brief_done``) rather than as a key inside a nested
object.  Whatever the model supplies wins; whatever it omits is composed;
nothing is refused for being thin.  Every child therefore reads a complete RCTF
brief, and the ledger records, field by field, which half wrote it -- because
"the worker got a brief" and "the lead wrote one" are different claims.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Field -> human name, in the order they are rendered.
REQUIRED_FIELDS: Dict[str, str] = {
    "role": "Role -- what the worker is and what it decides",
    "known": "Context/KNOWN -- established facts with provenance",
    "task": "Task -- the one cell, phrased so completion is observable",
    "done": "Definition of done -- a number or a command's output",
}

OPTIONAL_FIELDS: Dict[str, str] = {
    "neighbours": "Context/NEIGHBOURS -- who else writes these files now",
    "boundaries": "Context/BOUNDARIES -- what must not be touched, and why",
    "site_rules": "Site rules whose breach costs other people's work",
    "falsifier": "What result would refute the hypothesis",
    "not_reserved": "Actions reserved to the parent, not this worker",
}

# A brief must be a brief, not a shrug. These lengths are the shortest text that
# can carry provenance; anything below is a retelling.
_MIN_LEN = {"role": 12, "known": 20, "task": 20, "done": 10}


def coerce(brief: Any) -> Any:
    """Accept the shapes a model actually emits, without relaxing the contract.

    Small local models often emit the brief's *content* correctly inside the
    wrong *container*: a JSON string, or a fenced JSON block, where the schema
    asks for an object. Measured on qwen2.5:7b-instruct and 14b-instruct, a
    lead handed the cluster-agent-setup skill produced complete, correct RCTF
    content in both shapes.

    So a brief arriving as a JSON string (or fenced in one) is parsed here and
    then validated exactly like any other. Every required field is still
    required; only the wrapper is forgiving.
    """
    if not isinstance(brief, str):
        return brief
    text = brief.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        if text.endswith("```"):
            text = text[: -3]
    text = text.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return brief
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return brief
    return parsed if isinstance(parsed, dict) else brief


def validate(brief: Any, *, min_lengths: bool = True) -> Optional[str]:
    """Return an error string when *brief* is not dispatchable, else ``None``.

    ``min_lengths=False`` checks presence only. Composed briefs use it: the
    length floors exist to catch a *human-shaped* retelling, and applying them
    to a composed brief would refuse a delegation whose ``goal`` is simply
    short -- which is the caller's choice, not a defect, and refusing it stops
    the tree for no gain.
    """
    if not isinstance(brief, dict):
        return (
            "brief must be an object with keys: "
            + ", ".join(REQUIRED_FIELDS)
            + ". See the RCTF contract (role / context / task / format)."
        )

    missing: List[str] = []
    thin: List[str] = []
    for key, label in REQUIRED_FIELDS.items():
        val = brief.get(key)
        text = str(val).strip() if val is not None else ""
        if not text:
            missing.append(f"{key} ({label})")
        elif min_lengths and len(text) < _MIN_LEN.get(key, 0):
            thin.append(f"{key} (got {len(text)} chars, need >= {_MIN_LEN[key]})")

    problems: List[str] = []
    if missing:
        problems.append("missing: " + "; ".join(missing))
    if thin:
        problems.append(
            "too thin to be a brief rather than a retelling: " + "; ".join(thin)
        )
    if not problems:
        return None
    return (
        "Task brief rejected -- "
        + " | ".join(problems)
        + ". A worker sent an incomplete brief returns an unverifiable answer."
    )


def render(brief: Dict[str, Any]) -> str:
    """Render *brief* in the canonical RCTF shape the worker reads."""
    g = lambda k: str(brief.get(k) or "").strip()  # noqa: E731

    out: List[str] = ["ROLE", g("role")]

    if g("not_reserved"):
        out += ["", "RESERVED TO YOUR PARENT (do not attempt, do not wait for it)",
                g("not_reserved")]

    out += ["", "CONTEXT", f"KNOWN: {g('known')}"]
    if g("neighbours"):
        out.append(f"NEIGHBOURS: {g('neighbours')}")
    if g("boundaries"):
        out.append(f"BOUNDARIES: {g('boundaries')}")
    if g("site_rules"):
        out.append(f"SITE RULES: {g('site_rules')}")

    out += ["", "TASK", g("task")]
    if g("falsifier"):
        out += ["", "FALSIFIER", g("falsifier")]

    out += [
        "",
        "DEFINITION OF DONE",
        g("done"),
        "",
        "FORMAT",
        "Situation -> Task -> Action -> Result, at most 40 lines.",
        "Provenance for every number (tree / binary / commit).",
        "Final paragraph: what you did NOT do and why.",
        "Report a negative result or 'could not verify' as a result, "
        "never as silence or failure.",
    ]
    return "\n".join(out)


def schema_property() -> Dict[str, Any]:
    """JSON-schema fragment describing the ``brief`` field on a task."""
    props: Dict[str, Any] = {}
    for key, label in REQUIRED_FIELDS.items():
        props[key] = {"type": "string", "description": label}
    for key, label in OPTIONAL_FIELDS.items():
        props[key] = {"type": "string", "description": label}
    return {
        # A JSON string is accepted as well as an object -- see coerce(). Small
        # models emit the brief's content correctly and its container badly, and
        # a schema that only admits the container loses the brief entirely.
        "type": ["object", "string"],
        "properties": props,
        "required": list(REQUIRED_FIELDS),
        "description": (
            "Structured RCTF brief for this subagent. When present it replaces "
            "free-text context: the subagent reads the rendered brief. A brief "
            "missing a required key is refused and the subagent does not run. "
            "May be sent either as an object or as a JSON string with the same "
            "keys, whichever your tool-calling handles reliably."
        ),
    }


def contract_hint() -> str:
    """One-screen restatement of the contract, for a refusal the model can act on.

    A refusal that only names the rule makes the caller guess at the shape; this
    gives the exact object to send back.
    """
    required = "\n".join(f"  {key}: {label}" for key, label in REQUIRED_FIELDS.items())
    optional = "\n".join(f"  {key}: {label}" for key, label in OPTIONAL_FIELDS.items())
    return (
        "Add a `brief` to the task, alongside `goal`. Send it as an object, "
        "or -- if nested objects are awkward -- as a JSON string with these "
        "keys:\n"
        "REQUIRED\n" + required + "\n"
        "RECOMMENDED (a brief without a falsifier returns confirmation of "
        "whatever was assumed)\n" + optional + "\n"
        "Give measurements with provenance, not links to reports, and phrase "
        "`done` as a number or the output of a command.\n"
        "\nShape to copy and fill (keep `goal` as well):\n"
        + json.dumps(
            {
                "goal": "<one line: what the worker delivers>",
                "brief": {
                    "role": "<what the worker is; what is reserved to you>",
                    "known": "<facts with provenance: paths, numbers, commits>",
                    "task": "<the one cell, phrased so completion is observable>",
                    "done": "<a number, or the exit code of a named command>",
                    "boundaries": "<what must not be touched>",
                    "falsifier": "<what result would refute the assumption>",
                },
            },
            ensure_ascii=False,
            indent=1,
        )
    )


# ---------------------------------------------------------------------------
# Composition: the brief the harness builds, so nobody has to be asked for it
# ---------------------------------------------------------------------------

# Which flat, top-level tool arguments carry brief fields. Flat strings are what
# small models emit reliably; nested objects are what they drop. Only the fields
# the harness genuinely cannot know are offered here -- the rest are composed.
FLAT_FIELDS: Dict[str, str] = {
    "brief_done": "done",
    "brief_falsifier": "falsifier",
    "brief_known": "known",
}

# Actions that stay with the dispatcher no matter who finds the need. Same list
# as the skill's Boundary section; stated to the worker so it neither stalls
# waiting for permission nor takes permission it does not have.
RESERVED_TO_DISPATCHER = (
    "commit, branch, push, tag, merge, release, publication, changes to the "
    "user's own configuration, and any widening of the write boundary below"
)


def collect_flat(source: Dict[str, Any]) -> Dict[str, str]:
    """Pull the flat ``brief_*`` arguments out of *source* into brief fields."""
    out: Dict[str, str] = {}
    for flat, field in FLAT_FIELDS.items():
        val = source.get(flat)
        if val is None:
            continue
        text = str(val).strip()
        if text:
            out[field] = text
    return out


def _compose_role(depth: int, role: str) -> str:
    if role == "orchestrator":
        return (
            f"Lead at depth {depth} of a delegation tree. You split the cell "
            f"below into workers and dispatch them with delegate_task; you do "
            f"not do their work yourself. Each worker you dispatch is issued a "
            f"brief of this same shape, composed the same way."
        )
    return (
        f"Worker at depth {depth} of a delegation tree. You carry out the one "
        f"cell below yourself and report what it produced. You did not choose "
        f"the cell and you do not spawn further agents."
    )


def _compose_known(
    *,
    depth: int,
    role: str,
    task_index: int,
    task_count: int,
    write_roots: Sequence[str],
    shell_confined: bool,
    parent_brief: str,
) -> str:
    lines: List[str] = [
        f"You were dispatched by delegate_task as task {task_index + 1} of "
        f"{task_count} at depth {depth}, role {role}. Your context is fresh: "
        f"nothing in your dispatcher's conversation reached you except this "
        f"brief, and there is nobody to ask for the rest.",
    ]
    if write_roots:
        boundary = ", ".join(write_roots)
        confined = (
            " The shell is held to the same boundary by the kernel, so a "
            "command that writes outside it fails at open()."
            if shell_confined
            else ""
        )
        lines.append(
            f"Write boundary: {boundary}. The file tools refuse any path "
            f"outside it.{confined}"
        )
    if parent_brief:
        excerpt = parent_brief.strip()
        if len(excerpt) > 600:
            excerpt = excerpt[:600].rstrip() + " [...]"
        lines.append(
            "Issued to your dispatcher, so you need not re-derive it "
            f"(excerpt):\n{excerpt}"
        )
    return "\n".join(lines)


def _compose_neighbours(siblings: Sequence[str]) -> str:
    live = [str(s).strip() for s in siblings if str(s or "").strip()]
    if not live:
        return (
            "No sibling worker is running in this fan-out; every file under "
            "your boundary is yours alone for the length of this task."
        )
    listed = "; ".join(f"({i + 1}) {g[:160]}" for i, g in enumerate(live))
    return (
        f"{len(live)} sibling worker(s) run beside you right now, dispatched "
        f"in the same call: {listed}. Their files are not yours to write."
    )


def compose(
    *,
    goal: str,
    depth: int = 1,
    role: str = "leaf",
    task_index: int = 0,
    task_count: int = 1,
    write_roots: Sequence[str] = (),
    shell_confined: bool = False,
    siblings: Sequence[str] = (),
    parent_brief: str = "",
    site_rules: str = "",
    supplied: Optional[Any] = None,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Build a complete brief from the delegation structure plus *supplied*.

    Returns ``(brief, source)``. ``source`` maps each filled field to
    ``"model"``, ``"harness"`` or ``"model+harness"`` -- the ledger records it,
    so "the worker got a brief" and "the lead wrote one" stay separate claims.

    *supplied* is whatever the model sent: a brief object, a JSON string of one
    (see :func:`coerce`), or free prose. Prose is not refused -- refusing stops
    the tree, which is the failure this module was written to end -- it is
    folded into ``known`` as the dispatcher's own words.
    """
    model_fields: Dict[str, str] = {}
    prose = ""
    supplied = coerce(supplied) if supplied is not None else None
    if isinstance(supplied, dict):
        for key in list(REQUIRED_FIELDS) + list(OPTIONAL_FIELDS):
            val = supplied.get(key)
            text = str(val).strip() if val is not None else ""
            if text:
                model_fields[key] = text
    elif isinstance(supplied, str) and supplied.strip():
        prose = supplied.strip()

    brief: Dict[str, str] = {}
    source: Dict[str, str] = {}

    def put(field: str, harness_text: str, *, merge: bool = False) -> None:
        mine = model_fields.get(field, "")
        if mine and not merge:
            brief[field] = mine
            source[field] = "model"
            return
        if mine and merge:
            brief[field] = f"{mine}\n{harness_text}" if harness_text else mine
            source[field] = "model+harness" if harness_text else "model"
            return
        if harness_text:
            brief[field] = harness_text
            source[field] = "harness"

    # Role is merged, not replaced. A lead that writes "leaf" has said
    # something true and useless; the structural half -- depth, whether this
    # child may spawn, that it did not choose its cell -- is what the worker
    # cannot get anywhere else.
    put("role", _compose_role(depth, role), merge=True)
    put("not_reserved", f"Reserved to the agent that dispatched you: "
        f"{RESERVED_TO_DISPATCHER}. If the cell needs one of these, report the "
        f"need and stop -- do not attempt it, and do not wait for permission "
        f"that will not arrive.")

    known_harness = _compose_known(
        depth=depth,
        role=role,
        task_index=task_index,
        task_count=task_count,
        write_roots=write_roots,
        shell_confined=shell_confined,
        parent_brief=parent_brief,
    )
    if prose:
        known_harness = (
            f"In your dispatcher's own words: {prose}\n{known_harness}"
        )
    put("known", known_harness, merge=True)
    put("neighbours", _compose_neighbours(siblings))

    if write_roots:
        put(
            "boundaries",
            f"Write only inside {', '.join(write_roots)}. Everything else on "
            f"this machine belongs to somebody else's work.",
        )
    else:
        put("boundaries", "")

    put("site_rules", str(site_rules or "").strip())

    # The cell. The model already sends `goal` on every call, so this field
    # costs nothing extra and is the one part of the brief that is never
    # invented by the harness. Recorded as "goal" rather than "harness": the
    # words are the dispatcher's, they simply arrived through the argument it
    # already fills reliably.
    if "task" not in model_fields:
        text = str(goal or "").strip()
        if text:
            brief["task"] = text
            source["task"] = "goal"
    else:
        put("task", "")

    # The only content the harness cannot derive. When the dispatcher does not
    # state it, fall back to a definition that is still observable -- the
    # report must name a command and its exit code -- rather than to silence.
    put(
        "done",
        "Your report names the command you ran to check the result and that "
        "command's exit code. A result nobody can re-check is not done.",
    )
    put(
        "falsifier",
        "If the check you name does not pass, report that as the result. A "
        "negative result is a result; silence and 'failed' are not.",
    )
    return brief, source
