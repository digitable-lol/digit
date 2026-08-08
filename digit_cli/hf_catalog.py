"""Каталог наших открытых моделей и датасетов: `digit model --catalog`.

Зачем команда вообще есть. Модели, на которых стоит Digit, выложены открыто, и
до сих пор узнать об этом можно было только с портала. Человек, у которого уже
установлен агент, туда не пойдёт — он спросит у самого агента. Каталог с
объяснением «зачем она, чем подтверждено и чего она не умеет» обязан быть в
терминале, а не только на странице.

Данные читаются из ``hf_catalog.toml``, лежащего рядом. Это единственный
источник правды: тот же файл побайтово скопирован в портал
(``courses/data/hf-catalog.toml``), и расхождение копии ловит проверка
``npm run hf:catalog:check``. Второго списка нет намеренно — два списка
расходятся, и обнаруживает это читатель, а не сборка.

Печатается ВЕСЬ состав записи, включая ``limits``. Раздел границ здесь не
украшение и не осторожность: каталог, где у каждой модели названы только
сильные стороны, читается как реклама, и вместе с ней перестают верить и
числам. Поэтому вывод без границ невозможен — их нельзя отключить флагом.
"""

from __future__ import annotations

import re
import textwrap
import tomllib
from pathlib import Path
from typing import Any

from digit_cli.colors import Colors, color

CATALOG_PATH = Path(__file__).with_name("hf_catalog.toml")

# Ширина обёртки. Не берётся у терминала намеренно: вывод часто уезжает в файл
# или в чужой лог, и там ширина «текущего окна» — это ширина окна автора, а не
# читателя. 88 — компромисс между длинной строкой и рваным столбцом.
WRAP = 88

_KIND_TITLES = {
    "model": "Модели",
    "dataset": "Датасеты",
}

_ROLE_LABELS = {
    "shipping": "в поставке",
    "reference": "эталон для сравнения",
    "negative": "отрицательный результат",
    "training": "обучающие данные",
    "eval": "набор задач для замеров",
}


