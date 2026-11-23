import io
import urllib.error
from datetime import date

import pytest
from conftest import MELBOURNE, artist, at, show

from live_shows import calendar
from live_shows.calendar import CalendarError, Calendars, Collection
from live_shows.model import Tier
from live_shows.store import Store, show_record

ADDRESS = "listener@example.com"
HOME = "/dav/calendars/user/listener%40example.com/"
ROOT = f"https://caldav.fastmail.com{HOME}"
# Fastmail lists the calendars with the address unquoted.
LISTED_HOME = "/dav/calendars/user/listener@example.com/"
APP = "https://live-shows.example/"


def multistatus(*responses: str) -> str:
    return f'<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">{"".join(responses)}</d:multistatus>'


def collection_xml(href: str, name: str, calendar_type: bool = True) -> str:
    kind = "<c:calendar/>" if calendar_type else ""
    return (
        f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>"
        f"<d:displayname>{name}</d:displayname><d:resourcetype><d:collection/>{kind}"
        "</d:resourcetype></d:prop></d:propstat></d:response>"
    )


class FakeDav:
    """A CalDAV server holding the calendars and recording every request."""

    def __init__(self, names: dict[str, str], default: str = "personal") -> None:
        self.names = dict(names)
        self.default = default
        self.events: dict[str, str] = {}
        self.requests: list[tuple[str, str]] = []
        self.refuse: set[str] = set()
        self.listed_home = HOME
        self.unlisted: set[str] = set()

    def __call__(self, method, url, body, headers):
        path = url.removeprefix("https://caldav.fastmail.com")
        self.requests.append((method, path))
        if method in self.refuse:
            return 403, "Forbidden"
        if method == "PROPFIND" and path.endswith("Inbox/"):
            href = f"{self.listed_home}{self.default}/"
            return 207, multistatus(
                "<d:response><d:href>x</d:href><d:propstat><d:prop>"
                f"<c:schedule-default-calendar-URL><d:href>{href}</d:href>"
                "</c:schedule-default-calendar-URL></d:prop></d:propstat></d:response>"
            )
        if method == "PROPFIND":
            assert headers["Depth"] == "1"
            listed = [collection_xml(HOME, "", calendar_type=False)]
            listed += [
                collection_xml(f"{self.listed_home}{slug}/", name)
                for slug, name in self.names.items()
                if slug not in self.unlisted
            ]
            return 207, multistatus(*listed)
        if method == "MKCALENDAR":
            self.names[path.removeprefix(HOME).strip("/")] = "Live shows"
            return 201, ""
        if method == "PUT":
            # Fastmail holds one uid once across all of an account's calendars.
            uid = next(line for line in body.decode().split("\r\n") if line.startswith("UID:"))
            elsewhere = [held for held, ics in self.events.items() if held != path and uid in ics]
            if elsewhere:
                return 403, f"<C:unique-scheduling-object-resource>{elsewhere[0]}"
            self.events[path] = body.decode()
            return 201, ""
        if method == "DELETE":
            return (204, "") if self.events.pop(path, None) else (404, "")
        raise AssertionError(method)


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "db.sqlite")


def records(store: Store) -> list[dict]:
    return store.shows()


def test_live_shows_is_made_once_and_then_found(store: Store) -> None:
    dav = FakeDav({"personal": "Personal"})
    calendars = Calendars(dav, ROOT)

    made = calendars.live_shows()
    assert made == Collection(f"{HOME}live-shows/", "Live shows")
    assert ("MKCALENDAR", f"{HOME}live-shows/") in dav.requests
    assert calendars.live_shows() == made
    assert [m for m, _ in dav.requests].count("MKCALENDAR") == 1


def test_a_made_calendar_is_the_href_the_server_lists(store: Store) -> None:
    dav = FakeDav({"personal": "Personal"})
    dav.listed_home = LISTED_HOME

    made = Calendars(dav, ROOT).live_shows()

    assert ("MKCALENDAR", f"{HOME}live-shows/") in dav.requests
    assert made == Collection(f"{LISTED_HOME}live-shows/", "Live shows")


def test_a_made_calendar_the_server_does_not_list_is_refused(store: Store) -> None:
    dav = FakeDav({"personal": "Personal"})
    dav.unlisted = {"live-shows"}
    with pytest.raises(CalendarError, match="not listed"):
        Calendars(dav, ROOT).live_shows()


def test_rows_kept_under_the_quoted_href_are_synced_as_the_listed_one(store: Store) -> None:
    dav = FakeDav({"personal": "Personal", "live-shows": "Live shows"})
    dav.listed_home = LISTED_HOME
    cancelled = "2026-11-01~gone@live-shows.example"
    store.set_calendar_entry(f"{HOME}live-shows/", cancelled, "digest")

    counts = calendar.sync(
        Calendars(dav, ROOT), store, [], APP, date(2026, 10, 20), at("2026-10-20 07:00"), MELBOURNE
    )

    assert counts == {"written": 0, "removed": 1}
    assert ("DELETE", f"{LISTED_HOME}live-shows/{cancelled}.ics") in dav.requests
    assert store.calendar_entries(f"{HOME}live-shows/") == {}
    assert store.calendar_entries(f"{LISTED_HOME}live-shows/") == {}


