"""The one way the app reaches the web, so tests can stand a fake in its place."""

import json
import time
import urllib.parse
import urllib.request
from typing import Any, Protocol

USER_AGENT = "Mozilla/5.0 (live-shows; self-hosted)"
# Apple asks Search API callers for about 20 calls a minute.
HOST_PAUSES = {"itunes.apple.com": 3.1}


class Fetcher(Protocol):
    """Gets a page's text; the sources and the library readers take one of these."""

    def text(self, url: str, headers: dict[str, str] | None = None) -> str:
        """The body of a GET of `url`."""
        ...


class WebFetcher:
    """Fetches over the network with a browser-like agent, pausing between requests to a host."""

    def __init__(self, pause_seconds: float = 1.0, timeout_seconds: float = 30.0) -> None:
        self.pause_seconds = pause_seconds
        self.timeout_seconds = timeout_seconds
        self._last_request_at: dict[str, float] = {}

    def text(self, url: str, headers: dict[str, str] | None = None) -> str:
        """The body of a GET of `url`, at most one request a pause per host.

        The pause is `pause_seconds`, or the host's own in HOST_PAUSES where that is longer;
        none for a host with no dot in its name, a container beside this one (Plex).
        """
        parts = urllib.parse.urlparse(url)
        host = parts.netloc
        pause = max(self.pause_seconds, HOST_PAUSES.get(host, 0.0))
        if "." not in (parts.hostname or ""):
            pause = 0.0
        wait = self._last_request_at.get(host, 0.0) + pause - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "*/*", **(headers or {})}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read().decode("utf-8", errors="replace")
        finally:
            self._last_request_at[host] = time.monotonic()


def json_of(fetcher: Fetcher, url: str, headers: dict[str, str] | None = None) -> Any:
    """The parsed JSON body of a GET of `url`."""
    return json.loads(fetcher.text(url, {"Accept": "application/json", **(headers or {})}))
