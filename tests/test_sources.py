import json
from dataclasses import replace
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest
from conftest import MELBOURNE, PLACE, TODAY, UNTIL, artist, at, fixture

from live_shows.sources import (
    SourceEmpty,
    bandsintown,
    in_window,
    json_ld_blocks,
    lineups,
    local_time,
    looks_like_festival,
    moshtix,
    musick,
    oztix,
    ticketmaster,
)

# --- Shared helpers ---------------------------------------------------------------------


def test_window_is_inclusive_and_in_local_time() -> None:
    assert in_window(at("2026-10-20 00:30"), TODAY, UNTIL, MELBOURNE)
    assert not in_window(at("2026-10-19 23:30"), TODAY, UNTIL, MELBOURNE)


def test_local_time_keeps_an_offset_and_assumes_the_local_zone_without_one() -> None:
    assert local_time("2026-10-20T19:00:00", MELBOURNE) == at("2026-10-20 19:00")
    assert local_time("2026-10-20T19:00:00+11:00", MELBOURNE) == at("2026-10-20 19:00")


def test_json_ld_blocks_flattens_lists_and_skips_broken_blocks() -> None:
    page = (
        '<script type="application/ld+json">[{"a": 1}, {"b": 2}]</script>'
        '<script type="application/ld+json">{"c": 3}</script>'
        '<script type="application/ld+json">{broken</script>'
    )
    assert list(json_ld_blocks(page)) == [{"a": 1}, {"b": 2}, {"c": 3}]


def test_festival_hall_is_a_venue_not_a_festival() -> None:
    assert looks_like_festival("Good Things Festival 2026")
    assert not looks_like_festival("Northlane at Festival Hall")


# --- Bandsintown ------------------------------------------------------------------------


def test_bandsintown_keeps_only_the_dates_near_home(web) -> None:
    fake = web({"La%20Dispute": fixture("bandsintown-la-dispute.json")})
    events = bandsintown.fetch(fake, [artist("La Dispute")], PLACE, TODAY, UNTIL)

    assert [(e.venue, e.starts_at) for e in events] == [("170 Russell", at("2026-10-20 19:00"))]
    event = events[0]
    assert event.event_id.startswith("bit:")
    assert [p.name for p in event.performers] == ["La Dispute", "Special Features"]
    assert event.performers[0].mbid
    assert event.performers[1].mbid is None
    assert event.url.startswith("https://www.bandsintown.com/t/")
    assert event.on_sale_at == at("2026-07-16 12:00")
    assert not event.is_festival


def test_bandsintown_skips_unknown_artists_and_failed_lookups(web) -> None:
    fake = web(
        {
            "Nobody": json.dumps({"errorMessage": "[NotFound] The artist was not found"}),
            "Broken": OSError("timed out"),
            "Dance%20Gavin%20Dance": fixture("bandsintown-dance-gavin-dance.json"),
        }
    )
    artists = [artist("Nobody"), artist("Broken"), artist("Dance Gavin Dance")]

    events = bandsintown.fetch(fake, artists, PLACE, TODAY, UNTIL)

    assert [e.venue for e in events] == ["Forum Melbourne"]


def test_bandsintown_fails_loudly_when_no_lookup_answers(web) -> None:
    fake = web({"rest.bandsintown.com": OSError("down")})
    with pytest.raises(SourceEmpty):
        bandsintown.fetch(fake, [artist("La Dispute")], PLACE, TODAY, UNTIL)


def test_bandsintown_event_details_cover_festivals_and_missing_fields() -> None:
    listing = {
        "id": "9",
        "url": "https://www.bandsintown.com/e/9",
        "datetime": "2026-12-04T11:00:00",
        "title": "",
        "festival_start_date": "2026-12-04",
        "venue": {"name": "Flemington Racecourse", "city": "Flemington",
                  "latitude": "-37.79", "longitude": "144.91"},
        "offers": [{"type": "Other", "url": "x"}],
        "on_sale_datetime": "",
    }  # fmt: skip
    event = bandsintown._event(listing, artist("Neck Deep"), PLACE)

    assert event is not None
    assert event.is_festival
    assert event.url == "https://www.bandsintown.com/e/9"
    assert event.on_sale_at is None
    assert [p.name for p in event.performers] == ["Neck Deep"]
    assert bandsintown._event({**listing, "venue": {"name": "Nowhere"}}, artist("X"), PLACE) is None


