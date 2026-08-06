#!/usr/bin/env python3
"""Deterministic literal detectors.

Every detector is a shape test on the raw query string: either the text matches
a syntax or it does not. No probability, no embedding, no guess. A detector that
fires is hard evidence about what the user pasted; a detector that stays silent
is the main reason this system refuses.

Ordering matters: the more specific shapes are tried first, because a JWT is
also base64-ish and a CIDR is also an IP.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Ent:
    kind: str
    value: str
    start: int
    end: int


# --- individual shapes -----------------------------------------------------
RE_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*")
RE_URL = re.compile(r"\bhttps?://[^\s,;«»\"']+")
RE_SAFELINK = re.compile(r"\bhttps?://[a-z0-9\-]+\.safelinks\.protection\.outlook\.com/[^\s,;«»\"']*", re.I)
RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
RE_CIDR = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}\b")
RE_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
RE_MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:\-]){2,5}[0-9A-Fa-f]{2}\b")
RE_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
RE_PHONE = re.compile(r"(?<![\w.])(?:\+\d[\d \-()]{7,18}\d|8\d{10})(?![\w.])")
RE_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
RE_HEXCOLOR = re.compile(r"(?<!&)#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")
RE_BCRYPT = re.compile(r"\$2[aby]?\$\d{2}\$[./A-Za-z0-9]{53}")
RE_CRON = re.compile(r"(?<![\w/])(?:[\d*/,\-]+\s+){4}[\d*/,\-]+(?![\w/])")
RE_BINARY = re.compile(r"\b[01]{8}(?:\s+[01]{8})*\b")
RE_HTML_ENTITY = re.compile(r"&(?:[a-zA-Z]{2,10}|#\d{2,6}|#x[0-9a-fA-F]{2,6});")
# `&lt;` is an HTML escape; `&#72;` is a Unicode code point written decimally.
# Different tools, so they are different entity kinds.
RE_ENTITY_NAMED = re.compile(r"&[a-zA-Z]{2,10};")
RE_ENTITY_NUMERIC = re.compile(r"&#(?:\d{2,6}|x[0-9a-fA-F]{2,6});")
RE_PERCENT_ENC = re.compile(r"(?:%[0-9A-Fa-f]{2}){2,}")
RE_TAG = re.compile(r"<[A-Za-z!/][^<>]{0,400}>")
RE_TIMESTAMP = re.compile(r"(?<![\d.])1[0-9]{9}(?![\d.])")
RE_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+\-]\d{2}:?\d{2})?)?\b")
RE_ROMAN = re.compile(r"\b(?=[MDCLXVI]{2,})M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})\b")
RE_DOCKER_RUN = re.compile(r"\bdocker\s+run\b[^\n]*")
RE_SQL = re.compile(r"\b(?:select|insert|update|delete)\b[^\n]{10,}", re.I)
RE_UA = re.compile(r"\bMozilla/5\.0[^\n]+")
RE_TOTP_SECRET = re.compile(r"\b[A-Z2-7]{16,32}\b")
RE_HEX_BLOB = re.compile(r"\b[0-9a-f]{16,}\b")
RE_BASE64 = re.compile(r"\b(?:[A-Za-z0-9+/]{16,}={0,2})\b")
RE_MATH = re.compile(r"[-+*/^()\d\s.]*(?:sqrt|sin|cos|tan|abs|log|exp|\^|\*|/)[-+*/^()\d\s.a-z]*")
RE_REGEXY = re.compile(r"[\\][dwsbSWD]|\[\^?[^\]]+\]\{\d|\{\d+(?:,\d*)?\}")
RE_QUOTED = re.compile(r"«([^»]{1,400})»|\"([^\"]{1,400})\"|'([^']{1,400})'|`([^`]{1,400})`")
RE_CHMOD_WORD = re.compile(r"\bchmod\b", re.I)

# Latin-only "identifier-ish" literal: password123, s3cr3t, order-42, sk-live-...
RE_IDENTLIKE = re.compile(r"\b(?=[A-Za-z0-9]*\d)[A-Za-z0-9][A-Za-z0-9_@!#$%^&*.\-+]{2,}\b")


def _all_json(text: str) -> list[Ent]:
    """Every balanced {...} / [...] that actually parses. Parsing, not guessing.

    All of them, not just the first: «покажи, что изменилось между {…} и {…}»
    needs both operands or json-diff cannot be called.
    """
    out: list[Ent] = []
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        while start != -1:
            depth = 0
            end = None
            for i in range(start, len(text)):
                if text[i] == opener:
                    depth += 1
                elif text[i] == closer:
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end is not None:
                blob = text[start:end]
                try:
                    json.loads(blob)
                    out.append(Ent("json", blob, start, end))
                    start = text.find(opener, end)
                    continue
                except Exception:
                    pass
            start = text.find(opener, start + 1)
    out.sort(key=lambda e: e.start)
    return out


def _looks_yaml(text: str) -> Ent | None:
    """Two or more `key: value` lines with no JSON braces around them."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    hits = [i for i, ln in enumerate(lines)
            if re.fullmatch(r"\s*[\w.\-]+\s*:\s*\S.*", ln) and not ln.strip().startswith("{")]
    if len(hits) >= 2 and hits[-1] - hits[0] == len(hits) - 1:
        blob = "\n".join(lines[hits[0]:hits[-1] + 1])
        idx = text.find(lines[hits[0]])
        return Ent("yaml", blob, idx, idx + len(blob))
    return None


