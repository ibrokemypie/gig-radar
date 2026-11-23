# gig-radar

gig-radar finds gigs near you by the artists you actually listen to.

It reads your listening from Plex or Last.fm and sorts your artists into
pinned, loved and maybe. Every morning it checks the ticket sellers for shows
near home. You get:

- a page listing the shows by month, pinned ones on top;
- a morning email when a pinned artist announces a show or goes on sale;
- a Monday email of loved and kept artists' shows and new maybes;
- shows on your calendar, and tickets marked from your seller emails.

How the tiers and sources work is in [docs/design.md](docs/design.md).

It is a single-user tool: one install serves one listener. Run one per person.

## Quick start with Docker Compose

1. Copy the examples:

       cp compose.example.yml compose.yml
       cp .env.example .env

2. In `compose.yml`, set the image to the one you use, or replace `image:`
   with `build: .` to build from this checkout.
3. In `.env`, set at least `HOME_LATITUDE`, `HOME_LONGITUDE`, `HOME_TIMEZONE`,
   `HOME_COUNTRY`, and your listening (`PLEX_TOKEN`, or `LIBRARY=lastfm` and
   `LASTFM_USER`).
4. Start it:

       docker compose up -d

5. Open http://localhost:8080 and press Settings. Type in your keys and
   passwords there.

The `refresh` service runs the daily check at 06:30 your time. To run one now:

    docker compose run --rm refresh refresh

Add `--dry-run` to work on a copy of the database, print the emails instead
of sending them, and leave your calendars alone.

## Settings

Settings come from the environment. `.env.example` lists every one with a
comment. Keys and passwords are typed on the page instead, and are never
shown again.

### Home

| Setting | Meaning |
|---|---|
| `HOME_LATITUDE`, `HOME_LONGITUDE` | Your home point, in decimal degrees. Required. |
| `HOME_RADIUS_KM` | How far away a show may be. Default 150. |
| `HOME_TIMEZONE` | Your time zone, such as `Europe/London`. Required. |
| `HOME_COUNTRY` | Two-letter country code, such as `GB`. Required. |
| `HOME_CITY` | Your city's name, optional. Its words are ignored when telling venues apart. |
| `HOME_STATE` | Australia only: `ACT`, `NSW`, `NT`, `QLD`, `SA`, `TAS`, `VIC` or `WA`. |
| `FESTIVALS_FILE` | A JSON file of festival lineup pages to use instead of the built-in list. Each entry may name a `country` and `state`; it is read only for a home there. |

Bandsintown and Ticketmaster work anywhere. In Australia, Oztix, Moshtix and
musick.com.au are read too, for your state. The built-in festival lineup pages
are Australian, and a city's page (Laneway's Melbourne one) is read only in
that state.

### The page

| Setting | Meaning |
|---|---|
| `LIVE_SHOWS_URL` | The address you open the page at. Emails and calendar events link to it. |
| `LIVE_SHOWS_PASSWORD` | A password for the page. Unset, there is no login. |
| `LIVE_SHOWS_ALLOWED_HOSTS` | Other `host:port` names the page answers to, comma-separated. |
| `LIVE_SHOWS_DB` | The database file. Default `/data/live-shows.sqlite`. |

### Listening

| Setting | Meaning |
|---|---|
| `LIBRARY` | `plex` (default) or `lastfm`. |
| `PLEX_URL` | Your Plex server. Default `http://plex:32400`. |
| `PLEX_TOKEN` | A Plex token. |
| `PLEX_PREFERENCES` | Instead of a token, the path to the server's `Preferences.xml`. |
| `PLEX_ACCOUNT_ID` | Whose plays count. Default `1`, the server's owner. |
| `PLEX_MUSIC_SECTION` | The music library's id. Unset, the first music library. |
| `LASTFM_USER` | Your Last.fm username. |

### Mail and calendars

Each part is optional. Without `SMTP_HOST` no email is sent, without
`IMAP_HOST` no inbox is read, and without `CALDAV_URL` no calendar is written.

