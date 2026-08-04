"""``text_to_speech_tool`` and the content-addressed speech store.

The store is not a separate feature the user has to find: the tool consults
it before synthesizing and fills it afterwards. These tests pin the two
things that make that safe — a hit never calls the provider, and a broken
store never stops the provider.
"""

import json

import pytest

from tools import speech_cache, tts_tool


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "speech-store"
    monkeypatch.setenv("DIGIT_SPEECH_CACHE_DIR", str(root))
    monkeypatch.delenv("DIGIT_SPEECH_CACHE", raising=False)
    return root


@pytest.fixture
def edge(monkeypatch):
    """Stand in for the default provider, counting every synthesis."""
    calls = []

    async def _fake_edge(text, output_path, tts_config):
        calls.append((text, output_path))
        with open(output_path, "wb") as handle:
            handle.write(b"ID3" + text.encode("utf-8"))
        return output_path

    monkeypatch.setattr(tts_tool, "_generate_edge_tts", _fake_edge)
    monkeypatch.setattr(tts_tool, "_import_edge_tts", lambda: object())
    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {"provider": "edge"})
    return calls


def _speak(tmp_path, text, name="out.mp3"):
    return json.loads(tts_tool.text_to_speech_tool(text=text, output_path=str(tmp_path / name)))


class TestVoiceKey:
    def test_the_configured_voice_names_the_directory(self):
        key = tts_tool.speech_cache_voice("edge", {"edge": {"voice": "ru-RU-DmitryNeural"}})
        assert key == "edge-ru-ru-dmitryneural"

    def test_an_unconfigured_provider_still_names_its_default(self):
        """A release that changes the default must not reuse yesterday's audio."""
        key = tts_tool.speech_cache_voice("edge", {})
        assert tts_tool.DEFAULT_EDGE_VOICE.lower() in key

    def test_the_model_is_part_of_the_directory(self):
        key = tts_tool.speech_cache_voice("openai", {"openai": {"voice": "alloy"}})
        assert tts_tool.DEFAULT_OPENAI_MODEL in key

    def test_speed_is_part_of_the_directory(self):
        fast = tts_tool.speech_cache_voice("edge", {"speed": 1.5})
        normal = tts_tool.speech_cache_voice("edge", {})
        assert fast != normal

    def test_two_voices_never_share_a_directory(self):
        first = tts_tool.speech_cache_voice("edge", {"edge": {"voice": "a"}})
        second = tts_tool.speech_cache_voice("edge", {"edge": {"voice": "b"}})
        assert first != second


class TestFirstAndSecondListen:
    def test_the_first_listen_synthesizes_and_fills_the_store(self, tmp_path, store, edge):
        result = _speak(tmp_path, "Hello there, this is a spoken reply.")
        assert result["success"] is True
        assert len(edge) == 1
        assert list(store.rglob("*.json"))

    def test_the_second_listen_does_not_call_the_provider(self, tmp_path, store, edge):
        _speak(tmp_path, "Hello there, this is a spoken reply.")
        again = _speak(tmp_path, "Hello there, this is a spoken reply.", name="again.mp3")
        assert again["success"] is True
        assert len(edge) == 1

    def test_the_second_listen_produces_the_same_audio(self, tmp_path, store, edge):
        first = _speak(tmp_path, "Hello there, this is a spoken reply.")
        second = _speak(tmp_path, "Hello there, this is a spoken reply.", name="again.mp3")
        assert open(first["file_path"], "rb").read() == open(second["file_path"], "rb").read()

    def test_changed_text_is_synthesized_again(self, tmp_path, store, edge):
        _speak(tmp_path, "Hello there, this is a spoken reply.")
        _speak(tmp_path, "Hello there, this is a spoken reply!", name="two.mp3")
        assert len(edge) == 2

    def test_a_different_voice_is_synthesized_again(self, tmp_path, store, edge, monkeypatch):
        _speak(tmp_path, "Hello there, this is a spoken reply.")
        monkeypatch.setattr(
            tts_tool, "_load_tts_config", lambda: {"provider": "edge", "edge": {"voice": "other"}}
        )
        _speak(tmp_path, "Hello there, this is a spoken reply.", name="two.mp3")
        assert len(edge) == 2

    def test_the_stored_entry_is_addressed_by_the_spoken_text(self, tmp_path, store, edge):
        """Markdown is cleaned before hashing, so two spellings share one recording."""
        _speak(tmp_path, "**Hello** there, this is a spoken reply.")
        _speak(tmp_path, "Hello there, this is a spoken reply.", name="two.mp3")
        assert len(edge) == 1


class TestSwitchedOff:
    def test_nothing_is_stored_when_the_store_is_off(self, tmp_path, store, edge, monkeypatch):
        monkeypatch.setenv("DIGIT_SPEECH_CACHE", "0")
        _speak(tmp_path, "Hello there, this is a spoken reply.")
        _speak(tmp_path, "Hello there, this is a spoken reply.", name="two.mp3")
        assert len(edge) == 2
        assert not list(store.rglob("*.json"))


class TestTheStoreNeverBreaksSpeech:
    def test_a_store_that_cannot_be_read_still_speaks(self, tmp_path, edge, monkeypatch):
        monkeypatch.setattr(
            speech_cache, "lookup", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        assert _speak(tmp_path, "Hello there, this is a spoken reply.")["success"] is True

    def test_a_store_that_cannot_be_written_still_speaks(self, tmp_path, store, edge, monkeypatch):
        monkeypatch.setattr(
            speech_cache, "store", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("full"))
        )
        assert _speak(tmp_path, "Hello there, this is a spoken reply.")["success"] is True

    def test_a_hit_that_cannot_be_materialised_falls_back_to_synthesis(
        self, tmp_path, store, edge, monkeypatch
    ):
        _speak(tmp_path, "Hello there, this is a spoken reply.")
        monkeypatch.setattr(speech_cache, "serve", lambda *a, **k: None)
        _speak(tmp_path, "Hello there, this is a spoken reply.", name="two.mp3")
        assert len(edge) == 2
