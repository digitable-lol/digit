"""Contracts for the Digit distribution layer."""

from pathlib import Path

import yaml

from agent.prompt_builder import DEFAULT_AGENT_IDENTITY
from digit_cli.default_soul import DEFAULT_SOUL_MD
from digit_cli.skin_engine import load_skin


REPO = Path(__file__).resolve().parent.parent.parent


def test_digit_identity_is_consistent():
    for identity in (DEFAULT_AGENT_IDENTITY, DEFAULT_SOUL_MD):
        assert "Digit" in identity
        assert "Digitable" in identity
        assert "Digit" in identity


def test_digitable_skin_uses_canonical_brand_tokens():
    skin = load_skin("digitable")
    assert skin.name == "digitable"
    assert skin.get_branding("agent_name") == "Digit"
    assert skin.get_branding("byline") == "Digitable"
    assert skin.get_branding("command_name") == "digit"
    assert skin.get_branding("status_symbol") == "◇"
    assert skin.colors["ui_accent"] == "#00E5E5"
    assert skin.colors["status_bar_bg"] == "#071018"


def test_digit_skills_have_valid_frontmatter():
    paths = [
        REPO / "skills/autonomous-ai-agents/digit/SKILL.md",
        REPO / "skills/productivity/digitable-portal/SKILL.md",
        REPO / "skills/productivity/digitable-courses/SKILL.md",
        REPO / "skills/software-development/digitable-tools/SKILL.md",
        REPO / "skills/software-development/digitable-workbench/SKILL.md",
    ]

    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        _, raw_frontmatter, body = text.split("---", 2)
        frontmatter = yaml.safe_load(raw_frontmatter)
        assert frontmatter["name"]
        assert 0 < len(frontmatter["description"]) <= 1024
        assert body.strip()


def test_tools_skill_covers_every_current_it_tools_route():
    text = (REPO / "skills/software-development/digitable-tools/SKILL.md").read_text(encoding="utf-8")
    expected = {
        "token-generator", "hash-text", "bcrypt", "uuid-generator", "ulid-generator",
        "qrcode-generator", "wifi-qrcode-generator", "json-prettify", "regex-tester",
        "ipv4-subnet-calculator", "markdown-to-html", "phone-parser-and-formatter",
    }
    assert expected <= set(text.split("`"))


def test_digit_local_presets_never_leave_loopback():
    source = (REPO / "digit_cli/model_switch.py").read_text(encoding="utf-8")
    for alias, model in {
        "digit-local-small": "qwen3.5:2b",
        "digit-local": "qwen3.5:4b",
        "digit-local-plus": "qwen3.5:9b",
        "digit-gemma": "gemma3:4b",
    }.items():
        assert f'"{alias}": DirectAlias(' in source
        assert f'model="{model}", provider="custom", base_url="http://localhost:11434/v1"' in source
