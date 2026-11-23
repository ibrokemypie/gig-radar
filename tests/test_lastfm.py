import json

import pytest
from conftest import FakeWeb

from live_shows import lastfm
from live_shows.library import played_tiers
from live_shows.model import Tier


def top_tracks(tracks: list[dict], page: int = 1, total_pages: int = 1) -> str:
    """A user.getTopTracks answer, as Last.fm shapes it."""
    attr = {"user": "listener", "page": str(page), "totalPages": str(total_pages)}
    return json.dumps({"toptracks": {"track": tracks, "@attr": attr}})


def loved_tracks(tracks: list[dict] | dict) -> str:
    """A user.getLovedTracks answer."""
    attr = {"user": "listener", "page": "1", "totalPages": "1"}
    return json.dumps({"lovedtracks": {"track": tracks, "@attr": attr}})


def track(name: str, artist: str, plays: int | None = None, mbid: str = "") -> dict:
    found = {"name": name, "mbid": "", "artist": {"name": artist, "mbid": mbid, "url": "x"}}
    if plays is not None:
        found["playcount"] = str(plays)
    return found


def test_top_tracks_are_counted_plays_and_loved_tracks_are_hearts() -> None:
    web = FakeWeb({
        "method=user.gettoptracks": top_tracks(
            [track(f"Song {i}", "Moth Signal", 4, "mb-moth") for i in range(5)]
            + [track("One", "Quiet Atlas", 2)]
        ),
        "method=user.getlovedtracks": loved_tracks([
            track("Lantern 1", "The Paper Lanterns", mbid="mb-lanterns"),
            track("Lantern 2", "The Paper Lanterns"),
            track("Lantern 3", "The Paper Lanterns"),
        ]),
    })  # fmt: skip
    listening = lastfm.read_lastfm(web, "listener", "KEY")

    assert listening.plays[0] == {"grandparentTitle": "Moth Signal", "title": "Song 0", "count": 4}
    assert listening.artist_mbids == {"moth signal": "mb-moth", "paper lanterns": "mb-lanterns"}
    tiers = played_tiers(listening)
    assert tiers["moth signal"].tier is Tier.LOVE
    assert tiers["moth signal"].plays == 20
    assert not tiers["moth signal"].pinned
    assert tiers["quiet atlas"].tier is Tier.MAYBE
    assert tiers["paper lanterns"].pinned
    url = web.requests[0][0]
    assert "api_key=KEY" in url
    assert "user=listener" in url
    assert "format=json" in url
    assert "period=overall" in url


def test_pages_are_read_until_the_last_and_a_lone_track_comes_bare() -> None:
    def chart(url: str) -> str:
        page = int(url.split("page=")[1].split("&")[0])
        return top_tracks([track(f"S{page}", "A", 1)], page, 3)

    web = FakeWeb(
        {"user.gettoptracks": chart, "user.getlovedtracks": loved_tracks(track("L", "A"))}
    )
    listening = lastfm.read_lastfm(web, "listener", "KEY")

    assert [play["title"] for play in listening.plays] == ["S1", "S2", "S3"]
    assert listening.hearted == [{"grandparentTitle": "A", "title": "L"}]


def test_paging_stops_at_the_cap(monkeypatch) -> None:
    monkeypatch.setattr(lastfm, "MAX_PAGES", 2)
    web = FakeWeb({"user.gettoptracks": top_tracks([], 1, 50), "user.getlovedtracks": "{}"})
    listening = lastfm.read_lastfm(web, "listener", "KEY")
    assert listening.plays == []
    assert len(web.requests) == 3


def test_a_lastfm_error_is_raised_with_its_message() -> None:
    web = FakeWeb(
        {"ws.audioscrobbler.com": json.dumps({"error": 10, "message": "Invalid API key"})}
    )
    with pytest.raises(lastfm.LastfmError, match="Invalid API key"):
        lastfm.read_lastfm(web, "listener", "BAD")
    web = FakeWeb({"ws.audioscrobbler.com": json.dumps({"error": 8})})
    with pytest.raises(lastfm.LastfmError, match="8"):
        lastfm.read_lastfm(web, "listener", "KEY")


def test_a_track_missing_its_artist_or_plays_counts_for_nothing() -> None:
    web = FakeWeb({
        "user.gettoptracks": top_tracks([{"name": "Lost"}, track("No count", "B")]),
        "user.getlovedtracks": loved_tracks([]),
    })  # fmt: skip
    listening = lastfm.read_lastfm(web, "listener", "KEY")
    assert listening.plays == [
        {"grandparentTitle": "", "title": "Lost", "count": 0},
        {"grandparentTitle": "B", "title": "No count", "count": 0},
    ]
    assert played_tiers(listening) == {}
