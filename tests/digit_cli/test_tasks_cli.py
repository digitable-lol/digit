"""Tests for ``digit tasks`` — the uuid-only surface over the shared tracker.

The interesting tests here are not the happy paths; they are the two contracts
that the command exists to hold:

* a positional Taskwarrior id is refused, because it is not stable, and
  :func:`test_positional_ids_shift_while_uuids_do_not` proves that instability
  against a real Taskwarrior database rather than asserting it in prose;
* Taskwarrior exits 0 when a reference matches nothing, so success is read back
  from stored state, never from the exit code.

Everything that touches the tracker runs a real ``task`` binary against a
temporary data directory, per the project's "E2E validation, not just green
unit mocks" rule.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from digit_cli.tasks_cli import (
    TaskRefError,
    Tracker,
    TrackerError,
    TrackerNotFound,
    find_tracker,
    render_list,
    require_uuid,
    tasks_command,
)

pytestmark = pytest.mark.skipif(
    shutil.which("task") is None,
    reason="the shared tracker is a Taskwarrior database; `task` is not installed",
)

_A_UUID = "276c477e-9f8e-4f1b-9950-d05a3a725c51"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def tracker(tmp_path: Path) -> Tracker:
    """A real, empty Taskwarrior database with the tracker's own rc beside it."""
    root = tmp_path / ".digitable-tasks"
    data = root / "data"
    data.mkdir(parents=True)
    (root / "taskrc").write_text(
        f"data.location={data}\nconfirmation=no\nverbose=nothing\n",
        encoding="utf-8",
    )
    return Tracker(data)


# --------------------------------------------------------------------------
# The rule: uuid or nothing
# --------------------------------------------------------------------------


def test_accepts_canonical_uuid_and_normalises_case():
    assert require_uuid(_A_UUID) == _A_UUID
    assert require_uuid(_A_UUID.upper()) == _A_UUID
    assert require_uuid(f"  {_A_UUID}  ") == _A_UUID


@pytest.mark.parametrize("ref", ["1", "2", "66", "007"])
def test_refuses_bare_positional_id(ref):
    with pytest.raises(TaskRefError) as caught:
        require_uuid(ref)
    assert caught.value.kind == "numeric"


@pytest.mark.parametrize("ref", ["2,5", "3-7", "1,4-6"])
def test_refuses_positional_id_sets_and_ranges(ref):
    with pytest.raises(TaskRefError) as caught:
        require_uuid(ref)
    assert caught.value.kind == "numeric_set"


@pytest.mark.parametrize(
    "ref",
    [
        "276c477e",                              # the prefix Taskwarrior accepts
        "276c477e-9f8e",
        _A_UUID[:-1],                            # one hex short of canonical
    ],
)
def test_refuses_uuid_prefixes(ref):
    """A prefix is refused, and this is the load-bearing case.

    Taskwarrior resolves unique uuid prefixes, and a positional id is itself a
    valid hex string — so accepting prefixes would quietly re-admit exactly the
    reference class the command exists to reject.
    """
    with pytest.raises(TaskRefError) as caught:
        require_uuid(ref)
    assert caught.value.kind == "partial"


@pytest.mark.parametrize("ref", ["", "all", "+DIGIT", "project:DIGIT", "latest"])
def test_refuses_filters_and_junk(ref):
    with pytest.raises(TaskRefError) as caught:
        require_uuid(ref)
    assert caught.value.kind == "malformed"


def test_refusal_explains_the_instability_not_just_the_format():
    """The message has to say *why*, or the next agent retries with the id."""
    with pytest.raises(TaskRefError) as caught:
        require_uuid("2")
    message = str(caught.value).lower()
    assert "renumber" in message
    assert "digit tasks list" in message


# --------------------------------------------------------------------------
# Why the rule exists — proven against a real database
# --------------------------------------------------------------------------


