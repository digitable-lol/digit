"""Behavioral contracts for the bundled commit-convention skill.

The skill is only worth shipping if its checker keeps the two promises the
SKILL.md makes: it accepts the house style, and it rejects the diff-restating
messages a heuristic generator produces. Both are asserted here against real
message shapes rather than against the checker's own vocabulary.
"""

import subprocess
import sys
from pathlib import Path

from agent.skill_commands import scan_skill_commands
from agent.skill_utils import parse_frontmatter


REPO = Path(__file__).resolve().parent.parent.parent
SKILL = REPO / "skills/software-development/commit-convention/SKILL.md"
CHECKER = SKILL.parent / "scripts/check_commit_message.py"


def _check(tmp_path, message: str, diff: str | None = None):
    msg = tmp_path / "msg.txt"
    msg.write_text(message, encoding="utf-8")
    argv = [sys.executable, str(CHECKER), "--message-file", str(msg)]
    if diff is not None:
        d = tmp_path / "diff.txt"
        d.write_text(diff, encoding="utf-8")
        argv += ["--diff-file", str(d)]
    return subprocess.run(argv, capture_output=True, text=True)


def test_commit_convention_skill_is_loadable_as_a_digit_command(monkeypatch):
    from tools import skills_tool

    monkeypatch.setattr(skills_tool, "SKILLS_DIR", REPO / "skills")
    commands = scan_skill_commands()
    assert "/commit-convention" in commands
    assert Path(commands["/commit-convention"]["skill_md_path"]) == SKILL


def test_commit_convention_metadata_is_valid():
    content = SKILL.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(content)

    assert frontmatter["name"] == "commit-convention"
    assert 0 < len(frontmatter["description"]) <= 60
    assert frontmatter["description"].endswith(".")
    assert CHECKER.is_file()
    # The numbers in the skill are the corpus; if the body stops citing it, the
    # rules have become opinion again.
    assert "554" in body


def test_checker_accepts_a_house_style_message(tmp_path):
    msg = (
        "kb: чужой каталог среди мест поиска больше не роняет сборку индекса\n"
        "\n"
        "`digit kb index` падал трассировкой PermissionError на чужом чекауте.\n"
        "Path.exists() глотает только «нет файла», а на запрет доступа\n"
        "поднимает исключение.\n"
    )
    got = _check(tmp_path, msg)
    assert got.returncode == 0, got.stdout


def test_checker_rejects_a_content_free_subject(tmp_path):
    got = _check(tmp_path, "refactor: update existing code\n")
    assert got.returncode == 1
    assert "paperwork" in got.stdout


def test_checker_rejects_a_subject_that_restates_the_diff(tmp_path):
    got = _check(tmp_path, "feat: add validate_commit_message function\n")
    assert got.returncode == 1
    assert "restates the diff" in got.stdout


def test_checker_rejects_several_claims_joined_into_one_subject(tmp_path):
    got = _check(tmp_path, "docs: update documentation | feat: add new functionality\n")
    assert got.returncode == 1
    assert "one commit, one claim" in got.stdout


def test_checker_rejects_a_trailing_period(tmp_path):
    got = _check(tmp_path, "Кнопка «Слушать» не появлялась в проде.\n")
    assert got.returncode == 1
    assert "period" in got.stdout


def test_body_must_be_separated_from_the_subject(tmp_path):
    got = _check(tmp_path, "Кнопка «Слушать» не появлялась в проде\nпочему-то\n")
    assert got.returncode == 1
    assert "blank line" in got.stdout


def test_grounding_reports_facts_absent_from_the_diff_without_failing(tmp_path):
    """The measured failure mode: a number the diff cannot support.

    This must not be an error — half of a real body legitimately comes from
    outside the diff — but it must be named, or a fabricated measurement ships
    looking like an observed one.
    """
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n+++ b/app.py\n"
        "@@ -1 +1 @@\n-old = 1\n+new = 2\n"
    )
    msg = "Прогон 30943182943: 920 тестов, 7 упало\n"
    got = _check(tmp_path, msg, diff)
    assert got.returncode == 0, got.stdout
    assert "GROUNDING" in got.stdout
    assert "30943182943" in got.stdout
    assert "920" in got.stdout


def test_grounding_stays_quiet_when_every_fact_is_in_the_diff(tmp_path):
    diff = (
        "diff --git a/kb/indexer.py b/kb/indexer.py\n"
        "--- a/kb/indexer.py\n+++ b/kb/indexer.py\n"
        "@@ -1 +1,3 @@\n+def _readable(path):\n+    return path.exists()\n"
    )
    msg = "kb: недоступный каталог больше не роняет сборку\n\nОбёртка _readable ловит отказ доступа.\n"
    got = _check(tmp_path, msg, diff)
    assert got.returncode == 0, got.stdout
    assert "GROUNDING" not in got.stdout
