"""Turn what an agent passes as a range into an A1 range: a handle
(`'table for comms'!B2:F8`, a whole tab) or a scan alias (`comms#2`)."""
from __future__ import annotations

import re

from . import a1, reply, scan

_ALIAS = re.compile(r"^.+#[1-9][0-9]*$")


def resolve(spreadsheet_id: str, text: str | None) -> a1.Range:
    """The A1 range for a handle or a live scan alias; an empty range for None.

    An alias is looked up first, so a tab whose name ends in `#3` is read as
    an alias only while a scan names it so."""
    if not text:
        return a1.Range()
    handle = scan.resolve_alias(spreadsheet_id, text)
    if handle:
        return a1.parse(handle)
    try:
        return a1.parse(text)
    except a1.A1Error as e:
        if _ALIAS.match(text):
            raise reply.Refusal(
                f"{text!r} is not a live scan alias (aliases last 10 minutes after "
                "scan_sheet, and any write clears them); run scan_sheet again or pass "
                "an A1 range") from None
        raise reply.Refusal(str(e)) from None
