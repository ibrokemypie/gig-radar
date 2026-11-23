import json

import pytest
from conftest import FakeWeb, fixture

from live_shows.library import (
    AlbumOrderFetcher,
    Listening,
    _mbids_by_artist,
    add_similar,
    build_tiers,
    fewer_like,
    played_tiers,
    plex_music_section,
    read_plex,
    similar_artists,
)
from live_shows.model import Tier


def plays(artist: str, track_counts: dict[str, int]) -> list[dict]:
    """Plex history rows: each track played its count of times."""
    return [
        {"grandparentTitle": artist, "ratingKey": track, "title": track}
        for track, count in track_counts.items()
        for _ in range(count)
    ]


def hearts(artist: str, count: int) -> list[dict]:
    return [{"grandparentTitle": artist, "ratingKey": f"{artist}-h{i}"} for i in range(count)]


def example_listening() -> tuple[Listening, dict]:
    """Invented artists: plays, hearts, MBIDs and ListenBrainz answers for each tier."""
    data = json.loads(fixture("library-examples.json"))
    listening = Listening(data["plays"], data["hearted"], _mbids_by_artist(data["artists"]))
    return listening, data["similar"]


def test_example_listening_lands_in_the_expected_tiers() -> None:
    listening, similar = example_listening()
    tiers = played_tiers(listening)
    add_similar(tiers, similar)

    loved = ["velvet harbour", "moth signal", "glass orchard", "paper lanterns"]
    maybe = ["ember coast", "northbound static", "quiet atlas", "copper saints"]
    assert {key: tiers[key].tier for key in loved} == dict.fromkeys(loved, Tier.LOVE)
    assert {key: tiers[key].tier for key in maybe} == dict.fromkeys(maybe, Tier.MAYBE)
    assert tiers["paper lanterns"].pinned
    assert tiers["moth signal"].plays == 64
    assert tiers["copper saints"].plays == 0
    assert tiers["copper saints"].similar_to == ["Velvet Harbour", "Moth Signal", "Glass Orchard"]
    assert tiers["northbound static"].similar_to == ["Velvet Harbour"]
    assert {"lone pine choir", "various artists", "faint relay", "never found"}.isdisjoint(tiers)


def test_five_songs_on_repeat_is_loved_and_four_is_not() -> None:
    five = plays("A", {f"t{i}": 3 for i in range(5)})
    four = plays("B", {f"t{i}": 3 for i in range(4)})
    tiers = played_tiers(Listening(five + four))
    assert tiers["a"].tier is Tier.LOVE
    assert tiers["a"].songs_on_repeat == 5
    assert tiers["b"].tier is Tier.MAYBE


def test_three_hearts_is_loved_and_one_play_is_nothing() -> None:
    listening = Listening(plays("Once", {"t": 1}), [*hearts("Hearted", 3), {"title": "no artist"}])
    tiers = played_tiers(listening)
    assert tiers["hearted"].tier is Tier.LOVE
    assert tiers["hearted"].why == "hearted 3 tracks · 0 plays"
    assert "once" not in tiers


def test_library_names_are_cleaned_and_compilations_skipped() -> None:
    rows = plays("[Linkin Park]", {"t": 2}) + plays("Various Artists", {"t": 9})
    rows += [{"title": "no artist"}, {"grandparentTitle": "???", "ratingKey": "x"}]
    rows += [{"grandparentTitle": "Gone", "title": "Deleted Song"}] * 2
    tiers = played_tiers(Listening(rows))
    assert set(tiers) == {"linkin park", "gone"}
    assert tiers["linkin park"].name == "Linkin Park"


