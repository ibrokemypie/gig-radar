import http.client
import threading
from datetime import date

import pytest
from conftest import MELBOURNE, artist, at, settings, show

from live_shows import web
from live_shows.model import Tier
from live_shows.store import SECRETS, Store, show_record

TODAY = date(2026, 10, 20)
SETTINGS = settings()
NONE_SAVED = dict.fromkeys(SECRETS, False)


@pytest.fixture
def store(tmp_path) -> Store:
    store = Store(tmp_path / "db.sqlite")
    store.record_shows(
        [
            show(),
            show("2026-11-08", [artist("Dance Gavin Dance"), artist("The Fall of Troy", Tier.MAYBE)]),
            show("2026-12-04", [artist("Neck Deep"), artist("Spiritbox", Tier.MAYBE)],
                 venue="Flemington Racecourse", is_festival=True, title="Good Things Festival"),
            show("2027-02-19", [artist("Spiritbox", Tier.MAYBE)], on_sale_at=at("2026-10-24 10:00"),
                 title="Spiritbox"),
            show("2027-02-20", [artist("Laneway Act")], venue="Flemington Park", is_festival=True,
                 title="Laneway Festival"),
        ],
        at("2026-10-20 07:00"),
    )  # fmt: skip
    return store


@pytest.fixture
def server(store, monkeypatch):
    monkeypatch.setattr(web.Handler, "today", lambda self: TODAY)
    server = web.make_server(store, SETTINGS, 0, "127.0.0.1")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def request(
    server, method: str, path: str, form: str = "", headers: dict[str, str] | None = None
) -> http.client.HTTPResponse:
    """A request to the server; a POST comes from the page unless `headers` say otherwise."""
    host = f"127.0.0.1:{server.server_address[1]}"
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
    sent = {}
    if method == "POST":
        sent = {"Content-Type": "application/x-www-form-urlencoded", "Origin": f"http://{host}"}
    connection.request(method, path, body=form or None, headers={**sent, **(headers or {})})
    return connection.getresponse()


def test_the_page_lists_shows_by_month_with_tiers_and_reasons(store: Store) -> None:
    html = web.page(store.shows(), store.secret_status(), None, TODAY)

    assert html.index("<h2>October</h2>") < html.index("<h2>November</h2>")
    assert "<h2>February 2027</h2>" in html
    assert "Tonight · 170 Russell" in html
    assert "Dance Gavin Dance + The Fall of Troy" in html
    assert "Dance Gavin Dance: 100 plays · 0 songs on repeat" in html
    assert '<span class="chip fest">Festival</span>' in html
    assert "<b>Neck Deep</b> (100 plays · 0 songs on repeat) · 1 you might like: Spiritbox" in html
    assert "On sale Sat 24 Oct, 10:00" in html
    assert "Tickets on tickets.example ↗" in html
    assert "<b>Laneway Act</b> (100 plays · 0 songs on repeat)</div>" in html
    assert html.count("<h2>February 2027</h2>") == 1
    assert "Not checked yet" in html
    assert "Settings</h2>" not in html


def test_an_empty_list_says_so_and_a_run_reports_its_failures(tmp_path) -> None:
    run = {"started_at": "2026-10-19T07:00:00+11:00", "finished_at": None,
           "summary": {"failures": ["Oztix: down"]}}  # fmt: skip
    html = web.page([], NONE_SAVED, run, TODAY, notes=["No emails <yet>."], zone=MELBOURNE)
    assert "No shows found yet." in html
    assert "Checked Mon 19 Oct at 07:00." in html
    assert "Couldn't read Oztix: down" in html
    assert "No emails &lt;yet&gt;." in html


def test_settings_show_saved_or_not_set_and_never_the_value(store: Store) -> None:
    store.set_secret("mail_password", "hunter2")
    html = web.page(store.shows(), store.secret_status(), None, TODAY, settings_open=True)
    assert "hunter2" not in html
    assert '<span class="state saved">Saved</span>' in html
    assert '<span class="state unset">Not set</span>' in html
    assert "?settings=1&amp;replace=mail_password" in html
    assert "Close settings" in html

    replacing = web.page(store.shows(), store.secret_status(), None, TODAY, True, "mail_password")
    assert replacing.count('type="password"') == len(SECRETS)
    assert 'href="?settings=1">Cancel' in replacing


