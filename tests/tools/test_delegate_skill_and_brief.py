"""Handing a skill down the tree, and the brief contract that goes with it.

A subagent already inherits the ``skills`` toolset and could look a skill up
itself; what was missing was any way to say *which* skill governs the cell it
was handed. These cover the delivery path and the shapes a small model actually
produces when asked for a brief.
"""

import json

import pytest

from agent import task_brief as tb
from tools import delegate_tool as D


class _ReachedChildBuild(Exception):
    """Raised by a stubbed _build_child_agent: getting here proves the call
    passed every gate under test without building a real agent."""


class _FakeParent:
    """Minimal stand-in: delegate_task only reads these before it refuses."""

    model = "test-model"
    _delegate_depth = 0

    def __init__(self, require_brief=False):
        self._cluster_require_brief = require_brief


# ---------------------------------------------------------------------------
# The brief's container, not its contract
# ---------------------------------------------------------------------------


def _raise_reached(*args, **kwargs):
    raise _ReachedChildBuild


def test_coerce_parses_a_json_string_brief():
    payload = {"role": "worker", "known": "tree X", "task": "do Y", "done": "rc 0"}
    assert tb.coerce(json.dumps(payload)) == payload


def test_coerce_parses_a_fenced_json_brief():
    payload = {"role": "worker", "known": "tree X", "task": "do Y", "done": "rc 0"}
    assert tb.coerce("```json\n" + json.dumps(payload) + "\n```") == payload


def test_coerce_leaves_dicts_and_prose_alone():
    payload = {"role": "worker"}
    assert tb.coerce(payload) is payload
    assert tb.coerce("just some prose") == "just some prose"
    assert tb.coerce("{not json at all") == "{not json at all"


def test_coerce_does_not_relax_the_contract():
    """A parsed string is still validated field by field."""
    thin = tb.coerce('{"role": "w", "known": "x"}')
    assert isinstance(thin, dict)
    err = tb.validate(thin)
    assert err and "missing" in err


def test_schema_accepts_object_or_string():
    prop = tb.schema_property()
    assert prop["type"] == ["object", "string"]
    assert set(tb.REQUIRED_FIELDS) <= set(prop["properties"])


def test_contract_hint_carries_a_fillable_shape():
    hint = tb.contract_hint()
    for key in tb.REQUIRED_FIELDS:
        assert key in hint
    # A refusal that only names the rule makes the caller guess at the shape.
    assert '"goal"' in hint and '"brief"' in hint


# ---------------------------------------------------------------------------
# The single-task form carries the same fields as a task object
# ---------------------------------------------------------------------------


def test_top_level_brief_and_skill_are_in_the_schema():
    props = D.DELEGATE_TASK_SCHEMA["parameters"]["properties"]
    assert "brief" in props and "skill" in props
    per_task = props["tasks"]["items"]["properties"]
    assert "brief" in per_task and "skill" in per_task


def test_handler_passes_brief_and_skill_through(monkeypatch):
    """The registry lambda must forward the new arguments; dropping them makes
    the fields unreachable to every model that uses the single-task form."""
    seen = {}

    def _fake(**kwargs):
        seen.update(kwargs)
        return "{}"

    monkeypatch.setattr(D, "delegate_task", _fake)
    entry = D.registry.get_entry("delegate_task")
    entry.handler(
        {"goal": "g", "brief": {"role": "r"}, "skill": "s"}, parent_agent=None
    )
    assert seen["brief"] == {"role": "r"}
    assert seen["skill"] == "s"


def test_a_brief_without_a_goal_supplies_its_own(monkeypatch):
    """The brief's `task` field is the goal, stated more completely."""
    brief = {
        "role": "Worker on one file",
        "known": "tree /x, README present, no neighbours",
        "task": "create NOTES.md containing ready",
        "done": "the check command exits 0",
    }
    captured = {}

    def _fake_build(*args, **kwargs):
        captured["goal"] = kwargs.get("goal", args[1] if len(args) > 1 else None)
        raise _ReachedChildBuild

    monkeypatch.setattr(D, "_build_child_agent", _fake_build)
    with pytest.raises(_ReachedChildBuild):
        D.delegate_task(brief=brief, parent_agent=_FakeParent())
    assert captured["goal"] == brief["task"]


def test_a_brief_without_a_goal_or_task_is_refused():
    out = json.loads(D.delegate_task(brief={"role": "r"}, parent_agent=_FakeParent()))
    assert "no `task` field" in out["error"]


# ---------------------------------------------------------------------------
# Skills handed down
# ---------------------------------------------------------------------------


def test_unknown_skill_refuses_before_any_child_is_built():
    out = json.loads(
        D.delegate_task(
            goal="do the thing", skill="no-such-skill-anywhere",
            parent_agent=_FakeParent(),
        )
    )
    assert "no such skill" in out["error"]
    assert "skills.external_dirs" in out["error"]


def test_skill_references_are_inlined(tmp_path, monkeypatch):
    """A subagent has no user to nudge it, so a listed reference is an unread
    reference. The parts that carry the contract must arrive with the skill."""
    skill_dir = tmp_path / "demo"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("body")
    (skill_dir / "references" / "form.md").write_text("THE FORM: role, task, done")

    def _fake_skill_view(name):
        return json.dumps({"skill_dir": str(skill_dir)})

    import tools.skills_tool as st

    monkeypatch.setattr(st, "skill_view", _fake_skill_view)
    out = D._inline_skill_references("PRELOADED", ["demo"])
    assert "THE FORM" in out
    assert "references/form.md" in out


def test_oversized_reference_keeps_its_path_instead(tmp_path, monkeypatch):
    skill_dir = tmp_path / "demo"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "references" / "huge.md").write_text("x" * 100)

    import tools.skills_tool as st

    monkeypatch.setattr(
        st, "skill_view", lambda name: json.dumps({"skill_dir": str(skill_dir)})
    )
    monkeypatch.setattr(D, "_SKILL_REFERENCE_BUDGET_CHARS", 10)
    out = D._inline_skill_references("PRELOADED", ["demo"])
    assert "not inlined" in out
    assert "xxxxx" not in out


# ---------------------------------------------------------------------------
# require_brief: opt-in, and it propagates
# ---------------------------------------------------------------------------


def test_brief_is_not_required_by_default(monkeypatch):
    """Default off, measured: enforcing it turned a weak brief into no worker
    at all on qwen2.5:7b / 14b, and the tree stopped at depth 1."""
    monkeypatch.setattr(D, "_load_config", lambda: {})
    monkeypatch.setattr(D, "_build_child_agent", _raise_reached)
    with pytest.raises(_ReachedChildBuild):
        D.delegate_task(goal="do it", parent_agent=_FakeParent(require_brief=True))


def test_require_brief_refuses_a_bare_goal_when_enabled(monkeypatch):
    monkeypatch.setattr(D, "_load_config", lambda: {"require_brief": True})
    out = json.loads(
        D.delegate_task(goal="do it", parent_agent=_FakeParent(require_brief=True))
    )
    assert "needs a `brief`" in out["error"]
    assert '"role"' in out["error"]  # the fillable shape, not just the rule


def test_require_brief_does_not_apply_to_an_unbriefed_parent(monkeypatch):
    """The rule is 'if you were briefed, you brief' -- not 'everyone briefs'."""
    monkeypatch.setattr(D, "_load_config", lambda: {"require_brief": True})
    monkeypatch.setattr(D, "_build_child_agent", _raise_reached)
    with pytest.raises(_ReachedChildBuild):
        D.delegate_task(goal="do it", parent_agent=_FakeParent(require_brief=False))
