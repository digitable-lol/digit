"""Локальная модель, которую Digit ставит сам.

Тесты закрывают ровно те места, где ошибка стоит дороже всего и где живой
прогон её не поймает: подмену скачанного бинарника, распаковку архива за
пределы каталога и — главное — окно контекста. Последнее не абстракция: пресет
`digit-router`, приехавший в репозиторий 2026-08-06, при живой проверке
оказался неработоспособен как основная модель именно из-за окна (40 960 против
требуемых 64 000), и до теста это никак не проявлялось.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from digit_cli import local_model as lm
from agent.model_metadata import MINIMUM_CONTEXT_LENGTH


# ---------------------------------------------------------------------------
# Окно контекста
# ---------------------------------------------------------------------------


def test_chat_weights_clear_the_context_floor():
    """Модель по умолчанию обязана проходить порог, иначе первый запуск мёртв."""
    assert lm.CHAT_WEIGHTS.role == "chat"
    assert lm.CHAT_WEIGHTS.context_length >= MINIMUM_CONTEXT_LENGTH


def test_router_weights_are_not_offered_as_the_main_model():
    """Роутер порог не проходит — и обязан быть помечен как вспомогательный.

    Если однажды кто-то поднимет ему `context_length`, тест напомнит, что
    llama-server всё равно обрежет окно по обучающему.
    """
    assert lm.ROUTER_WEIGHTS.role != "chat"
    assert lm.ROUTER_WEIGHTS.context_length < MINIMUM_CONTEXT_LENGTH
    assert lm.ROUTER_WEIGHTS.note, "вспомогательная роль должна быть объяснена словами"


def test_config_never_advertises_a_window_the_server_cannot_serve(tmp_path, monkeypatch):
    """context_length в конфиге не должен превышать возможности модели."""
    monkeypatch.setattr(lm, "_digit_home", lambda: tmp_path)
    config = lm.local_model_config(lm.CHAT_WEIGHTS)
    assert config["provider"] == "custom"
    assert config["context_length"] <= lm.CHAT_WEIGHTS.context_length
    assert config["context_length"] >= MINIMUM_CONTEXT_LENGTH
    assert config["base_url"].startswith("http://127.0.0.1:")


def test_server_command_asks_for_a_window_the_model_actually_has():
    """--ctx-size не должен превышать обучающее окно: сервер всё равно обрежет."""
    cmd = lm.build_server_command(
        Path("/bin/llama-server"), Path("/w.gguf"), lm.ROUTER_WEIGHTS, 8127
    )
    ctx = int(cmd[cmd.index("--ctx-size") + 1])
    assert ctx <= lm.ROUTER_WEIGHTS.context_length


def test_server_command_enables_tool_calling_and_pins_the_model_name():
    """Без --jinja llama.cpp не соберёт tool_calls, без --alias не совпадёт имя."""
    cmd = lm.build_server_command(
        Path("/bin/llama-server"), Path("/w.gguf"), lm.CHAT_WEIGHTS, 8127
    )
    assert "--jinja" in cmd
    assert cmd[cmd.index("--alias") + 1] == Path(lm.CHAT_WEIGHTS.filename).name
    # Квантованный V-кэш llama.cpp принимает только вместе с flash-attention.
    if "--cache-type-v" in cmd:
        assert "--flash-attn" in cmd


# ---------------------------------------------------------------------------
# Установка бинарника
# ---------------------------------------------------------------------------


def _make_tar(dest: Path, members: dict[str, bytes], top: str | None = None) -> Path:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, payload in members.items():
            full = f"{top}/{name}" if top else name
            info = tarfile.TarInfo(full)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    dest.write_bytes(buf.getvalue())
    return dest


def test_every_supported_platform_has_a_pinned_checksum():
    """Ассет без хеша — это ассет, который поставится без проверки."""
    assert lm._LLAMA_CPP_ASSETS, "без таблицы ассетов автоустановка невозможна"
    for key, (asset, sha) in lm._LLAMA_CPP_ASSETS.items():
        assert lm.LLAMA_CPP_TAG in asset, f"{key}: имя ассета разошлось с тегом"
        assert len(sha) == 64 and set(sha) <= set("0123456789abcdef"), f"{key}: не sha256"


def test_install_refuses_a_tampered_archive(tmp_path, monkeypatch):
    """Несовпадение хеша обязано остановить установку до распаковки."""
    monkeypatch.setattr(lm, "_digit_home", lambda: tmp_path)
    monkeypatch.setattr(lm, "_platform_key", lambda: ("Linux", "x86_64"))

    def fake_download(url: str, dest: Path) -> None:
        _make_tar(dest, {lm._BINARY_NAME: b"not the real binary"}, top="llama-bXXX")

    monkeypatch.setattr(lm, "_http_download", fake_download)

    with pytest.raises(lm.LocalModelError, match="Хеш"):
        lm.install_llama_server()
    assert not (tmp_path / "bin").exists() or not list((tmp_path / "bin").glob("llama.cpp-*"))


def test_install_unpacks_a_matching_archive(tmp_path, monkeypatch):
    """Хеш сошёлся — бинарник оказывается на месте и исполняем."""
    monkeypatch.setattr(lm, "_digit_home", lambda: tmp_path)
    monkeypatch.setattr(lm, "_platform_key", lambda: ("Linux", "x86_64"))

    payload = {lm._BINARY_NAME: b"#!/bin/sh\nexit 0\n", "libggml.so": b"\x00"}
    staged = _make_tar(tmp_path / "src.tar.gz", payload, top=f"llama-{lm.LLAMA_CPP_TAG}")
    digest = hashlib.sha256(staged.read_bytes()).hexdigest()

    monkeypatch.setitem(
        lm._LLAMA_CPP_ASSETS, ("Linux", "x86_64"), ("llama-test.tar.gz", digest)
    )
    monkeypatch.setattr(
        lm, "_http_download", lambda url, dest: dest.write_bytes(staged.read_bytes())
    )

    binary = lm.install_llama_server()
    assert binary.is_file()
    # Библиотеки обязаны лежать рядом: llama-server ищет их через RUNPATH=$ORIGIN.
    assert (binary.parent / "libggml.so").is_file()


def test_extract_refuses_to_escape_the_target_directory(tmp_path):
    """Архив с ../ не должен писать за пределы каталога распаковки."""
    evil = tmp_path / "evil.tar.gz"
    _make_tar(evil, {"../../escaped": b"x"})
    with pytest.raises(lm.LocalModelError, match="вне каталога"):
        lm._extract_archive(evil, tmp_path / "out")


def test_extract_refuses_zip_slip(tmp_path):
    """То же самое для zip-сборок под Windows."""
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../escaped.txt", "x")
    (tmp_path / "out").mkdir()
    with pytest.raises(lm.LocalModelError, match="вне каталога"):
        lm._extract_archive(evil, tmp_path / "out")


def test_unsupported_platform_says_what_to_do_instead(monkeypatch):
    """Отсутствие сборки — не тупик: подсказка обязана предлагать выход."""
    monkeypatch.setattr(lm, "_platform_key", lambda: ("FreeBSD", "amd64"))
    hint = lm.unsupported_platform_hint()
    assert "FreeBSD" in hint
    assert "DIGIT_LLAMA_SERVER" in hint


def test_explicit_binary_path_wins_over_the_managed_one(tmp_path, monkeypatch):
    """У кого собрана своя сборка с ускорителем — тот и должен её получить."""
    monkeypatch.setattr(lm, "_digit_home", lambda: tmp_path)
    own = tmp_path / "my-llama-server"
    own.write_text("#!/bin/sh\n", encoding="utf-8")
    own.chmod(0o755)
    monkeypatch.setenv("DIGIT_LLAMA_SERVER", str(own))
    assert lm.find_llama_server() == own


# ---------------------------------------------------------------------------
# Веса
# ---------------------------------------------------------------------------


def test_partial_download_is_not_mistaken_for_ready_weights(tmp_path, monkeypatch):
    """Оборванная закачка обязана перекачаться, а не притвориться готовой."""
    monkeypatch.setattr(lm, "_digit_home", lambda: tmp_path)
    path = lm.weights_path(lm.ROUTER_WEIGHTS)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"truncated")

    calls: list[str] = []

    def fake_download(url: str, dest: Path) -> None:
        calls.append(url)
        dest.write_bytes(b"x" * lm.ROUTER_WEIGHTS.size_bytes)

    monkeypatch.setattr(lm, "_http_download", fake_download)
    lm.ensure_weights(lm.ROUTER_WEIGHTS)
    assert calls, "файл неверного размера должен был вызвать перезакачку"
    assert path.stat().st_size == lm.ROUTER_WEIGHTS.size_bytes


# ---------------------------------------------------------------------------
# Автоподъём
# ---------------------------------------------------------------------------


def test_autostart_ignores_a_foreign_server_on_the_same_port(monkeypatch):
    """Свой порт — ещё не свой сервер: там может стоять чужой llama.cpp."""
    alien = {"model": {"base_url": f"http://127.0.0.1:{lm.DEFAULT_PORT}/v1", "default": "llama3"}}
    assert lm.configured_local_spec(alien) is None

    started = []
    monkeypatch.setattr(lm, "start_server", lambda *a, **k: started.append(1))
    assert lm.autostart_if_configured(alien) is False
    assert not started


def test_autostart_does_not_download_gigabytes_behind_your_back(tmp_path, monkeypatch):
    """Обычный `digit` не должен молча начинать закачку весов."""
    monkeypatch.setattr(lm, "_digit_home", lambda: tmp_path)
    monkeypatch.setattr(lm, "server_healthy", lambda *a, **k: False)
    monkeypatch.setattr(lm, "find_llama_server", lambda **k: None)
    started = []
    monkeypatch.setattr(lm, "start_server", lambda *a, **k: started.append(1))

    config = {
        "model": {
            "base_url": f"http://127.0.0.1:{lm.DEFAULT_PORT}/v1",
            "default": Path(lm.CHAT_WEIGHTS.filename).name,
        }
    }
    assert lm.autostart_if_configured(config) is False
    assert not started


def test_autostart_starts_what_is_already_downloaded(tmp_path, monkeypatch):
    """Веса и бинарник на месте, сервер лежит — единственный случай для запуска."""
    monkeypatch.setattr(lm, "_digit_home", lambda: tmp_path)
    monkeypatch.setattr(lm, "server_healthy", lambda *a, **k: False)
    monkeypatch.setattr(lm, "find_llama_server", lambda **k: Path("/bin/llama-server"))
    path = lm.weights_path(lm.CHAT_WEIGHTS)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    started = []
    monkeypatch.setattr(lm, "start_server", lambda *a, **k: started.append(1) or 1)

    config = {
        "model": {
            "base_url": f"http://127.0.0.1:{lm.DEFAULT_PORT}/v1",
            "default": Path(lm.CHAT_WEIGHTS.filename).name,
        }
    }
    assert lm.autostart_if_configured(config) is True
    assert started


def test_weights_land_flat_even_when_the_repo_nests_them(tmp_path, monkeypatch):
    """В репозитории роутера файл лежит в gguf/, у нас — плоско."""
    monkeypatch.setattr(lm, "_digit_home", lambda: tmp_path)
    assert "/" in lm.ROUTER_WEIGHTS.filename
    assert lm.weights_path(lm.ROUTER_WEIGHTS).parent == tmp_path / "models"
    assert lm.weights_path(lm.ROUTER_WEIGHTS).name == "router-0.6b-v3-Q5_K_M.gguf"
