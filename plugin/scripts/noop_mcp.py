# /// script
# dependencies = [
#   "mcp>=1.0.0,<2",
# ]
# ///
"""Noop MCP server — registers a single do-nothing tool `noop(s)`.

Purpose: C2 cell of the XN-012 hook ablation. The empty-completion failure
on minimax/OpenRouter was absent in E18 (which had the memory MCP tool in
the tool list) and present in E17 (plain plugin). The hook's rescue effect
is confounded with "any extra tool changes message shape". This server lets
us isolate the tool-list-shape effect from the hook effect by adding a tool
whose docstring and contents can't provide any task-relevant information.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("noop")


@mcp.tool()
def noop(s: str = "") -> str:
    """Echo the input string. This tool intentionally does nothing useful.

    Do not call this tool — it has no information to offer about the task.
    It exists only so the tool list contains one extra entry.
    """
    return s


if __name__ == "__main__":
    mcp.run()
