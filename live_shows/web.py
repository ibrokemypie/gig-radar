"""The page: the list by month, You're going, Got tickets, and the settings behind a button.

Pink marks Love and indigo Might like; settings sit behind a corner button, and each row
shows the essentials until tapped. Pinned shows sit in their own list above the months.
An opened maybe shows three previews of the artist with Keep and Not for me; trimmed
shows fold under "Trimmed" at the end of their month. Every control is a plain form
posted to this server, so the page needs script only to open the row an email links to
and to play a preview.

With LIVE_SHOWS_PASSWORD set, every page but the health check asks for it once, then
keeps a session cookie. Every POST must come from the page itself: its Origin, or its
Referer when a browser sends no Origin, must name this server.
"""

import hmac
import html
import logging
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, tzinfo
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from live_shows.library import fewer_like
from live_shows.model import web_url
from live_shows.settings import Settings
from live_shows.store import SECRETS, Store

log = logging.getLogger(__name__)

SESSION_COOKIE = "live_shows_session"
SESSION_DAYS = 30
MAX_BODY_BYTES = 1024 * 1024
# The health check is reached from inside the container, by these names only.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
SECRET_LABELS = {
    "mail_password": (
        "Mail password",
        "Signs in to send the emails, read your ticket emails and fill the calendars, for each"
        " of those without its own password below. With Fastmail, an app password.",
    ),
    "smtp_password": ("Sending password", "Only if sending mail needs its own password."),
    "imap_password": ("Inbox password", "Only if reading ticket emails needs its own password."),
    "caldav_password": ("Calendar password", "Only if the calendars need their own password."),
    "lastfm_key": (
        "Last.fm API key",
        'Reads your listening when LIBRARY is lastfm. A free key from <a href="https://www.last.fm'
        '/api/account/create" target="_blank" rel="noopener">last.fm/api/account/create ↗</a>.',
    ),
    "ticketmaster_key": (
        "Ticketmaster API key",
        'Finds shows on Ticketmaster. A free key from <a href="https://developer.ticketmaster.com/"'
        ' target="_blank" rel="noopener">developer.ticketmaster.com ↗</a>.',
    ),
}


@dataclass
class Sampling:
    """What was decided about maybes and what there is to sample, by artist key."""

    choices: dict[str, dict[str, Any]] = field(default_factory=dict)
    samples: dict[str, dict[str, Any]] = field(default_factory=dict)
    fewer_like: dict[str, int] = field(default_factory=dict)

    def choice(self, act: dict[str, Any]) -> str | None:
        """'kept', 'trimmed', or None while undecided."""
        found = self.choices.get(act["key"])
        return found["choice"] if found else None

    def undecided(self, show: dict[str, Any]) -> list[dict[str, Any]]:
        """The show's maybe acts neither kept nor trimmed."""
        return [a for a in show["acts"] if a["tier"] == "maybe" and self.choice(a) is None]

    def is_trimmed(self, show: dict[str, Any]) -> bool:
        """A maybe show whose every act was trimmed."""
        return show["tier"] == "maybe" and all(self.choice(a) == "trimmed" for a in show["acts"])


_MONTHS = [
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
]  # fmt: skip


def page(
    shows: list[dict[str, Any]],
    secrets: dict[str, bool],
    last_run: dict[str, Any] | None,
    today: date,
    settings_open: bool = False,
    replacing: str | None = None,
    sampling: Sampling | None = None,
    notes: list[str] | None = None,
    zone: tzinfo = UTC,
) -> str:
    """The whole page as HTML; `notes` say what is not set up yet."""
    sampling = sampling or Sampling()
    gear = (
        '<a class="gear" href="./">Close settings</a>'
        if settings_open
        else '<a class="gear" href="?settings=1">⚙ Settings</a>'
    )
    going = [show for show in shows if show["bought"]]
    upcoming = [show for show in shows if not show["bought"]]
    body = [
        f'<div class="top"><h1>Live shows</h1>{gear}</div>',
        '<p class="sub">Gigs near you by artists from your listening.</p>',
        '<div class="legend"><span class="chip pin">Pinned</span><span>albums played front to'
        ' back, or hearted tracks</span><span class="chip love">Love</span><span>played or'
        ' hearted a lot</span><span class="chip like">Might like</span><span>a few plays, or'
        " similar to ones you love</span></div>",
        _run_status(last_run, notes or [], zone),
        _fewer_like(sampling.fewer_like),
    ]
    if settings_open:
        body.append(_settings(secrets, replacing))
    if going:
        body.append(_going(going, today))
    pinned = [show for show in upcoming if show["pinned"]]
    if pinned:
        body.append("<h2>Pinned</h2>" + "".join(_row(show, today, sampling) for show in pinned))
    rest = [show for show in upcoming if not show["pinned"]]
    body.append(_month_list(rest, today, sampling, has_pinned=bool(pinned)))
    return _document("".join(body))


