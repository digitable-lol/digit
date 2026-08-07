"""Сборка спецификации, прогон через компилятор и детектор, ответ из трёх исходов.

Исходов ровно три, и третий — не разновидность второго:

  1. ПРОВЕРЕНО И ВЕРНО — спецификация построена, скомпилирована, примеры
     исполнены, ворота выдали сертификат;
  2. ПРОВЕРЕНО И НЕВЕРНО — спецификация построена, но какая-то проверка упала;
     ответ обязан назвать, какая именно;
  3. НЕ УДАЛОСЬ ФОРМАЛИЗОВАТЬ — разбор отказался. Это честный отказ: догадка
     здесь стоила бы дороже, потому что она пришла бы с видом проверенной.

Ко всем исходам прикладывается граница: проверена ВЫВОДИМОСТЬ следствия из
посылки, а не истинность посылки. Модели мира у системы нет.
"""

from __future__ import annotations

from . import bridge, claimparse, ftsemit

LIMIT_NOTE = (
    "Граница: проверено ПОСТРОЕНИЕ правила и его согласованность — типы, покрытие, "
    "границы, отсутствие структурных дефектов вывода. НЕ проверено, верна ли сама "
    "посылка: у системы нет модели мира. Правило «НДС 20 % на экспорт» прошло бы "
    "эти же проверки и получило бы такой же зелёный ответ."
)


def run_verify(records: list[dict]) -> list[dict]:
    """Один запуск node на пачку записей.

    Где лежит node и где лежит компилятор — вопрос поставки, а не разбора, и
    он вынесен в bridge. Здесь остался только вызов.
    """
    return bridge.run(records)


# ------------------------------------------------------- сборка спецификации


def build_spec(rules: list[dict], schema: claimparse.Schema, category: str, examples=None, properties=None) -> dict:
    utility = dict(schema.utility)
    utility["rules"] = rules
    utility["properties"] = properties or []
    utility["examples"] = examples or []
    return {"category": category, "structures": schema.structures, "utility": utility}


def discriminating_schema(schema: "claimparse.Schema") -> "claimparse.Schema":
    """Та же схема, но с ненулевым начальным значением расчёта.

    Одиночное правило при «начинает с 0» делает «добавить X» и «результат равен
    X» неразличимыми: 0 + X = X. Сравнение смыслов в таком контексте слепо к
    подмене накопления присваиванием — а это ровно та ошибка, которая в расчёте
    с несколькими правилами меняет результат. Сдвиг начального значения делает
    контекст различающим; обе стороны сравнения видят один и тот же сдвиг.
    """
    utility = dict(schema.utility)
    if utility["output"] in ("Число", "Деньги") and utility.get("initial") in (0, 0.0):
        utility["initial"] = 7
    shifted = claimparse.Schema(schema.structures, utility)
    return shifted


def name_rule(rule: dict, index: int) -> str:
    fields = ", ".join(condition["field"] for condition in rule["when"])
    return f"Правило {index + 1}: {fields}"


# ------------------------------------------------------------ тест-векторы

TYPE_DEFAULT = {"Число": 0, "Деньги": 0, "Признак": False, "Строка": "", "Текст": "", "Дата": "2024-01-01"}


def interesting_values(field: str, field_type: str, rules: list[dict]) -> list:
    """Значения поля, на которых правило меняет поведение: пороги и их окрестность."""
    out = []
    for rule in rules:
        for condition in rule["when"] + [{"field": None, "value": rule["action"]["value"]}]:
            if condition.get("field") not in (field, None):
                continue
            operand = condition["value"]
            if operand.get("kind") != "value":
                continue
            value = operand["value"]
            if condition.get("field") != field:
                continue
            if isinstance(value, bool) or value is None or isinstance(value, str):
                out.append(value)
                continue
            for delta in (-1, 0, 1):
                out.append(value + delta)
                if isinstance(value, float):
                    out.append(round(value + delta * 0.5, 6))
    if field_type in ("Число", "Деньги"):
        out.extend([0, 1, 100, 1000])
    elif field_type == "Признак":
        out.extend([True, False])
    elif field_type in ("Строка", "Текст"):
        out.extend(["", "прочее"])
    else:
        out.append(TYPE_DEFAULT.get(field_type, ""))
    seen, unique = set(), []
    for value in out:
        key = (type(value).__name__, value)
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


