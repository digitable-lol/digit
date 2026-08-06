"""Regression contracts for the Hermes -> Digit rebrand's *runtime* surface.

The rebrand landed as a chain of commits (b2b2628a6..891f4c9f7) and left
behind a comment convention -- ``rebrand:keep`` -- marking every place where
the word "hermes" survives deliberately: upstream attribution, provider-side
model identifiers, the compatibility shims, and the trained wake-word model.
Nothing enforced any of it, so "is the rebrand done?" could only be answered
by grepping, and grep cannot tell a deliberate keep from a leftover.

This module is the oracle instead. It is deliberately scoped to the surface a
*user* meets -- what the agent calls itself, which commands get installed,
which URLs we print, and whether the old names still resolve -- and not to the
2000-odd historical mentions in CHANGELOG.md, BREAKING.md, the rollback guide
or the contributor records, which document the rename and must not be scrubbed.

The bug these tests exist to catch is a *second* blanket search-and-replace.
The first one (264cd2dda) corrupted a launcher list (see the post-mortem in
``digit_cli/uninstall.py``) and mangled the system prompt into "Digit or its
Digit foundation". A regex sweep over 462 files cannot distinguish the cases;
these assertions can.
"""

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Identity: what the agent says it is
# ---------------------------------------------------------------------------

def test_agent_introduces_itself_as_digit_but_keeps_upstream_attribution():
    """The prompt must lead with Digit and still credit Nous Research.

    Both halves are load-bearing. Dropping "Digit" makes the assistant
    introduce itself under the pre-rebrand name; dropping the Nous Research
    credit violates the attribution the README marks as non-removable.
    """
    from agent.prompt_builder import DEFAULT_AGENT_IDENTITY

    assert DEFAULT_AGENT_IDENTITY.startswith("You are Digit,")
    assert "Hermes Agent by Nous Research" in DEFAULT_AGENT_IDENTITY


def test_help_guidance_does_not_call_the_foundation_digit():
    """Guard the exact shape a blanket rename produces.

    ``s/Hermes/Digit/`` over this sentence turned "its Hermes Agent
    foundation" into "its Digit foundation" -- Digit built on Digit, which
    reads as nonsense in the live system prompt and tells the model nothing
    about what it actually runs on.
    """
    from agent.prompt_builder import DIGIT_AGENT_HELP_GUIDANCE

    assert "its Digit foundation" not in DIGIT_AGENT_HELP_GUIDANCE
    assert "Hermes Agent" in DIGIT_AGENT_HELP_GUIDANCE


def test_installed_commands_carry_no_legacy_names():
    """``pyproject`` console scripts are what lands on the user's PATH."""
    import tomllib

    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]

    assert set(scripts) == {"digit", "digit-agent", "digit-acp"}
    assert not [name for name in scripts if "hermes" in name.lower()]


# ---------------------------------------------------------------------------
# Compatibility: the old names must still resolve
# ---------------------------------------------------------------------------

def test_legacy_env_vars_are_adopted_and_announced():
    """``HERMES_*`` is a contract with already-installed copies.

    Users keep these in shell profiles, systemd units and CI secrets, so they
    outlive a rename of the repository. The bridge must copy the value *and*
    say so -- a silent adoption leaves the user with no signal to migrate.
    """
    import digit_compat

    env = {"HERMES_HOME": "/tmp/legacy", "HERMES_MODEL": "some-model"}
    digit_compat._warned = False
    bridged = digit_compat.adopt_legacy_env(env, warn=False)

    assert env["DIGIT_HOME"] == "/tmp/legacy"
    assert env["DIGIT_MODEL"] == "some-model"
    assert bridged == {"HERMES_HOME": "DIGIT_HOME", "HERMES_MODEL": "DIGIT_MODEL"}


def test_new_env_name_wins_over_the_legacy_one():
    """A migrated user is never overridden by a stale export left behind."""
    import digit_compat

    env = {"HERMES_HOME": "/tmp/old", "DIGIT_HOME": "/tmp/new"}
    digit_compat._warned = False
    bridged = digit_compat.adopt_legacy_env(env, warn=False)

    assert env["DIGIT_HOME"] == "/tmp/new"
    assert bridged == {}


def test_legacy_env_bridge_is_prefix_based_not_a_hand_written_map():
    """Coverage must be structural, so no variable can be forgotten.

    601 distinct names were renamed. If the bridge enumerated them, one
    omission would silently break an installed copy; deriving the new name
    from the prefix makes an unknown variable impossible to miss.
    """
    import digit_compat

    env = {"HERMES_A_NAME_NOBODY_LISTED": "value"}
    digit_compat._warned = False
    digit_compat.adopt_legacy_env(env, warn=False)

    assert env["DIGIT_A_NAME_NOBODY_LISTED"] == "value"


def test_legacy_skill_metadata_key_still_loads_and_warns():
    """Third-party skills on disk still say ``metadata.hermes``.

    Bundled skills were rewritten; skills installed from the hub or authored
    by users were not and cannot be, so the old key has to keep resolving.
    """
    import digit_compat

    assert digit_compat.read_skill_metadata_block({"digit": {"a": 1}}) == {"a": 1}
    assert digit_compat.read_skill_metadata_block({"hermes": {"b": 2}}) == {"b": 2}
    assert digit_compat.read_skill_metadata_block({"unrelated": 1}) == {}


