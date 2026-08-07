"""Слой проверки суждений: три исхода, и третий — не разновидность второго.

Проверяется не «умеет ли разборщик разбирать» — это измерено на 1 161 замере
и переизмеряется харнессом digit-ml, а не здесь. Здесь проверяются свойства,
ради которых слой встроен и которые сломать можно случайно:

  * отказ дёшев и наступает РАНЬШЕ компилятора: незнакомую формулировку
    переспрашивают, а не догадываются о ней;
  * разбор идёт только над объявленной схемой: незнакомое поле — отказ;
  * граница печатается во ВСЕХ трёх исходах, включая зелёный;
  * правило проверяется в контексте уже объявленного расчёта, иначе зелёный
    приговор достаётся правилу, которое расчёт ломает;
  * отсутствие компилятора — названный отказ, а не молчаливая деградация.
"""

from __future__ import annotations

import pytest

from digit_cli import claimcheck
from digit_cli.claimcheck import bridge, pipeline

needs_runtime = pytest.mark.skipif(
    not claimcheck.runtime_available(),
    reason="нет компилятора FTS: `digit mcp install fts-gate` либо DIGIT_FTS_GATE_HOME",
)


ORDER = claimcheck.Schema(
    [{"name": "Заказ", "fields": [
        {"name": "сумма заказа", "type": "Деньги", "optional": False},
        {"name": "постоянный клиент", "type": "Признак", "optional": False},
        {"name": "статус", "type": "Строка", "optional": False},
    ]}],
    {"name": "Рассчитать скидку", "input": "Заказ", "output": "Деньги", "initial": 0},
)


def _explode(*_args, **_kwargs):
    """Компилятор, который обязан не понадобиться."""
    raise AssertionError("отказ разбора не имеет права стоить запуска компилятора")


# ---------------------------------------------------------------------------
# Отказ дёшев: он наступает до компилятора и без него
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "statement, code",
    [
        # Поле не объявлено — над чужой схемой слой не работает и не делает вид.
        ("если вес посылки больше 10, то прибавить 5", "FIELD_UNKNOWN"),
        # Следствие недосказано: «скидка 10 процентов» — от чего?
        ("если сумма заказа больше 1000, скидка 10 процентов", "ACTION_BAD"),
        # Не утверждение вовсе.
        ("посчитай мне что-нибудь", "NO_FRAME"),
    ],
)
def test_a_refusal_costs_nothing_and_names_itself(statement, code, monkeypatch):
    monkeypatch.setattr(pipeline, "run_verify", _explode)

    result = claimcheck.answer([statement], ORDER, "Продажи")

    assert result["outcome"] == "не_формализовано"
    assert result["refusals"][0]["code"] == code
    assert result["refusals"][0]["statement"] == statement
    # Причина обязана быть названа: её читает человек, чтобы переформулировать.
    assert result["refusals"][0]["detail"]


def test_ambiguous_field_is_refused_not_guessed(monkeypatch):
    """Два поля подходят одинаково — монетка вместо ответа недопустима.

    Это самый дорогой вид ошибки: догадка компилируется, сертифицируется и
    приезжает к человеку с видом проверенной.
    """
    monkeypatch.setattr(pipeline, "run_verify", _explode)
    twin = claimcheck.Schema(
        [{"name": "Трафик", "fields": [
            {"name": "минут израсходовано", "type": "Число", "optional": False},
            {"name": "гигабайт израсходовано", "type": "Число", "optional": False},
        ]}],
        {"name": "Начислить", "input": "Трафик", "output": "Число", "initial": 0},
    )

    result = claimcheck.answer(["если израсходовано больше 10, то прибавить 5"],
                               twin, "Связь")

    assert result["outcome"] == "не_формализовано"
    assert result["refusals"][0]["code"] == "FIELD_AMBIGUOUS"


def test_the_boundary_is_attached_even_to_a_refusal(monkeypatch):
    monkeypatch.setattr(pipeline, "run_verify", _explode)

    result = claimcheck.answer(["если вес посылки больше 10, то прибавить 5"],
                               ORDER, "Продажи")

    assert result["note"] == claimcheck.LIMIT_NOTE
    assert "нет модели мира" in result["note"]


def test_the_boundary_says_what_was_not_checked():
    """Граница — не вежливая приписка, а содержательное «чего здесь нет».

    Она обязана называть именно истинность посылки, а не «возможны ошибки»:
    расплывчатая оговорка не мешает человеку поверить зелёному приговору.
    """
    assert "НЕ проверено, верна ли сама посылка" in claimcheck.LIMIT_NOTE
    assert "НДС" in claimcheck.LIMIT_NOTE


