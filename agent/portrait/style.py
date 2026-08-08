"""Слой 1 — СТИЛЬ: как владелец формулирует. Только измеримое.

Что здесь считается стилем
--------------------------
То, что можно снять с текста числом и пересчитать заново на том же корпусе:
длина сообщения и фразы, доля вопросов и повелительных, переключение на
латиницу посреди русской фразы, отличительная лексика — и главное, **какими
словами человек отказывает, сомневается и соглашается**. Речевой акт «отказ»
у одного звучит «мимо», у другого «не будем это делать»; воспроизвести можно
именно поверхность, и именно её здесь и записывают — дословно, с частотой.

Чего здесь нет и не будет
-------------------------
Психологических типологий. MBTI, соционика, архетипы, «психотип» — у них нет
доказанной предсказательной силы, и система, построенная на них, звучит
уверенно ровно там, где не знает ничего. «Владелец — логик-интроверт» нельзя
ни проверить, ни опровергнуть; «владелец отказывает словом „мимо“, 7 раз из
9 отказов» — можно и то, и другое. Портрет держится только на втором.

Отличительность лексики
-----------------------
Частота слова сама по себе бесполезна: топ любого корпуса — предлоги.
Отличительным слово делает сравнение с фоном, и фон здесь — ответы
ассистента в тех же сессиях. Тогда «отличительное» получает точный смысл:
то, что владелец говорит, а модель на его месте не говорит — ровно тот
словарь, которого не хватает, чтобы звучать как он.

Считается лог-шансами с информативным априором (Monroe, Colaresi & Quinn,
2008): голая разность частот даёт наверх редкие слова, встреченные дважды.
Априор берётся из объединённого корпуса, поэтому у слова, встреченного мало,
оценка сама уезжает к нулю — без ручных порогов «не меньше N раз».

Честная граница
---------------
Всё это описывает **поверхность речи**, а не человека. Портрет, набранный на
рабочей переписке, знает, как владелец пишет задачи агенту, и ничего не знает
о том, как он говорит с матерью. Смешивать эти два знания нельзя, поэтому у
каждой цифры хранится ``n`` — сколько сообщений за ней стоит.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from digit_cli.kb.lexical import STOPWORDS, stem

from .provenance import Origin

SCHEMA_VERSION = 2

#: Ниже этого числа сообщений агрегаты показываются как предварительные.
#: Не порог «портрет заработал», а порог «на это уже можно опираться»:
#: медиана длины по трём сообщениям — это длина одного из них.
MIN_MESSAGES_ESTABLISHED = 12

#: Сколько раз должна встретиться конкретная формулировка речевого акта,
#: чтобы считаться характерной, а не разовой.
MIN_ACT_OCCURRENCES = 3

#: Потолок словаря. Сверх него самые редкие термы отбрасываются: словарь
#: растёт линейно по корпусу, а полезен только его верх.
MAX_TERMS = 6000
PRUNE_TO = 4000

#: Блок кода в сообщении — не речь. Он ломает всё сразу: длину фразы, долю
#: латиницы, словарь. Стиль человека, который вставил лог на сорок строк, не
#: изменился от того, что он его вставил.
#: Заодно снимается разметка: вставленный кусок HTML — тоже не речь, а его
#: `div`, `class`, `type` уверенно занимают верх «отличительной лексики»,
#: потому что ассистент их не пишет. Слово, которое человек не произносил,
#: не может быть характерным для него.
_FENCE = re.compile(r"```.*?```|~~~.*?~~~|<[a-zA-Z/!][^>\n]{0,300}>", re.S)

# ---------------------------------------------------------------------------
# Вывод команд, вставленный без разметки
# ---------------------------------------------------------------------------
# Огородить лог тройными кавычками помнит не каждый и не всегда. Неразмеченный
# вывод `systemctl status` или `journalctl` попадал в словарь наравне с речью,
# и верх «отличительной лексики» занимали `service`, `loaded`, `active`,
# `statu`, `kern` — слова, которых владелец не произносил ни разу. Ассистент их
# тоже не пишет, поэтому лог-шансы к фону не спасают: там, где обе стороны
# молчат, любая частота выглядит отличительностью.
#
# Единица разбора — строка, а не сообщение: человек вставляет лог ВНУТРЬ
# сообщения, и выбрасывать сообщение целиком значило бы терять его слова.
# Приметы явные и проверяемые: распознаватель ошибается предсказуемо, и
# ошибку видно глазами на любой строке.
_MACHINE_LINE: Tuple[re.Pattern, ...] = (
    # syslog / journalctl: «Aug 08 10:47:51 host proc[3358]: …»
    re.compile(r"^[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s"),
    # время в начале строки: «2026-08-01 06:13:06 …», «[2026-08-01T06:13]»
    re.compile(r"^\s*\[?\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}"),
    # dmesg / Xorg: «[   41.275] …»
    re.compile(r"^\s*\[\s*\d+\.\d+\]"),
    # уровень журнала в начале строки
    re.compile(
        r"^\s*[\[<(]?(?:TRACE|DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|ERR|"
        r"CRIT|CRITICAL|FATAL|PANIC)[\]>)]?\s*[:\-]"),
    # трассировки Python / JVM / Node
    re.compile(r'^\s*Traceback \(most recent call last\)'),
    re.compile(r'^\s*File "[^"]+", line \d+'),
    re.compile(r"^\s*at [\w$.]+\("),
    re.compile(r"^\s*\w+(?:Error|Exception)\b\s*:"),
    # diff / patch
    re.compile(r"^\s*(?:diff --git |index [0-9a-f]{7,}|\+\+\+ |--- |@@ )"),
    # ls -l
    re.compile(r"^[-dlbcps][-rwxsStT]{9}[.+@]?\s"),
    # ip addr / ifconfig
    re.compile(r"^\s*\d+:\s+[\w.@\-]+:\s*<[A-Z_,\-]*>"),
    # строка access-лога
    re.compile(r'"(?:GET|POST|PUT|PATCH|DELETE|HEAD) /'),
)

#: Приметы, которым можно верить только на строке без кириллицы. «# Заголовок»
#: — это разметка русского текста, а не приглашение root: одна и та же примета
#: значит разное, и различает их язык строки.
_SHELL_PROMPT = re.compile(r"^\s*(?:[$❯➜]\s+\S|#\s+\S|[\w.\-]+@[\w.\-]+:\S*\s*[$#])")
_BOX_DRAWING = re.compile(r"[─│├└┌┐┘┴┬┼╭╰▶►]")
_LONE_PATH = re.compile(r"^[~.]?/[\w./+\-]{4,}[,;:]?$")
#: Маркер состояния, которым `systemctl`, `docker` и сборщики размечают вывод.
_STATUS_BULLET = re.compile(r"^[●○✔✓✗×✖⏺⚠]\s")
#: Строка «поле: значение» из вывода: ``Loaded: loaded (…)``, ``Active: active``.
#: Знак конца фразы её исключает — «Note: it works.» этим приметам не отвечает.
_KEY_VALUE = re.compile(r"^[A-Z][A-Za-z][\w .\-]{0,22}:\s+\S")
_COLUMN_GAP = re.compile(r"\S {2,}\S")
_SENTENCE_END = re.compile(r"[.!?…][»\"')]*$")
#: Поле, которое не бывает словом речи: с цифрой, косой чертой, знаком
#: равенства, аббревиатура целиком или хеш.
_MACHINE_FIELD = re.compile(r"[/\\=]|\d|^[A-Z][A-Z0-9_]+$|^[0-9a-f]{7,}$")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")
_WORD = re.compile(r"[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё'’\-]*")
_LATIN = re.compile(r"^[A-Za-z][A-Za-z0-9'’\-]*$")
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF←-⇿]"
)

# Русский повелительный: -и/-й/-ь(те) на конце первого слова. Приблизительно,
# но проверяемо — распознаватель ошибается предсказуемо и одинаково на всех.
_IMPERATIVE_RU = re.compile(
    r"^(?:"
    r"[а-яё]{2,}(?:[ий]|ь)(?:те)?"
    r")$"
)
_IMPERATIVE_HINTS = frozenset("""
сделай сделайте добавь добавьте убери уберите проверь проверьте покажи покажите
запусти запустите почини почините напиши напишите перепиши перепишите открой
посмотри глянь давай давайте возьми пробуй попробуй пиши читай удали исправь
скажи объясни разберись собери поставь включи выключи останови продолжи
do make add remove check show run fix write rewrite open look try take
""".split())

# ---------------------------------------------------------------------------
# Речевые акты. Ловится НАЧАЛО или отдельная фраза; в портрет кладётся
# дословная поверхность, а не имя класса: воспроизводить нужно «мимо», а не
# «REFUSAL».
# ---------------------------------------------------------------------------
_ACT_PATTERNS: Dict[str, List[re.Pattern]] = {
    "отказ": [
        re.compile(
            r"^(не надо|не нужно|не буду|не будем|не делай(?:те)?|не стоит|"
            r"нет,?|нету|отставить|мимо|не то|не так|убери|отмени|стоп|хватит|"
            r"верни как было|откати)\b",
            re.I,
        ),
        re.compile(r"^(no|nope|don't|do not|stop|revert|drop it|scrap that)\b", re.I),
    ],
    "сомнение": [
        re.compile(
            r"\b(не уверен\w*|сомневаюсь|вряд ли|наверное|наверно|может быть|"
            r"возможно|кажется|похоже|не факт|под вопросом|хз|не знаю)\b",
            re.I,
        ),
        re.compile(r"\b(not sure|maybe|probably|i doubt|seems like|might be)\b", re.I),
    ],
    "согласие": [
        re.compile(
            r"^(да,?|ага|угу|ок(?:ей)?|окей|хорошо|годится|пойдёт|пойдет|"
            r"принято|верно|точно|согласен|согласна|именно|так и есть)\b",
            re.I,
        ),
        re.compile(r"^(yes|yep|ok|okay|sure|agreed|right|correct|exactly)\b", re.I),
    ],
    "одобрение": [
        re.compile(
            r"\b(отлично|супер|класс|красиво|то что надо|идеально|молодец|"
            r"хорошо получилось|нравится)\b",
            re.I,
        ),
        re.compile(r"\b(great|nice|perfect|love it|well done|beautiful)\b", re.I),
    ],
    "требование": [
        re.compile(
            r"\b(обязательно|только так|строго|никаких|ни в коем случае|"
            r"немедленно|сначала|прежде чем|главное|важно чтобы|нельзя чтобы)\b",
            re.I,
        ),
        re.compile(r"\b(must|required|no exceptions|strictly|first of all)\b", re.I),
    ],
}


def sentences(text: str) -> List[str]:
    """Разбить сообщение на фразы. Пустые и служебные строки отбрасываются."""
    out: List[str] = []
    for chunk in _SENTENCE_SPLIT.split(text or ""):
        chunk = chunk.strip()
        if chunk:
            out.append(chunk)
    return out


def _machine_line(line: str) -> bool:
    """Похожа ли строка на вывод программы, а не на сказанное человеком."""
    for pattern in _MACHINE_LINE:
        if pattern.search(line):
            return True
    stripped = line.strip()
    if len(stripped) < 6 or _CYRILLIC.search(stripped):
        return False
    if _SHELL_PROMPT.match(stripped) or _BOX_DRAWING.search(stripped):
        return True
    if _LONE_PATH.match(stripped) or _STATUS_BULLET.match(stripped):
        return True
    fields = stripped.split()
    if len(fields) < 2 or _SENTENCE_END.search(stripped):
        return False
    if _KEY_VALUE.match(stripped):
        return True
    # Таблица: колонки выровнены пробелами, а поля не похожи на слова.
    gaps = len(_COLUMN_GAP.findall(line))
    machineish = sum(1 for field in fields if _MACHINE_FIELD.search(field))
    if len(fields) == 2:
        return gaps >= 1 and machineish >= 1
    return gaps >= 2 or machineish >= max(2, len(fields) * 0.5)


def strip_machine_output(text: str) -> str:
    """Убрать из сообщения строки вставленного вывода, оставив речь.

    После построчного разбора идёт сшивание: строка без собственной приметы,
    но зажатая между двумя строками вывода и без единой кириллической буквы,
    тоже считается выводом. Внутри вставленного блока такие есть всегда —
    ``built-ins``, английская фраза из справки, пустая рамка, — а речь
    владельца не вклинивается в чужой лог на одну строку.
    """
    lines = (text or "").split("\n")
    if len(lines) == 1:
        return text if not _machine_line(lines[0]) else ""
    flags = [_machine_line(line) for line in lines]
    kept: List[str] = []
    for index, line in enumerate(lines):
        if flags[index]:
            continue
        if not _CYRILLIC.search(line) and line.strip():
            if any(flags[max(0, index - 2):index]) and any(flags[index + 1:index + 3]):
                continue
        kept.append(line)
    return "\n".join(kept)


def speech_only(text: str) -> str:
    """Оставить от сообщения то, что владелец действительно произнёс.

    Один и тот же вычет на оба слоя: сначала снимается размеченное (блоки
    кода, HTML), потом неразмеченный вывод команд. Если стиль перестанет
    считать лог речью, а решения продолжат из него вычитываться, портрет
    начнёт ссылаться на фразы, которых в измеренной речи нет.
    """
    return strip_machine_output(_FENCE.sub(" ", text or ""))


def _empty_side() -> Dict[str, Any]:
    return {
        "messages": 0,
        "chars": 0,
        "words": 0,
        "sentences": 0,
        "questions": 0,
        "imperatives": 0,
        "emoji": 0,
        "caps": 0,
        "latin": 0,
        "cyr": 0,
        "len_hist": {},
        "sent_hist": {},
        "terms": {},
        "openers": {},
        "acts": {},
    }


def blank() -> Dict[str, Any]:
    """Пустые достаточные статистики стиля."""
    return {
        "version": SCHEMA_VERSION,
        "owner": _empty_side(),
        "baseline": _empty_side(),
        "first_seen": None,
        "last_seen": None,
    }


def _bump(counter: Dict[str, int], key: str, by: int = 1) -> None:
    counter[key] = counter.get(key, 0) + by


def observe(stats: Dict[str, Any], text: str, *, side: str = "owner") -> Dict[str, Any]:
    """Досчитать статистики стиля одним сообщением. Мутирует и возвращает ``stats``.

    ``side="baseline"`` — реплика ассистента: она нужна только как фон для
    отличительности лексики, поэтому у неё считается словарь и объём, а
    речевые акты и длины — нет. Мерить «как ассистент отказывает» смысла нет:
    это свойство промпта, а не человека.
    """
    text = speech_only(text).strip()
    if not text:
        return stats
    side_stats = stats.setdefault(side, _empty_side())
    now = int(time.time())
    if stats.get("first_seen") is None:
        stats["first_seen"] = now
    stats["last_seen"] = now

    # Словарь считается для обеих сторон — он и есть сравнение.
    words = _WORD.findall(text)
    side_stats["words"] += len(words)
    terms = side_stats["terms"]
    for word in words:
        low = word.lower()
        if low in STOPWORDS or len(low) < 3 or low.isdigit():
            continue
        _bump(terms, stem(low))
    _prune(terms)

    if side != "owner":
        return stats

    side_stats["messages"] += 1
    side_stats["chars"] += len(text)
    _bump(side_stats["len_hist"], str(min(len(text), 4000) // 40))
    side_stats["emoji"] += len(_EMOJI.findall(text))

    has_cyr = bool(_CYRILLIC.search(text))
    for word in words:
        if word.isupper() and len(word) >= 2:
            side_stats["caps"] += 1
        if _LATIN.match(word):
            if has_cyr:
                side_stats["latin"] += 1
        elif _CYRILLIC.search(word):
            side_stats["cyr"] += 1

    phrases = sentences(text)
    side_stats["sentences"] += len(phrases)
    for index, phrase in enumerate(phrases):
        phrase_words = _WORD.findall(phrase)
        _bump(side_stats["sent_hist"], str(min(len(phrase_words), 60)))
        if phrase.rstrip().endswith("?"):
            side_stats["questions"] += 1
        if phrase_words and _is_imperative(phrase_words[0]):
            side_stats["imperatives"] += 1
        if index == 0 and phrase_words:
            _bump(side_stats["openers"], phrase_words[0].lower())
        for act, surface in detect_acts(phrase):
            bucket = side_stats["acts"].setdefault(act, {})
            _bump(bucket, surface)
    return stats


def _is_imperative(first_word: str) -> bool:
    low = first_word.lower()
    if low in _IMPERATIVE_HINTS:
        return True
    return bool(_IMPERATIVE_RU.match(low)) and len(low) >= 4


def detect_acts(phrase: str) -> List[Tuple[str, str]]:
    """Речевые акты фразы: ``(класс, дословная поверхность)``.

    Поверхность нормализуется только регистром и хвостовой запятой — всё
    остальное сохраняется, потому что воспроизводить придётся именно это.
    """
    found: List[Tuple[str, str]] = []
    for act, patterns in _ACT_PATTERNS.items():
        for pattern in patterns:
            match = pattern.search(phrase)
            if match:
                surface = match.group(0).strip().rstrip(",").lower()
                found.append((act, surface))
                break
    return found


def _prune(terms: Dict[str, int]) -> None:
    if len(terms) <= MAX_TERMS:
        return
    keep = sorted(terms.items(), key=lambda kv: -kv[1])[:PRUNE_TO]
    terms.clear()
    terms.update(keep)


# ---------------------------------------------------------------------------
# Вычитание одной сессии
# ---------------------------------------------------------------------------
# Стиль — сумма достаточных статистик, и сумма разбирается обратно: счётчик
# уменьшается ровно на то, что принесла забываемая сессия. Это и делает
# «забудь этот разговор» правдой, а не половиной правды: иначе журнал решений
# чистился, а словарь и длины оставались, то есть по портрету всё ещё можно
# было сказать, о чём и как в том разговоре говорили.

_COUNTERS = ("len_hist", "sent_hist", "terms", "openers")
_SCALARS = ("messages", "chars", "words", "sentences", "questions",
            "imperatives", "emoji", "caps", "latin", "cyr")


def _take(counter: Dict[str, int], key: str, by: int) -> None:
    """Уменьшить счётчик, не уводя его ниже нуля.

    Ниже нуля он уйти может: агрегат подрезается (:func:`_prune`), и терм,
    выброшенный из него как редкий, в посессионном журнале остался. Обрезка
    по нулю смещает результат в единственную сторону, допустимую для
    забывания: остаться может меньше, чем было, но не больше.
    """
    left = counter.get(key, 0) - by
    if left > 0:
        counter[key] = left
    else:
        counter.pop(key, None)


def subtract(stats: Dict[str, Any], delta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Вычесть из агрегата стиля достаточные статистики одной сессии."""
    if not stats or not delta:
        return stats
    for side in ("owner", "baseline"):
        target = stats.get(side)
        source = delta.get(side)
        if not isinstance(target, dict) or not isinstance(source, dict):
            continue
        for key in _SCALARS:
            target[key] = max(0, int(target.get(key, 0)) - int(source.get(key, 0)))
        for key in _COUNTERS:
            bucket = target.get(key)
            if not isinstance(bucket, dict):
                continue
            for term, count in (source.get(key) or {}).items():
                _take(bucket, term, int(count))
        acts = target.get("acts")
        if isinstance(acts, dict):
            for act, surfaces in (source.get("acts") or {}).items():
                inner = acts.get(act)
                if not isinstance(inner, dict):
                    continue
                for surface, count in (surfaces or {}).items():
                    _take(inner, surface, int(count))
                if not inner:
                    acts.pop(act, None)
    return stats


