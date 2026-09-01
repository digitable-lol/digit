"""The cluster write boundary must hold over the shell, not just the file tools.

The gap these cover: `write_file('/outside/x')` was refused while
`sh -c 'echo x > /outside/x'` produced the same file.
"""

import json
import os
import subprocess
import sys

import pytest

from agent import cluster_boundary as cb
from agent import shell_confinement as sc


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    cb._roots.clear()
    monkeypatch.delenv(cb.ENV_ROOT, raising=False)
    monkeypatch.delenv(cb.ENV_LEDGER, raising=False)
    yield
    cb._roots.clear()


@pytest.fixture()
def boxes(tmp_path):
    inside = tmp_path / "in"
    outside = tmp_path / "out"
    inside.mkdir()
    outside.mkdir()
    cb.set_write_root("worker", [str(inside)])
    return str(inside), str(outside)


# ---------------------------------------------------------------------------
# Static pre-check: refuse the legible cases, in the boundary's own words
# ---------------------------------------------------------------------------


def test_redirect_outside_is_refused(boxes):
    inside, outside = boxes
    err = sc.check_command_allowed(f"echo x > {outside}/f.txt", "worker")
    assert err is not None
    assert "Refusing to write outside this agent's write boundary" in err
    assert outside in err


def test_redirect_inside_is_allowed(boxes):
    inside, _ = boxes
    assert sc.check_command_allowed(f"echo x > {inside}/f.txt", "worker") is None


def test_append_and_nested_shell_are_refused(boxes):
    _, outside = boxes
    assert sc.check_command_allowed(f"echo x >> {outside}/f", "worker")
    # The exact shape from the escape evidence.
    assert sc.check_command_allowed(f"sh -c 'echo x > {outside}/f'", "worker")


def test_writing_verbs_are_refused(boxes):
    inside, outside = boxes
    for command in (
        f"cp {inside}/a {outside}/b",
        f"mv {inside}/a {outside}/b",
        f"touch {outside}/b",
        f"mkdir -p {outside}/d",
        f"rm -f {outside}/b",
        f"echo x | tee {outside}/b",
    ):
        assert sc.check_command_allowed(command, "worker"), command


def test_reading_outside_is_not_refused(boxes):
    _, outside = boxes
    assert sc.check_command_allowed(f"cat {outside}/f", "worker") is None
    assert sc.check_command_allowed("grep -r foo /etc", "worker") is None


def test_dev_null_is_not_refused(boxes):
    assert sc.check_command_allowed("make 2>/dev/null >/dev/null", "worker") is None


def test_relative_target_resolves_against_cwd(boxes):
    inside, outside = boxes
    assert sc.check_command_allowed("echo x > f.txt", "worker", cwd=inside) is None
    assert sc.check_command_allowed("echo x > f.txt", "worker", cwd=outside)


def test_no_boundary_means_no_refusal(tmp_path):
    assert sc.check_command_allowed(f"echo x > {tmp_path}/f", "unregistered") is None


def test_dynamic_paths_are_left_to_the_kernel(boxes):
    """The scanner refuses only on evidence; it must not guess at expansions.

    This is not a hole — Landlock stops these — but it is the reason the static
    check alone would not be a boundary.
    """
    _, outside = boxes
    assert sc.check_command_allowed(f'P={outside}; echo x > "$P/f"', "worker") is None
    assert sc.check_command_allowed(
        f"python3 -c \"open('{outside}/f','w')\"", "worker"
    ) is None


# ---------------------------------------------------------------------------
# Landlock: the part that actually holds
# ---------------------------------------------------------------------------

requires_landlock = pytest.mark.skipif(
    not sc.available(), reason="Landlock unavailable on this kernel"
)


