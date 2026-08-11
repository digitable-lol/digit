#!/usr/bin/env python3
"""Argument extraction: pull VERBATIM literals out of the query.

Two principles, both of which exist to make silent errors impossible:

  * Nothing is ever generated. Every argument value is a substring of the user's
    own text (or a number read off it). The system cannot invent a hash input
    because it has no mechanism for inventing anything.
  * If a required argument cannot be located, the tool is not called. The parse
    fails and the system says so. Missing argument -> refusal, never a guess.

The Russian-specific machinery here is the "slot marker" idea: «строку X»,
«пароль X», «с ключом X», «для сообщения X». A marker noun in the accusative or
instrumental introduces its literal, and the literal ends at the next marker or
at a script change (a Latin literal ends where Cyrillic prose resumes).
"""
from __future__ import annotations

import re

from . import entities as E
from .morph import Token, normalize

# Nouns that introduce a literal. Lemma -> canonical slot name.
MARKERS: dict[str, str] = {
    "строка": "text", "текст": "text", "слово": "text", "фраза": "text",
    "сообщение": "text", "надпись": "text", "заголовок": "title",
    "название": "title", "фамилия": "text", "значение": "text",
    "пароль": "password", "логин": "login", "юзер": "login",
    "пользователь": "login", "имя": "login",
    "ключ": "secret", "секрет": "secret", "токен": "token",
    "алгоритм": "algo", "функция": "algo", "шрифт": "font",
    "длина": "length", "раунд": "rounds", "отступ": "indent",
    "страна": "country", "сеть": "ssid", "ssid": "ssid",
    "префикс": "prefix", "энтропия": "entropy", "число": "number",
    "адрес": "address", "url": "url", "ссылка": "url", "юрл": "url", "урл": "url",
    "расширение": "ext", "код": "code", "формат": "format",
    "выражение": "expr", "запрос": "query", "регулярка": "regex",
}
# A literal stops when one of these lemmas is reached: it opens the next slot.
NOTIONS = {
    "система", "счисление", "вид", "кодировка", "стандарт", "бит", "символ",
    "штука", "абзац", "раз", "формате", "через", "плз",
}
BOUNDARY = set(MARKERS) | NOTIONS

RE_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_.\-+@!#$%^&*]{2,}")
RE_TITLECASE_RUN = re.compile(r"(?:\b[А-ЯЁ][а-яё]+\b[ \t]+){1,}\b[А-ЯЁ][а-яё]+\b")
CYRILLIC = re.compile(r"[А-Яа-яЁё]")
LATIN = re.compile(r"[A-Za-z]")

RU_NUMERALS = {
    "один": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
    "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
    "двенадцать": 12, "шестнадцать": 16, "двадцать": 20, "тридцать": 30,
    "сорок": 40, "пятьдесят": 50, "шестьдесят": 60, "сто": 100, "двести": 200,
    "первый": 1, "второй": 2, "третий": 3, "четвертый": 4, "пятый": 5,
    "шестой": 6, "седьмой": 7, "восьмой": 8, "девятый": 9, "десятый": 10,
}
BASE_WORDS = {
    "десятичный": 10, "decimal": 10, "шестнадцатеричный": 16, "hex": 16,
    "двоичный": 2, "бинарный": 2, "binary": 2, "восьмеричный": 8, "octal": 8,
    "base64": 64,
}
HASH_ALGOS = {"md5": "MD5", "sha1": "SHA1", "sha224": "SHA224", "sha256": "SHA256",
              "sha384": "SHA384", "sha512": "SHA512", "sha3": "SHA3",
              "ripemd160": "RIPEMD160"}
CIPHERS = {"aes": "AES", "tripledes": "TripleDES", "rabbit": "Rabbit", "rc4": "RC4"}


def valid_literal(s: str | None) -> str | None:
    """Reject "literals" that are really part of the instruction.

    Three failure modes seen on red-team queries, all of which produced a
    confident tool call on nothing:
      «сделай хэш SHA-256»            -> the algorithm name became the payload
      «захэшируй пароль bcrypt-ом»    -> the tool name became the payload
      «совпадает ли пароль с этим хэшем» -> pure function words became the payload
    A literal must contain at least one word that is not a stopword, not a slot
    marker and not the name of a format, algorithm or tool.

    И четвёртый, найденный позже: «пароль корпоративного сервисного аккаунта
    И СКАЖИ, СКОЛЬКО раз он засветился» — маркер открыл значение, а закрыть
    его было нечем, и в аргумент уехала вторая половина просьбы. Значение не
    приказывает: повелительное наклонение внутри «литерала» означает, что
    граница потеряна и найденное — кусок инструкции.
    """
    if not s:
        return None
    from .morph import STOPWORDS, tokenize as _tok
    flat = re.sub(r"[^a-z0-9а-я]", "", normalize(s))
    if flat in SKIP_IDENTS or flat in HASH_ALGOS or flat in BASE_WORDS:
        return None
    if _is_instruction(s):
        return None
    content = [t for t in _tok(s)
               if t.norm not in STOPWORDS and t.lemma not in MARKERS
               and t.lemma not in BOUNDARY
               and re.sub(r"[^a-z0-9]", "", t.norm) not in SKIP_IDENTS
               and t.norm not in HASH_ALGOS]
    return s if content else None


