import ssl
from datetime import date
from email.message import EmailMessage

from conftest import artist, show

from live_shows import tickets
from live_shows.store import show_record

LA_DISPUTE = {**show_record(show()), "bought": False}
GOOD_THINGS = {
    **show_record(show("2026-12-04", [artist("Neck Deep")], is_festival=True,
                       title="Good Things Festival")),
    "bought": False,
}  # fmt: skip


def mail(
    sender: str, subject: str, body: str, html: str | None = None, message_id: str = "<1@x>"
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")
    return message


def test_a_seller_email_naming_the_act_and_day_confirms_the_show() -> None:
    message = mail(
        "Moshtix <orders@moshtix.com.au>",
        "Your Moshtix order",
        "Thanks! LA DISPUTE - 170 Russell - Tuesday 20 October 2026 7:00pm",
    )
    assert tickets.shows_in(message, [LA_DISPUTE, GOOD_THINGS]) == [LA_DISPUTE]


def test_html_bodies_subdomains_and_numeric_dates_count() -> None:
    message = mail(
        "Ticketek <noreply@mail.ticketek.com.au>",
        "Order confirmation",
        "see html",
        "<p>Good Things Festival</p><p>Date: 04/12/2026</p>",
    )
    assert tickets.shows_in(message, [LA_DISPUTE, GOOD_THINGS]) == [GOOD_THINGS]


def test_an_act_without_the_day_or_a_non_seller_confirms_nothing() -> None:
    wrong_day = mail("orders@moshtix.com.au", "Order", "La Dispute, 22 October 2026")
    assert tickets.shows_in(wrong_day, [LA_DISPUTE]) == []
    friend = mail("friend@gmail.com", "tickets!", "La Dispute 20 October 2026")
    assert tickets.shows_in(friend, [LA_DISPUTE]) == []
    lookalike = mail("orders@notmoshtix.com.au", "Order", "La Dispute 20 October 2026")
    assert tickets.shows_in(lookalike, [LA_DISPUTE]) == []


def test_date_forms_cover_the_usual_spellings() -> None:
    forms = tickets.date_forms(date(2026, 12, 4))
    assert "4 december 2026" in forms
    assert "dec 4 2026" in forms
    assert "04 12 2026" in forms
    assert "4 12 2026" in forms


class FakeImap:
    """An inbox holding messages by sender domain."""

    def __init__(self, by_domain: dict[str, list[EmailMessage]]) -> None:
        self.by_domain = by_domain
        self.numbered: dict[str, EmailMessage] = {}
        self.calls: list[tuple] = []

    def __call__(self, host: str, ssl_context: ssl.SSLContext) -> "FakeImap":
        self.calls.append(("connect", host))
        verified = ssl_context.verify_mode == ssl.CERT_REQUIRED and ssl_context.check_hostname
        self.calls.append(("verified", verified))
        return self

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def select(self, mailbox, readonly):
        self.calls.append(("select", mailbox, readonly))

    def search(self, charset, *criteria):
        domain = criteria[-1]
        numbers = []
        for message in self.by_domain.get(domain, []):
            number = str(len(self.numbered) + 1)
            self.numbered[number] = message
            numbers.append(number)
        return "OK", [" ".join(numbers).encode()]

    def fetch(self, number, parts):
        return "OK", [(b"1 (RFC822)", self.numbered[number].as_bytes())]

    def logout(self):
        self.calls.append(("logout",))


def test_read_confirmations_skips_messages_already_read_and_signs_out() -> None:
    read = mail("orders@moshtix.com.au", "a", "a", message_id="<old@x>")
    new = mail("orders@moshtix.com.au", "b", "b", message_id="<new@x>")
    imap = FakeImap({"moshtix.com.au": [read, new]})

    found = tickets.read_confirmations(
        "imap.example.com",
        "listener@example.com",
        "pw",
        date(2026, 10, 20),
        lambda mid: mid == "<old@x>",
        imap,
    )

    assert [message_id for message_id, _ in found] == ["<new@x>"]
    assert ("select", "INBOX", True) in imap.calls
    assert imap.calls[:2] == [("connect", "imap.example.com"), ("verified", True)]
    assert imap.calls[-1] == ("logout",)
