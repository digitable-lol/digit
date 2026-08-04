"""On-disk storage for the Digitable knowledge base.

Layout — everything lives under ``$DIGIT_HOME/kb`` (profile-aware, via
:func:`digit_constants.get_digit_home`)::

    ~/.digit/kb/
        kb.db        SQLite: meta, files, chunks, chunks_fts (FTS5)
        vectors.npy  float32 (N, D), L2-normalised, row i ↔ chunks.row = i

``D`` is a property of the encoder, not a constant: 4096 for
``Qwen/Qwen3-Embedding-8B``, 768 for ``nomic-embed-text``. It is recorded
in ``meta`` alongside the model name and enforced by
:func:`assert_compatible` on every read, because a slab mixing two widths
(or two models at the same width) answers confidently and wrongly instead
of failing.

Why SQLite + a raw ``.npy`` slab and not a vector database: the corpus is
~1200 documents / ~3·10^4 chunks. A brute-force ``matrix @ query`` over a
~500 MB float32 array is tens of milliseconds of numpy and is *exact* — an
ANN index would add a dependency, an approximation, and a build step to buy
nothing at this scale.

**Row alignment is the load-bearing invariant.** ``chunks.row`` is the
index into ``vectors.npy``. It is assigned only when the slab is
materialised, inside the same transaction that writes the file, and a
partial unique index enforces that no two chunks claim the same row.

**Durability during a long index.** Embedding the full corpus takes hours
(the encoder is a shared, single-stream GPU server), so an index run must
survive interruption.
Freshly computed vectors are committed per-file into ``chunks.vec`` (a
float32 BLOB) as they arrive. The ``.npy`` slab is rebuilt from
(surviving old rows ∪ new BLOBs) at the end of the run via
write-tmp-then-``os.replace``; the BLOBs are then dropped. Kill the
process at any point and the next run resumes from the last completed
file rather than from zero.
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Sequence

from digit_constants import get_digit_home

SCHEMA_VERSION = "1"

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def kb_dir() -> Path:
    """The KB directory for the active profile (``$DIGIT_HOME/kb``)."""
    return get_digit_home() / "kb"


def db_path() -> Path:
    return kb_dir() / "kb.db"


def vectors_path() -> Path:
    return kb_dir() / "vectors.npy"


def _wal_path() -> Path:
    return kb_dir() / "kb.db-wal"


def _shm_path() -> Path:
    return kb_dir() / "kb.db-shm"


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    rel_path   TEXT PRIMARY KEY,
    source     TEXT NOT NULL,
    abs_path   TEXT NOT NULL,
    digest     TEXT NOT NULL,
    size       INTEGER NOT NULL,
    mtime      REAL NOT NULL,
    n_chunks   INTEGER NOT NULL DEFAULT 0,
    indexed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    rel_path TEXT NOT NULL,
    track    TEXT NOT NULL,
    title    TEXT NOT NULL,
    heading  TEXT NOT NULL DEFAULT '',
    ordinal  INTEGER NOT NULL,
    words    INTEGER NOT NULL,
    text     TEXT NOT NULL,
    body     TEXT NOT NULL,
    row      INTEGER,
    vec      BLOB
);

CREATE INDEX IF NOT EXISTS idx_chunks_path  ON chunks(rel_path);
CREATE INDEX IF NOT EXISTS idx_chunks_track ON chunks(track);
CREATE UNIQUE INDEX IF NOT EXISTS idx_chunks_row
    ON chunks(row) WHERE row IS NOT NULL;

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    content='chunks',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text)
        VALUES ('delete', old.id, old.text);
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE OF text ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text)
        VALUES ('delete', old.id, old.text);
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
"""


class KBError(RuntimeError):
    """Any recoverable knowledge-base fault (missing index, model drift…)."""