def _strip(s: str) -> str:
    """Trim framing punctuation but keep sentence punctuation.

    «Привет, мир!» is the string the user wants hashed - exclamation mark and
    all. Stripping it would change the digest, which is the exact kind of quiet
    corruption this system is supposed to be incapable of.
    """
    return s.strip(" \t\n\r,;:—–«»\"'()")


# ---------------------------------------------------------------------------
# «Назван» против «описан»
#
# Весь этот блок отвечает на один вопрос: запрос ПРИНЁС операнд или только
# СКАЗАЛ, где тот лежит? Разница невидима для поиска подстрок и решает всё.
# «посчитай хеш строки digitable» — принёс. «в треке «Базы данных» назван
# порог, переведи это число в двоичную» — сказал: в запросе есть имя трека и
# указание на число, но самого числа нет. Пока извлечение отвечало «в тексте
# есть похожая на литерал подстрока», второе неотличимо от первого, и слой
# правил считал хеш от заголовка главы, показывая его как проверенный ответ.
#
# Отказ здесь ничего не стоит: неразобранный запрос уходит модели. Ложный
# ответ стоит всего, потому что после него модель уже не спросят.
# ---------------------------------------------------------------------------
def _is_instruction(text: str) -> bool:
    """Есть ли в куске повелительное наклонение.

    Приказ — это не данные. «...есть режим распознавания: загрузи мой
    скриншот с QR» ставит после двоеточия не полезную нагрузку, а вторую
    половину просьбы; хвост после двоеточия годится в аргументы, только пока
    в нём никого ни о чём не просят.

    Проверяется не список глаголов, а разбор: pymorphy помечает наклонение
    сам. Форма обязана быть глаголом ПЕРВЫМ разбором — иначе «мой» (которое
    разбирается ещё и как повелительное от «мыть») зарубало бы любой хвост с
    притяжательным местоимением.
    """
    from .morph import analyzer, tokenize as _tok
    for tok in _tok(text):
        if tok.pos != "VERB":
            continue
        if any(p.tag.mood == "impr" for p in analyzer().parse(tok.norm)):
            return True
    return False


def _token_before(tokens: list[Token], pos: int) -> Token | None:
    """Последний токен, кончающийся не позже `pos`."""
    best = None
    for tok in tokens:
        if tok.end <= pos:
            best = tok
        else:
            break
    return best


def payload_quotes(query: str, tokens: list[Token]) -> list[E.Ent]:
    """Кавычки, которые ЦИТИРУЮТ значение, а не НАЗЫВАЮТ источник.

    По-русски кавычки делают две несовместимые работы:

        переведи «Привет, мир!» в base64      — цитата, внутри сам операнд
        в треке «Базы данных» назван порог    — имя, внутри адрес операнда

    Различает их приложение. Имя стоит при родовом существительном («трек»,
    «глава», «курс», «статья»), которое и есть та вещь, что так называется;
    цитата не стоит ни при чём — либо стоит при слот-маркере («строка»,
    «текст», «заголовок»), который объявляет РОЛЬ значения, а не его адрес.
    Поэтому список родовых слов не нужен: достаточно уже имеющейся таблицы
    маркеров, а всякое иное существительное вплотную к кавычкам — источник.

    Вплотную: между существительным и кавычкой допустимы только пробелы.
    «покажи base64 результата: «Привет»» — двоеточие рвёт приложение, там
    кавычки снова цитируют.
    """
    out: list[E.Ent] = []
    for span in E.quoted_spans(query):
        head = _token_before(tokens, span.start)
        gap = query[head.end:span.start] if head else ""
        if (head is not None and head.pos == "NOUN" and gap.strip() == ""
                and head.lemma not in MARKERS):
            continue
        out.append(span)
    return out


