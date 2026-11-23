"""musick.com.au: the catch-all, covering gigs sold through sellers no other source reads.

Its sitemap lists every Australian gig by a slug that leads with the act's name and ends
with the date, so only the pages of the listener's artists' gigs in the window are fetched, and each
page's JSON-LD says where it is and who plays.
"""

import re
from datetime import date

from live_shows.http import Fetcher
from live_shows.model import Artist, Event, Performer
from live_shows.names import slug
from live_shows.settings import Place
from live_shows.sources import (
    SourceEmpty,
    in_window,
    json_ld_blocks,
    local_time,
    looks_like_festival,
    unescaped,
)

NAME = "musick"
SITEMAP = "https://musick.com.au/sitemap-gigs.xml"
_GIG_URL = re.compile(r"<loc>(https://musick\.com\.au/gig/([a-z0-9-]+)-(\d{4}-\d{2}-\d{2})/)</loc>")


def candidate_urls(
    sitemap: str, artists: list[Artist], first_day: date, last_day: date
) -> list[str]:
    """The gig pages in the window whose slug leads with one of the artists' names."""
    prefixes = tuple(f"{slug(artist.name)}-" for artist in artists if slug(artist.name))
    return [
        url
        for url, gig_slug, day in _GIG_URL.findall(sitemap)
        if first_day.isoformat() <= day <= last_day.isoformat()
        and f"{gig_slug}-".startswith(prefixes)
    ]


def fetch(
    fetcher: Fetcher, artists: list[Artist], place: Place, first_day: date, last_day: date
) -> list[Event]:
    """The gigs in the home state among the artists' candidate pages."""
    sitemap = fetcher.text(SITEMAP)
    if "<loc>" not in sitemap:
        raise SourceEmpty(f"{NAME}: the gig sitemap lists nothing")
    events = []
    for url in candidate_urls(sitemap, artists, first_day, last_day):
        for block in json_ld_blocks(fetcher.text(url)):
            if (
                block.get("@type") == "MusicEvent"
                and (event := _event(block, place))
                and in_window(event.starts_at, first_day, last_day, place.zone)
            ):
                events.append(event)
    return events


def _event(block: dict, place: Place) -> Event | None:
    """A gig page's MusicEvent as an Event, or None outside the home state."""
    location = block.get("location") or {}
    address = location.get("address") or {}
    if address.get("addressRegion") != place.state:
        return None
    performers = block.get("performer") or []
    if isinstance(performers, dict):
        performers = [performers]
    offers = block.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    title = unescaped(block.get("name"))
    return Event(
        source=NAME,
        event_id=f"mu:{block['url'].rstrip('/').rsplit('/', 1)[-1]}",
        title=title,
        starts_at=local_time(block["startDate"], place.zone),
        venue=unescaped(location.get("name")),
        locality=unescaped(address.get("addressLocality")),
        url=offers.get("url") or block["url"],
        performers=[Performer(unescaped(p.get("name"))) for p in performers if p.get("name")],
        is_festival=looks_like_festival(title),
    )