# ---------------------------------------------------------------------------
# Отсутствие компилятора — отказ, а не деградация
# ---------------------------------------------------------------------------
def test_missing_compiler_is_a_named_refusal(monkeypatch):
    monkeypatch.setenv(bridge.ENV_GATE_HOME, "/nonexistent/fts-gate")
    monkeypatch.delenv(bridge.ENV_FTS_HOME, raising=False)

    assert bridge.runtime_available() is False
    with pytest.raises(bridge.RuntimeMissing) as error:
        bridge.resolve()

    # Отказ обязан быть действенным: путь, который искали, и переменная,
    # которой его чинят. «Что-то не так с окружением» починить нельзя.
    assert "/nonexistent/fts-gate" in str(error.value)
    assert bridge.ENV_GATE_HOME in str(error.value)


def test_runtime_probe_never_raises(monkeypatch):
    """Проба — это вопрос, а не действие: падать на нём нечему."""
    monkeypatch.setenv(bridge.ENV_GATE_HOME, "/nonexistent/fts-gate")
    assert bridge.runtime_available() in (True, False)


def test_the_verifier_script_ships_with_the_package():
    """verify.mjs — вход алгоритма, а не файл чьей-то рабочей копии."""
    assert bridge._SCRIPT.is_file()
    assert bridge._SCRIPT.read_text(encoding="utf-8").startswith("#!/usr/bin/env node")


# ---------------------------------------------------------------------------
# Три исхода на настоящем компиляторе
# ---------------------------------------------------------------------------
@needs_runtime
def test_a_well_formed_rule_is_verified_and_read_back():
    result = claimcheck.answer(
        ["если сумма заказа больше 1000, то прибавить 10 процентов от суммы заказа"],
        ORDER, "Продажи")

    assert result["outcome"] == "проверено_верно"
    # Обратное чтение — главное, что видит человек: сверить с задуманным он
    # обязан ДО того, как поверит зелёному приговору.
    assert result["reading"] == [
        "если «сумма заказа» строго больше 1000, "
        "то прибавить к результату 10 процентов от поля «сумма заказа»"
    ]
    assert result["note"] == claimcheck.LIMIT_NOTE
    # Свидетельство: примеры, посчитанные настоящим интерпретатором.
    assert result["examples"]


@needs_runtime
def test_a_violated_property_names_the_check_and_a_counterexample():
    result = claimcheck.answer(
        ["если сумма заказа больше 1000, то прибавить 2000", "результат не больше 500"],
        ORDER, "Продажи")

    assert result["outcome"] == "проверено_неверно"
    assert result["failed_check"]["code"] == "FTS_UTILITY_PROPERTY"
    assert "контрпример" in result["failed_check"]["detail"]


@needs_runtime
def test_a_numeric_gap_between_thresholds_is_caught():
    """Дефект вывода, которого не видно глазами и который компилятор пропускает."""
    result = claimcheck.answer(
        ["если сумма заказа не меньше 1000, то прибавить 100",
         "если сумма заказа не больше 500, то прибавить 50"],
        ORDER, "Продажи")

    assert result["outcome"] == "проверено_неверно"
    assert result["failed_check"]["code"] == "NON_EXHAUSTIVE"
    assert result["verdict"]["fallacies"][0]["kind"] == "numeric_gap"


@needs_runtime
def test_a_factually_false_premise_still_passes_green():
    """Ровно та сцена, ради которой граница печатается в КАЖДОМ ответе.

    Экспорт облагается НДС по ставке 0 %, а не 20 %. Утверждение содержательно
    ложно, и система выдаёт по нему полноценное «проверено и верно» — потому
    что проверена выводимость следствия из посылки, а посылку проверить нечем.
    Если этот тест однажды покраснеет, значит кто-то приписал системе модель
    мира, которой у неё нет.
    """
    export = claimcheck.Schema(
        [{"name": "Отгрузка", "fields": [
            {"name": "стоимость товара", "type": "Деньги", "optional": False},
            {"name": "направление", "type": "Строка", "optional": False},
        ]}],
        {"name": "Начислить НДС", "input": "Отгрузка", "output": "Деньги", "initial": 0},
    )

    result = claimcheck.answer(
        ["если направление равно «экспорт», то прибавить 20 процентов от стоимости товара"],
        export, "Налоги")

    assert result["outcome"] == "проверено_верно"
    assert result["note"] == claimcheck.LIMIT_NOTE