def literal_after(query: str, tokens: list[Token], slot: str) -> str | None:
    """Text introduced by a marker of `slot`, ending at the next slot boundary.

    Every marker of the slot is tried and the longest usable literal wins:
    «сколько символов и слов в тексте Мы отправили заказ» has two `text`
    markers («слов», «тексте») and only the second one introduces anything.

    Stopping rules, in order:
      1. the next token that opens a DIFFERENT slot («... с ключом ...»);
      2. a script change, when the literal started in Latin and Cyrillic prose
         resumes («SMIRNOV, помоги с рацией» -> «SMIRNOV»);
      3. end of query.

    А перед всем этим — вопрос, открывает ли маркер значение вообще. Маркер,
    за которым идёт предлог или родительный падеж, ОПИСЫВАЕТ своё значение
    («пароль корпоративного аккаунта», «пароль с этим хэшем») и потому не
    вводит ничего: там, где нет литерала, лучше не найти ни одного, чем
    выдать за него кусок инструкции.
    """
    best: str | None = None
    for i, tok in enumerate(tokens):
        if MARKERS.get(tok.lemma) != slot:
            continue
        # A capitalised word mid-sentence is part of the literal, not a marker:
        # in «сделай из Моя Длинная Строка кебаб-кейс», «Строка» is the payload.
        if i > 0 and tok.text[:1].isupper() and not tok.is_latin:
            continue
        rest = tokens[i + 1:]
        if not rest:
            continue
        # ПОЧЕМУ предлог сразу после маркера закрывает слот, а не открывает.
        # «мой пароль С ЭТИМ хэшем» — предложная группа ОПИСЫВАЕТ пароль
        # («тот, что совпадает с хэшем»), а не называет его. Раньше отсюда
        # выходил «литерал» «с этим хэшем», и bcrypt считал хеш от служебных
        # слов. Настоящее значение идёт за маркером без предлога:
        # «строку digitable», «с ключом my secret key» (предлог там ПЕРЕД
        # маркером, а не после).
        if rest[0].is_prep:
            continue
        # ПОЧЕМУ родительный падеж сразу после маркера — тоже описание.
        # «пароль КОРПОРАТИВНОГО сервисного аккаунта» отвечает на вопрос
        # «чей пароль», то есть говорит, ГДЕ значение, а не какое оно.
        # Литерал так себя не ведёт: он либо не русский вовсе, либо стоит в
        # именительном («в тексте Мы отправили заказ», «строку Привет, мир!»).
        if rest[0].case == "gent" and not rest[0].is_latin and not rest[0].is_digit:
            continue
        # A marker directly followed by another marker introduces nothing:
        # «сколько символов и слов в тексте ...» - «слов» opens no literal.
        head = next((t for t in rest[:2] if not t.is_prep), None)
        if head is not None and head.lemma in BOUNDARY:
            continue
        start = rest[0].start
        end = len(query)
        for j, nxt in enumerate(rest):
            if j == 0:
                continue
            opens_other = nxt.lemma in BOUNDARY and MARKERS.get(nxt.lemma) != slot
            if opens_other:
                end = nxt.start
                break
            if nxt.is_prep and j + 1 < len(rest):
                after = rest[j + 1]
                if after.lemma in BOUNDARY and MARKERS.get(after.lemma) != slot:
                    end = nxt.start
                    break
        chunk = query[start:end]
        if LATIN.match(chunk.lstrip()[:1] or " "):
            m = CYRILLIC.search(chunk)
            if m:
                chunk = chunk[:m.start()]
        out = valid_literal(_strip(chunk))
        if out and (best is None or len(out) > len(best)):
            best = out
    return best


def all_marker_literals(query: str, tokens: list[Token]) -> set[str]:
    """Every literal introduced by an explicit slot marker.

    An argument the user labelled («в тексте X», «с ключом Y») is trustworthy
    even inside a question: naming the role IS the disambiguation.
    """
    out = set()
    for slot in set(MARKERS.values()):
        val = literal_after(query, tokens, slot)
        if val:
            out.add(val)
    return out


def main_literal(query: str, tokens: list[Token], ents: list[E.Ent]) -> str | None:
    """The one literal the query is obviously about, when no marker is present.

    Structured blobs are consulted BEFORE quoted spans: the `"name"` inside a
    pasted JSON object is a key, not a quotation, and picking it would send the
    key alone to the converter.

    Ниже по списку идут ветки, у которых нет за спиной ни одного детектора:
    хвост после двоеточия, латинская подстрока, цепочка слов с большой буквы.
    Они отвечают на вопрос «есть ли в тексте что-нибудь похожее на литерал»,
    а нужен ответ на вопрос «принёс ли запрос операнд». В любом русском
    вопросе, где упомянуты продукт, аббревиатура или заголовок главы, первое
    есть, а второго нет — отсюда «SKU» из «Какой SKU оферта фиксирует» и
    «Прикладная криптография» из «В главе «Прикладная криптография» сказано».
    Поэтому у каждой такой ветки стоит своя проверка на то, что найденное не
    несёт в предложении собственной работы.
    """
    for kind in ("json", "yaml", "toml", "xml", "markdown", "sql", "docker_run",
                 "user_agent", "jwt", "base64", "binary", "entity_named",
                 "entity_numeric", "percent_enc", "tag"):
        hit = [e for e in ents if e.kind == kind]
        if hit:
            return hit[0].value
    q = payload_quotes(query, tokens)
    if q:
        return q[0].value
    for kind in ("url",):
        hit = [e for e in ents if e.kind == kind]
        if hit:
            return hit[0].value
    # after a trailing colon: «переведи этот yaml в json:\nname: api»
    m = re.search(r":\s*\n(.+)$", query, re.S)
    if m and len(m.group(1).strip()) > 2 and not _is_instruction(m.group(1)):
        return m.group(1).strip()
    m = re.search(r":\s+(\S.*)$", query, re.S)
    if (m and len(m.group(1).strip()) > 2 and not re.match(r"//", m.group(1))
            and not _is_instruction(m.group(1))):
        return _strip(m.group(1))
    for m in RE_IDENT.finditer(query):
        cand = m.group(0)
        low = re.sub(r"[^a-z0-9]", "", normalize(cand))
        if low in SKIP_IDENTS or low in BASE_WORDS or low in HASH_ALGOS:
            continue
        # `SPV-` in «SPV-кошелёк» is the Latin half of a Russian compound, not
        # a literal the user pasted.
        if CYRILLIC.match(query[m.end():m.end() + 1] or " "):
            continue
        # Латиница сразу после слова-понятия ИМЕНУЕТ это понятие, а не
        # приносит данные: «в кодировке windows-1251», «по стандарту ICAO»,
        # «закодируй в base64 кодировку UTF-8». Раньше отсюда выходил
        # операнд, и text-statistics считал статистику слова «windows-1251».
        before = _token_before(tokens, m.start())
        if before is not None and before.lemma in NOTIONS:
            continue
        return cand
    m = RE_TITLECASE_RUN.search(query)
    if m:
        return valid_literal(_strip(m.group(0)))
    return None


