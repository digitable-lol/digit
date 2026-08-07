"""IR правила и печать спецификации FTS.

IR намеренно совпадает с AST компилятора (@digitable/fts, src/model.ts):
условие — {field, operator, value}, операнд — value | field | percent | result.
Совпадение снимает целый класс ошибок перевода: сравнивать разобранное с
эталонным можно поэлементно, без нормализации между двумя представлениями.

Исполнения здесь нет. Значения примеров и любое сравнение семантики считает
настоящий интерпретатор FTS через verify.mjs — второй реализации семантики в
этом проекте не существует, чтобы ей неоткуда было разойтись с первой.
"""

from __future__ import annotations

OPERATOR_SURFACE = {
    "gte": "не меньше",
    "lte": "не больше",
    "gt": "больше",
    "lt": "меньше",
    "eq": "равен",
    "neq": "не равен",
}

BUILTIN_TYPE_SURFACE = {
    "Строка": "строкой",
    "Текст": "текстом",
    "Число": "числом",
    "Дата": "датой",
    "Деньги": "деньгами",
    "Признак": "признаком",
}

RETURN_SURFACE = {
    "Строка": "строку",
    "Текст": "строку",
    "Число": "число",
    "Дата": "дату",
    "Деньги": "деньги",
    "Признак": "признак",
}


def num_text(x) -> str:
    """Ровно то, что вернёт Number(text) в JS. Без экспоненты."""
    if isinstance(x, bool):
        raise ValueError("не число")
    if isinstance(x, int):
        return str(x)
    if float(x).is_integer() and abs(x) < 1e15:
        return str(int(x))
    text = repr(float(x))
    if "e" in text or "E" in text:
        raise ValueError("не записывается в поверхностном синтаксисе")
    return text


def percent_word(value) -> str:
    if not float(value).is_integer():
        return "процента"
    n = abs(int(value))
    if n % 100 in (11, 12, 13, 14):
        return "процентов"
    last = n % 10
    if last == 1:
        return "процент"
    if last in (2, 3, 4):
        return "процента"
    return "процентов"


def scalar_surface(value) -> str:
    if value is None:
        return "ничто"
    if value is True:
        return "да"
    if value is False:
        return "нет"
    if isinstance(value, (int, float)):
        return num_text(value)
    return f"«{value}»"


def operand_surface(operand: dict) -> str:
    kind = operand["kind"]
    if kind == "value":
        return scalar_surface(operand["value"])
    if kind == "result":
        return "результат"
    if kind == "field":
        return f"поле «{operand['field']}»"
    if kind == "percent":
        return f"{num_text(operand['percent'])} {percent_word(operand['percent'])} от поля «{operand['field']}»"
    raise ValueError(kind)


def rule_lines(rule: dict) -> list[str]:
    out = [f"    правило «{rule['name']}»"]
    for index, condition in enumerate(rule["when"]):
        head = "если" if index == 0 else "и"
        operator = OPERATOR_SURFACE[condition["operator"]]
        out.append(f"      {head} «{condition['field']}» {operator} {operand_surface(condition['value'])}")
    action = rule["action"]
    if action["kind"] == "add":
        out.append(f"      то добавить {operand_surface(action['value'])}")
    else:
        out.append(f"      то результат равен {operand_surface(action['value'])}")
    return out


def property_lines(prop: dict) -> list[str]:
    return [
        f"    свойство «{prop['name']}»",
        f"      результат {OPERATOR_SURFACE[prop['operator']]} {operand_surface(prop['value'])}",
    ]


def field_line(field: dict) -> str:
    verb = "иногда является" if field.get("optional") else "является"
    surface = BUILTIN_TYPE_SURFACE.get(field["type"])
    if surface is None:
        surface = f"состоянием «{field['type']}»"
    return f"    «{field['name']}» {verb} {surface}"


def example_lines(example: dict) -> list[str]:
    out = [f"    пример «{example['name']}»"]
    for name, value in example["input"].items():
        out.append(f"      дано «{name}» равен {scalar_surface(value)}")
    out.append(f"      ожидается результат равен {scalar_surface(example['expected'])}")
    return out


def render_document(spec: dict) -> str:
    """spec = {category, structures, utility{name,input,output,initial,rules,examples}}."""
    lines = [f"категория «{spec['category']}»", ""]
    for structure in spec["structures"]:
        lines.append(f"  структура «{structure['name']}»")
        for field in structure["fields"]:
            lines.append(field_line(field))
        lines.append("")
    utility = spec["utility"]
    lines.append(f"  утилита «{utility['name']}»")
    lines.append(f"    принимает «{utility['input']}»")
    lines.append(f"    возвращает {RETURN_SURFACE[utility['output']]}")
    lines.append(f"    начинает с {scalar_surface(utility['initial'])}")
    for rule in utility["rules"]:
        lines.append("")
        lines.extend(rule_lines(rule))
    for prop in utility.get("properties", []):
        lines.append("")
        lines.extend(property_lines(prop))
    for example in utility.get("examples", []):
        lines.append("")
        lines.extend(example_lines(example))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------- обратный перевод


def back_translate(rule: dict, types: dict[str, str]) -> str:
    """Правило обратно на русский. Не парафраз входа, а чтение IR.

    Нужно ровно для одного: показать пользователю, ЧТО именно проверено, чтобы
    расхождение с задуманным он увидел до того, как поверит зелёному ответу.
    """
    def value_ru(operand: dict) -> str:
        kind = operand["kind"]
        if kind == "value":
            value = operand["value"]
            if value is None:
                return "не заполнено"
            if value is True:
                return "да"
            if value is False:
                return "нет"
            if isinstance(value, (int, float)):
                return num_text(value)
            return f"«{value}»"
        if kind == "result":
            return "накопленный результат"
        if kind == "field":
            return f"значение поля «{operand['field']}»"
        return f"{num_text(operand['percent'])} {percent_word(operand['percent'])} от поля «{operand['field']}»"

    words = {
        "gte": "не меньше",
        "lte": "не больше",
        "gt": "строго больше",
        "lt": "строго меньше",
        "eq": "равно",
        "neq": "не равно",
    }
    parts = []
    for condition in rule["when"]:
        parts.append(f"«{condition['field']}» {words[condition['operator']]} {value_ru(condition['value'])}")
    head = " и ".join(parts)
    action = rule["action"]
    if action["kind"] == "add":
        tail = f"прибавить к результату {value_ru(action['value'])}"
    else:
        tail = f"результат становится равен {value_ru(action['value'])}"
    return f"если {head}, то {tail}"