# ---------------------------------------------------------------------------
# Правило проверяется в контексте объявленного расчёта
# ---------------------------------------------------------------------------
@needs_runtime
def test_an_empty_context_reproduces_the_measured_behaviour():
    """Пустые base_* обязаны ничего не менять.

    На них слой и измерен; если приклеивание пустых списков что-то сдвигает,
    цифры из NOTICE перестают относиться к этому коду.
    """
    statement = ["если сумма заказа больше 1000, то прибавить 100"]
    bare = claimcheck.answer(statement, ORDER, "Продажи")
    empty = claimcheck.answer(statement, ORDER, "Продажи",
                              base_rules=[], base_properties=[], base_examples=[])

    assert bare["outcome"] == empty["outcome"] == "проверено_верно"
    assert bare["fts"] == empty["fts"]
    assert bare["reading"] == empty["reading"]


@needs_runtime
def test_a_rule_that_breaks_a_declared_property_is_refuted():
    """Само по себе правило безупречно — и всё равно ломает расчёт.

    Без объявленных свойств оно получило бы зелёный приговор. Это и есть
    цена проверки правила в одиночку, и платить её нельзя.
    """
    statement = ["если постоянный клиент равен да, то прибавить 900"]
    alone = claimcheck.answer(statement, ORDER, "Продажи")
    assert alone["outcome"] == "проверено_верно"

    declared_cap = {"name": "Скидка ограничена", "operator": "lte",
                    "value": {"kind": "value", "value": 500}}
    in_context = claimcheck.answer(statement, ORDER, "Продажи",
                                   base_properties=[declared_cap])

    assert in_context["outcome"] == "проверено_неверно"
    assert in_context["failed_check"]["code"] == "FTS_UTILITY_PROPERTY"
    # Читается всё равно только то, что человек написал сейчас.
    assert len(in_context["reading"]) == 1


@needs_runtime
def test_a_rule_that_breaks_a_declared_example_is_refuted():
    """Объявленный пример — чужое ожидание, и только оно способно упасть.

    Примеры, посчитанные нашим же интерпретатором минуту назад, сходятся по
    построению и ничего не проверяют. Разошедшийся объявленный пример — это
    ответ «новое правило меняет уже согласованный результат».
    """
    declared = {"name": "Мелкий заказ", "input": {"сумма заказа": 2000,
                                                  "постоянный клиент": False,
                                                  "статус": "оплачен"},
                "expected": 0}

    result = claimcheck.answer(["если сумма заказа больше 1000, то прибавить 100"],
                               ORDER, "Продажи", base_examples=[declared])

    assert result["outcome"] == "проверено_неверно"
    assert result["failed_check"]["code"] == "FTS_EXAMPLE_MISMATCH"
    assert "Мелкий заказ" in result["failed_check"]["detail"]


# ---------------------------------------------------------------------------
# Схему даёт сам компилятор, а не второй разборщик
# ---------------------------------------------------------------------------
@needs_runtime
def test_the_schema_comes_from_the_compiler(tmp_path):
    spec = tmp_path / "заказ.fts"
    spec.write_text(
        "категория «Продажи»\n\n"
        "  структура «Заказ»\n"
        "    «сумма заказа» является деньгами\n"
        "    «скидка» иногда является числом\n\n"
        "  утилита «Рассчитать скидку»\n"
        "    принимает «Заказ»\n"
        "    возвращает деньги\n"
        "    начинает с 0\n",
        encoding="utf-8",
    )

    document = claimcheck.schema_of(spec.read_text(encoding="utf-8"))

    assert document["category"] == "Продажи"
    assert document["utilities"][0]["name"] == "Рассчитать скидку"
    fields = {f["name"]: f["type"] for f in document["structures"][0]["fields"]}
    assert fields["сумма заказа"] == "Деньги"
    # Необязательность компилятор хранит внутри имени типа — команда обязана
    # развернуть это обратно, иначе поле тихо станет полем другого типа.
    assert fields["скидка"] == "Число | undefined"


@needs_runtime
def test_an_uncompilable_spec_is_a_refusal_not_a_verdict():
    with pytest.raises(bridge.RuntimeMissing):
        claimcheck.schema_of("это не спецификация FTS")