@requires_landlock
def test_landlock_confines_a_real_shell(boxes):
    inside, outside = boxes
    hook = sc.build_preexec([inside])

    def run(command):
        return subprocess.run(
            ["/bin/sh", "-c", command], preexec_fn=hook,
            capture_output=True, text=True,
        )

    assert run(f"echo ok > {inside}/f").returncode == 0
    assert os.path.exists(os.path.join(inside, "f"))

    # Every shape the static scanner cannot read is still stopped.
    assert run(f"echo esc > {outside}/f").returncode != 0
    assert run(f'P={outside}; echo esc > "$P/f"').returncode != 0
    assert run(
        f"{sys.executable} -c \"open('{outside}/f','w')\""
    ).returncode != 0
    assert os.listdir(outside) == []

    # Reads, execution and /dev/null keep working.
    assert run("cat /etc/hostname > /dev/null && echo alive").stdout.strip() == "alive"


@requires_landlock
def test_landlock_is_inherited_by_grandchildren(boxes):
    inside, outside = boxes
    hook = sc.build_preexec([inside])
    proc = subprocess.run(
        ["/bin/sh", "-c", f"/bin/sh -c 'echo esc > {outside}/f'"],
        preexec_fn=hook, capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert os.listdir(outside) == []


def test_build_preexec_refuses_a_root_that_does_not_exist(tmp_path):
    if not sc.available():
        pytest.skip("Landlock unavailable")
    with pytest.raises(sc.ConfinementUnavailable):
        sc.build_preexec([str(tmp_path / "missing")])


# ---------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------


def test_unbounded_agent_needs_no_confinement():
    assert sc.unavailable_reason("unregistered") is None


def test_bounded_agent_is_refused_a_shell_without_landlock(boxes, monkeypatch):
    monkeypatch.setattr(sc, "available", lambda: False)
    reason = sc.unavailable_reason("worker")
    assert reason is not None
    assert "cannot be enforced" in reason


def test_confined_raises_rather_than_degrading(boxes, monkeypatch):
    monkeypatch.setattr(sc, "landlock_abi", lambda: 0)
    with pytest.raises(sc.ConfinementUnavailable):
        with sc.confined("worker"):
            pass


def test_confined_is_a_noop_without_a_boundary():
    with sc.confined("unregistered") as hook:
        assert hook is None
    assert sc.preexec_for_current() is None


@requires_landlock
def test_confined_publishes_and_withdraws_the_hook(boxes):
    with sc.confined("worker") as hook:
        assert hook is not None
        assert sc.preexec_for_current() is hook
    assert sc.preexec_for_current() is None


# ---------------------------------------------------------------------------
# The refusal the model reads
# ---------------------------------------------------------------------------


def test_denial_is_explained_only_when_bounded(boxes):
    text = "sh: /x: Permission denied"
    assert "write boundary" in sc.explain_denial(text, "worker")
    assert sc.explain_denial(text, "unregistered") == text


def test_unrelated_output_is_not_annotated(boxes):
    assert sc.explain_denial("all good", "worker") == "all good"


@requires_landlock
def test_terminal_tool_blocks_a_write_outside_the_boundary(boxes):
    from tools import terminal_tool

    inside, outside = boxes
    blocked = json.loads(
        terminal_tool.terminal_tool(
            command=f"echo esc > {outside}/f", task_id="worker", timeout=20
        )
    )
    assert blocked["status"] == "blocked"
    assert "write boundary" in blocked["error"]
    assert not os.path.exists(os.path.join(outside, "f"))

    allowed = json.loads(
        terminal_tool.terminal_tool(
            command=f"echo ok > {inside}/f", task_id="worker", timeout=20
        )
    )
    assert allowed.get("status") != "blocked"
    assert os.path.exists(os.path.join(inside, "f"))


@requires_landlock
def test_force_does_not_lift_the_boundary(boxes):
    """force=True means the user confirmed the command, not that the agent may
    leave the filesystem slice its parent delegated to it."""
    from tools import terminal_tool

    _, outside = boxes
    result = json.loads(
        terminal_tool.terminal_tool(
            command=f"echo esc > {outside}/f", task_id="worker",
            force=True, timeout=20,
        )
    )
    assert result["status"] == "blocked"
    assert not os.path.exists(os.path.join(outside, "f"))
