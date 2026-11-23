"""The emails: one each morning for new pinned shows and on-sales, and a Monday one.

The morning email lists every new pinned show in one message, never one per show, and
nothing about a show with tickets; a loved show that is not pinned waits for the Monday
email. The Monday email lists loved and kept artists' shows and counts the new maybes in
one line linking to the page, where they are sampled. Mail goes out over SMTP with the
password typed on the settings page: STARTTLS, or TLS from the start on port 465.
"""

import html
import smtplib
from collections.abc import Callable
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Any

from live_shows.settings import Smtp

IMPLICIT_TLS_PORT = 465
ON_SALE_NOTICE = timedelta(hours=36)


def new_refs(show: dict[str, Any]) -> list[str]:
    """The refs that say a show was emailed as new: one per (day, artist) pair."""
    return [f"{day}|{key}" for day, key in show["pairs"]]


def on_sale_ref(show: dict[str, Any]) -> str:
    """The ref that says a show's on-sale reminder went: the show and its sale time."""
    return f"{show['show_id']}|{show['on_sale_at']}"


def morning_email(
    shows: list[dict[str, Any]],
    was_sent: Callable[[str, str], bool],
    at: datetime,
    sender: str,
    recipient: str,
    app_url: str,
) -> tuple[EmailMessage, list[tuple[str, list[str]]]] | None:
    """The morning email, or None when nothing is new and nothing goes on sale soon.

    Returns the message and the (kind, refs) to mark sent once it has gone.
    """
    open_pinned = [s for s in shows if s["pinned"] and not s["bought"]]
    new = [s for s in open_pinned if not all(was_sent("new", ref) for ref in new_refs(s))]
    on_sale = [
        s
        for s in open_pinned
        if s["on_sale_at"]
        and at <= datetime.fromisoformat(s["on_sale_at"]) <= at + ON_SALE_NOTICE
        and not was_sent("on-sale", on_sale_ref(s))
    ]
    if not new and not on_sale:
        return None

    sections = []
    if new:
        sections.append(("New shows", new))
    if on_sale:
        sections.append(("Going on sale soon", on_sale))
    subject = _morning_subject(new, on_sale)
    message = _message(subject, sections, sender, recipient, app_url)
    marks = [("new", [ref for s in new for ref in new_refs(s)])]
    marks.append(("on-sale", [on_sale_ref(s) for s in on_sale]))
    return message, marks


def digest_email(
    shows: list[dict[str, Any]],
    was_sent: Callable[[str, str], bool],
    choices: dict[str, dict[str, Any]],
    sender: str,
    recipient: str,
    app_url: str,
) -> tuple[EmailMessage, list[tuple[str, list[str]]]] | None:
    """The Monday email, or None when there is no loved or kept show and no new maybe.

    It lists every loved and kept show without tickets, and counts the undecided maybe
    artists whose shows were in no Monday email yet.
    """

    def choice(act: dict[str, Any]) -> str | None:
        found = choices.get(act["key"])
        return found["choice"] if found else None

    open_shows = [s for s in shows if not s["bought"]]
    loved = [s for s in open_shows if s["tier"] == "love"]
    kept = [
        s
        for s in open_shows
        if s["tier"] == "maybe" and any(choice(act) == "kept" for act in s["acts"])
    ]
    new_maybes = [
        s
        for s in open_shows
        if s["tier"] == "maybe"
        and s not in kept
        and any(choice(act) is None for act in s["acts"])
        and not all(was_sent("digest", ref) for ref in new_refs(s))
    ]
    new_artists = {act["key"] for s in new_maybes for act in s["acts"] if choice(act) is None}
    if not loved and not kept and not new_artists:
        return None

    sections = [(heading, found) for heading, found in (("Loved", loved), ("Kept", kept)) if found]
    count = len(new_artists)
    note = f"{count} new maybe{'s' if count != 1 else ''} this week: sample them" if count else None
    message = _message("Live shows this week", sections, sender, recipient, app_url, note)
    return message, [("digest", [ref for s in new_maybes for ref in new_refs(s)])]


