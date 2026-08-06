"""The deterministic half of commit-message generation.

The prompt has always stated the Conventional Commits rules. Nothing checked
them, so "the model was told to" was the only assurance a caller had. These
tests cover the check and the narrow set of repairs that cannot change what a
message says.
"""

from __future__ import annotations

import pytest

from agent.oneshot import (
    COMMIT_SUBJECT_LIMIT,
    COMMIT_TYPES,
    _COMMIT_INSTRUCTIONS,
    normalize_commit_message,
    validate_commit_message,
)


# --------------------------------------------------------------------------
# The check agrees with the prompt
# --------------------------------------------------------------------------


def test_every_type_the_prompt_offers_is_accepted_by_the_check():
    """The failure this prevents: the prompt inviting a type the check rejects.

    Both sides are read from the same module, so a type added to one without the
    other is a contradiction the generator would hit at runtime.
    """
    for kind in COMMIT_TYPES:
        assert kind in _COMMIT_INSTRUCTIONS, (
            f"'{kind}' is accepted by the check but never offered to the model"
        )
        assert validate_commit_message(f"{kind}: do the thing") == []


def test_the_prompt_and_the_check_agree_on_the_subject_limit():
    assert str(COMMIT_SUBJECT_LIMIT) in _COMMIT_INSTRUCTIONS


# --------------------------------------------------------------------------
# Accepting what is valid
# --------------------------------------------------------------------------


@pytest.mark.parametrize("message", [
    "fix: stop renumbering task references",
    "feat(speech): show the sentence being read",
    "refactor!: drop the hermes launchers",
    "perf(desktop)!: measure bands per frame",
    "docs: record where synthesis lives\n\nA body, after one blank line.\n"
    "Wrapped at about seventy-two columns so it reads in a terminal.",
])
def test_conforming_messages_pass(message):
    assert validate_commit_message(message) == []


# --------------------------------------------------------------------------
# Rejecting what is not
# --------------------------------------------------------------------------


def test_an_unknown_type_is_named_in_the_complaint():
    problems = validate_commit_message("wip: half a thing")
    assert any("'wip' is not one of the allowed types" in p for p in problems)


def test_a_missing_type_is_reported_as_a_shape_problem():
    problems = validate_commit_message("stop renumbering task references")
    assert any("not 'type(scope): summary'" in p for p in problems)


def test_an_over_long_subject_reports_its_actual_length():
    subject = "fix: " + ("x" * 100)
    problems = validate_commit_message(subject)
    assert any(f"{len(subject)} characters" in p for p in problems)


def test_a_trailing_period_is_reported():
    assert "subject ends with a period" in validate_commit_message(
        "fix: stop renumbering task references."
    )


def test_a_body_glued_to_the_subject_is_reported():
    problems = validate_commit_message("fix: a thing\nthe body starts here")
    assert any("blank line" in p for p in problems)


def test_an_empty_message_is_reported_once():
    assert validate_commit_message("") == ["the message is empty"]
    assert validate_commit_message("   \n  ") == ["the message is empty"]


def test_the_check_is_deterministic():
    """Same input, same verdict — the property that makes it usable as a gate."""
    message = "wip: something."
    assert validate_commit_message(message) == validate_commit_message(message)


# --------------------------------------------------------------------------
# Repairs that cannot change meaning
# --------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("fix: a thing.", "fix: a thing"),
    ('"fix: a thing"', "fix: a thing"),
    ("'fix: a thing'", "fix: a thing"),
    ("`fix: a thing`", "fix: a thing"),
    ("Commit message: fix: a thing", "fix: a thing"),
    ("Subject: fix: a thing", "fix: a thing"),
    ("fix:   a thing", "fix: a thing"),
    ("```\nfix: a thing\n```", "fix: a thing"),
])
def test_repairs_produce_a_conforming_subject(raw, expected):
    assert normalize_commit_message(raw) == expected
    assert validate_commit_message(normalize_commit_message(raw)) == []


def test_a_body_survives_repair_and_keeps_one_blank_line():
    repaired = normalize_commit_message(
        "fix: a thing.\n\n\nWhy it was broken.\nAnd what changed."
    )
    assert repaired.splitlines() == [
        "fix: a thing", "", "Why it was broken.", "And what changed.",
    ]
    assert validate_commit_message(repaired) == []


def test_a_body_glued_to_the_subject_gets_its_blank_line():
    repaired = normalize_commit_message("fix: a thing\nWhy it was broken.")
    assert validate_commit_message(repaired) == []


def test_an_apostrophe_inside_the_summary_is_not_treated_as_a_quote():
    assert normalize_commit_message("fix: don't drop the user's bindings") == (
        "fix: don't drop the user's bindings"
    )


def test_repair_does_not_invent_a_type():
    """An over-long subject or a missing type needs a rewrite, and a rewrite is
    a change of meaning. Those stay for the check to report."""
    assert normalize_commit_message("stop renumbering references") == (
        "stop renumbering references"
    )
    assert validate_commit_message("stop renumbering references")


def test_repair_does_not_shorten_an_over_long_subject():
    long_subject = "fix: " + ("x" * 100)
    assert normalize_commit_message(long_subject) == long_subject
    assert validate_commit_message(long_subject)


def test_repair_is_idempotent():
    once = normalize_commit_message('"Commit message: fix: a thing."')
    assert normalize_commit_message(once) == once