def test_bandsintown_url_never_splits_a_name_on_its_slash() -> None:
    assert "/artists/AC%252FDC/events" in bandsintown.artist_url("AC/DC")
    assert "/artists/Bad%252F%252FDreems/events" in bandsintown.artist_url("Bad//Dreems")


def test_bandsintown_url_spells_typographic_punctuation_plainly() -> None:
    assert "/artists/Guns%20N%27%20Roses/events" in bandsintown.artist_url("Guns N’ Roses")
    assert "/artists/alt-J/events" in bandsintown.artist_url("alt‐J")
    assert "/artists/Ne-Yo/events" in bandsintown.artist_url("Ne‑Yo")


# --- Oztix ------------------------------------------------------------------------------


def oztix_web(web, search: str):
    return web(
        {
            "www.oztix.com.au/js/app.abc3f3e0.js": fixture("oztix-app.js"),
            "www.oztix.com.au/": fixture("oztix-home.html"),
            "algolia.net": search,
        }
    )


def test_oztix_reads_bands_performances_and_guests_and_drops_cancellations(web) -> None:
    fake = oztix_web(web, fixture("oztix.json"))
    events = oztix.fetch(fake, PLACE, TODAY, UNTIL)

    by_title = {e.title: e for e in events}
    assert (
        "CANCELLED - Gold Chisel vs The Power & The Passion Midnight Oils Tribute." not in by_title
    )
    mudhoney = by_title["MUDHONEY (USA) AUSTRALIAN TOUR 2026"]
    assert [p.name for p in mudhoney.performers] == ["Mudhoney", "Laura", "The Hell Cutz"]
    assert mudhoney.starts_at == at("2026-10-20 18:30")
    assert by_title["Riverboats Music Festival 2027"].is_festival
    search_url, headers = fake.requests[-1]
    assert "Venue.State%3AVIC" in search_url
    assert headers and headers["X-Algolia-API-Key"] == "97457c1510bf43f21279078b2c848ef1"


def test_oztix_fails_loudly_on_an_empty_search(web) -> None:
    with pytest.raises(SourceEmpty):
        oztix.fetch(oztix_web(web, json.dumps({"hits": []})), PLACE, TODAY, UNTIL)


def test_oztix_fails_loudly_when_the_site_hides_its_key(web) -> None:
    with pytest.raises(SourceEmpty, match="no app bundle"):
        oztix.search_credentials(web({"oztix.com.au": "<html></html>"}))
    with pytest.raises(SourceEmpty, match="no longer names"):
        oztix.search_credentials(
            web({"app.abc3f3e0.js": "var e={}", "oztix.com.au/": fixture("oztix-home.html")})
        )


# --- Moshtix ----------------------------------------------------------------------------


def test_moshtix_reads_pages_until_an_empty_one(web) -> None:
    fake = web({"Page=1": fixture("moshtix-page.html"), "Page=2": "<html></html>"})
    events = moshtix.fetch(fake, PLACE, TODAY, UNTIL)

    assert [(e.title, e.starts_at.date()) for e in events] == [
        ("La Dispute", date(2026, 10, 20)),
        ("Winfield Road Experiment Residency", date(2026, 10, 22)),
    ]
    first = events[0]
    assert [p.name for p in first.performers] == ["La Dispute"]
    assert first.venue == "170 RUSSELL, MELBOURNE"
    assert first.event_id.startswith("mt:")


def test_moshtix_links_have_html_entities_decoded() -> None:
    listing = {
        "name": "Northlane",
        "startDate": "2026-11-20T19:00:00+11:00",
        "location": {"name": "Max Watt's", "address": {"addressLocality": "Melbourne"}},
        "url": "https://www.moshtix.com.au/v2/event/the-wind&#160;aus-tour/200644",
    }
    event = moshtix._event(listing, PLACE)
    assert event.url == "https://www.moshtix.com.au/v2/event/the-wind%C2%A0aus-tour/200644"
    assert event.event_id == "mt:200644"


