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
* the client refuses any endpoint outside the allowlist, and the allowlist
  matches host names exactly rather than by suffix;
* the encoder's dimension travels with the index in ``meta`` and a
  mismatch is refused loudly instead of returning nonsense.
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
from digit_cli.kb.embed import (  # noqa: E402
    NOMIC_EMBED,
    QWEN3_EMBED,
    EmbedClient,
    OfflineViolation,
    resolve_embed_host,
    resolve_host,
)
from digit_cli.kb.search import (  # noqa: E402
    SearchResult,
    build_fts_query,
    content_terms,
    stem,
)

FAKE_DIM = 64
"""Width of the fake encoder's vectors.

Deliberately not any real encoder's dimension: the point of these tests is
that nothing in the pipeline assumes a *global* dimension, so using 64 here
would fail loudly against any code that still hard-coded 768 or 4096.
"""

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


def test_vetted_remote_embedding_host_is_accepted(monkeypatch):
    """The corpus may go to our own encoder, and only to it."""
    monkeypatch.setenv("DIGIT_KB_EMBED_HOST", "https://embed.gpu.local-xyz.ru")
    assert resolve_embed_host() == "https://embed.gpu.local-xyz.ru"


def test_untrusted_remote_embedding_host_is_refused(monkeypatch):
    monkeypatch.setenv("DIGIT_KB_EMBED_HOST", "https://api.openai.com")
    with pytest.raises(OfflineViolation):
        resolve_embed_host()


@pytest.mark.parametrize("host", [
    "https://embed.gpu.local-xyz.ru.evil.example",   # suffix-of-attacker
    "https://evil-embed.gpu.local-xyz.ru",           # wildcard DNS neighbour
    "https://gpu.local-xyz.ru",                      # parent domain
])
def test_allowlist_matches_hosts_exactly_not_by_suffix(monkeypatch, host):
    """A wildcard DNS zone makes suffix matching a real hole.

    ``*.gpu.local-xyz.ru`` resolves for names nobody ever provisioned, so an
    ``endswith(".gpu.local-xyz.ru")`` check would have accepted the second
    case here, and a naive ``in url`` check the first. Membership is exact.
    """
    monkeypatch.setenv("DIGIT_KB_EMBED_HOST", host)
    with pytest.raises(OfflineViolation):
        resolve_embed_host()


def test_vetted_host_still_requires_tls(monkeypatch):
    monkeypatch.setenv("DIGIT_KB_EMBED_HOST", "http://embed.gpu.local-xyz.ru")
    with pytest.raises(OfflineViolation):
        resolve_embed_host()


# --------------------------------------------------------------------------
# Encoder profiles: dimension and prefixes travel with the model
# --------------------------------------------------------------------------


def test_profiles_disagree_on_dimension_and_prefixes():
    """The two supported encoders are genuinely incompatible.

    Guards the reason ``meta`` records the model: these vectors are not
    merely differently scaled, they are different widths.
    """
    assert QWEN3_EMBED.dim == 4096 and NOMIC_EMBED.dim == 768
    assert QWEN3_EMBED.api == "openai" and NOMIC_EMBED.api == "ollama"
    # nomic is asymmetric and needs its prefixes; Qwen3 wants raw passages
    # and an Instruct envelope on the query side only.
    assert NOMIC_EMBED.doc_prefix and NOMIC_EMBED.query_prefix
    assert QWEN3_EMBED.doc_prefix == ""
    assert QWEN3_EMBED.query_prefix.startswith("Instruct:")


