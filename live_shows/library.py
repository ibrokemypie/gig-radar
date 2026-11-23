"""The listener's artists and their tiers, from what they play and heart, and who is like them.

The rules (docs/design.md):

- Loved: at least 5 songs played 3 or more times each, or 3 or more hearted tracks.
- Maybe: 2 or more plays, or similar to 3 or more loved artists by ListenBrainz.
- Being in the library counts for nothing on its own.

Pinned: 2 or more albums played front to back, or one album twice, put on deliberately
(live_shows.albums), or 3 or more hearted tracks. A pinned artist is loved too, whatever
its plays.

And from what is trimmed on the page: a kept artist stays a maybe, and a loved artist
through which 3 artists were trimmed and none kept finds no new ones.

The plays and hearts come from Plex (read_plex here) or Last.fm (live_shows.lastfm), as
Plex's shapes: a play is a history row, and a row with a "count" stands for that many.
"""

import json
import logging
import urllib.parse
from collections import Counter, defaultdict
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime

from live_shows.albums import albums_worth_reading, front_to_back
from live_shows.http import Fetcher, json_of
from live_shows.model import Artist, Tier
from live_shows.names import clean_library_name, normalise

log = logging.getLogger(__name__)

HEARTED_RATING = 8
LOVED_SONGS_ON_REPEAT = 5
REPEAT_PLAYS = 3
LOVED_HEARTS = 3
PINNED_HEARTS = 3
PINNED_ALBUMS = 2  # different albums front to back, or listens of one album
MAYBE_PLAYS = 2
MAYBE_SIMILAR_TO_LOVED = 3
TRIMS_TO_STOP_A_SEED = 3
NOT_AN_ARTIST = {"various artists"}
HISTORY_PAGE = 10_000

LISTENBRAINZ_URL = "https://labs.api.listenbrainz.org/similar-artists/json"
LISTENBRAINZ_ALGORITHM = (
    "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30"
)


@dataclass
class Listening:
    """What the library holds about the listening.

    Each play, each hearted track, each artist's MBID, and the track ids in order of each
    album that may have been played front to back.
    """

    plays: list[dict] = field(default_factory=list)
    hearted: list[dict] = field(default_factory=list)
    artist_mbids: dict[str, str] = field(default_factory=dict)
    album_tracks: dict[str, list[str]] = field(default_factory=dict)


class AlbumOrderFetcher:
    """A Fetcher for an album's children that keeps only its track ids in disc and track order.

    It goes under a CachedFetcher, so the cache holds a few bytes an album, not Plex's page.
    """

    def __init__(self, fetcher: Fetcher) -> None:
        self.fetcher = fetcher

    def text(self, url: str, headers: dict[str, str] | None = None) -> str:
        """The album's track ids as a JSON list."""
        tracks = json_of(self.fetcher, url, headers)["MediaContainer"].get("Metadata") or []
        tracks.sort(key=lambda track: (track.get("parentIndex") or 1, track.get("index") or 0))
        return json.dumps([track["ratingKey"] for track in tracks])


def plex_url(base_url: str, token: str, path: str, **params: str) -> str:
    """A Plex API URL with the token on it."""
    return f"{base_url}{path}?{urllib.parse.urlencode({**params, 'X-Plex-Token': token})}"


def plex_music_section(fetcher: Fetcher, base_url: str, token: str) -> str:
    """The id of the server's first music library, for when none is set."""
    answer = json_of(fetcher, plex_url(base_url, token, "/library/sections"))
    for directory in answer["MediaContainer"].get("Directory") or []:
        if directory.get("type") == "artist":
            return str(directory["key"])
    raise RuntimeError("the Plex server has no music library; set PLEX_MUSIC_SECTION")


