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
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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


def validate(brief: Any) -> Optional[str]:
    """Return an error string when *brief* is not dispatchable, else ``None``."""
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
        elif len(text) < _MIN_LEN.get(key, 0):
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
        "type": "object",
        "properties": props,
        "required": list(REQUIRED_FIELDS),
        "description": (
            "Structured RCTF brief for this subagent. When present it replaces "
            "free-text context: the subagent reads the rendered brief. A brief "
            "missing a required key is refused and the subagent does not run."
        ),
    }
