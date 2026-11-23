from pathlib import Path

import pytest
from conftest import ADDRESS, BASE_ENVIRONMENT, MELBOURNE, PLACE, settings

from live_shows.settings import CalDav, Imap, SettingsError, Smtp, from_environment


def test_the_base_settings_and_their_defaults() -> None:
    found = from_environment({**BASE_ENVIRONMENT, "LIVE_SHOWS_URL": ""})
    assert found.place == PLACE
    assert found.place.zone == MELBOURNE
    assert found.database == Path("/data/live-shows.sqlite")
    assert found.app_url == "http://localhost:8080/"
    assert found.library == "plex"
    assert found.plex.account == "1"
    assert found.plex.section == "3"
    assert found.festivals is None
    assert found.password is None
    assert found.lastfm_user is None


def test_the_fastmail_preset_fills_every_host_from_the_address() -> None:
    found = settings()
    assert found.smtp == Smtp("smtp.fastmail.com", 587, ADDRESS, ADDRESS, ADDRESS)
    assert found.imap == Imap("imap.fastmail.com", ADDRESS)
    assert found.caldav == CalDav(
        "https://caldav.fastmail.com/dav/calendars/user/listener%40example.com/",
        ADDRESS,
        "Live shows",
        None,
    )


def test_a_set_value_wins_over_the_preset() -> None:
    found = settings(SMTP_PORT="465", IMAP_HOST="imap.example.com", CALDAV_TICKETS_CALENDAR="Gigs")
    assert found.smtp is not None and found.smtp.port == 465
    assert found.imap is not None and found.imap.host == "imap.example.com"
    assert found.caldav is not None and found.caldav.tickets_calendar == "Gigs"


def test_generic_mail_settings_with_their_own_users() -> None:
    found = settings(
        MAIL_PROVIDER="",
        MAIL_ADDRESS="",
        SMTP_HOST="mail.example.com",
        SMTP_USER="sender-login",
        MAIL_TO="me@example.com",
        MAIL_FROM="shows@example.com",
        IMAP_HOST="imap.example.com",
        IMAP_USER="inbox-login",
        CALDAV_URL="https://dav.example.com/calendars/me",
        CALDAV_USER="dav-login",
        CALDAV_CALENDAR="Gigs",
    )
    assert found.smtp == Smtp("mail.example.com", 587, "sender-login", "shows@example.com",
                              "me@example.com")  # fmt: skip
    assert found.imap == Imap("imap.example.com", "inbox-login")
    assert found.caldav == CalDav(
        "https://dav.example.com/calendars/me/", "dav-login", "Gigs", None
    )


def test_without_hosts_mail_imap_and_calendars_are_off() -> None:
    found = settings(MAIL_PROVIDER="")
    assert (found.smtp, found.imap, found.caldav) == (None, None, None)


