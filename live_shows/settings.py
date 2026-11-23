"""The settings, read once from the environment; secrets are typed on the page instead.

Every name is listed with a comment in `.env.example`. A setting that is wrong or missing
stops the app at start with a message naming it, rather than failing a run later.
"""

import math
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from live_shows.names import normalise

AU_STATES = ("ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA")
LIBRARIES = ("plex", "lastfm")
EARTH_RADIUS_KM = 6371
FASTMAIL = {
    "SMTP_HOST": "smtp.fastmail.com",
    "SMTP_PORT": "587",
    "IMAP_HOST": "imap.fastmail.com",
    "CALDAV_URL": "https://caldav.fastmail.com/dav/calendars/user/{address}/",
}
PRESETS = {"fastmail": FASTMAIL}


class SettingsError(Exception):
    """A setting is missing or cannot be read."""


@dataclass(frozen=True)
class Place:
    """Where shows count as near: a home point, a radius, and the local calendar."""

    zone: ZoneInfo
    latitude: float
    longitude: float
    radius_km: float
    country: str
    state: str | None = None
    city: str | None = None

    @property
    def words(self) -> set[str]:
        """The home city's and state's words, which say nothing about which venue it is."""
        return set(normalise(f"{self.city or ''} {self.state or ''}").split())

    @property
    def in_australia(self) -> bool:
        """Whether the Australian sources apply."""
        return self.country == "AU"

    def near(self, latitude: float, longitude: float) -> bool:
        """Whether a point is within the radius of home, by great-circle distance."""
        lat1, lon1 = math.radians(self.latitude), math.radians(self.longitude)
        lat2, lon2 = math.radians(latitude), math.radians(longitude)
        haversine = (
            math.sin((lat2 - lat1) / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
        )
        return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(haversine)) <= self.radius_km


@dataclass(frozen=True)
class Plex:
    """The Plex server and whose listening on it counts."""

    url: str
    token: str | None
    preferences: Path | None
    account: str
    section: str | None


@dataclass(frozen=True)
class Smtp:
    """Where the emails go out, and who they go to."""

    host: str
    port: int
    user: str
    sender: str
    recipient: str


@dataclass(frozen=True)
class Imap:
    """The inbox read for ticket confirmations."""

    host: str
    user: str


@dataclass(frozen=True)
class CalDav:
    """The calendar home, and the calendars written to."""

    url: str
    user: str
    calendar: str
    tickets_calendar: str | None


@dataclass(frozen=True)
class Settings:
    """Everything the app reads from the environment."""

    database: Path
    app_url: str
    place: Place
    library: str
    plex: Plex
    lastfm_user: str | None
    smtp: Smtp | None
    imap: Imap | None
    caldav: CalDav | None
    festivals: Path | None
    password: str | None
    allowed_hosts: tuple[str, ...] = ()

    @property
    def hosts(self) -> set[str]:
        """The Host headers the page answers to: LIVE_SHOWS_URL's, then any others allowed."""
        return {urllib.parse.urlsplit(self.app_url).netloc.lower(), *self.allowed_hosts}


