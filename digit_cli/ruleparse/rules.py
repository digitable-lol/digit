#!/usr/bin/env python3
"""Configuration R1: a hand-written Russian rule layer over the catalog.

R0 (lexicon.py) matches a query against the words the catalog already ships.
That covers a tool when the user happens to speak the catalog's vocabulary
(«сделай slug», «hmac»), and fails when they speak Russian («чьё это железо» for
mac-address-lookup, «открыть в экселе» for json-to-csv).

R1 adds three kinds of rule, all declarative, all auditable:

  1. OPERATIONS - Russian verb lemmas grouped into operation classes. Written
     once, shared by every tool.
  2. FORMATS + pair routing - the catalog's own titles («JSON в TOML») are
     parsed into a (source, target) routing table. Derived, not authored.
  3. TRIGGERS - per-tool evidence: lemma sets, entity kinds, regexes. This is
     the part that costs authoring time per tool, and its size is reported as
     the maintenance price of the approach.

Plus SLOTS: argument extraction, and REQUIRED: which arguments a tool cannot be
called without. REQUIRED is what turns «посчитай хэш» into a refusal instead of
an invented answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import entities as E
from .morph import Token, normalize, prep_frames

# ---------------------------------------------------------------------------
# 1. Operation classes: Russian verbs -> what the user wants done.
# ---------------------------------------------------------------------------
OPERATIONS: dict[str, set[str]] = {
    "hash": {"хешировать", "хэшировать", "захешировать", "захэшировать", "хеш",
             "хэш", "хеширование", "хэширование", "дайджест", "отпечаток"},
    "encrypt": {"шифровать", "зашифровать", "шифрование", "шифроваться"},
    "decrypt": {"расшифровать", "дешифровать", "расшифровка", "расшифровывать"},
    "encode": {"кодировать", "закодировать", "кодирование", "перекодировать",
               "экранировать", "экранирование"},
    "decode": {"раскодировать", "декодировать", "декодирование", "расшифровать",
               "разэкранировать"},
    "generate": {"генерировать", "сгенерировать", "сгенерить", "нагенерить",
                 "генератор", "создать", "сделать", "выдать", "дать", "накидать",
                 "собрать", "подсказать", "родить"},
    "convert": {"перевести", "переводить", "преобразовать", "преобразование",
                "конвертировать", "конвертер", "перегнать", "переделать",
                "превратить", "переложить", "записать", "запись"},
    "format": {"форматировать", "отформатировать", "форматирование",
               "причесать", "причесывать", "выровнять", "красивый", "читаемый",
               "читабельный", "минифицировать", "ужать", "сжать", "сжатие"},
    "parse": {"разобрать", "разбирать", "разбор", "распарсить", "парсить",
              "парсер", "прочитать", "глянуть", "посмотреть", "показать",
              "определить", "узнать", "восстановить", "увидеть"},
    "diff": {"сравнить", "сравнение", "различие", "отличаться", "отличие",
             "изменить", "измениться", "разница", "дифф"},
    "calc": {"посчитать", "считать", "вычислить", "рассчитать", "расчет",
             "калькулятор", "сколько", "скока", "сколь"},
    "validate": {"проверить", "проверка", "валидировать", "валидация",
                 "убедиться", "корректный", "правильность", "настоящий"},
    "mask": {"замаскировать", "маскировать", "маскирование", "скрыть",
             "спрятать", "обфусцировать", "затереть"},
    "normalize": {"нормализовать", "нормализация", "унифицировать",
                  "дедупликация", "задвоиться", "единый"},
}
LEMMA_TO_OPS: dict[str, set[str]] = {}
for _op, _lemmas in OPERATIONS.items():
    for _l in _lemmas:
        LEMMA_TO_OPS.setdefault(_l, set()).add(_op)

# Stems, checked as substrings of the surface form. OpenCorpora does not know
# «распарси», «раскодируй» or «хэш-функцыя», and a user who mistypes still
# means the same thing. Stems are the cheap robustness layer under the
# dictionary, and they are why typos cost this system less than they might.
OP_STEMS: list[tuple[str, str]] = [
    ("расшифр", "decrypt"), ("дешифр", "decrypt"), ("раскодир", "decode"),
    ("декодир", "decode"), ("шифр", "encrypt"), ("кодир", "encode"),
    ("хеш", "hash"), ("хэш", "hash"), ("хеширу", "hash"),
    ("парс", "parse"), ("разбер", "parse"), ("разбор", "parse"),
    ("генер", "generate"), ("сгенер", "generate"),
    ("конверт", "convert"), ("перевед", "convert"), ("перевод", "convert"),
    ("преобраз", "convert"), ("перегон", "convert"), ("переделай", "convert"),
    ("формат", "format"), ("минифиц", "format"), ("причеш", "format"),
    ("сравн", "diff"), ("отлич", "diff"), ("измен", "diff"),
    ("маскир", "mask"), ("обфусц", "mask"),
    ("посчита", "calc"), ("подсчита", "calc"), ("вычисл", "calc"),
    ("провер", "validate"), ("валид", "validate"),
    ("нормализ", "normalize"),
]


def ops_from_stems(text_norm: str) -> set[str]:
    return {op for stem, op in OP_STEMS if stem in text_norm}


# Nouns OpenCorpora does not have. «урла» lemmatises to «урла», «юрла» to
# «юрло», «двухфакторки» to «двухфакторк» - all of them useless as lexicon
# keys. A stem table is the standard repair, and it costs one line per word.
NOUN_STEMS: list[tuple[str, str]] = [
    ("урл", "урл"), ("юрл", "урл"), ("двухфактор", "двухфакторка"),
    ("юникод", "юникод"), ("эмодз", "эмодзи"), ("эмодж", "эмодзи"),
    ("кебаб", "кебаб"), ("регуляр", "регулярка"), ("подсет", "подсеть"),
    ("вендор", "вендор"), ("хеш", "хеш"), ("хэш", "хеш"),
    ("нумероним", "нумероним"), ("слаг", "слаг"), ("слуг", "слаг"),
    ("кронтаб", "кронтаб"), ("крон", "крон"), ("таймстам", "таймстамп"),
    ("айпи", "ip"), ("мак-адрес", "мак"), ("макадрес", "мак"),
    ("жсон", "json"), ("ямл", "yaml"), ("томл", "toml"),
    ("бэйс", "base64"), ("бейс", "base64"), ("б64", "base64"),
    ("двоич", "двоичный"), ("бинарн", "двоичный"),
    ("шестнадцатерич", "шестнадцатеричный"), ("десятич", "десятичный"),
    ("восьмерич", "восьмеричный"), ("римск", "римский"),
    ("фаренгейт", "фаренгейт"), ("цельси", "цельсий"), ("кельвин", "кельвин"),
    ("процент", "процент"), ("маскир", "маскировать"),
    ("идентификатор", "идентификатор"), ("сортир", "сортироваться"),
    ("вайфа", "wifi"), ("эксел", "эксель"), ("ексел", "эксель"), ("экран", "экранировать"), ("сущност", "сущность"),
    ("стойк", "стойкий"), ("подключ", "подключаться"),
]


def lemmas_from_stems(tokens_norm: list[str]) -> set[str]:
    out: set[str] = set()
    for word in tokens_norm:
        for stem, canon in NOUN_STEMS:
            if word.startswith(stem):
                out.add(canon)
    return out


# ---------------------------------------------------------------------------
# 2. Formats: how a Russian speaker names a data format.
# ---------------------------------------------------------------------------
FORMAT_WORDS: dict[str, str] = {
    "json": "json", "жсон": "json", "джейсон": "json",
    "yaml": "yaml", "yml": "yaml", "ямл": "yaml", "яамл": "yaml",
    "toml": "toml", "томл": "toml",
    "xml": "xml", "иксмл": "xml",
    "csv": "csv", "эксель": "csv", "excel": "csv", "ексель": "csv",
    "markdown": "markdown", "маркдаун": "markdown", "md": "markdown",
    "html": "html", "хтмл": "html",
    "base64": "base64", "бейс64": "base64", "бэйс64": "base64", "б64": "base64",
    "двоичный": "binary", "бинарный": "binary", "binary": "binary", "бит": "binary",
    "unicode": "unicode", "юникод": "unicode",
    "шестнадцатеричный": "hex", "hex": "hex", "хекс": "hex",
    "десятичный": "dec", "decimal": "dec",
    "восьмеричный": "oct", "octal": "oct",
    "римский": "roman", "roman": "roman", "арабский": "arabic",
    "цельсий": "celsius", "фаренгейт": "fahrenheit", "кельвин": "kelvin",
    "градус": "temperature", "температура": "temperature",
    "rgb": "rgb", "hsl": "hsl", "цвет": "color",
    "url": "url", "урл": "url", "юрл": "url", "ссылка": "url", "адрес": "url",
    "нато": "nato", "nato": "nato", "фонетический": "nato",
    "camelcase": "case", "кебаб": "case", "snake_case": "case",
    "регистр": "case", "кейс": "case",
    "compose": "compose", "докер": "docker", "docker": "docker",
    "sql": "sql", "скуль": "sql", "запрос": "sql",
}

# The catalog's own titles are a routing table: «JSON в TOML» -> json-to-toml.
PAIR_TITLE_RE = re.compile(r"^\s*(.+?)\s+в\s+(.+?)\s*$", re.I)


def derive_pair_table(tools) -> dict[tuple[str, str], str]:
    """(source_format, target_format) -> tool_id, read off `title_ru`.

    Zero hand-authoring: every «X в Y» title in the catalog becomes a route.
    """
    table: dict[tuple[str, str], str] = {}
    for tool in tools:
        m = PAIR_TITLE_RE.match(tool.title_ru)
        if not m:
            continue
        src = FORMAT_WORDS.get(normalize(m.group(1).split()[-1]))
        dst = FORMAT_WORDS.get(normalize(m.group(2).split()[0]))
        if src and dst:
            table[(src, dst)] = tool.tool_id
    # Titles that name the pair the other way round or with a different noun.
    table.update({
        ("json", "yaml"): "json-to-yaml-converter",
        ("yaml", "json"): "yaml-to-json-converter",
        ("json", "toml"): "json-to-toml",
        ("toml", "json"): "toml-to-json",
        ("toml", "yaml"): "toml-to-yaml",
        ("yaml", "toml"): "yaml-to-toml",
        ("xml", "json"): "xml-to-json",
        ("json", "xml"): "json-to-xml",
        ("json", "csv"): "json-to-csv",
        ("markdown", "html"): "markdown-to-html",
        ("docker", "compose"): "docker-run-to-docker-compose-converter",
        ("text", "binary"): "text-to-binary",
        ("binary", "text"): "text-to-binary",
        ("text", "unicode"): "text-to-unicode",
        ("unicode", "text"): "text-to-unicode",
        ("text", "nato"): "text-to-nato-alphabet",
        ("dec", "hex"): "base-converter",
        ("hex", "dec"): "base-converter",
        ("dec", "binary"): "base-converter",
        ("celsius", "fahrenheit"): "temperature-converter",
        ("fahrenheit", "celsius"): "temperature-converter",
        ("roman", "arabic"): "roman-numeral-converter",
        ("arabic", "roman"): "roman-numeral-converter",
    })
    return table


# ---------------------------------------------------------------------------
# 3. Per-tool triggers. THIS is the hand-authored part; count the entries.
# ---------------------------------------------------------------------------
@dataclass
class Trigger:
    tool_id: str
    weight: float = 10.0
    any_lemma: set[str] = field(default_factory=set)   # at least one must appear
    all_lemma: set[str] = field(default_factory=set)   # every one must appear
    any_ent: set[str] = field(default_factory=set)     # at least one entity kind
    any_op: set[str] = field(default_factory=set)      # at least one operation class
    rx: re.Pattern | None = None
    not_lemma: set[str] = field(default_factory=set)   # veto
    not_ent: set[str] = field(default_factory=set)     # veto on a detected shape
    not_op: set[str] = field(default_factory=set)      # veto on an operation

    def fires(self, lem: set[str], ents: set[str], ops: set[str], text: str) -> bool:
        if self.not_lemma & lem or self.not_ent & ents or self.not_op & ops:
            return False
        if self.all_lemma and not self.all_lemma <= lem:
            return False
        if self.any_lemma and not (self.any_lemma & lem):
            return False
        if self.any_ent and not (self.any_ent & ents):
            return False
        if self.any_op and not (self.any_op & ops):
            return False
        if self.rx and not self.rx.search(text):
            return False
        return bool(self.any_lemma or self.all_lemma or self.any_ent or self.any_op or self.rx)


def T(tool_id, weight=10.0, **kw) -> Trigger:
    for key in ("any_lemma", "all_lemma", "any_ent", "any_op", "not_lemma",
                "not_ent", "not_op"):
        if key in kw and not isinstance(kw[key], set):
            kw[key] = set(kw[key])
    return Trigger(tool_id, weight, **kw)


TRIGGERS: list[Trigger] = [
    # --- crypto -----------------------------------------------------------
    T("hash-text", 14, any_op={"hash"},
      not_lemma={"bcrypt", "hmac", "пароль", "подпись", "подписываться", "вебхук"}),
    T("hash-text", 12, any_lemma={"md5", "sha1", "sha256", "sha512", "sha384",
                                  "sha224", "sha3", "ripemd160"},
      not_lemma={"bcrypt", "hmac", "подпись", "вебхук", "секрет"}),
    T("hmac-generator", 18, any_lemma={"hmac", "подпись", "подписываться", "вебхук",
                                       "webhook", "хмак"}),
    T("bcrypt", 18, any_lemma={"bcrypt", "бикрипт"}),
    T("bcrypt", 12, all_lemma={"пароль"}, any_op={"hash"}),
    T("bcrypt", 14, any_ent={"bcrypt_hash"}),
    T("password-strength-analyser", 16,
      any_lemma={"стойкий", "стойкость", "надежность", "надежный", "перебрать",
                 "подбор", "взломать", "сложность"}, all_lemma={"пароль"}),
    T("token-generator", 14, all_lemma={"токен"}, any_op={"generate"},
      not_lemma={"jwt", "otp", "маскировать", "замаскировать", "ulid"},
      not_ent={"jwt"}),
    T("token-generator", 12, any_lemma={"случайный"}, all_lemma={"строка"}, any_op={"generate"}),
    T("uuid-generator", 18, any_lemma={"uuid", "гуид", "guid"}),
    T("ulid-generator", 18, any_lemma={"ulid", "улид"}),
    T("ulid-generator", 16, all_lemma={"идентификатор"},
      any_lemma={"сортироваться", "сортируемый", "лексикографический", "событие"}),
    # Encryption needs a key or a named cipher; without one the request is not
    # actionable and the base64 reading is the likelier one.
    T("encryption", 16, any_op={"encrypt", "decrypt"},
      not_lemma={"bcrypt", "hmac", "пароль", "хеш", "хэш"},
      rx=re.compile(r"\b(?:aes|tripledes|rabbit|rc4|ключ\w*|секрет\w*|шифр\w*)\b", re.I)),
    T("encryption", 14, any_lemma={"aes", "tripledes", "rabbit", "rc4"}),
    T("rsa-key-pair-generator", 18, any_lemma={"rsa"}),
    T("rsa-key-pair-generator", 10, all_lemma={"ключ"}, any_lemma={"пара", "pem", "приватный", "публичный"}),
    T("bip39-generator", 18, any_lemma={"bip39", "мнемоника", "мнемонический", "сид"}),
    T("otp-generator", 18, any_lemma={"otp", "totp", "одноразовый", "2fa", "мфа"}),
    T("otp-generator", 16, rx=re.compile(r"двухфактор|двухэтапн|аутентификатор|totp", re.I)),
    T("pdf-signature-checker", 16, all_lemma={"pdf"}, any_lemma={"подпись", "подписать"}),

    # --- encodings --------------------------------------------------------
    T("base64-string-converter", 14, any_lemma={"base64", "бейс64", "бэйс64", "б64"},
      not_lemma={"basic", "auth", "файл"}),
    # A lone base64 blob nobody else claims: «что зашифровано в этой строке: 0J/…»
    T("base64-string-converter", 12, any_ent={"base64"},
      not_lemma={"jwt", "hmac", "bip39", "энтропия", "мнемоника", "ключ", "секрет"},
      not_ent={"jwt", "iban", "bcrypt_hash"}, not_op={"decrypt"}),
    T("url-encoder", 16, all_lemma={"url"}, any_op={"encode", "decode"}),
    T("url-encoder", 14, any_lemma={"урл", "percent", "процентный"}, any_op={"encode", "decode"}),
    T("url-encoder", 12, any_ent={"percent_enc"}),
    T("html-entities", 16, any_lemma={"сущность", "entities", "энтити"},
      any_op={"encode", "decode", "parse"}),
    T("html-entities", 14, all_lemma={"html"}, any_op={"encode", "decode"}),
    T("html-entities", 14, any_ent={"entity_named"}),
    T("html-entities", 10, any_ent={"tag"}, any_op={"encode"}),
    T("text-to-binary", 14, any_ent={"binary"}),
    T("text-to-binary", 14, any_lemma={"двоичный"}, any_op={"convert", "encode"}),
    T("text-to-unicode", 14, any_lemma={"юникод", "unicode", "кодовый"},
      not_lemma={"эмодзи", "эмоджи"}),
    T("text-to-unicode", 14, any_ent={"entity_numeric"}),
    T("text-to-nato-alphabet", 18, any_lemma={"нато", "nato", "фонетический", "рация",
                                              "продиктовать", "диктовать"}),
    T("base-converter", 14, any_lemma={"система"}, all_lemma={"счисление"}),
    T("roman-numeral-converter", 16, any_lemma={"римский"}),
    # `CLI`, `MIX`, `DIM` are all valid Roman numerals by shape. The shape is
    # only evidence when the query is actually about a number.
    T("roman-numeral-converter", 14, any_ent={"roman"},
      any_lemma={"число", "цифра", "римский", "numeral", "арабский"},
      not_ent={"iban", "mac", "user_agent", "jwt", "url", "base64"}),
    T("color-converter", 16, any_lemma={"цвет", "hex", "rgb", "hsl"}, any_ent={"hexcolor"}),
    T("case-converter", 16, any_lemma={"camelcase", "camel", "snake", "кебаб", "kebab",
                                       "pascalcase", "регистр", "кейс"}),
    T("date-converter", 14, any_lemma={"таймстамп", "timestamp", "unix", "юникс", "эпоха"}),
    T("date-converter", 12, any_ent={"timestamp", "iso_date"}, any_op={"convert", "parse"}),
    T("temperature-converter", 18, any_lemma={"фаренгейт", "цельсий", "кельвин", "градус"}),

    # --- structured data --------------------------------------------------
    T("json-prettify", 12, all_lemma={"json"}, any_op={"format"},
      not_lemma={"минифицировать", "ужать", "сжать", "сжатие", "минифай"}),
    T("json-prettify", 12, all_lemma={"жсон"}, any_op={"format"},
      not_lemma={"минифицировать", "ужать", "сжать", "сжатие"}),
    T("json-minify", 16, any_lemma={"минифицировать", "минификация", "ужать", "сжать", "минифай"},
      all_lemma={"json"}),
    T("json-minify", 14, any_lemma={"ужать", "сжать", "минифицировать"}, all_lemma={"жсон"}),
    T("xml-formatter", 14, all_lemma={"xml"}, any_op={"format"}),
    T("yaml-prettify", 12, all_lemma={"yaml"}, any_op={"format"}),
    T("sql-prettify", 16, any_lemma={"sql", "скуль", "sql-запрос"}, any_op={"format"}),
    T("sql-prettify", 12, any_ent={"sql"}, any_op={"format"}),
    T("json-diff", 16, all_lemma={"json"}, any_op={"diff"}),
    T("json-diff", 12, any_op={"diff"}, rx=re.compile(r"\{.*\}.*\{.*\}", re.S)),
    T("text-diff", 12, any_op={"diff"}, any_lemma={"текст", "версия", "регламент", "документ"},
      not_lemma={"json", "жсон"}),
    T("list-converter", 14, any_lemma={"столбец", "список", "колонка"},
      any_op={"convert", "format", "generate"}),
    T("list-converter", 10, all_lemma={"кавычка"}, any_lemma={"склеить", "обернуть", "запятая"}),

    # --- network ----------------------------------------------------------
    T("ipv4-subnet-calculator", 16, any_ent={"cidr"},
      any_lemma={"подсеть", "маска", "broadcast", "хост", "диапазон", "сеть", "cidr"}),
    T("ipv4-subnet-calculator", 12, any_ent={"cidr"}, any_op={"calc"}),
    T("ipv4-address-converter", 14, any_ent={"ipv4"}, any_op={"convert"}),
    T("ipv4-address-converter", 12, any_ent={"ipv4"},
      any_lemma={"десятичный", "двоичный", "шестнадцатеричный", "acl"}),
    T("ipv4-range-expander", 16, any_lemma={"cidr", "диапазон"},
      rx=re.compile(r"(?:\d{1,3}\.){3}\d{1,3}\D{1,12}(?:\d{1,3}\.){3}\d{1,3}")),
    T("ipv6-ula-generator", 18, any_lemma={"ipv6", "ula"}),
    T("mac-address-generator", 16, any_lemma={"mac", "мак"}, any_op={"generate"},
      not_lemma={"вендор", "производитель", "железо", "чей"}),
    T("mac-address-lookup", 16, any_ent={"mac"},
      any_lemma={"вендор", "производитель", "железо", "чей", "устройство", "изготовитель"}),
    T("random-port-generator", 18, any_lemma={"порт"},
      any_op={"generate", "parse"}, not_lemma={"портировать"}),
    T("url-parser", 16, any_lemma={"url", "урл", "юрл", "ссылка"}, any_op={"parse"},
      not_lemma={"safelink", "outlook", "аутлук", "qr", "кюар"}),
    T("url-parser", 10, any_ent={"url"}, any_lemma={"параметр", "парамерт", "составной",
                                                    "часть", "протокол", "домен"}),
    T("safelink-decoder", 18, any_lemma={"safelink", "outlook", "аутлук"}),
    T("safelink-decoder", 16, any_ent={"safelink"}),
    T("user-agent-parser", 18, any_lemma={"useragent", "ua", "браузер"},
      any_ent={"user_agent"}),
    T("user-agent-parser", 14, any_ent={"user_agent"}),
    T("jwt-parser", 18, any_lemma={"jwt"}),
    T("jwt-parser", 16, any_ent={"jwt"}),
    T("basic-auth-generator", 18, all_lemma={"basic"}, any_lemma={"auth", "авторизация"}),
    T("basic-auth-generator", 14, any_lemma={"хидер", "заголовок", "header", "401"},
      all_lemma={"авторизация"}),
    T("basic-auth-generator", 12, any_lemma={"логин", "юзер", "пользователь"},
      all_lemma={"пароль"}, rx=re.compile(r"\b(?:basic|auth|401|хидер|заголовок|курл|curl)", re.I)),
    T("http-status-codes", 18, all_lemma={"http"}, any_lemma={"код", "статус"}),
    T("mime-types", 18, any_lemma={"mime", "майм"}),
    T("http-status-codes", 14,
      rx=re.compile(r"\bhttp[- ]?код|\bкод[а-я]*\s+(?:состояни|ответа|http)|"
                    r"\b(?:код|статус)[а-я]*\s+[1-5]\d\d\b", re.I)),

    # --- data / identifiers ----------------------------------------------
    T("iban-validator-and-parser", 18, any_lemma={"iban", "ибан"}),
    T("iban-validator-and-parser", 14, any_ent={"iban"}, any_op={"validate", "parse"}),
    T("phone-parser-and-formatter", 16, any_lemma={"телефон", "номер", "номир", "мобильный"},
      any_ent={"phone"}),
    T("phone-parser-and-formatter", 12, any_ent={"phone"}, any_op={"parse", "format", "validate"}),
    T("email-normalizer", 16, any_ent={"email"},
      any_op={"normalize"}, ),
    T("email-normalizer", 14, any_lemma={"почта", "адрес", "email", "мейл"},
      any_op={"normalize"}),
    T("chmod-calculator", 18, any_lemma={"chmod", "чмод", "право"},
      rx=re.compile(r"chmod|прав[ао]\s+доступ|владелец", re.I)),
    T("crontab-generator", 18, any_lemma={"cron", "crontab", "крон", "кронтаб", "расписание"}),
    T("crontab-generator", 14, any_ent={"cron"}),
    T("regex-tester", 18, any_lemma={"регулярка", "регулярный", "regex", "regexp", "рег"},
      any_op={"validate", "parse", "calc"}),
    T("regex-tester", 12, any_lemma={"регулярка", "регулярный", "regex"}),
    T("docker-run-to-docker-compose-converter", 18, any_ent={"docker_run"}),
    T("docker-run-to-docker-compose-converter", 14, all_lemma={"docker"}, any_lemma={"compose"}),

    # --- text -------------------------------------------------------------
    T("slugify-string", 18, any_lemma={"slug", "слаг", "слагифай"}),
    T("text-statistics", 16, any_lemma={"символ", "слово", "байт", "длина", "весить", "статистика"},
      any_op={"calc"}, not_lemma={"нумероним", "i18n", "токен"}),
    T("string-obfuscator", 16, any_op={"mask"}),
    T("string-obfuscator", 12, any_lemma={"опознать"},
      rx=re.compile(r"не\s+(?:прочитать|раскрыв|показыв)|перв\w+\s+\d|последн\w+\s+\d")),
    T("numeronym-generator", 18, any_lemma={"нумероним", "i18n", "a11y", "l10n"}),
    T("numeronym-generator", 12, any_lemma={"сокращать", "сократить", "сокращение"},
      rx=re.compile(r"i18n|a11y|l10n")),
    T("lorem-ipsum-generator", 18, any_lemma={"lorem", "ipsum", "лорем", "рыба", "заглушка"}),
    T("ascii-text-drawer", 18, any_lemma={"ascii", "аски"}, rx=re.compile(r"арт|art|надпись|шрифт", re.I)),
    T("emoji-picker", 16, any_lemma={"эмодзи", "эмоджи", "смайл", "emoji"}),
    T("text-diff", 10, any_op={"diff"}, any_lemma={"строка", "текст"}),

    # --- math / measurement ----------------------------------------------
    T("math-evaluator", 16, rx=re.compile(r"(?:sqrt|sin|cos|tan|abs|log|exp)\s*\(|\d\s*[\^*/+]\s*\d"),
      any_op={"calc"}),
    T("math-evaluator", 12, rx=re.compile(r"\d+\s*\^\s*\d+|sqrt\s*\(")),
    T("percentage-calculator", 18, any_lemma={"процент"}, any_op={"calc"}),
    T("percentage-calculator", 14, rx=re.compile(r"\d+\s*%"), any_op={"calc"}),
    T("percentage-calculator", 12, any_lemma={"вырасти", "упасть", "измениться", "прирост"},
      rx=re.compile(r"процент")),
    T("eta-calculator", 16, any_lemma={"закончить", "завершение", "успеть", "останется",
                                       "остаться", "eta"},
      rx=re.compile(r"\d+\s*(?:файл|штук|запис|строк|элемент)")),
    T("benchmark-builder", 14, any_lemma={"бенчмарк", "benchmark", "замер"}),
    T("qrcode-generator", 18, any_lemma={"qr", "кюар", "qrcode", "куар"},
      not_lemma={"wifi", "вайфай", "сеть"}),
    T("wifi-qrcode-generator", 20, any_lemma={"wifi", "вайфай", "wi-fi"},
      rx=re.compile(r"qr|кюар|куар|подключ", re.I)),
    T("wifi-qrcode-generator", 16, any_lemma={"сеть", "гость", "переговорка"},
      rx=re.compile(r"qr|кюар|куар", re.I), all_lemma={"пароль"}),
    # «чтобы гости сами подключались к сети X с паролем Y» never says "QR",
    # but naming a network AND its password is what the Wi-Fi tool is for.
    T("wifi-qrcode-generator", 16, all_lemma={"сеть"},
      any_lemma={"подключаться", "подключиться", "подключение", "подключать"},
      rx=re.compile(r"парол", re.I)),
]


# ---------------------------------------------------------------------------
# 4. Required arguments. A tool whose required argument is absent from the
#    query is NOT called - the system refuses. This single table is what makes
#    «посчитай хэш» a refusal rather than a hallucinated digest.
# ---------------------------------------------------------------------------
REQUIRED: dict[str, list[str]] = {
    "hash-text": ["clearText"],
    "hmac-generator": ["plainText", "secret"],
    "bcrypt": ["input"],
    "password-strength-analyser": ["password"],
    "encryption": ["cypherInput", "cypherSecret"],
    "bip39-generator": ["entropy"],
    "base64-string-converter": ["textInput"],
    "url-encoder": ["encodeInput"],
    "html-entities": ["escapeInput"],
    "text-to-binary": ["inputText"],
    "text-to-unicode": ["inputText"],
    "text-to-nato-alphabet": ["input"],
    "base-converter": ["input"],
    "date-converter": ["inputDate"],
    "case-converter": ["input"],
    "roman-numeral-converter": ["inputRoman"],
    "json-to-yaml-converter": ["input"],
    "yaml-to-json-converter": ["input"],
    "json-to-toml": ["input"],
    "toml-to-json": ["input"],
    "toml-to-yaml": ["input"],
    "yaml-to-toml": ["input"],
    "xml-to-json": ["input"],
    "json-to-xml": ["input"],
    "json-to-csv": ["input"],
    "markdown-to-html": ["inputMarkdown"],
    "json-prettify": ["rawJson"],
    "json-minify": ["input"],
    "xml-formatter": ["input"],
    "yaml-prettify": ["rawYaml"],
    "sql-prettify": ["rawSQL"],
    "json-diff": ["rawLeftJson", "rawRightJson"],
    "crontab-generator": ["cron"],
    "jwt-parser": ["rawJwt"],
    "url-parser": ["urlToParse"],
    "safelink-decoder": ["inputSafeLinkUrl"],
    "user-agent-parser": ["ua"],
    "iban-validator-and-parser": ["rawIban"],
    "phone-parser-and-formatter": ["rawPhone"],
    "email-normalizer": ["emails"],
    "ipv4-subnet-calculator": ["ip"],
    "ipv4-address-converter": ["rawIpAddress"],
    "ipv4-range-expander": ["rawStartAddress", "rawEndAddress"],
    "ipv6-ula-generator": ["macAddress"],
    "mac-address-lookup": ["macAddress"],
    "slugify-string": ["input"],
    "text-statistics": ["text"],
    "string-obfuscator": ["str"],
    "numeronym-generator": ["word"],
    "ascii-text-drawer": ["input"],
    "math-evaluator": ["expression"],
    "regex-tester": ["regex", "text"],
    "docker-run-to-docker-compose-converter": ["dockerRun"],
    "basic-auth-generator": ["username", "password"],
    "mime-types": ["selectedExtension"],
    "http-status-codes": ["search"],
    "qrcode-generator": ["text"],
    "wifi-qrcode-generator": ["ssid", "password"],
    "otp-generator": ["secret"],
    "eta-calculator": ["unitCount", "unitPerTimeSpan", "timeSpan"],
    "percentage-calculator": [],       # either (X,Y) or (from,to); checked in slots
    "chmod-calculator": ["permissions"],
    "mac-address-generator": [],
    "uuid-generator": [],
    "ulid-generator": [],
    "token-generator": [],
    "rsa-key-pair-generator": [],
    "lorem-ipsum-generator": [],
    "random-port-generator": [],
    "color-converter": [],
    "temperature-converter": [],
    "emoji-picker": [],
    "text-diff": [],
    "list-converter": [],
}

# Tools whose whole job is a shape the detectors recognise. If the shape is
# absent the route is void, whatever the words said.
ENTITY_GUARD: dict[str, set[str]] = {
    "jwt-parser": {"jwt"},
    "safelink-decoder": {"safelink", "url"},
    "url-parser": {"url"},
    "user-agent-parser": {"user_agent"},
    "iban-validator-and-parser": {"iban"},
    "phone-parser-and-formatter": {"phone"},
    "ipv4-subnet-calculator": {"cidr"},
    "ipv4-address-converter": {"ipv4"},
    "ipv4-range-expander": {"ipv4"},
    "mac-address-lookup": {"mac"},
    "ipv6-ula-generator": {"mac"},
    "email-normalizer": {"email"},
    "date-converter": {"timestamp", "iso_date"},
    "docker-run-to-docker-compose-converter": {"docker_run"},
    "sql-prettify": {"sql"},
    "json-diff": {"json"},
    "json-prettify": {"json"},
    "json-minify": {"json"},
    "markdown-to-html": {"markdown"},
    "bcrypt": {"bcrypt_hash", "quoted", "identlike", "latin"},
}
