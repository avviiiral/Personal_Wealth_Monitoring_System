from django.conf import settings
from django.db import models


class MutualFundResearchReport(models.Model):
    """Immutable-in-purpose dated research payload imported from a source report."""

    report_key = models.CharField(max_length=100)
    report_date = models.DateField()
    report_cycle = models.CharField(max_length=100, blank=True)
    source_file = models.CharField(max_length=255, blank=True)
    payload = models.JSONField(default=dict)
    imported_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-report_date", "-imported_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["report_key", "report_date"],
                name="unique_mf_research_report_key_date",
            )
        ]

    def __str__(self):
        return f"{self.report_key} ({self.report_date})"
