from django.db import models


class ProductType(models.TextChoices):
    MUTUAL_FUND = "MUTUAL_FUND", "Mutual Fund"
    PMS = "PMS", "PMS"


class InvestmentProduct(models.Model):
    """Provider-neutral master for research products and owned investments."""

    product_type = models.CharField(max_length=20, choices=ProductType.choices)
    name = models.CharField(max_length=500)
    provider = models.CharField(max_length=300, blank=True, null=True)
    country = models.CharField(max_length=100, blank=True, null=True)
    category = models.CharField(max_length=200, blank=True, null=True)
    sub_category = models.CharField(max_length=200, blank=True, null=True)
    isin = models.CharField(max_length=30, blank=True, null=True)
    external_identifier = models.CharField(max_length=150, blank=True, null=True)
    currency = models.CharField(max_length=20, blank=True, null=True)
    source = models.CharField(max_length=100, blank=True, null=True)
    source_reference = models.CharField(max_length=1000, blank=True, null=True)
    source_date = models.DateField(blank=True, null=True)
    official_website = models.URLField(max_length=1000, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    identity_key = models.CharField(max_length=600, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["product_type", "name"]),
            models.Index(fields=["product_type", "provider"]),
            models.Index(fields=["isin"]),
            models.Index(fields=["external_identifier"]),
        ]

    def __str__(self):
        return self.name


class MutualFundProduct(models.Model):
    product = models.OneToOneField(InvestmentProduct, on_delete=models.CASCADE, related_name="mutual_fund")
    scheme_code = models.CharField(max_length=50, blank=True, null=True)
    fund_type = models.CharField(max_length=150, blank=True, null=True)
    plan = models.CharField(max_length=50, blank=True, null=True)
    option = models.CharField(max_length=100, blank=True, null=True)
    inception_date = models.DateField(blank=True, null=True)
    aum = models.DecimalField(max_digits=24, decimal_places=2, blank=True, null=True)
    expense_ratio = models.DecimalField(max_digits=10, decimal_places=4, blank=True, null=True)
    benchmark = models.CharField(max_length=300, blank=True, null=True)
    fund_manager = models.CharField(max_length=500, blank=True, null=True)
    risk_level = models.CharField(max_length=100, blank=True, null=True)
    minimum_investment = models.DecimalField(max_digits=24, decimal_places=2, blank=True, null=True)
    exit_load = models.CharField(max_length=500, blank=True, null=True)
    latest_nav = models.DecimalField(max_digits=24, decimal_places=8, blank=True, null=True)
    latest_nav_date = models.DateField(blank=True, null=True)


class PMSProduct(models.Model):
    product = models.OneToOneField(InvestmentProduct, on_delete=models.CASCADE, related_name="pms")
    strategy_name = models.CharField(max_length=500, blank=True, null=True)
    strategy_type = models.CharField(max_length=200, blank=True, null=True)
    asset_class = models.CharField(max_length=200, blank=True, null=True)
    benchmark = models.CharField(max_length=300, blank=True, null=True)
    aum = models.DecimalField(max_digits=24, decimal_places=2, blank=True, null=True)
    inception_date = models.DateField(blank=True, null=True)
    minimum_investment = models.DecimalField(max_digits=24, decimal_places=2, blank=True, null=True)
    latest_value = models.DecimalField(max_digits=24, decimal_places=8, blank=True, null=True)
    risk_information = models.CharField(max_length=500, blank=True, null=True)


class PerformanceSnapshot(models.Model):
    product = models.ForeignKey(InvestmentProduct, on_delete=models.CASCADE, related_name="performance_snapshots")
    date = models.DateField()
    nav_or_value = models.DecimalField(max_digits=24, decimal_places=8, blank=True, null=True)
    aum = models.DecimalField(max_digits=24, decimal_places=2, blank=True, null=True)
    return_1d = models.DecimalField(max_digits=12, decimal_places=6, blank=True, null=True)
    return_1w = models.DecimalField(max_digits=12, decimal_places=6, blank=True, null=True)
    return_1m = models.DecimalField(max_digits=12, decimal_places=6, blank=True, null=True)
    return_3m = models.DecimalField(max_digits=12, decimal_places=6, blank=True, null=True)
    return_6m = models.DecimalField(max_digits=12, decimal_places=6, blank=True, null=True)
    return_1y = models.DecimalField(max_digits=12, decimal_places=6, blank=True, null=True)
    return_3y = models.DecimalField(max_digits=12, decimal_places=6, blank=True, null=True)
    return_5y = models.DecimalField(max_digits=12, decimal_places=6, blank=True, null=True)
    return_since_inception = models.DecimalField(max_digits=12, decimal_places=6, blank=True, null=True)
    cagr = models.DecimalField(max_digits=12, decimal_places=6, blank=True, null=True)
    source = models.CharField(max_length=100)
    source_reference = models.CharField(max_length=1000, blank=True, null=True)
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(fields=["product", "date", "source"], name="unique_watchlist_performance_snapshot")
        ]
        indexes = [models.Index(fields=["product", "-date"]), models.Index(fields=["date"]) ]


class DiscoveryRun(models.Model):
    source = models.CharField(max_length=100)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    discovered = models.PositiveIntegerField(default=0)
    updated = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    details = models.JSONField(default=dict)

    class Meta:
        ordering = ["-started_at"]
