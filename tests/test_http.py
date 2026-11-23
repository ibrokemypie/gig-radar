import io
import json

import pytest

from live_shows import http


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def test_a_second_request_to_a_host_waits_out_the_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter([100.0, 100.0, 100.2, 100.2])
    slept: list[float] = []
    sent: list = []
    monkeypatch.setattr(http.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(http.time, "sleep", slept.append)

    def urlopen(request, timeout):
        sent.append((request.full_url, request.headers, timeout))
        return FakeResponse(b"body")

    monkeypatch.setattr(http.urllib.request, "urlopen", urlopen)
    fetcher = http.WebFetcher(pause_seconds=1.0)

    assert fetcher.text("https://a.example/1") == "body"
    assert fetcher.text("https://a.example/2", {"X-Key": "k"}) == "body"

    assert slept == [pytest.approx(0.8)]
    assert sent[1][1]["X-key"] == "k"
    assert sent[0][1]["User-agent"] == http.USER_AGENT


def test_json_of_asks_for_json_and_parses_it() -> None:
    class Fetcher:
        def text(self, url, headers=None):
            self.headers = headers
            return json.dumps({"ok": True})

    fetcher = Fetcher()
    assert http.json_of(fetcher, "https://a.example") == {"ok": True}
    assert fetcher.headers == {"Accept": "application/json"}


def test_apples_search_api_gets_its_own_longer_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter([100.0, 100.0, 100.5, 100.5])
    slept: list[float] = []
    monkeypatch.setattr(http.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(http.time, "sleep", slept.append)
    monkeypatch.setattr(http.urllib.request, "urlopen", lambda r, timeout: FakeResponse(b"{}"))
    fetcher = http.WebFetcher(pause_seconds=1.0)

    fetcher.text("https://itunes.apple.com/search?a")
    fetcher.text("https://itunes.apple.com/lookup?b")

    assert slept == [pytest.approx(http.HOST_PAUSES["itunes.apple.com"] - 0.5)]


def test_a_container_beside_this_one_is_never_paused_for(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(http.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(http.time, "sleep", slept.append)
    monkeypatch.setattr(http.urllib.request, "urlopen", lambda r, timeout: FakeResponse(b"{}"))
    fetcher = http.WebFetcher(pause_seconds=1.0)

    fetcher.text("http://plex:32400/status/sessions/history/all")
    fetcher.text("http://plex:32400/library/metadata/1/children")

    assert slept == []
