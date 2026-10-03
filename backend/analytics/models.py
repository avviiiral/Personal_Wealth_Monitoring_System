from django.db import models
from django.db.models import Q


class StandardAllocation(models.Model):
    """
    Shared target allocation for one Dashboard Asset Category within a family.
    Standard Allocation is family-owned so every family member sees and edits
    the same target percentages.
    """

    family = models.ForeignKey(
        "users.FamilyGroup",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="standard_allocations",
        help_text="Null means this is the global Standard Allocation shared by all families.",
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
                fields=["family", "asset_category"],
                condition=Q(family__isnull=False),
                name="unique_family_standard_allocation",
            ),
            models.UniqueConstraint(
                fields=["asset_category"],
                condition=Q(family__isnull=True),
                name="unique_global_standard_allocation",
            ),
        ]

    def __str__(self):
        scope = self.family.name if self.family_id else "All Families"
        return f"{scope} - {self.asset_category}: {self.allocation_percent}%"
