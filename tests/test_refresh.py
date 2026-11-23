import json
from datetime import timedelta

import pytest
from conftest import FakeWeb, artist, at, fixture, settings, show
from test_calendar import FakeDav
from test_tickets import FakeImap, mail

from live_shows import refresh as refresh_module
from live_shows import store as store_module
from live_shows.model import Tier
from live_shows.refresh import Outcome, Services, fetch_samples, plex_token, refresh
from live_shows.settings import Settings
from live_shows.store import Store

MONDAY = at("2026-10-19 07:00")
TUESDAY = at("2026-10-20 07:00")


def plex_library() -> dict[str, str]:
    album = [
        {
            "grandparentTitle": "La Dispute",
            "ratingKey": f"t{i}",
            "parentKey": "/library/metadata/ld",
        }
        for i in range(5)
    ]
    history = [{**play, "viewedAt": 240 * n} for n, play in enumerate(album * 3)]
    history += [{"grandparentTitle": "Spiritbox", "ratingKey": "s"}] * 2
    return {
        "history": json.dumps({"MediaContainer": {"Metadata": history}}),
        "metadata/ld/children": json.dumps({"MediaContainer": {"Metadata": [
            {"ratingKey": f"t{i}", "index": i + 1} for i in range(5)]}}),
        "type=10": json.dumps({"MediaContainer": {}}),
        "type=8": json.dumps({"MediaContainer": {"Metadata": [
            {"title": "La Dispute", "Guid": [{"id": "mbid://mb-ld"}]}]}}),
        "listenbrainz": json.dumps([]),
    }  # fmt: skip


def sources() -> dict:
    return {
        "rest.bandsintown.com/artists/La%20Dispute": fixture("bandsintown-la-dispute.json"),
        "rest.bandsintown.com": json.dumps([]),
        "www.oztix.com.au/js/app.abc3f3e0.js": fixture("oztix-app.js"),
        "www.oztix.com.au/": fixture("oztix-home.html"),
        "algolia.net": fixture("oztix.json"),
        "moshtix.com.au/v2/search?query=&StateId=7&CategoryList=2&DateFilterId=6&Page=1": fixture(
            "moshtix-page.html"
        ),
        "moshtix.com.au": "<html></html>",
        "sitemap-gigs.xml": fixture("musick-gigs.xml"),
        "musick.com.au/gig/la-dispute-2026-10-20/": fixture("musick-gig.html"),
        "musick.com.au/gig/": "<html></html>",
        "goodthings": "<p>Neck Deep</p>",
        "app.ticketmaster.com": json.dumps({"page": {"totalPages": 1}}),
        "itunes.apple.com": json.dumps({"results": []}),
    }


@pytest.fixture
def config(tmp_path) -> Settings:
    return settings(LIVE_SHOWS_DB=str(tmp_path / "db.sqlite"))


class Outbox:
    def __init__(self):
        self.sent = []

    def __call__(self, host, port, timeout, context=None):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def starttls(self, context):
        pass

    def login(self, user, password):
        pass

    def send_message(self, message):
        self.sent.append(message)


def services(web: FakeWeb, now=TUESDAY, imap=None, dav=None, outbox=None) -> Services:
    return Services(
        fetcher=web,
        now=lambda: now,
        smtp=outbox or Outbox(),
        smtp_ssl=Outbox(),
        imap=imap or FakeImap({}),
        caldav=lambda user, password: dav or FakeDav({"personal": "Personal"}),
    )


def test_without_a_password_it_finds_shows_and_sends_nothing(config: Settings) -> None:
    store = Store(config.database)
    outbox = Outbox()
    outcome = refresh(
        config, services(FakeWeb({**plex_library(), **sources()}), outbox=outbox), store
    )

    assert outcome.failures == []
    assert outcome.counts["shows"] == 1
    [show] = store.shows()
    assert show["sources"] == ["Moshtix", "musick", "Bandsintown"]
    assert outbox.sent == []
    assert store.last_run()["ok"] == 1


def test_album_orders_are_read_once_and_then_come_from_the_store(config: Settings) -> None:
    store = Store(config.database)
    for _ in range(2):
        web = FakeWeb({**plex_library(), **sources()})
        refresh(config, services(web), store)
        children = [url for url, _ in web.requests if "/children" in url]
    assert children == []
    [show] = store.shows()
    assert show["pinned"] is True
    assert show["acts"][0]["why"] == "an album front to back 3 times · 15 plays"


