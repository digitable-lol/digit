"""Разбор русского утверждения в правило FTS. Правилами, без модели.

Границы объявлены сразу, потому что от них зависит, как читать замер:

  * разбирается ОДНО правило вида «условие → следствие» над ИЗВЕСТНОЙ схемой
    данных. Схема (поля и их типы, вход и выход расчёта) — это вход, а не то,
    что восстанавливается из текста;
  * истинность посылки не проверяется и проверена быть не может: у разборщика
    нет модели мира. «НДС 20 % на экспорт» разберётся и скомпилируется;
  * при неоднозначности разборщик отказывает, а не выбирает. Отказ дешевле
    тихой ошибки — вся затея держится на том, что «проверено» значит проверено.

Устройство: нормализация → выбор рамки (условие/следствие) перебором гипотез →
разбор конъюнктов → разбор операндов → сопоставление имён полей со схемой.
Каждая ступень либо возвращает разбор, либо причину отказа.
"""

from __future__ import annotations

import re
import unicodedata

# ------------------------------------------------------------- нормализация

QUOTES = {"«": "«", "»": "»", "“": "«", "”": "»", "„": "«", "‟": "«", '"': '"', "'": "'"}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    # «ё» здесь НЕ выпрямляется. Из нормализованного текста вырезаются строковые
    # ЗНАЧЕНИЯ, и замена «запрещён» → «запрещен» прошла бы компилятор зелёной,
    # дав ровно ту тихую ошибку, ради поимки которой всё это меряется. Свёртка
    # ё/е живёт только в fold(), то есть в сравнении, и в документ не попадает.
    text = text.replace("’", "'").replace(" ", " ")
    for source, target in (("“", "«"), ("”", "»"), ("„", "«"), ("‟", "»")):
        text = text.replace(source, target)
    text = re.sub(r'"([^"]*)"', r"«\1»", text)
    text = text.replace("≥", ">=").replace("≤", "<=").replace("≠", "!=")
    text = text.replace("⇒", "→")
    # 1 000 000 → 1000000; 0,25 → 0.25
    text = re.sub(r"(?<=\d)[  ](?=\d{3}\b)", "", text)
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fold(text: str) -> str:
    return text.lower().replace("ё", "е")


WORD = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9_]+")


MASK = "\x01"


def mask_quoted(text: str) -> str:
    """Свёрнутый текст той же длины, где содержимое «…» заперто под MASK.

    Значение может дословно совпасть со служебным словом: «тариф» равно
    «минимум» — и поиск сравнения, идущий по голому тексту, разрежет
    утверждение внутри строкового литерала. Совпадение длин обязательно:
    позиции, найденные по маске, режут исходный текст.
    """
    out = list(fold(text))
    depth = 0
    for index, char in enumerate(out):
        if char == "«":
            depth += 1
            continue
        if char == "»":
            depth = max(0, depth - 1)
            continue
        if depth > 0:
            out[index] = MASK
    return "".join(out)


def tokens(text: str) -> list[str]:
    return WORD.findall(fold(text))


# Крайне грубая нормализация окончаний. Полноценная морфология тут не нужна:
# сопоставляются имена полей, а не произвольный текст, и решение принимается по
# совпадению нескольких токенов, а не одного.
SUFFIXES = (
    "ями", "ами", "иями", "ов", "ев", "ей", "ой", "ий", "ый", "ые", "ая", "яя", "ое", "ее",
    "ам", "ям", "ах", "ях", "ом", "ем", "ум", "юм", "ию", "ья", "ье", "ии", "ие", "ым", "им",
    "у", "ю", "а", "я", "ы", "и", "о", "е", "ь",
)


def stem(word: str) -> str:
    if len(word) <= 4:
        return word
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


STOP = {"поле", "поля", "полю", "значение", "значения", "в", "у", "на", "по", "из", "с", "со", "и", "флаг", "признак"}


def key_tokens(text: str) -> list[str]:
    return [stem(token) for token in tokens(text) if token not in STOP and len(token) > 1]


# --------------------------------------------------------------- лексиконы