def test_got_tickets_moves_a_show_to_youre_going_and_not_going_undoes_it(server, store) -> None:
    response = request(server, "POST", "/tickets", "show_id=2026-10-20~la-dispute&action=got")
    assert response.status == 303
    assert response.getheader("Location") == "./#2026-10-20~la-dispute"
    assert store.shows()[0]["bought"]

    page = request(server, "GET", "/").read().decode()
    going = page[page.index("You're going") : page.index("<h2>")]
    assert "La Dispute" in going and "Not going" in going

    request(server, "POST", "/tickets", "show_id=2026-10-20~la-dispute&action=undo")
    assert not store.shows()[0]["bought"]


def test_settings_save_replace_and_clear_through_the_forms(server, store) -> None:
    response = request(server, "POST", "/settings", "name=ticketmaster_key&action=save&value=+K1+")
    assert response.status == 303
    assert response.getheader("Location") == "./?settings=1"
    assert store.secret("ticketmaster_key") == "K1"

    request(server, "POST", "/settings", "name=ticketmaster_key&action=clear")
    assert store.secret("ticketmaster_key") is None


def test_bad_requests_change_nothing(server, store) -> None:
    for path, form in [
        ("/tickets", "show_id=nope&action=got"),
        ("/tickets", "show_id=2026-10-20~la-dispute&action=sell"),
        ("/settings", "name=plex&action=save&value=x"),
        ("/settings", "name=ticketmaster_key&action=save&value=+"),
    ]:
        assert request(server, "POST", path, form).status == 400
    assert request(server, "POST", "/elsewhere", "").status == 404
    assert request(server, "GET", "/elsewhere").status == 404
    assert not any(record["bought"] for record in store.shows())
    assert store.secret_status() == NONE_SAVED


def test_health_and_the_settings_page_are_served(server) -> None:
    assert request(server, "GET", "/healthz").read() == b"ok"
    page = request(server, "GET", "/?settings=1&replace=bogus").read().decode()
    assert "<h2>Settings</h2>" in page
    assert page.count('type="password"') == len(SECRETS)


def test_checked_today_reads_as_today(monkeypatch) -> None:
    now = at("2026-10-20 09:00")
    monkeypatch.setattr(
        web, "datetime", type("Fixed", (web.datetime,), {"now": staticmethod(lambda tz=None: now)})
    )
    run = {"started_at": "2026-10-20T07:02:00+11:00", "finished_at": "2026-10-20T07:12:00+11:00",
           "summary": {}}  # fmt: skip
    html = web.page([], NONE_SAVED, run, TODAY, zone=MELBOURNE)
    assert "Checked today at 07:12." in html


def test_serve_starts_the_server(monkeypatch, tmp_path) -> None:
    started = []

    class FakeServer:
        def serve_forever(self):
            started.append(True)

    monkeypatch.setattr(web, "make_server", lambda store, settings, port: FakeServer())
    web.serve(Store(tmp_path / "db.sqlite"), SETTINGS, 8080)
    assert started == [True]


def found(name: str):
    """A maybe artist that similarity alone found, through La Dispute and Neck Deep."""
    return artist(name, Tier.MAYBE, plays=0, similar_to=["La Dispute", "Neck Deep"])


def records(*shows) -> list[dict]:
    return [{**show_record(s), "bought": False} for s in shows]


def kept(name: str) -> dict:
    return {"name": name, "choice": "kept", "seeds": ["La Dispute"], "at": "2026-11-03"}


def trimmed(name: str) -> dict:
    return {"name": name, "choice": "trimmed", "seeds": ["La Dispute"], "at": "2026-11-03"}


