from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class CorporateActionType(models.TextChoices):
    STOCK_SPLIT = "STOCK_SPLIT", "Stock split"
    REVERSE_SPLIT = "REVERSE_SPLIT", "Reverse stock split"
    BONUS = "BONUS", "Bonus issue"
    STOCK_DIVIDEND = "STOCK_DIVIDEND", "Stock dividend"
    CASH_DIVIDEND = "CASH_DIVIDEND", "Cash dividend"
    MERGER = "MERGER", "Merger / amalgamation"
    DEMERGER = "DEMERGER", "Demerger / spin-off"
    RIGHTS_ISSUE = "RIGHTS_ISSUE", "Rights issue"
    BUYBACK = "BUYBACK", "Buyback"
    TENDER_OFFER = "TENDER_OFFER", "Tender offer"
    CASH_IN_LIEU = "CASH_IN_LIEU", "Cash in lieu"
    WARRANT_CONVERSION = "WARRANT_CONVERSION", "Warrant conversion"
    ADR_RATIO_CHANGE = "ADR_RATIO_CHANGE", "ADR/GDR ratio change"
    TICKER_CHANGE = "TICKER_CHANGE", "Ticker change"
    ISIN_CHANGE = "ISIN_CHANGE", "ISIN change"
    DELISTING = "DELISTING", "Delisting / suspension"
    MF_DISTRIBUTION = "MF_DISTRIBUTION", "Mutual fund distribution / IDCW"
    MF_OPTION_CHANGE = "MF_OPTION_CHANGE", "Mutual fund plan / option change"
    MF_UNIT_ADJUSTMENT = "MF_UNIT_ADJUSTMENT", "Mutual fund unit adjustment"


class CorporateActionStatus(models.TextChoices):
    PENDING = "PENDING", "Pending review"
    CONFIRMED = "CONFIRMED", "Confirmed"
    REJECTED = "REJECTED", "Rejected"


RATIO_ACTIONS = {
    CorporateActionType.STOCK_SPLIT,
    CorporateActionType.REVERSE_SPLIT,
    CorporateActionType.BONUS,
    CorporateActionType.STOCK_DIVIDEND,
    CorporateActionType.ADR_RATIO_CHANGE,
    CorporateActionType.MF_UNIT_ADJUSTMENT,
}


class CorporateAction(models.Model):
    """Auditable corporate-action event keyed by security identifiers, not display name."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="corporate_actions",
    )
    action_type = models.CharField(max_length=32, choices=CorporateActionType.choices)
    security_name = models.CharField(max_length=255)
    isin = models.CharField(max_length=20, blank=True, default="", db_index=True)
    symbol = models.CharField(max_length=50, blank=True, default="", db_index=True)
    exchange = models.CharField(max_length=20, blank=True, default="")
    ex_date = models.DateField()
    record_date = models.DateField(null=True, blank=True)
    payment_date = models.DateField(null=True, blank=True)
    old_units = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    new_units = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    cash_per_unit = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    resulting_isin = models.CharField(max_length=20, blank=True, default="")
    resulting_symbol = models.CharField(max_length=50, blank=True, default="")
    source = models.CharField(max_length=40, blank=True, default="MANUAL")
    source_reference = models.URLField(max_length=1000, blank=True, default="")
    status = models.CharField(
        max_length=12, choices=CorporateActionStatus.choices, default=CorporateActionStatus.PENDING
    )
    notes = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-ex_date", "security_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "isin", "action_type", "ex_date"],
                condition=~Q(isin=""),
                name="uniq_corp_action_owner_isin_type_date",
            ),
        ]
        indexes = [
            models.Index(fields=["owner", "status", "ex_date"], name="corp_action_review_idx"),
            models.Index(fields=["isin", "ex_date"], name="corp_action_isin_date_idx"),
            models.Index(fields=["symbol", "exchange", "ex_date"], name="corp_action_symbol_idx"),
        ]

    def clean(self):
        super().clean()
        if not self.isin and not self.symbol:
            raise ValidationError("Provide an ISIN or a symbol to identify the security.")
        if self.action_type in RATIO_ACTIONS:
            if self.old_units is None or self.new_units is None:
                raise ValidationError("This action requires old_units and new_units.")
            if self.old_units <= 0 or self.new_units <= 0:
                raise ValidationError("Ratio units must be greater than zero.")
        if self.cash_per_unit is not None and self.cash_per_unit < 0:
            raise ValidationError("cash_per_unit cannot be negative.")
        if self.record_date and self.payment_date and self.payment_date < self.record_date:
            raise ValidationError("payment_date cannot precede record_date.")

    @property
    def quantity_factor(self):
        """Multiplier for eligible pre-event units; only valid for confirmed ratio actions."""
        if self.status != CorporateActionStatus.CONFIRMED or self.action_type not in RATIO_ACTIONS:
            return None
        if not self.old_units or not self.new_units:
            return None
        return self.new_units / self.old_units

    def __str__(self):
        return f"{self.security_name} — {self.get_action_type_display()} ({self.ex_date})"
