"""The brief is built, not begged for -- and the supervisor is code, not a turn.

Two devices, one test file, because they are the same idea twice: a decision
that was already written down should be executed by the harness rather than
re-derived by a model that gets it wrong differently every run.

* **Composition.** Measured on qwen2.5:7b/14b-instruct, a worker got either no
  brief at all (7b, 0 chars) or a retelling in ``context`` (14b, 486 chars, no
  role, no falsifier, no format) -- first because the model path dropped the
  ``brief`` argument, then because a thin brief was refused and the corrected
  call never came. Three of the five required contents of ``task-brief.md`` are
  facts the harness holds; the fourth is ``goal``. Only the definition of done
  is irreducibly the dispatcher's.

* **Supervision.** Start, watch, restart, give up, end -- ``agent/supervisor.py``
  does it; the model only declares the strategy.
"""

import json
import threading
from unittest.mock import MagicMock

from agent import task_brief as tb
from tools import delegate_tool as D


def _parent(depth=0, briefed=False, model="test-model"):
    p = MagicMock()
    p.base_url = ""
    p.api_key = "***"
    p.provider = None
    p.api_mode = None
    p.model = model
    p.platform = "cli"
    p.providers_allowed = None
    p.providers_ignored = None
    p.providers_order = None
    p.provider_sort = None
    p._session_db = None
    p._delegate_depth = depth
    p._active_children = []
    p._active_children_lock = threading.Lock()
    p._print_fn = None
    p.tool_progress_callback = None
    p.thinking_callback = None
    p._memory_manager = None
    p._cluster_require_brief = briefed
    p._cluster_brief_text = "a preloaded skill, then the brief" if briefed else ""
    # The excerpt handed to a grandchild is the brief alone, never whatever
    # else the parent's context happened to carry (a preloaded skill, say).
    p._cluster_brief_rendered = (
        "ROLE\nthe lead\n\nTASK\nsplit the work" if briefed else ""
    )
    p._current_task_id = None
    return p


def _capture_context(monkeypatch):
    """Stop at child construction and hand back what the child would have read."""
    seen = {}

    def _fake_build(**kwargs):
        seen.setdefault("contexts", []).append(kwargs.get("context"))
        raise _Stop

    monkeypatch.setattr(D, "_build_child_agent", _fake_build)
    return seen


class _Stop(Exception):
    pass


def _run_capturing(monkeypatch, **kwargs):
    seen = _capture_context(monkeypatch)
    try:
        D.delegate_task(**kwargs)
    except _Stop:
        pass
    return seen.get("contexts") or []


def _stub_children(monkeypatch, script):
    """Replace real agents and real runs with a scripted status per attempt."""
    builds = []

    def _fake_build(**kwargs):
        child = MagicMock()
        child.tool_progress_callback = None
        child._subagent_id = f"sa-{len(builds)}"
        builds.append(child)
        return child

    runs = []

    def _fake_run(task_index, goal, child=None, parent_agent=None, **kw):
        attempt = sum(1 for r in runs if r == task_index)
        runs.append(task_index)
        statuses = script.get(task_index, ["completed"])
        status = statuses[min(attempt, len(statuses) - 1)]
        return {
            "task_index": task_index,
            "status": status,
            "summary": f"{goal}:{status}",
            "error": None if status == "completed" else "child fell over",
            "api_calls": 1,
            "duration_seconds": 0.0,
        }

    monkeypatch.setattr(D, "_build_child_preserving_parent_tools", _fake_build)
    monkeypatch.setattr(D, "_run_single_child", _fake_run)
    return builds, runs


# ---------------------------------------------------------------------------
# The brief exists whether or not anybody wrote one
# ---------------------------------------------------------------------------

_HEADINGS = ("ROLE", "CONTEXT", "KNOWN:", "TASK", "DEFINITION OF DONE", "FORMAT")


def test_a_bare_goal_still_delivers_a_full_rctf_brief(monkeypatch):
    """The failing case, measured live: a lead that sends nothing but a goal."""
    ctx = _run_capturing(
        monkeypatch,
        goal="create NOTES.md containing the single word ready",
        parent_agent=_parent(),
    )[0]
    for heading in _HEADINGS:
        assert heading in ctx, heading
    assert "create NOTES.md containing the single word ready" in ctx


def test_the_composed_brief_names_the_write_boundary(monkeypatch):
    ctx = _run_capturing(
        monkeypatch,
        tasks=[{"goal": "do the cell", "write_root": "/srv/work/x"}],
        parent_agent=_parent(),
    )[0]
    assert "/srv/work/x" in ctx
    assert "BOUNDARIES:" in ctx


def test_siblings_become_the_neighbours_section(monkeypatch):
    """NEIGHBOURS is a required content of the brief and a fact only the
    harness has: it is the rest of this very fan-out."""
    contexts = _run_capturing(
        monkeypatch,
        tasks=[{"goal": "measure the parser"}, {"goal": "rewrite the loader"}],
        parent_agent=_parent(),
    )
    assert "rewrite the loader" in contexts[0]
    assert "NEIGHBOURS:" in contexts[0]


