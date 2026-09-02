"""OTP supervision, checked without a model.

The point of putting the supervisor in code rather than in a prompt is that its
behaviour can be stated and checked. These tests state it: which child restarts
under which strategy, which restart type fires on which termination, when the
restart intensity gives up, and that the supervisor ends by itself when the
work is done.
"""

import time

import pytest

from agent import supervisor as S


def _result(status="completed", **extra):
    out = {"status": status}
    out.update(extra)
    return out


class _Script:
    """A child whose result on each attempt is scripted in advance."""

    def __init__(self, *statuses):
        self.statuses = list(statuses)
        self.attempts = []

    def __call__(self, attempt):
        self.attempts.append(attempt)
        idx = min(attempt, len(self.statuses) - 1)
        return _result(self.statuses[idx])


def _spec(cid, script, restart=S.TRANSIENT, terminate=None):
    return S.ChildSpec(id=cid, start=script, restart=restart, terminate=terminate)


def _events(sink):
    def _on(event, **fields):
        sink.append((event, fields))

    return _on


# ---------------------------------------------------------------------------
# Flags and child specs are data, and bad data is refused
# ---------------------------------------------------------------------------


def test_default_flags_are_otps_names():
    flags = S.normalize_flags(None)
    assert flags.strategy == "one_for_one"
    assert (flags.max_restarts, flags.max_seconds) == (3, 300.0)


def test_a_bare_strategy_name_is_accepted():
    assert S.normalize_flags("one_for_all").strategy == "one_for_all"


@pytest.mark.parametrize("bad", ["one_for_two", "simple_one_for_one", "restart_all"])
def test_unknown_strategy_is_refused_by_name(bad):
    with pytest.raises(ValueError) as exc:
        S.normalize_flags(bad)
    for known in S.STRATEGIES:
        assert known in str(exc.value)


def test_unknown_restart_type_is_refused():
    with pytest.raises(ValueError):
        S.normalize_restart("sometimes")
    assert S.normalize_restart(None) == S.TRANSIENT


def test_intensity_must_be_a_window():
    with pytest.raises(ValueError):
        S.normalize_flags({"max_seconds": 0})
    with pytest.raises(ValueError):
        S.normalize_flags({"max_restarts": -1})


# ---------------------------------------------------------------------------
# Restart types
# ---------------------------------------------------------------------------


def test_transient_restarts_only_on_abnormal_termination():
    ok = _Script("completed")
    bad = _Script("error", "completed")
    rep = S.Supervisor(
        S.SupFlags(), [_spec("a", ok), _spec("b", bad)]
    ).run()
    assert ok.attempts == [0]          # a completed: never restarted
    assert bad.attempts == [0, 1]      # b failed once, restarted once
    assert rep["reason"] == "normal"
    assert rep["children"]["b"]["status"] == "completed"


def test_temporary_is_never_restarted():
    bad = _Script("error", "error", "completed")
    rep = S.Supervisor(
        S.SupFlags(), [_spec("a", bad, restart=S.TEMPORARY)]
    ).run()
    assert bad.attempts == [0]
    assert rep["reason"] == "normal"
    assert rep["children"]["a"]["status"] == "error"


def test_permanent_restarts_a_completed_child_until_intensity_gives_up():
    """Implemented as OTP defines it, and therefore useless for a task tree.

    A permanent child that completes is restarted anyway, so a task-scoped
    supervisor holding one always ends by exceeding its restart intensity.
    Named here rather than quietly renamed.
    """
    always = _Script("completed")
    rep = S.Supervisor(
        S.SupFlags(max_restarts=2, max_seconds=60),
        [_spec("a", always, restart=S.PERMANENT)],
    ).run()
    assert rep["reason"] == "shutdown"
    assert len(always.attempts) == 3  # first start + max_restarts restarts


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


def test_one_for_one_leaves_the_healthy_children_alone():
    a, b, c = _Script("completed"), _Script("error", "completed"), _Script("completed")
    S.Supervisor(
        S.SupFlags(strategy=S.ONE_FOR_ONE),
        [_spec("a", a), _spec("b", b), _spec("c", c)],
    ).run()
    assert (a.attempts, b.attempts, c.attempts) == ([0], [0, 1], [0])


def test_one_for_all_restarts_every_child():
    a, b, c = _Script("completed"), _Script("error", "completed"), _Script("completed")
    rep = S.Supervisor(
        S.SupFlags(strategy=S.ONE_FOR_ALL),
        [_spec("a", a), _spec("b", b), _spec("c", c)],
    ).run()
    assert a.attempts == [0, 1]
    assert b.attempts == [0, 1]
    assert c.attempts == [0, 1]
    assert rep["reason"] == "normal"


def test_rest_for_one_restarts_the_failed_child_and_those_after_it():
    a, b, c = _Script("completed"), _Script("error", "completed"), _Script("completed")
    S.Supervisor(
        S.SupFlags(strategy=S.REST_FOR_ONE),
        [_spec("a", a), _spec("b", b), _spec("c", c)],
    ).run()
    assert a.attempts == [0]           # declared before the failure
    assert b.attempts == [0, 1]
    assert c.attempts == [0, 1]        # declared after it


