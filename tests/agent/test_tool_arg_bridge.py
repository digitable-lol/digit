"""Мост имён аргументов: что он обязан переводить и что обязан НЕ переводить.

Тесты здесь не проверяют, верно ли выбран слот, — это не проверяется в
процессе, это измерено исполнением (scripts/prove_tool_arg_bridge.py, нужен
собранный бинарь tools-core). Здесь сторожится другое: СВОЙСТВА таблицы,
которые легко сломать правкой и невозможно заметить глазами.

Главное из них — отказ. Мост, который «выбросит непонятный аргумент и всё-таки
выполнит», страшнее моста, которого нет: он превращает просьбу про md5 в ответ
по умолчанию sha256, и ответ приходит с видом проверенного.
"""

from __future__ import annotations

import pytest

from agent import rule_cascade
from agent.tool_arg_bridge import BRIDGE, translate


# ---------------------------------------------------------------------------
# Отказ переводить
# ---------------------------------------------------------------------------
def test_an_argument_outside_the_bridge_cancels_the_whole_call():
    """Лишний аргумент — уступка целиком, а не вызов без него.

    Именно этот аргумент обычно и есть тот, ради которого звали: алгоритм
    хеша, отступ, основание системы счисления. Утилита молча возьмёт своё
    умолчание, и уверенный неправильный ответ уедет пользователю.
    """
    assert translate("hash-text", {"clearText": "x", "algorithm": "MD5"}) == (
        "hash_text", {"text": "x", "algorithm": "MD5"})
    assert translate("hash-text", {"clearText": "x", "выдуманный": 1}) is None


def test_tools_rejected_by_measurement_are_absent():
    """Шесть инструментов измерение не пропустило — их не должно быть в мосте.

    Список именной, а не «сколько-то»: возвращение любого из них — это
    возвращение конкретного неверного ответа, и тест обязан назвать какого.
    """
    for public in ("http-status-codes",       # строка «418» в целочисленный слот
                   "chmod-calculator",        # словарь прав в строковый слот
                   "temperature-converter",   # русская лемма в английский enum
                   "eta-calculator",          # минуты в слот миллисекунд
                   "email-normalizer"):       # запятая там, где ждут перевод строки
        assert public not in BRIDGE, public


def test_the_symmetric_half_of_percentage_stayed_out_and_the_asymmetric_one_did_not():
    """«X% от Y» симметрична — исполнение слот не определяет, значит не мост.

    А «выросло с X до Y» несимметрично: обратный порядок даёт другой ответ,
    слот различим, и этот вариант остался.
    """
    assert translate("percentage-calculator", {"percentageX": 15, "percentageY": 2400}) is None
    assert translate("percentage-calculator", {"numberFrom": 1200, "numberTo": 1560}) == (
        "percentage_calculate", {"operation": "change", "x": 1200, "y": 1560})


def test_pruned_pairs_take_their_call_down_with_them():
    """Подрезанная пара не значит «переведём остальное»: значит уступку.

    У lorem-ipsum доказан только `paragraphs`; `words` и `sentences`
    исполнением по отдельности не отличаются от слота `seed`.
    """
    assert translate("lorem-ipsum-generator", {"paragraphs": 3}) == (
        "lorem_ipsum_generate", {"paragraphCount": 3})
    assert translate("lorem-ipsum-generator", {"paragraphs": 3, "words": 5}) is None


def test_an_unknown_tool_is_a_cession():
    assert translate("такого-инструмента-нет", {}) is None


