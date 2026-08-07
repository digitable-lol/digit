"""Лексический слой поиска: токены, стемминг, запрос к FTS5. Без модели.

Вынесено из :mod:`digit_cli.kb.search`, потому что у этого кода стало два
потребителя с очень разными требованиями:

* ``digit kb`` — гибридный поиск по внешним корпусам, где лексический канал
  идёт рядом с плотным и подстраховывает его, когда эмбеддер недоступен;
* :mod:`agent.memory_recall` — поиск по заметкам памяти, который обязан
  работать **офлайн всегда**: он выполняется на каждом ходу агента, и
  зависимость от удалённого эмбеддера там не «нежелательна», а недопустима.

Второму нельзя импортировать ``digit_cli.kb.search`` целиком: тот на уровне
модуля тянет ``digit_cli.kb.embed`` (клиент эмбеддера с allowlist хостов и
разбором конфига) — это ~48 мс и сетевой клиент ради функции ``stem``. Здесь
нет ничего, кроме ``re``, так что импорт стоит доли миллисекунды.

Стеммер намеренно грубый: он производит **префикс**, который FTS5 расширяет
через ``*``. Пережать суффикс — потерять точность, недожать — потерять
полноту; для канала, который существует ради полноты, второе хуже.
"""

from __future__ import annotations

import re
from typing import List, Sequence, Tuple

MIN_STEM_LEN = 4
"""Never truncate a token below this; shorter prefixes match half the corpus."""

# Function words carry no retrieval signal but would otherwise dominate the
# lexical-coverage statistic that drives abstention ("что такое X" must be
# judged on X alone).
STOPWORDS = frozenset("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы
по только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг
ли если уже или ни быть был него до вас нибудь опять уж вам ведь там потом
себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам
чтоб без будто чего раз тоже себе под будет ж тогда кто этот того потому этого
какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда зачем
всех никогда можно при наконец два об другой хоть после над больше тот через
эти нас про всего них какая много разве три эту моя впрочем хорошо свою этой
перед иногда лучше чуть том нельзя такой им более всегда конечно всю между
это чем какие каких каком какому чем зачем почему отличие отличается разница
такое такие эта эти этих этими
the a an of to in is are was were be been and or for with on at by from as
that this these those it its what which how why when who whom whose do does
did not no nor but if then than so such can could should would will shall
""".split())


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[0-9a-zA-Zа-яёА-ЯЁ_.\-]+", text.lower())


# Longest-first so "ами" is tried before "и".
_SUFFIXES: Tuple[str, ...] = tuple(sorted(
    (
        # nouns / adjectives
        "ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими", "ах", "ях",
        "ам", "ям", "ов", "ев", "ей", "ой", "ый", "ий", "ая", "яя", "ое",
        "ее", "ые", "ие", "ью", "ия", "ии", "ом", "ем", "ем", "ы", "и", "а",
        "я", "о", "е", "у", "ю", "й", "ь",
        # verbs
        "ться", "тся", "ать", "ять", "ить", "еть", "уть", "ешь", "ишь",
        "ет", "ит", "ут", "ют", "ат", "ят", "ла", "ло", "ли", "л",
        # English plural / gerund
        "ing", "es", "s",
    ),
    key=len, reverse=True,
))


def stem(token: str) -> str:
    """Light suffix-stripping stemmer (Russian + naive English plurals).

    Deliberately crude. It only has to produce a *prefix* that FTS5 can
    expand with ``*``; over-stemming costs precision in the lexical channel,
    which RRF then dilutes, whereas under-stemming loses the recall this
    channel exists to provide.
    """
    t = token.lower().strip("._-")
    if len(t) <= MIN_STEM_LEN:
        return t
    for suf in _SUFFIXES:
        if t.endswith(suf) and len(t) - len(suf) >= MIN_STEM_LEN:
            return t[: -len(suf)]
    return t


def content_terms(query: str) -> List[str]:
    """Query tokens that carry retrieval signal, de-duplicated, order kept."""
    out: List[str] = []
    seen = set()
    for tok in _tokenize(query):
        if tok in STOPWORDS or len(tok) < 2:
            continue
        if tok.isdigit() and len(tok) < 3:
            continue
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def build_fts_query(terms: Sequence[str]) -> str:
    """OR of prefix-matched stems: ``"горутин"* OR "канал"*``."""
    parts = []
    for term in terms:
        s = stem(term).replace('"', "")
        if s:
            parts.append(f'"{s}"*')
    return " OR ".join(parts)