def test_with_a_password_it_reads_tickets_fills_calendars_and_mails(config: Settings) -> None:
    store = Store(config.database)
    store.set_secret("mail_password", "pw")
    store.set_secret("ticketmaster_key", "KEY")
    dav = FakeDav({"personal": "Personal"})
    outbox = Outbox()
    unrelated = mail(
        "orders@moshtix.com.au", "Order", "Someone 1 November 2026", message_id="<u@x>"
    )
    imap = FakeImap({"moshtix.com.au": [unrelated]})
    web = FakeWeb({**plex_library(), **sources()})

    outcome = refresh(config, services(web, imap=imap, dav=dav, outbox=outbox), store)

    assert outcome.failures == ["Ticketmaster: Ticketmaster: no music events near home"]
    assert outcome.counts["tickets_from_email"] == 0
    assert outcome.counts["written"] == 1
    assert [m["Subject"] for m in outbox.sent] == [
        "New show: La Dispute at 170 Russell, Tue 20 Oct"
    ]
    assert outcome.sent == [outbox.sent[0]["Subject"]]

    # The next morning the email confirming the ticket arrives; nothing new is mailed.
    confirmation = mail("orders@moshtix.com.au", "Order", "La Dispute 20 October 2026")
    imap = FakeImap({"moshtix.com.au": [confirmation]})
    outcome = refresh(
        config,
        services(web, now=TUESDAY + timedelta(hours=1), imap=imap, dav=dav, outbox=outbox),
        store,
    )
    assert outcome.counts["tickets_from_email"] == 1
    assert store.shows()[0]["bought"]
    assert len(outbox.sent) == 1
    assert any("/personal/" in path for path in dav.events)


def test_mondays_add_the_digest(config: Settings) -> None:
    store = Store(config.database)
    store.set_secret("mail_password", "pw")
    spiritbox = json.dumps([{
        "id": "s1", "url": "https://bit/s1", "datetime": "2026-10-25T19:00:00",
        "lineup": ["Spiritbox"], "venue": {"name": "Forum Melbourne", "latitude": "-37.81",
                                            "longitude": "144.97"}}])  # fmt: skip
    web = FakeWeb({**plex_library(), "artists/Spiritbox": spiritbox, **sources()})
    outbox = Outbox()

    refresh(config, services(web, now=MONDAY, outbox=outbox), store)

    assert [m["Subject"] for m in outbox.sent] == [
        "New show: La Dispute at 170 Russell, Tue 20 Oct",
        "Live shows this week",
    ]


def test_a_dry_run_prints_mail_and_leaves_calendars_alone(config: Settings, caplog) -> None:
    store = Store(config.database)
    store.set_secret("mail_password", "pw")
    dav = FakeDav({"personal": "Personal"})
    outbox = Outbox()
    caplog.set_level("INFO")

    refresh(
        config,
        services(FakeWeb({**plex_library(), **sources()}), dav=dav, outbox=outbox),
        store,
        dry_run=True,
    )

    assert outbox.sent == []
    assert dav.requests == []
    assert "would send: New show: La Dispute" in caplog.text


def test_a_failing_source_or_lineup_is_reported_and_the_rest_still_runs(config: Settings) -> None:
    store = Store(config.database)
    festival = json.dumps([{
        "id": "f1", "url": "https://bit/f1", "datetime": "2026-12-04T11:00:00",
        "title": "Good Things Festival 2026", "festival_start_date": "2026-12-04",
        "lineup": ["La Dispute"], "venue": {"name": "Flemington Racecourse",
                                            "latitude": "-37.79", "longitude": "144.91"}}])  # fmt: skip
    routes = {**plex_library(), **sources()}
    routes["rest.bandsintown.com/artists/La%20Dispute"] = festival
    routes["algolia.net"] = OSError("down")
    routes["goodthings"] = OSError("lineup down")
    outcome = refresh(config, services(FakeWeb(routes)), store)

    assert outcome.failures == ["Oztix: down", "lineup Good Things Festival: lineup down"]
    assert outcome.counts["Bandsintown"] == 1
    assert [s["title"] for s in store.shows()] == ["La Dispute", "Good Things Festival 2026"]
    assert store.shows()[1]["acts"][0]["name"] == "La Dispute"
    assert store.last_run()["ok"] == 0


