# Spoken replies: where synthesis runs, what is kept, what is shown

Digit already spoke before this document existed — `tools/tts_tool.py` has
eleven built-in providers, `tools/tts_streaming.py` cuts sentences,
`gateway/streaming_tts_consumer.py` and `/api/audio/speak-stream` carry audio
to every surface. What was missing was the other half: **not paying twice for
the same sentence, and showing which sentence is being said.** This document
covers those two, and the decision that sits behind them.

## Where synthesis runs

Both places, and the choice is a provider name — not a fork in the code.

| | Local | Remote |
| --- | --- | --- |
| Cost of a second of speech | seconds of CPU | 60 ms of GPU (RTF 0.060) |
| Latency floor | none | one network round trip |
| Availability | always | needs the box to be up |
| Quality on Russian prose | thin (piper, kitten) | good (Qwen3-TTS) |

The measurement behind the right-hand column is `digit-ml/voicer-eval`:
Qwen3-TTS on an RTX 6000 Ada synthesizes at **RTF 0.060 with batching** —
a second of speech costs 60 ms of card, so a reply is spoken faster than it
can be heard. Single-item synthesis is 0.498; batches of 48 reach 0.045. A
chat reply is a handful of sentences, which is exactly the regime where
batching does not help, so the honest number for one Digit answer is closer
to 0.5 than to 0.045 — still twice real time, still ahead of the listener.

**The default stays local-first.** Not because local is better, but because
of the constraint one line below in the requirements: silence is a normal
state. A user's laptop has no GPU and often no network; a Digit that needs a
server before it will talk is a Digit that stops talking on a train. The
built-in local providers (`piper`, `kittentts`, `neutts`) speak without any
network at all, and the cloud providers (`edge` by default, `openai`,
`elevenlabs`, …) cover quality when there is a network.

**The GPU box is reached as a provider, not as a special case.** Two existing
extension points already do it, both without new code in Digit:

* `tts.providers.<name>: type: command` — any shell template that produces an
  audio file. An `ssh` one-liner makes the remote synthesizer a normal
  provider, with voice, model and speed substituted from config like any
  other:

  ```yaml
  tts:
    provider: qwen3-remote
    providers:
      qwen3-remote:
        type: command
        command: >-
          ssh gpu 'python -m voicer.say --voice {voice} --out /tmp/say.wav'
          < {input_path} && scp gpu:/tmp/say.wav {output_path}
        output_format: wav
  ```

  Placeholders are shell-quoted for their surrounding context, and the voice
  arrives as a value rather than being baked into the template — the same
  rule the store applies when it puts the voice in the path.
* `agent.tts_provider.TTSProvider` — a Python plugin for a backend that needs
  an SDK, streaming bytes, or a voice catalogue.

That is the whole answer to "where does synthesis live": **the surface never
knows.** It hands text to `text_to_speech_tool` or to the streaming pipeline;
which machine warms up is a config value. The cache below makes the remote
mode practical, because the second time a sentence is said it costs neither
GPU nor network.

### Which voice

Not the owner's. `Qwen3-TTS-12Hz-1.7B-VoiceDesign` is in the model cache on
the box next to the cloning checkpoints, and a described voice is exactly
what an assistant that is not pretending to be a person should have. Until
that choice is made, **no voice is compiled into anything**: the voice is a
config value, and it reaches the store as a *directory name*, never as part
of an entry's identity. Changing it builds a second set beside the first, so
two voices can be compared without re-synthesizing either.

## The store

`tools/speech_cache.py`. The name of an entry is the hash of the text it
speaks:

```
id = sha256("digit-speech\n" + kind + "\n" + lang + "\n" + text)[:20]

<root>/<voice>/<aa>/<id>.<ext>     the audio
<root>/<voice>/<aa>/<id>.json      seconds, bytes, model, voice, marks
```

This is the portal's reader-audio scheme (`courses/docs/sdd/reader-audio.md`)
applied to what the agent says, deliberately rather than coincidentally: one
rule in two places beats two rules that drift. The consequences are the same
ones it buys there.