def _run_status(last_run: dict[str, Any] | None, notes: list[str], zone: tzinfo) -> str:
    """When the list was last checked, and anything that went wrong or is not set up."""
    lines = []
    if last_run is None:
        lines.append("Not checked yet.")
    else:
        finished = last_run.get("finished_at") or last_run["started_at"]
        lines.append(f"Checked {_when_checked(datetime.fromisoformat(finished), zone)}.")
        lines += [
            f"Couldn't read {html.escape(failure)}"
            for failure in last_run["summary"].get("failures", [])
        ]
        lines += [html.escape(warning) for warning in last_run["summary"].get("warnings", [])]
    lines += [html.escape(note) for note in notes]
    return '<div class="status">' + "".join(f"<div>{line}</div>" for line in lines) + "</div>"


def _when_checked(when: datetime, zone: tzinfo) -> str:
    """'today at 07:02' or 'Tue 20 Oct at 07:02', in local time."""
    local = when.astimezone(zone)
    day = "today" if local.date() == datetime.now(zone).date() else _short_date(local.date())
    return f"{day} at {local:%H:%M}"


def _fewer_like(stopped: dict[str, int]) -> str:
    """A line for each loved artist that finds no new maybes, with Undo."""
    return "".join(
        f'<div class="ls-learn">Finding fewer artists like <b>{html.escape(seed)}</b>: you'
        f" trimmed {count} found through it and kept none."
        f' <form method="post" action="learning"><input type="hidden" name="seed"'
        f' value="{_attr(seed)}"><button class="undo" name="action" value="undo">Undo</button>'
        "</form></div>"
        for seed, count in sorted(stopped.items())
    )


def setup_notes(settings: Settings, secrets: dict[str, bool]) -> list[str]:
    """What is set up in the environment but waits for a secret on the page."""
    notes = []
    if settings.library == "lastfm" and not secrets["lastfm_key"]:
        notes.append("No artists until the Last.fm API key is set in Settings.")
    for service, configured, what in (
        ("smtp", settings.smtp, "emails"),
        ("imap", settings.imap, "ticket emails read"),
        ("caldav", settings.caldav, "calendar"),
    ):
        if configured and not (secrets[f"{service}_password"] or secrets["mail_password"]):
            notes.append(f"No {what} until the mail password is set in Settings.")
    return notes


def login_page(wrong: bool = False) -> str:
    """The password form shown before the page when LIVE_SHOWS_PASSWORD is set."""
    error = '<p class="status">That password is wrong.</p>' if wrong else ""
    return _document(
        '<div class="top"><h1>Live shows</h1></div>'
        f"{error}"
        '<form method="post" action="login" class="acts-row">'
        '<input type="password" name="password" required autofocus aria-label="Password"'
        ' placeholder="Password" autocomplete="current-password">'
        '<button class="btn primary">Sign in</button></form>'
    )


def _settings(secrets: dict[str, bool], replacing: str | None) -> str:
    """The settings section: each secret as Saved or Not set, never its value."""
    cards = []
    for name in SECRETS:
        label, use = SECRET_LABELS[name]
        is_saved = secrets[name]
        if is_saved and replacing != name:
            controls = (
                f'<a class="btn" href="?settings=1&amp;replace={name}">Replace</a>'
                f'<form method="post" action="settings"><input type="hidden" name="name"'
                f' value="{name}"><button class="btn" name="action" value="clear">Clear</button>'
                "</form>"
            )
        else:
            cancel = '<a class="btn" href="?settings=1">Cancel</a>' if is_saved else ""
            controls = (
                f'<form method="post" action="settings" class="acts-row"><input type="hidden"'
                f' name="name" value="{name}"><input type="password" name="value" required'
                f' autocomplete="off" aria-label="{label}" placeholder="Paste it here">'
                f'<button class="btn primary" name="action" value="save">Save</button></form>'
                f"{cancel}"
            )
        state = (
            '<span class="state saved">Saved</span>'
            if is_saved
            else ('<span class="state unset">Not set</span>')
        )
        cards.append(
            f'<div class="secret"><div class="head"><b>{label}</b>{state}</div><p>{use}</p>'
            f'<div class="acts-row">{controls}</div></div>'
        )
    return '<h2>Settings</h2><div class="settings">' + "".join(cards) + "</div>"


