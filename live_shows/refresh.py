"""The daily run: the tiers, every source, the matched list, tickets, calendars and email.

A source that fails or comes back empty is recorded and fails the run (so a scheduler's
exit-status check shows it), but the rest still runs and still mails what it found.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from live_shows import calendar, lastfm, mailer, samples, tickets
from live_shows.http import Fetcher
from live_shows.library import (
    AlbumOrderFetcher,
    Listening,
    build_tiers,
    fewer_like,
    plex_music_section,
    read_plex,
)
from live_shows.matching import ArtistIndex, lineup_lookup, match_shows
from live_shows.model import Artist, Event
from live_shows.settings import Imap, Settings, Smtp
from live_shows.sources import bandsintown, lineups, moshtix, musick, oztix, ticketmaster
from live_shows.sources.lineups import LineupAct, LineupPage
from live_shows.store import CachedFetcher, Store

log = logging.getLogger(__name__)

WINDOW = timedelta(days=365)
SIMILAR_CACHE = timedelta(days=30)
MUSICK_PAGE_CACHE = timedelta(days=3)
SAMPLES_CACHE = timedelta(days=30)
ALBUM_ORDER_CACHE = timedelta(days=30)
DIGEST_WEEKDAY = 0  # Monday


@dataclass
class Services:
    """The outside world, so a test can replace each part."""

    fetcher: Fetcher
    now: Callable[[], datetime]
    smtp: Callable[..., Any] = mailer.smtplib.SMTP
    smtp_ssl: Callable[..., Any] = mailer.smtplib.SMTP_SSL
    imap: Callable[..., Any] = tickets.imaplib.IMAP4_SSL
    caldav: Callable[[str, str], calendar.Request] = calendar.web_request


@dataclass
class Outcome:
    """What a run did, for its record and the settings page."""

    failures: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    sent: list[str] = field(default_factory=list)


def plex_token(preferences: Path | None) -> str:
    """The server owner's token from Plex's Preferences.xml, when PLEX_TOKEN is not set."""
    if preferences is None:
        raise RuntimeError("no PLEX_TOKEN set")
    found = re.search(r'PlexOnlineToken="([^"]+)"', preferences.read_text())
    if not found:
        raise RuntimeError(f"no PlexOnlineToken in {preferences}")
    return found.group(1)


def read_listening(settings: Settings, services: Services, store: Store) -> Listening:
    """The listening from the library LIBRARY names."""
    if settings.library == "lastfm":
        key = store.secret("lastfm_key")
        if not key:
            raise RuntimeError("no Last.fm API key saved: set it in Settings on the page")
        return lastfm.read_lastfm(services.fetcher, settings.lastfm_user or "", key)
    plex = settings.plex
    token = plex.token or plex_token(plex.preferences)
    return read_plex(
        services.fetcher,
        plex.url,
        token,
        plex.account,
        plex.section or plex_music_section(services.fetcher, plex.url, token),
        CachedFetcher(AlbumOrderFetcher(services.fetcher), store, ALBUM_ORDER_CACHE),
    )


def gather_events(
    settings: Settings,
    services: Services,
    store: Store,
    artists: list[Artist],
    first_day: date,
    last_day: date,
    outcome: Outcome,
) -> list[Event]:
    """Every source's listings; a failing source is noted in `outcome` and skipped.

    Oztix, Moshtix and musick list Australian shows only, so they are read only there.
    """
    place = settings.place
    musick_pages = CachedFetcher(services.fetcher, store, MUSICK_PAGE_CACHE)
    readers: list[tuple[str, Callable[[], list[Event]]]] = [
        (
            bandsintown.NAME,
            lambda: bandsintown.fetch(services.fetcher, artists, place, first_day, last_day),
        ),
    ]
    if place.in_australia:
        readers += [
            (oztix.NAME, lambda: oztix.fetch(services.fetcher, place, first_day, last_day)),
            (moshtix.NAME, lambda: moshtix.fetch(services.fetcher, place, first_day, last_day)),
            (
                musick.NAME,
                lambda: musick.fetch(musick_pages, artists, place, first_day, last_day),
            ),
        ]
    if key := store.secret("ticketmaster_key"):
        readers.append(
            (
                ticketmaster.NAME,
                lambda: ticketmaster.fetch(services.fetcher, key, place, first_day, last_day),
            )
        )
    events = []
    for name, read in readers:
        try:
            found = read()
        except Exception as error:  # one source must not stop the others
            outcome.failures.append(f"{name}: {error}")
            continue
        outcome.counts[name] = len(found)
        events += found
    return events


def festival_lineups(
    settings: Settings, services: Services, outcome: Outcome
) -> Callable[[str], list[LineupAct] | None]:
    """A lineup reader for match_shows; a page that fails is noted and its festival falls back.

    Only the pages for the home country, and state where a page names one, are read.
    """
    place = settings.place

    def read(page: LineupPage) -> list[LineupAct] | None:
        try:
            return lineups.fetch(services.fetcher, page, place.state or "")
        except Exception as error:  # the sources' acts stand in
            outcome.failures.append(f"lineup {page.festival}: {error}")
            return None

    pages = [p for p in lineups.known_pages(settings.festivals) if p.applies_to(place)]
    return lineup_lookup(pages, read)


def fetch_samples(
    services: Services, store: Store, at: datetime, outcome: Outcome, country: str = "us"
) -> int:
    """Fetch samples for each undecided maybe on the list that has none
    younger than SAMPLES_CACHE; returns how many artists were fetched.

    Apple failing stops the step, noted in `outcome`, and keeps what was fetched.
    """
    decided = store.choices()
    held = store.samples()
    wanted = {
        act["key"]: act["name"]
        for show in store.shows()
        for act in show["acts"]
        if act["tier"] == "maybe" and act["key"] not in decided
    }
    fetched = 0
    for key, name in wanted.items():
        if key in held and at - datetime.fromisoformat(held[key]["fetched_at"]) < SAMPLES_CACHE:
            continue
        try:
            store.save_samples(key, samples.samples_for(services.fetcher, name, country))
        except Exception as error:  # Apple down: the page shows what it has
            outcome.failures.append(f"Apple samples: {error}")
            break
        fetched += 1
    return fetched


def mark_ticket_emails(
    imap: Imap, services: Services, store: Store, password: str, today: date
) -> int:
    """Mark bought each show a new seller email confirms; returns how many were marked."""
    shows = [s for s in store.shows() if not s["bought"]]
    found = tickets.read_confirmations(
        imap.host,
        imap.user,
        password,
        today,
        lambda message_id: store.was_sent("ticket-mail", message_id),
        services.imap,
    )
    marked = 0
    for message_id, message in found:
        for show in tickets.shows_in(message, shows):
            store.mark_bought([tuple(pair) for pair in show["pairs"]], "email")
            marked += 1
        store.mark_sent("ticket-mail", [message_id])
    return marked


def send_emails(
    smtp: Smtp,
    app_url: str,
    services: Services,
    store: Store,
    password: str,
    at: datetime,
    dry_run: bool,
) -> list[str]:
    """The morning email, and on Mondays the digest; returns the subjects sent."""
    shows = store.shows()
    who = (smtp.sender, smtp.recipient, app_url)
    drafts = [mailer.morning_email(shows, store.was_sent, at, *who)]
    if at.weekday() == DIGEST_WEEKDAY:
        drafts.append(mailer.digest_email(shows, store.was_sent, store.choices(), *who))
    subjects = []
    for draft in drafts:
        if draft is None:
            continue
        message, marks = draft
        if dry_run:
            log.info("would send: %s\n%s", message["Subject"], message.get_body(("plain",)))
        else:
            mailer.send(message, smtp, password, services.smtp, services.smtp_ssl)
            for kind, refs in marks:
                store.mark_sent(kind, refs)
        subjects.append(str(message["Subject"]))
    return subjects


def refresh(settings: Settings, services: Services, store: Store, dry_run: bool = False) -> Outcome:
    """One whole run; the outcome lists every failure, and the run is recorded in the store."""
    run_id = store.start_run()
    outcome = Outcome()
    at = services.now()
    today = at.date()
    try:
        _refresh(settings, services, store, outcome, at, today, dry_run)
    except Exception as error:  # recorded, then the caller exits non-zero
        log.exception("run failed")
        outcome.failures.append(f"run: {error}")
    store.finish_run(run_id, not outcome.failures, outcome.__dict__)
    return outcome


def _refresh(
    settings: Settings,
    services: Services,
    store: Store,
    outcome: Outcome,
    at: datetime,
    today: date,
    dry_run: bool,
) -> None:
    """The run's steps, in order, each later one using what the earlier ones stored."""
    listening = read_listening(settings, services, store)
    choices = store.choices()
    kept = {key: choice for key, choice in choices.items() if choice["choice"] == "kept"}
    tiers = build_tiers(
        listening,
        CachedFetcher(services.fetcher, store, SIMILAR_CACHE),
        fewer_like(choices, store.unlearned()),
        kept,
    )
    artists = sorted(tiers.values(), key=lambda artist: (artist.tier.value, -artist.plays))
    outcome.counts["artists"] = len(artists)

    events = gather_events(settings, services, store, artists, today, today + WINDOW, outcome)
    shows = match_shows(
        events,
        ArtistIndex(tiers),
        festival_lineups(settings, services, outcome),
        settings.place.words,
    )
    new = store.record_shows(shows, at)
    outcome.counts.update(shows=len(shows), new=len(new))
    outcome.counts["sampled"] = fetch_samples(
        services, store, at, outcome, settings.place.country.lower()
    )

    run_mail_steps(settings, services, store, outcome, at, dry_run)


def run_mail_steps(
    settings: Settings,
    services: Services,
    store: Store,
    outcome: Outcome,
    at: datetime,
    dry_run: bool,
) -> None:
    """Ticket emails, the calendars and the emails: each one set up and with a password."""
    if settings.imap and (password := store.password("imap")):
        outcome.counts["tickets_from_email"] = mark_ticket_emails(
            settings.imap, services, store, password, at.date()
        )
    if settings.caldav and (password := store.password("caldav")) and not dry_run:
        dav = settings.caldav
        calendars = calendar.Calendars(
            services.caldav(dav.user, password), dav.url, dav.calendar, dav.tickets_calendar
        )
        try:
            outcome.counts.update(
                calendar.sync(
                    calendars,
                    store,
                    store.shows(),
                    settings.app_url,
                    at.date(),
                    at,
                    settings.place.zone,
                )
            )
        except calendar.CalendarError as error:  # the morning email still goes out
            outcome.failures.append(f"calendar: {error}")
    if settings.smtp and (password := store.password("smtp")):
        outcome.sent = send_emails(
            settings.smtp, settings.app_url, services, store, password, at, dry_run
        )