@contextmanager
def connect(path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """Open the KB database, creating the schema on first use.

    Always closed on exit: the dashboard and gateway are long-lived
    processes and leaked SQLite file descriptors have bitten this codebase
    before (see ``projects_db.connect_closing``).
    """
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=30.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        # Only run the DDL when the schema is actually absent. ``CREATE TABLE
        # IF NOT EXISTS`` is cheap but still opens a write transaction, and a
        # read-only command (``kb search``) must not contend with a running
        # ``kb index`` for the write lock just to confirm tables it already
        # knows exist.
        if not _schema_present(conn):
            conn.executescript(SCHEMA_SQL)
        yield conn
    finally:
        conn.close()


def _schema_present(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM sqlite_master "
        "WHERE type IN ('table','trigger') AND name IN "
        "('meta','files','chunks','chunks_fts','chunks_ai','chunks_ad','chunks_au')"
    ).fetchone()
    return int(row["n"]) >= 7


@contextmanager
def write_txn(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """IMMEDIATE write transaction; rolls back on any exception."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise
    else:
        conn.execute("COMMIT")


# --------------------------------------------------------------------------
# meta
# --------------------------------------------------------------------------


def get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def assert_compatible(conn: sqlite3.Connection, model: str, dim: int) -> None:
    """Refuse to query a store built with a different encoder.

    Vectors from two models occupy unrelated spaces; mixing a 768-dim
    nomic slab with a 4096-dim Qwen3 query produces confident nonsense
    rather than an error, so this is checked explicitly on every search
    rather than trusted. It is the whole reason the model name and the
    dimension are persisted in ``meta`` instead of living as a constant
    beside the code that happens to be running.

    An index that has chunks but *no* recorded encoder is also refused: it
    predates this check, so nothing can vouch for what produced its
    vectors, and guessing is exactly the silent-wrong-answer this function
    exists to prevent.
    """
    stored_model = get_meta(conn, "embed_model")
    stored_dim = get_meta(conn, "embed_dim")
    stored_schema = get_meta(conn, "schema_version")
    if stored_schema and stored_schema != SCHEMA_VERSION:
        raise KBError(
            f"Index schema v{stored_schema} != code v{SCHEMA_VERSION}. "
            f"Run `digit kb index --rebuild`."
        )
    if chunk_count(conn) > 0 and not (stored_model and stored_dim):
        raise KBError(
            "The index has chunks but records no embedding model/dimension, "
            "so its vectors cannot be trusted to match the configured "
            f"encoder ({model}, {dim} dims). Run `digit kb index --rebuild`."
        )
    if stored_model and stored_model != model:
        raise KBError(
            f"Index was built with embedding model {stored_model!r}, but "
            f"{model!r} is configured. Vectors from different models are not "
            f"comparable. Run `digit kb index --rebuild` or pass "
            f"--embed-model {stored_model}."
        )
    if stored_dim and int(stored_dim) != dim:
        raise KBError(
            f"Index dimension {stored_dim} != encoder dimension {dim} "
            f"({model}). Vectors of different widths cannot be compared at "
            f"all. Run `digit kb index --rebuild`."
        )


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


@dataclass
class FileRecord:
    rel_path: str
    source: str
    abs_path: str
    digest: str
    size: int
    mtime: float
    n_chunks: int
    indexed_at: float


def known_files(conn: sqlite3.Connection) -> dict[str, FileRecord]:
    rows = conn.execute(
        "SELECT rel_path, source, abs_path, digest, size, mtime, n_chunks, indexed_at "
        "FROM files"
    ).fetchall()
    return {
        r["rel_path"]: FileRecord(
            r["rel_path"], r["source"], r["abs_path"], r["digest"],
            r["size"], r["mtime"], r["n_chunks"], r["indexed_at"],
        )
        for r in rows
    }


def delete_file(conn: sqlite3.Connection, rel_path: str) -> int:
    """Drop a file and its chunks. Returns the number of chunks removed."""
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE rel_path = ?", (rel_path,)
    ).fetchone()["n"]
    conn.execute("DELETE FROM chunks WHERE rel_path = ?", (rel_path,))
    conn.execute("DELETE FROM files WHERE rel_path = ?", (rel_path,))
    return int(n)


def upsert_file(conn: sqlite3.Connection, rec: FileRecord) -> None:
    conn.execute(
        "INSERT INTO files(rel_path, source, abs_path, digest, size, mtime, "
        "                  n_chunks, indexed_at) "
        "VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(rel_path) DO UPDATE SET "
        "  source=excluded.source, abs_path=excluded.abs_path, "
        "  digest=excluded.digest, size=excluded.size, mtime=excluded.mtime, "
        "  n_chunks=excluded.n_chunks, indexed_at=excluded.indexed_at",
        (rec.rel_path, rec.source, rec.abs_path, rec.digest, rec.size,
         rec.mtime, rec.n_chunks, rec.indexed_at),
    )


def insert_chunks(conn: sqlite3.Connection, chunks: Sequence, vectors) -> None:
    """Insert freshly embedded chunks with their vectors staged as BLOBs.

    ``row`` stays NULL until :func:`materialize_slab` assigns it, so a
    crash between here and the slab write leaves the store consistent:
    the chunks exist, carry their vectors, and are simply not yet
    searchable via the dense path.
    """
    import numpy as np

    payload = []
    for chunk, vec in zip(chunks, vectors):
        arr = np.asarray(vec, dtype=np.float32)
        payload.append(
            (chunk.rel_path, chunk.track, chunk.title, chunk.heading,
             chunk.ordinal, len(chunk.body.split()), chunk.embed_text,
             chunk.body, arr.tobytes())
        )
    conn.executemany(
        "INSERT INTO chunks(rel_path, track, title, heading, ordinal, words, "
        "                   text, body, row, vec) "
        "VALUES(?,?,?,?,?,?,?,?,NULL,?)",
        payload,
    )


def pending_count(conn: sqlite3.Connection) -> int:
    """Chunks holding a staged vector that is not yet in the slab."""
    return int(
        conn.execute("SELECT COUNT(*) AS n FROM chunks WHERE vec IS NOT NULL")
        .fetchone()["n"]
    )


def chunk_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"])


# --------------------------------------------------------------------------
# The vector slab
# --------------------------------------------------------------------------


def load_matrix(dim: int):
    """Memory-map ``vectors.npy``. Returns an empty (0, dim) array if absent."""
    import numpy as np

    path = vectors_path()
    if not path.exists():
        return np.zeros((0, dim), dtype=np.float32)
    mat = np.load(str(path), mmap_mode="r")
    if mat.ndim != 2 or mat.shape[1] != dim:
        raise KBError(
            f"{path} has shape {mat.shape}; expected (N, {dim}). "
            f"Run `digit kb index --rebuild`."
        )
    return mat


def materialize_slab(conn: sqlite3.Connection, dim: int) -> int:
    """Rebuild ``vectors.npy`` so that row i ↔ the i-th chunk, and clear BLOBs.

    Gathers, in one pass over ``chunks`` ordered by id:

    * chunks that already had a ``row`` → copied from the existing slab;
    * chunks with a staged ``vec`` BLOB → decoded from SQLite.

    The new slab is written to a temp file and ``os.replace``-d into place
    (atomic on POSIX), and only then is the row renumbering committed. If
    the process dies before the rename, the old slab and the old ``row``
    values are still mutually consistent.

    Returns the number of rows in the new slab.
    """
    import numpy as np

    rows = conn.execute(
        "SELECT id, row, vec IS NOT NULL AS has_vec FROM chunks ORDER BY id"
    ).fetchall()
    if not rows:
        _atomic_save(np.zeros((0, dim), dtype=np.float32))
        with write_txn(conn):
            set_meta(conn, "n_vectors", "0")
        return 0

    old = load_matrix(dim)
    out = np.zeros((len(rows), dim), dtype=np.float32)
    renumber: List[tuple[int, int]] = []
    missing = 0

    for new_row, r in enumerate(rows):
        if r["has_vec"]:
            blob = conn.execute(
                "SELECT vec FROM chunks WHERE id = ?", (r["id"],)
            ).fetchone()["vec"]
            vec = np.frombuffer(blob, dtype=np.float32)
            if vec.shape[0] != dim:
                raise KBError(
                    f"Staged vector for chunk {r['id']} has dim {vec.shape[0]}, "
                    f"expected {dim}."
                )
            out[new_row] = vec
        elif r["row"] is not None and r["row"] < old.shape[0]:
            out[new_row] = old[r["row"]]
        else:
            # Should be unreachable; a chunk with neither a staged vector
            # nor a valid old row cannot be searched, so surface it.
            missing += 1
        renumber.append((new_row, r["id"]))

    if missing:
        raise KBError(
            f"{missing} chunk(s) have no vector (neither staged nor in the "
            f"old slab). Run `digit kb index --rebuild`."
        )

    _normalize_inplace(out)
    _atomic_save(out)

    with write_txn(conn):
        # Clear rows first: the partial unique index on ``row`` would
        # otherwise collide while the renumbering is half applied.
        conn.execute("UPDATE chunks SET row = NULL")
        conn.executemany("UPDATE chunks SET row = ? WHERE id = ?", renumber)
        conn.execute("UPDATE chunks SET vec = NULL WHERE vec IS NOT NULL")
        set_meta(conn, "n_vectors", str(out.shape[0]))
        set_meta(conn, "slab_built_at", str(time.time()))
    _maybe_vacuum(conn)
    return out.shape[0]


VACUUM_FREE_PAGE_THRESHOLD = 2048
"""Reclaim the DB file only once enough pages are actually free.

Clearing the staged ``vec`` BLOBs frees a lot of space (16 KB each at 4096
float32 dims, and a full index stages ~32 thousand of them — half a
gigabyte), so without a VACUUM the file stays several times larger than its
live data. But VACUUM rewrites the entire
database, which at full corpus size is not free, and running it after every
incremental one-file update would be pure waste. So it is gated on the
freelist actually being large.
"""


def _maybe_vacuum(conn: sqlite3.Connection) -> None:
    """VACUUM only when the freelist is big enough to be worth the rewrite.

    Must run outside a transaction — SQLite refuses ``VACUUM`` inside one —
    hence the explicit commit of any implicit transaction first.
    """
    try:
        free = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    except (sqlite3.Error, TypeError, IndexError):
        return
    if free < VACUUM_FREE_PAGE_THRESHOLD:
        return
    try:
        conn.commit()
        conn.execute("VACUUM")
    except sqlite3.OperationalError:
        # A concurrent reader can block VACUUM; the space is reclaimed on a
        # later run and nothing is corrupted, so this is not fatal.
        pass


def _normalize_inplace(mat) -> None:
    """L2-normalise rows so cosine similarity is a plain dot product."""
    import numpy as np

    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    np.maximum(norms, 1e-12, out=norms)
    mat /= norms


def _atomic_save(mat) -> None:
    """Write the slab to a temp file, fsync, then rename over the old one.

    ``np.save`` is handed an open file object rather than a path: given a
    *path* it appends ``.npy`` unless the name already ends in it, so
    ``np.save("vectors.npy.tmp", …)`` silently writes
    ``vectors.npy.tmp.npy`` and the rename below fails with ENOENT. Passing
    a file object suppresses that behaviour entirely.

    The fsync before the rename is what makes the replacement crash-safe:
    without it the rename can land in the directory entry while the file's
    contents are still in page cache, leaving a truncated slab after a
    power loss.
    """
    import numpy as np

    path = vectors_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        np.save(fh, mat, allow_pickle=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(str(tmp), str(path))
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


# --------------------------------------------------------------------------
# Stats
# --------------------------------------------------------------------------


def index_stats(conn: sqlite3.Connection) -> dict:
    """Everything ``digit kb status`` reports."""
    n_files = conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"]
    n_chunks = chunk_count(conn)
    n_tracks = conn.execute(
        "SELECT COUNT(DISTINCT track) AS n FROM chunks"
    ).fetchone()["n"]
    words = conn.execute("SELECT COALESCE(SUM(words), 0) AS n FROM chunks").fetchone()["n"]
    # Count the WAL and shared-memory files too: in WAL mode a just-written
    # index can have most of its bytes still in ``kb.db-wal``, so reporting
    # ``kb.db`` alone understates the on-disk footprint (dramatically so
    # while an index run is in flight).
    db_bytes = sum(
        p.stat().st_size
        for p in (db_path(), _wal_path(), _shm_path())
        if p.exists()
    )
    vec_bytes = vectors_path().stat().st_size if vectors_path().exists() else 0
    return {
        "files": int(n_files),
        "chunks": int(n_chunks),
        "tracks": int(n_tracks),
        "words": int(words),
        "pending": pending_count(conn),
        "db_bytes": db_bytes,
        "vec_bytes": vec_bytes,
        "embed_model": get_meta(conn, "embed_model"),
        "embed_dim": get_meta(conn, "embed_dim"),
        "n_vectors": get_meta(conn, "n_vectors", "0"),
        "last_index": get_meta(conn, "last_index_at"),
        "kb_dir": str(kb_dir()),
    }


def track_breakdown(conn: sqlite3.Connection, limit: int = 0) -> List[sqlite3.Row]:
    sql = (
        "SELECT track, COUNT(*) AS chunks, COUNT(DISTINCT rel_path) AS files "
        "FROM chunks GROUP BY track ORDER BY chunks DESC"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()
