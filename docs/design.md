# How Live shows decides

This is how the app sorts artists into tiers, finds shows and tells you about
them. The thresholds are constants in `live_shows/library.py` and
`live_shows/albums.py`.

## Tiers

Every artist you play gets at most one tier. Being in your library counts for
nothing on its own.

| Tier | An artist is in it when |
|---|---|
| Pinned | 2 or more of its albums were played front to back, or one album twice, or 3 or more of its tracks are hearted |
| Loved | 5 or more of its songs were played 3 or more times each, or 3 or more tracks are hearted, or it is pinned |
| Maybe | it has 2 or more plays, or ListenBrainz lists it as similar to 3 or more loved artists |

A pinned artist counts as loved everywhere else: the calendar, the Monday
email and finding maybes.

"Hearted" means a track rated 4 stars or more in Plex, or a loved track on
Last.fm.

### Front to back

A front-to-back listen is a run of one album's tracks in album order (disc,
then track):

- later tracks only, and a repeat of the same track is ignored;
- no other album's track between, and no gap over 30 minutes;
- covering at least 80% of an album of 4 or more tracks.

Only albums put on deliberately count. A run is "chained", and does not count,
when the play just before it ends another artist's whole-album run, or the play
just after it starts one at its first track, within 30 minutes either way. That
is how album radio moves from one album to the next. The album's artist gets the
credit; a Various Artists album credits nobody.

Last.fm cannot tell albums played through, so with Last.fm only hearts pin an
artist.

### Maybes found through loved artists

For each loved artist with a MusicBrainz id, the app asks ListenBrainz for
similar artists. An artist similar to 3 or more loved artists becomes a maybe.

On the page you sample a maybe and pick Keep or Not for me:

- **Keep** keeps the artist a maybe for good, even when nothing finds it any
  more.
- **Not for me** folds its shows under "Trimmed".
- Once 3 artists found through one loved artist are trimmed and none kept,
  that loved artist finds no new maybes. The page says so, with Undo.

## Shows

- A show is a night, or a festival day, with at least one of your artists on
  the bill. Listings from different sellers are merged when they share the day
  and a venue word or one of your artists.
- Venue words like "hotel" and "the", the home city and state (`HOME_CITY`,
  `HOME_STATE`) and each listing's own locality are ignored when telling venues
  apart.
- A show is identified by its (day, artist) pairs, since sellers share no
  event id and spell venues differently.
- The window is a year ahead.
- A show is loved when any loved artist plays it, and pinned when any pinned
  artist does.

### Sources

| Source | Where | What it reads |
|---|---|---|
| Bandsintown | anywhere | each artist's dates within the radius of home |
| Ticketmaster | anywhere | music events within the radius, with on-sale times (needs a free key) |
| Oztix | Australia | music events in the home state |
| Moshtix | Australia | music events in the home state |
| musick.com.au | Australia | your artists' gigs in the home state |
| Festival lineups | each page's country and state | which acts play the home state's day |

A source that fails, or returns nothing at all, fails the run, so a broken
scraper shows up rather than reading as a quiet week. The other sources still
run.

## What reaches you

- **The morning email** lists every new pinned show, and pinned shows going on
  sale in the next 36 hours. One email, never one per show.
- **The Monday email** lists every loved and kept show you have no tickets
  for, and counts the new maybes in one line linking to the page.
- **Nothing is sent about a show you have tickets for.**
- **The calendars:** loved shows and shows with tickets go on the "Live shows"
  calendar; shows with tickets also go on your main (or named) calendar. Maybes
  stay off both unless you have tickets. Only events the app wrote are ever
  changed or deleted, and past shows are left alone.
- **Tickets** are marked from a seller's confirmation email that names both an
  act and the day, or with the page's Got tickets button.

## Samples

An opened maybe shows three music videos of the artist's own, or its top three
songs when it has none, from Apple's public iTunes Search API in your country's
store. Only the artist's own tracks count: features and collaborations are left
out. Samples are refetched monthly.
