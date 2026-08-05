"""Markdown-aware chunking for the Digitable knowledge base.

The corpus (``courses/content/post/**``, ``courses/docs/sdd``,
``courses/products/workbench/templates``, and the flang repository's
documentation) is Markdown: usually a YAML front matter block followed by a
body organised with ATX headings. Blind character-window chunking would cut
mid-sentence and, worse, strip the section heading away from the text it
introduces — and the heading *is* part of the meaning ("## Горутины" over a
paragraph that never repeats the word "горутина").

Files without front matter (the flang documents are plain READMEs and specs)
lose only the ``title`` field, which falls back to the file stem; the
heading path still carries the structure, so they chunk the same way.

So chunking here is structural:

1. Front matter is parsed out and kept as chunk metadata (``title``,
   ``summary``, ``tags``).
2. The body is split at heading boundaries, tracking the full heading path
   (``H1 › H2 › H3``).
3. A section longer than :data:`MAX_CHUNK_WORDS` is windowed into
   paragraph-aligned pieces with :data:`OVERLAP_WORDS` of overlap, so a
   fact that straddles a window boundary is still wholly inside one chunk.
4. Every chunk's *embedded* text is prefixed with a provenance header
   (track, title, heading path, summary, tags). This is what makes a chunk
   from the middle of ``04-concurrency.md`` retrievable by "Go concurrency"
   even when the chunk body never says "Go".

Why ``MAX_CHUNK_WORDS = 320``: ``nomic-embed-text`` is loaded with a 2048
token context (verified via ``/api/ps``). Russian tokenises at roughly
3-4 BPE tokens per word in that vocabulary, so ~320 words plus the
provenance header lands under the ceiling with margin. Anything past the
ceiling is silently dropped by the encoder — the tail of the chunk would
be embedded as if it did not exist.

Pure stdlib: this module must import cleanly with no third-party
dependency, so ``kb`` can be introspected (``kb status``) on a machine
where numpy was never installed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------

MAX_CHUNK_WORDS = 240
"""Hard ceiling on body words per chunk.

Sized against the *measured* encoder limit rather than a guess. The
endpoint accepted 400 words / 2753 chars of Russian and rejected 500 words
/ 3415 chars, and ollama refuses over-long input instead of truncating, so
an oversized chunk is a hard failure. 240 body words (~1650 chars) plus the
provenance header stays comfortably inside that bracket; the client-side
cap in :data:`digit_cli.kb.embed.MAX_EMBED_CHARS` is the backstop.
"""

TARGET_CHUNK_WORDS = 200
"""Greedy packing target when merging consecutive sections.

Measured on the real corpus, the articles use fine-grained headings: the
median section is ~91 words. Emitting those one-to-one produced 26,457
chunks over ``content/post`` alone. That is bad twice over. Retrieval-wise
a 91-word chunk rarely carries a complete thought, so the generator gets
fragments. Cost-wise it is far worse: the encoder has a large *fixed*
per-item cost (measured ~2.7 s for a two-word input on this CPU-only
host), so cost scales with the *number* of chunks far more than with their
size. Packing adjacent sections up to ~260 words cuts the chunk count
roughly threefold — and therefore the index time roughly threefold — while
making each chunk a more useful citation.
"""

MIN_CHUNK_WORDS = 25
"""A section this small is never emitted on its own.

