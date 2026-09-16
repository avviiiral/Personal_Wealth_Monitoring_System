from django.db import models
from django.conf import settings


class StandardAllocation(models.Model):
    """
    User-defined target allocation for one Dashboard Asset Category.

    The target is personal to the authenticated user and can be scoped
    to a selected family. A blank family_name represents the combined
    Dashboard view ("All" families).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="standard_allocations",
    )
    family_name = models.CharField(max_length=255, blank=True, default="")
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
                fields=["user", "family_name", "asset_category"],
                name="unique_standard_allocation_scope",
            ),
        ]

    def __str__(self):
        scope = self.family_name or "All Families"
        return f"{self.user.username} - {scope} - {self.asset_category}: {self.allocation_percent}%"