def test_moshtix_stops_at_its_page_limit(web, monkeypatch) -> None:
    monkeypatch.setattr(moshtix, "MAX_PAGES", 2)
    fake = web({"Page=": fixture("moshtix-page.html")})
    assert len(moshtix.fetch(fake, PLACE, TODAY, UNTIL)) == 4
    assert len(fake.requests) == 2


def test_moshtix_fails_loudly_with_nothing_in_the_window(web) -> None:
    with pytest.raises(SourceEmpty):
        moshtix.fetch(web({"Page=": "<html></html>"}), PLACE, TODAY, UNTIL)


def test_moshtix_title_lead_stands_in_for_missing_performers() -> None:
    listing = {
        "name": "Slowly Slowly - Forever Tour",
        "url": "https://www.moshtix.com.au/v2/event/x/123",
        "startDate": "2026-11-01T19:00:00",
        "location": {"name": "Corner Hotel", "address": {"addressLocality": "Richmond"}},
        "performers": [{"name": "Corner Hotel"}],
    }
    event = moshtix._event(listing, PLACE)
    assert [p.name for p in event.performers] == ["Slowly Slowly"]
    assert event.event_id == "mt:123"


def test_moshtix_skips_tribute_and_theme_nights() -> None:
    assert moshtix.title_lead("Foo Fighters Tribute Night") is None
    assert moshtix.title_lead("A Night of Paramore") is None
    assert moshtix.title_lead("Telenova with Guests") == "Telenova"


# --- musick -----------------------------------------------------------------------------


def test_musick_fetches_only_the_artists_gigs_and_keeps_the_home_states(web) -> None:
    fake = web(
        {
            "sitemap-gigs.xml": fixture("musick-gigs.xml"),
            "la-dispute-2026-10-20": fixture("musick-gig.html"),
            "la-dispute-": fixture("musick-gig.html").replace('"VIC"', '"SA"'),
        }
    )
    events = musick.fetch(fake, [artist("La Dispute")], PLACE, TODAY, UNTIL)

    fetched = [url for url, _ in fake.requests[1:]]
    assert all("/gig/la-dispute-" in url for url in fetched)
    assert len(fetched) == 5
    assert len(events) == 1
    event = events[0]
    assert event.venue == "170 Russell"
    assert [p.name for p in event.performers] == ["La Dispute"]
    assert event.url == "https://www.moshtix.com.au/v2/event/la-dispute/197471"


def test_musick_candidates_respect_the_window_and_whole_name_prefixes() -> None:
    sitemap = fixture("musick-gigs.xml")
    artists = [artist("La Dispute"), artist("La")]
    urls = musick.candidate_urls(sitemap, artists, date(2026, 10, 21), date(2026, 10, 23))
    assert urls == [
        "https://musick.com.au/gig/la-dispute-at-the-gov-the-venue-2026-10-21/",
        "https://musick.com.au/gig/la-dispute-2026-10-23/",
    ]


def test_musick_reads_performer_lists_and_offer_lists() -> None:
    block = {
        "url": "https://musick.com.au/gig/x-2026-11-01/",
        "name": "Two Bands",
        "startDate": "2026-11-01T19:00:00+11:00",
        "location": {"name": "Max Watts", "address": {"addressRegion": "VIC"}},
        "performer": [{"name": "One"}, {"name": "Two"}, {}],
        "offers": [],
    }
    event = musick._event(block, PLACE)
    assert event is not None
    assert [p.name for p in event.performers] == ["One", "Two"]
    assert event.url == block["url"]
    assert musick._event({**block, "offers": [{"url": "https://t/1"}]}, PLACE).url == "https://t/1"


def test_musick_fails_loudly_on_an_empty_sitemap(web) -> None:
    with pytest.raises(SourceEmpty):
        musick.fetch(web({"sitemap": "<urlset></urlset>"}), [artist("X")], PLACE, TODAY, UNTIL)


# --- Ticketmaster -----------------------------------------------------------------------