# Указатели наружу. Личные местоимения третьего лица заменяют то, что уже
# названо ГДЕ-ТО, указательные — то, на что показывают.
POINTER_PRONOUNS = {"он", "она", "оно", "они"}
DEMONSTRATIVES = {"этот", "тот", "такой", "это"}
# Служебные части речи между приказом и его объектом: «сделай ИЗ него slug»,
# «прогони ЧЕРЕЗ него пароль».
_SKIP_POS = {"PREP", "CONJ", "PRCL"}


def command_pointer(tokens: list[Token]) -> str | None:
    """На что показывает приказ, если он показывает, а не называет.

    Возвращает:
      * лемму существительного при указательном («переведи это ЧИСЛО» ->
        «число», «назови этот ХЭШ» -> «хэш»);
      * пустую строку, если местоимение голое («возьми ЕЁ», «сделай из НЕГО»);
      * None, если объект приказа назван прямо («переведи строку Привет»).

    Смотрится только ПЕРВОЕ дополнение каждого повелительного глагола: это и
    есть то, над чем велено работать. «закодируй строку a+b/c=d в base64,
    ... она пойдёт в query-параметр» — «она» здесь подлежащее другого
    предложения и объекта приказа не касается.
    """
    from .morph import analyzer
    for i, tok in enumerate(tokens):
        if tok.pos != "VERB":
            continue
        if not any(p.tag.mood == "impr" for p in analyzer().parse(tok.norm)):
            continue
        for j in range(i + 1, min(i + 5, len(tokens))):
            nxt = tokens[j]
            if nxt.pos in _SKIP_POS and nxt.lemma not in DEMONSTRATIVES:
                continue
            if nxt.lemma in DEMONSTRATIVES:
                for k in range(j + 1, min(j + 4, len(tokens))):
                    head = tokens[k]
                    # «переведи это 400 в двоичную» — «это» тут связка, а не
                    # определение к числу; категории оно не называет, значит
                    # указатель голый, и решать будет наличие опоры.
                    if head.is_digit:
                        return ""
                    if head.pos == "NOUN" or head.pos is None:
                        return head.lemma
                return ""
            if nxt.pos == "NPRO" and nxt.lemma in POINTER_PRONOUNS:
                return ""
            break
    return None


SKIP_IDENTS = {
    "base64", "json", "yaml", "yml", "toml", "xml", "csv", "html", "markdown",
    "http", "https", "url", "jwt", "hmac", "bcrypt", "uuid", "ulid", "rsa",
    "aes", "sha", "md5", "otp", "totp", "mac", "ipv4", "ipv6", "cidr", "iban",
    "mime", "cron", "crontab", "regex", "sql", "ascii", "unicode", "nato",
    "camelcase", "camel", "kebab", "snake", "pascal", "slug", "lorem", "ipsum",
    "docker", "compose", "wifi", "qrcode", "safelink", "outlook", "utf",
    "standard", "pem", "bip39", "user_first_name_PLACEHOLDER",
}


def numbers(tokens: list[Token]) -> list[int]:
    return [int(t.norm) for t in tokens if t.is_digit]


