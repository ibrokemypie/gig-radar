"""Listings matched to the listener's artists, merged into one Show per night or festival day.

A performer matches an artist by MusicBrainz id first, else by the normalised name; a name
that only resembles one never matches. Two listings are the same night when they share
a day and either a venue word or one of the listener's artists, since each seller spells
venues its own way.
"""

from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import date

from live_shows.model import Act, Artist, Event, Show, Tier
from live_shows.names import normalise
from live_shows.sources.lineups import LineupAct, LineupPage

# Where a show's ticket link and start time come from, first choice first: the sellers
# list the real door time, Bandsintown often only the date.
SELLER_ORDER = ["Moshtix", "Oztix", "Ticketmaster", "musick", "Bandsintown"]
# Words in many venues' names. The home city and state, and each listing's own locality,
# are filler too: "Forum Melbourne" and "The Forum" are one venue.
_VENUE_FILLER = {"hotel", "theatre", "the", "arena", "club", "festival", "st", "live"}


class ArtistIndex:
    """The listener's artists, found by MBID or by name."""

    def __init__(self, tiers: dict[str, Artist]) -> None:
        self.by_key = tiers
        self.by_mbid = {artist.mbid: artist for artist in tiers.values() if artist.mbid}

    def find(self, name: str, mbid: str | None = None) -> Artist | None:
        """The artist a performer is, or None when it is none of them."""
        if mbid and mbid in self.by_mbid:
            return self.by_mbid[mbid]
        return self.by_key.get(normalise(name))


@dataclass
class _Night:
    """Listings gathered for one show while merging."""

    day: date
    events: list[Event] = field(default_factory=list)
    acts: dict[str, Act] = field(default_factory=dict)
    venues: list[set[str]] = field(default_factory=list)
    titles: set[str] = field(default_factory=set)
    events_with_acts: list[Event] = field(default_factory=list)


def venue_words(venue: str, place_words: Collection[str] = ()) -> set[str]:
    """The words that tell one venue from another, ignoring 'hotel', 'the', the place's own
    words (`place_words`) and the like."""
    filler = _VENUE_FILLER | set(place_words)
    words = {word for word in normalise(venue).split() if word not in filler and len(word) > 2}
    return words or {normalise(venue)}


def same_venue(one: set[str], other: set[str]) -> bool:
    """Whether two venue spellings name one place: one's words all within the other's.

    A single shared word is not enough: "John Cain Arena" and "John Curtin Hotel" differ.
    """
    return one <= other or other <= one


def match_shows(
    events: list[Event],
    artists: ArtistIndex,
    lineup_for: Callable[[str], list[LineupAct] | None] = lambda _title: None,
    place_words: Collection[str] = (),
) -> list[Show]:
    """Every night with at least one of the listener's artists, gigs and festivals, by date.

    `lineup_for` gives a festival's acts from its own lineup page, by the festival
    listing's title, or None where there is no page; a page replaces the acts the sources
    place there. `place_words` are the home city's and state's words, filler in venue names.
    """
    gigs = _merge([e for e in events if not e.is_festival], artists, True, place_words)
    festivals = _merge([e for e in events if e.is_festival], artists, False, place_words)
    shows = [_show(night, is_festival=False) for night in gigs if night.acts]
    for night in festivals:
        lineup = next(
            (acts for event in night.events if (acts := lineup_for(event.title)) is not None),
            None,
        )
        if lineup is not None:
            night.acts = _lineup_acts(lineup, artists)
        if night.acts:
            shows.append(_show(night, is_festival=True))
    return sorted(shows, key=lambda show: (show.day, show.time, show.title))


def lineup_lookup(
    pages: list[LineupPage], read: Callable[[LineupPage], list[LineupAct] | None]
) -> Callable[[str], list[LineupAct] | None]:
    """A `lineup_for` that reads each festival's page once, on its first listing.

    `read` returns None for a page it could not read; that festival keeps the sources' acts.
    """
    read_pages: dict[str, list[LineupAct] | None] = {}

    def lineup_for(title: str) -> list[LineupAct] | None:
        page = next((p for p in pages if p.match in title.lower()), None)
        if page is None:
            return None
        if page.url not in read_pages:
            read_pages[page.url] = read(page)
        return read_pages[page.url]

    return lineup_for


