import json

from conftest import FakeWeb, fixture

from live_shows import samples

SEARCH = fixture("itunes-search-carpenter-brut.json")
VIDEOS = fixture("itunes-videos-carpenter-brut.json")
SONGS = fixture("itunes-songs-carpenter-brut.json")
NOTHING = json.dumps({"results": []})


def test_an_artist_gets_three_videos_of_its_own_from_apple() -> None:
    web = FakeWeb({"/search": SEARCH, "entity=musicVideo": VIDEOS})

    record = samples.samples_for(web, "Carpenter Brut", "au")

    assert record["apple"] == "https://music.apple.com/au/artist/carpenter-brut/593656958"
    assert record["kind"] == "videos"
    assert [item["name"] for item in record["items"]] == [
        "Speed or Perish",
        "Imaginary Fire (feat. Greg Puciato)",
        "Fab Tool (feat. David Eugene Edwards)",
    ]
    first = record["items"][0]
    assert first["preview"].endswith(".m4v")
    assert first["art"].endswith("/320x180bb.jpg")
    assert "?" not in first["url"]
    assert "country=au" in web.requests[0][0] and "id=593656958" in web.requests[1][0]


def test_an_artist_without_videos_of_its_own_gets_its_songs_each_title_once() -> None:
    web = FakeWeb({"/search": SEARCH, "entity=musicVideo": NOTHING, "entity=song": SONGS})

    record = samples.samples_for(web, "Carpenter Brut")

    assert record["kind"] == "songs"
    assert [item["name"] for item in record["items"]] == [
        "Maniac (feat. Yann Ligner)",
        "Turbo Killer",
        "You're Mine",
    ]
    assert record["items"][0]["art"].endswith("/200x200bb.jpg")


def test_an_artist_with_nothing_to_sample_keeps_its_apple_link() -> None:
    web = FakeWeb({"/search": SEARCH, "/lookup": NOTHING})
    assert samples.samples_for(web, "Carpenter Brut") == {
        "apple": "https://music.apple.com/au/artist/carpenter-brut/593656958",
        "kind": "none",
        "items": [],
    }


def test_an_artist_apple_does_not_name_exactly_has_no_samples() -> None:
    web = FakeWeb({"/search": SEARCH})
    assert samples.samples_for(web, "Carpenter") == {"apple": None, "kind": "none", "items": []}
    assert len(web.requests) == 1


def test_only_the_artists_own_tracks_with_a_preview_count() -> None:
    def track(name, artist="Toby Fox", kind="song", preview="p.m4a"):
        return {"trackName": name, "artistName": artist, "kind": kind, "previewUrl": preview}

    tracks = [
        track("Feature", artist="Someone & Toby Fox"),
        track("Video", kind="music-video"),
        track("No preview", preview=None),
        track("Megalovania"),
        track("MEGALOVANIA"),
    ]
    items = samples.own_items(tracks, "Toby Fox", "song")
    assert items == [{"name": "Megalovania", "art": "", "preview": "p.m4a", "url": ""}]


def test_artwork_is_resized_only_when_apple_named_a_size() -> None:
    assert samples.artwork("https://a/x.jpg/100x100bb.png", "1x1") == "https://a/x.jpg/1x1bb.png"
    assert samples.artwork("https://a/plain.jpg", "1x1") == "https://a/plain.jpg"
    assert samples.artwork("", "1x1") == ""
