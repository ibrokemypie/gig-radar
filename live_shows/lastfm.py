"""Last.fm as the library: a user's scrobbles and loved tracks, read through the public API.

What it maps onto (live_shows.library takes Plex's shapes):

- Each track in user.getTopTracks (the overall chart) is one history row with a "count"
  of its play count, so plays per artist and songs on repeat count as they do for Plex.
- Each loved track (user.getLovedTracks) is a hearted track.
- Each artist's MBID comes from the tracks it appears on.

Last.fm cannot say which albums were played front to back: that needs every scrobble in
order and each album's track list. So no artist is pinned by albums from Last.fm; 3 or
more loved tracks still pin one.
"""

import urllib.parse
from typing import Any

from live_shows.http import Fetcher, json_of
from live_shows.library import Listening
from live_shows.names import clean_library_name, normalise

API = "https://ws.audioscrobbler.com/2.0/"
PAGE_SIZE = 1000
# A cap so a broken answer cannot page for ever: 100,000 tracks.
MAX_PAGES = 100


class LastfmError(RuntimeError):
    """Last.fm answered with an error, such as a wrong key or an unknown user."""


def read_lastfm(fetcher: Fetcher, user: str, api_key: str) -> Listening:
    """A user's top tracks as plays and loved tracks as hearts, with artist MBIDs."""
    top = _tracks(fetcher, "user.gettoptracks", "toptracks", user, api_key, period="overall")
    loved = _tracks(fetcher, "user.getlovedtracks", "lovedtracks", user, api_key)
    plays = [{**_row(track), "count": int(track.get("playcount") or 0)} for track in top]
    hearted = [_row(track) for track in loved]
    mbids = {}
    for track in [*top, *loved]:
        artist = track.get("artist") or {}
        if artist.get("mbid") and artist.get("name"):
            mbids[normalise(clean_library_name(artist["name"]))] = artist["mbid"]
    return Listening(plays=plays, hearted=hearted, artist_mbids=mbids)


def _tracks(
    fetcher: Fetcher, method: str, root: str, user: str, api_key: str, **params: str
) -> list[dict[str, Any]]:
    """Every track a paged chart lists, page by page."""
    tracks: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        query = urllib.parse.urlencode(
            {
                "method": method,
                "user": user,
                "api_key": api_key,
                "format": "json",
                "limit": PAGE_SIZE,
                "page": page,
                **params,
            }
        )
        answer = json_of(fetcher, f"{API}?{query}")
        if "error" in answer:
            raise LastfmError(f"{method}: {answer.get('message') or answer['error']}")
        chart = answer.get(root) or {}
        found = chart.get("track") or []
        tracks += [found] if isinstance(found, dict) else found  # one track comes bare
        total_pages = int((chart.get("@attr") or {}).get("totalPages") or 0)
        if page >= total_pages:
            break
    return tracks


def _row(track: dict[str, Any]) -> dict[str, Any]:
    """A track as a Plex-shaped row: the artist as grandparentTitle, and its title."""
    artist = track.get("artist") or {}
    return {"grandparentTitle": artist.get("name") or "", "title": track.get("name") or ""}