# Фразы сравнения, от длинных к коротким. Порядок обязателен: «не менее»
# обязано разобраться раньше, чем «менее», иначе знак сравнения переворачивается
# — и это ровно та тихая ошибка, ради поимки которой всё меряется.
COMPARATORS: list[tuple[str, str]] = [
    ("не дотягивает до", "lt"),
    ("недотягивает до", "lt"),
    ("больше или равно", "gte"),
    ("больше либо равно", "gte"),
    ("меньше или равно", "lte"),
    ("меньше либо равно", "lte"),
    ("не превосходит", "lte"),
    ("не превышает", "lte"),
    ("не совпадает с", "neq"),
    ("не отличается от", "eq"),
    ("отличается от", "neq"),
    ("не выставлен в", "neq"),
    ("не меньше", "gte"),
    ("не менее", "gte"),
    ("не ниже", "gte"),
    ("не больше", "lte"),
    ("не более", "lte"),
    ("не выше", "lte"),
    ("не равен", "neq"),
    ("не равна", "neq"),
    ("не равно", "neq"),
    ("не равняется", "neq"),
    ("любое, кроме", "neq"),
    ("любой, кроме", "neq"),
    ("любая, кроме", "neq"),
    ("любое кроме", "neq"),
    ("любой кроме", "neq"),
    ("любая кроме", "neq"),
    ("все, кроме", "neq"),
    ("кроме", "neq"),
    ("не стоит в", "neq"),
    ("в пределах", "lte"),
    ("начиная с", "gte"),
    ("совпадает с", "eq"),
    ("строго больше", "gt"),
    ("строго меньше", "lt"),
    ("перевалила за", "gt"),
    ("перевалил за", "gt"),
    ("упала ниже", "lt"),
    ("упал ниже", "lt"),
    ("превышает", "gt"),
    ("превысила", "gt"),
    ("превысил", "gt"),
    ("достигает", "gte"),
    ("достигла", "gte"),
    ("составляет", "eq"),
    ("имеет значение", "eq"),
    ("значится", "eq"),
    ("числится", "eq"),
    ("проставлено значение", "eq"),
    ("минимум", "gte"),
    ("максимум", "lte"),
    ("не менее чем", "gte"),
    ("свыше", "gt"),
    ("больше", "gt"),
    ("меньше", "lt"),
    ("выше", "gt"),
    ("ниже", "lt"),
    ("более", "gt"),
    ("менее", "lt"),
    ("равен", "eq"),
    ("равна", "eq"),
    ("равно", "eq"),
    ("равняется", "eq"),
    ("ровно", "eq"),
    ("именно", "eq"),
    ("это", "eq"),
    ("стоит", "eq"),
    ("от", "gte"),
    ("до", "lte"),
    (">=", "gte"),
    ("<=", "lte"),
    ("!=", "neq"),
    ("не", "neq"),
    (">", "gt"),
    ("<", "lt"),
    ("=", "eq"),
    (":", "eq"),
    ("—", "eq"),
    ("-", "eq"),
]

# Постфиксные сравнения: значение стоит ПЕРЕД фразой («5 и выше», «до 100
# включительно», «100 или меньше»).
POSTFIX_COMPARATORS: list[tuple[str, str]] = [
    ("или больше", "gte"),
    ("или меньше", "lte"),
    ("и выше", "gte"),
    ("и больше", "gte"),
    ("и ниже", "lte"),
    ("и меньше", "lte"),
    ("включительно", "lte"),
]