def test_a_lone_worker_is_told_it_is_alone(monkeypatch):
    ctx = _run_capturing(
        monkeypatch, goal="the only cell", parent_agent=_parent()
    )[0]
    assert "No sibling worker" in ctx


def test_the_parents_own_brief_is_carried_down_as_provenance(monkeypatch):
    ctx = _run_capturing(
        monkeypatch, goal="the cell", parent_agent=_parent(briefed=True)
    )[0]
    assert "Issued to your dispatcher" in ctx
    assert "split the work" in ctx
    assert "a preloaded skill" not in ctx


def test_reserved_actions_are_stated_so_the_worker_neither_stalls_nor_pushes(
    monkeypatch,
):
    ctx = _run_capturing(monkeypatch, goal="the cell", parent_agent=_parent())[0]
    assert "RESERVED TO YOUR PARENT" in ctx
    for act in ("commit", "push", "merge", "release"):
        assert act in ctx


# ---------------------------------------------------------------------------
# What the model does send is kept, and recorded as its own
# ---------------------------------------------------------------------------


def test_the_flat_definition_of_done_wins_over_the_composed_one(monkeypatch):
    ctx = _run_capturing(
        monkeypatch,
        goal="the cell",
        brief_done="`pytest tests/x.py` exits 0",
        parent_agent=_parent(),
    )[0]
    assert "`pytest tests/x.py` exits 0" in ctx
    assert "A result nobody can re-check" not in ctx


def test_a_full_brief_object_still_wins_field_by_field(monkeypatch):
    ctx = _run_capturing(
        monkeypatch,
        goal="the cell",
        brief={
            "role": "You are the loader's only owner",
            "done": "loader_test passes",
        },
        parent_agent=_parent(),
    )[0]
    assert "You are the loader's only owner" in ctx
    assert "loader_test passes" in ctx
    # ...and the fields it left out are still composed rather than missing.
    assert "NEIGHBOURS:" in ctx and "FORMAT" in ctx


def test_free_text_where_a_brief_belongs_is_folded_in_not_refused(monkeypatch):
    """Refusing was measured to be the worse failure: the corrected second call
    never came, and the tree stopped at depth 1."""
    ctx = _run_capturing(
        monkeypatch,
        goal="the cell",
        brief="just do the thing carefully, the parser is at src/p.py",
        parent_agent=_parent(),
    )[0]
    assert "src/p.py" in ctx
    assert "in your dispatcher's own words" in ctx.lower()
    for heading in _HEADINGS:
        assert heading in ctx


def test_provenance_separates_who_wrote_which_field():
    brief, source = tb.compose(
        goal="the cell", supplied={"done": "make check exits 0"}
    )
    assert source["done"] == "model"
    assert source["task"] == "goal"
    assert source["role"] == "harness"
    assert source["neighbours"] == "harness"


def test_provenance_reaches_the_child_and_therefore_the_ledger(monkeypatch):
    builds, _ = _stub_children(monkeypatch, {0: ["completed"]})
    D.delegate_task(goal="the cell", brief_done="rc 0", parent_agent=_parent())
    # _make_child sets it on the agent; _run_single_child copies it into the
    # ledger's spawn row.
    assert builds[0]._cluster_brief_source["done"] == "model"
    assert builds[0]._cluster_brief_source["role"] == "harness"


def test_composition_can_be_switched_off_by_the_operator(monkeypatch):
    monkeypatch.setattr(D, "_load_config", lambda: {"compose_brief": False})
    ctx = _run_capturing(monkeypatch, goal="the cell", parent_agent=_parent())[0]
    assert ctx in (None, "")


# ---------------------------------------------------------------------------
# Supervision through delegate_task
# ---------------------------------------------------------------------------


def test_an_unsupervised_delegation_is_unchanged(monkeypatch):
    _stub_children(monkeypatch, {0: ["error"]})
    out = json.loads(D.delegate_task(goal="one cell", parent_agent=_parent()))
    assert "supervisor" not in out
    assert out["results"][0]["status"] == "error"


def test_a_supervisor_restarts_the_child_that_fell_over(monkeypatch):
    builds, runs = _stub_children(monkeypatch, {0: ["error", "completed"]})
    out = json.loads(
        D.delegate_task(
            goal="one cell", supervisor="one_for_one", parent_agent=_parent()
        )
    )
    assert out["results"][0]["status"] == "completed"
    assert out["supervisor"]["reason"] == "normal"
    assert len(out["supervisor"]["restarts"]) == 1
    assert runs == [0, 0]
    # Fail fast: the restart is a NEW agent, not the one that died.
    assert len(builds) == 2 and builds[0] is not builds[1]


