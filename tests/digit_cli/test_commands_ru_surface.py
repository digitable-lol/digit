# -*- coding: utf-8 -*-
"""Русская поверхность каталога команд: сторож против дрейфа и неоднозначности.

Зачем поверхность вообще нужна (DGT-DIGIT-03)
---------------------------------------------
Слой правил (`digit_cli/ruleparse`) сопоставляет русскую фразу с ИНСТРУМЕНТОМ по
русскому лексикону, выведенному из каталога инструментов. С КОМАНДАМИ так
нельзя: `CommandDef` несёт name/description/args_hint только по-английски, а
`agent/i18n.py` описания команд из локализации исключает намеренно («slash-command
descriptions all stay in English»). Правилам не по чему сопоставлять — и это
названный блокер задачи, не требующий чьего-либо решения.

Файл `digit_cli/ruleparse/commands_ru.json` закрывает его, не отменяя решения про
i18n: он не показывается пользователю и ничего не заменяет, он только даёт
русские имена и формулировки, ПО КОТОРЫМ можно сопоставлять.

Что держит этот сторож — и почему именно это
--------------------------------------------
1. Каждая запись обязана называть НАСТОЯЩУЮ команду. Русское имя, за которым
   нет команды, — это разбор, который уверенно приведёт в никуда.
2. Каждая команда области обязана иметь запись. Без этого условия поверхность
   молча отстанет от реестра: команду добавят, разбор её не увидит, и виноват
   будет «плохо понимает по-русски», а не отсутствующая строка.
3. Ни одна фраза не принадлежит двум командам. Это не аккуратность: слой правил
   обязан быть детерминированным, а фраза, подходящая к двум командам, ровно в
   этом месте его детерминированность и отменяет.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from digit_cli.commands import COMMAND_REGISTRY

SURFACE = Path(__file__).parent.parent.parent / "digit_cli" / "ruleparse" / "commands_ru.json"


def _surface() -> dict:
    return json.loads(SURFACE.read_text(encoding="utf-8"))["команды"]


def _all_surface_commands() -> dict:
    """Команды, доступные на всех поверхностях. Считаются ИЗ реестра, не из списка."""
    return {c.name: c for c in COMMAND_REGISTRY if not c.cli_only and not c.gateway_only}


def test_every_entry_names_a_real_command():
    unknown = sorted(set(_surface()) - {c.name for c in COMMAND_REGISTRY})
    assert not unknown, f"русская поверхность называет несуществующие команды: {unknown}"


def test_every_all_surface_command_is_covered():
    missing = sorted(set(_all_surface_commands()) - set(_surface()))
    assert not missing, (
        "у этих команд нет русской поверхности — разбор их не увидит: " + ", ".join(missing)
    )


def test_no_phrase_belongs_to_two_commands():
    owner: dict[str, str] = {}
    clashes = []
    for name, entry in _surface().items():
        for phrase in entry["фразы"]:
            key = re.sub(r"\s+", " ", phrase.strip().lower())
            if key in owner:
                clashes.append((key, owner[key], name))
            owner[key] = name
    assert not clashes, f"одна фраза у двух команд: {clashes}"


def test_entries_are_actually_russian_and_not_placeholders():
    """Пустая или английская запись хуже отсутствующей: сторож 2 её примет."""
    cyrillic = re.compile(r"[а-яё]", re.IGNORECASE)
    for name, entry in _surface().items():
        assert entry["имя"].strip(), name
        assert cyrillic.search(entry["имя"]), f"{name}: имя не по-русски"
        assert cyrillic.search(entry["описание"]), f"{name}: описание не по-русски"
        assert len(entry["фразы"]) >= 2, f"{name}: меньше двух формулировок"
        for phrase in entry["фразы"]:
            assert cyrillic.search(phrase), f"{name}: формулировка не по-русски: {phrase!r}"


def test_surface_does_not_touch_the_english_catalogue():
    """Решение про i18n не отменяется: description команд остаётся английским.

    Если однажды кто-то решит показывать русские описания пользователю, это
    будет отдельное решение с отдельным тестом — а не побочный эффект файла,
    заведённого для разбора.
    """
    for command in COMMAND_REGISTRY:
        assert not re.search(r"[а-яё]", command.description or "", re.IGNORECASE), (
            f"{command.name}: русский текст уехал в английский каталог команд"
        )
