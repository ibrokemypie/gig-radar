from collections import Counter

from live_shows.albums import (
    GAP_SECONDS,
    AlbumRun,
    album_of,
    albums_worth_reading,
    deliberate,
    front_to_back,
    whole_album_runs,
)

MINUTE = 60


def album(name: str, length: int) -> list[str]:
    """An album's track ids in order: 'name-1' to 'name-<length>'."""
    return [f"{name}-{n}" for n in range(1, length + 1)]


def played(
    name: str, numbers: list[int], start: int = 0, every: int = 4 * MINUTE, artist: str = ""
) -> list[dict]:
    """History rows playing album `name`'s tracks `numbers`, one each `every` seconds."""
    return [
        {
            "grandparentTitle": artist or name.upper(),
            "parentKey": f"/library/metadata/{name}",
            "ratingKey": f"{name}-{n}",
            "viewedAt": start + i * every,
        }
        for i, n in enumerate(numbers)
    ]


ALBUMS = {"a": album("a", 10), "b": album("b", 5), "c": album("c", 5), "short": album("short", 3)}


def runs_of(plays: list[dict]) -> list[tuple[str, int]]:
    return [(run.album, run.first_position) for run in whole_album_runs(plays, ALBUMS)]


def test_album_of_reads_the_parent_key() -> None:
    assert album_of({"parentKey": "/library/metadata/123"}) == "123"
    assert album_of({}) is None


def test_only_stretches_of_four_different_tracks_are_worth_reading() -> None:
    four = played("a", [1, 2, 3, 4])
    three = played("b", [1, 2, 2, 3], start=10_000)
    gapped = played("c", [1, 2], start=20_000) + played("c", [3, 4], start=20_000 + 2 * GAP_SECONDS)
    split = played("short", [1, 2], start=40_000) + played("x", [1], start=40_500)
    split += played("short", [3], start=41_000)
    no_album = [{"ratingKey": f"n{n}", "viewedAt": 50_000 + n} for n in range(5)]
    assert albums_worth_reading(four + three + gapped + split + no_album) == {"a"}


def test_a_whole_album_in_order_is_a_run() -> None:
    [run] = whole_album_runs(played("b", [1, 2, 3, 4, 5], start=100), ALBUMS)
    assert run == AlbumRun("b", "b", 1, 0, 4, 100, 100 + 4 * 4 * MINUTE)


def test_eighty_percent_of_the_album_counts_and_less_does_not() -> None:
    assert runs_of(played("a", [1, 2, 3, 4, 5, 6, 7, 8])) == [("a", 1)]
    assert runs_of(played("a", [1, 2, 3, 4, 5, 6, 7])) == []
    assert runs_of(played("b", [2, 3, 4, 5])) == [("b", 2)]
    assert runs_of(played("b", [1, 2, 3])) == []


def test_skipping_ahead_is_fine_but_going_back_starts_again() -> None:
    assert runs_of(played("a", [1, 2, 4, 5, 6, 7, 8, 9, 10])) == [("a", 1)]
    assert runs_of(played("b", [1, 2, 3, 1, 2])) == []


def test_a_repeated_track_is_ignored_and_keeps_the_run_alive() -> None:
    assert runs_of(played("b", [1, 2, 2, 2, 3, 4, 5])) == [("b", 1)]
    # The repeats keep each gap under 30 minutes; without them the gap is 40 minutes.
    repeats = played("b", [1, 2, 2, 2, 3, 4, 5], every=20 * MINUTE)
    assert runs_of(repeats) == [("b", 1)]
    assert runs_of(played("b", [1, 2, 3, 4, 5], every=40 * MINUTE)) == []
    [run] = whole_album_runs(played("b", [1, 2, 3, 4, 5, 5], start=0), ALBUMS)
    assert (run.last_play, run.last_played_at) == (5, 5 * 4 * MINUTE)


def test_a_gap_of_exactly_thirty_minutes_continues_and_longer_breaks() -> None:
    assert runs_of(played("b", [1, 2, 3, 4, 5], every=GAP_SECONDS)) == [("b", 1)]
    late = played("b", [1, 2], every=MINUTE) + played("b", [3, 4, 5], start=GAP_SECONDS + 61)
    assert runs_of(late) == []


def test_another_albums_track_between_breaks_the_run() -> None:
    plays = played("b", [1, 2]) + played("c", [1], start=500) + played("b", [3, 4, 5], start=600)
    assert runs_of(plays) == []


def test_a_track_not_on_any_album_read_breaks_the_run() -> None:
    gone = [{"grandparentTitle": "B", "ratingKey": "gone", "viewedAt": 500}]
    plays = played("b", [1, 2]) + gone + played("b", [3, 4, 5], start=600)
    assert runs_of(plays) == []


def test_albums_under_four_tracks_never_count() -> None:
    assert runs_of(played("short", [1, 2, 3])) == []


def test_a_run_closed_by_the_next_album_still_counts() -> None:
    plays = played("b", [1, 2, 3, 4, 5]) + played("c", [1, 2, 3, 4, 5], start=1_500)
    assert runs_of(plays) == [("b", 1), ("c", 1)]


def kept(*histories: list[dict]) -> list[tuple[str, int]]:
    """The (album, first position) of each deliberate run in the histories played in turn."""
    plays = [play for history in histories for play in history]
    return [(run.album, run.first_position) for run in deliberate(whole_album_runs(plays, ALBUMS))]


def test_an_album_another_artists_album_ran_straight_into_is_chained() -> None:
    before = played("c", [1, 2, 3, 4, 5], start=0)
    assert kept(before, played("b", [2, 3, 4, 5], start=960 + GAP_SECONDS)) == [("c", 1)]
    assert kept(before, played("b", [1, 2, 3, 4, 5], start=960 + GAP_SECONDS)) == []


def test_an_album_running_on_into_another_artists_first_track_is_chained() -> None:
    mine = played("b", [1, 2, 3, 4, 5], start=0)
    assert kept(mine, played("c", [2, 3, 4, 5], start=1_200)) == [("b", 1)]


def test_a_gap_over_thirty_minutes_or_a_play_between_breaks_the_chain() -> None:
    mine = played("b", [1, 2, 3, 4, 5], start=0)
    late = played("c", [1, 2, 3, 4, 5], start=960 + GAP_SECONDS + 1)
    assert kept(mine, late) == [("b", 1), ("c", 1)]
    stray = played("x", [1], start=1_000)
    assert kept(mine, stray, played("c", [1, 2, 3, 4, 5], start=1_200)) == [("b", 1), ("c", 1)]
    not_whole = played("c", [1, 2], start=1_000)
    assert kept(mine, not_whole) == [("b", 1)]


def test_the_same_artists_albums_back_to_back_are_not_chained() -> None:
    first = played("b", [1, 2, 3, 4, 5], artist="Me")
    second = played("c", [1, 2, 3, 4, 5], start=1_000, artist="Me")
    assert kept(first, second) == [("b", 1), ("c", 1)]


def test_front_to_back_counts_deliberate_listens_per_album() -> None:
    plays = played("b", [1, 2, 3, 4, 5], artist="Me")
    plays += played("b", [1, 2, 3, 4, 5], start=10_000, artist="Me")
    plays += played("c", [1, 2, 3, 4, 5], start=20_000, artist="Me")
    plays += played("a", list(range(1, 11)), start=30_000, artist="Radio")
    plays += played("c", [1, 2, 3, 4, 5], start=30_000 + 40 * MINUTE + 60, artist="Me")
    assert front_to_back(plays, ALBUMS) == {"me": Counter({"b": 2, "c": 1})}
