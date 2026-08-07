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

    The two detection knobs are included because their values are now a
    *measurement* (see the wake-word docs): a drift between the number the
    engine runs on and the number the docs derive their error rates from makes
    the published rates describe a configuration nobody is running.
    """
    from digit_cli.config_defaults import DEFAULT_CONFIG
    from tools import wake_word

    configured = DEFAULT_CONFIG["wake_word"]
    assert configured["phrase"] == wake_word._DEFAULTS["phrase"]
    assert configured["openwakeword"]["model"] == wake_word._BUNDLED_MODEL_NAME
    assert configured["sensitivity"] == wake_word._DEFAULTS["sensitivity"]
    assert configured["confirmation_frames"] == wake_word._DEFAULTS["confirmation_frames"]


def test_the_wake_phrase_change_is_documented_where_the_rebrand_is_explained():
    """README's replacement table is where a reader checks what changed.

    The wake phrase was the last user-visible string saying "hermes", and it
    was pinned for a reason a reader had to be told: it labels trained weights.
    Now that the weights were retrained, the same section has to carry the new
    fact AND what became of the old phrase -- someone upgrading needs to learn
    from the README, not from a wake word that stopped answering. Requiring
    both keeps the explanation next to the rest of the rebrand contract
    instead of only in a source comment nobody reading the README will reach.
    """
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    _, _, origins = readme.partition("## Происхождение")

    assert "hey digit" in origins.lower(), "README does not name the current wake phrase"
    assert "hey hermes" in origins.lower(), "README does not say what became of the old phrase"
    assert "tools/wakewords/" in origins


def test_the_runtime_defaults_agree_and_name_no_upstream_phrase():
    """The literals a rename would hit first, checked against the real risk.

    While the phrase was pinned to upstream's weights the risk was a blanket
    search-and-replace renaming the string without the model; the marker
    ``rebrand:keep`` guarded it. The model has since been retrained, so the
    risk inverted: what must not happen now is "hermes" creeping back into the
    default phrase or the bundled model name. Both defaults are asserted
    directly, in both files that carry them, because they are separate literals
    that can drift.
    """
    for path, needle in [
        ("tools/wake_word.py", '"phrase": "hey digit"'),
        ("digit_cli/config_defaults.py", '"phrase": "hey digit"'),
        ("tools/wake_word.py", '_BUNDLED_MODEL_NAME = "hey_digit"'),
        ("digit_cli/config_defaults.py", '"model": "hey_digit"'),
    ]:
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        assert needle in text, f"{path}: default {needle!r} disappeared"


def test_the_retired_wake_phrase_survives_only_as_a_marked_compatibility_alias():
    """"hermes" may appear in the wake-word engine exactly once, as compat.

    ``_LEGACY_MODEL_ALIASES`` is why an existing config naming the upstream
    model still loads instead of crashing -- the same practice as
    LS_BOARD_KEY_LEGACY. It is also precisely the kind of line a tidy-up
    deletes without noticing it is load-bearing, so it must carry the
    ``rebrand:keep`` marker. Everything else in the module must be clean:
    an unmarked "hermes" here is either a leftover or a regression of the
    phrase itself.
    """
    from tools import wake_word

    assert wake_word._LEGACY_MODEL_ALIASES >= {"hey_hermes", "hey hermes"}
    assert not (wake_word._BUNDLED_MODEL_ALIASES & wake_word._LEGACY_MODEL_ALIASES), (
        "a legacy name must be resolved on the announced path, not the silent one"
    )

    source = (REPO_ROOT / "tools" / "wake_word.py").read_text(encoding="utf-8")
    unmarked = [
        line for line in source.splitlines()
        if "hermes" in line.lower() and "rebrand:keep" not in line
    ]
    assert not unmarked, "unmarked 'hermes' in the wake-word engine: " + repr(unmarked)


def test_no_wake_word_artifact_named_after_upstream_ships():
    """``tools/wakewords/`` is where the rebrand was most visible to a user.

    The phrase was never a string we could edit -- it was the label of the
    weights in this directory. Shipping a file called ``hey_hermes.*`` again
    would mean either a second model answering to upstream's name or, worse,
    the old weights back under the new default. The directory must hold the
    bundled model and nothing named for upstream.
    """
    from tools import wake_word

    wakewords = REPO_ROOT / "tools" / "wakewords"
    artifacts = sorted(p.name for p in wakewords.iterdir() if p.suffix in (".onnx", ".tflite"))

    assert artifacts, "no wake-word artifacts ship at all"
    assert not [a for a in artifacts if "hermes" in a.lower()], artifacts
    for name in artifacts:
        assert name.startswith(wake_word._BUNDLED_MODEL_NAME), (
            f"{name} is not the bundled model and nothing selects it by default"
        )
