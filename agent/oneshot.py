"""Shared one-off LLM requests for non-conversational helpers.

A "one-shot" is a single, stateless model call that runs *outside* any
conversation: it never touches a session's history, never breaks prompt
caching, and returns plain text. UI surfaces use it for small generative
chores — a commit message from a diff, a rename suggestion, a summary —
where spinning up an agent turn would be wrong (it would pollute the thread)
and hand-rolling an LLM call at every call site would be worse.

Two ways to call it:

  * ``run_oneshot(instructions=..., user_input=...)`` — caller supplies the
    full prompt.
  * ``run_oneshot(template="commit_message", variables={...})`` — caller
    names a registered template and passes its variables; the template owns
    the prompt engineering so it stays consistent across CLI/TUI/desktop.

Model selection rides the same auxiliary plumbing as title generation
(:func:`agent.auxiliary_client.call_llm`): pass ``main_runtime`` to inherit
the live session's provider/model, otherwise the configured ``task`` (default
``title_generation``) resolves a cheap/fast backend.
"""

import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from agent.auxiliary_client import call_llm, extract_content_or_reasoning

logger = logging.getLogger(__name__)

# A template turns a variables dict into a (instructions, user_input) pair.
# Templates are plain callables (not str.format) so diff/code payloads with
# literal "{" / "}" pass through untouched.
PromptTemplate = Callable[[Dict[str, Any]], Tuple[str, str]]


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n…(truncated)"


_COMMIT_INSTRUCTIONS = (
    "You write git commit messages. Given a diff of staged changes, write ONE "
    "concise Conventional Commits message describing what the change does and why.\n"
    "Rules:\n"
    "- Subject line: type(scope): summary — imperative mood, lower-case, no "
    "trailing period, ≤ 72 characters. Types: feat, fix, refactor, perf, docs, "
    "test, build, chore, style, ci.\n"
    "- Omit the scope if it isn't obvious.\n"
    "- Add a short body (wrapped at ~72 cols) ONLY when the change needs "
    "explanation; skip it for small/obvious changes.\n"
    "- Describe the actual change, never restate the diff line-by-line.\n"
    "- Return ONLY the commit message text — no quotes, no markdown fences, no "
    "preamble."
)


#: The types the instructions above allow. One list, so the prompt and the
#: check cannot drift into disagreeing about what is legal.
COMMIT_TYPES = frozenset(
    {"feat", "fix", "refactor", "perf", "docs", "test", "build", "chore",
     "style", "ci"}
)

COMMIT_SUBJECT_LIMIT = 72

#: ``type(scope)!: summary`` — scope and the breaking-change bang are optional.
_COMMIT_SUBJECT_RE = re.compile(
    r"\A(?P<type>[a-z]+)(?:\((?P<scope>[^()]+)\))?(?P<bang>!)?:(?P<gap> *)"
    r"(?P<summary>.+)\Z"
)


def validate_commit_message(text: str) -> List[str]:
    """Return the ways ``text`` breaks the Conventional Commits contract.

    Deterministic and offline: same input, same verdict, no model involved. The
    prompt has always *stated* these rules; nothing checked them, so a caller
    could not tell a conforming message from a plausible-looking one.

    An empty list means conforming. The strings are meant to be shown, so each
    names the offending part rather than a rule number.
    """
    lines = (text or "").replace("\r\n", "\n").split("\n")
    subject = lines[0].strip() if lines else ""
    problems: List[str] = []

    if not subject:
        return ["the message is empty"]

    match = _COMMIT_SUBJECT_RE.match(subject)
    if match is None:
        problems.append(f"subject is not 'type(scope): summary': {subject!r}")
    else:
        kind = match.group("type")
        if kind not in COMMIT_TYPES:
            problems.append(
                f"'{kind}' is not one of the allowed types "
                f"({', '.join(sorted(COMMIT_TYPES))})"
            )
        if match.group("gap") != " ":
            problems.append("subject needs exactly one space after the colon")
        summary = match.group("summary")
        if summary.endswith("."):
            problems.append("subject ends with a period")

    if len(subject) > COMMIT_SUBJECT_LIMIT:
        problems.append(
            f"subject is {len(subject)} characters, over the "
            f"{COMMIT_SUBJECT_LIMIT}-character limit"
        )

    if len(lines) > 1 and lines[1].strip():
        problems.append("body must be separated from the subject by a blank line")

    return problems


#: Prefixes a model sometimes emits despite being told not to.
_COMMIT_PREAMBLE_RE = re.compile(
    r"\A(?:commit\s+message|subject|message)\s*:\s*", re.IGNORECASE
)


