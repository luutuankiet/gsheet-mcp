# Copied from google-docs-mcp@2fb4652 src/<package>/skill_bundle.py; renamed, skill name and repo URL swapped.
"""The agent skill that ships with the server, served by the `install_skill` tool.

The skill lives inside the package, at `gsheet_mcp/skills/gsheet-mcp/`, so
the wheel and the Cloud Run image carry it with no extra build step: the
image build copies only `src/` (see `.dockerignore`).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import __version__

SKILL_NAME = "gsheet-mcp"
REPO_URL = "https://github.com/luutuankiet/gsheet-mcp"

INSTALL_LOCATIONS: list[dict[str, str]] = [
    {"agent": "Claude Code", "user": "~/.claude/skills/", "project": ".claude/skills/"},
    {"agent": "Codex CLI", "user": "~/.agents/skills/", "project": ".agents/skills/"},
    {
        "agent": "Gemini CLI",
        "user": "~/.gemini/skills/ (also reads ~/.agents/skills/)",
        "project": ".gemini/skills/ (also reads .agents/skills/)",
    },
    {
        "agent": "anything else",
        "user": "ask the user where skills go",
        "project": "ask the user",
    },
]

INSTRUCTIONS: list[str] = [
    "If the user did not ask to install the skill, you are only loading it: read "
    "the files as reference for this conversation, skip the steps below, and do not "
    "ask the user anything about it.",
    "Identify which agent tool you are running in.",
    "Ask the user whether to install for their user account or the current project, "
    "and show the matching folder from install_locations. If your tool is not listed, "
    "ask the user where skills go.",
    f"If <root>/{SKILL_NAME}/ exists, delete it entirely.",
    f"Write each file to <root>/{SKILL_NAME}/<path>, creating folders as needed. The "
    "text blocks after this one hold the files, one block each, in the order of files.",
    "Tell the user what was installed, the version, and that a restart or new session "
    "may be needed before the skill is picked up.",
    "If you cannot write files, use the files as reference for this conversation and "
    "tell the user the skill could not be saved.",
    "To update later, call this tool again after the server is upgraded.",
]


def skill_dir() -> Path:
    """The skill folder inside the installed package."""
    here = Path(__file__).resolve().parent / "skills" / SKILL_NAME
    if not (here / "SKILL.md").is_file():
        raise FileNotFoundError(
            f"The {SKILL_NAME} skill files are missing from this installation (looked in "
            f"{here}). This is a packaging bug; please report it at {REPO_URL}/issues.")
    return here


def _skipped(rel: Path) -> bool:
    return any(part.startswith(".") or part == "__pycache__" for part in rel.parts)


def skill_files(root: Path) -> list[dict[str, str]]:
    files = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if path.is_file() and not _skipped(rel):
            files.append({"path": rel.as_posix(), "content": path.read_text(encoding="utf-8")})
    return sorted(files, key=lambda f: f["path"])


def bundle() -> dict[str, Any]:
    return {
        "skill_name": SKILL_NAME,
        "skill_version": __version__,
        "source_url": f"{REPO_URL}/tree/v{__version__}/src/gsheet_mcp/skills/{SKILL_NAME}",
        "files": skill_files(skill_dir()),
        "install_locations": INSTALL_LOCATIONS,
        "instructions": INSTRUCTIONS,
    }
