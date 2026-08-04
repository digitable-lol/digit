"""Digit knowledge base — fully offline RAG over the Digitable corpus.

``digit kb`` indexes the Digitable ``courses`` checkout (course articles,
SDD engineering documents, workbench templates) into a local store and
answers questions from it with citations, using only a loopback ollama.
Nothing in this package makes a network call to a non-loopback address —
the endpoint is validated against a loopback allowlist on construction.

Modules
-------
:mod:`~digit_cli.kb.chunker`  Markdown/front-matter aware chunking.
:mod:`~digit_cli.kb.store`    SQLite metadata + FTS5 + the ``.npy`` slab.
:mod:`~digit_cli.kb.embed`    Loopback-only ollama client.
:mod:`~digit_cli.kb.indexer`  Corpus discovery, incremental (re)index.
:mod:`~digit_cli.kb.search`   Dense + BM25 hybrid retrieval (RRF).
:mod:`~digit_cli.kb.ask`      Retrieval-gated, cited answering.
:mod:`~digit_cli.kb.cli`      ``digit kb …`` parser and handlers.

Imports here are deliberately lazy: ``digit_cli.main`` imports
:func:`digit_cli.kb.cli.add_parser` on every CLI start, and neither numpy
nor a live ollama may be required just to render ``digit --help``.
"""

from __future__ import annotations

__all__ = ["add_parser", "kb_command"]


def add_parser(subparsers):  # noqa: D401 - thin re-export
    """Register the ``kb`` subcommand (see :mod:`digit_cli.kb.cli`)."""
    from digit_cli.kb.cli import add_parser as _add

    return _add(subparsers)


def kb_command(args):  # noqa: D401 - thin re-export
    """Dispatch a parsed ``digit kb`` invocation."""
    from digit_cli.kb.cli import kb_command as _cmd

    return _cmd(args)
