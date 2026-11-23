"""Tickets bought, read from the sellers' confirmation emails in an IMAP inbox.

This and the page's Got tickets button both mark a show bought. An email marks a show
bought when it comes from a ticket seller and names both one of the show's acts and its
date; anything less certain is left for the button.
"""

import email
import email.policy
import email.utils
import imaplib
import re
import ssl
from collections.abc import Callable
from datetime import date, timedelta
from email.message import EmailMessage
from typing import Any, cast

from live_shows.names import normalise

LOOK_BACK = timedelta(days=14)
SELLER_DOMAINS = (
    "ticketmaster.com.au", "ticketmaster.com", "moshtix.com.au", "oztix.com.au",
    "ticketek.com.au", "eventbrite.com", "eventbrite.com.au", "humanitix.com", "dice.fm",
    "megatix.com.au", "trybooking.com",
)  # fmt: skip


def date_forms(day: date) -> list[str]:
    """The ways a seller writes a day, normalised as the email text will be."""
    forms = [
        f"{day.day} {day:%B %Y}",
        f"{day.day} {day:%b %Y}",
        f"{day:%B} {day.day} {day.year}",
        f"{day:%b} {day.day} {day.year}",
        f"{day.day:02d}/{day.month:02d}/{day.year}",
        f"{day.day}/{day.month}/{day.year}",
        day.isoformat(),
    ]
    return [normalise(form) for form in forms]


def is_from_seller(message: EmailMessage) -> bool:
    """Whether the sender's address is at one of the ticket sellers."""
    sender = email.utils.parseaddr(str(message.get("From", "")))[1].lower()
    domain = sender.rsplit("@", 1)[-1]
    return any(domain == d or domain.endswith(f".{d}") for d in SELLER_DOMAINS)


def message_text(message: EmailMessage) -> str:
    """A message's subject and body as one normalised string, HTML tags dropped."""
    parts = [str(message.get("Subject", ""))]
    for part in message.walk():
        if part.get_content_type() in ("text/plain", "text/html"):
            body = part.get_content()
            if part.get_content_type() == "text/html":
                body = re.sub(r"<[^>]+>", " ", body)
            parts.append(body)
    return f" {normalise(' '.join(parts))} "


def shows_in(message: EmailMessage, shows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The shows a seller's message confirms: one of the acts and the day are both named."""
    if not is_from_seller(message):
        return []
    text = message_text(message)
    found = []
    for show in shows:
        names = {normalise(act["listed_as"]) for act in show["acts"]}
        names |= {normalise(act["name"]) for act in show["acts"]}
        if show["is_festival"]:
            names.add(normalise(show["title"]))
        named = any(f" {name} " in text for name in names if name)
        dated = any(f" {form} " in text for form in date_forms(date.fromisoformat(show["day"])))
        if named and dated:
            found.append(show)
    return found


def read_confirmations(
    host: str,
    user: str,
    password: str,
    today: date,
    already_read: Callable[[str], bool],
    imap: Callable[..., imaplib.IMAP4] = imaplib.IMAP4_SSL,
) -> list[tuple[str, EmailMessage]]:
    """The seller messages of the last two weeks not read before, with their Message-IDs.

    The inbox is reached over TLS with its certificate and host name verified.
    """
    connection = imap(host, ssl_context=ssl.create_default_context())
    try:
        connection.login(user, password)
        connection.select("INBOX", readonly=True)
        since = (today - LOOK_BACK).strftime("%d-%b-%Y")
        found = []
        for domain in SELLER_DOMAINS:
            _status, data = connection.search(None, "SINCE", since, "FROM", domain)
            for number in (data[0] or b"").split():
                _status, fetched = connection.fetch(number.decode(), "(RFC822)")
                raw = fetched[0][1] if fetched and isinstance(fetched[0], tuple) else b""
                # The default policy always builds an EmailMessage.
                message = cast(
                    EmailMessage, email.message_from_bytes(raw, policy=email.policy.default)
                )
                message_id = str(message.get("Message-ID", "")).strip()
                if message_id and not already_read(message_id):
                    found.append((message_id, message))
        return found
    finally:
        connection.logout()
