"""Labelling helpers for portfolio holdings."""

import re
from datetime import date

from mf_intelligence.constants import SECTOR_STYLE


EQUITY_TYPES = {"equity"}
FIXED_INCOME_TYPES = {"debt", "money_market"}
CASH_TYPES = {"cash"}

# Government securities, state development loans and T-bills carry ISINs
# that start with "IN" followed by a digit (e.g. IN0020260025); corporate
# and PSU paper starts with "INE", fund units with "INF".
_SOVEREIGN_ISIN = re.compile(r"^IN\d")
_TBILL_NAME = re.compile(r"\b(t[\s-]?bill|treasury bill)", re.IGNORECASE)
_CASH_LIKE_NAME = re.compile(
    r"\b(call|cash|treps|tri[\s-]?party|cblo|repo|net current|receivables?|margin money)\b", re.IGNORECASE
)
# Disclosures print maturities in many shapes: "(01/12/2027)", "(MD 17/12/2026)",
# "RED 27-11-2026", "19.11.26", "(15-Dec-2026)", "NABARD CP 01-Jan-2027".
_NUMERIC_DATE = re.compile(r"(?<![\d.])(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4}|\d{2})(?![\d%])")
_MONTH_NAME_DATE = re.compile(r"(?<!\d)(\d{1,2})[\s\-/]?([A-Za-z]{3})[A-Za-z]*[\s\-/,]*(\d{4}|\d{2})(?!\d)")
_MONTHS = {name: index for index, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1
)}


def sector_style(industry):
    return SECTOR_STYLE.get((industry or "").strip(), "Other")


def is_equity(holding_type):
    return (holding_type or "").lower() in EQUITY_TYPES


def is_fixed_income(holding_type):
    return (holding_type or "").lower() in FIXED_INCOME_TYPES


def is_cash(holding_type):
    return (holding_type or "").lower() in CASH_TYPES


SOVEREIGN = "Sovereign"
CORPORATE = "Corporate / PSU"
CASH_LIKE = "Cash & equivalents"


def fixed_income_issuer_type(isin, name):
    text = (name or "").lower()
    if _SOVEREIGN_ISIN.match(isin or "") or _TBILL_NAME.search(text) or "government of india" in text or "state government" in text:
        return SOVEREIGN
    if not (isin or "").strip() and _CASH_LIKE_NAME.search(text):
        return CASH_LIKE
    if _CASH_LIKE_NAME.search(text) and not re.search(r"\d+(\.\d+)?\s*%", text):
        return CASH_LIKE
    return CORPORATE


def _year(value):
    year = int(value)
    return year + 2000 if year < 100 else year


def maturity_from_name(name):
    """Last date printed in a disclosure name (its maturity), or None."""
    text = name or ""
    candidates = []
    for match in _NUMERIC_DATE.finditer(text):
        day, month, year = match.groups()
        candidates.append((match.start(), int(day), int(month), _year(year)))
    for match in _MONTH_NAME_DATE.finditer(text):
        day, month_name, year = match.groups()
        month = _MONTHS.get(month_name.lower())
        if month:
            candidates.append((match.start(), int(day), month, _year(year)))
    for _, day, month, year in sorted(candidates, reverse=True):
        if 1990 <= year <= 2150:
            try:
                return date(year, month, day)
            except ValueError:
                continue
    return None


MATURITY_BUCKETS = (
    ("< 1Y", 0, 1),
    ("1-3Y", 1, 3),
    ("3-5Y", 3, 5),
    ("5-10Y", 5, 10),
    ("10Y+", 10, None),
)


def maturity_bucket(years):
    for label, low, high in MATURITY_BUCKETS:
        if years >= low and (high is None or years < high):
            return label
    return MATURITY_BUCKETS[0][0]


def short_fund_name(name):
    """Drop plan/option noise from a scheme name for compact labels."""
    text = re.sub(r"\s+", " ", name or "").strip()
    text = re.sub(r"\((?:G|IDCW|Growth)\)$", "", text, flags=re.IGNORECASE).strip()
    patterns = (
        r"[-\s]*(regular|direct)\s*plan\b.*$",
        r"[-\s]*(regular|direct)\b[-\s]*(growth|idcw|dividend).*$",
        r"[-\s]*(growth|idcw)\s*(option|plan)?\s*[-\s]*(regular|direct)?\s*(plan)?$",
        r"[-\s]+(regular|direct)$",
    )
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip(" -")
    return text or (name or "")


_MATCH_NOISE = re.compile(
    r"\b(direct|regular|plan|growth|option|options|idcw|payout|reinvest\w*|bonus|dividend|cumulative|the|fund)\b"
)
_JOINED_CAP_WORDS = (("midcap", "mid cap"), ("smallcap", "small cap"), ("largecap", "large cap"),
                     ("multicap", "multi cap"), ("flexicap", "flexi cap"))


def fund_match_key(name):
    """Plan/option-free key so a fund's Regular and Direct plans match."""
    text = (name or "").lower()
    text = re.sub(r"\(\s*formerly[^)]*\)?", " ", text)
    text = re.sub(r"[^a-z0-9&]+", " ", text)
    for joined, spaced in _JOINED_CAP_WORDS:
        text = text.replace(joined, spaced)
    text = _MATCH_NOISE.sub(" ", text)
    return " ".join(text.split())
