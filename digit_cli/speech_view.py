"""The terminal's share of "Digit talks": one live line while it speaks.

A printed answer cannot be re-styled — the bytes are already in the
scrollback, and repainting them would fight the pager, the selection and the
scroll position. So the classic CLI shows what is being said the only way a
stream of text can: a single line below the answer that is rewritten in
place, carrying the sentence currently leaving the speaker and a row of
frequency bars driven by the audio itself.

That line is the function, not the decoration. Seeing which sentence is being
spoken is what lets someone cut in on the right thought instead of waiting
for the reply to end. The bars are the decoration, and they are honest: they
are computed from the PCM handed to the audio device, so when there is no
chunked audio (a provider without a streaming API) they simply stay flat and
only the sentence moves.

Silence is the normal state. No terminal, no colours, a dumb ``TERM``, a
redirected stdout, ``NO_COLOR``, ``DIGIT_SPEECH_VIEW=0`` — every one of them
turns this into a no-op object that still accepts every call. Nothing here
plays, records or synthesizes anything.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from typing import Any, Optional, TextIO

from tools.speech_marks import bar_levels, render_bars

#: Redraw ceiling. Bars are read, not counted, and a terminal repainted 60
#: times a second costs more than it shows.
FRAME_INTERVAL = 0.05

#: Bands in the row. Twelve fits beside a sentence on an 80-column terminal.
DEFAULT_BANDS = 12

#: Bars decay toward zero when no audio arrives, so a provider that goes
#: quiet between sentences does not leave a frozen row on screen.
DECAY = 0.55

#: Blocks for the row. Silence is a flat baseline rather than blank space:
#: the row has to stay visible while a provider without chunked audio speaks,
#: otherwise the line looks broken exactly when it is telling the truth.
VIEW_BLOCKS = "▁▂▃▄▅▆▇█"

_OFF_VALUES = {"0", "off", "false", "no", "none", "disabled"}
_ON_VALUES = {"1", "on", "true", "yes", "always"}

_CLEAR_LINE = "\r\x1b[2K"
_DIM = "\x1b[2m"
_BOLD = "\x1b[1m"
_RESET = "\x1b[0m"


def view_enabled(stream: Optional[TextIO] = None) -> bool:
    """Whether a live speech line can be drawn on *stream*.

    ``DIGIT_SPEECH_VIEW`` decides when it is set either way; otherwise the
    terminal decides, and it has to be a real one that accepts colour.
    """
    setting = (os.environ.get("DIGIT_SPEECH_VIEW") or "").strip().lower()
    if setting in _OFF_VALUES:
        return False
    forced = setting in _ON_VALUES

    target = stream if stream is not None else sys.stderr
    try:
        if not target.isatty():
            return forced
    except Exception:
        return False
    if forced:
        return True
    if os.environ.get("NO_COLOR"):
        return False
    if (os.environ.get("TERM") or "").strip().lower() in {"dumb", ""}:
        return False
    return True


def _terminal_width(default: int = 80) -> int:
    try:
        return max(24, shutil.get_terminal_size((default, 24)).columns)
    except Exception:
        return default


def _fit(text: str, width: int) -> str:
    """Trim *text* to *width* columns, ending in an ellipsis when cut."""
    flat = " ".join(str(text or "").split())
    if width <= 1:
        return ""
    if len(flat) <= width:
        return flat
    return flat[: width - 1] + "…"


class SpeechView:
    """Draws, and erases, the live speech line.

    Instances are cheap and always safe to construct: when the terminal
    cannot carry the line, every method is still callable and does nothing.
    The object is fed through :func:`handle`, whose signature is the
    ``speech_callback`` contract of ``tools.tts_tool.stream_tts_to_speaker``.
    """

    def __init__(
        self,
        stream: Optional[TextIO] = None,
        *,
        bands: int = DEFAULT_BANDS,
        sample_rate: int = 24000,
        enabled: Optional[bool] = None,
        clock=time.monotonic,
    ) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._bands = max(1, int(bands))
        self._sample_rate = int(sample_rate) or 24000
        self._clock = clock
        self._enabled = view_enabled(self._stream) if enabled is None else bool(enabled)
        self._cue = ""
        self._levels = [0.0] * self._bands
        # Never throttle the first frame: the line has to appear the moment
        # speech starts, not one interval later.
        self._last_frame = float("-inf")
        self._drawn = False

    # -- state -----------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def cue(self) -> str:
        """The sentence currently being spoken, as last announced."""
        return self._cue

    @property
    def levels(self) -> tuple:
        return tuple(self._levels)

    # -- input -----------------------------------------------------------

    def handle(self, event: str, payload: Any) -> None:
        """Consume one speech event. Unknown events are ignored on purpose."""
        if event == "cue":
            self.set_cue(str(payload or ""))
        elif event == "pcm":
            self.feed_pcm(payload if isinstance(payload, (bytes, bytearray)) else b"")

    def set_cue(self, text: str) -> None:
        self._cue = " ".join(str(text or "").split())
        self.render(force=True)

    def feed_pcm(self, pcm: bytes) -> None:
        if not pcm:
            return
        fresh = bar_levels(bytes(pcm), self._bands, sample_rate=self._sample_rate)
        # Rise instantly, fall gently: a meter that drops as fast as it climbs
        # reads as noise rather than as a voice.
        self._levels = [
            max(new, old * DECAY) for new, old in zip(fresh, self._levels)
        ]
        self.render()

    # -- output ----------------------------------------------------------

    def line(self, width: Optional[int] = None) -> str:
        """The line as it would be drawn, without any terminal control."""
        columns = width if width is not None else _terminal_width()
        bars = render_bars(self._levels, blocks=VIEW_BLOCKS)
        room = columns - len(bars) - 2
        cue = _fit(self._cue, room) if room > 1 else ""
        return f"{bars} {cue}".rstrip() if cue else bars

    def render(self, *, force: bool = False) -> None:
        if not self._enabled:
            return
        now = self._clock()
        if not force and now - self._last_frame < FRAME_INTERVAL:
            return
        self._last_frame = now
        columns = _terminal_width()
        bars = render_bars(self._levels, blocks=VIEW_BLOCKS)
        room = columns - len(bars) - 2
        cue = _fit(self._cue, room) if room > 1 else ""
        painted = f"{_BOLD}{bars}{_RESET}"
        if cue:
            painted = f"{painted} {_DIM}{cue}{_RESET}"
        self._write(f"{_CLEAR_LINE}{painted}")
        self._drawn = True

    def close(self) -> None:
        """Erase the line and leave the cursor where it was found."""
        if self._enabled and self._drawn:
            self._write(_CLEAR_LINE)
        self._drawn = False
        self._cue = ""
        self._levels = [0.0] * self._bands

    def _write(self, text: str) -> None:
        try:
            self._stream.write(text)
            self._stream.flush()
        except Exception:
            # A closed or broken stream ends the decoration, not the speech.
            self._enabled = False

    # -- context manager -------------------------------------------------

    def __enter__(self) -> "SpeechView":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False


def make_speech_view(stream: Optional[TextIO] = None, **kwargs: Any) -> SpeechView:
    """Build a view for the current terminal.

    Always returns an object — a disabled one when the terminal cannot carry
    the line — so callers never branch on whether speech can be shown.
    """
    return SpeechView(stream, **kwargs)