def tm_listing(event_id: str, day: str | None, **extra) -> dict:
    listing = {
        "id": event_id,
        "name": "Northlane",
        "url": f"https://www.ticketmaster.com.au/e/{event_id}",
        "dates": {"start": {"localDate": day, "localTime": "19:30:00"}} if day else {},
        "sales": {"public": {"startDateTime": "2026-10-23T23:00:00Z"}},
        "_embedded": {
            "venues": [{"name": "Festival Hall", "city": {"name": "West Melbourne"}}],
            "attractions": [
                {"name": "Northlane", "externalLinks": {"musicbrainz": [{"id": "mb-1"}]}},
                {"name": "Guest"},
                {},
            ],
        },
    }
    return {**listing, **extra}


def test_ticketmaster_pages_through_and_reads_sale_times(web) -> None:
    pages = {
        "page=0": json.dumps({
            "_embedded": {"events": [tm_listing("a", "2026-12-01"), tm_listing("b", None)]},
            "page": {"totalPages": 2},
        }),
        "page=1": json.dumps({
            "_embedded": {"events": [tm_listing("c", "2028-01-01")]},
            "page": {"totalPages": 2},
        }),
    }  # fmt: skip
    fake = web(pages)
    events = ticketmaster.fetch(fake, "KEY", PLACE, TODAY, UNTIL)

    assert [e.event_id for e in events] == ["tm:a"]
    event = events[0]
    assert event.starts_at == at("2026-12-01 19:30")
    assert event.on_sale_at == at("2026-10-24 10:00")
    assert [(p.name, p.mbid) for p in event.performers] == [("Northlane", "mb-1"), ("Guest", None)]
    assert event.venue == "Festival Hall"
    assert "apikey=KEY" in fake.requests[0][0]


def test_ticketmaster_stops_at_the_apis_deep_paging_limit(web) -> None:
    page = json.dumps(
        {"_embedded": {"events": [tm_listing("a", "2026-12-01")]}, "page": {"totalPages": 50}}
    )
    fake = web({"ticketmaster": page})
    assert len(ticketmaster.fetch(fake, "K", PLACE, TODAY, UNTIL)) == ticketmaster.MAX_PAGES
    assert len(fake.requests) == ticketmaster.MAX_PAGES


def test_ticketmaster_without_sale_or_time_still_reads() -> None:
    listing = tm_listing("d", "2026-12-01", sales={}, _embedded={})
    listing["dates"]["start"].pop("localTime")
    event = ticketmaster._event(listing, PLACE)
    assert event is not None
    assert event.on_sale_at is None
    assert event.venue == ""
    assert event.starts_at == at("2026-12-01 19:00")


def test_ticketmaster_fails_loudly_with_no_events(web) -> None:
    with pytest.raises(SourceEmpty):
        ticketmaster.fetch(
            web({"ticketmaster": json.dumps({"page": {}})}), "K", PLACE, TODAY, UNTIL
        )


# --- Festival lineups -------------------------------------------------------------------


def names_playing(page: str, state: str = "VIC") -> dict[str, bool]:
    return {act.name.upper(): act.plays_here for act in lineups.read_lineup(page, state)}


def test_good_things_lineup_keeps_other_states_exclusives_out() -> None:
    acts = names_playing(fixture("lineup-good-things.html"))
    assert acts["NECK DEEP"]
    assert acts["SLOWLY SLOWLY"]
    assert acts["SPIRITBOX"]
    assert acts["MINISTRY"]  # QLD & VIC exclusive
    assert acts["BILMURI"]  # Australian exclusive
    assert not acts["MILLENCOLIN"]  # NSW exclusive, under a note line
    assert not acts["P.O.D"]


def test_laneway_melbourne_page_lists_its_acts() -> None:
    acts = names_playing(fixture("lineup-laneway.html"))
    assert acts["NINAJIRACHI"]
    assert acts["THE HAYLEY WILLIAMS SHOW"]


def test_known_pages_and_fetch(web) -> None:
    pages = lineups.known_pages()
    assert {page.match for page in pages} >= {"good things", "laneway"}
    fake = web({"goodthings": "<p>Neck Deep</p><p>NSW EXCLUSIVE</p>"})
    good_things = next(p for p in pages if p.match == "good things")
    assert lineups.fetch(fake, good_things, "VIC") == [lineups.LineupAct("Neck Deep", False)]
    assert lineups.fetch(fake, good_things, "NSW") == [lineups.LineupAct("Neck Deep", True)]