def send(
    message: EmailMessage,
    server: Smtp,
    password: str,
    smtp: Callable[..., smtplib.SMTP] = smtplib.SMTP,
    smtp_ssl: Callable[..., smtplib.SMTP] = smtplib.SMTP_SSL,
) -> None:
    """Send one message, signed in to the server: STARTTLS, or TLS from the start on 465."""
    implicit = server.port == IMPLICIT_TLS_PORT
    with (smtp_ssl if implicit else smtp)(server.host, server.port, timeout=60) as connection:
        if not implicit:
            connection.starttls()
        connection.login(server.user, password)
        connection.send_message(message)


def _morning_subject(new: list[dict], on_sale: list[dict]) -> str:
    """'La Dispute at 170 Russell, Tue 20 Oct', or a count when there are several."""
    if len(new) == 1 and not on_sale:
        return f"New show: {_headline(new[0])}, {_date(new[0])}"
    if not new and len(on_sale) == 1:
        return f"On sale soon: {_headline(on_sale[0])}"
    parts = []
    if new:
        parts.append(f"{len(new)} new show{'s' if len(new) != 1 else ''}")
    if on_sale:
        parts.append(f"{len(on_sale)} going on sale")
    return "Live shows: " + ", ".join(parts)


def _message(
    subject: str,
    sections: list[tuple[str, list[dict]]],
    sender: str,
    recipient: str,
    app_url: str,
    note: str | None = None,
) -> EmailMessage:
    """An email as plain text with an HTML alternative.

    A `note` ends it as a line linking to the page.
    """
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"Live shows <{sender}>"
    message["To"] = recipient
    text = _text(sections, app_url)
    page = _html(sections, app_url)
    if note:
        text += f"\n{note}: {app_url}\n"
        page = page.replace(
            "</body>",
            f"<p style='font:14px/1.4 sans-serif'><a href='{html.escape(app_url)}'>"
            f"{html.escape(note)}</a></p></body>",
        )
    message.set_content(text)
    message.add_alternative(page, subtype="html")
    return message


def _headline(show: dict) -> str:
    """The listener's acts and the venue: 'La Dispute at 170 Russell'."""
    if show["is_festival"]:
        return show["title"]
    names = " + ".join(act["name"] for act in show["acts"])
    return f"{names} at {show['venue']}"


def _date(show: dict) -> str:
    """'Tue 20 Oct'."""
    return datetime.fromisoformat(show["day"]).strftime("%a %-d %b")


def _details(show: dict) -> list[str]:
    """The lines under a show's headline: when, why it matters, the bill, on-sale."""
    lines = [f"{_date(show)}, {show['time']} · {show['venue']}"]
    lines += [f"{act['name']} ({_tier_word(act)}): {act['why']}" for act in show["acts"]]
    if show["with_them"]:
        lines.append("With " + ", ".join(show["with_them"]))
    if show["on_sale_at"]:
        on_sale = datetime.fromisoformat(show["on_sale_at"])
        lines.append(
            on_sale.strftime("On sale %a %-d %b, %-I:%M %p").replace("AM", "am").replace("PM", "pm")
        )
    return lines


def _tier_word(act: dict) -> str:
    """'pinned', 'loved' or 'maybe'."""
    if act["pinned"]:
        return "pinned"
    return "loved" if act["tier"] == "love" else "maybe"


def _text(sections: list[tuple[str, list[dict]]], app_url: str) -> str:
    """The plain-text body."""
    blocks = []
    for heading, shows in sections:
        blocks.append(heading.upper())
        for show in shows:
            blocks.append(
                "\n".join(
                    [
                        _headline(show),
                        *_details(show),
                        f"Tickets: {show['url']}",
                        f"Got tickets? {app_url}#{show['show_id']}",
                    ]
                )
            )
    return "\n\n".join(blocks) + "\n"


def _html(sections: list[tuple[str, list[dict]]], app_url: str) -> str:
    """The HTML body: the same shows, with links."""
    parts = []
    for heading, shows in sections:
        parts.append(f"<h2 style='font:600 16px sans-serif'>{html.escape(heading)}</h2>")
        for show in shows:
            details = "<br>".join(html.escape(line) for line in _details(show))
            parts.append(
                "<p style='font:14px/1.4 sans-serif'>"
                f"<b>{html.escape(_headline(show))}</b><br>{details}<br>"
                f"<a href='{html.escape(show['url'])}'>Tickets</a> · "
                f"<a href='{html.escape(app_url)}#{html.escape(show['show_id'])}'>Got tickets</a>"
                "</p>"
            )
    return "<html><body>" + "".join(parts) + "</body></html>"
