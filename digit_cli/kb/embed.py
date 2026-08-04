"""Local-only ollama client for the knowledge base.

Hard constraint: the KB is an **offline** subsystem. Every call this module
makes goes to the ollama daemon on loopback and nowhere else — the base URL
is validated against a loopback allowlist on construction, so a stray
``OLLAMA_HOST=https://…`` in the environment fails loudly instead of
silently shipping the corpus to a third party.

Deliberately not routed through digit's provider abstraction: that layer
resolves models against configured cloud providers and would happily answer
a KB question from Anthropic or OpenRouter. ``kb`` talks to ollama
directly, via ``urllib`` from the standard library, so there is no code
path from a KB command to the internet.

Two endpoints are used:

* ``POST /api/embed``    — ``nomic-embed-text``, 768 dims.
* ``POST /api/generate`` — answer synthesis for ``kb ask``.

``nomic-embed-text`` is an asymmetric encoder: it expects a
``search_document:`` prefix on indexed passages and ``search_query:`` on
queries. This is not cosmetic — on the Russian corpus, dropping the
prefixes reordered results badly enough to rank a borscht recipe above the
correct SRE passage for the query "бюджет ошибок SLO" (0.7654 vs 0.7357).
With prefixes the ordering came out correct. See :data:`DOC_PREFIX`.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import List, Optional, Sequence

# Порт 11435 — это ollama с машины gpu, проброшенная сюда ssh-туннелем
# (юнит ``ollama-gpu-tunnel`` у пользователя). Локальная ollama на 11434 не
# используется: на этой машине нет ускорителя (Virtio GPU), и эмбеддинг на CPU
# здесь неприменим — замер на реальном чанке в 400 слов: восемь чанков не
# уложились в 100 с против 14.6 чанк/с на gpu, то есть весь корпус в 15 тыс.
# чанков это ~17 минут против суток.
# Адрес остаётся loopback, так что проверка ниже по-прежнему запрещает
# отправлять корпус наружу: туннель терминируется на нашей же машине.
DEFAULT_HOST = "http://127.0.0.1:11435"
DEFAULT_EMBED_MODEL = "nomic-embed-text"

DEFAULT_CHAT_MODEL = "qwen3.5:4b"
"""Answer synthesis model.

Must be a model the *tunnelled* endpoint actually serves — the gpu box's
catalogue is not the local one, and ``llama3.2:3b`` / ``qwen2.5-coder:7b``
are not on it (verified via ``/api/tags``).

4B rather than the available ``qwen2.5:14b-instruct`` because the GPU is
shared: the 14B failed to load with ``CUDA error: out of memory`` while
another tenant held the card, and during the same window every model above
~5B refused to load. A model that answers is worth more than a better
model that 500s. ``--model qwen2.5:14b-instruct`` when the card is free.

``kb doctor`` verifies presence, so a different deployment only has to
change this constant or pass the flag.
"""

CODE_CHAT_MODEL = "qwen2.5:14b-instruct"
"""Stronger alternative when the GPU has room (``--model`` to select it)."""

EMBED_DIM = 768

DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "

MAX_EMBED_CHARS = 2600
"""Hard per-input character budget, enforced client-side.

``nomic-embed-text`` is served with a 2048-token window, and ollama 0.23.3
**rejects** an over-long input (``400 the input length exceeds the context
length``) rather than truncating it — one oversized chunk fails the whole
batch and, before this cap existed, aborted an entire index run.

