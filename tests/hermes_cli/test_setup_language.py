"""Digit's bilingual, local-first onboarding path."""

from unittest.mock import Mock

import hermes_cli.setup as setup


def test_default_language_prefers_saved_choice(monkeypatch):
    monkeypatch.setenv("LANG", "ru_RU.UTF-8")
    assert setup._default_setup_language({"display": {"language": "en"}}) == "en"


def test_default_language_detects_russian_locale(monkeypatch):
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "ru_RU.UTF-8")
    assert setup._default_setup_language({}) == "ru"


def test_language_prompt_persists_russian(monkeypatch):
    config = {}
    save = Mock()
    monkeypatch.setattr(setup, "prompt_choice", lambda *args, **kwargs: 0)
    monkeypatch.setattr(setup, "save_config", save)

    assert setup._choose_setup_language(config) == "ru"
    assert config["display"] == {"language": "ru", "skin": "digitable"}
    save.assert_called_once_with(config)


def test_russian_local_setup_writes_ollama_config(monkeypatch, tmp_path, capsys):
    config = {}
    saved_env = Mock()
    monkeypatch.setattr(setup, "save_env_value", saved_env)
    monkeypatch.setattr(setup, "remove_env_value", Mock())
    monkeypatch.setattr(setup, "save_config", Mock())
    monkeypatch.setattr(setup.shutil, "which", lambda name: None)
    monkeypatch.setattr(setup.Path, "home", lambda: tmp_path)

    setup._run_first_time_local_setup(config, tmp_path, "ru")

    assert config["model"] == {
        "provider": "custom",
        "default": "qwen3.5:4b",
        "base_url": "http://localhost:11434/v1",
        "api_mode": "chat_completions",
    }
    assert config["terminal"] == {"backend": "local", "cwd": str(tmp_path)}
    assert config["display"]["language"] == "ru"
    saved_env.assert_called_once_with("TERMINAL_ENV", "local")
    output = capsys.readouterr().out
    assert "Запросы остаются на этом компьютере" in output
    assert "ollama pull qwen3.5:4b" in output
    assert "digit" in output


def test_english_local_setup_is_fully_english(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(setup, "save_env_value", Mock())
    monkeypatch.setattr(setup, "remove_env_value", Mock())
    monkeypatch.setattr(setup, "save_config", Mock())
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/local/bin/ollama")
    monkeypatch.setattr(setup.Path, "home", lambda: tmp_path)

    setup._run_first_time_local_setup({}, tmp_path, "en")

    output = capsys.readouterr().out
    assert "Requests stay on this computer" in output
    assert "Ollama is installed" in output
    assert "Настро" not in output