def test_one_for_all_asks_a_running_sibling_to_terminate():
    stopped = []

    def _slow(attempt):
        for _ in range(200):
            if stopped:
                return _result("interrupted")
            time.sleep(0.01)
        return _result("completed")

    rep = S.Supervisor(
        S.SupFlags(strategy=S.ONE_FOR_ALL, max_restarts=0),
        [
            _spec("fast", _Script("error")),
            _spec("slow", _slow, terminate=lambda: stopped.append(True)),
        ],
    ).run()
    assert stopped, "the strategy took the sibling down but never asked it to stop"
    assert rep["children"]["slow"]["terminated_by_supervisor"] == S.ONE_FOR_ALL


# ---------------------------------------------------------------------------
# Restart intensity
# ---------------------------------------------------------------------------


def test_intensity_exceeded_shuts_the_supervisor_down():
    hopeless = _Script("error")
    rep = S.Supervisor(
        S.SupFlags(max_restarts=2, max_seconds=60), [_spec("a", hopeless)]
    ).run()
    assert rep["reason"] == "shutdown"
    assert hopeless.attempts == [0, 1, 2]
    assert len(rep["restarts"]) == 2


def test_zero_max_restarts_means_no_restart_at_all():
    hopeless = _Script("error")
    rep = S.Supervisor(
        S.SupFlags(max_restarts=0), [_spec("a", hopeless)]
    ).run()
    assert hopeless.attempts == [0]
    assert rep["reason"] == "shutdown"


def test_a_start_that_raises_is_an_abnormal_termination_not_a_crash():
    def _boom(attempt):
        if attempt == 0:
            raise RuntimeError("child blew up in start")
        return _result("completed")

    rep = S.Supervisor(S.SupFlags(), [_spec("a", _boom)]).run()
    assert rep["reason"] == "normal"
    assert rep["children"]["a"]["status"] == "completed"
    assert rep["restarts"][0]["because"].startswith("child start raised")


# ---------------------------------------------------------------------------
# The journal
# ---------------------------------------------------------------------------


def test_every_lifecycle_event_reaches_the_journal():
    sink = []
    bad = _Script("error", "completed")
    S.Supervisor(
        S.SupFlags(), [_spec("a", bad)], on_event=_events(sink), name="sup-1"
    ).run()
    names = [e for e, _ in sink]
    assert names[0] == "supervisor_start"
    assert names[-1] == "supervisor_exit"
    assert names.count("child_started") == 2      # start + restart
    assert names.count("child_terminated") == 2
    assert names.count("child_restart") == 1
    assert all(f.get("supervisor") == "sup-1" for _, f in sink)

    restart = next(f for e, f in sink if e == "child_restart")
    assert restart["child_id"] == "a" and restart["attempt"] == 1

    exit_ev = next(f for e, f in sink if e == "supervisor_exit")
    assert exit_ev["reason"] == "normal" and exit_ev["restarts"] == 1


def test_a_broken_journal_never_stops_the_supervision():
    def _explode(event, **fields):
        raise RuntimeError("journal is on fire")

    rep = S.Supervisor(
        S.SupFlags(), [_spec("a", _Script("completed"))], on_event=_explode
    ).run()
    assert rep["reason"] == "normal"


def test_the_supervisor_ends_when_the_work_is_done():
    """Task-scoped, unlike an OTP supervisor -- the deliberate deviation."""
    rep = S.Supervisor(
        S.SupFlags(), [_spec("a", _Script("completed")), _spec("b", _Script("completed"))]
    ).run()
    assert rep["reason"] == "normal"
    assert rep["waves"] == 1
    assert set(rep["children"]) == {"a", "b"}
    assert rep["duration_seconds"] >= 0


# ---------------------------------------------------------------------------
# What counts as a normal termination
# ---------------------------------------------------------------------------


def test_status_alone_does_not_decide_normality():
    """Measured live: a child that could not reach the model endpoint at all
    returned status="completed", exit_reason="max_iterations", summary "API
    call failed after 3 retries: Connection error." Keying the restart decision
    on status alone missed exactly the failure a restart is for."""
    assert S.is_normal({"status": "completed"}) is True
    assert S.is_normal({"status": "completed", "exit_reason": "completed"}) is True
    assert S.is_normal(
        {"status": "completed", "exit_reason": "max_iterations"}
    ) is False
    assert S.is_normal({"status": "completed", "exit_reason": "interrupted"}) is False
    assert S.is_normal({"status": "error", "exit_reason": "completed"}) is False


def test_a_budget_exhausted_child_is_restarted():
    calls = []

    def _start(attempt):
        calls.append(attempt)
        if attempt == 0:
            return {"status": "completed", "exit_reason": "max_iterations"}
        return {"status": "completed", "exit_reason": "completed"}

    rep = S.Supervisor(S.SupFlags(), [_spec("a", _start)]).run()
    assert calls == [0, 1]
    assert rep["reason"] == "normal"
