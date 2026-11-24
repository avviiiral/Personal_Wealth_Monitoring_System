from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from mf_intelligence.services.returns import (
    ValueSeries,
    absolute_return,
    annualised_return,
    calendar_year_returns,
    compute_return_profile,
    period_return,
    since_inception_return,
    ytd_return,
)


class ValueSeriesTests(SimpleTestCase):
    def test_cleans_sorts_and_deduplicates(self):
        series = ValueSeries([
            ("2026-01-05", "11"),
            ("2026-01-02", 10),
            ("2026-01-05", "12"),  # later duplicate wins
            ("bad-date", 5),
            ("2026-01-06", None),
            ("2026-01-07", 0),
            ("2026-01-08", "nan"),
        ])
        self.assertEqual(series.dates, [date(2026, 1, 2), date(2026, 1, 5)])
        self.assertEqual(series.values, [Decimal("10"), Decimal("12")])

    def test_on_or_before(self):
        series = ValueSeries([("2026-01-02", 10), ("2026-01-05", 11)])
        self.assertIsNone(series.on_or_before(date(2026, 1, 1)))
        self.assertEqual(series.on_or_before(date(2026, 1, 4)), (date(2026, 1, 2), Decimal("10")))
        self.assertEqual(series.on_or_before(date(2026, 1, 5)), (date(2026, 1, 5), Decimal("11")))

    def test_monthly_points_keep_last_value_of_each_month(self):
        series = ValueSeries([("2026-01-02", 10), ("2026-01-30", 11), ("2026-02-27", 12)])
        self.assertEqual(series.monthly_points(), [
            (date(2026, 1, 1), date(2026, 1, 30), Decimal("11")),
            (date(2026, 2, 1), date(2026, 2, 27), Decimal("12")),
        ])


class ReturnMathTests(SimpleTestCase):
    def test_absolute_and_annualised(self):
        self.assertEqual(absolute_return(Decimal("10"), Decimal("11")), Decimal("10.0000"))
        # 21% over two years is 10% a year.
        self.assertEqual(annualised_return(Decimal("100"), Decimal("121"), 2), Decimal("10.0000"))
        self.assertIsNone(absolute_return(None, Decimal("1")))
        self.assertIsNone(annualised_return(Decimal("1"), Decimal("2"), 0))

    def test_period_return_uses_value_on_or_before_start(self):
        series = ValueSeries([
            ("2025-11-21", 19.95),  # Friday before 2025-11-23
            ("2025-11-24", 19.91),
            ("2026-11-23", 21.62),
        ])
        # 1Y spans 365 days (to 2025-11-23) and starts from the Friday NAV.
        self.assertEqual(period_return(series, years=1), Decimal("8.3709"))

    def test_multi_year_periods_are_annualised(self):
        series = ValueSeries([("2023-11-24", 100), ("2026-11-23", 133.1)])
        self.assertEqual(period_return(series, years=3), Decimal("10.0000"))

    def test_period_not_reported_without_enough_history(self):
        series = ValueSeries([("2026-06-01", 10), ("2026-11-23", 11)])
        self.assertIsNone(period_return(series, years=1))
        self.assertIsNotNone(period_return(series, months=3))

    def test_ytd_from_previous_year_end(self):
        series = ValueSeries([("2025-12-31", 100), ("2026-01-02", 101), ("2026-11-23", 108)])
        self.assertEqual(ytd_return(series), Decimal("8.0000"))
        self.assertIsNone(ytd_return(ValueSeries([("2026-01-02", 1), ("2026-02-02", 2)])))

    def test_calendar_years_only_complete_years(self):
        series = ValueSeries([
            ("2022-06-30", 50),   # fund launched mid 2022: 2022 is partial
            ("2022-12-30", 100),
            ("2023-12-29", 120),
            ("2024-12-31", 132),
            ("2025-12-31", 118.8),
            ("2026-11-23", 130),  # running year -> YTD, not a calendar year
        ])
        self.assertEqual(calendar_year_returns(series), {
            "2023": Decimal("20.0000"),
            "2024": Decimal("10.0000"),
            "2025": Decimal("-10.0000"),
        })

    def test_calendar_year_skipped_when_year_end_value_missing(self):
        series = ValueSeries([("2023-12-29", 100), ("2024-06-28", 110), ("2025-12-31", 120)])
        # 2024 has no value near 31 Dec 2024.
        self.assertNotIn("2024", calendar_year_returns(series))

    def test_since_inception(self):
        series = ValueSeries([("2020-01-01", 10), ("2025-01-01", 20)])
        self.assertAlmostEqual(float(since_inception_return(series)), 14.87, places=1)
        short = ValueSeries([("2026-01-01", 10), ("2026-03-01", 11)])
        self.assertEqual(since_inception_return(short), Decimal("10.0000"))

    def test_empty_and_single_point_series_report_nothing(self):
        for series in (ValueSeries([]), ValueSeries.from_arrays(None, None), ValueSeries([("2026-01-02", 10)])):
            with self.subTest(points=len(series)):
                self.assertIsNone(period_return(series, months=1))
                self.assertIsNone(period_return(series, years=3))
                self.assertIsNone(ytd_return(series))
                self.assertIsNone(since_inception_return(series))
                self.assertEqual(calendar_year_returns(series), {})
                profile = compute_return_profile(series)
                self.assertTrue(all(value is None for key, value in profile.items() if key != "calendar_returns"))
                self.assertEqual(profile["calendar_returns"], {})
        empty = ValueSeries([])
        self.assertIsNone(empty.first_date)
        self.assertIsNone(empty.last_value)
        self.assertIsNone(empty.on_or_before(date(2026, 1, 1)))
        self.assertEqual(empty.monthly_points(), [])

    def test_profile_contains_every_stored_field(self):
        series = ValueSeries([("2015-01-01", 10), ("2026-11-23", 40)])
        profile = compute_return_profile(series)
        for field in (
            "return_1w", "return_1m", "return_3m", "return_6m", "return_1y", "return_2y",
            "return_3y", "return_5y", "return_10y", "return_ytd", "return_since_inception",
        ):
            self.assertIn(field, profile)
        self.assertIsInstance(profile["calendar_returns"], dict)
