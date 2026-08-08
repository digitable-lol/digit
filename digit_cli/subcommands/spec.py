"""Парсер подкоманды ``digit spec``.

Отдельным модулем — по образцу остальных подкоманд: main.py и без того
god-file, а обработчик сюда не импортируется, а прокидывается.
"""

from __future__ import annotations

import argparse
from typing import Callable


def build_spec_parser(subparsers, *, cmd_spec: Callable) -> None:
    """Прицепить подкоманду ``spec``."""
    from digit_cli.specgen.model import BRIEF_SHAPE

    parser = subparsers.add_parser(
        "spec",
        help="Write an FTS specification from a structured request",
        description=(
            "Ask the trained specification generator for an FTS document. The "
            "generated text is compiled, validated and its examples executed by "
            "the real FTS compiler before you ever see it: what does not pass "
            "is regenerated, and what never passes is refused. Nothing "
            "unverified is printed."
        ),
        # Форма задания печатается целиком, а не описывается словами. Она не
        # стилистическая: на свободной формулировке та же модель возвращает
        # вырожденный документ, который проходит компилятор и ничего не
        # вычисляет. Спрятать это в документацию значит раздать грабли.
        epilog=(
            "ЗАДАНИЕ ПИШЕТСЯ ПО ЭТОМУ ОБРАЗЦУ — генератор обучен ровно на нём.\n"
            "На свободной формулировке он возвращает документ, который "
            "компилируется и при этом\nничего не считает.\n\n"
            f"{BRIEF_SHAPE}\n\n"
            "Exit codes: 0 verified and printed, 1 no attempt passed the gate, "
            "4 the check could\nnot happen at all (no generator, no compiler)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # nargs="+", чтобы просьбу можно было писать без кавычек: она почти всегда
    # длиннее одного слова, и требовать кавычек значит собирать баг-репорты про
    # «команда съела половину фразы».
    parser.add_argument(
        "request",
        nargs="+",
        metavar="REQUEST",
        help="The brief, in the shape shown at the bottom of this help: the "
             "object with typed fields, the calculation with its rules, "
             "properties and at least one worked example",
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=None,
        help="How many samples to try before refusing (default: 2; the first "
             "one is greedy — the setting the generator was measured at)",
    )
    parser.add_argument(
        "--out",
        default=None,
        metavar="FILE.fts",
        help="Write the verified specification here instead of stdout",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the full outcome as JSON: text, verdict, per-attempt stages",
    )
    parser.add_argument(
        "--no-autostart",
        action="store_true",
        help="Do not start the generator server; fail if it is not already up",
    )
    parser.set_defaults(func=cmd_spec)
