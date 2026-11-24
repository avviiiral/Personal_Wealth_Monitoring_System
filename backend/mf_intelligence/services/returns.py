"""Return calculations on a NAV (or index value) history.

Conventions
-----------
* The series end date (latest NAV) is the "as of" date.
* The start value of a period is the last value on or before the period
  start; a period is reported only if the history reaches back that far.
* Periods under one year are absolute returns measured in calendar
  months; 1Y and longer span N x 365 days and are annualised over N
  years (CAGR) - the same convention CharlieAlpha uses for its trailing
  CAGR, so the two sets of figures agree for the same NAV date.
* YTD starts from the last value of the previous calendar year.
* A calendar year is reported only if both its opening value (last value
  of the previous year) and a closing value within two weeks of 31 Dec
  exist, so partial years are never presented as full ones. The running
  year is reported as YTD, not as a calendar year.

All arithmetic uses Decimal and results are rounded to four decimals.
"""

from bisect import bisect_right
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

from dateutil.relativedelta import relativedelta

from mf_intelligence.constants import CALENDAR_YEARS_KEPT, RETURN_PERIODS


FOUR_PLACES = Decimal("0.0001")
HUNDRED = Decimal("100")
YEAR_END_TOLERANCE_DAYS = 14


def to_decimal(value):
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not result.is_finite():
        return None
    return result


def to_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def quantize(value):
    if value is None:
        return None
    return value.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


class ValueSeries:
    """An ascending, de-duplicated list of (date, positive Decimal value)."""

    def __init__(self, points):
        cleaned = {}
        for raw_date, raw_value in points:
            point_date = to_date(raw_date)
            value = to_decimal(raw_value)
            if point_date is None or value is None or value <= 0:
                continue
            cleaned[point_date] = value
        self.dates = sorted(cleaned)
        self.values = [cleaned[point_date] for point_date in self.dates]

    @classmethod
    def from_arrays(cls, dates, values):
        dates = dates or []
        values = values or []
        return cls(zip(dates, values))

    def __len__(self):
        return len(self.dates)

    @property
    def first_date(self):
        return self.dates[0] if self.dates else None

    @property
    def last_date(self):
        return self.dates[-1] if self.dates else None

    @property
    def last_value(self):
        return self.values[-1] if self.values else None

    def on_or_before(self, target):
        """Return (date, value) of the last point on or before ``target``."""
        index = bisect_right(self.dates, target) - 1
        if index < 0:
            return None
        return self.dates[index], self.values[index]

    def monthly_points(self):
        """Last point of each calendar month as (month_start, date, value)."""
        months = {}
        for point_date, value in zip(self.dates, self.values):
            months[(point_date.year, point_date.month)] = (point_date, value)
        return [
            (date(year, month, 1), point_date, value)
            for (year, month), (point_date, value) in sorted(months.items())
        ]


def _growth(start_value, end_value):
    return end_value / start_value


def absolute_return(start_value, end_value):
    if not start_value or end_value is None:
        return None
    with localcontext() as ctx:
        ctx.prec = 28
        return quantize((_growth(start_value, end_value) - 1) * HUNDRED)


def annualised_return(start_value, end_value, years):
    if not start_value or end_value is None or not years or years <= 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 28
        growth = _growth(start_value, end_value)
        exponent = Decimal(1) / Decimal(str(years))
        return quantize((growth ** exponent - 1) * HUNDRED)


def period_start(as_of, months=None, years=None, days=None):
    if days:
        return as_of - timedelta(days=days)
    if years:
        return as_of - timedelta(days=365 * years)
    return as_of - relativedelta(months=months or 0)


def period_return(series, months=None, years=None, days=None):
    if len(series) < 2:
        return None
    as_of = series.last_date
    start_target = period_start(as_of, months=months, years=years, days=days)
    if series.first_date > start_target:
        return None
    start = series.on_or_before(start_target)
    if start is None:
        return None
    end_value = series.last_value
    if years and years > 1:
        return annualised_return(start[1], end_value, years)
    return absolute_return(start[1], end_value)


def ytd_return(series):
    if len(series) < 2:
        return None
    previous_year_end = date(series.last_date.year - 1, 12, 31)
    start = series.on_or_before(previous_year_end)
    if start is None:
        return None
    return absolute_return(start[1], series.last_value)


def since_inception_return(series):
    if len(series) < 2:
        return None
    days = (series.last_date - series.first_date).days
    if days <= 0:
        return None
    if days < 365:
        return absolute_return(series.values[0], series.last_value)
    return annualised_return(series.values[0], series.last_value, Decimal(days) / Decimal("365"))


def calendar_year_returns(series, years_kept=CALENDAR_YEARS_KEPT):
    """Return {"2024": Decimal, ...} for complete calendar years."""
    if len(series) < 2:
        return {}
    last_full_year = series.last_date.year - 1
    if (series.last_date.month, series.last_date.day) == (12, 31):
        last_full_year = series.last_date.year
    results = {}
    for year in range(last_full_year - years_kept + 1, last_full_year + 1):
        start = series.on_or_before(date(year - 1, 12, 31))
        end = series.on_or_before(date(year, 12, 31))
        if start is None or end is None:
            continue
        if end[0].year != year or (date(year, 12, 31) - end[0]).days > YEAR_END_TOLERANCE_DAYS:
            continue
        value = absolute_return(start[1], end[1])
        if value is not None:
            results[str(year)] = value
    return results


def compute_return_profile(series):
    """All stored return fields for a series, keyed by model field name."""
    profile = {}
    for suffix, months, years, _label in RETURN_PERIODS:
        days = 7 if suffix == "1w" else None
        profile[f"return_{suffix}"] = period_return(series, months=months, years=years, days=days)
    profile["return_ytd"] = ytd_return(series)
    profile["return_since_inception"] = since_inception_return(series)
    profile["calendar_returns"] = {
        year: str(value) for year, value in calendar_year_returns(series).items()
    }
    return profile
