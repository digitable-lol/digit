"""Behavioral contracts for Digit's bundled FTS skill."""

from pathlib import Path

from agent.skill_commands import scan_skill_commands
from agent.skill_utils import parse_frontmatter


REPO = Path(__file__).resolve().parent.parent.parent
SKILL = REPO / "skills/software-development/fts/SKILL.md"


def test_fts_skill_is_loadable_as_a_digit_command(monkeypatch):
    from tools import skills_tool

    monkeypatch.setattr(skills_tool, "SKILLS_DIR", REPO / "skills")
    commands = scan_skill_commands()
    assert "/fts" in commands
    assert Path(commands["/fts"]["skill_md_path"]) == SKILL


def test_fts_skill_metadata_and_template_are_valid():
    content = SKILL.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(content)

    assert frontmatter["name"] == "fts"
    assert 0 < len(frontmatter["description"]) <= 60
    assert frontmatter["description"].endswith(".")
    assert "fts_gate_check" in body and "fts_morphisms_list" in body
    assert (SKILL.parent / "templates/discount.fts").is_file()
