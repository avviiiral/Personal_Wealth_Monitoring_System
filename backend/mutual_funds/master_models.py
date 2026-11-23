"""Global, source-attributed mutual-fund masterdata snapshots.

These tables are keyed to AMFIMasterScheme and deliberately separate from the
family-scoped MutualFundScheme / MutualFundUnderlying ledger.
"""
from django.db import models
from django.utils import timezone


class MFMasterData(models.Model):
    """One point-in-time masterdata snapshot per AMFI scheme and as-of date."""

    scheme = models.ForeignKey(
        "mutual_funds.AMFIMasterScheme",
        on_delete=models.CASCADE,
        related_name="masterdata_snapshots",
    )
    as_of_date = models.DateField(db_index=True)

    # Identity is copied into each dated row so historical changes remain auditable.
    scheme_code = models.CharField(max_length=50, blank=True, default="")
    isin_growth = models.CharField(max_length=30, blank=True, default="")
    isin_idcw = models.CharField(max_length=30, blank=True, default="")
    amc = models.CharField(max_length=200, blank=True, default="")
    scheme_name = models.CharField(max_length=300, blank=True, default="")
    plan = models.CharField(max_length=40, blank=True, default="")
    option = models.CharField(max_length=160, blank=True, default="")
    sebi_category_level_1 = models.CharField(max_length=80, blank=True, default="")
    sebi_category_level_2 = models.CharField(max_length=120, blank=True, default="")
    sebi_category_level_3 = models.CharField(max_length=160, blank=True, default="")

    benchmark_name = models.CharField(max_length=300, blank=True, default="")
    inception_date = models.DateField(blank=True, null=True)
    fund_managers = models.JSONField(default=list, blank=True)
    riskometer = models.CharField(max_length=40, blank=True, default="")
    benchmark_riskometer = models.CharField(max_length=40, blank=True, default="")
    exit_load_text = models.TextField(blank=True, default="")
    min_lumpsum = models.DecimalField(max_digits=22, decimal_places=4, blank=True, null=True)
    min_sip = models.DecimalField(max_digits=22, decimal_places=4, blank=True, null=True)

    # AUM is a single typed slot per snapshot. QUARTERLY_AVG is never interpreted
    # as MONTH_END; other dated AUM observations are stored in their own rows.
    AUM_QUARTERLY_AVG = "QUARTERLY_AVG"
    AUM_MONTH_END = "MONTH_END"
    AUM_TYPE_CHOICES = (
        (AUM_QUARTERLY_AVG, "Quarterly average AUM"),
        (AUM_MONTH_END, "Month-end AUM"),
    )
    aum_cr = models.DecimalField(max_digits=24, decimal_places=4, blank=True, null=True)
    aum_type = models.CharField(max_length=20, choices=AUM_TYPE_CHOICES, blank=True, default="")
    aum_as_of = models.DateField(blank=True, null=True)
    aum_source = models.CharField(max_length=100, blank=True, default="")

    ter_regular_pct = models.DecimalField(max_digits=12, decimal_places=4, blank=True, null=True)
    ter_direct_pct = models.DecimalField(max_digits=12, decimal_places=4, blank=True, null=True)
    ter_as_of = models.DateField(blank=True, null=True)

    return_1m_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    return_3m_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    return_6m_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    return_1y_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    return_3y_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    return_5y_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    return_10y_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    since_inception_cagr_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)

    std_dev_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    sharpe = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    sortino = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    beta = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    alpha_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    treynor = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    r_squared = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    tracking_error_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    information_ratio = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    max_drawdown_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    upside_capture_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    downside_capture_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    risk_free_rate_used_pct = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)
    ratio_window_years = models.DecimalField(max_digits=8, decimal_places=4, blank=True, null=True)

    pe = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    pb = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    dividend_yield_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    turnover_ratio = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    large_cap_pct = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)
    mid_cap_pct = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)
    small_cap_pct = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)
    other_cap_pct = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)
    number_of_holdings = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    top10_concentration_pct = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)

    ytm_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    modified_duration = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    macaulay_duration = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    average_maturity = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    credit_rating_split = models.JSONField(default=dict, blank=True)

    equity_pct = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)
    debt_pct = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)
    cash_pct = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)
    others_pct = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)

    # JSON mapping: field_name -> {source, source_url, as_of_date, fetched_at}.
    field_sources = models.JSONField(default=dict, blank=True)
    data_quality_flags = models.JSONField(default=list, blank=True)
    fetched_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mf_masterdata"
        ordering = ["-as_of_date", "scheme__scheme_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["scheme", "as_of_date"],
                name="unique_mf_masterdata_scheme_date",
            ),
        ]
        indexes = [
            models.Index(fields=["scheme", "as_of_date"], name="mf_master_scheme_date_idx"),
            models.Index(fields=["as_of_date", "sebi_category_level_3"], name="mf_master_date_cat_idx"),
            models.Index(fields=["amc", "as_of_date"], name="mf_master_amc_date_idx"),
        ]

    def __str__(self):
        return f"{self.scheme_code or self.scheme.scheme_code} - {self.as_of_date}"