def observed_messages(stats: Optional[Dict[str, Any]]) -> int:
    """Сколько сообщений владельца стоит за агрегатом."""
    if not stats:
        return 0
    return int((stats.get("owner") or {}).get("messages", 0))


# ---------------------------------------------------------------------------
# Чтение: из достаточных статистик — в отчёт
# ---------------------------------------------------------------------------

def _median_from_hist(hist: Dict[str, int], scale: int = 1) -> Optional[float]:
    total = sum(hist.values())
    if not total:
        return None
    target = total / 2.0
    seen = 0
    for bucket in sorted(hist, key=lambda k: int(k)):
        seen += hist[bucket]
        if seen >= target:
            return (int(bucket) + 0.5) * scale
    return None


def _percentile_from_hist(hist: Dict[str, int], q: float, scale: int = 1) -> Optional[float]:
    total = sum(hist.values())
    if not total:
        return None
    target = total * q
    seen = 0
    for bucket in sorted(hist, key=lambda k: int(k)):
        seen += hist[bucket]
        if seen >= target:
            return (int(bucket) + 0.5) * scale
    return None


def distinctive_terms(stats: Dict[str, Any], limit: int = 25) -> List[Tuple[str, float, int]]:
    """Лексика владельца на фоне ответов ассистента: ``(терм, z, сколько раз)``.

    Лог-шансы с информативным априором Дирихле. Априор — объединённый корпус,
    поэтому редкое слово не всплывает наверх только оттого, что редкое.
    """
    owner = stats.get("owner", {}).get("terms", {}) or {}
    base = stats.get("baseline", {}).get("terms", {}) or {}
    if not owner:
        return []
    n_owner = sum(owner.values())
    n_base = sum(base.values())
    if n_owner == 0:
        return []
    # Без фона сравнивать не с чем: возвращаем частотный верх, честно
    # пометив это нулевым z — вызывающий увидит, что отличительность не
    # посчитана, а не примет частоту за неё.
    if n_base == 0:
        top = sorted(owner.items(), key=lambda kv: -kv[1])[:limit]
        return [(term, 0.0, count) for term, count in top]

    prior_total = 0.0
    prior: Dict[str, float] = {}
    for term in set(owner) | set(base):
        prior[term] = float(owner.get(term, 0) + base.get(term, 0))
        prior_total += prior[term]
    alpha0 = 1000.0
    scored: List[Tuple[str, float, int]] = []
    for term, count in owner.items():
        a_w = alpha0 * (prior[term] / prior_total) if prior_total else 1.0
        a_w = max(a_w, 0.01)
        y_i = count + a_w
        y_j = base.get(term, 0) + a_w
        d_i = n_owner + alpha0 - y_i
        d_j = n_base + alpha0 - y_j
        if d_i <= 0 or d_j <= 0:
            continue
        delta = math.log(y_i / d_i) - math.log(y_j / d_j)
        variance = 1.0 / y_i + 1.0 / y_j
        scored.append((term, delta / math.sqrt(variance), count))
    scored.sort(key=lambda row: -row[1])
    return scored[:limit]


