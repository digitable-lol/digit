"""Digit's bilingual, local-first onboarding path."""

from unittest.mock import Mock

import digit_cli.setup as setup


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
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/local/bin/ollama")
    monkeypatch.setattr(setup, "_ensure_local_ollama_model", lambda *args: True)
    monkeypatch.setattr(setup.Path, "home", lambda: tmp_path)

    setup._run_first_time_local_setup(config, tmp_path, "ru")

    assert config["model"] == {
        "provider": "custom",
        "default": "qwen3.5:4b",
        "base_url": "http://localhost:11434/v1",
        "api_mode": "chat_completions",
        # Ollama по умолчанию отдаёт окно 4096, а Digit не стартует ниже 64 000
        # и падает с явной ошибкой. Без этого ключа мастер завершался «успешно»,
        # а разваливалось всё на первом же запросе.
        "ollama_num_ctx": 65536,
    }
    assert config["terminal"] == {"backend": "local", "cwd": str(tmp_path)}
    assert config["display"]["language"] == "ru"
    saved_env.assert_called_once_with("TERMINAL_ENV", "local")
    output = capsys.readouterr().out
    assert "Запросы остаются на этом компьютере" in output
    assert "digit" in output


def test_english_local_setup_is_fully_english(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(setup, "save_env_value", Mock())
    monkeypatch.setattr(setup, "remove_env_value", Mock())
    monkeypatch.setattr(setup, "save_config", Mock())
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/local/bin/ollama")
    monkeypatch.setattr(setup, "_ensure_local_ollama_model", lambda *args: True)
    monkeypatch.setattr(setup.Path, "home", lambda: tmp_path)

    setup._run_first_time_local_setup({}, tmp_path, "en")

    output = capsys.readouterr().out
    assert "Requests stay on this computer" in output
    assert "Ollama is installed" in output
    assert "Настро" not in output


def test_local_setup_downloads_missing_model_before_saving(monkeypatch, tmp_path):
    config = {}
    save = Mock()
    ready = Mock(side_effect=[False, True])
    pull = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(setup, "_ollama_model_is_ready", ready)
    monkeypatch.setattr(setup.subprocess, "run", pull)
    monkeypatch.setattr(setup, "save_config", save)
    monkeypatch.setattr(setup, "save_env_value", Mock())
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/local/bin/ollama")
    monkeypatch.setattr(setup.Path, "home", lambda: tmp_path)

    assert setup._run_first_time_local_setup(config, tmp_path, "ru") is True
    pull.assert_called_once_with(
        ["/usr/local/bin/ollama", "pull", "qwen3.5:4b"], check=False
    )
    assert save.call_args_list[-1].args == (config,)


def test_local_setup_runs_its_own_server_when_ollama_is_absent(monkeypatch, tmp_path, capsys):
    """Отсутствие Ollama больше не тупик: Digit поднимает модель сам.

    Раньше эта ветка печатала ссылку на ollama.com и возвращала False — то есть
    «простая локальная установка» работала только у тех, у кого локальная
    установка уже была сделана.
    """
    from digit_cli import local_model as lm

    config = {}
    save = Mock()
    started = Mock(return_value=4242)
    monkeypatch.setattr(setup, "save_config", save)
    monkeypatch.setattr(setup, "save_env_value", Mock())
    monkeypatch.setattr(setup.shutil, "which", lambda name: None)
    monkeypatch.setattr(setup.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(lm, "start_server", started)

    assert setup._run_first_time_local_setup(config, tmp_path, "ru") is True
    started.assert_called_once()
    assert config["model"]["provider"] == "custom"
    assert config["model"]["base_url"] == f"http://127.0.0.1:{lm.DEFAULT_PORT}/v1"
    assert config["model"]["default"] == "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
    save.assert_called()
    assert "digit local" in capsys.readouterr().out


def test_local_setup_does_not_save_a_missing_model(monkeypatch, tmp_path, capsys):
    """Если и свой сервер поднять не вышло — в конфиг не пишется ничего.

    Записать модель, которой нет, значит поменять честный отказ мастера на
    непонятный отказ соединения при первом запросе.
    """
    from digit_cli import local_model as lm

    config = {}
    save = Mock()
    monkeypatch.setattr(setup, "save_config", save)
    monkeypatch.setattr(setup, "save_env_value", Mock())
    monkeypatch.setattr(setup.shutil, "which", lambda name: None)

    def _boom(*args, **kwargs):
        raise lm.LocalModelError("сеть недоступна")

    monkeypatch.setattr(lm, "start_server", _boom)

    assert setup._run_first_time_local_setup(config, tmp_path, "ru") is False
    save.assert_not_called()
    assert "model" not in config
    assert "сеть недоступна" in capsys.readouterr().out
