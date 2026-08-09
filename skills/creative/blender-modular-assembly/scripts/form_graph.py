#!/usr/bin/env python3
"""Create, validate, and advance a modular Blender FormGraph manifest."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
MODULE_STATUSES = {"planned", "building", "built", "validated", "rejected"}
CHECKPOINT_STATUSES = {"pending", "pass", "fail"}
UNITS = {"m", "cm", "mm"}
COORDINATE_SYSTEMS = {"Z_UP", "Y_UP"}
ATTACHMENT_MODES = {"parent", "rigid-skin", "deform", "constraint", "socket", "surface"}
TRANSITIONS = {
    "planned": {"building"},
    "building": {"built", "rejected"},
    "built": {"validated", "rejected", "building"},
    "validated": {"rejected"},
    "rejected": {"building"},
}


class FormGraphError(ValueError):
    """Raised when a FormGraph cannot be safely advanced."""


def load_graph(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FormGraphError(f"manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FormGraphError(f"invalid JSON at line {exc.lineno}: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise FormGraphError("manifest root must be an object")
    return value


def save_graph(path: Path, graph: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(graph, indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _ids(items: Any, label: str, errors: list[str]) -> list[str]:
    if not isinstance(items, list):
        errors.append(f"{label} must be an array")
        return []
    result: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{label}[{index}] must be an object")
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not ID_PATTERN.fullmatch(item_id):
            errors.append(f"{label}[{index}].id is invalid: {item_id!r}")
            continue
        result.append(item_id)
    duplicates = sorted(item for item, count in Counter(result).items() if count > 1)
    if duplicates:
        errors.append(f"duplicate {label} ids: {', '.join(duplicates)}")
    return result


def _cycle_errors(edges: dict[str, set[str]]) -> list[str]:
    errors: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, path: list[str]) -> None:
        if node in visiting:
            start = path.index(node) if node in path else 0
            errors.append("dependency cycle: " + " -> ".join(path[start:] + [node]))
            return
        if node in visited:
            return
        visiting.add(node)
        for dependency in sorted(edges.get(node, set())):
            visit(dependency, path + [node])
        visiting.remove(node)
        visited.add(node)

    for node in sorted(edges):
        visit(node, [])
    return list(dict.fromkeys(errors))


def validation_errors(graph: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if graph.get("schemaVersion") != SCHEMA_VERSION:
        errors.append(f"schemaVersion must be {SCHEMA_VERSION}")
    graph_id = graph.get("id")
    if not isinstance(graph_id, str) or not ID_PATTERN.fullmatch(graph_id):
        errors.append(f"id is invalid: {graph_id!r}")
    if graph.get("units") not in UNITS:
        errors.append(f"units must be one of {sorted(UNITS)}")
    if graph.get("coordinateSystem") not in COORDINATE_SYSTEMS:
        errors.append(f"coordinateSystem must be one of {sorted(COORDINATE_SYSTEMS)}")

    modules = graph.get("modules")
    module_ids = _ids(modules, "modules", errors)
    module_set = set(module_ids)
    root = graph.get("rootModule")
    if root not in module_set:
        errors.append(f"rootModule does not exist: {root!r}")
    edges: dict[str, set[str]] = {module_id: set() for module_id in module_ids}

    for module in modules if isinstance(modules, list) else []:
        if not isinstance(module, dict) or module.get("id") not in module_set:
            continue
        module_id = module["id"]
        status = module.get("status")
        if status not in MODULE_STATUSES:
            errors.append(f"module {module_id}: invalid status {status!r}")
        stage = module.get("stage")
        if not isinstance(stage, int) or stage < 0:
            errors.append(f"module {module_id}: stage must be a non-negative integer")
        parent = module.get("parent")
        if module_id == root and parent is not None:
            errors.append(f"root module {module_id} must have parent null")
        if module_id != root and parent is None:
            errors.append(f"module {module_id}: only rootModule may have parent null")
        if parent is not None:
            if parent not in module_set:
                errors.append(f"module {module_id}: missing parent {parent!r}")
            else:
                edges[module_id].add(parent)
        dependencies = module.get("dependsOn", [])
        if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
            errors.append(f"module {module_id}: dependsOn must be an array of ids")
            dependencies = []
        for dependency in dependencies:
            if dependency not in module_set:
                errors.append(f"module {module_id}: missing dependency {dependency!r}")
            elif dependency == module_id:
                errors.append(f"module {module_id}: cannot depend on itself")
            else:
                edges[module_id].add(dependency)
        attachments = module.get("attachments", [])
        if not isinstance(attachments, list):
            errors.append(f"module {module_id}: attachments must be an array")
            attachments = []
        for index, attachment in enumerate(attachments):
            if not isinstance(attachment, dict):
                errors.append(f"module {module_id}: attachment {index} must be an object")
                continue
            target = attachment.get("to")
            if target not in module_set:
                errors.append(f"module {module_id}: attachment {index} target is missing: {target!r}")
            if attachment.get("mode") not in ATTACHMENT_MODES:
                errors.append(f"module {module_id}: attachment {index} has invalid mode")
            if not isinstance(attachment.get("interface"), str) or not attachment["interface"].strip():
                errors.append(f"module {module_id}: attachment {index} needs an interface")
        acceptance = module.get("acceptance")
        if not isinstance(acceptance, dict):
            errors.append(f"module {module_id}: acceptance must be an object")
    errors.extend(_cycle_errors(edges))

    checkpoints = graph.get("checkpoints")
    checkpoint_ids = _ids(checkpoints, "checkpoints", errors)
    checkpoint_set = set(checkpoint_ids)
    for checkpoint in checkpoints if isinstance(checkpoints, list) else []:
        if not isinstance(checkpoint, dict) or checkpoint.get("id") not in checkpoint_set:
            continue
        checkpoint_id = checkpoint["id"]
        if checkpoint.get("status") not in CHECKPOINT_STATUSES:
            errors.append(f"checkpoint {checkpoint_id}: invalid status")
        after = checkpoint.get("after", [])
        if not isinstance(after, list) or not after:
            errors.append(f"checkpoint {checkpoint_id}: after must contain module ids")
            after = []
        for module_id in after:
            if module_id not in module_set:
                errors.append(f"checkpoint {checkpoint_id}: missing module {module_id!r}")
        views = checkpoint.get("requiredViews", [])
        if not isinstance(views, list) or not views or not all(isinstance(item, str) and item for item in views):
            errors.append(f"checkpoint {checkpoint_id}: requiredViews must contain view names")
    return list(dict.fromkeys(errors))


def require_valid(graph: dict[str, Any]) -> None:
    errors = validation_errors(graph)
    if errors:
        raise FormGraphError("; ".join(errors))


def modules_by_id(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {module["id"]: module for module in graph["modules"]}


def ready_modules(graph: dict[str, Any]) -> list[dict[str, Any]]:
    require_valid(graph)
    modules = modules_by_id(graph)
    ready: list[dict[str, Any]] = []
    for module in graph["modules"]:
        if module["status"] not in {"planned", "rejected"}:
            continue
        if all(modules[item]["status"] in {"built", "validated"} for item in module.get("dependsOn", [])):
            ready.append(module)
    return sorted(ready, key=lambda item: (item["stage"], item["id"]))


def set_module_status(
    graph: dict[str, Any], module_id: str, status: str, note: str | None = None, artifacts: list[str] | None = None
) -> None:
    require_valid(graph)
    modules = modules_by_id(graph)
    if module_id not in modules:
        raise FormGraphError(f"unknown module: {module_id}")
    module = modules[module_id]
    previous = module["status"]
    if status not in TRANSITIONS.get(previous, set()):
        raise FormGraphError(f"invalid transition for {module_id}: {previous} -> {status}")
    if status == "building":
        ready_ids = {item["id"] for item in ready_modules(graph)}
        if module_id not in ready_ids:
            raise FormGraphError(f"module is not dependency-ready: {module_id}")
    if status == "rejected" and not note:
        raise FormGraphError("rejected status requires --note")
    module["status"] = status
    if note:
        module.setdefault("notes", []).append(note)
    for artifact in artifacts or []:
        if artifact not in module.setdefault("artifacts", []):
            module["artifacts"].append(artifact)


def record_checkpoint(
    graph: dict[str, Any], checkpoint_id: str, status: str, renders: dict[str, str], note: str | None = None
) -> None:
    require_valid(graph)
    if status not in {"pass", "fail"}:
        raise FormGraphError("checkpoint status must be pass or fail")
    checkpoints = {item["id"]: item for item in graph["checkpoints"]}
    if checkpoint_id not in checkpoints:
        raise FormGraphError(f"unknown checkpoint: {checkpoint_id}")
    checkpoint = checkpoints[checkpoint_id]
    modules = modules_by_id(graph)
    if status == "pass":
        incomplete = [item for item in checkpoint["after"] if modules[item]["status"] != "validated"]
        if incomplete:
            raise FormGraphError("checkpoint modules are not validated: " + ", ".join(incomplete))
        missing_views = [item for item in checkpoint["requiredViews"] if item not in renders]
        if missing_views:
            raise FormGraphError("checkpoint renders are missing: " + ", ".join(missing_views))
    checkpoint["status"] = status
    checkpoint["renders"] = dict(sorted(renders.items()))
    if note:
        checkpoint.setdefault("notes", []).append(note)


def new_graph(graph_id: str) -> dict[str, Any]:
    if not ID_PATTERN.fullmatch(graph_id):
        raise FormGraphError(f"invalid graph id: {graph_id!r}")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": graph_id,
        "units": "m",
        "coordinateSystem": "Z_UP",
        "rootModule": "asset",
        "modules": [
            {
                "id": "asset",
                "kind": "assembly",
                "parent": None,
                "stage": 0,
                "generator": "composition",
                "dependsOn": [],
                "attachments": [],
                "parameters": {},
                "acceptance": {"required": ["named-hierarchy"], "visual": ["approved silhouette"]},
                "status": "planned",
                "notes": [],
                "artifacts": [],
            }
        ],
        "checkpoints": [],
        "metadata": {"sourcePrompt": "", "referenceImages": [], "targetRuntime": ""},
    }


def _render_map(values: list[str]) -> dict[str, str]:
    renders: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise FormGraphError(f"render must use view=/absolute/path: {value!r}")
        view, path = value.split("=", 1)
        if not view or not path:
            raise FormGraphError(f"invalid render mapping: {value!r}")
        renders[view] = path
    return renders


def _print(value: Any, compact: bool = False) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=None if compact else 2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("new", help="create a minimal FormGraph")
    create.add_argument("--id", required=True)
    create.add_argument("--output", type=Path, required=True)

    validate = subparsers.add_parser("validate", help="validate a FormGraph")
    validate.add_argument("manifest", type=Path)

    ready = subparsers.add_parser("ready", help="list dependency-ready modules")
    ready.add_argument("manifest", type=Path)

    status = subparsers.add_parser("set-status", help="advance one module")
    status.add_argument("manifest", type=Path)
    status.add_argument("module")
    status.add_argument("status", choices=sorted(MODULE_STATUSES))
    status.add_argument("--note")
    status.add_argument("--artifact", action="append", default=[])

    checkpoint = subparsers.add_parser("checkpoint", help="record a render checkpoint")
    checkpoint.add_argument("manifest", type=Path)
    checkpoint.add_argument("checkpoint")
    checkpoint.add_argument("status", choices=["pass", "fail"])
    checkpoint.add_argument("--render", action="append", default=[])
    checkpoint.add_argument("--note")

    summary = subparsers.add_parser("summary", help="summarize progress")
    summary.add_argument("manifest", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "new":
            graph = new_graph(args.id)
            save_graph(args.output, graph)
            _print({"ok": True, "manifest": str(args.output), "id": args.id})
            return 0

        graph = load_graph(args.manifest)
        if args.command == "validate":
            errors = validation_errors(graph)
            _print({"ok": not errors, "errors": errors})
            return 0 if not errors else 1
        if args.command == "ready":
            modules = ready_modules(graph)
            _print({"ok": True, "ready": [{"id": item["id"], "stage": item["stage"]} for item in modules]})
            return 0
        if args.command == "set-status":
            set_module_status(graph, args.module, args.status, args.note, args.artifact)
            save_graph(args.manifest, graph)
            _print({"ok": True, "module": args.module, "status": args.status})
            return 0
        if args.command == "checkpoint":
            record_checkpoint(graph, args.checkpoint, args.status, _render_map(args.render), args.note)
            save_graph(args.manifest, graph)
            _print({"ok": True, "checkpoint": args.checkpoint, "status": args.status})
            return 0
        if args.command == "summary":
            require_valid(graph)
            module_counts = Counter(item["status"] for item in graph["modules"])
            checkpoint_counts = Counter(item["status"] for item in graph["checkpoints"])
            _print(
                {
                    "ok": True,
                    "id": graph["id"],
                    "modules": dict(sorted(module_counts.items())),
                    "checkpoints": dict(sorted(checkpoint_counts.items())),
                    "ready": [item["id"] for item in ready_modules(graph)],
                }
            )
            return 0
    except FormGraphError as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
