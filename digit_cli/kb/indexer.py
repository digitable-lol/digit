"""Corpus discovery and incremental (re)indexing.

The expensive resource is the encoder, not the disk: on a CPU-only ollama a
single 320-word Russian chunk costs seconds, so the whole design is
organised around *not re-embedding text that has not changed*.

Incrementality contract
-----------------------
Each indexed file is recorded with the SHA-256 of its bytes. A run
classifies every corpus file as:

* **unchanged** — digest matches ⇒ skipped entirely, no encoder call;
* **new** — not in ``files`` ⇒ chunked and embedded;
* **changed** — digest differs ⇒ old chunks deleted, re-chunked, re-embedded;
* **deleted** — in ``files`` but no longer on disk ⇒ chunks removed.

Content hashing rather than mtime: ``git checkout`` rewrites mtimes without
changing bytes, and a spurious full re-index costs hours here.

Consequently ``digit kb index`` on an unchanged corpus performs zero
embedding calls, which is the idempotency the spec asks for, and
``digit kb update`` on a one-file edit re-embeds exactly that file.

Resumability
------------
Each file's chunks and vectors are committed as soon as they are embedded
(vectors staged as BLOBs — see :mod:`digit_cli.kb.store`). A run
interrupted after 900 of 1200 files resumes at file 901.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from digit_cli.kb import store
from digit_cli.kb.chunker import chunk_markdown, file_digest, iter_corpus_files
from digit_cli.kb.embed import EMBED_DIM, EmbedError, OllamaClient

# --------------------------------------------------------------------------
# Corpus definition
# --------------------------------------------------------------------------

DEFAULT_CORPUS_ENV = "DIGIT_KB_CORPUS"

#: (source label, path relative to the courses checkout).
CORPUS_SOURCES: tuple[tuple[str, str], ...] = (
    ("courses", "content/post"),
    ("sdd", "docs/sdd"),
    ("workbench", "products/workbench/templates"),
)

_DEFAULT_ROOTS = (
    Path.home() / "projects" / "courses",
    Path("/home/m/projects/courses"),
)


def corpus_root(explicit: Optional[str] = None) -> Path:
    """Locate the Digitable ``courses`` checkout.

    ``--corpus`` → ``DIGIT_KB_CORPUS`` → the conventional checkout paths.
    """
    if explicit:
        root = Path(explicit).expanduser()
        if not root.exists():
            raise store.KBError(f"corpus root does not exist: {root}")
        return root
    env = os.environ.get(DEFAULT_CORPUS_ENV, "").strip()
    if env:
        root = Path(env).expanduser()
        if not root.exists():
            raise store.KBError(
                f"{DEFAULT_CORPUS_ENV} points at a missing path: {root}"
            )
        return root
    for candidate in _DEFAULT_ROOTS:
        if (candidate / "content" / "post").is_dir():
            return candidate
    raise store.KBError(
        "cannot find the courses corpus. Pass --corpus /path/to/courses or "
        f"set {DEFAULT_CORPUS_ENV}."
    )


@dataclass
class CorpusFile:
    path: Path
    rel_path: str
    source: str


def discover(root: Path, tracks: Sequence[str] = ()) -> List[CorpusFile]:
    """Enumerate every Markdown file in the corpus, sorted and deduplicated.

    ``rel_path`` is relative to the *source* directory, so the first segment
    is the track name (``golang/04-concurrency.md``) — which is what the
    chunker derives ``track`` from and what citations show.
    """
    found: List[CorpusFile] = []
    wanted = {t.lower() for t in tracks}
    for source, rel in CORPUS_SOURCES:
        base = root / rel
        if not base.exists():
            continue
        for path in iter_corpus_files([base]):
            rel_path = str(path.relative_to(base))
            if source != "courses":
                rel_path = f"{source}/{rel_path}"
            track = rel_path.split("/")[0] if "/" in rel_path else source
            if wanted and track.lower() not in wanted:
                continue
            found.append(CorpusFile(path=path, rel_path=rel_path, source=source))
    found.sort(key=lambda f: f.rel_path)
    return found


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------


@dataclass
class IndexPlan:
    new: List[CorpusFile] = field(default_factory=list)
    changed: List[CorpusFile] = field(default_factory=list)
    unchanged: List[CorpusFile] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    digests: Dict[str, str] = field(default_factory=dict)

    @property
    def to_embed(self) -> List[CorpusFile]:
        return self.new + self.changed

    @property
    def is_noop(self) -> bool:
        return not self.new and not self.changed and not self.deleted


def plan(
    conn,
    files: Sequence[CorpusFile],
    *,
    force: bool = False,
    scoped: bool = False,
) -> IndexPlan:
    """Diff the corpus against the store.

    ``scoped`` suppresses deletion detection: when the caller restricted the
    scan with ``--track``, files outside that scope are absent from ``files``
    but must not be interpreted as deleted.
    """
    known = store.known_files(conn)
    result = IndexPlan()
    seen: set[str] = set()

    for cf in files:
        seen.add(cf.rel_path)
        digest = file_digest(cf.path)
        result.digests[cf.rel_path] = digest
        prior = known.get(cf.rel_path)
        if prior is None:
            result.new.append(cf)
        elif force or prior.digest != digest:
            result.changed.append(cf)
        else:
            result.unchanged.append(cf)

    if not scoped:
        result.deleted = sorted(set(known) - seen)
    return result


# --------------------------------------------------------------------------
# Indexing
# --------------------------------------------------------------------------


SLAB_REFRESH_FILES = 25
"""Rebuild ``vectors.npy`` every N files during a long run.

