"""Behavioral contracts for Digit's isolated runtime home."""

from pathlib import Path

import digit_constants
from digit_cli.update_cmd import OFFICIAL_REPO_URL, _is_fork


def test_posix_default_home_is_isolated_from_digit(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(digit_constants.sys, "platform", "darwin")
    monkeypatch.delenv("DIGIT_HOME", raising=False)

    assert digit_constants.get_digit_home() == tmp_path / ".digit"
    assert digit_constants.get_default_digit_root() == tmp_path / ".digit"


def test_legacy_home_override_remains_supported(tmp_path, monkeypatch):
    custom_home = tmp_path / "shared-runtime"
    monkeypatch.setenv("DIGIT_HOME", str(custom_home))

    assert digit_constants.get_digit_home() == custom_home
    assert digit_constants.get_process_digit_home() == custom_home


def test_updater_tracks_digit_distribution():
    assert OFFICIAL_REPO_URL == "https://github.com/digitable-lol/digit.git"
    assert not _is_fork("git@github.com:digitable-lol/digit.git")
    assert _is_fork("https://github.com/NousResearch/hermes-agent.git")
