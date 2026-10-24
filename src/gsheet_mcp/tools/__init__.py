# Copied from google-docs-mcp@2fb4652 src/<package>/tools/__init__.py; renamed, Docs tools dropped.
"""One module per MCP tool. Importing this package registers every tool.

A new tool goes in its own module here, imports `tool` and a hints constant
from `gsheet_mcp.app`, and is added to the import list below.
"""
from . import (  # noqa: F401
    auth_status,
    batch_update,
    edit_sheet,
    grep_sheet,
    install_skill,
    pull_tables,
    query_range,
    read_range,
    render_range,
    run_sheet_script,
    scan_sheet,
    table_status,
    upload_slot,
    write_cells,
)
