"""Tests for ACP session-provenance derivation (issue #33617).

Exercises acp_adapter.provenance against a real SessionDB — no mocks — covering
the acceptance-criteria matrix: root session, compression-split continuation,
multi-depth chains, rotation flagging, and graceful handling of unknown ids.
"""

import time

import pytest

from acp_adapter.provenance import build_session_provenance, session_provenance_meta
from digit_state import SessionDB


@pytest.fixture()
def db(tmp_path):
    d = SessionDB(db_path=tmp_path / "state.db")
    yield d


def _mk(db, sid, parent=None):
    db.create_session(session_id=sid, source="acp", parent_session_id=parent)


def test_root_session_no_compression(db):
    _mk(db, "root1")
    prov = build_session_provenance(db, "acp-1", "root1")
    assert prov["acpSessionId"] == "acp-1"
    assert prov["currentDigitSessionId"] == "root1"
    assert prov["rootDigitSessionId"] == "root1"
    assert prov["parentDigitSessionId"] is None
    assert prov["sessionKind"] == "root"
    assert prov["compressionDepth"] == 0
    assert "reason" not in prov  # no rotation signalled


def test_compression_split_continuation(db):
    # Parent ended with compression, child created afterwards.
    _mk(db, "old")
    db.end_session("old", "compression")
    time.sleep(0.001)
    _mk(db, "new", parent="old")

    prov = build_session_provenance(
        db, "acp-1", "new", previous_digit_session_id="old"
    )
    assert prov["sessionKind"] == "continuation"
    assert prov["parentDigitSessionId"] == "old"
    assert prov["rootDigitSessionId"] == "old"
    assert prov["compressionDepth"] == 1
    assert prov["previousDigitSessionId"] == "old"
    # Head rotated this turn → reason/creatorKind flagged.
    assert prov["reason"] == "compression"
    assert prov["creatorKind"] == "compression"




def test_non_compression_parent_is_root_not_continuation(db):
    # A child with a parent that did NOT end via compression (e.g. delegate
    # or branch child) must not be reported as a compression continuation.
    _mk(db, "p")
    _mk(db, "c", parent="p")  # parent still live, no end_reason
    prov = build_session_provenance(db, "acp-1", "c")
    assert prov["sessionKind"] == "root"
    assert prov["compressionDepth"] == 0
    assert prov["rootDigitSessionId"] == "p"  # lineage root still walked






# ---------------------------------------------------------------------------
# The ACP `_meta` namespace — a deliberate, unbridged wire break
# ---------------------------------------------------------------------------
#
# Until the Digit rebrand (2026-08-03) every `_meta` payload this adapter
# emitted was namespaced under the key "hermes". It is now "digit", and the old
# key is NOT emitted alongside it: the owner chose a clean break over a
# dual-emit release, so there is no deprecation window and no compatibility
# shim. See BREAKING.md.
#
# That decision is only safe if it is visible. The previous version of this
# test asserted `set(meta.keys()) == {"digit"}` and nothing else — it was
# rewritten mechanically together with the code it guards, stayed green
# throughout, and therefore stopped being a guard at all. The assertions below
# pin the new key by name AND assert the absence of the old one, so that
# reintroducing "hermes" (or renaming the namespace again) fails here rather
# than in someone's editor.
#
# External clients that read `_meta.hermes` — Zed, Digitable Workbench — see
# neither session provenance nor compaction markers until they are updated.

LEGACY_META_NAMESPACE = "hermes"
META_NAMESPACE = "digit"


def test_meta_wrapper_shape(db):
    _mk(db, "root1")
    meta = session_provenance_meta(db, "acp-1", "root1")

    # Exactly one namespace, and it is the new one.
    assert set(meta.keys()) == {META_NAMESPACE}

    # The break is deliberate: the legacy key is gone, not merely renamed
    # somewhere else in the payload. No dual emit, by owner decision.
    assert LEGACY_META_NAMESPACE not in meta
    assert LEGACY_META_NAMESPACE not in repr(meta)

    assert "sessionProvenance" in meta[META_NAMESPACE]
    assert meta[META_NAMESPACE]["sessionProvenance"]["currentDigitSessionId"] == "root1"


def test_compaction_meta_uses_the_same_namespace_and_drops_the_legacy_key():
    """The other two `_meta` emitters must not drift from provenance.

    `_history_summary_meta` produces the compaction markers that ACP frontends
    use to collapse or restyle a replayed handoff summary. It is the second
    half of the same wire contract, and nothing covered its key name before —
    which is exactly how the rename shipped unnoticed.
    """
    from acp_adapter.server import DigitACPAgent
    from agent.context_compressor import (
        COMPRESSED_SUMMARY_METADATA_KEY,
        SUMMARY_PREFIX,
    )

    # Flagged in-process, content not classifiable — the fallback path.
    flagged = DigitACPAgent._history_summary_meta(
        {COMPRESSED_SUMMARY_METADATA_KEY: True}, "anything at all"
    )
    assert flagged is not None, "a flagged summary message must carry _meta"
    assert set(flagged.keys()) == {META_NAMESPACE}
    assert LEGACY_META_NAMESPACE not in repr(flagged)
    assert flagged[META_NAMESPACE] == {"compactionSummary": True}

    # Classified from content, no flag — the DB-reload path.
    classified = DigitACPAgent._history_summary_meta({}, f"{SUMMARY_PREFIX}\nbody")
    assert classified is not None
    assert set(classified.keys()) == {META_NAMESPACE}
    assert LEGACY_META_NAMESPACE not in repr(classified)

    # An ordinary message stays unmarked under either namespace.
    assert DigitACPAgent._history_summary_meta({}, "just a user turn") is None