def read_plex(
    fetcher: Fetcher,
    base_url: str,
    token: str,
    account: str,
    section_id: str,
    album_fetcher: Fetcher | None = None,
) -> Listening:
    """An account's whole play history in one music section, its hearted tracks, the
    section's artist MBIDs and album orders.

    The history is read a page at a time. Only the albums some run of plays could have
    played through are read, through `album_fetcher` (an AlbumOrderFetcher, usually cached).
    """

    def url(path: str, **params: str) -> str:
        return plex_url(base_url, token, path, **params)

    def metadata(path: str, **params: str) -> list[dict]:
        return json_of(fetcher, url(path, **params))["MediaContainer"].get("Metadata") or []

    section = f"/library/sections/{section_id}/all"
    plays: list[dict] = []
    while True:
        page = metadata(
            "/status/sessions/history/all",
            accountID=account,
            librarySectionID=section_id,
            **{
                "X-Plex-Container-Start": str(len(plays)),
                "X-Plex-Container-Size": str(HISTORY_PAGE),
            },
        )
        plays += page
        if len(page) < HISTORY_PAGE:
            break
    hearted = metadata(section, type="10", **{"userRating>>": str(HEARTED_RATING - 1)})
    artists = metadata(section, type="8", includeGuids="1")

    album_fetcher = album_fetcher or AlbumOrderFetcher(fetcher)
    album_tracks = {
        album: json.loads(album_fetcher.text(url(f"/library/metadata/{album}/children")))
        for album in sorted(albums_worth_reading(plays))
    }
    return Listening(plays, hearted, _mbids_by_artist(artists), album_tracks)


def _mbids_by_artist(artists: list[dict]) -> dict[str, str]:
    """Each library artist's key mapped to its MusicBrainz id, where Plex has one."""
    mbids = {}
    for artist in artists:
        for guid in artist.get("Guid") or []:
            if guid.get("id", "").startswith("mbid://"):
                mbids[normalise(clean_library_name(artist["title"]))] = guid["id"].removeprefix(
                    "mbid://"
                )
    return mbids


def similar_artists(fetcher: Fetcher, mbid: str) -> list[dict]:
    """ListenBrainz's artists similar to one, most similar first; each has a name and MBID."""
    query = urllib.parse.urlencode({"artist_mbids": mbid, "algorithm": LISTENBRAINZ_ALGORITHM})
    answer = json_of(fetcher, f"{LISTENBRAINZ_URL}?{query}")
    return answer if isinstance(answer, list) else []


def played_tiers(listening: Listening) -> dict[str, Artist]:
    """Every artist played, hearted or played album by album enough for a tier, by key."""
    names: dict[str, str] = {}
    plays: Counter[str] = Counter()
    track_plays: defaultdict[str, Counter[str]] = defaultdict(Counter)
    hearts: defaultdict[str, set[str]] = defaultdict(set)

    for play in listening.plays:
        if key := _artist_key(play, names):
            count = play.get("count", 1)
            plays[key] += count
            track_plays[key][_track_id(play)] += count
    for track in listening.hearted:
        if key := _artist_key(track, names):
            hearts[key].add(_track_id(track))

    album_listens = front_to_back(listening.plays, listening.album_tracks)

    tiers = {}
    for key, name in names.items():
        on_repeat = sum(1 for count in track_plays[key].values() if count >= REPEAT_PLAYS)
        hearted = len(hearts[key])
        albums = album_listens.get(key, Counter())
        most_listens = max(albums.values(), default=0)
        is_pinned = (
            len(albums) >= PINNED_ALBUMS
            or most_listens >= PINNED_ALBUMS
            or hearted >= PINNED_HEARTS
        )
        if is_pinned or on_repeat >= LOVED_SONGS_ON_REPEAT or hearted >= LOVED_HEARTS:
            tier = Tier.LOVE
        elif plays[key] >= MAYBE_PLAYS:
            tier = Tier.MAYBE
        else:
            continue
        tiers[key] = Artist(
            name=name,
            key=key,
            tier=tier,
            plays=plays[key],
            songs_on_repeat=on_repeat,
            hearts=hearted,
            albums_front_to_back=len(albums),
            most_album_listens=most_listens,
            pinned=is_pinned,
            mbid=listening.artist_mbids.get(key),
        )
    return tiers