class CatalogError(RuntimeError):
    """Каталог не прочитался. Отдельный тип, чтобы CLI отличил его от прочего."""


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    """Прочитать каталог. Ошибку не глотает: пустой каталог хуже отсутствия."""
    target = path or CATALOG_PATH
    try:
        with open(target, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError as exc:  # pragma: no cover - зависит от сборки
        raise CatalogError(
            f"каталог не найден: {target}. Проверьте, что hf_catalog.toml "
            "попал в поставку (pyproject.toml, [tool.setuptools.package-data])"
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise CatalogError(f"каталог не разобрался: {target}: {exc}") from exc

    entries = data.get("entry")
    if not entries:
        raise CatalogError(f"в каталоге {target} нет ни одной записи")
    return data


# Склейка «число + знак процента» на время переноса.
#
# textwrap рвёт строку по любому пробелу, а `\s` в Python совпадает и с
# неразрывным U+00A0, поэтому обычный приём «поставить nbsp» здесь не работает.
# Пробел заменяется символом, которого в тексте быть не может, и возвращается
# после переноса. Иначе «9,3 %» регулярно уезжает знаком на следующую строку —
# в каталоге, который весь состоит из чисел, это читается как опечатка.
_GLUE = "\x00"
_GLUED = ((" %", f"{_GLUE}%"), (" п.п.", f"{_GLUE}п.п."))
# Разряды числа («4 400», «444 414 752») и номер раздела после «§» — тоже
# неразрывные группы: перенос внутри них превращает одно число в два.
_GLUE_GROUPS = re.compile(r"(?<=\d) (?=\d)|(?<=§) ")


def _fill(text: str, initial: str, subsequent: str) -> str:
    clean = " ".join(str(text).split())
    if not clean:
        return ""
    for space, glued in _GLUED:
        clean = clean.replace(space, glued)
    clean = _GLUE_GROUPS.sub(_GLUE, clean)
    filled = textwrap.fill(clean, width=WRAP, initial_indent=initial, subsequent_indent=subsequent)
    return filled.replace(_GLUE, " ")


def _wrap(text: str, indent: str = "") -> str:
    """Свернуть абзац по ширине. Пустой текст даёт пустую строку, а не 'None'."""
    return _fill(text, indent, indent)


def _bullet(text: str, marker: str = "  · ", indent: str = "    ") -> str:
    return _fill(text, marker, indent)


def format_catalog(data: dict[str, Any], *, use_color: bool = True) -> str:
    """Собрать печатный вид каталога.

    Отделено от печати, чтобы тест сверял текст, а не перехватывал stdout: у
    вывода есть обязательный состав (границы у каждой записи), и проверять его
    надо утверждением о строке.
    """

    def paint(text: str, *codes: str) -> str:
        return color(text, *codes) if use_color else text

    lines: list[str] = []
    lines.append(paint("Открытые модели и датасеты Digitable", Colors.BOLD))
    lines.append(paint(f"  {data['org_url']}", Colors.DIM))
    lines.append("")
    lines.append(
        _wrap(
            "Веса, обучающие данные и набор задач выложены открыто, чтобы числа "
            "ниже можно было перепроверить, а не принять на слово. У каждой "
            "записи названы граница и то, чего она не умеет.",
        )
    )

    for kind in ("model", "dataset"):
        group = [e for e in data["entry"] if e.get("kind") == kind]
        if not group:
            continue
        lines.append("")
        lines.append(paint(f"{_KIND_TITLES[kind]} ({len(group)})", Colors.BOLD))

        for entry in group:
            role = _ROLE_LABELS.get(entry.get("role", ""), entry.get("role", ""))
            lines.append("")
            lines.append(paint(f"  {entry['id']}", Colors.CYAN, Colors.BOLD) + paint(f"  — {role}", Colors.DIM))
            lines.append(paint(f"  {entry['url']}", Colors.DIM))
            lines.append("")
            lines.append(_wrap(entry["title"] + ". " + entry["tagline"], indent="  "))
            lines.append("")
            lines.append(paint("  Зачем", Colors.YELLOW))
            lines.append(_wrap(entry["purpose"], indent="    "))

            evidence = entry.get("evidence") or []
            lines.append("")
            if evidence:
                lines.append(paint("  Чем подтверждено", Colors.YELLOW))
                for item in evidence:
                    lines.append(
                        _bullet(f"{item['value']} — {item['claim']} ({item['source']})")
                    )
            else:
                lines.append(paint("  Чем подтверждено", Colors.YELLOW))
                lines.append(_bullet("замеров нет"))

            lines.append("")
            lines.append(paint("  Чего не умеет", Colors.YELLOW))
            for limit in entry.get("limits") or ["границы не измерены"]:
                lines.append(_bullet(limit))

            lines.append("")
            lines.append(_wrap(f"Размер: {entry['size']}", indent="    "))
            lines.append(_wrap(f"Основа: {entry['base']}", indent="    "))
            lines.append(_wrap(f"Обучение: {entry['trained_on']}", indent="    "))
            lines.append(_wrap(f"Лицензия: {entry['license']}", indent="    "))
            if entry.get("command"):
                lines.append(_wrap(f"Запуск: {entry['command']}", indent="    "))

    lines.append("")
    lines.append(
        _wrap(
            "Числа сняты нашими же прогонами; источник каждого назван в скобках "
            "и лежит в карточке репозитория. Подробный разбор — "
            "https://courses.digitable.life/digit/models/",
        )
    )
    return "\n".join(lines)


def print_catalog(*, use_color: bool = True) -> int:
    """Напечатать каталог. Код возврата — как у обычной команды CLI."""
    from digit_cli.cli_output import print_error

    try:
        data = load_catalog()
    except CatalogError as exc:
        print_error(str(exc))
        return 1

    print(format_catalog(data, use_color=use_color))
    return 0
