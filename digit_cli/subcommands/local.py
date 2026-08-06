"""``digit local`` — управление локальной моделью, которую Digit держит сам.

Отдельная команда нужна потому, что llama-server живёт обычным процессом, а не
системной службой: после перезагрузки его никто не поднимет. Прятать это за
«оно как-нибудь заработает» нельзя — отказ соединения на первом же запросе
объясняет ситуацию куда хуже, чем `digit local status`.
"""

from __future__ import annotations

from typing import Callable


def build_local_parser(subparsers, *, cmd_local: Callable) -> None:
    """Attach the ``local`` subcommand to ``subparsers``."""
    local_parser = subparsers.add_parser(
        "local",
        help="Local model server (llama.cpp) that Digit installs and runs itself",
        description=(
            "Start, stop and inspect the local llama.cpp server Digit sets up "
            "during the simple local install. Weights and the server binary "
            "live under the Digit home directory; nothing is sent anywhere."
        ),
    )
    local_sub = local_parser.add_subparsers(dest="local_action")

    _start = local_sub.add_parser(
        "start",
        help="Download what is missing and start the local model server",
    )
    _start.add_argument(
        "--model",
        default="chat",
        help=(
            "Which weights to serve: 'chat' (default agent model) or 'router' "
            "(Digitable's own routing model — auxiliary use only)"
        ),
    )
    _start.add_argument(
        "--port", type=int, default=None, help="Port to listen on (default: 8127)"
    )

    _stop = local_sub.add_parser("stop", help="Stop the local model server")
    _stop.add_argument(
        "--port", type=int, default=None, help="Port to stop (default: 8127)"
    )

    _status = local_sub.add_parser(
        "status", help="Show whether the server runs and what is downloaded"
    )
    _status.add_argument(
        "--port", type=int, default=None, help="Port to inspect (default: 8127)"
    )

    local_parser.set_defaults(func=cmd_local)
