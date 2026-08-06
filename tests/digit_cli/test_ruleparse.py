"""Слой правил: разбирает — или молчит. Третьей ветки нет.

Проверяется не «работает ли парсер вообще», а два свойства, ради которых он
встроен: аргументы берутся из запроса ДОСЛОВНО, и всякий неразбор выглядит как
уступка модели, а не как отказ.
"""

from __future__ import annotations

import pytest

from digit_cli.ruleparse import catalog_size, dependency_available, route
from digit_cli.ruleparse.cascade import (
    MAX_QUERY_CHARS,
    MIN_MARGIN,
    MIN_SCORE,
    RuleDecision,
    render_answer,
)

needs_dict = pytest.mark.skipif(
    not dependency_available(),
    reason="нет pymorphy3: каскад в этой среде всегда уступает модели",
)


def test_catalog_ships_with_the_package():
    """Каталог — вход алгоритма, а не внешний файл чьей-то рабочей копии."""
    assert catalog_size() == 86


def test_default_operating_point_is_the_measured_one():
    """Умолчания — конфигурация R1.

    Именно на ней измерены 3,8 % ложных ответов, 96,0 % выбора инструмента и
    100,0 % точности аргументов. Сдвинуть их молча значит превратить отчёт в
    рассказ про другую систему.
    """
    assert (MIN_SCORE, MIN_MARGIN) == (12.0, 1.0)


# ---------------------------------------------------------------------------
# Уступки, которые не требуют словаря вообще
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "query, expected",
    [
        ("", "empty"),
        ("   ", "empty"),
        ("compute the md5 of password123", "not-russian"),
        ("а" + "б" * MAX_QUERY_CHARS, "too-long"),
    ],
)
def test_cheap_gates_cede_without_touching_morphology(query, expected):
    decision = route(query)
    assert decision.routed is False
    assert decision.declined == expected


def test_non_string_and_broken_input_never_raise():
    """Каскад, который падает, — это каскад, который отказал пользователю."""
    for bad in (None, "", "\x00\x00"):
        assert route(bad).routed is False


# ---------------------------------------------------------------------------
# Разбор
# ---------------------------------------------------------------------------
@needs_dict
def test_routes_a_command_and_copies_arguments_verbatim():
    decision = route("посчитай md5 от password123")
    assert decision.routed is True
    assert decision.tool_id == "hash-text"
    # Дословность — это и есть всё обещание слоя: значение обязано встречаться
    # в исходном запросе посимвольно, иначе «проверенный ответ» ничем не
    # отличается от угаданного.
    assert "password123" in decision.args.values()
    assert decision.latency_ms >= 0.0


@needs_dict
def test_routes_a_bare_noun_phrase():
    decision = route("сгенерируй 3 ulid")
    assert decision.routed is True
    assert decision.tool_id == "ulid-generator"
    assert decision.args == {"amount": 3}


@needs_dict
def test_a_question_about_the_world_is_not_a_tool_call():
    """«Что такое морфизм?» — вопрос к корпусу, а не приказ утилите."""
    decision = route("что такое морфизм в курсе по алгебре?")
    assert decision.routed is False
    assert decision.declined == "no-parse"
    assert decision.reason  # причина обязана быть названа — её читает журнал


@needs_dict
def test_ambiguous_direction_is_not_guessed():
    """Инструмент опознан, направление не названо — монетка вместо ответа."""
    decision = route("обработай мне base64 строку dGVzdA==")
    assert decision.routed is False
    assert decision.tool_id == "base64-string-converter"
    assert "направление" in decision.reason


@needs_dict
def test_missing_required_argument_cedes_instead_of_inventing_one():
    decision = route("посчитай хэш")
    assert decision.routed is False


@needs_dict
def test_decision_is_deterministic():
    """Один запрос — один разбор. Всегда. Иначе «воспроизводимо» — слово."""
    first = route("переведи 255 из десятичной в шестнадцатеричную")
    second = route("переведи 255 из десятичной в шестнадцатеричную")
    assert (first.routed, first.tool_id, first.args) == (
        second.routed, second.tool_id, second.args)


# ---------------------------------------------------------------------------
# Показ
# ---------------------------------------------------------------------------
def test_render_shows_every_argument_so_a_human_can_check_it():
    decision = RuleDecision(routed=True, tool_id="hash-text",
                            args={"clearText": "password123", "algorithm": "MD5"})
    text = render_answer(decision)
    assert "hash-text" in text
    assert "password123" in text
    assert "MD5" in text


def test_render_of_a_ceded_decision_is_empty():
    """Уступка не имеет текста: её нельзя случайно показать как ответ."""
    assert render_answer(RuleDecision(routed=False, declined="no-parse")) == ""


def test_telemetry_record_carries_no_free_text():
    decision = RuleDecision(routed=True, tool_id="hash-text",
                            args={"clearText": "s3cret"}, score=31.5)
    record = decision.as_telemetry()
    # Значения аргументов — это данные пользователя. В журнал уходят имена.
    assert record["arg_names"] == ["clearText"]
    assert "s3cret" not in repr(record)
