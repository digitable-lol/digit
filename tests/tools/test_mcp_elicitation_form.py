"""Full MCP elicitation (form + URL mode) per the draft spec.

https://modelcontextprotocol.io/specification/draft/client/elicitation

The ``ask`` surface is scripted: each test lists exactly what the user answers,
so every rule (schema subset, validation, review, decline/cancel, URL consent)
is checked without a terminal or a live server.
"""

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp.types")

from tools import mcp_elicitation_form as form  # noqa: E402
from tools.mcp_elicitation_form import (  # noqa: E402
    CLARIFY_TIMEOUT_PREFIX,
    FormField,
    Option,
    SchemaNotSupported,
    check_value,
    collect_form,
    collect_url,
    parse_requested_schema,
    validate_content,
)
from tools.mcp_tool import ElicitationHandler  # noqa: E402


class Script:
    """A user who answers from a list and records every question shown."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.questions = []

    def __call__(self, question, choices, multi_select=False):
        self.questions.append((question, list(choices), multi_select))
        assert self.answers, f"unexpected question: {question!r}"
        return self.answers.pop(0)


@pytest.fixture(autouse=True)
def _no_process_surface():
    # Other suites run the turn prologue, which sets the surface in the
    # main thread's context; start every test from "never set".
    token = form.current_surface.set(form._UNSET)
    form.set_process_surface(None)
    yield
    form.set_process_surface(None)
    form.current_surface.reset(token)


SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "title": "Name", "minLength": 2},
        "email": {"type": "string", "format": "email"},
        "age": {"type": "integer", "minimum": 18},
        "color": {
            "type": "string",
            "oneOf": [
                {"const": "#f00", "title": "Red"},
                {"const": "#0f0", "title": "Green"},
            ],
        },
        "tags": {
            "type": "array",
            "items": {
                "anyOf": [{"const": "a", "title": "A"}, {"const": "b", "title": "B"}]
            },
            "maxItems": 2,
        },
        "subscribe": {"type": "boolean", "default": False},
    },
    "required": ["name", "email", "age"],
}


class TestSchema:
    def test_every_primitive_kind_parses(self):
        kinds = {f.name: f.kind for f in parse_requested_schema(SCHEMA)}
        assert kinds == {
            "name": "string",
            "email": "string",
            "age": "integer",
            "color": "enum",
            "tags": "multi_enum",
            "subscribe": "boolean",
        }

    def test_titled_and_legacy_enum_titles(self):
        fields = parse_requested_schema({
            "properties": {
                "a": {"type": "string", "enum": ["x", "y"], "enumNames": ["Ex", "Why"]},
                "b": {"type": "string", "enum": ["p"]},
            }
        })
        assert fields[0].options == (Option("x", "Ex"), Option("y", "Why"))
        assert fields[1].options == (Option("p", "p"),)

    @pytest.mark.parametrize(
        "bad",
        [
            {"properties": {"o": {"type": "object"}}},
            {"properties": {"l": {"type": "array", "items": {"type": "string"}}}},
            {"properties": {"s": {"type": "string", "format": "ipv4"}}},
            {"properties": {}, "required": ["ghost"]},
            {"type": "array"},
        ],
    )
    def test_outside_the_subset_is_refused(self, bad):
        with pytest.raises(SchemaNotSupported):
            parse_requested_schema(bad)


class TestValidation:
    @pytest.mark.parametrize(
        "fmt,good,bad",
        [
            ("email", "octo@github.com", "octo@github"),
            ("uri", "https://example.com/x", "example.com"),
            ("date", "2026-10-30", "30.10.2026"),
            ("date-time", "2026-10-30T15:00:00+03:00", "вчера"),
        ],
    )
    def test_string_formats(self, fmt, good, bad):
        field = FormField(name="f", kind="string", title="f", format=fmt)
        assert check_value(field, good) is None
        assert check_value(field, bad)

    def test_integer_is_not_a_float_and_bounds_hold(self):
        field = FormField(name="n", kind="integer", title="n", minimum=1, maximum=3)
        assert check_value(field, 2) is None
        assert check_value(field, 2.5)
        assert check_value(field, True)
        assert check_value(field, 4)

    def test_multi_enum_rules(self):
        field = parse_requested_schema(SCHEMA)[4]
        assert check_value(field, ["a"]) is None
        assert check_value(field, ["a", "a"])
        assert check_value(field, ["c"])

    def test_content_against_schema(self):
        fields = parse_requested_schema(SCHEMA)
        assert (
            validate_content(fields, {"name": "Al", "email": "a@b.co", "age": 20}) == []
        )
        problems = validate_content(fields, {"name": "A", "extra": 1})
        assert any("Name" in p for p in problems) and any(
            "extra" in p for p in problems
        )
        assert any("required" in p for p in problems)


class TestCollectForm:
    def test_fills_reviews_and_sends(self):
        user = Script("Ann", "ann@example.com", "30", "Green", ["A"], "Yes", "Send")
        outcome = collect_form(user, "srv", "please fill", SCHEMA)
        assert outcome.action == "accept"
        assert outcome.content == {
            "name": "Ann",
            "email": "ann@example.com",
            "age": 30,
            "color": "#0f0",
            "tags": ["a"],
            "subscribe": True,
        }
        assert all("srv" in question for question, _, _ in user.questions), (
            "the requesting server is always named"
        )
        assert "Review before sending" in user.questions[-1][0]

    def test_invalid_answer_is_asked_again_with_the_reason(self):
        user = Script("A", "Ann", "skip", "Send")
        schema = {
            "properties": {
                "name": {"type": "string", "minLength": 2},
                "nick": {"type": "string"},
            },
            "required": ["name"],
        }
        outcome = collect_form(user, "srv", "m", schema)
        assert outcome.content == {"name": "Ann"}
        assert "at least 2" in user.questions[1][0]

    def test_required_cannot_be_skipped(self):
        user = Script("skip", "Bo", "Send")
        outcome = collect_form(
            user,
            "srv",
            "m",
            {"properties": {"name": {"type": "string"}}, "required": ["name"]},
        )
        assert outcome.content == {"name": "Bo"}
        assert "required" in user.questions[1][0]

    def test_default_is_offered_as_a_choice(self):
        user = Script("10", "Send")
        user.answers[0] = "10"
        outcome = collect_form(
            user, "srv", "m", {"properties": {"n": {"type": "number", "default": 10}}}
        )
        assert user.questions[0][1] == ["10"]
        assert outcome.content == {"n": 10}

    def test_long_enum_is_paged(self):
        options = [{"const": str(i), "title": f"Option {i}"} for i in range(7)]
        user = Script(form.MORE, "Option 4", "Send")
        outcome = collect_form(
            user,
            "srv",
            "m",
            {"properties": {"o": {"type": "string", "oneOf": options}}},
        )
        assert outcome.content == {"o": "4"}
        assert all(
            len(choices) <= form.CHOICES_PER_PAGE for _, choices, _ in user.questions
        )

    def test_review_can_edit_an_answer(self):
        user = Script("Ann", form.EDIT, "name", "Bea", "Send")
        outcome = collect_form(
            user, "srv", "m", {"properties": {"name": {"type": "string"}}}
        )
        assert outcome.content == {"name": "Bea"}

    def test_decline_and_cancel_are_distinct(self):
        assert (
            collect_form(
                Script("Ann", "Decline"),
                "s",
                "m",
                {"properties": {"n": {"type": "string"}}},
            ).action
            == "decline"
        )
        assert (
            collect_form(
                Script("Ann", "Cancel"),
                "s",
                "m",
                {"properties": {"n": {"type": "string"}}},
            ).action
            == "cancel"
        )
        assert (
            collect_form(
                Script("cancel"), "s", "m", {"properties": {"n": {"type": "string"}}}
            ).action
            == "cancel"
        )

    def test_surface_timeout_is_a_cancel(self):
        user = Script(CLARIFY_TIMEOUT_PREFIX + " Use your best judgement.")
        assert (
            collect_form(
                user, "s", "m", {"properties": {"n": {"type": "string"}}}
            ).action
            == "cancel"
        )


class TestCollectUrl:
    def test_opens_only_after_consent(self):
        opened = []
        user = Script(form.OPEN_URL)
        outcome = collect_url(
            user,
            "srv",
            "connect",
            "https://mcp.example.com/connect?x=1",
            opener=opened.append,
        )
        assert outcome.action == "accept" and outcome.content is None
        assert opened == ["https://mcp.example.com/connect?x=1"]
        question = user.questions[0][0]
        assert (
            "Host: mcp.example.com" in question
            and "https://mcp.example.com/connect?x=1" in question
        )

    def test_decline_does_not_open(self):
        opened = []
        assert (
            collect_url(
                Script("Decline"), "s", "m", "https://a.example", opener=opened.append
            ).action
            == "decline"
        )
        assert opened == []

    def test_punycode_host_is_flagged(self):
        user = Script("Decline")
        collect_url(
            user, "s", "m", "https://xn--80ak6aa92e.com/", opener=lambda _url: None
        )
        assert "Punycode" in user.questions[0][0]

    def test_non_web_scheme_is_declined_without_asking(self):
        assert (
            collect_url(
                Script(), "s", "m", "javascript:alert(1)", opener=lambda _url: None
            ).action
            == "decline"
        )


class TestSurfaceResolution:
    def test_unset_context_falls_back_to_the_process_surface(self):
        process = Script()
        form.set_process_surface(process)
        assert form.resolve_surface() is process

    def test_turn_without_surface_does_not_borrow_the_process_one(self):
        form.set_process_surface(Script())
        token = form.current_surface.set(None)
        try:
            assert form.resolve_surface() is None
        finally:
            form.current_surface.reset(token)


class TestHandler:
    def _form(self, schema, message="fill"):
        return SimpleNamespace(mode="form", message=message, requested_schema=schema)

    def test_form_content_comes_back_through_the_handler(self):
        form.set_process_surface(Script("2", "Send"))
        params = self._form({
            "properties": {
                "choice": {
                    "type": "string",
                    "oneOf": [
                        {"const": "1", "title": "one"},
                        {"const": "2", "title": "two"},
                    ],
                }
            },
            "required": ["choice"],
        })
        result = asyncio.run(
            ElicitationHandler("srv", {"timeout": 5})(context=None, params=params)
        )
        assert result.action == "accept" and result.content == {"choice": "2"}

    def test_unsupported_schema_is_declined(self):
        form.set_process_surface(Script())
        result = asyncio.run(
            ElicitationHandler("srv", {"timeout": 5})(
                context=None,
                params=self._form({"properties": {"o": {"type": "object"}}}),
            )
        )
        assert result.action == "decline"

    def test_url_mode_with_a_surface(self, monkeypatch):
        opened = []
        monkeypatch.setattr(form.webbrowser, "open", opened.append)
        form.set_process_surface(Script(form.OPEN_URL))
        params = SimpleNamespace(
            mode="url",
            message="auth",
            url="https://mcp.example.com/connect",
            elicitation_id="e1",
        )
        result = asyncio.run(
            ElicitationHandler("srv", {"timeout": 5})(context=None, params=params)
        )
        assert result.action == "accept" and opened == [
            "https://mcp.example.com/connect"
        ]

    def test_real_sdk_form_params_reach_the_form(self):
        """mcp.types names the schema ``requestedSchema``; reading only the
        snake_case name left every real form empty."""
        from mcp.types import ElicitRequestFormParams

        form.set_process_surface(Script("Ann", "Send"))
        params = ElicitRequestFormParams(
            message="who?",
            requestedSchema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        )
        result = asyncio.run(
            ElicitationHandler("srv", {"timeout": 5})(context=None, params=params)
        )
        assert result.action == "accept" and result.content == {"name": "Ann"}

    def test_real_sdk_url_params(self, monkeypatch):
        from mcp.types import ElicitRequestURLParams

        opened = []
        monkeypatch.setattr(form.webbrowser, "open", opened.append)
        form.set_process_surface(Script(form.OPEN_URL))
        params = ElicitRequestURLParams(
            message="auth", url="https://mcp.example.com/connect", elicitationId="e1"
        )
        result = asyncio.run(
            ElicitationHandler("srv", {"timeout": 5})(context=None, params=params)
        )
        assert (
            result.action == "accept"
            and result.content is None
            and opened == ["https://mcp.example.com/connect"]
        )

    def test_fields_without_a_surface_are_declined_not_faked(self, monkeypatch):
        monkeypatch.setattr(
            "tools.approval.request_elicitation_consent",
            lambda *a, **k: pytest.fail("must not fake accept"),
        )
        result = asyncio.run(
            ElicitationHandler("srv", {"timeout": 5})(
                context=None,
                params=self._form({"properties": {"x": {"type": "string"}}}),
            )
        )
        assert result.action == "decline"
