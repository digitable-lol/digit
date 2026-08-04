"""``digit kb`` — parser construction and command handlers.

Registration follows the self-contained pattern used by
``digit_cli/portal_cli.py``: this module owns both the argparse tree and
the handler, sets ``func`` itself via ``set_defaults``, and ``main()``
only has to call :func:`add_parser`. The alternative in-tree convention
(``digit_cli/subcommands/<group>.py`` with a handler injected from
``main.py``) exists to break an import cycle for handlers that live in
``main``; ``kb``'s handlers do not, so the self-contained form keeps the
whole subsystem in one package.
"""

from __future__ import annotations

import argparse
import sys
import time

# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def add_parser(subparsers) -> argparse.ArgumentParser:
    """Attach the ``kb`` subcommand tree. Returns the top parser."""
    parser = subparsers.add_parser(
        "kb",
        help="Offline knowledge base over the Digitable courses corpus",
        description=(
            "A fully offline retrieval-augmented knowledge base built from the "
            "Digitable corpus (course articles, SDD documents, workbench "
            "templates).\n\n"
            "Embeddings and answer synthesis both run on a loopback ollama; "
            "the KB never contacts a non-loopback address.\n\n"
            "Typical flow:\n"
            "  digit kb index                 # first full build\n"
            "  digit kb update                # incremental, only changed files\n"
            "  digit kb search \"горутины\"     # ranked passages with sources\n"
            "  digit kb ask \"...?\"            # cited answer, or an honest refusal"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="kb_command")

    def _common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--host", default=None,
                       help="ollama base URL; must be loopback "
                            "(default http://127.0.0.1:11435)")
        p.add_argument("--embed-model", default=None,
                       help="embedding model (default nomic-embed-text)")

    # -- index / update ---------------------------------------------------
    p_index = sub.add_parser(
        "index",
        help="Build the index (incremental; safe to re-run)",
        description=(
            "Scans the corpus, embeds new and changed files, and drops chunks "
            "of files that disappeared. Unchanged files are skipped by content "
            "hash, so re-running on an untouched corpus costs zero encoder "
            "calls. Interrupting is safe: completed files are committed and "
            "the next run resumes."
        ),
    )
    _common(p_index)
    p_index.add_argument("--corpus", default=None,
                         help="path to the courses checkout (default: autodetect / $DIGIT_KB_CORPUS)")
    p_index.add_argument("--track", action="append", default=[],
                         help="restrict to a track (repeatable), e.g. --track golang")
    p_index.add_argument("--rebuild", action="store_true",
                         help="drop the existing index first (full re-embed)")
    p_index.add_argument("--force", action="store_true",
                         help="re-embed every file even if unchanged")
    p_index.add_argument("--limit", type=int, default=0,
                         help="stop after N files (for smoke tests)")
    p_index.add_argument("--batch-size", type=int, default=8,
                         help="chunks per encoder request (default 8)")
    p_index.add_argument("--dry-run", action="store_true",
                         help="report the plan without embedding anything")
    p_index.add_argument("--quiet", action="store_true", help="only print the summary")

    p_update = sub.add_parser(
        "update",
        help="Incremental refresh (alias of `index`, kept for the documented workflow)",
    )
    _common(p_update)
    p_update.add_argument("--corpus", default=None)
    p_update.add_argument("--track", action="append", default=[])
    p_update.add_argument("--limit", type=int, default=0)
    p_update.add_argument("--batch-size", type=int, default=8)
    p_update.add_argument("--dry-run", action="store_true")
    p_update.add_argument("--quiet", action="store_true")

    # -- search -----------------------------------------------------------
    p_search = sub.add_parser(
        "search", help="Hybrid (semantic + full-text) passage search",
        description=(
            "Ranks passages by Reciprocal Rank Fusion of a dense vector search "
            "and an FTS5/BM25 search. Prints the path, track, article title, "
            "heading and both component scores for each hit."
        ),
    )
    _common(p_search)
    p_search.add_argument("query", help="what to look for")
    p_search.add_argument("-k", type=int, default=5, help="number of results (default 5)")
    p_search.add_argument("--track", default=None, help="restrict results to one track")
    p_search.add_argument("--full", action="store_true", help="print whole chunks")
    p_search.add_argument("--json", action="store_true", dest="as_json",
                          help="machine-readable output")

    # -- ask --------------------------------------------------------------
    p_ask = sub.add_parser(
        "ask", help="Answer a question from the knowledge base, with sources",
        description=(
            "Retrieves supporting passages, then decides from retrieval "
            "statistics whether the corpus can support an answer at all. If it "
            "cannot, the generator is never invoked and the command says so. "
            "Otherwise a local model answers using only the retrieved passages "
            "and cites them by number.\n\n"
            "Exit code 3 means 'not in the knowledge base' — scripts can "
            "distinguish an honest refusal from an error (1) or an answer (0)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _common(p_ask)
    p_ask.add_argument("question")
    p_ask.add_argument("-k", type=int, default=6, help="passages to ground on (default 6)")
    p_ask.add_argument("--model", default=None,
                       help="generation model (default qwen3.5:4b; "
                            "`kb doctor` lists what the endpoint serves)")
    p_ask.add_argument("--track", default=None, help="restrict to one track")
    p_ask.add_argument("--show-sources", action="store_true",
                       help="print the full text of each cited passage")
    p_ask.add_argument("--json", action="store_true", dest="as_json")

    # -- housekeeping -----------------------------------------------------
    p_status = sub.add_parser("status", help="Index size, model, freshness, tracks")
    p_status.add_argument("--json", action="store_true", dest="as_json")
    p_status.add_argument("--tracks", action="store_true", help="per-track breakdown")

    p_doctor = sub.add_parser("doctor", help="Check ollama, models and index health")
    _common(p_doctor)

    sub.add_parser("tracks", help="List indexed tracks with chunk counts")

    p_reset = sub.add_parser("reset", help="Delete the index (keeps the corpus)")
    p_reset.add_argument("--yes", "-y", action="store_true", help="skip confirmation")

    parser.set_defaults(func=kb_command)
    return parser


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------


def _client(args):
    from digit_cli.kb.embed import DEFAULT_EMBED_MODEL, OllamaClient

    return OllamaClient(
        host=getattr(args, "host", None),
        embed_model=getattr(args, "embed_model", None) or DEFAULT_EMBED_MODEL,
    )


def _require_numpy() -> None:
    """numpy is the only third-party dependency, and only for the slab.

    Routed through the repo's lazy-install facility so a plain install picks
    it up on first use instead of failing, matching how ``voice``/``wake``
    handle their extras.
    """
    try:
        import numpy  # noqa: F401
        return
    except ImportError:
        pass
    try:
        from tools import lazy_deps

        lazy_deps.ensure("kb")
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            "digit kb needs numpy for the vector slab and it is not installed.\n"
            f"  reason: {exc}\n"
            "  fix:    pip install 'numpy==2.4.3'"
        ) from exc


def _human_bytes(n: int) -> str:
    step = 1024.0
    val = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if val < step or unit == "GB":
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= step
    return f"{val:.1f} GB"


def _fmt_dur(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def kb_command(args) -> int:
    """Dispatch ``digit kb <subcommand>``."""
    from digit_cli.kb import store
    from digit_cli.kb.embed import EmbedError, OfflineViolation

    command = getattr(args, "kb_command", None) or "status"
    try:
        if command in ("index", "update"):
            return _cmd_index(args, is_update=(command == "update"))
        if command == "search":
            return _cmd_search(args)
        if command == "ask":
            return _cmd_ask(args)
        if command == "status":
            return _cmd_status(args)
        if command == "tracks":
            return _cmd_tracks(args)
        if command == "doctor":
            return _cmd_doctor(args)
        if command == "reset":
            return _cmd_reset(args)
    except store.KBError as exc:
        print(f"kb: {exc}", file=sys.stderr)
        return 1
    except OfflineViolation as exc:
        print(f"kb: {exc}", file=sys.stderr)
        return 1
    except EmbedError as exc:
        # A model that will not load (CUDA OOM on a shared GPU is the common
        # one) is an operational condition, not a bug — report it as advice
        # rather than a traceback.
        print(f"kb: {exc}", file=sys.stderr)
        print(
            "     try a smaller model (`digit kb ask --model <name>`); "
            "`digit kb doctor` shows what the endpoint serves.",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130

    print(f"kb: unknown subcommand {command!r}", file=sys.stderr)
    return 2


def _cmd_index(args, *, is_update: bool) -> int:
    _require_numpy()
    from digit_cli.kb import indexer, store
    from digit_cli.kb.embed import EmbedError

    quiet = bool(getattr(args, "quiet", False))
    progress = (lambda m: None) if quiet else (lambda m: print(m, flush=True))

    root = indexer.corpus_root(getattr(args, "corpus", None))
    client = _client(args)

    try:
        client.version()
    except EmbedError as exc:
        print(f"kb: {exc}", file=sys.stderr)
        return 1

    with store.connect() as conn:
        if getattr(args, "rebuild", False):
            indexer.rebuild(conn, progress=progress)

        report = indexer.run_index(
            root=root,
            tracks=getattr(args, "track", []) or [],
            force=getattr(args, "force", False),
            limit=getattr(args, "limit", 0),
            batch_size=getattr(args, "batch_size", 8),
            client=client,
            conn=conn,
            progress=progress,
            dry_run=getattr(args, "dry_run", False),
        )
        stats = store.index_stats(conn)

    verb = "update" if is_update else "index"
    if getattr(args, "dry_run", False):
        print(
            f"\n{verb} (dry run): {report.files_new} new, "
            f"{report.files_changed} changed, {report.files_unchanged} unchanged, "
            f"{report.files_deleted} deleted — nothing embedded"
        )
        return 0

    print(
        f"\n{verb}: {report.files_new} new + {report.files_changed} changed file(s) "
        f"embedded, {report.files_unchanged} unchanged skipped, "
        f"{report.files_deleted} deleted"
    )
    print(
        f"  chunks:   +{report.chunks_added}  -{report.chunks_removed}  "
        f"(total {stats['chunks']} over {stats['files']} files, "
        f"{stats['tracks']} tracks)"
    )
    print(
        f"  encoder:  {report.embed_calls} request(s), "
        f"{report.rate:.2f} chunk/s, {_fmt_dur(report.elapsed)} elapsed"
    )
    print(
        f"  on disk:  {_human_bytes(stats['db_bytes'])} sqlite + "
        f"{_human_bytes(stats['vec_bytes'])} vectors "
        f"({stats['n_vectors']} × {stats['embed_dim']} float32)"
    )
    if report.interrupted:
        if report.error:
            print(f"  NOTE: stopped early — {report.error}")
        print("  NOTE: rerun to resume where it stopped (nothing is lost)")
        return 130
    return 0


def _cmd_search(args) -> int:
    _require_numpy()
    from digit_cli.kb.search import search

    t0 = time.time()
    result = search(
        args.query, k=args.k, client=_client(args),
        track=getattr(args, "track", None),
    )
    elapsed = time.time() - t0

    if getattr(args, "as_json", False):
        import json

        print(json.dumps({
            "query": result.query,
            "terms": result.terms,
            "attested": result.attested,
            "coverage": result.coverage,
            "dense_max": result.dense_max,
            "hits": [{
                "path": h.rel_path, "track": h.track, "title": h.title,
                "heading": h.heading, "ordinal": h.ordinal,
                "rrf": h.score, "cosine": h.dense, "bm25": h.bm25,
                "text": h.body,
            } for h in result.hits],
        }, ensure_ascii=False, indent=2))
        return 0

    if not result.hits:
        print(f'Ничего не найдено по запросу "{result.query}".')
        _print_diagnostics(result)
        return 1

    print(f'Запрос: "{result.query}"   ({len(result.hits)} из пула, {elapsed:.2f}s)\n')
    for i, hit in enumerate(result.hits, start=1):
        dense = f"cos={hit.dense:.4f}" if hit.dense is not None else "cos=—"
        bm25 = f"bm25={hit.bm25:.2f}" if hit.bm25 is not None else "bm25=—"
        print(f"{i}. [{hit.track}] {hit.title}")
        print(f"   {hit.rel_path}  chunk#{hit.ordinal}")
        if hit.heading:
            print(f"   раздел: {hit.heading}")
        print(f"   score={hit.score:.5f}  {dense}  {bm25}")
        body = hit.body if getattr(args, "full", False) else hit.snippet()
        for line in body.splitlines() if getattr(args, "full", False) else [body]:
            print(f"   │ {line}")
        print()
    _print_diagnostics(result)
    return 0


def _print_diagnostics(result) -> None:
    attested = ", ".join(
        f"{t}={result.attested.get(t, 0)}" for t in result.terms
    ) or "—"
    print(
        f"диагностика: опора лучшего фрагмента {result.support:.0%} "
        f"(доля терминов запроса в одном чанке) | "
        f"покрытие по корпусу {result.coverage:.0%} ({attested}) | "
        f"лучший cos={result.dense_max:.4f}"
    )
    if result.unattested:
        print(
            "  термины, которых нет в корпусе вообще: "
            + ", ".join(result.unattested)
        )
    if not result.dense_available:
        print(
            "  ВНИМАНИЕ: эмбеддер недоступен — работает только лексический "
            "канал (FTS5/BM25). Результаты валидны, но ранжирование без "
            "семантики."
        )
    if not result.dense_complete:
        print(
            f"  ВНИМАНИЕ: индексация не завершена — векторов "
            f"{result.n_vectors} из {result.n_chunks} чанков. "
            f"Семантический канал частичный, ранжирование не финальное."
        )


def _cmd_ask(args) -> int:
    _require_numpy()
    from digit_cli.kb.ask import ABSENT, ask

    t0 = time.time()
    answer = ask(
        args.question, k=args.k, client=_client(args),
        model=getattr(args, "model", None), track=getattr(args, "track", None),
    )
    elapsed = time.time() - t0

    if getattr(args, "as_json", False):
        import json

        print(json.dumps({
            "question": answer.question,
            "verdict": answer.verdict,
            "reason": answer.reason,
            "answer": answer.text,
            "model": answer.model,
            "sources": [{
                "n": i, "path": h.rel_path, "track": h.track,
                "title": h.title, "heading": h.heading, "ordinal": h.ordinal,
                "cosine": h.dense, "bm25": h.bm25,
            } for i, h in enumerate(answer.hits, start=1)],
        }, ensure_ascii=False, indent=2))
        return 0 if answer.grounded else 3

    print(f"Вопрос: {answer.question}\n")
    print(answer.text)
    print()
    if answer.hits and answer.verdict != ABSENT:
        print("Источники:")
        for i, hit in enumerate(answer.hits, start=1):
            print(f"  [{i}] {hit.rel_path}  chunk#{hit.ordinal}  — [{hit.track}] {hit.title}")
            if hit.heading:
                print(f"      раздел: {hit.heading}")
            if getattr(args, "show_sources", False):
                print(f"      {hit.snippet(600)}")
        print()
    elif answer.hits:
        print("Ближайшее из найденного (недостаточно для ответа):")
        for hit in answer.hits:
            print(f"  · {hit.rel_path} — [{hit.track}] {hit.title}"
                  + (f" (cos={hit.dense:.4f})" if hit.dense is not None else ""))
        print()

    print(f"вердикт: {answer.verdict} — {answer.reason}")
    print(f"модель: {answer.model}; {elapsed:.1f}s")
    return 0 if answer.grounded else 3


def _cmd_status(args) -> int:
    from digit_cli.kb import store

    with store.connect() as conn:
        stats = store.index_stats(conn)
        tracks = store.track_breakdown(conn) if getattr(args, "tracks", False) else []

    if getattr(args, "as_json", False):
        import json

        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0

    last = stats["last_index"]
    when = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(last)))
        if last else "never"
    )
    print(f"KB directory : {stats['kb_dir']}")
    print(f"files        : {stats['files']}")
    print(f"chunks       : {stats['chunks']}  ({stats['words']} words)")
    print(f"tracks       : {stats['tracks']}")
    print(f"vectors      : {stats['n_vectors']} × {stats['embed_dim'] or '?'}")
    print(f"embed model  : {stats['embed_model'] or '—'}")
    print(f"pending      : {stats['pending']} (staged, not yet in the slab)")
    print(f"disk         : {_human_bytes(stats['db_bytes'])} sqlite + "
          f"{_human_bytes(stats['vec_bytes'])} vectors")
    print(f"last index   : {when}")
    if tracks:
        print("\ntrack                     files   chunks")
        for row in tracks:
            print(f"  {row['track']:<22} {row['files']:>6} {row['chunks']:>8}")
    return 0


