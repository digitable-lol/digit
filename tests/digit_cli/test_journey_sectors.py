"""Behavior contracts for ``digit journey sectors`` (aka ``digit memory-graph sectors``).

The command has to be reachable through the real parser, not a hand-built
namespace: a fake namespace carries exactly the attributes whose absence would
be the bug. It also has to keep the two output contracts the timeline already
honours — forced ANSI for the interactive CLI, plain text when captured.
"""

from __future__ import annotations

import argparse
import contextlib
import io

from digit_constants import reset_digit_home_override, set_digit_home_override

NOTES = "\n§\n".join(
    [
        "# Vector search\n#retrieval HNSW. Related: [[Embeddings]].",
        "# Embeddings\nQwen3. Needed by [[Vector search]].",
        "# Espresso\n#daily The user drinks espresso.",
    ]
)


def _run(tmp_path, argv: list[str], *, force: bool = False) -> tuple[int, str]:
    from digit_cli.journey import register_cli

    home = tmp_path / ".digit"
    (home / "memories").mkdir(parents=True, exist_ok=True)
    (home / "memories" / "MEMORY.md").write_text(NOTES, encoding="utf-8")

    parser = argparse.ArgumentParser(add_help=False)
    register_cli(parser)
    args = parser.parse_args(argv)
    if force:
        args.force_color = True

    buf = io.StringIO()
    token = set_digit_home_override(home)
    try:
        with contextlib.redirect_stdout(buf):
            code = args.func(args)
    finally:
        reset_digit_home_override(token)
    return code, buf.getvalue()


def test_sectors_groups_notes_by_area(tmp_path):
    code, out = _run(tmp_path, ["sectors", "--width", "100"])

    assert code == 0
    assert "retrieval" in out and "daily" in out
    assert "Vector search" in out
    # Заголовок сектора несёт число заметок — без него разбивка нечитаема.
    assert "note↔note links" in out


def test_sectors_can_be_narrowed_to_one_area(tmp_path):
    code, out = _run(tmp_path, ["sectors", "daily", "--width", "100"])

    assert code == 0
    assert "Espresso" in out
    assert "Vector search" not in out


def test_unknown_sector_fails_loudly_instead_of_printing_nothing(tmp_path):
    code, out = _run(tmp_path, ["sectors", "nope"])

    assert code == 1
    assert "nope" in out


def test_sectors_json_is_machine_readable(tmp_path):
    import json

    code, out = _run(tmp_path, ["sectors", "--json"])

    assert code == 0
    payload = json.loads(out)
    assert {s["sector"] for s in payload["sectors"]} == {"retrieval", "daily"}
    assert payload["stats"]["memory_link_edges"] == 2


def test_sectors_honours_the_two_color_contracts(tmp_path):
    assert "\x1b[" in _run(tmp_path, ["sectors"], force=True)[1]
    assert "\x1b[" not in _run(tmp_path, ["sectors"], force=False)[1]
