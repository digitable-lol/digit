# Bundled wake-word models

`hey_digit.onnx` / `hey_digit.tflite` — the on-device "Hey Digit" hotword
model. This is the default detector for the wake word feature (see
`website/docs/user-guide/features/wake-word.md`); no training or setup is
required to say "hey digit".

- **Engine:** [openWakeWord](https://github.com/dscripka/openWakeWord) (Apache-2.0).
- **Architecture:** the same shape the upstream bundled model used — a 3-layer
  fully-connected net over openWakeWord's shared embeddings (input `[1, 16, 96]`,
  layer size 32), so it is a drop-in swap with no change to the always-on CPU
  cost.
- **Provenance:** trained with the openWakeWord training pipeline. Positive
  examples are synthetic speech from `en_US-libritts_r-medium` (904 speakers,
  SLERP-blended pairs); negatives are openWakeWord's ACAV100M feature set plus
  adversarial phrases, plus a deliberately weighted block of **"hey hermes"**
  so the model actively rejects the phrase it replaced. Augmented with MIT
  impulse responses and AudioSet ambience. Redistribution is permitted under
  the openWakeWord license.
- **Label:** the model registers as `hey_digit` (matches the filename). The
  phrase and the filename are one fact: what the detector fires on is baked
  into the weights, so neither may be changed without the other.
- **`.tflite` provenance:** converted from the `.onnx` in this directory by
  rebuilding the network in Keras from the ONNX weights, because
  openWakeWord's own `convert_onnx_to_tflite` depends on `onnx_tf`, which no
  longer installs against TensorFlow 2.20 / numpy 2. The converter was
  validated by round-tripping the *previous* bundled model: rebuilding
  `hey_hermes.onnx` reproduced upstream's own `hey_hermes.tflite` to within
  2.4e-07. For the pair shipped here, ONNX and TFLite agree to within
  **1.4e-06** over 400 inputs (random plus real embedding features) — which
  matters because `tools/wake_word.py:default_inference_framework` picks
  tflite on macOS ARM64 and onnx everywhere else, and
  `tests/tools/test_wake_word.py` guards the agreement.
- **Checksums:**
  `hey_digit.onnx` sha256 `230c01435c5ed1b7180f03d549ec2d77dbe5478d4e2e6b0fe4a52a796252a5f5`
  (205431 B), `hey_digit.tflite` sha256
  `795a75371f38bca5c91c66adf96dcb5d384ea0b5eb419a9b768230b7ef9eb91b` (207556 B).
- **Runtime:** openWakeWord's shared feature-extraction models (melspectrogram +
  embedding) are NOT bundled here — they are fetched once on first use by
  `tools/wake_word.py` via `openwakeword.utils.download_models()`.

## Measured behaviour

Held-out evaluation: 16 piper voices that training never saw (the whole
libritts family is excluded), replayed through the same rule the listener
applies — `sensitivity` as the per-frame threshold and `confirmation_frames`
consecutive frames before firing, plus the 2 s fire cooldown. Full table and
the reasoning for the working point:
`website/docs/user-guide/features/wake-word.md`.

At the shipped defaults (0.6 / 2 frames), on held-out voices:

| | clean | noisy (5–20 dB SNR + RIR) |
|---|---|---|
| "hey digit" missed | 6.2% (12/192) | 25.7% (247/960) |
| "hey hermes" false wake | 0.0% (0/896) | 0.1% (3/4480) |
| near-miss phrases false wake | 14.5% (93/640) | 20.2% (647/3200) |
| ordinary sentences false wake | 0.0% (0/256) | 0.0% (0/1280) |

False wakes on 10.7 h of real recorded speech: **0.19/hour**.

For comparison, on the same sets at the same working point, the retired
`hey_hermes` model missed **100%** of clean "hey digit" and recognised only
**57.7%** of clean "hey hermes" (42.3% missed). The swap therefore moved
detection from one phrase to the other *and* made it more reliable: 93.8%
recognised versus 57.7%.

Two variants were trained and rejected on measurement, not taste: a
96-unit layer with lighter negative weighting reached higher recall but 2.7
false wakes/hour at the same threshold (5x), and spreading the synthetic
positives over all 904 libritts speakers instead of the 34 the generator
reaches by default did not help generalisation (16.7% vs 14.6% clean miss).

## Retired: `hey_hermes`

The upstream model answered to "hey hermes". It no longer ships. Configs that
name it (`wake_word.openwakeword.model: hey_hermes`, and the bare forms
`hey hermes` / `hermes`) still load the bundled model rather than failing, and
log a warning saying the phrase is now "hey digit" —
`_LEGACY_MODEL_ALIASES` in `tools/wake_word.py`. To keep the old phrase, train
or obtain a `hey_hermes` model and point `wake_word.openwakeword.model` at its
path, or use the `sherpa` provider, which detects any typed phrase with no
model at all.

To use a different phrase, train your own model and point
`wake_word.openwakeword.model` at its path, or set a built-in openWakeWord name
(`hey_jarvis`, `alexa`, `hey_mycroft`, …). See the wake-word docs for the
training guide.