def _going(going: list[dict[str, Any]], today: date) -> str:
    """The You're going strip: one card per show with tickets, soonest first."""
    cards = []
    for show in going:
        time = "" if show["is_festival"] else f", {show['time']}"
        cards.append(
            f'<div class="ticket" id="{_attr(show["show_id"])}"'
            f' style="--tier: var(--t-{show["tier"]})">'
            f"<b>{html.escape(_name(show))}</b>"
            f'<div class="when">{_when(show, today)}{time}</div>'
            f'<div class="when">{html.escape(show["venue"])}</div>'
            f"{_ticket_form(show, 'undo', 'Not going', 'undo')}</div>"
        )
    strip = "".join(cards)
    heading = "<h3>You're going</h3>"
    return f'<section class="going">{heading}<div class="strip">{strip}</div></section>'


def _month_list(
    shows: list[dict[str, Any]], today: date, sampling: Sampling, has_pinned: bool = False
) -> str:
    """The list under month headings, trimmed shows folded at each month's end.

    The year is named only past this one.
    """
    if not shows:
        return "" if has_pinned else '<p class="empty">No shows found yet.</p>'
    months: dict[str, tuple[list[str], list[str]]] = {}
    for show in shows:
        day = date.fromisoformat(show["day"])
        heading = _MONTHS[day.month - 1] + ("" if day.year == today.year else f" {day.year}")
        rows, folded = months.setdefault(heading, ([], []))
        if sampling.is_trimmed(show):
            folded.append(_trimmed(show, today))
        else:
            rows.append(_row(show, today, sampling))
    parts = []
    for heading, (rows, folded) in months.items():
        parts.append(f"<h2>{heading}</h2>")
        parts += rows
        if folded:
            parts.append('<div class="folded"><div class="hint">Trimmed</div>')
            parts += folded
            parts.append("</div>")
    return "".join(parts)


def _trimmed(show: dict[str, Any], today: date) -> str:
    """A trimmed show folded to one line, with Bring back."""
    return (
        f'<div class="ls-trimmed" id="{_attr(show["show_id"])}">'
        f"{html.escape(_name(show))} · {_when(show, today)} "
        f'<form method="post" action="artists"><input type="hidden" name="show_id"'
        f' value="{_attr(show["show_id"])}"><button class="undo" name="action"'
        ' value="bring-back">Bring back</button></form></div>'
    )


def _row(show: dict[str, Any], today: date, sampling: Sampling) -> str:
    """One show: the essentials at rest; the time, bill, sale, links and samples when opened."""
    if show["pinned"]:
        tier_chip = '<span class="chip pin">Pinned</span>'
    elif show["tier"] == "love":
        tier_chip = '<span class="chip love">Love</span>'
    else:
        tier_chip = '<span class="chip like">Might like</span>'
    if any(sampling.choice(act) == "kept" for act in show["acts"]):
        tier_chip += ' <span class="chip kept">Kept</span>'
    undecided = sampling.undecided(show)
    hint = (
        "Tap for time, samples, sale and tickets"
        if undecided
        else ("Tap for time, sale and tickets")
    )
    festival_chip = ' <span class="chip fest">Festival</span>' if show["is_festival"] else ""
    details = [
        "All day"
        if show["is_festival"]
        else f"Doors {show['time']}"
        + (f" · {html.escape(show['title'])}" if show["title"] != _name(show) else "")
    ]
    if show["with_them"]:
        details.append("With " + html.escape(", ".join(show["with_them"])))
    if show["on_sale_at"]:
        on_sale = datetime.fromisoformat(show["on_sale_at"])
        details.append(f"On sale {_short_date(on_sale.date())}, {on_sale:%H:%M}")
    details.append("Seen on " + html.escape(", ".join(show["sources"])))
    details.append(
        f'<a href="{_attr(web_url(show["url"]))}" target="_blank" rel="noopener">'
        f"Tickets on {html.escape(_site(web_url(show['url'])))} ↗</a>"
    )
    return (
        f'<details class="row" id="{_attr(show["show_id"])}"'
        f' style="--tier: var(--t-{show["tier"]})"><summary><div>'
        f'<div class="name">{html.escape(_name(show))} {tier_chip}{festival_chip}</div>'
        f'<div class="line">{_when(show, today)} · {html.escape(show["venue"])}</div>'
        f'<div class="why">{_why(show)}</div>'
        f'<div class="hint">{hint}</div></div></summary>'
        f"{_ticket_form(show, 'got', 'Got tickets', 'got')}"
        f'<div class="more">{"".join(f"<div>{line}</div>" for line in details)}</div>'
        f"{_sampling(show, sampling)}</details>"
    )


