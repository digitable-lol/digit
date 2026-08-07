"""Слой 2 — РЕШЕНИЯ: факты с проверяемым происхождением.

Что отличает решение от сказанного мимоходом
--------------------------------------------
Сообщение владельца — поток намерений, вопросов, реплик и уточнений. Записать
всё — получить журнал переписки, а не портрет; записывать по команде
«запомни» — переложить работу на человека, который её не сделает. Значит,
нужен признак, который отличает «решил» от «сказал», и он должен быть
проверяемым, а не «моделью на глаз».

Фраза попадает в журнал, если в ней есть **и то, и другое**:

1. **маркер обязательства** — «делаем», «переходим на», «не будем»,
   «с этого момента», ``let's go with``, ``never``. Не любой глагол, а тот,
   которым распоряжаются;
2. **конкретный предмет** — путь, идентификатор, версия, имя в обратных
   кавычках или просто содержательное слово. «Давай сделаем красиво» —
   маркер без предмета, и это мимоходом.

И не выполняется ни одно из вето: фраза не вопрос («перейдём на uv?» — это
не решение, а вопрос) и не гипотеза («если не заработает, попробуем uv» —
условие, а не выбор).

Чем подтверждается
------------------
У записи два уровня свидетельства, и разница между ними видна в отчёте:

``сказано``  — фраза есть, действий за ней в этом ходу не последовало;
``сделано``  — в том же ходу агент вызывал инструменты, и их имена записаны.

Это и есть разница между «он так сказал» и «он так сделал». Второе весит
больше, и оно же снимает главный класс ложных срабатываний: пересказ чужого
решения инструментов за собой не тянет.

Почему цитата дословная
-----------------------
Запись хранит фразу владельца как есть. Не резюме, не «суть» — суть сочиняет
модель, и тогда запись перестаёт отличаться от догадки. Дословная цитата +
сессия + время = то, что можно показать третьему лицу и что он может
проверить, не веря на слово ни агенту, ни модели.

Отмена, а не правка
-------------------
Позднее решение о том же предмете не стирает прежнее, а помечает его
отменённым отдельной строкой журнала. История выбора — часть портрета: «мы
уходили с X на Y, потом вернулись» знает только тот, кто хранит оба хода.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from digit_cli.kb.lexical import STOPWORDS, stem

from . import store
from .provenance import Origin
# Один и тот же вычет «это не речь» на оба слоя: если стиль перестанет
# считать разметку, а решения продолжат из неё вычитываться, портрет начнёт
# ссылаться на фразы, которых в измеренной речи нет.
from .style import _FENCE, sentences

MAX_QUOTE_CHARS = 400

# --- маркеры обязательства -------------------------------------------------
_DO_MARKERS: List[Tuple[str, re.Pattern]] = [
    ("решение", re.compile(r"\b(реши(?:л|ли|ла|ло)|решено|договорились)\b", re.I)),
    ("выбор", re.compile(
        r"\b(дела(?:ем|ю)|сдела(?:ем|ю)|буд(?:ем|у)|бер[её]м|использу(?:ем|ю)|"
        r"переход(?:им|ю)|переезжа(?:ем|ю)|ставим|включа(?:ем|ю)|оставля(?:ем|ю)|"
        r"фиксиру(?:ем|ю)|выбира(?:ем|ю)|остановимся на|остановились на|"
        r"перевод(?:им|ишь) на|меняем на)\b", re.I)),
    ("правило", re.compile(
        r"(^|\s)(с этого момента|отныне|впредь|по умолчанию|всегда|"
        r"каждый раз|правило\s*[:—-])", re.I)),
    ("предпочтение", re.compile(r"\b(предпочита(?:ю|ем))\b", re.I)),
    ("указание", re.compile(r"^\s*дава(?:й|йте)\s+\S", re.I)),
    ("choice", re.compile(
        r"\b(let'?s (?:use|go with|do|switch)|we'?ll use|we will use|"
        r"decided to|going with|switch(?:ing)? to|default to|from now on|"
        r"always)\b", re.I)),
]

# «хватит» в запретах нет намеренно: по-русски это и «прекрати», и
# «достаточно». Фраза «6 ГБ хватит» записывалась как запрет — маркер, который
# переворачивает полярность записи, обходится дороже пропущенного решения.
_DONT_MARKERS: List[Tuple[str, re.Pattern]] = [
    ("запрет", re.compile(
        r"\b(не буд(?:ем|у)|не дела(?:ем|й|йте)|не использу(?:ем|й)|"
        r"не бер[её]м|не ставим|не трога(?:ем|й)|не нужно|не надо|"
        r"отказыва(?:емся|юсь)|убира(?:ем|й)|выпиливаем|запрещено|нельзя|"
        r"никогда не|ни в коем случае|перестань)\b", re.I)),
    ("ban", re.compile(
        r"\b(don'?t use|do not use|never|stop using|no longer|avoid|"
        r"drop the|remove the)\b", re.I)),
]

#: «вместо», «а не», «лучше чем» — противопоставление, а не обязательство.
#: Стандалон-маркером они были ошибкой: «измеряй, а не предполагай» и «баг
#: архитектуры, а не статистика» — это риторика, которой держится любой
#: объясняющий текст, и на живой переписке она давала больше «решений», чем
#: было сообщений. Осталась их настоящая роль: усиливать соседний маркер
#: обязательства («переходим на uv ВМЕСТО poetry») и повышать вес записи.
_CONTRAST = re.compile(r"\b(вместо|а не|лучше чем|не\s+\w+,\s*а)\b", re.I)

#: Сколько решений максимум берётся из одного сообщения. Длинное техзадание
#: содержит десятки указаний, но решений в нём единицы: остальное —
#: подробности их исполнения. Без потолка портрет заполняется абзацами
#: одного вечера и перестаёт показывать хоть что-то.
MAX_PER_MESSAGE = 3

# --- вето ------------------------------------------------------------------
_HEDGES = re.compile(
    r"\b(если|наверное|наверно|может быть|возможно|вроде|кажется|похоже|"
    r"попробу(?:й|ем)|а что если|хз|не уверен\w*|или нет|или лучше|"
    r"я думаю|думаю,|было бы неплохо|было бы не плохо|хотелось бы|емнип|"
    r"maybe|perhaps|what if|not sure|i think we might|or should we)\b",
    re.I,
)
#: Фраза, начинающаяся с указательного местоимения, называет предмет только
#: через предыдущую фразу. Записать её как решение значит положить в журнал
#: цитату, которую нельзя прочесть отдельно, — а вся ценность записи в том,
#: что её можно показать вне контекста.
_ANAPHORA = re.compile(
    r"^\s*[«\"'*]*\s*(именно\s+)?(это|то|оно|такое|такой|таких|здесь|тут|там)\b",
    re.I,
)
#: Строка структуры данных, а не речь: ``"ключ": "значение"``. В брифах
#: владельца такие строки — примеры внутри JSON, и маркер в них принадлежит
#: примеру, а не автору.
_STRUCT_LINE = re.compile(r"^\s*[\"'][\w_]+[\"']\s*:")
#: Фраза целиком в кавычках — цитата, а не высказывание владельца. В его
#: собственных брифах так оформлены чужие реплики и разбираемые примеры.
_FULLY_QUOTED = re.compile(r"^\s*[«\"'][^«»]{5,}[»\"']\s*[.!]?\s*$")
#: Два и более длинных закавыченных куска в одной фразе — перечисление
#: примеров («…, „…“, „…“»), а не одно решение. Так выглядят наборы тестовых
#: вопросов внутри техзадания: маркер в них принадлежит примеру.
_QUOTED_SPAN = re.compile(r"[«\"„][^«»\"„“]{20,}[»\"“]")
#: Пересказ чужого решения. «Он сказал, что переходим на uv» — не выбор
#: владельца, а цитата, и в портрет владельца попадать не должна.
_REPORTED = re.compile(
    r"\b(он|она|они|заказчик|клиент|коллег\w+|команда|говорит|сказал\w*|"
    r"пишет|считает|предлагает|требует)\b\s*,?\s*(что|чтобы)\b",
    re.I,
)

# --- предметы --------------------------------------------------------------
_BACKTICKED = re.compile(r"`([^`]{1,80})`")
_QUOTED = re.compile(r"[«\"']([^«»\"']{2,80})[»\"']")
_PATHISH = re.compile(r"(?:^|\s)((?:~|\.{1,2})?/[\w./\-]{2,}|[\w\-]+/[\w./\-]{2,})")
_DOTTED = re.compile(r"\b([A-Za-z_][\w]*(?:[._][\w]+)+)\b")
_VERSION = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b")
_URL = re.compile(r"https?://\S+")
_ALLCAPS = re.compile(r"\b([A-ZА-ЯЁ]{2,}[A-ZА-ЯЁ0-9_]*)\b")
_WORD = re.compile(r"[0-9A-Za-zА-Яа-яЁё][0-9A-Za-zА-Яа-яЁё\-]*")
#: Признак того, что фраза называет вещь, а не рассуждает о ней: путь, имя в
#: обратных кавычках, точечный идентификатор, версия, ссылка.
_CONCRETE = re.compile(
    r"`[^`]{1,80}`|https?://\S|(?:^|\s)(?:~|\.{1,2})?/[\w./\-]{2,}|"
    r"\b[A-Za-z_][\w]*[._][\w]+\b|\bv?\d+\.\d+\b"
)

#: Слова-маркеры сами предметом не считаются: иначе «не будем» вечно
#: указывало бы само на себя и склеивало бы несвязанные решения.
_NON_OBJECTS = frozenset("""
делаем сделаем будем берем берём используем переходим ставим включаем оставляем
фиксируем выбираем решил решили решено договорились вместо лучше предпочитаю
давай давайте всегда никогда нужно надо нельзя запрещено убираем отказываемся
момента отныне впредь умолчанию правило каждый раз этого нибудь
use using switch always never stop drop remove decided going default from
""".split())

#: Инструменты, вызов которых означает, что в ходе что-то изменилось.
#: Чтение файла решением не подтверждает — им подтверждает запись.
#: Только однозначные изменители. ``bash``/``git``/``python`` сюда не входят
#: намеренно: ими и читают, и правят, и на агентских транскриптах они делают
#: свидетельство «сделано» истинным почти всегда — то есть бесполезным.
#: Свидетельство должно означать «после этих слов что-то изменилось», а не
#: «агент чем-то занимался».
_WRITE_TOOLS = (
    "write", "patch", "edit", "apply", "create", "delete", "move",
    "notebook", "multiedit", "str_replace",
)

#: Вес маркера при отборе. Запрет наверху не случайно: «не будем X» —
#: самое дорогое и самое теряемое знание о владельце, потому что о нём
#: обычно говорят один раз и больше не вспоминают.
_MARKER_WEIGHT = {
    "запрет": 5.0, "ban": 5.0,
    "решение": 4.0,
    "правило": 3.5,
    "предпочтение": 3.0,
    "выбор": 2.0, "choice": 2.0,
    "указание": 1.0,
}

_MD_NOISE = re.compile(r"^\s*(#{1,6}\s|>\s|\|)")


@dataclass
class DecisionRecord:
    """Слой 2: что решено, дословно, с происхождением."""

    origin = Origin.RECORD

    id: str
    quote: str
    marker: str
    polarity: str            # "do" | "dont"
    objects: List[str]
    evidence: str            # "сказано" | "сделано"
    evidence_tools: List[str] = field(default_factory=list)
    session_id: str = ""
    ts: int = 0
    source: str = ""
    superseded_by: Optional[str] = None

    def when(self) -> str:
        if not self.ts:
            return "время неизвестно"
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.ts))

    def citation(self) -> str:
        """Ссылка на источник — то, чем эту запись можно проверить."""
        parts = [self.when()]
        if self.session_id:
            parts.append(f"сессия {self.session_id[:12]}")
        if self.source:
            parts.append(self.source)
        parts.append(self.evidence)
        if self.evidence_tools:
            parts.append("→ " + ", ".join(self.evidence_tools[:4]))
        return "; ".join(parts)

    def as_text(self) -> str:
        head = "не делать" if self.polarity == "dont" else "делать"
        line = f"[{head}] «{self.quote}»\n    ({self.citation()})"
        if self.superseded_by:
            line += f"\n    ОТМЕНЕНО более поздним решением {self.superseded_by[:8]}"
        return line

    def to_row(self) -> Dict[str, Any]:
        return {
            "t": "d",
            "id": self.id,
            "quote": self.quote,
            "marker": self.marker,
            "polarity": self.polarity,
            "objects": self.objects,
            "evidence": self.evidence,
            "tools": self.evidence_tools,
            "session": self.session_id,
            "ts": self.ts,
            "source": self.source,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "DecisionRecord":
        return cls(
            id=str(row.get("id", "")),
            quote=str(row.get("quote", "")),
            marker=str(row.get("marker", "")),
            polarity=str(row.get("polarity", "do")),
            objects=list(row.get("objects") or []),
            evidence=str(row.get("evidence", "сказано")),
            evidence_tools=list(row.get("tools") or []),
            session_id=str(row.get("session", "")),
            ts=int(row.get("ts", 0) or 0),
            source=str(row.get("source", "")),
        )


# ---------------------------------------------------------------------------
# Извлечение
# ---------------------------------------------------------------------------

def objects_of(phrase: str) -> List[str]:
    """Предметы фразы: сначала конкретика, потом содержательные слова.

    Порядок не косметический: первый предмет — ключ, по которому позднее
    решение отменяет прежнее, и путь ``digit_cli/main.py`` в этой роли
    несравнимо надёжнее слова «файл».
    """
    found: List[str] = []

    def add(value: str) -> None:
        value = value.strip().strip(".,;:")
        if not value or value.lower() in _NON_OBJECTS:
            return
        # Кусок уже найденного предметом не считается: `digit_cli/main.py`
        # уже назвал и `digit_cli`, и `main.py`, а лишние предметы разводят
        # ключ отмены — решение о файле переставало бы отменять прежнее
        # решение о том же файле.
        low = value.lower()
        for existing in found:
            if low in existing.lower():
                return
        found[:] = [e for e in found if e.lower() not in low]
        found.append(value)

    for pattern in (_URL, _BACKTICKED, _QUOTED, _PATHISH, _DOTTED, _VERSION):
        for match in pattern.finditer(phrase):
            add(match.group(1) if match.groups() else match.group(0))
    for match in _ALLCAPS.finditer(phrase):
        add(match.group(1))
    if len(found) < 3:
        for word in _WORD.findall(phrase):
            low = word.lower()
            if low in STOPWORDS or low in _NON_OBJECTS:
                continue
            # Латиницей пишут имена инструментов, и они коротки: uv, go, ci,
            # k8s. Порог в 4 символа, разумный для русского слова, выбросил бы
            # ровно то, ради чего решение и записывают.
            ascii_name = low.isascii() and any(c.isalpha() for c in low)
            if len(low) < (2 if ascii_name else 4):
                continue
            # Инфинитив — действие, а не предмет. «Не будем трогать X»
            # решается про X; «трогать» в роли предмета склеило бы это
            # решение с любым другим, где есть тот же глагол.
            if not ascii_name and (low.endswith("ться") or low.endswith("ть")):
                continue
            add(word)
            if len(found) >= 3:
                break
    return found[:6]


def _match_marker(phrase: str) -> Optional[Tuple[str, str]]:
    """``(polarity, marker)`` первого сработавшего маркера, иначе ``None``.

    Запреты проверяются первыми: «не будем использовать X» содержит и
    «не будем», и «использу…», и приоритет положительного маркера
    перевернул бы смысл записи на противоположный.
    """
    for name, pattern in _DONT_MARKERS:
        if pattern.search(phrase):
            return "dont", name
    for name, pattern in _DO_MARKERS:
        if pattern.search(phrase):
            return "do", name
    return None


def extract(
    text: str,
    *,
    session_id: str = "",
    ts: Optional[int] = None,
    source: str = "",
    tools_used: Optional[Sequence[str]] = None,
) -> List[DecisionRecord]:
    """Достать решения из одного сообщения владельца. Без модели, детерминированно."""
    if not text:
        return []
    now = int(ts if ts is not None else time.time())
    wrote: List[str] = []
    for tool in tools_used or []:
        if tool and tool not in wrote and any(w in tool.lower() for w in _WRITE_TOOLS):
            wrote.append(tool)
    evidence = "сделано" if wrote else "сказано"

    # Код, вставленный в сообщение, — не речь владельца: в нём есть и
    # «не», и «if», и имена файлов, но нет ни одного его слова. Мерить по
    # нему стиль и вычитывать из него решения одинаково бессмысленно.
    body = _FENCE.sub(" ", text)

    scored: List[Tuple[float, int, DecisionRecord]] = []
    seen: set = set()
    for position, phrase in enumerate(sentences(body)):
        stripped = phrase.strip()
        if stripped.endswith("?") or _MD_NOISE.match(stripped):
            continue
        if _ANAPHORA.match(stripped) or _STRUCT_LINE.match(stripped):
            continue
        if _FULLY_QUOTED.match(stripped) or len(_QUOTED_SPAN.findall(stripped)) >= 2:
            continue
        if _HEDGES.search(stripped) or _REPORTED.search(stripped):
            continue
        matched = _match_marker(stripped)
        if not matched:
            continue
        polarity, marker = matched
        objects = objects_of(stripped)
        if not objects:
            continue
        quote = stripped[:MAX_QUOTE_CHARS]
        key = quote.lower()
        if key in seen:
            continue
        seen.add(key)
        digest = hashlib.sha1(
            f"{session_id}|{now}|{quote}".encode("utf-8")
        ).hexdigest()[:16]
        weight = _MARKER_WEIGHT.get(marker, 1.0)
        if _CONTRAST.search(stripped):
            weight += 1.0
        if _CONCRETE.search(stripped):
            weight += 1.5
        scored.append((weight, position, DecisionRecord(
            id=digest,
            quote=quote,
            marker=marker,
            polarity=polarity,
            objects=objects,
            evidence=evidence,
            evidence_tools=wrote[:6],
            session_id=session_id,
            ts=now,
            source=source,
        )))
    scored.sort(key=lambda row: (-row[0], row[1]))
    kept = scored[:MAX_PER_MESSAGE]
    # Порядок в журнале — порядок в сообщении, а не порядок веса: журнал
    # читают как хронологию, и переставленные фразы одного сообщения
    # читались бы как переставленные во времени.
    kept.sort(key=lambda row: row[1])
    return [record for _weight, _position, record in kept]


def tools_of_turn(messages: Optional[Sequence[Dict[str, Any]]]) -> List[str]:
    """Имена инструментов, вызванных ПОСЛЕ последнего сообщения пользователя.

    Свидетельство «сделано» должно относиться к этому ходу, а не ко всей
    сессии, поэтому список читается с хвоста до последней реплики
    пользователя.
    """
    if not messages:
        return []
    names: List[str] = []
    for message in reversed(list(messages)):
        if message.get("role") == "user":
            break
        for call in message.get("tool_calls") or []:
            name = (call.get("function") or {}).get("name") or call.get("name")
            if name and name not in names:
                names.append(name)
    return list(reversed(names))


# ---------------------------------------------------------------------------
# Журнал
# ---------------------------------------------------------------------------

def load() -> List[DecisionRecord]:
    """Прочитать журнал и разложить отмены по записям."""
    records: List[DecisionRecord] = []
    index: Dict[str, DecisionRecord] = {}
    for row in store.iter_lines(store.DECISIONS_FILE):
        kind = row.get("t")
        if kind == "d":
            record = DecisionRecord.from_row(row)
            records.append(record)
            index[record.id] = record
        elif kind == "s":
            target = index.get(str(row.get("id", "")))
            if target is not None:
                target.superseded_by = str(row.get("by", ""))
    return records


def append(records: Iterable[DecisionRecord]) -> int:
    """Записать новые решения, отметив отменённые ими прежние.

    Отменяется прежнее решение, чей главный предмет назван в новом — где
    угодно среди его предметов, не обязательно первым. «Переходим на uv, а
    не poetry» отменяет «переходим на poetry» именно потому, что называет
    poetry вторым: требовать совпадения главных предметов значило бы не
    замечать самый частый способ передумать — назвать замену и отвергаемое
    в одной фразе. Сравнение по основе, а не по строке: «uv» и «uv-ом» —
    одна вещь.
    """
    new = list(records)
    if not new:
        return 0
    existing = load()
    live = [r for r in existing if not r.superseded_by]
    for record in new:
        store.append_line(store.DECISIONS_FILE, record.to_row())
        mentioned = _all_keys(record)
        if mentioned:
            for old in live:
                if old.id == record.id or old.superseded_by:
                    continue
                key = _primary_key(old)
                if key and key in mentioned:
                    old.superseded_by = record.id
                    store.append_line(store.DECISIONS_FILE, {
                        "t": "s", "id": old.id, "by": record.id, "ts": record.ts,
                    })
        live.append(record)
    return len(new)


def _primary_key(record: DecisionRecord) -> str:
    if not record.objects:
        return ""
    return stem(record.objects[0].lower())


def _all_keys(record: DecisionRecord) -> set:
    return {stem(obj.lower()) for obj in record.objects}


def search(records: Sequence[DecisionRecord], query: str, limit: int = 8) -> List[DecisionRecord]:
    """Найти решения, относящиеся к запросу. Лексически, по основам слов.

    Тот же приём, что у поиска памяти: стемминг с совпадением по префиксу.
    Префикс здесь не послабление, а необходимость: «прокси» и
    «прокси-сервером» — один токен каждый, и точное равенство основ развело
    бы их в разные вещи. Синонимов такой поиск не понимает — и это сказано
    вслух, чтобы промах не выглядел как «решений не было».
    """
    terms = {
        stem(word.lower())
        for word in _WORD.findall(query or "")
        if word.lower() not in STOPWORDS and len(word) >= 3
    }
    if not terms:
        return []
    scored: List[Tuple[float, DecisionRecord]] = []
    for record in records:
        haystack = {stem(w.lower()) for w in _WORD.findall(record.quote)}
        haystack |= {stem(o.lower()) for o in record.objects}
        overlap = sum(
            1 for term in terms
            if any(h.startswith(term) or term.startswith(h) for h in haystack)
        )
        if not overlap:
            continue
        score = overlap
        if record.evidence == "сделано":
            score += 0.5
        if record.superseded_by:
            score -= 1.5
        scored.append((score, record))
    scored.sort(key=lambda row: (-row[0], -row[1].ts))
    return [record for _score, record in scored[:limit]]
