"""FastMCP middleware that augments tool returns with world_state diffs.

After every tool call, this middleware:
1. Takes a fresh snapshot of the desktop world state.
2. Computes a diff vs the previous snapshot.
3. Appends a delimited TextContent block to the tool's ToolResult
   carrying the JSON-serialized diff:

       <world_state>{"timestamp": ..., "foreground_window": ...}</world_state>

The LLM thus sees a fresh view of the world after every action without
paying for a separate state-read tool call. This is the core mechanism
behind the "30% fewer tool calls" hypothesis we're benchmarking.

Why a content block instead of ToolResult.meta?
-----------------------------------------------
The `meta` field is the protocol-native channel for runtime metadata,
but Claude Desktop strips it before surfacing tool results to the model
(verified empirically). We pivoted to tagged content blocks because they
are guaranteed to reach the model — they ARE the tool's response text.
The delimiter tags let Claude distinguish state from the tool's primary
answer.

Design notes
------------
- get_world_state is skipped (it IS world_state — no need to wrap it).
- screenshot is wrapped: appending a text block after an Image block is
  valid MCP; Claude reads both.
- Failures in world_state snapshotting must NEVER fail the tool call.
  Errors degrade to a `<world_state>{"error": "..."}</world_state>` block.
"""

from __future__ import annotations

import json
import logging

from fastmcp.server.middleware import Middleware, MiddlewareContext, CallNext
from fastmcp.tools.tool import ToolResult
import mcp.types as mt


from jarvis_mcp.world_state import WorldState


logger = logging.getLogger(__name__)


class WorldStateMiddleware(Middleware):
    """Append a world_state diff to every tool result's content list."""

    def __init__(self, world_state: WorldState) -> None:
        self.world_state = world_state
        # get_world_state IS world_state — wrapping it would be a
        # pointless double-snapshot. Everything else gets wrapped.
        self.skip_tools: set[str] = {"get_world_state"}

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        tool_name = context.message.name
        result = await call_next(context)

        if tool_name in self.skip_tools:
            return result

        # Snapshot AFTER the tool ran — the world the tool just shaped.
        try:
            diff = self.world_state.snapshot_diff()
        except Exception as e:  # noqa: BLE001
            logger.exception("world_state snapshot failed")
            diff = {"error": f"{type(e).__name__}: {e}"}

        ws_block = mt.TextContent(
            type="text",
            text=f"<world_state>{json.dumps(diff, default=str)}</world_state>",
        )

        return ToolResult(
            content=list(result.content) + [ws_block],
            structured_content=result.structured_content,
            meta=result.meta,
        )