"""The dashboard host and the bundled plugin bundles must agree on names.

A dashboard plugin is a plain JS bundle the host injects into the page. There
is no import graph between them and no build step that could catch a mismatch:
the whole contract is a handful of ``window.__*__`` globals plus the plugin's
name from ``manifest.json``. Get one of those names wrong and nothing throws --
every bundle opens with ``if (!SDK) return;``, so it silently renders nothing.

That is exactly what the Digit rebrand did. ``web/src/plugins/registry.ts`` was
renamed to expose ``__DIGIT_PLUGIN_SDK__`` / ``__DIGIT_PLUGINS__``; the two
checked-in bundles under ``plugins/*/dashboard/dist/`` were not, and kept
reading ``__HERMES_*``. Both bundled dashboard tabs -- Kanban and Achievements
-- rendered empty, and the achievements bundle additionally registered itself
as "hermes-achievements" and called ``/api/plugins/hermes-achievements``, so
even a fixed SDK lookup would have missed and every request would have 404'd.

Grep cannot find this class of bug: the word "hermes" was present on *both*
sides of the rename, just not in agreement. These tests compare the two sides
instead, which is why they assert relations (subset, membership) rather than
spellings -- they keep holding when a global is added, and fail on whichever
side drifts next, in either direction.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGINS_DIR = REPO_ROOT / "plugins"


def _window_globals(text: str) -> set:
    return set(re.findall(r"window\.(__[A-Z_]+__)", text))


def _manifests() -> list:
    return sorted(PLUGINS_DIR.glob("*/dashboard/manifest.json"))


def test_there_are_bundles_to_check():
    """Keep the globs from turning this file into a vacuous pass."""
    assert _manifests(), "no plugin dashboard manifests found — layout drift?"
    assert sorted(PLUGINS_DIR.glob("*/dashboard/dist/*.js")), "no plugin bundles found"


#: Where the host actually puts a ``window.__X__`` there for a plugin to find.
#: ``registry.ts`` assigns the SDK and registry objects at runtime;
#: ``web_server.py`` injects the request-scoped ones into the served HTML
#: (``<script>window.__DIGIT_SESSION_TOKEN__=…``) and ``vite.config.ts`` does
#: the same for the dev server. Reading the *setters* is the point: a name only
#: counts as part of the contract if some host code puts it on ``window``.
_HOST_SOURCES = (
    "web/src/plugins/registry.ts",
    "web/vite.config.ts",
    "digit_cli/web_server.py",
)


def test_bundles_only_read_globals_the_host_actually_exposes():
    """Every ``window.__X__`` a bundle reads must be one the host sets."""
    host = set()
    for rel in _HOST_SOURCES:
        path = REPO_ROOT / rel
        assert path.is_file(), f"host source moved: {rel}"
        host |= _window_globals(path.read_text(encoding="utf-8"))

    assert "__DIGIT_PLUGIN_SDK__" in host, "host no longer exposes the plugin SDK"
    assert "__DIGIT_PLUGINS__" in host, "host no longer exposes the plugin registry"

    for bundle in sorted(PLUGINS_DIR.glob("*/dashboard/dist/*.js")):
        unknown = _window_globals(bundle.read_text(encoding="utf-8")) - host
        assert not unknown, (
            f"{bundle.relative_to(REPO_ROOT)} reads globals the host never sets: "
            f"{sorted(unknown)} — the tab will render empty, silently"
        )


def test_bundles_register_under_their_manifest_name():
    """``usePlugins.ts`` resolves the component by ``manifest.name``.

    A mismatch is reported to the user only as the generic ``NO_REGISTER``
    load error, with no hint that the two names differ.
    """
    for manifest_path in _manifests():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        bundle = manifest_path.parent / manifest["entry"]
        assert bundle.is_file(), f"{manifest_path}: entry {manifest['entry']} is missing"

        registered = set(
            re.findall(r"\.register\(\s*[\"']([^\"']+)[\"']", bundle.read_text(encoding="utf-8"))
        )
        assert manifest["name"] in registered, (
            f"{bundle.relative_to(REPO_ROOT)} registers {sorted(registered)}, "
            f"but the host looks it up as {manifest['name']!r}"
        )


def test_bundles_call_only_their_own_api_namespace():
    """Plugin routers are mounted at ``/api/plugins/<manifest name>/``.

    ``web_server.py`` builds that prefix from the manifest, so a bundle asking
    for any other namespace 404s on every request it makes.
    """
    for manifest_path in _manifests():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        text = (manifest_path.parent / manifest["entry"]).read_text(encoding="utf-8")
        called = set(re.findall(r"[\"']/api/plugins/([a-zA-Z0-9_-]+)", text))
        foreign = called - {manifest["name"]}
        assert not foreign, (
            f"{manifest['name']}: bundle calls foreign API namespaces {sorted(foreign)}"
        )
