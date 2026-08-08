"""Contracts for «карта Excalidraw → задачи Taskwarrior».

Two things are being protected here, and they are not the same thing.

The first is the *reading*: a drawing is not a schema, so every rule that turns
a shape into a task has to be pinned down — otherwise the same picture produces
a different backlog next month. These tests are pure and always run.

The second is the *writing*, and it is the dangerous half. The owner and several
agents share one Taskwarrior database. Every test that writes does so into a
temporary directory created by pytest, never into a resolved tracker: a test
that "just checks" against the real database is one bad path away from editing
the owner's backlog.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "skills/creative/excalidraw/scripts/tasks.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("excalidraw_tasks", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tasks = _load_module()

requires_task = pytest.mark.skipif(
    shutil.which("task") is None, reason="taskwarrior не установлен")


@pytest.fixture()
def sandbox(tmp_path: Path):
    """A Taskwarrior database that exists only for this test."""
    data = tmp_path / "data"
    data.mkdir()
    (tmp_path / "taskrc").write_text(f"data.location={data}\n", encoding="utf-8")
    module = tasks._tracker_module()
    return module.Tracker(data)


# -- reading ---------------------------------------------------------------


def test_labelled_shapes_become_tasks():
    plan = tasks.parse_map(tasks._sample_map())
    assert set(plan["nodes"]) == {"box1", "box2"}
    assert plan["nodes"]["box1"]["description"] == "Собрать основу"


def test_arrow_becomes_a_dependency_in_the_direction_it_points():
    """An arrow from A to B means B waits for A, not the other way round."""
    plan = tasks.parse_map(tasks._sample_map())
    assert plan["links"] == [{"from": "box1", "to": "box2"}]


def test_frame_and_group_make_a_two_level_project():
    plan = tasks.parse_map(tasks._sample_map())
    assert plan["nodes"]["box1"]["project"] == "Этап 1-второй.Разбор"


def test_dot_inside_a_frame_name_does_not_add_a_level():
    """A frame called «этап 2.1» must not silently become a third level."""
    document = tasks._sample_map()
    for element in document["elements"]:
        if element["id"] == "frame1":
            element["name"] = "этап 2.1"
    plan = tasks.parse_map(document)
    assert plan["nodes"]["box1"]["project"].count(".") == 1


def test_explicit_mark_beats_the_fill_colour():
    """The mark was typed on this shape; the fill may have been copied in."""
    plan = tasks.parse_map(tasks._sample_map())
    assert plan["nodes"]["box1"]["priority"] == "H"   # from #ffc9c9
    assert plan["nodes"]["box2"]["priority"] == "M"   # from «!M», fill is blue
    assert "!M" not in plan["nodes"]["box2"]["description"]


def test_colour_table_can_be_replaced():
    plan = tasks.parse_map(tasks._sample_map(), colors={"#a5d8ff": "L"})
    assert plan["nodes"]["box1"]["priority"] is None
    assert plan["nodes"]["box2"]["priority"] == "M"   # mark still wins


def test_nothing_is_dropped_in_silence():
    """An unbound arrow looks like a connection and is not one."""
    plan = tasks.parse_map(tasks._sample_map())
    assert any("loose" in note for note in plan["notes"])
    assert any("mute" in note for note in plan["notes"])


def test_deleted_elements_are_not_tasks():
    plan = tasks.parse_map(tasks._sample_map())
    assert "gone" not in plan["nodes"]


def test_uuid_comes_from_the_element_id_not_from_position():
    first = tasks.parse_map(tasks._sample_map())
    shuffled = tasks._sample_map()
    shuffled["elements"].reverse()
    second = tasks.parse_map(shuffled)
    assert first["nodes"]["box1"]["uuid"] == second["nodes"]["box1"]["uuid"]


def test_a_cycle_is_found_before_anything_is_written():
    plan = tasks.parse_map(tasks._sample_map())
    plan["links"].append({"from": "box2", "to": "box1"})
    assert tasks.find_cycle(plan["nodes"], plan["links"])


# -- the flatness requirement ---------------------------------------------


def test_schemas_are_flat_and_offer_no_alternatives():
    """Digit's router model requires it, and «digit-integrations.md» says so."""
    for name, schema in tasks.SCHEMAS.items():
        assert tasks.schema_depth(schema) <= 2, name
        assert tasks.schema_alternatives(schema) == [], name


def test_schema_depth_actually_notices_a_third_level():
    """A check that cannot fail proves nothing."""
    nested = {"type": "object", "properties": {
        "where": {"type": "object", "properties": {"x": {"type": "number"}}}}}
    assert tasks.schema_depth(nested) == 3


# -- writing ---------------------------------------------------------------


@requires_task
def test_the_uda_carries_the_element_id(sandbox):
    tasks.apply_plan(sandbox, tasks.parse_map(tasks._sample_map()))
    written = {t["description"]: t for t in sandbox.export([])}
    assert written["Собрать основу"][tasks.UDA] == "box1"


@requires_task
def test_a_second_run_updates_and_does_not_duplicate(sandbox):
    tasks.apply_plan(sandbox, tasks.parse_map(tasks._sample_map()))
    tasks.apply_plan(sandbox, tasks.parse_map(tasks._sample_map()))
    assert len(sandbox.export([])) == 2


@requires_task
def test_a_second_run_keeps_what_the_utility_did_not_write(sandbox):
    """``task import`` of an existing uuid replaces the record wholesale.

    So the annotation an agent left, and the task's own history, would vanish
    on the next sync — silently. Read-merge-import is the reason it does not.
    """
    tasks.apply_plan(sandbox, tasks.parse_map(tasks._sample_map()))
    box1 = tasks.task_uuid_for("box1")
    sandbox.annotate(box1, "проверено вручную")

    tasks.apply_plan(sandbox, tasks.parse_map(tasks._sample_map()))

    after = sandbox.require(box1)
    assert len(after.get("annotations") or []) == 1


@requires_task
def test_a_cycle_stops_the_write_before_the_first_task(sandbox):
    """Taskwarrior refuses a circular ``depends`` — but only when it reaches it,
    leaving half the dependencies applied. The check has to happen first."""
    plan = tasks.parse_map(tasks._sample_map())
    plan["links"].append({"from": "box2", "to": "box1"})
    plan["cycle"] = tasks.find_cycle(plan["nodes"], plan["links"])
    with pytest.raises(tasks.Refused):
        tasks.apply_plan(sandbox, plan)
    assert sandbox.export([]) == []


# -- the utility as a command ---------------------------------------------


def test_self_test_passes():
    """Every utility in this skill answers ``--self-test``; this one must too."""
    result = subprocess.run(
        ["python3", str(SCRIPT), "--self-test"],
        capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_dry_run_writes_nothing_and_needs_no_database(tmp_path: Path):
    map_path = tmp_path / "карта.excalidraw"
    map_path.write_text(json.dumps(tasks._sample_map(), ensure_ascii=False),
                        encoding="utf-8")
    result = subprocess.run(
        ["python3", str(SCRIPT), "from-map", str(map_path), "--dry-run"],
        capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Собрать основу" in result.stdout
    assert "ничего не записано" in result.stdout
