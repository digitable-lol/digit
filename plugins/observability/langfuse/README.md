# Langfuse Observability Plugin

This plugin ships bundled with Digit but is **opt-in** — it only loads when
you explicitly enable it.

## Enable

Pick one:

```bash
# Interactive: walks you through credentials + SDK install + enable
digit tools  # → Langfuse Observability

# Manual
pip install langfuse
digit plugins enable observability/langfuse
```

## Required credentials

Set these in `~/.digit/.env` (or via `digit tools`):

```bash
DIGIT_LANGFUSE_PUBLIC_KEY=pk-lf-...
DIGIT_LANGFUSE_SECRET_KEY=sk-lf-...
DIGIT_LANGFUSE_BASE_URL=https://cloud.langfuse.com   # or your self-hosted URL
```

Without the SDK or credentials the hooks no-op silently — the plugin fails
open.

## Verify

```bash
digit plugins list                 # observability/langfuse should show "enabled"
digit chat -q "hello"              # then check Langfuse for a "Digit turn" trace
```

## Optional tuning

```bash
DIGIT_LANGFUSE_ENV=production       # environment tag
DIGIT_LANGFUSE_RELEASE=v1.0.0       # release tag
DIGIT_LANGFUSE_SAMPLE_RATE=0.5      # sample 50% of traces
DIGIT_LANGFUSE_MAX_CHARS=12000      # max chars per field (default: 12000)
DIGIT_LANGFUSE_DEBUG=true           # verbose plugin logging
```

## Disable

```bash
digit plugins disable observability/langfuse
```