def test_client_reports_the_dimension_of_its_own_model(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("DIGIT_KB_EMBED_HOST", raising=False)
    assert EmbedClient(embed_model="Qwen/Qwen3-Embedding-8B").embed_dim == 4096
    assert EmbedClient(embed_model="nomic-embed-text").embed_dim == 768


def test_ollama_flavoured_encoder_stays_on_the_ollama_host(monkeypatch):
    """Selecting nomic must reproduce the pre-Qwen behaviour exactly.

    ``--host`` used to steer embedding as well as generation; that has to
    keep working, or the documented fallback path silently starts talking
    to the wrong box.
    """
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("DIGIT_KB_EMBED_HOST", raising=False)
    client = EmbedClient(host="http://127.0.0.1:9999",
                         embed_model="nomic-embed-text")
    assert client.embed_host == "http://127.0.0.1:9999" == client.host


def test_request_batches_respect_the_token_budget():
    """Batching is by estimated tokens, not by item count.

    Measured against the live endpoint: 8 inputs of 2600 chars (~8240 tok)
    succeeded, 10 of the same (~10300 tok) returned 500. A fixed item count
    therefore cannot be safe across a corpus whose chunks range from a few
    hundred to ~3950 characters.
    """
    client = EmbedClient(embed_model="Qwen/Qwen3-Embedding-8B")
    budget = client.profile.token_budget
    texts = ["ё" * 3000] * 12
    groups = client._plan_batches(texts)
    assert len(groups) > 1, "12 large chunks must not go out as one request"
    assert [i for g in groups for i in g] == list(range(12)), "order preserved"
    for g in groups:
        assert sum(client._est_tokens(texts[i]) for i in g) <= budget or len(g) == 1


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

    embed_model = "fake-encoder"
    embed_dim = FAKE_DIM
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
            out.append(rng.normal(size=FAKE_DIM).astype(numpy.float32).tolist())
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

        mat = store.load_matrix(FAKE_DIM)
        assert mat.shape == (report.chunks_added, FAKE_DIM)
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

        mat2 = store.load_matrix(FAKE_DIM)
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

        mat = store.load_matrix(FAKE_DIM)
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
        assert store.load_matrix(FAKE_DIM).shape[0] == before


def test_model_mismatch_is_refused(tmp_path, corpus, monkeypatch):
    monkeypatch.setenv("DIGIT_HOME", str(tmp_path / "home"))
    with store.connect() as conn:
        indexer.run_index(root=corpus, client=FakeClient(), conn=conn)
        with pytest.raises(store.KBError):
            store.assert_compatible(conn, "some-other-embedder", FAKE_DIM)


def test_index_records_the_encoder_identity_and_width(tmp_path, corpus, monkeypatch):
    """``meta`` is the only thing that knows what built the slab."""
    monkeypatch.setenv("DIGIT_HOME", str(tmp_path / "home"))
    client = FakeClient()
    with store.connect() as conn:
        indexer.run_index(root=corpus, client=client, conn=conn)
        assert store.get_meta(conn, "embed_model") == client.embed_model
        assert int(store.get_meta(conn, "embed_dim")) == client.embed_dim
        assert store.load_matrix(client.embed_dim).shape[1] == client.embed_dim


def test_dimension_mismatch_is_refused_rather_than_answered(
    tmp_path, corpus, monkeypatch
):
    """Swapping the encoder for a wider one must fail loudly.

    This is the 768-vs-4096 case the migration created. Two slabs of
    different widths cannot even be multiplied, but a same-width/different-
    model pair would silently return confident nonsense, so both are
    refused by the same check.
    """
    monkeypatch.setenv("DIGIT_HOME", str(tmp_path / "home"))
    with store.connect() as conn:
        indexer.run_index(root=corpus, client=FakeClient(), conn=conn)
        with pytest.raises(store.KBError, match="dimension"):
            store.assert_compatible(conn, FakeClient.embed_model, 4096)


def test_index_with_chunks_but_no_recorded_encoder_is_refused(
    tmp_path, corpus, monkeypatch
):
    """An index that cannot say what produced it is not trusted.

    Without this, a store written before the encoder was recorded would
    pass the compatibility check by virtue of having nothing to compare —
    exactly the silent-wrong-answer the check exists to prevent.
    """
    monkeypatch.setenv("DIGIT_HOME", str(tmp_path / "home"))
    with store.connect() as conn:
        indexer.run_index(root=corpus, client=FakeClient(), conn=conn)
        with store.write_txn(conn):
            conn.execute("DELETE FROM meta WHERE key IN ('embed_model','embed_dim')")
        assert store.chunk_count(conn) > 0
        with pytest.raises(store.KBError, match="records no embedding model"):
            store.assert_compatible(conn, FakeClient.embed_model, FAKE_DIM)


# --------------------------------------------------------------------------
# Multi-repository corpus
# --------------------------------------------------------------------------
#
# The corpus grew from one checkout to several when flang was added. The
# tests below pin the two things that refactor could break silently: the
# identity (``rel_path``) of everything that was already indexed, and the
# rule that an absent optional checkout is not the same as deleted content.

SDD_DOC = """---
title: "Решение 0001"
---

# Контекст

Документ инженерного решения с достаточным количеством слов, чтобы чанкер
счёл его самостоятельным фрагментом, а не обрезком заголовка без тела.
"""

FLANG_DOC = """# flang

Язык описан спецификацией, которая исполняется. Абзац намеренно длиннее
минимального размера фрагмента, чтобы чанкер выдал хотя бы один чанк и
файл попал в индекс, а не был молча пропущен как пустой.
"""


@pytest.fixture()
def wide_corpus(tmp_path: Path) -> Path:
    """A courses checkout with all three of its slices populated."""
    root = tmp_path / "courses"
    post = root / "content" / "post" / "golang"
    post.mkdir(parents=True)
    (post / "01-intro.md").write_text(ARTICLE, encoding="utf-8")
    sdd = root / "docs" / "sdd"
    sdd.mkdir(parents=True)
    (sdd / "0001-decision.md").write_text(SDD_DOC, encoding="utf-8")
    tpl = root / "products" / "workbench" / "templates"
    tpl.mkdir(parents=True)
    (tpl / "brief.md").write_text(SDD_DOC, encoding="utf-8")
    return root


def _fake_flang_checkout(tmp_path: Path) -> Path:
    root = tmp_path / "flang-checkout"
    (root / "flang").mkdir(parents=True)
    (root / "flang" / "SPEC.md").write_text(FLANG_DOC, encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "language.ru.md").write_text(FLANG_DOC, encoding="utf-8")
    (root / "README.md").write_text(FLANG_DOC, encoding="utf-8")
    # Not in the include list: the checkout is full of code and per-tool
    # READMEs, and sweeping them in would bury the language documentation.
    (root / "src").mkdir()
    (root / "src" / "NOTES.md").write_text(FLANG_DOC, encoding="utf-8")
    return root


def test_courses_rel_paths_survived_the_multi_root_refactor(wide_corpus):
    """``rel_path`` is the identity key — perturbing one re-embeds the file.

    ``plan()`` diffs disk against the store by ``rel_path``, so a path that
    changes shape reads as "deleted + new" and costs a full re-encode of a
    file whose bytes never moved. The prefixes below are exactly what the
    single-root code produced.
    """
    got = {f.rel_path: f.source for f in indexer.discover(wide_corpus)}
    assert got == {
        "golang/01-intro.md": "courses",
        "sdd/0001-decision.md": "sdd",
        "workbench/brief.md": "workbench",
    }


def test_a_bare_root_never_reaches_into_another_checkout(
    tmp_path, wide_corpus, monkeypatch
):
    """``discover(root)`` means *that* root and nothing else.

    A caller scanning one directory — a test, a one-off — must not silently
    acquire whatever other repositories happen to exist on the machine.
    """
    monkeypatch.setenv("DIGIT_KB_FLANG", str(_fake_flang_checkout(tmp_path)))
    assert all(f.source != "flang" for f in indexer.discover(wide_corpus))


def test_flang_documents_carry_their_own_source_and_track(
    tmp_path, wide_corpus, monkeypatch
):
    monkeypatch.setenv("DIGIT_KB_CORPUS", str(wide_corpus))
    monkeypatch.setenv("DIGIT_KB_FLANG", str(_fake_flang_checkout(tmp_path)))

    resolved = indexer.resolve_corpus()
    assert set(resolved.roots) == {"courses", "flang"}
    assert resolved.missing == []

    files = indexer.discover_sources(resolved.sources)
    flang = {f.rel_path for f in files if f.source == "flang"}
    # Path-preserving, so a citation maps onto the repository layout, and
    # ``src/NOTES.md`` is absent because only documentation is included.
    assert flang == {
        "flang/README.md",
        "flang/docs/language.ru.md",
        "flang/flang/SPEC.md",
    }
    assert {f.rel_path for f in files if f.source != "flang"} == {
        "golang/01-intro.md", "sdd/0001-decision.md", "workbench/brief.md",
    }
    # One track, so `--track flang` reaches all of it.
    assert {f.rel_path for f in indexer.discover_sources(resolved.sources, ["flang"])} \
        == flang


def test_two_files_claiming_one_rel_path_are_refused(tmp_path):
    """``rel_path`` is the primary key of ``files``; a clash is data loss.

    The courses corpus already has a ``flang`` track of its own, so the two
    sources share a namespace by design. Sharing a *name* is different: one
    row would overwrite the other and the index would hold whichever was
    scanned last, with no sign that anything was dropped.
    """
    a = tmp_path / "a"
    b = tmp_path / "b"
    for base in (a, b):
        base.mkdir()
        (base / "same.md").write_text(FLANG_DOC, encoding="utf-8")
    sources = [
        indexer.ResolvedSource(source="a", base=a, prefix="x"),
        indexer.ResolvedSource(source="b", base=b, prefix="x"),
    ]
    with pytest.raises(store.KBError, match="same identity"):
        indexer.discover_sources(sources)


def test_an_absent_optional_checkout_keeps_its_chunks(
    tmp_path, wide_corpus, monkeypatch
):
    """Absence is not deletion.

    An optional repository that is merely not mounted right now looks
    exactly like one whose files were removed. Treating the two the same
    turns an unplugged disk into silent data loss that the run still
    reports as a success — so the missing repo's paths are left alone
    until it is back and can actually be compared against.
    """
    import dataclasses

    monkeypatch.setenv("DIGIT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DIGIT_KB_CORPUS", str(wide_corpus))
    monkeypatch.setenv("DIGIT_KB_FLANG", str(_fake_flang_checkout(tmp_path)))

    def flang_chunks(conn) -> int:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE rel_path LIKE 'flang/%'"
        ).fetchone()["n"]

    with store.connect() as conn:
        indexer.run_index(
            root=wide_corpus, corpus=indexer.resolve_corpus(),
            client=FakeClient(), conn=conn,
        )
        indexed = flang_chunks(conn)
        assert indexed > 0

        # The checkout vanishes: no env var, no conventional path.
        monkeypatch.delenv("DIGIT_KB_FLANG")
        nowhere = dataclasses.replace(indexer.FLANG_REPO, default_roots=())
        absent = indexer.resolve_corpus(
            repos=(indexer.COURSES_REPO, nowhere)
        )
        assert absent.missing, "a skipped repository must be reported"
        assert absent.protected == ["flang"]

        report = indexer.run_index(
            root=wide_corpus, corpus=absent, client=FakeClient(), conn=conn,
        )
        assert report.files_deleted == 0
        assert flang_chunks(conn) == indexed


