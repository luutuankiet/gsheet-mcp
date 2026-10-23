# Copied from google-docs-mcp@2fb4652 src/<package>/server.py; renamed.
"""The MCP server with every tool registered. `http_mode.serve()` runs it."""
from __future__ import annotations

from . import tools  # noqa: F401  (registers the tools on `mcp`)
from .app import INSTRUCTIONS, mcp

__all__ = ["INSTRUCTIONS", "mcp"]
