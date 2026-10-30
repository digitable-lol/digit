"""Full MCP elicitation for the client side: form mode and URL mode.

Spec: https://modelcontextprotocol.io/specification/draft/client/elicitation

The MCP SDK declares ``elicitation: {form, url}`` as soon as a client passes an
``elicitation_callback`` (``mcp.client.session.ClientSession.initialize``), so a
client that only *approves* a request and answers ``accept`` with an empty
``content`` breaks the contract twice: the server's ``requestedSchema`` is never
filled, and URL mode is advertised but declined. This module implements both
modes on top of the one question surface every Digit front end already has --
the clarify callback ``callback(question, choices, multi_select=False) -> str``
(CLI panel, TUI, Telegram / Slack / Discord via ``tools.clarify_gateway``).

Everything here is synchronous and pure apart from the ``ask`` callable that is
passed in, so the schema rules are unit-testable without a terminal or a server.

Spec rules implemented (section names from the draft):

* *Requested Schema* -- flat object, primitive properties only: string
  (``minLength``, ``maxLength``, ``format`` email/uri/date/date-time), number /
  integer (``minimum``, ``maximum``), boolean, single-select enum (``enum`` or
  ``oneOf`` of ``{const, title}``), multi-select enum (``type: array`` with
  ``items.enum`` or ``items.anyOf`` of ``{const, title}``, ``minItems``,
  ``maxItems``); ``default`` on every type. Anything else is not representable
  and the request is declined rather than half-answered.
* *User Interaction Model* -- the UI names the requesting server, lets the user
  review and modify the answers before sending, and offers decline and cancel.
* *Form Mode Security* -- the collected content is validated against the schema
  before it is returned.
* *Response Actions* -- ``accept`` (with content), ``decline`` (explicit no),
  ``cancel`` (dismissed: timeout, closed prompt).
* *Safe URL Handling* -- the full URL is shown with its host called out,
  Punycode hosts are flagged, nothing is fetched, and the URL is opened only
  after explicit consent.
"""

from __future__ import annotations

import contextvars
import datetime as _dt
import webbrowser
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

#: ``ask(question, choices, multi_select) -> str | list[str]`` -- the clarify
#: callback contract. ``choices`` empty means free text.
Ask = Callable[..., Any]

#: Answer every Digit surface returns when the user did not respond in time
#: (``cli.py`` clarify callback, ``digit_cli/callbacks.py``).
CLARIFY_TIMEOUT_PREFIX = "The user did not provide a response within the time limit."

#: Choices a clarify surface shows besides the always-present "Other (type your
#: answer)" row (``tools.clarify_tool.MAX_CHOICES``). Longer enums are paged.
CHOICES_PER_PAGE = 4

STRING_FORMATS = ("email", "uri", "date", "date-time")

SEND, EDIT, DECLINE, CANCEL = "Send", "Edit an answer", "Decline", "Cancel"
OPEN_URL = "Open in browser"
MORE = "More options…"
CANCEL_WORDS = ("cancel", "отмена")
SKIP_WORDS = ("skip", "пропустить")
YES_WORDS = ("yes", "да", "true")
NO_WORDS = ("no", "нет", "false")


class SchemaNotSupported(ValueError):
    """``requestedSchema`` uses JSON Schema the spec does not allow in form mode."""


class Dismissed(Exception):
    """The user dismissed the form (timeout, closed prompt, typed cancel)."""


@dataclass(frozen=True)
class Option:
    value: str
    title: str


@dataclass(frozen=True)
class FormField:
    name: str
    kind: str  # string | number | integer | boolean | enum | multi_enum
    title: str
    description: str = ""
    required: bool = False
    default: Any = None
    options: tuple[Option, ...] = ()
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    format: Optional[str] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    min_items: Optional[int] = None
    max_items: Optional[int] = None


@dataclass(frozen=True)
class Outcome:
    action: str  # accept | decline | cancel
    content: Optional[dict] = None


# ---------------------------------------------------------------------------
# Surface: where the questions go
# ---------------------------------------------------------------------------

#: The question surface of the agent turn that issued the MCP tool call. Set in
#: the turn prologue (``agent.turn_context``); the MCP tool wrapper snapshots
#: the agent's context before the RPC, and the elicitation handler replays that
#: snapshot, so the request reaches the session that caused it.
#: A turn that has no question surface (cron, background agents) sets ``None``
#: explicitly; only a context that never set it falls back to the process one.
_UNSET: Any = object()
current_surface: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "mcp_elicitation_surface", default=_UNSET
)

_process_surface: Optional[Ask] = None


def set_process_surface(ask: Optional[Ask]) -> None:
    """Register a process-wide fallback surface (the single-user CLI)."""
    global _process_surface
    _process_surface = ask


