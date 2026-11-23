"""The command line: `refresh` for one run, `schedule` to run it daily, `serve` for the page."""

import argparse
import logging
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, tzinfo
from datetime import time as clock
from pathlib import Path

from live_shows.http import WebFetcher
from live_shows.refresh import Services, refresh
from live_shows.settings import Settings, SettingsError, from_environment
from live_shows.store import Store

log = logging.getLogger("live_shows")


def run_refresh(settings: Settings, dry_run: bool) -> int:
    """One refresh; the exit status is 1 when it had any failure."""
    database = settings.database
    if dry_run:
        scratch = Path(tempfile.mkdtemp()) / database.name
        if database.exists():
            shutil.copy(database, scratch)
        database = scratch
    zone = settings.place.zone
    services = Services(fetcher=WebFetcher(), now=lambda: datetime.now(zone))
    outcome = refresh(settings, services, Store(database), dry_run=dry_run)
    for failure in outcome.failures:
        print(f"FAILED {failure}", file=sys.stderr)
    print(outcome.counts, outcome.sent)
    return 1 if outcome.failures else 0


def next_run(now: datetime, at: clock) -> datetime:
    """The next time of day `at` after `now`, in `now`'s zone."""
    today = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    return today if today > now else today + timedelta(days=1)


def seconds_between(start: datetime, end: datetime) -> float:
    """Elapsed seconds from one time to another, across any change of UTC offset.

    Python subtracts two times in one zone by their clocks, ignoring a change of offset
    between them, so both are taken to UTC first.
    """
    return (end.astimezone(UTC) - start.astimezone(UTC)).total_seconds()


def run_daily(
    at: clock,
    zone: tzinfo,
    job: Callable[[], object],
    now: Callable[[tzinfo], datetime] = datetime.now,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Run `job` each day at `at` local time, for ever.

    The sleep is real elapsed time, so a daylight-saving change between now and the
    run does not move it off `at`.
    """
    while True:
        start = now(zone)
        due = next_run(start, at)
        log.info("next refresh at %s", due.isoformat())
        sleep(seconds_between(start, due))
        job()


def main(argv: list[str] | None = None) -> int:
    """Run a command; the exit status is 1 when a refresh had any failure, 2 for bad settings."""
    parser = argparse.ArgumentParser(prog="live_shows")
    commands = parser.add_subparsers(dest="command", required=True)
    refresh_command = commands.add_parser("refresh", help="find shows, then mail and calendar them")
    refresh_command.add_argument(
        "--dry-run",
        action="store_true",
        help="work on a copy of the database, print the emails and write no calendar",
    )
    schedule_command = commands.add_parser("schedule", help="refresh every day at a set time")
    schedule_command.add_argument(
        "--at",
        type=clock.fromisoformat,
        default=clock(6, 30),
        help="the local time of day to refresh, HH:MM (default 06:30)",
    )
    serve_command = commands.add_parser("serve", help="serve the page")
    serve_command.add_argument("--port", type=int, default=8080)
    arguments = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        settings = from_environment(os.environ)
    except SettingsError as error:
        print(f"live_shows: {error}", file=sys.stderr)
        return 2
    if arguments.command == "serve":
        from live_shows.web import serve

        serve(Store(settings.database), settings, arguments.port)
        return 0
    if arguments.command == "schedule":
        run_daily(arguments.at, settings.place.zone, lambda: run_refresh(settings, False))
        return 0
    return run_refresh(settings, arguments.dry_run)


if __name__ == "__main__":
    sys.exit(main())
