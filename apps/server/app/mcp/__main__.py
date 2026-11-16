"""``python -m app.mcp`` entry point for the stdio MCP server."""

from __future__ import annotations

import asyncio

from app.mcp.server import run

if __name__ == "__main__":
    asyncio.run(run())