A lone "## Итоги" with one line under it is not independently retrievable
and only adds noise to the index.
"""

OVERLAP_WORDS = 40
"""Word overlap between consecutive windows of an over-long section."""


# --------------------------------------------------------------------------
# Front matter
# --------------------------------------------------------------------------

_FM_DELIM = re.compile(r"^---\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_CODE_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass
class FrontMatter:
    """The subset of Hugo front matter the KB cares about."""

    title: str = ""
    summary: str = ""
    tags: List[str] = field(default_factory=list)
    draft: bool = False


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _parse_tag_list(value: str) -> List[str]:
    """Parse an inline YAML list: ``["go", "обзор"]`` or ``[go, обзор]``."""
    value = value.strip()
    if not (value.startswith("[") and value.endswith("]")):
        return [t for t in (_strip_quotes(value),) if t]
    inner = value[1:-1]
    return [t for t in (_strip_quotes(p) for p in inner.split(",")) if t]


def parse_front_matter(text: str) -> tuple[FrontMatter, str]:
    """Split ``text`` into (front matter, body).

    Hand-rolled rather than via PyYAML: the KB must work on a bare install,
    and the fields needed are three scalars and one inline list. Anything
    unrecognised (nested maps, block lists) is ignored rather than fatal —
    a malformed header must not take a whole file out of the index.
    """
    lines = text.splitlines()
    if not lines or not _FM_DELIM.match(lines[0]):
        return FrontMatter(), text

    end = None
    for i in range(1, len(lines)):
        if _FM_DELIM.match(lines[i]):
            end = i
            break
    if end is None:
        return FrontMatter(), text

    fm = FrontMatter()
    pending_list_key: Optional[str] = None
    for raw in lines[1:end]:
        if not raw.strip():
            continue
        # Block-style list continuation ("  - go").
        stripped = raw.strip()
        if stripped.startswith("- ") and pending_list_key == "tags":
            fm.tags.append(_strip_quotes(stripped[2:]))
            continue
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        key = key.strip().lower()
        value = value.strip()
        pending_list_key = key if not value else None
        if key == "title":
            fm.title = _strip_quotes(value)
        elif key == "summary" or (key == "description" and not fm.summary):
            fm.summary = _strip_quotes(value)
        elif key == "tags" and value:
            fm.tags = _parse_tag_list(value)
        elif key == "draft":
            fm.draft = _strip_quotes(value).lower() in ("true", "yes", "1")

    return fm, "\n".join(lines[end + 1:])


# --------------------------------------------------------------------------
# Sectioning
# --------------------------------------------------------------------------


@dataclass
class Section:
    """A run of body text under one heading path."""

    heading_path: List[str]
    lines: List[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()

    @property
    def word_count(self) -> int:
        return len(self.text.split())


def split_sections(body: str) -> List[Section]:
    """Split a Markdown body at ATX heading boundaries.

    Fenced code blocks are tracked so a ``#`` comment inside a Python or
    shell snippet is not mistaken for a heading — the corpus is full of
    them, and a false heading would shred a code example into fragments.
    """
    sections: List[Section] = []
    path: List[str] = []
    current = Section(heading_path=[])
    in_fence = False
    fence_marker = ""

    for line in body.splitlines():
        fence = _CODE_FENCE.match(line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence, fence_marker = False, ""
            current.lines.append(line)
            continue

        heading = None if in_fence else _HEADING.match(line)
        if heading:
            if current.lines and current.text:
                sections.append(current)
            level = len(heading.group(1))
            title = heading.group(2).strip()
            path = path[: level - 1]
            while len(path) < level - 1:
                path.append("")
            path.append(title)
            current = Section(heading_path=[p for p in path if p])
        else:
            current.lines.append(line)

    if current.lines and current.text:
        sections.append(current)
    return sections


def _pack_sections(sections: Sequence[Section]) -> List[Section]:
    """Greedily pack consecutive sections up to :data:`TARGET_CHUNK_WORDS`.

    Packing is only ever done across *adjacent* sections of the *same
    article*, which are topically contiguous by construction, so this does
    not blend unrelated material. The heading path of the first section in
    a group becomes the chunk's heading, and every subsequent section's own
    heading is re-materialised inline (``### Мьютексы``) so no structural
    signal is lost from the embedded text.

    A section that already exceeds the target is emitted alone and left to
    :func:`_window` to split.
    """
    out: List[Section] = []
    buf: Optional[Section] = None
    buf_words = 0

    def flush() -> None:
        nonlocal buf, buf_words
        if buf is not None and buf.text:
            out.append(buf)
        buf, buf_words = None, 0

    for sec in sections:
        words = sec.word_count
        if words == 0:
            continue
        if words >= TARGET_CHUNK_WORDS:
            flush()
            out.append(sec)
            continue
        if buf is None:
            buf, buf_words = sec, words
            continue
        if buf_words + words > TARGET_CHUNK_WORDS:
            flush()
            buf, buf_words = sec, words
            continue
        buf = Section(
            heading_path=buf.heading_path,
            lines=buf.lines + [""] + _with_heading(sec),
        )
        buf_words += words
    flush()

    # A trailing stub that could not be packed forward folds backward, so a
    # two-line "## Итоги" never becomes its own chunk.
    if len(out) >= 2 and out[-1].word_count < MIN_CHUNK_WORDS:
        tail = out.pop()
        out[-1] = Section(
            heading_path=out[-1].heading_path,
            lines=out[-1].lines + [""] + _with_heading(tail),
        )
    return out


def _with_heading(sec: Section) -> List[str]:
    """Re-materialise a merged section's own heading inside its text."""
    if sec.heading_path:
        return [f"### {sec.heading_path[-1]}"] + sec.lines
    return list(sec.lines)


def _window(text: str) -> List[str]:
    """Split over-long section text into paragraph-aligned word windows."""
    words = text.split()
    if len(words) <= MAX_CHUNK_WORDS:
        return [text]

    # Prefer paragraph boundaries: accumulate paragraphs until the ceiling.
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    windows: List[str] = []
    buf: List[str] = []
    buf_words = 0
    for para in paras:
        pw = len(para.split())
        if pw > MAX_CHUNK_WORDS:
            # A single monster paragraph (or code block): hard-window it.
            if buf:
                windows.append("\n\n".join(buf))
                buf, buf_words = [], 0
            pwords = para.split()
            step = MAX_CHUNK_WORDS - OVERLAP_WORDS
            for i in range(0, len(pwords), step):
                windows.append(" ".join(pwords[i:i + MAX_CHUNK_WORDS]))
                if i + MAX_CHUNK_WORDS >= len(pwords):
                    break
            continue
        if buf_words + pw > MAX_CHUNK_WORDS and buf:
            windows.append("\n\n".join(buf))
            # Carry the tail of the emitted window as overlap.
            tail = " ".join("\n\n".join(buf).split()[-OVERLAP_WORDS:])
            buf, buf_words = [tail], len(tail.split())
        buf.append(para)
        buf_words += pw
    if buf:
        windows.append("\n\n".join(buf))
    return [w for w in windows if w.strip()]


# --------------------------------------------------------------------------
# Public chunk type
# --------------------------------------------------------------------------


@dataclass
class Chunk:
    """One indexable unit: metadata + the exact text that gets embedded."""

    rel_path: str
    track: str
    title: str
    heading: str
    ordinal: int
    body: str
    summary: str = ""
    tags: str = ""

    @property
    def embed_text(self) -> str:
        """Provenance header + body — what actually goes to the encoder.

        The header is deliberately part of the embedded string (and of the
        FTS row): it carries the track name, article title and heading path,
        which are frequently the only place a query's key term appears.
        """
        head = [f"[{self.track}] {self.title}".strip()]
        if self.heading:
            head.append(self.heading)
        if self.summary:
            head.append(self.summary)
        if self.tags:
            head.append(self.tags)
        return "\n".join(h for h in head if h) + "\n\n" + self.body


def _derive_track(rel_path: str) -> str:
    """First path segment under the corpus root is the track name.

    ``content/post/golang/04-concurrency.md`` → ``golang``;
    ``docs/sdd/0001-foo.md`` → ``sdd``.
    """
    parts = Path(rel_path).parts
    return parts[0] if len(parts) > 1 else "root"


def chunk_markdown(rel_path: str, text: str) -> List[Chunk]:
    """Turn one Markdown document into an ordered list of chunks."""
    fm, body = parse_front_matter(text)
    track = _derive_track(rel_path)
    title = fm.title or Path(rel_path).stem.replace("-", " ")
    tags = ", ".join(fm.tags)

    sections = _pack_sections(split_sections(body))
    if not sections:
        stripped = body.strip()
        if not stripped:
            return []
        sections = [Section(heading_path=[], lines=[stripped])]

    chunks: List[Chunk] = []
    for sec in sections:
        heading = " › ".join(sec.heading_path)
        for window in _window(sec.text):
            if len(window.split()) < 5:
                continue
            chunks.append(
                Chunk(
                    rel_path=rel_path,
                    track=track,
                    title=title,
                    heading=heading,
                    ordinal=len(chunks),
                    body=window,
                    summary=fm.summary,
                    tags=tags,
                )
            )
    return chunks


def file_digest(path: Path) -> str:
    """Content hash driving incremental re-index.

    Content-addressed rather than mtime-based on purpose: ``git checkout``
    and rsync rewrite mtimes without changing bytes, and re-embedding
    thousands of unchanged files costs hours on a CPU-only encoder.
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()
