"""What the installed MCP Python SDK is, decided by the SDK itself.

mcp 2.x moved its HTTP stack from ``httpx`` to the separate ``httpx2`` package
(``mcp.shared._httpx_utils.create_mcp_http_client`` returns an
``httpx2.AsyncClient``; ``OAuthClientProvider`` subclasses ``httpx2.Auth``),
renamed ``McpError`` to ``MCPError`` and dropped some constructor parameters.
Anything Digit hands to the SDK -- an HTTP client, an ``Auth``, a request built
by an SDK helper -- must come from the SDK's own HTTP module, while Digit's own
probes keep using ``httpx``. Every answer here is read from the installed SDK,
never from a version number.
"""

from __future__ import annotations

import inspect
from types import ModuleType
from typing import Any, Callable


def _sdk_httpx() -> ModuleType:
    """The HTTP module the installed MCP SDK builds its clients with."""
    try:
        from mcp.shared import _httpx_utils
    except ImportError:  # pragma: no cover -- SDK without the shared helpers
        import httpx

        return httpx
    module = getattr(_httpx_utils, "httpx2", None) or getattr(
        _httpx_utils, "httpx", None
    )
    if isinstance(module, ModuleType):
        return module
    import httpx  # pragma: no cover -- helper module without a bound HTTP module

    return httpx


sdk_httpx: ModuleType = _sdk_httpx()


def _sdk_error_type() -> type[BaseException]:
    from mcp.shared import exceptions

    return getattr(exceptions, "MCPError", None) or getattr(exceptions, "McpError")


try:
    MCPErrorType: type[BaseException] = _sdk_error_type()
except ImportError:  # pragma: no cover -- SDK not installed
    MCPErrorType = Exception


def accepts_kwarg(callable_: Callable[..., Any], name: str) -> bool:
    """Whether ``callable_`` takes keyword ``name`` (directly or via ``**kwargs``)."""
    try:
        parameters = inspect.signature(callable_).parameters
    except (TypeError, ValueError):
        return False
    return name in parameters or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()
    )


def http_status_error_types() -> tuple[type[BaseException], ...]:
    """``HTTPStatusError`` of both HTTP modules: Digit's own and the SDK's."""
    import httpx

    return tuple({httpx.HTTPStatusError, sdk_httpx.HTTPStatusError})


def sdk_follows_redirects_within_origin() -> bool:
    """mcp 2.x transports follow same-origin redirects themselves (``stream_within_origin``)."""
    from mcp.shared import _httpx_utils

    return hasattr(_httpx_utils, "stream_within_origin")


def oauth_provider_accepts_timeout() -> bool:
    """mcp 1.x ``OAuthClientProvider`` took a whole-flow ``timeout``; mcp 2.x does not.

    Checked on the SDK base class: Digit's subclass forwards ``**kwargs``, so
    its own signature would always say yes.
    """
    from mcp.client.auth.oauth2 import OAuthClientProvider

    return "timeout" in inspect.signature(OAuthClientProvider.__init__).parameters


_MISSING: Any = object()


def _snake(wire_name: str) -> str:
    return "".join("_" + ch.lower() if ch.isupper() else ch for ch in wire_name)


def sdk_field(obj: Any, wire_name: str, default: Any = None) -> Any:
    """A field of an SDK model by its wire (camelCase) name.

    mcp 1.x models expose the wire name as the attribute (``result.isError``);
    mcp 2.x models expose snake_case (``result.is_error``) and keep camelCase
    only as the serialization alias, so ``getattr(result, "isError", None)``
    silently returns the default there.
    """
    # Wire name first: a real mcp 2.x model has no such attribute and falls
    # through to snake_case, a real 1.x model answers at once, and an explicit
    # value on a test double is not shadowed by an auto-created snake_case one.
    for name in (wire_name, _snake(wire_name)):
        value = getattr(obj, name, _MISSING)
        if value is not _MISSING:
            return value
    return default


def has_sdk_field(obj: Any, wire_name: str) -> bool:
    """Whether an SDK model has the field (either spelling)."""
    return sdk_field(obj, wire_name, _MISSING) is not _MISSING
