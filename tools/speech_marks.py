"""Speech cues: what is being said, where it came from, and how loud it is.

Speaking a reply is only half of "Digit talks".  The other half is *showing*
it: the surface has to be able to point at the sentence currently leaving the
speaker, so the user can see where the agent is and cut in on the right
thought.  That needs three things this module provides, none of which involve
audio alignment:

``plan_speech``
    Cuts the spoken script into cues and, for each cue, points back at the
    range of the **original** text it came from.  The spoken script is not the
    displayed text — ``prepare_spoken_text`` strips Markdown, drops emoji and
    expands symbols — so a surface cannot highlight by string search.  The
    back-pointer is computed once here and travels with the cue.

``marks_from_durations``
    Turns per-cue audio durations into absolute start times.  Synthesis is
    already per sentence on every path (``SentenceChunker`` cuts, the provider
    speaks one sentence at a time), so the timings are a by-product of
    synthesis rather than a forced alignment.  This mirrors the reader-audio
    contract of the portal, where the synthesizer returns ``marks`` in seconds
    for the sentence boundaries it was handed.

``bar_levels`` / ``render_bars``
    Frequency-band levels straight off the int16 PCM being played, and their
    block-character rendering.  Decoration, not function — but it is the part
    that makes a spoken reply feel alive rather than buffered.

Everything here is pure stdlib and pure function: no audio device, no network,
no provider.  A surface with no sound at all can still import this module, and
a test that calls it never makes a noise.
"""

from __future__ import annotations

import math
import re
from array import array
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

# Kept in sync with ``tools.tts_streaming.SENTENCE_BOUNDARY_RE`` — the cutter
# the speaker pipeline and the speak-stream WebSocket already use.  Duplicated
# rather than imported so this module stays free of the provider stack (which
# pulls in the whole TTS tool); ``tests/tools/test_speech_marks.py`` fails if
# the two spellings ever drift, the same guard ``agent.tts_registry`` uses for
# its built-in name list.
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])(?:\s|\n)|(?:\n\n)")

# A cue shorter than this is merged into the next one, matching
# ``SentenceChunker(min_len=20)``: "Ha!" is not worth its own clip, its own
# highlight, or its own network round-trip.
MIN_CUE_CHARS = 20

# Anchor length for the back-pointer search.  Long enough that a match is not
# a coincidence, short enough to survive a symbol expansion in the middle of
# the sentence ("°C" -> "degrees Celsius").
_ANCHOR_CHARS = 12

_BLOCKS = " ▁▂▃▄▅▆▇█"


@dataclass(frozen=True)
class Cue:
    """One spoken unit and its two coordinate systems.

    ``spoken_start``/``spoken_end`` index the spoken script (what the
    synthesizer is handed).  ``source_start``/``source_end`` index the
    original text (what the user sees) and are ``None`` when the back-pointer
    could not be established — a surface then simply does not highlight that
    cue, which is a normal state, not an error.
    """

    index: int
    text: str
    spoken_start: int
    spoken_end: int
    source_start: Optional[int] = None
    source_end: Optional[int] = None

    @property
    def has_source(self) -> bool:
        return self.source_start is not None and self.source_end is not None


@dataclass(frozen=True)
class SpeechPlan:
    """The spoken script plus the cues that make it up."""

    source: str
    spoken: str
    cues: Tuple[Cue, ...]

    def cue_at(self, seconds: float, marks: Sequence[float]) -> Optional[Cue]:
        """Return the cue playing at *seconds*, given per-cue start times."""
        found: Optional[Cue] = None
        for cue, start in zip(self.cues, marks):
            if start <= seconds:
                found = cue
            else:
                break
        return found


# ---------------------------------------------------------------------------
# Back-pointer: spoken sentence -> range in the original text
# ---------------------------------------------------------------------------


def _project(text: str) -> Tuple[str, List[int]]:
    """Reduce *text* to its alphanumerics, remembering where each one came from.

    Markdown syntax, emoji, punctuation and whitespace all vanish, so the same
    sentence projects identically before and after ``prepare_spoken_text``.
    The index list maps each projected character back to its offset in *text*.
    """
    chars: List[str] = []
    offsets: List[int] = []
    for position, char in enumerate(text):
        if char.isalnum():
            chars.append(char.lower())
            offsets.append(position)
    return "".join(chars), offsets


def _longest_match(needle: str, haystack: str, start_at: int, *, from_end: bool) -> Tuple[int, int]:
    """Longest prefix (or suffix) of *needle* present in *haystack*.

    Returns ``(position, length)``, or ``(-1, 0)`` when even the shortest
    acceptable anchor is missing.  Binary search: the property "a prefix of
    length k occurs" is monotone — if k occurs then so does k-1 — which is
    exactly what makes halving valid here.
    """
    low, high = _ANCHOR_CHARS, len(needle)
    best_at, best_len = -1, 0
    while low <= high:
        middle = (low + high) // 2
        piece = needle[-middle:] if from_end else needle[:middle]
        found = haystack.find(piece, start_at)
        if found >= 0:
            best_at, best_len = found, middle
            low = middle + 1
        else:
            high = middle - 1
    return best_at, best_len


