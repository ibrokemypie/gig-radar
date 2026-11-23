"""The shapes the sources, the matcher and the outputs pass between them."""

import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum


def web_url(url: str | None) -> str:
    """A scraped URL fit for a link, an email or a calendar: http or https, else ''.

    A seller's page is not trusted: a javascript: or data: link, or one carrying line
    breaks, must not reach the page, an email or an ICS file.
    """
    parts = urllib.parse.urlsplit((url or "").strip())  # drops tabs and line breaks
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return ""
    return urllib.parse.urlunsplit(parts)


class Tier(StrEnum):
    """How much the listener cares about an artist: loved acts weekly, maybe acts counted weekly.

    Pinned artists, a flag on loved ones (Artist.pinned), are the ones emailed at once.
    """

    LOVE = "love"
    MAYBE = "maybe"


@dataclass(frozen=True)
class Performer:
    """One act as a source lists it, with its MusicBrainz id when the source gives one."""

    name: str
    mbid: str | None = None


@dataclass
class Event:
    """One listing from one source: a night at a venue with the acts it names."""

    source: str
    event_id: str
    title: str
    starts_at: datetime
    venue: str
    locality: str
    url: str
    performers: list[Performer]
    is_festival: bool = False
    on_sale_at: datetime | None = None


@dataclass
class Artist:
    """One of the listener's artists, with the tier the listening rules give it and why."""

    name: str
    key: str
    tier: Tier
    plays: int = 0
    songs_on_repeat: int = 0
    hearts: int = 0
    albums_front_to_back: int = 0
    most_album_listens: int = 0
    pinned: bool = False
    mbid: str | None = None
    similar_to: list[str] = field(default_factory=list)

    @property
    def found_through(self) -> list[str]:
        """The loved artists it was found through, when similarity alone made it a maybe."""
        return [] if self.tier is Tier.LOVE or self.plays else self.similar_to

    @property
    def why(self) -> str:
        """Why it matters, in plain terms: what is hearted and played, or who it is like."""
        if self.pinned:
            return " · ".join([*self._pinned_because, _plural(self.plays, "play")])
        if self.tier is Tier.LOVE:
            return (
                f"{_plural(self.plays, 'play')} · {_plural(self.songs_on_repeat, 'song')} on repeat"
            )
        if self.plays:
            return _plural(self.plays, "play")
        return "similar to " + " and ".join(self.similar_to[:2])

    @property
    def _pinned_because(self) -> list[str]:
        """The albums played through and the tracks hearted, whichever pinned it."""
        reasons = []
        if self.albums_front_to_back >= 2:
            reasons.append(f"{self.albums_front_to_back} albums front to back")
        elif self.most_album_listens >= 2:
            reasons.append(f"an album front to back {self.most_album_listens} times")
        if self.hearts >= 3:
            reasons.append(f"hearted {self.hearts} tracks")
        return reasons


@dataclass
class Act:
    """One of the listener's artists on a show's bill."""

    artist: Artist
    listed_as: str


@dataclass
class Show:
    """One night worth going to: a gig, or a festival day, merged across sources.

    `pairs` are the (day, artist key) pairs that identify it from run to run, since no
    source's event id is shared with another and a venue's spelling differs between them.
    """

    day: date
    time: str
    title: str
    venue: str
    url: str
    acts: list[Act]
    with_them: list[str]
    sources: list[str]
    event_ids: list[str]
    is_festival: bool = False
    on_sale_at: datetime | None = None

    @property
    def tier(self) -> Tier:
        """Love when any loved artist plays it."""
        return Tier.LOVE if any(act.artist.tier is Tier.LOVE for act in self.acts) else Tier.MAYBE

    @property
    def pinned(self) -> bool:
        """Pinned when any pinned artist plays it, a festival's bill included."""
        return any(act.artist.pinned for act in self.acts)

    @property
    def pairs(self) -> list[tuple[str, str]]:
        """The (ISO day, artist key) pairs that name this show across runs."""
        return [(self.day.isoformat(), act.artist.key) for act in self.acts]

    @property
    def show_id(self) -> str:
        """An id for links and buttons: the day and the alphabetically first of its acts."""
        first_key = min(act.artist.key for act in self.acts)
        return f"{self.day.isoformat()}~{first_key.replace(' ', '-')}"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"
