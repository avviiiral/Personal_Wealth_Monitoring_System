from django.conf import settings
from django.db import models

from typing import TYPE_CHECKING

from investments.models import Asset


class DataSource(models.TextChoices):
    YAHOO_FINANCE = "YAHOO_FINANCE", "Yahoo Finance"
    AMFI = "AMFI", "AMFI"
    MANUAL = "MANUAL", "Manual"
    OTHER = "OTHER", "Other"


class MarketPrice(models.Model):
    """
    Historical market price for an asset.

    One record represents the market data for one asset
    on one trading/business date.
    """

    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="market_prices",
    )

    if TYPE_CHECKING:
        asset_id: int

    date = models.DateField()

    open_price = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        null=True,
        blank=True,
    )

    high_price = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        null=True,
        blank=True,
    )

    low_price = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        null=True,
        blank=True,
    )

    close_price = models.DecimalField(
        max_digits=20,
        decimal_places=6,
    )

    adjusted_close = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        null=True,
        blank=True,
    )

    volume = models.BigIntegerField(
        null=True,
        blank=True,
    )

    source = models.CharField(
        max_length=30,
        choices=DataSource.choices,
        default=DataSource.YAHOO_FINANCE,
    )

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="manual_price_updates",
        help_text=(
            "User who manually set this price. Only ever set for "
            "source=MANUAL rows; audit trail for manual overrides."
        ),
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["-date"]

        constraints = [
            models.UniqueConstraint(
                fields=["asset", "date", "source"],
                name="unique_asset_market_price",
            )
        ]

        indexes = [
            models.Index(
                fields=["asset", "-date"]
            ),
            models.Index(
                fields=["date"]
            ),
        ]

    def __str__(self):
        return (
            f"{self.asset.name} - "
            f"{self.date} - "
            f"{self.close_price}"
        )


class ManualAssetPrice(models.Model):
    """
    Manually entered current price for an asset.

    This is used when automatic market data is not
    available for the asset.

    Examples:
        - Sovereign Gold Bonds
        - Private/unlisted investments
        - Private equity
        - AIFs
        - Assets without ISIN
        - Any security that cannot be resolved
          automatically
    """

    asset = models.OneToOneField(
        Asset,
        on_delete=models.CASCADE,
        related_name="manual_price",
    )

    if TYPE_CHECKING:
        asset_id: int

    price = models.DecimalField(
        max_digits=20,
        decimal_places=6,
    )

    price_date = models.DateField()

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["-price_date"]

    def __str__(self):
        return (
            f"{self.asset.name} - "
            f"{self.price} - "
            f"{self.price_date}"
        )