def test_a_calendar_refusal_is_reported_and_the_email_still_goes(config: Settings) -> None:
    store = Store(config.database)
    store.set_secret("mail_password", "pw")
    dav = FakeDav({"personal": "Personal"})
    dav.refuse = {"PUT"}
    outbox = Outbox()

    outcome = refresh(
        config, services(FakeWeb({**plex_library(), **sources()}), dav=dav, outbox=outbox), store
    )

    assert [f for f in outcome.failures if f.startswith("calendar:")] == [
        "calendar: PUT 2026-10-20~la-dispute@shows.example: 403 Forbidden"
    ]
    assert [m["Subject"] for m in outbox.sent] == [
        "New show: La Dispute at 170 Russell, Tue 20 Oct"
    ]
    assert store.last_run()["ok"] == 0


def test_a_broken_run_is_recorded_not_raised(tmp_path) -> None:
    preferences = tmp_path / "Preferences.xml"
    preferences.write_text("<Preferences />")
    config = settings(
        LIVE_SHOWS_DB=str(tmp_path / "db.sqlite"), PLEX_TOKEN="", PLEX_PREFERENCES=str(preferences)
    )
    store = Store(config.database)
    outcome = refresh(config, services(FakeWeb({})), store)
    assert outcome.failures == [f"run: no PlexOnlineToken in {preferences}"]
    assert store.last_run()["ok"] == 0


def test_plex_token_reads_the_owners_token(tmp_path) -> None:
    path = tmp_path / "Preferences.xml"
    path.write_text('<Preferences A="1" PlexOnlineToken="abc" />')
    assert plex_token(path) == "abc"
    with pytest.raises(RuntimeError, match="PLEX_TOKEN"):
        plex_token(None)


def test_the_window_is_a_year() -> None:
    assert timedelta(days=365) == refresh_module.WINDOW


def maybe(name: str):
    return artist(name, Tier.MAYBE, plays=0, similar_to=["La Dispute"])


def store_with_maybes(config: Settings, monkeypatch) -> Store:
    store = Store(config.database)
    store.record_shows(
        [show("2026-11-01", [artist("La Dispute"), maybe("Kept Band"), maybe("Trimmed Band")]),
         show("2026-11-02", [maybe("Fresh Band"), maybe("Stale Band")]),
         show("2026-11-03", [maybe("New Band")])],
        TUESDAY,
    )  # fmt: skip
    store.set_choice("kept band", "Kept Band", "kept", ["La Dispute"])
    store.set_choice("trimmed band", "Trimmed Band", "trimmed", ["La Dispute"])
    monkeypatch.setattr(store_module, "now", lambda: TUESDAY - timedelta(days=29))
    store.save_samples("fresh band", {"apple": None, "kind": "none", "items": []})
    monkeypatch.setattr(store_module, "now", lambda: TUESDAY - timedelta(days=31))
    store.save_samples("stale band", {"apple": None, "kind": "none", "items": []})
    monkeypatch.setattr(store_module, "now", lambda: TUESDAY)
    return store


def test_samples_are_fetched_for_undecided_maybes_without_recent_ones(
    config: Settings, monkeypatch
) -> None:
    store = store_with_maybes(config, monkeypatch)
    web = FakeWeb({"itunes.apple.com": json.dumps({"results": []})})
    outcome = Outcome()

    assert fetch_samples(services(web), store, TUESDAY, outcome, "nz") == 2
    assert all("country=nz" in url for url, _ in web.requests)

    searched = [url.split("term=")[1].split("&")[0] for url, _ in web.requests]
    assert searched == ["Stale+Band", "New+Band"]
    assert store.samples()["new band"]["fetched_at"] == TUESDAY.isoformat()
    assert "kept band" not in store.samples() and "trimmed band" not in store.samples()
    assert outcome.failures == []


def test_apple_failing_stops_sampling_and_is_reported(config: Settings, monkeypatch) -> None:
    store = store_with_maybes(config, monkeypatch)
    web = FakeWeb({"itunes.apple.com": OSError("down")})
    outcome = Outcome()

    assert fetch_samples(services(web), store, TUESDAY, outcome) == 0

    assert len(web.requests) == 1
    assert outcome.failures == ["Apple samples: down"]
    assert "new band" not in store.samples()