def resolve_surface() -> Optional[Ask]:
    """The surface of the current context; the process fallback only when the context never set one."""
    surface = current_surface.get()
    return _process_surface if surface is _UNSET else surface


# ---------------------------------------------------------------------------
# Requested schema
# ---------------------------------------------------------------------------


def _number(value: Any) -> Optional[float]:
    return (
        float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else None
    )


def _whole(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _options(spec: Any, where: str) -> Optional[tuple[Option, ...]]:
    """Enum options with titles: ``enum`` (+ legacy ``enumNames``) or ``oneOf``/``anyOf`` of ``{const, title}``."""
    if not isinstance(spec, dict):
        return None
    if "enum" in spec:
        values = spec["enum"]
        names = spec.get("enumNames")
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(v, str) for v in values)
        ):
            raise SchemaNotSupported(
                f"{where}: enum must be a non-empty list of strings"
            )
        titles = (
            names if isinstance(names, list) and len(names) == len(values) else values
        )
        return tuple(Option(v, str(t)) for v, t in zip(values, titles))
    for key in ("oneOf", "anyOf"):
        if key in spec:
            items = spec[key]
            if not isinstance(items, list) or not items:
                raise SchemaNotSupported(f"{where}: {key} must be a non-empty list")
            options = []
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("const"), str):
                    raise SchemaNotSupported(
                        f"{where}: every {key} entry needs a string const"
                    )
                options.append(
                    Option(item["const"], str(item.get("title") or item["const"]))
                )
            return tuple(options)
    return None


def _field(name: str, spec: Any, required: bool) -> FormField:
    if not isinstance(spec, dict):
        raise SchemaNotSupported(f"property {name!r} is not an object")
    kind = spec.get("type")
    title = str(spec.get("title") or name)
    description = str(spec.get("description") or "")
    default = spec.get("default")
    if kind == "array":
        options = _options(spec.get("items"), f"property {name!r} items")
        if not options:
            raise SchemaNotSupported(
                f"property {name!r}: arrays are allowed only as multi-select enums"
            )
        return FormField(
            name=name,
            kind="multi_enum",
            title=title,
            description=description,
            required=required,
            default=default,
            options=options,
            min_items=_whole(spec.get("minItems")),
            max_items=_whole(spec.get("maxItems")),
        )
    if kind == "string":
        options = _options(spec, f"property {name!r}")
        if options:
            return FormField(
                name=name,
                kind="enum",
                title=title,
                description=description,
                required=required,
                default=default,
                options=options,
            )
        fmt = spec.get("format")
        if fmt is not None and fmt not in STRING_FORMATS:
            raise SchemaNotSupported(
                f"property {name!r}: format {fmt!r} is not one of {', '.join(STRING_FORMATS)}"
            )
        return FormField(
            name=name,
            kind="string",
            title=title,
            description=description,
            required=required,
            default=default,
            format=fmt,
            min_length=_whole(spec.get("minLength")),
            max_length=_whole(spec.get("maxLength")),
        )
    if kind in ("number", "integer"):
        return FormField(
            name=name,
            kind=kind,
            title=title,
            description=description,
            required=required,
            default=default,
            minimum=_number(spec.get("minimum")),
            maximum=_number(spec.get("maximum")),
        )
    if kind == "boolean":
        return FormField(
            name=name,
            kind="boolean",
            title=title,
            description=description,
            required=required,
            default=default,
        )
    raise SchemaNotSupported(
        f"property {name!r}: type {kind!r} is not a primitive form type"
    )


def parse_requested_schema(schema: Any) -> list[FormField]:
    """Fields of a form-mode ``requestedSchema``; ``SchemaNotSupported`` outside the spec subset."""
    if not isinstance(schema, dict):
        raise SchemaNotSupported("requestedSchema is not an object")
    if schema.get("type", "object") != "object":
        raise SchemaNotSupported("requestedSchema must have type object")
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        raise SchemaNotSupported("requestedSchema.properties is not an object")
    required = schema.get("required") or []
    if not isinstance(required, list):
        raise SchemaNotSupported("requestedSchema.required is not a list")
    unknown = [r for r in required if r not in properties]
    if unknown:
        raise SchemaNotSupported(
            f"required names properties that do not exist: {', '.join(map(str, unknown))}"
        )
    return [_field(name, spec, name in required) for name, spec in properties.items()]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _format_error(fmt: str, value: str) -> Optional[str]:
    if fmt == "email":
        local, at, domain = value.partition("@")
        good = (
            at
            and local
            and "." in domain
            and not domain.startswith(".")
            and not domain.endswith(".")
        )
        return (
            None
            if good and " " not in value and "@" not in domain
            else "not an email address"
        )
    if fmt == "uri":
        parts = urlsplit(value)
        return (
            None
            if parts.scheme and (parts.netloc or parts.path)
            else "not a URI (scheme required)"
        )
    if fmt == "date":
        try:
            _dt.date.fromisoformat(value)
            return None
        except ValueError:
            return "not a date (YYYY-MM-DD)"
    if fmt == "date-time":
        try:
            _dt.datetime.fromisoformat(value)
            return None
        except ValueError:
            return "not a date-time (ISO 8601)"
    return None


