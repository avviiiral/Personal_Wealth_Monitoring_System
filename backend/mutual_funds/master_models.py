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
    rolling_3y_cagr_mean_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    rolling_3y_cagr_min_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    rolling_3y_cagr_max_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    rolling_3y_cagr_observations = models.PositiveIntegerField(blank=True, null=True)

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
    sector_allocation = models.JSONField(default=dict, blank=True)

    equity_pct = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)
    debt_pct = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)
    cash_pct = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)
    others_pct = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)

    # ---- Automated ingestion pipeline (mf_pipeline) -------------------------
    # Latest official NAV carried on the snapshot (history stays in AMFIMasterNAV).
    nav = models.DecimalField(max_digits=20, decimal_places=4, blank=True, null=True)
    nav_as_of = models.DateField(blank=True, null=True)
    nav_source = models.CharField(max_length=100, blank=True, default="")
    ter_source = models.CharField(max_length=100, blank=True, default="")

    # AMFI "Disclosure of Risk Parameters" (SEBI mid/small-cap disclosure).
    # AMFI publishes portfolio/benchmark annualised SD and portfolio beta only;
    # it does not publish a Sharpe ratio, so amfi_sharpe stays null until an
    # official source exists.
    amfi_std_dev_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    amfi_benchmark_std_dev_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    amfi_beta = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    amfi_sharpe = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    amfi_risk_as_of = models.DateField(blank=True, null=True)
    amfi_risk_source = models.CharField(max_length=100, blank=True, default="")
    amfi_tracking_error_regular_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    amfi_tracking_error_direct_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    amfi_tracking_error_as_of = models.DateField(blank=True, null=True)
    amfi_tracking_error_source = models.CharField(max_length=100, blank=True, default="")

    # PWMS-calculated ratios (services/mf_ratios.py): daily NAV returns,
    # annualised, 1Y and 3Y trailing windows. The pre-existing std_dev_pct /
    # sharpe / sortino / beta / alpha_pct columns are left untouched.
    calc_std_dev_1y_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    calc_sharpe_1y = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    calc_sortino_1y = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    calc_beta_1y = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    calc_alpha_1y_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    calc_std_dev_3y_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    calc_sharpe_3y = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    calc_sortino_3y = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    calc_beta_3y = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    calc_alpha_3y_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    calc_risk_free_rate_pct = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)
    calc_benchmark_index_code = models.CharField(max_length=100, blank=True, default="")
    calc_ratios_as_of = models.DateField(blank=True, null=True)
    calc_ratios_source = models.CharField(max_length=100, blank=True, default="")

    # Holdings summary (detail rows live in mf_master_holdings).
    top10_holdings = models.JSONField(default=list, blank=True)
    holdings_as_of = models.DateField(blank=True, null=True)
    holdings_source = models.CharField(max_length=100, blank=True, default="")

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
    ticker = models.CharField(max_length=40, blank=True, default="")
    security_key = models.CharField(max_length=320)
    DATE_BASIS_DISCLOSURE = "DISCLOSURE"
    DATE_BASIS_FETCHED_AT = "FETCHED_AT"
    PORTFOLIO_DATE_BASIS_CHOICES = (
        (DATE_BASIS_DISCLOSURE, "Provider disclosure date"),
        (DATE_BASIS_FETCHED_AT, "Provider retrieval date only"),
    )
    portfolio_date_basis = models.CharField(
        max_length=16, choices=PORTFOLIO_DATE_BASIS_CHOICES,
        default=DATE_BASIS_DISCLOSURE, db_index=True,
    )
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
                fields=["scheme", "portfolio_date", "portfolio_date_basis", "security_key"],
                name="uq_mf_holding_date_basis",
            ),
        ]
        indexes = [
            models.Index(fields=["scheme", "portfolio_date"], name="mf_master_hold_scheme_date_idx"),
            models.Index(fields=["portfolio_date"], name="mf_master_hold_date_idx"),
        ]

    def __str__(self):
        return f"{self.scheme.scheme_code} - {self.security_name} - {self.portfolio_date}"