# ---------------------------------------------------------------------------
# Число, НАЗЫВАЮЩЕЕ ПАРАМЕТР, — это не операнд (DGT-DIGIT-12).
#
# Тот же класс ошибки, что уже пойман для литералов в `_is_instruction`, но у
# чисел: «переведи число из системы счисления 16 в 2» не содержит числа для
# перевода — 16 и 2 называют ОСНОВАНИЯ. «верхний предел 3999» называет предел,
# а не операнд. Пока извлечение брало `numbers(tokens)[0]`, оно переводило
# основание в двоичную и печатало ответ как проверенный.
#
# Замер на измерительном наборе: слой правил доходил до ответа на 10 запросах,
# где набор ждёт отказа; три из них — ровно этот случай.
#
# Отказ здесь ничего не стоит: пустой обязательный слот значит «не разобрали»,
# и запрос уходит модели обычным ходом (agent/rule_cascade.py).
# ---------------------------------------------------------------------------
def _numbers_bound_to(tokens: list[Token], lemmas: set[str], span: int = 2) -> set[int]:
    """Значения чисел, стоящих вплотную к слову, которое называет параметр."""
    bound: set[int] = set()
    for i, tok in enumerate(tokens):
        if tok.lemma not in lemmas and tok.norm not in lemmas:
            continue
        for j in range(max(0, i - span), min(len(tokens), i + span + 1)):
            if tokens[j].is_digit:
                bound.add(int(tokens[j].norm))
    return bound


#: Слова, после которых число называет ОСНОВАНИЕ системы счисления.
BASE_CONTEXT = {"система", "счисление", "основание", "base", "ричный", "разряд"}
#: Слова, после которых число называет ГРАНИЦУ, а не операнд.
LIMIT_CONTEXT = {"предел", "максимум", "минимум", "лимит", "диапазон",
                 "граница", "порог", "ограничение"}


def free_numbers(tokens: list[Token], context: set[str]) -> list[int]:
    """Числа, НЕ связанные словом-именем параметра, в порядке появления.

    Прилагается к операндным слотам. Пустой список значит «операнда в запросе
    нет», а не «возьми первое попавшееся число».
    """
    bound = _numbers_bound_to(tokens, context)
    return [n for n in numbers(tokens) if n not in bound]


def number_near(tokens: list[Token], lemmas: set[str],
                exclude: set[int] | None = None) -> int | None:
    """The digit standing next to one of `lemmas` («12 раундами», «отступ 4»).

    Surface form is checked as well as the lemma: OpenCorpora reads «бит» as a
    form of «битый», so «на 4096 бит» would otherwise find nothing.
    """
    for i, tok in enumerate(tokens):
        if tok.lemma in lemmas or tok.norm in lemmas:
            for j in (i - 1, i + 1, i - 2, i + 2):
                if 0 <= j < len(tokens) and tokens[j].is_digit:
                    val = int(tokens[j].norm)
                    # Число, уже занятое другим параметром (номер версии), не
                    # годится в этот слот — но соседнее свободное годится.
                    if exclude and val in exclude:
                        continue
                    return val
    return None


def ru_number_near(tokens: list[Token], lemmas: set[str]) -> int | None:
    """«шестьдесят четвёртой системе» -> 64. Numerals spelled out in words."""
    for i, tok in enumerate(tokens):
        if tok.lemma not in lemmas:
            continue
        acc, seen = 0, False
        for j in range(max(0, i - 3), i):
            val = RU_NUMERALS.get(tokens[j].lemma)
            if val is not None:
                acc += val
                seen = True
        if seen:
            return acc
    return None


def _ent(ents: list[E.Ent], *kinds: str) -> str | None:
    for kind in kinds:
        for e in ents:
            if e.kind == kind:
                return e.value
    return None


def _ents_all(ents: list[E.Ent], kind: str) -> list[str]:
    return [e.value for e in ents if e.kind == kind]


