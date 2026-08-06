#!/usr/bin/env python3
"""Read and revise an existing ``.excalidraw`` file as data.

The skill's original workflow is one-way: the agent writes a whole file. That is
fine for a diagram nobody has touched, and wrong for the loop this script exists
for — the owner sketches rough shapes, the agent tidies them, the owner keeps
editing. In that loop the file is a *shared* document, and the agent is the
participant most likely to destroy it, because most of what is in the file is
not something the agent wrote.

Three things go wrong when an agent rewrites an Excalidraw file wholesale, and
all three are silent:

**Fields it does not know about.** An element carries ``seed``, ``versionNonce``,
``groupIds``, ``boundElements``, ``frameId``, ``link``, ``customData`` and
whatever the current app version added last month. Re-emitting "the same"
element from an understood subset drops the rest.

**Bindings.** Arrows attach to shapes through ``startBinding`` / ``endBinding``
on the arrow and ``boundElements`` on the shape; text inside a shape uses
``containerId``. Delete a shape and leave the arrow pointing at its id and
Excalidraw drops the arrow without a word.

**Change tracking.** Excalidraw reconciles edits with ``version``,
``versionNonce`` and ``updated``. An element changed without bumping those may
lose to a stale copy in an open tab — the owner watches the agent's edit revert
itself.

So this script edits in place, by element id, and touches nothing else. Untouched
elements come out of the file exactly as they went in, key order included.

Usage
-----
    revise.py inspect DIAGRAM.excalidraw
    revise.py apply DIAGRAM.excalidraw --edits EDITS.json [--output OUT]
    revise.py apply DIAGRAM.excalidraw --edits - < EDITS.json

``inspect`` prints one line per element — enough to decide what to change
without reading the whole file into the conversation.

``--edits`` takes a JSON list of operations::

    [{"id": "r1", "set": {"x": 120, "backgroundColor": "#a5d8ff"}},
     {"id": "t1", "set": {"text": "Новый заголовок"}},
     {"add": {"type": "rectangle", "id": "r9", "x": 0, "y": 0,
              "width": 100, "height": 40}},
     {"delete": "r7"}]

Exit codes: 0 applied, 1 refused (with the reason), 2 bad arguments.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: Fields through which one element refers to another. A delete has to consider
#: every one of them or it leaves a dangling reference.
_REF_TO_ONE = ("containerId", "frameId")
_REF_BINDING = ("startBinding", "endBinding")
_REF_MANY = ("boundElements",)


class Refused(RuntimeError):
    """The edit was not applied, and the reason is worth printing."""


# --------------------------------------------------------------------------
# Load / save
# --------------------------------------------------------------------------


def load(path: Path) -> Dict[str, Any]:
    """Parse a ``.excalidraw`` document, preserving key order.

    ``json.load`` keeps insertion order in dicts, which is what lets an
    untouched element round-trip byte-for-byte on the way out.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Refused(f"cannot read {path}: {exc}") from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Refused(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise Refused(f"{path} is not an Excalidraw document (expected an object)")
    if not isinstance(document.get("elements"), list):
        raise Refused(f"{path} has no 'elements' array")
    return document


def save(document: Dict[str, Any], path: Path) -> None:
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# Inspect
# --------------------------------------------------------------------------

#: Element keys this script understands well enough to describe. Everything else
#: is counted, not interpreted — and the count is the point: it tells the reader
#: how much of the element would be lost by rewriting it from scratch.
_DESCRIBED = {
    "id", "type", "x", "y", "width", "height", "text", "label",
    "strokeColor", "backgroundColor",
}


def describe(element: Dict[str, Any]) -> str:
    text = element.get("text")
    if not text and isinstance(element.get("label"), dict):
        text = element["label"].get("text")
    opaque = [k for k in element if k not in _DESCRIBED]
    label = f" {text!r}" if text else ""
    return (
        f"{str(element.get('id', '?')):16} {str(element.get('type', '?')):12}"
        f" x={_num(element.get('x'))!s:>7} y={_num(element.get('y'))!s:>7}"
        f" w={_num(element.get('width'))!s:>6} h={_num(element.get('height'))!s:>6}"
        f" +{len(opaque)} other field(s){label}"
    )


def _num(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def references(document: Dict[str, Any]) -> Dict[str, List[str]]:
    """Map element id → ids of elements that refer to it."""
    incoming: Dict[str, List[str]] = {}

    def note(target: Any, source: Any) -> None:
        if isinstance(target, str) and target:
            incoming.setdefault(target, []).append(str(source))

    for element in document["elements"]:
        if not isinstance(element, dict):
            continue
        source = element.get("id")
        for key in _REF_TO_ONE:
            note(element.get(key), source)
        for key in _REF_BINDING:
            binding = element.get(key)
            if isinstance(binding, dict):
                note(binding.get("elementId"), source)
        for key in _REF_MANY:
            bound = element.get(key)
            if isinstance(bound, list):
                for entry in bound:
                    if isinstance(entry, dict):
                        note(entry.get("id"), source)
    return incoming


# --------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------


def _bump(element: Dict[str, Any]) -> None:
    """Advance Excalidraw's own change-tracking fields on an edited element.

    Set only when the field is already present: adding ``version`` to an element
    that never had one is a change of shape, not of content.
    """
    if "version" in element and isinstance(element["version"], int):
        element["version"] += 1
    if "versionNonce" in element:
        element["versionNonce"] = random.randint(0, 2**31 - 1)
    if "updated" in element:
        element["updated"] = int(time.time() * 1000)


def _strip_refs_to(document: Dict[str, Any], gone: str) -> None:
    """Remove references to a deleted element, so nothing dangles."""
    for element in document["elements"]:
        if not isinstance(element, dict):
            continue
        touched = False
        for key in _REF_TO_ONE:
            if element.get(key) == gone:
                element[key] = None
                touched = True
        for key in _REF_BINDING:
            binding = element.get(key)
            if isinstance(binding, dict) and binding.get("elementId") == gone:
                element[key] = None
                touched = True
        for key in _REF_MANY:
            bound = element.get(key)
            if isinstance(bound, list):
                kept = [
                    entry for entry in bound
                    if not (isinstance(entry, dict) and entry.get("id") == gone)
                ]
                if len(kept) != len(bound):
                    element[key] = kept
                    touched = True
        if touched:
            _bump(element)


def apply_edits(document: Dict[str, Any], edits: Iterable[Dict[str, Any]],
                *, force: bool = False) -> List[str]:
    """Apply edits in place. Returns a line per change, for reporting."""
    elements: List[Any] = document["elements"]
    by_id = {
        e["id"]: e for e in elements
        if isinstance(e, dict) and isinstance(e.get("id"), str)
    }
    report: List[str] = []

    for edit in edits:
        if not isinstance(edit, dict):
            raise Refused(f"each edit must be an object, got {type(edit).__name__}")

        if "add" in edit:
            new = edit["add"]
            if not isinstance(new, dict) or not isinstance(new.get("id"), str):
                raise Refused("'add' needs an element object with a string id")
            if new["id"] in by_id:
                raise Refused(f"cannot add {new['id']}: that id already exists")
            missing = [k for k in ("type", "x", "y") if k not in new]
            if missing:
                raise Refused(
                    f"cannot add {new['id']}: missing required field(s) "
                    f"{', '.join(missing)}"
                )
            elements.append(new)
            by_id[new["id"]] = new
            report.append(f"added {new['id']} ({new.get('type')})")
            continue

        if "delete" in edit:
            target = edit["delete"]
            if target not in by_id:
                raise Refused(f"cannot delete {target}: no element with that id")
            pointing = references(document).get(target, [])
            if pointing and not force:
                raise Refused(
                    f"cannot delete {target}: {len(pointing)} element(s) refer to "
                    f"it ({', '.join(sorted(set(pointing)))}). Deleting it leaves a "
                    f"dangling reference and Excalidraw drops the arrow or label "
                    f"without reporting anything. Re-run with --force to delete it "
                    f"and clean those references, or edit them first."
                )
            _strip_refs_to(document, target)
            elements[:] = [
                e for e in elements
                if not (isinstance(e, dict) and e.get("id") == target)
            ]
            del by_id[target]
            report.append(
                f"deleted {target}"
                + (f" and cleaned {len(pointing)} reference(s)" if pointing else "")
            )
            continue

        target_id = edit.get("id")
        changes = edit.get("set")
        if not isinstance(target_id, str) or not isinstance(changes, dict):
            raise Refused(
                "an edit must be {'id': ..., 'set': {...}}, {'add': {...}} or "
                "{'delete': id}"
            )
        element = by_id.get(target_id)
        if element is None:
            raise Refused(f"no element with id {target_id}")
        if "id" in changes and changes["id"] != target_id:
            raise Refused(
                f"refusing to change the id of {target_id}: every reference to it "
                f"elsewhere in the file would dangle"
            )
        element.update(changes)
        _bump(element)
        report.append(f"set {', '.join(sorted(changes))} on {target_id}")

    return report


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _read_edits(source: str) -> List[Dict[str, Any]]:
    text = sys.stdin.read() if source == "-" else Path(source).read_text(
        encoding="utf-8")
    try:
        edits = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Refused(f"edits are not valid JSON: {exc}") from exc
    if isinstance(edits, dict):
        edits = [edits]
    if not isinstance(edits, list):
        raise Refused("edits must be a JSON list of operations")
    return edits


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser(
        "inspect", help="List elements without loading the whole file")
    p_inspect.add_argument("path", type=Path)

    p_apply = sub.add_parser("apply", help="Apply edits by element id")
    p_apply.add_argument("path", type=Path)
    p_apply.add_argument("--edits", required=True,
                         help="path to a JSON list of edits, or - for stdin")
    p_apply.add_argument("--output", type=Path, default=None,
                         help="write here instead of editing in place")
    p_apply.add_argument("--force", action="store_true",
                         help="allow deleting an element others refer to, "
                              "cleaning those references")

    args = parser.parse_args(argv)

    try:
        document = load(args.path)
        if args.command == "inspect":
            incoming = references(document)
            print(f"  {len(document['elements'])} element(s) in {args.path}")
            for element in document["elements"]:
                if not isinstance(element, dict):
                    continue
                line = describe(element)
                pointing = incoming.get(element.get("id"), [])
                if pointing:
                    line += f"  ← {len(pointing)} ref(s)"
                print(f"  {line}")
            for key in ("files", "appState"):
                if key in document:
                    size = len(document[key]) if hasattr(document[key], "__len__") else 1
                    print(f"  (document also carries {key}: {size} entry/entries)")
            return 0

        report = apply_edits(document, _read_edits(args.edits), force=args.force)
        save(document, args.output or args.path)
        for line in report:
            print(f"  {line}")
        print(f"  wrote {args.output or args.path}")
        return 0
    except Refused as exc:
        print(f"  refused: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"  error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