| Setting | Meaning |
|---|---|
| `MAIL_ADDRESS` | Your address: the default sender, recipient and login. |
| `MAIL_PROVIDER` | `fastmail` fills in every host below from `MAIL_ADDRESS`. |
| `SMTP_HOST`, `SMTP_PORT` | Where mail is sent. Port 587 uses STARTTLS (default); 465 uses TLS. |
| `SMTP_USER` | The sending login. Default `MAIL_ADDRESS`. |
| `MAIL_FROM`, `MAIL_TO` | Who the emails come from and go to. Default `MAIL_ADDRESS`. |
| `IMAP_HOST`, `IMAP_USER` | The inbox read for ticket confirmations. |
| `CALDAV_URL` | Your CalDAV calendar home, such as `https://dav.example.com/calendars/you/`. |
| `CALDAV_USER` | The calendar login. Default `MAIL_ADDRESS`. |
| `CALDAV_CALENDAR` | The calendar this app makes and fills. Default `Live shows`. |
| `CALDAV_TICKETS_CALENDAR` | The calendar for shows you have tickets for. Default: your account's default calendar. |

## Keys and passwords

These go in Settings on the page.

- **Mail password**: used for sending, the inbox and the calendars. If one of
  them needs a different password, set its own (sending, inbox or calendar
  password) as well.
- **Ticketmaster API key**: optional, but it is the only source that knows
  when tickets go on sale. Sign up at
  [developer.ticketmaster.com](https://developer.ticketmaster.com/), open
  My Apps, and copy the Consumer Key.
- **Last.fm API key**: needed with `LIBRARY=lastfm`. Sign in and create an API
  account at [last.fm/api/account/create](https://www.last.fm/api/account/create),
  then copy the API key. The shared secret is not needed.

### Plex token

Plex explains how to find one in
[Finding an authentication token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/).
Put it in `PLEX_TOKEN`. If the app runs beside your Plex server, you can mount
the server's `Preferences.xml` and set `PLEX_PREFERENCES` instead.

### Last.fm instead of Plex

Last.fm gives your play counts and loved tracks. Loved tracks count as hearts.
Last.fm cannot tell which albums you played front to back, so with Last.fm
only 3 or more loved tracks pin an artist.

### Fastmail or any other provider

With Fastmail, set:

    MAIL_PROVIDER=fastmail
    MAIL_ADDRESS=you@fastmail.com

Then create an app password in Fastmail's settings, under Privacy & Security,
with access to mail and calendars, and type it on the page as the mail
password.

With any other provider, leave `MAIL_PROVIDER` empty and set `SMTP_HOST`,
`IMAP_HOST` and `CALDAV_URL` yourself. Your calendar home URL is in your
provider's help pages. If your server has no default calendar to find, set
`CALDAV_TICKETS_CALENDAR` to a calendar's name.

## The login

Set `LIVE_SHOWS_PASSWORD` to ask for a password before the page. A correct
password starts a session that lasts 30 days; changing the password signs
everyone out. The page also refuses any form not sent from the page itself.

The page answers only requests addressed to the host and port in
`LIVE_SHOWS_URL`, or one listed in `LIVE_SHOWS_ALLOWED_HOSTS`. Anything else
gets 421, which stops DNS rebinding. If you open the page at
`http://127.0.0.1:8080` instead of `http://localhost:8080`, add
`127.0.0.1:8080` to the allowed hosts.

The compose example publishes the page on this machine only. **Set
`LIVE_SHOWS_PASSWORD` before you expose it beyond this machine.** The login is
one shared password with no rate limit, so keep the page on your own network,
or put it behind a reverse proxy with TLS, and set `LIVE_SHOWS_URL` to the
address you use.

## Scheduling

The compose example runs `python -m live_shows schedule --at 06:30`, which
sleeps until that time in `HOME_TIMEZONE`, runs the refresh, and repeats.

To use cron or a systemd timer instead, run once a day:

    docker compose run --rm refresh refresh

A refresh exits with status 1 when any source failed, so a scheduler can flag
it. Bad settings exit with status 2 and name the setting.

## Without Docker

It needs Python 3.13 and nothing else at runtime.

    python -m live_shows serve --port 8080
    python -m live_shows refresh [--dry-run]
    python -m live_shows schedule --at 06:30

## Development

    uv run pytest                     # tests, 100% line and branch coverage
    uv run ruff check . && uv run ruff format --check .
    uv run pyright

## Licence

AGPL-3.0-or-later. See [LICENSE](LICENSE).
