"""Readers for the listing sites, each turning one site's listings into Events.

Every reader takes the window of days to keep and the home Place, and returns the Events
near home within it.
A reader that finds nothing raises SourceEmpty: a site that changed its pages reads as
empty, and an empty run must never pass for a quiet week.
"""

import html
import json
import re
from collections.abc import Iterator
from datetime import date, datetime, tzinfo
from typing import Any


class SourceEmpty(RuntimeError):
    """A source returned no listings at all, which means it broke, not that nothing is on."""


def in_window(when: datetime, first_day: date, last_day: date, zone: tzinfo) -> bool:
    """Whether an event starts between the two days, inclusive, in local time."""
    return first_day <= when.astimezone(zone).date() <= last_day


def local_time(text: str, zone: tzinfo) -> datetime:
    """A seller's local date and time ('2026-10-20T19:00:00', with or without an offset)."""
    when = datetime.fromisoformat(text)
    return when if when.tzinfo else when.replace(tzinfo=zone)


def json_ld_blocks(page: str) -> Iterator[Any]:
    """Each JSON-LD object on a page, lists flattened; a block that does not parse is skipped."""
    for block in re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', page, re.DOTALL
    ):
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        yield from parsed if isinstance(parsed, list) else [parsed]


def unescaped(text: str | None) -> str:
    """HTML entities decoded and the ends trimmed; None as empty."""
    return html.unescape(text or "").strip()


def looks_like_festival(title: str) -> bool:
    """Whether a title names a festival; a Festival Hall is a venue, not one."""
    return "festival" in re.sub(r"festival hall", "", title.lower())
