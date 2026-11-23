"""Artist and venue names reduced to a form two spellings of one name share."""

import html
import re
import unicodedata

HYPHENS = re.compile("[‐-―−]")
# Plex titles that are not the artist's own name: "[Linkin Park]", "Turnover, Audiotree".
_LIBRARY_SUFFIX = re.compile(r",\s*(audiotree|live|sessions?)\b.*$", re.IGNORECASE)


def normalise(name: str) -> str:
    """Lower-case and accent-free, '&' as 'and', no leading 'the', punctuation as spaces.

    A bracketed country tag ("(USA)") is dropped, since ticket sellers add it and the
    library never does.
    """
    text = HYPHENS.sub("-", html.unescape(name)).replace("’", "'")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ")
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return re.sub(r"^the ", "", text)


def clean_library_name(name: str) -> str:
    """The artist's name as a ticket seller would print it, from a Plex artist title."""
    text = HYPHENS.sub("-", html.unescape(name)).strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return _LIBRARY_SUFFIX.sub("", text).strip()


def slug(name: str) -> str:
    """The name as a URL slug: 'La Dispute' -> 'la-dispute'."""
    return normalise(name).replace(" ", "-")
