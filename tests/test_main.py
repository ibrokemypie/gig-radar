from datetime import UTC, datetime, timedelta
from datetime import time as clock

import pytest
from conftest import BASE_ENVIRONMENT, MELBOURNE

from live_shows import __main__ as main_module
from live_shows.refresh import Outcome


@pytest.fixture
def environment(monkeypatch, tmp_path):
    for name, value in BASE_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("LIVE_SHOWS_DB", str(tmp_path / "db.sqlite"))
    return tmp_path


def test_bad_settings_exit_two_and_say_which(environment, monkeypatch, capsys) -> None:
    monkeypatch.delenv("HOME_TIMEZONE")
    assert main_module.main(["refresh"]) == 2
    assert "HOME_TIMEZONE is not set" in capsys.readouterr().err


def test_refresh_exits_one_when_anything_failed(environment, monkeypatch, capsys) -> None:
    outcomes = [Outcome(failures=["Oztix: down"]), Outcome()]
    seen = []

    def fake_refresh(settings, services, store, dry_run):
        seen.append((store.db, dry_run, services.now().tzinfo))
        return outcomes.pop(0)

    monkeypatch.setattr(main_module, "refresh", fake_refresh)
    assert main_module.main(["refresh"]) == 1
    assert "FAILED Oztix: down" in capsys.readouterr().err
    assert main_module.main(["refresh"]) == 0
    assert seen[0][2] == MELBOURNE


def test_a_dry_run_works_on_a_copy_of_the_database(environment, monkeypatch) -> None:
    from live_shows.store import Store

    Store(environment / "db.sqlite").set_secret("ticketmaster_key", "K")
    used = []

    def fake_refresh(settings, services, store, dry_run):
        used.append((store.secret("ticketmaster_key"), dry_run))
        store.set_secret("ticketmaster_key", "changed")
        return Outcome()

    monkeypatch.setattr(main_module, "refresh", fake_refresh)
    assert main_module.main(["refresh", "--dry-run"]) == 0
    assert used == [("K", True)]
    assert Store(environment / "db.sqlite").secret("ticketmaster_key") == "K"

    (environment / "db.sqlite").unlink()
    assert main_module.main(["refresh", "--dry-run"]) == 0


def test_serve_hands_the_store_and_settings_to_the_page(environment, monkeypatch) -> None:
    served = []
    monkeypatch.setattr(
        "live_shows.web.serve", lambda store, settings, port: served.append((settings, port))
    )
    assert main_module.main(["serve", "--port", "9000"]) == 0
    assert served[0][1] == 9000
    assert served[0][0].place.state == "VIC"


def test_schedule_runs_the_refresh_daily_at_the_time_given(environment, monkeypatch) -> None:
    started = []
    monkeypatch.setattr(
        main_module, "run_daily", lambda at, zone, job: started.append((at, zone, job))
    )
    monkeypatch.setattr(main_module, "refresh", lambda *args, **kwargs: Outcome())
    assert main_module.main(["schedule", "--at", "07:15"]) == 0
    at, zone, job = started[0]
    assert (at, zone) == (clock(7, 15), MELBOURNE)
    assert job() == 0


def test_the_next_run_is_later_today_or_tomorrow() -> None:
    morning = datetime(2026, 10, 20, 5, 0, tzinfo=MELBOURNE)
    assert main_module.next_run(morning, clock(6, 30)) == morning.replace(hour=6, minute=30)
    evening = morning.replace(hour=20)
    assert main_module.next_run(evening, clock(6, 30)) == datetime(
        2026, 10, 21, 6, 30, tzinfo=MELBOURNE
    )


def test_run_daily_sleeps_until_each_run_then_runs_the_job() -> None:
    times = iter([datetime(2026, 10, 20, 6, 0, tzinfo=MELBOURNE)] * 3)
    slept, ran = [], []

    class Stop(Exception):
        pass

    def sleep(seconds: float) -> None:
        if len(slept) == 2:
            raise Stop
        slept.append(seconds)

    with pytest.raises(Stop):
        main_module.run_daily(
            clock(6, 30), MELBOURNE, lambda: ran.append(1), lambda zone: next(times), sleep
        )
    assert slept == [1800.0, 1800.0]
    assert ran == [1, 1]


@pytest.mark.parametrize(
    ("evening", "hours"),
    [
        (datetime(2026, 4, 4, 20, 0), 11.5),  # clocks go back an hour that night
        (datetime(2026, 10, 3, 20, 0), 9.5),  # clocks go forward an hour that night
        (datetime(2026, 6, 1, 20, 0), 10.5),
    ],
)
def test_the_sleep_to_the_next_run_is_real_time_across_daylight_saving(evening, hours) -> None:
    start = evening.replace(tzinfo=MELBOURNE)
    slept = []

    class Stop(Exception):
        pass

    def job() -> None:
        raise Stop

    with pytest.raises(Stop):
        main_module.run_daily(clock(6, 30), MELBOURNE, job, lambda zone: start, slept.append)
    assert slept == [hours * 3600]
    woke = (start.astimezone(UTC) + timedelta(seconds=slept[0])).astimezone(MELBOURNE)
    assert (woke.hour, woke.minute) == (6, 30)