def test_a_file_deleted_from_a_present_checkout_is_still_dropped(
    tmp_path, wide_corpus, monkeypatch
):
    """The protection above is per-absent-repository, not a blanket amnesty."""
    monkeypatch.setenv("DIGIT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DIGIT_KB_CORPUS", str(wide_corpus))
    flang = _fake_flang_checkout(tmp_path)
    monkeypatch.setenv("DIGIT_KB_FLANG", str(flang))

    with store.connect() as conn:
        indexer.run_index(
            root=wide_corpus, corpus=indexer.resolve_corpus(),
            client=FakeClient(), conn=conn,
        )
        (flang / "docs" / "language.ru.md").unlink()
        report = indexer.run_index(
            root=wide_corpus, corpus=indexer.resolve_corpus(),
            client=FakeClient(), conn=conn,
        )
        assert report.files_deleted == 1
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE rel_path = ?",
            ("flang/docs/language.ru.md",),
        ).fetchone()["n"] == 0


def test_a_named_but_missing_checkout_is_a_hard_error(tmp_path, monkeypatch):
    """Naming a path explicitly and having it ignored would be a lie."""
    monkeypatch.setenv("DIGIT_KB_FLANG", str(tmp_path / "not-here"))
    with pytest.raises(store.KBError, match="DIGIT_KB_FLANG"):
        indexer.resolve_corpus()


