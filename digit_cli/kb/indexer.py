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

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Mapping, Optional, Sequence

from digit_cli.kb import store
from digit_cli.kb.chunker import chunk_markdown, file_digest
from digit_cli.kb.embed import EmbedClient, EmbedError

# --------------------------------------------------------------------------
# Corpus definition
# --------------------------------------------------------------------------
#
# The corpus is *several* checkouts, not one. It started as one — the
# Digitable ``courses`` repository — and every slice was expressed as a path
# relative to that single root. flang broke that: it is a different
# repository, on its own release cadence, and a language a day old that the
# generator has never seen, so retrieval is the only way it can be known at
# all.
#
# The shape below therefore separates two things the old tuple conflated:
#
# * a **repository** (:class:`RepoSpec`) — how to find a checkout, and
#   whether the index is allowed to exist without it;
# * a **slice** of one (:class:`SourceSpec`) — which directory and which
#   files inside it, and what the resulting ``rel_path`` looks like.
#
# ``rel_path`` is the identity key between disk and store: :func:`plan`
# diffs against it, so perturbing one for an already-indexed file reads as
# "deleted + new" and re-embeds it. The courses paths below are byte-for-byte
# what the single-root code produced — the ``prefix`` field just makes the
# old ``if source != "courses"`` special case explicit data.

DEFAULT_CORPUS_ENV = "DIGIT_KB_CORPUS"
FLANG_CORPUS_ENV = "DIGIT_KB_FLANG"


@dataclass(frozen=True)
class SourceSpec:
    """One indexable slice of a checkout.

    ``base`` is a directory relative to the repository root and ``rel_path``
    is relative to *that*, which is why the first segment of a courses path
    is the track name (``golang/04-concurrency.md``). ``prefix`` is prepended
    afterwards, so a slice can be namespaced without its files pretending to
    live somewhere they do not.
    """

    source: str
    base: str = ""
    prefix: str = ""
    include: tuple[str, ...] = ("**/*.md",)


@dataclass(frozen=True)
class RepoSpec:
    """A checkout contributing documents to the index.

    ``marker`` is a path that must exist inside a candidate directory for it
    to count as this repository — a bare ``is_dir()`` would happily accept an
    empty directory of the right name and then index nothing.

    ``required=False`` means the index is valid without it. That is not
    politeness: an optional checkout that is merely *absent right now* must
    not be mistaken for content that was *deleted*, or an unmounted disk
    silently drops chunks and the run still reports success. See
    ``protected`` in :class:`ResolvedCorpus`.
    """

    name: str
    env_var: str
    marker: str
    default_roots: tuple[Path, ...]
    sources: tuple[SourceSpec, ...]
    required: bool = True


COURSES_SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec("courses", "content/post"),
    SourceSpec("sdd", "docs/sdd", prefix="sdd"),
    SourceSpec("workbench", "products/workbench/templates", prefix="workbench"),
)

#: Backwards-compatible view of :data:`COURSES_SOURCES` in its original
#: ``(source label, path relative to the courses checkout)`` form.
CORPUS_SOURCES: tuple[tuple[str, str], ...] = tuple(
    (s.source, s.base) for s in COURSES_SOURCES
)

FLANG_SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(
        source="flang",
        prefix="flang",
        # Markdown only, and enumerated rather than swept. The checkout also
        # holds ``.flang`` sources, JS implementation files and per-tool
        # READMEs; ``docs/`` plus the four top-level documents and the
        # language spec are the prose that answers questions about the
        # language. ``README.ru.md`` is listed next to ``README.md`` because
        # the corpus and its queries are Russian and the two files are
        # separate documents, not translations of each other's chunks.
        include=(
            "README.md",
            "README.ru.md",
            "AGENTS.md",
            "CONTRIBUTING.md",
            "flang/SPEC.md",
            "docs/**/*.md",
        ),
    ),
)

COURSES_REPO = RepoSpec(
    name="courses",
    env_var=DEFAULT_CORPUS_ENV,
    marker="content/post",
    default_roots=(
        Path.home() / "projects" / "courses",
        Path("/home/m/projects/courses"),
    ),
    sources=COURSES_SOURCES,
    required=True,
)

