from conftest import artist, at

from live_shows.matching import ArtistIndex, _merge, lineup_lookup, match_shows, venue_words
from live_shows.model import Event, Performer, Tier
from live_shows.sources.lineups import LineupAct, LineupPage

LA_DISPUTE = artist("La Dispute", mbid="mb-ld", plays=279)
FALL_OF_TROY = artist("The Fall of Troy", Tier.MAYBE, plays=7)
DGD = artist("Dance Gavin Dance", plays=1465)
NECK_DEEP = artist("Neck Deep", plays=255)
SPIRITBOX = artist("Spiritbox", Tier.MAYBE, plays=28)
INDEX = ArtistIndex({a.key: a for a in [LA_DISPUTE, FALL_OF_TROY, DGD, NECK_DEEP, SPIRITBOX]})


def event(source: str, when: str, venue: str, *names: str, **fields) -> Event:
    return Event(
        source=source,
        event_id=f"{source}:{when}:{venue}",
        title=fields.pop("title", names[0] if names else ""),
        starts_at=at(when),
        venue=venue,
        locality="Melbourne",
        url=f"https://{source.lower()}.example/{len(names)}",
        performers=[Performer(n) for n in names],
        **fields,
    )


def test_one_night_from_several_sellers_is_one_show() -> None:
    events = [
        event("Bandsintown", "2026-10-20 19:00", "170 Russell", "La Dispute", "Special Features"),
        event("Moshtix", "2026-10-20 19:30", "170 RUSSELL, MELBOURNE", "La Dispute"),
        event("musick", "2026-10-20 19:00", "170 Russell", "La Dispute"),
    ]
    [show] = match_shows(events, INDEX)

    assert show.sources == ["Moshtix", "musick", "Bandsintown"]
    assert show.url == "https://moshtix.example/1"
    assert show.time == "19:30"
    assert show.venue == "170 Russell"
    assert show.with_them == ["Special Features"]
    assert show.tier is Tier.LOVE
    assert show.pairs == [("2026-10-20", "la dispute")]


def test_a_shared_artist_joins_listings_whose_venue_names_differ() -> None:
    events = [
        event("Bandsintown", "2026-11-08 19:00", "Forum Melbourne", "Dance Gavin Dance"),
        event("Oztix", "2026-11-08 19:00", "The Forum", "Dance Gavin Dance", "The Fall of Troy"),
    ]
    [show] = match_shows(events, INDEX)
    assert [act.artist.name for act in show.acts] == ["Dance Gavin Dance", "The Fall of Troy"]
    assert show.with_them == []


def test_a_maybe_only_bill_is_a_maybe_show_and_unrelated_nights_stay_apart() -> None:
    events = [
        event("Oztix", "2026-11-01 19:00", "Corner Hotel", "Spiritbox"),
        event("Oztix", "2026-11-01 20:00", "Northcote Theatre", "Someone Else"),
        event("Oztix", "2026-11-02 19:00", "Corner Hotel", "La Dispute"),
    ]
    shows = match_shows(events, INDEX)
    assert [(s.day.isoformat(), s.tier) for s in shows] == [
        ("2026-11-01", Tier.MAYBE),
        ("2026-11-02", Tier.LOVE),
    ]


def test_a_performer_is_found_by_mbid_before_name() -> None:
    renamed = Event(
        source="Bandsintown", event_id="b1", title="", starts_at=at("2026-10-20 19:00"),
        venue="170 Russell", locality="", url="u", performers=[Performer("LA DISPUTE (US)", "mb-ld")],
    )  # fmt: skip
    [show] = match_shows([renamed], INDEX)
    assert show.acts[0].artist is LA_DISPUTE
    assert show.title == "170 Russell"


def test_a_festival_takes_its_acts_from_its_lineup_page() -> None:
    events = [
        event("Bandsintown", "2026-12-04 11:00", "Flemington Racecourse", "Spiritbox",
              title="Good Things Festival 2026", is_festival=True),
        event("Oztix", "2026-12-04 11:00", "Flemington Racecourse", title="Good Things Festival",
              is_festival=True),
    ]  # fmt: skip
    lineup = [LineupAct("NECK DEEP", True), LineupAct("SPIRITBOX", True), LineupAct("DGD", False)]

    [festival] = match_shows(events, INDEX, lambda title: lineup if "Good" in title else None)

    assert festival.is_festival
    assert festival.title == "Good Things Festival"
    assert [a.artist.name for a in festival.acts] == ["Neck Deep", "Spiritbox"]
    assert festival.tier is Tier.LOVE
    assert festival.with_them == []


