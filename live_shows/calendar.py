"""The calendars over CalDAV: loved shows on "Live shows", ticketed ones on a second one.

The "Live shows" calendar (its name is a setting) holds every loved show and every show
with tickets; the tickets calendar (by default the account's main one) holds only the
shows with tickets. A maybe show is on neither unless it has tickets. Only events this app
wrote are ever changed or deleted, and a show that has passed is left where it is.
"""

import base64
import hashlib
import html
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any
from xml.etree import ElementTree

from live_shows.model import web_url
from live_shows.store import Store

LIVE_SHOWS_SLUG = "live-shows"
# Fastmail refuses an event whose uid is already in another of the account's calendars
# (403 unique-scheduling-object-resource), so the tickets calendar's copy has its own.
TICKETS_PREFIX = "tickets."
GIG_LENGTH = timedelta(hours=3)
_NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}

Request = Callable[[str, str, bytes | None, dict[str, str]], tuple[int, str]]


class CalendarError(RuntimeError):
    """The CalDAV server refused a request, or could not be reached.

    From sync, `counts` holds what the calendars that did sync wrote and removed.
    """

    def __init__(self, message: str, counts: dict[str, int] | None = None) -> None:
        super().__init__(message)
        self.counts = counts or {}


@dataclass(frozen=True)
class Collection:
    """A calendar on the server."""

    href: str
    name: str


