"""Behavioral contracts for Digit's bundled server-backups skill."""

from pathlib import Path

from agent.skill_commands import scan_skill_commands
from agent.skill_utils import SKILL_PROMPT_DESC_LIMIT, parse_frontmatter
from tools.skill_manager_tool import _validate_frontmatter


REPO = Path(__file__).resolve().parent.parent.parent
SKILL = REPO / "skills/software-development/server-backups/SKILL.md"


def test_server_backups_skill_is_loadable_as_a_digit_command(monkeypatch):
    from tools import skills_tool

    monkeypatch.setattr(skills_tool, "SKILLS_DIR", REPO / "skills")
    commands = scan_skill_commands()
    assert "/server-backups" in commands
    assert Path(commands["/server-backups"]["skill_md_path"]) == SKILL


def test_server_backups_frontmatter_passes_the_new_skill_validator():
    content = SKILL.read_text()
    # new_skill=True is the strict path: it also enforces the 60-char budget,
    # which is what keeps the routing signal alive in the skill index.
    assert _validate_frontmatter(content, new_skill=True) is None

    meta, _ = parse_frontmatter(content)
    assert meta["name"] == "server-backups"
    assert len(meta["description"]) <= SKILL_PROMPT_DESC_LIMIT


def test_server_backups_keeps_the_prove_before_destroy_ordering():
    """The non-obvious rule: a destructive step must be gated by a check.

    A backup skill that documents shredding a key without first proving the
    copy is good teaches the exact mistake that loses data, so pin it.
    """
    body = SKILL.read_text()
    prove = body.index("Prove before you destroy")
    assert body.index("sha256sum", prove) < body.index("shred -u", prove)


def test_server_backups_states_the_scope_hole():
    """A backup with an unstated hole buys confidence it has not earned."""
    body = SKILL.read_text()
    assert "/var/lib/mysql" in body
    assert "NO ACCESS" in body


def test_server_backups_requires_a_restore_not_just_an_archive():
    body = SKILL.read_text()
    assert "Restore-test, or it is not a backup" in body
    # count alone passes on an archive of empty files; content must be checked
    assert "Checksum one real file" in body