def _sampling(show: dict[str, Any], sampling: Sampling) -> str:
    """Each undecided maybe act's previews with Keep and Not for me; each kept one's Undo."""
    blocks = []
    names_each = len(show["acts"]) > 1
    for act in show["acts"]:
        if act["tier"] != "maybe":
            continue
        choice = sampling.choice(act)
        if choice == "kept":
            blocks.append(
                f'<div class="ls-kept">You kept {html.escape(act["name"])}. '
                f"{_artist_form(show, act, [('undo', 'Undo keep', 'undo')])}</div>"
            )
        elif choice is None:
            who = f'<div class="ls-who">{html.escape(act["name"])}</div>' if names_each else ""
            buttons = [("keep", "Keep", "ls-keep"), ("trim", "Not for me", "ls-no")]
            blocks.append(
                f"{who}{_previews(sampling.samples.get(act['key']))}"
                f'<div class="ls-trim">{_artist_form(show, act, buttons)}</div>'
            )
    return f'<div class="ls-samples">{"".join(blocks)}</div>' if blocks else ""


def _previews(record: dict[str, Any] | None) -> str:
    """An artist's preview cards, a link to Apple Music when there are none, or nothing yet."""
    if record is None:
        return ""
    if not record["items"]:
        if record["apple"]:
            return (
                f'<a class="ls-apple" href="{_attr(web_url(record["apple"]))}" target="_blank"'
                ' rel="noopener">No previews · Apple Music ↗</a>'
            )
        return '<div class="hint">Not on Apple Music</div>'
    is_song = record["kind"] == "songs"
    song = " song" if is_song else ""
    cards = [
        f'<div class="ls-vid{song}"><button type="button" class="ls-thumb{song}"'
        f' data-preview="{_attr(web_url(item["preview"]))}"'
        f' data-kind="{"audio" if is_song else "video"}"'
        f' aria-label="Play {_attr(item["name"])}"><img src="{_attr(web_url(item["art"]))}" alt=""'
        f' loading="lazy"><span class="ls-playicon">{"♪" if is_song else "▶"}</span></button>'
        f'<div class="ls-vname">{html.escape(item["name"])}</div>'
        f'<div class="ls-vmeta">{"song" if is_song else "video"} preview · '
        f'<a href="{_attr(web_url(item["url"]))}" target="_blank" rel="noopener">Apple ↗</a>'
        "</div></div>"
        for item in record["items"]
    ]
    return f'<div class="ls-strip">{"".join(cards)}</div>'


def _artist_form(
    show: dict[str, Any], act: dict[str, Any], buttons: list[tuple[str, str, str]]
) -> str:
    """A form posting one of `buttons` (action, label, class) about one act of a show."""
    return (
        f'<form method="post" action="artists"><input type="hidden" name="show_id"'
        f' value="{_attr(show["show_id"])}"><input type="hidden" name="key"'
        f' value="{_attr(act["key"])}">'
        + "".join(
            f'<button class="{css}" name="action" value="{action}">{label}</button>'
            for action, label, css in buttons
        )
        + "</form>"
    )


def _ticket_form(show: dict[str, Any], action: str, label: str, css: str) -> str:
    return (
        f'<form method="post" action="tickets" class="{css}-form"><input type="hidden"'
        f' name="show_id" value="{_attr(show["show_id"])}"><button class="{css}" name="action"'
        f' value="{action}">{label}</button></form>'
    )


def _name(show: dict[str, Any]) -> str:
    """A festival's name, or the listener's acts joined with '+'."""
    if show["is_festival"]:
        return show["title"]
    return " + ".join(act["name"] for act in show["acts"])


def _why(show: dict[str, Any]) -> str:
    """Why it matters: one act's reason, each act's, or a festival's loved acts then maybes."""
    acts = show["acts"]
    if show["is_festival"]:
        loved = [a for a in acts if a["tier"] == "love"]
        maybe = [a for a in acts if a["tier"] != "love"]
        parts = [f"<b>{html.escape(a['name'])}</b> ({html.escape(a['why'])})" for a in loved]
        if maybe:
            names = ", ".join(html.escape(a["name"]) for a in maybe)
            parts.append(f"{len(maybe)} you might like: {names}")
        return " · ".join(parts)
    if len(acts) == 1:
        return html.escape(acts[0]["why"])
    return " · ".join(html.escape(f"{a['name']}: {a['why']}") for a in acts)


