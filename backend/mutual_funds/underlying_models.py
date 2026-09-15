from django.db import models


class MutualFundUnderlying(models.Model):
    """Historical holdings disclosed by a mutual-fund scheme."""

    scheme = models.ForeignKey(
        "mutual_funds.MutualFundScheme",
        on_delete=models.CASCADE,
        related_name="underlying_holdings",
    )
    security_name = models.CharField(max_length=300)
    isin = models.CharField(max_length=20, blank=True, null=True)
    security_key = models.CharField(
        max_length=320,
        help_text="Normalized ISIN or security name used for deduplication.",
    )
    quantity = models.DecimalField(
        max_digits=24,
        decimal_places=6,
        blank=True,
        null=True,
    )
    market_value = models.DecimalField(
        max_digits=24,
        decimal_places=2,
        blank=True,
        null=True,
    )
    percentage_of_nav = models.DecimalField(
        max_digits=10,
        decimal_places=4,
    )
    portfolio_date = models.DateField()
    source = models.CharField(max_length=20, default="AMFI")
    source_reference = models.CharField(
        max_length=500,
        blank=True,
        null=True,
    )
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-portfolio_date", "-percentage_of_nav", "security_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["scheme", "portfolio_date", "security_key", "source"],
                name="unique_mf_underlying_snapshot",
            )
        ]
        indexes = [
            models.Index(
                fields=["scheme", "-portfolio_date"],
                name="mf_underlying_scheme_date_idx",
            ),
            models.Index(
                fields=["isin"],
                name="mf_underlying_isin_idx",
            ),
            models.Index(
                fields=["security_key"],
                name="mf_underlying_security_key_idx",
            ),
        ]

    def __str__(self):
        return f"{self.scheme.scheme_name} - {self.security_name} - {self.portfolio_date}"
