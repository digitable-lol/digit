"""The digitdisk discovery + version gate behind the `/machine` panel.

The three cases that matter — a fresh binary, one too old, and none at all —
are exactly the ones you cannot conjure on a developer's machine on demand, so
every one of them is driven here through injected stand-ins rather than
whatever happens to be on PATH.
"""

import json
import subprocess

import pytest

from tui_gateway.digitdisk_probe import (
    MIN_VERSION,
    MIN_VERSION_TEXT,
    find_binary,
    parse_version,
    probe,
)

SNAPSHOT = {
    "taken_at": "2026-09-02T20:15:26Z",
    "host": {"hostname": "dev", "cpu_model": "AMD EPYC 7742"},
    "load": {"cpu_count": 256, "busy_percent": 10.3, "cores": [{"busy_percent": 100}]},
    "memory": {"total_bytes": 535951720448, "used_bytes": 125783687168},
    "disks": [{"mount_point": "/", "total_bytes": 1, "used_bytes": 1}],
    "network": [{"name": "eno33", "oper_state": "up"}],
    "gpus": [{"name": "Matrox G200eW3", "driver": "mgag200"}],
}


def _proc(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _runner(version_out, snapshot_out, snapshot_rc=0):
    """A stand-in digitdisk: answers --version, then `status --json`."""
    calls = []

    def run(binary, args, timeout):
        args = list(args)
        calls.append(args)
        if args == ["--version"]:
            return _proc(version_out)
        assert args[0] == "status", f"digitdisk was invoked as {args!r}"
        return _proc(snapshot_out, returncode=snapshot_rc)

    run.calls = calls
    return run


class TestParseVersion:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("digitdisk 0.6.0\nсборка 9477a31", (0, 6, 0)),
            ("digitdisk 0.4.0", (0, 4, 0)),
            ("digitdisk v1.2.3", (1, 2, 3)),
            ("digitdisk 1.2", (1, 2, 0)),
            ("digitdisk 0.7.0-rc1", (0, 7, 0)),
        ],
    )
    def test_reads_the_first_line(self, text, expected):
        assert parse_version(text) == expected

    @pytest.mark.parametrize("text", ["digitdisk dev", "", "digitdisk", "garbage"])
    def test_unparseable_is_none_not_a_guess(self, text):
        # A source build stamps "dev"; inventing 0.0.0 would make the version
        # gate silently reject every developer's own build.
        assert parse_version(text) is None


class TestFindBinary:
    def test_env_override_wins(self):
        found = find_binary(env={"DIGITDISK_BIN": "/opt/mine/digitdisk"}, is_exec=lambda p: True)
        assert found == "/opt/mine/digitdisk"

    def test_bad_override_is_not_silently_replaced(self):
        # Pointing DIGITDISK_BIN at a missing file must report "absent", not
        # quietly run a different binary than the one that was asked for.
        found = find_binary(
            env={"DIGITDISK_BIN": "/opt/gone/digitdisk"},
            is_exec=lambda p: False,
            which=lambda name: "/usr/bin/digitdisk",
        )
        assert found is None

    def test_falls_back_to_path(self):
        found = find_binary(env={}, which=lambda name: "/usr/bin/digitdisk", is_exec=lambda p: False)
        assert found == "/usr/bin/digitdisk"

    def test_then_the_known_install_dirs(self):
        found = find_binary(
            env={},
            which=lambda name: None,
            is_exec=lambda p: p == "/opt/homebrew/bin/digitdisk",
            fallback_dirs=("/opt/homebrew/bin", "/usr/local/bin"),
        )
        assert found == "/opt/homebrew/bin/digitdisk"

    def test_absent_everywhere(self):
        assert find_binary(env={}, which=lambda name: None, is_exec=lambda p: False) is None


class TestProbe:
    def test_fresh_binary_yields_the_snapshot(self):
        run = _runner("digitdisk 0.6.0\n", json.dumps(SNAPSHOT))
        res = probe(runner=run, finder=lambda **_: "/usr/local/bin/digitdisk")

        assert res["state"] == "ok"
        assert res["version"] == MIN_VERSION_TEXT
        assert res["version_known"] is True
        assert res["snapshot"]["host"]["hostname"] == "dev"
        # The flag must follow the subcommand: digitdisk reads a bare word in
        # the subcommand slot as a PATH, so `--json status` is an error.
        assert run.calls[1][:2] == ["status", "--json"]

    def test_newer_binary_is_accepted(self):
        run = _runner("digitdisk 9.9.9\n", json.dumps(SNAPSHOT))
        res = probe(runner=run, finder=lambda **_: "/usr/local/bin/digitdisk")
        assert res["state"] == "ok"

    def test_old_binary_is_refused_and_never_parsed(self):
        run = _runner("digitdisk 0.4.0\n", json.dumps({"old": "shape"}))
        res = probe(runner=run, finder=lambda **_: "/usr/local/bin/digitdisk")

        assert res["state"] == "outdated"
        assert res["version"] == "0.4.0"
        assert res["required"] == MIN_VERSION_TEXT
        assert MIN_VERSION_TEXT in res["hint"]
        # The whole point of the gate: `status` was never even run, so an
        # older payload shape can never reach the renderer.
        assert run.calls == [["--version"]]

    def test_missing_binary_says_how_to_get_it(self):
        res = probe(runner=_runner("", ""), finder=lambda **_: None)

        assert res["state"] == "missing"
        assert res["required"] == MIN_VERSION_TEXT
        assert MIN_VERSION_TEXT in res["hint"]
        assert "DIGITDISK_BIN" in res["hint"]

    def test_source_build_runs_but_is_flagged_unverified(self):
        run = _runner("digitdisk dev\n", json.dumps(SNAPSHOT))
        res = probe(runner=run, finder=lambda **_: "/home/dev/digitdisk")

        assert res["state"] == "ok"
        assert res["version_known"] is False
        assert res["version"] is None

    def test_nonzero_exit_is_reported_not_swallowed(self):
        run = _runner("digitdisk 0.6.0\n", "", snapshot_rc=1)
        res = probe(runner=run, finder=lambda **_: "/usr/local/bin/digitdisk")
        assert res["state"] == "failed"

    def test_unparseable_json_is_reported(self):
        run = _runner("digitdisk 0.6.0\n", "not json at all")
        res = probe(runner=run, finder=lambda **_: "/usr/local/bin/digitdisk")

        assert res["state"] == "failed"
        assert "parse" in res["error"]

    def test_a_json_array_is_not_mistaken_for_a_snapshot(self):
        run = _runner("digitdisk 0.6.0\n", "[1, 2, 3]")
        res = probe(runner=run, finder=lambda **_: "/usr/local/bin/digitdisk")
        assert res["state"] == "failed"

    def test_timeout_does_not_raise_out_of_the_handler(self):
        def run(binary, args, timeout):
            if list(args) == ["--version"]:
                return _proc("digitdisk 0.6.0\n")
            raise subprocess.TimeoutExpired(cmd="digitdisk", timeout=timeout)

        res = probe(runner=run, finder=lambda **_: "/usr/local/bin/digitdisk")

        assert res["state"] == "failed"
        assert "timed out" in res["error"]

    def test_binary_vanishing_between_find_and_run_reads_as_missing(self):
        def run(binary, args, timeout):
            raise OSError(2, "No such file or directory")

        res = probe(runner=run, finder=lambda **_: "/usr/local/bin/digitdisk")
        assert res["state"] == "missing"


def test_min_version_matches_the_documented_text():
    assert MIN_VERSION_TEXT == ".".join(str(p) for p in MIN_VERSION)