def _locate(
    needle: str,
    haystack: str,
    offsets: Sequence[int],
    cursor: int,
    source_len: int,
) -> Tuple[Optional[int], Optional[int], int]:
    """Find *needle* in the projected *haystack* at or after *cursor*.

    Returns ``(source_start, source_end, new_cursor)``.  An exact match is the
    normal case; when the cleaner rewrote something in the middle of the
    sentence ("18 °C" becomes "18 degrees Celsius", "&" becomes "and"), the
    longest surviving prefix and suffix bracket the range instead.  Below the
    anchor length there is nothing left to be sure of, so the answer is "no
    back-pointer" rather than a guess — an unhighlighted sentence is a normal
    state, a wrongly highlighted one is a lie.
    """
    if not needle:
        return None, None, cursor

    hit = haystack.find(needle, cursor)
    if hit >= 0:
        end = hit + len(needle) - 1
        return offsets[hit], offsets[end] + 1, end + 1

    if len(needle) <= _ANCHOR_CHARS:
        return None, None, cursor

    head_at, head_len = _longest_match(needle, haystack, cursor, from_end=False)
    if head_at < 0:
        return None, None, cursor

    tail_at, tail_len = _longest_match(
        needle, haystack, head_at + head_len, from_end=True
    )
    if tail_at < 0:
        # The head matched but nothing past it did; point at what is certain.
        end = head_at + head_len - 1
    else:
        end = tail_at + tail_len - 1

    if end >= len(offsets) or offsets[end] + 1 > source_len:
        return None, None, cursor
    return offsets[head_at], offsets[end] + 1, end + 1


# Punctuation that belongs to the sentence it follows.  The projection stops
# at the last letter, and a highlight that ends one character before the full
# stop reads as broken, so the range is nudged over whatever closes it.
_CLOSERS = ".!?…,;:\"')]}»”’"


def _extend_over_closers(text: str, end: int) -> int:
    while end < len(text) and text[end] in _CLOSERS:
        end += 1
    return end


def split_spoken(spoken: str, *, min_len: int = MIN_CUE_CHARS) -> List[Tuple[int, int]]:
    """Cut a spoken script into ``(start, end)`` sentence ranges.

    Same boundaries as ``SentenceChunker``, but offset-preserving: the caller
    needs to know *where* in the script each sentence sits, not just its text.
    """
    ranges: List[Tuple[int, int]] = []
    start = 0
    search = 0
    for match in SENTENCE_BOUNDARY_RE.finditer(spoken):
        if match.end() <= search:
            continue
        head = spoken[start:match.end()]
        if len(head.strip()) < min_len:
            search = match.end()
            continue
        ranges.append((start, match.end()))
        start = match.end()
        search = start
    if spoken[start:].strip():
        ranges.append((start, len(spoken)))
    return ranges


def plan_speech(text: str, *, max_chars: Optional[int] = None) -> SpeechPlan:
    """Prepare *text* for speech and cut it into cues with back-pointers.

    The spoken script comes from ``prepare_spoken_text`` untouched — one
    cleaner, all paths.  This function only adds coordinates.
    """
    if not text or not text.strip():
        return SpeechPlan(source=text or "", spoken="", cues=())

    try:
        from tools.tts_text_normalize import prepare_spoken_text

        spoken = prepare_spoken_text(text, max_chars=max_chars)
    except Exception:  # pragma: no cover - defensive, cleaner is stdlib-only
        spoken = text.strip()

    if not spoken:
        return SpeechPlan(source=text, spoken="", cues=())

    source_projection, source_offsets = _project(text)
    cursor = 0
    cues: List[Cue] = []
    for index, (start, end) in enumerate(split_spoken(spoken)):
        piece = spoken[start:end]
        needle, _ = _project(piece)
        source_start, source_end, cursor = _locate(
            needle, source_projection, source_offsets, cursor, len(text)
        )
        if source_end is not None:
            source_end = _extend_over_closers(text, source_end)
        cues.append(
            Cue(
                index=index,
                text=piece.strip(),
                spoken_start=start,
                spoken_end=end,
                source_start=source_start,
                source_end=source_end,
            )
        )
    return SpeechPlan(source=text, spoken=spoken, cues=tuple(cues))