# Предикаты без явного значения: поле + знак + подразумеваемая величина.
PREDICATES: list[tuple[re.Pattern, str, object]] = [
    (re.compile(r"^не заполнен[оаы]?\s+(?P<f>.+)$"), "eq", None),
    (re.compile(r"^нет\s+(?P<f>.+)$"), "eq", False),
    (re.compile(r"^(?:нет|отсутствует)\s+(?P<f>.+)$"), "eq", None),
    (re.compile(r"^(?P<f>.+?)\s+не заполнен[оаы]?$"), "eq", None),
    (re.compile(r"^(?P<f>.+?)\s+не задан[оаы]?$"), "eq", None),
    (re.compile(r"^(?P<f>.+?)\s+не указан[оаы]?$"), "eq", None),
    (re.compile(r"^(?P<f>.+?)\s+не проставлен[оаы]?$"), "eq", None),
    (re.compile(r"^(?P<f>.+?)\s+пуст[оаы]?е?$"), "eq", None),
    (re.compile(r"^(?P<f>.+?)\s+отсутствует$"), "eq", None),
    (re.compile(r"^(?P<f>.+?)\s+заполнен[оаы]?$"), "neq", None),
    (re.compile(r"^(?P<f>.+?)\s+задан[оаы]?$"), "neq", None),
    (re.compile(r"^(?P<f>.+?)\s+указан[оаы]?$"), "neq", None),
    (re.compile(r"^(?P<f>.+?)\s+присутствует$"), "neq", None),
    (re.compile(r"^(?P<f>.+?)\s+не пуст[оаы]?е?$"), "neq", None),
    (re.compile(r"^(?P<f>.+?)\s+есть$"), "neq", None),
    (re.compile(r"^(?:флаг\s+)?(?P<f>.+?)\s+не поднят$"), "eq", False),
    (re.compile(r"^(?:флаг\s+)?(?P<f>.+?)\s+поднят$"), "eq", True),
    (re.compile(r"^(?P<f>.+?)\s+не включ[её]н$"), "eq", False),
    (re.compile(r"^(?P<f>.+?)\s+включ[её]н$"), "eq", True),
    (re.compile(r"^(?P<f>.+?)\s+выключен$"), "eq", False),
    (re.compile(r"^(?P<f>.+?)\s+не отмечен$"), "eq", False),
    (re.compile(r"^(?P<f>.+?)\s+отмечен$"), "eq", True),
    (re.compile(r"^(?P<f>.+?)\s+не проставлен$"), "eq", False),
    (re.compile(r"^(?P<f>.+?)\s+проставлен$"), "eq", True),
    (re.compile(r"^(?:есть|имеется)\s+(?P<f>.+)$"), "eq", True),
    (re.compile(r"^(?P<f>.+?)\s+не стоит$"), "eq", False),
    (re.compile(r"^(?P<f>.+?)\s+стоит$"), "eq", True),
]

NULL_WORDS = {"ничто", "не задано", "не задан", "пусто", "пустое", "отсутствует", "null", "ничего", "не заполнено"}
TRUE_WORDS = {"да", "истина", "true", "верно"}
FALSE_WORDS = {"нет", "ложь", "false", "неверно"}

MONEY_TAIL = re.compile(r"\s*(?:руб(?:\.|лей|ля|ль)?|р\.?|₽|коп\.?|тг|usd|eur|\$)$", re.IGNORECASE)
NUMBER_TAIL = re.compile(r"(?P<v>-?\d+(?:\.\d+)?\s*(?:руб(?:\.|лей|ля|ль)?|р\.?|₽|коп\.?|тг|usd|eur|\$|%)?)\s*$", re.IGNORECASE)

CONDITION_MARKERS = ("если ", "при условии, что ", "при условии что ", "когда ", "в случае, когда ",
                     "в случае когда ", "в случае, если ", "в случае если ", "при ", "правило: ", "правило ")

SPLITTERS = [", то ", " то ", " → ", " -> ", " => ", " — ", " – ", " - ", ", ", ": "]

CONJUNCTIONS = [" и при этом ", ", а также ", " а также ", " + ", " и "]

# Слова, с которых начинается СЛЕДСТВИЕ. Нужны для рамки без знаков препинания
# («при сумме свыше 1000 добавить 10 %»): разделителя нет, и границу приходится
# искать по началу действия. Гипотеза всё равно проверяется разбором обеих
# половин, поэтому ошибочный разрез не проходит — он просто не разбирается.
ACTION_HEADS = [
    "к результату", "прибавить", "прибавляем", "добавить", "добавляем", "добавь",
    "начислить", "начисляем", "накинуть", "накидываем", "плюсуем", "плюс",
    "удвоить", "удваиваем", "вернуть", "верни", "возвращаем", "возвратить",
    "ставим", "ставить", "поставить", "считаем", "считать", "выдаем", "выдать",
    "выдай", "результат", "итог", "наценка", "надбавка", "доплата", "скидка",
]


