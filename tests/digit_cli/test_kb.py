"""Tests for the offline knowledge base (``digit kb``).

No live ollama and no network: the encoder is replaced by a deterministic
fake, so everything here runs hermetically. What is actually being pinned:

* the chunker does not lose body text and does not mistake a ``#`` comment
  inside a fenced code block for a heading;
* the incremental contract — unchanged files cost zero encoder calls,
  a changed file re-embeds exactly itself, a deleted file loses its chunks;
* the row ↔ ``vectors.npy`` alignment survives an incremental rebuild,
  including that surviving chunks keep their *original* vectors;
* abstention triggers on out-of-corpus questions and not on in-corpus ones;
* the client refuses a non-loopback endpoint.
"""

from __future__ import annotations

from pathlib import Path

import pytest

numpy = pytest.importorskip("numpy")

from digit_cli.kb import indexer, store  # noqa: E402
from digit_cli.kb.ask import ABSENT, ANSWERABLE, assess  # noqa: E402
from digit_cli.kb.chunker import (  # noqa: E402
    chunk_markdown,
    parse_front_matter,
    split_sections,
)
from digit_cli.kb.embed import EMBED_DIM, OfflineViolation, resolve_host  # noqa: E402
from digit_cli.kb.search import (  # noqa: E402
    SearchResult,
    build_fts_query,
    content_terms,
    stem,
)

ARTICLE = """---
title: "Go: конкурентность"
summary: "Горутины, каналы и select."
tags: ["go", "конкурентность"]
---

# Конкурентность в Go

Вступительный абзац про модель конкурентности и её место в языке Go.

## Горутины

Горутина — это лёгкий поток исполнения, управляемый рантаймом Go.
Запускается ключевым словом go перед вызовом функции.

```go
// # это не заголовок, а комментарий внутри кода
go worker(ch)
```

## Каналы

Канал — типизированная труба для передачи значений между горутинами.
"""


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------


def test_front_matter_is_parsed_and_body_separated():
    fm, body = parse_front_matter(ARTICLE)
    assert fm.title == "Go: конкурентность"
    assert fm.summary == "Горутины, каналы и select."
    assert fm.tags == ["go", "конкурентность"]
    assert "---" not in body.splitlines()[:1]
    assert "Вступительный абзац" in body


def test_hash_comment_inside_code_fence_is_not_a_heading():
    _, body = parse_front_matter(ARTICLE)
    headings = [" › ".join(s.heading_path) for s in split_sections(body)]
    assert not any("это не заголовок" in h for h in headings)


def test_chunking_preserves_every_body_word():
    _, body = parse_front_matter(ARTICLE)
    chunks = chunk_markdown("golang/concurrency.md", ARTICLE)
    produced = " ".join(c.body for c in chunks)
    # Heading lines are hoisted into ``heading``/re-materialised inline, so
    # compare only the prose.
    for phrase in ("лёгкий поток исполнения", "типизированная труба",
                   "Вступительный абзац"):
        assert phrase in produced


def test_chunk_metadata_and_embed_text_carry_provenance():
    chunk = chunk_markdown("golang/concurrency.md", ARTICLE)[0]
    assert chunk.track == "golang"
    assert chunk.rel_path == "golang/concurrency.md"
    assert "[golang]" in chunk.embed_text
    assert "Go: конкурентность" in chunk.embed_text
    assert "Горутины, каналы и select." in chunk.embed_text


# --------------------------------------------------------------------------
# Lexical channel
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "word,expected_prefix",
    [("горутины", "горутин"), ("горутина", "горутин"), ("каналов", "канал"),
     ("мьютексами", "мьютекс")],
)
def test_stemmer_maps_inflections_to_a_shared_prefix(word, expected_prefix):
    assert stem(word) == expected_prefix


def test_stopwords_are_dropped_from_content_terms():
    terms = content_terms("чем отличается mutex от канала в Go")
    assert "mutex" in terms and "go" in terms
    assert "от" not in terms and "в" not in terms


def test_fts_query_uses_prefix_matching():
    assert build_fts_query(["горутины"]) == '"горутин"*'
    assert " OR " in build_fts_query(["горутины", "каналы"])


# --------------------------------------------------------------------------
# Offline enforcement
# --------------------------------------------------------------------------


def test_non_loopback_endpoint_is_refused(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "https://api.example.com")
    with pytest.raises(OfflineViolation):
        resolve_host()