class SourceTracker:
    """Locate spoken sentences inside a reply that is still arriving.

    The streaming path never has the finished text: deltas arrive, sentences
    leave, and the surface has to be told *now* which part of what it already
    drew is being spoken. This keeps the growing raw text and its projection
    side by side, and answers one sentence at a time, moving forward only —
    the same sentence is never matched twice, and a sentence that cannot be
    found leaves the cursor where it was so the next one still can be.
    """

    def __init__(self) -> None:
        self._source = ""
        self._projection = ""
        self._offsets: List[int] = []
        self._cursor = 0

    @property
    def source(self) -> str:
        return self._source

    def feed(self, delta: str) -> None:
        """Absorb one more piece of the raw reply."""
        if not delta:
            return
        base = len(self._source)
        chars: List[str] = []
        for position, char in enumerate(delta):
            if char.isalnum():
                chars.append(char.lower())
                self._offsets.append(base + position)
        self._projection += "".join(chars)
        self._source += delta

    def locate(self, spoken: str) -> Tuple[Optional[int], Optional[int]]:
        """Return the range of *spoken* in the raw text, or ``(None, None)``."""
        needle, _ = _project(spoken or "")
        start, end, cursor = _locate(
            needle, self._projection, self._offsets, self._cursor, len(self._source)
        )
        self._cursor = cursor
        if end is not None:
            end = _extend_over_closers(self._source, end)
        return start, end


# ---------------------------------------------------------------------------
# Timings
# ---------------------------------------------------------------------------


def marks_from_durations(durations: Sequence[float]) -> Tuple[float, ...]:
    """Turn per-cue durations into absolute cue start times, in seconds.

    ``marks[i]`` is when cue *i* starts.  Negative or non-finite durations are
    treated as zero: a provider that fails to report a length must not push
    every later mark into the past.
    """
    marks: List[float] = []
    running = 0.0
    for value in durations:
        marks.append(round(running, 3))
        try:
            step = float(value)
        except (TypeError, ValueError):
            step = 0.0
        if not math.isfinite(step) or step < 0:
            step = 0.0
        running += step
    return tuple(marks)


def pcm_seconds(pcm_bytes: int, *, sample_rate: int = 24000, channels: int = 1) -> float:
    """Duration of raw int16 PCM, in seconds."""
    frame = 2 * max(1, channels)
    if sample_rate <= 0:
        return 0.0
    return max(0.0, pcm_bytes / frame / sample_rate)


# ---------------------------------------------------------------------------
# Frequency bands
# ---------------------------------------------------------------------------


def band_centres(bands: int, *, sample_rate: int = 24000) -> Tuple[float, ...]:
    """Log-spaced band centres from 100 Hz to just under Nyquist."""
    bands = max(1, int(bands))
    low = 100.0
    high = min(6000.0, sample_rate * 0.45)
    if high <= low or bands == 1:
        return (low,)
    step = (math.log(high) - math.log(low)) / (bands - 1)
    return tuple(math.exp(math.log(low) + step * index) for index in range(bands))


def bar_levels(
    pcm: bytes,
    bands: int = 12,
    *,
    sample_rate: int = 24000,
    window: int = 512,
    floor_db: float = -55.0,
) -> Tuple[float, ...]:
    """Band energies of int16 mono PCM, each normalised to ``0.0..1.0``.

    A Goertzel filter per band over the tail of the buffer — the most recent
    audio, which is what the eye expects the bars to follow.  Pure Python on
    purpose: numpy is an optional dependency in this repo, and a decoration
    must never be the reason a reply refuses to be spoken.
    """
    bands = max(1, int(bands))
    if not pcm or len(pcm) < 4:
        return tuple(0.0 for _ in range(bands))

    usable = len(pcm) - (len(pcm) % 2)
    samples = array("h")
    samples.frombytes(pcm[:usable])
    if len(samples) > window:
        samples = samples[-window:]
    count = len(samples)
    if count < 8:
        return tuple(0.0 for _ in range(bands))

    scale = 1.0 / 32768.0
    values = [sample * scale for sample in samples]

    levels: List[float] = []
    for centre in band_centres(bands, sample_rate=sample_rate):
        omega = 2.0 * math.pi * centre / sample_rate
        coeff = 2.0 * math.cos(omega)
        s1 = 0.0
        s2 = 0.0
        for value in values:
            s0 = value + coeff * s1 - s2
            s2 = s1
            s1 = s0
        power = s1 * s1 + s2 * s2 - coeff * s1 * s2
        magnitude = math.sqrt(max(0.0, power)) / (count / 2.0)
        decibels = 20.0 * math.log10(magnitude + 1e-9)
        level = (decibels - floor_db) / (0.0 - floor_db)
        levels.append(min(1.0, max(0.0, level)))
    return tuple(levels)


def render_bars(levels: Sequence[float], *, blocks: str = _BLOCKS) -> str:
    """Render band levels as one row of block characters.

    One row, not a column chart: the terminal surfaces have a single line to
    spend on decoration and the row has to sit next to text without pushing it
    around.
    """
    if not levels:
        return ""
    top = len(blocks) - 1
    out: List[str] = []
    for level in levels:
        try:
            value = float(level)
        except (TypeError, ValueError):
            value = 0.0
        if not math.isfinite(value):
            value = 0.0
        step = int(round(min(1.0, max(0.0, value)) * top))
        out.append(blocks[step])
    return "".join(out)
