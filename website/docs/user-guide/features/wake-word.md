---
sidebar_position: 11
title: "Wake Word"
description: "Hands-free 'Hey Digit' wake word — start a voice session by speaking, the 'Hey Siri' way"
---

# Wake Word ("Hey Digit")

The wake word turns Digit into a hands-free assistant across the CLI, TUI, and
desktop app: with one setting on, Digit listens in the background for a spoken
trigger phrase. Say it, and Digit starts a fresh session, opens the microphone,
captures your command via the normal [voice pipeline](/user-guide/features/voice-mode),
and answers — exactly like "Hey Siri" or "Alexa". Use `surface` to pick which
one listens.

Detection runs **entirely on-device**. The always-on listener only watches for
the wake phrase; no audio leaves your machine until you actually speak a command
to the agent.

## How it works

1. With `wake_word.enabled: true` (or after `/wake on`), a lightweight hotword
   detector listens on your configured input device, or the process default
   microphone when `wake_word.input_device` is unset.
2. When it hears the wake phrase it pauses itself (freeing the mic), starts a new
   session, and records one utterance with voice mode's silence detection.
3. Your speech is transcribed and sent to the agent. After it replies, the
   listener resumes automatically and waits for the next wake word.

It is **off by default** — nothing listens until you turn it on.

On the desktop app, a hands-free voice conversation can be ended by simply
**saying "stop"** (or "never mind", "goodbye", "cancel", "that's all") — the
spoken command ends the conversation instead of being sent to the agent. Only a
whole-utterance stop command matches, so a real request like "stop the docker
container" still goes through normally.

## Engines

| Engine | Cost | API key | Notes |
|--------|------|---------|-------|
| **openWakeWord** (default) | Free | None | Local ONNX models. Ships a bundled **"hey digit"** model (default); also supports `hey_jarvis`, `alexa`, `hey_mycroft`, … and custom models |
| **sherpa** | Free | None | **Open vocabulary** — detects ANY typed phrase with zero training. Small English model auto-downloads on first use (~13 MB) |
| **Porcupine** | Free tier / paid | `PORCUPINE_ACCESS_KEY` | Picovoice engine; built-in keywords + custom `.ppn` files |

By default the phrase is **"hey digit"** — a model for it ships with Digit, so
it works out of the box with no training. (On first use, openWakeWord downloads
its shared feature-extraction models — a small one-time fetch.)

Both are lazy-installed the first time you enable the wake word (desktop
installs made with `--include-desktop` pre-install them, so the ear works
instantly). To install ahead of time:

```bash
cd ~/.digit/digit && uv pip install -e ".[wake]"
```

## Quick start

```bash
# In an interactive `digit` session:
/wake on        # start listening (installs the engine on first use)
/wake status    # show phrase, provider, and state
/wake off       # stop listening
```

In the desktop app, click the ear icon in the composer.

The toggle IS the setting: turning the wake word on or off — via `/wake` or the
desktop ear button — also writes `wake_word.enabled` to `~/.digit/config.yaml`,
so your choice persists across sessions. You can also flip it by hand:

```yaml
wake_word:
  enabled: true
```

## Configuration

```yaml
wake_word:
  enabled: false
  surface: auto               # eligible surface: "auto" | "cli" | "tui" | "gui"
  input_device: null           # PortAudio input index or device-name substring; null = process default
  provider: openwakeword      # "openwakeword" (free, local) | "sherpa" (free, any phrase) | "porcupine"
  phrase: "hey digit"         # cosmetic label only — detection is keyed by the model/keyword below
  sensitivity: 0.6            # 0.0-1.0 — higher = stricter (fewer false triggers), consistent across all engines
  confirmation_frames: 2      # openWakeWord only — consecutive over-threshold frames required to fire
  start_new_session: true     # start a fresh session on wake vs. continue the current one
  openwakeword:
    model: hey_digit          # bundled default; OR a built-in name OR a path to a custom .onnx/.tflite
    inference_framework: ""   # "" (auto) | "onnx" | "tflite"
  porcupine:
    keyword: jarvis           # built-in keyword OR path to a custom .ppn
```

`sensitivity`, `phrase`, and `start_new_session` apply to both engines. The
`openwakeword` and `porcupine` blocks select the actual detection model.

`input_device` is passed directly to the wake listener's PortAudio
(`sounddevice`) stream. Use either a numeric device index or an unambiguous
device-name substring. This setting only changes wake-word capture; desktop
push-to-talk still uses the desktop application's microphone path.

### Reducing false triggers on ambient speech

openWakeWord scores one short (~80ms) audio frame at a time, so a stray phoneme
in background conversation can occasionally spike a single frame over the
threshold and fire the wake word unintentionally. Two knobs control this:

