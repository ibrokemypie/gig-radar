"""The app's SQLite state: secrets, what was seen when, tickets, what was sent, and caches.

No source says when a show was announced, so the first run that saw a (day, artist) pair
stands in for it. Secrets typed on the settings page are kept here and never shown again.
"""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import urllib.parse
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from live_shows.http import Fetcher
from live_shows.model import Show
from live_shows.names import normalise

# The mail password serves SMTP, IMAP and CalDAV alike unless that service has its own.
SECRETS = (
    "mail_password",
    "smtp_password",
    "imap_password",
    "caldav_password",
    "ticketmaster_key",
    "lastfm_key",
)
SERVICES = ("smtp", "imap", "caldav")
# Names a secret was saved under before.
_RENAMED = {"fastmail_app_password": "mail_password"}
# Query parameters that carry credentials, left out of cache keys.
_SECRET_PARAMS = ("X-Plex-Token",)
CHOICES = ("kept", "trimmed")

_SCHEMA = """
create table if not exists settings (name text primary key, value text not null);
create table if not exists sightings (
    day text not null, artist_key text not null, first_seen text not null,
    primary key (day, artist_key));
create table if not exists shows (
    show_id text primary key, day text not null, record text not null);
create table if not exists tickets (
    day text not null, artist_key text not null, how text not null, at text not null,
    primary key (day, artist_key));
create table if not exists sent (kind text not null, ref text not null, at text not null,
    primary key (kind, ref));
create table if not exists cache (
    url text primary key, fetched_at text not null, body text not null);
create table if not exists runs (
    id integer primary key, started_at text not null, finished_at text, ok integer,
    summary text);
create table if not exists choices (
    artist_key text primary key, name text not null, choice text not null,
    seeds text not null, at text not null);
create table if not exists samples (
    artist_key text primary key, fetched_at text not null, record text not null);
create table if not exists unlearned (seed text primary key, at text not null);
create table if not exists sessions (
    token_hash text primary key, tag text not null, expires_at text not null);
create table if not exists calendar (
    uid text not null, collection text not null, digest text not null,
    primary key (uid, collection));
"""


def now() -> datetime:
    """The time in UTC, to the second."""
    return datetime.now(UTC).replace(microsecond=0)