def test_a_run_keeps_kept_artists_and_stops_trimmed_out_loved_ones(config: Settings) -> None:
    store = Store(config.database)
    store.set_choice("kept band", "Kept Band", "kept", ["Neck Deep"])
    for name in ("One", "Two", "Three"):
        store.set_choice(name.lower(), name, "trimmed", ["La Dispute"])
    passed = {}
    build_tiers = refresh_module.build_tiers

    def spy(listening, lookup, stopped_seeds, kept):
        passed.update(stopped=stopped_seeds, kept=list(kept))
        return build_tiers(listening, lookup, stopped_seeds, kept)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(refresh_module, "build_tiers", spy)
        outcome = refresh(config, services(FakeWeb({**plex_library(), **sources()})), store)

    assert passed == {"stopped": {"La Dispute": 3}, "kept": ["kept band"]}
    assert outcome.counts["artists"] == 3  # La Dispute, Spiritbox and the kept band
    assert outcome.counts["sampled"] == 0


def test_the_music_section_is_found_when_none_is_set(tmp_path) -> None:
    config = settings(LIVE_SHOWS_DB=str(tmp_path / "db.sqlite"), PLEX_MUSIC_SECTION="")
    sections = json.dumps({"MediaContainer": {"Directory": [{"key": "9", "type": "artist"}]}})
    web = FakeWeb({"/library/sections?": sections, **plex_library(), **sources()})
    refresh(config, services(web), Store(config.database))
    history = next(url for url, _ in web.requests if "history" in url)
    assert "librarySectionID=9" in history


def test_lastfm_is_read_with_the_key_saved_on_the_page(tmp_path) -> None:
    config = settings(LIVE_SHOWS_DB=str(tmp_path / "db.sqlite"), LIBRARY="lastfm",
                      LASTFM_USER="listener")  # fmt: skip
    store = Store(config.database)
    top = json.dumps({"toptracks": {"track": [
        {"name": f"T{i}", "playcount": "3", "artist": {"name": "La Dispute", "mbid": "mb-ld"}}
        for i in range(5)], "@attr": {"totalPages": "1"}}})  # fmt: skip
    routes = {"user.gettoptracks": top, "user.getlovedtracks": "{}", **plex_library(), **sources()}

    outcome = refresh(config, services(FakeWeb(routes)), store)
    assert outcome.failures == ["run: no Last.fm API key saved: set it in Settings on the page"]

    store.set_secret("lastfm_key", "KEY")
    web = FakeWeb(routes)
    outcome = refresh(config, services(web), store)
    assert outcome.failures == []
    assert store.shows()[0]["acts"][0]["tier"] == "love"
    assert not any("plex" in url for url, _ in web.requests)


def test_outside_australia_only_the_worldwide_sources_are_read(tmp_path) -> None:
    config = settings(LIVE_SHOWS_DB=str(tmp_path / "db.sqlite"), HOME_COUNTRY="NZ", HOME_STATE="",
                      HOME_TIMEZONE="Pacific/Auckland", HOME_LATITUDE="-36.85",
                      HOME_LONGITUDE="174.76")  # fmt: skip
    store = Store(config.database)
    store.set_secret("ticketmaster_key", "KEY")
    web = FakeWeb({**plex_library(), **sources()})

    outcome = refresh(config, services(web), store)

    assert set(outcome.counts) & {"Oztix", "Moshtix", "musick"} == set()
    assert outcome.counts["Bandsintown"] == 1  # the Auckland date, not the Melbourne one
    hosts = {url.split("/")[2] for url, _ in web.requests}
    assert hosts.isdisjoint({"www.oztix.com.au", "www.moshtix.com.au", "musick.com.au"})
    assert "goodthingsfestival.com.au" not in hosts
    assert "countryCode=NZ" in next(url for url, _ in web.requests if "ticketmaster" in url)


def test_each_mail_step_runs_only_when_it_is_set_up(tmp_path) -> None:
    config = settings(LIVE_SHOWS_DB=str(tmp_path / "db.sqlite"), MAIL_PROVIDER="",
                      SMTP_HOST="smtp.example.com", SMTP_PORT="465")  # fmt: skip
    store = Store(config.database)
    store.set_secret("smtp_password", "pw")
    imap = FakeImap({})
    dav = FakeDav({"personal": "Personal"})
    starttls, implicit = Outbox(), Outbox()
    run = Services(fetcher=FakeWeb({**plex_library(), **sources()}), now=lambda: TUESDAY,
                   smtp=starttls, smtp_ssl=implicit, imap=imap,
                   caldav=lambda user, password: dav)  # fmt: skip

    outcome = refresh(config, run, store)

    assert outcome.failures == []
    assert "tickets_from_email" not in outcome.counts
    assert imap.calls == []
    assert dav.requests == []
    assert starttls.sent == []
    assert [m["To"] for m in implicit.sent] == ["listener@example.com"]
