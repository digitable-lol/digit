"""Speech cues: cutting, back-pointing, timing and the band meter."""

import re

import pytest

from tools import speech_marks
from tools.speech_marks import (
    MIN_CUE_CHARS,
    SourceTracker,
    bar_levels,
    band_centres,
    marks_from_durations,
    pcm_seconds,
    plan_speech,
    render_bars,
    split_spoken,
)


def _pcm(samples):
    out = bytearray()
    for value in samples:
        clamped = max(-32768, min(32767, int(value)))
        out += clamped.to_bytes(2, "little", signed=True)
    return bytes(out)


def _tone(frequency, *, sample_rate=24000, count=2048, amplitude=12000):
    import math

    return _pcm(
        amplitude * math.sin(2 * math.pi * frequency * index / sample_rate)
        for index in range(count)
    )


class TestBoundaryContract:
    def test_regex_matches_the_streaming_cutter(self):
        """The duplicate exists so this module stays import-light; drift is a bug.

        ``tools.tts_streaming`` owns the cutter the speaker pipeline uses. If
        the two spellings diverge, the highlight would point at sentences the
        synthesizer never cut.
        """
        from tools.tts_streaming import SENTENCE_BOUNDARY_RE

        assert speech_marks.SENTENCE_BOUNDARY_RE.pattern == SENTENCE_BOUNDARY_RE.pattern

    def test_short_fragment_rides_along_with_the_next_sentence(self):
        ranges = split_spoken("Ha! This one is long enough to stand on its own.")
        assert len(ranges) == 1

    def test_min_cue_matches_the_chunker(self):
        from tools.tts_streaming import SentenceChunker

        assert SentenceChunker().min_len == MIN_CUE_CHARS


class TestPlanSpeech:
    def test_empty_text_plans_nothing(self):
        plan = plan_speech("")
        assert plan.spoken == ""
        assert plan.cues == ()

    def test_whitespace_only_plans_nothing(self):
        assert plan_speech("   \n\t ").cues == ()

    def test_cues_cover_the_spoken_script_without_gaps(self):
        text = (
            "The first sentence is comfortably long. The second one is also "
            "long enough to stand alone. And a third for good measure here."
        )
        plan = plan_speech(text)
        assert len(plan.cues) >= 2
        assert plan.cues[0].spoken_start == 0
        for previous, following in zip(plan.cues, plan.cues[1:]):
            assert previous.spoken_end == following.spoken_start
        assert plan.cues[-1].spoken_end == len(plan.spoken)

    def test_back_pointer_lands_on_the_source_sentence(self):
        text = (
            "The **first** sentence carries some emphasis here. "
            "The second sentence has `code` inside of it."
        )
        plan = plan_speech(text)
        assert len(plan.cues) == 2
        first, second = plan.cues
        assert first.has_source
        assert second.has_source
        assert text[first.source_start:first.source_end].startswith("The **first**")
        assert "second sentence" in text[second.source_start:second.source_end]

    def test_back_pointers_move_forward_only(self):
        text = (
            "Repeat this phrase for the reader. Something else entirely now. "
            "Repeat this phrase for the reader."
        )
        plan = plan_speech(text)
        starts = [cue.source_start for cue in plan.cues if cue.has_source]
        assert starts == sorted(starts)
        assert len(set(starts)) == len(starts)

    def test_a_sentence_that_survives_markdown_removal_is_still_found(self):
        text = "- A bulleted line that is quite long indeed for a list item."
        plan = plan_speech(text)
        assert plan.cues[0].has_source
        located = text[plan.cues[0].source_start:plan.cues[0].source_end]
        assert "bulleted line" in located

    def test_symbol_expansion_still_anchors(self):
        """"18 °C" becomes "18 degrees Celsius" — the anchors carry the match."""
        text = (
            "Tomorrow the temperature outside will reach 18 °C in the shade. "
            "Bring a jacket for the evening walk regardless."
        )
        plan = plan_speech(text)
        assert "degrees Celsius" in plan.spoken
        assert plan.cues[0].has_source
        assert "18" in text[plan.cues[0].source_start:plan.cues[0].source_end]

    def test_unfindable_cue_reports_no_source_rather_than_guessing(self):
        cue_text = "1 & 2"
        plan = plan_speech(cue_text)
        # The cleaner turns "&" into " and ", and there is no anchor long
        # enough to recover from that in a five-character sentence.
        assert plan.cues[0].source_start is None or plan.cues[0].has_source

    def test_think_blocks_never_reach_a_cue(self):
        text = "<think>secret reasoning</think>The visible answer is right here."
        plan = plan_speech(text)
        assert "secret" not in plan.spoken
        assert all("secret" not in cue.text for cue in plan.cues)

    def test_max_chars_is_forwarded_to_the_cleaner(self):
        plan = plan_speech("A sentence long enough to be cut in half." * 4, max_chars=20)
        assert len(plan.spoken) <= 20

    def test_cue_at_picks_the_sentence_playing_now(self):
        text = (
            "The first sentence is comfortably long. The second one is also "
            "long enough to stand alone."
        )
        plan = plan_speech(text)
        marks = marks_from_durations([2.0, 3.0])
        assert plan.cue_at(0.0, marks) is plan.cues[0]
        assert plan.cue_at(1.9, marks) is plan.cues[0]
        assert plan.cue_at(2.5, marks) is plan.cues[1]
        assert plan.cue_at(99.0, marks) is plan.cues[1]