def test_positional_ids_shift_while_uuids_do_not(tracker: Tracker):
    """Closing one task renumbers the tasks after it; uuids are unaffected.

    This is the failure the command prevents: an agent lists tasks, decides to
    close the second one, and by the time it acts the number has moved onto a
    task it never read.
    """
    first = tracker.add("первая", project="TEST")
    second = tracker.add("вторая", project="TEST")
    third = tracker.add("третья", project="TEST")

    def positional_ids() -> dict:
        return {
            t["uuid"]: t["id"]
            for t in tracker.export(["status:pending"])
        }

    before = positional_ids()
    assert before[first] < before[second] < before[third]
    second_was = before[second]

    tracker.done(first)
    after = positional_ids()

    # The uuids of the survivors are unchanged and still resolve.
    assert set(after) == {second, third}
    assert tracker.get(second) is not None
    assert tracker.get(third) is not None

    # But the positional id that pointed at `second` now points elsewhere:
    # either at a different task, or at nothing.
    assert after[second] != second_was
    now_at_old_number = [u for u, i in after.items() if i == second_was]
    assert now_at_old_number != [second]


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------


def test_add_returns_a_uuid_that_resolves(tracker: Tracker):
    created = tracker.add("проверить прогоном", project="DIGIT", tags=["agent"])
    assert require_uuid(created) == created

    stored = tracker.get(created)
    assert stored is not None
    assert stored["description"] == "проверить прогоном"
    assert stored["project"] == "DIGIT"
    assert stored["status"] == "pending"


def test_add_refuses_an_empty_description(tracker: Tracker):
    with pytest.raises(TrackerError):
        tracker.add("   ")


def test_annotate_appends_without_losing_earlier_notes(tracker: Tracker):
    created = tracker.add("задача с историей")
    tracker.annotate(created, "первое доказательство")
    after = tracker.annotate(created, "второе доказательство")

    notes = [a["description"] for a in after["annotations"]]
    assert notes == ["первое доказательство", "второе доказательство"]


def test_done_records_the_note_before_closing(tracker: Tracker):
    created = tracker.add("закрыть с доказательством")
    closed = tracker.done(created, note="доказано прогоном")

    assert closed["status"] == "completed"
    assert [a["description"] for a in closed["annotations"]] == ["доказано прогоном"]


def test_done_twice_is_refused(tracker: Tracker):
    created = tracker.add("однократное закрытие")
    tracker.done(created)
    with pytest.raises(TrackerError, match="already completed"):
        tracker.done(created)


# --------------------------------------------------------------------------
# Taskwarrior's silent no-op
# --------------------------------------------------------------------------


def test_unknown_uuid_fails_silently_at_the_transport(tracker: Tracker):
    """A mutation on a missing uuid exits non-zero but says nothing.

    ``rc.verbose=nothing`` is required to keep ``export`` parseable, and it also
    silences failure text. So the exit code alone cannot tell a caller which
    uuid was wrong or which database was consulted — which is why the wrapper
    checks existence up front instead of forwarding a bare code.
    """
    tracker.add("что-то есть")  # a non-empty database: the realistic case
    absent = "11111111-2222-4333-8444-555555555555"
    assert tracker.get(absent) is None

    code, out, err = tracker._run([absent, "done"])
    assert code != 0
    assert (out.strip(), err.strip()) == ("", "")


def test_unknown_uuid_is_reported_with_the_uuid_and_the_database(tracker: Tracker):
    absent = "11111111-2222-4333-8444-555555555555"
    with pytest.raises(TrackerError, match="no task with uuid"):
        tracker.done(absent)
    with pytest.raises(TrackerError, match="no task with uuid"):
        tracker.annotate(absent, "нечего аннотировать")