@dataclass
class StyleProfile:
    """Слой 1 целиком: измеренное, с числом сообщений за каждой цифрой."""

    origin = Origin.STYLE

    messages: int = 0
    established: bool = False
    median_chars: Optional[float] = None
    p90_chars: Optional[float] = None
    median_sentence_words: Optional[float] = None
    question_rate: Optional[float] = None
    imperative_rate: Optional[float] = None
    emoji_per_message: Optional[float] = None
    caps_per_message: Optional[float] = None
    latin_share: Optional[float] = None
    lexicon: List[Tuple[str, float, int]] = field(default_factory=list)
    acts: Dict[str, List[Tuple[str, int]]] = field(default_factory=dict)
    openers: List[Tuple[str, int]] = field(default_factory=list)
    baseline_words: int = 0

    def is_empty(self) -> bool:
        """Портрет считается пустым, пока из него нечего воспроизвести.

        Не «нет файла», а «нет ни одной черты, по которой можно писать за
        владельца»: ни установленной длины фразы, ни повторившейся
        формулировки речевого акта, ни отличительного слова.
        """
        return not (self.established or self.acts or self.lexicon)

    def as_text(self) -> str:
        if self.messages == 0:
            return "Стиль не измерен: ни одного сообщения владельца не наблюдалось."
        lines = [f"Измерено на {self.messages} сообщениях владельца."]
        if not self.established:
            lines.append(
                f"Предварительно: для устойчивых средних нужно "
                f"≥{MIN_MESSAGES_ESTABLISHED} сообщений."
            )
        if self.median_chars is not None:
            lines.append(
                f"Длина сообщения: медиана ~{self.median_chars:.0f} симв., "
                f"p90 ~{self.p90_chars:.0f}."
            )
        if self.median_sentence_words is not None:
            lines.append(f"Фраза: медиана ~{self.median_sentence_words:.0f} слов.")
        if self.question_rate is not None:
            lines.append(
                f"Вопросов среди фраз: {self.question_rate:.0%}; "
                f"повелительных: {self.imperative_rate:.0%}."
            )
        if self.latin_share:
            lines.append(
                f"Латиница внутри русской фразы: {self.latin_share:.0%} слов "
                "(термины не переводит)."
            )
        if self.emoji_per_message:
            lines.append(f"Эмодзи: {self.emoji_per_message:.2f} на сообщение.")
        for act, surfaces in self.acts.items():
            shown = ", ".join(f"«{s}» ×{n}" for s, n in surfaces[:4])
            lines.append(f"{act.capitalize()} выражает так: {shown}.")
        if self.lexicon:
            words = ", ".join(term for term, _z, _n in self.lexicon[:12])
            tail = "" if self.baseline_words else " (фон пуст — это просто частотный верх)"
            lines.append(f"Отличительная лексика{tail}: {words}.")
        if self.openers:
            starts = ", ".join(f"«{w}» ×{n}" for w, n in self.openers[:5])
            lines.append(f"Начинает сообщение с: {starts}.")
        return "\n".join(lines)