def web_request(user: str, password: str) -> Request:
    """A CalDAV request function signed in with HTTP basic auth."""
    token = base64.b64encode(f"{user}:{password}".encode()).decode()

    def request(
        method: str, url: str, body: bytes | None, headers: dict[str, str]
    ) -> tuple[int, str]:
        req = urllib.request.Request(
            url, data=body, method=method, headers={"Authorization": f"Basic {token}", **headers}
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return response.status, response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8", errors="replace")
        except OSError as error:  # unreachable, refused, timed out, a bad certificate
            raise CalendarError(f"{method} {url}: {error}") from error

    return request


class Calendars:
    """A calendar home: finding, making and writing to the two calendars this app uses.

    `root` is the calendar home's URL; `name` the calendar this app makes and fills;
    `tickets_name` the calendar for shows with tickets, or None for the account's default.
    """

    def __init__(
        self, request: Request, root: str, name: str = "Live shows", tickets_name: str | None = None
    ) -> None:
        self.request = request
        self.root = root
        self.name = name
        self.tickets_name = tickets_name
        self.origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(self.root))

    def collections(self) -> list[Collection]:
        """Every calendar in the home, by href and display name."""
        body = (
            b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:displayname/>'
            b"<d:resourcetype/></d:prop></d:propfind>"
        )
        tree = self._xml("PROPFIND", self.root, body, {"Depth": "1"})
        found = []
        for response in tree.iterfind("d:response", _NS):
            if response.find(".//d:resourcetype/c:calendar", _NS) is None:
                continue
            found.append(
                Collection(
                    href=response.findtext("d:href", "", _NS),
                    name=response.findtext(".//d:displayname", "", _NS),
                )
            )
        return found

    def live_shows(self) -> Collection:
        """This app's calendar, made the first time it is needed.

        Always the href the server lists, which may spell the address with "@" where the
        request quoted it as "%40": the store keys its rows by that href.
        """
        existing = self._find_live_shows()
        if existing:
            return existing
        href = urllib.parse.urlparse(self.root).path + f"{LIVE_SHOWS_SLUG}/"
        body = (
            '<?xml version="1.0"?><c:mkcalendar xmlns:d="DAV:" '
            'xmlns:c="urn:ietf:params:xml:ns:caldav"><d:set><d:prop>'
            f"<d:displayname>{html.escape(self.name)}</d:displayname></d:prop></d:set></c:mkcalendar>"
        ).encode()
        status, text = self.request("MKCALENDAR", self.origin + href, body, _XML)
        if status not in (200, 201):
            raise CalendarError(f"MKCALENDAR {href}: {status} {text[:200]}")
        made = self._find_live_shows()
        if made is None:
            raise CalendarError(f"MKCALENDAR {href} succeeded but the calendar is not listed")
        return made

    def _find_live_shows(self) -> Collection | None:
        return next((c for c in self.collections() if c.name == self.name), None)

    def tickets(self) -> Collection:
        """The calendar for shows with tickets: the one named, else the account's default.

        The default is read from the scheduling inbox (`Inbox/` under the home), as
        Fastmail serves it; a server without one needs the calendar named.
        """
        if self.tickets_name is not None:
            collections = self.collections()
            named = next((c for c in collections if c.name == self.tickets_name), None)
            if named is None:
                raise CalendarError(
                    f"no calendar named {self.tickets_name} among {[c.name for c in collections]}"
                )
            return named
        body = (
            b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:" '
            b'xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop>'
            b"<c:schedule-default-calendar-URL/></d:prop></d:propfind>"
        )
        tree = self._xml("PROPFIND", self.root + "Inbox/", body, {"Depth": "0"})
        default = tree.findtext(".//c:schedule-default-calendar-URL/d:href", "", _NS)
        collections = self.collections()
        found = next((c for c in collections if c.href == default), None)
        if found is None:
            raise CalendarError(f"no default calendar among {[c.name for c in collections]}")
        return found

    def put(self, collection: Collection, uid: str, ics: str) -> None:
        """Write one event, replacing what was there under its uid."""
        url = self.origin + collection.href + f"{uid}.ics"
        status, text = self.request(
            "PUT", url, ics.encode(), {"Content-Type": "text/calendar; charset=utf-8"}
        )
        if status not in (200, 201, 204):
            raise CalendarError(f"PUT {uid}: {status} {text[:200]}")

    def delete(self, collection: Collection, uid: str) -> None:
        """Remove one event this app wrote; one already gone is fine."""
        status, text = self.request(
            "DELETE", self.origin + collection.href + f"{uid}.ics", None, {}
        )
        if status not in (200, 204, 404):
            raise CalendarError(f"DELETE {uid}: {status} {text[:200]}")

    def _xml(
        self, method: str, url: str, body: bytes, headers: dict[str, str]
    ) -> ElementTree.Element:
        status, text = self.request(method, url, body, {**_XML, **headers})
        if status != 207:
            raise CalendarError(f"{method} {url}: {status} {text[:200]}")
        try:
            return ElementTree.fromstring(text)
        except ElementTree.ParseError as error:
            raise CalendarError(f"{method} {url}: not XML: {error}") from error


_XML = {"Content-Type": "application/xml; charset=utf-8"}


def uid_domain(app_url: str) -> str:
    """The domain event uids end in: the page's host."""
    return urllib.parse.urlparse(app_url).hostname or "live-shows.invalid"


def event_uid(show: dict[str, Any], domain: str) -> str:
    """The event's uid in one calendar: the show's id, which leads with its day."""
    return f"{show['show_id']}@{domain}"


