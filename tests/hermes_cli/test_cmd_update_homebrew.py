from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_cli.main import _cmd_update_check, cmd_update


@patch("hermes_cli.config.is_managed", return_value=False)
@patch("hermes_cli.config.detect_install_method", return_value="homebrew")
@patch("subprocess.run")
def test_cmd_update_in_homebrew_prints_brew_guidance_and_exits(
    mock_run, _mock_method, _mock_managed, capsys
):
    with pytest.raises(SystemExit) as excinfo:
        cmd_update(SimpleNamespace(check=False))

    assert excinfo.value.code == 1
    assert "brew upgrade digitable-lol/tap/digit" in capsys.readouterr().out
    mock_run.assert_not_called()


@patch("hermes_cli.config.detect_install_method", return_value="homebrew")
@patch("subprocess.run")
def test_update_check_in_homebrew_prints_brew_guidance_and_exits(
    mock_run, _mock_method, capsys
):
    with pytest.raises(SystemExit) as excinfo:
        _cmd_update_check()

    assert excinfo.value.code == 1
    assert "brew upgrade digitable-lol/tap/digit" in capsys.readouterr().out
    mock_run.assert_not_called()
