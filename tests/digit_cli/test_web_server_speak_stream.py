"""/api/audio/speak-stream — desktop streaming TTS over WebSocket."""

from __future__ import annotations

import json
import time
from urllib.parse import urlencode

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from digit_cli import web_server


@pytest.fixture
def stream_client(monkeypatch, _isolate_digit_home):
    previous_auth_required = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.auth_required = False

    client = TestClient(web_server.app)
    try:
        yield client
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            close()
        if previous_auth_required is None:
            if hasattr(web_server.app.state, "auth_required"):
                delattr(web_server.app.state, "auth_required")
        else:
            web_server.app.state.auth_required = previous_auth_required


def _url(token: str | None = None) -> str:
    return f"/api/audio/speak-stream?{urlencode({'token': token or web_server._SESSION_TOKEN})}"


class _FakeStreamer:
    sample_rate = 24000
    channels = 1

    def __init__(self, chunks):
        self.chunks = chunks
        self.requests: list[str] = []

    def stream(self, text):
        self.requests.append(text)
        yield from self.chunks


def _patch_provider(monkeypatch, streamer, cap=4000):
    monkeypatch.setattr("tools.tts_streaming.resolve_streaming_provider", lambda cfg: streamer)
    monkeypatch.setattr("tools.tts_tool._load_tts_config", lambda: {})
    monkeypatch.setattr("tools.tts_tool._get_provider", lambda cfg: "fake")
    monkeypatch.setattr("tools.tts_tool._resolve_max_text_length", lambda provider, cfg: cap)






def test_streams_pcm_frames_then_end(stream_client, monkeypatch):
    streamer = _FakeStreamer([b"\x01\x02\x03\x04", b"\x05\x06"])
    _patch_provider(monkeypatch, streamer)

    with stream_client.websocket_connect(_url()) as conn:
        start = conn.receive_json()
        assert start == {"type": "start", "sample_rate": 24000, "channels": 1}

        conn.send_text(json.dumps({"text": "Hello there.", "done": True}))
        # The sentence announces itself just ahead of its own audio.
        assert conn.receive_json()["type"] == "mark"
        assert conn.receive_bytes() == b"\x01\x02\x03\x04"
        assert conn.receive_bytes() == b"\x05\x06"
        assert conn.receive_json() == {"type": "end"}

    assert streamer.requests == ["Hello there."]








def test_long_text_is_split_across_provider_requests(stream_client, monkeypatch):
    streamer = _FakeStreamer([b"\x00\x00"])
    _patch_provider(monkeypatch, streamer, cap=24)

    with stream_client.websocket_connect(_url()) as conn:
        assert conn.receive_json()["type"] == "start"
        conn.send_text(
            json.dumps(
                {"text": "First sentence here. Second sentence here. Third one.", "done": True}
            )
        )
        # One PCM frame per split piece, then end. Marks are interleaved —
        # one per sentence, not per split piece — and are counted separately.
        frames = 0
        marks = 0
        while True:
            message = conn.receive()
            if message.get("bytes") is not None:
                frames += 1
                continue
            payload = json.loads(message["text"])
            if payload.get("type") == "mark":
                marks += 1
                continue
            assert payload == {"type": "end"}
            break

    assert len(streamer.requests) > 1
    assert frames == len(streamer.requests)
    assert 0 < marks <= len(streamer.requests)
    # Nothing lost in the split: every sentence reached the provider.
    joined = " ".join(streamer.requests)
    for fragment in ("First sentence here.", "Second sentence here.", "Third one."):
        assert fragment in joined


def _drain(conn):
    """Collect ``(marks, pcm_frames)`` until the stream ends."""
    marks, frames = [], 0
    while True:
        message = conn.receive()
        if message.get("bytes") is not None:
            frames += 1
            continue
        payload = json.loads(message["text"])
        if payload.get("type") == "mark":
            marks.append(payload)
            continue
        assert payload["type"] == "end"
        return marks, frames


def test_mark_points_back_at_the_text_the_client_sent(stream_client, monkeypatch):
    """The highlight needs coordinates in the reply, not in the spoken script."""
    streamer = _FakeStreamer([b"\x00\x00"])
    _patch_provider(monkeypatch, streamer)
    reply = "The first sentence is here. The second sentence follows it."

    with stream_client.websocket_connect(_url()) as conn:
        assert conn.receive_json()["type"] == "start"
        conn.send_text(json.dumps({"text": reply, "done": True}))
        marks, _ = _drain(conn)

    assert [mark["index"] for mark in marks] == list(range(len(marks)))
    assert len(marks) == 2
    assert reply[marks[0]["start"]:marks[0]["end"]] == "The first sentence is here."
    assert reply[marks[1]["start"]:marks[1]["end"]] == "The second sentence follows it."


def test_marks_advance_through_a_reply_that_repeats_itself(stream_client, monkeypatch):
    streamer = _FakeStreamer([b"\x00\x00"])
    _patch_provider(monkeypatch, streamer)
    reply = "Repeat this line exactly. Something else here now. Repeat this line exactly."

    with stream_client.websocket_connect(_url()) as conn:
        assert conn.receive_json()["type"] == "start"
        conn.send_text(json.dumps({"text": reply, "done": True}))
        marks, _ = _drain(conn)

    starts = [mark["start"] for mark in marks if mark["start"] is not None]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)


def test_mark_carries_the_spoken_text_even_without_coordinates(stream_client, monkeypatch):
    """A sentence that cannot be traced is still announced — just not highlighted."""
    streamer = _FakeStreamer([b"\x00\x00"])
    _patch_provider(monkeypatch, streamer)

    with stream_client.websocket_connect(_url()) as conn:
        assert conn.receive_json()["type"] == "start"
        conn.send_text(json.dumps({"text": "A single spoken sentence here.", "done": True}))
        marks, _ = _drain(conn)

    assert marks[0]["text"]
    assert set(marks[0]) == {"type", "index", "start", "end", "text"}


def test_a_mark_precedes_the_audio_it_describes(stream_client, monkeypatch):
    """Order is the timing: the client hangs the highlight off the next buffer."""
    streamer = _FakeStreamer([b"\x00\x00"])
    _patch_provider(monkeypatch, streamer)

    order = []
    with stream_client.websocket_connect(_url()) as conn:
        assert conn.receive_json()["type"] == "start"
        conn.send_text(
            json.dumps({"text": "First sentence is here. Second sentence is here.", "done": True})
        )
        while True:
            message = conn.receive()
            if message.get("bytes") is not None:
                order.append("pcm")
                continue
            payload = json.loads(message["text"])
            if payload.get("type") == "mark":
                order.append("mark")
                continue
            break

    assert order == ["mark", "pcm", "mark", "pcm"]


def test_split_text_respects_cap_and_preserves_content():
    text = "Alpha beta. Gamma delta epsilon. Zeta eta theta iota kappa."
    pieces = web_server._split_text_for_speak_stream(text, 30)
    assert pieces
    assert all(len(piece) <= 30 for piece in pieces)
    joined = " ".join(pieces)
    for word in text.replace(".", "").split():
        assert word in joined


