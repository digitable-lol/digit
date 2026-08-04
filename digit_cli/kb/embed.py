"""Encoder and generator clients for the knowledge base.

Hard constraint: the KB never ships the corpus to a third party. Every
endpoint this module talks to is validated on construction against an
explicit allowlist — a stray ``OLLAMA_HOST=https://api.somewhere.com`` in
the environment fails loudly instead of silently uploading the corpus.

Deliberately not routed through digit's provider abstraction: that layer
resolves models against configured cloud providers and would happily answer
a KB question from Anthropic or OpenRouter. ``kb`` speaks to its endpoints
directly, via ``urllib`` from the standard library.

Two endpoints, and they are no longer the same host
---------------------------------------------------
* **embedding** — by default ``https://embed.gpu.local-xyz.ru/v1/embeddings``
  (mistral.rs, OpenAI-compatible, ``Qwen/Qwen3-Embedding-8B`` BF16, 4096
  dims).
* **generation** — ``POST /api/generate`` on the loopback ollama at
  ``127.0.0.1:11435`` (an ssh tunnel to the same gpu box).

Why a non-loopback address is allowed now, and why that is not a hole
--------------------------------------------------------------------
``embed.gpu.local-xyz.ru`` is *our own* machine. It is not on the public
internet in any meaningful sense: nginx in front of it enforces an ACL that
rejects every source address except ours with 403 (and fail2ban bans the
caller for 24h), and the box is the same gpu host whose ollama this module
already used through an ssh tunnel. So the corpus goes exactly where it
went before — the only thing that changed is that the last hop is TLS to
our own nginx instead of an ssh tunnel to our own ollama.

What the allowlist protects is unchanged: an *arbitrary* host still cannot
be configured. :data:`TRUSTED_REMOTE_HOSTS` is matched **exactly**, never
by suffix — the ``*.gpu.local-xyz.ru`` DNS record is a wildcard, so
``endswith(".gpu.local-xyz.ru")`` would accept names nobody has ever
provisioned. Non-loopback hosts additionally must be ``https``, so the
corpus is never sent in clear text.

Encoder profiles
----------------
The dimension, the wire protocol and the prefix convention are properties
of the *model*, not global constants — mixing a 768-dim vector into a
4096-dim slab is a silent wrong answer, so they travel together in an
:class:`EmbedProfile` and the dimension the index was built with is
recorded in ``meta`` (see :func:`digit_cli.kb.store.assert_compatible`).

Prefixes are measured, not assumed. ``nomic-embed-text`` is an asymmetric
encoder needing ``search_document:``/``search_query:``. Qwen3-Embedding is
not: it wants raw passages and an ``Instruct: …\\nQuery: …`` envelope on
the query side only. Measured on 268 real corpus chunks and the four
acceptance queries, best-relevant vs best-unrelated cosine:

===========================  ==========  ==========  =========
scheme                       relevant    unrelated   gap
===========================  ==========  ==========  =========
doc=raw / query=raw          0.7559      0.5563      +0.1996
doc=raw / query=Instruct     0.7705      0.5376      **+0.2329**
doc=nomic / query=nomic      0.7720      0.5902      +0.1818
===========================  ==========  ==========  =========

and on the out-of-corpus probe "как ухаживать за орхидеей" the *ceiling*
over unrelated chunks was 0.3965 (raw query) vs **0.3128** (Instruct) — the
Instruct envelope both raises real hits and pushes irrelevant ones down,
so it is what :data:`QWEN3_EMBED` uses.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------

# Port 11435 is the gpu box's ollama, forwarded here by an ssh tunnel (the
# user's ``ollama-gpu-tunnel`` unit). The local ollama on 11434 is not used:
# this machine has no accelerator (Virtio GPU) and generation on CPU is
# unusable here.
DEFAULT_HOST = "http://127.0.0.1:11435"
"""Ollama base URL — used for *generation* and for ollama-flavoured embedding."""

DEFAULT_EMBED_HOST = "https://embed.gpu.local-xyz.ru"
"""OpenAI-compatible embedding server (mistral.rs) on our gpu box."""

TRUSTED_REMOTE_HOSTS = frozenset({
    "embed.gpu.local-xyz.ru",
    "rerank.gpu.local-xyz.ru",
})
"""Non-loopback hosts the KB may talk to. Matched **exactly** — see module docstring.