def _when(show: dict[str, Any], today: date) -> str:
    day = date.fromisoformat(show["day"])
    return "Tonight" if day == today else _short_date(day)


def _short_date(day: date) -> str:
    """'Tue 20 Oct'."""
    return f"{day:%a} {day.day} {day:%b}"


def _site(url: str) -> str:
    """A ticket link's site name: 'moshtix.com.au'."""
    host = urllib.parse.urlparse(url).hostname or url
    return host.removeprefix("www.").removeprefix("premier.")


def _attr(text: str) -> str:
    return html.escape(text, quote=True)


def _document(body: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Live shows</title><style>{STYLE}</style></head>"
        f'<body><div class="app"><div class="wrap">{body}</div></div>'
        f"<script>{OPEN_LINKED_ROW}</script></body></html>"
    )


# Opens the row an email's Got tickets link points at, and plays a tapped preview in place,
# pausing any other.
OPEN_LINKED_ROW = """
const linked = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1)));
if (linked && linked.tagName === "DETAILS") linked.open = true;
document.addEventListener("click", (event) => {
  const thumb = event.target.closest("[data-preview]");
  if (!thumb) return;
  document.querySelectorAll("video, audio").forEach((other) => other.pause());
  const player = document.createElement(thumb.dataset.kind);
  player.src = thumb.dataset.preview;
  player.controls = true;
  player.autoplay = true;
  player.playsInline = true;
  if (thumb.dataset.kind === "video") player.poster = thumb.querySelector("img").src;
  const holder = document.createElement("div");
  holder.className = "ls-player";
  holder.appendChild(player);
  thumb.replaceWith(holder);
});
"""

