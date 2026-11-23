"""Shared test helpers: a fake web, saved pages, a home and some artists."""

from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from live_shows.model import Act, Artist, Show, Tier
from live_shows.names import normalise
from live_shows.settings import Place, Settings, from_environment

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 20)
UNTIL = date(2027, 10, 20)
MELBOURNE = ZoneInfo("Australia/Melbourne")
# Most saved pages are Melbourne's, so the tests live there.
PLACE = Place(MELBOURNE, -37.8136, 144.9631, 150, "AU", "VIC")
ADDRESS = "listener@example.com"
BASE_ENVIRONMENT = {
    "HOME_TIMEZONE": "Australia/Melbourne",
    "HOME_LATITUDE": "-37.8136",
    "HOME_LONGITUDE": "144.9631",
    "HOME_COUNTRY": "AU",
    "HOME_STATE": "VIC",
    "LIVE_SHOWS_URL": "https://shows.example/",
    "PLEX_URL": "http://plex",
    "PLEX_TOKEN": "TOKEN",
    "PLEX_MUSIC_SECTION": "3",
    "MAIL_PROVIDER": "fastmail",
    "MAIL_ADDRESS": ADDRESS,
}


def settings(**environment: str) -> Settings:
    """Settings from the base environment with `environment` over it ('' unsets a name)."""
    return from_environment({**BASE_ENVIRONMENT, **environment})


def fixture(name: str) -> str:
    """A saved page's text."""
    return (FIXTURES / name).read_text()


class FakeWeb:
    """A Fetcher answering from routes: the first route a URL contains wins.

    A route's answer is text, an exception to raise, or a function of the URL.
    """

    def __init__(self, routes: dict[str, str | Exception | Callable[[str], str]]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, dict[str, str] | None]] = []

    def text(self, url: str, headers: dict[str, str] | None = None) -> str:
        """The routed answer for `url`; an unrouted URL is a test error."""
        self.requests.append((url, headers))
        for part, answer in self.routes.items():
            if part in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer(url) if callable(answer) else answer
        raise AssertionError(f"unrouted request: {url}")


def artist(name: str, tier: Tier = Tier.LOVE, plays: int = 100, **fields) -> Artist:
    """An artist with a tier."""
    return Artist(name=name, key=normalise(name), tier=tier, plays=plays, **fields)


def at(text: str) -> datetime:
    """A Melbourne time from '2026-10-20 19:00'."""
    return datetime.fromisoformat(text).replace(tzinfo=MELBOURNE)


def show(
    day: str = "2026-10-20",
    acts: list[Artist] | None = None,
    venue: str = "170 Russell",
    is_festival: bool = False,
    on_sale_at: datetime | None = None,
    title: str = "",
) -> Show:
    """A matched show."""
    acts = acts or [artist("La Dispute")]
    return Show(
        day=date.fromisoformat(day),
        time="19:00",
        title=title or acts[0].name,
        venue=venue,
        url="https://tickets.example/1",
        acts=[Act(a, a.name) for a in acts],
        with_them=[] if is_festival else ["Special Features"],
        sources=["Moshtix"],
        event_ids=["mt:1"],
        is_festival=is_festival,
        on_sale_at=on_sale_at,
    )


@pytest.fixture
def web() -> Callable[..., FakeWeb]:
    """Make a FakeWeb from routes."""
    return FakeWeb
