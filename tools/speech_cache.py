"""Content-addressed store for synthesized speech.

The name of an entry **is** the hash of the text it speaks, so "the file
exists" and "the text has not changed" are one statement rather than two.
That removes the whole class of staleness bugs a timestamped cache invites:
there is nothing to invalidate, no mtime to compare, no "re-synthesize
everything because the config was touched".  The portal's reader audio is
stored on exactly this rule (``docs/sdd/reader-audio.md`` in the courses
repo); this module is the same scheme applied to what Digit says out loud, so
the two never drift into two different ideas of the same thing.

Layout::

    <root>/<voice>/<aa>/<id>.<ext>     the audio
    <root>/<voice>/<aa>/<id>.json      what is known about it

``<id>`` is the hash of the spoken text, ``<aa>`` its first two characters
(256 shelves, so no directory grows unbounded), and ``<voice>`` carries
**every rendering parameter** — provider, model, voice id, speed.  The voice
lives in the path and not in the id on purpose: changing voice must create a
second set next to the first, not rename it.  That is what lets a voice be
compared against another, or replaced later, without re-synthesizing anything
that is already correct.

An entry is only usable when both files are present.  Audio without its
sidecar is a half-finished write (crash, disk full, killed process) and is
treated as absent — the same rule the portal applies, for the same reason:
the sidecar carries the sentence timings, and without them the surface cannot
show what is being said.

Nothing here plays, synthesizes, or downloads anything.  A miss is an
ordinary answer, and every failure path degrades to "no cache" rather than to
an error: speech is an addition to Digit, never a precondition.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Namespace prefix in the hash.  Keeps these ids from ever colliding with the
#: portal's reader-audio ids, which use their own prefix over the same
#: algorithm — two stores, one rule, no shared filenames.
CACHE_NAMESPACE = "digit-speech"

#: Length of the hex id.  20 hex characters is 80 bits: at the scale of one
#: user's spoken replies a collision is not a thing that happens.
ID_LENGTH = 20

#: Audio extensions the store recognises, in the order a hit is preferred.
#: Opus first because it is the smallest for speech at equal quality.
AUDIO_EXTENSIONS: Tuple[str, ...] = (".ogg", ".opus", ".mp3", ".wav", ".flac")

#: Default ceiling for the store.  Speech is small (a spoken reply is tens of
#: kilobytes) but a long-running assistant speaks a lot; the pruner keeps the
#: directory from becoming a slow leak.
DEFAULT_MAX_BYTES = 512 * 1024 * 1024

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_OFF_VALUES = {"0", "off", "false", "no", "none", "disabled"}


@dataclass(frozen=True)
class CacheEntry:
    """A stored recording and everything known about it."""

    id: str
    voice: str
    audio_path: Path
    sidecar_path: Path
    seconds: float = 0.0
    bytes: int = 0
    model: str = ""
    marks: Tuple[float, ...] = ()

    @property
    def suffix(self) -> str:
        return self.audio_path.suffix


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def cache_id(text: str, *, kind: str = "reply", lang: str = "") -> str:
    """Hash the spoken text into an entry id.

    Only what reaches the synthesizer goes into the hash.  No path, no
    session, no position in the conversation: the same sentence said twice is
    the same recording, and a reply that repeats a phrase from yesterday costs
    nothing to say again.
    """
    payload = f"{CACHE_NAMESPACE}\n{kind}\n{lang}\n{text or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:ID_LENGTH]


def voice_key(
    provider: str,
    voice: str = "",
    model: str = "",
    *,
    speed: Optional[float] = None,
) -> str:
    """Build the directory name that carries the rendering parameters.

    Everything that changes the sound but not the words belongs here.  Speed
    is included because a reply read at 1.25× is a different recording of the
    same text — putting it in the id instead would make the two fight over one
    filename.
    """
    parts: List[str] = [str(provider or "tts")]
    if model:
        parts.append(str(model))
    if voice:
        parts.append(str(voice))
    if speed is not None:
        try:
            rate = float(speed)
        except (TypeError, ValueError):
            rate = 1.0
        if abs(rate - 1.0) > 1e-6:
            parts.append(f"s{rate:g}")
    slug = _SLUG_RE.sub("-", "-".join(parts).lower()).strip("-")
    return slug or "tts"


# ---------------------------------------------------------------------------
# Location and switches
# ---------------------------------------------------------------------------


def cache_root() -> Path:
    """Where the store lives.

    ``DIGIT_SPEECH_CACHE_DIR`` wins so a machine with a small home directory
    can put the audio elsewhere; otherwise it sits under the Digit home like
    every other cache.
    """
    override = (os.environ.get("DIGIT_SPEECH_CACHE_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    from digit_constants import get_digit_dir

    return Path(get_digit_dir("cache/speech", "speech_cache"))


def enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    """Whether the store is in use.

    On by default: a cache that has to be discovered is a cache nobody has.
    ``DIGIT_SPEECH_CACHE=0`` turns it off for a run, and ``tts.cache: false``
    turns it off for good.
    """
    env = (os.environ.get("DIGIT_SPEECH_CACHE") or "").strip().lower()
    if env:
        return env not in _OFF_VALUES
    if isinstance(config, dict):
        value = config.get("cache")
        if isinstance(value, dict):
            value = value.get("enabled")
        if value is not None:
            if isinstance(value, bool):
                return value
            if str(value).strip().lower() in _OFF_VALUES:
                return False
    return True


def max_bytes(config: Optional[Dict[str, Any]] = None) -> int:
    """Ceiling for the store, in bytes."""
    env = (os.environ.get("DIGIT_SPEECH_CACHE_MAX_MB") or "").strip()
    if env:
        try:
            return max(0, int(float(env) * 1024 * 1024))
        except ValueError:
            pass
    if isinstance(config, dict):
        section = config.get("cache")
        if isinstance(section, dict) and section.get("max_mb") is not None:
            try:
                return max(0, int(float(section["max_mb"]) * 1024 * 1024))
            except (TypeError, ValueError):
                pass
    return DEFAULT_MAX_BYTES


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def entry_dir(voice: str, entry_id: str, *, root: Optional[Path] = None) -> Path:
    base = Path(root) if root is not None else cache_root()
    return base / voice / entry_id[:2]


def sidecar_path(voice: str, entry_id: str, *, root: Optional[Path] = None) -> Path:
    return entry_dir(voice, entry_id, root=root) / f"{entry_id}.json"


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def _read_sidecar(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _marks_of(data: Dict[str, Any]) -> Tuple[float, ...]:
    raw = data.get("marks")
    if not isinstance(raw, (list, tuple)):
        return ()
    out: List[float] = []
    for value in raw:
        try:
            out.append(float(value))
        except (TypeError, ValueError):
            return ()
    return tuple(out)


def lookup(
    text: str,
    voice: str,
    *,
    kind: str = "reply",
    lang: str = "",
    root: Optional[Path] = None,
) -> Optional[CacheEntry]:
    """Return the stored recording of *text* in *voice*, or None.

    Never raises.  A store on a read-only mount, a half-written entry, a
    sidecar someone edited by hand — all of them are simply a miss.
    """
    if not text:
        return None
    entry_id = cache_id(text, kind=kind, lang=lang)
    try:
        folder = entry_dir(voice, entry_id, root=root)
        sidecar = folder / f"{entry_id}.json"
        if not sidecar.is_file():
            return None
        data = _read_sidecar(sidecar)
        if data is None:
            return None
        for extension in AUDIO_EXTENSIONS:
            audio = folder / f"{entry_id}{extension}"
            if not audio.is_file() or audio.stat().st_size <= 0:
                continue
            # Touch on read so the pruner drops what nobody says any more,
            # not what happens to be oldest.
            with _quiet():
                os.utime(audio, None)
            return CacheEntry(
                id=entry_id,
                voice=voice,
                audio_path=audio,
                sidecar_path=sidecar,
                seconds=float(data.get("seconds") or 0.0),
                bytes=int(data.get("bytes") or audio.stat().st_size),
                model=str(data.get("model") or ""),
                marks=_marks_of(data),
            )
    except OSError as exc:
        logger.debug("speech cache lookup failed: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


class _quiet:
    """Swallow OSError in a small block without nesting try/except noise."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(exc_type, OSError)


