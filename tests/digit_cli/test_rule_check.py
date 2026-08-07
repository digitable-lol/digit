"""``digit rule-check``: что человек видит и в каком порядке.

Команда существует ради доверия, а доверие держится на порядке строк, а не на
их наличии. Поэтому проверяется именно он: сначала «прочитано так», потом
приговор, граница — всегда. Плюс коды возврата, чтобы отказ было видно скрипту.

Отдельно проверяется главное условие встраивания: разбор идёт НАД ОБЪЯВЛЕННОЙ
СХЕМОЙ. Схему даёт спецификация, а не текст фразы; фраза без спецификации
командой не принимается вовсе — этого не даёт даже сделать argparse.
"""

from __future__ import annotations

import argparse
import json

import pytest

from digit_cli import claimcheck
from digit_cli.claimcheck import bridge
from digit_cli.rule_check import (
    EXIT_NOT_FORMALIZED,
    EXIT_CANNOT_CHECK,
    EXIT_REFUTED,
    EXIT_VERIFIED,
    cmd_rule_check,
)
from digit_cli.subcommands.rule_check import build_rule_check_parser

needs_runtime = pytest.mark.skipif(
    not claimcheck.runtime_available(),
    reason="нет компилятора FTS: `digit mcp install fts-gate` либо DIGIT_FTS_GATE_HOME",
)

ORDER_SPEC = """категория «Продажи»

  структура «Заказ»
    «сумма заказа» является деньгами
    «постоянный клиент» является признаком

  утилита «Рассчитать скидку»
    принимает «Заказ»
    возвращает деньги
    начинает с 0

    правило «Крупный заказ»
      если «сумма заказа» не меньше 10000
      то добавить 10 процентов от поля «сумма заказа»

    свойство «Скидка ограничена»
      результат не больше 20 процентов от поля «сумма заказа»

    пример «Крупный заказ обычного клиента»
      дано «сумма заказа» равен 10000
      дано «постоянный клиент» равен нет
      ожидается результат равен 1000
"""


#: Правило, которое встаёт в расчёт из ORDER_SPEC без противоречий: 10 % за
#: крупный заказ плюс 5 % за постоянного клиента укладываются в объявленный
#: потолок 20 %. Вынесено в имя, потому что повторяется в половине тестов.
LOYAL_BONUS = "если постоянный клиент равен да, то прибавить 5 процентов от суммы заказа"


def _parse(*argv) -> argparse.Namespace:
    """Разобрать командную строку НАСТОЯЩИМ парсером подкоманды.

    Namespace руками собрать быстрее, но тогда тест перестал бы замечать
    разъехавшиеся имена аргументов — а это ровно тот отказ, который увидит
    пользователь и не увидит набор тестов.
    """
    parser = argparse.ArgumentParser(prog="digit")
    subparsers = parser.add_subparsers(dest="command")
    build_rule_check_parser(subparsers, cmd_rule_check=cmd_rule_check)
    return parser.parse_args(["rule-check", *argv])