FLANG_REPO = RepoSpec(
    name="flang",
    env_var=FLANG_CORPUS_ENV,
    marker="flang/SPEC.md",
    # Only checkouts owned by this account. Other people's clones of flang
    # exist on this machine and move under them; indexing one would make the
    # KB's answers depend on somebody else's uncommitted work.
    default_roots=(
        Path.home() / "flang",
        Path("/home/u/flang"),
    ),
    sources=FLANG_SOURCES,
    required=False,
)

REPOS: tuple[RepoSpec, ...] = (COURSES_REPO, FLANG_REPO)


class MissingCheckout(Exception):
    """A repository could not be located. Fatal only if it is required."""

    def __init__(self, spec: RepoSpec, message: str):
        super().__init__(message)
        self.spec = spec


def _readable(path: Path) -> bool:
    """``path.exists()``, но «нельзя посмотреть» — это тоже «нет».

    ``Path.exists()`` глотает только «нет такого файла»; на запрет доступа он
    поднимает ``PermissionError``. А среди умолчательных мест поиска стоят
    чужие чекауты (``/home/u/flang``), и заглянуть в них с нашего аккаунта
    нельзя. Без этой обёртки ``digit kb index`` падал трассировкой на чужом
    каталоге — то есть необязательный корпус, которого у нас всё равно нет,
    ронял сборку индекса по обязательному.
    """
    try:
        return path.exists()
    except OSError:
        return False


def _resolve_root(spec: RepoSpec, explicit: Optional[str] = None) -> Path:
    """``explicit`` → ``$<env_var>`` → the conventional checkout paths."""
    if explicit:
        root = Path(explicit).expanduser()
        if not _readable(root):
            raise store.KBError(f"corpus root does not exist: {root}")
        return root
    env = os.environ.get(spec.env_var, "").strip()
    if env:
        root = Path(env).expanduser()
        if not _readable(root):
            raise store.KBError(f"{spec.env_var} points at a missing path: {root}")
        return root
    for candidate in spec.default_roots:
        if _readable(candidate / spec.marker):
            return candidate
    looked = ", ".join(str(p) for p in spec.default_roots)
    raise MissingCheckout(
        spec,
        f"no {spec.name} checkout found"
        + (f" (looked for {spec.marker} under {looked})" if looked else "")
        + f"; set {spec.env_var} to index it",
    )


def corpus_root(explicit: Optional[str] = None) -> Path:
    """Locate the Digitable ``courses`` checkout.

    ``--corpus`` → ``DIGIT_KB_CORPUS`` → the conventional checkout paths.
    """
    try:
        return _resolve_root(COURSES_REPO, explicit)
    except MissingCheckout:
        raise store.KBError(
            "cannot find the courses corpus. Pass --corpus /path/to/courses or "
            f"set {DEFAULT_CORPUS_ENV}."
        ) from None


@dataclass(frozen=True)
class ResolvedSource:
    """A :class:`SourceSpec` bound to an actual directory on this machine."""

    source: str
    base: Path
    prefix: str = ""
    include: tuple[str, ...] = ("**/*.md",)


@dataclass
class ResolvedCorpus:
    """Everything a run needs to know about where documents come from."""

    sources: List[ResolvedSource] = field(default_factory=list)
    roots: Dict[str, Path] = field(default_factory=dict)
    #: Human-readable notes about optional repositories that were skipped.
    missing: List[str] = field(default_factory=list)
    #: ``rel_path`` prefixes exempt from deletion detection this run, because
    #: the checkout that owns them is not here to be scanned.
    protected: List[str] = field(default_factory=list)


def sources_for(
    root: Path, spec: Optional[RepoSpec] = None
) -> List[ResolvedSource]:
    """Bind one repository's slices to a concrete checkout directory."""
    spec = spec if spec is not None else COURSES_REPO
    return [
        ResolvedSource(
            source=s.source,
            base=root / s.base if s.base else root,
            prefix=s.prefix,
            include=s.include,
        )
        for s in spec.sources
    ]