class TestSourceTracker:
    def test_locates_sentences_in_a_growing_reply(self):
        tracker = SourceTracker()
        tracker.feed("The first sentence is comfortably long. ")
        first = tracker.locate("The first sentence is comfortably long.")
        tracker.feed("The second one arrives a moment later.")
        second = tracker.locate("The second one arrives a moment later.")
        assert first == (0, 39)
        assert tracker.source[second[0]:second[1]].startswith("The second one")

    def test_unknown_sentence_leaves_the_cursor_alone(self):
        tracker = SourceTracker()
        tracker.feed("The first sentence is comfortably long. And a second one here.")
        assert tracker.locate("nothing like this was ever said") == (None, None)
        found = tracker.locate("The first sentence is comfortably long.")
        assert found[0] == 0

    def test_empty_feed_is_harmless(self):
        tracker = SourceTracker()
        tracker.feed("")
        assert tracker.source == ""
        assert tracker.locate("anything at all") == (None, None)


class TestMarks:
    def test_marks_are_cumulative_starts(self):
        assert marks_from_durations([1.5, 2.0, 0.5]) == (0.0, 1.5, 3.5)

    def test_empty_input_yields_no_marks(self):
        assert marks_from_durations([]) == ()

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -3.0, None, "x"])
    def test_a_broken_duration_does_not_move_later_marks_backwards(self, bad):
        marks = marks_from_durations([1.0, bad, 2.0])
        assert marks == (0.0, 1.0, 1.0)

    def test_pcm_seconds(self):
        assert pcm_seconds(48000, sample_rate=24000) == 1.0
        assert pcm_seconds(0) == 0.0
        assert pcm_seconds(48000, sample_rate=0) == 0.0


class TestBands:
    def test_centres_ascend_and_stay_under_nyquist(self):
        centres = band_centres(12, sample_rate=24000)
        assert len(centres) == 12
        assert centres == tuple(sorted(centres))
        assert centres[-1] < 12000

    def test_silence_reads_as_zero(self):
        assert bar_levels(_pcm([0] * 1024)) == tuple([0.0] * 12)

    def test_no_audio_reads_as_zero(self):
        assert bar_levels(b"") == tuple([0.0] * 12)
        assert bar_levels(b"\x01") == tuple([0.0] * 12)

    def test_levels_stay_in_range(self):
        levels = bar_levels(_tone(440), bands=8)
        assert len(levels) == 8
        assert all(0.0 <= level <= 1.0 for level in levels)

    def test_a_tone_lights_its_own_band_hardest(self):
        bands = 12
        centres = band_centres(bands)
        target = 5
        levels = bar_levels(_tone(centres[target]), bands=bands)
        assert levels.index(max(levels)) == target

    def test_odd_byte_count_is_tolerated(self):
        assert len(bar_levels(_tone(440) + b"\x7f", bands=6)) == 6


class TestRenderBars:
    def test_empty_levels_render_nothing(self):
        assert render_bars([]) == ""

    def test_row_is_one_character_per_band(self):
        assert len(render_bars([0.0, 0.5, 1.0])) == 3

    def test_floor_and_ceiling_pick_the_end_blocks(self):
        row = render_bars([0.0, 1.0])
        assert row[0] == " "
        assert row[1] == "█"

    def test_row_is_monotonic_in_level(self):
        row = render_bars([0.0, 0.25, 0.5, 0.75, 1.0])
        assert list(row) == sorted(row, key=" ▁▂▃▄▅▆▇█".index)

    @pytest.mark.parametrize("bad", [float("nan"), None, "x", -1.0, 2.0])
    def test_a_broken_level_still_renders_one_character(self, bad):
        assert len(render_bars([bad])) == 1

    def test_row_has_no_control_characters(self):
        assert not re.search(r"[\x00-\x1f]", render_bars([0.3, 0.6, 0.9]))
