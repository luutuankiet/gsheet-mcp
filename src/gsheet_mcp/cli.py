# Copied from google-docs-mcp@2fb4652 src/<package>/cli.py; renamed.
"""gsheet-mcp command line.

Subcommands:
  serve-http  Run as a remote MCP server over HTTP; `serve-http --help` lists its settings

There is no stdio mode and no local sign-in: every caller signs in through
the HTTP server.
"""
from __future__ import annotations

import sys


def _dispatch_http(remaining_args: list[str]) -> int:
    from gsheet_mcp import http_mode

    if remaining_args in (["-h"], ["--help"]):
        print(http_mode.SETTINGS_HELP, end="")
        return 0
    if remaining_args:
        print(f"gsheet-mcp serve-http: takes no arguments, got {remaining_args[0]!r}; "
              "settings come from environment variables (see --help)", file=sys.stderr)
        return 2
    return http_mode.serve()


def main() -> int:
    args = sys.argv[1:]

    if args in (["-h"], ["--help"]):
        print(__doc__ or "")
        return 0

    if args and args[0] == "serve-http":
        return _dispatch_http(args[1:])

    # A bare start would only show up on Cloud Run as a failed health check,
    # so anything but `serve-http` is refused loudly.
    what = f"unknown command: {args[0]}" if args else "missing command"
    print(f"{what}; run `gsheet-mcp serve-http` (see --help)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
