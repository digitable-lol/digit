"""Contracts for ``digit excalidraw`` — Workbench widgets from the command line.

The command's whole claim is that it draws with the *same* shapes a person drags
onto the canvas by hand. So the tests that matter are the ones that would catch
a second, private set of shapes creeping in, and the ones that catch a drawing
which is technically valid and visually broken.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from digit_cli import excalidraw_cli as widgets

REPO = Path(__file__).resolve().parent.parent.parent

OUTLINE = """
Тема
  Ветвь
    Лист
  Вторая ветвь
Ветвь -> Вторая ветвь: связь
"""


@pytest.fixture()
def skill():
    return widgets._skill()


def _library(skill, widget):
    spec = widgets.WIDGETS[widget]
    try:
        return skill.load_library(skill.find_library(None, name=spec["library"]))
    except skill.Refused:
        pytest.skip(f"набор «{spec['library']}» не найден рядом")


# -- reading the outline ---------------------------------------------------


def test_indent_gives_the_level_whatever_its_width():
    """Two spaces or four must mean the same thing, or the diagram depends on
    the editor it was typed in."""
    two = widgets.parse_outline("A\n  B\n    C\n")[0]
    four = widgets.parse_outline("A\n    B\n        C\n")[0]
    assert [n["level"] for n in two] == [n["level"] for n in four] == [0, 1, 2]


def test_a_link_to_a_shape_that_is_not_there_is_reported():
    _nodes, links, notes = widgets.parse_outline("A\nA -> Б: нет такой\n")
    assert links == []
    assert any("Б" in note for note in notes)


def test_a_repeated_label_is_refused_not_silently_merged():
    """Links are made by label, so two shapes with one label make the link
    ambiguous — and an ambiguous link is drawn to the wrong shape."""
    nodes, _links, notes = widgets.parse_outline("A\nA\n")
    assert len(nodes) == 1
    assert notes


# -- drawing ---------------------------------------------------------------


@pytest.mark.parametrize("widget", sorted(widgets.WIDGETS))
def test_every_widget_draws_and_labels_its_shapes(widget, skill):
    items = _library(skill, widget)
    document, _notes = widgets.build(widget, OUTLINE, items, skill)
    texts = {" ".join(str(e.get("text") or "").split())
             for e in document["elements"]}
    assert {"Тема", "Ветвь", "Лист"} <= texts


@pytest.mark.parametrize("widget", sorted(widgets.WIDGETS))
def test_no_widget_puts_one_shape_on_top_of_another(widget, skill):
    """Shape heights differ between sets — a fixed row step overlaps in some
    of them and leaves holes in others."""
    items = _library(skill, widget)
    document, _notes = widgets.build(widget, OUTLINE, items, skill)
    assert widgets._overlap_report(document, skill) == ""


@pytest.mark.parametrize("widget", sorted(widgets.WIDGETS))
def test_drawing_is_repeatable(widget, skill):
    items = _library(skill, widget)
    first, _ = widgets.build(widget, OUTLINE, items, skill)
    second, _ = widgets.build(widget, OUTLINE, items, skill)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_arrows_are_bound_at_both_ends(skill):
    items = _library(skill, "mindmap")
    document, _notes = widgets.build("mindmap", OUTLINE, items, skill)
    arrows = [e for e in document["elements"] if e.get("type") == "arrow"]
    assert arrows
    for arrow in arrows:
        assert arrow["startBinding"]["elementId"]
        assert arrow["endBinding"]["elementId"]


def test_a_tree_shows_who_belongs_to_whom(skill):
    """Indentation says the branch belongs to the theme; on a tree that is a
    line. Without it a mindmap is three columns of rectangles."""
    items = _library(skill, "mindmap")
    document, _notes = widgets.build("mindmap", OUTLINE, items, skill)
    arrows = [e for e in document["elements"] if e.get("type") == "arrow"]
    assert len(arrows) >= 4  # три родства и одна названная связь


def test_a_lane_label_is_written_even_when_it_is_bound_elsewhere(skill):
    """«Дорожка» keeps its caption on a small tag beside a 1200-point body.
    Looking only inside the biggest shape leaves the library's own word on it."""
    items = _library(skill, "roadmap")
    document, _notes = widgets.build("roadmap", OUTLINE, items, skill)
    texts = {" ".join(str(e.get("text") or "").split())
             for e in document["elements"]}
    assert "Тема" in texts
    assert "Дорожка" not in texts


def test_the_shapes_come_from_the_library_and_not_from_here(skill):
    """Every drawn shape must be traceable to a library element id."""
    items = _library(skill, "c4")
    document, _notes = widgets.build("c4", OUTLINE, items, skill)
    from_library = {
        skill.stable_id(f"c4:{title}", element["id"])
        for title in ("Тема", "Ветвь", "Лист", "Вторая ветвь")
        for name in widgets.WIDGETS["c4"]["levels"]
        for element in items.get(name, ())
    }
    # Стрелки и их подписи рисуются здесь — связи в наборах нет; всё
    # остальное обязано быть выведено из элемента библиотеки.
    shapes = [e for e in document["elements"]
              if e.get("type") in skill.TASK_SHAPES]
    assert shapes
    assert all(e["id"] in from_library for e in shapes)


# -- the command -----------------------------------------------------------


def test_self_test_passes():
    assert widgets.self_test() == 0


def test_the_command_writes_a_file(tmp_path: Path, skill):
    _library(skill, "mindmap")
    source = tmp_path / "схема.txt"
    source.write_text(OUTLINE, encoding="utf-8")
    out = tmp_path / "карта.excalidraw"

    code = widgets.excalidraw_command(argparse.Namespace(
        self_test=False, excalidraw_command="mindmap", text=str(source),
        out=str(out), library=None, palette="carbon"))

    assert code == 0
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["type"] == "excalidraw"
    assert document["elements"]


def test_it_is_registered_as_a_digit_subcommand():
    """A utility the CLI does not expose is a utility nobody runs."""
    main = (REPO / "digit_cli/main.py").read_text(encoding="utf-8")
    assert "from digit_cli.excalidraw_cli import add_parser" in main