def test_main_is_the_accounts_default_calendar(store: Store) -> None:
    dav = FakeDav({"personal": "Personal", "work": "Work"})
    assert Calendars(dav, ROOT).tickets() == Collection(f"{HOME}personal/", "Personal")
    dav.default = "missing"
    with pytest.raises(CalendarError, match="no default calendar"):
        Calendars(dav, ROOT).tickets()


def test_every_show_goes_on_live_shows_and_ticketed_ones_on_her_main(store: Store) -> None:
    dav = FakeDav({"personal": "Personal"})
    bought = show("2026-11-08", [artist("Dance Gavin Dance")])
    store.record_shows([show(), bought], at("2026-10-20 07:00"))
    store.mark_bought(bought.pairs, "button")
    calendars = Calendars(dav, ROOT)

    counts = calendar.sync(
        calendars, store, records(store), APP, date(2026, 10, 20), at("2026-10-20 07:00"), MELBOURNE
    )

    assert counts == {"written": 3, "removed": 0}
    assert sorted(dav.events) == [
        f"{HOME}live-shows/2026-10-20~la-dispute@live-shows.example.ics",
        f"{HOME}live-shows/2026-11-08~dance-gavin-dance@live-shows.example.ics",
        f"{HOME}personal/2026-11-08~dance-gavin-dance@tickets.live-shows.example.ics",
    ]
    assert (
        "UID:2026-11-08~dance-gavin-dance@tickets.live-shows.example"
        in dav.events[f"{HOME}personal/2026-11-08~dance-gavin-dance@tickets.live-shows.example.ics"]
    )

    again = calendar.sync(
        calendars, store, records(store), APP, date(2026, 10, 20), at("2026-10-21 07:00"), MELBOURNE
    )
    assert again == {"written": 0, "removed": 0}


def test_maybe_shows_stay_off_both_calendars_unless_she_has_tickets(store: Store) -> None:
    dav = FakeDav({"personal": "Personal"})
    calendars = Calendars(dav, ROOT)
    was_loved = show("2026-11-01", [artist("Spiritbox")])
    store.record_shows([was_loved], at("2026-10-20 07:00"))
    calendar.sync(
        calendars, store, records(store), APP, date(2026, 10, 20), at("2026-10-20 07:00"), MELBOURNE
    )

    now_maybe = show("2026-11-01", [artist("Spiritbox", Tier.MAYBE)])
    maybe = show("2026-11-05", [artist("Northlane", Tier.MAYBE)])
    bought_maybe = show("2026-11-09", [artist("Foo Fighters", Tier.MAYBE)])
    store.record_shows([now_maybe, maybe, bought_maybe], at("2026-10-21 07:00"))
    store.mark_bought(bought_maybe.pairs, "button")
    counts = calendar.sync(
        calendars, store, records(store), APP, date(2026, 10, 21), at("2026-10-21 07:00"), MELBOURNE
    )

    assert counts == {"written": 2, "removed": 1}
    assert sorted(dav.events) == [
        f"{HOME}live-shows/2026-11-09~foo-fighters@live-shows.example.ics",
        f"{HOME}personal/2026-11-09~foo-fighters@tickets.live-shows.example.ics",
    ]


def test_cancelled_shows_leave_live_shows_but_her_tickets_stay_until_taken_back(
    store: Store,
) -> None:
    dav = FakeDav({"personal": "Personal"})
    calendars = Calendars(dav, ROOT)
    cancelled = show("2026-11-01", [artist("Telenova")])
    bought = show("2026-11-08", [artist("Dance Gavin Dance")])
    past = show("2026-10-21", [artist("Old")])
    store.record_shows([cancelled, bought, past], at("2026-10-20 07:00"))
    store.mark_bought(bought.pairs, "button")
    calendar.sync(
        calendars, store, records(store), APP, date(2026, 10, 20), at("2026-10-20 07:00"), MELBOURNE
    )

    # A day later Telenova is gone, the ticketed show is no longer listed, Old has passed.
    store.record_shows([], at("2026-10-22 07:00"))
    counts = calendar.sync(
        calendars, store, records(store), APP, date(2026, 10, 22), at("2026-10-22 07:00"), MELBOURNE
    )
    assert counts == {"written": 0, "removed": 1}
    assert (
        f"{HOME}personal/2026-11-08~dance-gavin-dance@tickets.live-shows.example.ics" in dav.events
    )
    assert f"{HOME}live-shows/2026-10-21~old@live-shows.example.ics" in dav.events

    store.unmark_bought(bought.pairs)
    store.record_shows([bought], at("2026-10-23 07:00"))
    counts = calendar.sync(
        calendars, store, records(store), APP, date(2026, 10, 23), at("2026-10-23 07:00"), MELBOURNE
    )
    assert counts["removed"] == 1
    assert (
        f"{HOME}personal/2026-11-08~dance-gavin-dance@tickets.live-shows.example.ics"
        not in dav.events
    )


