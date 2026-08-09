"""Behavioral contracts for the modular Blender assembly skill."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from agent.skill_commands import scan_skill_commands
from agent.skill_utils import parse_frontmatter


REPO = Path(__file__).resolve().parent.parent.parent
SKILL = REPO / "skills/creative/blender-modular-assembly/SKILL.md"
SCRIPT = SKILL.parent / "scripts/form_graph.py"
TEMPLATE = SKILL.parent / "templates/form-graph.json"


def load_form_graph_module():
    spec = importlib.util.spec_from_file_location("form_graph", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_skill_is_loadable_as_a_digit_command(monkeypatch):
    from tools import skills_tool

    monkeypatch.setattr(skills_tool, "SKILLS_DIR", REPO / "skills")
    commands = scan_skill_commands()
    assert "/blender-modular-assembly" in commands
    assert Path(commands["/blender-modular-assembly"]["skill_md_path"]) == SKILL


def test_skill_metadata_and_template_are_valid():
    frontmatter, _body = parse_frontmatter(SKILL.read_text(encoding="utf-8"))
    assert frontmatter["name"] == "blender-modular-assembly"
    assert 0 < len(frontmatter["description"]) <= 60
    assert frontmatter["description"].endswith(".")

    module = load_form_graph_module()
    graph = module.load_graph(TEMPLATE)
    assert module.validation_errors(graph) == []


def test_dependency_readiness_and_status_transitions(tmp_path):
    module = load_form_graph_module()
    graph = module.new_graph("fixture")
    graph["modules"].append(
        {
            "id": "detail",
            "kind": "mesh",
            "parent": "asset",
            "stage": 1,
            "generator": "primitive",
            "dependsOn": ["asset"],
            "attachments": [{"to": "asset", "interface": "socket:detail", "mode": "parent"}],
            "parameters": {},
            "acceptance": {"required": ["nonempty"], "visual": ["silhouette passes"]},
            "status": "planned",
            "notes": [],
            "artifacts": [],
        }
    )
    path = tmp_path / "form-graph.json"
    module.save_graph(path, graph)

    assert [item["id"] for item in module.ready_modules(graph)] == ["asset"]
    module.set_module_status(graph, "asset", "building")
    module.set_module_status(graph, "asset", "built")
    assert [item["id"] for item in module.ready_modules(graph)] == ["detail"]
    module.set_module_status(graph, "detail", "building")
    module.set_module_status(graph, "detail", "rejected", note="wrong silhouette")
    assert [item["id"] for item in module.ready_modules(graph)] == ["detail"]


def test_cycles_and_unexplained_rejections_are_blocked():
    module = load_form_graph_module()
    graph = module.new_graph("fixture")
    graph["modules"][0]["dependsOn"] = ["asset"]
    assert any("itself" in error or "cycle" in error for error in module.validation_errors(graph))

    graph = module.new_graph("fixture")
    module.set_module_status(graph, "asset", "building")
    with pytest.raises(module.FormGraphError, match="requires --note"):
        module.set_module_status(graph, "asset", "rejected")
