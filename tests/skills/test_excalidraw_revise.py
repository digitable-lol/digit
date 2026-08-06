"""Contracts for two-way ``.excalidraw`` editing.

The file is a shared document: the owner sketches, the agent tidies, the owner
keeps editing. Every test here is about the agent not destroying what it did not
write — unknown fields, bindings, and Excalidraw's own change tracking.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "skills/creative/excalidraw/scripts/revise.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("excalidraw_revise", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


revise = _load_module()


def _document() -> dict:
    """A document shaped like one the app writes: a shape, its label, and an
    arrow bound to it, each carrying fields an agent has no reason to know."""
    return {
        "type": "excalidraw",
        "version": 2,
        "source": "https://excalidraw.com",
        "elements": [
            {
                "id": "box1", "type": "rectangle", "x": 100, "y": 100,
                "width": 200, "height": 100,
                "strokeColor": "#1e1e1e", "backgroundColor": "transparent",
                "seed": 1968410350, "versionNonce": 1150084233, "version": 42,
                "updated": 1754400000000, "groupIds": ["g1"], "frameId": None,
                "roundness": {"type": 3}, "link": None, "locked": False,
                "customData": {"ownerNote": "нарисовано вручную"},
                "boundElements": [
                    {"id": "label1", "type": "text"},
                    {"id": "arrow1", "type": "arrow"},
                ],
            },
            {
                "id": "label1", "type": "text", "x": 110, "y": 140,
                "width": 180, "height": 25, "text": "черновик",
                "containerId": "box1", "fontSize": 20, "fontFamily": 1,
                "seed": 55, "versionNonce": 66, "version": 3,
                "updated": 1754400000001, "textAlign": "center",
            },
            {
                "id": "arrow1", "type": "arrow", "x": 320, "y": 150,
                "width": 120, "height": 0,
                "points": [[0, 0], [120, 0]],
                "startBinding": {"elementId": "box1", "focus": 0, "gap": 4},
                "endBinding": None,
                "seed": 77, "versionNonce": 88, "version": 5,
                "updated": 1754400000002,
            },
            {
                # Nothing binds to this one — the only element that can be
                # deleted without cleaning up after it.
                "id": "free1", "type": "ellipse", "x": 600, "y": 100,
                "width": 60, "height": 60,
                "seed": 99, "versionNonce": 111, "version": 1,
                "updated": 1754400000003,
            },
        ],
        "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
        "files": {"img1": {"mimeType": "image/png", "dataURL": "data:..."}},
    }


@pytest.fixture
def diagram(tmp_path: Path) -> Path:
    path = tmp_path / "d.excalidraw"
    path.write_text(json.dumps(_document(), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _by_id(document: dict, element_id: str) -> dict:
    return next(e for e in document["elements"] if e["id"] == element_id)


# --------------------------------------------------------------------------
# Fidelity
# --------------------------------------------------------------------------


def test_editing_one_element_leaves_the_others_byte_identical(diagram, tmp_path):
    edits = tmp_path / "e.json"
    edits.write_text(json.dumps([{"id": "label1", "set": {"text": "готово"}}]),
                     encoding="utf-8")
    before = _read(diagram)

    assert revise.main(["apply", str(diagram), "--edits", str(edits)]) == 0

    after = _read(diagram)
    for element_id in ("box1", "arrow1"):
        assert _by_id(after, element_id) == _by_id(before, element_id)
        # Key order too: a reordered element is a rewritten element.
        assert (list(_by_id(after, element_id))
                == list(_by_id(before, element_id)))


def test_unknown_fields_survive_on_the_edited_element(diagram, tmp_path):
    """The agent knows nothing about customData or groupIds; they must stay."""
    edits = tmp_path / "e.json"
    edits.write_text(json.dumps([{"id": "box1", "set": {"x": 140}}]),
                     encoding="utf-8")

    assert revise.main(["apply", str(diagram), "--edits", str(edits)]) == 0

    box = _by_id(_read(diagram), "box1")
    assert box["x"] == 140
    assert box["customData"] == {"ownerNote": "нарисовано вручную"}
    assert box["groupIds"] == ["g1"]
    assert box["roundness"] == {"type": 3}
    assert box["seed"] == 1968410350  # not regenerated


def test_document_level_keys_survive(diagram, tmp_path):
    """``files`` carries embedded images and ``appState`` the canvas settings.
    A wholesale rewrite is how those vanish."""
    edits = tmp_path / "e.json"
    edits.write_text(json.dumps([{"id": "box1", "set": {"x": 140}}]),
                     encoding="utf-8")
    before = _read(diagram)

    revise.main(["apply", str(diagram), "--edits", str(edits)])

    after = _read(diagram)
    assert after["files"] == before["files"]
    assert after["appState"] == before["appState"]
    assert after["source"] == before["source"]


# --------------------------------------------------------------------------
# Change tracking
# --------------------------------------------------------------------------


def test_an_edited_element_advances_excalidraw_change_tracking(diagram, tmp_path):
    """Without this the owner's open tab can revert the agent's edit."""
    edits = tmp_path / "e.json"
    edits.write_text(json.dumps([{"id": "label1", "set": {"text": "готово"}}]),
                     encoding="utf-8")
    before = _by_id(_read(diagram), "label1")

    revise.main(["apply", str(diagram), "--edits", str(edits)])

    after = _by_id(_read(diagram), "label1")
    assert after["version"] == before["version"] + 1
    assert after["versionNonce"] != before["versionNonce"]
    assert after["updated"] > before["updated"]


def test_change_tracking_is_not_invented_where_it_was_absent(tmp_path):
    """Adding ``version`` to an element that never had one changes its shape."""
    path = tmp_path / "d.excalidraw"
    path.write_text(json.dumps({
        "type": "excalidraw", "version": 2, "elements": [
            {"id": "r1", "type": "rectangle", "x": 0, "y": 0,
             "width": 10, "height": 10},
        ],
    }), encoding="utf-8")
    edits = tmp_path / "e.json"
    edits.write_text(json.dumps([{"id": "r1", "set": {"x": 5}}]), encoding="utf-8")

    revise.main(["apply", str(path), "--edits", str(edits)])

    element = _by_id(_read(path), "r1")
    assert element["x"] == 5
    assert "version" not in element
    assert "versionNonce" not in element


# --------------------------------------------------------------------------
# Bindings
# --------------------------------------------------------------------------


def test_deleting_a_bound_shape_is_refused_by_default(diagram, tmp_path, capsys):
    """The silent failure this guards: Excalidraw drops an arrow whose binding
    points at a missing element, and says nothing about it."""
    edits = tmp_path / "e.json"
    edits.write_text(json.dumps([{"delete": "box1"}]), encoding="utf-8")

    assert revise.main(["apply", str(diagram), "--edits", str(edits)]) == 1

    err = capsys.readouterr().err
    assert "dangling" in err
    assert "arrow1" in err and "label1" in err
    # Nothing was written.
    assert _by_id(_read(diagram), "box1")


def test_forced_delete_cleans_every_kind_of_reference(diagram, tmp_path):
    edits = tmp_path / "e.json"
    edits.write_text(json.dumps([{"delete": "box1"}]), encoding="utf-8")

    assert revise.main(["apply", str(diagram), "--edits", str(edits),
                        "--force"]) == 0

    after = _read(diagram)
    assert [e["id"] for e in after["elements"]] == ["label1", "arrow1", "free1"]
    # containerId, startBinding and boundElements all cleaned.
    assert _by_id(after, "label1")["containerId"] is None
    assert _by_id(after, "arrow1")["startBinding"] is None
    for element in after["elements"]:
        for entry in element.get("boundElements") or []:
            assert entry["id"] != "box1"


def test_deleting_an_unreferenced_element_needs_no_force(diagram, tmp_path):
    edits = tmp_path / "e.json"
    edits.write_text(json.dumps([{"delete": "free1"}]), encoding="utf-8")

    assert revise.main(["apply", str(diagram), "--edits", str(edits)]) == 0
    assert [e["id"] for e in _read(diagram)["elements"]] == [
        "box1", "label1", "arrow1"]


def test_changing_an_id_is_refused(diagram, tmp_path, capsys):
    edits = tmp_path / "e.json"
    edits.write_text(json.dumps([{"id": "box1", "set": {"id": "box2"}}]),
                     encoding="utf-8")

    assert revise.main(["apply", str(diagram), "--edits", str(edits)]) == 1
    assert "dangle" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Adding
# --------------------------------------------------------------------------


def test_add_appends_without_disturbing_existing_elements(diagram, tmp_path):
    edits = tmp_path / "e.json"
    edits.write_text(json.dumps([{"add": {
        "id": "new1", "type": "ellipse", "x": 500, "y": 500,
        "width": 80, "height": 80,
    }}]), encoding="utf-8")
    before = _read(diagram)

    assert revise.main(["apply", str(diagram), "--edits", str(edits)]) == 0

    after = _read(diagram)
    existing = [e["id"] for e in before["elements"]]
    assert [e["id"] for e in after["elements"]] == [*existing, "new1"]
    for element_id in existing:
        assert _by_id(after, element_id) == _by_id(before, element_id)


def test_add_with_a_duplicate_id_is_refused(diagram, tmp_path, capsys):
    edits = tmp_path / "e.json"
    edits.write_text(json.dumps([{"add": {
        "id": "box1", "type": "ellipse", "x": 0, "y": 0,
    }}]), encoding="utf-8")

    assert revise.main(["apply", str(diagram), "--edits", str(edits)]) == 1
    assert "already exists" in capsys.readouterr().err


def test_add_without_required_fields_is_refused(diagram, tmp_path, capsys):
    edits = tmp_path / "e.json"
    edits.write_text(json.dumps([{"add": {"id": "new1", "type": "ellipse"}}]),
                     encoding="utf-8")

    assert revise.main(["apply", str(diagram), "--edits", str(edits)]) == 1
    assert "missing required field" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Inspect
# --------------------------------------------------------------------------


def test_inspect_reports_elements_and_how_much_it_does_not_interpret(diagram, capsys):
    """The count of uninterpreted fields is the warning: it is how much a
    from-scratch rewrite would throw away."""
    assert revise.main(["inspect", str(diagram)]) == 0

    out = capsys.readouterr().out
    assert "box1" in out and "label1" in out and "arrow1" in out
    assert "other field(s)" in out
    assert "черновик" in out
    assert "ref(s)" in out          # box1 is referred to
    assert "files" in out           # embedded images are announced


def test_a_broken_file_is_refused_with_the_reason(tmp_path, capsys):
    path = tmp_path / "bad.excalidraw"
    path.write_text("{not json", encoding="utf-8")
    assert revise.main(["inspect", str(path)]) == 1
    assert "not valid JSON" in capsys.readouterr().err


def test_a_document_without_elements_is_refused(tmp_path, capsys):
    path = tmp_path / "bad.excalidraw"
    path.write_text('{"type": "excalidraw"}', encoding="utf-8")
    assert revise.main(["inspect", str(path)]) == 1
    assert "no 'elements' array" in capsys.readouterr().err


def test_output_flag_leaves_the_original_untouched(diagram, tmp_path):
    edits = tmp_path / "e.json"
    edits.write_text(json.dumps([{"id": "box1", "set": {"x": 999}}]),
                     encoding="utf-8")
    out = tmp_path / "revised.excalidraw"
    before = _read(diagram)

    assert revise.main(["apply", str(diagram), "--edits", str(edits),
                        "--output", str(out)]) == 0

    assert _read(diagram) == before
    assert _by_id(_read(out), "box1")["x"] == 999


# --------------------------------------------------------------------------
# What the model is told
# --------------------------------------------------------------------------


def test_the_webui_hint_does_not_promise_a_preview_that_cannot_render():
    """No Excalidraw renderer exists in web/, ui-tui/ or apps/desktop/.

    The hint used to tell the model that ``.excalidraw`` files "render as rich
    previews" in the WebUI. Acting on that, the model shows a diagram the user
    never sees — worse than handing over a path they can open. Whoever adds a
    renderer should delete this test in the same change that adds the word back.
    """
    from agent.prompt_builder import PLATFORM_HINTS

    assert "excalidraw" not in PLATFORM_HINTS["webui"].lower()