def fewer_like(choices: dict[str, dict], unlearned: dict[str, str]) -> dict[str, int]:
    """The loved artists that find no new maybes, each with how many found by it were trimmed.

    One counts once 3 artists found through it were trimmed and none kept; trims from
    before that was undone (`unlearned`) no longer count.
    """
    trimmed: Counter[str] = Counter()
    kept_from = set()
    for choice in choices.values():
        for seed in choice["seeds"]:
            if choice["choice"] == "kept":
                kept_from.add(seed)
            elif seed not in unlearned or datetime.fromisoformat(
                choice["at"]
            ) > datetime.fromisoformat(unlearned[seed]):
                trimmed[seed] += 1
    return {
        seed: count
        for seed, count in trimmed.items()
        if count >= TRIMS_TO_STOP_A_SEED and seed not in kept_from
    }


def add_similar(
    tiers: dict[str, Artist],
    similar: dict[str, list[dict]],
    stopped_seeds: Collection[str] = (),
    kept: dict[str, dict] | None = None,
) -> None:
    """Make Maybe each artist ListenBrainz lists as similar to 3 or more loved artists.

    `similar` maps a loved artist's MBID to ListenBrainz's answer for it. An artist already
    Maybe by plays keeps its tier and gains the loved artists it is like. The loved artists
    named in `stopped_seeds` find nobody, and a kept artist (`kept`, by key) stays a
    Maybe even when nothing finds it any more.
    """
    loved_by_mbid = {
        artist.mbid: artist
        for artist in tiers.values()
        if artist.tier is Tier.LOVE and artist.mbid and artist.name not in stopped_seeds
    }
    like: defaultdict[str, list[str]] = defaultdict(list)
    found: dict[str, dict] = {}
    for seed_mbid, neighbours in similar.items():
        seed = loved_by_mbid.get(seed_mbid)
        if seed is None:
            continue
        for neighbour in neighbours:
            key = normalise(neighbour["name"])
            if seed.name not in like[key]:
                like[key].append(seed.name)
            found.setdefault(key, neighbour)

    for key, seeds in like.items():
        artist = tiers.get(key)
        if artist and artist.tier is Tier.LOVE:
            continue
        if artist:
            artist.similar_to = seeds
        elif len(seeds) >= MAYBE_SIMILAR_TO_LOVED:
            tiers[key] = Artist(
                name=found[key]["name"],
                key=key,
                tier=Tier.MAYBE,
                mbid=found[key].get("artist_mbid"),
                similar_to=seeds,
            )

    for key, choice in (kept or {}).items():
        if key not in tiers:
            tiers[key] = Artist(
                name=choice["name"], key=key, tier=Tier.MAYBE, similar_to=choice["seeds"]
            )


def build_tiers(
    listening: Listening,
    similar_lookup: Fetcher,
    stopped_seeds: Collection[str] = (),
    kept: dict[str, dict] | None = None,
) -> dict[str, Artist]:
    """Every artist with a tier: played ones, then those like enough of the loved ones.

    `similar_lookup` fetches ListenBrainz, usually through a cache; a loved artist whose
    lookup fails adds nobody this run. `stopped_seeds` and `kept` are as add_similar takes.
    """
    tiers = played_tiers(listening)
    similar = {}
    for artist in tiers.values():
        if artist.tier is not Tier.LOVE or not artist.mbid:
            continue
        try:
            similar[artist.mbid] = similar_artists(similar_lookup, artist.mbid)
        except Exception as error:  # one lookup must not end the run
            log.warning("listenbrainz: %s: %s", artist.name, error)
    add_similar(tiers, similar, stopped_seeds, kept)
    return tiers


def _artist_key(item: dict, names: dict[str, str]) -> str | None:
    """The artist key of a play or track, remembering its printable name; None for none."""
    title = item.get("grandparentTitle")
    if not title:
        return None
    name = clean_library_name(title)
    key = normalise(name)
    if not key or key in NOT_AN_ARTIST:
        return None
    names.setdefault(key, name)
    return key


def _track_id(item: dict) -> str:
    """A track's id: its rating key, or its title for a track since removed from the library."""
    return item.get("ratingKey") or normalise(item.get("title") or "")
