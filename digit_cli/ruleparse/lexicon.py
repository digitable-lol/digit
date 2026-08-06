#!/usr/bin/env python3
"""Catalog-derived lexicon: configuration R0, built with zero hand-authoring.

Everything here is derived mechanically from tool_catalog.json - the Russian
title, the Russian description and the English keyword list that each of the 86
utilities already ships. Nobody writes a rule per tool. This is the honest
floor: what you get for free the day a new utility is added to the catalog.

Matching is lemma overlap weighted by inverse document frequency, so «конвертер»
(which 12 tools claim) counts for far less than «нумероним» (which one does).
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .morph import lemma_set, normalize

HERE = os.path.dirname(os.path.abspath(__file__))
# Каталог едет ВНУТРИ пакета, а не по абсолютному пути в чужой домашний
# каталог, как было в эксперименте. Лексикон — чистая функция каталога: если
# каталог подменить, изменится маршрутизация. Значит каталог обязан приезжать
# тем же артефактом, что и код, иначе «правило ответило так-то» перестаёт быть
# воспроизводимым утверждением.
#
# DIGIT_TOOL_CATALOG — явная точка подмены для отладки и для сверки с
# измерительным харнессом. По умолчанию не используется.
CATALOG_PATH = os.environ.get(
    "DIGIT_TOOL_CATALOG", os.path.join(HERE, "tool_catalog.json"))

W_KEYWORD = 3.0
W_TITLE = 2.0
W_DESC = 1.0


@dataclass
class Tool:
    tool_id: str
    category: str
    title_ru: str
    description_ru: str
    keywords: list[str]
    args: list[str]
    weights: dict[str, float] = field(default_factory=dict)


def _split_keyword(kw: str) -> set[str]:
    """`user-agent` -> {user, agent, useragent}; `SHA256` -> {sha256}."""
    k = normalize(kw)
    parts = {p for p in re.split(r"[\s\-_/.]+", k) if len(p) > 1}
    parts.add(re.sub(r"[\s\-_/.]+", "", k))
    return {p for p in parts if len(p) > 1}


def load_catalog(path: str = CATALOG_PATH) -> list[Tool]:
    raw = json.load(open(path, encoding="utf-8"))
    tools = []
    for t in raw["tools"]:
        tools.append(Tool(
            tool_id=t["tool_id"], category=t.get("category", ""),
            title_ru=t.get("title_ru", ""), description_ru=t.get("description_ru", "") or "",
            keywords=list(t.get("keywords", [])), args=list(t.get("args", [])),
        ))
    return tools


def build_lexicon(tools: list[Tool]) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """(tool_id -> {lemma: weight}, lemma -> idf). Pure function of the catalog."""
    per_tool: dict[str, dict[str, float]] = {}
    doc_freq: Counter[str] = Counter()

    for tool in tools:
        bag: dict[str, float] = defaultdict(float)
        for kw in tool.keywords:
            for piece in _split_keyword(kw):
                bag[piece] += W_KEYWORD
        for lemma in lemma_set(tool.title_ru):
            bag[lemma] += W_TITLE
        # The tool_id itself is a legitimate handle: users paste "base64-string-converter".
        for piece in _split_keyword(tool.tool_id):
            bag[piece] += W_TITLE
        for lemma in lemma_set(tool.description_ru):
            bag[lemma] += W_DESC
        per_tool[tool.tool_id] = dict(bag)
        for lemma in bag:
            doc_freq[lemma] += 1

    n = len(tools)
    idf = {lemma: math.log(1 + n / df) for lemma, df in doc_freq.items()}
    return per_tool, idf


class CatalogMatcher:
    """R0 scorer: IDF-weighted lemma overlap, nothing else."""

    def __init__(self, path: str = CATALOG_PATH):
        self.tools = load_catalog(path)
        self.by_id = {t.tool_id: t for t in self.tools}
        self.per_tool, self.idf = build_lexicon(self.tools)

    def score(self, query: str) -> list[tuple[str, float, list[str]]]:
        q = lemma_set(query)
        # Latin words also enter un-lemmatised so `sha256` hits the keyword bag.
        q |= {w for w in re.findall(r"[a-z]{2,}[a-z0-9]*", normalize(query))}
        out = []
        for tool_id, bag in self.per_tool.items():
            hits = [lemma for lemma in q if lemma in bag]
            if not hits:
                continue
            s = sum(bag[h] * self.idf.get(h, 1.0) for h in hits)
            out.append((tool_id, s, sorted(hits, key=lambda h: -bag[h] * self.idf.get(h, 1.0))))
        out.sort(key=lambda r: -r[1])
        return out
