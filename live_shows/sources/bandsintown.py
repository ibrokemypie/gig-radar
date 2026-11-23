"""Bandsintown: each artist's upcoming dates, looked up one artist at a time.

It is the one source that finds arena shows sold through Ticketmaster and Ticketek. The
API is the one Bandsintown's own embeddable widget calls, with the widget's app id.
"""

import logging
import urllib.parse
from datetime import date

from live_shows.http import Fetcher, json_of
from live_shows.model import Artist, Event, Performer
from live_shows.names import HYPHENS
from live_shows.settings import Place
from live_shows.sources import (
    SourceEmpty,
    in_window,
    local_time,
    looks_like_festival,
)

NAME = "Bandsintown"
log = logging.getLogger(__name__)


def artist_url(name: str) -> str:
    """The upcoming-events URL for an artist.

    Bandsintown knows names only in plain punctuation, so a curly apostrophe or a
    typographic hyphen is a 404. A slash is escaped twice (%252F), as its API asks:
    escaped once, the server reads it as a path separator.
    """
    plain = HYPHENS.sub("-", name).replace("\u2019", "'")
    quoted = urllib.parse.quote(plain, safe="").replace("%2F", "%252F")
    return f"https://rest.bandsintown.com/artists/{quoted}/events?app_id=js_app_id&date=upcoming"


def fetch(
    fetcher: Fetcher, artists: list[Artist], place: Place, first_day: date, last_day: date
) -> list[Event]:
    """Every listed date near home in the window, for each artist in turn.

    An artist Bandsintown does not know, or one lookup that fails, is skipped; a run in
    which every lookup failed raises SourceEmpty.
    """
    events: list[Event] = []
    answered = 0
    for artist in artists:
        try:
            listings = json_of(fetcher, artist_url(artist.name))
        except Exception as error:  # one artist must not end the run
            log.warning("bandsintown: %s: %s", artist.name, error)
            continue
        answered += 1
        if not isinstance(listings, list):
            continue
        events += [
            event
            for listing in listings
            if (event := _event(listing, artist, place))
            and in_window(event.starts_at, first_day, last_day, place.zone)
        ]
    if artists and not answered:
        raise SourceEmpty(f"{NAME}: no lookup of {len(artists)} artists answered")
    return events


def _event(listing: dict, artist: Artist, place: Place) -> Event | None:
    """One listing as an Event, or None when it is not near home."""
    venue = listing.get("venue") or {}
    try:
        latitude, longitude = float(venue["latitude"]), float(venue["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    if not place.near(latitude, longitude):
        return None

    mbid = (listing.get("artist") or {}).get("mbid") or None
    performers = [
        Performer(name, mbid if name == artist.name else None)
        for name in listing.get("lineup") or [artist.name]
    ]
    offers = listing.get("offers") or []
    ticket_url = next((o["url"] for o in offers if o.get("type") == "Tickets"), listing["url"])
    on_sale = listing.get("on_sale_datetime") or None
    title = listing.get("title") or ""
    return Event(
        source=NAME,
        event_id=f"bit:{listing['id']}",
        title=title,
        starts_at=local_time(listing["datetime"], place.zone),
        venue=venue.get("name") or "",
        locality=venue.get("city") or "",
        url=ticket_url,
        performers=performers,
        is_festival=bool(listing.get("festival_start_date")) or looks_like_festival(title),
        on_sale_at=local_time(on_sale, place.zone) if on_sale else None,
    )
