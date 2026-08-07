"""Hybrid retrieval over the knowledge base: dense vectors + FTS5 BM25.

Why hybrid and not pure vector search
-------------------------------------
Two independent reasons, and only the first one improved when the encoder
was replaced.

**Calibration.** Under ``nomic-embed-text`` the dense channel was very
nearly useless as evidence: on the full index a *relevant* top hit scored
0.7643 ("горутины и каналы") while a completely off-corpus query
("как ухаживать за орхидеей") still reached 0.7503 — a margin of 0.014,
far inside the noise. ``Qwen/Qwen3-Embedding-8B`` is dramatically better
separated: the same off-corpus query tops out around 0.31-0.44 against
relevant hits at 0.77-0.82. Cosine is now genuinely informative.

**Attestation is still not cosine.** Better separation raises the dense
channel's value for *ranking*, but an honest "not in the knowledge base"
verdict still rests on a **lexical fact** rather than on any threshold:
the word "борщ" occurs zero times in a corpus about Go, SRE and Python,
and SQLite's FTS5 index proves that in a single query. A threshold, however
well separated today, is a number that can drift with the corpus, the
query language, or the next model swap; an attestation count cannot. That
attestation is what makes ``ask``'s abstention defensible rather than a
tuned guess, so the refusal rule was left exactly as it was and merely
gained margin.

Say precisely what this does and does not give. It is evidence about the
*corpus* — this term does or does not occur — and it grounds a claim in a
passage the reader can check. It is **not** a proof that the answer is
true: prose about goroutines is a statement of practice, not a theorem,
and no retrieval statistic can make it one. Formal proof lives where the
facts are already typed (see the FTS gate and its morphism manifest); this
module deliberately does not claim it.

The two rankings are fused with Reciprocal Rank Fusion. RRF combines
*ranks*, not scores, which is exactly right here: the dense scores are not
comparable to BM25 scores, and normalising them against each other would
re-introduce the calibration problem the lexical channel exists to avoid.

Russian morphology
------------------
FTS5's ``unicode61`` tokeniser indexes Cyrillic correctly but does no
stemming, so a query for "горутина" would miss a chunk that says
"горутины". Verified against a live FTS5 table. The fix is a light
suffix-stripping stemmer plus FTS5 prefix queries (``"горутин"*``), which
matches every inflection sharing the stem.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from digit_cli.kb import store
from digit_cli.kb.embed import EmbedClient, EmbedError

# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------

RRF_K = 60
"""RRF damping constant. 60 is the value from the original Cormack et al.
paper and is not sensitive at this corpus size."""

DENSE_POOL = 60
LEXICAL_POOL = 60

# Токенизация, стеммер и сборка FTS-запроса переехали в
# :mod:`digit_cli.kb.lexical`: ровно этот же лексический канал теперь нужен
# памяти агента (``agent.memory_recall``), а ей нельзя импортировать
# ``search`` — он на уровне модуля тянет клиент эмбеддера. Имена
# ре-экспортируются здесь, потому что публичный вход в лексический поиск для
# ``kb`` исторически — ``digit_cli.kb.search``.
from digit_cli.kb.lexical import (  # noqa: F401  (re-export)
    MIN_STEM_LEN,
    STOPWORDS,
    _tokenize,
    build_fts_query,
    content_terms,
    stem,
)


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


class DenseUnavailable(Exception):
    """Internal signal: the encoder is down, continue with lexical only."""


@dataclass
class Hit:
    """One retrieved chunk plus the evidence for why it was retrieved."""

    chunk_id: int
    rel_path: str
    track: str
    title: str
    heading: str
    ordinal: int
    body: str
    score: float = 0.0            # fused RRF score
    dense: Optional[float] = None  # cosine similarity, if in the dense pool
    bm25: Optional[float] = None   # FTS5 bm25 (lower is better), if matched
    dense_rank: Optional[int] = None
    lex_rank: Optional[int] = None

    @property
    def citation(self) -> str:
        loc = f"{self.rel_path}#{self.ordinal}"
        if self.heading:
            return f"{loc} ({self.track} › {self.heading})"
        return f"{loc} ({self.track})"

    def snippet(self, limit: int = 400) -> str:
        text = " ".join(self.body.split())
        return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class SearchResult:
    """Hits plus the retrieval diagnostics that justify trusting them."""

    query: str
    hits: List[Hit] = field(default_factory=list)
    terms: List[str] = field(default_factory=list)
    attested: Dict[str, int] = field(default_factory=dict)
    dense_max: float = 0.0
    dense_mean_top: float = 0.0
    n_vectors: int = 0
    n_chunks: int = 0
    dense_available: bool = True
    """False when the encoder could not be reached and results are lexical-only."""

    @property
    def support(self) -> float:
        """Best per-chunk term support among the returned hits.

        For each hit, the fraction of the query's content terms whose stem
        literally occurs in that chunk; the result is the maximum.

        This is the signal :mod:`digit_cli.kb.ask` actually trusts, and it
        exists because corpus-global attestation proved too weak. Measured
        on the real index, the query "рецепт борща" scores 100% *coverage*
        — "рецепт" appears in 315 chunks as an ordinary Russian word, and
        the corpus really does mention "борща" twice, as a pedagogical
        counter-example. Coverage therefore said "present" for a question
        the corpus cannot answer. Support asks the sharper question: is
        there a single retrieved passage containing these terms *together*?
        For the borscht query no chunk does, while a genuine query's top hit
        contains all of them.
        """
        if not self.terms or not self.hits:
            return 0.0
        stems = [stem(t) for t in self.terms]
        best = 0.0
        for hit in self.hits:
            low = f"{hit.title} {hit.heading} {hit.body}".lower()
            present = sum(1 for s in stems if s and s in low)
            best = max(best, present / len(stems))
        return best

    @property
    def dense_complete(self) -> bool:
        """False while an index run has chunks not yet in the vector slab.

        Retrieval still works (the lexical channel covers every chunk the
        moment it is committed), but results are not the final ranking, and
        an ``ask`` verdict computed now could differ from one computed after
        the run finishes. Callers surface this rather than pretending the
        index is whole.
        """
        return self.n_vectors >= self.n_chunks

    @property
    def coverage(self) -> float:
        """Fraction of query content terms attested anywhere in the corpus."""
        if not self.terms:
            return 0.0
        return sum(1 for t in self.terms if self.attested.get(t, 0) > 0) / len(self.terms)

    @property
    def unattested(self) -> List[str]:
        return [t for t in self.terms if not self.attested.get(t, 0)]


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------


def term_attestation(conn: sqlite3.Connection, terms: Sequence[str]) -> Dict[str, int]:
    """How many chunks contain each term (prefix-stemmed). 0 ⇒ absent.

    This is the ground truth behind ``kb ask``'s refusal to answer: a term
    with zero hits is provably not in the corpus, no threshold involved.
    """
    counts: Dict[str, int] = {}
    for term in terms:
        expr = build_fts_query([term])
        if not expr:
            counts[term] = 0
            continue
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM chunks_fts WHERE chunks_fts MATCH ?",
                (expr,),
            ).fetchone()
            counts[term] = int(row["n"])
        except sqlite3.OperationalError:
            counts[term] = 0
    return counts


def _lexical_pool(
    conn: sqlite3.Connection, terms: Sequence[str], limit: int
) -> List[Tuple[int, float]]:
    expr = build_fts_query(terms)
    if not expr:
        return []
    try:
        rows = conn.execute(
            "SELECT rowid AS id, bm25(chunks_fts) AS score "
            "FROM chunks_fts WHERE chunks_fts MATCH ? "
            "ORDER BY score LIMIT ?",
            (expr, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [(int(r["id"]), float(r["score"])) for r in rows]


def _dense_pool(
    conn: sqlite3.Connection, client: EmbedClient, query: str, limit: int
) -> Tuple[List[Tuple[int, float]], float, float]:
    """Cosine top-N. Returns an empty pool if the encoder is unavailable.

    Degrading instead of raising is deliberate. The FTS5 half of retrieval
    is pure local SQLite and keeps working when the encoder does not — and
    the encoder *does* go away in practice: the shared GPU behind this
    endpoint ran out of memory mid-session and returned 500 for every
    model. A knowledge base that answers nothing because a GPU is busy is
    worse than one that answers from its lexical index and says so. The
    caller reports the degradation via
    :attr:`SearchResult.dense_available`.
    """
    import numpy as np

    mat = store.load_matrix(client.embed_dim)
    if mat.shape[0] == 0:
        return [], 0.0, 0.0

    try:
        raw = client.embed_one(query, is_query=True)
    except EmbedError:
        raise DenseUnavailable() from None

    qvec = np.asarray(raw, dtype=np.float32)
    qnorm = float(np.linalg.norm(qvec)) or 1.0
    qvec /= qnorm

    scores = np.asarray(mat @ qvec, dtype=np.float32)
    take = min(limit, scores.shape[0])
    top = np.argpartition(-scores, take - 1)[:take]
    top = top[np.argsort(-scores[top])]

    rows = conn.execute(
        "SELECT id, row FROM chunks WHERE row IS NOT NULL"
    ).fetchall()
    row_to_id = {int(r["row"]): int(r["id"]) for r in rows}

    pool: List[Tuple[int, float]] = []
    for r in top:
        cid = row_to_id.get(int(r))
        if cid is not None:
            pool.append((cid, float(scores[r])))
    dense_max = float(scores[top[0]]) if take else 0.0
    dense_mean = float(np.mean(scores[top[: min(5, take)]])) if take else 0.0
    return pool, dense_max, dense_mean


def _load_hits(conn: sqlite3.Connection, ids: Sequence[int]) -> Dict[int, Hit]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, rel_path, track, title, heading, ordinal, body "
        f"FROM chunks WHERE id IN ({marks})",
        tuple(ids),
    ).fetchall()
    return {
        int(r["id"]): Hit(
            chunk_id=int(r["id"]),
            rel_path=r["rel_path"],
            track=r["track"],
            title=r["title"],
            heading=r["heading"],
            ordinal=int(r["ordinal"]),
            body=r["body"],
        )
        for r in rows
    }


def search(
    query: str,
    k: int = 5,
    *,
    client: Optional[EmbedClient] = None,
    conn: Optional[sqlite3.Connection] = None,
    track: Optional[str] = None,
    dense_pool: int = DENSE_POOL,
    lexical_pool: int = LEXICAL_POOL,
) -> SearchResult:
    """Hybrid search. Opens its own DB connection unless one is supplied."""
    if conn is not None:
        return _search(query, k, client or EmbedClient(), conn, track,
                       dense_pool, lexical_pool)
    with store.connect() as own:
        return _search(query, k, client or EmbedClient(), own, track,
                       dense_pool, lexical_pool)


def _search(
    query: str,
    k: int,
    client: EmbedClient,
    conn: sqlite3.Connection,
    track: Optional[str],
    dense_pool_size: int,
    lexical_pool_size: int,
) -> SearchResult:
    # Before anything else, and specifically before the encoder is called:
    # a dim mismatch raised from ``embed()`` would surface as
    # DenseUnavailable and degrade silently to lexical-only, which looks
    # like a working search. The store check must fail loudly first.
    store.assert_compatible(conn, client.embed_model, client.embed_dim)
    if store.chunk_count(conn) == 0:
        raise store.KBError(
            "The knowledge base is empty. Run `digit kb index` first."
        )

    terms = content_terms(query)
    attested = term_attestation(conn, terms)

    lex = _lexical_pool(conn, terms, lexical_pool_size)
    dense_available = True
    try:
        dense, dense_max, dense_mean = _dense_pool(
            conn, client, query, dense_pool_size
        )
    except DenseUnavailable:
        dense, dense_max, dense_mean = [], 0.0, 0.0
        dense_available = False

    hits = _load_hits(conn, [cid for cid, _ in lex] + [cid for cid, _ in dense])

    # Reciprocal Rank Fusion over the two pools.
    fused: Dict[int, float] = {}
    for rank, (cid, score) in enumerate(dense, start=1):
        if cid in hits:
            hits[cid].dense = score
            hits[cid].dense_rank = rank
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank)
    for rank, (cid, score) in enumerate(lex, start=1):
        if cid in hits:
            hits[cid].bm25 = score
            hits[cid].lex_rank = rank
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank)

    ordered = []
    for cid, score in sorted(fused.items(), key=lambda kv: -kv[1]):
        hit = hits[cid]
        if track and hit.track != track:
            continue
        hit.score = score
        ordered.append(hit)

    return SearchResult(
        query=query,
        hits=ordered[:k],
        terms=terms,
        attested=attested,
        dense_max=dense_max,
        dense_mean_top=dense_mean,
        n_vectors=int(store.get_meta(conn, "n_vectors", "0") or 0),
        n_chunks=store.chunk_count(conn),
        dense_available=dense_available,
    )
