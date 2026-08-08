"""Wikilinks, tags and sectors over the freeform memory store.

``MEMORY.md`` / ``USER.md`` are a flat list of ``§``-delimited prose entries:
no ids, no declared relations, no index. That is enough for a scratchpad and
too little for a graph — a note that says "see [[Vector search]]" is, to the
assembler, the same opaque blob as a note about coffee.

This module is the missing layer, and it deliberately invents no syntax. It
reads what people already type into notes — ``[[wikilink]]`` and ``#tag`` —
and turns it into structure:

- **links**: a wikilink resolved to the note whose first line it names;
- **backlinks**: the same edge seen from the other end, which is what makes a
  link *traversable* rather than merely written down. Without them a note can
  only ever answer "where do I point?", never "who points at me?";
- **sectors**: the area a note belongs to, so a graph can be read by area
  instead of as one undifferentiated cloud of dots.

Pure and stdlib-only: it takes cards, it returns structure. Nothing here reads
or writes the memory files — that stays with the caller that owns them.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# Заметки без тега и без связей всё равно должны иметь сектор: «непонятно куда»
# — это честный ответ, а молчаливое исчезновение из разбивки — нет.
SECTOR_UNSORTED = "unsorted"

# ``[[Цель]]`` и ``[[Цель|как показать]]``. Внутрь скобок не пускаем сами
# скобки, иначе одна незакрытая пара съедала бы весь остаток заметки.
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|]{1,200})(?:\|[^\[\]]{0,200})?\]\]")

# ``#тег``: решётка в начале слова и сразу за ней буква. Требование буквы —
# это и есть отличие тега от заголовка Markdown (``# Заголовок`` — за решёткой
# пробел) и от ``#42`` (номер issue, а не область знания). Взгляд назад
# отсекает вторую решётку в ``## Заголовок``.
_TAG_RE = re.compile(r"(?<![^\s(\[])#([^\W\d_][\w/-]{0,63})", re.UNICODE)

_WS_RE = re.compile(r"\s+")

# Теги, дописанные в конец строки заголовка: ``Сборка образов #сеть #devops``.
# Требование пробела перед решёткой — то же, что и в ``_TAG_RE``: оно отличает
# тег от «C#» и от якоря в ссылке, у которых слева стоит буква.
_TRAILING_TAGS_RE = re.compile(
    r"(?:\s+#[^\W\d_][\w/-]{0,63})+\s*$", re.UNICODE
)


def normalize_key(text: str) -> str:
    """The comparison form of a note title / link target.

    Resolution has to survive the way people actually write: a heading marker
    on the note (``# Vector search``) and none in the link, a stray trailing
    colon, ``Ё`` against ``ё``, double spaces. Case folding plus whitespace
    collapse handles all of it without a fuzzy matcher nobody could predict.

    Теги в строке заголовка снимаются, и это не косметика. Заметку принято
    подписывать прямо в первой строке — ``# Сборка образов #сеть``, — а
    ссылаются на неё названием: ``[[Сборка образов]]``. Пока тег входил в
    ключ, такая ссылка числилась битой: человек написал её правильно, граф
    терял ребро, а поиск — шаг по связи. Снимается только хвост строки:
    ``#`` внутри названия (``C#``, якорь в URL) тегом не считается, ровно по
    тому же правилу, что и в ``extract_tags``. Если после снятия тегов не
    остаётся ничего (заголовок — сам тег), ключ остаётся прежним: пустой ключ
    не резолвится ни во что.
    """
    stripped = text.strip().lstrip("#").strip()
    stripped = stripped.strip("*_`").strip()
    stripped = stripped.rstrip(":.,;!?").strip()
    untagged = _TRAILING_TAGS_RE.sub("", stripped).rstrip(":.,;!?—–-").strip()
    if untagged:
        stripped = untagged
    return _WS_RE.sub(" ", stripped).casefold()


def extract_links(text: str) -> list[str]:
    """Wikilink targets in order of first appearance, de-duplicated."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in _WIKILINK_RE.findall(text or ""):
        target = raw.strip()
        key = normalize_key(target)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(target)
    return out


def extract_tags(text: str) -> list[str]:
    """``#tag`` values in order of first appearance, de-duplicated, lowercased."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in _TAG_RE.findall(text or ""):
        tag = raw.strip("/-").casefold()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out


def card_key(card: dict[str, Any]) -> str:
    """Resolution key of a memory card.

    Prefers the explicit untruncated ``key`` the assembler stores, because the
    displayed ``title`` is cut at 80 chars with an ellipsis — resolving against
    a truncated title would silently fail for exactly the long-titled notes.
    """
    return normalize_key(str(card.get("key") or card.get("title") or ""))


def build_index(cards: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Map normalized title → card position. First occurrence wins.

    Two notes may legitimately share a first line; picking the first keeps
    resolution deterministic instead of dependent on iteration order.
    """
    index: dict[str, int] = {}
    for i, card in enumerate(cards):
        key = card_key(card)
        if key and key not in index:
            index[key] = i
    return index


def resolve_links(
    cards: list[dict[str, Any]],
) -> tuple[list[tuple[int, int]], dict[int, list[str]]]:
    """Resolve every card's wikilinks against the note set.

    Returns directed ``(source_index, target_index)`` edges plus, per card, the
    link targets that matched no note. Dangling links are reported rather than
    dropped: in a Zettelkasten a link to a note that does not exist yet is a
    normal intermediate state, and hiding it would hide the next thing to write.
    """
    index = build_index(cards)
    edges: list[tuple[int, int]] = []
    unresolved: dict[int, list[str]] = {}
    seen: set[tuple[int, int]] = set()

    for i, card in enumerate(cards):
        # ``is None``, а не «пусто»: у заметки без ссылок список законно пустой,
        # и повторный разбор по ``body`` перечитывал бы обрезанный текст вместо
        # исходного куска, который уже разобран вызывающей стороной.
        declared = card.get("links")
        for target in (extract_links(str(card.get("body", ""))) if declared is None else declared):
            j = index.get(normalize_key(str(target)))
            if j is None:
                unresolved.setdefault(i, []).append(str(target))
                continue
            # Ссылка на саму себя ничего не добавляет графу, но раздувает
            # степень узла и врёт в статистике связности.
            if j == i or (i, j) in seen:
                continue
            seen.add((i, j))
            edges.append((i, j))
    return edges, unresolved


def backlink_map(edges: Iterable[tuple[int, int]], count: int) -> dict[int, list[int]]:
    """Incoming edges per card — the half of a link the writer never types."""
    back: dict[int, list[int]] = {i: [] for i in range(count)}
    for src, dst in edges:
        if 0 <= dst < count and 0 <= src < count:
            back[dst].append(src)
    return back


def _components(edges: Iterable[tuple[int, int]], count: int) -> list[int]:
    """Connected-component id per card over the *undirected* link graph."""
    parent = list(range(count))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        if 0 <= a < count and 0 <= b < count:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra
    return [find(i) for i in range(count)]


def assign_sectors(
    cards: list[dict[str, Any]], edges: Iterable[tuple[int, int]]
) -> list[str]:
    """A sector per card, aligned with ``cards`` by position.

    A tag is a direct statement of area, so a tagged note takes its first tag.
    An untagged note inherits the dominant tag of the notes it is linked to:
    linking a note into a cluster is itself the act of filing it, and demanding
    the tag be repeated would punish exactly the well-connected notes. Whatever
    is left — no tag, no link into a tagged cluster — is honestly ``unsorted``
    rather than assigned a sector nobody chose.
    """
    count = len(cards)
    sectors: list[Optional[str]] = []
    for card in cards:
        tags = card.get("tags")
        if tags is None:
            tags = extract_tags(str(card.get("body", "")))
        sectors.append(str(tags[0]) if tags else None)

    comp = _components(edges, count)
    votes: dict[int, dict[str, int]] = {}
    for i, sector in enumerate(sectors):
        if sector:
            votes.setdefault(comp[i], {})
            votes[comp[i]][sector] = votes[comp[i]].get(sector, 0) + 1

    resolved: list[str] = []
    for i, sector in enumerate(sectors):
        if sector:
            resolved.append(sector)
            continue
        tally = votes.get(comp[i])
        if tally:
            # Ничья разрешается по алфавиту, чтобы разбивка не прыгала между
            # запусками на одних и тех же данных.
            resolved.append(min(tally, key=lambda t: (-tally[t], t)))
        else:
            resolved.append(SECTOR_UNSORTED)
    return resolved


def annotate(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """Everything the graph needs about a set of memory cards, in one pass.

    ``links``/``backlinks`` are index lists so the caller — which owns the node
    id scheme — stays free to name nodes however it likes.
    """
    for card in cards:
        body = f"{card.get('title', '')}\n{card.get('body', '')}"
        card.setdefault("links", extract_links(body))
        card.setdefault("tags", extract_tags(body))

    edges, unresolved = resolve_links(cards)
    back = backlink_map(edges, len(cards))
    sectors = assign_sectors(cards, edges)

    out_links: dict[int, list[int]] = {i: [] for i in range(len(cards))}
    for src, dst in edges:
        out_links[src].append(dst)

    return {
        "edges": edges,
        "links": out_links,
        "backlinks": back,
        "sectors": sectors,
        "unresolved": unresolved,
    }