- **`confirmation_frames`** (default `2`, openWakeWord only) — how many
  *consecutive* over-threshold frames are required before the wake fires. A real
  "hey digit" holds a high score across several frames; an ambient blip spikes
  just one. Raise it (e.g. `3`–`4`) if you still get false triggers in a noisy
  room; the cost is a few tens of milliseconds of extra latency and more missed
  utterances — see the measured table below. `1` restores the old
  fire-on-first-frame behavior.
- **`sensitivity`** (default `0.6`) — the detection threshold, `0.0`–`1.0`.
  Higher is stricter (fewer false triggers). This direction is consistent across
  **all** engines — for openWakeWord it's the raw per-frame score threshold, for
  sherpa it maps onto the keyword threshold, and for Porcupine it's inverted
  internally so "higher = stricter" holds there too. The `0.6` default sits
  above openWakeWord's permissive `0.5` baseline, which let near-misses like
  "hey hor" through; raise toward `0.8` if you still get false fires, lower it
  if real "hey digit" utterances are missed.

The `sherpa` and `porcupine` engines decode the whole phrase internally, so they
don't have the single-frame-spike problem and ignore `confirmation_frames`
(but they still honor `sensitivity`).

### What the defaults cost, measured

The bundled `hey_digit` model was evaluated on speech from **16 synthetic voices
that training never saw** (the whole libritts family, which training used, is
excluded), replayed through the same rule the listener applies. "Noisy" mixes in
held-out ambient audio at 5–20 dB SNR and convolves half the clips with a room
impulse response. False wakes per hour come from **10.7 hours of real recorded
speech** (openWakeWord's false-positive validation set), with the listener's 2 s
cooldown applied — so it counts interruptions, not frames.

At the shipped defaults (`sensitivity: 0.6`, `confirmation_frames: 2`):

| What you say | Clean | Noisy |
|---|---|---|
| "hey digit" — **missed** (you say it, nothing happens) | 6.2% | 25.6% |
| "hey hermes" — false wake (the retired phrase) | 0.0% | 0.1% |
| near-misses ("hey digital", "hey gadget", "hey did it", …) — false wake | 14.5% | 20.2% |
| ordinary sentences — false wake | 0.0% | 0.1% |

**False wakes on real speech: 0.19 per hour** — roughly one every five hours of
continuous conversation in the room.

Both directions have a price and neither is zero. `confirmation_frames: 3` cuts
false wakes to 0.09/hour and near-miss fires to 7.5%, but more than doubles the
missed-phrase rate, to 14.6% clean / 34.2% noisy — at which point the wake word
starts feeling broken. `2` is where the phrase works reliably in a quiet room
while false wakes stay at the rate the previous bundled model had at *its* own
default (0.19/hour). Raise to `3`–`4` if your room is noisy and you would rather
repeat yourself than be interrupted; drop `sensitivity` to `0.4` (keeping
`confirmation_frames: 2`) for 3.6% clean / 21.9% noisy misses at the cost of
0.47 false wakes per hour.

The noisy column is the honest number for a room with a TV on. If false wakes
matter more than convenience, the `sherpa` engine decodes the whole phrase and
does not have this trade-off shape.

`inference_framework` picks the openWakeWord backend. Leave it empty (the
default) to let Digit choose per platform: **tflite on Apple Silicon**, onnx
everywhere else. openWakeWord's onnx backend returns near-zero scores on macOS
ARM64 ([openWakeWord#336](https://github.com/dscripka/openWakeWord/issues/336)),
so a listener pinned to `onnx` there will arm, show as listening, and never
fire. The tflite backend needs `ai-edge-litert` on macOS, which Digit installs
on demand alongside the other wake-word deps.

### Surfaces (CLI, TUI, GUI)

The wake word works in all three Digit surfaces, and `surface` picks which one
owns the listener and opens the new session when it fires:

| `surface` | Behavior |
|-----------|----------|
| `auto` (default) | All local surfaces are eligible; the first one to arm owns the listener. |
| `cli` | Only the classic `digit` CLI. |
| `tui` | Only `digit --tui`. |
| `gui` | Only the desktop app. |

The detector is on-device and single-mic, so only one surface listens at a time,
including when Digit surfaces run in separate processes. Ownership is sticky:
the first eligible claimant keeps the listener until it stops, disconnects, or
its process exits. Digit does not silently fail over to another open surface.
Set `surface` when you want to pin ownership instead of using first-claim wins.
The TUI and desktop GUI share the same Python backend (`tui_gateway`), which
runs the detector server-side and yields the mic to voice capture while a
command records.

## Using a different phrase

"Hey Digit" works out of the box — the bundled openWakeWord model
(`model: hey_digit`) is the default. To wake on something else, the easiest
path is the open-vocabulary engine:

### Option A — sherpa (any phrase, zero training)

Type the phrase you want; it's tokenized at runtime — "hey coder",
"computer", "wake up neo", anything:

```yaml
wake_word:
  enabled: true
  provider: sherpa
  phrase: "hey coder"        # detection key — just type your phrase
```

The small English KWS model (~13 MB) downloads once on first use. Each
profile can set its own phrase — "hey \<profile\>" for every profile you run.

### Waking a specific profile (desktop)

With the sherpa engine, ONE listener can wake ANY profile. Every profile
whose config has `wake_word.enabled: true` is enrolled automatically; its
phrase defaults to `hey <profile name>` when unset. Say a profile's phrase
and the desktop app live-switches to that profile, opens a fresh session
there, and starts hands-free voice:

- "hey digit" → default profile
- "hey coder" → the `coder` profile
- "hey trader" → the `trader` profile

Set `wake_word.profile_routing: false` on the listener's profile to opt out
and listen only for its own phrase. The CLI and TUI are single-profile
processes: a wake phrase belonging to another profile prints the switch
command (`digit -p <profile>`) instead of routing.

Names are matched acoustically by their English subword sounds: two-word
phrases with distinct, 2+ syllable names work best. Very short names, heavy
non-English phonology, or two profiles with similar-sounding names will
degrade accuracy — tune per-profile `sensitivity` if needed.

### Option B — openWakeWord (free, trained model)

Name a built-in model (`hey_jarvis`, `alexa`, `hey_mycroft`, …), or train a
custom model (≈75–90 min on a free/Colab GPU) for maximum robustness, drop
the `.onnx` file somewhere, and reference it:

```yaml
wake_word:
  enabled: true
  provider: openwakeword
  phrase: "computer"
  openwakeword:
    model: ~/.digit/wakewords/computer.onnx   # or a built-in name like hey_jarvis
```

Training references:

- [openWakeWord](https://github.com/dscripka/openWakeWord)
- [2026 training Colab](https://github.com/alfiedennen/openwakeword-colab-2026)

:::tip Pick a distinctive phrase
Wake phrases that don't collide with everyday speech generalize best. Two
syllables with an uncommon word ("digit" qualifies) beat common words like
"hello" or "stop".
:::

### Option C — Porcupine (custom keyword in seconds)

Create a "Hey Digit" keyword in the [Picovoice Console](https://console.picovoice.ai/),
download the `.ppn`, and:

```yaml
wake_word:
  enabled: true
  provider: porcupine
  phrase: "hey digit"
  porcupine:
    keyword: ~/.digit/wakewords/hey_digit.ppn
```

Set your access key in `~/.digit/.env`:

```bash
PORCUPINE_ACCESS_KEY=your-key-here
```

## Requirements

- A working microphone and the `sounddevice` + `numpy` audio stack (shared with
  voice mode).
- An STT provider for transcribing the spoken command — local `faster-whisper`
  works out of the box; see [Voice Mode](/user-guide/features/voice-mode) for the
  full provider list.
- A TTS provider for speaking the reply (the default `edge-tts` works with no
  key). The wake flow is fully hands-free, so the toggle refuses to arm until
  both STT and TTS are ready — `digit tools` (Voice section) sets them up.
- The wake engine deps (auto-installed, or `digit[wake]`).

`/wake status` reports exactly what's missing if the listener won't start.

### "Listening" but never wakes (macOS)

macOS grants microphone access per **process**. STT working in the desktop app
proves the *renderer* has mic access — the wake listener runs in the Python
*backend*, which needs its own grant. Without it, CoreAudio hands the backend a
"working" stream that only ever delivers silence, so the ear shows listening
but the phrase never fires. Digit detects this (`/wake status` shows
"mic delivers only silence"; the desktop ear tooltip carries the same hint).
Fix: System Settings → Privacy & Security → Microphone → enable the Digit
backend (it may appear as your terminal, `python`, or Digit), then toggle the
wake word off and on.

### "Listening" but receives silence (Windows)

Desktop push-to-talk and wake-word capture use different microphone paths.
Push-to-talk uses the desktop application's browser capture, while the
wake-word listener opens a PortAudio stream in the Python backend. One can work
while the other selects a silent or unusable Windows input.

`/wake status` reports the selected input device and Windows audio host API.
When it reports silence, set `wake_word.input_device` to the numeric index or an
unambiguous name of the working PortAudio input, then toggle the wake word:

```bash
digit config set wake_word.input_device "Microphone Array"
```

Use `null` to return to the process default:

```bash
digit config set wake_word.input_device null
```

## Notes & limits

- **Local surfaces only.** The wake word runs in the CLI, TUI, and desktop GUI —
  wherever a local microphone is available. It does not run in the messaging
  gateway (Telegram, Discord, …), which has no mic.
- **One mic at a time.** The detector releases the microphone while a command is
  recording and reclaims it once the turn ends, so it won't fight voice capture.
- **Privacy.** Hotword detection is local. Set `sensitivity` higher if you get
  false triggers, lower if it misses you.
