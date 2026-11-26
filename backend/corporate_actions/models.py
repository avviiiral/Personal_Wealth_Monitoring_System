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
    resulting_security_name = models.CharField(max_length=255, blank=True, default="")
    # Official fraction of the pre-event cost basis that moves to the resulting
    # security (demerger), or that is carried to the successor shares when a
    # merger also pays cash. Never inferred.
    cost_allocation_ratio = models.DecimalField(max_digits=12, decimal_places=8, null=True, blank=True)
    # Official price paid per share for fractional entitlements (cash in lieu).
    cash_in_lieu_price = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
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
        if self.cost_allocation_ratio is not None and not (0 < self.cost_allocation_ratio <= 1):
            raise ValidationError("cost_allocation_ratio must be greater than 0 and at most 1.")
        if self.cash_in_lieu_price is not None and self.cash_in_lieu_price < 0:
            raise ValidationError("cash_in_lieu_price cannot be negative.")
        if self.record_date and self.payment_date and self.payment_date < self.record_date:
            raise ValidationError("payment_date cannot precede record_date.")

    @property
    def quantity_factor(self):
        """Total-holding multiplier of a confirmed ratio action (informational only).

        BONUS / STOCK_DIVIDEND issue ``new_units`` extra shares per ``old_units``
        held, so the holding grows by (old + new) / old; splits convert
        ``old_units`` into ``new_units``. Holdings change only through accepted
        ledger transactions, never through this value.
        """
        if self.status != CorporateActionStatus.CONFIRMED or self.action_type not in RATIO_ACTIONS:
            return None
        if not self.old_units or not self.new_units:
            return None
        if self.action_type in {CorporateActionType.BONUS, CorporateActionType.STOCK_DIVIDEND}:
            return (self.old_units + self.new_units) / self.old_units
        return self.new_units / self.old_units

    @property
    def event_identity(self):
        """Owner-independent identity of the underlying event."""
        identity = (self.isin or "").strip().upper() or f"SYMBOL:{(self.symbol or '').strip().upper()}"
        return f"{identity}|{self.action_type}|{self.ex_date.isoformat() if self.ex_date else ''}"

    def __str__(self):
        return f"{self.security_name} — {self.get_action_type_display()} ({self.ex_date})"



class CorporateActionReconciliationStatus(models.TextChoices):
    PENDING = "PENDING", "Pending review"
    ALREADY_RECORDED = "ALREADY_RECORDED", "Already recorded"
    COMPLETED = "COMPLETED", "Completed"
    DISMISSED = "DISMISSED", "Dismissed"
    MANUAL_REVIEW = "MANUAL_REVIEW", "Manual verification required"
    PENDING_USER_APPROVAL = "PENDING_USER_APPROVAL", "Awaiting identity approval"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"


class CorporateActionExposure(models.Model):
    """Snapshot of direct or indirect exposure when an event is matched."""

    action = models.ForeignKey(CorporateAction, on_delete=models.CASCADE, related_name="exposures")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="corporate_action_exposures")
    family_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    exposure_type = models.CharField(max_length=20, choices=[("DIRECT", "Direct"), ("MUTUAL_FUND", "Mutual fund underlying"), ("PMS", "PMS underlying")])
    investment_name = models.CharField(max_length=300)
    investment_id = models.PositiveBigIntegerField(null=True, blank=True)
    quantity = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    holding_weight = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    snapshot_date = models.DateField(null=True, blank=True)
    is_current = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["action", "owner", "exposure_type", "investment_name", "investment_id"], name="uniq_corp_action_exposure"),
        ]
        indexes = [
            models.Index(fields=["owner", "action"], name="corp_exposure_owner_idx"),
            models.Index(fields=["family_id_snapshot", "owner"], name="corp_exposure_family_idx"),
        ]


class CorporateActionReconciliation(models.Model):
    """Per-user decision/state; unique action+owner makes repeated scans idempotent."""

    action = models.ForeignKey(CorporateAction, on_delete=models.CASCADE, related_name="reconciliations")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="corporate_action_reconciliations")
    family_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=24, choices=CorporateActionReconciliationStatus.choices, default=CorporateActionReconciliationStatus.PENDING)
    matched_transaction_ids = models.JSONField(default=list, blank=True)
    identity_match_method = models.CharField(max_length=24, blank=True, default="")
    candidate_asset_ids = models.JSONField(default=list, blank=True)
    selected_asset = models.ForeignKey("investments.Asset", on_delete=models.SET_NULL, null=True, blank=True, related_name="corporate_action_reconciliations")
    suggested_quantity = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_corporate_action_reconciliations")
    notes = models.TextField(blank=True, default="")
    idempotency_key = models.CharField(max_length=64, unique=True)
    # Hash of the proposal last shown to the user; acceptance re-calculates
    # under lock and refuses to book a proposal that changed in between.
    proposal_fingerprint = models.CharField(max_length=64, blank=True, default="")
    last_reconciled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["action", "owner"], name="uniq_corp_action_reconciliation")]
        indexes = [models.Index(fields=["owner", "status", "-updated_at"], name="corp_recon_owner_status_idx")]