def test_similar_needs_three_loved_artists_and_never_demotes_or_counts_others() -> None:
    tiers = played_tiers(
        Listening(
            plays("Played", {"t": 2}),
            hearts("L1", 3) + hearts("L2", 3) + hearts("L3", 3),
            artist_mbids={"l1": "m1", "l2": "m2", "l3": "m3"},
        )
    )
    neighbour = {"name": "New Band", "artist_mbid": "mn"}
    similar = {
        "m1": [neighbour, {"name": "L2"}, {"name": "Played"}, {"name": "Two Only"}],
        "m2": [neighbour, neighbour, {"name": "Two Only"}],
        "m3": [neighbour],
        "not-loved": [{"name": "Two Only"}],
    }
    add_similar(tiers, similar)

    assert tiers["new band"].tier is Tier.MAYBE
    assert tiers["new band"].mbid == "mn"
    assert tiers["new band"].similar_to == ["L1", "L2", "L3"]
    assert tiers["new band"].why == "similar to L1 and L2"
    assert "two only" not in tiers
    assert tiers["l2"].tier is Tier.LOVE
    assert tiers["played"].similar_to == ["L1"]
    assert tiers["played"].why == "2 plays"


def test_read_plex_asks_for_the_account_and_section_and_reads_mbids() -> None:
    web = FakeWeb({
        "history": json.dumps({"MediaContainer": {"Metadata": plays("A", {"t": 1})}}),
        "type=10": json.dumps({"MediaContainer": {}}),
        "type=8": json.dumps({"MediaContainer": {"Metadata": [
            {"title": "A", "Guid": [{"id": "plex://x"}, {"id": "mbid://m-a"}]},
            {"title": "B"},
        ]}}),
    })  # fmt: skip
    listening = read_plex(web, "http://plex:32400", "TOKEN", "7", "4")

    assert len(listening.plays) == 1
    assert listening.hearted == []
    assert listening.artist_mbids == {"a": "m-a"}
    history_url = web.requests[0][0]
    assert "accountID=7" in history_url
    assert "librarySectionID=4" in history_url
    assert "/library/sections/4/all" in web.requests[1][0]
    assert "X-Plex-Token=TOKEN" in history_url


def test_similar_artists_reads_a_list_and_treats_anything_else_as_none() -> None:
    web = FakeWeb({"m-list": json.dumps([{"name": "X"}]), "m-dict": json.dumps({"error": 1})})
    assert similar_artists(web, "m-list") == [{"name": "X"}]
    assert similar_artists(web, "m-dict") == []
    assert "algorithm=session_based" in web.requests[0][0]


def test_build_tiers_looks_up_loved_artists_and_survives_a_failed_lookup() -> None:
    listening = Listening(
        plays("Maybe", {"t": 2}),
        hearts("L1", 3) + hearts("L2", 3) + hearts("L3", 3) + hearts("NoMbid", 3),
        artist_mbids={"l1": "m1", "l2": "m2", "l3": "m3", "maybe": "mm"},
    )
    neighbours = json.dumps([{"name": "New Band", "artist_mbid": "mn"}])
    web = FakeWeb({"m1": neighbours, "m2": neighbours, "m3": OSError("down")})

    tiers = build_tiers(listening, web)

    assert len(web.requests) == 3
    assert "new band" not in tiers  # only two loved artists answered


def trims(seeds: list[str], count: int, at: str = "2026-11-03T10:00:00+11:00") -> dict:
    """Choices records: `count` artists trimmed, each found through `seeds`."""
    return {
        f"{'-'.join(seeds)}-{i}": {"name": f"Band {i}", "choice": "trimmed", "seeds": seeds,
                                   "at": at}
        for i in range(count)
    }  # fmt: skip


def test_three_trims_and_no_keeps_through_a_loved_artist_stop_it() -> None:
    choices = {
        **trims(["Stopped"], 3),
        **trims(["Two Only"], 2),
        **trims(["Kept One"], 3),
        "kept": {"name": "Kept", "choice": "kept", "seeds": ["Kept One"], "at": "2026-11-03"},
    }
    assert fewer_like(choices, {}) == {"Stopped": 3}


def test_trims_before_an_undo_no_longer_count() -> None:
    choices = {
        **trims(["Undone"], 3, at="2026-11-01T10:00:00+11:00"),
        **trims(["Undone", "Also"], 1, at="2026-11-03T10:00:00+11:00"),
    }
    assert fewer_like(choices, {"Undone": "2026-11-02T10:00:00+11:00"}) == {}
    assert fewer_like(choices, {}) == {"Undone": 4}


