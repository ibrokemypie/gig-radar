import ssl
from datetime import timedelta

from conftest import artist, at, show

from live_shows import mailer
from live_shows.model import Artist, Tier
from live_shows.names import normalise
from live_shows.settings import Smtp
from live_shows.store import show_record

NOW = at("2026-10-20 07:00")
ADDRESS = "listener@example.com"
SERVER = Smtp("smtp.example.com", 587, "login", ADDRESS, ADDRESS)
APP = "https://live-shows.example/"


def record(*args, bought: bool = False, **kwargs) -> dict:
    return {**show_record(show(*args, **kwargs)), "bought": bought}


def pinned(name: str = "La Dispute", **fields) -> Artist:
    """An artist with 2 albums played front to back."""
    return artist(name, pinned=True, albums_front_to_back=2, **fields)


def never_sent(_kind: str, _ref: str) -> bool:
    return False


def test_nothing_new_and_nothing_on_sale_sends_nothing() -> None:
    assert mailer.morning_email([], never_sent, NOW, ADDRESS, ADDRESS, APP) is None
    seen = record()
    assert mailer.morning_email([seen], lambda k, r: True, NOW, ADDRESS, ADDRESS, APP) is None


def test_one_new_pinned_show_gets_its_own_subject_and_both_links() -> None:
    draft = mailer.morning_email([record(acts=[pinned()])], never_sent, NOW, ADDRESS, ADDRESS, APP)
    assert draft is not None
    message, marks = draft

    assert message["Subject"] == "New show: La Dispute at 170 Russell, Tue 20 Oct"
    assert message["To"] == ADDRESS
    text = message.get_body(("plain",)).get_content()
    assert "La Dispute (pinned): 2 albums front to back · 100 plays" in text
    assert "With Special Features" in text
    assert f"Got tickets? {APP}#2026-10-20~la-dispute" in text
    html = message.get_body(("html",)).get_content()
    assert "href='https://tickets.example/1'" in html
    assert marks == [("new", ["2026-10-20|la dispute"]), ("on-sale", [])]


def test_bought_maybe_and_unpinned_loved_shows_never_reach_the_morning_email() -> None:
    maybe = record(acts=[artist("Spiritbox", Tier.MAYBE)])
    bought = record("2026-11-08", [artist("Dance Gavin Dance")], bought=True)
    loved = record("2026-11-09", [artist("Telenova")])
    loved_on_sale = record("2026-11-10", [artist("Telenova")], on_sale_at=NOW)
    shows = [maybe, bought, loved, loved_on_sale]
    assert mailer.morning_email(shows, never_sent, NOW, ADDRESS, ADDRESS, APP) is None


def test_on_sale_soon_is_sent_once_and_only_inside_the_notice() -> None:
    soon = record("2027-03-01", [pinned()], on_sale_at=NOW + timedelta(hours=27))
    later = record("2027-03-02", [pinned("Neck Deep")], on_sale_at=NOW + timedelta(days=5))
    sent = {("new", "2027-03-01|la dispute"), ("new", "2027-03-02|neck deep")}

    def was_sent(kind: str, ref: str) -> bool:
        return (kind, ref) in sent

    message, marks = mailer.morning_email([soon, later], was_sent, NOW, ADDRESS, ADDRESS, APP)
    assert message["Subject"] == "On sale soon: La Dispute at 170 Russell"
    assert "On sale Wed 21 Oct, 10:00 am" in message.get_body(("plain",)).get_content()
    assert marks[1] == ("on-sale", [mailer.on_sale_ref(soon)])

    sent.add(("on-sale", mailer.on_sale_ref(soon)))
    assert mailer.morning_email([soon, later], was_sent, NOW, ADDRESS, ADDRESS, APP) is None


def test_several_shows_are_counted_in_the_subject_and_festivals_go_by_name() -> None:
    festival = record("2026-12-04", [pinned("Neck Deep")], is_festival=True, title="Good Things")
    plain = record(acts=[pinned()])
    on_sale = record("2027-01-01", [pinned("Telenova")], on_sale_at=NOW + timedelta(hours=1))
    message, _ = mailer.morning_email(
        [festival, plain, on_sale], never_sent, NOW, ADDRESS, ADDRESS, APP
    )
    assert message["Subject"] == "Live shows: 3 new shows, 1 going on sale"
    assert "Good Things" in message.get_body(("plain",)).get_content()

    message, _ = mailer.morning_email([festival, plain], never_sent, NOW, ADDRESS, ADDRESS, APP)
    assert message["Subject"] == "Live shows: 2 new shows"
    message, _ = mailer.morning_email(
        [on_sale], lambda kind, ref: kind == "new", NOW, ADDRESS, ADDRESS, APP
    )
    assert message["Subject"] == "On sale soon: Telenova at 170 Russell"
    two_sales = [on_sale, record("2027-01-02", [pinned("A")], on_sale_at=NOW)]
    message, _ = mailer.morning_email(
        two_sales, lambda kind, ref: kind == "new", NOW, ADDRESS, ADDRESS, APP
    )
    assert message["Subject"] == "Live shows: 2 going on sale"