class Store:
    """The app's database file, created on first use and readable only by its owner."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=0o600, exist_ok=True)
        os.chmod(path, 0o600)
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        # Cache rows once kept a URL's credentials in their key.
        self.db.execute("delete from cache where url like ?", (f"%{_SECRET_PARAMS[0]}=%",))
        for old, new in _RENAMED.items():
            self.db.execute("update or ignore settings set name = ? where name = ?", (new, old))

    @contextmanager
    def transaction(self) -> Generator[None]:
        """Run the block's writes as one transaction."""
        self.db.execute("begin immediate")
        try:
            yield
        except BaseException:
            self.db.execute("rollback")
            raise
        self.db.execute("commit")

    # --- Secrets typed on the settings page -------------------------------------------

    def set_secret(self, name: str, value: str) -> None:
        """Save a secret, or forget it when `value` is empty."""
        if name not in SECRETS:
            raise KeyError(name)
        if value:
            self.db.execute(
                "insert into settings values (?, ?) on conflict (name) do update set value = ?",
                (name, value, value),
            )
        else:
            self.db.execute("delete from settings where name = ?", (name,))

    def secret(self, name: str) -> str | None:
        """A saved secret's value, for the code that uses it; never for a page."""
        row = self.db.execute("select value from settings where name = ?", (name,)).fetchone()
        return row["value"] if row else None

    def password(self, service: str) -> str | None:
        """The password for "smtp", "imap" or "caldav": its own, else the mail password."""
        if service not in SERVICES:
            raise KeyError(service)
        return self.secret(f"{service}_password") or self.secret("mail_password")

    def secret_status(self) -> dict[str, bool]:
        """Whether each secret is saved: all the settings page may know of them."""
        return {name: self.secret(name) is not None for name in SECRETS}

    # --- Page sessions ----------------------------------------------------------------

    def start_session(self, password: str, days: int) -> str:
        """A new random session token, good for `days` and only while `password` stands."""
        token = secrets.token_urlsafe(32)
        expires = now() + timedelta(days=days)
        self.db.execute(
            "insert into sessions values (?, ?, ?)",
            (_digest(token), self._password_tag(password), expires.isoformat()),
        )
        return token

    def has_session(self, token: str, password: str) -> bool:
        """Whether a token is a live session started under this password; drops expired ones."""
        self.db.execute("delete from sessions where expires_at <= ?", (now().isoformat(),))
        row = self.db.execute(
            "select tag from sessions where token_hash = ?", (_digest(token),)
        ).fetchone()
        return row is not None and hmac.compare_digest(row["tag"], self._password_tag(password))

    def _password_tag(self, password: str) -> str:
        """The password keyed with this database's own random secret, made on first use."""
        row = self.db.execute("select value from settings where name = 'session_key'").fetchone()
        if row is None:
            key = secrets.token_hex(32)
            self.db.execute("insert into settings values ('session_key', ?)", (key,))
        else:
            key = row["value"]
        return hmac.new(key.encode(), password.encode(), hashlib.sha256).hexdigest()

    # --- Shows: the current list and when each was first seen --------------------------

    def record_shows(self, shows: list[Show], seen_at: datetime) -> list[Show]:
        """Replace the current list with `shows`; return those with a pair never seen before.

        A show with tickets stays until its day has passed, even once no seller
        lists it, unless a show in the new list takes its place.
        """
        new = []
        listed_pairs = {pair for show in shows for pair in show.pairs}
        kept = [
            record
            for record in self.shows()
            if record["bought"]
            and record["day"] >= seen_at.date().isoformat()
            and not {tuple(pair) for pair in record["pairs"]} & listed_pairs
        ]
        with self.transaction():
            self.db.execute("delete from shows")
            for record in kept:
                record.pop("bought")
                self.db.execute(
                    "insert into shows values (?, ?, ?)",
                    (record["show_id"], record["day"], json.dumps(record)),
                )
            for show in shows:
                fresh = False
                for day, key in show.pairs:
                    cursor = self.db.execute(
                        "insert or ignore into sightings values (?, ?, ?)",
                        (day, key, seen_at.isoformat()),
                    )
                    fresh = fresh or cursor.rowcount == 1
                if fresh:
                    new.append(show)
                self.db.execute(
                    "insert or replace into shows values (?, ?, ?)",
                    (show.show_id, show.day.isoformat(), json.dumps(show_record(show))),
                )
        return new

    def shows(self) -> list[dict[str, Any]]:
        """The current list as records, each with whether it has tickets, by date."""
        bought = self._bought_pairs()
        records = []
        for row in self.db.execute("select record from shows order by day"):
            record = json.loads(row["record"])
            record["bought"] = any(tuple(pair) in bought for pair in record["pairs"])
            record.setdefault("pinned", False)  # records saved before pinning carry none
            for act in record["acts"]:  # records saved before acts carried these
                act.setdefault("key", normalise(act["name"]))
                act.setdefault("found_through", [])
                act.setdefault("pinned", False)
            records.append(record)
        return records

    def first_seen(self, show: Show) -> datetime:
        """When a show's earliest pair was first seen."""
        rows = [
            self.db.execute(
                "select first_seen from sightings where day = ? and artist_key = ?", pair
            ).fetchone()
            for pair in show.pairs
        ]
        return min(datetime.fromisoformat(row["first_seen"]) for row in rows if row)

    # --- Tickets -----------------------------------------------------------------------

    def mark_bought(self, pairs: list[tuple[str, str]], how: str) -> None:
        """Record tickets for the show these pairs name."""
        at = now().isoformat()
        for day, key in pairs:
            self.db.execute(
                "insert or ignore into tickets values (?, ?, ?, ?)", (day, key, how, at)
            )

    def unmark_bought(self, pairs: list[tuple[str, str]]) -> None:
        """Forget tickets for the show these pairs name (a press of Got tickets undone)."""
        for pair in pairs:
            self.db.execute("delete from tickets where day = ? and artist_key = ?", pair)

    def is_bought(self, show: Show) -> bool:
        """Whether a show has tickets, by any of its pairs."""
        return any(pair in self._bought_pairs() for pair in show.pairs)

    def _bought_pairs(self) -> set[tuple[str, str]]:
        return {(row["day"], row["artist_key"]) for row in self.db.execute("select * from tickets")}

    # --- Maybes sampled: kept, trimmed, and the samples themselves ----------------------

    def set_choice(self, key: str, name: str, choice: str, seeds: list[str]) -> None:
        """Record that an artist was kept or trimmed, with the loved artists it was found by."""
        if choice not in CHOICES:
            raise ValueError(choice)
        self.db.execute(
            "insert or replace into choices values (?, ?, ?, ?, ?)",
            (key, name, choice, json.dumps(seeds), now().isoformat()),
        )

    def clear_choice(self, key: str) -> None:
        """Forget a keep or a trim (its Undo)."""
        self.db.execute("delete from choices where artist_key = ?", (key,))

    def choices(self) -> dict[str, dict[str, Any]]:
        """Each artist kept or trimmed: name, choice, seeds and when."""
        return {
            row["artist_key"]: {
                "name": row["name"],
                "choice": row["choice"],
                "seeds": json.loads(row["seeds"]),
                "at": row["at"],
            }
            for row in self.db.execute("select * from choices")
        }

    def save_samples(self, key: str, record: dict[str, Any]) -> None:
        """Keep an artist's samples, stamped with when they were fetched."""
        self.db.execute(
            "insert or replace into samples values (?, ?, ?)",
            (key, now().isoformat(), json.dumps(record)),
        )

    def samples(self) -> dict[str, dict[str, Any]]:
        """Each artist's samples record, with `fetched_at` added."""
        return {
            row["artist_key"]: {**json.loads(row["record"]), "fetched_at": row["fetched_at"]}
            for row in self.db.execute("select * from samples")
        }

    def unlearn(self, seed: str) -> None:
        """Let a loved artist find new maybes again, counting trims afresh from now."""
        self.db.execute("insert or replace into unlearned values (?, ?)", (seed, now().isoformat()))

    def unlearned(self) -> dict[str, str]:
        """Each loved artist whose learning was undone, with when."""
        return {row["seed"]: row["at"] for row in self.db.execute("select * from unlearned")}

    # --- What was sent, so nothing is sent twice --------------------------------------

    def was_sent(self, kind: str, ref: str) -> bool:
        """Whether a message of this kind about this thing went out already."""
        query = "select 1 from sent where kind = ? and ref = ?"
        return self.db.execute(query, (kind, ref)).fetchone() is not None

    def mark_sent(self, kind: str, refs: list[str]) -> None:
        """Record that messages of this kind went out about these things."""
        at = now().isoformat()
        for ref in refs:
            self.db.execute("insert or ignore into sent values (?, ?, ?)", (kind, ref, at))

    # --- What is on the calendars --------------------------------------------------------

    def calendar_entries(self, collection: str) -> dict[str, str]:
        """Each event uid this app last put in a calendar, with a digest of what it wrote."""
        rows = self.db.execute(
            "select uid, digest from calendar where collection = ?", (collection,)
        )
        return {row["uid"]: row["digest"] for row in rows}

    def adopt_calendar_entries(self, collection: str) -> None:
        """Move rows kept under another spelling of this href ("%40" for "@") onto it.

        Where both spellings hold a uid, the row under this href stays.
        """
        spellings = [
            row["collection"]
            for row in self.db.execute("select distinct collection from calendar")
            if row["collection"] != collection
            and urllib.parse.unquote(row["collection"]) == urllib.parse.unquote(collection)
        ]
        for spelling in spellings:
            self.db.execute(
                "update or ignore calendar set collection = ? where collection = ?",
                (collection, spelling),
            )
            self.db.execute("delete from calendar where collection = ?", (spelling,))

    def set_calendar_entry(self, collection: str, uid: str, digest: str | None) -> None:
        """Record what was written for an event uid, or that it was deleted (None)."""
        if digest is None:
            self.db.execute(
                "delete from calendar where uid = ? and collection = ?", (uid, collection)
            )
        else:
            self.db.execute(
                "insert or replace into calendar values (?, ?, ?)", (uid, collection, digest)
            )

    # --- Runs ----------------------------------------------------------------------------

    def start_run(self) -> int:
        """Record a run starting; returns its id."""
        cursor = self.db.execute("insert into runs (started_at) values (?)", (now().isoformat(),))
        return int(cursor.lastrowid or 0)

    def finish_run(self, run_id: int, ok: bool, summary: dict[str, Any]) -> None:
        """Record how a run ended and what it found."""
        self.db.execute(
            "update runs set finished_at = ?, ok = ?, summary = ? where id = ?",
            (now().isoformat(), int(ok), json.dumps(summary), run_id),
        )

    def last_run(self) -> dict[str, Any] | None:
        """The latest run, finished or not, with its summary parsed."""
        row = self.db.execute("select * from runs order by id desc limit 1").fetchone()
        if row is None:
            return None
        return {**dict(row), "summary": json.loads(row["summary"] or "{}")}