def three_loved() -> dict:
    return played_tiers(
        Listening(
            [],
            hearts("L1", 3) + hearts("L2", 3) + hearts("L3", 3),
            artist_mbids={"l1": "m1", "l2": "m2", "l3": "m3"},
        )
    )


def test_a_stopped_loved_artist_finds_nobody() -> None:
    tiers = three_loved()
    neighbour = {"name": "New Band"}
    add_similar(tiers, {"m1": [neighbour], "m2": [neighbour], "m3": [neighbour]}, ["L3"])
    assert "new band" not in tiers


def test_a_kept_artist_stays_a_maybe_when_nothing_finds_it() -> None:
    tiers = three_loved()
    kept = {
        "kept band": {"name": "Kept Band", "choice": "kept", "seeds": ["L1"], "at": "2026-11-03"},
        "l2": {"name": "L2", "choice": "kept", "seeds": [], "at": "2026-11-03"},
    }
    add_similar(tiers, {}, kept=kept)

    assert tiers["kept band"].tier is Tier.MAYBE
    assert tiers["kept band"].similar_to == ["L1"]
    assert tiers["kept band"].found_through == ["L1"]
    assert tiers["l2"].tier is Tier.LOVE


def test_build_tiers_passes_stopped_and_kept_artists_on() -> None:
    listening = Listening(
        [], hearts("L1", 3) + hearts("L2", 3) + hearts("L3", 3),
        artist_mbids={"l1": "m1", "l2": "m2", "l3": "m3"},
    )  # fmt: skip
    web = FakeWeb({"listenbrainz": json.dumps([{"name": "New Band"}])})
    kept = {"kept band": {"name": "Kept Band", "choice": "kept", "seeds": ["L1"], "at": "x"}}

    tiers = build_tiers(listening, web, ["L1"], kept)

    assert "new band" not in tiers
    assert tiers["kept band"].tier is Tier.MAYBE


def test_a_trim_after_an_undo_counts_across_a_daylight_saving_change() -> None:
    def trim(at: str) -> dict:
        return {"name": "x", "choice": "trimmed", "seeds": ["Seed"], "at": at}

    # 02:30+11:00 is 15:30 UTC the day before, after an undo at 02:10+10:00 (16:10 UTC).
    choices = {"a": trim("2026-04-05T02:30:00+11:00"), "b": trim("2026-04-05T03:00:00+10:00"),
               "c": trim("2026-04-05T04:00:00+10:00")}  # fmt: skip
    unlearned = {"Seed": "2026-04-05T02:10:00+10:00"}
    assert fewer_like(choices, unlearned) == {}
    choices["a"] = trim("2026-04-05T02:20:00+10:00")
    assert fewer_like(choices, unlearned) == {"Seed": 3}


def album_plays(artist: str, album: str, length: int, start: int) -> list[dict]:
    """History rows playing every track of an album in order, four minutes apart."""
    return [
        {"grandparentTitle": artist, "parentKey": f"/library/metadata/{album}",
         "ratingKey": f"{album}-{n}", "title": f"{album}-{n}", "viewedAt": start + 240 * n}
        for n in range(1, length + 1)
    ]  # fmt: skip


def album_order(*albums: str, length: int = 5) -> dict[str, list[str]]:
    return {album: [f"{album}-{n}" for n in range(1, length + 1)] for album in albums}


DAY = 86_400


def test_two_albums_or_one_album_twice_front_to_back_pins_an_artist() -> None:
    plays = album_plays("Two Albums", "a1", 5, 0) + album_plays("Two Albums", "a2", 5, DAY)
    plays += album_plays("Twice", "b1", 5, 2 * DAY) + album_plays("Twice", "b1", 5, 3 * DAY)
    plays += album_plays("Once", "c1", 5, 4 * DAY)
    tiers = played_tiers(Listening(plays, album_tracks=album_order("a1", "a2", "b1", "c1")))

    assert tiers["two albums"].pinned and tiers["two albums"].tier is Tier.LOVE
    assert tiers["two albums"].why == "2 albums front to back · 10 plays"
    assert tiers["twice"].pinned
    assert tiers["twice"].why == "an album front to back 2 times · 10 plays"
    assert not tiers["once"].pinned
    assert tiers["once"].tier is Tier.MAYBE


