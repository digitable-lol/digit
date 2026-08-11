#!/usr/bin/env python3
"""Build and train a deterministic structural FormGraph ranker."""

from __future__ import annotations

import argparse
import copy
import concurrent.futures
import hashlib
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


FEATURE_NAMES = (
    "module_count",
    "checkpoint_count",
    "parent_edges",
    "dependency_edges",
    "attachment_count",
    "stage_span",
    "max_parent_depth",
    "cycle_count",
    "missing_reference_count",
    "duplicate_id_count",
    "acceptance_coverage",
    "visual_acceptance_coverage",
    "attachment_coverage",
    "generator_diversity",
    "identity_anchor_coverage",
    "validated_fraction",
)
IDENTITY_ANCHORS = ("gaze", "evidence", "seal", "current", "face", "rig")
MUTATIONS = (
    "missing-dependency",
    "self-dependency",
    "drop-acceptance",
    "drop-attachment",
    "duplicate-module",
    "invalid-status",
    "strip-visual-qa",
    "strip-identity",
    "collapse-monolith",
)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _module_ids(graph: dict[str, Any]) -> list[str]:
    return [
        item.get("id", "")
        for item in graph.get("modules", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]


def _reference_metrics(graph: dict[str, Any]) -> tuple[int, int, int]:
    modules = [item for item in graph.get("modules", []) if isinstance(item, dict)]
    ids = _module_ids(graph)
    id_set = set(ids)
    duplicates = sum(count - 1 for count in Counter(ids).values() if count > 1)
    missing = 0
    edges: dict[str, set[str]] = {item: set() for item in id_set}
    for module in modules:
        module_id = module.get("id")
        if module_id not in id_set:
            continue
        for reference in [module.get("parent"), *module.get("dependsOn", [])]:
            if reference is None:
                continue
            if reference not in id_set:
                missing += 1
            else:
                edges[module_id].add(reference)
        for attachment in module.get("attachments", []):
            if isinstance(attachment, dict) and attachment.get("to") not in id_set:
                missing += 1

    visiting: set[str] = set()
    visited: set[str] = set()
    cycle_count = 0

    def visit(node: str) -> None:
        nonlocal cycle_count
        if node in visiting:
            cycle_count += 1
            return
        if node in visited:
            return
        visiting.add(node)
        for dependency in edges.get(node, set()):
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for node in sorted(edges):
        visit(node)
    return missing, duplicates, cycle_count


def _max_parent_depth(graph: dict[str, Any]) -> int:
    parents = {
        item.get("id"): item.get("parent")
        for item in graph.get("modules", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    maximum = 0
    for module_id in parents:
        depth = 0
        seen: set[str] = set()
        current = module_id
        while current in parents and parents[current] is not None and current not in seen:
            seen.add(current)
            current = parents[current]
            depth += 1
        maximum = max(maximum, depth)
    return maximum


def extract_features(graph: dict[str, Any]) -> dict[str, float]:
    modules = [item for item in graph.get("modules", []) if isinstance(item, dict)]
    checkpoints = [item for item in graph.get("checkpoints", []) if isinstance(item, dict)]
    count = max(1, len(modules))
    missing, duplicates, cycles = _reference_metrics(graph)
    stages = [item.get("stage", 0) for item in modules if isinstance(item.get("stage", 0), int)]
    parent_edges = sum(item.get("parent") is not None for item in modules)
    dependency_edges = sum(len(item.get("dependsOn", [])) for item in modules)
    attachment_count = sum(len(item.get("attachments", [])) for item in modules)
    acceptance = sum(isinstance(item.get("acceptance"), dict) for item in modules)
    visual = sum(
        bool(item.get("acceptance", {}).get("visual"))
        for item in modules
        if isinstance(item.get("acceptance"), dict)
    )
    non_root = [item for item in modules if item.get("parent") is not None]
    attached = sum(bool(item.get("attachments")) for item in non_root)
    generators = {item.get("generator") for item in modules if item.get("generator")}
    searchable = " ".join(
        str(value).lower()
        for item in modules
        for value in (item.get("id", ""), item.get("generator", ""))
    )
    identity_hits = sum(anchor in searchable for anchor in IDENTITY_ANCHORS)
    validated = sum(item.get("status") in {"built", "validated"} for item in modules)
    values = {
        "module_count": float(len(modules)),
        "checkpoint_count": float(len(checkpoints)),
        "parent_edges": float(parent_edges),
        "dependency_edges": float(dependency_edges),
        "attachment_count": float(attachment_count),
        "stage_span": float(max(stages) - min(stages) if stages else 0),
        "max_parent_depth": float(_max_parent_depth(graph)),
        "cycle_count": float(cycles),
        "missing_reference_count": float(missing),
        "duplicate_id_count": float(duplicates),
        "acceptance_coverage": acceptance / count,
        "visual_acceptance_coverage": visual / count,
        "attachment_coverage": attached / max(1, len(non_root)),
        "generator_diversity": len(generators) / count,
        "identity_anchor_coverage": identity_hits / len(IDENTITY_ANCHORS),
        "validated_fraction": validated / count,
    }
    return {name: float(values[name]) for name in FEATURE_NAMES}


def mutate_graph(graph: dict[str, Any], mutation: str, rng: random.Random) -> dict[str, Any]:
    value = copy.deepcopy(graph)
    modules = [item for item in value.get("modules", []) if isinstance(item, dict)]
    if not modules:
        return value
    root = next((item for item in modules if item.get("parent") is None), modules[0])
    leaves = [item for item in modules if item is not root] or modules
    target = rng.choice(leaves)
    if mutation == "missing-dependency":
        target.setdefault("dependsOn", []).append("missing-module")
    elif mutation == "self-dependency":
        target.setdefault("dependsOn", []).append(target.get("id"))
    elif mutation == "drop-acceptance":
        target = rng.choice([item for item in modules if "acceptance" in item] or leaves)
        target.pop("acceptance", None)
    elif mutation == "drop-attachment":
        target = rng.choice([item for item in modules if item.get("attachments")] or leaves)
        target["attachments"] = []
    elif mutation == "duplicate-module":
        value["modules"].append(copy.deepcopy(target))
    elif mutation == "invalid-status":
        target["status"] = "imagined"
    elif mutation == "strip-visual-qa":
        for module in modules:
            if isinstance(module.get("acceptance"), dict):
                module["acceptance"]["visual"] = []
        value["checkpoints"] = []
    elif mutation == "strip-identity":
        filtered = [
            item
            for item in modules
            if not any(anchor in str(item.get("id", "")).lower() for anchor in IDENTITY_ANCHORS)
        ]
        if filtered:
            value["modules"] = filtered
            live_ids = {item.get("id") for item in filtered}
            for module in filtered:
                module["dependsOn"] = [item for item in module.get("dependsOn", []) if item in live_ids]
                module["attachments"] = [
                    item for item in module.get("attachments", []) if item.get("to") in live_ids
                ]
            value["checkpoints"] = []
    elif mutation == "collapse-monolith":
        monolith = copy.deepcopy(root)
        monolith["id"] = "monolith"
        monolith["parent"] = None
        monolith["dependsOn"] = []
        monolith["attachments"] = []
        monolith["generator"] = "opaque-prompt-to-mesh"
        monolith["acceptance"] = {"required": ["nonempty"], "visual": []}
        value["rootModule"] = "monolith"
        value["modules"] = [monolith]
        value["checkpoints"] = []
    else:
        raise ValueError(f"unknown mutation: {mutation}")
    return value


def graph_paths(inputs: Iterable[Path]) -> list[Path]:
    paths: set[Path] = set()
    for input_path in inputs:
        if input_path.is_dir():
            paths.update(input_path.rglob("form-graph.json"))
        elif input_path.is_file():
            paths.add(input_path)
    return sorted(paths)


def build_dataset(
    inputs: Iterable[Path], *, negatives_per_positive: int = 18, seed: int = 260811
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    paths = graph_paths(inputs)
    if not paths:
        raise ValueError("no form-graph.json inputs found")
    for path in paths:
        graph = load_json(path)
        group = str(graph.get("id") or path.parent.name)
        rows.append({
            "group": group,
            "label": 1,
            "mutation": "accepted-source",
            "source": str(path),
            "features": extract_features(graph),
        })
        for index in range(negatives_per_positive):
            mutation = MUTATIONS[index % len(MUTATIONS)]
            candidate = mutate_graph(graph, mutation, rng)
            rows.append({
                "group": group,
                "label": 0,
                "mutation": mutation,
                "source": str(path),
                "features": extract_features(candidate),
            })
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> str:
    payload = "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _sigmoid(value: float) -> float:
    value = max(-40.0, min(40.0, value))
    return 1.0 / (1.0 + math.exp(-value))


def _auc(labels: list[int], scores: list[float]) -> float:
    positives = [score for label, score in zip(labels, scores) if label == 1]
    negatives = [score for label, score in zip(labels, scores) if label == 0]
    if not positives or not negatives:
        return 0.0
    wins = sum((p > n) + 0.5 * (p == n) for p in positives for n in negatives)
    return wins / (len(positives) * len(negatives))


def _metrics(rows: list[dict[str, Any]], scores: list[float]) -> dict[str, float]:
    labels = [int(row["label"]) for row in rows]
    predictions = [score >= 0.5 for score in scores]
    accuracy = sum(
        prediction == bool(label) for prediction, label in zip(predictions, labels)
    ) / max(1, len(rows))
    grouped: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for row, score in zip(rows, scores):
        grouped[row["group"]].append((int(row["label"]), score))
    wins = 0
    judged = 0
    for values in grouped.values():
        positives = [score for label, score in values if label == 1]
        negatives = [score for label, score in values if label == 0]
        if positives and negatives:
            judged += 1
            wins += max(positives) > max(negatives)
    return {
        "accuracy": round(accuracy, 6),
        "rocAuc": round(_auc(labels, scores), 6),
        "groupTop1": round(wins / max(1, judged), 6),
        "examples": len(rows),
        "groups": len(grouped),
    }


def train_ranker(
    rows: list[dict[str, Any]],
    *,
    epochs: int = 2400,
    learning_rate: float = 0.04,
    l2: float = 0.002,
) -> dict[str, Any]:
    groups = sorted({row["group"] for row in rows})
    holdout_count = max(1, len(groups) // 4)
    test_groups = set(groups[-holdout_count:])
    train_rows = [row for row in rows if row["group"] not in test_groups]
    test_rows = [row for row in rows if row["group"] in test_groups]
    if not train_rows or not test_rows:
        raise ValueError("dataset needs at least two graph groups")
    grouped_train: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in train_rows:
        grouped_train[row["group"]].append(row)
    pair_differences: list[list[float]] = []
    for group_rows in grouped_train.values():
        positives = [row for row in group_rows if int(row["label"]) == 1]
        negatives = [row for row in group_rows if int(row["label"]) == 0]
        for positive in positives:
            for negative in negatives:
                pair_differences.append(
                    [
                        positive["features"][name] - negative["features"][name]
                        for name in FEATURE_NAMES
                    ]
                )
    if not pair_differences:
        raise ValueError("training groups need accepted/rejected candidate pairs")
    accepted_train = [row for row in train_rows if int(row["label"]) == 1]
    means = {
        name: sum(row["features"][name] for row in accepted_train) / len(accepted_train)
        for name in FEATURE_NAMES
    }
    scales: dict[str, float] = {}
    for name in FEATURE_NAMES:
        index = FEATURE_NAMES.index(name)
        variance = sum(values[index] ** 2 for values in pair_differences) / len(
            pair_differences
        )
        scales[name] = max(1e-9, math.sqrt(variance))

    def vector(row: dict[str, Any]) -> list[float]:
        return [
            (row["features"][name] - means[name]) / scales[name]
            for name in FEATURE_NAMES
        ]

    weights = [0.0] * len(FEATURE_NAMES)
    bias = 0.0
    for _epoch in range(epochs):
        gradient = [0.0] * len(weights)
        for difference in pair_differences:
            features = [
                value / scales[name] for value, name in zip(difference, FEATURE_NAMES)
            ]
            score = _sigmoid(
                sum(weight * value for weight, value in zip(weights, features))
            )
            error = score - 1.0
            for index, value in enumerate(features):
                gradient[index] += error * value
        for index in range(len(weights)):
            gradient[index] = gradient[index] / len(pair_differences) + l2 * weights[index]
            weights[index] -= learning_rate * gradient[index]

    def score_rows(selected: list[dict[str, Any]]) -> list[float]:
        return [
            _sigmoid(bias + sum(weight * value for weight, value in zip(weights, vector(row))))
            for row in selected
        ]

    return {
        "format": "digitmorf-formgraph-ranker/v1",
        "featureNames": list(FEATURE_NAMES),
        "means": means,
        "scales": scales,
        "weights": dict(zip(FEATURE_NAMES, weights)),
        "bias": bias,
        "training": {
            "epochs": epochs,
            "learningRate": learning_rate,
            "l2": l2,
            "objective": "pairwise-logistic",
            "pairCount": len(pair_differences),
            "trainGroups": sorted(set(groups) - test_groups),
            "testGroups": sorted(test_groups),
        },
        "metrics": {
            "train": _metrics(train_rows, score_rows(train_rows)),
            "test": _metrics(test_rows, score_rows(test_rows)),
        },
    }


def score_graph(model: dict[str, Any], graph: dict[str, Any]) -> float:
    features = extract_features(graph)
    value = float(model["bias"])
    for name in model["featureNames"]:
        normalized = (features[name] - model["means"][name]) / model["scales"][name]
        value += model["weights"][name] * normalized
    return _sigmoid(value)


def _train_configuration(payload: tuple[list[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    rows, configuration = payload
    model = train_ranker(rows, **configuration)
    return {"configuration": configuration, "metrics": model["metrics"], "model": model}


def sweep_rankers(
    rows: list[dict[str, Any]],
    configurations: list[dict[str, Any]],
    *,
    workers: int = 1,
) -> dict[str, Any]:
    if not configurations:
        raise ValueError("at least one ranker configuration is required")
    payloads = [(rows, configuration) for configuration in configurations]
    if workers == 1:
        results = [_train_configuration(payload) for payload in payloads]
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(_train_configuration, payloads))

    def quality(result: dict[str, Any]) -> tuple[float, float, float, float]:
        test = result["metrics"]["test"]
        train = result["metrics"]["train"]
        return (test["groupTop1"], test["rocAuc"], test["accuracy"], train["rocAuc"])

    results.sort(
        key=lambda result: (
            *quality(result),
            -result["configuration"]["epochs"],
            -result["configuration"]["learning_rate"],
            -result["configuration"]["l2"],
        ),
        reverse=True,
    )
    best = results[0]
    best["model"]["sweep"] = {
        "workers": workers,
        "configurations": len(configurations),
        "selectionMetric": ["test.groupTop1", "test.rocAuc", "test.accuracy", "train.rocAuc"],
        "results": [
            {"configuration": result["configuration"], "metrics": result["metrics"]}
            for result in results
        ],
    }
    return best["model"]


def command_dataset(args: argparse.Namespace) -> int:
    rows = build_dataset(
        [Path(value).expanduser().resolve() for value in args.inputs],
        negatives_per_positive=args.negatives_per_positive,
        seed=args.seed,
    )
    output = Path(args.output).expanduser().resolve()
    digest = write_jsonl(output, rows)
    counts = Counter(row["label"] for row in rows)
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(output),
                "sha256": digest,
                "examples": len(rows),
                "positive": counts[1],
                "negative": counts[0],
            },
            sort_keys=True,
        )
    )
    return 0


def command_train(args: argparse.Namespace) -> int:
    dataset = Path(args.dataset).expanduser().resolve()
    rows = read_jsonl(dataset)
    model = train_ranker(
        rows,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        l2=args.l2,
    )
    model["datasetSha256"] = hashlib.sha256(dataset.read_bytes()).hexdigest()
    output = Path(args.output).expanduser().resolve()
    save_json(output, model)
    print(
        json.dumps(
            {"ok": True, "output": str(output), "metrics": model["metrics"]},
            sort_keys=True,
        )
    )
    return 0


def command_sweep(args: argparse.Namespace) -> int:
    dataset = Path(args.dataset).expanduser().resolve()
    rows = read_jsonl(dataset)
    configurations = [
        {"epochs": epochs, "learning_rate": learning_rate, "l2": l2}
        for epochs in (600, 1200, 2400)
        for learning_rate in (0.015, 0.03, 0.05, 0.075)
        for l2 in (0.0005, 0.002, 0.01)
    ]
    model = sweep_rankers(rows, configurations, workers=args.workers)
    model["datasetSha256"] = hashlib.sha256(dataset.read_bytes()).hexdigest()
    output = Path(args.output).expanduser().resolve()
    save_json(output, model)
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(output),
                "metrics": model["metrics"],
                "selected": model["training"],
                "configurations": len(configurations),
                "workers": args.workers,
            },
            sort_keys=True,
        )
    )
    return 0


def command_score(args: argparse.Namespace) -> int:
    model = load_json(Path(args.model).expanduser().resolve())
    graph = load_json(Path(args.graph).expanduser().resolve())
    print(json.dumps({"ok": True, "score": score_graph(model, graph)}, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    dataset = subparsers.add_parser("dataset")
    dataset.add_argument("inputs", nargs="+")
    dataset.add_argument("--output", required=True)
    dataset.add_argument("--negatives-per-positive", type=int, default=18)
    dataset.add_argument("--seed", type=int, default=260811)
    dataset.set_defaults(run=command_dataset)
    train = subparsers.add_parser("train")
    train.add_argument("--dataset", required=True)
    train.add_argument("--output", required=True)
    train.add_argument("--epochs", type=int, default=2400)
    train.add_argument("--learning-rate", type=float, default=0.04)
    train.add_argument("--l2", type=float, default=0.002)
    train.set_defaults(run=command_train)
    sweep = subparsers.add_parser("sweep")
    sweep.add_argument("--dataset", required=True)
    sweep.add_argument("--output", required=True)
    sweep.add_argument("--workers", type=int, default=16)
    sweep.set_defaults(run=command_sweep)
    score = subparsers.add_parser("score")
    score.add_argument("--model", required=True)
    score.add_argument("--graph", required=True)
    score.set_defaults(run=command_score)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.run(args))


if __name__ == "__main__":
    sys.exit(main())
