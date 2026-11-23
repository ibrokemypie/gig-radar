"""Short previews of a maybe artist, to sample it on the page and decide.

Three music videos of the artist's own, or its top three songs when it has none. They
come from Apple's public iTunes Search API, which needs no account or token; its previews
are 30-second files every browser plays as they are.
"""

import urllib.parse
from typing import Any

from live_shows.http import Fetcher, json_of
from live_shows.names import normalise

ITUNES = "https://itunes.apple.com"
SAMPLES_EACH = 3
# More than SAMPLES_EACH, since features and repeats are dropped from what comes back.
LOOKUP_LIMIT = 10
VIDEO_ART = "320x180"
SONG_ART = "200x200"


def samples_for(fetcher: Fetcher, name: str, country: str = "us") -> dict[str, Any]:
    """An artist's samples: its Apple Music page and three videos, or three songs, or none.

    `country` is the store to search, the home country's code.

    The record is `{"apple": url | None, "kind": "videos" | "songs" | "none", "items": [...]}`,
    each item a name, artwork, preview file and Apple Music link.
    """
    found = find_artist(fetcher, name, country)
    if found is None:
        return {"apple": None, "kind": "none", "items": []}
    apple = found["artistLinkUrl"].split("?")[0]

    videos = own_items(
        lookup(fetcher, found["artistId"], "musicVideo", country), name, "music-video"
    )
    if videos:
        return {"apple": apple, "kind": "videos", "items": videos}
    songs = own_items(lookup(fetcher, found["artistId"], "song", country), name, "song")
    return {"apple": apple, "kind": "songs" if songs else "none", "items": songs}


def find_artist(fetcher: Fetcher, name: str, country: str) -> dict[str, Any] | None:
    """Apple's artist of this name, or None when no artist found has exactly this name."""
    query = urllib.parse.urlencode(
        {"term": name, "country": country, "media": "music", "entity": "musicArtist", "limit": 5}
    )
    results = json_of(fetcher, f"{ITUNES}/search?{query}").get("results", [])
    return next((r for r in results if normalise(r.get("artistName", "")) == normalise(name)), None)


def lookup(fetcher: Fetcher, artist_id: int, entity: str, country: str) -> list[dict[str, Any]]:
    """An artist's tracks of one entity (musicVideo, song), as Apple ranks them in a store."""
    query = urllib.parse.urlencode(
        {"id": artist_id, "country": country, "entity": entity, "limit": LOOKUP_LIMIT}
    )
    results = json_of(fetcher, f"{ITUNES}/lookup?{query}").get("results", [])
    return [result for result in results if result.get("wrapperType") == "track"]


def own_items(tracks: list[dict[str, Any]], name: str, kind: str) -> list[dict[str, str]]:
    """The first three tracks of `kind` that are the artist's own, each title once.

    A track whose artist is a collaboration or another act is left out: Daniel Caesar's top
    videos were all other artists' songs he features on.
    """
    items: list[dict[str, str]] = []
    seen_titles = set()
    for track in tracks:
        title = normalise(track.get("trackName", ""))
        if (
            track.get("kind") != kind
            or normalise(track.get("artistName", "")) != normalise(name)
            or not track.get("previewUrl")
            or title in seen_titles
        ):
            continue
        seen_titles.add(title)
        items.append(
            {
                "name": track["trackName"],
                "art": artwork(
                    track.get("artworkUrl100", ""), VIDEO_ART if kind == "music-video" else SONG_ART
                ),
                "preview": track["previewUrl"],
                "url": track.get("trackViewUrl", "").split("?")[0],
            }
        )
        if len(items) == SAMPLES_EACH:
            break
    return items


def artwork(url: str, size: str) -> str:
    """An Apple artwork URL resized: '…/100x100bb.jpg' becomes '…/320x180bb.jpg'."""
    head, _, file = url.rpartition("/")
    if not head or "bb." not in file:
        return url
    return f"{head}/{size}bb.{file.rsplit('.', 1)[-1]}"
