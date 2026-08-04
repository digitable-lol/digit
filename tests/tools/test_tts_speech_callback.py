"""``speech_callback``: what is audible right now, not what is queued.

The highlight is only worth having if it points at the sentence being heard.
Synthesis runs ahead of playback by design, so the interesting property is not
"a cue was emitted" but "the cue for sentence two did not arrive while
sentence one was still playing".

Nothing here makes a sound: the streamer is a fake, the audio device is made
unavailable on purpose, and the one playback call left is stubbed.
"""

import queue
import threading

import pytest

from tools import tts_tool


class _FakeStreamer:
    sample_rate = 24000
    channels = 1

    def __init__(self):
        self.requests = []

    def stream(self, text):
        self.requests.append(text)
        yield b"\x00\x01" * 16


@pytest.fixture
def speaker(monkeypatch):
    """Wire a fake streamer, a dead audio device, and a silent player."""
    streamer = _FakeStreamer()
    played = []

    monkeypatch.setattr("tools.tts_streaming.resolve_streaming_provider", lambda cfg, preferred=None: streamer)
    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {})
    monkeypatch.setattr(tts_tool, "_get_provider", lambda cfg: "fake")
    monkeypatch.setattr(tts_tool, "_resolve_max_text_length", lambda provider, cfg: 4000)

    # No sounddevice → the worker falls back to the tempfile player, which is
    # the one call that would otherwise reach the speakers.
    def _no_device():
        raise ImportError("no audio device in tests")

    monkeypatch.setattr(tts_tool, "_import_sounddevice", _no_device)

    import tools.voice_mode as voice_mode

    def _silent_play(path, *args, **kwargs):
        played.append(path)
        return True

    monkeypatch.setattr(voice_mode, "play_audio_file", _silent_play)

    return streamer, played


def _speak(text, speech_callback=None, timeout=20.0):
    """Run the pipeline over *text* and return once it has drained."""
    text_queue: queue.Queue = queue.Queue()
    stop = threading.Event()
    done = threading.Event()

    thread = threading.Thread(
        target=tts_tool.stream_tts_to_speaker,
        args=(text_queue, stop, done),
        kwargs={"speech_callback": speech_callback},
        daemon=True,
    )
    thread.start()
    text_queue.put(text)
    text_queue.put(None)
    thread.join(timeout=timeout)
    assert not thread.is_alive(), "streaming TTS pipeline did not finish"
    return done


TWO_SENTENCES = "The first sentence is long enough to stand alone. The second sentence is too."


class TestCuesFollowPlayback:
    def test_every_spoken_sentence_announces_itself_once(self, speaker):
        events = []
        _speak(TWO_SENTENCES, lambda kind, payload: events.append((kind, payload)))

        cues = [payload for kind, payload in events if kind == "cue"]
        assert len(cues) == 2
        assert "first sentence" in cues[0]
        assert "second sentence" in cues[1]

    def test_a_cue_never_runs_ahead_of_the_sound(self, speaker):
        """Synthesis pipelines a sentence ahead; the marker must not."""
        _, played = speaker
        order = []

        def observe(kind, payload):
            if kind == "cue":
                order.append("cue")

        import tools.voice_mode as voice_mode

        original = voice_mode.play_audio_file

        def _watch(path, *args, **kwargs):
            order.append("play")
            return original(path, *args, **kwargs)

        voice_mode.play_audio_file = _watch
        try:
            _speak(TWO_SENTENCES, observe)
        finally:
            voice_mode.play_audio_file = original

        assert order == ["cue", "play", "cue", "play"]
        assert len(played) == 2

    def test_the_pipeline_still_finishes_without_any_observer(self, speaker):
        done = _speak(TWO_SENTENCES)
        assert done.is_set()

    def test_a_broken_observer_does_not_stop_the_speech(self, speaker):
        streamer, played = speaker

        def hostile(kind, payload):
            raise RuntimeError("the decoration exploded")

        done = _speak(TWO_SENTENCES, hostile)

        assert done.is_set()
        assert len(played) == 2
        assert len(streamer.requests) == 2

    def test_nothing_is_announced_for_an_empty_reply(self, speaker):
        events = []
        _speak("   ", lambda kind, payload: events.append(kind))

        assert events == []