These are our own machines behind an IP-ACL'd nginx. Adding a host here is
a deliberate act; suffix or wildcard matching is specifically avoided
because the DNS zone is a wildcard and would otherwise let a typo resolve
to something nobody provisioned.
"""

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"})


# --------------------------------------------------------------------------
# Encoder profiles
# --------------------------------------------------------------------------

QWEN3_INSTRUCT = (
    "Instruct: Given a technical question in Russian, retrieve passages "
    "from an engineering knowledge base that answer it\nQuery: "
)


@dataclass(frozen=True)
class EmbedProfile:
    """Everything about an encoder that must not drift from its slab.

    ``name`` is the identity recorded in ``meta.embed_model`` and checked on
    every search; ``wire_model`` is what actually goes into the request body.
    They differ only if a server insists on its own naming — this endpoint
    accepts the full ``Qwen/Qwen3-Embedding-8B`` (verified: any other string
    is rejected 422), so the identity is honest rather than the generic
    ``"default"`` alias the server also answers to. Storing ``"default"``
    would have made the compatibility check a no-op the day a different
    model is served on the same URL.
    """

    name: str
    wire_model: str
    api: str            # "ollama" (/api/embed) | "openai" (/v1/embeddings)
    dim: int
    doc_prefix: str
    query_prefix: str
    max_chars: int
    token_budget: int
    remote: bool = False
    """True when the encoder lives off-box and needs its own host."""


NOMIC_EMBED = EmbedProfile(
    name="nomic-embed-text",
    wire_model="nomic-embed-text",
    api="ollama",
    dim=768,
    doc_prefix="search_document: ",
    query_prefix="search_query: ",
    # ollama 0.23.3 *rejects* an over-long input (400 "the input length
    # exceeds the context length") rather than truncating, and one oversized
    # chunk failed a whole index run before this cap existed. Measured on
    # Russian prose: 400 words / 2753 chars accepted, 500 / 3415 rejected.
    max_chars=2600,
    # ollama accepted 8 × 2600-char inputs (~8240 estimated tokens) as a
    # single /api/embed call throughout the pre-migration index runs, so the
    # fallback path keeps that request size. Budgeting lower would quadruple
    # the round trips on a path that is already the slow one.
    token_budget=8000,
)

QWEN3_EMBED = EmbedProfile(
    name="Qwen/Qwen3-Embedding-8B",
    wire_model="Qwen/Qwen3-Embedding-8B",
    api="openai",
    dim=4096,
    doc_prefix="",
    query_prefix=QWEN3_INSTRUCT,
    # 32k context: the largest chunk in the corpus is 3947 chars, so nothing
    # is ever clipped. The cap is a guard against a pathological input, not
    # a routine truncation the way nomic's was.
    max_chars=8000,
    token_budget=6000,
    remote=True,
)

PROFILES: Dict[str, EmbedProfile] = {
    p.name: p for p in (QWEN3_EMBED, NOMIC_EMBED)
}
#: Convenience aliases so ``--embed-model qwen3`` works.
PROFILE_ALIASES: Dict[str, str] = {
    "qwen3": QWEN3_EMBED.name,
    "qwen3-embedding": QWEN3_EMBED.name,
    "qwen3-embedding-8b": QWEN3_EMBED.name,
    "default": QWEN3_EMBED.name,
    "nomic": NOMIC_EMBED.name,
    "nomic-embed-text:latest": NOMIC_EMBED.name,
}

DEFAULT_EMBED_MODEL = QWEN3_EMBED.name

DEFAULT_CHAT_MODEL = "qwen2.5:14b-instruct"
"""Answer synthesis model.

Must be a model the tunnelled ollama actually serves — the gpu box's
catalogue is not the local one.

