import json
import os
import stat
from datetime import timedelta

import pytest
from conftest import FakeWeb, artist, at, show

from live_shows import store as store_module
from live_shows.model import Tier
from live_shows.store import CachedFetcher, Store, show_record


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "data" / "live-shows.sqlite")


def test_the_database_is_private_to_its_owner(tmp_path) -> None:
    path = tmp_path / "data" / "db.sqlite"
    Store(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_secrets_save_replace_clear_and_only_report_saved(store: Store) -> None:
    assert not any(store.secret_status().values())
    store.set_secret("mail_password", "one")
    store.set_secret("mail_password", "two")
    assert store.secret("mail_password") == "two"
    assert store.secret_status()["mail_password"] is True
    store.set_secret("mail_password", "")
    assert store.secret("mail_password") is None
    with pytest.raises(KeyError):
        store.set_secret("plex_token", "x")


def test_a_service_uses_its_own_password_else_the_mail_password(store: Store) -> None:
    assert store.password("smtp") is None
    store.set_secret("mail_password", "shared")
    store.set_secret("caldav_password", "calendar")
    assert store.password("smtp") == "shared"
    assert store.password("imap") == "shared"
    assert store.password("caldav") == "calendar"
    with pytest.raises(KeyError):
        store.password("ftp")


def test_a_password_saved_under_its_old_name_is_kept(tmp_path) -> None:
    path = tmp_path / "db.sqlite"
    Store(path).db.execute("insert into settings values ('fastmail_app_password', 'old')")
    assert Store(path).secret("mail_password") == "old"


def test_a_show_is_new_only_the_first_time_any_of_its_pairs_is_seen(store: Store) -> None:
    first = show(acts=[artist("La Dispute")])
    assert store.record_shows([first], at("2026-10-20 07:00")) == [first]
    assert store.record_shows([first], at("2026-10-21 07:00")) == []

    with_support = show(acts=[artist("La Dispute"), artist("Special Features")])
    assert store.record_shows([with_support], at("2026-10-22 07:00")) == [with_support]
    assert store.first_seen(with_support) == at("2026-10-20 07:00")


def test_the_list_is_replaced_but_a_ticketed_show_stays_until_its_day(store: Store) -> None:
    gone_bought = show("2026-11-08", [artist("Dance Gavin Dance")])
    gone_unbought = show("2026-11-09", [artist("Telenova")])
    past_bought = show("2026-10-01", [artist("Old Band")])
    store.record_shows([gone_bought, gone_unbought, past_bought], at("2026-09-01 07:00"))
    store.mark_bought(gone_bought.pairs, "button")
    store.mark_bought(past_bought.pairs, "button")

    store.record_shows([show("2026-12-01", [artist("Neck Deep")])], at("2026-10-20 07:00"))

    listed = {(record["day"], record["bought"]) for record in store.shows()}
    assert listed == {("2026-11-08", True), ("2026-12-01", False)}


def test_a_relisted_ticketed_show_is_not_kept_twice(store: Store) -> None:
    bought = show("2026-11-08", [artist("Dance Gavin Dance")])
    store.record_shows([bought], at("2026-10-20 07:00"))
    store.mark_bought(bought.pairs, "email")
    store.record_shows([bought], at("2026-10-21 07:00"))
    assert [record["bought"] for record in store.shows()] == [True]
    assert store.is_bought(bought)
    store.unmark_bought(bought.pairs)
    assert not store.is_bought(bought)


def test_a_failed_write_leaves_the_list_as_it_was(store: Store, monkeypatch) -> None:
    store.record_shows([show()], at("2026-10-20 07:00"))
    bad = show("2026-11-01", [artist("Neck Deep")])
    monkeypatch.setattr(
        store_module, "show_record", lambda _show: (_ for _ in ()).throw(ValueError())
    )
    with pytest.raises(ValueError):
        store.record_shows([bad], at("2026-10-21 07:00"))
    assert [record["day"] for record in store.shows()] == ["2026-10-20"]


def test_sent_messages_are_remembered(store: Store) -> None:
    assert not store.was_sent("new", "a")
    store.mark_sent("new", ["a", "b"])
    store.mark_sent("new", ["a"])
    assert store.was_sent("new", "a")
    assert not store.was_sent("digest", "a")


def test_calendar_entries_are_kept_per_calendar(store: Store) -> None:
    store.set_calendar_entry("/live/", "uid-1", "d1")
    store.set_calendar_entry("/main/", "uid-1", "d2")
    assert store.calendar_entries("/live/") == {"uid-1": "d1"}
    store.set_calendar_entry("/live/", "uid-1", None)
    assert store.calendar_entries("/live/") == {}
    assert store.calendar_entries("/main/") == {"uid-1": "d2"}


def test_rows_under_another_spelling_of_a_calendar_move_onto_it(store: Store) -> None:
    quoted, listed = "/cal/listener%40example.com/live/", "/cal/listener@example.com/live/"
    store.set_calendar_entry(quoted, "only-quoted", "a")
    store.set_calendar_entry(quoted, "both", "old")
    store.set_calendar_entry(listed, "both", "new")
    store.set_calendar_entry("/cal/listener@example.com/personal/", "other", "c")

    store.adopt_calendar_entries(listed)

    assert store.calendar_entries(listed) == {"only-quoted": "a", "both": "new"}
    assert store.calendar_entries(quoted) == {}
    assert store.calendar_entries("/cal/listener@example.com/personal/") == {"other": "c"}


def test_runs_are_recorded(store: Store) -> None:
    assert store.last_run() is None
    run_id = store.start_run()
    assert store.last_run()["summary"] == {}
    store.finish_run(run_id, False, {"failures": ["Oztix: down"]})
    last = store.last_run()
    assert last["ok"] == 0
    assert last["summary"] == {"failures": ["Oztix: down"]}


def test_cached_fetcher_answers_from_the_store_until_the_page_is_old(store, monkeypatch) -> None:
    clock = [at("2026-10-20 07:00")]
    monkeypatch.setattr(store_module, "now", lambda: clock[0])
    web = FakeWeb({"lb": "answer"})
    cached = CachedFetcher(web, store, timedelta(days=30))

    assert cached.text("https://lb/1") == "answer"
    assert cached.text("https://lb/1") == "answer"
    assert len(web.requests) == 1
    clock[0] += timedelta(days=31)
    cached.text("https://lb/1")
    assert len(web.requests) == 2


def test_show_record_carries_tier_reasons_and_sale_time() -> None:
    record = show_record(show(on_sale_at=at("2026-10-24 10:00")))
    assert record["tier"] == "love"
    assert record["acts"][0]["why"] == "100 plays · 0 songs on repeat"
    assert record["on_sale_at"] == "2026-10-24T10:00:00+11:00"
    assert record["show_id"] == "2026-10-20~la-dispute"
    assert show_record(show())["on_sale_at"] is None


def test_a_keep_or_trim_is_recorded_replaced_and_undone(store: Store, monkeypatch) -> None:
    monkeypatch.setattr(store_module, "now", lambda: at("2026-11-03 10:00"))
    store.set_choice("spiritbox", "Spiritbox", "trimmed", ["La Dispute"])
    store.set_choice("telenova", "Telenova", "kept", [])
    store.set_choice("spiritbox", "Spiritbox", "kept", ["La Dispute", "Neck Deep"])

    assert store.choices() == {
        "spiritbox": {"name": "Spiritbox", "choice": "kept", "seeds": ["La Dispute", "Neck Deep"],
                      "at": "2026-11-03T10:00:00+11:00"},
        "telenova": {"name": "Telenova", "choice": "kept", "seeds": [],
                     "at": "2026-11-03T10:00:00+11:00"},
    }  # fmt: skip
    store.clear_choice("spiritbox")
    assert list(store.choices()) == ["telenova"]


def test_only_kept_or_trimmed_is_a_choice(store: Store) -> None:
    with pytest.raises(ValueError):
        store.set_choice("spiritbox", "Spiritbox", "loved", [])
    assert store.choices() == {}


def test_samples_are_kept_with_when_they_were_fetched(store: Store, monkeypatch) -> None:
    monkeypatch.setattr(store_module, "now", lambda: at("2026-11-03 10:00"))
    record = {"apple": None, "kind": "none", "items": []}
    store.save_samples("spiritbox", {"apple": "https://old", "kind": "songs", "items": []})
    store.save_samples("spiritbox", record)
    assert store.samples() == {"spiritbox": {**record, "fetched_at": "2026-11-03T10:00:00+11:00"}}


def test_undoing_learning_is_remembered_with_when(store: Store, monkeypatch) -> None:
    assert store.unlearned() == {}
    monkeypatch.setattr(store_module, "now", lambda: at("2026-11-03 10:00"))
    store.unlearn("La Dispute")
    assert store.unlearned() == {"La Dispute": "2026-11-03T10:00:00+11:00"}


def test_acts_saved_before_keys_were_recorded_get_them_on_reading(store: Store) -> None:
    old = {"show_id": "2026-10-20~the-fall-of-troy", "day": "2026-10-20",
           "pairs": [["2026-10-20", "the fall of troy"]],
           "acts": [{"name": "The Fall of Troy", "tier": "maybe", "why": "2 plays"}]}  # fmt: skip
    store.db.execute(
        "insert into shows values (?, ?, ?)", (old["show_id"], old["day"], json.dumps(old))
    )
    [act] = store.shows()[0]["acts"]
    assert act["key"] == "fall of troy"
    assert act["found_through"] == []


def test_show_record_names_who_a_similar_only_maybe_was_found_through() -> None:
    found = artist("Telenova", Tier.MAYBE, plays=0, similar_to=["La Dispute", "Neck Deep"])
    played = artist("Spiritbox", Tier.MAYBE, similar_to=["La Dispute"])
    loved = artist("Neck Deep", similar_to=["La Dispute"])
    acts = show_record(show(acts=[found, played, loved]))["acts"]
    assert [act["found_through"] for act in acts] == [["La Dispute", "Neck Deep"], [], []]
    assert acts[0]["key"] == "telenova"


def test_records_saved_before_pinning_read_as_not_pinned(store: Store) -> None:
    old = {"show_id": "2026-10-20~la-dispute", "day": "2026-10-20",
           "pairs": [["2026-10-20", "la dispute"]],
           "acts": [{"name": "La Dispute", "tier": "love", "why": "100 plays"}]}  # fmt: skip
    store.db.execute(
        "insert into shows values (?, ?, ?)", (old["show_id"], old["day"], json.dumps(old))
    )
    [record] = store.shows()
    assert record["pinned"] is False
    assert record["acts"][0]["pinned"] is False


def test_a_festival_with_a_pinned_act_is_pinned() -> None:
    pinned = artist("Sleep Token", pinned=True, hearts=4)
    record = show_record(show(acts=[artist("Neck Deep"), pinned], is_festival=True))
    assert record["pinned"] is True
    assert [act["pinned"] for act in record["acts"]] == [False, True]
    assert show_record(show())["pinned"] is False


def test_cache_keys_leave_out_the_plex_token(store: Store) -> None:
    web = FakeWeb({"children": "[1]"})
    cached = CachedFetcher(web, store, timedelta(days=1))
    assert cached.text("http://plex/library/metadata/1/children?X-Plex-Token=SECRET&a=1") == "[1]"
    assert cached.text("http://plex/library/metadata/1/children?a=1&X-Plex-Token=OTHER") == "[1]"
    assert len(web.requests) == 1
    keys = [row["url"] for row in store.db.execute("select url from cache")]
    assert keys == ["http://plex/library/metadata/1/children?a=1"]


def test_cache_rows_keyed_with_a_token_are_dropped_on_open(tmp_path) -> None:
    path = tmp_path / "db.sqlite"
    Store(path).db.execute(
        "insert into cache values ('http://plex/x?X-Plex-Token=SECRET', '2026-10-20', 'b')"
    )
    assert Store(path).db.execute("select count(*) from cache").fetchone()[0] == 0