class MFExternalPortfolioSnapshot(models.Model):
    """Provider response metadata kept separate from dated AMC disclosures."""

    PROVIDER_MFFORECAST = "MFFORECAST"
    PROVIDER_CHOICES = ((PROVIDER_MFFORECAST, "MF Forecast"),)

    scheme = models.ForeignKey(
        "mutual_funds.AMFIMasterScheme",
        on_delete=models.CASCADE,
        related_name="external_portfolio_snapshots",
    )
    provider = models.CharField(max_length=40, choices=PROVIDER_CHOICES, default=PROVIDER_MFFORECAST)
    observed_at = models.DateTimeField(default=timezone.now, db_index=True)
    provider_as_of_date = models.DateField(blank=True, null=True)
    scheme_name = models.CharField(max_length=300, blank=True, default="")
    fund_house = models.CharField(max_length=200, blank=True, default="")
    category = models.CharField(max_length=160, blank=True, default="")
    aum_cr = models.DecimalField(max_digits=24, decimal_places=4, blank=True, null=True)
    expense_ratio_pct = models.DecimalField(max_digits=12, decimal_places=4, blank=True, null=True)
    asset_allocation = models.JSONField(default=dict, blank=True)
    holdings_count = models.PositiveIntegerField(default=0)
    total_weight_pct = models.DecimalField(max_digits=12, decimal_places=4, blank=True, null=True)
    quality_flags = models.JSONField(default=list, blank=True)
    source_url = models.URLField(max_length=1000, blank=True, default="")
    raw_file_path = models.CharField(max_length=1000, blank=True, default="")

    class Meta:
        db_table = "mf_external_portfolio_snapshots"
        ordering = ["-observed_at"]
        indexes = [
            models.Index(fields=["scheme", "-observed_at"], name="mf_ext_port_scheme_obs_idx"),
            models.Index(fields=["provider", "-observed_at"], name="mf_ext_port_prov_obs_idx"),
        ]

    def __str__(self):
        return f"{self.provider} - {self.scheme.scheme_code} - {self.observed_at:%Y-%m-%d %H:%M}"


class MFSourceRun(models.Model):
    """Audit record for an individual source download/import/build run."""

    class Status(models.TextChoices):
        RUNNING = "RUNNING", "Running"
        SUCCESS = "SUCCESS", "Success"
        PARTIAL = "PARTIAL", "Partial"
        FAILED = "FAILED", "Failed"
        DRY_RUN = "DRY_RUN", "Dry run"
        # Pipeline job ran but had nothing to do: unchanged file hash, period
        # already loaded, source not yet published or not configured. The
        # reason is stored in metadata["skip_reason"].
        SKIPPED = "SKIPPED", "Skipped"

    source = models.CharField(max_length=100, db_index=True)
    # Pipeline jobs store their job name here (e.g. "mf_sync_nav").
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
    rows_failed = models.PositiveIntegerField(default=0)
    source_file_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)
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


class MFBenchmarkTRI(models.Model):
    """Daily official benchmark total-return-index observations."""
    index_code = models.CharField(max_length=100, db_index=True)
    index_name = models.CharField(max_length=300)
    date = models.DateField(db_index=True)
    tri_value = models.DecimalField(max_digits=24, decimal_places=4)
    source = models.CharField(max_length=100)
    source_url = models.URLField(max_length=1000, blank=True, default="")
    fetched_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "mf_benchmark_tri"
        ordering = ["index_code", "date"]
        constraints = [
            models.UniqueConstraint(fields=["index_code", "date"], name="unique_mf_benchmark_tri_date"),
        ]
        indexes = [
            models.Index(fields=["index_code", "-date"], name="mf_benchmark_tri_code_date_idx"),
        ]

    def __str__(self):
        return f"{self.index_name} - {self.date} - {self.tri_value}"


class MFMarketCapClassification(models.Model):
    """ISIN-level AMFI large/mid/small-cap classification for an effective half-year."""
    LARGE_CAP = "LARGE_CAP"
    MID_CAP = "MID_CAP"
    SMALL_CAP = "SMALL_CAP"
    MARKET_CAP_CHOICES = (
        (LARGE_CAP, "Large Cap"),
        (MID_CAP, "Mid Cap"),
        (SMALL_CAP, "Small Cap"),
    )

    isin = models.CharField(max_length=20, db_index=True)
    company_name = models.CharField(max_length=300, blank=True, default="")
    market_cap_rank = models.PositiveIntegerField(blank=True, null=True)
    market_cap_class = models.CharField(max_length=20, choices=MARKET_CAP_CHOICES)
    effective_period = models.CharField(max_length=20)
    source = models.CharField(max_length=100, default="AMFI")
    source_url = models.URLField(max_length=1000, blank=True, default="")
    fetched_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "mf_market_cap_classification"
        ordering = ["effective_period", "market_cap_rank", "company_name"]
        constraints = [
            models.UniqueConstraint(fields=["isin", "effective_period"], name="unique_mf_market_cap_period_isin"),
        ]
        indexes = [
            models.Index(fields=["effective_period", "market_cap_class"], name="mf_market_cap_period_idx"),
        ]

    def __str__(self):
        return f"{self.company_name or self.isin} - {self.market_cap_class} - {self.effective_period}"