def event_ics(show: dict[str, Any], uid: str, app_url: str, stamp: datetime, zone: tzinfo) -> str:
    """One show as an iCalendar event: timed for a gig, all day for a festival."""
    names = " + ".join(act["name"] for act in show["acts"])
    summary = show["title"] if show["is_festival"] else f"{names} at {show['venue']}"
    description = "\n".join(
        [
            *(f"{act['name']}: {act['why']}" for act in show["acts"]),
            f"Tickets: {web_url(show['url'])}",
            f"{app_url}#{show['show_id']}",
        ]
    )
    day = date.fromisoformat(show["day"])
    if show["is_festival"]:
        when = [
            f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
            f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
        ]
    else:
        start = datetime.fromisoformat(f"{show['day']}T{show['time']}").replace(tzinfo=zone)
        when = [f"DTSTART:{_utc(start)}", f"DTEND:{_utc(start + GIG_LENGTH)}"]
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{uid_domain(app_url)}//live-shows//EN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_utc(stamp)}",
        *when,
        f"SUMMARY:{_escape(summary)}",
        f"LOCATION:{_escape(show['venue'])}",
        f"DESCRIPTION:{_escape(description)}",
        f"URL:{web_url(show['url'])}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def sync(
    calendars: Calendars,
    store: Store,
    shows: list[dict[str, Any]],
    app_url: str,
    today: date,
    stamp: datetime,
    zone: tzinfo,
) -> dict[str, int]:
    """Bring both calendars in line with the list; returns the events written and removed.

    Each calendar syncs on its own: when one fails, the other still syncs, and then a
    CalendarError names every failure and carries the counts. "Live shows" holds the
    loved shows and those with tickets, and loses a future show that is no longer listed
    (cancelled) or no longer loved. The tickets calendar loses a show only when Got
    tickets is taken back, never because a seller stopped listing it.
    """
    counts = {"written": 0, "removed": 0}
    shows_domain = uid_domain(app_url)
    tickets_domain = TICKETS_PREFIX + shows_domain
    not_bought = {event_uid(s, tickets_domain) for s in shows if not s["bought"]}
    loved_or_bought = [s for s in shows if s["tier"] == "love" or s["bought"]]
    targets = [
        ("shows", calendars.live_shows, shows_domain, loved_or_bought, None),
        ("tickets", calendars.tickets, tickets_domain, [s for s in shows if s["bought"]],
         not_bought),
    ]  # fmt: skip
    errors = []
    for name, find, domain, wanted, removable in targets:
        try:
            _sync_one(calendars, store, find(), domain, wanted, removable, counts, app_url, today,
                      stamp, zone)  # fmt: skip
        except CalendarError as error:
            errors.append(f"{name} calendar: {error}")
    if errors:
        raise CalendarError("; ".join(errors), counts)
    return counts


def _sync_one(
    calendars: Calendars,
    store: Store,
    collection: Collection,
    domain: str,
    wanted: list[dict[str, Any]],
    removable: set[str] | None,
    counts: dict[str, int],
    app_url: str,
    today: date,
    stamp: datetime,
    zone: tzinfo,
) -> None:
    """Bring one calendar in line with the shows it should hold, adding to `counts`."""
    store.adopt_calendar_entries(collection.href)
    known = store.calendar_entries(collection.href)
    for show in wanted:
        uid = event_uid(show, domain)
        ics = event_ics(show, uid, app_url, stamp, zone)
        digest = hashlib.sha256(_without_stamp(ics).encode()).hexdigest()
        if known.get(uid) != digest:
            calendars.put(collection, uid, ics)
            store.set_calendar_entry(collection.href, uid, digest)
            counts["written"] += 1
    wanted_uids = {event_uid(show, domain) for show in wanted}
    for uid in known.keys() - wanted_uids:
        if date.fromisoformat(uid[:10]) < today or (removable is not None and uid not in removable):
            continue
        calendars.delete(collection, uid)
        store.set_calendar_entry(collection.href, uid, None)
        counts["removed"] += 1


def _utc(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _escape(text: str) -> str:
    """Text as an iCalendar value: backslash, semicolon, comma and line breaks escaped.

    A CR or CRLF from a scraped title becomes \\n too, so no text can start a property.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,").replace("\n", r"\n")


def _fold(line: str) -> str:
    """A content line folded at 74 characters, as iCalendar requires."""
    chunks = [line[i : i + 74] for i in range(0, len(line), 74)] or [""]
    return "\r\n ".join(chunks)


def _without_stamp(ics: str) -> str:
    """An event without its DTSTAMP, so an unchanged show is not rewritten every run."""
    return re.sub(r"DTSTAMP:\S+\r\n", "", ics)