def test_a_gig_is_three_hours_in_utc_and_a_festival_is_all_day() -> None:
    gig = {**show_record(show()), "bought": False}
    ics = calendar.event_ics(gig, "u@x", APP, at("2026-10-20 07:00"), MELBOURNE)
    assert "DTSTART:20261020T080000Z" in ics
    assert "DTEND:20261020T110000Z" in ics
    assert "SUMMARY:La Dispute at 170 Russell" in ics
    assert "UID:u@x\r\n" in ics
    assert ics.endswith("END:VCALENDAR\r\n")

    festival = {**show_record(show("2026-12-04", is_festival=True, title="Good Things, Melbourne"))}
    ics = calendar.event_ics(festival, "u@x", APP, at("2026-10-20 07:00"), MELBOURNE)
    assert "DTSTART;VALUE=DATE:20261204" in ics
    assert "DTEND;VALUE=DATE:20261205" in ics
    assert r"SUMMARY:Good Things\, Melbourne" in ics


def test_long_lines_fold_and_text_is_escaped() -> None:
    long_show = {**show_record(show(acts=[artist("A" * 120)])), "bought": False}
    ics = calendar.event_ics(long_show, "u@x", APP, at("2026-10-20 07:00"), MELBOURNE)
    assert all(len(line) <= 75 for line in ics.split("\r\n"))
    assert calendar._escape("a;b\\c\nd") == r"a\;b\\c\nd"
    assert calendar._fold("") == ""


def test_refusals_raise(store: Store) -> None:
    dav = FakeDav({"personal": "Personal"})
    calendars = Calendars(dav, ROOT)
    target = Collection(f"{HOME}personal/", "Personal")
    for method, call in [
        ("PUT", lambda: calendars.put(target, "u", "ics")),
        ("DELETE", lambda: calendars.delete(target, "u")),
        ("PROPFIND", calendars.collections),
        ("MKCALENDAR", calendars.live_shows),
    ]:
        dav.refuse = {method}
        with pytest.raises(CalendarError):
            call()
    dav.refuse = set()
    calendars.delete(target, "never-there")


def test_web_request_signs_in_and_returns_errors_as_statuses(monkeypatch) -> None:
    seen = []

    class Response(io.BytesIO):
        status = 207

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

    def urlopen(request, timeout):
        seen.append(request)
        if request.get_method() == "DELETE":
            raise urllib.error.HTTPError(request.full_url, 404, "gone", {}, io.BytesIO(b"nope"))
        return Response(b"<ok/>")

    monkeypatch.setattr(calendar.urllib.request, "urlopen", urlopen)
    request = calendar.web_request(ADDRESS, "pw")
    assert request("PROPFIND", "https://x/", b"<a/>", {"Depth": "1"}) == (207, "<ok/>")
    assert request("DELETE", "https://x/e.ics", None, {}) == (404, "nope")
    assert seen[0].headers["Authorization"].startswith("Basic ")
    assert seen[0].headers["Depth"] == "1"


def test_a_named_tickets_calendar_is_found_by_its_name(store: Store) -> None:
    dav = FakeDav({"personal": "Personal", "gigs": "Gigs"})
    calendars = Calendars(dav, ROOT, "Shows", "Gigs")
    assert calendars.tickets() == Collection(f"{HOME}gigs/", "Gigs")
    with pytest.raises(CalendarError, match="no calendar named Missing"):
        Calendars(dav, ROOT, "Shows", "Missing").tickets()


def test_the_app_calendar_takes_its_configured_name(store: Store) -> None:
    dav = FakeDav({"personal": "Personal", "shows": "Shows"})
    assert Calendars(dav, ROOT, "Shows").live_shows() == Collection(f"{HOME}shows/", "Shows")

    made = []

    def server(method, url, body, headers):
        if method == "MKCALENDAR":
            made.append(body.decode())
            return 201, ""
        return 207, multistatus()

    with pytest.raises(CalendarError, match="not listed"):
        Calendars(server, ROOT, "A & B").live_shows()
    assert "<d:displayname>A &amp; B</d:displayname>" in made[0]


def test_uids_and_prodid_follow_the_page_address() -> None:
    assert calendar.uid_domain("https://shows.example/live/") == "shows.example"
    assert calendar.uid_domain("not a url") == "live-shows.invalid"
    ics = calendar.event_ics(
        show_record(show()), "u@x", "https://shows.example/", at("2026-10-20 07:00"), MELBOURNE
    )
    assert "PRODID:-//shows.example//live-shows//EN" in ics