TERMINAL_RECONCILIATION_STATUSES = {
    CorporateActionReconciliationStatus.COMPLETED,
    CorporateActionReconciliationStatus.APPROVED,
    CorporateActionReconciliationStatus.REJECTED,
    CorporateActionReconciliationStatus.DISMISSED,
}


class CorporateActionPositionStatus(models.TextChoices):
    PENDING = "PENDING", "Pending review"
    ALREADY_RECORDED = "ALREADY_RECORDED", "Already recorded"
    MANUAL_REVIEW = "MANUAL_REVIEW", "Manual review"
    NOT_ELIGIBLE = "NOT_ELIGIBLE", "Not eligible"
    APPLIED = "APPLIED", "Applied"
    REJECTED = "REJECTED", "Rejected"


class CorporateActionPosition(models.Model):
    """One eligible ledger position (family member + portfolio hierarchy) of a reconciliation.

    This is the proposal shown to the user and its audit trail; the accounting
    itself lives only in ``investments.Transaction``.
    """

    reconciliation = models.ForeignKey(CorporateActionReconciliation, on_delete=models.CASCADE, related_name="positions")
    family = models.ForeignKey("users.FamilyGroup", on_delete=models.PROTECT, null=True, blank=True, related_name="corporate_action_positions")
    position_key = models.CharField(max_length=64)
    status = models.CharField(max_length=20, choices=CorporateActionPositionStatus.choices, default=CorporateActionPositionStatus.PENDING)
    family_name = models.CharField(max_length=255, blank=True, default="")
    portfolio = models.CharField(max_length=255, blank=True, default="")
    asset_class = models.CharField(max_length=255, blank=True, default="")
    sub_class = models.CharField(max_length=255, blank=True, default="")
    asset_name = models.CharField(max_length=255, blank=True, default="")
    underlying = models.CharField(max_length=255, blank=True, default="")
    advisors = models.CharField(max_length=255, blank=True, default="")
    asset = models.ForeignKey("investments.Asset", on_delete=models.SET_NULL, null=True, blank=True, related_name="corporate_action_positions")
    asset_ids = models.JSONField(default=list, blank=True)
    eligibility_cutoff = models.DateField(null=True, blank=True)
    eligible_quantity = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    quantity_delta = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    resulting_quantity = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    new_security_quantity = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    fractional_entitlement = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    cost_basis = models.DecimalField(max_digits=24, decimal_places=4, null=True, blank=True)
    resulting_cost_basis = models.DecimalField(max_digits=24, decimal_places=4, null=True, blank=True)
    allocated_cost = models.DecimalField(max_digits=24, decimal_places=4, null=True, blank=True)
    cash_amount = models.DecimalField(max_digits=24, decimal_places=4, null=True, blank=True)
    matched_transaction_ids = models.JSONField(default=list, blank=True)
    created_transaction_ids = models.JSONField(default=list, blank=True)
    planned_entries = models.JSONField(default=list, blank=True)
    calculation = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True, default="")
    applied_at = models.DateTimeField(null=True, blank=True)
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="applied_corporate_action_positions")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["family_name", "portfolio", "asset_class", "sub_class", "asset_name", "id"]
        constraints = [
            models.UniqueConstraint(fields=["reconciliation", "position_key"], name="uniq_corp_action_position"),
        ]
        indexes = [models.Index(fields=["reconciliation", "status"], name="corp_position_status_idx")]

    def __str__(self):
        return f"{self.family_name} / {self.asset_name} / {self.underlying} ({self.status})"


class CorporateActionLedgerEntry(models.Model):
    """Audit link between an accepted position and the ledger row it created."""

    position = models.ForeignKey(CorporateActionPosition, on_delete=models.CASCADE, related_name="ledger_entries")
    transaction = models.OneToOneField("investments.Transaction", on_delete=models.CASCADE, related_name="corporate_action_entry")
    role = models.CharField(max_length=20)
    description = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position_id", "id"]

    def __str__(self):
        return self.description


class CorporateActionNotification(models.Model):
    """Deduplicated actionable notification for an individual user."""

    action = models.ForeignKey(CorporateAction, on_delete=models.CASCADE, related_name="notifications")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="corporate_action_notifications")
    reconciliation = models.ForeignKey(CorporateActionReconciliation, on_delete=models.CASCADE, related_name="notifications")
    reviewed = models.BooleanField(default=False)
    dismissed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["action", "owner"], name="uniq_corp_action_notification")]
        indexes = [models.Index(fields=["owner", "reviewed", "dismissed", "-created_at"], name="corp_notif_inbox_idx")]