def _cmd_tracks(args) -> int:
    from digit_cli.kb import store

    with store.connect() as conn:
        rows = store.track_breakdown(conn)
    if not rows:
        print("index is empty — run `digit kb index`")
        return 1
    print(f"{'track':<26}{'files':>7}{'chunks':>9}")
    for row in rows:
        print(f"{row['track']:<26}{row['files']:>7}{row['chunks']:>9}")
    return 0


def _cmd_doctor(args) -> int:
    from digit_cli.kb import store
    from digit_cli.kb.embed import CODE_CHAT_MODEL, DEFAULT_CHAT_MODEL, EmbedError

    ok = True
    client = _client(args)
    print(f"endpoint     : {client.host}  (loopback enforced)")
    try:
        print(f"ollama       : v{client.version()}")
    except EmbedError as exc:
        print(f"ollama       : UNREACHABLE — {exc}")
        return 1

    for label, name in (("embedder", client.embed_model),
                        ("generator", DEFAULT_CHAT_MODEL),
                        ("code model", CODE_CHAT_MODEL)):
        try:
            present = client.has_model(name)
        except Exception as exc:  # noqa: BLE001
            present, exc_msg = False, str(exc)
            print(f"{label:<13}: {name} — check failed: {exc_msg}")
            ok = False
            continue
        print(f"{label:<13}: {name} — {'present' if present else 'MISSING (ollama pull)'}")
        ok = ok and present

    try:
        vec = client.embed_one("проверка", is_query=True)
        print(f"embed probe  : OK, dim={len(vec)}")
    except Exception as exc:  # noqa: BLE001
        print(f"embed probe  : FAILED — {exc}")
        ok = False

    try:
        import numpy
        print(f"numpy        : {numpy.__version__}")
    except ImportError:
        print("numpy        : MISSING (pip install numpy==2.4.3)")
        ok = False

    with store.connect() as conn:
        stats = store.index_stats(conn)
        import sqlite3
        print(f"sqlite       : {sqlite3.sqlite_version} (FTS5 required)")
        try:
            conn.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'x'")
            print("fts5         : OK")
        except sqlite3.OperationalError as exc:
            print(f"fts5         : FAILED — {exc}")
            ok = False
    print(f"index        : {stats['chunks']} chunks / {stats['files']} files, "
          f"{stats['n_vectors']} vectors")
    if stats["pending"]:
        print(f"  WARNING    : {stats['pending']} staged vectors are not in the slab; "
              f"rerun `digit kb index`")
    if stats["chunks"] and stats["chunks"] != int(stats["n_vectors"] or 0):
        print("  WARNING    : chunk count != vector count; run `digit kb index`")
        ok = False
    return 0 if ok else 1


def _cmd_reset(args) -> int:
    from digit_cli.kb import indexer, store

    if not getattr(args, "yes", False):
        try:
            reply = input(f"Delete the index in {store.kb_dir()}? [y/N] ")
        except EOFError:
            reply = ""
        if reply.strip().lower() not in ("y", "yes"):
            print("aborted")
            return 1
    with store.connect() as conn:
        indexer.rebuild(conn, progress=lambda m: print(m))
    print("index cleared")
    return 0