def vectors(rules: list[dict], schema: claimparse.Schema, cap: int = 64) -> list[dict]:
    """Покрывающий набор входов: по одному «интересному» значению за раз."""
    fields = list(schema.input_fields.items())
    base = {}
    for name, field_type in fields:
        if field_type.startswith(("Строка", "Текст")):
            base[name] = "прочее"
        elif field_type == "Дата":
            base[name] = "2024-01-01"
        elif field_type == "Признак":
            base[name] = False
        elif field_type in ("Число", "Деньги"):
            base[name] = 0
        else:
            base[name] = "прочее"
    out = [dict(base)]
    for name, field_type in fields:
        for value in interesting_values(name, field_type, rules):
            if isinstance(value, str) and field_type in ("Число", "Деньги"):
                continue
            if not isinstance(value, str) and field_type not in ("Число", "Деньги", "Признак"):
                continue
            if isinstance(value, bool) and field_type != "Признак":
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool) and field_type not in ("Число", "Деньги"):
                continue
            vector = dict(base)
            vector[name] = value
            out.append(vector)
            if len(out) >= cap:
                return out
    # Пары значений: одиночных векторов мало, когда правило конъюнктивное.
    if len(fields) > 1:
        first, second = fields[0], fields[1]
        for left in interesting_values(first[0], first[1], rules)[:4]:
            for right in interesting_values(second[0], second[1], rules)[:4]:
                vector = dict(base)
                if type(left) is bool or first[1] in ("Число", "Деньги") or isinstance(left, str):
                    vector[first[0]] = left
                if type(right) is bool or second[1] in ("Число", "Деньги") or isinstance(right, str):
                    vector[second[0]] = right
                out.append(vector)
                if len(out) >= cap:
                    return out
    return out


# ------------------------------------------------------------------ ответ


def parse_all(statements: list[str], schema: claimparse.Schema):
    """Утверждения делятся на правила («если … то …») и свойства («результат …»)."""
    rules, properties, refusals = [], [], []
    for index, text in enumerate(statements):
        try:
            if claimparse.looks_like_property(text):
                prop = claimparse.parse_property(text, schema)
                prop["name"] = f"Свойство {len(properties) + 1}"
                properties.append(prop)
                continue
            parsed = claimparse.parse_statement_rules(text, schema)
        except claimparse.Refusal as error:
            refusals.append({"statement": text, "code": error.code, "detail": error.detail})
            continue
        for order, rule in enumerate(parsed):
            rule["name"] = name_rule(rule, index) + (f" · вариант {order + 1}" if len(parsed) > 1 else "")
            rules.append(rule)
    return rules, properties, refusals


