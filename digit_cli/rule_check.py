"""``digit rule-check`` — дописать правило к расчёту словами и узнать приговор.

Форма команды следует из первого условия, при котором слой измерялся: разбор
идёт ТОЛЬКО над объявленной схемой. Поэтому спецификация обязательна и стоит
первой, а фраза человека — вторым: команда добавляет правило к существующему
расчёту, а не сочиняет расчёт из фразы.

    digit rule-check заказ.fts "если сумма заказа больше 1000, то прибавить 100"

Что печатается и почему именно это
----------------------------------
1. ПРОЧИТАНО ТАК — обратный перевод построенного правила на русский. Это
   главная строка вывода и она печатается первой во всех исходах, где разбор
   состоялся. Не парафраз ввода, а чтение того, что реально уедет в
   компилятор: расхождение с задуманным человек обязан увидеть ДО того, как
   поверит зелёному приговору. Зелёный приговор на правильно построенном, но
   не том правиле — это ровно тот вид ошибки, ради недопущения которого всё
   остальное здесь и стоит.
2. Приговор: проверено и верно / проверено и неверно с названной упавшей
   проверкой / не удалось формализовать.
3. Граница — во всех трёх исходах, включая зелёный.

Коды возврата, чтобы отказ было видно скрипту, а не только глазами:

    0 — проверено и верно
    1 — проверено и неверно
    3 — не удалось формализовать: разбор отказался, приговора нет
    4 — проверка не состоялась вовсе: нет компилятора, не читается
        спецификация, или расчёт назван неоднозначно

Тройка и четвёрка разделены потому, что разными их видит тот, кто чинит:
тройку чинит человек, переформулировав фразу, четвёрку — оператор, поправив
окружение. Двойка не занята намеренно: её argparse отдаёт за ошибку в самой
командной строке, и путать это с отказом разбора значит терять единственную
разницу, ради которой коды и заведены.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EXIT_VERIFIED = 0
EXIT_REFUTED = 1
EXIT_NOT_FORMALIZED = 3
EXIT_CANNOT_CHECK = 4


def _die(message: str, code: int) -> int:
    print(message, file=sys.stderr)
    return code


def _pick_utility(utilities: list[dict], wanted: str | None) -> dict:
    """Какой расчёт дописываем.

    Молчаливый выбор первой утилиты был бы догадкой о намерении там, где
    спросить стоит одну строку, поэтому при нескольких объявленных расчётах и
    неназванном имени команда перечисляет их и останавливается.
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


def _split_optional(field: dict) -> dict:
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


def cmd_rule_check(args) -> int:
    from digit_cli import claimcheck
    from digit_cli.claimcheck import bridge

    path = Path(args.spec).expanduser()
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as error:
        return _die(f"не читается спецификация {path}: {error}", EXIT_CANNOT_CHECK)

    try:
        document = bridge.schema_of(source)
    except bridge.RuntimeMissing as error:
        # Отсутствие компилятора — отказ с названной причиной, а не «похоже,
        # всё в порядке». Проверять нечем и это обязано быть видно.
        return _die(f"ПРОВЕРИТЬ НЕЧЕМ — {error}", EXIT_CANNOT_CHECK)

    try:
        utility = _pick_utility(document["utilities"], args.utility)
    except ValueError as error:
        return _die(str(error), EXIT_CANNOT_CHECK)

    structures = [
        {"name": s["name"], "fields": [_split_optional(f) for f in s["fields"]]}
        for s in document["structures"]
    ]
    schema = claimcheck.Schema(
        structures,
        {"name": utility["name"], "input": utility["input"],
         "output": utility["output"], "initial": utility["initial"]},
    )
    if not schema.input_fields:
        return _die(
            f"структура «{utility['input']}» не объявлена или пуста — "
            "разбирать правило не над чем",
            EXIT_CANNOT_CHECK,
        )

    try:
        result = claimcheck.answer(
            list(args.statement),
            schema,
            args.category or document.get("category") or "Правила",
            base_rules=utility["rules"],
            base_properties=utility["properties"],
            base_examples=utility["examples"],
        )
    except bridge.RuntimeMissing as error:
        return _die(f"ПРОВЕРИТЬ НЕЧЕМ — {error}", EXIT_CANNOT_CHECK)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_render(result, schema, utility, path, show_fts=args.fts))

    return {
        "проверено_верно": EXIT_VERIFIED,
        "проверено_неверно": EXIT_REFUTED,
        "не_формализовано": EXIT_NOT_FORMALIZED,
    }[result["outcome"]]