def resolve_corpus(
    overrides: Optional[Mapping[str, str]] = None,
    *,
    repos: Optional[Sequence[RepoSpec]] = None,
) -> ResolvedCorpus:
    """Locate every repository in ``repos`` (default: :data:`REPOS`).

    A missing *required* repository is a hard error; a missing optional one
    is recorded in ``missing`` and its ``rel_path`` prefixes are added to
    ``protected`` so this run cannot mistake absence for deletion.

    ``repos`` defaults to ``None`` rather than to :data:`REPOS` directly: a
    default argument is bound once, at import, so ``REPOS`` as the default
    would keep answering with the import-time registry no matter what a
    caller (or a test) had replaced it with — a divergence with no symptom.
    """
    repos = repos if repos is not None else REPOS
    over = dict(overrides or {})
    unknown = set(over) - {r.name for r in repos}
    if unknown:
        raise store.KBError(
            "unknown corpus name(s): " + ", ".join(sorted(unknown))
            + "; known: " + ", ".join(r.name for r in repos)
        )

    out = ResolvedCorpus()
    for spec in repos:
        try:
            root = _resolve_root(spec, over.get(spec.name))
        except MissingCheckout as exc:
            if spec.required:
                raise store.KBError(str(exc)) from None
            out.missing.append(str(exc))
            out.protected.extend(s.prefix for s in spec.sources if s.prefix)
            continue
        # A path the user named explicitly (override or env var) that does not
        # exist stays a hard error even for an optional repo: they asked for
        # it by name, so silently indexing without it would be a lie.
        out.roots[spec.name] = root
        out.sources.extend(sources_for(root, spec))
    return out


@dataclass
class CorpusFile:
    path: Path
    rel_path: str
    source: str


def _iter_source_files(base: Path, include: Sequence[str]) -> Iterator[Path]:
    """Yield the files matching ``include`` under ``base``, deduplicated.

    Deduplication is by *resolved* path so a symlinked document is indexed
    once, and patterns are expanded in the order given so an explicit file
    always wins over a later sweep.
    """
    seen: set[Path] = set()
    for pattern in include:
        for path in sorted(base.glob(pattern)):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield path


def discover_sources(
    sources: Sequence[ResolvedSource], tracks: Sequence[str] = ()
) -> List[CorpusFile]:
    """Enumerate every Markdown file of ``sources``, sorted and deduplicated.

    ``rel_path`` is relative to the source's base directory, so the first
    segment is the track name (``golang/04-concurrency.md``, ``sdd/0001.md``,
    ``flang/docs/language.ru.md``) — which is what the chunker derives
    ``track`` from and what citations show.
    """
    found: List[CorpusFile] = []
    claimed: Dict[str, Path] = {}
    wanted = {t.lower() for t in tracks}
    for src in sources:
        if not src.base.exists():
            continue
        for path in _iter_source_files(src.base, src.include):
            rel_path = str(path.relative_to(src.base))
            if src.prefix:
                rel_path = f"{src.prefix}/{rel_path}"
            # Two repositories can legitimately share a track — the courses
            # corpus has a ``flang`` track of its own, and having both it and
            # the upstream documents answer ``--track flang`` is the point.
            # Two *files* sharing a rel_path cannot: rel_path is the primary
            # key of ``files``, so one would overwrite the other's row and
            # the index would quietly hold whichever was scanned last.
            prior = claimed.get(rel_path)
            if prior is not None and prior != path:
                raise store.KBError(
                    f"two corpus files claim the same identity {rel_path!r}: "
                    f"{prior} and {path}. Give one of their sources a "
                    f"different prefix before indexing."
                )
            claimed[rel_path] = path
            track = rel_path.split("/")[0] if "/" in rel_path else src.source
            if wanted and track.lower() not in wanted:
                continue
            found.append(CorpusFile(path=path, rel_path=rel_path, source=src.source))
    found.sort(key=lambda f: f.rel_path)
    return found


def discover(root: Path, tracks: Sequence[str] = ()) -> List[CorpusFile]:
    """Enumerate the *courses* checkout at ``root``.

    Deliberately single-repository: callers that pass a bare root (tests,
    one-off scans) must get exactly that root's files and never reach out to
    whatever other checkouts happen to exist on the machine. Multi-repository
    runs go through :func:`resolve_corpus` + :func:`discover_sources`.
    """
    return discover_sources(sources_for(root), tracks)


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