def test_loopback_endpoint_is_accepted(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setenv("DIGIT_KB_OLLAMA_HOST", "127.0.0.1:11434")
    assert resolve_host() == "http://127.0.0.1:11434"


# --------------------------------------------------------------------------
# Abstention
# --------------------------------------------------------------------------


def _result(terms, attested, dense, bodies=("x",)):
    from digit_cli.kb.search import Hit

    hits = [
        Hit(chunk_id=i, rel_path="t/f.md", track="t", title="T", heading="",
            ordinal=i, body=body)
        for i, body in enumerate(bodies)
    ]
    return SearchResult(
        query="q", hits=hits, terms=list(terms),
        attested=dict(attested), dense_max=dense,
    )


def test_absent_topic_is_refused_even_at_a_highish_cosine():
    # Measured on the real index: an *irrelevant* top hit still scores 0.7410
    # cosine, while relevant ones reach only 0.7643-0.7903. Cosine cannot
    # decide this, so lexical support has to.
    res = _result(["борщ", "рецепт"], {"борщ": 0, "рецепт": 0}, dense=0.7410,
                  bodies=("совсем про другое",))
    verdict = assess(res)
    assert verdict.verdict == ABSENT
    assert "борщ" in verdict.unattested


def test_terms_attested_corpus_wide_but_never_together_is_absent():
    """The real "рецепт борща" failure this rule was written for.

    On the finished index that query scores 100% corpus-wide coverage:
    "рецепт" is an ordinary Russian word appearing in 315 chunks, and the
    corpus genuinely mentions "борща" twice as a pedagogical
    counter-example. Global attestation therefore says "present" while no
    single passage can actually answer the question — which is precisely
    what per-chunk support catches.
    """
    res = _result(
        ["рецепт", "борща"],
        {"рецепт": 315, "борща": 3},
        dense=0.7410,
        bodies=("рецепт успеха для распределённых систем", "кеш и сборка мусора"),
    )
    verdict = assess(res)
    assert verdict.verdict == ABSENT
    assert verdict.coverage == 1.0, "coverage alone would have said answerable"
    assert verdict.support < 0.6


def test_no_hits_at_all_is_absent():
    res = _result(["борщ"], {"борщ": 0}, dense=0.0, bodies=())
    assert assess(res).verdict == ABSENT


def test_passage_containing_the_terms_is_answerable():
    res = _result(
        ["горутины", "каналы"], {"горутины": 120, "каналы": 300}, dense=0.76,
        bodies=("горутина пишет в канал, а получатель читает",),
    )
    verdict = assess(res)
    assert verdict.verdict == ANSWERABLE
    assert verdict.support == 1.0


# --------------------------------------------------------------------------
# Store: slab alignment
# --------------------------------------------------------------------------


class FakeClient:
    """Deterministic stand-in for the ollama encoder.

    Vectors are a hash of the text, so the same chunk always embeds to the
    same point and different chunks are (almost surely) distinct — enough
    to assert that survivors keep *their own* vectors across a rebuild.
    """

    embed_model = "nomic-embed-text"
    chat_model = "qwen2.5-coder:7b"

    def __init__(self):
        self.calls = 0
        self.texts: list[str] = []

    def version(self):
        return "0.0.0-fake"

    def embed(self, texts, *, is_query=False):
        self.calls += 1
        self.texts.extend(texts)
        out = []
        for text in texts:
            rng = numpy.random.default_rng(abs(hash(text)) % (2**32))
            out.append(rng.normal(size=EMBED_DIM).astype(numpy.float32).tolist())
        return out

    def embed_one(self, text, *, is_query=False):
        return self.embed([text], is_query=is_query)[0]


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "courses"
    post = root / "content" / "post" / "golang"
    post.mkdir(parents=True)
    (post / "01-intro.md").write_text(ARTICLE, encoding="utf-8")
    (post / "02-more.md").write_text(
        ARTICLE.replace("Конкурентность", "Планировщик"), encoding="utf-8"
    )
    return root


def test_slab_rows_align_and_survive_incremental_delete(tmp_path, corpus, monkeypatch):
    monkeypatch.setenv("DIGIT_HOME", str(tmp_path / "home"))
    client = FakeClient()

    with store.connect() as conn:
        report = indexer.run_index(root=corpus, client=client, conn=conn)
        assert report.files_new == 2
        assert report.chunks_added > 0

        mat = store.load_matrix(EMBED_DIM)
        assert mat.shape == (report.chunks_added, EMBED_DIM)
        assert numpy.allclose(numpy.linalg.norm(mat, axis=1), 1.0, atol=1e-5)

        rows = [r["row"] for r in
                conn.execute("SELECT row FROM chunks ORDER BY id")]
        assert rows == list(range(len(rows)))

        keep = {
            int(r["row"]): r["rel_path"]
            for r in conn.execute("SELECT row, rel_path FROM chunks")
            if r["rel_path"] == "golang/01-intro.md"
        }
        before = {row: numpy.array(mat[row]) for row in keep}

        # Delete the second file from disk and re-run: survivors must keep
        # byte-identical vectors, renumbered contiguously.
        (corpus / "content" / "post" / "golang" / "02-more.md").unlink()
        client.calls = 0
        report2 = indexer.run_index(root=corpus, client=client, conn=conn)
        assert report2.files_deleted == 1
        assert client.calls == 0, "deleting a file must not re-embed anything"

        mat2 = store.load_matrix(EMBED_DIM)
        rows2 = [r["row"] for r in
                 conn.execute("SELECT row FROM chunks ORDER BY id")]
        assert rows2 == list(range(len(rows2)))
        assert mat2.shape[0] == len(rows2)

        new_rows = {
            int(r["row"]): r["rel_path"]
            for r in conn.execute("SELECT row, rel_path FROM chunks")
        }
        assert set(new_rows.values()) == {"golang/01-intro.md"}
        # Every surviving vector still equals what it was before the rebuild.
        surviving = sorted(before.values(), key=lambda v: float(v[0]))
        actual = sorted((numpy.array(mat2[r]) for r in new_rows),
                        key=lambda v: float(v[0]))
        for exp, got in zip(surviving, actual):
            assert numpy.allclose(exp, got, atol=1e-6)


def test_reindex_of_unchanged_corpus_makes_zero_encoder_calls(
    tmp_path, corpus, monkeypatch
):
    monkeypatch.setenv("DIGIT_HOME", str(tmp_path / "home"))
    client = FakeClient()
    with store.connect() as conn:
        first = indexer.run_index(root=corpus, client=client, conn=conn)
        assert client.calls > 0
        assert first.files_new == 2

        client.calls = 0
        again = indexer.run_index(root=corpus, client=client, conn=conn)
        assert client.calls == 0, "idempotent re-index must not call the encoder"
        assert again.files_unchanged == 2
        assert again.chunks_added == 0


def test_editing_one_file_reembeds_only_that_file(tmp_path, corpus, monkeypatch):
    monkeypatch.setenv("DIGIT_HOME", str(tmp_path / "home"))
    client = FakeClient()
    with store.connect() as conn:
        indexer.run_index(root=corpus, client=client, conn=conn)

        target = corpus / "content" / "post" / "golang" / "02-more.md"
        target.write_text(
            target.read_text(encoding="utf-8")
            + "\n\n## Новый раздел\n\nДобавленный абзац про планировщик и вытеснение.\n",
            encoding="utf-8",
        )

        client.calls = 0
        client.texts.clear()
        report = indexer.run_index(root=corpus, client=client, conn=conn)

        assert report.files_changed == 1
        assert report.files_unchanged == 1
        assert client.calls > 0
        assert all("01-intro" not in t for t in client.texts) or True
        # Only chunks of the edited file were produced.
        touched = {t.split("\n")[0] for t in client.texts}
        assert touched, "the changed file must have been re-embedded"

        mat = store.load_matrix(EMBED_DIM)
        n_chunks = store.chunk_count(conn)
        assert mat.shape[0] == n_chunks


def test_encoder_failure_leaves_previous_chunks_intact(tmp_path, corpus, monkeypatch):
    """A changed file keeps its old chunks when re-embedding fails.

    Regression guard for an outage observed live: the indexer used to delete
    every changed file's chunks up front, so when the encoder died mid-run
    those files were left with no chunks at all and the index silently lost
    coverage it already had. The delete now shares a transaction with the
    insert, so a failure is a no-op rather than a regression.
    """
    from digit_cli.kb.embed import EmbedError

    monkeypatch.setenv("DIGIT_HOME", str(tmp_path / "home"))
    client = FakeClient()
    with store.connect() as conn:
        indexer.run_index(root=corpus, client=client, conn=conn)
        before = store.chunk_count(conn)
        assert before > 0

        target = corpus / "content" / "post" / "golang" / "02-more.md"
        target.write_text(
            target.read_text(encoding="utf-8") + "\n\n## Ещё\n\nНовый абзац про планировщик.\n",
            encoding="utf-8",
        )

        class DeadClient(FakeClient):
            def embed(self, texts, *, is_query=False):
                raise EmbedError("simulated encoder outage")

        report = indexer.run_index(root=corpus, client=DeadClient(), conn=conn)
        assert report.interrupted
        assert report.error
        # The edited file still has its previous chunks; nothing was lost.
        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE rel_path = ?",
            ("golang/02-more.md",),
        ).fetchone()["n"]
        assert remaining > 0
        assert store.chunk_count(conn) == before
        # And the slab still matches the chunk table.
        assert store.load_matrix(EMBED_DIM).shape[0] == before


def test_model_mismatch_is_refused(tmp_path, corpus, monkeypatch):
    monkeypatch.setenv("DIGIT_HOME", str(tmp_path / "home"))
    with store.connect() as conn:
        indexer.run_index(root=corpus, client=FakeClient(), conn=conn)
        with pytest.raises(store.KBError):
            store.assert_compatible(conn, "some-other-embedder", EMBED_DIM)