# ---------------------------------------------------------------------------
def extract(tool_id: str, query: str, tokens: list[Token],
            ents: list[E.Ent], lem: set[str], ops: set[str]) -> dict:
    """Best-effort argument dict. Missing keys are the caller's problem."""
    g = lambda *k: _ent(ents, *k)                                    # noqa: E731
    lit = lambda slot: literal_after(query, tokens, slot)            # noqa: E731
    args: dict = {}

    def put(key, value):
        if value is not None and value != "":
            args[key] = value

    if tool_id == "hash-text":
        put("clearText", lit("text") or main_literal(query, tokens, ents))
        for a, name in HASH_ALGOS.items():
            if a in lem:
                put("algorithm", name)
    elif tool_id == "hmac-generator":
        put("plainText", g("json") or lit("text") or main_literal(query, tokens, ents))
        put("secret", lit("secret"))
        for a, name in HASH_ALGOS.items():
            if a in lem:
                put("hashFunction", name)
    elif tool_id == "bcrypt":
        if g("bcrypt_hash"):
            put("compareHash", g("bcrypt_hash"))
            put("compareString", lit("password") or lit("text"))
        else:
            put("input", lit("password") or lit("text") or main_literal(query, tokens, ents))
            put("saltCount", number_near(tokens, {"раунд", "round", "соль"}))
    elif tool_id == "password-strength-analyser":
        put("password", lit("password") or main_literal(query, tokens, ents))
    elif tool_id == "encryption":
        blob = g("base64")
        # «её шифровали AES ... верни исходный текст» is a decryption request
        # even though the only verb in it is «шифровали».
        if blob and ({"исходный", "вернуть", "восстановить", "первоначальный",
                      "обратно", "оригинальный"} & lem):
            ops = ops | {"decrypt"}
        if "decrypt" in ops and blob:
            put("decryptInput", blob)
            put("decryptSecret", lit("secret"))
            for a, name in CIPHERS.items():
                if a in lem:
                    put("decryptAlgo", name)
        else:
            put("cypherInput", lit("text") or main_literal(query, tokens, ents))
            put("cypherSecret", lit("secret"))
            for a, name in CIPHERS.items():
                if a in lem:
                    put("cypherAlgo", name)
    elif tool_id == "rsa-key-pair-generator":
        put("bits", number_near(tokens, {"бит", "bit"}))
    elif tool_id == "bip39-generator":
        put("entropy", lit("entropy") or g("base64") or
            (re.search(r"\b[0-9a-f]{16,}\b", query).group(0)
             if re.search(r"\b[0-9a-f]{16,}\b", query) else None))
    elif tool_id == "token-generator":
        put("length", number_near(tokens, {"символ", "длина", "знак"}))
    elif tool_id == "uuid-generator":
        # Номер ВЕРСИИ — не количество. Кириллическую запись «версии 5» отсекали
        # и раньше, но латинская «UUID v5» проходила мимо, и пятёрка уезжала в
        # count: пользователь просил пятую версию, а получал пять штук четвёртой.
        #
        # Гасить count целиком при виде версии нельзя — «дай мне 5 uuid v4»
        # называет и версию, и количество, и запрос законный. Поэтому из
        # кандидатов вычёркивается ровно число версии, а не считается весь
        # запрос испорченным.
        ver = {int(m) for m in re.findall(r"(?:верси\w*\s*|\bv\s?)(\d+)", normalize(query))}
        put("count", number_near(tokens, {"uuid", "штука", "гуид"}, exclude=ver))
    elif tool_id == "ulid-generator":
        put("amount", number_near(tokens, {"ulid", "штука", "идентификатор"}))
    elif tool_id == "otp-generator":
        # A TOTP secret has a shape (base32); prefer the shape over the slot
        # marker, which here tends to swallow the surrounding sentence.
        m = E.RE_TOTP_SECRET.search(query)
        put("secret", (m.group(0) if m else None) or lit("secret"))
    elif tool_id == "base64-string-converter":
        blob = g("base64")
        if not blob and ("decode" in ops or "parse" in ops):
            cand = main_literal(query, tokens, ents)
            if cand and re.fullmatch(r"[A-Za-z0-9+/=]{4,}", cand or ""):
                blob = cand
        # You do not base64-encode something that is already base64: a detected
        # blob means decode unless the user explicitly asked to encode.
        if blob and "encode" not in ops:
            put("base64Input", blob)
        elif ("decode" in ops or "parse" in ops) and blob:
            put("base64Input", blob)
        else:
            put("textInput", lit("text") or main_literal(query, tokens, ents))
            if {"safe", "url", "урл", "urlsafe"} & lem or "url safe" in normalize(query):
                put("encodeUrlSafe", True)
    elif tool_id == "url-encoder":
        pe = g("percent_enc")
        if pe and ("decode" in ops or "parse" in ops or "encode" not in ops):
            put("decodeInput", pe)
        else:
            put("encodeInput", lit("text") or main_literal(query, tokens, ents))
    elif tool_id == "html-entities":
        he = g("entity_named")
        if he:
            put("unescapeInput", he)
        else:
            put("escapeInput", g("tag") or lit("text") or main_literal(query, tokens, ents))
    elif tool_id == "text-to-binary":
        b = g("binary")
        if b:
            put("inputBinary", b)
        else:
            put("inputText", lit("text") or main_literal(query, tokens, ents))
    elif tool_id == "text-to-unicode":
        u = g("entity_numeric")
        if u:
            put("inputUnicode", u)
        else:
            put("inputText", lit("text") or main_literal(query, tokens, ents))
    elif tool_id == "text-to-nato-alphabet":
        put("input", lit("text") or main_literal(query, tokens, ents))
    elif tool_id == "base-converter":
        # Числа, называющие ОСНОВАНИЕ, операндом быть не могут. Кроме соседства
        # со словом «система/счисление» основание встаёт и за предлогом —
        # «...счисления 16 в 2», где второе число называет цель перевода.
        nums = free_numbers(tokens, BASE_CONTEXT)
        if nums and any(t.lemma in BASE_CONTEXT or t.norm in BASE_CONTEXT for t in tokens):
            after_prep = {int(tokens[i].norm) for i in range(1, len(tokens))
                          if tokens[i].is_digit and tokens[i - 1].lemma in {"в", "из", "к", "на"}}
            nums = [n for n in nums if n not in after_prep]
        put("input", str(nums[0]) if nums else None)
        src = dst = None
        from .morph import prep_frames
        for role, _prep, head in prep_frames(tokens):
            base = BASE_WORDS.get(head.lemma)
            if base is None:
                base = ru_number_near(tokens, {"система", "счисление"}) \
                    if head.lemma in {"система", "счисление"} else None
            if base is None:
                continue
            if role == "source" and src is None:
                src = base
            elif role == "target" and dst is None:
                dst = base
        if dst is None:
            dst = ru_number_near(tokens, {"система", "счисление"})
        put("inputBase", src or 10)
        put("outputBase", dst)
    elif tool_id == "date-converter":
        put("inputDate", g("timestamp", "iso_date"))
    elif tool_id == "case-converter":
        put("input", lit("text") or main_literal(query, tokens, ents))
    elif tool_id == "roman-numeral-converter":
        r = g("roman")
        if r:
            put("inputRoman", r)
        else:
            # «верхний предел 3999» называет границу, а не число для перевода.
            nums = free_numbers(tokens, LIMIT_CONTEXT)
            put("inputNumeral", nums[0] if nums else None)
    elif tool_id in {"json-to-yaml-converter", "yaml-to-json-converter", "json-to-toml",
                     "toml-to-json", "toml-to-yaml", "yaml-to-toml", "xml-to-json",
                     "json-to-xml", "json-to-csv", "json-minify", "xml-formatter",
                     "list-converter"}:
        put("input", main_literal(query, tokens, ents))
        if tool_id == "xml-formatter":
            put("indentSize", number_near(tokens, {"отступ", "пробел"}))
    elif tool_id == "markdown-to-html":
        put("inputMarkdown", g("markdown") or main_literal(query, tokens, ents))
    elif tool_id == "json-prettify":
        put("rawJson", g("json"))
        put("indentSize", number_near(tokens, {"отступ", "пробел"}))
    elif tool_id == "yaml-prettify":
        put("rawYaml", g("yaml") or main_literal(query, tokens, ents))
        put("indentSize", number_near(tokens, {"отступ", "пробел"}))
    elif tool_id == "sql-prettify":
        put("rawSQL", g("sql") or main_literal(query, tokens, ents))
    elif tool_id == "json-diff":
        blobs = _ents_all(ents, "json")
        if len(blobs) >= 2:
            put("rawLeftJson", blobs[0])
            put("rawRightJson", blobs[1])
    elif tool_id == "crontab-generator":
        put("cron", g("cron"))
    elif tool_id == "jwt-parser":
        put("rawJwt", g("jwt"))
    elif tool_id == "url-parser":
        put("urlToParse", g("url"))
    elif tool_id == "safelink-decoder":
        put("inputSafeLinkUrl", g("safelink", "url"))
    elif tool_id == "user-agent-parser":
        put("ua", g("user_agent"))
    elif tool_id == "iban-validator-and-parser":
        put("rawIban", g("iban"))
    elif tool_id == "phone-parser-and-formatter":
        put("rawPhone", g("phone"))
        m = re.search(r"\bстран[аыу]?\s+([A-Z]{2})\b", query)
        if m:
            put("defaultCountryCode", m.group(1))
    elif tool_id == "email-normalizer":
        mails = _ents_all(ents, "email")
        if mails:
            first = query.find(mails[0])
            put("emails", _strip(query[first:]))
    elif tool_id == "ipv4-subnet-calculator":
        put("ip", g("cidr"))
    elif tool_id == "ipv4-address-converter":
        put("rawIpAddress", g("ipv4"))
    elif tool_id == "ipv4-range-expander":
        ips = _ents_all(ents, "ipv4")
        if len(ips) >= 2:
            put("rawStartAddress", ips[0])
            put("rawEndAddress", ips[1])
    elif tool_id == "ipv6-ula-generator":
        put("macAddress", g("mac"))
    elif tool_id == "mac-address-lookup":
        put("macAddress", g("mac"))
    elif tool_id == "mac-address-generator":
        put("amount", number_near(tokens, {"адрес", "штука", "мак", "mac"}))
        put("macAddressPrefix", g("mac"))
    elif tool_id == "basic-auth-generator":
        put("username", lit("login"))
        put("password", lit("password"))
    elif tool_id == "mime-types":
        m = re.search(r"расширени\w*\s+([A-Za-z0-9]{2,5})", query) or \
            re.search(r"\.([a-z0-9]{2,5})\b", query)
        put("selectedExtension", m.group(1) if m else None)
    elif tool_id == "http-status-codes":
        m = re.search(r"\b([1-5]\d\d)\b", query)
        put("search", m.group(1) if m else None)
    elif tool_id == "slugify-string":
        put("input", lit("title") or lit("text") or main_literal(query, tokens, ents))
    elif tool_id == "text-statistics":
        put("text", lit("text") or main_literal(query, tokens, ents))
    elif tool_id == "string-obfuscator":
        put("str", lit("token") or lit("secret") or main_literal(query, tokens, ents))
        put("keepFirst", number_near(tokens, {"первый"}))
        put("keepLast", number_near(tokens, {"последний"}))
    elif tool_id == "numeronym-generator":
        put("word", lit("text") or main_literal(query, tokens, ents))
    elif tool_id == "lorem-ipsum-generator":
        put("paragraphs", number_near(tokens, {"абзац", "параграф"}))
        put("words", number_near(tokens, {"слово"}))
        put("sentences", number_near(tokens, {"предложение"}))
    elif tool_id == "ascii-text-drawer":
        put("input", lit("text") or main_literal(query, tokens, ents))
        m = re.search(r"шрифт\w*\s+([A-Za-z][A-Za-z0-9_\-]*)", query)
        put("font", m.group(1) if m else None)
    elif tool_id == "math-evaluator":
        m = E.RE_MATH.search(query)
        if m:
            expr = _strip(m.group(0))
            expr = re.sub(r"\s+", " ", expr).strip(" ?")
            put("expression", expr or None)
    elif tool_id == "percentage-calculator":
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*%\s*от\s*(\d+(?:[.,]\d+)?)", query)
        if m:
            put("percentageX", float(m.group(1)) if "." in m.group(1) else int(m.group(1)))
            put("percentageY", float(m.group(2)) if "." in m.group(2) else int(m.group(2)))
        else:
            m2 = re.search(r"\bс\s+(\d+(?:[.,]\d+)?)\s+до\s+(\d+(?:[.,]\d+)?)", query)
            if m2:
                put("numberFrom", int(m2.group(1)))
                put("numberTo", int(m2.group(2)))
    elif tool_id == "eta-calculator":
        nums = numbers(tokens)
        put("unitCount", nums[0] if len(nums) > 0 else None)
        put("unitPerTimeSpan", nums[1] if len(nums) > 1 else None)
        put("timeSpan", nums[2] if len(nums) > 2 else None)
    elif tool_id == "regex-tester":
        m = re.search(r"(?:[\\][a-zA-Z]|\[[^\]]+\]|\S)*(?:\{\d+(?:,\d*)?\}|\+|\*)(?:[^\s,]|\\.)*",
                      query)
        put("regex", m.group(0) if m and re.search(r"[\\{\[]", m.group(0)) else None)
        put("text", lit("text"))
    elif tool_id == "docker-run-to-docker-compose-converter":
        put("dockerRun", g("docker_run"))
    elif tool_id == "qrcode-generator":
        put("text", g("url") or lit("text") or main_literal(query, tokens, ents))
    elif tool_id == "wifi-qrcode-generator":
        put("ssid", lit("ssid"))
        put("password", lit("password"))
    elif tool_id == "chmod-calculator":
        perms = _chmod(query, tokens)
        put("permissions", perms)
    elif tool_id == "text-diff":
        # Two operands or no call: «сравни с моим текстом: «…»» supplies one.
        pair = len(E.quoted_spans(query)) >= 2 or bool(
            {"два", "две", "оба", "обе", "двумя", "старый", "новый"} & lem)
        put("_operands", "2" if pair else None)
    elif tool_id == "color-converter":
        put("input", g("hexcolor"))
    elif tool_id == "temperature-converter":
        nums = numbers(tokens)
        put("value", nums[0] if nums else None)
        scales = {"цельсий", "фаренгейт", "кельвин", "ранкин", "делиль",
                  "ньютон", "реомюр", "ремер"} & lem
        put("scale", sorted(scales)[0] if scales else None)
    return args


WHO = {"владелец": "owner", "владельцу": "owner", "группа": "group",
       "группе": "group", "остальные": "public", "остальным": "public",
       "все": "public", "прочие": "public", "другой": "public"}
WHAT = {"чтение": "read", "читать": "read", "запись": "write", "писать": "write",
        "выполнение": "execute", "выполнять": "execute", "исполнение": "execute"}


def _chmod(query: str, tokens: list[Token]) -> dict | None:
    """«владельцу чтение, запись и выполнение, группе чтение...» -> permission map."""
    perms = {who: {"read": False, "write": False, "execute": False}
             for who in ("owner", "group", "public")}
    current = None
    seen = False
    for tok in tokens:
        who = WHO.get(tok.lemma) or WHO.get(tok.norm)
        if who:
            current = who
            continue
        what = WHAT.get(tok.lemma) or WHAT.get(tok.norm)
        if what and current:
            perms[current][what] = True
            seen = True
        if tok.lemma in {"ничто", "ничего"} and current:
            seen = True
    return perms if seen else None