Still 4B rather than something larger, and now for a sharper reason than
before: the embedding server *is* the memory pressure. Measured with the
new stack up (``nvidia-smi`` on gpu): 49140 MiB total, 43787 MiB used,
**4752 MiB free** — Qwen3-Embedding-8B and Qwen3-Reranker-4B in BF16 hold
most of the card. ``qwen3.5:4b`` is 3.39 GB and fits; the next model up in
the catalogue, ``qwen2.5:14b-instruct``, is 8.99 GB and does not.

``kb doctor`` verifies presence, so a different deployment only has to
change this constant or pass the flag.
"""

CODE_CHAT_MODEL = "qwen2.5:14b-instruct"
"""Stronger alternative for when the card is free (``--model`` to select it).

Does **not** fit alongside the embedding server; use it only after checking
``nvidia-smi``, or expect ``CUDA error: out of memory``.
"""

MAX_EMBED_CHARS = QWEN3_EMBED.max_chars
"""Default per-input character budget (see :attr:`EmbedProfile.max_chars`)."""

_CHARS_PER_TOKEN = 0.45
"""Token estimate for request sizing. Measured: 2600 chars of Russian prose
tokenised to 1030 tokens (0.396 tok/char); 0.45 adds headroom so the
estimate errs towards *smaller* batches."""


def resolve_profile(model: Optional[str]) -> EmbedProfile:
    """Look up an :class:`EmbedProfile` by name or alias."""
    if not model:
        return PROFILES[DEFAULT_EMBED_MODEL]
    if model in PROFILES:
        return PROFILES[model]
    alias = PROFILE_ALIASES.get(model.lower())
    if alias:
        return PROFILES[alias]
    raise EmbedError(
        f"unknown embedding model {model!r}. Known: "
        + ", ".join(sorted(PROFILES)) + ". A new encoder needs an EmbedProfile "
        "(dimension and prefix convention are not guessable)."
    )


# --------------------------------------------------------------------------
# Errors and endpoint validation
# --------------------------------------------------------------------------


class OfflineViolation(RuntimeError):
    """Raised when the configured endpoint is not on the allowlist."""


class EmbedError(RuntimeError):
    """The encoder could not be reached or returned something unusable."""


def _validate_endpoint(url: str) -> str:
    """Allow loopback (http/https) and exactly-listed trusted hosts (https)."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise OfflineViolation(f"KB endpoint must be http(s), got {url!r}")
    host = (parsed.hostname or "").lower()
    if host in _LOOPBACK_HOSTS:
        return url.rstrip("/")
    if host in TRUSTED_REMOTE_HOSTS:
        if parsed.scheme != "https":
            raise OfflineViolation(
                f"KB refuses to send the corpus to {host!r} over plain HTTP; "
                f"use https://{host}."
            )
        return url.rstrip("/")
    raise OfflineViolation(
        f"KB refuses to talk to {host!r}: the knowledge base only speaks to "
        f"loopback or to our own vetted hosts "
        f"({', '.join(sorted(TRUSTED_REMOTE_HOSTS))}). "
        f"Unset OLLAMA_HOST or point it at localhost."
    )


def resolve_host(explicit: Optional[str] = None) -> str:
    """Ollama base URL (generation), from ``--host`` → env → default."""
    raw = explicit or os.environ.get("DIGIT_KB_OLLAMA_HOST") or os.environ.get(
        "OLLAMA_HOST"
    ) or DEFAULT_HOST
    if "://" not in raw:
        raw = f"http://{raw}"
    return _validate_endpoint(raw)


def resolve_embed_host(explicit: Optional[str] = None) -> str:
    """Embedding base URL, from ``--embed-host`` → env → default."""
    raw = explicit or os.environ.get("DIGIT_KB_EMBED_HOST") or DEFAULT_EMBED_HOST
    if "://" not in raw:
        raw = f"https://{raw}"
    return _validate_endpoint(raw)


def _clip(text: str, limit: int) -> str:
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


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


