"""`install_skill` through the tool layer: the bundle, the tools it names, and
the notation table checked against what the read module really prints."""
from __future__ import annotations

import json
import re
from pathlib import Path

from gsheet_mcp import notation, skill_bundle
from tests.conftest import call
from tests.fake_sheets import CANARY_ID

SPEC = Path(__file__).resolve().parents[2] / "docs" / "spec.md"
COOKBOOK = "sheets-cookbook.md"


async def _bundle(client) -> tuple[dict, dict[str, str]]:
    result = await client.call_tool("install_skill", {})
    assert not result.is_error
    blocks = [b.text for b in result.content]
    meta = json.loads(blocks[0])
    assert len(blocks) == 1 + len(meta["files"])
    return meta, dict(zip(meta["files"], blocks[1:], strict=True))


def _spec_tools() -> set[str]:
    """Tool names as the spec introduces them: in a heading, as the first cell
    of a table row, or as a bold lead-in; a tool name is snake_case."""
    text = SPEC.read_text()
    name = r"`([a-z]+_[a-z_]+)`"
    names = set(re.findall(rf"^#+ .*{name}", text, re.M))
    names |= set(re.findall(rf"^\| {name} \|", text, re.M))
    names |= set(re.findall(rf"\*\*{name}", text))
    return names


def _table(markdown: str, header: str) -> dict[str, str]:
    """The rows of the Markdown table whose header row is `header`, first cell -> second."""
    lines = [line.strip() for line in markdown.splitlines()]
    start = lines.index(header) + 2
    rows = {}
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line)[1:-1]]
        rows[cells[0]] = cells[1]
    return rows


def _literals(cell: str) -> list[str]:
    return [lit.replace("\\|", "|") for lit in re.findall(r"`([^`]+)`", cell)]


async def test_install_skill_returns_the_bundle(client):
    meta, files = await _bundle(client)
    assert meta["skill_name"] == "gsheet-mcp"
    assert meta["files"] == ["SKILL.md", COOKBOOK]
    assert files["SKILL.md"].startswith("---\nname: gsheet-mcp\n")
    assert f"]({COOKBOOK})" in files["SKILL.md"]
    assert meta["instructions"] == skill_bundle.INSTRUCTIONS
    headings = re.findall(r"^## (.+)$", files[COOKBOOK], re.M)
    assert headings == ["Scan first", "The read notation", "The plan path", "When to render",
                        "When to pull with the gsheet CLI", "The guard's limits"]
    assert "narrows the race to about one round trip. It is not a lock" in files[COOKBOOK]


async def test_every_tool_in_the_spec_is_registered_and_named_in_the_bundle(client):
    registered = {t.name for t in await client.list_tools()}
    spec = _spec_tools()
    assert spec == registered
    _, files = await _bundle(client)
    text = "\n".join(files.values())
    missing = sorted(name for name in registered if f"`{name}`" not in text)
    assert not missing, missing


async def test_notation_table_matches_the_spec_and_the_read_module(fake, signed_in, client):
    _, files = await _bundle(client)
    skill = _table(files[COOKBOOK], "| thing | shown as |")
    spec = _table(SPEC.read_text(), "| thing | shown as |")
    assert set(skill) == set(spec)

    # What the read module prints: the canary's comms tab read through the
    # tool, plus the cases the canary does not hold, through the same notation.
    read, is_error = await call(client, "read_range", spreadsheet=CANARY_ID, range="'comms'")
    assert not is_error, read
    rule = {"condition": {"type": "ONE_OF_LIST", "values": [
        {"userEnteredValue": v} for v in ("On track", "At risk", "Blocked")]}}
    printed = read.splitlines() + [
        notation.value({"formattedValue": "Done", "dataValidation": rule}, "v1"),
        notation.value({"formattedValue": "Ada", "chipRuns": [
            {"startIndex": 0, "chip": {"personProperties": {"email": "ada@example.com"}}}]}),
        notation.value({"userEnteredValue": {
            "formulaValue": '=IMAGE("https://example.com/logo.png")'}}),
    ]
    for thing, shown in skill.items():
        literals = _literals(shown)
        assert literals, thing
        for literal in literals:
            assert any(line == literal or line.endswith(f": {literal}") or line.strip() == literal
                       for line in printed), (thing, literal)


async def test_style_legend_example_matches_the_read(fake, signed_in, client):
    _, files = await _bundle(client)
    example = re.search(r"```text\n(.*?)```", files[COOKBOOK], re.S).group(1).splitlines()
    read, _ = await call(client, "read_range", spreadsheet=CANARY_ID, range="'comms'")
    lines = read.splitlines()
    for line in example:
        assert line in lines, line
