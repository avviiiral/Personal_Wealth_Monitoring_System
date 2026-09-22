from django.db import models
from django.contrib.auth.models import User

from users.models import FamilyGroup

from typing import TYPE_CHECKING


class MutualFundScheme(models.Model):
    """
    Master information about a mutual fund scheme.
    """

    owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mutual_fund_schemes",
    )

    family = models.ForeignKey(FamilyGroup, on_delete=models.PROTECT, related_name="mutual_fund_schemes", null=True, blank=True, db_index=True)

    scheme_name = models.CharField(
        max_length=300,
    )

    amc_name = models.CharField(
        max_length=200,
        blank=True,
        null=True,
    )

    scheme_code = models.CharField(
        max_length=50,
        blank=True,
        null=True,
    )

    isin_growth = models.CharField(
        max_length=30,
        blank=True,
        null=True,
    )

    isin_dividend = models.CharField(
        max_length=30,
        blank=True,
        null=True,
    )

    plan = models.CharField(
        max_length=30,
        blank=True,
        null=True,
    )

    option = models.CharField(
        max_length=50,
        blank=True,
        null=True,
    )

    category = models.CharField(
        max_length=150,
        blank=True,
        null=True,
    )

    is_active = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["scheme_name"]

        constraints = [
            models.UniqueConstraint(
                fields=["family", "scheme_code"],
                name="unique_mf_scheme_family_code",
            ),
            models.UniqueConstraint(
                fields=["family", "scheme_name"],
                name="unique_mf_scheme_family_name",
            ),
        ]

    def __str__(self):
        return self.scheme_name


class MutualFundNAV(models.Model):
    """Historical NAV for a mutual fund scheme."""

    scheme = models.ForeignKey(
        MutualFundScheme,
        on_delete=models.CASCADE,
        related_name="nav_history",
    )

    if TYPE_CHECKING:
        scheme_id: int

    date = models.DateField()

    nav = models.DecimalField(
        max_digits=20,
        decimal_places=6,
    )

    source = models.CharField(
        max_length=50,
        default="AMFI",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(
                fields=["scheme", "date", "source"],
                name="unique_mf_nav",
            )
        ]
        indexes = [
            models.Index(fields=["scheme", "-date"]),
            models.Index(fields=["date"]),
        ]

    def __str__(self):
        return f"{self.scheme.scheme_name} - {self.date} - {self.nav}"


class MutualFundTransactionType(models.TextChoices):
    PURCHASE = "PURCHASE", "Purchase"
    SIP = "SIP", "SIP"
    REDEMPTION = "REDEMPTION", "Redemption"
    DIVIDEND = "DIVIDEND", "Dividend"


class MutualFundTransaction(models.Model):
    """Investor transaction in a mutual fund scheme."""

    owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mutual_fund_transactions",
    )
    family = models.ForeignKey(FamilyGroup, on_delete=models.PROTECT, related_name="mutual_fund_transactions", null=True, blank=True, db_index=True)
    family_name = models.CharField(max_length=255, blank=True, null=True)
    portfolio = models.CharField(max_length=255, blank=True, null=True)
    scheme = models.ForeignKey(
        MutualFundScheme,
        on_delete=models.CASCADE,
        related_name="transactions",
    )

    if TYPE_CHECKING:
        scheme_id: int

    transaction_type = models.CharField(
        max_length=20,
        choices=MutualFundTransactionType.choices,
    )
    transaction_date = models.DateField()
    units = models.DecimalField(max_digits=20, decimal_places=6)
    nav = models.DecimalField(max_digits=20, decimal_places=6)
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    fees = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    notes = models.TextField(blank=True, null=True)
    source_key = models.CharField(max_length=64, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-transaction_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["family", "source_key"],
                condition=models.Q(source_key__isnull=False),
                name="unique_mf_transaction_family_source_key",
            ),
        ]
        indexes = [
            models.Index(fields=["family", "source_key"], name="mf_tx_family_source_key_idx"),
        ]

    def __str__(self):
        return f"{self.scheme.scheme_name} - {self.transaction_type} - {self.amount}"


class SIPFrequency(models.TextChoices):
    MONTHLY = "MONTHLY", "Monthly"
    WEEKLY = "WEEKLY", "Weekly"
    QUARTERLY = "QUARTERLY", "Quarterly"
    YEARLY = "YEARLY", "Yearly"


class SIP(models.Model):
    """SIP instruction/configuration."""

    owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sips",
    )
    family = models.ForeignKey(FamilyGroup, on_delete=models.PROTECT, related_name="sips", null=True, blank=True, db_index=True)
    scheme = models.ForeignKey(
        MutualFundScheme,
        on_delete=models.CASCADE,
        related_name="sips",
    )
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    frequency = models.CharField(
        max_length=20,
        choices=SIPFrequency.choices,
        default=SIPFrequency.MONTHLY,
    )
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    next_installment_date = models.DateField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["next_installment_date", "scheme__scheme_name"]

    def __str__(self):
        return f"{self.scheme.scheme_name} - ₹{self.amount} - {self.frequency}"


class MutualFundHolding(models.Model):
    """Current calculated position in a mutual fund scheme."""

    owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mutual_fund_holdings",
    )
    family = models.ForeignKey(FamilyGroup, on_delete=models.PROTECT, related_name="mutual_fund_holdings", null=True, blank=True, db_index=True)
    scheme = models.OneToOneField(
        MutualFundScheme,
        on_delete=models.CASCADE,
        related_name="holding",
    )

    if TYPE_CHECKING:
        scheme_id: int

    units = models.DecimalField(max_digits=20, decimal_places=6, default=0)
    invested_value = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    average_nav = models.DecimalField(max_digits=20, decimal_places=6, default=0)
    current_nav = models.DecimalField(max_digits=20, decimal_places=6, default=0)
    current_value = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    unrealized_pnl = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scheme__scheme_name"]

    def __str__(self):
        return f"{self.scheme.scheme_name} - {self.units} units"


class SIPInstallmentStatus(models.TextChoices):
    SCHEDULED = "SCHEDULED", "Scheduled"
    DUE = "DUE", "Due"
    EXECUTED = "EXECUTED", "Executed"
    SKIPPED = "SKIPPED", "Skipped"
    FAILED = "FAILED", "Failed"


class SIPInstallment(models.Model):
    """Individual scheduled SIP installment."""

    sip = models.ForeignKey(
        SIP,
        on_delete=models.CASCADE,
        related_name="installments",
    )
    scheduled_date = models.DateField()
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    status = models.CharField(
        max_length=20,
        choices=SIPInstallmentStatus.choices,
        default=SIPInstallmentStatus.SCHEDULED,
    )
    transaction = models.OneToOneField(
        "MutualFundTransaction",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="sip_installment",
    )
    executed_at = models.DateTimeField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scheduled_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["sip", "scheduled_date"],
                name="unique_sip_installment",
            )
        ]
        indexes = [
            models.Index(fields=["sip", "scheduled_date"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.sip.scheme.scheme_name} - {self.scheduled_date} - {self.status}"


# Keep the historical models above stable while exposing the new
# disclosure model through the conventional mutual_funds.models module.
from .underlying_models import MutualFundUnderlying  # noqa: E402,F401