def _under_any(rel_path: str, prefixes: Sequence[str]) -> bool:
    return any(
        rel_path == p or rel_path.startswith(p + "/") for p in prefixes if p
    )


def plan(
    conn,
    files: Sequence[CorpusFile],
    *,
    force: bool = False,
    scoped: bool = False,
    protected_prefixes: Sequence[str] = (),
) -> IndexPlan:
    """Diff the corpus against the store.

    ``scoped`` suppresses deletion detection: when the caller restricted the
    scan with ``--track``, files outside that scope are absent from ``files``
    but must not be interpreted as deleted.

    ``protected_prefixes`` is the same idea for a whole repository. When an
    optional checkout is not on this machine, its already-indexed files are
    likewise absent from ``files`` — and dropping their chunks would turn an
    unmounted disk or a renamed directory into silent, invisible data loss
    that the run still reports as success. They are left alone until the
    checkout is back and can actually be compared against.
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
        gone = set(known) - seen
        if protected_prefixes:
            gone = {r for r in gone if not _under_any(r, protected_prefixes)}
        result.deleted = sorted(gone)
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
    batch_size: int = 32,
    client: Optional[EmbedClient] = None,
    conn=None,
    progress: Optional[Callable[[str], None]] = None,
    dry_run: bool = False,
    corpus: Optional[ResolvedCorpus] = None,
) -> IndexReport:
    """Index (or incrementally update) the corpus. Returns what it did.

    ``corpus`` carries the resolved multi-repository layout. Without it the
    run covers exactly the courses checkout at ``root`` and nothing else —
    which is what a caller passing a bare ``root`` means, and keeps a scan of
    one directory from silently pulling in unrelated checkouts.
    """
    if conn is None:
        with store.connect() as own:
            return _run_index(
                root=root, tracks=tracks, force=force, limit=limit,
                batch_size=batch_size, client=client or EmbedClient(),
                conn=own, progress=progress, dry_run=dry_run, corpus=corpus,
            )
    return _run_index(
        root=root, tracks=tracks, force=force, limit=limit,
        batch_size=batch_size, client=client or EmbedClient(),
        conn=conn, progress=progress, dry_run=dry_run, corpus=corpus,
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
    client: EmbedClient,
    conn,
    progress: Optional[Callable[[str], None]],
    dry_run: bool,
    corpus: Optional[ResolvedCorpus] = None,
) -> IndexReport:
    started = time.time()
    report = IndexReport()

    if corpus is None:
        corpus = ResolvedCorpus(
            sources=sources_for(root), roots={COURSES_REPO.name: root}
        )

    scoped = bool(tracks)
    files = discover_sources(corpus.sources, tracks)
    where = ", ".join(str(p) for p in corpus.roots.values()) or str(root)
    if not files:
        raise store.KBError(
            f"no Markdown files found under {where} "
            f"{'for tracks ' + ', '.join(tracks) if tracks else ''}".strip()
        )

    for note in corpus.missing:
        _emit(progress, f"corpus: skipping — {note}")
    _emit(progress, f"corpus: {len(files)} markdown files under {where}")
    the_plan = plan(
        conn, files, force=force, scoped=scoped,
        protected_prefixes=corpus.protected,
    )
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
        store.assert_compatible(conn, client.embed_model, client.embed_dim)

    with store.write_txn(conn):
        store.set_meta(conn, "schema_version", store.SCHEMA_VERSION)
        store.set_meta(conn, "embed_model", client.embed_model)
        store.set_meta(conn, "embed_dim", str(client.embed_dim))
        # ``corpus_root`` stays the courses checkout for compatibility with
        # anything reading the old single-root key; ``corpus_roots`` records
        # the full layout so a later run can say which repositories the
        # index actually covers.
        store.set_meta(conn, "corpus_root", str(root))
        store.set_meta(
            conn, "corpus_roots",
            json.dumps({k: str(v) for k, v in corpus.roots.items()},
                       ensure_ascii=False, sort_keys=True),
        )

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
            report.vectors = store.materialize_slab(conn, client.embed_dim)
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
                store.materialize_slab(conn, client.embed_dim)
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
    report.vectors = store.materialize_slab(conn, client.embed_dim)
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
