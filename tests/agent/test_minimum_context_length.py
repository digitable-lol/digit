"""The 64K floor is a policy default, and the floor under it is measured.

Digit refused any model below 64,000 tokens of context. Measured with
``agent.context_breakdown.fixed_prefix_tokens``, the thing that floor protects
is 6,990 tokens for a delegation toolset and 15,491 for the full default one --
so it is roughly 4x the largest realistic prefix, not a hard requirement. These
cover the override and the hard floor that survives it.
"""

import pytest

from agent import model_metadata as mm


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(mm.ENV_MINIMUM_CONTEXT_LENGTH, raising=False)
    yield


class _Agent:
    """Stand-in whose measured prefix is whatever the test says it is."""

    def __init__(self, requested=None):
        if requested is not None:
            self._minimum_context_requested = requested


def _with_prefix(monkeypatch, tokens):
    monkeypatch.setattr(
        "agent.context_breakdown.fixed_prefix_tokens", lambda agent: tokens
    )


# ---------------------------------------------------------------------------
# The operator's floor
# ---------------------------------------------------------------------------


def test_default_is_unchanged():
    assert mm.configured_minimum_context_length() == mm.MINIMUM_CONTEXT_LENGTH
    assert mm.configured_minimum_context_length({}) == mm.MINIMUM_CONTEXT_LENGTH


def test_config_lowers_the_floor():
    assert mm.configured_minimum_context_length({"minimum_context_length": 32000}) == 32000


def test_env_wins_over_config(monkeypatch):
    monkeypatch.setenv(mm.ENV_MINIMUM_CONTEXT_LENGTH, "24576")
    assert mm.configured_minimum_context_length({"minimum_context_length": 32000}) == 24576


def test_junk_and_zero_leave_the_default_in_place():
    for value in ("not a number", 0, -1, None, ""):
        assert (
            mm.configured_minimum_context_length({"minimum_context_length": value})
            == mm.MINIMUM_CONTEXT_LENGTH
        )


# ---------------------------------------------------------------------------
# The floor that cannot be configured away
# ---------------------------------------------------------------------------


def test_a_lowered_floor_is_honoured_above_the_hard_minimum(monkeypatch):
    _with_prefix(monkeypatch, 6_990)  # measured: file + terminal + delegation
    effective, requested, hard = mm.minimum_context_length_for(
        _Agent(), {"minimum_context_length": 32_000}
    )
    assert requested == 32_000
    assert hard == 6_990 + mm.MINIMUM_WORKING_ROOM_TOKENS
    assert effective == 32_000


def test_the_hard_minimum_wins_when_the_request_is_absurd(monkeypatch):
    _with_prefix(monkeypatch, 6_990)
    effective, requested, hard = mm.minimum_context_length_for(
        _Agent(), {"minimum_context_length": 4_000}
    )
    assert requested == 4_000
    assert effective == hard == 14_990


def test_a_bigger_toolset_raises_the_hard_minimum(monkeypatch):
    _with_prefix(monkeypatch, 15_491)  # measured: the full default toolset
    effective, _, hard = mm.minimum_context_length_for(
        _Agent(), {"minimum_context_length": 8_000}
    )
    assert hard == 23_491
    assert effective == 23_491


def test_an_unmeasurable_prefix_does_not_invent_a_floor(monkeypatch):
    monkeypatch.setattr(
        "agent.context_breakdown.fixed_prefix_tokens",
        lambda agent: (_ for _ in ()).throw(RuntimeError("no tools yet")),
    )
    effective, requested, hard = mm.minimum_context_length_for(
        _Agent(), {"minimum_context_length": 32_000}
    )
    assert hard == 0
    assert effective == requested == 32_000


def test_the_runtime_gate_reads_the_floor_resolved_at_startup(monkeypatch):
    """conversation_loop has the agent but not its config dict."""
    _with_prefix(monkeypatch, 6_990)
    effective, requested, _ = mm.minimum_context_length_for(_Agent(requested=24_576))
    assert requested == 24_576
    assert effective == 24_576


def test_no_agent_means_no_measurement():
    effective, requested, hard = mm.minimum_context_length_for(None, {})
    assert hard == 0
    assert effective == requested == mm.MINIMUM_CONTEXT_LENGTH
