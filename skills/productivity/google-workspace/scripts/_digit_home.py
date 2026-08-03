"""Resolve DIGIT_HOME for standalone skill scripts.

Skill scripts may run outside the Digit process (e.g. system Python,
nix env, CI) where ``digit_constants`` is not importable.  This module
provides the same ``get_digit_home()`` and ``display_digit_home()``
contracts as ``digit_constants`` without requiring it on ``sys.path``.

When ``digit_constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``digit_constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``DIGIT_HOME = Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from digit_constants import display_digit_home as display_digit_home
    from digit_constants import get_digit_home as get_digit_home
except (ModuleNotFoundError, ImportError):

    def get_digit_home() -> Path:
        """Return the Digit home directory (default: ~/.digit).

        Mirrors ``digit_constants.get_digit_home()``."""
        val = os.environ.get("DIGIT_HOME", "").strip()
        return Path(val) if val else Path.home() / ".digit"

    def display_digit_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``digit_constants.display_digit_home()``."""
        home = get_digit_home()
        try:
            return "~/" + str(home.relative_to(Path.home()))
        except ValueError:
            return str(home)
