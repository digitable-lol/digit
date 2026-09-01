"""Tests for per-agent write boundaries, the cluster ledger, and RCTF briefs."""

import json
import os

import pytest

from agent import cluster_boundary as cb
from agent import task_brief as tb


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Each test gets a private registry, ledger, and env root."""
    monkeypatch.setattr(cb, "_roots", {})
    monkeypatch.delenv(cb.ENV_ROOT, raising=False)
    monkeypatch.setenv(cb.ENV_LEDGER, str(tmp_path / "ledger.jsonl"))
    yield


# ---------------------------------------------------------------------------
# Boundary registration
# ---------------------------------------------------------------------------


def test_no_boundary_means_unbounded(tmp_path):
    """Default behaviour is preserved: an unregistered task writes anywhere."""
    assert cb.get_write_roots("nobody") == ()
    assert cb.check_write_allowed(str(tmp_path / "x"), "nobody") is None


def test_child_may_narrow(tmp_path):
    parent, child = tmp_path / "p", tmp_path / "p" / "c"
    child.mkdir(parents=True)
    assert cb.set_write_root("P", [str(parent)]) is None
    assert cb.set_write_root("C", [str(child)], parent_task_id="P") is None
    assert cb.get_write_roots("C") == (os.path.realpath(str(child)),)


def test_child_may_not_widen(tmp_path):
    parent = tmp_path / "p"
    parent.mkdir()
    cb.set_write_root("P", [str(parent)])
    err = cb.set_write_root("C", [str(tmp_path)], parent_task_id="P")
    assert err is not None
    assert "outside the parent's boundary" in err
    # A refused registration must not leave a boundary behind.
    assert "C" not in cb._roots


def test_child_may_not_escape_sideways(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    cb.set_write_root("P", [str(tmp_path / "a")])
    assert cb.set_write_root("C", [str(tmp_path / "b")], parent_task_id="P")


def test_child_without_request_inherits_parent(tmp_path):
    cb.set_write_root("P", [str(tmp_path)])
    assert cb.set_write_root("C", [], parent_task_id="P") is None
    assert cb.get_write_roots("C") == cb.get_write_roots("P")


def test_env_root_is_the_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv(cb.ENV_ROOT, str(tmp_path))
    assert cb.get_write_roots("unregistered") == (os.path.realpath(str(tmp_path)),)


def test_traversal_cannot_escape(tmp_path):
    inside = tmp_path / "in"
    inside.mkdir()
    cb.set_write_root("W", [str(inside)])
    assert cb.check_write_allowed(str(inside / ".." / "out.txt"), "W") is not None


def test_sibling_prefix_is_not_inside(tmp_path):
    """`/root/ab` must not count as inside `/root/a`."""
    (tmp_path / "a").mkdir()
    (tmp_path / "ab").mkdir()
    cb.set_write_root("W", [str(tmp_path / "a")])
    assert cb.check_write_allowed(str(tmp_path / "ab" / "f.txt"), "W") is not None


def test_clear_releases(tmp_path):
    cb.set_write_root("W", [str(tmp_path)])
    cb.clear_write_root("W")
    assert cb.get_write_roots("W") == ()


# ---------------------------------------------------------------------------
# Enforcement through the real write tool
# ---------------------------------------------------------------------------


def test_write_tool_refuses_outside_boundary(tmp_path):
    from tools.file_tools import write_file_tool

    inside, outside = tmp_path / "in", tmp_path / "out"
    inside.mkdir()
    outside.mkdir()
    cb.set_write_root("W", [str(inside)])

    ok = json.loads(write_file_tool(str(inside / "a.txt"), "x", task_id="W"))
    assert "error" not in ok

    bad = json.loads(write_file_tool(str(outside / "b.txt"), "x", task_id="W"))
    assert "write boundary" in bad.get("error", "")
    assert not (outside / "b.txt").exists()


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def test_ledger_appends_and_reads():
    cb.record("spawn", parent_task_id="A", child_task_id="B", depth=1)
    cb.record("finish", child_task_id="B", status="completed")
    rows = cb.read_ledger()
    assert [r["event"] for r in rows] == ["spawn", "finish"]
    assert rows[0]["depth"] == 1
    assert all("ts" in r for r in rows)


def test_ledger_never_raises(monkeypatch):
    monkeypatch.setenv(cb.ENV_LEDGER, "/proc/definitely/not/writable/l.jsonl")
    cb.record("spawn", child_task_id="X")  # must not raise


# ---------------------------------------------------------------------------
# RCTF briefs
# ---------------------------------------------------------------------------


GOOD_BRIEF = {
    "role": "You are a worker who measures and does not merge.",
    "known": "digit commit a091c49d2; max_spawn_depth=2 from config.yaml",
    "task": "Run the depth probe and record which depths are refused",
    "done": "probe prints REFUSED at depth 2",
}


def test_brief_accepts_complete():
    assert tb.validate(GOOD_BRIEF) is None


def test_brief_rejects_free_text():
    assert "must be an object" in tb.validate("go fix the thing")


@pytest.mark.parametrize("missing", sorted(tb.REQUIRED_FIELDS))
def test_brief_rejects_missing_required(missing):
    brief = {k: v for k, v in GOOD_BRIEF.items() if k != missing}
    err = tb.validate(brief)
    assert err and missing in err


def test_brief_rejects_a_shrug():
    err = tb.validate({"role": "w", "known": "s", "task": "d", "done": "k"})
    assert "too thin" in err


def test_render_contains_the_contract():
    out = tb.render(GOOD_BRIEF)
    for heading in ("ROLE", "CONTEXT", "KNOWN:", "TASK", "DEFINITION OF DONE", "FORMAT"):
        assert heading in out
    assert "did NOT do" in out


def test_render_includes_optional_sections():
    brief = dict(GOOD_BRIEF, falsifier="if depth 2 passes, the cap is off",
                 neighbours="nobody", boundaries="write only under work/")
    out = tb.render(brief)
    assert "FALSIFIER" in out and "NEIGHBOURS:" in out and "BOUNDARIES:" in out


def test_schema_property_requires_the_four():
    assert set(tb.schema_property()["required"]) == set(tb.REQUIRED_FIELDS)


# ---------------------------------------------------------------------------
# Wiring into delegate_task
# ---------------------------------------------------------------------------


def test_delegate_schema_exposes_brief_and_write_root():
    from tools.delegate_tool import DELEGATE_TASK_SCHEMA

    item = DELEGATE_TASK_SCHEMA["parameters"]["properties"]["tasks"]["items"]
    assert "brief" in item["properties"]
    assert "write_root" in item["properties"]


def test_delegate_refuses_incomplete_brief():
    """An unusable brief is refused before any child is constructed."""
    from tools import delegate_tool as D

    class Parent:
        _delegate_depth = 0
        session_id = None

    out = D.delegate_task(
        tasks=[{"goal": "x", "brief": {"role": "w"}}], parent_agent=Parent()
    )
    assert "Task brief rejected" in out


def test_depth_cap_refuses_at_the_ceiling(monkeypatch):
    from tools import delegate_tool as D

    monkeypatch.setattr(D, "_get_max_spawn_depth", lambda: 2)

    class Parent:
        session_id = None

        def __init__(self, depth):
            self._delegate_depth = depth

    out = D.delegate_task(goal="x", parent_agent=Parent(2))
    assert "Delegation depth limit reached" in out
    assert "max_spawn_depth=2" in out
