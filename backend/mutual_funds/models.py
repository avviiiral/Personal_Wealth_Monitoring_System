from django.db import models
from django.contrib.auth.models import User

from typing import TYPE_CHECKING


class MutualFundScheme(models.Model):
    """
    Master information about a mutual fund scheme.
    """

    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="mutual_fund_schemes",
    )

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
            # scheme_code is nullable (a scheme can be created
            # without a known AMFI code via transaction_import.py)
            # - SQLite/Postgres both treat NULL as distinct in a
            # unique index by default, so this only actually
            # enforces uniqueness once scheme_code is set, which
            # is exactly what's needed here. Required for
            # bulk_create(update_conflicts=True) below to have a
            # real conflict target to upsert against.
            models.UniqueConstraint(
                fields=["owner", "scheme_code"],
                name="unique_mf_scheme_owner_code",
            ),
        ]

    def __str__(self):
        return self.scheme_name


class MutualFundNAV(models.Model):
    """
    Historical NAV for a mutual fund scheme.
    """

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

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["-date"]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "scheme",
                    "date",
                    "source",
                ],
                name="unique_mf_nav",
            )
        ]

        indexes = [
            models.Index(
                fields=[
                    "scheme",
                    "-date",
                ]
            ),
            models.Index(
                fields=[
                    "date",
                ]
            ),
        ]

    def __str__(self):
        return (
            f"{self.scheme.scheme_name} - "
            f"{self.date} - "
            f"{self.nav}"
        )


class MutualFundTransactionType(models.TextChoices):

    PURCHASE = "PURCHASE", "Purchase"

    SIP = "SIP", "SIP"

    REDEMPTION = "REDEMPTION", "Redemption"

    DIVIDEND = "DIVIDEND", "Dividend"


class MutualFundTransaction(models.Model):
    """
    Investor transaction in a mutual fund scheme.
    """

    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="mutual_fund_transactions",
    )

    family_name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    portfolio = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

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

    units = models.DecimalField(
        max_digits=20,
        decimal_places=6,
    )

    nav = models.DecimalField(
        max_digits=20,
        decimal_places=6,
    )

    amount = models.DecimalField(
        max_digits=20,
        decimal_places=2,
    )

    fees = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
    )

    notes = models.TextField(
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = [
            "-transaction_date",
            "-created_at",
        ]

    def __str__(self):
        return (
            f"{self.scheme.scheme_name} - "
            f"{self.transaction_type} - "
            f"{self.amount}"
        )


class SIPFrequency(models.TextChoices):

    MONTHLY = "MONTHLY", "Monthly"

    WEEKLY = "WEEKLY", "Weekly"

    QUARTERLY = "QUARTERLY", "Quarterly"

    YEARLY = "YEARLY", "Yearly"


class SIP(models.Model):
    """
    SIP instruction/configuration.

    Actual investment transactions are stored separately
    in MutualFundTransaction.
    """

    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="sips",
    )

    scheme = models.ForeignKey(
        MutualFundScheme,
        on_delete=models.CASCADE,
        related_name="sips",
    )

    amount = models.DecimalField(
        max_digits=20,
        decimal_places=2,
    )

    frequency = models.CharField(
        max_length=20,
        choices=SIPFrequency.choices,
        default=SIPFrequency.MONTHLY,
    )

    start_date = models.DateField()

    end_date = models.DateField(
        blank=True,
        null=True,
    )

    next_installment_date = models.DateField(
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
        ordering = [
            "next_installment_date",
            "scheme__scheme_name",
        ]

    def __str__(self):
        return (
            f"{self.scheme.scheme_name} - "
            f"₹{self.amount} - "
            f"{self.frequency}"
        )


class MutualFundHolding(models.Model):
    """
    Current calculated position in a mutual fund scheme.
    """

    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="mutual_fund_holdings",
    )

    scheme = models.OneToOneField(
        MutualFundScheme,
        on_delete=models.CASCADE,
        related_name="holding",
    )

    if TYPE_CHECKING:
        scheme_id: int

    units = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        default=0,
    )

    invested_value = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
    )

    average_nav = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        default=0,
    )

    current_nav = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        default=0,
    )

    current_value = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
    )

    unrealized_pnl = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "scheme__scheme_name",
        ]

    def __str__(self):
        return (
            f"{self.scheme.scheme_name} - "
            f"{self.units} units"
        )
        
class SIPInstallmentStatus(models.TextChoices):
    
    SCHEDULED = "SCHEDULED", "Scheduled"

    DUE = "DUE", "Due"

    EXECUTED = "EXECUTED", "Executed"

    SKIPPED = "SKIPPED", "Skipped"

    FAILED = "FAILED", "Failed"


class SIPInstallment(models.Model):
    """
    Individual scheduled SIP installment.

    This records the difference between a scheduled SIP
    and an actual investment transaction.
    """

    sip = models.ForeignKey(
        SIP,
        on_delete=models.CASCADE,
        related_name="installments",
    )

    scheduled_date = models.DateField()

    amount = models.DecimalField(
        max_digits=20,
        decimal_places=2,
    )

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

    executed_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    notes = models.TextField(
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "scheduled_date",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "sip",
                    "scheduled_date",
                ],
                name="unique_sip_installment",
            )
        ]

        indexes = [
            models.Index(
                fields=[
                    "sip",
                    "scheduled_date",
                ]
            ),
            models.Index(
                fields=[
                    "status",
                ]
            ),
        ]

    def __str__(self):

        return (
            f"{self.sip.scheme.scheme_name} - "
            f"{self.scheduled_date} - "
            f"{self.status}"
        )
