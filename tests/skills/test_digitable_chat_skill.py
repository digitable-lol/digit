"""Contracts for the digitable-chat optional skill.

The skill is the *whole* integration: Digitable Chat is GPL-2.0, so Digit opens
a URL and ships none of the application. That arrangement is only worth anything
if it stays true, so the load-bearing test here is not about prose — it checks
that no chat client has been vendored into the distribution.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.skill_utils import SKILL_PROMPT_DESC_LIMIT, parse_frontmatter
from tools.skill_manager_tool import _validate_frontmatter


REPO = Path(__file__).resolve().parent.parent.parent
SKILL_DIR = REPO / "optional-skills/communication/digitable-chat"
SKILL = SKILL_DIR / "SKILL.md"


def test_frontmatter_passes_the_strict_validator():
    """Strict, not lenient. A sibling optional skill carries a 500-char
    description that the skill index truncates to 57 chars — which is exactly
    the routing signal this skill needs to be picked for "start a chat"."""
    content = SKILL.read_text(encoding="utf-8")
    assert _validate_frontmatter(content, new_skill=True) is None

    meta, _ = parse_frontmatter(content)
    assert meta["name"] == "digitable-chat"
    assert len(meta["description"]) <= SKILL_PROMPT_DESC_LIMIT


def test_frontmatter_carries_the_fields_its_siblings_carry():
    meta, _ = parse_frontmatter(SKILL.read_text(encoding="utf-8"))
    assert meta["author"]
    assert meta["license"]
    assert meta["metadata"]["digit"]["tags"]


# --------------------------------------------------------------------------
# The licence boundary
# --------------------------------------------------------------------------


def test_no_chat_client_is_vendored_beside_the_skill():
    """The skill says Digit "holds no part of the application". Check it.

    A skill directory that grows a bundled client is how a GPL-2.0 dependency
    arrives without anyone deciding to add one — the skill would still read
    correctly while the distribution had changed underneath it.
    """
    shipped = sorted(p.name for p in SKILL_DIR.rglob("*") if p.is_file())
    assert shipped == ["SKILL.md"], (
        f"digitable-chat must ship prose only; found {shipped}"
    )


def test_no_chat_client_dependency_in_the_node_manifest():
    """The other route a GPL client could arrive by."""
    manifest = json.loads((REPO / "package.json").read_text(encoding="utf-8"))
    declared = {
        *manifest.get("dependencies", {}),
        *manifest.get("devDependencies", {}),
        *manifest.get("optionalDependencies", {}),
    }
    # Packages that would mean the chat client itself is in the tree, as
    # opposed to something merely WebRTC-adjacent.
    forbidden = {"trystero", "digitable-chat", "@digitable-lol/chat"}
    assert not (declared & forbidden)


def test_skill_states_why_the_code_is_not_embedded():
    """Without the reason, the next contributor "fixes" the missing offline
    mode by vendoring the client and relicenses the whole application."""
    body = SKILL.read_text(encoding="utf-8")
    boundary = body[body.index("What this skill does not do"):]
    assert "GPL-2.0" in boundary
    assert "under the same terms" in boundary
    assert "Do not vendor" in boundary


def test_skill_leaves_the_embedding_decision_with_the_owner():
    body = SKILL.read_text(encoding="utf-8")
    assert "belongs to" in body and "owner" in body


# --------------------------------------------------------------------------
# Room safety
# --------------------------------------------------------------------------


def test_skill_keeps_the_password_off_the_link():
    """A private room whose password travels with the link is a public room."""
    body = SKILL.read_text(encoding="utf-8")
    assert "never part of the URL" in body
    assert "different routes" in body


def test_skill_requires_an_unguessable_room_id():
    body = SKILL.read_text(encoding="utf-8")
    assert "/dev/urandom" in body


def test_skill_does_not_promise_a_person_is_waiting():
    body = SKILL.read_text(encoding="utf-8")
    assert "not summon anybody into it" in body