def _render(result: dict, schema, utility: dict, path: Path, show_fts: bool) -> str:
    """Ответ человеку. Порядок строк — это и есть смысл команды.

    Сначала «прочитано так», потом приговор. Приговор, прочитанный раньше
    прочтения, читается как приговор фразе; на деле он относится к тому, во
    что фраза превратилась, и увидеть это превращение человек обязан первым.
    """
    outcome = result["outcome"]
    lines = [f"Расчёт: «{utility['name']}» из {path} "
             f"({len(utility['rules'])} объявленных правил, "
             f"{len(utility['properties'])} свойств, "
             f"{len(utility['examples'])} примеров)", ""]

    if outcome == "не_формализовано":
        lines.append("НЕ УДАЛОСЬ ФОРМАЛИЗОВАТЬ — приговора не будет.")
        for refusal in result["refusals"]:
            # Часть отказов уже несёт свой код внутри пояснения — печатать его
            # дважды значит делать причину отказа менее читаемой, а читают её
            # ровно затем, чтобы переформулировать и попробовать снова.
            detail = refusal["detail"]
            prefix = f"{refusal['code']}: "
            if detail.startswith(prefix):
                detail = detail[len(prefix):]
            lines.append(f"  • «{refusal['statement']}»")
            lines.append(f"    {refusal['code']}: {detail}")
        fields = ", ".join(f"«{name}» ({kind})" for name, kind in schema.input_fields.items())
        lines.append(f"  объявленные поля расчёта: {fields}")
        lines.append("")
        lines.append("Догадка здесь была бы хуже отказа: она пришла бы с видом проверенной.")
        lines.append("")
        lines.append(result["note"])
        return "\n".join(lines)

    lines.append("Прочитано так:")
    for item in result["reading"]:
        lines.append(f"  {item}")
    lines.append("")

    if outcome == "проверено_верно":
        lines.append("ПРОВЕРЕНО И ВЕРНО — правило встаёт в расчёт без противоречий.")
        if utility["examples"]:
            # Единственное свидетельство в зелёном ответе, которое МОГЛО не
            # сойтись: ожидания в объявленных примерах писал автор расчёта, а
            # не наш же интерпретатор минуту назад.
            lines.append(f"  объявленные примеры расчёта ({len(utility['examples'])}) "
                         "по-прежнему сходятся")
    else:
        failed = result["failed_check"]
        lines.append(f"ПРОВЕРЕНО И НЕВЕРНО — упала проверка «{failed['stage']}», "
                     f"код {failed['code']}.")
        lines.append(f"  {failed['detail']}")
        for fallacy in result.get("verdict", {}).get("fallacies", []) or []:
            lines.append(f"  дефект вывода: {fallacy['code']} / {fallacy['kind']} — {fallacy['where']}")

    if result.get("examples"):
        lines.append("")
        lines.append("Исполнено компилятором на проверочных случаях:")
        for example in result["examples"][:5]:
            given = ", ".join(f"{k} = {v}" for k, v in example["input"].items())
            lines.append(f"  {given} → {example['expected']}")

    if show_fts:
        lines.append("")
        lines.append("Спецификация, которую проверял компилятор:")
        lines.extend(f"  {line}" for line in result["fts"].splitlines())

    lines.append("")
    lines.append(result["note"])
    return "\n".join(lines)