# ---------------------------------------------------------------------------
# Направление
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("public,args,core", [
    # У it-tools одно окно на две стороны, у tools-core — две утилиты.
    # Направление обязано опознаваться по НАБОРУ аргументов: слова запроса уже
    # разобраны, и переспрашивать их значило бы гадать второй раз.
    ("base64-string-converter", {"textInput": "привет"}, "base64_encode"),
    ("base64-string-converter", {"base64Input": "0J8="}, "base64_decode"),
    ("url-encoder", {"encodeInput": "а б"}, "url_encode"),
    ("url-encoder", {"decodeInput": "%D0%B0"}, "url_decode"),
    ("html-entities", {"escapeInput": "<b>"}, "html_escape"),
    ("html-entities", {"unescapeInput": "&lt;b&gt;"}, "html_unescape"),
    ("text-to-binary", {"inputText": "Hi"}, "text_to_binary"),
    ("text-to-binary", {"inputBinary": "01001000"}, "binary_to_text"),
    ("text-to-unicode", {"inputText": "Hi"}, "text_to_unicode"),
    ("text-to-unicode", {"inputUnicode": "&#72;"}, "unicode_to_text"),
    ("roman-numeral-converter", {"inputRoman": "XIV"}, "roman_to_arabic"),
    ("roman-numeral-converter", {"inputNumeral": 14}, "arabic_to_roman"),
    ("encryption", {"cypherInput": "т", "cypherSecret": "к"}, "encrypt_text"),
    ("encryption", {"decryptInput": "U2F", "decryptSecret": "к"}, "decrypt_text"),
    ("bcrypt", {"input": "п"}, "bcrypt_hash"),
    ("bcrypt", {"compareHash": "$2a$", "compareString": "п"}, "bcrypt_compare"),
])
def test_the_direction_is_read_off_the_arguments(public, args, core):
    bridged = translate(public, args)
    assert bridged is not None and bridged[0] == core


def test_a_direction_is_never_chosen_when_its_arguments_are_missing():
    """Пустой разбор не имеет права стать вызовом по умолчанию."""
    assert translate("base64-string-converter", {}) is None
    assert translate("encryption", {}) is None
    assert translate("list-converter", {}) is None


# ---------------------------------------------------------------------------
# Целостность таблицы
# ---------------------------------------------------------------------------
def test_every_gate_argument_is_also_translated():
    """Аргумент, по которому опознали направление, обязан иметь свой слот.

    Иначе направление опознаётся по значению, которое потом не доедет до
    утилиты, — и вызов уйдёт наполовину пустым.
    """
    for public, variants in BRIDGE.items():
        for variant in variants:
            for name in variant.when:
                assert name in variant.args, f"{public}: {name} опознаёт, но не переводит"


def test_no_two_public_arguments_share_a_slot():
    """Два имени в один слот — это молчаливая потеря одного из значений."""
    for public, variants in BRIDGE.items():
        for variant in variants:
            slots = list(variant.args.values()) + list(variant.const or {})
            assert len(slots) == len(set(slots)), f"{public} -> {variant.core}"


def test_tools_without_an_executable_counterpart_are_not_in_the_bridge():
    """Два разных «не могу» не должны склеиваться в журнале."""
    assert not (rule_cascade.NO_CORE_TOOL & set(BRIDGE))


def test_the_cascade_cedes_with_a_distinguishable_reason(monkeypatch):
    """Уступка моста и отсутствие аналога различимы в телеметрии.

    Без этого «куда расти дальше» пришлось бы выяснять заново: обе причины
    выглядели бы как одна строка «каскад промолчал».
    """
    from digit_cli.ruleparse.cascade import RuleDecision

    class FakeAgent:
        valid_tool_names = {"tools_execute"}
        session_id = "s"
        provider = "llamacpp"
        base_url = ""

    monkeypatch.setattr(rule_cascade, "is_enabled", lambda agent=None: True)
    agent = FakeAgent()

    monkeypatch.setattr("digit_cli.ruleparse.route",
                        lambda _q: RuleDecision(routed=True, tool_id="text-diff",
                                                args={"_operands": "2"}))
    assert rule_cascade.try_turn(agent, "сравни", [], []) is None
    assert agent._last_rule_cascade["detail"] == "no-core-tool"

    monkeypatch.setattr("digit_cli.ruleparse.route",
                        lambda _q: RuleDecision(routed=True, tool_id="eta-calculator",
                                                args={"unitCount": 1,
                                                      "unitPerTimeSpan": 2,
                                                      "timeSpan": 3}))
    assert rule_cascade.try_turn(agent, "когда закончит", [], []) is None
    assert agent._last_rule_cascade["detail"] == "no-arg-bridge"
