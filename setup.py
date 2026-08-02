"""
setup.py — wheel/sdist build guard.

pip/PyPI are not supported distribution methods for Digit. Homebrew builds
are supported only through the official ``digitable-lol/tap`` formula. The
wheel would ship without bundled assets (locales, skills, optional-mcps,
web_dist, tui_dist, plugin manifests) since those are resolved at runtime
via env-var overrides set by the Nix/Homebrew wrappers or the source-checkout
layout.

This file overrides the ``bdist_wheel`` and ``sdist`` setuptools commands
to raise an error when run outside an approved package build. The PEP 517
``build_wheel`` / ``build_sdist`` hooks in
``setuptools.build_meta`` call these commands internally, so the guard
fires for ``uv build``, ``pip wheel``, ``python -m build``, and direct
``setup.py`` invocations alike.

The approved consumers of ``build_wheel`` are uv2nix and the official
Homebrew formula. uv2nix calls
``setuptools.build_meta.build_wheel`` (→ ``bdist_wheel``) inside a Nix
build sandbox. ``nix/python.nix`` sets ``HERMES_NIX_BUILD=1`` on the
Hermes package derivation. The tap formula sets
``DIGIT_HOMEBREW_BUILD=1`` while assembling its isolated environment.

Editable installs (``uv sync``, ``pip install -e .``, ``nix develop``)
use ``build_editable``, which does NOT call ``bdist_wheel`` — it calls
``build_ext`` in editable mode. So the guard does not affect development.
"""

import os

from setuptools import setup
from setuptools.command.sdist import sdist

_IN_APPROVED_PACKAGE_BUILD = (
    os.environ.get("HERMES_NIX_BUILD") == "1"
    or os.environ.get("DIGIT_HOMEBREW_BUILD") == "1"
)

_BLOCK_MESSAGE = (
    "Building wheels or sdists for hermes-agent outside an approved package build is not supported.\n"
    "Digit is distributed via its installer, Homebrew tap, Docker image, or Nix.\n"
    "See: https://github.com/digitable-lol/digit#установка\n"
    "\n"
    "If you are developing, use an editable install instead:\n"
    "  uv sync          # or: uv pip install -e .\n"
    "\n"
    "Approved Nix and Homebrew builds set an internal build marker.\n"
    "If an official package build reaches this error, file a bug."
)


class _GuardedSdist(sdist):
    def run(self, *args, **kwargs):
        if not _IN_APPROVED_PACKAGE_BUILD:
            raise RuntimeError(_BLOCK_MESSAGE)
        return super().run(*args, **kwargs)


cmdclass = {"sdist": _GuardedSdist}

# bdist_wheel is only available when the `wheel` package is installed.
# setuptools.build_meta.build_wheel() calls it internally, so the guard
# fires for all PEP 517 wheel build paths. Define the subclass only when
# the import succeeds — otherwise a None base class raises TypeError at
# class-definition time, before the cmdclass guard can run.
try:
    from setuptools.command.bdist_wheel import bdist_wheel

    class _GuardedBdistWheel(bdist_wheel):
        def run(self, *args, **kwargs):
            if not _IN_APPROVED_PACKAGE_BUILD:
                raise RuntimeError(_BLOCK_MESSAGE)
            return super().run(*args, **kwargs)

    cmdclass["bdist_wheel"] = _GuardedBdistWheel
except ImportError:
    pass

setup(cmdclass=cmdclass)
