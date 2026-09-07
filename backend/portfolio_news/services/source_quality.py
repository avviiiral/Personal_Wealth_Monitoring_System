"""
Source-quality classification.

Maps a publisher name (as returned by a NewsProvider, e.g. the
`<source>` field of a Google News RSS entry) to a
SourceQualityTier. Deliberately a plain lookup rather than
anything ML-based - transparent, fast, and trivial to extend by
editing the lists below or overriding via the
NEWS_SOURCE_QUALITY_OVERRIDES setting (a dict of
{lowercased substring: tier value}).

This is intentionally decoupled from any specific provider:
GoogleNewsRSSProvider, or any future provider, only needs to
supply a publisher name string.
"""

from django.conf import settings

from ..constants import SourceQualityTier


# Primary / official / top-tier wire and financial press.
# Matched as a case-insensitive substring of the publisher name,
# since RSS "source" fields vary in exact formatting
# (e.g. "Reuters", "Reuters.com", "Reuters India").
TIER_1_PUBLISHERS = [
    "reuters",
    "bloomberg",
    "cnbc",
    "financial times",
    "economic times",
    "moneycontrol",
    "business standard",
    "livemint",
    "mint",
    "nse",
    "bse",
    "sebi",
    "rbi",
    "reserve bank of india",
    "ministry of",
    "pib.gov.in",
    "press information bureau",
]

TIER_2_PUBLISHERS = [
    "the hindu",
    "hindu businessline",
    "business today",
    "financial express",
    "ndtv profit",
    "ndtv business",
    "zee business",
    "cnbc-tv18",
    "the economic times",
    "outlook business",
    "forbes india",
    "fortune india",
    "wall street journal",
    "wsj",
    "the times of india",
    "hindustan times",
    "livelaw",
]


def _overrides() -> dict:
    return getattr(settings, "NEWS_SOURCE_QUALITY_OVERRIDES", {}) or {}


def classify_source(publisher_name: str) -> str:
    """
    Returns a SourceQualityTier value for the given publisher
    name. Unknown/empty publishers default to TIER_3 rather than
    raising, since RSS feeds frequently omit or mangle this
    field and that must never break ingestion.
    """

    if not publisher_name:
        return SourceQualityTier.TIER_3

    name = publisher_name.strip().lower()

    for substring, tier in _overrides().items():
        if substring.lower() in name:
            return tier

    for substring in TIER_1_PUBLISHERS:
        if substring in name:
            return SourceQualityTier.TIER_1

    for substring in TIER_2_PUBLISHERS:
        if substring in name:
            return SourceQualityTier.TIER_2

    return SourceQualityTier.TIER_3


def best_tier(tiers) -> str:
    """
    Given an iterable of SourceQualityTier values (e.g. every
    source that reported the same event), returns the highest
    (best) one. Empty input defaults to TIER_3.
    """

    ranking = {
        SourceQualityTier.TIER_1: 0,
        SourceQualityTier.TIER_2: 1,
        SourceQualityTier.TIER_3: 2,
    }

    tiers = list(tiers)

    if not tiers:
        return SourceQualityTier.TIER_3

    return min(tiers, key=lambda t: ranking.get(t, 2))