def test_a_trimmed_show_folds_under_its_month(store: Store) -> None:
    shows = records(
        show("2026-11-01", [found("Telenova")]),
        show("2026-11-02", [artist("Neck Deep")]),
        show("2026-11-03", [found("Spiritbox")]),
        show("2026-12-01", [found("Polaris")]),
    )
    sampling = web.Sampling({"telenova": trimmed("Telenova"), "polaris": trimmed("Polaris")})

    html = web.page(shows, store.secret_status(), None, TODAY, sampling=sampling)

    november = html[html.index("<h2>November</h2>") : html.index("<h2>December</h2>")]
    assert november.index('id="2026-11-02~neck-deep"') < november.index('id="2026-11-03~spiritbox"')
    assert november.index('id="2026-11-03~spiritbox"') < november.index(">Trimmed</div>")
    assert '<div class="ls-trimmed" id="2026-11-01~telenova">Telenova · Sun 1 Nov ' in november
    assert 'value="bring-back">Bring back</button>' in november
    december = html[html.index("<h2>December</h2>") :]
    assert 'class="ls-trimmed" id="2026-12-01~polaris"' in december
    assert "<details" not in december


def test_an_undecided_maybe_offers_previews_keep_and_not_for_me(store: Store) -> None:
    shows = records(show("2026-11-01", [found("Telenova")]))
    record = {"apple": "https://music.apple.com/au/artist/telenova/1", "kind": "songs",
              "items": [{"name": "Tremors", "art": "https://art/1.jpg",
                         "preview": "https://audio/1.m4a", "url": "https://music/1"}]}  # fmt: skip
    sampling = web.Sampling(samples={"telenova": record})

    html = web.page(shows, store.secret_status(), None, TODAY, sampling=sampling)

    assert "Tap for time, samples, sale and tickets" in html
    assert 'data-preview="https://audio/1.m4a" data-kind="audio"' in html
    assert 'value="keep">Keep</button>' in html and 'value="trim">Not for me</button>' in html
    assert 'name="key" value="telenova"' in html
    assert 'class="ls-who"' not in html
    assert '<span class="chip kept">' not in html


def test_a_kept_maybe_shows_kept_with_undo_and_no_samples(store: Store) -> None:
    shows = records(show("2026-11-01", [found("Telenova")]))
    sampling = web.Sampling({"telenova": kept("Telenova")})

    html = web.page(shows, store.secret_status(), None, TODAY, sampling=sampling)

    assert '<span class="chip kept">Kept</span>' in html
    assert "You kept Telenova." in html and 'value="undo">Undo keep</button>' in html
    assert "Tap for time, sale and tickets" in html
    assert 'value="keep"' not in html


def test_a_bill_of_several_names_each_act_left_to_decide(store: Store) -> None:
    bill = show("2026-11-01", [artist("Neck Deep"), found("Telenova"), found("Polaris")])
    sampling = web.Sampling({"polaris": trimmed("Polaris")})

    html = web.page(records(bill), store.secret_status(), None, TODAY, sampling=sampling)

    assert '<div class="ls-who">Telenova</div>' in html
    assert '"ls-who">Polaris' not in html and 'class="ls-trimmed"' not in html


def test_a_love_show_has_no_samples_to_offer(store: Store) -> None:
    html = web.page(records(show()), store.secret_status(), None, TODAY)
    assert 'class="ls-samples"' not in html
    assert "Tap for time, sale and tickets" in html


def test_previews_are_videos_songs_a_link_or_a_note() -> None:
    video = {"name": "Speed <or> Perish", "art": "https://art/v.jpg",
             "preview": "https://video/v.m4v", "url": "https://music/v"}  # fmt: skip
    videos = web._previews({"apple": "https://apple", "kind": "videos", "items": [video]})
    assert 'data-preview="https://video/v.m4v" data-kind="video"' in videos
    assert 'class="ls-vid"' in videos and "▶" in videos and "video preview" in videos
    assert "Speed &lt;or&gt; Perish" in videos

    songs = web._previews({"apple": "https://apple", "kind": "songs", "items": [video]})
    assert 'class="ls-vid song"' in songs and "♪" in songs and "song preview" in songs

    assert web._previews(None) == ""
    assert ">No previews · Apple Music ↗</a>" in web._previews(
        {"apple": "https://apple", "kind": "none", "items": []}
    )
    assert web._previews({"apple": None, "kind": "none", "items": []}) == (
        '<div class="hint">Not on Apple Music</div>'
    )