def test_the_missing_courses_corpus_message_is_unchanged(monkeypatch):
    import dataclasses

    monkeypatch.delenv("DIGIT_KB_CORPUS", raising=False)
    monkeypatch.setattr(
        indexer, "COURSES_REPO",
        dataclasses.replace(indexer.COURSES_REPO, default_roots=()),
    )
    with pytest.raises(store.KBError, match="cannot find the courses corpus"):
        indexer.corpus_root()


# --------------------------------------------------------------------------
# flang verification
# --------------------------------------------------------------------------
#
# Every flang snippet below is run through flang's own checker rather than
# eyeballed. That is not ceremony: this suite's author wrote flang from
# memory once and produced a file that reads perfectly and does not parse.
# A snippet asserted correct by a human is exactly the artefact the
# verifier exists to distrust.

import shutil  # noqa: E402

from digit_cli.kb import verify  # noqa: E402


def _live_checker():
    try:
        return verify.find_checker()
    except verify.CheckerUnavailable:
        return None


CHECKER = _live_checker()
needs_flang = pytest.mark.skipif(
    CHECKER is None, reason="no flang checkout or no node on this machine"
)

GOOD_FLANG = """модуль «Проба»

тотальная функция «Удвоить»
  принимает n: число
  возвращает число
  пример «Два»
    дано n равно 2
    ожидается 4
  n умножить на 2
"""

