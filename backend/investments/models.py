from django.db import models
from django.contrib.auth.models import User

from users.models import FamilyGroup

from typing import TYPE_CHECKING


class AssetCategory(models.TextChoices):
    STOCK = "STOCK", "Stock"
    MUTUAL_FUND = "MUTUAL_FUND", "Mutual Fund"
    ETF = "ETF", "ETF"
    FIXED_DEPOSIT = "FIXED_DEPOSIT", "Fixed Deposit"
    GOLD = "GOLD", "Gold"
    CASH = "CASH", "Cash"
    REAL_ESTATE = "REAL_ESTATE", "Real Estate"
    BOND = "BOND", "Bond"
    CRYPTO = "CRYPTO", "Cryptocurrency"
    OTHER = "OTHER", "Other"


class Asset(models.Model):
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="assets")
    family = models.ForeignKey(FamilyGroup, on_delete=models.PROTECT, related_name="assets", null=True, blank=True, db_index=True)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=30, choices=AssetCategory.choices)
    symbol = models.CharField(max_length=50, blank=True, null=True)
    isin = models.CharField(max_length=20, blank=True, null=True)
    institution = models.CharField(max_length=255, blank=True, null=True)
    currency = models.CharField(max_length=10, default="INR")
    is_active = models.BooleanField(default=True)
    security_master = models.ForeignKey("SecurityMaster", on_delete=models.SET_NULL, blank=True, null=True, related_name="assets")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class TransactionType(models.TextChoices):
    BUY = "BUY", "Buy"
    SELL = "SELL", "Sell"
    SIP = "SIP", "SIP"
    DIVIDEND = "DIVIDEND", "Dividend"
    INTEREST = "INTEREST", "Interest"
    DEPOSIT = "DEPOSIT", "Deposit"
    WITHDRAWAL = "WITHDRAWAL", "Withdrawal"
    BONUS = "BONUS", "Bonus"
    SPLIT = "SPLIT", "Split"
    OTHER = "OTHER", "Other"


class TransactionSource(models.TextChoices):
    EXCEL = "EXCEL", "Excel"
    MANUAL = "MANUAL", "Manual"
    OTHER = "OTHER", "Other"


class Transaction(models.Model):
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions")
    family = models.ForeignKey(FamilyGroup, on_delete=models.PROTECT, related_name="transactions", null=True, blank=True, db_index=True)
    family_name = models.CharField(max_length=255, blank=True, null=True)
    portfolio = models.CharField(max_length=255, blank=True, null=True)
    asset_class = models.CharField(max_length=255, blank=True, null=True)
    sub_class = models.CharField(max_length=255, blank=True, null=True)
    asset_name = models.CharField(max_length=255, blank=True, null=True)
    underlying = models.CharField(max_length=255, blank=True, null=True)
    advisors = models.CharField(max_length=255, blank=True, null=True)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="transactions")

    if TYPE_CHECKING:
        asset_id: int

    transaction_type = models.CharField(max_length=20, choices=TransactionType.choices)
    transaction_date = models.DateField()
    quantity = models.DecimalField(max_digits=20, decimal_places=6, default=0)
    price_per_unit = models.DecimalField(max_digits=20, decimal_places=6, default=0)
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    fees = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    notes = models.TextField(blank=True, null=True)
    source = models.CharField(max_length=20, choices=TransactionSource.choices, default=TransactionSource.MANUAL)
    source_key = models.CharField(max_length=64, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-transaction_date", "-created_at"]
        indexes = [
            models.Index(fields=["family", "source", "source_key"], name="transaction_source_key_idx"),
            models.Index(fields=["owner", "family_name", "asset_class", "sub_class"], name="transaction_hierarchy_idx"),
            models.Index(fields=["family", "asset", "transaction_date"], name="transaction_asset_date_idx"),
            models.Index(fields=["family", "transaction_date"], name="transaction_family_date_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["family", "source", "source_key"],
                condition=models.Q(source_key__isnull=False),
                name="unique_transaction_family_source_key",
            )
        ]

    def __str__(self):
        return f"{self.asset.name} - {self.transaction_type} - {self.amount}"


class TransactionEditHistory(models.Model):
    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="edit_history",
    )
    owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transaction_edit_history",
    )
    family = models.ForeignKey(FamilyGroup, on_delete=models.PROTECT, related_name="transaction_edit_history", null=True, blank=True, db_index=True)
    edited_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transaction_edits_made",
    )
    edited_at = models.DateTimeField(auto_now_add=True)
    old_values = models.JSONField(default=dict)
    new_values = models.JSONField(default=dict)
    changed_fields = models.JSONField(default=list)

    class Meta:
        ordering = ["-edited_at"]
        indexes = [
            models.Index(fields=["owner", "-edited_at"], name="tx_edit_owner_date_idx"),
            models.Index(fields=["transaction", "-edited_at"], name="tx_edit_transaction_date_idx"),
        ]

    def __str__(self):
        transaction_id = self.transaction_id or "deleted"
        return f"Transaction {transaction_id} edited by {self.edited_by.username}"