def _looks_toml(text: str) -> Ent | None:
    if re.search(r"^\s*\[[\w.\-]+\]\s*$", text, re.M) and re.search(r"^\s*[\w.\-]+\s*=", text, re.M):
        return Ent("toml", text.strip(), 0, len(text))
    return None


def _looks_markdown(text: str) -> Ent | None:
    """Markdown starts at its first marker, not at the start of the sentence.

    «переведи markdown в html: # Заголовок» - the payload begins at the `#`,
    and the instruction in front of it is not part of the document.
    """
    m = re.search(r"^#{1,6}\s+\S", text, re.M) or re.search(r"#{1,6}\s+\S", text)
    if m:
        return Ent("markdown", text[m.start():].strip(), m.start(), len(text))
    m = re.search(r"\*\*[^*]+\*\*", text)
    if m:
        line_start = text.rfind("\n", 0, m.start()) + 1
        return Ent("markdown", text[line_start:].strip(), line_start, len(text))
    return None


HTML_TAGS = {"html", "head", "body", "div", "span", "p", "a", "ul", "ol", "li",
             "script", "style", "table", "tr", "td", "th", "b", "i", "em",
             "strong", "h1", "h2", "h3", "h4", "form", "input", "img", "br"}


def _looks_xml(text: str) -> Ent | None:
    """A balanced tag pair whose name is not an HTML element.

    `<order><id>15</id></order>` is XML someone wants converted;
    `<script>alert(1)</script>` is HTML someone wants escaped. The tag
    vocabulary is the only thing that separates them, so it is what decides.
    """
    m = re.search(r"<([A-Za-z][\w\-]*)(?:\s[^>]*)?>.*?</\1>", text, re.S)
    if m and m.group(1).lower() not in HTML_TAGS:
        return Ent("xml", m.group(0), m.start(), m.end())
    return None


# Order is significant: a long container shape must claim its span before the
# small shapes inside it do. A user-agent string contains what looks like an
# IPv4 address (`120.0.0.0`) and what looks like base64 (`AppleWebKit/537`);
# letting those match first destroyed the user-agent route entirely.
DETECTORS: list[tuple[str, re.Pattern]] = [
    ("jwt", RE_JWT),
    ("safelink", RE_SAFELINK),
    ("user_agent", RE_UA),
    ("docker_run", RE_DOCKER_RUN),
    ("url", RE_URL),
    ("email", RE_EMAIL),
    ("bcrypt_hash", RE_BCRYPT),
    ("uuid", RE_UUID),
    ("cidr", RE_CIDR),
    ("mac", RE_MAC),
    ("ipv4", RE_IPV4),
    ("iban", RE_IBAN),
    ("phone", RE_PHONE),
    ("entity_numeric", RE_ENTITY_NUMERIC),
    ("entity_named", RE_ENTITY_NAMED),
    ("hexcolor", RE_HEXCOLOR),
    ("binary", RE_BINARY),
    ("percent_enc", RE_PERCENT_ENC),
    ("iso_date", RE_ISO_DATE),
    ("timestamp", RE_TIMESTAMP),
    ("tag", RE_TAG),
    ("cron", RE_CRON),
    ("roman", RE_ROMAN),
]