def test_the_smtp_user_falls_back_to_the_sender() -> None:
    found = settings(MAIL_PROVIDER="", MAIL_ADDRESS="", SMTP_HOST="h", MAIL_TO="to@example.com")
    assert found.smtp == Smtp("h", 587, "to@example.com", "to@example.com", "to@example.com")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"HOME_TIMEZONE": ""}, "HOME_TIMEZONE is not set"),
        ({"HOME_TIMEZONE": "Mars/Olympus"}, "not a known time zone"),
        ({"HOME_TIMEZONE": "../etc"}, "not a known time zone"),
        ({"HOME_LATITUDE": "south"}, "HOME_LATITUDE is not a number"),
        ({"HOME_STATE": "VICTORIA"}, "HOME_STATE must be one of"),
        ({"HOME_STATE": ""}, "HOME_STATE must be one of"),
        ({"LIBRARY": "spotify"}, "LIBRARY must be one of"),
        ({"PLEX_TOKEN": ""}, "neither PLEX_TOKEN nor PLEX_PREFERENCES"),
        ({"LIBRARY": "lastfm"}, "LASTFM_USER is not set"),
        ({"MAIL_PROVIDER": "gmail"}, "MAIL_PROVIDER must be one of"),
        ({"MAIL_ADDRESS": ""}, "MAIL_ADDRESS is not set"),
        ({"MAIL_PROVIDER": "", "MAIL_ADDRESS": "", "SMTP_HOST": "h"}, "neither MAIL_TO"),
        ({"MAIL_PROVIDER": "", "MAIL_ADDRESS": "", "IMAP_HOST": "h"}, "IMAP_USER is not set"),
        ({"MAIL_PROVIDER": "", "MAIL_ADDRESS": "", "CALDAV_URL": "h"}, "CALDAV_USER is not set"),
        ({"SMTP_PORT": "submission"}, "SMTP_PORT is not a number"),
        ({"HOME_LATITUDE": "91"}, "HOME_LATITUDE must be between -90 and 90"),
        ({"HOME_LATITUDE": "nan"}, "HOME_LATITUDE must be between"),
        ({"HOME_LONGITUDE": "-180.5"}, "HOME_LONGITUDE must be between -180 and 180"),
        ({"HOME_LONGITUDE": "inf"}, "HOME_LONGITUDE must be between"),
        ({"HOME_RADIUS_KM": "0"}, "HOME_RADIUS_KM must be a number of km above 0"),
        ({"HOME_RADIUS_KM": "-5"}, "HOME_RADIUS_KM must be"),
        ({"HOME_RADIUS_KM": "nan"}, "HOME_RADIUS_KM must be"),
        ({"HOME_RADIUS_KM": "inf"}, "HOME_RADIUS_KM must be"),
    ],
)
def test_a_bad_or_missing_setting_names_itself(changes: dict, message: str) -> None:
    with pytest.raises(SettingsError, match=message):
        settings(**changes)


def test_outside_australia_there_is_no_state_and_any_radius() -> None:
    found = settings(HOME_COUNTRY="gb", HOME_STATE="", HOME_TIMEZONE="Europe/London",
                     HOME_RADIUS_KM="40.5")  # fmt: skip
    assert found.place.country == "GB"
    assert found.place.state is None
    assert found.place.radius_km == 40.5
    assert not found.place.in_australia


def test_plex_preferences_and_lastfm_and_the_rest() -> None:
    found = settings(PLEX_TOKEN="", PLEX_PREFERENCES="/plex/Preferences.xml", PLEX_ACCOUNT_ID="2",
                     PLEX_URL="http://plex:32400/", FESTIVALS_FILE="/data/festivals.json",
                     LIVE_SHOWS_PASSWORD="pw", LASTFM_USER="listener")  # fmt: skip
    assert found.plex.preferences == Path("/plex/Preferences.xml")
    assert found.plex.url == "http://plex:32400"
    assert found.plex.account == "2"
    assert found.festivals == Path("/data/festivals.json")
    assert found.password == "pw"
    assert found.lastfm_user == "listener"


def test_near_takes_geelong_and_ballarat_but_not_sydney_or_albury() -> None:
    assert PLACE.near(-38.1499, 144.3617)  # Geelong
    assert PLACE.near(-37.5622, 143.8503)  # Ballarat
    assert not PLACE.near(-33.8988, 151.2093)  # Sydney
    assert not PLACE.near(-36.0737, 146.9135)  # Albury


def test_the_home_city_and_state_words() -> None:
    assert settings(HOME_CITY="Melbourne").place.words == {"melbourne", "vic"}
    assert settings().place.words == {"vic"}
    outside = settings(HOME_COUNTRY="GB", HOME_STATE="", HOME_TIMEZONE="Europe/London")
    assert outside.place.words == set()


def test_the_ends_of_each_range_are_allowed() -> None:
    edge = settings(HOME_LATITUDE="-90", HOME_LONGITUDE="180", HOME_RADIUS_KM="0.5")
    assert (edge.place.latitude, edge.place.longitude, edge.place.radius_km) == (-90, 180, 0.5)