class CachedFetcher:
    """A Fetcher that answers from the store while a page is younger than `max_age`."""

    def __init__(self, fetcher: Fetcher, store: Store, max_age: timedelta) -> None:
        self.fetcher = fetcher
        self.store = store
        self.max_age = max_age

    def text(self, url: str, headers: dict[str, str] | None = None) -> str:
        """The cached body of `url`, fetched again once it is too old.

        Rows are keyed by the URL without its credentials, so no token is stored.
        """
        key = cache_key(url)
        row = self.store.db.execute(
            "select fetched_at, body from cache where url = ?", (key,)
        ).fetchone()
        if row and now() - datetime.fromisoformat(row["fetched_at"]) < self.max_age:
            return row["body"]
        body = self.fetcher.text(url, headers)
        self.store.db.execute(
            "insert or replace into cache values (?, ?, ?)", (key, now().isoformat(), body)
        )
        return body


def cache_key(url: str) -> str:
    """A URL without the query parameters that carry credentials."""
    parts = urllib.parse.urlsplit(url)
    query = [
        (name, value)
        for name, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if name not in _SECRET_PARAMS
    ]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def _digest(token: str) -> str:
    """How a session token is kept: hashed, so the database alone signs nobody in."""
    return hashlib.sha256(token.encode()).hexdigest()


def show_record(show: Show) -> dict[str, Any]:
    """A show as the page and the emails read it: plain values, tier and reasons included."""
    record = asdict(show)
    record.update(
        day=show.day.isoformat(),
        on_sale_at=show.on_sale_at.isoformat() if show.on_sale_at else None,
        tier=show.tier.value,
        pinned=show.pinned,
        show_id=show.show_id,
        pairs=show.pairs,
        acts=[
            {
                "name": act.artist.name,
                "listed_as": act.listed_as,
                "tier": act.artist.tier.value,
                "pinned": act.artist.pinned,
                "why": act.artist.why,
                "key": act.artist.key,
                "found_through": act.artist.found_through,
            }
            for act in show.acts
        ],
    )
    return record