def check_value(field: FormField, value: Any) -> Optional[str]:
    """Why ``value`` does not satisfy ``field``, or None when it does."""
    if field.kind == "string":
        if not isinstance(value, str):
            return "expected text"
        if field.min_length is not None and len(value) < field.min_length:
            return f"at least {field.min_length} characters"
        if field.max_length is not None and len(value) > field.max_length:
            return f"at most {field.max_length} characters"
        return _format_error(field.format, value) if field.format else None
    if field.kind in ("number", "integer"):
        if field.kind == "integer" and _whole(value) is None:
            return "expected a whole number"
        number = _number(value)
        if number is None:
            return "expected a number"
        if field.minimum is not None and number < field.minimum:
            return f"at least {field.minimum:g}"
        if field.maximum is not None and number > field.maximum:
            return f"at most {field.maximum:g}"
        return None
    if field.kind == "boolean":
        return None if isinstance(value, bool) else "expected yes or no"
    allowed = {option.value for option in field.options}
    if field.kind == "enum":
        return None if value in allowed else "not one of the offered options"
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return "expected a list of options"
    if any(v not in allowed for v in value) or len(set(value)) != len(value):
        return "only offered options, each once"
    if field.min_items is not None and len(value) < field.min_items:
        return f"choose at least {field.min_items}"
    if field.max_items is not None and len(value) > field.max_items:
        return f"choose at most {field.max_items}"
    return None


def validate_content(fields: list[FormField], content: dict) -> list[str]:
    """Every way ``content`` breaks the schema (empty list: valid)."""
    known = {field.name: field for field in fields}
    problems = [f"{name}: not in the schema" for name in content if name not in known]
    for field in fields:
        if field.name not in content:
            if field.required:
                problems.append(f"{field.title}: required")
            continue
        why = check_value(field, content[field.name])
        if why:
            problems.append(f"{field.title}: {why}")
    return problems


# ---------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------


def _ask(
    ask: Ask, question: str, choices: list[str], multi_select: bool = False
) -> Any:
    answer = ask(question, choices, multi_select=multi_select)
    if isinstance(answer, str):
        text = answer.strip()
        if text.startswith(CLARIFY_TIMEOUT_PREFIX) or text.lower() in CANCEL_WORDS:
            raise Dismissed()
        return text
    if answer is None:
        raise Dismissed()
    return answer


def _shown(value: Any, field: FormField) -> str:
    titles = {option.value: option.title for option in field.options}
    if isinstance(value, list):
        return ", ".join(titles.get(v, v) for v in value) or "(none)"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return titles.get(value, str(value)) if value is not None else "(empty)"


def _field_question(header: str, field: FormField, problem: Optional[str]) -> str:
    lines = [
        header,
        "",
        f"{field.title}{' (required)' if field.required else ' (optional)'}",
    ]
    if field.description:
        lines.append(field.description)
    limits = [
        f"{label} {value:g}" if isinstance(value, float) else f"{label} {value}"
        for label, value in (
            ("min length", field.min_length),
            ("max length", field.max_length),
            ("format", field.format),
            ("min", field.minimum),
            ("max", field.maximum),
            ("choose at least", field.min_items),
            ("choose at most", field.max_items),
        )
        if value is not None
    ]
    if limits:
        lines.append("Constraints: " + ", ".join(limits))
    if field.default is not None:
        lines.append(f"Default: {_shown(field.default, field)}")
    hints = ["type `cancel` to stop"]
    if not field.required:
        hints.insert(0, "type `skip` to leave it empty")
    lines.append("(" + "; ".join(hints) + ")")
    if problem:
        lines.append(f"⚠ {problem}")
    return "\n".join(lines)


_EMPTY = object()


def _pick(ask: Ask, question: str, field: FormField) -> Any:
    """One option (enum) or several (multi_enum), paging when there are more than fit."""
    pages = [
        field.options[i : i + CHOICES_PER_PAGE - 1]
        for i in range(0, len(field.options), CHOICES_PER_PAGE - 1)
    ]
    if len(field.options) <= CHOICES_PER_PAGE:
        pages = [field.options]
    multi = field.kind == "multi_enum"
    page = 0
    while True:
        shown = list(pages[page])
        choices = [option.title for option in shown] + (
            [MORE] if len(pages) > 1 and not multi else []
        )
        if multi:
            choices = [option.title for option in field.options]
        answer = _ask(ask, question, choices, multi_select=multi)
        if multi:
            from tools.clarify_tool import _parse_multi_select_response

            picked = _parse_multi_select_response(answer)
            if any(p.lower() in SKIP_WORDS for p in picked):
                return _EMPTY
            return [_match_option(field, p) for p in picked]
        if answer == MORE:
            page = (page + 1) % len(pages)
            continue
        if answer.lower() in SKIP_WORDS:
            return _EMPTY
        return _match_option(field, answer)


