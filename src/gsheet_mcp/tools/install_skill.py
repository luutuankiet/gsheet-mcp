# Copied from google-docs-mcp@2fb4652 src/<package>/tools/install_skill.py; renamed, description rewritten for Sheets.
"""install_skill: hand the shipped agent skill (tool guide and cookbook) to an agent."""
from __future__ import annotations

import json

from fastmcp.tools import ToolResult

from .. import reply, skill_bundle
from ..app import READ, tool


@tool(READ)
def install_skill() -> ToolResult:
    """Return the gsheet-mcp agent skill: a tool guide and a cookbook.

    Call this when you are unsure which tool fits a job, before a sheet
    script or raw batch_update requests, and when the user asks to install,
    set up or update the gsheet-mcp skill. Load it with your own skill loader
    instead if it is already installed. Needs no spreadsheet and no Google
    sign-in.

    Returns:
      A JSON text block (skill name, version, source URL, the file paths in
      order, install locations and the steps to follow), then one text block
      per file holding its full content, in that order.
    """
    out = skill_bundle.bundle()
    meta = {**out, "files": [f["path"] for f in out["files"]]}
    return reply.text(json.dumps(meta, indent=2), *(f["content"] for f in out["files"]))
