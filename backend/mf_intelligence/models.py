"""Persistent store for Mutual Fund Intelligence.

Everything here is market-wide reference data fetched from the public
CharlieAlpha API (https://api.charliealpha.in) and AMFI. None of it is
owned by a user or a family, so these tables carry no owner/family
columns and are readable by every authenticated user.

Computed values (returns, calendar-year returns) are stored with four
decimal places; the frontend rounds them for display.
"""

from django.conf import settings
from django.db import models


def _pct(**kwargs):
    """Percentage / ratio column: four decimals, nullable."""
    return models.DecimalField(max_digits=16, decimal_places=4, null=True, blank=True, **kwargs)


class MFCategory(models.Model):
    slug = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=200)
    asset_class = models.CharField(max_length=50, blank=True, default="")
    display_order = models.PositiveIntegerField(default=0)
    total_schemes = models.PositiveIntegerField(default=0)
    peer_count = models.PositiveIntegerField(null=True, blank=True)
    median_all_time_max_drawdown = _pct()
    metrics_as_of = models.DateField(null=True, blank=True)
    metrics_synced_at = models.DateTimeField(null=True, blank=True)
    funds_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name_plural = "MF categories"

    def __str__(self):
        return self.name


class MFScheme(models.Model):
    """One AMFI scheme code (a plan/option variant of a fund)."""

    amfi_code = models.CharField(max_length=20, unique=True)
    isin = models.CharField(max_length=20, blank=True, default="")
    name = models.CharField(max_length=500)
    slug = models.CharField(max_length=500, blank=True, default="")
    amc = models.CharField(max_length=300, blank=True, default="")
    category = models.ForeignKey(
        MFCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="schemes",
    )
    plan = models.CharField(max_length=20, blank=True, default="")
    option = models.CharField(max_length=20, blank=True, default="")
    is_active = models.BooleanField(default=True)

    # Scheme facts reported by /mf/metrics-by-cat and /mf/hnav.
    data_as_of = models.DateField(null=True, blank=True)
    age_label = models.CharField(max_length=50, blank=True, default="")
    age_months = _pct()
    avg_aum_crore = _pct()
    aum_period = models.CharField(max_length=100, blank=True, default="")
    ter = _pct()
    ter_date = models.DateField(null=True, blank=True)
    all_time_max_drawdown = _pct()

    # NAV and the returns computed from the full NAV history.
    latest_nav = models.DecimalField(max_digits=24, decimal_places=6, null=True, blank=True)
    latest_nav_date = models.DateField(null=True, blank=True)
    first_nav_date = models.DateField(null=True, blank=True)
    return_1w = _pct()
    return_1m = _pct()
    return_3m = _pct()
    return_6m = _pct()
    return_ytd = _pct()
    return_1y = _pct()
    return_2y = _pct()
    return_3y = _pct()
    return_5y = _pct()
    return_10y = _pct()
    return_since_inception = _pct()
    calendar_returns = models.JSONField(default=dict, blank=True)

    metrics_synced_at = models.DateTimeField(null=True, blank=True)
    nav_synced_at = models.DateTimeField(null=True, blank=True)
    holdings_synced_at = models.DateTimeField(null=True, blank=True)
    aum_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["category", "plan", "option"], name="mfi_scheme_cat_plan_idx"),
            models.Index(fields=["isin"], name="mfi_scheme_isin_idx"),
            models.Index(fields=["amc"], name="mfi_scheme_amc_idx"),
        ]

    def __str__(self):
        return f"{self.name} ({self.amfi_code})"


class MFSchemeMetric(models.Model):
    """Risk / return metrics for one scheme over one horizon (latest only)."""

    scheme = models.ForeignKey(MFScheme, on_delete=models.CASCADE, related_name="metrics")
    horizon = models.CharField(max_length=5)
    as_of = models.DateField(null=True, blank=True)

    trailing_cagr = _pct()
    avg_rolling_return = _pct()
    max_rolling_return = _pct()
    min_rolling_return = _pct()
    rolling_return_p10 = _pct()
    rolling_return_p25 = _pct()
    rolling_return_p50 = _pct()
    rolling_return_p75 = _pct()
    rolling_return_p90 = _pct()
    rolling_return_sd = _pct()
    trailing_xirr = _pct()
    avg_rolling_xirr = _pct()
    max_rolling_xirr = _pct()
    min_rolling_xirr = _pct()
    rolling_xirr_p10 = _pct()
    rolling_xirr_p25 = _pct()
    rolling_xirr_p50 = _pct()
    rolling_xirr_p75 = _pct()
    rolling_xirr_p90 = _pct()
    rolling_xirr_sd = _pct()
    amfi_volatility = _pct()
    beta = _pct()
    correlation = _pct()
    alpha = _pct()
    upside_capture = _pct()
    downside_capture = _pct()
    sharpe_ratio = _pct()
    sortino_ratio = _pct()
    information_ratio = _pct()
    trailing_max_drawdown = _pct()

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["scheme", "horizon"], name="mfi_unique_scheme_metric_horizon"),
        ]


class MFCategoryMedian(models.Model):
    """Category medians reported by the API (computed across all plans)."""

    category = models.ForeignKey(MFCategory, on_delete=models.CASCADE, related_name="medians")
    horizon = models.CharField(max_length=5)
    as_of = models.DateField(null=True, blank=True)
    values = models.JSONField(default=dict, blank=True)
    sample_counts = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["category", "horizon"], name="mfi_unique_category_median_horizon"),
        ]


