"""Behavioral tests for the deterministic FormGraph structural ranker."""

from __future__ import annotations

import importlib.util
import json
import random
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent.parent
SKILL = REPO / "skills/creative/blender-modular-assembly"
SCRIPT = SKILL / "scripts/formgraph_ranker.py"
TEMPLATE = SKILL / "templates/form-graph.json"


def load_ranker_module():
    spec = importlib.util.spec_from_file_location("formgraph_ranker", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_graph(path: Path, graph_id: str) -> dict:
    graph = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    graph["id"] = graph_id
    for module in graph["modules"]:
        module["status"] = "validated"
    path.mkdir(parents=True)
    (path / "form-graph.json").write_text(
        json.dumps(graph, indent=2) + "\n", encoding="utf-8"
    )
    return graph


def test_dataset_contains_deterministic_positive_and_mutation_pairs(tmp_path):
    ranker = load_ranker_module()
    write_graph(tmp_path / "core", "core")
    write_graph(tmp_path / "forge", "forge")

    first = ranker.build_dataset([tmp_path], negatives_per_positive=18, seed=42)
    second = ranker.build_dataset([tmp_path], negatives_per_positive=18, seed=42)

    assert first == second
    assert sum(row["label"] == 1 for row in first) == 2
    assert {row["mutation"] for row in first if row["label"] == 0} == set(
        ranker.MUTATIONS
    )


def test_ranker_prefers_validated_modular_graph_to_collapsed_monolith(tmp_path):
    ranker = load_ranker_module()
    graphs = [
        write_graph(tmp_path / graph_id, graph_id)
        for graph_id in ("archivist", "forge", "sentinel", "weaver")
    ]
    rows = ranker.build_dataset([tmp_path], negatives_per_positive=27, seed=7)
    model = ranker.train_ranker(rows, epochs=900)

    assert model["format"] == "digitmorf-formgraph-ranker/v1"
    assert model["metrics"]["test"]["groupTop1"] == 1.0
    for graph in graphs:
        collapsed = ranker.mutate_graph(
            graph, "collapse-monolith", random.Random(260811)
        )
        assert ranker.score_graph(model, graph) > ranker.score_graph(model, collapsed)


def test_sweep_selects_and_records_the_best_configuration(tmp_path):
    ranker = load_ranker_module()
    for graph_id in ("core", "forge", "sentinel", "weaver"):
        write_graph(tmp_path / graph_id, graph_id)
    rows = ranker.build_dataset([tmp_path], negatives_per_positive=9, seed=11)
    model = ranker.sweep_rankers(
        rows,
        [
            {"epochs": 100, "learning_rate": 0.02, "l2": 0.01},
            {"epochs": 300, "learning_rate": 0.05, "l2": 0.002},
        ],
        workers=1,
    )

    assert model["sweep"]["configurations"] == 2
    assert model["training"]["epochs"] in {100, 300}
    assert model["metrics"]["test"]["groupTop1"] == 1.0
