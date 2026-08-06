"""Behavior contracts for the sector axis of the learning graph.

The timeline answers "when", the sector view answers "about what". These
assert that memory notes become a real network in the payload — links
resolved, backlinks present, sectors assigned — and that adding that axis did
not disturb the category legend the timeline already draws from.
"""

from __future__ import annotations

from agent import learning_graph, learning_graph_render as render
from digit_constants import reset_digit_home_override, set_digit_home_override

NOTES = "\n§\n".join(
    [
        "# Vector search\n#retrieval HNSW. Related: [[Embeddings]].",
        "# Embeddings\nQwen3. Needed by [[Vector search]].",
        "# Hybrid search\n#retrieval BM25 over [[Vector search]].",
        "# Espresso\n#daily The user drinks espresso.",
        "# Plans\nRead [[Reranker]] next.",
    ]
)


def _graph(tmp_path, notes: str = NOTES, profile: str = ""):
    home = tmp_path / ".digit"
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "MEMORY.md").write_text(notes, encoding="utf-8")
    if profile:
        (home / "memories" / "USER.md").write_text(profile, encoding="utf-8")
    token = set_digit_home_override(home)
    try:
        return learning_graph.build_learning_graph()
    finally:
        reset_digit_home_override(token)


def _by_label(graph: dict, label: str) -> dict:
    return next(n for n in graph["nodes"] if n["label"] == label)


def test_wikilinks_between_notes_become_graph_edges(tmp_path):
    graph = _graph(tmp_path)
    links = [(e["source"], e["target"]) for e in graph["edges"] if e["kind"] == "link"]

    vector, embeddings = _by_label(graph, "Vector search"), _by_label(graph, "Embeddings")
    assert (vector["id"], embeddings["id"]) in links
    assert (embeddings["id"], vector["id"]) in links


def test_every_edge_still_resolves_to_a_real_node(tmp_path):
    graph = _graph(tmp_path)
    ids = {n["id"] for n in graph["nodes"]}

    assert all(e["source"] in ids and e["target"] in ids for e in graph["edges"])


def test_a_note_carries_the_backlinks_pointing_at_it(tmp_path):
    graph = _graph(tmp_path, profile="# Me\nWorking on [[Embeddings]].")
    embeddings = _by_label(graph, "Embeddings")
    me = _by_label(graph, "Me")

    # USER.md-заметка ссылается на MEMORY.md-заметку: беклинк обязан пересекать
    # границу двух файлов, иначе половина сети невидима.
    assert me["id"] in embeddings["backlinks"]
    assert embeddings["id"] in me["links"]


def test_sectors_cover_every_node_and_count_them(tmp_path):
    graph = _graph(tmp_path)
    totals = {s["sector"]: s["count"] for s in graph["sectors"]}

    assert all(n.get("sector") for n in graph["nodes"])
    assert sum(totals.values()) == len(graph["nodes"])
    # Два тега #retrieval плюс "Embeddings", у которой тега нет — она попадает
    # в сектор по ссылке, и это и есть проверяемое правило.
    assert totals["retrieval"] == 3
    assert totals["daily"] == 1


def test_dangling_links_are_surfaced_on_the_node_and_in_stats(tmp_path):
    graph = _graph(tmp_path)

    assert _by_label(graph, "Plans")["unresolvedLinks"] == ["Reranker"]
    assert graph["stats"]["unresolved_links"] == 1


def test_memory_nodes_keep_their_category_so_the_skill_legend_is_unchanged(tmp_path):
    graph = _graph(tmp_path)

    # Легенда категорий отличает навыки от памяти по ``category == "memory"``;
    # если сектор протечёт в это поле, в легенде навыков появятся темы заметок.
    assert {n["category"] for n in graph["nodes"] if n["kind"] == "memory"} == {"memory"}
    assert all(c["category"] != "retrieval" for c in graph["clusters"])
    assert render.category_legend(graph) == []


def test_sector_groups_rank_hubs_above_orphans(tmp_path):
    graph = _graph(tmp_path)
    groups = {g["sector"]: g for g in render.sector_groups(graph)}

    # "Vector search" держит три конца из шести в секторе — в связанном корпусе
    # именно хаб является входной точкой, поэтому он идёт первым, а не самый
    # свежий узел.
    retrieval = groups["retrieval"]
    assert retrieval["nodes"][0]["label"] == "Vector search"
    assert retrieval["orphans"] == 0
    assert groups["daily"]["orphans"] == 1


def test_renderer_survives_a_graph_with_nothing_in_it(tmp_path):
    graph = _graph(tmp_path, notes="")

    assert render.render_sectors(graph)["groups"] == []
    assert render.build_sector_summary(graph)


def test_tokenizer_no_longer_drops_non_latin_words():
    # ``[^a-z0-9]+`` считал разделителем всю кириллицу, поэтому у русской
    # заметки токенов не оставалось и лексическая связь не возникала никогда.
    assert learning_graph._tokenize("Векторный поиск") == {"векторный", "поиск"}