def kept(*names: str) -> dict:
    return {normalise(n): {"name": n, "choice": "kept", "seeds": [], "at": "x"} for n in names}


def test_monday_lists_loved_and_kept_shows_and_counts_new_maybes_once() -> None:
    maybe = record(acts=[artist("Spiritbox", Tier.MAYBE, plays=0, similar_to=["A", "B", "C"])])
    loved = record("2026-11-01", [artist("Neck Deep")])
    bought = record("2026-11-02", [artist("Thornhill", Tier.MAYBE)], bought=True)
    northlane = record("2026-11-03", [artist("Northlane", Tier.MAYBE)])
    two_new = record("2026-11-04", [artist("Alt", Tier.MAYBE), artist("Other", Tier.MAYBE)])

    draft = mailer.digest_email(
        [maybe, loved, bought, northlane, two_new],
        never_sent,
        kept("Northlane"),
        ADDRESS,
        ADDRESS,
        APP,
    )
    assert draft is not None
    message, marks = draft
    text = message.get_body(("plain",)).get_content()
    assert message["Subject"] == "Live shows this week"
    assert (
        text.index("LOVED")
        < text.index("Neck Deep")
        < text.index("KEPT")
        < text.index("Northlane at")
    )
    assert "Spiritbox at" not in text and "Thornhill" not in text
    assert f"3 new maybes this week: sample them: {APP}" in text
    html_body = message.get_body(("html",)).get_content()
    assert f"<a href='{APP}'>3 new maybes this week: sample them</a>" in html_body
    assert marks == [("digest", ["2026-10-20|spiritbox", "2026-11-04|alt", "2026-11-04|other"])]


def test_monday_counts_one_maybe_in_the_singular_and_skips_trimmed_and_digested_ones() -> None:
    maybe = record(acts=[artist("Spiritbox", Tier.MAYBE)])
    trimmed = record("2026-11-03", [artist("Gone", Tier.MAYBE)])
    choices = {"gone": {"name": "Gone", "choice": "trimmed", "seeds": [], "at": "x"}}

    draft = mailer.digest_email([maybe, trimmed], never_sent, choices, ADDRESS, ADDRESS, APP)
    assert draft is not None
    text = draft[0].get_body(("plain",)).get_content()
    assert "1 new maybe this week: sample them" in text
    assert "LOVED" not in text and "KEPT" not in text
    assert (
        mailer.digest_email([maybe, trimmed], lambda k, r: True, choices, ADDRESS, ADDRESS, APP)
        is None
    )


def test_monday_with_only_loved_shows_has_no_maybe_line() -> None:
    draft = mailer.digest_email([record()], lambda k, r: True, {}, ADDRESS, ADDRESS, APP)
    assert draft is not None
    assert "sample them" not in draft[0].get_body(("plain",)).get_content()
    assert draft[1] == [("digest", [])]


def verifies(context: ssl.SSLContext) -> bool:
    """Whether a TLS context checks the certificate and the host name."""
    return context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


class FakeSMTP:
    """An SMTP connection recording what was asked of it."""

    def __init__(self, calls: list):
        self.calls = calls

    def __call__(self, host, port, timeout, context=None):
        self.calls.append(("connect", host, port))
        if context is not None:
            self.calls.append(("context", verifies(context)))
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.calls.append(("quit",))

    def starttls(self, context):
        self.calls.append(("starttls", verifies(context)))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, message):
        self.calls.append(("send", message["Subject"]))


def test_send_signs_in_over_starttls() -> None:
    calls: list = []
    message, _ = mailer.morning_email(
        [record(acts=[pinned()])], never_sent, NOW, ADDRESS, ADDRESS, APP
    )
    mailer.send(message, SERVER, "app-pass", FakeSMTP(calls), FakeSMTP(["wrong"]))
    assert calls == [
        ("connect", "smtp.example.com", 587),
        ("starttls", True),
        ("login", "login", "app-pass"),
        ("send", message["Subject"]),
        ("quit",),
    ]


def test_send_on_port_465_is_tls_from_the_start() -> None:
    calls: list = []
    message, _ = mailer.morning_email(
        [record(acts=[pinned()])], never_sent, NOW, ADDRESS, ADDRESS, APP
    )
    server = Smtp("smtp.example.com", 465, "login", "from@example.com", ADDRESS)
    mailer.send(message, server, "pw", FakeSMTP(["wrong"]), FakeSMTP(calls))
    assert calls == [
        ("connect", "smtp.example.com", 465),
        ("context", True),
        ("login", "login", "pw"),
        ("send", message["Subject"]),
        ("quit",),
    ]


def test_the_message_is_from_the_sender_to_the_recipient() -> None:
    message, _ = mailer.morning_email(
        [record(acts=[pinned()])], never_sent, NOW, "from@example.com", ADDRESS, APP
    )
    assert message["From"] == "Live shows <from@example.com>"
    assert message["To"] == ADDRESS
