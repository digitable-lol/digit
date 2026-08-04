"""The terminal's live "what is being spoken" line."""

import io
import math

import pytest

from digit_cli.speech_view import (
    DECAY,
    FRAME_INTERVAL,
    SpeechView,
    make_speech_view,
    view_enabled,
)


class _Tty(io.StringIO):
    """A StringIO that claims to be a terminal."""

    def isatty(self) -> bool:
        return True


def _tone(frequency=440.0, *, sample_rate=24000, count=1024, amplitude=12000):
    out = bytearray()
    for index in range(count):
        value = int(amplitude * math.sin(2 * math.pi * frequency * index / sample_rate))
        out += value.to_bytes(2, "little", signed=True)
    return bytes(out)


@pytest.fixture
def terminal(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("DIGIT_SPEECH_VIEW", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    return _Tty()


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestWhenItDraws:
    def test_a_terminal_that_takes_colour_gets_the_line(self, terminal):
        assert view_enabled(terminal) is True

    def test_redirected_output_gets_nothing(self, monkeypatch):
        monkeypatch.delenv("DIGIT_SPEECH_VIEW", raising=False)
        assert view_enabled(io.StringIO()) is False

    def test_no_color_means_no_line(self, terminal, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert view_enabled(terminal) is False

    def test_a_dumb_terminal_gets_nothing(self, terminal, monkeypatch):
        monkeypatch.setenv("TERM", "dumb")
        assert view_enabled(terminal) is False

    def test_an_unset_term_gets_nothing(self, terminal, monkeypatch):
        monkeypatch.delenv("TERM", raising=False)
        assert view_enabled(terminal) is False

    @pytest.mark.parametrize("value", ["0", "off", "false", "no"])
    def test_the_switch_can_turn_it_off(self, terminal, monkeypatch, value):
        monkeypatch.setenv("DIGIT_SPEECH_VIEW", value)
        assert view_enabled(terminal) is False

    def test_the_switch_can_force_it_on_where_it_would_be_off(self, monkeypatch):
        monkeypatch.setenv("DIGIT_SPEECH_VIEW", "1")
        monkeypatch.setenv("NO_COLOR", "1")
        assert view_enabled(io.StringIO()) is True

    def test_a_stream_that_cannot_be_asked_gets_nothing(self, monkeypatch):
        monkeypatch.delenv("DIGIT_SPEECH_VIEW", raising=False)

        class Hostile:
            def isatty(self):
                raise OSError("closed")

        assert view_enabled(Hostile()) is False


class TestDisabledViewIsStillAnObject:
    def test_every_call_is_accepted_and_writes_nothing(self):
        view = SpeechView(io.StringIO(), enabled=False)
        view.handle("cue", "Anything at all.")
        view.handle("pcm", _tone())
        view.render(force=True)
        view.close()
        assert view.enabled is False
        assert view._stream.getvalue() == ""

    def test_the_factory_always_returns_something(self, monkeypatch):
        monkeypatch.setenv("DIGIT_SPEECH_VIEW", "0")
        assert make_speech_view(io.StringIO()) is not None


class TestTheLine:
    def test_the_cue_appears_beside_the_bars(self, terminal):
        view = SpeechView(terminal, enabled=True)
        view.set_cue("The answer is forty two.")
        assert "The answer is forty two." in terminal.getvalue()

    def test_the_line_is_rewritten_in_place_not_appended(self, terminal):
        view = SpeechView(terminal, enabled=True, clock=_Clock())
        view.set_cue("First sentence here.")
        view.set_cue("Second sentence here.")
        assert terminal.getvalue().count("\x1b[2K") == 2
        assert "\n" not in terminal.getvalue()

    def test_closing_erases_the_line(self, terminal):
        view = SpeechView(terminal, enabled=True)
        view.set_cue("Something spoken.")
        view.close()
        assert terminal.getvalue().endswith("\r\x1b[2K")
        assert view.cue == ""

    def test_closing_an_undrawn_view_writes_nothing(self, terminal):
        SpeechView(terminal, enabled=True).close()
        assert terminal.getvalue() == ""

    def test_a_long_cue_is_trimmed_to_the_width(self, terminal):
        view = SpeechView(terminal, enabled=True, bands=4)
        view.set_cue("word " * 200)
        assert len(view.line(width=40)) <= 40
        assert view.line(width=40).endswith("…")

    def test_a_narrow_terminal_keeps_the_bars_and_drops_the_words(self, terminal):
        view = SpeechView(terminal, enabled=True, bands=12)
        view.set_cue("Some sentence that will not fit.")
        assert view.line(width=13) == view.line(width=13).rstrip()
        assert len(view.line(width=13)) <= 13

    def test_the_cue_is_flattened_to_one_line(self, terminal):
        view = SpeechView(terminal, enabled=True)
        view.set_cue("first\n\nsecond   third")
        assert view.cue == "first second third"

    def test_the_line_carries_one_character_per_band(self, terminal):
        view = SpeechView(terminal, enabled=True, bands=6)
        assert len(view.line(width=200).split(" ")[0]) == 6

    def test_a_broken_stream_disables_the_view_instead_of_raising(self, terminal):
        class Broken(_Tty):
            def write(self, text):
                raise OSError("gone")

        view = SpeechView(Broken(), enabled=True)
        view.set_cue("Anything at all.")
        assert view.enabled is False


class TestBars:
    def test_silence_leaves_the_bars_at_the_floor(self, terminal):
        view = SpeechView(terminal, enabled=True, bands=8)
        view.feed_pcm(b"\x00\x00" * 1024)
        assert view.levels == tuple([0.0] * 8)

    def test_audio_lifts_the_bars(self, terminal):
        view = SpeechView(terminal, enabled=True, bands=8, clock=_Clock())
        view.feed_pcm(_tone())
        assert max(view.levels) > 0.0

    def test_bars_fall_gently_rather_than_snapping_to_zero(self, terminal):
        clock = _Clock()
        view = SpeechView(terminal, enabled=True, bands=8, clock=clock)
        view.feed_pcm(_tone())
        loud = max(view.levels)
        clock.advance(FRAME_INTERVAL * 2)
        view.feed_pcm(b"\x00\x00" * 1024)
        assert 0 < max(view.levels) <= loud * DECAY + 1e-9

    def test_an_empty_buffer_changes_nothing(self, terminal):
        view = SpeechView(terminal, enabled=True, bands=8)
        view.feed_pcm(_tone())
        before = view.levels
        view.feed_pcm(b"")
        assert view.levels == before

    def test_closing_resets_the_bars(self, terminal):
        view = SpeechView(terminal, enabled=True, bands=8)
        view.feed_pcm(_tone())
        view.close()
        assert view.levels == tuple([0.0] * 8)


class TestFrameRate:
    def test_frames_are_throttled(self, terminal):
        clock = _Clock()
        view = SpeechView(terminal, enabled=True, clock=clock)
        for _ in range(10):
            view.feed_pcm(_tone())
        assert terminal.getvalue().count("\x1b[2K") == 1

    def test_a_new_cue_always_redraws_immediately(self, terminal):
        clock = _Clock()
        view = SpeechView(terminal, enabled=True, clock=clock)
        view.feed_pcm(_tone())
        view.set_cue("A brand new sentence.")
        assert terminal.getvalue().count("\x1b[2K") == 2

    def test_time_passing_lets_the_next_frame_through(self, terminal):
        clock = _Clock()
        view = SpeechView(terminal, enabled=True, clock=clock)
        view.feed_pcm(_tone())
        clock.advance(FRAME_INTERVAL * 2)
        view.feed_pcm(_tone())
        assert terminal.getvalue().count("\x1b[2K") == 2


class TestEventContract:
    def test_handle_matches_the_speech_callback_shape(self, terminal):
        view = SpeechView(terminal, enabled=True, clock=_Clock())
        view.handle("cue", "The sentence being spoken.")
        view.handle("pcm", _tone())
        assert view.cue == "The sentence being spoken."
        assert max(view.levels) > 0.0

    def test_an_unknown_event_is_ignored(self, terminal):
        view = SpeechView(terminal, enabled=True)
        view.handle("weather", {"sunny": True})
        assert view.cue == ""

    def test_a_pcm_event_carrying_the_wrong_type_is_ignored(self, terminal):
        view = SpeechView(terminal, enabled=True)
        view.handle("pcm", "not bytes")
        assert view.levels == tuple([0.0] * 12)

    def test_the_view_is_a_context_manager_that_cleans_up(self, terminal):
        with SpeechView(terminal, enabled=True) as view:
            view.set_cue("Spoken inside the block.")
        assert terminal.getvalue().endswith("\r\x1b[2K")