def normalize_commit_message(text: str) -> str:
    """Repair the violations that can be fixed without guessing at intent.

    Deliberately narrow. Dropping a trailing period, a wrapping quote or a
    "Commit message:" preamble cannot change what the message says. Rewording an
    over-long subject or inventing a missing type could, so those are left for
    :func:`validate_commit_message` to report rather than silently "fixed" here.
    """
    cleaned = _strip_code_fence((text or "").strip())
    lines = cleaned.replace("\r\n", "\n").split("\n")
    subject = lines[0].strip()

    # Peel wrappers until stable: a model can produce both at once
    # ('"Commit message: fix: a thing"'), and stripping in a single fixed order
    # leaves whichever one was on the inside.
    for _ in range(4):
        peeled = _COMMIT_PREAMBLE_RE.sub("", subject).strip()
        # A quote wrapping the whole subject, not an apostrophe or inner quote.
        for quote in ('"', "'", "`"):
            if (len(peeled) >= 2 and peeled.startswith(quote)
                    and peeled.endswith(quote)):
                peeled = peeled[1:-1].strip()
                break
        if peeled == subject:
            break
        subject = peeled

    match = _COMMIT_SUBJECT_RE.match(subject)
    if match is not None:
        head = match.group("type")
        if match.group("scope"):
            head += f"({match.group('scope')})"
        if match.group("bang"):
            head += "!"
        subject = f"{head}: {match.group('summary').strip().rstrip('.')}"

    body = lines[1:]
    while body and not body[0].strip():
        body.pop(0)
    if body:
        return "\n".join([subject, "", *body]).rstrip()
    return subject


def _commit_message_template(variables: Dict[str, Any]) -> Tuple[str, str]:
    diff = _truncate(str(variables.get("diff") or ""), 12000)
    recent = _truncate(str(variables.get("recent_commits") or ""), 1500)

    parts = []
    if recent.strip():
        parts.append(
            "Recent commit subjects from this repo (match their style/conventions):\n"
            f"{recent}"
        )
    parts.append("Diff to describe:\n" + (diff or "(no textual diff available)"))

    # "Regenerate" must yield something new even on models that decode greedily
    # / pin temperature server-side. A trailing nonce isn't enough, so we hand
    # back the previous message and require a genuinely different one.
    avoid = _truncate(str(variables.get("avoid") or "").strip(), 1000)
    if avoid:
        parts.append(
            "You already proposed the message below and the user wants a "
            "different one. Write a NEW message with different wording (and, if "
            "reasonable, a different emphasis or scope framing) — do not repeat "
            f"it:\n{avoid}"
        )

    return _COMMIT_INSTRUCTIONS, "\n\n".join(parts)


# Registry of named templates. Add an entry here to give a new surface a
# consistent, reusable prompt without teaching every caller the prompt text.
PROMPT_TEMPLATES: Dict[str, PromptTemplate] = {
    "commit_message": _commit_message_template,
}


def render_template(name: str, variables: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """Resolve a registered template into (instructions, user_input).

    Raises KeyError if the template name is unknown so callers fail loudly
    instead of silently sending an empty prompt.
    """
    template = PROMPT_TEMPLATES.get(name)
    if template is None:
        raise KeyError(f"unknown one-shot template: {name}")
    return template(variables or {})


def run_oneshot(
    *,
    instructions: str = "",
    user_input: str = "",
    template: Optional[str] = None,
    variables: Optional[Dict[str, Any]] = None,
    task: str = "title_generation",
    max_tokens: int = 1024,
    temperature: Optional[float] = 0.3,
    timeout: float = 60.0,
    main_runtime: Optional[Dict[str, Any]] = None,
) -> str:
    """Run a single stateless LLM request and return its text.

    Provide either a registered ``template`` (+ ``variables``) or an explicit
    ``instructions`` / ``user_input`` pair. Returns the model's text answer,
    stripped of surrounding whitespace and any wrapping code fence.

    Raises RuntimeError when no LLM provider is configured (surfaced from
    :func:`call_llm`) and KeyError for an unknown template name.
    """
    if template:
        instructions, user_input = render_template(template, variables)

    if not (instructions or "").strip() and not (user_input or "").strip():
        raise ValueError("run_oneshot requires a template or instructions/user_input")

    messages = []
    if (instructions or "").strip():
        messages.append({"role": "system", "content": instructions})
    messages.append({"role": "user", "content": user_input or ""})

    response = call_llm(
        task=task,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
        main_runtime=main_runtime,
    )

    text = _strip_code_fence((extract_content_or_reasoning(response) or "").strip())

    # Deterministic post-processing per template. The commit-message prompt
    # states its format rules, and nothing used to enforce them: a stray
    # trailing period or a "Commit message:" preamble reached the user's ship
    # bar as-is. Repairs are the ones that cannot change meaning; anything else
    # is only reported.
    if template == "commit_message" and text:
        text = normalize_commit_message(text)
        problems = validate_commit_message(text)
        if problems:
            logger.info(
                "one-shot commit message does not conform: %s", "; ".join(problems)
            )
    return text


def _strip_code_fence(text: str) -> str:
    """Drop a single wrapping ``` fence the model may have added."""
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text