def test_the_restarted_child_reads_the_same_brief(monkeypatch):
    builds, _ = _stub_children(monkeypatch, {0: ["error", "completed"]})
    D.delegate_task(
        goal="one cell", brief_done="rc 0",
        supervisor="one_for_one", parent_agent=_parent(),
    )
    assert len(builds) == 2
    assert builds[0]._cluster_brief_text == builds[1]._cluster_brief_text
    assert "DEFINITION OF DONE" in builds[1]._cluster_brief_text


def test_a_hopeless_child_exhausts_the_intensity_and_the_supervisor_gives_up(
    monkeypatch,
):
    _, runs = _stub_children(monkeypatch, {0: ["error"]})
    out = json.loads(
        D.delegate_task(
            goal="one cell",
            supervisor={"strategy": "one_for_one", "max_restarts": 2},
            parent_agent=_parent(),
        )
    )
    assert out["supervisor"]["reason"] == "shutdown"
    assert runs == [0, 0, 0]
    assert out["results"][0]["status"] == "error"


def test_one_for_all_restarts_the_sibling_that_had_finished(monkeypatch):
    _, runs = _stub_children(
        monkeypatch, {0: ["completed"], 1: ["error", "completed"]}
    )
    out = json.loads(
        D.delegate_task(
            tasks=[{"goal": "cell A"}, {"goal": "cell B"}],
            supervisor="one_for_all",
            parent_agent=_parent(),
        )
    )
    assert out["supervisor"]["strategy"] == "one_for_all"
    assert runs.count(0) == 2 and runs.count(1) == 2


def test_a_temporary_task_is_never_restarted(monkeypatch):
    _, runs = _stub_children(monkeypatch, {0: ["error"]})
    out = json.loads(
        D.delegate_task(
            tasks=[{"goal": "cell", "restart": "temporary"}],
            supervisor="one_for_one",
            parent_agent=_parent(),
        )
    )
    assert runs == [0]
    assert out["supervisor"]["reason"] == "normal"


def test_a_misspelled_strategy_is_refused_before_anything_is_built(monkeypatch):
    builds, _ = _stub_children(monkeypatch, {})
    out = json.loads(
        D.delegate_task(
            goal="cell", supervisor="one_for_two", parent_agent=_parent()
        )
    )
    assert "unknown supervision strategy" in out["error"]
    assert "rest_for_one" in out["error"]
    assert builds == []


def test_the_restarts_reach_the_cluster_ledger(monkeypatch, tmp_path):
    from agent import cluster_boundary as cb

    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setenv(cb.ENV_LEDGER, str(ledger))
    _stub_children(monkeypatch, {0: ["error", "completed"]})
    D.delegate_task(
        goal="one cell", supervisor="one_for_one", parent_agent=_parent()
    )
    events = [r["event"] for r in cb.read_ledger(str(ledger))]
    assert "supervisor_start" in events
    assert "child_started" in events
    assert "child_terminated" in events
    assert "child_restart" in events
    assert "supervisor_exit" in events


# ---------------------------------------------------------------------------
# The tool surface the model actually sees
# ---------------------------------------------------------------------------


def test_the_flat_fields_and_supervisor_are_in_the_schema():
    props = D.DELEGATE_TASK_SCHEMA["parameters"]["properties"]
    for key in ("brief_done", "brief_falsifier", "brief_known", "supervisor"):
        assert key in props
    per_task = props["tasks"]["items"]["properties"]
    for key in ("brief_done", "restart"):
        assert key in per_task
    assert props["supervisor"]["properties"]["strategy"]["enum"] == [
        "one_for_one", "one_for_all", "rest_for_one"
    ]


def test_the_handler_forwards_every_new_argument(monkeypatch):
    seen = {}

    def _fake(**kwargs):
        seen.update(kwargs)
        return "{}"

    monkeypatch.setattr(D, "delegate_task", _fake)
    D.registry.get_entry("delegate_task").handler(
        {
            "goal": "g", "brief_done": "d", "brief_falsifier": "f",
            "brief_known": "k", "supervisor": "one_for_all",
        },
        parent_agent=None,
    )
    assert seen["brief_done"] == "d"
    assert seen["brief_falsifier"] == "f"
    assert seen["brief_known"] == "k"
    assert seen["supervisor"] == "one_for_all"


def test_the_live_model_path_forwards_them_too():
    """The registry lambda is not the path a model takes; run_agent's dispatch
    is. brief and skill were in the schema but missing there, so a model that
    did send a brief had it dropped silently -- which no measurement of "the
    lead does not brief" could distinguish from the lead's own omission.
    """
    import inspect
    import run_agent

    src = inspect.getsource(run_agent.AIAgent._dispatch_delegate_task)
    for field in (
        "brief", "brief_done", "brief_falsifier", "brief_known",
        "skill", "supervisor",
    ):
        assert f'function_args.get("{field}")' in src, field
