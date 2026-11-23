"""Moshtix: every music event in the home state (Australia only), from its browse pages' JSON-LD.

Many listings name no performer, so the act leading the title stands in for one
("La Dispute - Australian Tour"). A title elsewhere naming an act is usually someone
else's night, a tribute or a theme night, so only the lead counts and tributes are
skipped.
"""

import re
from datetime import date
from urllib.parse import quote

from live_shows.http import Fetcher
from live_shows.model import Event, Performer
from live_shows.names import normalise
from live_shows.settings import Place
from live_shows.sources import (
    SourceEmpty,
    in_window,
    json_ld_blocks,
    local_time,
    looks_like_festival,
    unescaped,
)

NAME = "Moshtix"
PAGE_URL = (
    "https://www.moshtix.com.au/v2/search?query=&StateId={state}&CategoryList=2&DateFilterId=6"
    "&Page={page}"
)
# The site's own state filter, read from its search page on 2026-11-23.
STATE_IDS = {"ACT": 1, "NSW": 2, "NT": 3, "QLD": 4, "SA": 5, "TAS": 6, "VIC": 7, "WA": 8}
MAX_PAGES = 40
NOT_THE_BAND = re.compile(
    r"tribute|themed|dj night|karaoke|celebrat|salute|a night of|plays the music of|vs\.? ",
    re.IGNORECASE,
)
_TITLE_LEAD_END = re.compile(r" - | – | : |: | with | w/ | \(| \| ", re.IGNORECASE)


def fetch(fetcher: Fetcher, place: Place, first_day: date, last_day: date) -> list[Event]:
    """Every listed music event in the home state in the window, page by page until one is
    empty."""
    state = STATE_IDS[place.state or ""]
    events: list[Event] = []
    for page_number in range(1, MAX_PAGES + 1):
        listings = [
            block
            for block in json_ld_blocks(
                fetcher.text(PAGE_URL.format(state=state, page=page_number))
            )
            if block.get("@type") == "MusicEvent"
        ]
        if not listings:
            break
        events += [
            event
            for listing in listings
            if in_window(
                (event := _event(listing, place)).starts_at, first_day, last_day, place.zone
            )
        ]
    if not events:
        raise SourceEmpty(f"{NAME}: no {place.state} music events on the browse pages")
    return events


def title_lead(title: str) -> str | None:
    """The act a title leads with, or None for a tribute or theme night."""
    if NOT_THE_BAND.search(title):
        return None
    return _TITLE_LEAD_END.split(title, maxsplit=1)[0].strip()


def _event(listing: dict, place: Place) -> Event:
    """One JSON-LD MusicEvent as an Event, the title's lead act standing in for performers."""
    title = unescaped(listing.get("name"))
    location = listing.get("location") or {}
    venue = unescaped(location.get("name"))
    venue_keys = {normalise(venue), normalise(venue.split(",")[0])}

    names = [
        unescaped(performer.get("name"))
        for performer in listing.get("performers") or []
        if normalise(unescaped(performer.get("name"))) not in venue_keys
    ]
    lead = title_lead(title)
    if lead and normalise(lead) not in {normalise(name) for name in names}:
        names.append(lead)

    url = quote(unescaped(listing.get("url")), safe=":/?&=#%")
    return Event(
        source=NAME,
        event_id=f"mt:{url.rstrip('/').rsplit('/', 1)[-1]}",
        title=title,
        starts_at=local_time(listing["startDate"], place.zone),
        venue=venue,
        locality=unescaped((location.get("address") or {}).get("addressLocality")),
        url=url,
        performers=[Performer(name) for name in names if name],
        is_festival=looks_like_festival(title),
    )