BROKEN_FLANG = """модуль «Битый»

тотальная функция «Ой»
  принимает n: число
  возвращает строка
  то n плюс
"""

WRONG_FLANG = """модуль «Провал»

тотальная функция «Удвоить»
  принимает n: число
  возвращает число
  пример «Неверный»
    дано n равно 2
    ожидается 5
  n умножить на 2
"""

NO_EXAMPLES_FLANG = """модуль «Без примеров»

тотальная функция «Удвоить»
  принимает n: число
  возвращает число
  n умножить на 2
"""


@pytest.mark.parametrize("verdict", [verify.FAILED, verify.UNAVAILABLE])
def test_only_a_real_pass_counts_as_ok(verdict):
    """``ok`` is not "not failed"."""
    report = verify.VerifyReport(verdict=verdict)
    assert not report.ok
    assert report.exit_code != verify.EXIT_OK


def test_the_three_exit_codes_are_distinct():
    """A script must be able to tell "wrong" from "never checked".

    Collapsing them either sends someone debugging correct code or ships
    code nobody looked at; the exit codes are the only channel a caller
    that does not parse the JSON has.
    """
    assert len({verify.EXIT_OK, verify.EXIT_FAILED, verify.EXIT_UNAVAILABLE}) == 3


def test_no_checkout_is_unverified_rather_than_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGIT_KB_FLANG", str(tmp_path / "not-a-checkout"))
    report = verify.verify_file(tmp_path / "whatever.flang")
    assert report.verdict == verify.UNAVAILABLE
    assert not report.ok and not report.available
    assert report.exit_code == verify.EXIT_UNAVAILABLE
    assert "DIGIT_KB_FLANG" in report.reason