STYLE = """
:root { --t-love: #c2185b; --t-like: #4a55a2; --a-bg: #fbfaf7; --a-fg: #1f1d1a;
  --a-mut: #6b655c; --a-line: #e3ddd2; --a-acc: #b4462f; --a-card: #ffffff;
  color-scheme: light dark; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { --t-love: #f48fb1; --t-like: #9fa8f0; --a-bg: #17161a;
    --a-fg: #ebe7e0; --a-mut: #9d978d; --a-line: #34313a; --a-acc: #e98b6d; --a-card: #1f1e23; }
}
:root[data-theme="dark"] { --t-love: #f48fb1; --t-like: #9fa8f0; --a-bg: #17161a;
  --a-fg: #ebe7e0; --a-mut: #9d978d; --a-line: #34313a; --a-acc: #e98b6d; --a-card: #1f1e23; }
body { margin: 0; background: var(--a-bg); }
.app { background: var(--a-bg); color: var(--a-fg); font: 15px/1.5 -apple-system, system-ui,
  sans-serif; padding: 20px 16px 40px; overflow-wrap: anywhere; }
.wrap { max-width: 860px; margin: 0 auto; }
h1 { font-size: 1.9em; line-height: 1.15; margin: 0 0 4px; }
.sub { color: var(--a-mut); margin: 0 0 12px; font-size: 14px; }
.top { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
button { font: inherit; color: inherit; cursor: pointer; }
form { margin: 0; display: inline; }
.gear, .btn { border: 1px solid var(--a-line); background: var(--a-card); border-radius: 6px;
  padding: 4px 10px; font-size: 13px; white-space: nowrap; color: var(--a-fg);
  text-decoration: none; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 12px; font-size: 13px; color: var(--a-mut);
  margin-bottom: 8px; }
.status { font-size: 13px; color: var(--a-mut); margin-bottom: 8px; }
h2 { font-size: 1.15em; margin: 22px 0 6px; border-bottom: 1px solid var(--a-line);
  padding-bottom: 2px; }
.chip { display: inline-block; font-size: 11.5px; font-weight: 600; line-height: 1.5;
  border-radius: 999px; padding: 0 8px; white-space: nowrap; vertical-align: 1px; }
.chip.love { background: var(--t-love); color: var(--a-bg); }
.chip.pin { background: var(--t-love); color: var(--a-bg); outline: 1.5px solid var(--t-love);
  outline-offset: 1.5px; }
.chip.like { border: 1.5px solid var(--t-like); color: var(--t-like); }
.chip.fest { border: 1px solid var(--a-mut); color: var(--a-mut); font-weight: 500; }
.going { margin: 6px 0 4px; }
.going h3 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--a-mut); margin: 0 0 6px; }
.strip { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px; }
.ticket { flex: 0 0 172px; background: var(--a-card); border: 1px solid var(--a-line);
  border-left: 4px solid var(--tier); border-radius: 8px; padding: 8px 10px; font-size: 13px; }
.ticket b { display: block; font-size: 14px; }
.ticket .when { color: var(--a-mut); }
.undo { border: 0; background: none; padding: 0; color: var(--a-mut); text-decoration: underline;
  font-size: 12px; }
.row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 4px 12px;
  align-items: start; padding: 10px 0 10px 12px; border-left: 4px solid var(--tier);
  border-bottom: 1px solid var(--a-line); margin-left: -2px; }
.row summary { list-style: none; cursor: pointer; grid-column: 1; }
.row summary::-webkit-details-marker { display: none; }
.row .got-form { grid-column: 2; grid-row: 1; }
.row[open] .hint { display: none; }
.name { font-weight: 600; }
.line, .why { font-size: 14px; }
.why { color: var(--a-mut); }
.why b { color: var(--a-fg); }
.more { grid-column: 1 / -1; font-size: 13.5px; color: var(--a-mut); display: grid; gap: 2px; }
.more a, .settings a:not(.btn) { color: var(--a-acc); }
.got { border: 1px solid var(--a-acc); background: none; color: var(--a-acc); border-radius: 6px;
  padding: 3px 10px; font-size: 13px; white-space: nowrap; }
.hint { font-size: 12px; color: var(--a-mut); }
.empty { color: var(--a-mut); }
.settings { display: grid; gap: 10px; }
.secret { background: var(--a-card); border: 1px solid var(--a-line); border-radius: 8px;
  padding: 10px 12px; display: grid; gap: 6px; }
.secret .head { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 6px; }
.secret .state { font-size: 13px; font-weight: 600; }
.secret .state.saved { color: var(--t-like); }
.secret .state.unset { color: var(--a-mut); }
.secret p { margin: 0; font-size: 13px; color: var(--a-mut); }
.acts-row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.secret input[type=password] { flex: 1 1 180px; min-width: 0; font: inherit; font-size: 14px;
  padding: 4px 8px; border: 1px solid var(--a-line); border-radius: 6px; background: var(--a-bg);
  color: var(--a-fg); }
.btn { background: var(--a-bg); padding: 3px 10px; }
.btn.primary { border-color: var(--a-acc); color: var(--a-acc); }
.chip.kept { border: 1px solid var(--a-mut); color: var(--a-mut); font-weight: 500; }
.ls-learn { font-size: 13px; color: var(--a-mut); background: var(--a-card);
  border: 1px dashed var(--a-line); border-radius: 8px; padding: 6px 10px; margin: 8px 0; }
.ls-learn b { color: var(--a-fg); }
.ls-samples { grid-column: 1 / -1; display: grid; gap: 6px; margin-top: 4px; }
.ls-who { font-size: 13px; font-weight: 600; }
.ls-strip { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 2px; }
.ls-vid { flex: 0 0 136px; font-size: 12px; min-width: 0; }
.ls-vid.song { flex-basis: 96px; }
.ls-thumb { position: relative; display: block; width: 100%; padding: 0; border: 0;
  border-radius: 6px; overflow: hidden; background: var(--a-line); aspect-ratio: 16 / 9; }
.ls-thumb.song { aspect-ratio: 1; }
.ls-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; opacity: 0.92; }
.ls-playicon { position: absolute; inset: auto auto 6px 6px; width: 22px; height: 22px;
  border-radius: 999px; background: rgba(0, 0, 0, 0.55); color: #fff; font-size: 10px;
  display: grid; place-items: center; }
.ls-player video { width: 100%; aspect-ratio: 16 / 9; border-radius: 6px; background: #000;
  display: block; }
.ls-player audio { width: 100%; }
.ls-vname { margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ls-vmeta { color: var(--a-mut); font-size: 11px; }
.ls-vmeta a, .ls-apple { color: var(--a-acc); }
.ls-apple { font-size: 13px; }
.ls-trim form { display: flex; gap: 8px; }
.ls-keep { border: 1px solid var(--t-like); background: none; color: var(--t-like);
  border-radius: 6px; padding: 2px 10px; font-size: 13px; }
.ls-no { border: 1px solid var(--a-line); background: none; color: var(--a-mut);
  border-radius: 6px; padding: 2px 10px; font-size: 13px; }
.ls-kept { font-size: 13px; color: var(--a-mut); }
.folded { padding: 8px 0 0 12px; opacity: 0.6; font-size: 13px; }
.ls-trimmed { padding: 2px 0; }
@media (max-width: 520px) {
  .row { grid-template-columns: minmax(0, 1fr); }
  .row .got-form { grid-column: 1; grid-row: auto; }
}
"""


