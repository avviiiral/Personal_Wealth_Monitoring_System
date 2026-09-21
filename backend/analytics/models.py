from django.db import models


class StandardAllocation(models.Model):
    """
    Shared target allocation for one Dashboard Asset Category within a family.
    Standard Allocation is family-owned so every family member sees and edits
    the same target percentages.
    """

    family = models.ForeignKey(
        "users.FamilyGroup",
        on_delete=models.CASCADE,
        related_name="standard_allocations",
    )
    asset_category = models.CharField(max_length=100)
    allocation_percent = models.DecimalField(
        max_digits=6,
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
                name="unique_family_standard_allocation",
            ),
        ]

    def __str__(self):
        return f"{self.family.name} - {self.asset_category}: {self.allocation_percent}%"
