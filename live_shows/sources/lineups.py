"""Festival lineup pages: which acts play the home state's day, from the festival's own site.

A festival's site is the authority on its bill: the sources place acts at a festival in a
city the site marks them out of. A page lists each act as a line of text, sometimes with
a note under it such as "NSW EXCLUSIVE", which keeps that act out of the other states.
Each page names the country, and state where it is one city's, whose shows it lists.
"""

import html
import json
import re
from collections import Counter
from dataclasses import dataclass
from importlib import resources
from itertools import takewhile
from pathlib import Path

from live_shows.http import Fetcher
from live_shows.settings import Place

_STATES = re.compile(r"\b(QLD|NSW|VIC|SA|WA|TAS|ACT|NT)\b")
_LONGEST_ACT_NAME = 60
_NOTE_LINES = 4
# A line the page repeats this often is a block's boilerplate ("competition!"), which ends
# the notes under an act.
_SEPARATOR_REPEATS = 3


@dataclass(frozen=True)
class LineupPage:
    """A festival whose site publishes a lineup: the words its listings carry, and the page."""

    festival: str
    match: str
    url: str
    country: str | None = None
    state: str | None = None

    def applies_to(self, place: Place) -> bool:
        """Whether the page lists the home's shows: no country or state, or the home's."""
        return self.country in (None, place.country) and self.state in (None, place.state)


@dataclass(frozen=True)
class LineupAct:
    """One line of a lineup page and whether it plays the home state."""

    name: str
    plays_here: bool


def known_pages(path: Path | None = None) -> list[LineupPage]:
    """The lineup pages to read: from `path`, or festivals.json beside this module."""
    text = (path or resources.files("live_shows").joinpath("festivals.json")).read_text()
    return [LineupPage(**page) for page in json.loads(text)]


def page_lines(page: str) -> list[str]:
    """A page's visible text, one line per text node, scripts and styles left out."""
    page = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.DOTALL | re.IGNORECASE)
    lines = (html.unescape(line).strip() for line in re.split(r"<[^>]+>", page))
    return [line for line in lines if line]


def read_lineup(page: str, state: str) -> list[LineupAct]:
    """Every short line of a lineup page, as an act and whether it plays `state`.

    A line followed, before the block's boilerplate, by "<STATES> EXCLUSIVE" plays only
    those states; an "AUSTRALIAN EXCLUSIVE" plays them all. Lines that are not acts are
    returned too and simply match none of the artists.
    """
    lines = page_lines(page)
    separators = {
        line
        for line, count in Counter(lines).items()
        if count >= _SEPARATOR_REPEATS and "EXCLUSIVE" not in line
    }
    acts = []
    for index, line in enumerate(lines):
        if len(line) > _LONGEST_ACT_NAME or "EXCLUSIVE" in line or line in separators:
            continue
        notes = list(
            takewhile(
                lambda note: note not in separators, lines[index + 1 : index + 1 + _NOTE_LINES]
            )
        )
        exclusive = next((note for note in notes if "EXCLUSIVE" in note), None)
        states = _STATES.findall(exclusive) if exclusive and "AUSTRALIAN" not in exclusive else []
        acts.append(LineupAct(line, not states or state in states))
    return acts


def fetch(fetcher: Fetcher, page: LineupPage, state: str) -> list[LineupAct]:
    """The acts on one festival's lineup page."""
    return read_lineup(fetcher.text(page.url), state)