_ARTIST_ACTIONS = ("keep", "trim", "undo", "bring-back")


class Handler(BaseHTTPRequestHandler):
    """Serves the page and takes its forms; the store and settings are set on the class."""

    store: Store
    settings: Settings

    def today(self) -> date:
        """Today in local time."""
        return datetime.now(self.settings.place.zone).date()

    def do_GET(self) -> None:
        """The page, or the health check, or the login form."""
        url = urllib.parse.urlparse(self.path)
        if url.path == "/healthz" and self._host_name() in LOCAL_HOSTS:
            self._reply(HTTPStatus.OK, "ok", "text/plain")
            return
        if not self._known_host():
            return
        if url.path == "/healthz":
            self._reply(HTTPStatus.OK, "ok", "text/plain")
            return
        if url.path != "/":
            self._reply(HTTPStatus.NOT_FOUND, "Not found", "text/plain")
            return
        if not self._signed_in():
            self._reply(HTTPStatus.UNAUTHORIZED, login_page(), "text/html; charset=utf-8")
            return
        query = urllib.parse.parse_qs(url.query)
        replacing = query.get("replace", [None])[0]
        secrets = self.store.secret_status()
        body = page(
            self.store.shows(),
            secrets,
            self.store.last_run(),
            self.today(),
            settings_open="settings" in query,
            replacing=replacing if replacing in SECRETS else None,
            sampling=self._sampling(),
            notes=setup_notes(self.settings, secrets),
            zone=self.settings.place.zone,
        )
        self._reply(HTTPStatus.OK, body, "text/html; charset=utf-8")

    def do_POST(self) -> None:
        """Sign in, Got tickets, Not going, sampling, and saving or clearing a secret."""
        url = urllib.parse.urlparse(self.path)
        if not self._known_host():
            return
        form = self._form()
        if form is None:
            return
        if not self._same_origin():
            self._reply(HTTPStatus.FORBIDDEN, "Forms are taken only from this page", "text/plain")
            return
        if url.path == "/login":
            self._login(form)
            return
        if not self._signed_in():
            self._reply(HTTPStatus.UNAUTHORIZED, login_page(), "text/html; charset=utf-8")
            return
        if url.path == "/tickets":
            self._tickets(form)
        elif url.path == "/artists":
            self._artists(form)
        elif url.path == "/learning":
            self._learning(form)
        elif url.path == "/settings":
            self._settings(form)
        else:
            self._reply(HTTPStatus.NOT_FOUND, "Not found", "text/plain")

    def _host_name(self) -> str:
        """The request's Host header without its port."""
        return urllib.parse.urlsplit(f"//{self.headers.get('Host') or ''}").hostname or ""

    def _known_host(self) -> bool:
        """Whether the Host header is LIVE_SHOWS_URL's or an allowed one; refuses it if not.

        A page reached by any other name could be a DNS rebinding attack.
        """
        if (self.headers.get("Host") or "").lower() in self.settings.hosts:
            return True
        self._reply(HTTPStatus.MISDIRECTED_REQUEST, "Unknown host", "text/plain")
        return False

    def _form(self) -> dict[str, str] | None:
        """The posted form, or None once a bad or oversized body was refused."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self._reply(HTTPStatus.BAD_REQUEST, "Bad Content-Length", "text/plain")
            return None
        if length > MAX_BODY_BYTES:
            self._reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Too large", "text/plain")
            return None
        body = self.rfile.read(length).decode(errors="replace")
        return {key: values[0] for key, values in urllib.parse.parse_qs(body).items()}

    def _same_origin(self) -> bool:
        """Whether the form came from this page: its Origin, else Referer, is a known host."""
        source = self.headers.get("Origin") or self.headers.get("Referer") or ""
        return urllib.parse.urlsplit(source).netloc.lower() in self.settings.hosts

    def _signed_in(self) -> bool:
        """Whether no password is set, or the session cookie is a live session under it."""
        password = self.settings.password
        if not password:
            return True
        cookie = SimpleCookie(self.headers.get("Cookie") or "").get(SESSION_COOKIE)
        return cookie is not None and self.store.has_session(cookie.value, password)

    def _login(self, form: dict[str, str]) -> None:
        """Check the password and set the session cookie, or show the form again."""
        password = self.settings.password
        if not password:
            self._redirect("./")
            return
        if not hmac.compare_digest(form.get("password", "").encode(), password.encode()):
            log.warning("a wrong password was tried")
            self._reply(HTTPStatus.UNAUTHORIZED, login_page(wrong=True), "text/html; charset=utf-8")
            return
        secure = "; Secure" if self.settings.app_url.startswith("https:") else ""
        self._redirect(
            "./",
            f"{SESSION_COOKIE}={self.store.start_session(password, SESSION_DAYS)}; Path=/"
            "; HttpOnly; SameSite=Lax"
            f"; Max-Age={SESSION_DAYS * 86400}{secure}",
        )

    def _tickets(self, form: dict[str, str]) -> None:
        show = next((s for s in self.store.shows() if s["show_id"] == form.get("show_id")), None)
        action = form.get("action")
        if show is None or action not in ("got", "undo"):
            self._reply(HTTPStatus.BAD_REQUEST, "No such show or action", "text/plain")
            return
        pairs = [(day, key) for day, key in show["pairs"]]
        if action == "got":
            self.store.mark_bought(pairs, "button")
        else:
            self.store.unmark_bought(pairs)
        self._redirect(f"./#{urllib.parse.quote(show['show_id'])}")

    def _sampling(self) -> Sampling:
        choices = self.store.choices()
        return Sampling(choices, self.store.samples(), fewer_like(choices, self.store.unlearned()))

    def _artists(self, form: dict[str, str]) -> None:
        """Keep, Not for me, Undo keep, and Bring back a trimmed show."""
        show = next((s for s in self.store.shows() if s["show_id"] == form.get("show_id")), None)
        action = form.get("action")
        if show is None or action not in _ARTIST_ACTIONS:
            self._reply(HTTPStatus.BAD_REQUEST, "No such show or action", "text/plain")
            return
        maybes = [a for a in show["acts"] if a["tier"] == "maybe"]
        if action == "bring-back":
            for maybe in maybes:
                self.store.clear_choice(maybe["key"])
        else:
            act = next((a for a in maybes if a["key"] == form.get("key")), None)
            if act is None:
                self._reply(HTTPStatus.BAD_REQUEST, "No such artist on that show", "text/plain")
                return
            if action == "undo":
                self.store.clear_choice(act["key"])
            else:
                choice = "kept" if action == "keep" else "trimmed"
                self.store.set_choice(act["key"], act["name"], choice, act["found_through"])
        log.info("%s %s", action, form.get("key") or show["show_id"])
        self._redirect(f"./#{urllib.parse.quote(show['show_id'])}")

    def _learning(self, form: dict[str, str]) -> None:
        """Undo a loved artist finding fewer maybes."""
        seed = form.get("seed", "")
        if form.get("action") != "undo" or seed not in self._sampling().fewer_like:
            self._reply(HTTPStatus.BAD_REQUEST, "No such loved artist or action", "text/plain")
            return
        self.store.unlearn(seed)
        self._redirect("./")

    def _settings(self, form: dict[str, str]) -> None:
        name = form.get("name", "")
        action = form.get("action")
        if name not in SECRETS or action not in ("save", "clear"):
            self._reply(HTTPStatus.BAD_REQUEST, "No such setting or action", "text/plain")
            return
        value = form.get("value", "").strip() if action == "save" else ""
        if action == "save" and not value:
            self._reply(HTTPStatus.BAD_REQUEST, "Nothing to save", "text/plain")
            return
        self.store.set_secret(name, value)
        log.info("%s %s", "saved" if value else "cleared", name)
        self._redirect("./?settings=1")

    def _redirect(self, location: str, cookie: str | None = None) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _reply(self, status: HTTPStatus, body: str, content_type: str) -> None:
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        """Requests go to the log at debug level, not to stderr."""
        log.debug(format, *args)


def make_server(
    store: Store, settings: Settings, port: int, host: str = "0.0.0.0"
) -> ThreadingHTTPServer:
    """A server for the page on `port`, bound to the store and settings."""
    handler = type("BoundHandler", (Handler,), {"store": store, "settings": settings})
    return ThreadingHTTPServer((host, port), handler)


def serve(store: Store, settings: Settings, port: int) -> None:
    """Serve the page until stopped."""
    server = make_server(store, settings, port)
    log.info("serving on port %d", port)
    server.serve_forever()