class Holding(models.Model):
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="holdings")
    family = models.ForeignKey(FamilyGroup, on_delete=models.PROTECT, related_name="holdings", null=True, blank=True, db_index=True)
    asset = models.OneToOneField(Asset, on_delete=models.CASCADE, related_name="holding")

    if TYPE_CHECKING:
        asset_id: int

    quantity = models.DecimalField(max_digits=20, decimal_places=6, default=0)
    average_cost = models.DecimalField(max_digits=20, decimal_places=6, default=0)
    invested_value = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    current_price = models.DecimalField(max_digits=20, decimal_places=6, default=0)
    current_value = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    unrealized_pnl = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["asset__name"]

    def __str__(self):
        return f"{self.asset.name} - {self.quantity}"


class PortfolioPosition(models.Model):
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="portfolio_positions")
    family = models.ForeignKey(FamilyGroup, on_delete=models.PROTECT, related_name="portfolio_positions", null=True, blank=True, db_index=True)
    family_name = models.CharField(max_length=255)
    portfolio = models.CharField(max_length=255)
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="portfolio_positions")
    quantity = models.DecimalField(max_digits=20, decimal_places=6, default=0)
    average_cost = models.DecimalField(max_digits=20, decimal_places=6, default=0)
    invested_value = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    current_price = models.DecimalField(max_digits=20, decimal_places=6, default=0)
    current_value = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    gain = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["family_name", "portfolio", "asset__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["family", "family_name", "portfolio", "asset"],
                name="unique_portfolio_position",
            )
        ]

    def __str__(self):
        return f"{self.family_name} - {self.portfolio} - {self.asset.name}"


class SecurityMaster(models.Model):
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="security_masters")
    family = models.ForeignKey(FamilyGroup, on_delete=models.PROTECT, related_name="security_masters", null=True, blank=True, db_index=True)
    isin = models.CharField(max_length=20, blank=True, null=True)
    asset_name = models.CharField(max_length=255)
    sector = models.CharField(max_length=100, blank=True, null=True)
    cap_type = models.CharField(max_length=50, blank=True, null=True)
    amc_name = models.CharField(max_length=255, blank=True, null=True)

    pe_ratio = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    pb_ratio = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    peg_ratio = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    roe = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)

    credit_rating = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        choices=[
            ("SOVEREIGN", "Sovereign"),
            ("AAA", "AAA / AAA+"),
            ("AA", "AA / AA+"),
            ("A_AND_BELOW", "A and Below"),
            ("UNRATED", "Unrated"),
        ],
    )

    ytm = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True, help_text="Yield to Maturity, percent.")
    modified_duration = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    average_maturity = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True, help_text="Years.")
    manual_nav_enabled = models.BooleanField(default=False)
    manual_nav = models.DecimalField(max_digits=20, decimal_places=6, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["asset_name"]
        constraints = [
            models.UniqueConstraint(fields=["family", "isin"], name="unique_security_master_family_isin")
        ]

    def __str__(self):
        if self.isin:
            return f"{self.asset_name} ({self.isin})"
        return self.asset_name


class AssetUnderlyingHolding(models.Model):
    """Family-owned underlying holdings uploaded for a portfolio asset."""

    owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="asset_underlying_holdings",
    )
    family = models.ForeignKey(
        FamilyGroup,
        on_delete=models.PROTECT,
        related_name="asset_underlying_holdings",
        db_index=True,
    )
    asset = models.ForeignKey(
        Asset,
        on_delete=models.CASCADE,
        related_name="underlying_holdings",
    )
    stock_name = models.CharField(max_length=300)
    holding_percentage = models.DecimalField(max_digits=10, decimal_places=4)
    sector = models.CharField(max_length=150, blank=True, null=True)
    cap_type = models.CharField(max_length=50, blank=True, null=True)
    source = models.CharField(max_length=30, default="EXCEL")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-holding_percentage", "stock_name"]
        indexes = [
            models.Index(fields=["family", "asset"], name="au_family_asset_idx"),
            models.Index(fields=["asset", "stock_name"], name="au_asset_stock_idx"),
            models.Index(fields=["family", "stock_name"], name="au_family_stock_idx"),
        ]

    def __str__(self):
        return f"{self.asset.name} - {self.stock_name} - {self.holding_percentage}%"
