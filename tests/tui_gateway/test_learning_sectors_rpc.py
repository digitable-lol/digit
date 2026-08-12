"""Контракт learning.sectors — сектор-ось графа знаний для оболочки TUI.

Поля ``sector`` / ``links`` / ``backlinks`` появились на каждом узле в
424f171fe, и CLI рисует их с тех пор (``digit journey sectors``). Оболочка
TUI не рисовала: единственный её источник ``learning.frames`` отдаёт
хронологию, а хронология рассыпает один предмет по всем строкам дат. Метод
добавлен рядом с ``learning.frames`` и отвечает на второй вопрос — «о чём я
знаю и что держит каждую область вместе».

Главное свойство, которое здесь сторожится, — НЕ состав картинки, а то, что
картинка одна на две поверхности. Разбиение по секторам, степень узла,
ранжирование и счёт сирот считает ``render_sectors``; в TypeScript не
пересчитывается ничего. Второе разбиение было бы вторым мнением о тех же
числах, и тогда терминал и командная строка отвечали бы на один вопрос
по-разному — а расходятся такие пары молча.

Второе свойство — усечение обязано быть названным. Список внутри сектора
режется по ``per_sector``, и урезанная картинка выглядит целой; поэтому
скрытое обязано быть и сосчитано в ``groups[].hidden``, и произнесено
строкой в самой сетке.
"""

from __future__ import annotations

import pytest

from digit_constants import reset_digit_home_override, set_digit_home_override
from tui_gateway import server

NOTES = "\n§\n".join(
    [
        "# Vector search\n#retrieval HNSW. Related: [[Embeddings]].",
        "# Embeddings\nQwen3. Needed by [[Vector search]].",
        "# Hybrid search\n#retrieval BM25 over [[Vector search]].",
        "# Reranker\n#retrieval Cross-encoder after [[Hybrid search]].",
        "# Chunking\n#retrieval Sentence windows feed [[Embeddings]].",
        "# Espresso\n#daily The user drinks espresso.",
    ]
)


@pytest.fixture
def home(tmp_path):
    memories = tmp_path / ".digit" / "memories"
    memories.mkdir(parents=True)
    (memories / "MEMORY.md").write_text(NOTES, encoding="utf-8")
    token = set_digit_home_override(tmp_path / ".digit")
    try:
        yield tmp_path / ".digit"
    finally:
        reset_digit_home_override(token)


def _call(params: dict) -> dict:
    resp = server._methods["learning.sectors"]("r1", params)
    assert "error" not in resp, resp
    return resp["result"]


def test_the_method_is_registered_beside_learning_frames():
    assert "learning.sectors" in server._methods
    assert "learning.frames" in server._methods


def test_the_payload_is_exactly_what_the_cli_renderer_produces(home):
    """Ни одного числа оболочка не считает сама — вот доказательство."""
    from agent.learning_graph import build_learning_graph
    from agent.learning_graph_render import build_sector_summary, render_sectors

    result = _call({"cols": 80})
    payload = build_learning_graph()
    expected = render_sectors(payload, cols=80, per_sector=6)

    assert result["grid"] == expected["grid"]
    assert result["groups"] == expected["groups"]
    assert result["summary"] == build_sector_summary(payload)


def test_every_note_lands_in_a_sector_and_none_is_lost(home):
    result = _call({"cols": 80})
    counted = sum(g["count"] for g in result["groups"])

    assert counted == result["count"]
    assert {g["sector"] for g in result["groups"]} >= {"retrieval", "daily"}


def test_links_are_reported_in_both_directions(home):
    """Исходящая и входящая ссылка — разные факты; сложить их значит потерять оба."""
    result = _call({"cols": 80})
    text = "".join(run[0] for row in result["grid"] for run in row)

    # Заметки памяти печатают «→исх ←вх» раздельно, и обе стрелки обязаны быть.
    assert "→" in text and "←" in text


def test_truncation_is_counted_and_said_out_loud(home):
    """Урезанный список выглядит целым — значит, обязан о себе сообщать."""
    result = _call({"cols": 80, "limit": 2})
    retrieval = next(g for g in result["groups"] if g["sector"] == "retrieval")

    # Пять, а не четыре: четыре заметки несут тег #retrieval, а «Embeddings»
    # тега не несёт и попадает в сектор по ссылке. Ожидание «четыре» было моей
    # ошибкой в этом тесте, и она же — причина держать проверку именно здесь.
    assert retrieval["count"] == 5
    assert len(retrieval["nodes"]) == 2
    assert retrieval["hidden"] == 3

    text = "".join(run[0] for row in result["grid"] for run in row)
    assert "+3 more" in text


def test_a_junk_width_falls_back_instead_of_failing_the_overlay(home):
    """Оболочка присылает размер терминала; кривой размер не повод показать ошибку."""
    result = _call({"cols": "wide", "limit": None})

    assert result["grid"]
    assert result["groups"]


def test_an_empty_corpus_answers_instead_of_erroring(tmp_path):
    memories = tmp_path / ".digit" / "memories"
    memories.mkdir(parents=True)
    (memories / "MEMORY.md").write_text("", encoding="utf-8")
    token = set_digit_home_override(tmp_path / ".digit")
    try:
        result = _call({"cols": 80})
    finally:
        reset_digit_home_override(token)

    assert result["groups"] == []
    assert result["summary"], "пустой корпус — это ответ, а не молчание"