def detect(text: str) -> list[Ent]:
    """All literal shapes present in the query, most specific first.

    Overlapping matches are suppressed: a JWT swallows the base64 inside it.
    """
    found: list[Ent] = []
    taken: list[tuple[int, int]] = []

    def free(s: int, e: int) -> bool:
        return not any(s < te and ts < e for ts, te in taken)

    # XML is probed first so the generic tag detector cannot shred it.
    xml = _looks_xml(text)
    if xml:
        found.append(xml)
        taken.append((xml.start, xml.end))

    for kind, rx in DETECTORS:
        for m in rx.finditer(text):
            s, e = m.span()
            if free(s, e):
                found.append(Ent(kind, m.group(0), s, e))
                taken.append((s, e))

    for ent in _all_json(text):
        if free(ent.start, ent.end):
            found.append(ent)
            taken.append((ent.start, ent.end))

    for probe in (_looks_toml, _looks_yaml, _looks_markdown):
        ent = probe(text)
        if ent and free(ent.start, ent.end):
            found.append(ent)
            taken.append((ent.start, ent.end))

    for m in RE_SQL.finditer(text):
        if free(*m.span()):
            found.append(Ent("sql", m.group(0).strip(), *m.span()))
            taken.append(m.span())

    # base64 last: everything structured has already claimed its span.
    for m in RE_BASE64.finditer(text):
        s, e = m.span()
        blob = m.group(0)
        if free(s, e) and re.search(r"[A-Z]", blob) and re.search(r"[a-z0-9]", blob):
            found.append(Ent("base64", blob, s, e))
            taken.append((s, e))
    # Padded base64 may be short («aGVsbG8=»); the `=` is the evidence.
    for m in re.finditer(r"\b[A-Za-z0-9+/]{4,}={1,2}", text):
        if free(*m.span()):
            found.append(Ent("base64", m.group(0), *m.span()))
            taken.append(m.span())

    found = [_trim_cyrillic(e) if e.kind in {"docker_run", "sql", "user_agent"} else e
             for e in found]
    for kind in ("tag", "entity_named", "entity_numeric"):
        found = _join_runs(found, kind)
    found = fill_values(found, text)
    found.sort(key=lambda ent: ent.start)
    return found


def _trim_cyrillic(ent: Ent) -> Ent:
    """A shell command or a UA string ends where Russian prose resumes."""
    m = re.search(r"[А-Яа-яЁё]", ent.value)
    val = ent.value[:m.start()] if m else ent.value
    val = val.rstrip(" ,;—–-")
    return Ent(ent.kind, val, ent.start, ent.start + len(val))


def _join_runs(ents: list[Ent], kind: str) -> list[Ent]:
    """Merge adjacent same-kind matches into the one literal the user pasted.

    `<script>alert(1)</script>` is two tag matches around a payload; the tool
    argument is the whole thing, not the opening tag.
    """
    same = sorted([e for e in ents if e.kind == kind], key=lambda e: e.start)
    if len(same) < 2:
        return ents
    others = [e for e in ents if e.kind != kind]
    merged: list[Ent] = []
    cur = same[0]
    for nxt in same[1:]:
        if nxt.start - cur.end <= 60:
            cur = Ent(kind, None, cur.start, nxt.end)  # value filled below
        else:
            merged.append(cur)
            cur = nxt
    merged.append(cur)
    return others + merged


def fill_values(ents: list[Ent], text: str) -> list[Ent]:
    return [e if e.value is not None else Ent(e.kind, text[e.start:e.end], e.start, e.end)
            for e in ents]


def quoted_spans(text: str) -> list[Ent]:
    out = []
    for m in RE_QUOTED.finditer(text):
        val = next(g for g in m.groups() if g is not None)
        out.append(Ent("quoted", val, m.start(), m.end()))
    return out


def by_kind(ents: list[Ent], *kinds: str) -> list[Ent]:
    want = set(kinds)
    return [e for e in ents if e.kind in want]
