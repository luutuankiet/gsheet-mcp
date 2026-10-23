# Copied from google-docs-mcp@2fb4652 src/<package>/reply.py; renamed, Docs layout hint dropped.
"""How every tool replies: `content` blocks only, never `structuredContent`.

Claude Code shows the model a reply's structuredContent instead of its text
when both are present,
so tools return `ToolResult(content=[...])` and are registered with
`output_schema=None`.
"""
from __future__ import annotations

from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from mcp.types import ContentBlock, TextContent


class Refusal(ToolError):
    """Raised by a tool to refuse; the agent sees the message as an error reply."""


def text(*blocks: str, extra: list[ContentBlock] | None = None) -> ToolResult:
    """One text block per argument, then any extra blocks (images), in order."""
    content: list[ContentBlock] = [TextContent(type="text", text=b) for b in blocks]
    return ToolResult(content=content + list(extra or []))


def refusal(message: str) -> ToolResult:
    return ToolResult(content=[TextContent(type="text", text=message)], is_error=True)
