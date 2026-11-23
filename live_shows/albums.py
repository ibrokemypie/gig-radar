"""Albums put on and played front to back, from a Plex play history.

An artist is pinned when 2 of their albums were listened through front to back, or one
album twice, counting only albums put on deliberately, never ones chained on after another
artist's album (docs/design.md).

What "front to back" means: a run of one album's tracks in album
order (later tracks only, a repeat of the same track ignored), no other album's track
between and no gap over 30 minutes, covering at least 80% of an album of 4 or more tracks.
A run is chained when the play just before it ends another artist's whole-album run, or
the play just after it starts one at its first track, within 30 minutes either way: that
is how Plexamp's album radio moves from one album to the next.
"""

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from live_shows.names import clean_library_name, normalise

GAP_SECONDS = 30 * 60
WHOLE_SHARE = 0.8
MIN_ALBUM_TRACKS = 4


@dataclass(frozen=True)
class AlbumRun:
    """One album played through in order: who made it, and its first and last plays.

    `first_play` and `last_play` index the history in time order; the last play may be a
    repeat of the run's last track.
    """

    album: str
    artist: str
    first_position: int
    first_play: int
    last_play: int
    started_at: int
    last_played_at: int


def album_of(play: dict) -> str | None:
    """The id of the album a history row's track is on, from its parent key."""
    parent = play.get("parentKey") or ""
    return parent.rsplit("/", 1)[-1] or None


def artist_of(item: dict) -> str:
    """The album artist key of a history row, '' when it names none."""
    return normalise(clean_library_name(item.get("grandparentTitle") or ""))


def in_time_order(plays: Iterable[dict]) -> list[dict]:
    """History rows oldest first."""
    return sorted(plays, key=lambda play: play.get("viewedAt") or 0)


def albums_worth_reading(plays: list[dict]) -> set[str]:
    """The albums some run of plays could have played through, so only their tracks are read.

    A stretch of consecutive plays of one album with no gap over 30 minutes needs at least
    4 different tracks to cover 80% of any album long enough to count.
    """
    worth: set[str] = set()
    album, tracks, last_at = None, set(), 0
    for play in in_time_order(plays):
        this_album, at = album_of(play), play.get("viewedAt") or 0
        if this_album != album or at - last_at > GAP_SECONDS:
            album, tracks = this_album, set()
        tracks.add(play.get("ratingKey"))
        last_at = at
        if album and len(tracks) >= MIN_ALBUM_TRACKS:
            worth.add(album)
    return worth


def whole_album_runs(plays: list[dict], album_tracks: Mapping[str, list[str]]) -> list[AlbumRun]:
    """Every run that played at least 80% of an album of 4 or more tracks in order.

    `album_tracks` maps an album to its track ids in disc and track order; a play of a track
    not listed there (an album not read, or a track since removed) ends any run.
    """
    position = {
        track: (album, number)
        for album, tracks in album_tracks.items()
        for number, track in enumerate(tracks, 1)
    }
    runs: list[AlbumRun] = []
    run: _Run | None = None
    for index, play in enumerate(in_time_order(plays)):
        at = play.get("viewedAt") or 0
        album, number = position.get(play.get("ratingKey") or "", (None, 0))
        if run and run.continues(album, number, at):
            run.add(number, index, at)
            continue
        if run and run.is_whole(len(album_tracks[run.album])):
            runs.append(run.finished())
        run = _Run(album, artist_of(play), number, index, at) if album else None
    if run and run.is_whole(len(album_tracks[run.album])):
        runs.append(run.finished())
    return runs


def deliberate(runs: list[AlbumRun]) -> list[AlbumRun]:
    """The runs put on deliberately: none another artist's album ran straight into or out of."""
    ending_at = {run.last_play: run for run in runs}
    starting_at = {run.first_play: run for run in runs}

    def chained(run: AlbumRun) -> bool:
        before = ending_at.get(run.first_play - 1)
        after = starting_at.get(run.last_play + 1)
        return bool(
            before
            and before.artist != run.artist
            and run.started_at - before.last_played_at <= GAP_SECONDS
        ) or bool(
            after
            and after.artist != run.artist
            and after.first_position == 1
            and after.started_at - run.last_played_at <= GAP_SECONDS
        )

    return [run for run in runs if not chained(run)]


def front_to_back(
    plays: list[dict], album_tracks: Mapping[str, list[str]]
) -> dict[str, Counter[str]]:
    """Each album artist's deliberate front-to-back listens, counted per album."""
    listens: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for run in deliberate(whole_album_runs(plays, album_tracks)):
        listens[run.artist][run.album] += 1
    return dict(listens)


class _Run:
    """A run being followed through the history."""

    def __init__(self, album: str, artist: str, number: int, index: int, at: int) -> None:
        self.album = album
        self.artist = artist
        self.numbers = [number]
        self.first_play = self.last_play = index
        self.started_at = self.last_at = at

    def continues(self, album: str | None, number: int, at: int) -> bool:
        """Whether a play of `album`'s track `number` at `at` carries this run on."""
        return (
            album == self.album and at - self.last_at <= GAP_SECONDS and number >= self.numbers[-1]
        )

    def add(self, number: int, index: int, at: int) -> None:
        """Take a play `continues` accepted: a later track extends the run, a repeat does not."""
        self.last_play, self.last_at = index, at
        if number > self.numbers[-1]:
            self.numbers.append(number)

    def is_whole(self, album_length: int) -> bool:
        """Whether the run covered enough of a long enough album."""
        return album_length >= MIN_ALBUM_TRACKS and len(self.numbers) >= math.ceil(
            WHOLE_SHARE * album_length
        )

    def finished(self) -> AlbumRun:
        return AlbumRun(
            self.album,
            self.artist,
            self.numbers[0],
            self.first_play,
            self.last_play,
            self.started_at,
            self.last_at,
        )