def test_new_metadata_key_wins_over_the_legacy_one():
    import digit_compat

    block = digit_compat.read_skill_metadata_block({"digit": {"a": 1}, "hermes": {"b": 2}})
    assert block == {"a": 1}


# ---------------------------------------------------------------------------
# URLs we print or generate must point at Digit, not upstream
# ---------------------------------------------------------------------------

def _install_urls(text: str) -> set:
    return set(re.findall(r"https://\S*?install\.(?:sh|ps1)", text))


def test_uninstall_offers_digits_installer_not_upstreams():
    """``digit uninstall`` prints how to reinstall.

    Pointing that at upstream's installer reinstalls Hermes Agent over the
    ``~/.digit`` the same command just preserved. The URLs it prints must be
    the ones README documents, so the two cannot drift apart.
    """
    uninstall_urls = _install_urls(
        (REPO_ROOT / "digit_cli" / "uninstall.py").read_text(encoding="utf-8")
    )
    readme_urls = _install_urls((REPO_ROOT / "README.md").read_text(encoding="utf-8"))

    assert uninstall_urls, "uninstall.py no longer names an installer URL"
    assert uninstall_urls <= readme_urls, (
        f"uninstall.py points somewhere README does not: {uninstall_urls - readme_urls}"
    )


def test_generated_changelog_links_at_digits_own_repository():
    """The release script's default is the link target of every release note.

    Its only caller does not pass ``repo_url``, so an upstream default sends
    every PR, commit and compare link to a repository where our SHAs do not
    exist. Read the default structurally rather than importing the script,
    which expects release-time arguments.
    """
    tree = ast.parse((REPO_ROOT / "scripts" / "release.py").read_text(encoding="utf-8"))
    func = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "generate_changelog"
    )
    defaults = dict(
        zip([a.arg for a in func.args.args][-len(func.args.defaults):], func.args.defaults)
    )

    assert ast.literal_eval(defaults["repo_url"]) == "https://github.com/digitable-lol/digit"


# ---------------------------------------------------------------------------
# Wake word: pinned to a trained artifact, not to a brand name
# ---------------------------------------------------------------------------

def test_wake_phrase_matches_a_wake_word_model_that_actually_ships():
    """The phrase is not ours to rename -- it labels trained weights.

    ``tools/wakewords/`` holds an openWakeWord model whose label is baked into
    its weights. Renaming the phrase to "hey digit" without shipping a
    ``hey_digit`` model would advertise a phrase the detector never fires on
    (openWakeWord) or change what users must say with no matching model
    (sherpa). This asserts the invariant rather than the spelling: rename the
    phrase and the model together and the test goes green on its own.
    """
    from tools import wake_word

    phrase = wake_word._DEFAULTS["phrase"]
    assert phrase.replace(" ", "_") == wake_word._BUNDLED_MODEL_NAME

    for framework in ("onnx", "tflite"):
        model = Path(wake_word._bundled_wakeword_path(framework))
        assert model.is_file(), f"wake-word model missing: {model}"
        assert model.stem == wake_word._BUNDLED_MODEL_NAME


def test_shipped_config_defaults_agree_with_the_wake_word_engine():
    """A user editing config sees ``config_defaults``; the engine reads its own.

    They are separate literals in separate files, so they can drift; when they
    do, the documented default stops describing the running one.
    """
    from digit_cli.config_defaults import DEFAULT_CONFIG
    from tools import wake_word

    configured = DEFAULT_CONFIG["wake_word"]
    assert configured["phrase"] == wake_word._DEFAULTS["phrase"]
    assert configured["openwakeword"]["model"] == wake_word._BUNDLED_MODEL_NAME


def test_the_wake_phrase_pin_is_documented_where_the_rebrand_is_explained():
    """README's replacement table is where a reader checks what survived.

    The wake phrase is the one user-visible string still saying "hermes", so
    an undocumented pin reads as an oversight and invites exactly the blanket
    rename that would break detection. Requiring the explanation here keeps
    the reason next to the rest of the rebrand contract, not only in a
    source comment nobody reading the README will reach.
    """
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    _, _, origins = readme.partition("## Происхождение")

    assert "hey hermes" in origins.lower()
    assert "tools/wakewords/" in origins


def test_the_runtime_defaults_that_pin_the_phrase_say_why():
    """The two literals a rename would hit first must carry the marker.

    ``rebrand:keep`` is what separates a deliberate keep from a leftover. It
    is required on the *default values* -- not on prose -- because those are
    the lines a search-and-replace rewrites without anyone reading the
    paragraph above them.
    """
    for path, needle in [
        ("tools/wake_word.py", '"phrase": "hey hermes"'),
        ("digit_cli/config_defaults.py", '"phrase": "hey hermes"'),
        ("tools/wake_word.py", '_BUNDLED_MODEL_NAME = "hey_hermes"'),
        ("digit_cli/config_defaults.py", '"model": "hey_hermes"'),
    ]:
        lines = [
            line for line in (REPO_ROOT / path).read_text(encoding="utf-8").splitlines()
            if needle in line
        ]
        assert lines, f"{path}: pinned default {needle!r} disappeared"
        for line in lines:
            assert "rebrand:keep" in line, f"{path}: unmarked pinned default: {line.strip()}"
