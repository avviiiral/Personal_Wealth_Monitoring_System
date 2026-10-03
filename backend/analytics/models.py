from django.db import models
from django.db.models import Q


class StandardAllocation(models.Model):
    """
    Standard Allocation target for one Dashboard Asset Category.

    A null family_name is the shared baseline for all family members. A
    non-null family_name row is an override for that Family Member label only.
    The percentage is authoritative; allocation_amount is retained as a
    compatibility/cache field and is recalculated from the current portfolio
    total when allocations are read.
    """

    family_name = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
        help_text="Null means this is the global Standard Allocation shared by all Family Members.",
    )
    asset_category = models.CharField(max_length=100)
    allocation_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0,
    )
    allocation_amount = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=0,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["asset_category"]
        constraints = [
            models.UniqueConstraint(
                fields=["family_name", "asset_category"],
                condition=Q(family_name__isnull=False),
                name="unique_family_standard_allocation",
            ),
            models.UniqueConstraint(
                fields=["asset_category"],
                condition=Q(family_name__isnull=True),
                name="unique_global_standard_allocation",
            ),
        ]

    def __str__(self):
        scope = self.family_name or "All Families"
        return f"{scope} - {self.asset_category}: {self.allocation_percent}%"