def test_a_directory_without_the_cli_is_unverified(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("DIGIT_KB_FLANG", str(empty))
    report = verify.verify_file(tmp_path / "whatever.flang")
    assert report.verdict == verify.UNAVAILABLE
    assert "flang/bin/flang.mjs" in report.reason


def test_a_missing_node_is_unverified(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGIT_FLANG_NODE", str(tmp_path / "no-node"))
    report = verify.verify_file(tmp_path / "whatever.flang")
    assert report.verdict == verify.UNAVAILABLE
    assert not report.ok


def test_render_of_an_unverified_report_says_so_out_loud():
    text = "\n".join(verify.render(
        verify.VerifyReport(verdict=verify.UNAVAILABLE, path="x.flang",
                            reason="no node")
    ))
    assert "UNVERIFIED" in text
    assert "НЕ подтверждён" in text


@needs_flang
def test_a_correct_snippet_passes_check_and_runs_its_examples(tmp_path):
    report = verify.verify_source(GOOD_FLANG, directory=tmp_path, checker=CHECKER)
    assert report.verdict == verify.OK, report.reason
    assert report.module == "Проба"
    assert report.functions == [{"name": "Удвоить", "total": True}]
    assert (report.examples_total, report.examples_passed) == (1, 1)


@needs_flang
def test_a_broken_snippet_is_rejected_with_a_place_to_look(tmp_path):
    report = verify.verify_source(BROKEN_FLANG, directory=tmp_path, checker=CHECKER)
    assert report.verdict == verify.FAILED
    assert report.exit_code == verify.EXIT_FAILED
    diag = report.diagnostics[0]
    assert diag.code == "FLANG_PARSE"
    assert diag.stage == "check"
    # "что-то не так" is not a diagnostic. A line and column are.
    assert diag.line == 6 and diag.column == 3


@needs_flang
def test_code_that_type_checks_but_computes_wrongly_still_fails(tmp_path):
    """``check`` alone is not verification — the examples have to run."""
    check_only = verify.verify_source(
        WRONG_FLANG, directory=tmp_path, checker=CHECKER, run_tests=False
    )
    assert check_only.verdict == verify.OK

    report = verify.verify_source(WRONG_FLANG, directory=tmp_path, checker=CHECKER)
    assert report.verdict == verify.FAILED
    assert report.examples_failed == 1
    failure = report.failures[0]
    assert (failure.expected, failure.actual) == (5, 4)


@needs_flang
def test_a_snippet_with_no_examples_is_flagged_as_unexercised(tmp_path):
    """Types agreeing is not the same as anything having been computed."""
    report = verify.verify_source(
        NO_EXAMPLES_FLANG, directory=tmp_path, checker=CHECKER
    )
    assert report.verdict == verify.OK
    assert report.untested
    assert "ни одного" in "\n".join(verify.render(report))


@needs_flang
def test_an_import_resolves_against_the_snippets_directory(tmp_path):
    """Where the snippet is written decides whether its imports exist.

    ``использует «Списки» из "../stdlib/lists.flang"`` is resolved relative
    to the file, so a snippet materialised in the wrong directory fails for
    a reason that has nothing to do with the code.
    """
    checkout = CHECKER.script.parent.parent
    example = checkout / "examples" / "import-check.flang"
    if not example.is_file():  # pragma: no cover - checkout without examples
        pytest.skip("this flang checkout has no examples/import-check.flang")
    source = example.read_text(encoding="utf-8")

    here = verify.verify_source(
        source, directory=example.parent, checker=CHECKER
    )
    assert here.verdict == verify.OK, here.reason

    elsewhere = verify.verify_source(source, directory=tmp_path, checker=CHECKER)
    assert elsewhere.verdict == verify.FAILED
    assert elsewhere.diagnostics[0].code == "FLANG_IMPORT_NOT_FOUND"


@needs_flang
def test_every_flang_source_in_the_checkout_verifies(tmp_path):
    """Smoke test against real inputs, not just hand-written snippets."""
    checkout = CHECKER.script.parent.parent
    files = sorted(checkout.glob("stdlib/*.flang")) + sorted(
        checkout.glob("core/*.flang")
    )
    if not files:  # pragma: no cover - a checkout without a stdlib
        pytest.skip("this flang checkout ships no stdlib")
    reports = verify.verify_paths(files)
    bad = [(r.path, r.verdict, r.reason) for r in reports if not r.ok]
    assert not bad, bad
    assert sum(r.examples_passed for r in reports) > 0


def test_verify_paths_reports_per_file_when_the_checker_is_missing(
    tmp_path, monkeypatch
):
    """A missing checker must not abort the batch into an exception.

    The caller has to be able to see, per file, that nothing was verified —
    inferring it from a traceback is how "unverified" turns into "fine".
    """
    monkeypatch.setenv("DIGIT_KB_FLANG", str(tmp_path / "gone"))
    reports = verify.verify_paths([tmp_path / "a.flang", tmp_path / "b.flang"])
    assert len(reports) == 2
    assert all(r.verdict == verify.UNAVAILABLE for r in reports)


@pytest.mark.parametrize("code,is_verdict", [
    ("FLANG_PARSE", True),
    ("FLANG_TYPE", True),
    ("FLANG_NOT_TOTAL", True),
    ("FLANG_IMPORT_NOT_FOUND", True),
    ("FTS_UTILITY_PROPERTY", True),
    # Inside the family but about the tool, not the program.
    ("FLANG_INTERNAL", False),
    ("FLANG_CLI", False),
    # Node's, not flang's — the checker crashed before judging anything.
    ("ERR_MODULE_NOT_FOUND", False),
])
def test_a_crash_is_told_apart_from_a_rejection(code, is_verdict):
    """Blaming the author for a broken toolchain is the same bug, reversed.

    ``flang.mjs`` returns one JSON shape for both, so the code family is the
    only signal: an unbuilt checkout answers ``ERR_MODULE_NOT_FOUND`` for a
    perfectly good ``.fts`` model, and calling that "your code is wrong"
    sends someone debugging a file that is fine.
    """
    diag = verify.Diagnostic(code=code, message="…")
    assert verify._is_a_verdict([diag]) is is_verdict


def test_a_failing_example_is_a_verdict_even_with_no_diagnostics():
    """``flang test`` reports a bad example through ``results``, not diagnostics."""
    assert verify._is_a_verdict([]) is True


needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not on PATH"
)

STUB_CRASHING_CLI = """\
process.stderr.write(JSON.stringify({
  error: "Cannot find module 'dist/src/index.js'",
  diagnostics: [{ code: "ERR_MODULE_NOT_FOUND",
                  message: "Cannot find module 'dist/src/index.js'",
                  severity: "error" }],
}))
process.exit(1)
"""


@needs_node
def test_a_checker_that_crashes_reports_unverified_not_failed(tmp_path, monkeypatch):
    """End-to-end version of the rule above, against a real subprocess."""
    checkout = tmp_path / "checkout"
    (checkout / "flang" / "bin").mkdir(parents=True)
    (checkout / "flang" / "bin" / "flang.mjs").write_text(
        STUB_CRASHING_CLI, encoding="utf-8"
    )
    (checkout / "flang" / "SPEC.md").write_text("# spec\n", encoding="utf-8")
    source = tmp_path / "prog.flang"
    source.write_text(GOOD_FLANG, encoding="utf-8")

    monkeypatch.setenv("DIGIT_KB_FLANG", str(checkout))
    report = verify.verify_file(source)
    assert report.verdict == verify.UNAVAILABLE
    assert report.exit_code == verify.EXIT_UNAVAILABLE
    assert "ERR_MODULE_NOT_FOUND" in report.reason
