# Model presets for Digit on Apple Silicon

Digit does not bundle model weights. A model preset selects a runtime and model identifier, while the user downloads weights directly from the model publisher or runtime catalog. This keeps the application small and makes license acceptance explicit.

Digit ships four Ollama aliases: `/model digit-local-small` (Qwen3.5 2B), `/model digit-local` (Qwen3.5 4B), `/model digit-local-plus` (Qwen3.5 9B), and `/model digit-gemma` (Gemma 3 4B). They connect only to the local endpoint and never download or launch a model implicitly.

## Runtime order

1. **Ollama** — default local setup and the least-friction OpenAI-compatible endpoint at `http://localhost:11434/v1`.
2. **LM Studio** — visual model management and a first-class Hermes provider.
3. **MLX / MLX-VLM server** — performance-oriented Apple Silicon path for text and multimodal models.
4. **Bring-your-own endpoint** — an optional user-configured fallback when the local model lacks context, vision, or reliable tool calling.

## Recommended presets

The memory bands below are conservative engineering starting points for 4-bit weights plus runtime/KV-cache headroom, not guaranteed limits. Long context and images increase memory use substantially.

| Unified memory | Default text preset | Multimodal preset | Intended use |
|---|---|---|---|
| 8 GB M1 | 2B instruct, 4-bit | Qwen3.5 2B, short context | chat, portal routing, small edits |
| 16 GB M1/M2 | 4B–8B instruct, 4-bit | Qwen3.5 4B or Gemma 3 4B | everyday local assistant, screenshots |
| 24–32 GB M1 Pro/Max+ | 8B–14B instruct, 4-bit | Qwen3.5 9B or Gemma 3 12B | stronger coding, vision, longer sessions |
| 48–64 GB+ | 14B–32B instruct, quantized | Qwen3.5 27B or Gemma 3 27B where supported | heavier agent and document work |

Start with **Qwen3.5 4B** for an Apache-2.0 multimodal preset, or **Gemma 3 4B** when its license fits the distribution. Both accept images; Qwen3.5 is explicitly trained as a unified vision-language model and reports agent/tool-use evaluations. For consequential actions and difficult code changes, optionally configure a stronger endpoint you control. **Qwen3-VL 4B** remains a useful compatibility preset where a runtime has not yet added Qwen3.5.

## Local endpoint setup

Run `digit model`, select **Custom endpoint**, and use:

```text
API base URL: http://localhost:11434/v1
API key: ollama
Model name: <the exact local model id>
Context length: <the server's actual configured context>
```

For LM Studio, choose its dedicated provider in `digit model`. An MLX-VLM server can be connected as a custom OpenAI-compatible endpoint when its selected server mode implements the endpoints required by Digit.

## Preset acceptance gates

A model is advertised as a Digit preset only after it passes:

- Russian and English instruction following;
- multi-turn tool calling without emitting fake tool results;
- JSON/schema adherence for three consecutive runs;
- portal/course retrieval with citations;
- code edit plus test repair in a fixture repository;
- image understanding for multimodal presets;
- a 30-minute memory and thermal stability run on the target Apple machine.

Do not equate “loads on M1” with “works as an agent.” Models that fail tool-use gates remain chat-only presets.

## Primary references

- Apple MLX and MLX-LM: https://github.com/ml-explore/mlx and https://github.com/ml-explore/mlx-lm
- MLX-VLM: https://github.com/Blaizzy/mlx-vlm
- Qwen3.5 4B model card: https://huggingface.co/Qwen/Qwen3.5-4B
- Qwen3-VL 4B compatibility model card: https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct
- Gemma 3 model card: https://ai.google.dev/gemma/docs/core/model_card_3
- Hermes local-model setup: `website/docs/reference/faq.md`
