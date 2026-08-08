"""Behavior contracts for the wikilink/tag layer over freeform memory.

These assert the *rules* a Zettelkasten needs to hold — a link is traversable
from both ends, a tag files a note, a link into a filed cluster files it too,
and a link to a note that doesn't exist is reported rather than swallowed.
They deliberately avoid asserting on syntax variants nobody writes.
"""

from __future__ import annotations

from agent import memory_links as ml


def _card(title: str, body: str = "") -> dict:
    text = f"{title}\n{body}".strip()
    return {
        "title": title,
        "key": ml.normalize_key(title),
        "body": text,
        "links": ml.extract_links(text),
        "tags": ml.extract_tags(text),
    }


# ── parsing ─────────────────────────────────────────────────────────────────


def test_wikilink_aliases_resolve_to_the_target_not_the_alias():
    assert ml.extract_links("see [[Vector search|the fast one]] today") == ["Vector search"]


def test_markdown_headings_are_not_tags():
    # "# Заголовок" и "## Раздел" — разметка, а не область знания; спутать их
    # значит завести сектор на каждый заголовок в корпусе.
    assert ml.extract_tags("# Heading\n## Sub\n#real content") == ["real"]


def test_issue_numbers_are_not_tags():
    assert ml.extract_tags("fixes #26045 in #infra") == ["infra"]


def test_url_fragments_are_not_tags():
    # Якорь в ссылке — самый частый ложный тег: он встречается в заметках чаще,
    # чем настоящие теги, и завёл бы сектор на каждый заголовок чужой страницы.
    assert ml.extract_tags("see https://docs.example.com/guide#installation") == []


def test_a_hash_glued_to_a_word_is_not_a_tag():
    assert ml.extract_tags("C#, F# and item#3") == []


def test_tags_and_links_survive_non_latin_text():
    text = "#поиск заметка про [[Векторный поиск]]"
    assert ml.extract_tags(text) == ["поиск"]
    assert ml.extract_links(text) == ["Векторный поиск"]


def test_normalize_key_bridges_heading_markers_and_case():
    assert ml.normalize_key("# Vector  Search:") == ml.normalize_key("vector search")


def test_a_tag_in_the_heading_does_not_rename_the_note():
    # Заметку подписывают тегом прямо в первой строке, а ссылаются на неё
    # названием. Пока тег входил в ключ, правильно написанная ссылка числилась
    # битой.
    assert ml.normalize_key("# Сборка образов #сеть") == ml.normalize_key("Сборка образов")
    assert ml.normalize_key("Сборка образов #сеть #devops") == ml.normalize_key("Сборка образов")


def test_a_hash_inside_the_heading_is_part_of_the_name():
    # То же правило, что и у тегов: решётка без пробела слева — не тег.
    assert ml.normalize_key("Язык C#") == "язык c#"
    assert ml.normalize_key("Задача #42") == "задача #42"
    assert ml.normalize_key("см https://x.dev/g#install") == "см https://x.dev/g#install"


def test_a_heading_that_is_only_a_tag_keeps_its_name():
    # Снять тег значило бы получить пустой ключ, а он не резолвится ни во что.
    assert ml.normalize_key("#сеть") == "сеть"


def test_link_resolves_to_a_note_tagged_in_its_heading():
    cards = [
        _card("Прокси в CI", "см [[Сборка образов]]"),
        _card("Сборка образов #сеть", "docker buildx"),
    ]
    result = ml.annotate(cards)

    assert result["edges"] == [(0, 1)]
    assert result["unresolved"] == {}


# ── linking ─────────────────────────────────────────────────────────────────


def test_backlinks_expose_the_edge_the_writer_never_typed():
    cards = [
        _card("Vector search", "linked to [[Embeddings]]"),
        _card("Embeddings", "just a model"),
    ]
    result = ml.annotate(cards)

    assert result["edges"] == [(0, 1)]
    # Заметка "Embeddings" ничего про себя не объявляла — ссылку на неё видно
    # только с обратной стороны, ради чего беклинки и существуют.
    assert result["backlinks"][1] == [0]
    assert result["backlinks"][0] == []


def test_self_links_do_not_inflate_connectivity():
    cards = [_card("Recursion", "see [[Recursion]]")]
    result = ml.annotate(cards)

    assert result["edges"] == []
    assert result["backlinks"][0] == []


def test_dangling_links_are_reported_not_dropped():
    cards = [_card("Plans", "read [[Reranker]] next")]
    result = ml.annotate(cards)

    assert result["edges"] == []
    assert result["unresolved"][0] == ["Reranker"]


def test_duplicate_titles_resolve_to_the_first_note_deterministically():
    cards = [_card("Notes", "first"), _card("Notes", "second"), _card("Ptr", "[[Notes]]")]
    result = ml.annotate(cards)

    assert result["edges"] == [(2, 0)]


# ── sectors ─────────────────────────────────────────────────────────────────


def test_first_tag_files_the_note():
    cards = [_card("A", "#infra #later text")]
    assert ml.assign_sectors(cards, []) == ["infra"]


def test_untagged_note_inherits_the_sector_of_the_cluster_it_links_into():
    cards = [
        _card("Vector search", "#retrieval core"),
        _card("Embeddings", "feeds [[Vector search]]"),
    ]
    result = ml.annotate(cards)

    # Связать заметку с кластером — это и есть акт её раскладки по полкам;
    # требовать повторить тег значило бы штрафовать самые связные заметки.
    assert result["sectors"] == ["retrieval", "retrieval"]


def test_note_with_neither_tag_nor_link_is_honestly_unsorted():
    cards = [_card("Loose", "nothing here")]
    assert ml.assign_sectors(cards, []) == [ml.SECTOR_UNSORTED]


def test_sector_inheritance_is_stable_when_a_cluster_ties():
    cards = [
        _card("A", "#beta"),
        _card("B", "#alpha"),
        _card("C", "[[A]] and [[B]]"),
    ]
    result = ml.annotate(cards)

    # Ничья 1:1 разрешается по алфавиту, иначе разбивка прыгала бы между
    # запусками на одних и тех же данных.
    assert result["sectors"][2] == "alpha"