Rebuilding is O(total chunks) — a few hundred milliseconds and one file
write at corpus scale — so doing it every 25 files is negligible against
the minutes each file costs to embed, and it keeps a partially built index
usable (and crash-durable) instead of dark until the very end.
"""


@dataclass
class IndexReport:
    files_new: int = 0
    files_changed: int = 0
    files_unchanged: int = 0
    files_deleted: int = 0
    chunks_added: int = 0
    chunks_removed: int = 0
    embed_calls: int = 0
    elapsed: float = 0.0
    vectors: int = 0
    interrupted: bool = False
    error: str = ""

    @property
    def rate(self) -> float:
        return self.chunks_added / self.elapsed if self.elapsed > 0 else 0.0


def run_index(
    *,
    root: Path,
    tracks: Sequence[str] = (),
    force: bool = False,
    limit: int = 0,
    batch_size: int = 8,
    client: Optional[OllamaClient] = None,
    conn=None,
    progress: Optional[Callable[[str], None]] = None,
    dry_run: bool = False,
) -> IndexReport:
    """Index (or incrementally update) the corpus. Returns what it did."""
    if conn is None:
        with store.connect() as own:
            return _run_index(
                root=root, tracks=tracks, force=force, limit=limit,
                batch_size=batch_size, client=client or OllamaClient(),
                conn=own, progress=progress, dry_run=dry_run,
            )
    return _run_index(
        root=root, tracks=tracks, force=force, limit=limit,
        batch_size=batch_size, client=client or OllamaClient(),
        conn=conn, progress=progress, dry_run=dry_run,
    )


def _emit(progress: Optional[Callable[[str], None]], msg: str) -> None:
    if progress:
        progress(msg)


def _run_index(
    *,
    root: Path,
    tracks: Sequence[str],
    force: bool,
    limit: int,
    batch_size: int,
    client: OllamaClient,
    conn,
    progress: Optional[Callable[[str], None]],
    dry_run: bool,
) -> IndexReport:
    started = time.time()
    report = IndexReport()

    scoped = bool(tracks)
    files = discover(root, tracks)
    if not files:
        raise store.KBError(
            f"no Markdown files found under {root} "
            f"{'for tracks ' + ', '.join(tracks) if tracks else ''}".strip()
        )

    _emit(progress, f"corpus: {len(files)} markdown files under {root}")
    the_plan = plan(conn, files, force=force, scoped=scoped)
    report.files_new = len(the_plan.new)
    report.files_changed = len(the_plan.changed)
    report.files_unchanged = len(the_plan.unchanged)
    report.files_deleted = len(the_plan.deleted)

    _emit(
        progress,
        f"plan: {report.files_new} new, {report.files_changed} changed, "
        f"{report.files_unchanged} unchanged, {report.files_deleted} deleted",
    )
    if dry_run:
        report.elapsed = time.time() - started
        return report

    # Existing store must agree with the encoder before anything is written.
    if store.chunk_count(conn) > 0:
        store.assert_compatible(conn, client.embed_model, EMBED_DIM)

    with store.write_txn(conn):
        store.set_meta(conn, "schema_version", store.SCHEMA_VERSION)
        store.set_meta(conn, "embed_model", client.embed_model)
        store.set_meta(conn, "embed_dim", str(EMBED_DIM))
        store.set_meta(conn, "corpus_root", str(root))

    # Files that vanished from disk are dropped up front — there is nothing
    # to replace them with, so holding their stale chunks helps nobody.
    #
    # Files that merely *changed* are NOT dropped here. An earlier version
    # deleted every changed file's chunks before embedding anything, and a
    # real encoder outage mid-run then left those files with no chunks at
    # all: the index silently lost coverage it previously had, and only a
    # successful rerun restored it. Each changed file's old chunks are now
    # deleted inside the same transaction that inserts its new ones, so the
    # replacement is atomic and an outage leaves the *previous* good chunks
    # in place.
    if the_plan.deleted:
        with store.write_txn(conn):
            for rel in the_plan.deleted:
                report.chunks_removed += store.delete_file(conn, rel)

    todo = the_plan.to_embed
    if limit:
        todo = todo[:limit]
    total = len(todo)
    if total == 0:
        _emit(progress, "nothing to embed — index already current")
        # Rebuild the slab when it is out of date with the chunk table, not
        # only after deletions. A previous run that died between committing
        # a file's vectors and materialising them (encoder crash, SIGKILL,
        # power loss) leaves staged BLOBs and no slab rows; if a no-op run
        # skipped the rebuild, the dense channel would stay dark forever
        # while `index` cheerfully reported "already current".
        staged = store.pending_count(conn)
        slab_rows = int(store.get_meta(conn, "n_vectors", "0") or 0)
        if the_plan.deleted or staged or slab_rows != store.chunk_count(conn):
            if staged:
                _emit(progress, f"recovering {staged} staged vector(s) into the slab…")
            report.vectors = store.materialize_slab(conn, EMBED_DIM)
        else:
            report.vectors = slab_rows
        with store.write_txn(conn):
            store.set_meta(conn, "last_index_at", str(time.time()))
        report.elapsed = time.time() - started
        return report

    _emit(progress, f"embedding {total} file(s) with {client.embed_model}…")
    t_embed = time.time()
    try:
        for i, cf in enumerate(todo, start=1):
            try:
                text = cf.path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                _emit(progress, f"  !! {cf.rel_path}: {exc}")
                continue

            chunks = chunk_markdown(cf.rel_path, text)
            if not chunks:
                with store.write_txn(conn):
                    report.chunks_removed += store.delete_file(conn, cf.rel_path)
                    store.upsert_file(conn, store.FileRecord(
                        rel_path=cf.rel_path, source=cf.source,
                        abs_path=str(cf.path),
                        digest=the_plan.digests[cf.rel_path],
                        size=cf.path.stat().st_size,
                        mtime=cf.path.stat().st_mtime,
                        n_chunks=0, indexed_at=time.time(),
                    ))
                continue

            vectors: List[List[float]] = []
            for start in range(0, len(chunks), batch_size):
                batch = chunks[start:start + batch_size]
                vectors.extend(client.embed([c.embed_text for c in batch]))
                report.embed_calls += 1

            with store.write_txn(conn):
                # Atomic replace: the old chunks of a *changed* file die in
                # the same transaction that inserts their replacements.
                report.chunks_removed += store.delete_file(conn, cf.rel_path)
                store.insert_chunks(conn, chunks, vectors)
                st = cf.path.stat()
                store.upsert_file(conn, store.FileRecord(
                    rel_path=cf.rel_path, source=cf.source,
                    abs_path=str(cf.path),
                    digest=the_plan.digests[cf.rel_path],
                    size=st.st_size, mtime=st.st_mtime,
                    n_chunks=len(chunks), indexed_at=time.time(),
                ))
            report.chunks_added += len(chunks)

            done_frac = i / total
            elapsed = time.time() - t_embed
            eta = elapsed / done_frac - elapsed if done_frac else 0.0
            _emit(
                progress,
                f"  [{i}/{total}] {cf.rel_path} — {len(chunks)} chunks "
                f"({report.chunks_added} total, "
                f"{report.chunks_added / max(elapsed, 1e-9):.2f} chunk/s, "
                f"ETA {_fmt_dur(eta)})",
            )

            # Refresh the slab periodically. A full-corpus build takes hours
            # on a CPU encoder; without this the dense channel stays empty
            # for the whole run (and after a SIGKILL, which never reaches
            # the materialise call below), so `kb search` would silently
            # degrade to lexical-only against a half-built index.
            if i % SLAB_REFRESH_FILES == 0 and i != total:
                store.materialize_slab(conn, EMBED_DIM)
                _emit(progress, f"       … slab refreshed at {i}/{total}")
    except KeyboardInterrupt:
        report.interrupted = True
        _emit(progress, "\ninterrupted — committed work is preserved; rerun to resume")
    except EmbedError as exc:
        # The encoder died mid-run — observed for real: under heavy system
        # load ollama's model runner crashed and returned
        # ``400 do embedding request: … EOF`` for every retry.
        #
        # This must NOT abort the way an unexpected exception would. Files
        # already embedded are committed but their vectors are still staged
        # BLOBs; falling through to the slab rebuild below is what makes the
        # dense channel usable for the work that *did* succeed. Without this
        # branch a crash at file 900 of 1200 leaves a lexical-only index
        # until someone reruns the whole command.
        report.interrupted = True
        report.error = str(exc)
        _emit(
            progress,
            f"\nencoder failed: {exc}\n"
            f"committed work is preserved — rerun to resume from here",
        )

    _emit(progress, "materialising vector slab…")
    report.vectors = store.materialize_slab(conn, EMBED_DIM)
    with store.write_txn(conn):
        store.set_meta(conn, "last_index_at", str(time.time()))
    report.elapsed = time.time() - started
    return report


def _fmt_dur(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def rebuild(conn, *, progress: Optional[Callable[[str], None]] = None) -> None:
    """Drop every chunk and vector, keeping the schema. Used by --rebuild."""
    _emit(progress, "dropping existing index…")
    with store.write_txn(conn):
        conn.execute("DELETE FROM chunks")
        conn.execute("DELETE FROM files")
        conn.execute("DELETE FROM meta")
        # Rebuild the FTS index inside the same transaction as the deletes:
        # the ``chunks_ad`` trigger already queued a delete per row, and this
        # collapses the leftover state in one shot.
        conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    vec = store.vectors_path()
    if vec.exists():
        vec.unlink()
    # VACUUM must run outside any transaction — pysqlite leaves an implicit
    # one open after the DML above, which made a bare ``conn.execute("VACUUM")``
    # fail with "cannot VACUUM from within a transaction". Reclaiming here is
    # worth it: a full reset frees the entire corpus's text and BLOBs.
    conn.commit()
    try:
        conn.execute("VACUUM")
    except sqlite3.OperationalError as exc:  # pragma: no cover - lock contention
        _emit(progress, f"  (skipped VACUUM: {exc})")
