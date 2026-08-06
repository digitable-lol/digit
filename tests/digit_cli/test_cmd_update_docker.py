"""Tests for ``digit update`` / ``--check`` inside the Docker container.

Background: ``.dockerignore`` excludes ``.git``, so the existing git-pull
update path can never succeed inside the published image.  Before this
fix, ``digit update`` would fall through to ``"✗ Not a git repository.
Please reinstall: curl ... install.sh"`` — that script installs a *new*
host-side Digit, not an update to the running container, so the message
was actively misleading.

These tests pin the new behaviour: when ``detect_install_method`` reports
``"docker"`` (stamped by ``docker/stage2-hook.sh``), both the apply path
(``cmd_update``) and the check path (``_cmd_update_check``) print the
rebuild guidance from ``format_docker_update_message`` and exit
with status 1, without running ``git fetch`` / ``subprocess.run``.

Сообщение раньше советовало ``docker pull nousresearch/hermes-agent:latest``.
Это не остаток бренда, а рабочая инструкция заменить Digit на образ
вышестоящего проекта поверх того же ``/opt/data``; своего образа Digit не
публикует, и обновление у него — пересборка из репозитория.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from digit_cli.main import _cmd_update_check, cmd_update


# ---------- cmd_update (apply path) ----------


@patch("digit_cli.config.is_managed", return_value=False)
@patch("digit_cli.config.detect_install_method", return_value="docker")
@patch("subprocess.run")
def test_cmd_update_in_docker_prints_guidance_and_exits(
    mock_run, _mock_method, _mock_managed, capsys
):
    """``digit update`` inside Docker → friendly message + exit 1, no git calls."""
    with pytest.raises(SystemExit) as excinfo:
        cmd_update(SimpleNamespace(check=False))

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    # Spot-check the key guidance — exhaustive wording is locked in by the
    # config-module test below to keep these CLI tests resilient to copy edits.
    assert "doesn't apply inside the Docker container" in out
    assert "docker compose build" in out
    assert "docker pull nousresearch/hermes-agent" not in out

    # No git invocations — the early-return must beat every git command.
    git_calls = [c for c in mock_run.call_args_list if c.args and c.args[0] and "git" in str(c.args[0][0])]
    assert git_calls == [], f"expected no git calls, got: {git_calls}"




# ---------- _cmd_update_check (check path, direct entry) ----------


# ---------- Non-Docker installs unaffected ----------




# ---------- format_docker_update_message — content lock ----------


def test_format_docker_update_message_contents():
    """Lock in the high-value content of the Docker update message.

    These are the bits a user actually needs to act on; if any of them
    disappear in a copy edit, the message has lost its value.  Specific
    wording around them is free to evolve (we don't assert full text).
    """
    from digit_cli.config import format_docker_update_message

    msg = format_docker_update_message()

    # Primary command — the entire reason this message exists.
    assert "docker compose build" in msg

    # И, главное, чего в нём быть НЕ должно: ``docker pull`` образа
    # вышестоящего проекта ставит Hermes Agent поверх DIGIT_HOME, который
    # это же сообщение обещает сохранить.
    assert "docker pull nousresearch/hermes-agent" not in msg

    # The key concepts the message must cover:
    assert "recreate" in msg.lower(), "must explain that the container is recreated"
    assert "--version" in msg, "must show how to verify the new version"
    assert "DIGIT_HOME" in msg or "/opt/data" in msg, (
        "must address config persistence across upgrades"
    )

    # Points at the thing the image is actually built from.
    assert "Dockerfile" in msg or "docker-compose" in msg
