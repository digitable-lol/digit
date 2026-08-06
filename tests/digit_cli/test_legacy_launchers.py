"""The pre-rebrand ``hermes*`` launchers: identification, cleanup, uninstall.

Before this file the rebrand had no test covering the *command names* at all
(only the data dir, the env vars and the ACP metadata key). That gap is what let
the blanket rename collapse ``uninstall``'s launcher list into duplicates
without anything going red.

Two contracts are pinned here:

* the launcher-name lists are complete and self-consistent — every legacy name
  maps to a distinct current one, and uninstall covers both generations;
* nothing is deleted on a name match alone. A launcher we cannot recognise as
  ours is reported, not removed.
"""

from __future__ import annotations


from pathlib import Path

import pytest

import digit_compat
from digit_cli import uninstall as uninstall_mod
from digit_cli.update_cmd import _clear_legacy_launchers


def _hermes_launcher(path: Path) -> Path:
    """A launcher shaped like the one a pre-rebrand installer wrote."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/usr/bin/env bash\n"
        'exec "$HOME/.hermes/hermes/.venv/bin/python" -m hermes_cli.main "$@"\n',
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _digit_launcher(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/usr/bin/env bash\n"
        'exec "$HOME/.digit/digit/.venv/bin/python" -m digit_cli.main "$@"\n',
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


@pytest.fixture
def bin_dir(tmp_path, monkeypatch) -> Path:
    """Point the launcher directories at one writable temp directory."""
    target = tmp_path / "bin"
    target.mkdir()
    monkeypatch.setattr(digit_compat, "LAUNCHER_DIRS", (str(target),))
    return target


# --------------------------------------------------------------------------
# The name lists
# --------------------------------------------------------------------------


def test_every_legacy_command_maps_to_a_distinct_current_one():
    """The invariant the collapsed list violated.

    ``hermes-agent`` was rewritten to ``digit`` rather than ``digit-agent``,
    producing a duplicate. A mapping whose values are not distinct, or which
    names a command that does not ship, is that bug.
    """
    replacements = list(digit_compat.LEGACY_COMMANDS.values())
    assert len(set(replacements)) == len(replacements)
    assert set(replacements) <= set(digit_compat.CURRENT_COMMANDS)


def test_current_commands_match_the_console_scripts_that_ship():
    """Derived from the packaging metadata, so adding an entry point without
    teaching uninstall about it cannot pass."""
    import tomllib

    repo = Path(__file__).resolve().parents[2]
    with (repo / "pyproject.toml").open("rb") as handle:
        scripts = tomllib.load(handle)["project"]["scripts"]

    assert set(digit_compat.CURRENT_COMMANDS) == set(scripts)


def test_legacy_and_current_names_do_not_overlap():
    assert not (set(digit_compat.LEGACY_COMMANDS)
                & set(digit_compat.CURRENT_COMMANDS))


# --------------------------------------------------------------------------
# Identification
# --------------------------------------------------------------------------


def test_recognises_a_launcher_we_wrote(tmp_path):
    launcher = _hermes_launcher(tmp_path / "hermes-acp")
    assert digit_compat.is_our_legacy_launcher(launcher) is True


def test_does_not_claim_an_unrelated_program_of_the_same_name(tmp_path):
    """A name match is not evidence of ownership."""
    stranger = tmp_path / "hermes"
    stranger.write_text("#!/bin/sh\necho 'a different hermes'\n", encoding="utf-8")
    assert digit_compat.is_our_legacy_launcher(stranger) is False


def test_recognises_a_symlink_pointing_into_an_old_install(tmp_path):
    link = tmp_path / "hermes"
    link.symlink_to(tmp_path / ".hermes" / "hermes" / ".venv" / "bin" / "hermes")
    assert digit_compat.is_our_legacy_launcher(link) is True


def test_finds_dangling_symlinks_not_just_regular_files(bin_dir):
    """A launcher pointing into an already-removed install is still a name on
    PATH, and ``exists()`` alone would skip it."""
    link = bin_dir / "hermes-acp"
    link.symlink_to(bin_dir / "gone" / "hermes-acp")
    assert not link.exists()  # dangling
    assert link in digit_compat.legacy_launcher_paths()


# --------------------------------------------------------------------------
# Cleanup on update
# --------------------------------------------------------------------------


def test_update_removes_our_stale_legacy_launchers(bin_dir, capsys):
    for name in digit_compat.LEGACY_COMMANDS:
        _hermes_launcher(bin_dir / name)
    _digit_launcher(bin_dir / "digit")

    removed = _clear_legacy_launchers()

    assert {p.name for p in removed} == set(digit_compat.LEGACY_COMMANDS)
    for name in digit_compat.LEGACY_COMMANDS:
        assert not (bin_dir / name).exists()
    # The current launcher is untouched.
    assert (bin_dir / "digit").is_file()

    out = capsys.readouterr().out
    assert "digit-acp" in out  # the replacement name is named for hermes-acp


def test_update_leaves_a_stranger_alone_and_says_so(bin_dir, capsys):
    """The dangerous direction: removing a program that only shares the name."""
    stranger = bin_dir / "hermes"
    stranger.write_text("#!/bin/sh\necho nope\n", encoding="utf-8")
    _hermes_launcher(bin_dir / "hermes-acp")

    removed = _clear_legacy_launchers()

    assert [p.name for p in removed] == ["hermes-acp"]
    assert stranger.is_file()

    out = capsys.readouterr().out
    assert "not recognisably ours" in out
    assert str(stranger) in out


def test_update_cleanup_is_idempotent(bin_dir):
    _hermes_launcher(bin_dir / "hermes-acp")
    assert len(_clear_legacy_launchers()) == 1
    assert _clear_legacy_launchers() == []


def test_update_cleanup_is_a_noop_with_nothing_to_clean(bin_dir):
    _digit_launcher(bin_dir / "digit")
    assert _clear_legacy_launchers() == []
    assert (bin_dir / "digit").is_file()


def test_acp_replacement_exists_before_the_legacy_one_is_taken_away(bin_dir):
    """Ordering contract for the ACP hosts.

    ``hermes-acp`` is the one legacy name whose staleness is silent, and an ACP
    host resolves the agent by command name. If cleanup ran before the
    ``digit-acp`` self-heal, there would be a window with neither name present.
    """
    from digit_cli import update_cmd

    _digit_launcher(bin_dir / "digit")
    _hermes_launcher(bin_dir / "hermes-acp")

    # Exercise the two steps in the order the update flow invokes them.
    update_cmd._ensure_acp_launcher()
    assert (bin_dir / "digit-acp").exists(), (
        "the replacement must be written before the legacy name is removed, "
        "or an ACP host has neither to resolve"
    )

    removed = update_cmd._clear_legacy_launchers()
    assert [p.name for p in removed] == ["hermes-acp"]
    assert (bin_dir / "digit-acp").exists()


# --------------------------------------------------------------------------
# Cleanup on uninstall
# --------------------------------------------------------------------------


def test_uninstall_removes_both_generations_including_digit_agent(bin_dir):
    """``digit-agent`` is the name the collapsed list dropped."""
    for name in (*digit_compat.CURRENT_COMMANDS, *digit_compat.LEGACY_COMMANDS):
        if name.startswith("hermes"):
            _hermes_launcher(bin_dir / name)
        else:
            _digit_launcher(bin_dir / name)

    removed = uninstall_mod.remove_wrapper_script()

    assert "digit-agent" in {p.name for p in removed}
    assert {p.name for p in removed} == (
        set(digit_compat.CURRENT_COMMANDS) | set(digit_compat.LEGACY_COMMANDS)
    )
    for name in (*digit_compat.CURRENT_COMMANDS, *digit_compat.LEGACY_COMMANDS):
        assert not (bin_dir / name).exists()


def test_uninstall_recognises_the_shape_the_installer_really_emits(bin_dir):
    """Pinned against a real generated launcher, not an assumed one.

    ``scripts/install.sh`` writes a bash shim that execs the install's venv
    interpreter against a checked-in entrypoint. It contains no ``digit_cli``,
    so an ownership check written from memory (as the first version of this fix
    was) recognises nothing and removes nothing.
    """
    home = bin_dir.parent
    launcher = bin_dir / "digit"
    launcher.write_text(
        "#!/usr/bin/env bash\n"
        "unset PYTHONPATH\n"
        "unset PYTHONHOME\n"
        f'exec "{home}/.digit/digit/venv/bin/python" '
        f'"{home}/.digit/digit/digit" "$@"\n',
        encoding="utf-8",
    )
    agent = bin_dir / "digit-agent"
    agent.write_text(
        "#!/usr/bin/env bash\n"
        f'exec "{home}/.digit/digit/venv/bin/python" '
        f'"{home}/.digit/digit/run_agent.py" "$@"\n',
        encoding="utf-8",
    )

    removed = uninstall_mod.remove_wrapper_script()

    assert {p.name for p in removed} == {"digit", "digit-agent"}


def test_uninstall_leaves_a_stranger_alone(bin_dir):
    stranger = bin_dir / "hermes"
    stranger.write_text("#!/bin/sh\necho nope\n", encoding="utf-8")
    _digit_launcher(bin_dir / "digit")

    removed = uninstall_mod.remove_wrapper_script()

    assert [p.name for p in removed] == ["digit"]
    assert stranger.is_file()


def test_uninstall_with_nothing_installed_removes_nothing(bin_dir):
    assert uninstall_mod.remove_wrapper_script() == []
