"""Time helpers for scheduled jobs.

Every function takes ``now`` explicitly so the rules can be tested with a
fixed clock. All schedule times are local to ``market.timezone`` in
``pwms_config.json`` (Asia/Kolkata by default).
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from config.pwms_config import get as get_pwms_config


def zone():
    return ZoneInfo(get_pwms_config("market", "timezone", "Asia/Kolkata"))


def now():
    """Current time in the configured scheduler time zone."""
    return datetime.now(zone())


def localize(value):
    """Return ``value`` as an aware datetime in the scheduler time zone."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=zone())
    return value.astimezone(zone())


def parse_hhmm(value):
    text = str(value or "").strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid time {value!r}; expected HH:MM.") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid time {value!r}; expected HH:MM.")
    return time(hour, minute)


def _at(day, slot_time, tzinfo):
    return datetime.combine(day, slot_time, tzinfo=tzinfo)


def latest_daily_slot(current, hhmm):
    """Most recent occurrence of the daily ``hhmm`` at or before ``current``."""
    current = localize(current)
    slot_time = parse_hhmm(hhmm)
    candidate = _at(current.date(), slot_time, current.tzinfo)
    if candidate > current:
        candidate -= timedelta(days=1)
    return candidate


def next_daily_slot(current, hhmm):
    """Next occurrence of the daily ``hhmm`` strictly after ``current``."""
    return latest_daily_slot(current, hhmm) + timedelta(days=1)


def last_weekday_on_or_before(day):
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def last_weekday_before(day):
    return last_weekday_on_or_before(day - timedelta(days=1))


class MarketCalendar:
    """NSE cash-market session rules used by the price freshness checks.

    A session day is a weekday that is not listed in ``market.holidays``.
    "Market hours" run from the open to the close plus a buffer, so a fetch
    shortly after 15:30 still counts as intraday and is corrected later.
    """

    def __init__(self, *, open_time="09:15", close_time="15:30", buffer_minutes=15, holidays=(), tzinfo=None):
        self.open_time = parse_hhmm(open_time)
        self.close_time = parse_hhmm(close_time)
        self.buffer = timedelta(minutes=int(buffer_minutes))
        self.holidays = {date.fromisoformat(str(value)) for value in (holidays or [])}
        self.tzinfo = tzinfo

    @classmethod
    def from_config(cls):
        return cls(
            open_time=get_pwms_config("market", "open", "09:15"),
            close_time=get_pwms_config("market", "close", "15:30"),
            buffer_minutes=get_pwms_config("market", "close_buffer_minutes", 15),
            holidays=get_pwms_config("market", "holidays", []),
        )

    def is_session_day(self, day):
        return day.weekday() < 5 and day not in self.holidays

    def session_end(self, day, tzinfo):
        return _at(day, self.close_time, tzinfo) + self.buffer

    def in_market_hours(self, current):
        current = localize(current)
        if not self.is_session_day(current.date()):
            return False
        opens = _at(current.date(), self.open_time, current.tzinfo)
        return opens <= current < self.session_end(current.date(), current.tzinfo)

    def last_session_end(self, current):
        """Close (+ buffer) of the most recent session that has finished."""
        current = localize(current)
        day = current.date()
        for _ in range(15):
            if self.is_session_day(day):
                end = self.session_end(day, current.tzinfo)
                if end <= current:
                    return end
            day -= timedelta(days=1)
        # Fifteen consecutive non-session days only happens with a broken
        # holiday list; fall back to "yesterday's close" instead of failing.
        return self.session_end(current.date() - timedelta(days=1), current.tzinfo)
