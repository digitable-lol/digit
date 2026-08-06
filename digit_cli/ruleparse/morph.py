#!/usr/bin/env python3
"""Russian morphology layer. No neural network anywhere in this file.

pymorphy3 (MIT code) + OpenCorpora dictionary (CC BY-SA) give lemma, part of
speech and case for every token. That is the whole "understanding" budget of
this system: a finite-state dictionary lookup, deterministic, offline, and
auditable word by word.

What morphology buys us, concretely:
  * «строки», «строку», «строкой» all collapse to «строка», so the catalog
    lexicon can be written once in the nominative.
  * case + preposition identifies argument roles: «из десятичной» (gent after
    «из») is a source, «в шестнадцатеричную» (accs after «в») is a target.
What it does NOT buy us: anything about word MEANING. `хеш` and `подпись` are
different lemmas and morphology has no opinion on whether they are related.
That gap is what the hand-written rule layer in rules.py has to pay for.
"""
from __future__ import annotations

import functools
import re
from dataclasses import dataclass

# ПОЧЕМУ ленивый анализатор, а не `import pymorphy3` на уровне модуля.
#
# В эксперименте (digit-ml/ruleparse) словарь был всегда: там его ставили
# руками перед прогоном. В Digit pymorphy3 — необязательная зависимость
# (см. `tools/lazy_deps.py`, ключ `ruleparse`), потому что словарь OpenCorpora
# весит 16 МБ и нужен только русскоязычному каскаду правил. Импорт на уровне
# модуля означал бы, что `import digit_cli.ruleparse` падает у каждого, кто
# словарь не ставил, — а каскад обязан УСТУПАТЬ модели, а не ломать сессию.
#
# Плюс цена: MorphAnalyzer() читает словарь ~0.2 с. Платить её при импорте
# пакета (то есть на каждом старте CLI) нельзя; платим при первом русском
# запросе, и один раз на процесс.
_MORPH = None


def analyzer():
    """pymorphy3.MorphAnalyzer, созданный при первом обращении.

    Бросает ImportError, если pymorphy3 не установлен. Ловить его — работа
    `cascade.py`: там это означает «каскад молчит», а не «сессия сломалась».
    """
    global _MORPH
    if _MORPH is None:
        import pymorphy3

        _MORPH = pymorphy3.MorphAnalyzer()
    return _MORPH


def available() -> bool:
    """True, если морфология поднимется. Не поднимает её."""
    import importlib.util

    return importlib.util.find_spec("pymorphy3") is not None

# Token = a run of Cyrillic/Latin letters, or digits, kept with its span so the
# argument extractor can slice the ORIGINAL string (arguments must be verbatim).
TOKEN_RE = re.compile(r"[А-Яа-яЁёA-Za-z]+(?:[-'][А-Яа-яЁёA-Za-z]+)*|\d+(?:[.,]\d+)*")

# Function words. Kept out of the lexical match, but prepositions are still
# needed by the case frames, so they are tagged rather than dropped.
PREPOSITIONS = {
    "в", "во", "из", "с", "со", "на", "по", "до", "от", "для", "к", "у", "о",
    "об", "при", "под", "над", "за", "через", "между", "про",
}
STOPWORDS = PREPOSITIONS | {
    "и", "а", "но", "или", "же", "ли", "не", "ни", "бы", "что", "как", "это",
    "этот", "эта", "эти", "тот", "та", "те", "я", "мне", "меня", "мой", "моя",
    "мои", "ты", "вы", "он", "она", "они", "надо", "нужно", "нужен", "нужна",
    "нужны", "хочу", "хочется", "пожалуйста", "плз", "срочно", "тут", "здесь",
    "там", "вот", "ещё", "еще", "уже", "только", "очень", "самый", "весь",
    "все", "всё", "быть", "мочь", "который", "чтобы", "если", "так", "также",
    "потом", "затем", "теперь", "сам", "свой", "их", "его", "её", "ее", "нам",
    "чем", "чём", "то", "да", "нет", "один", "два",
}