def test_a_festivals_file_replaces_the_known_pages(tmp_path) -> None:
    path = tmp_path / "festivals.json"
    path.write_text('[{"festival": "Some Fest", "match": "some fest", "url": "https://x/"}]')
    assert lineups.known_pages(path) == [lineups.LineupPage("Some Fest", "some fest", "https://x/")]


def test_moshtix_and_oztix_ask_for_the_home_state(web) -> None:
    sydney = replace(PLACE, state="NSW")
    fake = web({"Page=": "<html></html>"})
    with pytest.raises(SourceEmpty, match="NSW"):
        moshtix.fetch(fake, sydney, TODAY, UNTIL)
    assert "StateId=2&" in fake.requests[0][0]
    fake = oztix_web(web, json.dumps({"hits": []}))
    with pytest.raises(SourceEmpty, match="NSW"):
        oztix.fetch(fake, sydney, TODAY, UNTIL)
    assert "Venue.State%3ANSW" in fake.requests[-1][0]


def test_ticketmaster_asks_for_events_around_home(web) -> None:
    page = json.dumps({"_embedded": {"events": [tm_listing("a", "2026-12-01")]}, "page": {}})
    fake = web({"ticketmaster": page})
    ticketmaster.fetch(fake, "K", PLACE, TODAY, UNTIL)
    query = parse_qs(urlparse(fake.requests[0][0]).query)
    assert query["geoPoint"] == [ticketmaster.geohash(-37.8136, 144.9631)]
    assert "latlong" not in query
    assert query["radius"] == ["150"]
    assert query["unit"] == ["km"]
    assert query["countryCode"] == ["AU"]
    assert "stateCode" not in query


def test_geohash_matches_known_points() -> None:
    assert ticketmaster.geohash(42.6, -5.6, 5) == "ezs42"
    assert ticketmaster.geohash(57.64911, 10.40744, 11) == "u4pruydqqvj"
    assert len(ticketmaster.geohash(-37.8136, 144.9631)) == 9
    assert ticketmaster.geohash(-37.8136, 144.9631).startswith("r1r0")


def test_a_lineup_page_applies_to_its_country_and_state_only() -> None:
    laneway = lineups.LineupPage("Laneway", "laneway", "https://x/", "AU", "VIC")
    assert laneway.applies_to(PLACE)
    assert not laneway.applies_to(replace(PLACE, state="NSW"))
    assert lineups.LineupPage("Tour", "tour", "https://x/", "AU").applies_to(
        replace(PLACE, state="NSW")
    )
    assert not lineups.LineupPage("Tour", "tour", "https://x/", "AU").applies_to(
        replace(PLACE, country="NZ", state=None)
    )
    assert lineups.LineupPage("Any", "any", "https://x/").applies_to(replace(PLACE, country="GB"))
    defaults = {page.match: page for page in lineups.known_pages()}
    assert (defaults["laneway"].country, defaults["laneway"].state) == ("AU", "VIC")
    assert (defaults["good things"].country, defaults["good things"].state) == ("AU", None)


def test_ticketmaster_says_when_its_event_cap_cuts_the_window_short(web) -> None:
    capped = json.dumps(
        {"_embedded": {"events": [tm_listing("a", "2026-12-01")]}, "page": {"totalPages": 50}}
    )
    warnings: list[str] = []
    ticketmaster.fetch(web({"ticketmaster": capped}), "K", PLACE, TODAY, UNTIL, warnings.append)
    assert warnings == [
        "Ticketmaster: stopped at the API's 1,000 events; nothing after 2026-12-01 was read"
        " (a smaller HOME_RADIUS_KM reads further ahead)"
    ]

    empty = json.dumps({"page": {"totalPages": 50}})
    warnings.clear()
    with pytest.raises(SourceEmpty):
        ticketmaster.fetch(web({"ticketmaster": empty}), "K", PLACE, TODAY, UNTIL, warnings.append)
    assert "nothing after 2026-10-20" in warnings[0]

    whole = json.dumps(
        {"_embedded": {"events": [tm_listing("z", "2027-10-20")]}, "page": {"totalPages": 50}}
    )
    warnings.clear()
    ticketmaster.fetch(web({"ticketmaster": whole}), "K", PLACE, TODAY, UNTIL, warnings.append)
    assert warnings == []
