"""Protocol 2026-07-28 end to end on real SDK objects (no doubles).

A real ``MCPServer`` whose tool needs the user's choice answers the first
``tools/call`` with ``input_required`` (an embedded ``elicitation/create`` and
a ``request_state``). A real ``ClientSession`` wired like Digit's -- era
negotiation through ``MCPServerTask._negotiate``, the call through
``_call_tool_any_era``, the form through ``ElicitationHandler`` on a scripted
question surface -- must come back with the tool's final result.
"""

import pytest

pytest.importorskip("mcp.client._input_required")

import mcp.types as types  # noqa: E402
from mcp.client._memory import InMemoryTransport  # noqa: E402
from mcp.client.session import ClientSession  # noqa: E402
from mcp.server.mcpserver import Context, MCPServer  # noqa: E402

from tools import mcp_elicitation_form as form  # noqa: E402
from tools.mcp_tool import ElicitationHandler, MCPServerTask, _call_tool_any_era  # noqa: E402

CHOICES = [{"const": "1", "title": "element 1 — taken"}, {"const": "2", "title": "element 2 — free"}]


def _server(seen: dict) -> MCPServer:
    server = MCPServer("guide")

    @server.tool()
    async def place(ctx: Context) -> types.CallToolResult | types.InputRequiredResult:
        answer = (ctx.input_responses or {}).get("choice")
        if answer is None:
            return types.InputRequiredResult(
                input_requests={
                    "choice": types.ElicitRequest(
                        params=types.ElicitRequestFormParams(
                            message="Which element?",
                            requested_schema={
                                "type": "object",
                                "properties": {"choice": {"type": "string", "oneOf": CHOICES}},
                                "required": ["choice"],
                            },
                        )
                    )
                },
                request_state="round-1",
            )
        seen["state"] = ctx.request_state
        seen["answer"] = answer
        content = getattr(answer, "content", None) or {}
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"placed on {content.get('choice')}")])

    return server


class Script:
    def __init__(self, *answers):
        self.answers = list(answers)

    def __call__(self, question, choices, multi_select=False):
        return self.answers.pop(0)


@pytest.fixture(autouse=True)
def _surface():
    token = form.current_surface.set(form._UNSET)
    yield
    form.set_process_surface(None)
    form.current_surface.reset(token)


@pytest.mark.asyncio
async def test_input_required_round_trip_reaches_the_user_and_completes():
    seen: dict = {}
    form.set_process_surface(Script("element 2 — free", "Send"))
    task = MCPServerTask("guide")
    handler = ElicitationHandler("guide", {"timeout": 5})
    async with InMemoryTransport(_server(seen)) as (read, write):
        async with ClientSession(read, write, elicitation_callback=handler) as session:
            result = await task._negotiate(session, {})
            assert isinstance(result, types.DiscoverResult), "the server speaks 2026-07-28: discover, not initialize"
            assert session.protocol_version == "2026-07-28"
            final = await _call_tool_any_era(session, "place", {})
    assert seen["state"] == "round-1", "the echoed request_state is the server's own"
    assert getattr(seen["answer"], "action", None) == "accept"
    assert [block.text for block in final.content] == ["placed on 2"]


@pytest.mark.asyncio
async def test_protocol_legacy_config_skips_the_probe():
    task = MCPServerTask("guide")
    async with InMemoryTransport(_server({})) as (read, write):
        async with ClientSession(read, write) as session:
            result = await task._negotiate(session, {"protocol": "legacy"})
            assert isinstance(result, types.InitializeResult)
            assert session.discover_result is None


@pytest.mark.asyncio
async def test_tool_list_changes_arrive_over_subscriptions_listen(monkeypatch):
    """2026-07-28 has no unsolicited notifications: Digit must open subscriptions/listen."""
    import asyncio

    refreshed = asyncio.Event()
    monkeypatch.setattr(MCPServerTask, "_schedule_tools_refresh", lambda self: refreshed.set())
    server = _server({})

    @server.tool()
    async def grow(ctx: Context) -> str:
        def added() -> str:
            return "new"

        server.add_tool(added, name="added")
        await ctx.notify_tools_changed()
        return "grown"

    task = MCPServerTask("guide")
    async with InMemoryTransport(server) as (read, write):
        async with ClientSession(read, write) as session:
            await task._negotiate(session, {})
            await asyncio.sleep(0.2)  # the listener's acknowledgment
            await _call_tool_any_era(session, "grow", {})
            await asyncio.wait_for(refreshed.wait(), timeout=5)
            task._listen_task.cancel()