def test_a_loved_artist_finding_fewer_says_so_with_undo(store: Store) -> None:
    sampling = web.Sampling(fewer_like={"La Dispute": 3})
    html = web.page([], store.secret_status(), None, TODAY, sampling=sampling)
    assert "Finding fewer artists like <b>La Dispute</b>: you trimmed 3 found" in html
    assert 'action="learning"' in html and 'name="seed" value="La Dispute"' in html


def test_keep_trim_undo_and_bring_back_through_the_forms(server, store) -> None:
    troy = "show_id=2026-11-08~dance-gavin-dance&key=fall+of+troy"
    response = request(server, "POST", "/artists", f"{troy}&action=keep")
    assert response.status == 303
    assert response.getheader("Location") == "./#2026-11-08~dance-gavin-dance"
    assert store.choices()["fall of troy"]["choice"] == "kept"
    assert "You kept The Fall of Troy." in request(server, "GET", "/").read().decode()

    request(server, "POST", "/artists", f"{troy}&action=undo")
    assert store.choices() == {}

    request(server, "POST", "/artists", f"{troy}&action=trim")
    assert store.choices()["fall of troy"]["choice"] == "trimmed"

    spiritbox = "show_id=2027-02-19~spiritbox"
    request(server, "POST", "/artists", f"{spiritbox}&key=spiritbox&action=trim")
    page = request(server, "GET", "/").read().decode()
    assert 'class="ls-trimmed" id="2027-02-19~spiritbox"' in page
    response = request(server, "POST", "/artists", f"{spiritbox}&action=bring-back")
    assert response.getheader("Location") == "./#2027-02-19~spiritbox"
    assert list(store.choices()) == ["fall of troy"]


def test_bad_artist_requests_change_nothing(server, store) -> None:
    for form in [
        "show_id=nope&key=spiritbox&action=keep",
        "show_id=2027-02-19~spiritbox&key=spiritbox&action=love",
        "show_id=2026-11-08~dance-gavin-dance&key=dance+gavin+dance&action=keep",
        "show_id=2026-11-08~dance-gavin-dance&key=spiritbox&action=keep",
        "show_id=2026-11-08~dance-gavin-dance&action=trim",
    ]:
        assert request(server, "POST", "/artists", form).status == 400
    assert store.choices() == {}


def test_undoing_learning_only_for_a_loved_artist_finding_fewer(server, store) -> None:
    for name in ("One", "Two", "Three"):
        store.set_choice(name.lower(), name, "trimmed", ["La Dispute"])
    assert "Finding fewer artists like" in request(server, "GET", "/").read().decode()

    assert request(server, "POST", "/learning", "seed=La+Dispute&action=redo").status == 400
    assert request(server, "POST", "/learning", "seed=Neck+Deep&action=undo").status == 400
    assert store.unlearned() == {}

    response = request(server, "POST", "/learning", "seed=La+Dispute&action=undo")
    assert response.status == 303
    assert response.getheader("Location") == "./"
    assert list(store.unlearned()) == ["La Dispute"]
    assert request(server, "POST", "/learning", "seed=La+Dispute&action=undo").status == 400


def test_pinned_shows_sit_above_the_months_with_their_reason(tmp_path) -> None:
    store = Store(tmp_path / "db.sqlite")
    pinned = artist("Sleep Token", pinned=True, albums_front_to_back=2, plays=40)
    store.record_shows(
        [show("2027-03-01", [pinned]), show("2026-10-20", [artist("Neck Deep")])],
        at("2026-10-20 07:00"),
    )
    html = web.page(store.shows(), store.secret_status(), None, TODAY)

    assert html.index("<h2>Pinned</h2>") < html.index("Sleep Token") < html.index("<h2>October")
    assert "March 2027" not in html
    assert '<span class="chip pin">Pinned</span>' in html
    assert "2 albums front to back · 40 plays" in html