class MFMasterHolding(models.Model):
    """One security/instrument row in a global scheme portfolio snapshot."""

    scheme = models.ForeignKey(
        "mutual_funds.AMFIMasterScheme",
        on_delete=models.CASCADE,
        related_name="master_holdings",
    )
    portfolio_date = models.DateField()
    security_name = models.CharField(max_length=300)
    isin = models.CharField(max_length=20, blank=True, default="", db_index=True)
    security_key = models.CharField(max_length=320)
    instrument_type = models.CharField(max_length=60, blank=True, default="OTHER")
    sector = models.CharField(max_length=150, blank=True, default="")
    industry = models.CharField(max_length=150, blank=True, default="")
    rating = models.CharField(max_length=80, blank=True, default="")
    rating_agency = models.CharField(max_length=80, blank=True, default="")
    quantity = models.DecimalField(max_digits=28, decimal_places=4, blank=True, null=True)
    market_value = models.DecimalField(max_digits=28, decimal_places=4, blank=True, null=True)
    pct_of_nav = models.DecimalField(max_digits=12, decimal_places=4, blank=True, null=True)
    maturity_date = models.DateField(blank=True, null=True)
    ytm_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    source = models.CharField(max_length=100)
    source_url = models.URLField(max_length=1000, blank=True, default="")
    fetched_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "mf_master_holdings"
        ordering = ["-portfolio_date", "-pct_of_nav", "security_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["scheme", "portfolio_date", "security_key"],
                name="unique_mf_master_holding_snapshot",
            ),
        ]
        indexes = [
            models.Index(fields=["scheme", "portfolio_date"], name="mf_master_hold_scheme_date_idx"),
            models.Index(fields=["portfolio_date"], name="mf_master_hold_date_idx"),
        ]

    def __str__(self):
        return f"{self.scheme.scheme_code} - {self.security_name} - {self.portfolio_date}"


class MFSourceRun(models.Model):
    """Audit record for an individual source download/import/build run."""

    class Status(models.TextChoices):
        RUNNING = "RUNNING", "Running"
        SUCCESS = "SUCCESS", "Success"
        PARTIAL = "PARTIAL", "Partial"
        FAILED = "FAILED", "Failed"
        DRY_RUN = "DRY_RUN", "Dry run"

    source = models.CharField(max_length=100, db_index=True)
    command = models.CharField(max_length=120, blank=True, default="")
    as_of_date = models.DateField(blank=True, null=True, db_index=True)
    source_url = models.URLField(max_length=1000, blank=True, default="")
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RUNNING)
    rows_parsed = models.PositiveIntegerField(default=0)
    rows_inserted = models.PositiveIntegerField(default=0)
    rows_updated = models.PositiveIntegerField(default=0)
    rows_skipped = models.PositiveIntegerField(default=0)
    rows_unmatched = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    raw_file_path = models.CharField(max_length=1000, blank=True, default="")

    class Meta:
        db_table = "mf_source_runs"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["source", "started_at"], name="mf_source_run_src_start_idx"),
            models.Index(fields=["as_of_date", "status"], name="mf_source_run_date_status_idx"),
        ]

    def __str__(self):
        return f"{self.source} - {self.started_at:%Y-%m-%d %H:%M} - {self.status}"