* **There is nothing to invalidate.** "The file exists" and "the text has not
  changed" are one statement, because the filename *is* the hash of the text.
  No mtimes, no "re-synthesize everything, the config was touched".
* **The voice lives in the path.** Everything that changes the sound without
  changing the words — provider, model, voice id, speed — is folded into
  `<voice>`. Everything that changes the words is in the id. Nothing is in
  both.
* **An entry needs both files.** Audio without its sidecar is a half-written
  entry and reads as absent. The sidecar is written last, so a crash leaves a
  miss rather than a lie.
* **The shelf is the first two characters** — 256 of them, so no directory
  grows without bound.
* **A hard link, not a copy.** The recording stays where the caller asked for
  it; the store takes a link, with a copy as the fallback across filesystems.

Where it pays is not the repeated reply — replies rarely repeat verbatim —
but the repeated *listen*: pressing read-aloud again, replaying a message,
re-hearing an answer while scrolling. Those cost nothing and start
instantly. On a remote synthesizer they also cost no GPU and no round trip.

`marks` in the sidecar is the synthesizer's to fill, and stays empty until
one does. The reader-audio contract has the synthesizer return sentence
timings in seconds beside the audio, and the field is kept so a provider that
reports them drops straight in. Today's whole-file providers report nothing,
so it is `[]` — not an estimate. A guessed timing is worse than none: the
highlight would drift, which is exactly what an alignment-free design exists
to avoid. Live speech never needs this field; it takes its cues from the
stream instead, as described below.

Switches: `DIGIT_SPEECH_CACHE=0` and `tts.cache: false` turn it off,
`DIGIT_SPEECH_CACHE_DIR` moves it, `DIGIT_SPEECH_CACHE_MAX_MB` caps it (512
MB by default, least-recently-spoken pruned first — reading an entry keeps it
alive, so the pruner drops what nobody says any more rather than what is
merely old).

The store never raises. A read-only mount, a full disk, a sidecar someone
edited by hand — every one of them is a miss, and a miss is an ordinary
answer.

## Showing speech

Two things of different natures, and they are not built the same way.

**The highlight is a function.** It says where the agent is in its own reply,
which is what turns interrupting into a decision instead of a guess. It rests
on cues, not on alignment: synthesis is already per sentence on every path,
so the sentence boundaries are a by-product rather than something recovered
from audio afterwards.

**The bars are decoration.** They give a reply the texture of speech rather
than of a progress bar. They are still honest: every level is measured from
the audio on its way to the device, so a provider with no chunked API shows a
flat baseline instead of a pretend animation.

### The coordinate problem

The spoken script is not the displayed text. `prepare_spoken_text` removes
Markdown, drops emoji, expands symbols — so a surface cannot find the spoken
sentence by searching for it, and character offsets do not survive either.

What does survive is **the letters and digits, in order**. Both sides are
projected down to their alphanumerics; the sentence is located in that
projection; the result maps back. When the cleaner rewrote something in the
middle (`18 °C` → `18 degrees Celsius`), the longest surviving prefix and
suffix bracket the range instead. Below twelve characters of anchor there is
nothing left to be sure of, and the answer is *no back-pointer* rather than a
guess: an unhighlighted sentence is a normal state, a wrongly highlighted one
is a lie.

`tools/speech_marks.py` owns this — `plan_speech` for finished text,
`SourceTracker` for a reply that is still arriving.

### Timing without alignment

Nobody measures where a word falls inside an audio file. Each surface learns
the moment a sentence becomes audible from the machinery it already has:

* **CLI / TUI** — `stream_tts_to_speaker` gained a `speech_callback`. It
  fires `("cue", sentence)` from the *playback worker*, not from the
  synthesis queue: synthesis runs a sentence or two ahead, and a marker
  following the queue would point at words nobody has heard yet. It also
  fires `("pcm", chunk)` for every buffer handed to the device.