def profile(stats: Optional[Dict[str, Any]]) -> StyleProfile:
    """Собрать отчёт слоя 1 из достаточных статистик."""
    if not stats:
        return StyleProfile()
    owner = stats.get("owner") or {}
    messages = int(owner.get("messages", 0))
    if messages == 0:
        return StyleProfile()
    phrases = max(int(owner.get("sentences", 0)), 1)
    acts: Dict[str, List[Tuple[str, int]]] = {}
    for act, bucket in (owner.get("acts") or {}).items():
        kept = [(s, n) for s, n in sorted(bucket.items(), key=lambda kv: -kv[1])
                if n >= MIN_ACT_OCCURRENCES]
        if kept:
            acts[act] = kept
    openers = [
        (word, n)
        for word, n in sorted((owner.get("openers") or {}).items(), key=lambda kv: -kv[1])
        if n >= MIN_ACT_OCCURRENCES
    ]
    latin = int(owner.get("latin", 0))
    cyr = int(owner.get("cyr", 0))
    return StyleProfile(
        messages=messages,
        established=messages >= MIN_MESSAGES_ESTABLISHED,
        median_chars=_median_from_hist(owner.get("len_hist") or {}, scale=40),
        p90_chars=_percentile_from_hist(owner.get("len_hist") or {}, 0.9, scale=40),
        median_sentence_words=_median_from_hist(owner.get("sent_hist") or {}),
        question_rate=int(owner.get("questions", 0)) / phrases,
        imperative_rate=int(owner.get("imperatives", 0)) / phrases,
        emoji_per_message=int(owner.get("emoji", 0)) / messages,
        caps_per_message=int(owner.get("caps", 0)) / messages,
        latin_share=(latin / (latin + cyr)) if (latin + cyr) else 0.0,
        lexicon=distinctive_terms(stats),
        acts=acts,
        openers=openers,
        baseline_words=int((stats.get("baseline") or {}).get("words", 0)),
    )