class MFRiskFreeRate(models.Model):
    """Risk-free rate observations (default tenor: 91-day T-bill), annual %."""

    TENOR_91D = "91D"
    tenor = models.CharField(max_length=10, default=TENOR_91D)
    date = models.DateField(db_index=True)
    rate_pct = models.DecimalField(max_digits=10, decimal_places=4)
    source = models.CharField(max_length=100)
    source_url = models.URLField(max_length=1000, blank=True, default="")
    fetched_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "mf_risk_free_rate"
        ordering = ["tenor", "-date"]
        constraints = [
            models.UniqueConstraint(fields=["tenor", "date"], name="unique_mf_risk_free_tenor_date"),
        ]

    def __str__(self):
        return f"{self.tenor} {self.date}: {self.rate_pct}%"


class MFSchemeIdentityMap(models.Model):
    """Maps an external (non-ISIN) scheme identifier to AMFI scheme codes.

    AMFI's TER, risk-parameter and tracking-error feeds identify the *parent*
    scheme (NSDL scheme code / AMFI risk SchemeID / parent scheme name) rather
    than a plan-level ISIN. Each confirmed row links that external identity to
    one plan/option-level AMFIMasterScheme, so the feeds are joined by stored
    IDs, never by re-matching names at load time.
    """

    SOURCE_TER_NSDL = "AMFI_TER_NSDL"
    SOURCE_RISK_SCHEME_ID = "AMFI_RISK_SCHEME_ID"
    SOURCE_TRACKING_NAME = "AMFI_TRACKING_PARENT"
    SOURCE_CHOICES = (
        (SOURCE_TER_NSDL, "AMFI TER NSDL scheme code"),
        (SOURCE_RISK_SCHEME_ID, "AMFI risk-parameter SchemeID"),
        (SOURCE_TRACKING_NAME, "AMFI tracking-error parent scheme"),
    )
    METHOD_PARENT_NAME_EXACT = "AMFI_PARENT_NAME_EXACT"
    METHOD_MANUAL = "MANUAL"
    STATUS_ACTIVE = "ACTIVE"
    STATUS_REJECTED = "REJECTED"
    STATUS_CHOICES = ((STATUS_ACTIVE, "Active"), (STATUS_REJECTED, "Rejected"))

    source = models.CharField(max_length=40, choices=SOURCE_CHOICES, db_index=True)
    external_id = models.CharField(max_length=120)
    external_name = models.CharField(max_length=300, blank=True, default="")
    scheme = models.ForeignKey(
        "mutual_funds.AMFIMasterScheme",
        on_delete=models.CASCADE,
        related_name="identity_maps",
    )
    match_method = models.CharField(max_length=40, default=METHOD_PARENT_NAME_EXACT)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mf_scheme_identity_map"
        ordering = ["source", "external_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "external_id", "scheme"],
                name="unique_mf_identity_source_ext_scheme",
            ),
        ]
        indexes = [
            models.Index(fields=["source", "external_id"], name="mf_identity_src_ext_idx"),
        ]

    def __str__(self):
        return f"{self.source}:{self.external_id} -> {self.scheme_id}"


class MFAMFIRiskParameter(models.Model):
    """Raw AMFI risk-parameter disclosure row, one per AMFI SchemeID per month."""

    risk_scheme_id = models.CharField(max_length=40)
    report_month = models.DateField(help_text="First day of the reporting month, as AMFI labels it.")
    category_id = models.CharField(max_length=10, blank=True, default="")
    mf_id = models.CharField(max_length=20, blank=True, default="")
    amc_name = models.CharField(max_length=200, blank=True, default="")
    scheme_name = models.CharField(max_length=300, blank=True, default="")
    aum_cr = models.DecimalField(max_digits=24, decimal_places=4, blank=True, null=True)
    portfolio_std_dev_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    benchmark_std_dev_pct = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    portfolio_beta = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    portfolio_pe = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    portfolio_turnover_ratio = models.DecimalField(max_digits=14, decimal_places=4, blank=True, null=True)
    raw = models.JSONField(default=dict, blank=True)
    source = models.CharField(max_length=100, default="AMFI_RISK_PARAMETERS")
    source_url = models.URLField(max_length=1000, blank=True, default="")
    fetched_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "mf_amfi_risk_parameter"
        ordering = ["-report_month", "scheme_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["risk_scheme_id", "report_month"],
                name="unique_mf_amfi_risk_scheme_month",
            ),
        ]

    def __str__(self):
        return f"{self.scheme_name} - {self.report_month}"