def link_or_copy(source: Path, target: Path) -> None:
    """Hard-link *source* to *target*, falling back to a copy.

    A hard link costs nothing and keeps one recording one file even when it is
    handed to several places at once.  Different filesystem, or a system
    without links — copy, and say nothing about it.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        with _quiet():
            target.unlink()
    try:
        os.link(source, target)
        return
    except (OSError, AttributeError, NotImplementedError):
        shutil.copyfile(source, target)


def store(
    text: str,
    voice: str,
    audio_path: str | Path,
    *,
    kind: str = "reply",
    lang: str = "",
    model: str = "",
    seconds: float = 0.0,
    marks: Sequence[float] = (),
    root: Optional[Path] = None,
) -> Optional[CacheEntry]:
    """Put a freshly synthesized recording into the store.

    The audio file stays where the caller put it; the store takes its own hard
    link.  Returns the entry, or None when the store could not be written —
    which is not an error the caller has to handle, only an opportunity that
    was missed.
    """
    source = Path(audio_path)
    if not text or not source.is_file():
        return None
    extension = source.suffix.lower()
    if extension not in AUDIO_EXTENSIONS:
        return None
    entry_id = cache_id(text, kind=kind, lang=lang)
    try:
        folder = entry_dir(voice, entry_id, root=root)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{entry_id}{extension}"
        link_or_copy(source, target)
        size = target.stat().st_size
        sidecar = folder / f"{entry_id}.json"
        # The sidecar is written last: until it exists the entry reads as
        # absent, so a crash mid-write leaves a miss rather than a lie.
        payload = {
            "seconds": round(float(seconds or 0.0), 3),
            "bytes": size,
            "model": str(model or ""),
            "voice": voice,
            "kind": kind,
            "lang": lang,
            "marks": [round(float(value), 3) for value in marks],
        }
        sidecar.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        return CacheEntry(
            id=entry_id,
            voice=voice,
            audio_path=target,
            sidecar_path=sidecar,
            seconds=float(payload["seconds"]),
            bytes=size,
            model=str(model or ""),
            marks=tuple(float(value) for value in marks),
        )
    except OSError as exc:
        logger.debug("speech cache store failed: %s", exc)
        return None


def serve(entry: CacheEntry, destination: str | Path) -> Optional[str]:
    """Materialise a cached recording at *destination*.

    Returns the path actually written, or None when the link/copy failed and
    the caller should synthesize after all.
    """
    target = Path(destination)
    if target.suffix.lower() != entry.suffix:
        target = target.with_suffix(entry.suffix)
    try:
        if target.resolve() == entry.audio_path.resolve():
            return str(target)
    except OSError:
        pass
    try:
        link_or_copy(entry.audio_path, target)
    except OSError as exc:
        logger.debug("speech cache serve failed: %s", exc)
        return None
    return str(target)


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------


def iter_entries(*, root: Optional[Path] = None) -> Iterable[Tuple[Path, Path, int, float]]:
    """Yield ``(audio, sidecar, size, mtime)`` for every complete entry."""
    base = Path(root) if root is not None else cache_root()
    if not base.is_dir():
        return
    for audio in base.rglob("*"):
        if not audio.is_file() or audio.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        sidecar = audio.with_suffix(".json")
        if not sidecar.is_file():
            continue
        try:
            info = audio.stat()
        except OSError:
            continue
        yield audio, sidecar, info.st_size, info.st_mtime


def total_bytes(*, root: Optional[Path] = None) -> int:
    return sum(size for _, _, size, _ in iter_entries(root=root))


def prune(limit: Optional[int] = None, *, root: Optional[Path] = None) -> int:
    """Drop the least recently spoken entries until the store fits *limit*.

    Returns the number of bytes reclaimed.  Audio and sidecar go together —
    an orphaned half is worse than no entry, because it reads as a hit until
    something tries to play it.
    """
    ceiling = max_bytes() if limit is None else int(limit)
    if ceiling <= 0:
        return 0
    entries = sorted(iter_entries(root=root), key=lambda item: item[3])
    used = sum(item[2] for item in entries)
    freed = 0
    for audio, sidecar, size, _ in entries:
        if used - freed <= ceiling:
            break
        with _quiet():
            audio.unlink()
        with _quiet():
            sidecar.unlink()
        freed += size
    return freed
