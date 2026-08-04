"""The content-addressed store for synthesized speech."""

import json
import os

import pytest

from tools import speech_cache
from tools.speech_cache import (
    AUDIO_EXTENSIONS,
    CacheEntry,
    cache_id,
    enabled,
    iter_entries,
    lookup,
    max_bytes,
    prune,
    serve,
    store,
    total_bytes,
    voice_key,
)


@pytest.fixture
def root(tmp_path):
    return tmp_path / "speech"


@pytest.fixture
def recording(tmp_path):
    def _make(name="reply.mp3", payload=b"ID3fake-audio-bytes"):
        path = tmp_path / name
        path.write_bytes(payload)
        return path

    return _make


class TestIdentity:
    def test_the_name_is_the_hash_of_the_text(self):
        assert cache_id("hello") == cache_id("hello")
        assert cache_id("hello") != cache_id("hello ")

    def test_kind_and_language_are_part_of_the_name(self):
        assert cache_id("hello", kind="reply") != cache_id("hello", kind="notice")
        assert cache_id("hello", lang="ru") != cache_id("hello", lang="en")

    def test_the_name_is_short_and_hexadecimal(self):
        value = cache_id("hello")
        assert len(value) == 20
        assert set(value) <= set("0123456789abcdef")

    def test_the_namespace_keeps_us_off_the_portal_names(self):
        """Same algorithm, different prefix — two stores never share a filename."""
        import hashlib

        portal = hashlib.sha256(b"digitable-audio\nprose\nru\nhello").hexdigest()[:20]
        assert cache_id("hello", kind="prose", lang="ru") != portal


class TestVoiceKey:
    def test_voice_and_model_live_in_the_directory_name(self):
        assert voice_key("edge", "ru-RU-DmitryNeural") == "edge-ru-ru-dmitryneural"
        assert voice_key("openai", "alloy", "gpt-4o-mini-tts") == "openai-gpt-4o-mini-tts-alloy"

    def test_changing_the_voice_starts_a_second_set(self):
        assert voice_key("edge", "a") != voice_key("edge", "b")

    def test_speed_is_a_rendering_parameter_not_a_text_change(self):
        assert voice_key("edge", "a", speed=1.0) == voice_key("edge", "a")
        assert voice_key("edge", "a", speed=1.25) == "edge-a-s1-25"

    def test_a_nonsense_speed_does_not_break_the_key(self):
        assert voice_key("edge", "a", speed="fast") == voice_key("edge", "a")

    def test_provider_alone_is_enough(self):
        assert voice_key("edge") == "edge"
        assert voice_key("") == "tts"


