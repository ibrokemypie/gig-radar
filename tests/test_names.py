from live_shows.names import clean_library_name, normalise, slug


def test_spellings_of_one_name_normalise_alike() -> None:
    assert normalise("The Hayley Williams Show") == normalise("hayley williams show")
    assert normalise("Mudhoney (USA)") == "mudhoney"
    assert normalise("Of Mice &amp; Men") == "of mice and men"
    assert normalise("Sigur Rós") == "sigur ros"
    assert normalise("Blink‐182") == normalise("blink-182")


def test_library_titles_lose_brackets_and_session_suffixes() -> None:
    assert clean_library_name("[Linkin Park]") == "Linkin Park"
    assert clean_library_name("Turnover, Audiotree") == "Turnover"
    assert clean_library_name("Pianos Become the Teeth") == "Pianos Become the Teeth"


def test_slug_matches_the_sites_url_slugs() -> None:
    assert slug("La Dispute") == "la-dispute"
    assert slug("The Fall of Troy") == "fall-of-troy"
