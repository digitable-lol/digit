"""Behavioral contracts for the bundled digitable-tasks skill.

The skill's whole job is to keep an agent off positional Taskwarrior ids, so the
tests here pin the parts that carry that instruction — and pin that the skill
and the command it documents cannot drift apart on the exit code.
"""

from pathlib import Path

from agent.skill_commands import scan_skill_commands
from agent.skill_utils import SKILL_PROMPT_DESC_LIMIT, parse_frontmatter
from tools.skill_manager_tool import _validate_frontmatter


REPO = Path(__file__).resolve().parent.parent.parent
SKILL = REPO / "skills/productivity/digitable-tasks/SKILL.md"


def test_digitable_tasks_skill_is_loadable_as_a_digit_command(monkeypatch):
    from tools import skills_tool

    monkeypatch.setattr(skills_tool, "SKILLS_DIR", REPO / "skills")
    commands = scan_skill_commands()
    assert "/digitable-tasks" in commands
    assert Path(commands["/digitable-tasks"]["skill_md_path"]) == SKILL


def test_digitable_tasks_frontmatter_passes_the_new_skill_validator():
    content = SKILL.read_text(encoding="utf-8")
    assert _validate_frontmatter(content, new_skill=True) is None

    meta, _ = parse_frontmatter(content)
    assert meta["name"] == "digitable-tasks"
    assert len(meta["description"]) <= SKILL_PROMPT_DESC_LIMIT


def test_skill_documents_the_exit_code_the_command_actually_uses():
    """The skill tells the agent "exit 2 means wrong reference kind".

    If the command's code and the skill's prose disagree, an agent reads the
    prose and mis-diagnoses a real failure, so they are pinned together here
    rather than kept aligned by memory.
    """
    from digit_cli.tasks_cli import tasks_command

    class _Args:
        tasks_command = "done"
        data_dir = None
        uuid = "2"
        note = None

    assert tasks_command(_Args()) == 2
    assert "exits **2**" in SKILL.read_text(encoding="utf-8")


def test_skill_refuses_prefixes_for_the_reason_the_command_does():
    """A prefix ban with no stated reason reads as pedantry and gets worked
    around; the reason is that a bare number is a valid hex prefix."""
    body = SKILL.read_text(encoding="utf-8")
    prefix_section = body[body.index("refuses uuid *prefixes*"):]
    assert "valid hex prefix" in prefix_section


def test_skill_tells_the_agent_to_read_annotations_before_working():
    body = SKILL.read_text(encoding="utf-8")
    assert "digit tasks show <uuid>` is not optional" in body


def test_skill_requires_evidence_and_forbids_readme_numbers():
    body = SKILL.read_text(encoding="utf-8")
    evidence = body[body.index("what an annotation has to contain"):]
    assert "commit sha" in evidence
    assert "not the numbers a README claims" in evidence


def test_skill_states_the_owner_decision_boundary():
    """An agent that decides on the owner's behalf is worse than one that
    stalls, so the skill has to name the boundary explicitly."""
    body = SKILL.read_text(encoding="utf-8")
    assert "It does not decide." in body
    assert "stays open" in body


def test_skill_separates_the_backlog_from_the_kanban_board():
    """Two trackers exist and they are not the same lifecycle; a skill that
    blurs them invites an agent to mirror tasks between databases."""
    body = SKILL.read_text(encoding="utf-8")
    assert "digit kanban" in body
    assert "different databases" in body