def test_a_festival_without_a_page_keeps_the_sources_acts_and_one_with_none_is_dropped() -> None:
    events = [
        event("Bandsintown", "2027-01-10 12:00", "Park", "Spiritbox",
              title="Small Fest", is_festival=True),
        event("Oztix", "2027-01-11 12:00", "Field", title="Empty Fest", is_festival=True),
    ]  # fmt: skip
    [festival] = match_shows(events, INDEX)
    assert festival.title == "Small Fest"
    assert festival.tier is Tier.MAYBE


def test_lineup_lookup_reads_each_page_once_and_knows_only_its_festivals() -> None:
    pages = [LineupPage("Good Things Festival", "good things", "https://gt/lineup")]
    reads: list[str] = []

    def read(page: LineupPage) -> list[LineupAct] | None:
        reads.append(page.url)
        return [LineupAct("NECK DEEP", True)]

    lookup = lineup_lookup(pages, read)
    assert lookup("Good Things Festival 2026") == [LineupAct("NECK DEEP", True)]
    assert lookup("GOOD THINGS - Melbourne") == [LineupAct("NECK DEEP", True)]
    assert lookup("Laneway") is None
    assert reads == ["https://gt/lineup"]


def test_on_sale_time_and_upper_case_venues() -> None:
    events = [
        event("Ticketmaster", "2027-03-01 19:30", "MARGARET COURT ARENA", "Spiritbox",
              on_sale_at=at("2026-10-24 10:00")),
    ]  # fmt: skip
    [show] = match_shows(events, INDEX)
    assert show.on_sale_at == at("2026-10-24 10:00")
    assert show.venue == "Margaret Court Arena"


def test_venue_words_ignore_filler_and_keep_a_name_made_only_of_it() -> None:
    assert venue_words("The Forum Theatre, Melbourne") == {"forum", "melbourne"}
    assert venue_words("The Forum Theatre, Melbourne", {"melbourne", "vic"}) == {"forum"}
    assert venue_words("The Hotel") == {"hotel"}


def test_one_shared_venue_word_does_not_join_two_venues() -> None:
    events = [
        event("Bandsintown", "2026-11-26 19:00", "John Cain Arena", "Dance Gavin Dance"),
        event("Oztix", "2026-11-26 19:00", "John Curtin Hotel", "Aus Music T-Shirt Day"),
        event("Moshtix", "2026-11-26 19:00", "Sidney Myer Music Bowl", "La Dispute"),
        event("Oztix", "2026-11-26 20:00", "Sound Music Bar", "Divine Divas"),
    ]
    shows = match_shows(events, INDEX)
    assert sorted((s.venue, s.url) for s in shows) == [
        ("John Cain Arena", "https://bandsintown.example/1"),
        ("Sidney Myer Music Bowl", "https://moshtix.example/1"),
    ]


def test_the_link_comes_from_a_listing_that_names_her_act() -> None:
    events = [
        event("Bandsintown", "2026-10-20 19:00", "170 Russell", "La Dispute"),
        event("Moshtix", "2026-10-20 19:00", "170 Russell", "Someone Else", title="Other gig"),
    ]
    [show] = match_shows(events, INDEX)
    assert show.url == "https://bandsintown.example/1"
    assert show.sources == ["Moshtix", "Bandsintown"]


def test_pinned_acts_lead_the_bill() -> None:
    pinned = artist("Spiritbox", pinned=True, albums_front_to_back=2, plays=28)
    index = ArtistIndex({a.key: a for a in [DGD, pinned]})
    events = [event("Moshtix", "2026-10-20 19:00", "170 Russell", "Dance Gavin Dance", "Spiritbox")]
    [show] = match_shows(events, index)
    assert [act.artist.name for act in show.acts] == ["Spiritbox", "Dance Gavin Dance"]
    assert show.pinned


def test_the_home_city_and_state_and_each_locality_are_venue_filler() -> None:
    def listing(source: str, venue: str, locality: str = "") -> Event:
        return Event(source=source, event_id=source, title=venue, starts_at=at("2026-10-20 19:00"),
                     venue=venue, locality=locality, url="u", performers=[])  # fmt: skip

    corner = [
        listing("Moshtix", "Corner Hotel, Richmond VIC"),
        listing("Oztix", "Corner Melbourne"),
    ]
    assert len(_merge(corner, INDEX, False, ())) == 2
    assert len(_merge(corner, INDEX, False, {"melbourne", "vic"})) == 1
    by_locality = [listing("Moshtix", "Corner Richmond", "Richmond"), corner[1]]
    assert len(_merge(by_locality, INDEX, False, {"melbourne"})) == 1
