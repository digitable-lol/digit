"""Regression contracts for Digit's first-run and returning-user identity."""

import os
from unittest.mock import patch

import yaml
from rich.console import Console

import hermes_cli.banner as banner
import model_tools
import tools.mcp_tool
from hermes_cli.config import migrate_config
from hermes_cli.digit_ui import digit_ui_text
from hermes_cli.skin_engine import get_active_skin_name, set_active_skin


def test_digit_startup_copy_supports_russian_and_english():
    assert digit_ui_text("capabilities_title", language="ru") == "Что умеет Digit"
    assert digit_ui_text("capabilities_title", language="en") == "What Digit can do"
    assert "обычными словами" in digit_ui_text("welcome", language="ru")


def test_digit_status_bar_uses_its_own_identity_mark():
    from cli import HermesCLI

    previous_skin = get_active_skin_name()
    set_active_skin("digitable")
    try:
        assert HermesCLI._get_brand_status_symbol() == "◇"
        runtime = object.__new__(HermesCLI)
        runtime._get_status_bar_snapshot = lambda: {
            "model_short": "qwen3.5:4b",
            "context_percent": None,
            "context_length": None,
            "context_tokens": 0,
            "duration": "1s",
            "battery_label": "",
            "focus_label": "",
            "goal_active": False,
            "compressions": 0,
            "active_background_tasks": 0,
            "active_background_processes": 0,
            "active_background_subagents": 0,
            "prompt_elapsed": None,
            "idle_since": None,
        }
        runtime._is_session_yolo_active = lambda: False
        rendered = runtime._build_status_bar_text(width=100)
        assert rendered.startswith("◇ qwen3.5:4b")
        assert "⚕" not in rendered
    finally:
        set_active_skin(previous_skin)


def test_v34_migrates_the_persisted_upstream_skin(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({"_config_version": 33, "display": {"language": "ru", "skin": "default"}}),
        encoding="utf-8",
    )

    with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
        migrate_config(interactive=False, quiet=True)

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["_config_version"] == 34
    assert config["display"] == {"language": "ru", "skin": "digitable"}


def test_v34_preserves_an_explicit_non_upstream_skin(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({"_config_version": 33, "display": {"skin": "mono"}}),
        encoding="utf-8",
    )

    with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
        migrate_config(interactive=False, quiet=True)

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["display"]["skin"] == "mono"


def test_russian_digit_banner_explains_outcomes_without_upstream_catalog(tmp_path, monkeypatch):
    previous_skin = get_active_skin_name()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_LANGUAGE", "ru")
    set_active_skin("digitable")
    tool_defs = [
        {"function": {"name": "read_file"}},
        {"function": {"name": "browser_click"}},
        {"function": {"name": "delegate_task"}},
        {"function": {"name": "image_generate"}},
    ]
    toolsets = {
        "read_file": "file",
        "browser_click": "browser",
        "delegate_task": "delegation",
        "image_generate": "image_gen",
    }

    try:
        with (
            patch.object(model_tools, "check_tool_availability", return_value=([], [])),
            patch.object(
                banner,
                "get_available_skills",
                return_value={"productivity": ["digitable-courses"]},
            ),
            patch.object(banner, "get_update_result", return_value=None),
            patch.object(banner, "get_latest_release_tag", return_value=None),
            patch.object(tools.mcp_tool, "get_mcp_status", return_value=[]),
        ):
            console = Console(record=True, force_terminal=False, color_system=None, width=160)
            banner.build_welcome_banner(
                console=console,
                model="qwen3.5:4b",
                provider="ollama",
                cwd="/tmp/project",
                session_id="abc123",
                tools=tool_defs,
                enabled_toolsets=[*toolsets.values(), "skills"],
                get_toolset_for_tool=toolsets.get,
            )
        output = console.export_text()
    finally:
        set_active_skin(previous_skin)

    assert "Что умеет Digit" in output
    assert "С чего начать" in output
    assert "Код и файлы" in output
    assert "Интернет" in output
    assert "Задачи" in output
    assert "Медиа" in output
    assert "курсы, утилиты, портал и Workbench" in output
    assert "Сеанс: abc123" in output
    assert "Ollama" in output
    assert "Available Tools" not in output
    assert "Available Skills" not in output
    assert "Hermes" not in output
    assert "Nous Research" not in output
    assert "upstream" not in output
