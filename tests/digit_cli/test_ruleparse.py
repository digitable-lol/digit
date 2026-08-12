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

    Именно на ней измерены 1,2 % ложных ответов, 96,0 % выбора инструмента и
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


# ---------------------------------------------------------------------------
# Операнд НАЗВАН или только ОПИСАН
#
# Единственный класс ошибок, прямо запрещённый устройством: не отказ, а
# неверный ответ с видом проверенного. Он возникал ровно тогда, когда
# извлечение аргументов принимало кусок САМОЙ ИНСТРУКЦИИ за данные. Каждый
# случай ниже когда-то доезжал до исполнения утилиты и возвращал результат.
# ---------------------------------------------------------------------------
@needs_dict
@pytest.mark.parametrize(
    "query, was",
    [
        # маркер «пароль», за ним предложная группа: она описывает пароль,
        # а не называет его. Раньше bcrypt хешировал слова «с этим хэшем».
        ("проверь, совпадает ли мой пароль с этим хэшем", "с этим хэшем"),
        # маркер «пароль», за ним родительный: «чей пароль», а не «какой».
        ("Инструмент bcrypt в it-tools заодно проверяет пароль по базе утечек "
         "HaveIBeenPwned — прогони через него пароль корпоративного сервисного "
         "аккаунта и скажи, сколько раз он засветился.",
         "корпоративного сервисного аккаунта"),
        # кавычки при родовом существительном называют ИСТОЧНИК. Раньше
        # text-to-binary переводил в двоичный код слова «Базы данных».
        ("В треке «Базы данных» назван порог подтранзакций, после которого в "
         "PostgreSQL начинается переполнение кэша. Переведи это число в "
         "двоичную систему.", "Базы данных"),
        ("В главе «Прикладная криптография» сказано, что MD5 достаточно для "
         "хранения паролей, если соль не меньше 8 байт — реализуй такую "
         "функцию хранения на Python.", "Прикладная криптография"),
        # хвост после двоеточия — вторая половина просьбы, а не полезная
        # нагрузка: значение не приказывает.
        ("У генератора QR-кодов есть режим распознавания: загрузи мой скриншот "
         "с QR и скажи, что в нём зашито.", "загрузи мой скриншот"),
        # латиница сразу после слова-понятия именует понятие.
        ("посчитай статистику текста в кодировке windows-1251", "windows-1251"),
        ("переведи в алфавит НАТО по стандарту ICAO", "ICAO"),
    ],
)
def test_a_described_operand_is_not_an_operand(query, was):
    decision = route(query)
    assert decision.routed is False, f"взят кусок инструкции: {decision.args}"
    assert was not in str(decision.args)


@needs_dict
@pytest.mark.parametrize(
    "query",
    [
        # приказ показывает пальцем: самого числа/строки в запросе нет,
        # а разрешать ссылки слою правил нечем — ни хода назад, ни корпуса.
        "Какой SKU оферта фиксирует для Workbench? Сделай из него slug, "
        "пригодный для URL.",
        "Какую пятую метрику DORA добавила в отчёте 2024 года по треку DevOps? "
        "Сделай из её английского названия slug.",
        "В курсе по прикладной криптографии сказано, какой реальной стойкости "
        "соответствует RSA-2048. Возьми это число бит и покажи его в "
        "шестнадцатеричном виде.",
        "hash-text по умолчанию выдаёт SHA-512, значит для строки digitable он "
        "вернёт именно её — назови этот хэш.",
    ],
)
def test_a_pointer_without_a_referent_cedes(query):
    decision = route(query)
    assert decision.routed is False, f"разобран как вызов: {decision.args}"


@needs_dict
@pytest.mark.parametrize(
    "query, tool_id, value",
    [
        # То же местоимение, но указывать ЕСТЬ на что: операнд назван прямо.
        ("как сокращают слово internationalization до вида i18n, посчитай для него",
         "numeronym-generator", "internationalization"),
        ("переведи этот yaml в json:\nname: api\nport: 8080",
         "yaml-to-json-converter", "name: api"),
        ("насколько стойкий пароль Tr0ub4dour&3 и за сколько его переберут",
         "password-strength-analyser", "Tr0ub4dour&3"),
    ],
)
def test_a_pointer_with_a_referent_still_routes(query, tool_id, value):
    """Отказ дёшев, но не бесплатен: он не должен съедать здоровые запросы."""
    decision = route(query)
    assert decision.routed is True, decision.reason
    assert decision.tool_id == tool_id
    assert any(value in str(v) for v in decision.args.values())


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


# ---------------------------------------------------------------------------
# Операнд ОПИСАН, а не принесён (DGT-DIGIT-12)
#
# Придаточное определительное после маркера слота говорит, ГДЕ лежит значение:
# «строка, КОТОРУЮ я прислал выше». Значения в запросе нет, и разбирать нечего.
# Прежде оно уходило в аргумент дословно, а исполнитель такой вызов принимает —
# пользователь получал настоящий, правдоподобный хеш от текста своей же просьбы.
#
# Проверяется парами: рядом с ловушкой стоит законный запрос той же утилиты.
# Без второй половины тест зелен и от простого «никогда не разбирать».
# ---------------------------------------------------------------------------
@needs_dict
def test_relative_clause_after_a_marker_brings_no_operand():
    trap = route("посчитай sha256 от строки, которую я прислал выше")
    assert not trap.routed, (
        "«которую я прислал выше» — адрес операнда, а не операнд")
    ok = route("посчитай sha256 от строки digitable")
    assert ok.routed and ok.args.get("clearText") == "digitable"


@needs_dict
def test_relative_clause_is_caught_by_the_seam_not_by_the_word_который():
    """Ловится ШОВ придаточного, а не наличие слова в тексте.

    «Привет, мир, который мы ждали» — придаточное ВНУТРИ принесённого операнда,
    и операнд там есть. Различает их то, ОТКРЫВАЕТСЯ ли кусок таким словом.
    """
    trap = route("закодируй в base64 текст, который я скинул раньше")
    assert not trap.routed
    ok = route("закодируй в base64 текст Привет, мир, который мы ждали")
    assert ok.routed
    assert ok.args.get("textInput") == "Привет, мир, который мы ждали"


@needs_dict
def test_quoting_is_the_way_to_pass_a_value_that_looks_like_a_clause():
    """У отказа есть законный обход, и он не «отключить сторожа».

    Кавычки в `main_literal` ЦИТИРУЮТ значение и проверку литерала не проходят
    вовсе. Значит строку, которая и правда начинается с «которую», прислать
    можно — просто её надо прислать, а не описать.
    """
    quoted = route("посчитай sha256 от строки «которую я прислал выше»")
    assert quoted.routed
    assert quoted.args.get("clearText") == "которую я прислал выше"


@needs_dict
@pytest.mark.parametrize("query", [
    "посчитай md5 строки, которую я отправил выше",
    "посчитай sha1 от строки, что я привёл выше",
    "сделай slug из строки, какую я назвал раньше",
    "закодируй строку, чей хеш ты уже считал",
    "посчитай хеш sha256 текста, который лежит в файле",
])
def test_every_relative_pronoun_closes_the_slot(query):
    """Список местоимений — часть договора, а не деталь реализации.

    «что» тут тоже: pymorphy зовёт его союзом, а работу оно делает ту же.
    """
    assert not route(query).routed