def test_only_pinned_shows_leave_no_empty_note(tmp_path) -> None:
    store = Store(tmp_path / "db.sqlite")
    pinned = artist("Sleep Token", pinned=True, hearts=7)
    store.record_shows([show(acts=[pinned])], at("2026-10-20 07:00"))
    html = web.page(store.shows(), store.secret_status(), None, TODAY)
    assert "<h2>Pinned</h2>" in html
    assert "No shows found yet." not in html
    assert "hearted 7 tracks · 100 plays" in html


def test_setup_notes_name_each_secret_still_missing() -> None:
    lastfm = settings(LIBRARY="lastfm", LASTFM_USER="listener")
    assert web.setup_notes(lastfm, NONE_SAVED) == [
        "No artists until the Last.fm API key is set in Settings.",
        "No emails until the mail password is set in Settings.",
        "No ticket emails read until the mail password is set in Settings.",
        "No calendar until the mail password is set in Settings.",
    ]
    own = {**NONE_SAVED, "lastfm_key": True, "smtp_password": True}
    assert web.setup_notes(lastfm, own) == [
        "No ticket emails read until the mail password is set in Settings.",
        "No calendar until the mail password is set in Settings.",
    ]
    assert web.setup_notes(SETTINGS, {**NONE_SAVED, "mail_password": True}) == []
    assert web.setup_notes(settings(MAIL_PROVIDER=""), NONE_SAVED) == []


def test_the_page_shows_what_is_not_set_up(server) -> None:
    page = request(server, "GET", "/").read().decode()
    assert "No emails until the mail password is set in Settings." in page


def test_a_post_from_elsewhere_is_refused(server, store) -> None:
    form = "show_id=2026-10-20~la-dispute&action=got"
    for headers in [
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Origin": "", "Referer": "https://evil.example/page"},
        {"Origin": ""},
    ]:
        assert request(server, "POST", "/tickets", form, headers).status == 403
    assert not store.shows()[0]["bought"]
    referer = {"Origin": "", "Referer": "https://shows.example/?settings=1"}
    assert request(server, "POST", "/tickets", form, referer).status == 303
    assert store.shows()[0]["bought"]


@pytest.fixture
def locked(store, monkeypatch):
    monkeypatch.setattr(web.Handler, "today", lambda self: TODAY)
    server = web.make_server(store, settings(LIVE_SHOWS_PASSWORD="opensesame"), 0, "127.0.0.1")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def test_with_a_password_the_page_asks_for_it_first(locked, store) -> None:
    response = request(locked, "GET", "/")
    assert response.status == 401
    assert 'name="password"' in response.read().decode()
    assert request(locked, "GET", "/healthz").status == 200
    form = "show_id=2026-10-20~la-dispute&action=got"
    assert request(locked, "POST", "/tickets", form).status == 401
    assert not store.shows()[0]["bought"]

    wrong = request(locked, "POST", "/login", "password=guess")
    assert wrong.status == 401
    assert "That password is wrong." in wrong.read().decode()

    right = request(locked, "POST", "/login", "password=opensesame")
    assert right.status == 303
    cookie = right.getheader("Set-Cookie")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Secure" in cookie
    session = {"Cookie": cookie.split(";")[0]}
    assert request(locked, "GET", "/", headers=session).status == 200
    assert request(locked, "POST", "/tickets", form, session).status == 303
    assert store.shows()[0]["bought"]
    assert (
        request(locked, "GET", "/", headers={"Cookie": "live_shows_session=forged"}).status == 401
    )


def test_without_a_password_login_just_goes_to_the_page(server) -> None:
    response = request(server, "POST", "/login", "password=anything")
    assert response.status == 303
    assert response.getheader("Set-Cookie") is None


def test_the_cookie_is_not_secure_on_a_plain_http_page(store) -> None:
    server = web.make_server(
        store,
        settings(LIVE_SHOWS_PASSWORD="pw", LIVE_SHOWS_URL="http://shows.lan/"),
        0,
        "127.0.0.1",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = request(server, "POST", "/login", "password=pw")
        assert "Secure" not in response.getheader("Set-Cookie")
    finally:
        server.shutdown()


def test_today_is_local() -> None:
    handler = web.Handler.__new__(web.Handler)
    handler.settings = SETTINGS
    assert handler.today() == web.datetime.now(MELBOURNE).date()