class MFNavMonthly(models.Model):
    """Last available NAV of each calendar month (used for growth charts)."""

    scheme = models.ForeignKey(MFScheme, on_delete=models.CASCADE, related_name="monthly_navs")
    month = models.DateField(help_text="First day of the month this NAV belongs to.")
    nav_date = models.DateField()
    nav = models.DecimalField(max_digits=24, decimal_places=6)

    class Meta:
        ordering = ["scheme", "month"]
        constraints = [
            models.UniqueConstraint(fields=["scheme", "month"], name="mfi_unique_scheme_nav_month"),
        ]


class MFHoldingSnapshot(models.Model):
    """One portfolio disclosure of a scheme (monthly or fortnightly)."""

    scheme = models.ForeignKey(MFScheme, on_delete=models.CASCADE, related_name="holding_snapshots")
    as_of = models.DateField()
    prev_as_of = models.DateField(null=True, blank=True)
    total_holdings = models.PositiveIntegerField(default=0)
    equity_pct = _pct()
    debt_pct = _pct()
    fund_unit_pct = _pct()
    commodity_pct = _pct()
    cash_pct = _pct()
    total_pct = _pct()
    derivatives_gross_pct = _pct()
    derivatives_net_pct = _pct()
    derivatives_count = models.PositiveIntegerField(null=True, blank=True)
    net_equity_exposure_pct = _pct()
    new_additions_count = models.PositiveIntegerField(null=True, blank=True)
    complete_exits_count = models.PositiveIntegerField(null=True, blank=True)
    increased_count = models.PositiveIntegerField(null=True, blank=True)
    decreased_count = models.PositiveIntegerField(null=True, blank=True)
    unchanged_count = models.PositiveIntegerField(null=True, blank=True)
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scheme", "-as_of"]
        constraints = [
            models.UniqueConstraint(fields=["scheme", "as_of"], name="mfi_unique_scheme_holding_date"),
        ]


class MFHolding(models.Model):
    snapshot = models.ForeignKey(MFHoldingSnapshot, on_delete=models.CASCADE, related_name="holdings")
    isin = models.CharField(max_length=20, blank=True, default="")
    name = models.CharField(max_length=500)
    industry = models.CharField(max_length=200, blank=True, default="")
    holding_type = models.CharField(max_length=30, blank=True, default="")
    position_side = models.CharField(max_length=10, blank=True, default="")
    holding_pct = _pct()
    quantity = models.DecimalField(max_digits=24, decimal_places=4, null=True, blank=True)
    prev_holding_pct = _pct()
    change_pct = _pct()
    status = models.CharField(max_length=20, blank=True, default="")
    is_exit = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["isin"], name="mfi_holding_isin_idx"),
            models.Index(fields=["snapshot", "holding_type"], name="mfi_holding_snap_type_idx"),
        ]


class MFAumHistory(models.Model):
    scheme = models.ForeignKey(MFScheme, on_delete=models.CASCADE, related_name="aum_history")
    as_of_date = models.DateField()
    period = models.CharField(max_length=100, blank=True, default="")
    avg_aum_crore = _pct()

    class Meta:
        ordering = ["scheme", "as_of_date"]
        constraints = [
            models.UniqueConstraint(fields=["scheme", "as_of_date"], name="mfi_unique_scheme_aum_date"),
        ]


class MFBenchmark(models.Model):
    slug = models.CharField(max_length=120, unique=True)
    name = models.CharField(max_length=200)
    asset_type = models.CharField(max_length=50, blank=True, default="")
    start_date = models.DateField(null=True, blank=True)
    latest_date = models.DateField(null=True, blank=True)
    latest_value = models.DecimalField(max_digits=24, decimal_places=6, null=True, blank=True)
    return_1w = _pct()
    return_1m = _pct()
    return_3m = _pct()
    return_6m = _pct()
    return_ytd = _pct()
    return_1y = _pct()
    return_2y = _pct()
    return_3y = _pct()
    return_5y = _pct()
    return_10y = _pct()
    calendar_returns = models.JSONField(default=dict, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["asset_type", "name"]

    def __str__(self):
        return self.name


class MFBenchmarkMonthly(models.Model):
    benchmark = models.ForeignKey(MFBenchmark, on_delete=models.CASCADE, related_name="monthly_values")
    month = models.DateField()
    value_date = models.DateField()
    value = models.DecimalField(max_digits=24, decimal_places=6)

    class Meta:
        ordering = ["benchmark", "month"]
        constraints = [
            models.UniqueConstraint(fields=["benchmark", "month"], name="mfi_unique_benchmark_month"),
        ]


class MFStockClassification(models.Model):
    """Large / Mid / Small cap bucket for an equity ISIN."""

    SOURCE_AMFI = "AMFI"
    SOURCE_INDEX_PROXY = "INDEX_PROXY"

    isin = models.CharField(max_length=20, unique=True)
    company_name = models.CharField(max_length=300, blank=True, default="")
    cap_bucket = models.CharField(max_length=20)
    rank = models.PositiveIntegerField(null=True, blank=True)
    avg_market_cap_crore = models.DecimalField(max_digits=24, decimal_places=4, null=True, blank=True)
    source = models.CharField(max_length=30)
    period_end = models.DateField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["source", "period_end"], name="mfi_stockclass_source_idx")]


class MFSyncRun(models.Model):
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_PARTIAL = "partial"
    STATUS_FAILED = "failed"

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, default=STATUS_RUNNING)
    trigger = models.CharField(max_length=20, default="manual")
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mf_intelligence_sync_runs",
    )
    scope = models.JSONField(default=dict, blank=True)
    current_step = models.CharField(max_length=200, blank=True, default="")
    stats = models.JSONField(default=dict, blank=True)
    errors = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-started_at"]