class TestSwitches:
    def test_on_by_default(self, monkeypatch):
        monkeypatch.delenv("DIGIT_SPEECH_CACHE", raising=False)
        assert enabled() is True

    @pytest.mark.parametrize("value", ["0", "off", "false", "no"])
    def test_the_environment_can_turn_it_off(self, monkeypatch, value):
        monkeypatch.setenv("DIGIT_SPEECH_CACHE", value)
        assert enabled() is False

    def test_config_can_turn_it_off(self, monkeypatch):
        monkeypatch.delenv("DIGIT_SPEECH_CACHE", raising=False)
        assert enabled({"cache": False}) is False
        assert enabled({"cache": {"enabled": False}}) is False
        assert enabled({"cache": True}) is True

    def test_the_environment_wins_over_config(self, monkeypatch):
        monkeypatch.setenv("DIGIT_SPEECH_CACHE", "1")
        assert enabled({"cache": False}) is True

    def test_ceiling_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("DIGIT_SPEECH_CACHE_MAX_MB", "8")
        assert max_bytes() == 8 * 1024 * 1024

    def test_a_nonsense_ceiling_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv("DIGIT_SPEECH_CACHE_MAX_MB", "loud")
        assert max_bytes() == speech_cache.DEFAULT_MAX_BYTES

    def test_ceiling_from_config(self, monkeypatch):
        monkeypatch.delenv("DIGIT_SPEECH_CACHE_MAX_MB", raising=False)
        assert max_bytes({"cache": {"max_mb": 2}}) == 2 * 1024 * 1024

    def test_root_honours_the_environment(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DIGIT_SPEECH_CACHE_DIR", str(tmp_path / "elsewhere"))
        assert speech_cache.cache_root() == tmp_path / "elsewhere"

    def test_root_defaults_under_the_digit_home(self, monkeypatch):
        monkeypatch.delenv("DIGIT_SPEECH_CACHE_DIR", raising=False)
        assert speech_cache.cache_root().name in {"speech", "speech_cache"}


class TestStoreAndLookup:
    def test_a_miss_before_anything_is_stored(self, root):
        assert lookup("hello there", "edge-aria", root=root) is None

    def test_stored_text_is_found_again(self, root, recording):
        entry = store("hello there", "edge-aria", recording(), root=root)
        assert entry is not None
        found = lookup("hello there", "edge-aria", root=root)
        assert found is not None
        assert found.id == entry.id
        assert found.audio_path.read_bytes() == recording().read_bytes()

    def test_a_different_voice_is_a_different_entry(self, root, recording):
        store("hello there", "edge-aria", recording(), root=root)
        assert lookup("hello there", "edge-dmitry", root=root) is None

    def test_changed_text_is_a_miss_with_nothing_to_invalidate(self, root, recording):
        store("hello there", "edge-aria", recording(), root=root)
        assert lookup("hello there!", "edge-aria", root=root) is None

    def test_the_shelf_is_the_first_two_characters(self, root, recording):
        entry = store("hello there", "edge-aria", recording(), root=root)
        assert entry.audio_path.parent.name == entry.id[:2]
        assert entry.audio_path.parent.parent.name == "edge-aria"

    def test_the_sidecar_records_what_is_known(self, root, recording):
        entry = store(
            "hello there",
            "edge-aria",
            recording(),
            model="edge",
            seconds=1.5,
            marks=[0.0, 0.8],
            root=root,
        )
        payload = json.loads(entry.sidecar_path.read_text(encoding="utf-8"))
        assert payload["seconds"] == 1.5
        assert payload["marks"] == [0.0, 0.8]
        assert payload["voice"] == "edge-aria"
        assert payload["bytes"] == entry.audio_path.stat().st_size

    def test_marks_come_back_on_lookup(self, root, recording):
        store("hello there", "edge-aria", recording(), marks=[0.0, 1.2], root=root)
        assert lookup("hello there", "edge-aria", root=root).marks == (0.0, 1.2)

    def test_audio_without_its_sidecar_reads_as_absent(self, root, recording):
        entry = store("hello there", "edge-aria", recording(), root=root)
        entry.sidecar_path.unlink()
        assert lookup("hello there", "edge-aria", root=root) is None

    def test_a_corrupt_sidecar_reads_as_absent(self, root, recording):
        entry = store("hello there", "edge-aria", recording(), root=root)
        entry.sidecar_path.write_text("{not json", encoding="utf-8")
        assert lookup("hello there", "edge-aria", root=root) is None

    def test_an_empty_recording_reads_as_absent(self, root, recording):
        entry = store("hello there", "edge-aria", recording(), root=root)
        entry.audio_path.write_bytes(b"")
        assert lookup("hello there", "edge-aria", root=root) is None

    def test_unknown_marks_are_an_empty_tuple_not_a_guess(self, root, recording):
        entry = store("hello there", "edge-aria", recording(), root=root)
        payload = json.loads(entry.sidecar_path.read_text(encoding="utf-8"))
        payload["marks"] = "nonsense"
        entry.sidecar_path.write_text(json.dumps(payload), encoding="utf-8")
        assert lookup("hello there", "edge-aria", root=root).marks == ()

    def test_empty_text_is_never_stored(self, root, recording):
        assert store("", "edge-aria", recording(), root=root) is None

    def test_a_missing_file_is_never_stored(self, root, tmp_path):
        assert store("hello", "edge-aria", tmp_path / "gone.mp3", root=root) is None

    def test_an_unknown_format_is_never_stored(self, root, recording):
        assert store("hello", "edge-aria", recording("reply.aiff"), root=root) is None

    @pytest.mark.parametrize("extension", AUDIO_EXTENSIONS)
    def test_every_recognised_format_round_trips(self, root, recording, extension):
        store("hello there", "v", recording(f"reply{extension}"), root=root)
        assert lookup("hello there", "v", root=root).suffix == extension

    def test_storing_the_same_text_twice_keeps_one_entry(self, root, recording):
        store("hello there", "v", recording(), root=root)
        store("hello there", "v", recording(payload=b"ID3different"), root=root)
        assert len(list(iter_entries(root=root))) == 1

    def test_one_recording_is_one_file_not_a_copy(self, root, recording):
        source = recording()
        entry = store("hello there", "v", source, root=root)
        if hasattr(os, "link"):
            assert entry.audio_path.stat().st_ino == source.stat().st_ino


class TestServe:
    def test_a_hit_materialises_where_the_caller_asked(self, root, recording, tmp_path):
        store("hello there", "v", recording(), root=root)
        hit = lookup("hello there", "v", root=root)
        target = tmp_path / "out" / "spoken.mp3"
        target.parent.mkdir()
        assert serve(hit, target) == str(target)
        assert target.read_bytes() == recording().read_bytes()

    def test_the_extension_follows_the_stored_recording(self, root, recording, tmp_path):
        store("hello there", "v", recording("reply.wav"), root=root)
        hit = lookup("hello there", "v", root=root)
        served = serve(hit, tmp_path / "spoken.mp3")
        assert served.endswith(".wav")

    def test_serving_onto_itself_is_not_a_deletion(self, root, recording):
        store("hello there", "v", recording(), root=root)
        hit = lookup("hello there", "v", root=root)
        assert serve(hit, hit.audio_path) == str(hit.audio_path)
        assert hit.audio_path.is_file()


class TestHousekeeping:
    def test_total_counts_only_complete_entries(self, root, recording):
        entry = store("hello there", "v", recording(), root=root)
        assert total_bytes(root=root) == entry.bytes
        entry.sidecar_path.unlink()
        assert total_bytes(root=root) == 0

    def test_prune_drops_the_least_recently_spoken(self, root, recording):
        old = store("older line here", "v", recording("a.mp3", b"ID3" + b"x" * 40), root=root)
        new = store("newer line here", "v", recording("b.mp3", b"ID3" + b"y" * 40), root=root)
        os.utime(old.audio_path, (1, 1))
        freed = prune(50, root=root)
        assert freed > 0
        assert not old.audio_path.exists()
        assert not old.sidecar_path.exists()
        assert new.audio_path.exists()

    def test_prune_keeps_everything_under_the_ceiling(self, root, recording):
        entry = store("hello there", "v", recording(), root=root)
        assert prune(10 * 1024 * 1024, root=root) == 0
        assert entry.audio_path.exists()

    def test_a_zero_ceiling_means_no_pruning_at_all(self, root, recording):
        entry = store("hello there", "v", recording(), root=root)
        assert prune(0, root=root) == 0
        assert entry.audio_path.exists()

    def test_pruning_an_absent_store_is_quiet(self, tmp_path):
        assert prune(1, root=tmp_path / "never-made") == 0

    def test_reading_an_entry_keeps_it_alive(self, root, recording):
        entry = store("hello there", "v", recording(), root=root)
        os.utime(entry.audio_path, (1, 1))
        lookup("hello there", "v", root=root)
        assert entry.audio_path.stat().st_mtime > 1


class TestNeverRaises:
    def test_lookup_on_an_unreadable_root_is_a_miss(self, tmp_path):
        blocker = tmp_path / "file-not-a-dir"
        blocker.write_text("x")
        assert lookup("hello", "v", root=blocker) is None

    def test_store_on_an_unwritable_root_returns_none(self, tmp_path, recording):
        blocker = tmp_path / "file-not-a-dir"
        blocker.write_text("x")
        assert store("hello", "v", recording(), root=blocker) is None

    def test_serve_to_an_impossible_target_returns_none(self, root, recording, tmp_path):
        store("hello there", "v", recording(), root=root)
        hit = lookup("hello there", "v", root=root)
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        assert serve(hit, blocker / "nested" / "out.mp3") is None

    def test_lookup_of_nothing_is_a_miss(self, root):
        assert lookup("", "v", root=root) is None


class TestEntry:
    def test_suffix_reports_the_stored_format(self, tmp_path):
        entry = CacheEntry(
            id="a" * 20,
            voice="v",
            audio_path=tmp_path / "a.ogg",
            sidecar_path=tmp_path / "a.json",
        )
        assert entry.suffix == ".ogg"
