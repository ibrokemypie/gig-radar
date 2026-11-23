"""Ticketmaster's Discovery API: music events near home, with the time each goes on sale.

It needs a free API key of your own, typed on the settings page; with none it is skipped. It
is the one source that says when tickets go on sale, which the on-sale reminder needs.
"""

import logging
import urllib.parse
from collections.abc import Callable
from datetime import date, datetime

from live_shows.http import Fetcher, json_of
from live_shows.model import Event, Performer
from live_shows.settings import Place
from live_shows.sources import SourceEmpty, in_window, looks_like_festival

NAME = "Ticketmaster"
log = logging.getLogger(__name__)
# The API's `latlong` filter "is deprecated and maybe removed in a future release, please use
# geoPoint instead"; geoPoint takes a geohash, and 9 characters place home within metres.
GEOHASH_PRECISION = 9
_GEOHASH_ALPHABET = "0123456789bcdefghjkmnpqrstuvwxyz"
API = "https://app.ticketmaster.com/discovery/v2/events.json"
PAGE_SIZE = 200
# The API refuses a page whose size times number passes 1,000.
MAX_PAGES = 1000 // PAGE_SIZE


def fetch(
    fetcher: Fetcher,
    api_key: str,
    place: Place,
    first_day: date,
    last_day: date,
    warn: Callable[[str], None] = log.warning,
) -> list[Event]:
    """Every music event within the radius of home in the window, page by page.

    The API serves at most 1,000 events a search. When home has more before the window
    ends, the later ones are missed, and `warn` is told up to which day it read.
    """
    events: list[Event] = []
    for page in range(MAX_PAGES):
        query = urllib.parse.urlencode(
            {
                "apikey": api_key,
                "countryCode": place.country,
                "geoPoint": geohash(place.latitude, place.longitude),
                "radius": round(place.radius_km),
                "unit": "km",
                "classificationName": "Music",
                "sort": "date,asc",
                "size": PAGE_SIZE,
                "page": page,
                "startDateTime": f"{first_day.isoformat()}T00:00:00Z",
            }
        )
        answer = json_of(fetcher, f"{API}?{query}")
        listings = (answer.get("_embedded") or {}).get("events") or []
        events += [
            event
            for listing in listings
            if (event := _event(listing, place))
            and in_window(event.starts_at, first_day, last_day, place.zone)
        ]
        if page + 1 >= (answer.get("page") or {}).get("totalPages", 0):
            break
    else:
        last_read = max((e.starts_at.astimezone(place.zone).date() for e in events), default=None)
        if last_read is None or last_read < last_day:
            warn(
                f"{NAME}: stopped at the API's {MAX_PAGES * PAGE_SIZE:,} events; nothing after"
                f" {last_read or first_day} was read (a smaller HOME_RADIUS_KM reads further ahead)"
            )
    if not events:
        raise SourceEmpty(f"{NAME}: no music events near home")
    return events


def geohash(latitude: float, longitude: float, precision: int = GEOHASH_PRECISION) -> str:
    """A point as a geohash: longitude and latitude bits interleaved, five to a character."""
    bounds = {"lat": [-90.0, 90.0], "lon": [-180.0, 180.0]}
    value = {"lat": latitude, "lon": longitude}
    characters = []
    bits = 0
    for index in range(precision * 5):
        axis = "lon" if index % 2 == 0 else "lat"
        low, high = bounds[axis]
        middle = (low + high) / 2
        bit = value[axis] >= middle
        bounds[axis] = [middle, high] if bit else [low, middle]
        bits = bits * 2 + bit
        if index % 5 == 4:
            characters.append(_GEOHASH_ALPHABET[bits])
            bits = 0
    return "".join(characters)


def _event(listing: dict, place: Place) -> Event | None:
    """One Discovery event as an Event, or None when it has no date yet."""
    start = (listing.get("dates") or {}).get("start") or {}
    if not start.get("localDate"):
        return None
    starts_at = datetime.fromisoformat(
        f"{start['localDate']}T{start.get('localTime') or '19:00:00'}"
    ).replace(tzinfo=place.zone)

    embedded = listing.get("_embedded") or {}
    venue = (embedded.get("venues") or [{}])[0]
    performers = [
        Performer(
            attraction["name"],
            ((attraction.get("externalLinks") or {}).get("musicbrainz") or [{}])[0].get("id"),
        )
        for attraction in embedded.get("attractions") or []
        if attraction.get("name")
    ]
    public_sale = ((listing.get("sales") or {}).get("public") or {}).get("startDateTime")
    title = listing.get("name") or ""
    return Event(
        source=NAME,
        event_id=f"tm:{listing['id']}",
        title=title,
        starts_at=starts_at,
        venue=venue.get("name") or "",
        locality=(venue.get("city") or {}).get("name") or "",
        url=listing.get("url") or "",
        performers=performers,
        is_festival=looks_like_festival(title),
        on_sale_at=(
            datetime.fromisoformat(public_sale).astimezone(place.zone) if public_sale else None
        ),
    )