@pytest.fixture
def spec(tmp_path):
    path = tmp_path / "заказ.fts"
    path.write_text(ORDER_SPEC, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Форма команды
# ---------------------------------------------------------------------------
def test_a_statement_without_a_specification_is_not_a_command():
    """Первое условие встраивания, выраженное грамматикой самой команды.

    Голое «если сумма больше 1000, скидка 10 %» без известных полей — не
    задача этого слоя, и отклоняться оно должно раньше, чем что-либо успеет
    его прочитать.
    """
    with pytest.raises(SystemExit):
        _parse("если сумма заказа больше 1000, то прибавить 100")


def test_several_statements_are_accepted_at_once(spec):
    """Свойство проверяется только вместе с правилом, которое его не нарушит."""
    args = _parse(str(spec), "если сумма заказа больше 1000, то прибавить 100",
                  "результат не больше 500")
    assert len(args.statement) == 2


def test_the_command_is_registered_in_the_cli():
    """Регистрация в main — то, чего не видно ни из парсера, ни из обработчика."""
    from digit_cli.main import _BUILTIN_SUBCOMMANDS

    assert "rule-check" in _BUILTIN_SUBCOMMANDS


# ---------------------------------------------------------------------------
# Проверять нечем — отказ, а не зелёный приговор
# ---------------------------------------------------------------------------
def test_a_missing_compiler_refuses_with_its_own_exit_code(spec, monkeypatch, capsys):
    monkeypatch.setenv(bridge.ENV_GATE_HOME, "/nonexistent/fts-gate")
    monkeypatch.delenv(bridge.ENV_FTS_HOME, raising=False)

    code = cmd_rule_check(_parse(str(spec), "если сумма заказа больше 1000, то прибавить 100"))

    assert code == EXIT_CANNOT_CHECK
    error = capsys.readouterr().err
    assert "ПРОВЕРИТЬ НЕЧЕМ" in error
    assert bridge.ENV_GATE_HOME in error


def test_an_unreadable_specification_says_so(tmp_path, capsys):
    code = cmd_rule_check(_parse(str(tmp_path / "нет.fts"), "если сумма больше 1, то прибавить 1"))

    assert code == EXIT_CANNOT_CHECK
    assert "не читается спецификация" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Три исхода: что печатается и в каком порядке
# ---------------------------------------------------------------------------
@needs_runtime
def test_a_verified_rule_is_read_back_before_the_verdict(spec, capsys):
    code = cmd_rule_check(_parse(str(spec), LOYAL_BONUS))
    out = capsys.readouterr().out

    assert code == EXIT_VERIFIED
    assert "ПРОВЕРЕНО И ВЕРНО" in out
    assert "«постоянный клиент» равно да" in out
    # Порядок — это и есть смысл команды: приговор, прочитанный раньше
    # прочтения, читается как приговор фразе, а он относится к тому, во что
    # фраза превратилась.
    assert out.index("Прочитано так:") < out.index("ПРОВЕРЕНО И ВЕРНО")


@needs_runtime
def test_the_boundary_is_printed_even_when_the_verdict_is_green(spec, capsys):
    cmd_rule_check(_parse(str(spec), LOYAL_BONUS))
    out = capsys.readouterr().out

    assert claimcheck.LIMIT_NOTE in out
    # Граница стоит последней и относится ко всему выводу, а не к абзацу.
    assert out.rstrip().endswith(claimcheck.LIMIT_NOTE)


@needs_runtime
def test_a_green_verdict_names_the_only_evidence_that_could_have_failed(spec, capsys):
    """Примеры, посчитанные нашим же интерпретатором, сходятся по построению.

    Единственное, что в зелёном ответе МОГЛО не сойтись, — объявленные
    примеры: их ожидания писал автор расчёта. Про них и говорится вслух,
    иначе зелёный приговор выглядит увереннее, чем он есть.
    """
    cmd_rule_check(_parse(str(spec), LOYAL_BONUS))
    out = capsys.readouterr().out

    assert "объявленные примеры расчёта (1) по-прежнему сходятся" in out


@needs_runtime
def test_a_rule_breaking_the_declared_property_is_refuted(spec, capsys):
    """Правило само по себе безупречно и всё равно ломает объявленный расчёт.

    Ради этого случая команда и обязана читать спецификацию целиком, а не
    только её структуры.
    """
    code = cmd_rule_check(_parse(str(spec), "если сумма заказа не меньше 10000, "
                                            "то прибавить 15 процентов от суммы заказа"))
    out = capsys.readouterr().out

    assert code == EXIT_REFUTED
    assert "ПРОВЕРЕНО И НЕВЕРНО" in out
    assert "Скидка ограничена" in out
    assert claimcheck.LIMIT_NOTE in out


@needs_runtime
def test_an_unknown_field_refuses_and_shows_the_declared_ones(spec, capsys):
    """Отказ обязан быть действенным: человек переформулирует по этому списку."""
    code = cmd_rule_check(_parse(str(spec), "если вес посылки больше 10, то прибавить 5"))
    out = capsys.readouterr().out

    assert code == EXIT_NOT_FORMALIZED
    assert "НЕ УДАЛОСЬ ФОРМАЛИЗОВАТЬ" in out
    assert "FIELD_UNKNOWN" in out
    assert "«сумма заказа» (Деньги)" in out
    assert "Догадка здесь была бы хуже отказа" in out
    assert claimcheck.LIMIT_NOTE in out


@needs_runtime
def test_the_refusal_code_is_not_printed_twice(spec, capsys):
    cmd_rule_check(_parse(str(spec), "если вес посылки больше 10, то прибавить 5"))
    out = capsys.readouterr().out

    assert "FIELD_UNKNOWN: FIELD_UNKNOWN" not in out


@needs_runtime
def test_the_three_outcomes_have_three_different_exit_codes(spec):
    """«Не понял» и «понял, и это неверно» — разные утверждения.

    Склеенные в один код возврата, они лгут в обе стороны: скрипт либо
    примет отказ за опровержение, либо опровержение за отказ.
    """
    verified = cmd_rule_check(_parse(str(spec), LOYAL_BONUS))
    refuted = cmd_rule_check(_parse(str(spec), "если сумма заказа не меньше 10000, "
                                               "то прибавить 15 процентов от суммы заказа"))
    unparsed = cmd_rule_check(_parse(str(spec), "если вес посылки больше 10, то прибавить 5"))

    assert {verified, refuted, unparsed} == {EXIT_VERIFIED, EXIT_REFUTED, EXIT_NOT_FORMALIZED}
    # Двойка занята argparse под ошибку в командной строке — путать её с
    # отказом разбора значит терять единственную разницу, ради которой коды есть.
    assert 2 not in {verified, refuted, unparsed}


# ---------------------------------------------------------------------------
# Схема берётся из спецификации, а не из фразы
# ---------------------------------------------------------------------------
@needs_runtime
def test_the_calculation_being_extended_is_named(spec, capsys):
    cmd_rule_check(_parse(str(spec), LOYAL_BONUS))
    out = capsys.readouterr().out

    assert "«Рассчитать скидку»" in out
    assert "1 объявленных правил" in out
    assert "1 свойств" in out


@needs_runtime
def test_several_utilities_without_a_choice_is_a_refusal(tmp_path, capsys):
    """Молчаливый выбор первой утилиты был бы догадкой о намерении."""
    path = tmp_path / "два.fts"
    path.write_text(
        ORDER_SPEC + "\n"
        "  утилита «Рассчитать бонус»\n"
        "    принимает «Заказ»\n"
        "    возвращает число\n"
        "    начинает с 0\n",
        encoding="utf-8",
    )

    code = cmd_rule_check(_parse(str(path), "если постоянный клиент равен да, то прибавить 1"))

    assert code == EXIT_CANNOT_CHECK
    error = capsys.readouterr().err
    assert "--utility" in error
    assert "«Рассчитать бонус»" in error


@needs_runtime
def test_naming_a_utility_that_is_not_declared_lists_the_declared_ones(spec, capsys):
    code = cmd_rule_check(_parse(str(spec), "если постоянный клиент равен да, то прибавить 1",
                                 "--utility", "Рассчитать пеню"))

    assert code == EXIT_CANNOT_CHECK
    assert "«Рассчитать скидку»" in capsys.readouterr().err


@needs_runtime
def test_an_optional_field_survives_the_round_trip(tmp_path, capsys):
    """«иногда является» компилятор хранит внутри имени типа.

    Не развернув это обратно, команда отправила бы в спецификацию поле
    состояния с именем «Число | undefined» — то есть тихо подменила бы тип.
    """
    path = tmp_path / "необязательное.fts"
    path.write_text(
        "категория «Продажи»\n\n"
        "  структура «Заказ»\n"
        "    «сумма заказа» является деньгами\n"
        "    «купон» иногда является числом\n\n"
        "  утилита «Рассчитать скидку»\n"
        "    принимает «Заказ»\n"
        "    возвращает деньги\n"
        "    начинает с 0\n",
        encoding="utf-8",
    )

    code = cmd_rule_check(_parse(str(path), "если купон больше 0, то прибавить 50", "--fts"))
    out = capsys.readouterr().out

    assert code == EXIT_VERIFIED
    assert "«купон» иногда является числом" in out
    assert "undefined" not in out


# ---------------------------------------------------------------------------
# Машинный вывод
# ---------------------------------------------------------------------------
@needs_runtime
def test_json_carries_the_reading_the_verdict_and_the_boundary(spec, capsys):
    """Граница обязана доезжать и до машины: её читает следующий слой."""
    code = cmd_rule_check(_parse(str(spec), LOYAL_BONUS, "--json"))
    payload = json.loads(capsys.readouterr().out)

    assert code == EXIT_VERIFIED
    assert payload["outcome"] == "проверено_верно"
    assert payload["reading"] == ["если «постоянный клиент» равно да, "
                                  "то прибавить к результату 5 процентов от поля «сумма заказа»"]
    assert payload["note"] == claimcheck.LIMIT_NOTE
    assert payload["fts"]


@needs_runtime
def test_json_of_a_refusal_is_a_refusal_not_an_empty_verdict(spec, capsys):
    code = cmd_rule_check(_parse(str(spec), "если вес посылки больше 10, то прибавить 5", "--json"))
    payload = json.loads(capsys.readouterr().out)

    assert code == EXIT_NOT_FORMALIZED
    assert payload["outcome"] == "не_формализовано"
    assert payload["refusals"][0]["code"] == "FIELD_UNKNOWN"
    assert "verdict" not in payload
    assert payload["note"] == claimcheck.LIMIT_NOTE
