"""Unit tests for services/mf_ratios.py (pure calculations, no database)."""
import math
import statistics
from datetime import date, timedelta
from decimal import Decimal

from django.test import SimpleTestCase

from mutual_funds.services import mf_ratios


def business_days(start, count):
    days, current = [], start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def series_from_returns(days, returns, start_value=100.0):
    values, value = [(days[0], start_value)], start_value
    for day, ret in zip(days[1:], returns):
        value *= 1 + ret
        values.append((day, value))
    return values


class RatioFormulaTests(SimpleTestCase):
    def setUp(self):
        # ~3.2 years of business days, deterministic alternating returns.
        self.days = business_days(date(2023, 9, 1), 840)
        self.bench_returns = [0.004 if i % 3 else -0.005 for i in range(len(self.days) - 1)]
        self.fund_returns = [2 * r for r in self.bench_returns]  # beta exactly 2
        self.fund = series_from_returns(self.days, self.fund_returns)
        self.bench = series_from_returns(self.days, self.bench_returns, 1000.0)
        self.as_of = self.days[-1]
        self.rf_pct = Decimal("6.5")

    def _window(self, values, years):
        start = mf_ratios.window_start(self.as_of, years)
        return [(d, v) for d, v in values if start <= d <= self.as_of]

    def _expected(self, years):
        fund = self._window(self.fund, years)
        bench = self._window(self.bench, years)
        rets = [fund[i][1] / fund[i - 1][1] - 1 for i in range(1, len(fund))]
        brets = [bench[i][1] / bench[i - 1][1] - 1 for i in range(1, len(bench))]
        elapsed = (fund[-1][0] - fund[0][0]).days / 365.25
        rp = (fund[-1][1] / fund[0][1]) ** (1 / elapsed) - 1
        rm = (bench[-1][1] / bench[0][1]) ** (1 / elapsed) - 1
        rf = 0.065
        sd = statistics.stdev(rets) * math.sqrt(252)
        rf_daily = (1 + rf) ** (1 / 252) - 1
        downside = math.sqrt(sum(min(r - rf_daily, 0) ** 2 for r in rets) / len(rets)) * math.sqrt(252)
        beta = statistics.covariance(rets, brets) / statistics.variance(brets)
        return {
            "std_dev_pct": sd * 100,
            "sharpe": (rp - rf) / sd,
            "sortino": (rp - rf) / downside,
            "beta": beta,
            "alpha_pct": (rp - (rf + beta * (rm - rf))) * 100,
        }

    def assertClose(self, actual, expected, places=3):
        self.assertIsNotNone(actual)
        self.assertAlmostEqual(float(actual), expected, places=places)

    def test_one_and_three_year_ratios_match_independent_calculation(self):
        result = mf_ratios.compute_ratio_set(
            self.fund, self.bench, as_of=self.as_of, risk_free_rate_pct=self.rf_pct,
        )
        for years, suffix in ((1, "1y"), (3, "3y")):
            expected = self._expected(years)
            self.assertClose(result[f"calc_std_dev_{suffix}_pct"], expected["std_dev_pct"])
            self.assertClose(result[f"calc_sharpe_{suffix}"], expected["sharpe"])
            self.assertClose(result[f"calc_sortino_{suffix}"], expected["sortino"])
            self.assertClose(result[f"calc_beta_{suffix}"], expected["beta"])
            self.assertClose(result[f"calc_alpha_{suffix}_pct"], expected["alpha_pct"])
        self.assertAlmostEqual(float(result["calc_beta_3y"]), 2.0, places=4)

    def test_values_are_four_decimal_places(self):
        result = mf_ratios.compute_ratio_set(
            self.fund, self.bench, as_of=self.as_of, risk_free_rate_pct=self.rf_pct,
        )
        for key, value in result.items():
            if isinstance(value, Decimal):
                self.assertEqual(value.as_tuple().exponent, -4, key)

    def test_without_benchmark_beta_and_alpha_are_null(self):
        result = mf_ratios.compute_ratio_set(
            self.fund, None, as_of=self.as_of, risk_free_rate_pct=self.rf_pct,
        )
        self.assertIsNotNone(result["calc_sharpe_1y"])
        self.assertIsNone(result["calc_beta_1y"])
        self.assertIsNone(result["calc_alpha_3y_pct"])
        self.assertIn("BENCHMARK_NOT_AVAILABLE", result["flags"])

    def test_short_history_leaves_three_year_ratios_null(self):
        short = self.fund[-300:]
        result = mf_ratios.compute_ratio_set(
            short, None, as_of=self.as_of, risk_free_rate_pct=self.rf_pct,
        )
        self.assertIsNotNone(result["calc_std_dev_1y_pct"])
        self.assertIsNone(result["calc_std_dev_3y_pct"])
        self.assertIsNone(result["calc_sharpe_3y"])
        self.assertIn("INSUFFICIENT_HISTORY_3Y", result["flags"])

    def test_sparse_window_is_rejected(self):
        # Weekly observations only -> coverage far below the daily threshold.
        weekly = self.fund[::5]
        result = mf_ratios.compute_ratio_set(
            weekly, None, as_of=weekly[-1][0], risk_free_rate_pct=self.rf_pct,
        )
        self.assertIsNone(result["calc_std_dev_1y_pct"])
        self.assertIn("INSUFFICIENT_HISTORY_1Y", result["flags"])

    def test_zero_volatility_does_not_divide_by_zero(self):
        flat = [(d, 100.0) for d in self.days]
        result = mf_ratios.compute_ratio_set(
            flat, flat, as_of=self.as_of, risk_free_rate_pct=self.rf_pct,
        )
        self.assertEqual(result["calc_std_dev_1y_pct"], Decimal("0.0000"))
        self.assertIsNone(result["calc_sharpe_1y"])
        self.assertIsNone(result["calc_beta_1y"])

    def test_empty_fund_series_returns_all_null_with_flags(self):
        result = mf_ratios.compute_ratio_set(
            [], self.bench, as_of=self.as_of, risk_free_rate_pct=self.rf_pct,
        )
        for key, value in result.items():
            if key.startswith("calc_") and key != "calc_risk_free_rate_pct":
                self.assertIsNone(value, key)
        self.assertIn("INSUFFICIENT_HISTORY_1Y", result["flags"])
        self.assertIn("INSUFFICIENT_HISTORY_3Y", result["flags"])

    def test_alpha_formula_matches_capm_definition(self):
        alpha = mf_ratios.capm_alpha(rp=0.18, rf=0.065, beta=0.9, rm=0.15)
        self.assertAlmostEqual(alpha, 0.18 - (0.065 + 0.9 * (0.15 - 0.065)))