def normalize(text: str) -> str:
    """ё → е and lowercase. Every downstream lookup uses this form."""
    return text.lower().replace("ё", "е")


@dataclass(frozen=True)
class Token:
    text: str          # surface form as written, unmodified
    norm: str          # lowercased, ё→е
    lemma: str         # dictionary form
    pos: str | None    # NOUN / VERB / ADJF / ...
    case: str | None   # nomn / gent / datv / accs / ablt / loct
    start: int
    end: int
    is_latin: bool
    is_digit: bool

    @property
    def is_stop(self) -> bool:
        return self.norm in STOPWORDS

    @property
    def is_prep(self) -> bool:
        return self.norm in PREPOSITIONS


@functools.lru_cache(maxsize=200_000)
def _analyse(word: str) -> tuple[str, str | None, str | None]:
    """(lemma, POS, case) for one normalised word. Cached: the eval reuses words."""
    if not re.search(r"[а-я]", word):
        # Latin or digits: pymorphy has nothing to say, the form IS the lemma.
        return word, None, None
    p = analyzer().parse(word)[0]
    return (
        p.normal_form.replace("ё", "е"),
        str(p.tag.POS) if p.tag.POS else None,
        str(p.tag.case) if p.tag.case else None,
    )


def tokenize(text: str) -> list[Token]:
    out: list[Token] = []
    for m in TOKEN_RE.finditer(text):
        surface = m.group(0)
        n = normalize(surface)
        lemma, pos, case = _analyse(n)
        out.append(Token(
            text=surface, norm=n, lemma=lemma, pos=pos, case=case,
            start=m.start(), end=m.end(),
            is_latin=bool(re.fullmatch(r"[a-z][a-z\-']*", n)),
            is_digit=n.isdigit(),
        ))
    return out


def lemmas(text: str, drop_stop: bool = True) -> list[str]:
    return [t.lemma for t in tokenize(text) if not (drop_stop and t.is_stop)]


def lemma_set(text: str, drop_stop: bool = True) -> set[str]:
    """Lemmas plus the parts of hyphenated compounds.

    «юникод-коды» lemmatises as one unit, so «юникод» would never match the
    catalog. Splitting on the hyphen costs nothing and recovers the head.
    Single characters are dropped: the catalog lists «X, I, V, L, C, D, M» as
    keywords of the Roman numeral tool, and letting one stray Latin letter
    score a match made it the top candidate for unrelated queries.
    """
    out: set[str] = set()
    for t in tokenize(text):
        if drop_stop and t.is_stop:
            continue
        out.add(t.lemma)
        if "-" in t.norm:
            out.update(p for p in t.norm.split("-") if len(p) > 1)
            out.add(t.norm.replace("-", ""))
    return {w for w in out if len(w) > 1}


# ---------------------------------------------------------------------------
# Case frames: the one place where morphological CASE, not just the lemma,
# changes the parse. Used to tell a conversion source from its target.
# ---------------------------------------------------------------------------
SOURCE_PREPS = {"из", "с", "со", "от"}      # + gent
TARGET_PREPS = {"в", "во", "на", "под", "к", "до"}  # + accs/loct/datv


def prep_frames(tokens: list[Token]) -> list[tuple[str, str, Token]]:
    """[(role, preposition, head token)] for every «prep + nominal» group.

    role is "source" or "target". The head is the first nominal after the
    preposition, skipping adjectives is NOT done - the adjective usually IS the
    format word («в шестнадцатеричную [систему]»), so the first nominal wins.
    """
    frames: list[tuple[str, str, Token]] = []
    for i, tok in enumerate(tokens):
        if not tok.is_prep:
            continue
        role = "source" if tok.norm in SOURCE_PREPS else \
               "target" if tok.norm in TARGET_PREPS else None
        if role is None:
            continue
        for nxt in tokens[i + 1:i + 4]:
            if nxt.pos in {"NOUN", "ADJF", "ADJS", "PRTF"} or nxt.is_latin:
                frames.append((role, tok.norm, nxt))
                break
    return frames