class Refusal(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


# ------------------------------------------------------------- схема данных


class Schema:
    def __init__(self, structures: list[dict], utility: dict):
        self.structures = structures
        self.utility = utility
        self.input_fields: dict[str, str] = {}
        for structure in structures:
            if structure["name"] != utility["input"]:
                continue
            for field in structure["fields"]:
                self.input_fields[field["name"]] = field["type"]
        self._index = [(name, set(key_tokens(name))) for name in self.input_fields]

    def resolve(self, expression: str) -> str:
        """Имя поля из куска текста. Отказ при пустом или неоднозначном совпадении."""
        raw = expression.strip().strip("«»\"' ").strip()
        raw = re.sub(r"^(?:в\s+)?(?:значени[ея]\s+)?(?:поля|поле|полю)\s+", "", fold(raw)).strip()
        raw = re.sub(r"^(?:флаг|признак|статус поля)\s+", "", raw).strip()
        if not raw:
            raise Refusal("FIELD_EMPTY", f"не нашлось имени поля в «{expression}»")
        for name in self.input_fields:
            if fold(name) == raw:
                return name
        wanted = set(key_tokens(raw))
        if not wanted:
            raise Refusal("FIELD_EMPTY", f"не нашлось имени поля в «{expression}»")
        scored = []
        for name, declared in self._index:
            shared = wanted & declared
            if not shared:
                continue
            # Доля совпавших токонов имени поля и доля «съеденного» текста.
            scored.append((len(shared) / len(declared), len(shared) / len(wanted), name))
        if not scored:
            raise Refusal("FIELD_UNKNOWN", f"поле «{expression.strip()}» не объявлено во входной структуре")
        scored.sort(reverse=True)
        best = scored[0]
        if len(scored) > 1 and abs(scored[1][0] - best[0]) < 1e-9 and abs(scored[1][1] - best[1]) < 1e-9:
            names = ", ".join(f"«{item[2]}»" for item in scored[:3])
            raise Refusal("FIELD_AMBIGUOUS", f"«{expression.strip()}» одинаково подходит к нескольким полям: {names}")
        return best[2]

    def type_of(self, name: str) -> str:
        return self.input_fields[name]


# --------------------------------------------------------------- операнды


def parse_number(text: str):
    text = MONEY_TAIL.sub("", text.strip()).strip()
    text = text.rstrip("%").strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return None
    return float(text) if "." in text else int(text)


PERCENT = re.compile(r"^(?P<p>-?\d+(?:\.\d+)?)\s*(?:%|процент\w*)\s*(?:от|к)\s+(?P<f>.+)$")
FIELD_OPERAND = re.compile(r"^(?:значени[ея]\s+)?(?:поля|поле|полю)\s+(?P<f>.+)$")
RESULT_WORDS = {"результат", "текущий результат", "накопленный результат", "текущего результата",
                "накопленного результата", "результата", "итог", "итога"}


def parse_operand(text: str, schema: Schema, allow_field: bool = True) -> dict:
    raw = text.strip().strip(".,;")
    low = fold(raw)
    if low in RESULT_WORDS:
        return {"kind": "result"}
    if low in NULL_WORDS:
        return {"kind": "value", "value": None}
    if low in TRUE_WORDS:
        return {"kind": "value", "value": True}
    if low in FALSE_WORDS:
        return {"kind": "value", "value": False}
    match = PERCENT.match(low)
    if match is not None:
        percent = parse_number(match.group("p"))
        if percent is None:
            raise Refusal("OPERAND_BAD", f"не разобрал процент в «{raw}»")
        return {"kind": "percent", "percent": percent, "field": schema.resolve(match.group("f"))}
    number = parse_number(raw)
    if number is not None:
        return {"kind": "value", "value": number}
    if raw.startswith("«") and raw.endswith("»"):
        inner = raw[1:-1]
        if "«" in inner or "»" in inner:
            # «оплачен» или статус равен «отгружен» — это НЕ одно значение.
            # Взять его целиком значило бы построить правило, которое
            # скомпилируется и будет означать не то, что написано.
            raise Refusal("VALUE_NOT_ATOMIC", f"«{raw}» — не одно значение, а несколько")
        return {"kind": "value", "value": inner}
    match = FIELD_OPERAND.match(low)
    if match is not None:
        if not allow_field:
            raise Refusal("OPERAND_BAD", f"ссылка на поле здесь не допускается: «{raw}»")
        return {"kind": "field", "field": schema.resolve(match.group("f"))}
    if allow_field:
        try:
            return {"kind": "field", "field": schema.resolve(raw)}
        except Refusal:
            pass
    return {"kind": "value", "value": raw}


def coerce(operand: dict, field_type: str, schema: Schema) -> dict:
    """Согласование операнда с объявленным типом поля.

    Голое слово из текста двусмысленно: «оплачен» — это строковое значение или
    имя поля? Решает объявленный тип поля, с которым его сравнивают. Там, где
    тип не решает, разбор отказывает выше по стеку.
    """
    if operand["kind"] != "value":
        return operand
    value = operand["value"]
    if field_type in ("Число", "Деньги"):
        if isinstance(value, str):
            number = parse_number(value)
            if number is None:
                raise Refusal("TYPE_MISMATCH", f"поле объявлено числом, а сравнивается с «{value}»")
            return {"kind": "value", "value": number}
        return operand
    if field_type == "Признак":
        if isinstance(value, str):
            if fold(value) in TRUE_WORDS:
                return {"kind": "value", "value": True}
            if fold(value) in FALSE_WORDS:
                return {"kind": "value", "value": False}
            raise Refusal("TYPE_MISMATCH", f"поле объявлено признаком, а сравнивается с «{value}»")
        return operand
    if value is None or isinstance(value, bool):
        return operand
    if isinstance(value, (int, float)):
        return {"kind": "value", "value": str(value)}
    return operand


# ------------------------------------------------------------- конъюнкты


def strip_markers(text: str) -> tuple[str, bool]:
    low = fold(text).lstrip()
    for marker in CONDITION_MARKERS:
        if low.startswith(marker):
            return text.lstrip()[len(marker):].strip(), True
    return text.strip(), False


def find_comparator(text: str) -> tuple[int, int, str] | None:
    """Самая длинная фраза сравнения, самая левая среди равных по длине."""
    low = mask_quoted(text)
    best = None
    for phrase, operator in COMPARATORS:
        pattern = re.escape(phrase)
        if phrase[0].isalpha():
            pattern = r"(?<![а-яa-z0-9])" + pattern + r"(?![а-яa-z0-9])"
        for match in re.finditer(pattern, low):
            candidate = (len(phrase), -match.start(), match.start(), match.end(), operator)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
    if best is None:
        return None
    return best[2], best[3], best[4]


def parse_conjunct(text: str, schema: Schema) -> dict:
    raw = text.strip().strip(",;")
    if not raw:
        raise Refusal("COND_EMPTY", "пустое условие")

    masked = mask_quoted(raw)
    for pattern, operator, value in PREDICATES:
        match = pattern.match(masked)
        if match is None:
            continue
        field = schema.resolve(raw[match.start("f"): match.end("f")])
        field_type = schema.type_of(field)
        if value is None and field_type == "Признак":
            continue
        if isinstance(value, bool) and field_type != "Признак":
            continue
        return {"field": field, "operator": operator, "value": {"kind": "value", "value": value}}

    for phrase, operator in POSTFIX_COMPARATORS:
        match = re.search(r"(?<![а-яa-z0-9])" + re.escape(phrase) + r"$", masked)
        if match is None:
            continue
        head = raw[: match.start()].strip()
        # «цена до 100 включительно»: знак сравнения назван дважды, впереди и
        # сзади. Впереди он точнее — постфикс работает только там, где его нет.
        if find_comparator(head) is not None:
            raw, masked = head, mask_quoted(head)
            break
        tail = NUMBER_TAIL.search(head)
        if tail is None:
            continue
        field_text, value_text = head[: tail.start("v")].strip(), tail.group("v")
        if not field_text:
            continue
        field = schema.resolve(field_text)
        operand = coerce(parse_operand(value_text, schema, allow_field=False), schema.type_of(field), schema)
        return {"field": field, "operator": operator, "value": operand}

    found = find_comparator(raw)
    if found is None:
        raise Refusal("NO_COMPARATOR", f"не нашлось знака сравнения в «{raw}»")
    start, end, operator = found
    field_text = raw[:start].strip()
    value_text = raw[end:].strip()
    if not field_text or not value_text:
        raise Refusal("COND_SHAPE", f"условие «{raw}» не распадается на поле и величину")
    field = schema.resolve(field_text)
    field_type = schema.type_of(field)
    if operator in ("gt", "lt", "gte", "lte") and field_type not in ("Число", "Деньги", "Дата"):
        # На строках и признаках порядок не объявлен. Сравнение «статус больше
        # 100» скомпилируется, но означать будет не то, что имел в виду автор.
        raise Refusal("ORDER_ON_UNORDERED",
                      f"поле «{field}» объявлено как {field_type}: сравнение по порядку для него не определено")
    operand = coerce(parse_operand(value_text, schema), field_type, schema)
    if operand["kind"] == "field" and operand["field"] == field:
        raise Refusal("COND_SELF", f"условие сравнивает поле «{field}» само с собой")
    return {"field": field, "operator": operator, "value": operand}


def split_conjuncts(text: str) -> list[str]:
    masked = mask_quoted(text)
    for conjunction in CONJUNCTIONS:
        spans = [m.span() for m in re.finditer(re.escape(conjunction), masked)]
        if conjunction == " и ":
            spans = [span for span in spans
                     if not re.match(r"(?:выше|больше|ниже|меньше)(?![а-я])", masked[span[1]:])]
        if not spans:
            continue
        parts, cursor = [], 0
        for start, end in spans:
            parts.append(text[cursor:start])
            cursor = end
        parts.append(text[cursor:])
        cleaned = [part for part in (item.strip() for item in parts) if part]
        if len(cleaned) > 1:
            return cleaned
    return [text.strip()]


def parse_condition(text: str, schema: Schema) -> list[dict]:
    parts = split_conjuncts(text)
    if len(parts) > 1:
        try:
            return [parse_conjunct(part, schema) for part in parts]
        except Refusal:
            # Союз оказался частью одного условия, а не границей двух.
            pass
    return [parse_conjunct(text, schema)]


# --------------------------------------------------------------- действие

SET_PATTERNS = [
    re.compile(r"^результат(?:ом)?\s+(?:становится|станет|будет|равен|равна|равно|=|—|-|:)\s*(?P<v>.+)$"),
    re.compile(r"^результат\s+(?P<v>.+)$"),
    re.compile(r"^(?:вернуть|верни|возвращаем|возвратить)\s+(?P<v>.+)$"),
    re.compile(r"^(?:ставим|ставить|поставить)\s+(?P<v>.+)$"),
    re.compile(r"^(?:итог|ответ|вывод)\s*[:—-]\s*(?P<v>.+)$"),
    re.compile(r"^(?:считаем|считать)\s+(?P<v>.+)$"),
    re.compile(r"^(?:выда[ёе]м|выдать|выдай)\s+(?P<v>.+)$"),
]

ADD_PATTERNS = [
    re.compile(r"^(?:к результату\s+)?(?:прибавить|прибавляем|добавить|добавляем|добавь|начислить|начисляем|накинуть|накидываем|плюс|плюсуем)\s+(?P<v>.+)$"),
    re.compile(r"^к результату\s+(?P<v>.+)$"),
    re.compile(r"^(?:наценка|надбавка|доплата|штраф|скидка|бонус)\s+(?P<v>.+)$"),
    re.compile(r"^\+\s*(?P<v>.+)$"),
]

DOUBLE = re.compile(r"^(?:удвоить|удваиваем)\s+результат$")


def parse_action(text: str, schema: Schema) -> dict:
    raw = text.strip().strip(".;")
    low = fold(raw)
    if DOUBLE.match(low):
        return {"kind": "add", "value": {"kind": "result"}}
    for pattern in SET_PATTERNS:
        match = pattern.match(low)
        if match is None:
            continue
        tail = raw[match.start("v"):].strip()
        return {"kind": "set", "value": parse_operand(tail, schema)}
    for pattern in ADD_PATTERNS:
        match = pattern.match(low)
        if match is None:
            continue
        tail = raw[match.start("v"):].strip()
        operand = parse_operand(tail, schema)
        if operand["kind"] == "value" and isinstance(operand["value"], str):
            raise Refusal("ACTION_BAD", f"не разобрал прибавляемую величину в «{raw}»")
        return {"kind": "add", "value": operand}
    raise Refusal("ACTION_UNKNOWN", f"не разобрал следствие «{raw}»")


# ----------------------------------------------------------------- рамка


def frame_candidates(text: str) -> list[tuple[str, str, int]]:
    """Гипотезы «где условие, где следствие», от надёжных к слабым.

    Возвращает (условие, следствие, вес). Перебор вместо одной грамматики:
    формы рамки в живом языке пересекаются, и дешевле проверить гипотезу
    разбором обеих половин, чем пытаться разделить их заранее.
    """
    out = []
    low = mask_quoted(text)

    # «… если/при условии, что <условие>» — следствие впереди.
    for marker in (", если ", " если ", ", при условии, что ", " при условии, что ", " при условии что ",
                   ", когда ", " когда ", ", в случае если ", " в случае если "):
        at = low.rfind(marker)
        if at > 0:
            out.append((text[at + len(marker):], text[:at], 3))

    # «<маркер> <условие> <разделитель> <следствие>».
    body, marked = strip_markers(text)
    for index, splitter in enumerate(SPLITTERS):
        low_body = mask_quoted(body)
        start = 0
        while True:
            at = low_body.find(splitter, start)
            if at < 0:
                break
            start = at + 1
            left, right = body[:at].strip(), body[at + len(splitter):].strip()
            if not left or not right:
                continue
            weight = (4 if marked else 2) - index * 0.05
            out.append((left, right, weight))

    # Рамка без разделителя. Ищется начало ДЕЙСТВИЯ, а не конец условия:
    # список глаголов следствия короток и закрыт, список условий — нет.
    for head in ACTION_HEADS:
        for match in re.finditer(r"(?<![а-яa-z0-9])" + re.escape(head) + r"(?![а-яa-z0-9])", mask_quoted(body)):
            if match.start() == 0:
                continue
            left, right = body[: match.start()].strip(" ,;—-"), body[match.start():].strip()
            if not left or not right:
                continue
            out.append((left, right, 1.5 if marked else 1.0))
    for match in re.finditer(r"(?<=[а-яa-z0-9\s])\+(?=\s*[\d«а-яa-z])", mask_quoted(body)):
        left, right = body[: match.start()].strip(" ,;—-"), body[match.start():].strip()
        if left and right:
            out.append((left, right, 1.5 if marked else 1.0))

    out.sort(key=lambda item: -item[2])
    return out


def parse_statement(text: str, schema: Schema) -> dict:
    """Русское утверждение → правило FTS. Бросает Refusal, если не вышло."""
    source = normalize(text)
    if not source:
        raise Refusal("EMPTY", "пустое утверждение")

    errors = []
    best = None
    for condition_text, action_text, weight in frame_candidates(source):
        try:
            condition_text, _ = strip_markers(condition_text)
            action = parse_action(action_text, schema)
            when = parse_condition(condition_text, schema)
        except Refusal as error:
            errors.append(f"{error.code}: {error.detail}")
            continue
        if not when:
            continue
        # Больше конъюнктов — точнее рамка: гипотеза, съевшая весь текст,
        # предпочтительнее той, что отбросила его половину.
        score = (weight, len(when))
        if best is None or score > best[0]:
            best = (score, {"when": when, "action": action})
    if best is None:
        detail = errors[0] if errors else "не нашлось рамки «условие → следствие»"
        code = detail.split(":")[0] if errors else "NO_FRAME"
        raise Refusal(code, detail)
    return best[1]

# ------------------------------------------------------------- свойства

RESULT_HEAD = re.compile(r"^(?:свойство\s*[:—-]?\s*)?(?:результат|итог|ответ)\b(?P<tail>.*)$")
NONNEG = re.compile(r"^\s*(?:неотрицателен|неотрицательный|не отрицателен|>=\s*0)\s*$")


def parse_property(text: str, schema: Schema) -> dict:
    """«результат не больше 500» → всеобщее утверждение о расчёте.

    Свойство — не пример. Пример показывает одну точку, свойство обязано
    держаться на всех, и компилятор проверяет его исполнением: нарушение —
    это ПРОВЕРЕНО И НЕВЕРНО, а не мнение.
    """
    source = normalize(text)
    match = RESULT_HEAD.match(fold(source))
    if match is None:
        raise Refusal("NOT_A_PROPERTY", f"«{source}» не похоже на свойство результата")
    tail = source[match.start("tail"):].strip().lstrip(":—- ").strip()
    if NONNEG.match(fold(tail)):
        return {"operator": "gte", "value": {"kind": "value", "value": 0}}
    found = find_comparator(tail)
    if found is None or found[0] != 0:
        raise Refusal("NO_COMPARATOR", f"не нашлось знака сравнения в свойстве «{source}»")
    _, end, operator = found
    operand = parse_operand(tail[end:].strip(), schema)
    return {"operator": operator, "value": operand}


def looks_like_property(text: str) -> bool:
    folded = fold(normalize(text))
    if "если" in folded or " то " in folded:
        return False
    return RESULT_HEAD.match(folded) is not None


# ------------------------------------------------- принадлежность множеству

DISJUNCTION = re.compile(r"(?<![а-яa-z0-9])(?:или|либо)(?![а-яa-z0-9])")

# «больше или равно» — знак сравнения, а не перечисление. Эти фразы заперты
# перед поиском дизъюнкции, иначе каждый нестрогий порог читался бы как
# множество из двух элементов.
COMPOUND_OPERATORS = sorted(
    (phrase for phrase, _ in COMPARATORS + POSTFIX_COMPARATORS if re.search(r"(?:или|либо)", phrase)),
    key=len, reverse=True,
)


def mask_disjunction_context(text: str) -> str:
    masked = list(mask_quoted(text))
    lowered = "".join(masked)
    for phrase in COMPOUND_OPERATORS:
        for match in re.finditer(re.escape(phrase), lowered):
            for index in range(*match.span()):
                masked[index] = MASK
    return "".join(masked)


def parse_statement_rules(text: str, schema: Schema) -> list[dict]:
    """Утверждение → одно или несколько правил FTS.

    «если статус — «оплачен» или «отгружен»» — принадлежность множеству. В
    правиле FTS нет дизъюнкции: `если`/`и` строят только конъюнкцию. Множество
    раскладывается на правила по одному на элемент — они взаимно исключают друг
    друга, потому что это равенства по одному полю, и порядок их применения
    ничего не меняет.

    Всё, что не раскладывается таким образом, отклоняется. Свернуть «или» в
    одно условие нельзя, а сделать вид, что его не было, — тихая ошибка.
    """
    source = normalize(text)
    if DISJUNCTION.search(mask_disjunction_context(source)) is None:
        return [parse_statement(source, schema)]

    errors = []
    for condition_text, action_text, _ in frame_candidates(source):
        try:
            action = parse_action(action_text, schema)
        except Refusal as error:
            errors.append(f"{error.code}: {error.detail}")
            continue
        body, _ = strip_markers(condition_text)
        masked = mask_disjunction_context(body)
        spans = [match.span() for match in DISJUNCTION.finditer(masked)]
        if not spans:
            continue
        parts, cursor = [], 0
        for start, end in spans:
            parts.append(body[cursor:start].strip(" ,;"))
            cursor = end
        parts.append(body[cursor:].strip(" ,;"))
        if any(not part for part in parts):
            continue

        conditions, head = [], None
        try:
            for part in parts:
                if find_comparator(part) is None and head is not None:
                    # «… равен «оплачен» или «отгружен»»: во втором элементе
                    # поле и знак опущены — берутся из первого.
                    operand = coerce(parse_operand(part, schema, allow_field=False),
                                     schema.type_of(head["field"]), schema)
                    conditions.append({"field": head["field"], "operator": head["operator"], "value": operand})
                    continue
                parsed = parse_conjunct(part, schema)
                if head is None:
                    head = parsed
                conditions.append(parsed)
        except Refusal as error:
            errors.append(f"{error.code}: {error.detail}")
            continue

        fields = {item["field"] for item in conditions}
        operators = {item["operator"] for item in conditions}
        if len(fields) != 1 or operators != {"eq"}:
            errors.append("DISJUNCTION_SHAPE: «или» соединяет не равенства по одному полю")
            continue
        return [{"when": [item], "action": action} for item in conditions]

    detail = errors[0] if errors else "«или» в правиле FTS не выражается"
    raise Refusal(detail.split(":")[0] if errors else "DISJUNCTION_UNSUPPORTED", detail)