def test_three_hearts_pins_an_artist_and_two_do_not() -> None:
    listening = Listening(plays("Two", {"t": 2}), [*hearts("Three", 3), *hearts("Two", 2)])
    tiers = played_tiers(listening)
    assert tiers["three"].pinned
    assert tiers["three"].why == "hearted 3 tracks · 0 plays"
    assert not tiers["two"].pinned


def test_loved_by_songs_on_repeat_alone_is_not_pinned() -> None:
    tiers = played_tiers(Listening(plays("A", {f"t{i}": 3 for i in range(5)})))
    assert tiers["a"].tier is Tier.LOVE
    assert not tiers["a"].pinned


def test_a_various_artists_album_pins_nobody() -> None:
    plays = album_plays("Various Artists", "v", 5, 0) + album_plays("Various Artists", "v", 5, DAY)
    assert played_tiers(Listening(plays, album_tracks=album_order("v"))) == {}


def test_chained_albums_do_not_pin() -> None:
    radio = album_plays("Mine", "m1", 5, 0) + album_plays("Theirs", "t1", 5, 1_500)
    radio += album_plays("Mine", "m2", 5, 3_000)
    tiers = played_tiers(Listening(radio, album_tracks=album_order("m1", "m2", "t1")))
    assert not tiers["mine"].pinned


def test_read_plex_pages_the_history_and_reads_only_albums_worth_reading(monkeypatch) -> None:
    monkeypatch.setattr("live_shows.library.HISTORY_PAGE", 4)
    history = album_plays("A", "full", 5, 0) + album_plays("A", "bits", 2, DAY)

    def history_page(url: str) -> str:
        start = int(url.split("X-Plex-Container-Start=")[1].split("&")[0])
        return json.dumps({"MediaContainer": {"Metadata": history[start : start + 4]}})

    children = [{"ratingKey": "full-2", "index": 1, "parentIndex": 2},
                {"ratingKey": "full-1", "index": 1}, {"ratingKey": "full-0", "index": 0}]  # fmt: skip
    web = FakeWeb({
        "history": history_page,
        "metadata/full/children": json.dumps({"MediaContainer": {"Metadata": children}}),
        "type=10": json.dumps({"MediaContainer": {}}),
        "type=8": json.dumps({"MediaContainer": {}}),
    })  # fmt: skip
    listening = read_plex(web, "http://plex:32400", "TOKEN", "1", "3")

    assert len(listening.plays) == 7
    assert listening.album_tracks == {"full": ["full-0", "full-1", "full-2"]}
    starts = [url.split("Start=")[1][:1] for url, _ in web.requests if "history" in url]
    assert starts == ["0", "4"]


def test_album_order_fetcher_keeps_only_ordered_track_ids() -> None:
    web = FakeWeb({"children": json.dumps({"MediaContainer": {}})})
    assert AlbumOrderFetcher(web).text("http://plex/library/metadata/1/children") == "[]"


def test_the_music_section_is_the_first_artist_library() -> None:
    sections = {"MediaContainer": {"Directory": [{"key": "1", "type": "movie"},
                                                 {"key": 5, "type": "artist"}]}}  # fmt: skip
    web = FakeWeb({"/library/sections": json.dumps(sections)})
    assert plex_music_section(web, "http://plex", "TOKEN") == "5"
    assert "X-Plex-Token=TOKEN" in web.requests[0][0]


def test_a_server_with_no_music_library_says_to_set_one() -> None:
    web = FakeWeb({"/library/sections": json.dumps({"MediaContainer": {}})})
    with pytest.raises(RuntimeError, match="PLEX_MUSIC_SECTION"):
        plex_music_section(web, "http://plex", "TOKEN")


def test_a_counted_play_counts_as_that_many() -> None:
    rows = [{"grandparentTitle": "A", "title": f"t{i}", "count": 3} for i in range(5)]
    tiers = played_tiers(Listening(plays=rows))
    assert tiers["a"].tier is Tier.LOVE
    assert tiers["a"].plays == 15
    assert tiers["a"].songs_on_repeat == 5