def from_environment(env: Mapping[str, str]) -> Settings:
    """The settings from `env`, with the mail preset filled in; raises SettingsError."""
    env = with_preset(env)

    def get(name: str) -> str | None:
        return env.get(name, "").strip() or None

    def get_or(name: str, default: str) -> str:
        return get(name) or default

    def need(name: str) -> str:
        value = get(name)
        if value is None:
            raise SettingsError(f"{name} is not set")
        return value

    library = get_or("LIBRARY", "plex")
    if library not in LIBRARIES:
        raise SettingsError(f"LIBRARY must be one of {', '.join(LIBRARIES)}, not {library}")
    plex = Plex(
        url=get_or("PLEX_URL", "http://plex:32400").rstrip("/"),
        token=get("PLEX_TOKEN"),
        preferences=_path(get("PLEX_PREFERENCES")),
        account=get_or("PLEX_ACCOUNT_ID", "1"),
        section=get("PLEX_MUSIC_SECTION"),
    )
    if library == "plex" and not (plex.token or plex.preferences):
        raise SettingsError("LIBRARY is plex but neither PLEX_TOKEN nor PLEX_PREFERENCES is set")
    lastfm_user = get("LASTFM_USER")
    if library == "lastfm" and not lastfm_user:
        raise SettingsError("LIBRARY is lastfm but LASTFM_USER is not set")

    address = get("MAIL_ADDRESS")
    smtp = imap = caldav = None
    if smtp_host := get("SMTP_HOST"):
        recipient = get("MAIL_TO") or address
        if recipient is None:
            raise SettingsError("SMTP_HOST is set but neither MAIL_TO nor MAIL_ADDRESS is")
        sender = get_or("MAIL_FROM", recipient)
        smtp = Smtp(
            host=smtp_host,
            port=_number(need, "SMTP_PORT", int) if get("SMTP_PORT") else 587,
            user=get("SMTP_USER") or address or sender,
            sender=sender,
            recipient=recipient,
        )
    if imap_host := get("IMAP_HOST"):
        imap = Imap(imap_host, _user(get("IMAP_USER") or address, "IMAP"))
    if caldav_url := get("CALDAV_URL"):
        caldav = CalDav(
            url=caldav_url if caldav_url.endswith("/") else caldav_url + "/",
            user=_user(get("CALDAV_USER") or address, "CALDAV"),
            calendar=get_or("CALDAV_CALENDAR", "Live shows"),
            tickets_calendar=get("CALDAV_TICKETS_CALENDAR"),
        )

    return Settings(
        database=Path(get_or("LIVE_SHOWS_DB", "/data/live-shows.sqlite")),
        app_url=get_or("LIVE_SHOWS_URL", "http://localhost:8080/"),
        place=_place(get, need),
        library=library,
        plex=plex,
        lastfm_user=lastfm_user,
        smtp=smtp,
        imap=imap,
        caldav=caldav,
        festivals=_path(get("FESTIVALS_FILE")),
        password=get("LIVE_SHOWS_PASSWORD"),
        allowed_hosts=tuple(
            host.strip().lower()
            for host in (get("LIVE_SHOWS_ALLOWED_HOSTS") or "").split(",")
            if host.strip()
        ),
    )


def with_preset(env: Mapping[str, str]) -> dict[str, str]:
    """`env` with a mail preset's hosts and URLs filled in where they are not set."""
    filled = dict(env)
    provider = filled.get("MAIL_PROVIDER", "").strip().lower()
    if not provider:
        return filled
    if provider not in PRESETS:
        raise SettingsError(f"MAIL_PROVIDER must be one of {', '.join(PRESETS)}, not {provider}")
    address = filled.get("MAIL_ADDRESS", "").strip()
    if not address:
        raise SettingsError(f"MAIL_PROVIDER is {provider} but MAIL_ADDRESS is not set")
    for name, value in PRESETS[provider].items():
        if not filled.get(name, "").strip():
            filled[name] = value.format(address=urllib.parse.quote(address))
    return filled


def _place(get: Callable[[str], str | None], need: Callable[[str], str]) -> Place:
    """The home point, radius, zone, country and state."""
    name = need("HOME_TIMEZONE")
    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise SettingsError(f"HOME_TIMEZONE is not a known time zone: {name}") from error
    country = need("HOME_COUNTRY").upper()
    state = (get("HOME_STATE") or "").upper() or None
    if country == "AU" and state not in AU_STATES:
        raise SettingsError(f"HOME_STATE must be one of {', '.join(AU_STATES)} in Australia")
    return Place(
        zone=zone,
        latitude=_number(need, "HOME_LATITUDE", float),
        longitude=_number(need, "HOME_LONGITUDE", float),
        radius_km=_number(need, "HOME_RADIUS_KM", float) if get("HOME_RADIUS_KM") else 150.0,
        country=country,
        state=state,
        city=get("HOME_CITY"),
    )


def _number[T: (int, float)](need: Callable[[str], str], name: str, kind: type[T]) -> T:
    """A setting read as a number."""
    text = need(name)
    try:
        return kind(text)
    except ValueError as error:
        raise SettingsError(f"{name} is not a number: {text}") from error


def _user(user: str | None, service: str) -> str:
    """The login for a service, which falls back to MAIL_ADDRESS."""
    if user is None:
        raise SettingsError(f"{service}_USER is not set, and nor is MAIL_ADDRESS")
    return user


def _path(value: str | None) -> Path | None:
    return Path(value) if value else None