class EmbedClient:
    """Dependency-free client for the encoder and the generator.

    The two live on different hosts now, so they are configured separately:
    ``embed_host`` (OpenAI-compatible by default) and ``host`` (loopback
    ollama, used for ``/api/generate``, ``/api/version`` and ``/api/tags``).
    Selecting an ollama-flavoured profile such as ``nomic-embed-text``
    collapses them back onto the ollama host, which is exactly the pre-Qwen
    behaviour.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        embed_model: Optional[str] = None,
        chat_model: str = DEFAULT_CHAT_MODEL,
        timeout: float = 600.0,
        num_ctx: Optional[int] = None,
        embed_host: Optional[str] = None,
    ) -> None:
        self.host = resolve_host(host)
        self.profile = resolve_profile(embed_model)
        if self.profile.remote:
            self.embed_host = resolve_embed_host(embed_host)
        else:
            # ollama-flavoured encoder: same box as generation, and --host
            # must keep steering it the way it always did.
            self.embed_host = resolve_embed_host(embed_host) if embed_host else self.host
        self.chat_model = chat_model
        self.timeout = timeout
        self.num_ctx = num_ctx

    # -- identity ---------------------------------------------------------

    @property
    def embed_model(self) -> str:
        """Identity recorded in the index and checked on every search."""
        return self.profile.name

    @property
    def embed_dim(self) -> int:
        """Vector width this encoder produces. Never assume it globally."""
        return self.profile.dim

    # -- plumbing ---------------------------------------------------------

    def _post(
        self,
        path: str,
        payload: dict,
        timeout: Optional[float] = None,
        base: Optional[str] = None,
    ) -> dict:
        root = base or self.host
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{root}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            # No proxy handler: an http_proxy in the environment must not be
            # able to divert the corpus off its intended path.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=timeout or self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise _HTTPFailure(exc.code, f"{root}{path} returned {exc.code}: {detail}")
        except urllib.error.URLError as exc:
            raise _TransportFailure(f"cannot reach {root} ({exc.reason})")

    # -- health -----------------------------------------------------------

    def version(self) -> str:
        req = urllib.request.Request(f"{self.host}/api/version")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8")).get("version", "?")
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            raise EmbedError(f"ollama not reachable at {self.host}: {exc}") from exc

    def embed_endpoint_ok(self) -> bool:
        """True when the embedding server answers and serves our model."""
        if self.profile.api == "ollama":
            return self.has_model(self.profile.wire_model)
        req = urllib.request.Request(f"{self.embed_host}/v1/models")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise EmbedError(
                f"embedding server not reachable at {self.embed_host}: {exc}"
            ) from exc
        served = {m.get("id") for m in data.get("data", [])}
        return self.profile.wire_model in served

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

    def _est_tokens(self, text: str) -> int:
        return int(len(text) * _CHARS_PER_TOKEN) + 8

    def _plan_batches(self, texts: Sequence[str]) -> List[List[int]]:
        """Group input indices so each request stays under the token budget.

        A fixed chunk count cannot work: the server rejects a request whose
        *total* token count is too high, and corpus chunks range from a few
        hundred to ~3950 characters. Measured against the live endpoint,
        8 × 2600 chars (~8240 tok) succeeded while 10 × 2600 (~10300 tok)
        returned 500, so the budget is expressed in estimated tokens with
        room to spare rather than in items.
        """
        budget = self.profile.token_budget
        batches: List[List[int]] = []
        current: List[int] = []
        used = 0
        for i, text in enumerate(texts):
            cost = self._est_tokens(text)
            if current and used + cost > budget:
                batches.append(current)
                current, used = [], 0
            current.append(i)
            used += cost
        if current:
            batches.append(current)
        return batches

    def _embed_request(self, batch: Sequence[str]) -> List[List[float]]:
        """One request, with the right shape for the configured API."""
        if self.profile.api == "openai":
            payload = {"model": self.profile.wire_model, "input": list(batch)}
            data = self._post("/v1/embeddings", payload, base=self.embed_host)
            rows = data.get("data")
            if not isinstance(rows, list):
                raise EmbedError(f"embedding server returned no 'data': {str(data)[:200]}")
            # The spec allows results out of order; ``index`` is authoritative.
            try:
                ordered = sorted(rows, key=lambda r: int(r.get("index", 0)))
            except (TypeError, ValueError):
                ordered = rows
            return [r["embedding"] for r in ordered]

        payload = {"model": self.profile.wire_model, "input": list(batch)}
        if self.num_ctx:
            payload["options"] = {"num_ctx": int(self.num_ctx)}
        data = self._post("/api/embed", payload, base=self.embed_host)
        vectors = data.get("embeddings")
        if not isinstance(vectors, list):
            raise EmbedError(f"ollama returned no 'embeddings': {str(data)[:200]}")
        return vectors

    def _embed_batch(self, batch: List[str]) -> List[List[float]]:
        """Send one batch, splitting on an oversize rejection.

        Three failure modes, handled differently, because conflating them
        wastes a long index run:

        * an **HTTP error on a multi-item batch** is treated as "too big"
          and the batch is halved. Retrying the identical oversize payload
          three times with backoff — which is what the previous code did —
          is three guaranteed failures followed by an aborted run.
        * an **HTTP error on a single item** cannot be split further, but a
          shared GPU does return the occasional transient 500, and over the
          ~2300 requests of a full index one of those would otherwise end
          the run. It gets the backoff retry before being called fatal.
        * a **transport error** (connection reset, timeout) is transient by
          definition and always gets the backoff retry.
        """
        last: Optional[Exception] = None
        for attempt in range(3):
            try:
                return self._embed_request(batch)
            except _HTTPFailure as exc:
                if len(batch) > 1:
                    mid = len(batch) // 2
                    return (
                        self._embed_batch(batch[:mid])
                        + self._embed_batch(batch[mid:])
                    )
                last = exc
            except _TransportFailure as exc:
                last = exc
            if attempt == 2:
                break
            time.sleep(2 ** attempt)
        raise EmbedError(str(last))

    def embed(
        self, texts: Sequence[str], *, is_query: bool = False
    ) -> List[List[float]]:
        """Embed a batch. One ``embed_dim``-wide vector per input, in order."""
        if not texts:
            return []
        prefix = self.profile.query_prefix if is_query else self.profile.doc_prefix
        prepared = [_clip(prefix + t, self.profile.max_chars) for t in texts]

        out: List[List[float]] = []
        for group in self._plan_batches(prepared):
            got = self._embed_batch([prepared[i] for i in group])
            if len(got) != len(group):
                raise EmbedError(
                    f"encoder returned {len(got)} embeddings for {len(group)} inputs"
                )
            out.extend(got)

        if len(out) != len(texts):
            raise EmbedError(
                f"encoder returned {len(out)} embeddings for {len(texts)} inputs"
            )
        dim = len(out[0])
        if dim != self.embed_dim:
            raise EmbedError(
                f"expected {self.embed_dim}-dim vectors from {self.embed_model}, "
                f"got {dim}. The EmbedProfile and the served model disagree — "
                f"do not index with this, the slab would be unusable."
            )
        return out

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
        """One-shot completion on ollama. ``stream=False`` keeps parsing trivial.

        ``think=False`` matters for reasoning models. Observed with
        ``qwen3.5:4b``: with thinking enabled the whole ``num_predict``
        budget was consumed by the hidden reasoning block and ``response``
        came back an **empty string** — the command produced no answer at
        all while looking like a model failure. Grounded extraction from
        supplied passages does not need a chain of thought, so it is
        disabled and the budget goes to the answer.

        ``num_ctx`` is 6144 rather than 8192 because the KV cache is
        allocated up front and the card is nearly full (4752 MiB free with
        the embedding server resident); the prompt budget (~9000 chars ≈
        3-4k tokens) fits comfortably.
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
        try:
            data = self._post("/api/generate", payload, timeout=self.timeout)
        except (_HTTPFailure, _TransportFailure) as exc:
            raise EmbedError(str(exc)) from exc
        text = (data.get("response") or "").strip()
        if not text:
            # Some builds still return the reasoning separately; prefer a
            # visible answer over silence.
            text = (data.get("thinking") or "").strip()
        return text


class _HTTPFailure(EmbedError):
    """Server answered with an error status (may mean 'batch too large')."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


class _TransportFailure(EmbedError):
    """Could not reach the server at all — transient, worth retrying."""


#: Historical name. The class talks to more than ollama now, but the KB's
#: callers (and any out-of-tree user) refer to it by this name.
OllamaClient = EmbedClient