def test_a_write_that_cannot_land_is_not_reported_as_success(tracker: Tracker):
    """The read-back guard, exercised through a real failure.

    A tracker directory that has gone read-only under us — the shared database
    lives in a group-writable tree, so this is a live possibility — must produce
    an error, not a cheerful "completed".
    """
    created = tracker.add("не закроется")
    files = sorted(tracker.data_dir.iterdir())
    for path in files:
        path.chmod(0o400)
    try:
        with pytest.raises(TrackerError) as caught:
            tracker.done(created)
    finally:
        for path in files:
            path.chmod(0o600)

    # Taskwarrior's own explanation is repeated rather than replaced with a
    # guess: without it the caller is told only that nothing happened.
    assert "permission" in str(caught.value).lower()

    # And the task is genuinely still open, so the error was not a false alarm.
    assert tracker.get(created)["status"] == "pending"


def test_export_of_a_missing_uuid_is_a_successful_empty_result(tracker: Tracker):
    """The other half of why exit codes are not a uniform signal here.

    A query for a task that does not exist is a *successful* query with no
    rows — so "non-zero means absent" does not hold across reads and writes.
    """
    tracker.add("что-то есть")
    absent = "11111111-2222-4333-8444-555555555555"
    code, _out, _err = tracker._run([absent, "export"])
    assert code == 0
    assert tracker.export([absent]) == []


def test_export_of_an_empty_database_is_not_an_error(tracker: Tracker):
    assert tracker.export(["status:pending"]) == []


# --------------------------------------------------------------------------
# Locating the tracker
# --------------------------------------------------------------------------


def test_finds_the_tracker_by_walking_up_from_the_working_directory(tmp_path: Path):
    data = tmp_path / ".digitable-tasks" / "data"
    data.mkdir(parents=True)
    deep = tmp_path / "digit" / "digit_cli" / "nested"
    deep.mkdir(parents=True)

    assert find_tracker(start=deep, environ={}) == data


def test_taskdata_is_honoured_when_no_tracker_is_nearby(tmp_path: Path):
    """Taskwarrior's own env contract, not a new DIGIT_* knob."""
    elsewhere = tmp_path / "somewhere" / "data"
    elsewhere.mkdir(parents=True)
    empty = tmp_path / "empty"
    empty.mkdir()

    found = find_tracker(start=empty, environ={"TASKDATA": str(elsewhere)})
    assert found == elsewhere


def test_explicit_data_dir_wins_over_config_and_environment(tmp_path: Path):
    wanted = tmp_path / "wanted"
    wanted.mkdir()
    other = tmp_path / "other"
    other.mkdir()

    found = find_tracker(
        str(wanted),
        start=tmp_path,
        environ={"TASKDATA": str(other)},
        config={"tasks": {"data_dir": str(other)}},
    )
    assert found == wanted


def test_config_data_dir_wins_over_environment(tmp_path: Path):
    configured = tmp_path / "configured"
    configured.mkdir()
    env_dir = tmp_path / "from-env"
    env_dir.mkdir()

    found = find_tracker(
        start=tmp_path,
        environ={"TASKDATA": str(env_dir)},
        config={"tasks": {"data_dir": str(configured)}},
    )
    assert found == configured


def test_missing_tracker_names_the_directory_it_looked_for(tmp_path: Path):
    empty = tmp_path / "no-tracker-here"
    empty.mkdir()
    with pytest.raises(TrackerNotFound, match=r"\.digitable-tasks"):
        find_tracker(start=empty, environ={})


# --------------------------------------------------------------------------
# Command surface
# --------------------------------------------------------------------------


class _Args:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_refused_reference_exits_two_not_one(tracker: Tracker, capsys):
    """A distinct exit code so a caller can tell "wrong kind of reference"
    apart from "the tracker is unreachable" without matching on prose."""
    code = tasks_command(_Args(
        tasks_command="done", data_dir=str(tracker.data_dir), uuid="2", note=None,
    ))
    assert code == 2
    assert "refused" in capsys.readouterr().err


def test_unreachable_tracker_exits_one(tmp_path: Path, capsys):
    code = tasks_command(_Args(
        tasks_command="list", data_dir=str(tmp_path / "absent"),
        project=None, status="pending", json=False,
    ))
    assert code == 1
    assert "error" in capsys.readouterr().err


