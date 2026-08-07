"""Парсер подкоманды ``digit rule-check``.

Отдельным модулем — по образцу остальных подкоманд: main.py и без того
god-file, и обработчик сюда не импортируется, а прокидывается.
"""

from __future__ import annotations

from typing import Callable


def build_rule_check_parser(subparsers, *, cmd_rule_check: Callable) -> None:
    """Прицепить подкоманду ``rule-check``."""
    parser = subparsers.add_parser(
        "rule-check",
        help="Check a business rule written in Russian against an FTS specification",
        description=(
            "Add a rule to an existing FTS calculation by writing it in plain "
            "Russian. The statement is parsed by rules (no model), emitted as "
            "an FTS specification, compiled and executed by the real compiler, "
            "then screened by fts-gate for structural fallacies. Prints how the "
            "statement was READ before the verdict, and always prints what the "
            "check does not cover."
        ),
        epilog=(
            "Exit codes: 0 verified, 1 refuted, 3 could not formalize, "
            "4 the check did not happen at all (no compiler, unreadable spec, "
            "ambiguous utility)."
        ),
    )
    parser.add_argument(
        "spec",
        metavar="SPEC.fts",
        help="Existing FTS specification: the declared schema the rule is added to",
    )
    # nargs="+": свойство («результат не больше 500») — такое же утверждение,
    # как правило, и проверяется оно только вместе с правилом, которое обязано
    # его не нарушить. Разрешать одно утверждение за раз значило бы запретить
    # самую полезную проверку.
    parser.add_argument(
        "statement",
        nargs="+",
        metavar="STATEMENT",
        help="Rule(s) and/or property(-ies) in Russian, e.g. "
             "'если сумма заказа больше 1000, то прибавить 100'",
    )
    parser.add_argument(
        "--utility",
        default=None,
        help="Which utility to extend. Required only when the spec declares more than one",
    )
    parser.add_argument(
        "--category",
        default=None,
        help="Override the category header (default: the one declared in the spec)",
    )
    parser.add_argument(
        "--fts",
        action="store_true",
        help="Also print the FTS specification that was compiled",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the full result as JSON (reading, verdict, examples, boundary)",
    )
    parser.set_defaults(func=cmd_rule_check)
