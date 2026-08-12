"""Скомпилированный документ FTS -> схема, над которой разбирают правило.

Почему это отдельный модуль, а не код внутри команды
---------------------------------------------------
Перевод «что вернул компилятор» в «над чем разбирать» — это утверждение о
семантике: какой расчёт дописываем, какие у него поля и какое из них
необязательное. Пока такой перевод жил внутри ``digit rule-check``, у второго
вызывающего (гейт хода агента, DGT-DIGIT-13) не было выбора, кроме написать его
второй раз — а вторая реализация здесь означает вторую версию того, что значит
«необязательное поле», и расхождение между командой и гейтом, невидимое обоим.

Поэтому перевод лежит здесь один, и оба вызывающих зовут его. Компилятор
по-прежнему один и внешний (см. bridge.py): схему даёт он, а не разборщик на
Python.
"""

from __future__ import annotations

from typing import Any


def pick_utility(utilities: list[dict], wanted: str | None) -> dict:
    """Какой расчёт дописываем.

    Молчаливый выбор первой утилиты был бы догадкой о намерении там, где
    спросить стоит одну строку, поэтому при нескольких объявленных расчётах и
    неназванном имени вызывающий обязан получить отказ, а не первую строку.
    """
    if not utilities:
        raise ValueError("в спецификации не объявлено ни одной утилиты — дописывать нечего")
    if wanted:
        for utility in utilities:
            if utility["name"] == wanted:
                return utility
        names = ", ".join(f"«{u['name']}»" for u in utilities)
        raise ValueError(f"утилита «{wanted}» в спецификации не объявлена; есть: {names}")
    if len(utilities) == 1:
        return utilities[0]
    names = ", ".join(f"«{u['name']}»" for u in utilities)
    raise ValueError(f"в спецификации несколько утилит — назовите одну через --utility: {names}")


def split_optional(field: dict) -> dict:
    """Развернуть «иногда является» обратно.

    Компилятор хранит необязательность внутри имени типа («Деньги |
    undefined»), а печать спецификации ждёт отдельный флаг. Без этого шага
    необязательное поле уехало бы в спецификацию как поле состояния с именем
    «Деньги | undefined» — то есть тихо превратилось бы в другой тип.
    """
    name, type_name = field["name"], field["type"]
    optional = type_name.endswith(" | undefined")
    if optional:
        type_name = type_name[: -len(" | undefined")]
    return {"name": name, "type": type_name, "optional": optional}


def schema_of_document(document: dict, utility: str | None = None) -> tuple[Any, dict, str]:
    """Схема, выбранный расчёт и домен из скомпилированного документа.

    Возвращает ровно то, что нужно `pipeline.answer`: саму схему, утилиту (её
    объявленные правила, свойства и примеры проверяются ВМЕСТЕ с новым
    правилом — без них половина детектора слепа) и домен для заголовка.

    Поднимает ValueError, если расчёт назван неоднозначно или у его входной
    структуры нет полей: разбирать правило не над чем — это отказ, а не
    зелёный ответ.
    """
    from .claimparse import Schema

    chosen = pick_utility(document["utilities"], utility)
    structures = [
        {"name": s["name"], "fields": [split_optional(f) for f in s["fields"]]}
        for s in document["structures"]
    ]
    schema = Schema(
        structures,
        {"name": chosen["name"], "input": chosen["input"],
         "output": chosen["output"], "initial": chosen["initial"]},
    )
    if not schema.input_fields:
        raise ValueError(
            f"структура «{chosen['input']}» не объявлена или пуста — "
            "разбирать правило не над чем"
        )
    return schema, chosen, str(document.get("category") or "Правила")