def _merge(
    events: list[Event], artists: ArtistIndex, by_artist: bool, place_words: Collection[str]
) -> list[_Night]:
    """Listings grouped into nights by day and venue, or a shared artist for gigs.

    Festival listings also join on a shared title, since their venues are often vague.
    """
    nights: list[_Night] = []
    for event in sorted(events, key=lambda e: (e.starts_at, SELLER_ORDER.index(e.source))):
        acts = _acts(event, artists)
        words = venue_words(event.venue, {*place_words, *normalise(event.locality).split()})
        title = normalise(event.title)
        day = event.starts_at.date()
        night = next(
            (
                n
                for n in nights
                if n.day == day
                and (
                    any(same_venue(words, venue) for venue in n.venues)
                    or (by_artist and n.acts.keys() & acts.keys())
                    or (not by_artist and title in n.titles)
                )
            ),
            None,
        )
        if night is None:
            night = _Night(day)
            nights.append(night)
        night.events.append(event)
        night.venues.append(words)
        night.titles.add(title)
        if acts:
            night.events_with_acts.append(event)
        for key, act in acts.items():
            night.acts.setdefault(key, act)
    return nights


def _acts(event: Event, artists: ArtistIndex) -> dict[str, Act]:
    """The listener's artists among a listing's performers, by artist key."""
    acts = {}
    for performer in event.performers:
        artist = artists.find(performer.name, performer.mbid)
        if artist:
            acts.setdefault(artist.key, Act(artist, performer.name))
    return acts


def _lineup_acts(lineup: list[LineupAct], artists: ArtistIndex) -> dict[str, Act]:
    """The listener's artists among a lineup page's acts playing the home state."""
    acts = {}
    for line in lineup:
        artist = artists.find(line.name)
        if artist and line.plays_here:
            acts.setdefault(artist.key, Act(artist, line.name))
    return acts


def _billing(act: Act) -> tuple[bool, bool, int]:
    """Sorts pinned acts first, then loved, each most played first."""
    return (not act.artist.pinned, act.artist.tier is not Tier.LOVE, -act.artist.plays)


def _show(night: _Night, is_festival: bool) -> Show:
    """A merged night as a Show: pinned acts, then loved, first; the best seller's link and time.

    The link and time come from a listing that names one of the listener's acts where there is one,
    so a different gig merged in by venue never supplies them.
    """
    events = sorted(night.events, key=lambda e: SELLER_ORDER.index(e.source))
    naming_her = sorted(night.events_with_acts, key=lambda e: SELLER_ORDER.index(e.source))
    seller = (naming_her or events)[0]
    acts = sorted(night.acts.values(), key=_billing)
    her_names = {normalise(act.listed_as) for act in acts} | {act.artist.key for act in acts}
    with_them: dict[str, str] = {}
    if not is_festival:
        for event in events:
            for performer in event.performers:
                key = normalise(performer.name)
                if key and key not in her_names:
                    with_them.setdefault(key, performer.name)
    on_sale = [e.on_sale_at for e in events if e.on_sale_at]
    return Show(
        day=night.day,
        time=seller.starts_at.strftime("%H:%M"),
        title=_title(events, is_festival),
        venue=_venue_name(events),
        url=seller.url,
        acts=acts,
        with_them=list(with_them.values()),
        sources=sorted({e.source for e in events}, key=SELLER_ORDER.index),
        event_ids=sorted({e.event_id for e in events}),
        is_festival=is_festival,
        on_sale_at=max(on_sale) if on_sale else None,
    )


def _title(events: list[Event], is_festival: bool) -> str:
    """A festival's shortest listing title (the plainest name); a gig's seller's title."""
    titles = [e.title for e in events if e.title]
    if not titles:
        return events[0].venue
    return min(titles, key=len) if is_festival else titles[0]


def _venue_name(events: list[Event]) -> str:
    """The venue as a seller spells it, without a trailing ', CITY', in title case."""
    name = events[0].venue.split(",")[0].strip()
    return name.title() if name.isupper() else name