* **Desktop** — `/api/audio/speak-stream` sends a `mark` frame immediately
  before the PCM of each sentence. **Position in the stream is the timing**:
  the client already schedules every buffer, so it knows precisely when the
  next one becomes audible and hangs the cue off that same schedule.

### One idea, three surfaces, three shapes

| | CLI | TUI | Desktop |
| --- | --- | --- | --- |
| Highlight | one line rewritten in place under the answer | a row above the composer | painted over the rendered message |
| Bars | block characters, Goertzel over the PCM | block characters, levels sent as whole steps | canvas spectrum off an `AnalyserNode` |
| Off when | not a TTY, `NO_COLOR`, dumb `TERM`, `DIGIT_SPEECH_VIEW=0` | nothing is speaking | nothing is speaking, or no Custom Highlight API |

**CLI** (`digit_cli/speech_view.py`). A printed answer cannot be re-styled —
the bytes are in the scrollback, and repainting them would fight the pager,
the selection and the scroll position. So the terminal gets the only thing a
stream of text can offer: one line below the answer, rewritten in place,
carrying the current sentence and a row of bars, erased when the turn ends.
Bands come from a Goertzel filter over the int16 PCM in pure Python — numpy
is optional in this repo, and a decoration must never be the reason a reply
goes unspoken.

**TUI** (`ui-tui/src/components/speechBar.tsx`). The TUI repaints every
frame, so it can afford a row that lives in the chrome, directly above the
composer — the sentence being spoken is next to the box you would type in to
interrupt it. The backend quantises band energies to whole block steps before
sending them (`voice.speech` events at 12 Hz): the terminal has nine glyphs,
and anything finer is thrown away on arrival.

**Desktop** (`apps/desktop/src/lib/speech-highlight.ts`,
`src/components/voice/speech-bars.tsx`). Here the highlight can be truly
inline. It is painted with the CSS Custom Highlight API over the existing
text nodes rather than by wrapping them in elements: the message is rendered
markdown, and inserting spans would fight the renderer, break selection, and
reflow the paragraph mid-sentence. The bars are a canvas spectrum from an
`AnalyserNode` spliced into the playback graph, and the loop stops when the
window is hidden or unfocused.

## Voice in

Already there, and reused rather than rebuilt: `tools/voice_mode.py`
(recording, VAD, barge-in), `tools/transcription_tools.py` (seven STT
backends, faster-whisper locally by default), `tools/wake_word.py`,
`digit_cli/voice.py` for the TUI gateway. `faster-whisper-large-v3` is in the
cache on the GPU box for the same remote-provider treatment as TTS.

## Silence is a normal state

No synthesis, no network, no card, no terminal that takes colour — Digit
works in text with no error at all. Every piece above is additive:

* the store returns a miss and the provider is called as before;
* a missing provider leaves `check_tts_requirements()` false and nothing is
  spoken;
* `speech_callback` exceptions are swallowed inside the pipeline;
* `SpeechView` is a real object that draws nothing when the terminal cannot
  carry a line, so no caller branches on whether speech can be shown;
* an untraceable sentence is announced but not highlighted.

Test runs stay quiet by construction: `tests/conftest.py::_audio_playback_guard`
already neutralizes playback, nothing added here plays or synthesizes
anything, and `DIGIT_HOME` is per-test so the store is always empty and always
misses.

## Verification

```
scripts/run_tests.sh tests/tools/test_speech_marks.py
scripts/run_tests.sh tests/tools/test_speech_cache.py
scripts/run_tests.sh tests/tools/test_tts_speech_cache_wiring.py
scripts/run_tests.sh tests/digit_cli/test_speech_view.py
scripts/run_tests.sh tests/digit_cli/test_web_server_speak_stream.py
npm test --workspace ui-tui            # speechBar.test.tsx
npm run test:ui --workspace apps/desktop  # speech-highlight, speech-analyser, speech-cue
```
