# -*- coding: utf-8 -*-
"""Класс задач «операнд описан, а не принесён» — набор, которого не было в стенде.

Зачем файл с данными, а не набор тестов на месте
------------------------------------------------
Дефект DGT-DIGIT-12 дожил до находки не потому, что его прятали, а потому что
измерительный стенд (`/home/a/projects/digit-ml/eval`, 630 задач в 12 файлах) не
содержал НИ ОДНОЙ задачи этого класса. Дифференциальный прогон правки a235c1896
по всем 630 показал ноль изменённых решений — то есть стенд к этому дефекту
слеп, и любая следующая правка разбора снова прошла бы по нему незамеченной.

Поэтому набор лежит рядом как ДАННЫЕ в формате стенда
(`tests/digit_cli/vectors/ho_operand_described.json`, та же схема, что у
`eval/tasks_holdout/ho_bait_argument.json`: `mode_expected`, `expected.refuse`,
`expected.missing_params`, `expected.must_not_contain`, `refusal_reason_expected`).
Файл копируется в стенд без переделки, а до тех пор его сторожит этот тест.

`must_not_contain` — не проза, а оракул: там лежит и утёкший «операнд»
(«которую я прислал выше»), и НАСТОЯЩИЙ ответ, который получался от него
(sha256 = b2236d08…). Промах этого класса молчаливый: пользователю уезжает
исполненный и правдоподобный ответ на текст его же просьбы, поэтому ловить его
можно только так — по содержимому ответа, а не по коду возврата.

Что измерено этим набором (числа сняты прогоном, не переписаны)
--------------------------------------------------------------
    origin/main         a091c49d2   17 ловушек из 24 разбираются и исполняются
    work/restore-after-reset eb2855d6d 3 из 24
    здесь                            0 из 24
Законных запросов тех же утилит 13, и они разбираются на всех трёх ревизиях —
включая «текст Привет, мир, который мы ждали» (придаточное ВНУТРИ принесённого
операнда), обход кавычками «от строки «которую я прислал выше»» и три случая,
где слово-адрес стоит ВНУТРИ принесённого значения («Порог указан выше»,
«Отчёт получен вовремя», «Указанный порядок работ»). Последние три — цена
сторожа: без них «all» нельзя отличить от «any», и правило, которое отказывает
при ЛЮБОМ слове-адресе, прошло бы прогон незамеченным.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from digit_cli.ruleparse import route

VECTORS = Path(__file__).parent / "vectors" / "ho_operand_described.json"


def _tasks():
    data = json.loads(VECTORS.read_text(encoding="utf-8"))
    return data["tasks"]


def _traps():
    return [t for t in _tasks() if t["mode_expected"] == "refuse"]


def _legit():
    return [t for t in _tasks() if t["mode_expected"] != "refuse"]


@pytest.mark.parametrize("task", _traps(), ids=lambda t: t["id"])
def test_operand_described_is_refused(task):
    """Описанный операнд не становится операндом.

    Проверяется не только отказ: если разбор всё-таки состоялся, ни один
    аргумент не имеет права содержать адрес операнда. Второе условие ловит
    случай «сторож обошли другим путём извлечения», который отказ бы не показал.
    """
    decision = route(task["query"])
    forbidden = [s for s in task["expected"]["must_not_contain"] if s]
    if decision.routed:
        flat = json.dumps(decision.args, ensure_ascii=False).lower()
        leaked = [s for s in forbidden if s in flat]
        assert not leaked, (
            f"{task['id']}: адрес операнда уехал в аргументы как значение: "
            f"{leaked} в {decision.args}"
        )
        pytest.fail(
            f"{task['id']}: разбор состоялся на запросе, где операнда нет: "
            f"{decision.tool_id} {decision.args}"
        )


@pytest.mark.parametrize("task", _legit(), ids=lambda t: t["id"])
def test_operand_carried_still_parses(task):
    """Ловится шов, а не слово: принесённый операнд разбирается по-прежнему.

    Без этой половины сторож нельзя отличить от «выключить разбор»: отказ на
    всём тоже даёт ноль ложных ответов и ноль пользы.
    """
    decision = route(task["query"])
    assert decision.routed, f"{task['id']}: законный запрос перестал разбираться"
    assert decision.tool_id == task["expected"]["tool_id"]
    for name, value in (task["expected"].get("args") or {}).items():
        assert decision.args.get(name) == value, (
            f"{task['id']}: {name} = {decision.args.get(name)!r}, ждали {value!r}"
        )


def test_set_covers_more_than_one_utility():
    """Набор описывает КЛАСС, а не один запрос.

    Один пример закрыла бы любая правка про одну строку. Класс держится тем,
    что ловушки размазаны по разным утилитам и по разным способам сослаться на
    операнд — придаточным, наречием места, причастием.
    """
    traps = _traps()
    tools = {t["expected"]["tool_id"] for t in traps}
    assert len(tools) >= 8, sorted(tools)
    assert len(traps) >= 24, len(traps)
    assert len(_legit()) >= 13, len(_legit())


def test_every_trap_names_the_missing_slot_and_the_wrong_answer():
    """Каждая задача набора годится для стенда, а не только для этого теста.

    Стенд оценивает ответ модели, а не решение правил, поэтому у задачи обязаны
    быть и пропущенный слот, и запрещённая подстрока. Задача без них проходит
    любой прогон и ничего не измеряет.
    """
    for task in _traps():
        assert task["expected"]["refuse"] is True, task["id"]
        assert task["expected"]["missing_params"], task["id"]
        assert task["expected"]["must_not_contain"], task["id"]
        assert task["refusal_reason_expected"], task["id"]
