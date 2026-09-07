import re

from bs4 import BeautifulSoup


# Google News RSS (and many publishers) suffix the headline with
# " - Source Name". Strip that before comparing titles across
# sources, otherwise "X gets approval - Reuters" and
# "X gets approval - Economic Times" look artificially different.
_SOURCE_SUFFIX_PATTERN = re.compile(r"\s+-\s+[^-]{2,60}$")

_NON_ALNUM_PATTERN = re.compile(r"[^a-z0-9\s]")

_WHITESPACE_PATTERN = re.compile(r"\s+")

DESCRIPTION_MAX_LENGTH = 1000


def strip_html(raw_html: str) -> str:
    """
    Strip HTML markup from an RSS description/snippet.

    Google News RSS descriptions typically contain an <a> tag
    and font styling rather than plain text. This returns the
    visible text only, truncated to a safe storage length.
    """

    if not raw_html:
        return ""

    text = BeautifulSoup(raw_html, "html.parser").get_text()

    text = _WHITESPACE_PATTERN.sub(" ", text).strip()

    return text[:DESCRIPTION_MAX_LENGTH]


def normalize_title(title: str) -> str:
    """
    Normalize a headline for duplicate-event matching.

    Lowercases, strips the trailing " - Source" suffix,
    removes punctuation, and collapses whitespace so that
    minor wording/punctuation differences between publishers
    don't prevent duplicate detection.
    """

    if not title:
        return ""

    text = _SOURCE_SUFFIX_PATTERN.sub("", title)

    text = text.lower()

    text = _NON_ALNUM_PATTERN.sub(" ", text)

    text = _WHITESPACE_PATTERN.sub(" ", text).strip()

    return text