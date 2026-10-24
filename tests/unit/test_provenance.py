# Copied from google-docs-mcp@2fb4652 tests/unit/test_provenance.py; origin is google-docs-mcp.
"""Every file copied from google-docs-mcp names its origin on its first line, so a
fix made there can be found and ported by hand. `<package>` stands for the Docs
MCP's package directory, whose name never appears in this repo."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "google-docs-mcp@2fb4652"

# file in this repo -> the path it was copied from in google-docs-mcp
COPIED = {
    "pyproject.toml": "pyproject.toml",
    ".python-version": ".python-version",
    "Dockerfile": "Dockerfile",
    ".dockerignore": ".dockerignore",
    ".gcloudignore": ".gcloudignore",
    ".github/workflows/release.yml": ".github/workflows/release.yml",
    "src/gsheet_mcp/__init__.py": "src/<package>/__init__.py",
    "src/gsheet_mcp/app.py": "src/<package>/app.py",
    "src/gsheet_mcp/auth.py": "src/<package>/auth.py",
    "src/gsheet_mcp/cli.py": "src/<package>/cli.py",
    "src/gsheet_mcp/http_mode.py": "src/<package>/http_mode.py",
    "src/gsheet_mcp/server.py": "src/<package>/server.py",
    "src/gsheet_mcp/state_store.py": "src/<package>/state_store.py",
    "src/gsheet_mcp/reply.py": "src/<package>/reply.py",
    "src/gsheet_mcp/sheets_api.py": "src/<package>/docs_api.py",
    "src/gsheet_mcp/sandbox/__init__.py": "src/<package>/sandbox/__init__.py",
    "src/gsheet_mcp/sandbox/process.py": "src/<package>/sandbox/process.py",
    "src/gsheet_mcp/sandbox/worker.py": "src/<package>/sandbox/worker.py",
    "src/gsheet_mcp/sandbox/prelude.js": "src/<package>/sandbox/prelude.js",
    "src/gsheet_mcp/tools/__init__.py": "src/<package>/tools/__init__.py",
    "src/gsheet_mcp/pages.py": "src/<package>/pages.py",
    "src/gsheet_mcp/notation.py": "src/<package>/render.py",
    "src/gsheet_mcp/transfer.py": "src/<package>/transfer.py",
    "src/gsheet_mcp/tools/render_range.py": "src/<package>/tools/render_pages.py",
    "src/gsheet_mcp/tools/grep_sheet.py": "src/<package>/tools/grep.py",
    "tests/conftest.py": "tests/conftest.py",
    "tests/fake_sheets.py": "tests/fake_docs.py",
    "tests/unit/test_provenance.py": "tests/unit/test_provenance.py",
    "tests/unit/test_http_mode.py": "tests/unit/test_http_mode.py",
    "tests/unit/test_state_store.py": "tests/unit/test_state_store.py",
    "tests/integration/compose.yaml": "tests/integration/compose.yaml",
    "tests/integration/test_state_store_firestore.py":
        "tests/integration/test_state_store_firestore.py",
    "tests/smoke/test_live_canary.py": "tests/smoke/test_live_canary.py",
    "scripts/deploy": "docs/reference/deploying-to-cloud-run.md",
    "docs/deploy.md": "docs/reference/deploying-to-cloud-run.md",
}


def _stamp_line(lines: list[str]) -> str:
    """Line 1, or line 2 when line 1 is a shebang that has to stay first."""
    return lines[1] if len(lines) > 1 and lines[0].startswith("#!") else lines[0]


@pytest.mark.parametrize("path", sorted(COPIED))
def test_copied_file_names_its_origin_on_line_1(path):
    first = _stamp_line((ROOT / path).read_text().splitlines())
    assert ORIGIN in first and COPIED[path] in first, first


def test_every_file_claiming_an_origin_is_listed():
    tracked = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout
    claiming = set()
    for name in tracked.splitlines():
        file = ROOT / name
        if name.startswith("tmp/") or not file.is_file():
            continue
        try:
            lines = file.read_text().splitlines()
        except UnicodeDecodeError:
            continue
        if lines and ORIGIN in _stamp_line(lines):
            claiming.add(name)
    assert claiming == set(COPIED)