Measured against the live endpoint on Russian prose: 400 words / 2753 chars
was accepted, 500 words / 3415 chars was rejected. 2600 chars sits below
the accepted end of that bracket with margin for the provenance header and
for text that tokenises worse than the sample (Cyrillic runs 4-6 BPE tokens
per word in this vocabulary, which is why the limit is expressed in
characters rather than words).
"""


def _clip(text: str, limit: int = MAX_EMBED_CHARS) -> str:
    """Trim to ``limit`` characters on a word boundary.

    A backstop, not the primary mechanism — the chunker is sized so this
    rarely fires. Losing the tail of one outlier chunk is strictly better
    than the encoder refusing the batch it belongs to.
    """
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return cut[:space] if space > limit // 2 else cut

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}


class OfflineViolation(RuntimeError):
    """Raised when the configured endpoint is not on loopback."""


class EmbedError(RuntimeError):
    """The encoder could not be reached or returned something unusable."""


def _validate_local(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise OfflineViolation(f"KB endpoint must be http(s), got {url!r}")
    host = (parsed.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        raise OfflineViolation(
            f"KB refuses to talk to {host!r}: the knowledge base is offline by "
            f"design and only speaks to a loopback ollama. Unset OLLAMA_HOST or "
            f"point it at localhost."
        )
    return url.rstrip("/")


def resolve_host(explicit: Optional[str] = None) -> str:
    """Loopback ollama base URL, from ``--host`` → ``OLLAMA_HOST`` → default."""
    raw = explicit or os.environ.get("DIGIT_KB_OLLAMA_HOST") or os.environ.get(
        "OLLAMA_HOST"
    ) or DEFAULT_HOST
    if "://" not in raw:
        raw = f"http://{raw}"
    return _validate_local(raw)


class OllamaClient:
    """Thin, dependency-free client for the two endpoints the KB needs."""

    def __init__(
        self,
        host: Optional[str] = None,
        embed_model: str = DEFAULT_EMBED_MODEL,
        chat_model: str = DEFAULT_CHAT_MODEL,
        timeout: float = 600.0,
        num_ctx: Optional[int] = None,
    ) -> None:
        self.host = resolve_host(host)
        self.embed_model = embed_model
        self.chat_model = chat_model
        self.timeout = timeout
        self.num_ctx = num_ctx

    # -- plumbing ---------------------------------------------------------

    def _post(self, path: str, payload: dict, timeout: Optional[float] = None) -> dict:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.host}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            # No proxy handler: an http_proxy in the environment must not
            # be able to divert a "local" call off the machine.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=timeout or self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise EmbedError(f"ollama {path} returned {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise EmbedError(
                f"cannot reach ollama at {self.host} ({exc.reason}). "
                f"Start it with `ollama serve`."
            ) from exc

    # -- health -----------------------------------------------------------

    def version(self) -> str:
        req = urllib.request.Request(f"{self.host}/api/version")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8")).get("version", "?")
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            raise EmbedError(f"ollama not reachable at {self.host}: {exc}") from exc

    def has_model(self, name: str) -> bool:
        req = urllib.request.Request(f"{self.host}/api/tags")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=30) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
        wanted = name.split(":")[0]
        return any(
            m.get("name", "").split(":")[0] == wanted for m in tags.get("models", [])
        )

    # -- embeddings -------------------------------------------------------

    def embed(self, texts: Sequence[str], *, is_query: bool = False) -> List[List[float]]:
        """Embed a batch. Returns one 768-float vector per input, in order."""
        if not texts:
            return []
        prefix = QUERY_PREFIX if is_query else DOC_PREFIX
        payload: dict = {
            "model": self.embed_model,
            "input": [_clip(prefix + t) for t in texts],
        }
        if self.num_ctx:
            payload["options"] = {"num_ctx": int(self.num_ctx)}

        last: Optional[Exception] = None
        for attempt in range(3):
            try:
                data = self._post("/api/embed", payload)
                break
            except EmbedError as exc:
                last = exc
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
        else:  # pragma: no cover - loop always breaks or raises
            raise EmbedError(str(last))

        vectors = data.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise EmbedError(
                f"ollama returned {len(vectors) if isinstance(vectors, list) else '?'} "
                f"embeddings for {len(texts)} inputs"
            )
        dim = len(vectors[0])
        if dim != EMBED_DIM:
            raise EmbedError(
                f"expected {EMBED_DIM}-dim vectors from {self.embed_model}, got {dim}"
            )
        return vectors

    def embed_one(self, text: str, *, is_query: bool = False) -> List[float]:
        return self.embed([text], is_query=is_query)[0]

    # -- generation -------------------------------------------------------

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        model: Optional[str] = None,
        temperature: float = 0.1,
        num_predict: int = 700,
        num_ctx: int = 6144,
        think: bool = False,
    ) -> str:
        """One-shot completion. ``stream=False`` keeps parsing trivial.

        ``think=False`` matters for reasoning models. Observed with
        ``qwen3.5:4b``: with thinking enabled the whole ``num_predict``
        budget was consumed by the hidden reasoning block and ``response``
        came back an **empty string** — the command produced no answer at
        all while looking like a model failure. Grounded extraction from
        supplied passages does not need a chain of thought, so it is
        disabled and the budget goes to the answer.

        ``num_ctx`` is 6144 rather than 8192 because the KV cache is
        allocated up front, and this endpoint is a *shared* GPU that
        repeatedly refused to load models under memory pressure; the prompt
        budget (~9000 chars ≈ 3-4k tokens) fits comfortably.
        """
        payload = {
            "model": model or self.chat_model,
            "prompt": prompt,
            "stream": False,
            "think": think,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
                "num_ctx": num_ctx,
            },
        }
        if system:
            payload["system"] = system
        data = self._post("/api/generate", payload, timeout=self.timeout)
        text = (data.get("response") or "").strip()
        if not text:
            # Some builds still return the reasoning separately; prefer a
            # visible answer over silence.
            text = (data.get("thinking") or "").strip()
        return text
