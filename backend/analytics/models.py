from django.db import models


class StandardAllocation(models.Model):
    """
    Global target allocation for one Dashboard Asset Category.
    The same target percentages are shared by every family and every family
    member. The displayed rupee amount is calculated from the selected
    family's current portfolio value.
    """

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
                fields=["asset_category"],
                name="unique_global_standard_allocation",
            ),
        ]

    def __str__(self):
        return f"{self.asset_category}: {self.allocation_percent}%"
