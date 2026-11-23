"""Oztix: music events in the home state (Australia only), from its own site's search index.

The index's application id and public search key sit in the site's script bundle; they
are read from there on each run, so a rotated key needs no change here.
"""

import re
import urllib.parse
from datetime import date, datetime

from live_shows.http import Fetcher, json_of
from live_shows.model import Event, Performer
from live_shows.settings import Place
from live_shows.sources import SourceEmpty, in_window, unescaped

NAME = "Oztix"
HOME = "https://www.oztix.com.au/"
INDEX = "prod_oztix_eventguide"
FILTERS = "Venue.State:{state} AND Categories:Music"
_GUEST_SPLIT = re.compile(r",|&| \+ | and |/", re.IGNORECASE)


def search_credentials(fetcher: Fetcher) -> tuple[str, str]:
    """The (application id, search key) from the site's app bundle."""
    home = fetcher.text(HOME)
    bundle_path = re.search(r"/js/app\.[0-9a-f]+\.js", home)
    if not bundle_path:
        raise SourceEmpty(f"{NAME}: no app bundle on {HOME}")
    bundle = fetcher.text(urllib.parse.urljoin(HOME, bundle_path.group(0)))
    app_id = re.search(r'ALGOLIA_APPLICATION_ID:"(\w+)"', bundle)
    key = re.search(r'ALGOLIA_AUTOCOMPLETE_API_KEY:"(\w+)"', bundle)
    if not (app_id and key):
        raise SourceEmpty(f"{NAME}: the app bundle no longer names its search key")
    return app_id.group(1), key.group(1)


def fetch(fetcher: Fetcher, place: Place, first_day: date, last_day: date) -> list[Event]:
    """Every music event in the home state in the window, cancelled ones left out."""
    app_id, key = search_credentials(fetcher)
    query = urllib.parse.urlencode(
        {"filters": FILTERS.format(state=place.state), "hitsPerPage": 1000}
    )
    answer = json_of(
        fetcher,
        f"https://{app_id}-dsn.algolia.net/1/indexes/{INDEX}?{query}",
        {"X-Algolia-Application-Id": app_id, "X-Algolia-API-Key": key},
    )
    hits = answer.get("hits") or []
    if not hits:
        raise SourceEmpty(f"{NAME}: the {place.state} music search returned nothing")
    return [
        event
        for hit in hits
        if not hit.get("IsCancelled")
        and in_window((event := _event(hit, place)).starts_at, first_day, last_day, place.zone)
    ]


def _event(hit: dict, place: Place) -> Event:
    """One search hit as an Event; its start is a Unix time, its DateStart is UTC."""
    venue = hit.get("Venue") or {}
    return Event(
        source=NAME,
        event_id=f"oz:{hit['objectID']}",
        title=unescaped(hit.get("EventName")),
        starts_at=datetime.fromtimestamp(hit["DateStartUnix"], place.zone),
        venue=unescaped(venue.get("Name")),
        locality=unescaped(venue.get("Locality")),
        url=hit.get("EventUrl") or "",
        performers=[Performer(name) for name in _names(hit)],
        is_festival="Festivals" in (hit.get("Categories") or []),
    )


def _names(hit: dict) -> list[str]:
    """The acts a hit names: its bands, its performances and its special guests."""
    names = [*(hit.get("Bands") or [])]
    names += [p["Name"] for p in hit.get("Performances") or [] if p.get("Name")]
    guests = re.sub(
        r"^\s*(with|w/|plus|\+)\s+", "", hit.get("SpecialGuests") or "", flags=re.IGNORECASE
    )
    names += [guest.strip() for guest in _GUEST_SPLIT.split(guests) if guest.strip()]
    return list(dict.fromkeys(unescaped(name) for name in names))