def test_list_prints_uuids_and_no_positional_ids(tracker: Tracker, capsys):
    created = tracker.add("видно по uuid", project="DIGIT")
    code = tasks_command(_Args(
        tasks_command="list", data_dir=str(tracker.data_dir),
        project="DIGIT", status="pending", json=False,
    ))
    assert code == 0

    out = capsys.readouterr().out
    assert created in out

    # The positional id must not be offered anywhere on the task's own row —
    # printing it is what teaches an agent to reach for the unstable reference.
    # Asserted against the row, not the whole page, because the trailing count
    # legitimately contains digits.
    stored = tracker.get(created)
    row = next(line for line in out.splitlines() if created in line)
    assert row.split() == [created, "DIGIT", "видно", "по", "uuid"]
    assert str(stored["id"]) not in row.split()


def test_list_json_carries_the_uuid(tracker: Tracker, capsys):
    created = tracker.add("машиночитаемо", project="DIGIT")
    tasks_command(_Args(
        tasks_command="list", data_dir=str(tracker.data_dir),
        project="DIGIT", status="pending", json=True,
    ))
    payload = json.loads(capsys.readouterr().out)
    assert [t["uuid"] for t in payload] == [created]


def test_add_then_done_through_the_command_surface(tracker: Tracker, capsys):
    tasks_command(_Args(
        tasks_command="add", data_dir=str(tracker.data_dir),
        description=["сквозной", "прогон"], project="DIGIT", tag=[],
        priority=None, json=True,
    ))
    created = json.loads(capsys.readouterr().out)["uuid"]

    code = tasks_command(_Args(
        tasks_command="done", data_dir=str(tracker.data_dir),
        uuid=created, note=["доказано", "прогоном"],
    ))
    assert code == 0

    stored = tracker.get(created)
    assert stored["status"] == "completed"
    assert stored["description"] == "сквозной прогон"
    assert [a["description"] for a in stored["annotations"]] == ["доказано прогоном"]


def test_unknown_subcommand_is_rejected(capsys):
    assert tasks_command(_Args(tasks_command="obliterate")) == 1
    assert "Unknown tasks subcommand" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def test_render_list_leads_with_the_uuid_column():
    rendered = render_list([
        {"uuid": _A_UUID, "project": "DIGIT", "description": "что-то", "id": 7},
    ])
    header, _divider, row = rendered.splitlines()[:3]
    assert header.split() == ["uuid", "project", "description"]
    assert row.strip().startswith(_A_UUID)


def test_render_list_says_how_to_close_a_task():
    rendered = render_list([
        {"uuid": _A_UUID, "project": "DIGIT", "description": "что-то"},
    ])
    assert "digit tasks done <uuid>" in rendered


def test_render_list_handles_an_empty_result():
    assert "no matching tasks" in render_list([])


def test_bare_invocation_lists_instead_of_crashing(tracker: Tracker, capsys):
    """``digit tasks`` без подкоманды обязан показать список.

    Подкоманда необязательна, и обработчик по умолчанию — ``list``, но при
    разборе без подпарсера флаги, объявленные только у ``list``
    (``--project``, ``--status``, ``--json``), в пространстве имён
    отсутствовали, и обработчик падал на первом же обращении к ним. Ломался при
    этом самый естественный для человека вызов — тот, которым он открывает
    список.

    Парсер здесь настоящий: самодельный namespace несёт как раз те атрибуты,
    отсутствие которых и было ошибкой, поэтому подделка ничего бы не поймала.
    """
    import argparse

    from digit_cli.tasks_cli import add_parser

    tracker.add("задача, которую видно в списке", project="DIGIT")

    root = argparse.ArgumentParser()
    add_parser(root.add_subparsers(dest="command"))
    args = root.parse_args(["tasks", "--data-dir", str(tracker.data_dir)])

    assert args.func(args) == 0
    assert "задача, которую видно в списке" in capsys.readouterr().out
