"""When does a stored stock/ETF/SGB/bond price need a new fetch?

* During NSE market hours (session day, 09:15 to 15:30 plus a 15-minute
  buffer) the latest stored day is always re-fetched, inclusive, so today's
  row keeps moving instead of freezing after the first fetch of the day.
* Outside market hours the asset is current if its latest row was fetched
  after the most recent session close (plus buffer). Otherwise it is fetched
  once more, which replaces an intraday value with the official close. The
  same rule covers weekends and holidays without a holiday list.
"""
from config.scheduling.clock import MarketCalendar, localize


class PriceFreshness:
    def __init__(self, calendar=None):
        self.calendar = calendar or MarketCalendar.from_config()

    def needs_fetch(self, latest_date, latest_fetched_at, now):
        """Return ``(needs_fetch, reason)``."""
        if latest_date is None:
            return True, "no stored price"
        if self.calendar.in_market_hours(now):
            return True, "market is open"
        session_end = self.calendar.last_session_end(now)
        if latest_fetched_at is not None and localize(latest_fetched_at) >= session_end:
            return False, f"fetched after the {session_end:%d %b %H:%M} close"
        return True, f"final close for {session_end:%d %b} not stored yet"