def answer(statements: list[str], schema: claimparse.Schema, category: str,
           base_rules: list[dict] | None = None,
           base_properties: list[dict] | None = None,
           base_examples: list[dict] | None = None) -> dict:
    """Полный проход: разбор → спецификация → проверка → ответ из трёх исходов.

    `base_*` — то, что в расчёте УЖЕ объявлено: правила, свойства и примеры
    существующей утилиты. Правило проверяется вместе с ними, а не в пустоте, и
    без этого половина детектора слепа: непокрытая ветка между новым порогом и
    старым, правило, полностью перекрытое соседним, нарушенное свойство и
    разъехавшийся пример — всё это утверждения о ПАРЕ «новое и объявленное».
    Проверять новое правило в одиночку значит выдавать зелёный на том, что
    ломает расчёт.

    Пустые `base_*` дают ровно то поведение, на котором слой измерен: списки
    приклеиваются спереди, и при пустых списках каждая последующая строка
    получает те же значения, что и до появления этих параметров.
    """
    parsed_rules, parsed_properties, refusals = parse_all(statements, schema)
    if refusals or not parsed_rules:
        return {
            "outcome": "не_формализовано",
            "refusals": refusals,
            "note": LIMIT_NOTE,
        }

    rules = list(base_rules or []) + parsed_rules
    properties = list(base_properties or []) + parsed_properties

    # Свойства прикладываются СРАЗУ: свойство — всеобщее утверждение, и его
    # нарушение обязано всплыть как нарушение свойства с контрпримером, а не
    # как расхождение примера, посчитанного до того, как свойство объявили.
    spec = build_spec(rules, schema, category, None, properties)
    source = ftsemit.render_document(spec)
    probes = vectors(rules, schema)
    evaluated = run_verify([{"id": "eval", "mode": "eval", "fts": source,
                             "utility": schema.utility["name"], "vectors": probes}])[0]

    types = dict(schema.input_fields)
    # Читается вслух ТОЛЬКО разобранное. Объявленные правила пользователь не
    # писал сейчас, и подмешивать их в «прочитано так» значило бы прятать его
    # собственную фразу среди чужих строк — а сверяет он глазами именно её.
    reading = [ftsemit.back_translate(rule, types) for rule in parsed_rules]

    if not evaluated.get("ok"):
        return {"outcome": "проверено_неверно", "fts": source, "reading": reading,
                "verdict": {"stage": "compile", "code": "FTS_INVALID", "detail": evaluated.get("error", "")},
                "failed_check": {"stage": "compile", "code": "FTS_INVALID", "detail": evaluated.get("error", "")},
                "examples": [], "note": LIMIT_NOTE}

    fired, quiet, violations = [], [], []
    initial = schema.utility["initial"]
    for index, (vector, result) in enumerate(zip(probes, evaluated["results"])):
        if not result.get("ok"):
            violations.append({"input": vector, "error": result.get("error", "")})
            continue
        example = {"name": f"Проверочный случай {index + 1}", "input": vector, "expected": result["value"]}
        # Случаи, где правило СРАБОТАЛО, идут первыми: свидетельство, состоящее
        # из одних несрабатываний, не показывает, что именно проверено.
        (quiet if result["value"] == initial else fired).append(example)
    examples = fired[:4] + quiet[:4]

    if violations:
        first = violations[0]
        given = ", ".join(f"{k} = {v}" for k, v in first["input"].items())
        return {
            "outcome": "проверено_неверно",
            "fts": source, "reading": reading, "examples": examples,
            "verdict": {"stage": "свойство", "code": "FTS_UTILITY_PROPERTY", "detail": first["error"]},
            "failed_check": {"stage": "свойство", "code": "FTS_UTILITY_PROPERTY",
                             "detail": f"{first['error']}; контрпример: {given}"},
            "note": LIMIT_NOTE,
        }

    # Объявленные примеры идут ПЕРВЫМИ и с чужими ожиданиями: их значения
    # писал автор расчёта, а не наш же интерпретатор минуту назад. Только они и
    # способны упасть — посчитанные примеры сходятся по построению. Расхождение
    # здесь и есть настоящий ответ «новое правило меняет объявленный результат».
    spec = build_spec(rules, schema, category, list(base_examples or []) + examples, properties)
    source = ftsemit.render_document(spec)
    verdict = run_verify([{"id": "check", "mode": "check", "fts": source}])[0]

    payload = {
        "fts": source,
        "reading": reading,
        "verdict": verdict,
        "examples": examples,
        "note": LIMIT_NOTE,
    }
    if verdict.get("ok"):
        payload["outcome"] = "проверено_верно"
    else:
        payload["outcome"] = "проверено_неверно"
        payload["failed_check"] = {"stage": verdict.get("stage"), "code": verdict.get("code"), "detail": verdict.get("detail")}
    return payload


def render_answer(result: dict) -> str:
    """Ответ пользователю. Граница называется всегда, во всех трёх исходах."""
    lines = []
    outcome = result["outcome"]
    if outcome == "не_формализовано":
        lines.append("НЕ УДАЛОСЬ ФОРМАЛИЗОВАТЬ — ответа не будет.")
        for refusal in result["refusals"]:
            lines.append(f"  • «{refusal['statement']}»")
            lines.append(f"    {refusal['code']}: {refusal['detail']}")
        lines.append("Догадка здесь была бы хуже отказа: она пришла бы с видом проверенной.")
        lines.append("")
        lines.append(result["note"])
        return "\n".join(lines)

    if outcome == "проверено_верно":
        lines.append("ПРОВЕРЕНО И ВЕРНО.")
    else:
        failed = result["failed_check"]
        lines.append(f"ПРОВЕРЕНО И НЕВЕРНО — упала проверка «{failed['stage']}», код {failed['code']}.")
        lines.append(f"  {failed['detail']}")
        for fallacy in result["verdict"].get("fallacies", []):
            lines.append(f"  дефект вывода: {fallacy['code']} / {fallacy['kind']} — {fallacy['where']}")

    lines.append("")
    lines.append("Прочитано так:")
    for item in result["reading"]:
        lines.append(f"  {item}")
    if result.get("examples"):
        lines.append("")
        lines.append("Исполнено компилятором на проверочных случаях:")
        for example in result["examples"][:5]:
            given = ", ".join(f"{k} = {v}" for k, v in example["input"].items())
            lines.append(f"  {given} → {example['expected']}")
    lines.append("")
    lines.append(result["note"])
    return "\n".join(lines)