def _match_option(field: FormField, text: str) -> str:
    for option in field.options:
        if text in (option.title, option.value) or text.casefold() in (
            option.title.casefold(),
            option.value.casefold(),
        ):
            return option.value
    return text  # rejected by check_value, the field is asked again


def _typed(field: FormField, text: str) -> Any:
    if field.kind == "integer":
        try:
            return int(text)
        except ValueError:
            return text
    if field.kind == "number":
        try:
            return int(text) if text.lstrip("-").isdigit() else float(text)
        except ValueError:
            return text
    return text


def ask_field(ask: Ask, header: str, field: FormField) -> Any:
    """Ask one field until the answer satisfies the schema; ``_EMPTY`` when skipped."""
    problem = None
    while True:
        question = _field_question(header, field, problem)
        if field.kind in ("enum", "multi_enum"):
            value = _pick(ask, question, field)
        elif field.kind == "boolean":
            answer = _ask(ask, question, ["Yes", "No"]).lower()
            if answer in SKIP_WORDS:
                value = _EMPTY
            elif answer in YES_WORDS or answer in NO_WORDS:
                value = answer in YES_WORDS
            else:
                problem = "answer yes or no"
                continue
        else:
            choices = (
                [_shown(field.default, field)] if field.default is not None else []
            )
            answer = _ask(ask, question, choices)
            value = (
                _EMPTY
                if answer.lower() in SKIP_WORDS
                else (
                    field.default
                    if choices and answer == choices[0]
                    else _typed(field, answer)
                )
            )
        if value is _EMPTY:
            if field.required:
                problem = "this answer is required"
                continue
            return _EMPTY
        problem = check_value(field, value)
        if problem is None:
            return value


def collect_form(ask: Ask, server: str, message: str, schema: Any) -> Outcome:
    """Fill ``requestedSchema`` with the user: field by field, then review before sending."""
    fields = parse_requested_schema(schema)
    header = f"MCP server «{server}» asks:\n{message}"
    content: dict = {}
    try:
        for field in fields:
            value = ask_field(ask, header, field)
            if value is not _EMPTY:
                content[field.name] = value
        while True:
            summary = [
                f"• {f.title}: {_shown(content.get(f.name), f)}" for f in fields
            ] or ["(nothing to fill in)"]
            problems = validate_content(fields, content)
            review = [
                header,
                "",
                "Review before sending:",
                *summary,
                *(f"⚠ {p}" for p in problems),
            ]
            choice = _ask(
                ask,
                "\n".join(review),
                [SEND, EDIT, DECLINE, CANCEL] if fields else [SEND, DECLINE, CANCEL],
            )
            if choice == SEND:
                if not problems:
                    return Outcome("accept", content)
                continue
            if choice == DECLINE:
                return Outcome("decline")
            if choice == CANCEL:
                return Outcome("cancel")
            if choice == EDIT and fields:
                chosen = _pick(
                    ask,
                    header + "\n\nWhich answer to change?",
                    FormField(
                        name="",
                        kind="enum",
                        title="answer",
                        options=tuple(Option(f.name, f.title) for f in fields),
                    ),
                )
                for field in fields:
                    if field.name == chosen:
                        value = ask_field(ask, header, field)
                        content.pop(field.name, None)
                        if value is not _EMPTY:
                            content[field.name] = value
    except Dismissed:
        return Outcome("cancel")


def collect_url(
    ask: Ask,
    server: str,
    message: str,
    url: str,
    opener: Optional[Callable[[str], Any]] = None,
) -> Outcome:
    """URL mode: show the full URL and its host, open it only with explicit consent."""
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        return Outcome("decline")
    host = parts.hostname
    lines = [
        f"MCP server «{server}» asks you to open a link:",
        message,
        "",
        f"Host: {host}",
        f"Full URL: {url}",
    ]
    if any(label.startswith("xn--") for label in host.split(".")):
        lines.append("⚠ The host is Punycode (xn--): check it is the site you expect.")
    if parts.scheme == "http":
        lines.append("⚠ Plain http: the connection is not encrypted.")
    try:
        choice = _ask(ask, "\n".join(lines), [OPEN_URL, DECLINE, CANCEL])
    except Dismissed:
        return Outcome("cancel")
    if choice == OPEN_URL:
        # Looked up at call time, not bound as a default: the environment
        # (BROWSER, a test double) may change after import.
        (opener or webbrowser.open)(url)
        return Outcome("accept")
    return Outcome("decline" if choice == DECLINE else "cancel")
